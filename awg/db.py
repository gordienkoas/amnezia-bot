import os
import subprocess
import configparser
import json
import pytz
import socket
import logging
import tempfile
import shutil
from datetime import datetime, timedelta
from typing import Optional, List, Tuple, Dict, Any

EXPIRATIONS_FILE = 'files/expirations.json'
PAYMENTS_FILE = 'files/payments.json'
ADMINS_FILE = 'files/admins.json'
MODERATORS_FILE = 'files/moderators.json'
PROMOCODES_FILE = 'files/promocodes.json'
USER_TELEGRAM_IDS_FILE = 'files/user_telegram_ids.json'
CONFIG_FILE = 'files/setting.ini'
BACKUP_DIR = 'files/backups'
LOG_FILE = 'files/db.log'
UTC = pytz.UTC

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def ensure_files_exist():
    """Создаёт все необходимые файлы, если они отсутствуют."""
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    os.makedirs(os.path.dirname(EXPIRATIONS_FILE), exist_ok=True)
    os.makedirs(os.path.dirname(BACKUP_DIR), exist_ok=True)

    for file_path in [EXPIRATIONS_FILE, PAYMENTS_FILE, ADMINS_FILE, MODERATORS_FILE, PROMOCODES_FILE, USER_TELEGRAM_IDS_FILE]:
        if not os.path.exists(file_path):
            with open(file_path, 'w') as f:
                json.dump({}, f) if file_path != PAYMENTS_FILE else json.dump([], f)

def get_amnezia_container() -> str:
    """Получает имя запущенного Docker-контейнера Amnezia AWG."""
    cmd = "docker ps --filter 'name=amnezia-awg' --format '{{.Names}}'"
    try:
        output = subprocess.check_output(cmd, shell=True).decode().strip()
        if output:
            return output
        else:
            logger.error("Docker-контейнер 'amnezia-awg' не найден или не запущен.")
            raise RuntimeError("Контейнер не найден")
    except subprocess.CalledProcessError as e:
        logger.error(f"Ошибка при поиске контейнера: {e}")
        raise

def create_config(path: str = CONFIG_FILE) -> None:
    """Создаёт конфигурационный файл с настройками бота."""
    ensure_files_exist()
    config = configparser.ConfigParser()
    config.add_section("setting")

    print("Настройка конфигурации бота...")
    bot_token = input('Введите токен Telegram бота: ').strip()
    admin_ids_input = input('Введите Telegram ID администраторов через запятую (например, 12345,67890): ').strip()
    admin_ids = [admin_id.strip() for admin_id in admin_ids_input.split(',') if admin_id.strip()]
    moderator_ids_input = input('Введите Telegram ID модераторов через запятую (опционально): ').strip()
    moderator_ids = [mod_id.strip() for mod_id in moderator_ids_input.split(',') if mod_id.strip()]
    yoomoney_token = input('Введите токен YooMoney: ').strip()
    yoomoney_wallet = input('Введите номер кошелька YooMoney (15-18 цифр): ').strip()
    pricing = {
        '1_month': 1000.0,
        '3_months': 2500.0,
        '6_months': 4500.0,
        '12_months': 8000.0
    }

    try:
        docker_container = get_amnezia_container()
    except RuntimeError:
        docker_container = input('Введите имя Docker-контейнера Amnezia AWG: ').strip()

    config.set("setting", "bot_token", bot_token)
    config.set("setting", "admin_ids", ",".join(admin_ids))
    config.set("setting", "moderator_ids", ",".join(moderator_ids))
    config.set("setting", "wg_config_file", "/root/amnezia-bot/amnezia-awg.conf")
    config.set("setting", "docker_container", docker_container)
    config.set("setting", "endpoint", socket.gethostbyname(socket.gethostname()))
    config.set("setting", "yoomoney_token", yoomoney_token)
    config.set("setting", "yoomoney_wallet", yoomoney_wallet)
    config.set("setting", "pricing", json.dumps(pricing))

    with open(path, 'w') as configfile:
        config.write(configfile)

    with open(ADMINS_FILE, 'w') as f:
        json.dump(admin_ids, f)
    with open(MODERATORS_FILE, 'w') as f:
        json.dump(moderator_ids, f)

    logger.info("Конфигурация успешно создана.")

