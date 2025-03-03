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
from aiogram import Bot, types
from aiogram.dispatcher import Dispatcher
from aiogram.dispatcher.middlewares import BaseMiddleware
from aiogram.utils import executor
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime
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

class AdminMessageDeletionMiddleware(BaseMiddleware):
    async def on_process_message(self, message: types.Message, data: dict):
        if message.from_user.id in admins and message.text.startswith('/'):
            asyncio.create_task(delete_message_after_delay(message.chat.id, message.message_id))

dp = Dispatcher(bot)
scheduler = AsyncIOScheduler(timezone=pytz.UTC)
scheduler.start()
dp.middleware.setup(AdminMessageDeletionMiddleware())

# Главное меню с новым порядком и эмодзи
def get_main_menu_markup(user_id):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("➕ Добавить пользователя", callback_data="add_user"))
    markup.insert(InlineKeyboardButton("📋 Список клиентов", callback_data="list_users"))
    markup.insert(InlineKeyboardButton("🔑 Получить конфиг", callback_data="get_config"))
    if user_id in admins:
        markup.insert(InlineKeyboardButton("👥 Список админов", callback_data="list_admins"))
        markup.insert(InlineKeyboardButton("👤 Добавить админа", callback_data="add_admin"))
    if user_id in admins:
        markup.add(InlineKeyboardButton("💾 Создать бекап", callback_data="create_backup"))
    markup.add(InlineKeyboardButton("ℹ️ Инструкция", callback_data="instructions"))
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

@dp.message_handler(commands=['start', 'help'])
async def help_command_handler(message: types.Message):
    user_id = message.from_user.id
    if user_id in admins or user_id in moderators:
        sent_message = await message.answer("Выберите действие:", reply_markup=get_main_menu_markup(user_id))
        user_main_messages[user_id] = {'chat_id': sent_message.chat.id, 'message_id': sent_message.message_id}
        # Не закрепляем меню
    else:
        await message.answer("У вас нет доступа к этому боту.")

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
    if user_id not in admins and user_id not in moderators:
        await message.answer("У вас нет доступа.")
        return
    
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
                    # Отправляем конфиг отдельным сообщением и закрепляем его
                    config_message = await bot.send_document(user_id, config, caption=caption, parse_mode="Markdown")
                    await bot.pin_chat_message(user_id, config_message.message_id, disable_notification=True)
        # Обновляем меню внизу, не закрепляя его
        await bot.edit_message_text(
            chat_id=user_main_messages[user_id]['chat_id'],
            message_id=user_main_messages[user_id]['message_id'],
            text="Выберите действие:",
            reply_markup=get_main_menu_markup(user_id)
        )
        user_main_messages[user_id]['state'] = None
    elif user_state == 'waiting_for_admin_id' and user_id in admins:
        try:
            new_admin_id = int(message.text.strip())
            if new_admin_id not in admins:
                db.add_admin(new_admin_id)
                admins.append(new_admin_id)
                await message.reply(f"Админ {new_admin_id} добавлен.")
                await bot.send_message(new_admin_id, "Вы назначены администратором!")
            await bot.edit_message_text(
                chat_id=user_main_messages[user_id]['chat_id'],
                message_id=user_main_messages[user_id]['message_id'],
                text="Выберите действие:",
                reply_markup=get_main_menu_markup(user_id)
            )
            user_main_messages[user_id]['state'] = None
        except:
            await message.reply("Введите корректный Telegram ID.")

