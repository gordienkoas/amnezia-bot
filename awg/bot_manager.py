import db
import aiohttp
import logging
import asyncio
import aiofiles
import os
import re
import json
import subprocess
import sys
import pytz
import zipfile
import ipaddress
import humanize
import shutil
import uuid
from aiogram import Bot, types
from aiogram.dispatcher import Dispatcher
from aiogram.dispatcher.middlewares import BaseMiddleware
from aiogram.utils import executor
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Загрузка конфигурации
setting = db.get_config()
bot_token = setting.get('bot_token')
admin_ids = setting.get('admin_ids', [])
moderator_ids = setting.get('moderator_ids', [])
wg_config_file = setting.get('wg_config_file')
docker_container = setting.get('docker_container')
endpoint = setting.get('endpoint')

if not all([bot_token, admin_ids, wg_config_file, docker_container, endpoint]):
    logger.error("Некоторые обязательные настройки отсутствуют.")
    sys.exit(1)

admins = [int(admin_id) for admin_id in admin_ids]
moderators = [int(mod_id) for mod_id in moderator_ids]
bot = Bot(bot_token)
WG_CONFIG_FILE = wg_config_file
DOCKER_CONTAINER = docker_container
ENDPOINT = endpoint

# Цены за периоды (в условных единицах)
PRICING = {
    '1_month': 10.0,
    '3_months': 25.0,
    '6_months': 45.0
}

class AdminMessageDeletionMiddleware(BaseMiddleware):
    async def on_process_message(self, message: types.Message, data: dict):
        if message.from_user.id in admins and message.text.startswith('/'):
            asyncio.create_task(delete_message_after_delay(message.chat.id, message.message_id))

dp = Dispatcher(bot)
scheduler = AsyncIOScheduler(timezone=pytz.UTC)
scheduler.start()
dp.middleware.setup(AdminMessageDeletionMiddleware())

# Главное меню
def get_main_menu_markup(user_id):
    markup = InlineKeyboardMarkup(row_width=2)
    if user_id in admins:
        markup.add(
            InlineKeyboardButton("➕ Добавить пользователя", callback_data="add_user"),
            InlineKeyboardButton("📋 Список клиентов", callback_data="list_users")
        )
        markup.add(
            InlineKeyboardButton("🔑 Получить конфиг", callback_data="get_config"),
            InlineKeyboardButton("ℹ️ Инструкция", callback_data="instructions")
        )
        markup.add(
            InlineKeyboardButton("👥 Список админов", callback_data="list_admins"),
            InlineKeyboardButton("👤 Добавить админа", callback_data="add_admin")
        )
        markup.add(
            InlineKeyboardButton("💾 Создать бекап", callback_data="create_backup"),
            InlineKeyboardButton("🎟️ Управление промокодами", callback_data="manage_promocodes")
        )
        markup.add(
            InlineKeyboardButton("🔄 Проверить обновления", callback_data="check_updates"),
            InlineKeyboardButton("🔄 Перезагрузить VPN", callback_data="restart_vpn")
        )
    elif user_id in moderators:
        markup.add(
            InlineKeyboardButton("➕ Добавить пользователя", callback_data="add_user"),
            InlineKeyboardButton("📋 Список клиентов", callback_data="list_users")
        )
        markup.add(
            InlineKeyboardButton("🔑 Получить конфиг", callback_data="get_config"),
            InlineKeyboardButton("ℹ️ Инструкция", callback_data="instructions")
        )
    else:
        markup.add(
            InlineKeyboardButton("💳 Купить ключ", callback_data="buy_key"),
            InlineKeyboardButton("🎟️ Использовать промокод", callback_data="use_promocode")
        )
    return markup

user_main_messages = {}
isp_cache = {}
ISP_CACHE_FILE = 'files/isp_cache.json'
CACHE_TTL = 24 * 3600  # 24 часа в секундах

def get_interface_name():
    return os.path.basename(WG_CONFIG_FILE).split('.')[0]

async def load_isp_cache():
    global isp_cache
    if os.path.exists(ISP_CACHE_FILE):
        async with aiofiles.open(ISP_CACHE_FILE, 'r') as f:
            isp_cache = json.loads(await f.read())

async def save_isp_cache():
    async with aiofiles.open(ISP_CACHE_FILE, 'w') as f:
        await f.write(json.dumps(isp_cache))

async def get_isp_info(ip: str) -> str:
    now = datetime.now(pytz.UTC).timestamp()
    if ip in isp_cache and (now - isp_cache[ip]['timestamp']) < CACHE_TTL:
        return isp_cache[ip]['isp']
    
    try:
        if ipaddress.ip_address(ip).is_private:
            return "Private Range"
    except:
        return "Invalid IP"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://ip-api.com/json/{ip}?fields=isp") as resp:
            if resp.status == 200:
                data = await resp.json()
                isp = data.get('isp', 'Unknown ISP')
                isp_cache[ip] = {'isp': isp, 'timestamp': now}
                await save_isp_cache()
                return isp
    return "Unknown ISP"