def get_config(path: str = CONFIG_FILE) -> Dict[str, Any]:
    """Читает конфигурацию из файла."""
    if not os.path.exists(path):
        create_config(path)
    
    config = configparser.ConfigParser()
    config.read(path)
    
    settings = {
        'bot_token': config.get("setting", "bot_token", fallback=""),
        'admin_ids': [id for id in config.get("setting", "admin_ids", fallback="").split(",") if id],
        'moderator_ids': [id for id in config.get("setting", "moderator_ids", fallback="").split(",") if id],
        'wg_config_file': config.get("setting", "wg_config_file", fallback=""),
        'docker_container': config.get("setting", "docker_container", fallback=""),
        'endpoint': config.get("setting", "endpoint", fallback=""),
        'yoomoney_token': config.get("setting", "yoomoney_token", fallback=""),
        'yoomoney_wallet': config.get("setting", "yoomoney_wallet", fallback=""),
        'pricing': json.loads(config.get(
            "setting", "pricing",
            fallback='{"1_month": 1000.0, "3_months": 2500.0, "6_months": 4500.0, "12_months": 8000.0}'
        ))
    }
    
    return settings

def set_yoomoney_config(token: Optional[str] = None, wallet: Optional[str] = None, path: str = CONFIG_FILE) -> None:
    """Обновляет настройки YooMoney в конфигурации."""
    config = configparser.ConfigParser()
    config.read(path)
    
    if token:
        config.set("setting", "yoomoney_token", token)
    if wallet:
        config.set("setting", "yoomoney_wallet", wallet)
    
    with open(path, 'w') as configfile:
        config.write(configfile)
    logger.info(f"YooMoney settings updated: token={'***' if token else 'unchanged'}, wallet={wallet or 'unchanged'}")

def set_pricing(period: str, price: float, path: str = CONFIG_FILE) -> None:
    """Обновляет цену для указанного периода подписки."""
    config = configparser.ConfigParser()
    config.read(path)
    
    pricing = json.loads(config.get(
        "setting", "pricing",
        fallback='{"1_month": 1000.0, "3_months": 2500.0, "6_months": 4500.0, "12_months": 8000.0}'
    ))
    pricing[period] = float(price)
    config.set("setting", "pricing", json.dumps(pricing))
    
    with open(path, 'w') as configfile:
        config.write(configfile)
    logger.info(f"Pricing updated: {period} set to ₽{price}")

def root_add(user_name: str, ipv6: bool = False) -> bool:
    """Добавляет нового пользователя через скрипт newclient.sh."""
    cmd = ["/root/amnezia-bot/newclient.sh", user_name]
    if not ipv6:
        cmd.append("no-ipv6")
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        logger.info(f"Пользователь {user_name} успешно добавлен: {result.stdout}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Ошибка при добавлении пользователя {user_name}: {e.stderr}")
        return False

def deactive_user_db(user_name: str) -> bool:
    """Деактивирует пользователя через скрипт removeclient.sh."""
    cmd = ["/root/amnezia-bot/removeclient.sh", user_name]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        logger.info(f"Пользователь {user_name} успешно деактивирован: {result.stdout}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Ошибка при деактивации пользователя {user_name}: {e.stderr}")
        return False

def get_client_list() -> List[Tuple[str, str, str]]:
    """Получает список всех клиентов из WireGuard."""
    cmd = "docker exec $(docker ps -q -f name=amnezia-awg) awg show all dump"
    try:
        output = subprocess.check_output(cmd, shell=True, text=True).strip()
        clients = []
        for line in output.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 5:
                clients.append((parts[0], parts[2], parts[3]))
        return clients
    except subprocess.CalledProcessError as e:
        logger.error(f"Ошибка при получении списка клиентов: {e}")
        return []

def get_active_list() -> List[Tuple[str, str, str, str]]:
    """Получает список активных клиентов с информацией о последнем подключении."""
    cmd = "docker exec $(docker ps -q -f name=amnezia-awg) awg show all latest-handshakes transfer peer"
    try:
        output = subprocess.check_output(cmd, shell=True, text=True).strip()
        active_clients = []
        for line in output.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 4:
                active_clients.append((parts[0], parts[1], parts[2], parts[3]))
        return active_clients
    except subprocess.CalledProcessError as e:
        logger.error(f"Ошибка при получении активных клиентов: {e}")
        return []

