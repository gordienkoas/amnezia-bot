import db
import aiohttp
import logging
import asyncio
import aiofiles
import os
import re
import json
import sys
import uuid
import pytz
import zipfile
import shutil
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import BaseMiddleware
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from yoomoney import Client, Quickpay

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Базовый путь
BASE_DIR = "/root/amnezia-bot/awg"

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

# Проверка обязательных настроек
required_settings = ['bot_token', 'admin_ids', 'wg_config_file', 'docker_container', 'endpoint']
missing_settings = [key for key in required_settings if not setting.get(key)]
if missing_settings:
    logger.error(f"Отсутствуют обязательные настройки: {', '.join(missing_settings)}")
    sys.exit(1)

# Проверка настроек YooMoney
if not all([yoomoney_token, yoomoney_wallet]):
    logger.warning("Настройки YooMoney отсутствуют. Установите их через бот.")

# Преобразование admin_ids и moderator_ids
try:
    admins = [int(admin_id) for admin_id in admin_ids.split(',')] if isinstance(admin_ids, str) else [int(admin_id) for admin_id in admin_ids]
    moderators = [int(mod_id) for mod_id in moderator_ids.split(',')] if isinstance(moderator_ids, str) else [int(mod_id) for mod_id in moderator_ids]
except ValueError as e:
    logger.error(f"Ошибка преобразования admin_ids или moderator_ids: {str(e)}")
    sys.exit(1)

bot = Bot(token=bot_token)
dp = Dispatcher()
WG_CONFIG_FILE = wg_config_file
DOCKER_CONTAINER = docker_container
ENDPOINT = endpoint
yoomoney_client = Client(yoomoney_token) if yoomoney_token else None
PRICING = pricing

class AdminMessageDeletionMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, types.Message) and event.from_user.id in admins and event.text.startswith('/'):
            asyncio.create_task(delete_message_after_delay(event.chat.id, event.message_id))
        return await handler(event, data)

scheduler = AsyncIOScheduler(timezone=pytz.utc)
scheduler.start()
dp.message.middleware(AdminMessageDeletionMiddleware())

# Вспомогательная функция для обновления сообщений
async def update_user_message(user_id, chat_id, text, reply_markup, old_message_id=None, state=None):
    if old_message_id:
        try:
            await bot.delete_message(chat_id, old_message_id)
        except:
            pass
    sent_message = await bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode="Markdown")
    user_main_messages[user_id] = {
        'chat_id': sent_message.chat.id,
        'message_id': sent_message.message_id,
        'state': state
    }
    return sent_message

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
            InlineKeyboardButton("🎟️ Управление промокодами", callback_data="manage_promocodes")
        )
        markup.add(
            InlineKeyboardButton("⚙️ Настройки", callback_data="settings"),
            InlineKeyboardButton("🏠 Домой", callback_data="home")
        )
    elif user_id in moderators:
        markup.add(
            InlineKeyboardButton("➕ Добавить пользователя", callback_data="add_user"),
            InlineKeyboardButton("📋 Список клиентов", callback_data="list_users")
        )
        markup.add(
            InlineKeyboardButton("🔑 Получить конфиг", callback_data="get_config"),
            InlineKeyboardButton("🏠 Домой", callback_data="home")
        )
    else:
        markup.add(
            InlineKeyboardButton("💳 Купить ключ", callback_data="buy_key"),
            InlineKeyboardButton("🎟️ Получить ключ по промокоду", callback_data="use_promocode")
        )
    return markup

# Меню покупки ключа
def get_buy_key_menu(user_id):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("📅 Выбрать период", callback_data="select_subscription_period"),
        InlineKeyboardButton("🎟️ Ввести промокод", callback_data="enter_promocode_for_buy")
    )
    markup.add(InlineKeyboardButton("🏠 Домой", callback_data="home"))
    discount = user_main_messages.get(user_id, {}).get('promocode_discount', 0)
    if discount > 0:
        markup.add(InlineKeyboardButton(f"Сбросить скидку ({discount}%)", callback_data="reset_promocode"))
    return markup

