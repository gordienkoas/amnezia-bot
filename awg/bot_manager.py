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
from yoomoney import Client, Quickpay

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
yoomoney_token = setting.get('yoomoney_token')
yoomoney_wallet = setting.get('yoomoney_wallet')
pricing = setting.get('pricing', {
    '1_month': 1000.0,
    '3_months': 2500.0,
    '6_months': 4500.0,
    '12_months': 8000.0
})

if not all([bot_token, admin_ids, wg_config_file, docker_container, endpoint]):
    logger.error("Некоторые обязательные настройки отсутствуют.")
    sys.exit(1)

if not all([yoomoney_token, yoomoney_wallet]):
    logger.warning("Настройки YooMoney отсутствуют. Установите их через бот.")

admins = [int(admin_id) for admin_id in admin_ids]
moderators = [int(mod_id) for mod_id in moderator_ids]
bot = Bot(bot_token)
WG_CONFIG_FILE = wg_config_file
DOCKER_CONTAINER = docker_container
ENDPOINT = endpoint
yoomoney_client = Client(yoomoney_token) if yoomoney_token else None
PRICING = pricing

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
            InlineKeyboardButton("🎟️ Управление промокодами", callback_data="manage_promocodes"),
            InlineKeyboardButton("⚙️ Настройки", callback_data="settings")
        )
        markup.add(InlineKeyboardButton("🏠 Домой", callback_data="home"))
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

# Меню настроек
def get_settings_menu():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🔄 Проверить обновления", callback_data="check_updates"),
        InlineKeyboardButton("🔄 Перезагрузить VPN", callback_data="restart_vpn")
    )
    markup.add(
        InlineKeyboardButton("🗑️ Очистить старые ключи", callback_data="clear_old_keys"),
        InlineKeyboardButton("💾 Создать бэкап", callback_data="create_backup")
    )
    markup.add(
        InlineKeyboardButton("👥 Список админов", callback_data="list_admins"),
        InlineKeyboardButton("👤 Добавить админа", callback_data="add_admin")
    )
    markup.add(
        InlineKeyboardButton("💸 Настройки YooMoney", callback_data="yoomoney_settings"),
        InlineKeyboardButton("💰 Настройки цен", callback_data="pricing_settings")
    )
    markup.add(InlineKeyboardButton("⬅️ Назад", callback_data="home"))
    return markup

# Меню настроек YooMoney
def get_yoomoney_settings_menu():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🔑 Установить токен", callback_data="set_yoomoney_token"),
        InlineKeyboardButton("💼 Установить кошелёк", callback_data="set_yoomoney_wallet")
    )
    markup.add(InlineKeyboardButton("⬅️ Назад", callback_data="settings"))
    return markup

# Меню настроек цен
def get_pricing_settings_menu():
    markup = InlineKeyboardMarkup(row_width=2)
    periods = [
        ("1 месяц", "1_month"),
        ("3 месяца", "3_months"),
        ("6 месяцев", "6_months"),
        ("12 месяцев", "12_months")
    ]
    for period_name, period_key in periods:
        markup.add(InlineKeyboardButton(
            f"{period_name} - ₽{PRICING.get(period_key, 0):.2f}",
            callback_data=f"set_price_{period_key}"
        ))
    markup.add(InlineKeyboardButton("⬅️ Назад", callback_data="settings"))
    return markup

# Клавиатура для выбора периода продления
def get_renewal_period_keyboard(username):
    markup = InlineKeyboardMarkup(row_width=2)
    periods = [
        ("1 месяц", "1_month"),
        ("3 месяца", "3_months"),
        ("6 месяцев", "6_months"),
        ("12 месяцев", "12_months")
    ]
    for period_name, period_key in periods:
        markup.add(InlineKeyboardButton(period_name, callback_data=f"renew_period_{username}_{period_key}"))
    markup.add(InlineKeyboardButton("Отмена", callback_data="home"))
    return markup

# Клавиатура для выбора даты очистки ключей
def get_clear_keys_date_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    dates = [
        ("1 месяц назад", (datetime.now(pytz.UTC) - timedelta(days=30)).isoformat()),
        ("3 месяца назад", (datetime.now(pytz.UTC) - timedelta(days=90)).isoformat()),
        ("6 месяцев назад", (datetime.now(pytz.UTC) - timedelta(days=180)).isoformat()),
        ("1 год назад", (datetime.now(pytz.UTC) - timedelta(days=365)).isoformat())
    ]
    for date_name, date_iso in dates:
        markup.add(InlineKeyboardButton(date_name, callback_data=f"clear_keys_date_{date_iso}"))
    markup.add(InlineKeyboardButton("Отмена", callback_data="home"))
    return markup