@dp.callback_query_handler(lambda c: c.data == "add_user")
async def prompt_for_user_name(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins and user_id not in moderators:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text="Введите имя пользователя:",
        reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🏠 Домой", callback_data="home"))
    )
    user_main_messages[user_id]['state'] = 'waiting_for_user_name'
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "add_admin")
async def prompt_for_admin_id(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text="Введите Telegram ID нового админа:",
        reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🏠 Домой", callback_data="home"))
    )
    user_main_messages[user_id]['state'] = 'waiting_for_admin_id'
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data.startswith('client_'))
async def client_selected_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins and user_id not in moderators:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    username = callback_query.data.split('client_')[1]
    clients = db.get_client_list()
    client_info = next((c for c in clients if c[0] == username), None)
    if not client_info:
        await callback_query.answer("Пользователь не найден.", show_alert=True)
        return
    
    status = "🔴 Офлайн"
    incoming_traffic = "↓—"
    outgoing_traffic = "↑—"
    ipv4_address = re.search(r'(\d{1,3}\.){3}\d{1,3}/\d+', client_info[2]) or "—"
    active_clients = db.get_active_list()
    active_info = next((ac for ac in active_clients if ac[0] == username), None)
    
    if active_info and active_info[1].lower() not in ['never', 'нет данных', '-']:
        last_handshake = parse_relative_time(active_info[1])
        status = "🟢 Онлайн" if (datetime.now(pytz.UTC) - last_handshake).total_seconds() <= 60 else "❌ Офлайн"
        incoming_bytes, outgoing_bytes = parse_transfer(active_info[2])
        incoming_traffic = f"↓{humanize.naturalsize(incoming_bytes)}"
        outgoing_traffic = f"↑{humanize.naturalsize(outgoing_bytes)}"
    
    text = (
        f"📧 *Имя:* {username}\n"
        f"🌐 *IPv4:* {ipv4_address}\n"
        f"🌐 *Статус:* {status}\n"
        f"🔼 *Исходящий:* {incoming_traffic}\n"
        f"🔽 *Входящий:* {outgoing_traffic}"
    )
    keyboard = InlineKeyboardMarkup(row_width=2).add(
        InlineKeyboardButton("ℹ️ IP info", callback_data=f"ip_info_{username}"),
        InlineKeyboardButton("🔗 Подключения", callback_data=f"connections_{username}"),
        InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_user_{username}"),
        InlineKeyboardButton("⬅️ Назад", callback_data="list_users"),
        InlineKeyboardButton("🏠 Домой", callback_data="home")
    )
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text=text,
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "list_users")
async def list_users_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins and user_id not in moderators:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    clients = db.get_client_list()
    if not clients:
        await callback_query.answer("Список пуст.", show_alert=True)
        return
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    active_clients = {client[0]: client[1] for client in db.get_active_list()}
    now = datetime.now(pytz.UTC)
    for client in clients:
        username = client[0]
        last_handshake = active_clients.get(username)
        status = "❌" if not last_handshake or last_handshake.lower() in ['never', 'нет данных', '-'] else "🟢" if (now - parse_relative_time(last_handshake)).days <= 5 else "❌"
        keyboard.insert(InlineKeyboardButton(f"{status} {username}", callback_data=f"client_{username}"))
    keyboard.add(InlineKeyboardButton("🏠 Домой", callback_data="home"))
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text="Выберите пользователя:",
        reply_markup=keyboard
    )
    await callback_query.answer()

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
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text=f"Администраторы:\n" + "\n".join(f"- {admin_id}" for admin_id in admins),
        reply_markup=keyboard
    )
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
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text=text,
        parse_mode="Markdown",
        reply_markup=keyboard
    )
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
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text=text,
        parse_mode="Markdown",
        reply_markup=keyboard
    )
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
        text = f"Пользователь **{username}** удален."
    else:
        text = f"Не удалось удалить **{username}**."
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text=text,
        parse_mode="Markdown",
        reply_markup=get_main_menu_markup(user_id)
    )
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "home")
async def return_home(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins and user_id not in moderators:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    user_main_messages[user_id]['state'] = None
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text="Выберите действие:",
        reply_markup=get_main_menu_markup(user_id)
    )
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
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text="Выберите пользователя:",
        reply_markup=keyboard
    )
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
            # Отправляем конфиг отдельным сообщением и закрепляем его
            config_message = await bot.send_document(user_id, config, caption=caption, parse_mode="Markdown")
            await bot.pin_chat_message(user_id, config_message.message_id, disable_notification=True)
    else:
        await bot.send_message(user_id, f"Конфигурация для **{username}** не найдена.", parse_mode="Markdown")
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
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text="Выберите тип устройства для инструкции:",
        reply_markup=keyboard
    )
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "mobile_instructions")
async def mobile_instructions(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins and user_id not in moderators:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    instruction_text = (
        "📱 *Инструкция для мобильных устройств:*\n\n"
        "1.1. Скачайте приложение AmneziaVPN (Android}:\n"
        "   - [Google Play](https://play.google.com/store/apps/details?id=org.amnezia.vpn&hl=ru)\n"
        "   - Или через [GitHub](https://github.com/amnezia-vpn/amnezia-client)\n"
        "1.2. Скачайте приложение AmneziaWG (iOS):\n"
        "   - [App Store](https://apps.apple.com/ru/app/amneziawg/id6478942365)\n"
        "   - Или если у вас регион не Россия то [AmneziaVPN](https://apps.apple.com/kz/app/amneziavpn/id1600529900)"
        "2. Откройте приложение и выберите 'Добавить конфигурацию'.\n"
        "3. Скопируйте VPN ключ из сообщения с файлом .conf.\n"
        "4. Вставьте ключ в приложение и нажмите 'Подключить'.\n"
        "5. Готово! Вы подключены к VPN."
    )
    keyboard = InlineKeyboardMarkup().add(
        InlineKeyboardButton("⬅️ Назад", callback_data="instructions"),
        InlineKeyboardButton("🏠 Домой", callback_data="home")
    )
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text=instruction_text,
        parse_mode="Markdown",
        reply_markup=keyboard
    )
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
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text=instruction_text,
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    await callback_query.answer()

