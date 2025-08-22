import asyncio
import logging
import os
import re
import json
import uuid
from datetime import datetime, timedelta

import aiohttp
import aiofiles
from aiogram import Bot, types
from aiogram.dispatcher import Dispatcher
from aiogram.dispatcher.middlewares import BaseMiddleware
from aiogram.utils import executor
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz

import db

# Настройка логирования
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
pricing = setting.get('pricing', {
    '1_month': 1000.0,
    '3_months': 2500.0,
    '6_months': 4500.0,
    '12_months': 8000.0
})

# Инициализация бота и диспетчера
bot = Bot(token=bot_token)
dp = Dispatcher(bot)

# Списки администраторов и модераторов
admins = [int(admin_id) for admin_id in admin_ids]
moderators = [int(mod_id) for mod_id in moderator_ids]

# Глобальные переменные для состояний
user_states = {}
awaiting_promo_application = {}


# Функция для удаления сообщений с задержкой
async def delete_message_after_delay(chat_id, message_id, delay=2):
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception as e:
        logger.error(f"Ошибка удаления сообщения: {e}")


# Middleware для удаления сообщений администраторов
class AdminMessageDeletionMiddleware(BaseMiddleware):
    async def on_process_message(self, message: types.Message, data: dict):
        if message.from_user.id in admins and message.text.startswith('/'):
            asyncio.create_task(delete_message_after_delay(message.chat.id, message.message_id))


# Добавляем middleware
dp.middleware.setup(AdminMessageDeletionMiddleware())


# Функция для создания главного меню
def get_main_menu_markup(user_id):
    markup = InlineKeyboardMarkup(row_width=2)

    if user_id in admins:
        buttons = [
            InlineKeyboardButton("➕ Добавить пользователя", callback_data="add_user"),
            InlineKeyboardButton("👥 Список пользователей", callback_data="list_users"),
            InlineKeyboardButton("📊 Активные подключения", callback_data="active_connections"),
            InlineKeyboardButton("⚙️ Настройки цен", callback_data="pricing_settings"),
            InlineKeyboardButton("🎫 Промокоды", callback_data="manage_promocodes"),
            InlineKeyboardButton("📦 Создать бэкап", callback_data="create_backup"),
            InlineKeyboardButton("🔧 Настройки сервера", callback_data="server_settings")
        ]
    elif user_id in moderators:
        buttons = [
            InlineKeyboardButton("➕ Добавить пользователя", callback_data="add_user"),
            InlineKeyboardButton("👥 Список пользователей", callback_data="list_users"),
            InlineKeyboardButton("📊 Активные подключения", callback_data="active_connections")
        ]
    else:
        buttons = [
            InlineKeyboardButton("ℹ️ Информация", callback_data="info"),
            InlineKeyboardButton("📞 Поддержка", callback_data="support")
        ]

    # Добавляем кнопки в 2 колонки
    for i in range(0, len(buttons), 2):
        if i + 1 < len(buttons):
            markup.row(buttons[i], buttons[i + 1])
        else:
            markup.row(buttons[i])

    return markup


# Обработчик команд /start и /help
@dp.message_handler(commands=['start', 'help'])
async def start_command_handler(message: types.Message):
    user_id = message.from_user.id
    welcome_text = """
🤖 *Добро пожаловать в VPN бот!*

Здесь вы можете управлять вашим VPN сервисом.

*Доступные команды:*
• /start - Главное меню
• /help - Помощь

Выберите действие из меню ниже:
"""

    await message.answer(welcome_text, parse_mode='Markdown',
                         reply_markup=get_main_menu_markup(user_id))


