import json
import os
import subprocess
import logging
from datetime import datetime
import pytz
import shutil

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CONFIG_FILE = 'files/config.json'
USER_EXPIRATION_FILE = 'files/user_expiration.json'
USER_TELEGRAM_FILE = 'files/user_telegram.json'
PROMOCODES_FILE = 'files/promocodes.json'
PAYMENTS_FILE = 'files/payments.json'

def load_json(file_path, default=None):
    """Загружает JSON-файл, возвращает default при ошибке или отсутствии файла."""
    try:
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка загрузки {file_path}: {str(e)}")
    return default if default is not None else {}

def save_json(file_path, data):
    """Сохраняет данные в JSON-файл."""
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w') as f:
            json.dump(data, f, indent=4, default=str)
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения {file_path}: {str(e)}")
        return False

def get_config():
    """Возвращает конфигурацию из config.json."""
    return load_json(CONFIG_FILE, {})

def add_admin(admin_id):
    """Добавляет ID администратора в конфигурацию."""
    config = get_config()
    admin_ids = config.get('admin_ids', [])
    if str(admin_id) not in admin_ids:
        admin_ids.append(str(admin_id))
        config['admin_ids'] = admin_ids
        save_json(CONFIG_FILE, config)

def remove_admin(admin_id):
    """Удаляет ID администратора из конфигурации."""
    config = get_config()
    admin_ids = config.get('admin_ids', [])
    admin_id_str = str(admin_id)
    if admin_id_str in admin_ids:
        admin_ids.remove(admin_id_str)
        config['admin_ids'] = admin_ids
        save_json(CONFIG_FILE, config)

def set_yoomoney_config(token=None, wallet=None):
    """Обновляет настройки YooMoney в конфигурации."""
    config = get_config()
    if token:
        config['yoomoney_token'] = token
    if wallet:
        config['yoomoney_wallet'] = wallet
    save_json(CONFIG_FILE, config)

def set_pricing(period, price):
    """Устанавливает цену для указанного периода подписки."""
    config = get_config()
    config['pricing'] = config.get('pricing', {})
    config['pricing'][period] = price
    save_json(CONFIG_FILE, config)

def root_add(name, ipv6=False):
    """Добавляет нового пользователя через newclient.sh."""
    try:
        cmd = ['./newclient.sh', name]
        if not ipv6:
            cmd.append('--no-ipv6')
        process = subprocess.run(cmd, capture_output=True, text=True)
        if process.returncode == 0:
            return True
        logger.error(f"Ошибка добавления пользователя {name}: {process.stderr}")
        return False
    except Exception as e:
        logger.error(f"Исключение при добавлении пользователя {name}: {str(e)}")
        return False

def deactive_user_db(name):
    """Деактивирует пользователя через removeclient.sh."""
    try:
        process = subprocess.run(['./removeclient.sh', name], capture_output=True, text=True)
        if process.returncode == 0:
            return True
        logger.error(f"Ошибка удаления пользователя {name}: {process.stderr}")
        return False
    except Exception as e:
        logger.error(f"Исключение при удалении пользователя {name}: {str(e)}")
        return False

def get_client_list():
    """Возвращает список клиентов (имя и конфигурация)."""
    clients = []
    users_dir = 'users'
    if os.path.exists(users_dir):
        for user_dir in os.listdir(users_dir):
            user_path = os.path.join(users_dir, user_dir)
            if os.path.isdir(user_path):
                conf_file = os.path.join(user_path, f"{user_dir}.conf")
                if os.path.exists(conf_file):
                    with open(conf_file, 'r') as f:
                        config = f.read()
                    clients.append((user_dir, config))
    return clients

def get_active_list():
    """Возвращает список активных клиентов с последним handshake."""
    active = []
    users_dir = 'users'
    if os.path.exists(users_dir):
        for user_dir in os.listdir(users_dir):
            user_path = os.path.join(users_dir, user_dir)
            if os.path.isdir(user_path):
                status_file = os.path.join(user_path, 'status.json')
                if os.path.exists(status_file):
                    with open(status_file, 'r') as f:
                        status = json.load(f)
                    last_handshake = status.get('last_handshake', 'never')
                    active.append((user_dir, last_handshake))
    return active

