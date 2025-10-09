"""
Конфигурация и переменные окружения для Fashion Bot
"""
import os
import sys
import logging
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

# Конфигурация из .env
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@bnbslow")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Демо-режим
# True - генерировать демо-изображения для тестирования
# False - использовать реальный Gemini 2.5 Flash Image API
GEMINI_DEMO_MODE = False  # Отключен - используем реальный API!

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Проверка обязательных переменных
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN не установлен в .env файле")
    sys.exit(1)

