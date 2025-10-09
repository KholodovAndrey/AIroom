import asyncio
import logging
import sqlite3
import os
import sys
import tempfile
import base64
import json
from enum import Enum
from typing import Dict, Any
from dataclasses import dataclass
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, FSInputFile, BufferedInputFile
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.utils.media_group import MediaGroupBuilder

# Gemini imports
from google import genai
from google.api_core import exceptions
from PIL import Image
import io

# Загрузка переменных окружения
load_dotenv()

# Конфигурация из .env
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@bnbslow")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not BOT_TOKEN:
    print("❌ BOT_TOKEN не установлен в .env файле")
    sys.exit(1)

if not GEMINI_API_KEY:
    print("❌ GEMINI_API_KEY не установлен в .env файле")
    sys.exit(1)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('fashion_bot.db', check_same_thread=False)
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

init_db()

class GenderType(Enum):
    WOMEN = "женская"
    MEN = "мужская" 
    KIDS = "детская"
    DISPLAY = "витринное фото"

class LocationType(Enum):
    STREET = "Улица"
    STUDIO = "Фотостудия"
    FLOOR_ZONE = "Фотозона на полу"

class AgeGroup(Enum):
    WOMEN_MEN = ["18-20", "22-28", "32-40", "42-55"]
    KIDS = ["0.3-1", "2-4", "7-10", "13-17"]

class SizeType(Enum):
    SIZE_42_46 = "42-46"
    SIZE_50_54 = "50-54"
    SIZE_58_64 = "58-64"
    SIZE_64_68 = "64-68"

class LocationStyle(Enum):
    NEW_YEAR = "Новогодняя атмосфера"
    SUMMER = "Лето" 
    NATURE = "Природа"
    PARK_WINTER = "Парк (зима)"
    PARK_SUMMER = "Парк (лето)"
    REGULAR = "обычный"
    CAR = "Рядом с машиной"

class PoseType(Enum):
    SITTING = "Сидя"
    STANDING = "Стоя"

class ViewType(Enum):
    BACK = "Сзади"
    FRONT = "Передняя часть"

# Состояния FSM
class ProductCreationStates(StatesGroup):
    waiting_for_gender = State()
    waiting_for_photo = State()
    waiting_for_height = State()
    waiting_for_location = State()
    waiting_for_age = State()
    waiting_for_size = State()
    waiting_for_location_style = State()
    waiting_for_pose = State()
    waiting_for_view = State()
    waiting_for_confirmation = State()

class Database:
    def __init__(self):
        self.conn = sqlite3.connect('fashion_bot.db', check_same_thread=False)
        self.cursor = self.conn.cursor()

    def get_user_balance(self, user_id: int) -> int:
        self.cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
        result = self.cursor.fetchone()
        if result:
            return result[0]
        else:
            self.cursor.execute(
                'INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, 0)',
                (user_id,)
            )
            self.conn.commit()
            return 0

    def update_user_balance(self, user_id: int, balance: int):
        self.cursor.execute(
            'INSERT OR REPLACE INTO users (user_id, balance) VALUES (?, ?)',
            (user_id, balance)
        )
        self.conn.commit()

    def add_generation(self, user_id: int, prompt: str):
        self.cursor.execute(
            'INSERT INTO generations (user_id, prompt) VALUES (?, ?)',
            (user_id, prompt)
        )
        self.conn.commit()

    def get_user_generations_count(self, user_id: int) -> int:
        self.cursor.execute(
            'SELECT COUNT(*) FROM generations WHERE user_id = ?',
            (user_id,)
        )
        return self.cursor.fetchone()[0]

    def get_all_users_stats(self):
        self.cursor.execute('SELECT COUNT(*) FROM users')
        total_users = self.cursor.fetchone()[0]
        
        self.cursor.execute('SELECT COUNT(*) FROM generations')
        total_generations = self.cursor.fetchone()[0]
        
        self.cursor.execute('SELECT SUM(balance) FROM users')
        total_balance = self.cursor.fetchone()[0] or 0
        
        return total_users, total_generations, total_balance