def parse_transfer(transfer_str):
    incoming, outgoing = re.split(r'[/,]', transfer_str)[:2]
    size_map = {'B': 1, 'KB': 10**3, 'KiB': 1024, 'MB': 10**6, 'MiB': 1024**2, 'GB': 10**9, 'GiB': 1024**3}
    for unit, multiplier in size_map.items():
        if unit in incoming:
            incoming_bytes = float(re.match(r'([\d.]+)', incoming)[0]) * multiplier
        if unit in outgoing:
            outgoing_bytes = float(re.match(r'([\d.]+)', outgoing)[0]) * multiplier
    return incoming_bytes, outgoing_bytes

async def generate_vpn_key(conf_path: str) -> str:
    process = await asyncio.create_subprocess_exec(
        'python3.11', 'awg-decode.py', '--encode', conf_path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    return stdout.decode().strip() if process.returncode == 0 and stdout.decode().startswith('vpn://') else ""

async def check_environment():
    if DOCKER_CONTAINER not in subprocess.check_output(f"docker ps --filter 'name={DOCKER_CONTAINER}' --format '{{{{.Names}}}}'", shell=True).decode().strip().split('\n'):
        logger.error(f"Контейнер '{DOCKER_CONTAINER}' не найден.")
        return False
    subprocess.check_call(f"docker exec {DOCKER_CONTAINER} test -f {WG_CONFIG_FILE}", shell=True)
    return True

async def on_startup(dp):
    os.makedirs('files/connections', exist_ok=True)
    os.makedirs('users', exist_ok=True)
    await load_isp_cache()
    if not await check_environment():
        for admin_id in admins:
            await bot.send_message(admin_id, "Ошибка инициализации AmneziaVPN.")
        await bot.close()
        sys.exit(1)
    if not db.get_admins():
        logger.error("Список админов пуст.")
        sys.exit(1)
    scheduler.add_job(db.ensure_peer_names, IntervalTrigger(minutes=1))

async def on_shutdown(dp):
    scheduler.shutdown()

if __name__ == '__main__':
    executor.start_polling(dp, on_startup=on_startup, on_shutdown=on_shutdown)