async def delete_message_after_delay(chat_id: int, message_id: int, delay: int = 2):
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except:
        pass

def parse_relative_time(relative_str: str) -> datetime:
    if not isinstance(relative_str, str) or not relative_str.strip():
        logger.error(f"Некорректный relative_str: {relative_str}")
        return datetime.now(pytz.UTC)
    try:
        relative_str = relative_str.lower().replace(' ago', '')
        delta = 0
        for part in relative_str.split(', '):
            num, unit = part.split()
            num = int(num)
            if 'minute' in unit:
                delta += num * 60
            elif 'hour' in unit:
                delta += num * 3600
            elif 'day' in unit:
                delta += num * 86400
            elif 'week' in unit:
                delta += num * 604800
            elif 'month' in unit:
                delta += num * 2592000
        return datetime.now(pytz.UTC) - timedelta(seconds=delta)
    except Exception as e:
        logger.error(f"Ошибка в parse_relative_time: {str(e)}")
        return datetime.now(pytz.UTC)

def parse_transfer(transfer_str: str) -> tuple:
    try:
        incoming, outgoing = transfer_str.split('/')
        incoming_bytes = humanize.parse_bytes(incoming.strip())
        outgoing_bytes = humanize.parse_bytes(outgoing.strip())
        return incoming_bytes, outgoing_bytes
    except:
        return 0, 0

async def generate_vpn_key(conf_path: str) -> str:
    async with aiofiles.open(conf_path, 'r') as f:
        config = await f.read()
    config = config.replace(f'Endpoint = {ENDPOINT}', f'Endpoint = {ENDPOINT}')
    return config

@dp.message_handler(commands=['start', 'help'])
async def start_command_handler(message: types.Message):
    user_id = message.from_user.id
    # Удаляем старое сообщение с меню, если оно существует
    if user_id in user_main_messages:
        try:
            await bot.delete_message(
                chat_id=user_main_messages[user_id]['chat_id'],
                message_id=user_main_messages[user_id]['message_id']
            )
        except:
            pass
    # Отправляем новое сообщение с меню
    sent_message = await message.answer("Выберите действие:", reply_markup=get_main_menu_markup(user_id))
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': None
    }

@dp.message_handler(commands=['add_admin'])
async def add_admin_command(message: types.Message):
    if message.from_user.id not in admins:
        await message.answer("У вас нет прав.")
        return
    try:
        new_admin_id = int(message.text.split()[1])
        if new_admin_id not in admins:
            db.add_admin(new_admin_id)
            admins.append(new_admin_id)
            await message.answer(f"Админ {new_admin_id} добавлен.")
            await bot.send_message(new_admin_id, "Вы назначены администратором!")
    except:
        await message.answer("Формат: /add_admin <user_id>")