# --- Функция вызова API Gemini ---
def call_nano_banana_api(
    input_image_path: str, 
    prompt: str, 
    extra_params: Dict[str, Any] = None
) -> bytes:
    """
    Отправляет изображение и промпт в Gemini 2.5 Flash Image (Nano Banana) 
    и корректно извлекает бинарные данные из ответа.
    """
    
    # 1. Загрузка API Key и инициализация клиента
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        raise Exception(f"Ошибка инициализации Gemini клиента. Проверьте GEMINI_API_KEY в .env. Детали: {e}")

    # 2. Чтение изображения с помощью Pillow
    try:
        input_image = Image.open(input_image_path)
    except FileNotFoundError:
        raise ValueError(f"Исходный файл не найден: {input_image_path}")
    except Exception as e:
        raise ValueError(f"Ошибка чтения изображения: {e}")

    logger.info(f"Отправка промпта '{prompt[:50]}...' и изображения в Gemini...")

    # 3. Вызов модели Image-to-Image
    config_params = extra_params if extra_params is not None else {}
    
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash-image",
            contents=[prompt, input_image],
            **config_params
        )
    except exceptions.PermissionDenied as e:
        if "location is not supported" in str(e).lower():
            raise Exception(
                "❌ Сервис недоступен в вашем регионе. "
                "Для использования Gemini API требуется VPN или настройка в поддерживаемом регионе."
            )
        else:
            raise e
    except exceptions.FailedPrecondition as e:
        if "location is not supported" in str(e).lower():
            raise Exception(
                "❌ Сервис недоступен в вашем регионе. "
                "Для использования Gemini API требуется VPN или настройка в поддерживаемом регионе."
            )
        else:
            raise e
    except Exception as e:
        raise Exception(f"Ошибка API: {str(e)}")
    
    # 4. Детальное логирование структуры ответа
    logger.info("=== ДЕТАЛЬНАЯ ИНФОРМАЦИЯ ОБ ОТВЕТЕ GEMINI ===")
    logger.info(f"Количество кандидатов: {len(response.candidates) if response.candidates else 0}")
    
    if response.candidates:
        for i, candidate in enumerate(response.candidates):
            logger.info(f"Кандидат {i}:")
            logger.info(f"  Финиш-причина: {getattr(candidate, 'finish_reason', 'N/A')}")
            logger.info(f"  Рейтинг безопасности: {getattr(candidate, 'safety_ratings', 'N/A')}")
            
            if hasattr(candidate, 'content') and candidate.content:
                logger.info(f"  Количество частей контента: {len(candidate.content.parts) if candidate.content.parts else 0}")
                
                if candidate.content.parts:
                    for j, part in enumerate(candidate.content.parts):
                        logger.info(f"  Часть {j}:")
                        logger.info(f"    Тип: {type(part)}")
                        logger.info(f"    Атрибуты: {dir(part)}")
                        
                        # Логируем все атрибуты части
                        for attr in dir(part):
                            if not attr.startswith('_'):
                                try:
                                    value = getattr(part, attr)
                                    if not callable(value):
                                        logger.info(f"    {attr}: {type(value)} = {str(value)[:200]}...")
                                except Exception as attr_e:
                                    logger.info(f"    {attr}: ОШИБКА получения - {attr_e}")
            else:
                logger.info("  Контент отсутствует или пуст")
    else:
        logger.info("Кандидаты отсутствуют в ответе")
    
    logger.info("=== КОНЕЦ ДЕТАЛЬНОЙ ИНФОРМАЦИИ ===")
    
    # 5. Корректная обработка и извлечение изображения
    if not response.candidates:
        raise Exception("API не вернул кандидатов (candidates).")
    
    if not response.candidates[0].content.parts:
        raise Exception("API не вернул частей контента (parts).")

    first_part = response.candidates[0].content.parts[0]

    # 1. Проверяем, что это не текстовый ответ (фильтры безопасности)
    if not hasattr(first_part, 'inline_data'):
        if hasattr(first_part, 'text'):
            # Возвращаем текст ошибки, если Gemini отказался генерировать
            raise Exception(f"Gemini вернул текст вместо изображения (отклонен фильтром?): {first_part.text}")
        else:
            # Детальное логирование структуры части
            logger.error("Неизвестная структура части контента:")
            for attr in dir(first_part):
                if not attr.startswith('_'):
                    try:
                        value = getattr(first_part, attr)
                        if not callable(value):
                            logger.error(f"  {attr}: {type(value)} = {str(value)[:500]}")
                    except Exception as attr_e:
                        logger.error(f"  {attr}: ОШИБКА получения - {attr_e}")
            raise Exception("Получен ответ неизвестной структуры.")

    # 2. Получаем объект InlineData
    inline_data = first_part.inline_data
    logger.info(f"Объект inline_data: {type(inline_data)}")
    logger.info(f"Атрибуты inline_data: {[attr for attr in dir(inline_data) if not attr.startswith('_')]}")

    # 3. Извлекаем бинарные данные
    output_image_bytes = None
    
    # Проверяем все возможные атрибуты, где могут быть данные
    for attr_name in ['data', 'image', 'bytes', 'content', 'blob']:
        if hasattr(inline_data, attr_name):
            attr_value = getattr(inline_data, attr_name)
            logger.info(f"Найден атрибут {attr_name}: {type(attr_value)}")
            
            if isinstance(attr_value, str):
                try:
                    # Пробуем декодировать как Base64
                    output_image_bytes = base64.b64decode(attr_value)
                    logger.info(f"✅ Успешно декодировано из Base64 из атрибута {attr_name}, размер: {len(output_image_bytes)} байт")
                    break
                except Exception as e:
                    logger.warning(f"Не удалось декодировать {attr_name} как Base64: {e}")
            
            elif isinstance(attr_value, bytes):
                output_image_bytes = attr_value
                logger.info(f"✅ Успешно получены байты из атрибута {attr_name}, размер: {len(output_image_bytes)} байт")
                break
            
            elif hasattr(attr_value, 'getvalue'):
                # Если это объект с методом getvalue (например, BytesIO)
                try:
                    output_image_bytes = attr_value.getvalue()
                    logger.info(f"✅ Успешно получены байты через getvalue() из атрибута {attr_name}, размер: {len(output_image_bytes)} байт")
                    break
                except Exception as e:
                    logger.warning(f"Ошибка при вызове getvalue() для {attr_name}: {e}")
            
            else:
                logger.info(f"Атрибут {attr_name} имеет неподдерживаемый тип: {type(attr_value)}")

    if output_image_bytes is None:
        # Если ни один из методов не сработал, логируем все атрибуты для отладки
        logger.error("Не удалось извлечь данные изображения. Все атрибуты inline_data:")
        for attr in dir(inline_data):
            if not attr.startswith('_'):
                try:
                    value = getattr(inline_data, attr)
                    if not callable(value):
                        logger.error(f"  {attr}: {type(value)} = {str(value)[:200]}...")
                except Exception as attr_e:
                    logger.error(f"  {attr}: ОШИБКА получения - {attr_e}")
        
        raise Exception("Объект inline_data не содержит байтов изображения в ожидаемом формате.")

    return output_image_bytes

