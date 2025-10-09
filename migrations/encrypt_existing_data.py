import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from database.database import SessionLocal
from database.models import User, Conversation
from utils.encryption import field_encryptor

def migrate_existing_data():
    session = SessionLocal()
    
    try:
        # Мигрируем пользователей
        users = session.query(User).all()
        for user in users:
            if user._email and not user._email.startswith('gAAAA'):  # Если не зашифровано
                user.email = user._email  # Вызовет сеттер для шифрования
        
        # Мигрируем беседы
        conversations = session.query(Conversation).all()
        for conv in conversations:
            if conv._messages and not conv._messages.startswith('gAAAA'):
                try:
                    messages_data = json.loads(conv._messages)
                    conv.messages = messages_data  # Вызовет сеттер для шифрования
                except:
                    pass
        
        session.commit()
        print("Миграция данных завершена успешно!")
        
    except Exception as e:
        session.rollback()
        print(f"Ошибка миграции: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    migrate_existing_data()