@dp.message_handler()
async def handle_messages(message: types.Message):
    user_id = message.from_user.id
    user_state = user_main_messages.get(user_id, {}).get('state')
    
    if user_state == 'waiting_for_user_name':
        user_name = message.text.strip()
        if not re.match(r'^[a-zA-Z0-9_-]+$', user_name):
            await message.reply("Имя может содержать только буквы, цифры, - и _.")
            return
        success = db.root_add(user_name, ipv6=False)
        if success:
            conf_path = os.path.join('users', user_name, f'{user_name}.conf')
            if os.path.exists(conf_path):
                vpn_key = await generate_vpn_key(conf_path)
                caption = f"Конфигурация для {user_name}:\nAmneziaVPN:\n[Google Play](https://play.google.com/store/apps/details?id=org.amnezia.vpn&hl=ru)\n[GitHub](https://github.com/amnezia-vpn/amnezia-client)\n```\n{vpn_key}\n```"
                with open(conf_path, 'rb') as config:
                    config_message = await bot.send_document(user_id, config, caption=caption, parse_mode="Markdown")
                    await bot.pin_chat_message(user_id, config_message.message_id, disable_notification=True)
        # Отправляем новое сообщение с меню
        sent_message = await message.answer("Выберите действие:", reply_markup=get_main_menu_markup(user_id))
        user_main_messages[user_id] = {
            'chat_id': sent_message.chat.id,
            'message_id': sent_message.message_id,
            'state': None
        }
    elif user_state == 'waiting_for_admin_id' and user_id in admins:
        try:
            new_admin_id = int(message.text.strip())
            if new_admin_id not in admins:
                db.add_admin(new_admin_id)
                admins.append(new_admin_id)
                await message.reply(f"Админ {new_admin_id} добавлен.")
                await bot.send_message(new_admin_id, "Вы назначены администратором!")
            sent_message = await message.answer("Выберите действие:", reply_markup=get_main_menu_markup(user_id))
            user_main_messages[user_id] = {
                'chat_id': sent_message.chat.id,
                'message_id': sent_message.message_id,
                'state': None
            }
        except:
            await message.reply("Введите корректный Telegram ID.")
    elif user_state == 'waiting_for_promocode':
        promocode = message.text.strip()
        discount = db.apply_promocode(promocode)
        if discount:
            user_main_messages[user_id]['promocode_discount'] = discount
            await message.reply(f"Промокод активирован! Скидка: {discount}%")
        else:
            await message.reply("Неверный или истёкший промокод.")
        sent_message = await message.answer("Выберите действие:", reply_markup=get_main_menu_markup(user_id))
        user_main_messages[user_id] = {
            'chat_id': sent_message.chat.id,
            'message_id': sent_message.message_id,
            'state': None
        }
    elif user_state == 'waiting_for_new_promocode' and user_id in admins:
        try:
            code, discount, days_valid, max_uses = message.text.strip().split()
            discount = float(discount)
            days_valid = int(days_valid)
            max_uses = int(max_uses) if max_uses.lower() != 'none' else None
            expires_at = datetime.now(pytz.UTC) + timedelta(days=days_valid) if days_valid > 0 else None
            if db.add_promocode(code, discount, expires_at, max_uses):
                await message.reply(f"Промокод {code} добавлен: скидка {discount}%, действует {days_valid} дней, макс. использований: {max_uses or 'неограничено'}")
            else:
                await message.reply("Промокод уже существует.")
        except:
            await message.reply("Формат: <код> <скидка%> <дней_действия> <макс_использований|none>")
        sent_message = await message.answer("Выберите действие:", reply_markup=get_main_menu_markup(user_id))
        user_main_messages[user_id] = {
            'chat_id': sent_message.chat.id,
            'message_id': sent_message.message_id,
            'state': None
        }
    elif user_state == 'waiting_for_renewal_period' and user_id in admins:
        try:
            username = user_main_messages[user_id]['renewal_username']
            period = message.text.strip().lower()
            if period not in PRICING:
                await message.reply("Неверный период. Введите: 1_month, 3_months или 6_months.")
                return
            months = {'1_month': 1, '3_months': 3, '6_months': 6}[period]
            expiration = datetime.now(pytz.UTC) + timedelta(days=30 * months)
            db.set_user_expiration(username, expiration, "Неограниченно")
            await message.reply(f"Подписка для {username} продлена до {expiration.strftime('%Y-%m-%d')}.")
            sent_message = await message.answer("Выберите действие:", reply_markup=get_main_menu_markup(user_id))
            user_main_messages[user_id] = {
                'chat_id': sent_message.chat.id,
                'message_id': sent_message.message_id,
                'state': None
            }
        except Exception as e:
            await message.reply(f"Ошибка при продлении: {str(e)}")

@dp.callback_query_handler(lambda c: c.data == "add_user")
async def prompt_for_user_name(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins and user_id not in moderators:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    # Удаляем старое сообщение
    try:
        await bot.delete_message(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id
        )
    except:
        pass
    sent_message = await bot.send_message(
        chat_id=callback_query.message.chat.id,
        text="Введите имя пользователя:",
        reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🏠 Домой", callback_data="home"))
    )
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': 'waiting_for_user_name'
    }
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "add_admin")
async def prompt_for_admin_id(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    try:
        await bot.delete_message(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id
        )
    except:
        pass
    sent_message = await bot.send_message(
        chat_id=callback_query.message.chat.id,
        text="Введите Telegram ID нового админа:",
        reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🏠 Домой", callback_data="home"))
    )
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': 'waiting_for_admin_id'
    }
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data.startswith('client_'))
async def client_selected_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins and user_id not in moderators:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    
    try:
        username = callback_query.data.split('client_')[1]
        clients = db.get_client_list()
        client_info = next((c for c in clients if c[0] == username), None)
        if not client_info:
            await callback_query.answer("Пользователь не найден.", show_alert=True)
            return
        
        status = "🔴 Офлайн"
        incoming_traffic = "↓—"
        outgoing_traffic = "↑—"
        ipv4_address = "—"
        expiration = db.get_user_expiration(username)
        expiration_text = expiration.strftime("%Y-%m-%d %H:%M UTC") if expiration else "Не установлен"
        # Предполагается, что db.get_user_telegram_id возвращает Telegram ID или username
        # Если метода нет, нужно добавить в db.py функцию, которая возвращает Telegram ID, связанный с username
        telegram_id = db.get_user_telegram_id(username) or "Не указан"  # Замените на реальный вызов, если есть

        if isinstance(client_info, (tuple, list)) and len(client_info) > 2 and client_info[2] is not None:
            ip_match = re.search(r'(\d{1,3}\.){3}\d{1,3}/\d+', str(client_info[2]))
            ipv4_address = ip_match.group(0) if ip_match else "—"
        
        active_clients = db.get_active_list()
        active_info = next((ac for ac in active_clients if ac[0] == username), None)
        
        if active_info and isinstance(active_info, (tuple, list)) and len(active_info) > 2:
            if active_info[1] and active_info[1].lower() not in ['never', 'нет данных', '-']:
                try:
                    last_handshake = parse_relative_time(active_info[1])
                    status = "🟢 Онлайн" if (datetime.now(pytz.UTC) - last_handshake).total_seconds() <= 60 else "❌ Офлайн"
                except:
                    pass
            if active_info[2]:
                try:
                    incoming_bytes, outgoing_bytes = parse_transfer(active_info[2])
                    incoming_traffic = f"↓{humanize.naturalsize(incoming_bytes)}"
                    outgoing_traffic = f"↑{humanize.naturalsize(outgoing_bytes)}"
                except:
                    pass
        
        text = (
            f"📧 *Имя:* {username}\n"
            f"👤 *Пользователь:* {telegram_id}\n"
            f"🌐 *IPv4:* {ipv4_address}\n"
            f"🌐 *Статус:* {status}\n"
            f"🔼 *Исходящий:* {incoming_traffic}\n"
            f"🔽 *Входящий:* {outgoing_traffic}\n"
            f"⏰ *Срок действия:* {expiration_text}"
        )
        
        keyboard = InlineKeyboardMarkup(row_width=2).add(
            InlineKeyboardButton("ℹ️ IP info", callback_data=f"ip_info_{username}"),
            InlineKeyboardButton("🔗 Подключения", callback_data=f"connections_{username}"),
            InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_user_{username}"),
            InlineKeyboardButton("🔄 Продлить", callback_data=f"renew_user_{username}"),
            InlineKeyboardButton("⬅️ Назад", callback_data="list_users"),
            InlineKeyboardButton("🏠 Домой", callback_data="home")
        )
        
        # Отправляем новое сообщение вместо редактирования
        try:
            await bot.delete_message(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id
            )
        except:
            pass
        sent_message = await bot.send_message(
            chat_id=callback_query.message.chat.id,
            text=text,
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        user_main_messages[user_id] = {
            'chat_id': sent_message.chat.id,
            'message_id': sent_message.message_id,
            'state': None
        }
        await callback_query.answer()
    
    except Exception as e:
        logger.error(f"Ошибка в client_selected_callback: {str(e)}")
        sent_message = await bot.send_message(
            chat_id=callback_query.message.chat.id,
            text=f"Ошибка при загрузке профиля: {str(e)}",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("⬅️ Назад", callback_data="list_users"),
                InlineKeyboardButton("🏠 Домой", callback_data="home")
            )
        )
        user_main_messages[user_id] = {
            'chat_id': sent_message.chat.id,
            'message_id': sent_message.message_id,
            'state': None
        }
        await callback_query.answer("Ошибка на сервере.", show_alert=True)