# Меню настроек
def get_settings_menu():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("💾 Создать бэкап", callback_data="create_backup"),
        InlineKeyboardButton("👥 Список админов", callback_data="list_admins")
    )
    markup.add(
        InlineKeyboardButton("👤 Добавить админа", callback_data="add_admin"),
        InlineKeyboardButton("💸 Настройки YooMoney", callback_data="yoomoney_settings")
    )
    markup.add(
        InlineKeyboardButton("💰 Настройки цен", callback_data="pricing_settings"),
        InlineKeyboardButton("⬅️ Назад", callback_data="home")
    )
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
        ("12 месяцев", "12_months"),
        ("Кастомная дата", "custom_date")
    ]
    for period_name, period_key in periods:
        markup.add(InlineKeyboardButton(period_name, callback_data=f"renew_period_{username}_{period_key}"))
    markup.add(InlineKeyboardButton("Отмена", callback_data="home"))
    return markup

user_main_messages = {}

async def delete_message_after_delay(chat_id: int, message_id: int, delay: int = 2):
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except:
        pass

async def generate_vpn_key(conf_path: str) -> str:
    try:
        process = await asyncio.create_subprocess_exec(
            'python3.11', os.path.join(BASE_DIR, 'awg-decode.py'), '--encode', conf_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        if process.returncode == 0 and stdout.decode().startswith('vpn://'):
            return stdout.decode().strip()
        else:
            logger.error(f"Ошибка генерации vpn://: {stderr.decode()}")
            return ""
    except Exception as e:
        logger.error(f"Ошибка в generate_vpn_key: {str(e)}")
        return ""

async def issue_vpn_key(user_id: int, period: str) -> bool:
    try:
        username = f"user_{user_id}_{uuid.uuid4().hex[:8]}"
        success = db.root_add(username, ipv6=False)
        if success:
            months = {'1_month': 1, '3_months': 3, '6_months': 6, '12_months': 12}.get(period, 1)
            expiration = datetime.now(pytz.utc) + timedelta(days=30 * months)
            db.set_user_expiration(username, expiration, "Неограниченно")
            db.set_user_telegram_id(username, user_id)
            conf_path = os.path.join(BASE_DIR, 'users', username, f'{username}.conf')
            if os.path.exists(conf_path):
                vpn_key = await generate_vpn_key(conf_path)
                caption = f"Ваш VPN ключ ({period.replace('_', ' ')}):\nAmneziaVPN:\n[Google Play](https://play.google.com/store/apps/details?id=org.amnezia.vpn&hl=ru)\n[GitHub](https://github.com/amnezia-vpn/amnezia-client)\n```\n{vpn_key}\n```"
                with open(conf_path, 'rb') as config:
                    config_message = await bot.send_document(user_id, config, caption=caption, parse_mode="Markdown")
                    await bot.pin_chat_message(user_id, config_message.message_id, disable_notification=True)
                return True
        return False
    except Exception as e:
        logger.error(f"Ошибка в issue_vpn_key для user_id {user_id}: {str(e)}")
        return False

@dp.message(Command(commands=['start', 'help']))
async def start_command_handler(message: types.Message):
    user_id = message.from_user.id
    await update_user_message(
        user_id=user_id,
        chat_id=message.chat.id,
        text="Выберите действие:",
        reply_markup=get_main_menu_markup(user_id),
        old_message_id=user_main_messages.get(user_id, {}).get('message_id')
    )

@dp.message(Command(commands=['add_admin']))
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
        else:
            await message.answer(f"Пользователь {new_admin_id} уже админ.")
    except:
        await message.answer("Формат: /add_admin <user_id>")
    await update_user_message(
        user_id=message.from_user.id,
        chat_id=message.chat.id,
        text="Выберите действие:",
        reply_markup=get_main_menu_markup(message.from_user.id),
        old_message_id=message.message_id
    )

@dp.message()
async def handle_messages(message: types.Message):
    global PRICING
    user_id = message.from_user.id
    user_state = user_main_messages.get(user_id, {}).get('state')

    try:
        if user_state == 'waiting_for_user_name':
            user_name = message.text.strip()
            if not re.match(r'^[a-zA-Z0-9_-]+$', user_name):
                await message.reply("Имя может содержать только буквы, цифры, - и _.")
                return
            success = db.root_add(user_name, ipv6=False)
            if success:
                conf_path = os.path.join(BASE_DIR, 'users', user_name, f'{user_name}.conf')
                if os.path.exists(conf_path):
                    vpn_key = await generate_vpn_key(conf_path)
                    caption = f"Конфигурация для {user_name}:\nAmneziaVPN:\n[Google Play](https://play.google.com/store/apps/details?id=org.amnezia.vpn&hl=ru)\n[GitHub](https://github.com/amnezia-vpn/amnezia-client)\n```\n{vpn_key}\n```"
                    with open(conf_path, 'rb') as config:
                        config_message = await bot.send_document(user_id, config, caption=caption, parse_mode="Markdown")
                        await bot.pin_chat_message(user_id, config_message.message_id, disable_notification=True)
            else:
                await message.reply("Ошибка добавления пользователя.")
        elif user_state == 'waiting_for_admin_id' and user_id in admins:
            try:
                new_admin_id = int(message.text.strip())
                if new_admin_id not in admins:
                    db.add_admin(new_admin_id)
                    admins.append(new_admin_id)
                    await message.reply(f"Админ {new_admin_id} добавлен.")
                    await bot.send_message(new_admin_id, "Вы назначены администратором!")
                else:
                    await message.reply(f"Пользователь {new_admin_id} уже админ.")
            except:
                await message.reply("Введите корректный Telegram ID.")
        elif user_state == 'waiting_for_promocode':
            promocode = message.text.strip()
            promocode_data = db.apply_promocode(promocode)
            if promocode_data:
                subscription_period = promocode_data.get('subscription_period')
                if subscription_period:
                    success = await issue_vpn_key(user_id, subscription_period)
                    if success:
                        await message.reply(f"Промокод активирован! VPN ключ на {subscription_period.replace('_', ' ')} выдан.")
                    else:
                        await message.reply("Ошибка при выдаче ключа. Обратитесь к администратору.")
                else:
                    await message.reply("Промокод не предоставляет ключ.")
            else:
                await message.reply("Неверный или истёкший промокод.")
        elif user_state == 'waiting_for_promocode_for_buy':
            promocode = message.text.strip()
            promocode_data = db.apply_promocode(promocode)
            if promocode_data:
                discount = promocode_data.get('discount', 0)
                user_main_messages[user_id]['promocode_discount'] = discount
                await message.reply(f"Промокод активирован! Скидка: {discount}%")
            else:
                await message.reply("Неверный или истёкший промокод.")
            await update_user_message(
                user_id=user_id,
                chat_id=message.chat.id,
                text="Меню покупки ключа:",
                reply_markup=get_buy_key_menu(user_id),
                state=None
            )
            return
        elif user_state == 'waiting_for_new_promocode' and user_id in admins:
            try:
                parts = message.text.strip().split()
                if len(parts) != 5:
                    raise ValueError("Неверный формат")
                code, discount, days_valid, max_uses, subscription_period = parts
                discount = float(discount)
                days_valid = int(days_valid)
                max_uses = int(max_uses) if max_uses.lower() != 'none' else None
                if subscription_period not in ['none', '1_month', '3_months', '6_months', '12_months']:
                    raise ValueError("Неверный период подписки")
                subscription_period = None if subscription_period.lower() == 'none' else subscription_period
                expires_at = datetime.now(pytz.utc) + timedelta(days=days_valid) if days_valid > 0 else None
                if db.add_promocode(code, discount, expires_at, max_uses, subscription_period):
                    await message.reply(
                        f"Промокод {code} добавлен: скидка {discount}%, действует {days_valid} дней, "
                        f"макс. использований: {max_uses or 'неограничено'}, период подписки: {subscription_period or 'нет'}"
                    )
                else:
                    await message.reply("Промокод уже существует.")
            except:
                await message.reply(
                    "Формат: <код> <скидка%> <дней_действия> <макс_использований|none> <период_подписки|none>\n"
                    "Пример: PROMO1 10 30 none 1_month"
                )
        elif user_state == 'waiting_for_yoomoney_token' and user_id in admins:
            token = message.text.strip()
            db.set_yoomoney_config(token=token)
            global yoomoney_client, yoomoney_token
            yoomoney_token = token
            yoomoney_client = Client(yoomoney_token)
            await message.reply("Токен YooMoney успешно обновлён.")
            await update_user_message(
                user_id=user_id,
                chat_id=message.chat.id,
                text="Настройки YooMoney:",
                reply_markup=get_yoomoney_settings_menu(),
                state=None
            )
            return
        elif user_state == 'waiting_for_yoomoney_wallet' and user_id in admins:
            wallet = message.text.strip()
            if not re.match(r'^\d{15,18}$', wallet):
                await message.reply("Введите корректный номер кошелька YooMoney (15-18 цифр).")
                return
            db.set_yoomoney_config(wallet=wallet)
            global yoomoney_wallet
            yoomoney_wallet = wallet
            await message.reply("Номер кошелька YooMoney успешно обновлён.")
            await update_user_message(
                user_id=user_id,
                chat_id=message.chat.id,
                text="Настройки YooMoney:",
                reply_markup=get_yoomoney_settings_menu(),
                state=None
            )
            return
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
            await update_user_message(
                user_id=user_id,
                chat_id=message.chat.id,
                text="Настройки цен:",
                reply_markup=get_pricing_settings_menu(),
                state=None
            )
            return
        elif user_state.startswith('waiting_for_custom_date_') and user_id in admins:
            username = user_state.split('waiting_for_custom_date_')[1]
            try:
                expiration = datetime.strptime(message.text.strip(), '%d-%m-%Y').replace(tzinfo=pytz.utc)
                if expiration < datetime.now(pytz.utc):
                    await message.reply("Дата должна быть в будущем.")
                    return
                db.set_user_expiration(username, expiration, "Неограниченно")
                await message.reply(f"Подписка для {username} продлена до {expiration.strftime('%d-%m-%Y')}.")
            except:
                await message.reply("Введите дату в формате ДД-ММ-ГГГГ (например, 31-12-2025).")
                return
        await update_user_message(
            user_id=user_id,
            chat_id=message.chat.id,
            text="Выберите действие:",
            reply_markup=get_main_menu_markup(user_id),
            state=None
        )
    except Exception as e:
        logger.error(f"Ошибка в handle_messages для user_id {user_id}: {str(e)}")
        await message.reply("Произошла ошибка. Попробуйте снова.")
        await update_user_message(
            user_id=user_id,
            chat_id=message.chat.id,
            text="Выберите действие:",
            reply_markup=get_main_menu_markup(user_id),
            state=None
        )

@dp.callback_query(lambda c: c.data == "settings")
async def settings_menu_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    await update_user_message(
        user_id=user_id,
        chat_id=callback_query.message.chat.id,
        text="Настройки:",
        reply_markup=get_settings_menu(),
        old_message_id=callback_query.message.message_id
    )
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "yoomoney_settings")
async def yoomoney_settings_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    await update_user_message(
        user_id=user_id,
        chat_id=callback_query.message.chat.id,
        text="Настройки YooMoney:",
        reply_markup=get_yoomoney_settings_menu(),
        old_message_id=callback_query.message.message_id
    )
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "set_yoomoney_token")
async def set_yoomoney_token_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    await update_user_message(
        user_id=user_id,
        chat_id=callback_query.message.chat.id,
        text="Введите токен YooMoney:",
        reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ Назад", callback_data="yoomoney_settings")),
        old_message_id=callback_query.message.message_id,
        state='waiting_for_yoomoney_token'
    )
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "set_yoomoney_wallet")
async def set_yoomoney_wallet_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    await update_user_message(
        user_id=user_id,
        chat_id=callback_query.message.chat.id,
        text="Введите номер кошелька YooMoney (15-18 цифр):",
        reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ Назад", callback_data="yoomoney_settings")),
        old_message_id=callback_query.message.message_id,
        state='waiting_for_yoomoney_wallet'
    )
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "pricing_settings")
async def pricing_settings_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    await update_user_message(
        user_id=user_id,
        chat_id=callback_query.message.chat.id,
        text="Настройки цен:",
        reply_markup=get_pricing_settings_menu(),
        old_message_id=callback_query.message.message_id
    )
    await callback_query.answer()