def set_user_expiration(username, expiration, transfer_limit):
    """Устанавливает срок действия и лимит трафика для пользователя."""
    data = load_json(USER_EXPIRATION_FILE, {})
    data[username] = {
        'expiration': expiration.isoformat() if expiration else None,
        'transfer_limit': transfer_limit
    }
    save_json(USER_EXPIRATION_FILE, data)

def get_user_expiration(username):
    """Получает срок действия подписки пользователя."""
    data = load_json(USER_EXPIRATION_FILE, {})
    user_data = data.get(username, {})
    expiration = user_data.get('expiration')
    return datetime.fromisoformat(expiration) if expiration else None

def remove_user_expiration(username):
    """Удаляет информацию о сроке действия подписки пользователя."""
    data = load_json(USER_EXPIRATION_FILE, {})
    if username in data:
        del data[username]
        save_json(USER_EXPIRATION_FILE, data)

def set_user_telegram_id(username, telegram_id):
    """Связывает имя пользователя с Telegram ID."""
    data = load_json(USER_TELEGRAM_FILE, {})
    data[username] = telegram_id
    save_json(USER_TELEGRAM_FILE, data)

def get_user_telegram_id(username):
    """Получает Telegram ID пользователя по имени."""
    data = load_json(USER_TELEGRAM_FILE, {})
    return data.get(username)

def add_promocode(code, discount, expires_at, max_uses, subscription_period):
    """Добавляет новый промокод."""
    promocodes = load_json(PROMOCODES_FILE, {})
    if code in promocodes:
        return False
    promocodes[code] = {
        'discount': discount,
        'expires_at': expires_at.isoformat() if expires_at else None,
        'max_uses': max_uses,
        'uses': 0,
        'subscription_period': subscription_period
    }
    save_json(PROMOCODES_FILE, promocodes)
    return True

def apply_promocode(code):
    """Применяет промокод, увеличивает счетчик использований."""
    promocodes = load_json(PROMOCODES_FILE, {})
    now = datetime.now(pytz.utc)
    promo = promocodes.get(code)
    if not promo:
        return None
    if promo['expires_at'] and datetime.fromisoformat(promo['expires_at']) < now:
        return None
    if promo['max_uses'] is not None and promo['uses'] >= promo['max_uses']:
        return None
    promo['uses'] += 1
    save_json(PROMOCODES_FILE, promocodes)
    return {
        'discount': promo['discount'],
        'subscription_period': promo['subscription_period']
    }

def get_promocodes():
    """Возвращает список всех промокодов."""
    promocodes = load_json(PROMOCODES_FILE, {})
    result = {}
    for code, info in promocodes.items():
        result[code] = {
            'discount': info['discount'],
            'expires_at': datetime.fromisoformat(info['expires_at']) if info['expires_at'] else None,
            'max_uses': info['max_uses'],
            'uses': info['uses'],
            'subscription_period': info['subscription_period']
        }
    return result

def remove_promocode(code):
    """Удаляет промокод."""
    promocodes = load_json(PROMOCODES_FILE, {})
    if code in promocodes:
        del promocodes[code]
        save_json(PROMOCODES_FILE, promocodes)
        return True
    return False

def add_payment(user_id, payment_id, amount, status, period=None):
    """Добавляет информацию о платеже."""
    payments = load_json(PAYMENTS_FILE, {})
    payments[payment_id] = {
        'user_id': user_id,
        'amount': amount,
        'status': status,
        'period': period,
        'created_at': datetime.now(pytz.utc).isoformat()
    }
    save_json(PAYMENTS_FILE, payments)

def update_payment_status(payment_id, status):
    """Обновляет статус платежа."""
    payments = load_json(PAYMENTS_FILE, {})
    if payment_id in payments:
        payments[payment_id]['status'] = status
        save_json(PAYMENTS_FILE, payments)
        return True
    return False

def get_pending_payments():
    """Возвращает список незавершенных платежей."""
    payments = load_json(PAYMENTS_FILE, {})
    return [
        (p['user_id'], payment_id, p['amount'], p['period'])
        for payment_id, p in payments.items()
        if p['status'] == 'pending'
    ]