@dp.callback_query_handler(lambda c: c.data == "list_users")
async def list_users_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins and user_id not in moderators:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    
    try:
        clients = db.get_client_list()
        if not clients:
            try:
                await bot.delete_message(
                    chat_id=callback_query.message.chat.id,
                    message_id=callback_query.message.message_id
                )
            except:
                pass
            sent_message = await bot.send_message(
                chat_id=callback_query.message.chat.id,
                text="Список клиентов пуст.",
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("🏠 Домой", callback_data="home")
                )
            )
            user_main_messages[user_id] = {
                'chat_id': sent_message.chat.id,
                'message_id': sent_message.message_id,
                'state': None
            }
            await callback_query.answer()
            return
        
        keyboard = InlineKeyboardMarkup(row_width=2)
        active_clients = {client[0]: client[1] for client in db.get_active_list()}
        for client in clients:
            username = client[0]
            last_handshake = active_clients.get(username)
            status = "❌" if not last_handshake or last_handshake.lower() in ['never', 'нет данных', '-'] else "🟢"
            button_text = f"{status} {username}"
            keyboard.insert(InlineKeyboardButton(button_text, callback_data=f"client_{username}"))
        
        keyboard.add(InlineKeyboardButton("🏠 Домой", callback_data="home"))
        
        try:
            await bot.delete_message(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id
            )
        except:
            pass
        sent_message = await bot.send_message(
            chat_id=callback_query.message.chat.id,
            text="Выберите пользователя:",
            reply_markup=keyboard
        )
        user_main_messages[user_id] = {
            'chat_id': sent_message.chat.id,
            'message_id': sent_message.message_id,
            'state': None
        }
        await callback_query.answer()
    
    except Exception as e:
        logger.error(f"Ошибка в list_users_callback: {str(e)}")
        sent_message = await bot.send_message(
            chat_id=callback_query.message.chat.id,
            text=f"Ошибка: {str(e)}",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🏠 Домой", callback_data="home")
            )
        )
        user_main_messages[user_id] = {
            'chat_id': sent_message.chat.id,
            'message_id': sent_message.message_id,
            'state': None
        }
        await callback_query.answer("Ошибка на сервере.", show_alert=True)