@dp.callback_query(lambda c: c.data.startswith('set_price_'))
async def set_price_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    period = callback_query.data.split('set_price_')[1]
    await update_user_message(
        user_id=user_id,
        chat_id=callback_query.message.chat.id,
        text=f"Введите новую цену для {period.replace('_', ' ')} в рублях (например, 1000.00):",
        reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ Назад", callback_data="pricing_settings")),
        old_message_id=callback_query.message.message_id,
        state=f'waiting_for_price_{period}'
    )
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "add_user")
async def prompt_for_user_name(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins and user_id not in moderators:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    await update_user_message(
        user_id=user_id,
        chat_id=callback_query.message.chat.id,
        text="Введите имя пользователя:",
        reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🏠 Домой", callback_data="home")),
        old_message_id=callback_query.message.message_id,
        state='waiting_for_user_name'
    )
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "add_admin")
async def prompt_for_admin_id(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    await update_user_message(
        user_id=user_id,
        chat_id=callback_query.message.chat.id,
        text="Введите Telegram ID нового админа:",
        reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("Отмена", callback_data="home")),
        old_message_id=callback_query.message.message_id,
        state='waiting_for_admin_id'
    )
    await callback_query.answer()

@dp.callback_query(lambda c: c.data.startswith('client_'))
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
        expiration = db.get_user_expiration(username)
        expiration_text = expiration.strftime("%Y-%m-%d %H:%M UTC") if expiration else "Не установлен"

        active_clients = db.get_active_list()
        active_info = next((ac for ac in active_clients if ac[0] == username), None)
        if active_info and active_info[1] and active_info[1].lower() not in ['never', 'нет данных', '-']:
            try:
                last_handshake = datetime.strptime(active_info[1], "%Y-%m-%d %H:%M:%S")
                status = "🟢 Онлайн" if (datetime.now(pytz.utc) - last_handshake).total_seconds() <= 60 else "❌ Офлайн"
            except:
                pass

        text = (
            f"📧 *Имя:* {username}\n"
            f"🌐 *Статус:* {status}\n"
            f"⏰ *Срок действия:* {expiration_text}"
        )

        keyboard = InlineKeyboardMarkup(row_width=2).add(
            InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_user_{username}"),
            InlineKeyboardButton("🔄 Продлить", callback_data=f"renew_user_{username}"),
            InlineKeyboardButton("⬅️ Назад", callback_data="list_users"),
            InlineKeyboardButton("🏠 Домой", callback_data="home")
        )

        await update_user_message(
            user_id=user_id,
            chat_id=callback_query.message.chat.id,
            text=text,
            reply_markup=keyboard,
            old_message_id=callback_query.message.message_id
        )
        await callback_query.answer()

    except Exception as e:
        logger.error(f"Ошибка в client_selected_callback: {str(e)}")
        await update_user_message(
            user_id=user_id,
            chat_id=callback_query.message.chat.id,
            text=f"Ошибка при загрузке профиля: {str(e)}",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("⬅️ Назад", callback_data="list_users"),
                InlineKeyboardButton("🏠 Домой", callback_data="home")
            ),
            old_message_id=callback_query.message.message_id
        )
        await callback_query.answer("Ошибка на сервере.", show_alert=True)

