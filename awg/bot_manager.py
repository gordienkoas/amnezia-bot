# db.py
import sqlite3

conn = sqlite3.connect('bot.db', check_same_thread=False)
cursor = conn.cursor()

# Создание таблицы admins
cursor.execute('''CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)''')
conn.commit()

def get_admins():
    cursor.execute("SELECT user_id FROM admins")
    return [row[0] for row in cursor.fetchall()]

def add_admin(user_id):
    cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (user_id,))
    conn.commit()

def remove_admin(user_id):
    cursor.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
    conn.commit()

# Инициализация первого админа (замените на ваш ID)
def init_first_admin(admin_id):
    cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (admin_id,))
    conn.commit()

# Пример вызова при старте бота
# init_first_admin(123456789)  # Ваш Telegram ID