# Обработчик кнопки добавления пользователя
@dp.callback_query_handler(lambda c: c.data == "add_user")
async def prompt_for_user_name(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins and user_id not in moderators:
        await callback_query.answer("❌ Доступ запрещен")
        return

    await callback_query.message.answer("Введите имя нового пользователя:")
    user_states[user_id] = 'awaiting_username'


# Обработчик ввода имени пользователя
@dp.message_handler(
    lambda message: message.from_user.id in user_states and user_states[message.from_user.id] == 'awaiting_username')
async def process_username(message: types.Message):
    user_id = message.from_user.id
    username = message.text.strip()

    # Проверка имени пользователя
    if not re.match(r'^[a-zA-Z0-9_]+$', username):
        await message.answer("❌ Неверное имя пользователя. Используйте только буквы, цифры и подчеркивания.")
        return

    # Добавление пользователя
    success = db.root_add(username)
    if success:
        await message.answer(f"✅ Пользователь {username} успешно добавлен!")

        # Отправка конфигурационного файла
        users_dir = 'users'
        conf_file = os.path.join(users_dir, username, f"{username}.conf")

        if os.path.exists(conf_file):
            with open(conf_file, 'r') as f:
                config_content = f.read()

            # Создание файла для отправки
            filename = f"{username}_vpn_config.conf"
            with open(filename, 'w') as f:
                f.write(config_content)

            await message.answer_document(types.InputFile(filename),
                                          caption=f"📁 Конфигурационный файл для {username}")
            os.remove(filename)
        else:
            await message.answer("⚠️ Конфигурационный файл не найден")
    else:
        await message.answer("❌ Ошибка при добавлении пользователя")

    del user_states[user_id]


# Обработчик кнопки списка пользователей
@dp.callback_query_handler(lambda c: c.data == "list_users")
async def list_users_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins and user_id not in moderators:
        await callback_query.answer("❌ Доступ запрещен")
        return

    clients = db.get_client_list()
    if not clients:
        await callback_query.message.answer("📝 Список пользователей пуст")
        return

    response = "📋 *Список пользователей:*\n\n"
    for i, (username, config) in enumerate(clients, 1):
        response += f"{i}. `{username}`\n"

    await callback_query.message.answer(response, parse_mode='Markdown')


# Обработчик активных подключений
@dp.callback_query_handler(lambda c: c.data == "active_connections")
async def active_connections_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins and user_id not in moderators:
        await callback_query.answer("❌ Доступ запрещен")
        return

    active_clients = db.get_active_list()
    if not active_clients:
        await callback_query.message.answer("📊 Нет активных подключений")
        return

    response = "📊 *Активные подключения:*\n\n"
    for i, (username, last_handshake) in enumerate(active_clients, 1):
        response += f"{i}. `{username}` - последнее подключение: {last_handshake}\n"

    await callback_query.message.answer(response, parse_mode='Markdown')


# Обработчик управления промокодами
@dp.callback_query_handler(lambda c: c.data == "manage_promocodes")
async def manage_promocodes_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins:
        await callback_query.answer("❌ Только для администраторов")
        return

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("➕ Добавить промокод", callback_data="add_promocode"))
    markup.add(InlineKeyboardButton("📋 Список промокодов", callback_data="list_promocodes"))
    markup.add(InlineKeyboardButton("↩️ Назад", callback_data="back_to_main"))

    await callback_query.message.answer("🎫 Управление промокодами:", reply_markup=markup)


# Обработчик создания бэкапа
@dp.callback_query_handler(lambda c: c.data == "create_backup")
async def create_backup_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins:
        await callback_query.answer("❌ Только для администраторов")
        return

    try:
        # Создание временной директории для бэкапа
        backup_dir = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        os.makedirs(backup_dir, exist_ok=True)

        # Копирование файлов
        if os.path.exists('users'):
            shutil.copytree('users', os.path.join(backup_dir, 'users'))
        if os.path.exists('files'):
            shutil.copytree('files', os.path.join(backup_dir, 'files'))

        # Создание архива
        shutil.make_archive(backup_dir, 'zip', backup_dir)

        # Отправка архива
        await callback_query.message.answer_document(
            types.InputFile(f"{backup_dir}.zip"),
            caption="📦 Резервная копия создана"
        )

        # Очистка
        shutil.rmtree(backup_dir)
        os.remove(f"{backup_dir}.zip")

    except Exception as e:
        logger.error(f"Ошибка создания бэкапа: {e}")
        await callback_query.message.answer("❌ Ошибка при создании бэкапа")


# Функция для проверки истекших подписок
async def check_expired_subscriptions():
    try:
        data = db.load_json(db.USER_EXPIRATION_FILE, {})
        now = datetime.now(pytz.utc)

        for username, user_data in data.items():
            expiration = user_data.get('expiration')
            if expiration and datetime.fromisoformat(expiration) < now:
                # Деактивация пользователя с истекшей подпиской
                db.deactive_user_db(username)
                db.remove_user_expiration(username)

                # Уведомление администратора
                for admin_id in admins:
                    try:
                        await bot.send_message(admin_id, f"⚠️ Пользователь {username} деактивирован (истекла подписка)")
                    except:
                        pass
    except Exception as e:
        logger.error(f"Ошибка проверки подписок: {e}")


# Планировщик для периодических задач
scheduler = AsyncIOScheduler()
scheduler.add_job(check_expired_subscriptions, 'interval', hours=1)
scheduler.start()

# Основная функция запуска
if __name__ == '__main__':
    # Проверка наличия токена
    if not bot_token:
        logger.error("❌ Токен бота не найден в конфигурации!")
        logger.error("Добавьте токен в files/config.json:")
        logger.error('{"bot_token": "YOUR_BOT_TOKEN", "admin_ids": [123456789]}')
        exit(1)

    # Проверка наличия администраторов
    if not admins:
        logger.warning("⚠️ Список администраторов пуст! Добавьте admin_ids в config.json")

    logger.info("🤖 Бот запускается...")
    executor.start_polling(dp, skip_updates=True)