@dp.callback_query(lambda c: c.data == "list_users")
async def list_users_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins and user_id not in moderators:
        await callback_query.answer("Нет прав.", show_alert=True)
        return

    try:
        clients = db.get_client_list()
        if not clients:
            await update_user_message(
                user_id=user_id,
                chat_id=callback_query.message.chat.id,
                text="Список клиентов пуст.",
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("🏠 Домой", callback_data="home")
                ),
                old_message_id=callback_query.message.message_id
            )
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

        await update_user_message(
            user_id=user_id,
            chat_id=callback_query.message.chat.id,
            text="Выберите пользователя:",
            reply_markup=keyboard,
            old_message_id=callback_query.message.message_id
        )
        await callback_query.answer()

    except Exception as e:
        logger.error(f"Ошибка в list_users_callback: {str(e)}")
        await update_user_message(
            user_id=user_id,
            chat_id=callback_query.message.chat.id,
            text=f"Ошибка: {str(e)}",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🏠 Домой", callback_data="home")
            ),
            old_message_id=callback_query.message.message_id
        )
        await callback_query.answer("Ошибка на сервере.", show_alert=True)

@dp.callback_query(lambda c: c.data == "list_admins")
async def list_admins_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    keyboard = InlineKeyboardMarkup(row_width=2)
    for admin_id in admins:
        keyboard.insert(InlineKeyboardButton(f"🗑️ Удалить {admin_id}", callback_data=f"remove_admin_{admin_id}"))
    keyboard.add(InlineKeyboardButton("⬅️ Назад", callback_data="settings"))
    await update_user_message(
        user_id=user_id,
        chat_id=callback_query.message.chat.id,
        text=f"Администраторы:\n" + "\n".join(f"- {admin_id}" for admin_id in admins),
        reply_markup=keyboard,
        old_message_id=callback_query.message.message_id
    )
    await callback_query.answer()