# Альтернативная функция для демонстрации (заглушка)
def generate_demo_image(prompt: str) -> bytes:
    """Генерирует демо-изображение когда Gemini недоступен"""
    from PIL import Image, ImageDraw, ImageFont
    import io
    
    # Создаем простое изображение с текстом
    img = Image.new('RGB', (512, 512), color=(73, 109, 137))
    d = ImageDraw.Draw(img)
    
    # Простой текст вместо изображения
    text = "Демо-режим\n\nПромпт:\n" + prompt[:100] + "..."
    
    # Разбиваем текст на строки
    lines = []
    words = text.split()
    line = ""
    for word in words:
        test_line = line + word + " "
        if len(test_line) > 30:
            lines.append(line)
            line = word + " "
        else:
            line = test_line
    if line:
        lines.append(line)
    
    # Рисуем текст
    y = 50
    for line in lines:
        d.text((50, y), line, fill=(255, 255, 255))
        y += 30
    
    # Сохраняем в bytes
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='JPEG')
    return img_byte_arr.getvalue()

class FashionBot:
    def __init__(self, token: str):
        self.bot = Bot(token=token)
        self.dp = Dispatcher()
        self.db = Database()
        self.setup_handlers()

    def setup_handlers(self):
        # Команда старт
        self.dp.message.register(self.start_handler, Command("start"))
        
        # Команды администратора
        self.dp.message.register(self.add_balance_handler, Command("add_balance"))
        self.dp.message.register(self.stats_handler, Command("stats"))
        
        # Основные обработчики
        self.dp.callback_query.register(self.accept_terms_handler, F.data == "accept_terms")
        self.dp.callback_query.register(self.support_handler, F.data == "support")
        self.dp.callback_query.register(self.create_photo_handler, F.data == "create_photo")
        self.dp.callback_query.register(self.topup_balance_handler, F.data == "topup_balance")
        self.dp.callback_query.register(self.back_to_main_handler, F.data == "back_to_main")
        
        # Обработчики создания фото
        self.dp.callback_query.register(self.gender_select_handler, F.data.startswith("gender_"))
        self.dp.message.register(self.photo_handler, StateFilter(ProductCreationStates.waiting_for_photo))
        self.dp.message.register(self.height_handler, StateFilter(ProductCreationStates.waiting_for_height))
        self.dp.callback_query.register(self.location_handler, F.data.startswith("location_"))
        self.dp.callback_query.register(self.age_handler, F.data.startswith("age_"))
        self.dp.callback_query.register(self.size_handler, F.data.startswith("size_"))
        self.dp.callback_query.register(self.location_style_handler, F.data.startswith("style_"))
        self.dp.callback_query.register(self.pose_handler, F.data.startswith("pose_"))
        self.dp.callback_query.register(self.view_handler, F.data.startswith("view_"))
        self.dp.callback_query.register(self.confirmation_handler, F.data.startswith("confirm_"))

    async def start_handler(self, message: Message):
        """Обработчик команды /start"""
        welcome_text = (
            "👋 Добро пожаловать в Fashion AI Generator!\n\n"
            "Превращаем фотографии вашей одежды в профессиональные снимки на моделях.\n\n"
            "📋 Перед использованием ознакомьтесь с:\n"
            "1. Условиями использования\n"  
            "2. Согласием на обработку данных"
        )
        
        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Принять и продолжить", callback_data="accept_terms")
        builder.button(text="💬 Написать в поддержку", callback_data="support")
        builder.adjust(1)
        
        await message.answer(welcome_text, reply_markup=builder.as_markup())

    async def accept_terms_handler(self, callback: CallbackQuery):
        """Обработчик принятия условий"""
        await callback.message.delete()
        await self.show_main_menu(callback.message)

    async def show_main_menu(self, message: Message):
        """Показать главное меню"""
        main_menu_text = "🎯 Главное меню:"
        builder = InlineKeyboardBuilder()
        builder.button(text="📸 Создать фото", callback_data="create_photo")
        builder.button(text="💳 Пополнить баланс", callback_data="topup_balance")
        builder.button(text="🆘 Написать в поддержку", callback_data="support")
        builder.adjust(1)
        
        await message.answer(main_menu_text, reply_markup=builder.as_markup())

    async def back_to_main_handler(self, callback: CallbackQuery):
        """Обработчик возврата в главное меню"""
        await callback.message.delete()
        await self.show_main_menu(callback.message)

    async def support_handler(self, callback: CallbackQuery):
        """Обработчик связи с поддержкой"""
        support_text = f"📞 Для связи с поддержкой напишите: {SUPPORT_USERNAME}"
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 Назад", callback_data="back_to_main")
        await callback.message.answer(support_text, reply_markup=builder.as_markup())

    async def topup_balance_handler(self, callback: CallbackQuery):
        """Обработчик пополнения баланса"""
        user_id = callback.from_user.id
        current_balance = self.db.get_user_balance(user_id)
        
        balance_text = (
            f"💳 Пополнение баланса\n\n"
            f"Текущий баланс: {current_balance} генераций\n\n"
            "Для пополнения баланса напишите нашему менеджеру:\n"
            f"{SUPPORT_USERNAME}\n\n"
            f"Укажите ваш ID для зачисления: `{user_id}`"
        )
        
        builder = InlineKeyboardBuilder()
        builder.button(text="📞 Написать менеджеру", url=f"tg://resolve?domain={SUPPORT_USERNAME[1:]}")
        builder.button(text="🔙 Назад", callback_data="back_to_main")
        builder.adjust(1)
        
        await callback.message.answer(balance_text, reply_markup=builder.as_markup(), parse_mode="Markdown")

    async def create_photo_handler(self, callback: CallbackQuery):
        """Обработчик начала создания фото"""
        user_id = callback.from_user.id
        current_balance = self.db.get_user_balance(user_id)
        
        if current_balance <= 0:
            builder = InlineKeyboardBuilder()
            builder.button(text="💳 Пополнить баланс", callback_data="topup_balance")
            builder.button(text="🔙 Назад", callback_data="back_to_main")
            builder.adjust(1)
            
            await callback.message.answer(
                "❌ Недостаточно генераций. Пожалуйста, пополните баланс.",
                reply_markup=builder.as_markup()
            )
            return

        gender_text = "Выберите пожалуйста какой продукт вы хотите создать?"
        builder = InlineKeyboardBuilder()
        builder.button(text="👚 Женская одежда", callback_data="gender_women")
        builder.button(text="👔 Мужская одежда", callback_data="gender_men") 
        builder.button(text="👶 Детская одежда", callback_data="gender_kids")
        builder.button(text="🖼️ Витринное фото", callback_data="gender_display")
        builder.button(text="🔙 Назад", callback_data="back_to_main")
        builder.adjust(1)
        
        await callback.message.answer(gender_text, reply_markup=builder.as_markup())

    async def gender_select_handler(self, callback: CallbackQuery, state: FSMContext):
        """Обработчик выбора пола/категории"""
        gender_map = {
            "gender_women": GenderType.WOMEN,
            "gender_men": GenderType.MEN,
            "gender_kids": GenderType.KIDS,
            "gender_display": GenderType.DISPLAY
        }
        
        gender = gender_map[callback.data]
        await state.update_data(gender=gender)
        
        # Для витринного фото не показываем примеры
        if gender != GenderType.DISPLAY:
            # Отправляем примеры фото
            try:
                media_group = MediaGroupBuilder()
                photo1 = FSInputFile("photo/example1.jpg")
                photo2 = FSInputFile("photo/example2.jpg")
                media_group.add_photo(media=photo1)
                media_group.add_photo(media=photo2)
                await callback.message.answer_media_group(media=media_group.build())
            except Exception as e:
                logger.warning(f"Не удалось загрузить примеры фото: {e}")
                # Если фото нет, просто продолжаем

        if gender == GenderType.DISPLAY:
            instruction_text = (
                "📸 Пожалуйста пришлите фотографию вашего товара для создания витринного фото.\n\n"
                "⚠️ Обратите внимание: фотография вашего товара должна быть четко видна "
                "без лишних бликов и размытостей.\n\n"
                f"Если остались вопросы - пишите в поддержку {SUPPORT_USERNAME}"
            )
        else:
            instruction_text = (
                "📸 Пожалуйста пришлите фотографию вашего товара.\n\n"
                "⚠️ Обратите внимание: фотография вашего товара должна быть четко видна "
                "без лишних бликов и размытостей.\n\n"
                f"Если остались вопросы - пишите в поддержку {SUPPORT_USERNAME}"
            )
        
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 Назад", callback_data="create_photo")
        
        await callback.message.answer(instruction_text, reply_markup=builder.as_markup())
        await state.set_state(ProductCreationStates.waiting_for_photo)

    async def photo_handler(self, message: Message, state: FSMContext):
        """Обработчик загрузки фото"""
        if not message.photo:
            await message.answer("📸 Пожалуйста, отправьте фотографию товара.")
            return
            
        photo_file_id = message.photo[-1].file_id
        await state.update_data(photo_file_id=photo_file_id)
        
        # Скачиваем фото для временного хранения
        try:
            file = await self.bot.get_file(photo_file_id)
            file_path = file.file_path
            
            # Создаем временный файл
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
            temp_path = temp_file.name
            temp_file.close()
            
            await self.bot.download_file(file_path, temp_path)
            await state.update_data(temp_photo_path=temp_path)
            
        except Exception as e:
            logger.error(f"Ошибка при сохранении фото: {e}")
            await message.answer("❌ Ошибка при обработке фото. Попробуйте еще раз.")
            return
        
        data = await state.get_data()
        gender = data['gender']
        
        if gender == GenderType.DISPLAY:
            # Для витринного фото сразу переходим к подтверждению
            prompt = await self.generate_prompt(data)
            await state.update_data(prompt=prompt)
            
            user_id = message.from_user.id
            self.db.add_generation(user_id, prompt)
            
            summary = await self.generate_summary(data)
            summary_text = f"📋 Проверьте выбранные параметры:\n\n{summary}"
            
            builder = InlineKeyboardBuilder()
            builder.button(text="🚀 Начать генерацию", callback_data="confirm_generate")
            builder.button(text="✏️ Внести изменения", callback_data="confirm_edit")
            builder.adjust(1)
            
            await message.answer(summary_text, reply_markup=builder.as_markup())
            await state.set_state(ProductCreationStates.waiting_for_confirmation)
        else:
            await state.set_state(ProductCreationStates.waiting_for_height)
            await message.answer("📏 Напишите рост модели (в см):")

    # ... (остальные методы класса FashionBot остаются без изменений)
    # Для экономии места оставляю только измененные методы

    async def confirmation_handler(self, callback: CallbackQuery, state: FSMContext):
        """Обработчик подтверждения генерации"""
        user_id = callback.from_user.id
        current_balance = self.db.get_user_balance(user_id)
        
        if callback.data == "confirm_generate":
            if current_balance <= 0:
                await callback.message.answer("❌ Недостаточно генераций. Пополните баланс.")
                await state.clear()
                return
                
            new_balance = current_balance - 1
            self.db.update_user_balance(user_id, new_balance)
            
            data = await state.get_data()
            prompt = data.get('prompt', '')
            temp_photo_path = data.get('temp_photo_path')
            
            # Отправляем сообщение о начале генерации
            generating_msg = await callback.message.answer(
                f"🎨 Генерация изображения началась... Это может занять 10-20 секунд.\n\n"
                f"Использовано 1 генерация\n"
                f"Осталось генераций: {new_balance}"
            )
            
            try:
                # Генерируем изображение через Gemini API
                processed_image_bytes = call_nano_banana_api(temp_photo_path, prompt)
                
                # Отправляем сгенерированное изображение
                generated_image = BufferedInputFile(processed_image_bytes, filename="generated_fashion.jpg")
                
                await callback.message.answer_photo(
                    generated_image,
                    caption="✨ Генерация завершена успешно!"
                )
                
                # Удаляем временное сообщение
                await generating_msg.delete()
                
            except Exception as e:
                logger.error(f"Ошибка при генерации изображения: {e}")
                await generating_msg.delete()
                
                error_msg = str(e)
                if "location is not supported" in error_msg.lower():
                    await callback.message.answer(
                        "❌ Сервис генерации изображений недоступен в вашем регионе.\n\n"
                        "Возможные решения:\n"
                        "• Используйте VPN\n"
                        "• Настройте Google Cloud проект в поддерживаемом регионе\n"
                        "• Обратитесь к администратору\n\n"
                        "Ваш баланс был возвращен."
                    )
                    # Возвращаем баланс
                    self.db.update_user_balance(user_id, current_balance)
                else:
                    await callback.message.answer(
                        f"❌ Произошла ошибка при генерации изображения:\n\n"
                        f"`{error_msg}`\n\n"
                        f"Попробуйте изменить параметры или обратитесь в поддержку.\n"
                        f"Ваш баланс был возвращен.",
                        parse_mode="Markdown"
                    )
                    # Возвращаем баланс при любой ошибке генерации
                    self.db.update_user_balance(user_id, current_balance)
                
            finally:
                # Удаляем временный файл
                if temp_photo_path and os.path.exists(temp_photo_path):
                    os.unlink(temp_photo_path)
            
        else:
            # Внести изменения - начинаем заново
            await self.create_photo_handler(callback)
            
        await state.clear()

    # ... (остальные методы класса FashionBot)

async def main():
    bot = FashionBot(BOT_TOKEN)
    await bot.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("⏹️ Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")