user_main_messages = {}
isp_cache = {}
ISP_CACHE_FILE = 'files/isp_cache.json'
CACHE_TTL = 24 * 3600

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
    process = await asyncio.create_subprocess_exec(
        'python3.11', '/root/amnezia-bot/awg/awg-decode.py', '--encode', conf_path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    if process.returncode == 0 and stdout.decode().startswith('vpn://'):
        return stdout.decode().strip()
    else:
        logger.error(f"Ошибка генерации vpn://: {stderr.decode()}")
        return ""

async def issue_vpn_key(user_id: int, period: str) -> bool:
    username = f"user_{user_id}_{uuid.uuid4().hex[:8]}"
    success = db.root_add(username, ipv6=False)
    if success:
        months = {'1_month': 1, '3_months': 3, '6_months': 6, '12_months': 12}[period]
        expiration = datetime.now(pytz.UTC) + timedelta(days=30 * months)
        db.set_user_expiration(username, expiration, "Неограниченно")
        db.set_user_telegram_id(username, user_id)
        conf_path = os.path.join('users', username, f'{username}.conf')
        if os.path.exists(conf_path):
            vpn_key = await generate_vpn_key(conf_path)
            caption = f"Ваш VPN ключ ({period.replace('_', ' ')}):\nAmneziaVPN:\n[Google Play](https://play.google.com/store/apps/details?id=org.amnezia.vpn&hl=ru)\n[GitHub](https://github.com/amnezia-vpn/amnezia-client)\n```\n{vpn_key}\n```"
            with open(conf_path, 'rb') as config:
                config_message = await bot.send_document(user_id, config, caption=caption, parse_mode="Markdown")
                await bot.pin_chat_message(user_id, config_message.message_id, disable_notification=True)
            return True
    return False

@dp.message_handler(commands=['start', 'help'])
async def start_command_handler(message: types.Message):
    user_id = message.from_user.id
    if user_id in user_main_messages:
        try:
            await bot.delete_message(
                chat_id=user_main_messages[user_id]['chat_id'],
                message_id=user_main_messages[user_id]['message_id']
            )
        except:
            pass
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
    global PRICING  # Объявляем PRICING глобальной в начале функции
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
        promocode_data = db.apply_promocode(promocode)
        if promocode_data:
            discount = promocode_data.get('discount', 0)
            subscription_period = promocode_data.get('subscription_period')
            if subscription_period:
                success = await issue_vpn_key(user_id, subscription_period)
                if success:
                    await message.reply(f"Промокод активирован! VPN ключ на {subscription_period.replace('_', ' ')} выдан.")
                else:
                    await message.reply("Ошибка при выдаче ключа. Обратитесь к администратору.")
            else:
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
            parts = message.text.strip().split()
            if len(parts) != 5:
                raise ValueError("Неверный формат")
            code, discount, days_valid, max_uses, subscription_period = parts
            discount = float(discount)
            days_valid = int(days_valid)
            max_uses = int(max_uses) if max_uses.lower() != 'none' else None
            if subscription_period not in PRICING and subscription_period.lower() != 'none':
                raise ValueError("Неверный период подписки")
            subscription_period = None if subscription_period.lower() == 'none' else subscription_period
            expires_at = datetime.now(pytz.UTC) + timedelta(days=days_valid) if days_valid > 0 else None
            if db.add_promocode(code, discount, expires_at, max_uses, subscription_period):
                await message.reply(
                    f"Промокод {code} добавлен: скидка {discount}%, действует {days_valid} дней, "
                    f"макс. использований: {max_uses or 'неограничено'}, подписка: {subscription_period or 'нет'}"
                )
            else:
                await message.reply("Промокод уже существует.")
        except:
            await message.reply(
                "Формат: <код> <скидка%> <дней_действия> <макс_использований|none> <период_подписки|none>"
            )
        sent_message = await message.answer("Выберите действие:", reply_markup=get_main_menu_markup(user_id))
        user_main_messages[user_id] = {
            'chat_id': sent_message.chat.id,
            'message_id': sent_message.message_id,
            'state': None
        }
    elif user_state == 'waiting_for_yoomoney_token' and user_id in admins:
        token = message.text.strip()
        db.set_yoomoney_config(token=token)
        global yoomoney_client, yoomoney_token
        yoomoney_token = token
        yoomoney_client = Client(yoomoney_token)
        await message.reply("Токен YooMoney успешно обновлён.")
        sent_message = await message.answer("Настройки YooMoney:", reply_markup=get_yoomoney_settings_menu())
        user_main_messages[user_id] = {
            'chat_id': sent_message.chat.id,
            'message_id': sent_message.message_id,
            'state': None
        }
    elif user_state == 'waiting_for_yoomoney_wallet' and user_id in admins:
        wallet = message.text.strip()
        if not re.match(r'^\d{15,18}$', wallet):
            await message.reply("Введите корректный номер кошелька YooMoney (15-18 цифр).")
            return
        db.set_yoomoney_config(wallet=wallet)
        global yoomoney_wallet
        yoomoney_wallet = wallet
        await message.reply("Номер кошелька YooMoney успешно обновлён.")
        sent_message = await message.answer("Настройки YooMoney:", reply_markup=get_yoomoney_settings_menu())
        user_main_messages[user_id] = {
            'chat_id': sent_message.chat.id,
            'message_id': sent_message.message_id,
            'state': None
        }
    elif user_state.startswith('waiting_for_price_') and user_id in admins:
        period = user_state.split('waiting_for_price_')[1]
        try:
            price = float(message.text.strip())
            if price <= 0:
                raise ValueError("Цена должна быть положительной.")
            db.set_pricing(period, price)
            PRICING[period] = price
            await message.reply(f"Цена для {period.replace('_', ' ')} обновлена: ₽{price:.2f}")
        except:
            await message.reply("Введите корректное число (например, 1000.00).")
            return
        sent_message = await message.answer("Настройки цен:", reply_markup=get_pricing_settings_menu())
        user_main_messages[user_id] = {
            'chat_id': sent_message.chat.id,
            'message_id': sent_message.message_id,
            'state': None
        }

@dp.callback_query_handler(lambda c: c.data == "settings")
async def settings_menu_callback(callback_query: types.CallbackQuery):
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
        text="Настройки:",
        reply_markup=get_settings_menu()
    )
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': None
    }
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "yoomoney_settings")
async def yoomoney_settings_callback(callback_query: types.CallbackQuery):
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
        text="Настройки YooMoney:",
        reply_markup=get_yoomoney_settings_menu()
    )
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': None
    }
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "set_yoomoney_token")
async def set_yoomoney_token_callback(callback_query: types.CallbackQuery):
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
        text="Введите токен YooMoney:",
        reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ Назад", callback_data="yoomoney_settings"))
    )
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': 'waiting_for_yoomoney_token'
    }
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "set_yoomoney_wallet")
async def set_yoomoney_wallet_callback(callback_query: types.CallbackQuery):
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
        text="Введите номер кошелька YooMoney (15-18 цифр):",
        reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ Назад", callback_data="yoomoney_settings"))
    )
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': 'waiting_for_yoomoney_wallet'
    }
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "pricing_settings")
async def pricing_settings_callback(callback_query: types.CallbackQuery):
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
        text="Настройки цен:",
        reply_markup=get_pricing_settings_menu()
    )
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': None
    }
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data.startswith('set_price_'))
async def set_price_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    period = callback_query.data.split('set_price_')[1]
    try:
        await bot.delete_message(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id
        )
    except:
        pass
    sent_message = await bot.send_message(
        chat_id=callback_query.message.chat.id,
        text=f"Введите новую цену для {period.replace('_', ' ')} в рублях (например, 1000.00):",
        reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ Назад", callback_data="pricing_settings"))
    )
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': f'waiting_for_price_{period}'
    }
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "clear_old_keys")
async def clear_old_keys_callback(callback_query: types.CallbackQuery):
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
        text="Выберите дату, до которой удалить ключи:",
        reply_markup=get_clear_keys_date_keyboard()
    )
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': None
    }
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data.startswith('clear_keys_date_'))
async def clear_keys_date_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    before_date = callback_query.data.split('clear_keys_date_')[1]
    try:
        if db.clear_old_keys(before_date):
            await bot.send_message(user_id, f"Старые ключи до {before_date} удалены.", parse_mode="Markdown")
        else:
            await bot.send_message(user_id, "Не найдено ключей для удаления.", parse_mode="Markdown")
    except Exception as e:
        await bot.send_message(user_id, f"Ошибка при удалении ключей: {str(e)}")
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