@dp.callback_query(lambda c: c.data.startswith('remove_admin_'))
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

@dp.callback_query(lambda c: c.data.startswith('delete_user_'))
async def client_delete_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins and user_id not in moderators:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    username = callback_query.data.split('delete_user_')[1]
    try:
        if db.deactive_user_db(username):
            shutil.rmtree(os.path.join(BASE_DIR, 'users', username), ignore_errors=True)
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
    await update_user_message(
        user_id=user_id,
        chat_id=callback_query.message.chat.id,
        text=text,
        reply_markup=get_main_menu_markup(user_id),
        old_message_id=callback_query.message.message_id
    )
    await callback_query.answer()

@dp.callback_query(lambda c: c.data.startswith('renew_user_'))
async def renew_user_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    username = callback_query.data.split('renew_user_')[1]
    await update_user_message(
        user_id=user_id,
        chat_id=callback_query.message.chat.id,
        text="Выберите период продления или укажите дату:",
        reply_markup=get_renewal_period_keyboard(username),
        old_message_id=callback_query.message.message_id
    )
    await callback_query.answer()

@dp.callback_query(lambda c: c.data.startswith('renew_period_'))
async def renew_period_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    try:
        parts = callback_query.data.split('renew_period_')[1].split('_', 1)
        username = parts[0]
        period = parts[1] if len(parts) > 1 else 'custom_date'
        if period == 'custom_date':
            await update_user_message(
                user_id=user_id,
                chat_id=callback_query.message.chat.id,
                text="Введите дату продления в формате ДД-ММ-ГГГГ (например, 31-12-2025):",
                reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("Отмена", callback_data="home")),
                old_message_id=callback_query.message.message_id,
                state=f'waiting_for_custom_date_{username}'
            )
        else:
            months = {'1_month': 1, '3_months': 3, '6_months': 6, '12_months': 12}[period]
            expiration = datetime.now(pytz.utc) + timedelta(days=30 * months)
            db.set_user_expiration(username, expiration, "Неограниченно")
            text = f"Подписка для {username} продлена до {expiration.strftime('%Y-%m-%d %H:%M UTC')}."
            logger.info(f"Подписка для {username} продлена на {period} до {expiration}.")
            await update_user_message(
                user_id=user_id,
                chat_id=callback_query.message.chat.id,
                text=text,
                reply_markup=get_main_menu_markup(user_id),
                old_message_id=callback_query.message.message_id
            )
        await callback_query.answer()
    except Exception as e:
        text = f"Ошибка при продлении: {str(e)}"
        logger.error(f"Ошибка при продлении подписки для {username}: {str(e)}")
        await update_user_message(
            user_id=user_id,
            chat_id=callback_query.message.chat.id,
            text=text,
            reply_markup=get_main_menu_markup(user_id),
            old_message_id=callback_query.message.message_id
        )
        await callback_query.answer()