def set_user_expiration(user_name: str, expiration_date: Optional[datetime], traffic_limit: str, path: str = EXPIRATIONS_FILE) -> None:
    """Устанавливает срок действия и лимит трафика для пользователя."""
    try:
        with open(path, 'r') as f:
            expirations = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        expirations = {}
    
    expirations[user_name] = {
        'expiration_date': expiration_date.isoformat() if expiration_date else None,
        'traffic_limit': traffic_limit
    }
    
    with open(path, 'w') as f:
        json.dump(expirations, f, indent=2)
    logger.info(f"Expiration set for {user_name}: {expiration_date}")

def get_user_expiration(user_name: str, path: str = EXPIRATIONS_FILE) -> Optional[datetime]:
    """Получает срок действия подписки пользователя."""
    try:
        with open(path, 'r') as f:
            expirations = json.load(f)
        user_data = expirations.get(user_name, {})
        expiration = user_data.get('expiration_date')
        return datetime.fromisoformat(expiration) if expiration else None
    except (FileNotFoundError, json.JSONDecodeError):
        return None

def remove_user_expiration(user_name: str, path: str = EXPIRATIONS_FILE) -> None:
    """Удаляет информацию о сроке действия пользователя."""
    try:
        with open(path, 'r') as f:
            expirations = json.load(f)
        expirations.pop(user_name, None)
        with open(path, 'w') as f:
            json.dump(expirations, f, indent=2)
        logger.info(f"Expiration removed for {user_name}")
    except (FileNotFoundError, json.JSONDecodeError):
        pass

def clear_old_keys(before_date: str, path: str = EXPIRATIONS_FILE) -> bool:
    """Удаляет пользователей с истёкшими ключами до указанной даты."""
    try:
        before = datetime.fromisoformat(before_date)
        with open(path, 'r') as f:
            expirations = json.load(f)
        
        users_to_remove = [
            user for user, data in expirations.items()
            if data.get('expiration_date') and datetime.fromisoformat(data['expiration_date']) < before
        ]
        
        for user in users_to_remove:
            deactive_user_db(user)
            expirations.pop(user, None)
            remove_user_telegram_id(user)
            user_dir = os.path.join('users', user)
            if os.path.exists(user_dir):
                shutil.rmtree(user_dir, ignore_errors=True)
        
        with open(path, 'w') as f:
            json.dump(expirations, f, indent=2)
        logger.info(f"Removed {len(users_to_remove)} expired keys before {before_date}")
        return bool(users_to_remove)
    except Exception as e:
        logger.error(f"Ошибка при очистке старых ключей: {str(e)}")
        return False

def add_payment(user_id: int, payment_id: str, amount: float, status: str, path: str = PAYMENTS_FILE) -> None:
    """Добавляет запись о платеже."""
    try:
        with open(path, 'r') as f:
            payments = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        payments = []
    
    payments.append({
        'user_id': user_id,
        'payment_id': payment_id,
        'amount': amount,
        'status': status,
        'created_at': datetime.now(UTC).isoformat()
    })
    
    with open(path, 'w') as f:
        json.dump(payments, f, indent=2)
    logger.info(f"Payment added: {payment_id} for user {user_id}")

def update_payment_status(payment_id: str, status: str, path: str = PAYMENTS_FILE) -> None:
    """Обновляет статус платежа."""
    try:
        with open(path, 'r') as f:
            payments = json.load(f)
        for payment in payments:
            if payment['payment_id'] == payment_id:
                payment['status'] = status
                payment['updated_at'] = datetime.now(UTC).isoformat()
                break
        with open(path, 'w') as f:
            json.dump(payments, f, indent=2)
        logger.info(f"Payment status updated: {payment_id} to {status}")
    except (FileNotFoundError, json.JSONDecodeError):
        pass

def get_pending_payments(path: str = PAYMENTS_FILE) -> List[Tuple[int, str, float, str]]:
    """Получает список незавершённых платежей."""
    try:
        with open(path, 'r') as f:
            payments = json.load(f)
        return [
            (p['user_id'], p['payment_id'], p['amount'], p['created_at'])
            for p in payments if p['status'] == 'pending'
        ]
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def add_admin(admin_id: int, path: str = ADMINS_FILE) -> None:
    """Добавляет нового администратора."""
    try:
        with open(path, 'r') as f:
            admins = json.load(f)
        admin_id_str = str(admin_id)
        if admin_id_str not in admins:
            admins.append(admin_id_str)
            with open(path, 'w') as f:
                json.dump(admins, f, indent=2)
            logger.info(f"Admin added: {admin_id}")
    except (FileNotFoundError, json.JSONDecodeError):
        with open(path, 'w') as f:
            json.dump([str(admin_id)], f)
        logger.info(f"Admin file created and admin added: {admin_id}")