@dp.callback_query_handler(lambda c: c.data == "add_user")
async def prompt_for_user_name(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins and user_id not in moderators:
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
        reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("Отмена", callback_data="home"))
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
        telegram_id = db.get_user_telegram_id(username) or "Не указан"

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
    keyboard.add(InlineKeyboardButton("⬅️ Назад", callback_data="settings"))
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
    try:
        if db.deactive_user_db(username):
            shutil.rmtree(os.path.join('users', username), ignore_errors=True)
            db.remove_user_expiration(username)
            db.set_user_telegram_id(username, None)
            logger.info(f"Пользователь {username} успешно удалён.")
            text = f"Пользователь **{username}** удалён."
        else:
            logger.error(f"Не удалось удалить пользователя {username} через db.deactive_user_db.")
            text = f"Не удалось удалить **{username}**. Проверьте логи."
    except Exception as e:
        logger.error(f"Ошибка при удалении пользователя {username}: {str(e)}")
        text = f"Ошибка при удалении **{username}**: {str(e)}"
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
        text="Выберите период продления:",
        reply_markup=get_renewal_period_keyboard(username)
    )
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': None
    }
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data.startswith('renew_period_'))
async def renew_period_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    try:
        username, period = callback_query.data.split('renew_period_')[1].split('_', 1)
        months = {'1_month': 1, '3_months': 3, '6_months': 6, '12_months': 12}[period]
        expiration = datetime.now(pytz.UTC) + timedelta(days=30 * months)
        db.set_user_expiration(username, expiration, "Неограниченно")
        text = f"Подписка для {username} продлена до {expiration.strftime('%Y-%m-%d %H:%M UTC')}."
        logger.info(f"Подписка для {username} продлена на {period} до {expiration}.")
    except Exception as e:
        text = f"Ошибка при продлении: {str(e)}"
        logger.error(f"Ошибка при продлении подписки для {username}: {str(e)}")
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
    keyboard = InlineKeyboardMarkup(row_width=2)
    for period, price in PRICING.items():
        keyboard.add(InlineKeyboardButton(
            f"{period.replace('_', ' ')} - ₽{price:.2f}",
            callback_data=f"select_period_{period}"
        ))
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
    
    if not yoomoney_client or not yoomoney_wallet:
        await callback_query.answer("Платежи недоступны. Обратитесь к администратору.", show_alert=True)
        return
    
    payment_id = str(uuid.uuid4())
    db.add_payment(user_id, payment_id, final_price, 'pending')
    
    quickpay = Quickpay(
        receiver=yoomoney_wallet,
        quickpay_form="shop",
        targets=f"VPN Subscription {period}",
        paymentType="SB",
        sum=final_price,
        label=payment_id
    )
    payment_url = quickpay.redirected_url
    
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
        text=f"Подписка на {period.replace('_', ' ')}: ₽{final_price:.2f} (скидка {discount}%)\nОплатите по ссылке:",
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
        f"{code}: {info['discount']}% (использовано {info['uses']}/{info['max_uses'] or '∞'}, до {info['expires_at'] or 'неограничено'}, подписка: {info['subscription_period'] or 'нет'})"
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
        text="Введите промокод в формате: <код> <скидка%> <дней_действия> <макс_использований|none> <период_подписки|none>",
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
        process = await asyncio.create_subprocess_exec(
            'docker', 'ps', '-q', '-f', f'name={DOCKER_CONTAINER}',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        if not stdout.decode().strip():
            await bot.send_message(user_id, f"Контейнер {DOCKER_CONTAINER} не найден.", parse_mode="Markdown")
        else:
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
        try:
            if yoomoney_client:
                operation = yoomoney_client.operation_history(label=payment_id)
                for op in operation.operations:
                    if op.label == payment_id and op.status == "success":
                        db.update_payment_status(payment_id, 'completed')
                        pending_payment = user_main_messages.get(user_id, {}).get('pending_payment', {})
                        if pending_payment and pending_payment['payment_id'] == payment_id:
                            period = pending_payment['period']
                            success = await issue_vpn_key(user_id, period)
                            if success:
                                await bot.send_message(user_id, "Оплата подтверждена! Ваш VPN ключ отправлен.")
                            else:
                                await bot.send_message(user_id, "Ошибка при создании пользователя. Обратитесь к администратору.")
                            user_main_messages[user_id].pop('pending_payment', None)
                        break
        except Exception as e:
            logger.error(f"Ошибка проверки платежа {payment_id}: {str(e)}")

if __name__ == '__main__':
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(load_isp_cache())
        scheduler.add_job(check_payment_status, IntervalTrigger(minutes=5))
        executor.start_polling(dp, skip_updates=True, loop=loop)
    finally:
        loop.close()