@dp.callback_query(lambda c: c.data == "home")
async def return_home(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    await update_user_message(
        user_id=user_id,
        chat_id=callback_query.message.chat.id,
        text="Выберите действие:",
        reply_markup=get_main_menu_markup(user_id),
        old_message_id=callback_query.message.message_id
    )
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "get_config")
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
    await update_user_message(
        user_id=user_id,
        chat_id=callback_query.message.chat.id,
        text="Выберите пользователя:",
        reply_markup=keyboard,
        old_message_id=callback_query.message.message_id
    )
    await callback_query.answer()

@dp.callback_query(lambda c: c.data.startswith('send_config_'))
async def send_user_config(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins and user_id not in moderators:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    username = callback_query.data.split('send_config_')[1]
    conf_path = os.path.join(BASE_DIR, 'users', username, f'{username}.conf')
    if os.path.exists(conf_path):
        vpn_key = await generate_vpn_key(conf_path)
        caption = f"Конфигурация для {username}:\nAmneziaVPN:\n[Google Play](https://play.google.com/store/apps/details?id=org.amnezia.vpn&hl=ru)\n[GitHub](https://github.com/amnezia-vpn/amnezia-client)\n```\n{vpn_key}\n```"
        with open(conf_path, 'rb') as config:
            config_message = await bot.send_document(user_id, config, caption=caption, parse_mode="Markdown")
            await bot.pin_chat_message(user_id, config_message.message_id, disable_notification=True)
    else:
        await bot.send_message(user_id, f"Конфигурация для **{username}** не найдена.", parse_mode="Markdown")
    await update_user_message(
        user_id=user_id,
        chat_id=callback_query.message.chat.id,
        text="Выберите действие:",
        reply_markup=get_main_menu_markup(user_id),
        old_message_id=callback_query.message.message_id
    )
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "create_backup")
async def create_backup_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    try:
        backup_filename = f"backup_{datetime.now().strftime('%Y-%m-%d')}.zip"
        with zipfile.ZipFile(backup_filename, 'w') as zipf:
            for file in ['awg-decode.py', 'newclient.sh', 'removeclient.sh']:
                file_path = os.path.join(BASE_DIR, file)
                if os.path.exists(file_path):
                    zipf.write(file_path, file)
            for root, _, files in os.walk(os.path.join(BASE_DIR, 'files')):
                for file in files:
                    file_path = os.path.join(root, file)
                    zipf.write(file_path, os.path.relpath(file_path, BASE_DIR))
            for root, _, files in os.walk(os.path.join(BASE_DIR, 'users')):
                for file in files:
                    file_path = os.path.join(root, file)
                    zipf.write(file_path, os.path.relpath(file_path, BASE_DIR))
        with open(backup_filename, 'rb') as f:
            await bot.send_document(user_id, f, caption=backup_filename)
        os.remove(backup_filename)
    except Exception as e:
        logger.error(f"Ошибка при создании бэкапа: {str(e)}")
        await bot.send_message(user_id, f"Ошибка при создании бэкапа: {str(e)}")
    await update_user_message(
        user_id=user_id,
        chat_id=callback_query.message.chat.id,
        text="Выберите действие:",
        reply_markup=get_main_menu_markup(user_id),
        old_message_id=callback_query.message.message_id
    )
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "buy_key")
async def buy_key_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    await update_user_message(
        user_id=user_id,
        chat_id=callback_query.message.chat.id,
        text="Меню покупки ключа:",
        reply_markup=get_buy_key_menu(user_id),
        old_message_id=callback_query.message.message_id
    )
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "select_subscription_period")
async def select_period_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    keyboard = InlineKeyboardMarkup(row_width=2)
    discount = user_main_messages.get(user_id, {}).get('promocode_discount', 0)
    for period, price in PRICING.items():
        final_price = price * (1 - discount / 100)
        keyboard.add(InlineKeyboardButton(
            f"{period.replace('_', ' ')} - ₽{final_price:.2f}{' (-' + str(discount) + '%)' if discount > 0 else ''}",
            callback_data=f"confirm_period_{period}"
        ))
    keyboard.add(InlineKeyboardButton("⬅️ Назад", callback_data="buy_key"))
    keyboard.add(InlineKeyboardButton("🏠 Домой", callback_data="home"))
    await update_user_message(
        user_id=user_id,
        chat_id=callback_query.message.chat.id,
        text="Выберите период подписки:",
        reply_markup=keyboard,
        old_message_id=callback_query.message.message_id
    )
    await callback_query.answer()