def remove_admin(admin_id: int, path: str = ADMINS_FILE) -> None:
    """Удаляет администратора."""
    try:
        with open(path, 'r') as f:
            admins = json.load(f)
        admin_id_str = str(admin_id)
        if admin_id_str in admins:
            admins.remove(admin_id_str)
            with open(path, 'w') as f:
                json.dump(admins, f, indent=2)
            logger.info(f"Admin removed: {admin_id}")
    except (FileNotFoundError, json.JSONDecodeError):
        pass

def add_moderator(moderator_id: int, path: str = MODERATORS_FILE) -> None:
    """Добавляет нового модератора."""
    try:
        with open(path, 'r') as f:
            moderators = json.load(f)
        moderator_id_str = str(moderator_id)
        if moderator_id_str not in moderators:
            moderators.append(moderator_id_str)
            with open(path, 'w') as f:
                json.dump(moderators, f, indent=2)
            logger.info(f"Moderator added: {moderator_id}")
    except (FileNotFoundError, json.JSONDecodeError):
        with open(path, 'w') as f:
            json.dump([str(moderator_id)], f)
        logger.info(f"Moderator file created and moderator added: {moderator_id}")

def remove_moderator(moderator_id: int, path: str = MODERATORS_FILE) -> None:
    """Удаляет модератора."""
    try:
        with open(path, 'r') as f:
            moderators = json.load(f)
        moderator_id_str = str(moderator_id)
        if moderator_id_str in moderators:
            moderators.remove(moderator_id_str)
            with open(path, 'w') as f:
                json.dump(moderators, f, indent=2)
            logger.info(f"Moderator removed: {moderator_id}")
    except (FileNotFoundError, json.JSONDecodeError):
        pass

def add_promocode(code: str, discount: float, expires_at: Optional[datetime], max_uses: Optional[int], subscription_period: Optional[str], path: str = PROMOCODES_FILE) -> bool:
    """Добавляет новый промокод."""
    try:
        with open(path, 'r') as f:
            promocodes = json.load(f)
        if code in promocodes:
            logger.warning(f"Promocode {code} already exists")
            return False
        promocodes[code] = {
            'discount': float(discount),
            'expires_at': expires_at.isoformat() if expires_at else None,
            'max_uses': max_uses,
            'uses': 0,
            'subscription_period': subscription_period
        }
        with open(path, 'w') as f:
            json.dump(promocodes, f, indent=2)
        logger.info(f"Promocode added: {code}")
        return True
    except (FileNotFoundError, json.JSONDecodeError):
        promocodes = {code: {
            'discount': float(discount),
            'expires_at': expires_at.isoformat() if expires_at else None,
            'max_uses': max_uses,
            'uses': 0,
            'subscription_period': subscription_period
        }}
        with open(path, 'w') as f:
            json.dump(promocodes, f, indent=2)
        logger.info(f"Promocode file created and promocode added: {code}")
        return True

def remove_promocode(code: str, path: str = PROMOCODES_FILE) -> bool:
    """Удаляет промокод."""
    try:
        with open(path, 'r') as f:
            promocodes = json.load(f)
        if code in promocodes:
            del promocodes[code]
            with open(path, 'w') as f:
                json.dump(promocodes, f, indent=2)
            logger.info(f"Promocode removed: {code}")
            return True
        return False
    except (FileNotFoundError, json.JSONDecodeError):
        return False