@dp.callback_query_handler(lambda c: c.data == "list_admins")
async def list_admins_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    keyboard = InlineKeyboardMarkup(row_width=2)
    for admin_id in admins:
        keyboard.insert(InlineKeyboardButton(f"🗑️ Удалить {admin_id}", callback_data=f"remove_admin_{admin_id}"))
    keyboard.add(InlineKeyboardButton("🏠 Домой", callback_data="home"))
    try:
        await bot.delete_message(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id
        )
    except:
        pass
    sent_message = await bot.send_message(
        chat_id=callback_query.message.chat.id,
        text=f"Администраторы:\n" + "\n".join(f"- {admin_id}" for admin_id in admins),
        reply_markup=keyboard
    )
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': None
    }
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data.startswith('remove_admin_'))
async def remove_admin_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    admin_id = int(callback_query.data.split('_')[2])
    if admin_id not in admins or len(admins) <= 1:
        await callback_query.answer("Нельзя удалить последнего админа или несуществующего.", show_alert=True)
        return
    db.remove_admin(admin_id)
    admins.remove(admin_id)
    await bot.send_message(admin_id, "Вы удалены из администраторов.")
    await list_admins_callback(callback_query)

@dp.callback_query_handler(lambda c: c.data.startswith('connections_'))
async def client_connections_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins and user_id not in moderators:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    username = callback_query.data.split('connections_')[1]
    file_path = os.path.join('files', 'connections', f'{username}_ip.json')
    if not os.path.exists(file_path):
        await callback_query.answer("Нет данных о подключениях.", show_alert=True)
        return
    
    async with aiofiles.open(file_path, 'r') as f:
        data = json.loads(await f.read())
    last_connections = sorted(data.items(), key=lambda x: datetime.strptime(x[1], '%d.%m.%Y %H:%M'), reverse=True)[:5]
    isp_results = await asyncio.gather(*(get_isp_info(ip) for ip, _ in last_connections))
    
    text = f"*Последние подключения {username}:*\n" + "\n".join(f"{ip} ({isp}) - {time}" for (ip, time), isp in zip(last_connections, isp_results))
    keyboard = InlineKeyboardMarkup(row_width=2).add(
        InlineKeyboardButton("⬅️ Назад", callback_data=f"client_{username}"),
        InlineKeyboardButton("🏠 Домой", callback_data="home")
    )
    try:
        await bot.delete_message(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id
        )
    except:
        pass
    sent_message = await bot.send_message(
        chat_id=callback_query.message.chat.id,
        text=text,
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': None
    }
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data.startswith('ip_info_'))
async def ip_info_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins and user_id not in moderators:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    username = callback_query.data.split('ip_info_')[1]
    active_info = next((ac for ac in db.get_active_list() if ac[0] == username), None)
    if not active_info:
        await callback_query.answer("Нет данных о подключении.", show_alert=True)
        return
    
    ip_address = active_info[3].split(':')[0]
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://ip-api.com/json/{ip_address}") as resp:
            data = await resp.json() if resp.status == 200 else {}
    
    text = f"*IP info {username}:*\n" + "\n".join(f"{k.capitalize()}: {v}" for k, v in data.items())
    keyboard = InlineKeyboardMarkup(row_width=2).add(
        InlineKeyboardButton("⬅️ Назад", callback_data=f"client_{username}"),
        InlineKeyboardButton("🏠 Домой", callback_data="home")
    )
    try:
        await bot.delete_message(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id
        )
    except:
        pass
    sent_message = await bot.send_message(
        chat_id=callback_query.message.chat.id,
        text=text,
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': None
    }
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data.startswith('delete_user_'))
async def client_delete_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins and user_id not in moderators:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    username = callback_query.data.split('delete_user_')[1]
    if db.deactive_user_db(username):
        shutil.rmtree(os.path.join('users', username), ignore_errors=True)
        db.remove_user_expiration(username)
        text = f"Пользователь **{username}** удален."
    else:
        text = f"Не удалось удалить **{username}**."
    try:
        await bot.delete_message(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id
        )
    except:
        pass
    sent_message = await bot.send_message(
        chat_id=callback_query.message.chat.id,
        text=text,
        parse_mode="Markdown",
        reply_markup=get_main_menu_markup(user_id)
    )
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': None
    }
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data.startswith('renew_user_'))
async def renew_user_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    username = callback_query.data.split('renew_user_')[1]
    try:
        await bot.delete_message(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id
        )
    except:
        pass
    sent_message = await bot.send_message(
        chat_id=callback_query.message.chat.id,
        text="Введите период продления (1_month, 3_months, 6_months):",
        reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🏠 Домой", callback_data="home"))
    )
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': 'waiting_for_renewal_period',
        'renewal_username': username
    }
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "home")
async def return_home(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    try:
        await bot.delete_message(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id
        )
    except:
        pass
    sent_message = await bot.send_message(
        chat_id=callback_query.message.chat.id,
        text="Выберите действие:",
        reply_markup=get_main_menu_markup(user_id)
    )
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': None
    }
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "get_config")
async def list_users_for_config(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins and user_id not in moderators:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    clients = db.get_client_list()
    if not clients:
        await callback_query.answer("Список пуст.", show_alert=True)
        return
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    for client in clients:
        keyboard.insert(InlineKeyboardButton(client[0], callback_data=f"send_config_{client[0]}"))
    keyboard.add(InlineKeyboardButton("🏠 Домой", callback_data="home"))
    try:
        await bot.delete_message(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id
        )
    except:
        pass
    sent_message = await bot.send_message(
        chat_id=callback_query.message.chat.id,
        text="Выберите пользователя:",
        reply_markup=keyboard
    )
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': None
    }
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data.startswith('send_config_'))
async def send_user_config(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins and user_id not in moderators:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    username = callback_query.data.split('send_config_')[1]
    conf_path = os.path.join('users', username, f'{username}.conf')
    if os.path.exists(conf_path):
        vpn_key = await generate_vpn_key(conf_path)
        caption = f"Конфигурация для {username}:\nAmneziaVPN:\n[Google Play](https://play.google.com/store/apps/details?id=org.amnezia.vpn&hl=ru)\n[GitHub](https://github.com/amnezia-vpn/amnezia-client)\n```\n{vpn_key}\n```"
        with open(conf_path, 'rb') as config:
            config_message = await bot.send_document(user_id, config, caption=caption, parse_mode="Markdown")
            await bot.pin_chat_message(user_id, config_message.message_id, disable_notification=True)
    else:
        await bot.send_message(user_id, f"Конфигурация для **{username}** не найдена.", parse_mode="Markdown")
    # Отправляем новое меню
    sent_message = await bot.send_message(
        chat_id=callback_query.message.chat.id,
        text="Выберите действие:",
        reply_markup=get_main_menu_markup(user_id)
    )
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': None
    }
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "create_backup")
async def create_backup_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    backup_filename = f"backup_{datetime.now().strftime('%Y-%m-%d')}.zip"
    with zipfile.ZipFile(backup_filename, 'w') as zipf:
        for file in ['awg-decode.py', 'newclient.sh', 'removeclient.sh']:
            if os.path.exists(file):
                zipf.write(file)
        for root, _, files in os.walk('files'):
            for file in files:
                zipf.write(os.path.join(root, file), os.path.relpath(os.path.join(root, file), os.getcwd()))
        for root, _, files in os.walk('users'):
            for file in files:
                zipf.write(os.path.join(root, file), os.path.relpath(os.path.join(root, file), os.getcwd()))
    with open(backup_filename, 'rb') as f:
        await bot.send_document(user_id, f, caption=backup_filename)
    os.remove(backup_filename)
    # Отправляем новое меню
    sent_message = await bot.send_message(
        chat_id=callback_query.message.chat.id,
        text="Выберите действие:",
        reply_markup=get_main_menu_markup(user_id)
    )
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': None
    }
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "instructions")
async def show_instructions(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins and user_id not in moderators:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    keyboard = InlineKeyboardMarkup(row_width=2).add(
        InlineKeyboardButton("📱 Для мобильных", callback_data="mobile_instructions"),
        InlineKeyboardButton("💻 Для компьютеров", callback_data="pc_instructions"),
        InlineKeyboardButton("🏠 Домой", callback_data="home")
    )
    try:
        await bot.delete_message(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id
        )
    except:
        pass
    sent_message = await bot.send_message(
        chat_id=callback_query.message.chat.id,
        text="Выберите тип устройства для инструкции:",
        reply_markup=keyboard
    )
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': None
    }
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "mobile_instructions")
async def mobile_instructions(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins and user_id not in moderators:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    instruction_text = (
        "📱 *Инструкция для мобильных устройств:*\n\n"
        "1. Скачайте приложение AmneziaVPN:\n"
        "   - [Google Play](https://play.google.com/store/apps/details?id=org.amnezia.vpn&hl=ru)\n"
        "   - Или через [GitHub](https://github.com/amnezia-vpn/amnezia-client)\n"
        "2. Откройте приложение и выберите 'Добавить конфигурацию'.\n"
        "3. Скопируйте VPN ключ из сообщения с файлом .conf.\n"
        "4. Вставьте ключ в приложение и нажмите 'Подключить'.\n"
        "5. Готово! Вы подключены к VPN."
    )
    keyboard = InlineKeyboardMarkup().add(
        InlineKeyboardButton("⬅️ Назад", callback_data="instructions"),
        InlineKeyboardButton("🏠 Домой", callback_data="home")
    )
    try:
        await bot.delete_message(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id
        )
    except:
        pass
    sent_message = await bot.send_message(
        chat_id=callback_query.message.chat.id,
        text=instruction_text,
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': None
    }
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "pc_instructions")
async def pc_instructions(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins and user_id not in moderators:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    instruction_text = (
        "💻 *Инструкция для компьютеров:*\n\n"
        "1. Скачайте клиент AmneziaVPN с [GitHub](https://github.com/amnezia-vpn/amnezia-client).\n"
        "2. Установите программу на ваш компьютер.\n"
        "3. Откройте AmneziaVPN и выберите 'Импорт конфигурации'.\n"
        "4. Укажите путь к скачанному файлу .conf.\n"
        "5. Нажмите 'Подключить' для активации VPN.\n"
        "6. Готово! VPN активен."
    )
    keyboard = InlineKeyboardMarkup().add(
        InlineKeyboardButton("⬅️ Назад", callback_data="instructions"),
        InlineKeyboardButton("🏠 Домой", callback_data="home")
    )
    try:
        await bot.delete_message(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id
        )
    except:
        pass
    sent_message = await bot.send_message(
        chat_id=callback_query.message.chat.id,
        text=instruction_text,
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': None
    }
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "buy_key")
async def buy_key_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    keyboard = InlineKeyboardMarkup(row_width=2).add(
        InlineKeyboardButton("1 месяц - $10", callback_data="select_period_1_month"),
        InlineKeyboardButton("3 месяца - $25", callback_data="select_period_3_months"),
        InlineKeyboardButton("6 месяцев - $45", callback_data="select_period_6_months"),
        InlineKeyboardButton("🏠 Домой", callback_data="home")
    )
    try:
        await bot.delete_message(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id
        )
    except:
        pass
    sent_message = await bot.send_message(
        chat_id=callback_query.message.chat.id,
        text="Выберите период подписки:",
        reply_markup=keyboard
    )
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': None
    }
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data.startswith('select_period_'))
async def select_period_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    period = callback_query.data.split('select_period_')[1]
    price = PRICING[period]
    discount = user_main_messages.get(user_id, {}).get('promocode_discount', 0)
    final_price = price * (1 - discount / 100)
    
    payment_id = str(uuid.uuid4())
    db.add_payment(user_id, payment_id, final_price, 'pending')
    
    payment_url = f"https://example.com/pay/{payment_id}"  # Замените на реальный URL платежной системы
    
    keyboard = InlineKeyboardMarkup().add(
        InlineKeyboardButton("💳 Оплатить", url=payment_url),
        InlineKeyboardButton("⬅️ Назад", callback_data="buy_key"),
        InlineKeyboardButton("🏠 Домой", callback_data="home")
    )
    try:
        await bot.delete_message(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id
        )
    except:
        pass
    sent_message = await bot.send_message(
        chat_id=callback_query.message.chat.id,
        text=f"Подписка на {period.replace('_', ' ')}: ${final_price:.2f} (скидка {discount}%)\nОплатите по ссылке:",
        reply_markup=keyboard
    )
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': None,
        'pending_payment': {
            'payment_id': payment_id,
            'period': period,
            'username': None
        }
    }
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "use_promocode")
async def use_promocode_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    try:
        await bot.delete_message(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id
        )
    except:
        pass
    sent_message = await bot.send_message(
        chat_id=callback_query.message.chat.id,
        text="Введите промокод:",
        reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🏠 Домой", callback_data="home"))
    )
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': 'waiting_for_promocode'
    }
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "manage_promocodes")
async def manage_promocodes_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    promocodes = db.get_promocodes()
    text = "Промокоды:\n" + "\n".join(
        f"{code}: {info['discount']}% (использовано {info['uses']}/{info['max_uses'] or '∞'}, до {info['expires_at'] or 'неограничено'})"
        for code, info in promocodes.items()
    ) if promocodes else "Промокоды отсутствуют."
    keyboard = InlineKeyboardMarkup(row_width=2).add(
        InlineKeyboardButton("➕ Добавить промокод", callback_data="add_promocode"),
        InlineKeyboardButton("🗑️ Удалить промокод", callback_data="delete_promocode"),
        InlineKeyboardButton("🏠 Домой", callback_data="home")
    )
    try:
        await bot.delete_message(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id
        )
    except:
        pass
    sent_message = await bot.send_message(
        chat_id=callback_query.message.chat.id,
        text=text,
        reply_markup=keyboard
    )
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': None
    }
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "add_promocode")
async def add_promocode_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    try:
        await bot.delete_message(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id
        )
    except:
        pass
    sent_message = await bot.send_message(
        chat_id=callback_query.message.chat.id,
        text="Введите промокод в формате: <код> <скидка%> <дней_действия> <макс_использований|none>",
        reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🏠 Домой", callback_data="home"))
    )
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': 'waiting_for_new_promocode'
    }
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "delete_promocode")
async def delete_promocode_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    promocodes = db.get_promocodes()
    keyboard = InlineKeyboardMarkup(row_width=2)
    for code in promocodes:
        keyboard.insert(InlineKeyboardButton(f"🗑️ {code}", callback_data=f"remove_promocode_{code}"))
    keyboard.add(InlineKeyboardButton("🏠 Домой", callback_data="home"))
    try:
        await bot.delete_message(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id
        )
    except:
        pass
    sent_message = await bot.send_message(
        chat_id=callback_query.message.chat.id,
        text="Выберите промокод для удаления:",
        reply_markup=keyboard
    )
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': None
    }
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data.startswith('remove_promocode_'))
async def remove_promocode_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    code = callback_query.data.split('remove_promocode_')[1]
    if db.remove_promocode(code):
        await callback_query.answer(f"Промокод {code} удалён.", show_alert=True)
    else:
        await callback_query.answer(f"Промокод {code} не найден.", show_alert=True)
    await manage_promocodes_callback(callback_query)