@dp.callback_query(lambda c: c.data.startswith('confirm_period_'))
async def confirm_period_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    period = callback_query.data.split('confirm_period_')[1]
    price = PRICING[period]
    discount = user_main_messages.get(user_id, {}).get('promocode_discount', 0)
    final_price = price * (1 - discount / 100)

    if not yoomoney_client or not yoomoney_wallet:
        await callback_query.answer("Платежи недоступны. Настройки YooMoney не установлены.", show_alert=True)
        return

    try:
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
            InlineKeyboardButton("⬅️ Назад", callback_data="select_subscription_period"),
            InlineKeyboardButton("🏠 Домой", callback_data="home")
        )
        await update_user_message(
            user_id=user_id,
            chat_id=callback_query.message.chat.id,
            text=f"Подписка на {period.replace('_', ' ')}: ₽{final_price:.2f} (скидка {discount}%)\nОплатите по ссылке:",
            reply_markup=keyboard,
            old_message_id=callback_query.message.message_id,
            state=None,
            pending_payment={
                'payment_id': payment_id,
                'period': period,
                'username': None
            }
        )
        await callback_query.answer()
    except Exception as e:
        logger.error(f"Ошибка при создании платежа для user_id {user_id}: {str(e)}")
        await callback_query.answer("Ошибка при создании платежа.", show_alert=True)

