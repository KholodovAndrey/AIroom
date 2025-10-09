"""
Скрипт миграции существующих данных на зашифрованное хранение
"""
import sqlite3
from database import db
from lavacrypto import crypto

def migrate_existing_data():
    """Миграция существующих данных в зашифрованный формат"""
    conn = sqlite3.connect('fashion_bot.db')
    cursor = conn.cursor()
    
    try:
        # Проверяем существование старых таблиц/полей
        cursor.execute("PRAGMA table_info(users)")
        columns = [column[1] for column in cursor.fetchall()]
        
        # Если есть старые незашифрованные поля
        if 'username' in columns and 'username_encrypted' not in columns:
            print("🔧 Миграция данных пользователей...")
            
            # Получаем всех пользователей
            cursor.execute('SELECT user_id, username, full_name FROM users')
            users = cursor.fetchall()
            
            for user_id, username, full_name in users:
                # Шифруем данные
                username_encrypted = crypto.encrypt(username or "")
                full_name_encrypted = crypto.encrypt(full_name or "")
                
                # Обновляем запись
                cursor.execute('''
                    UPDATE users 
                    SET username_encrypted = ?, full_name_encrypted = ?
                    WHERE user_id = ?
                ''', (username_encrypted, full_name_encrypted, user_id))
            
            print(f"✅ Мигрировано {len(users)} пользователей")
        
        # Миграция промптов генераций
        cursor.execute("PRAGMA table_info(generations)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'prompt' in columns and 'prompt_encrypted' not in columns:
            print("🔧 Миграция промптов генераций...")
            
            cursor.execute('SELECT id, prompt FROM generations')
            generations = cursor.fetchall()
            
            for gen_id, prompt in generations:
                prompt_encrypted = crypto.encrypt(prompt or "")
                cursor.execute(
                    'UPDATE generations SET prompt_encrypted = ? WHERE id = ?',
                    (prompt_encrypted, gen_id)
                )
            
            print(f"✅ Мигрировано {len(generations)} генераций")
        
        conn.commit()
        print("🎉 Миграция завершена успешно!")
        
    except Exception as e:
        print(f"❌ Ошибка миграции: {e}")
    finally:
        conn.close()

if __name__ == '__main__':
    migrate_existing_data()