@dp.callback_query_handler(lambda c: c.data == "check_updates")
async def check_updates_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    try:
        process = await asyncio.create_subprocess_exec(
            '/root/install.sh', '--check-update',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        output = stdout.decode().strip() + stderr.decode().strip()
        if "Репозиторий актуален" in output:
            await bot.send_message(user_id, "Репозиторий актуален, обновления не требуются.", parse_mode="Markdown")
        elif "Обновление репозитория... Done!" in output:
            await bot.send_message(user_id, "Репозиторий успешно обновлён и служба перезапущена.", parse_mode="Markdown")
        else:
            await bot.send_message(user_id, f"Ошибка проверки обновлений:\n```\n{output}\n```", parse_mode="Markdown")
    except Exception as e:
        await bot.send_message(user_id, f"Ошибка при проверке обновлений: {str(e)}")
    # Отправляем новое меню
    sent_message = await bot.send_message(
        chat_id=callback_query.message.chat.id,
        text="Выберите действие:",
        reply_markup=get_main_menu_markup(user_id)
    )
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': None
    }
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "restart_vpn")
async def restart_vpn_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    try:
        # Проверяем, существует ли контейнер
        process = await asyncio.create_subprocess_exec(
            'docker', 'ps', '-q', '-f', f'name={DOCKER_CONTAINER}',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        if not stdout.decode().strip():
            await bot.send_message(user_id, f"Контейнер {DOCKER_CONTAINER} не найден.", parse_mode="Markdown")
        else:
            # Перезапускаем контейнер
            process = await asyncio.create_subprocess_exec(
                'docker', 'restart', DOCKER_CONTAINER,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            if process.returncode == 0:
                await bot.send_message(user_id, f"VPN-контейнер {DOCKER_CONTAINER} успешно перезапущен.", parse_mode="Markdown")
            else:
                await bot.send_message(user_id, f"Ошибка при перезапуске VPN:\n```\n{stderr.decode().strip()}\n```", parse_mode="Markdown")
    except Exception as e:
        await bot.send_message(user_id, f"Ошибка при перезапуске VPN: {str(e)}")
    # Отправляем новое меню
    sent_message = await bot.send_message(
        chat_id=callback_query.message.chat.id,
        text="Выберите действие:",
        reply_markup=get_main_menu_markup(user_id)
    )
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': None
    }
    await callback_query.answer()

async def check_payment_status():
    payments = db.get_pending_payments()
    for user_id, payment_id, amount, _ in payments:
        # Здесь должна быть интеграция с платежной системой для проверки статуса
        # Для примера предполагаем, что статус изменился на 'completed'
        payment_status = 'completed'  # Замените на реальную проверку
        if payment_status == 'completed':
            db.update_payment_status(payment_id, 'completed')
            pending_payment = user_main_messages.get(user_id, {}).get('pending_payment', {})
            if pending_payment and pending_payment['payment_id'] == payment_id:
                period = pending_payment['period']
                username = f"user_{user_id}_{uuid.uuid4().hex[:8]}"
                success = db.root_add(username, ipv6=False)
                if success:
                    months = {'1_month': 1, '3_months': 3, '6_months': 6}[period]
                    expiration = datetime.now(pytz.UTC) + timedelta(days=30 * months)
                    db.set_user_expiration(username, expiration, "Неограниченно")
                    # Сохраняем Telegram ID пользователя в базе
                    # Если db.set_user_telegram_id не существует, добавьте метод в db.py
                    db.set_user_telegram_id(username, user_id)  # Раскомментируйте, если метод есть
                    conf_path = os.path.join('users', username, f'{username}.conf')
                    if os.path.exists(conf_path):
                        vpn_key = await generate_vpn_key(conf_path)
                        caption = f"Ваш VPN ключ ({period.replace('_', ' ')}):\nAmneziaVPN:\n[Google Play](https://play.google.com/store/apps/details?id=org.amnezia.vpn&hl=ru)\n[GitHub](https://github.com/amnezia-vpn/amnezia-client)\n```\n{vpn_key}\n```"
                        with open(conf_path, 'rb') as config:
                            config_message = await bot.send_document(user_id, config, caption=caption, parse_mode="Markdown")
                            await bot.pin_chat_message(user_id, config_message.message_id, disable_notification=True)
                        await bot.send_message(user_id, "Оплата подтверждена! Ваш VPN ключ отправлен.")
                    else:
                        await bot.send_message(user_id, "Ошибка: конфигурация не найдена. Обратитесь к администратору.")
                else:
                    await bot.send_message(user_id, "Ошибка при создании пользователя. Обратитесь к администратору.")
                user_main_messages[user_id].pop('pending_payment', None)

if __name__ == '__main__':
    import asyncio
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(load_isp_cache())
        scheduler.add_job(check_payment_status, IntervalTrigger(minutes=5))
        executor.start_polling(dp, skip_updates=True, loop=loop)
    finally:
        loop.close()