@dp.callback_query(lambda c: c.data == "enter_promocode_for_buy")
async def enter_promocode_for_buy_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    await update_user_message(
        user_id=user_id,
        chat_id=callback_query.message.chat.id,
        text="Введите промокод для скидки:",
        reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ Назад", callback_data="buy_key")),
        old_message_id=callback_query.message.message_id,
        state='waiting_for_promocode_for_buy'
    )
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "reset_promocode")
async def reset_promocode_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if 'promocode_discount' in user_main_messages.get(user_id, {}):
        del user_main_messages[user_id]['promocode_discount']
    await update_user_message(
        user_id=user_id,
        chat_id=callback_query.message.chat.id,
        text="Скидка сброшена. Меню покупки ключа:",
        reply_markup=get_buy_key_menu(user_id),
        old_message_id=callback_query.message.message_id
    )
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "use_promocode")
async def use_promocode_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    await update_user_message(
        user_id=user_id,
        chat_id=callback_query.message.chat.id,
        text="Введите промокод для получения ключа:",
        reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🏠 Домой", callback_data="home")),
        old_message_id=callback_query.message.message_id,
        state='waiting_for_promocode'
    )
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "manage_promocodes")
async def manage_promocodes_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    promocodes = db.get_promocodes()
    text = "Промокоды:\n" + "\n".join(
        f"{code}: {info['discount']}% (использовано {info['uses']}/{info['max_uses'] or '∞'}, до {info['expires_at'].strftime('%Y-%m-%d %H:%M UTC') if info['expires_at'] else 'неограничено'}, период подписки: {info['subscription_period'] or 'нет'})"
        for code, info in promocodes.items()
    ) if promocodes else "Промокоды отсутствуют."
    keyboard = InlineKeyboardMarkup(row_width=2).add(
        InlineKeyboardButton("➕ Добавить промокод", callback_data="add_promocode"),
        InlineKeyboardButton("🗑️ Удалить промокод", callback_data="delete_promocode"),
        InlineKeyboardButton("🏠 Домой", callback_data="home")
    )
    await update_user_message(
        user_id=user_id,
        chat_id=callback_query.message.chat.id,
        text=text,
        reply_markup=keyboard,
        old_message_id=callback_query.message.message_id
    )
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "add_promocode")
async def add_promocode_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in admins:
        await callback_query.answer("Нет прав.", show_alert=True)
        return
    await update_user_message(
        user_id=user_id,
        chat_id=callback_query.message.chat.id,
        text="Введите промокод в формате: <код> <скидка%> <дней_действия> <макс_использований|none> <период_подписки|none>\nПример: PROMO1 10 30 none 1_month",
        reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🏠 Домой", callback_data="home")),
        old_message_id=callback_query.message.message_id,
        state='waiting_for_new_promocode'
    )
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "delete_promocode")
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
    await update_user_message(
        user_id=user_id,
        chat_id=callback_query.message.chat.id,
        text="Выберите промокод для удаления:",
        reply_markup=keyboard,
        old_message_id=callback_query.message.message_id
    )
    await callback_query.answer()

@dp.callback_query(lambda c: c.data.startswith('remove_promocode_'))
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

async def check_pending_payments():
    try:
        payments = db.get_pending_payments()
        for payment in payments:
            user_id, payment_id, amount, period = payment
            try:
                history = yoomoney_client.operation_history(label=payment_id)
                for operation in history.operations:
                    if operation.status == "success" and operation.amount == amount:
                        db.update_payment_status(payment_id, 'completed')
                        success = await issue_vpn_key(user_id, period)
                        if success:
                            await bot.send_message(user_id, f"Оплата подтверждена! VPN ключ на {period.replace('_', ' ')} выдан.")
                        else:
                            await bot.send_message(user_id, "Ошибка при выдаче ключа. Обратитесь к администратору.")
                        break
            except Exception as e:
                logger.error(f"Ошибка при проверке платежа {payment_id}: {str(e)}")
    except Exception as e:
        logger.error(f"Ошибка в check_pending_payments: {str(e)}")

scheduler.add_job(check_pending_payments, IntervalTrigger(minutes=5))

async def main():
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == '__main__':
    asyncio.run(main())
