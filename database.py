"""
Работа с базой данных SQLite
"""
import sqlite3
from typing import Tuple
from config import logger


class Database:
    """Класс для работы с базой данных"""
    
    def __init__(self, db_name: str = 'fashion_bot.db'):
        self.db_name = db_name
        self._init_db()
    
    def _get_connection(self) -> sqlite3.Connection:
        """Создает новое подключение к БД"""
        return sqlite3.connect(self.db_name, check_same_thread=False)
    
    def _init_db(self):
        """Инициализация базы данных"""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                balance INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS generations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                prompt TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')

        conn.commit()
        conn.close()
        logger.info("✅ База данных инициализирована")

    def get_user_balance(self, user_id: int) -> int:
        """Получить баланс пользователя"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            
            if result:
                return result[0]
            else:
                cursor.execute(
                    'INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, 0)',
                    (user_id,)
                )
                conn.commit()
                return 0
        finally:
            conn.close()

    def update_user_balance(self, user_id: int, balance: int):
        """Обновить баланс пользователя"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                'INSERT OR REPLACE INTO users (user_id, balance) VALUES (?, ?)',
                (user_id, balance)
            )
            conn.commit()
        finally:
            conn.close()

    def add_generation(self, user_id: int, prompt: str):
        """Добавить запись о генерации"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                'INSERT INTO generations (user_id, prompt) VALUES (?, ?)',
                (user_id, prompt)
            )
            conn.commit()
        finally:
            conn.close()

    def get_user_generations_count(self, user_id: int) -> int:
        """Получить количество генераций пользователя"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                'SELECT COUNT(*) FROM generations WHERE user_id = ?',
                (user_id,)
            )
            return cursor.fetchone()[0]
        finally:
            conn.close()

    def get_all_users_stats(self) -> Tuple[int, int, int]:
        """Получить общую статистику"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute('SELECT COUNT(*) FROM users')
            total_users = cursor.fetchone()[0]

            cursor.execute('SELECT COUNT(*) FROM generations')
            total_generations = cursor.fetchone()[0]

            cursor.execute('SELECT SUM(balance) FROM users')
            total_balance = cursor.fetchone()[0] or 0

            return total_users, total_generations, total_balance
        finally:
            conn.close()