def get_promocodes(path: str = PROMOCODES_FILE) -> Dict[str, Dict]:
    """Получает список всех промокодов."""
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def apply_promocode(code: str, path: str = PROMOCODES_FILE) -> Optional[Dict[str, Any]]:
    """Применяет промокод и возвращает его данные, если он действителен."""
    try:
        with open(path, 'r') as f:
            promocodes = json.load(f)
        promo = promocodes.get(code)
        if not promo:
            return None
        expires_at = datetime.fromisoformat(promo['expires_at']) if promo['expires_at'] else None
        if expires_at and expires_at < datetime.now(UTC):
            logger.info(f"Promocode {code} expired")
            return None
        if promo['max_uses'] is not None and promo['uses'] >= promo['max_uses']:
            logger.info(f"Promocode {code} max uses reached")
            return None
        promo['uses'] += 1
        with open(path, 'w') as f:
            json.dump(promocodes, f, indent=2)
        logger.info(f"Promocode applied: {code}")
        return {
            'discount': promo['discount'],
            'subscription_period': promo['subscription_period']
        }
    except (FileNotFoundError, json.JSONDecodeError):
        return None

def set_user_telegram_id(user_name: str, telegram_id: Optional[int], path: str = USER_TELEGRAM_IDS_FILE) -> None:
    """Сопоставляет имя пользователя с Telegram ID."""
    try:
        with open(path, 'r') as f:
            user_ids = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        user_ids = {}
    
    user_ids[user_name] = telegram_id
    with open(path, 'w') as f:
        json.dump(user_ids, f, indent=2)
    logger.info(f"Telegram ID {telegram_id} set for user {user_name}")

def get_user_telegram_id(user_name: str, path: str = USER_TELEGRAM_IDS_FILE) -> Optional[int]:
    """Получает Telegram ID пользователя."""
    try:
        with open(path, 'r') as f:
            user_ids = json.load(f)
        return user_ids.get(user_name)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

def remove_user_telegram_id(user_name: str, path: str = USER_TELEGRAM_IDS_FILE) -> None:
    """Удаляет Telegram ID пользователя."""
    try:
        with open(path, 'r') as f:
            user_ids = json.load(f)
        user_ids.pop(user_name, None)
        with open(path, 'w') as f:
            json.dump(user_ids, f, indent=2)
        logger.info(f"Telegram ID removed for user {user_name}")
    except (FileNotFoundError, json.JSONDecodeError):
        pass

def create_backup() -> str:
    """Создаёт резервную копию всех данных."""
    backup_filename = os.path.join(BACKUP_DIR, f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip")
    os.makedirs(BACKUP_DIR, exist_ok=True)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_files_dir = os.path.join(temp_dir, 'files')
        temp_users_dir = os.path.join(temp_dir, 'users')
        shutil.copytree('files', temp_files_dir, ignore=shutil.ignore_patterns('backups'))
        if os.path.exists('users'):
            shutil.copytree('users', temp_users_dir)
        
        with zipfile.ZipFile(backup_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(temp_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, temp_dir)
                    zipf.write(file_path, arcname)
    
    logger.info(f"Backup created: {backup_filename}")
    return backup_filename

def get_user_traffic(user_name: str) -> Tuple[int, int]:
    """Получает данные о трафике пользователя (входящий/исходящий)."""
    active_clients = get_active_list()
    for client in active_clients:
        if client[0] == user_name and len(client) > 2:
            try:
                incoming, outgoing = client[2].split('/')
                incoming_bytes = humanize.parse_bytes(incoming.strip())
                outgoing_bytes = humanize.parse_bytes(outgoing.strip())
                return incoming_bytes, outgoing_bytes
            except:
                pass
    return 0, 0

def get_user_status(user_name: str) -> str:
    """Получает статус пользователя (онлайн/офлайн)."""
    active_clients = get_active_list()
    for client in active_clients:
        if client[0] == user_name and len(client) > 1:
            last_handshake = client[1]
            if last_handshake.lower() in ['never', 'нет данных', '-']:
                return "offline"
            try:
                handshake_time = parse_relative_time(last_handshake)
                if (datetime.now(UTC) - handshake_time).total_seconds() <= 60:
                    return "online"
            except:
                pass
    return "offline"

def parse_relative_time(relative_str: str) -> datetime:
    """Парсит относительное время (например, '2 minutes ago') в datetime."""
    if not isinstance(relative_str, str) or not relative_str.strip():
        return datetime.now(UTC)
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
        return datetime.now(UTC) - timedelta(seconds=delta)
    except Exception as e:
        logger.error(f"Ошибка в parse_relative_time: {str(e)}")
        return datetime.now(UTC)

if __name__ == "__main__":
    # Тестирование или инициализация
    ensure_files_exist()
    if not os.path.exists(CONFIG_FILE):
        create_config()
