import asyncio
import logging
import sqlite3
import os
import sys
import tempfile
import base64
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
    Отправляет изображение и промпт в Gemini 2.5 Flash Image и извлекает байты.
    """
    
    # 1. Инициализация клиента (предполагаем, что ключ загружен)
    client = genai.Client()

    # 2. Чтение изображения
    try:
        input_image = Image.open(input_image_path)
    except Exception as e:
        raise ValueError(f"Ошибка чтения исходного изображения: {e}")

    # 3. Вызов модели
    config_params = extra_params if extra_params is not None else {}
    
    response = client.models.generate_content(
        model="gemini-2.5-flash-image",
        contents=[prompt, input_image],
        **config_params
    )
    
    # 4. Корректная обработка и извлечение изображения
    
    if not response.candidates:
        raise Exception("API не вернул кандидатов (candidates).")

    first_part = response.candidates[0].content.parts[0]
    
    # Проверка на наличие inline_data (признак бинарного содержимого)
    if not hasattr(first_part, 'inline_data'):
        # Если нет inline_data, проверяем, не вернул ли Gemini текст с объяснением
        if hasattr(first_part, 'text'):
            raise Exception(f"Gemini вернул текст вместо изображения: {first_part.text}")
        else:
            raise Exception("Получен ответ с неизвестной структурой (ни изображение, ни текст).")

    # Получаем объект InlineData
    inline_data = first_part.inline_data
    
    # Бинарные данные всегда должны находиться в атрибуте .data
    if not hasattr(inline_data, 'data'):
        raise Exception(f"Объект inline_data не содержит атрибута 'data'. MIME-тип: {getattr(inline_data, 'mime_type', 'N/A')}")

    data_content = inline_data.data
    
    if isinstance(data_content, str):
        # 4.1. Если это строка, считаем, что это Base64 и декодируем
        try:
            output_image_bytes = base64.b64decode(data_content)
        except Exception as e:
            raise Exception(f"Ошибка декодирования Base64. Возможно, данные не Base64. Ошибка: {e}")
            
    elif isinstance(data_content, bytes):
        # 4.2. Если это уже байты, используем их напрямую
        output_image_bytes = data_content
        
    else:
        # 4.3. Неожиданный тип данных
        raise Exception(f"Объект inline_data.data имеет неожиданный тип: {type(data_content)}. Ожидались str (Base64) или bytes.")
        
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

    async def height_handler(self, message: Message, state: FSMContext):
        """Обработчик ввода роста"""
        height = message.text
        if not height.isdigit():
            await message.answer("❌ Пожалуйста, введите числовое значение роста в см:")
            return
            
        await state.update_data(height=height)
        await state.set_state(ProductCreationStates.waiting_for_location)
        
        builder = InlineKeyboardBuilder()
        builder.button(text="🏙️ Улица", callback_data="location_street")
        builder.button(text="📸 Фотостудия", callback_data="location_studio") 
        builder.button(text="📐 Фотозона на полу", callback_data="location_floor")
        builder.adjust(1)
        
        await message.answer("📍 Пожалуйста выберите локацию:", reply_markup=builder.as_markup())

    async def location_handler(self, callback: CallbackQuery, state: FSMContext):
        """Обработчик выбора локации"""
        location_map = {
            "location_street": LocationType.STREET,
            "location_studio": LocationType.STUDIO,
            "location_floor": LocationType.FLOOR_ZONE
        }
        
        location = location_map[callback.data]
        await state.update_data(location=location)
        
        data = await state.get_data()
        gender = data['gender']
        
        builder = InlineKeyboardBuilder()
        
        if gender == GenderType.KIDS:
            age_groups = AgeGroup.KIDS.value
        else:
            age_groups = AgeGroup.WOMEN_MEN.value
            
        for age in age_groups:
            builder.button(text=age, callback_data=f"age_{age}")
            
        builder.adjust(2)
        
        await callback.message.answer("🎂 Пожалуйста выберите возраст модели:", reply_markup=builder.as_markup())
        await state.set_state(ProductCreationStates.waiting_for_age)

    async def age_handler(self, callback: CallbackQuery, state: FSMContext):
        """Обработчик выбора возраста"""
        age = callback.data.replace("age_", "")
        await state.update_data(age=age)
        
        data = await state.get_data()
        gender = data['gender']
        
        if gender == GenderType.KIDS:
            await state.set_state(ProductCreationStates.waiting_for_location_style)
            
            builder = InlineKeyboardBuilder()
            builder.button(text="🎄 Новогодняя", callback_data="style_new_year")
            builder.button(text="☀️ Лето", callback_data="style_summer")
            builder.button(text="🌳 Природа", callback_data="style_nature")
            builder.button(text="🏞️ Парк (зима)", callback_data="style_park_winter")
            builder.button(text="🌲 Парк (лето)", callback_data="style_park_summer")
            builder.button(text="🏢 Обычный", callback_data="style_regular")
            builder.button(text="🚗 Рядом с машиной", callback_data="style_car")
            builder.adjust(2)
            
            await callback.message.answer("🎨 Пожалуйста, выберите стиль локации:", reply_markup=builder.as_markup())
        else:
            await state.set_state(ProductCreationStates.waiting_for_size)
            
            builder = InlineKeyboardBuilder()
            builder.button(text="42-46", callback_data="size_42_46")
            builder.button(text="50-54", callback_data="size_50_54")
            builder.button(text="58-64", callback_data="size_58_64")
            builder.button(text="64-68", callback_data="size_64_68")
            builder.adjust(2)
            
            await callback.message.answer("📏 Пожалуйста выберите размер одежды:", reply_markup=builder.as_markup())

    async def size_handler(self, callback: CallbackQuery, state: FSMContext):
        """Обработчик выбора размера"""
        size_map = {
            "size_42_46": SizeType.SIZE_42_46,
            "size_50_54": SizeType.SIZE_50_54,
            "size_58_64": SizeType.SIZE_58_64,
            "size_64_68": SizeType.SIZE_64_68
        }
        
        size = size_map[callback.data]
        await state.update_data(size=size)
        await state.set_state(ProductCreationStates.waiting_for_location_style)
        
        builder = InlineKeyboardBuilder()
        builder.button(text="🎄 Новогодняя", callback_data="style_new_year")
        builder.button(text="☀️ Лето", callback_data="style_summer")
        builder.button(text="🌳 Природа", callback_data="style_nature")
        builder.button(text="🏞️ Парк (зима)", callback_data="style_park_winter")
        builder.button(text="🌲 Парк (лето)", callback_data="style_park_summer")
        builder.button(text="🏢 Обычный", callback_data="style_regular")
        builder.button(text="🚗 Рядом с машиной", callback_data="style_car")
        builder.adjust(2)
        
        await callback.message.answer("🎨 Пожалуйста, выберите стиль локации:", reply_markup=builder.as_markup())

    async def location_style_handler(self, callback: CallbackQuery, state: FSMContext):
        """Обработчик выбора стиля локации"""
        style_map = {
            "style_new_year": LocationStyle.NEW_YEAR,
            "style_summer": LocationStyle.SUMMER,
            "style_nature": LocationStyle.NATURE,
            "style_park_winter": LocationStyle.PARK_WINTER,
            "style_park_summer": LocationStyle.PARK_SUMMER,
            "style_regular": LocationStyle.REGULAR,
            "style_car": LocationStyle.CAR
        }
        
        location_style = style_map[callback.data]
        await state.update_data(location_style=location_style)
        await state.set_state(ProductCreationStates.waiting_for_pose)
        
        builder = InlineKeyboardBuilder()
        builder.button(text="🪑 Сидя", callback_data="pose_sitting")
        builder.button(text="🧍 Стоя", callback_data="pose_standing")
        builder.adjust(2)
        
        await callback.message.answer("🧘 Пожалуйста выберите положение тела:", reply_markup=builder.as_markup())

    async def pose_handler(self, callback: CallbackQuery, state: FSMContext):
        """Обработчик выбора позы"""
        pose_map = {
            "pose_sitting": PoseType.SITTING,
            "pose_standing": PoseType.STANDING
        }
        
        pose = pose_map[callback.data]
        await state.update_data(pose=pose)
        await state.set_state(ProductCreationStates.waiting_for_view)
        
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 Сзади", callback_data="view_back")
        builder.button(text="👤 Передняя часть", callback_data="view_front")
        builder.adjust(2)
        
        await callback.message.answer("👀 Пожалуйста выберите вид:", reply_markup=builder.as_markup())

    async def view_handler(self, callback: CallbackQuery, state: FSMContext):
        """Обработчик выбора вида"""
        view_map = {
            "view_back": ViewType.BACK,
            "view_front": ViewType.FRONT
        }
        
        view = view_map[callback.data]
        await state.update_data(view=view)
        
        # Формируем сводку
        data = await state.get_data()
        summary = await self.generate_summary(data)
        prompt = await self.generate_prompt(data)
        
        await state.update_data(prompt=prompt)
        
        user_id = callback.from_user.id
        self.db.add_generation(user_id, prompt)
        
        summary_text = f"📋 Проверьте выбранные параметры:\n\n{summary}"
        
        builder = InlineKeyboardBuilder()
        builder.button(text="🚀 Начать генерацию", callback_data="confirm_generate")
        builder.button(text="✏️ Внести изменения", callback_data="confirm_edit")
        builder.adjust(1)
        
        await callback.message.answer(summary_text, reply_markup=builder.as_markup())
        await state.set_state(ProductCreationStates.waiting_for_confirmation)

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
                f"🎨 Генерация изображения началась... Это может занять несколько секунд.\n\n"
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
                    # Для других ошибок пробуем демо-режим
                    try:
                        await callback.message.answer("🔄 Пробуем демо-режим...")
                        demo_image_bytes = generate_demo_image(prompt)
                        demo_image = BufferedInputFile(demo_image_bytes, filename="demo_fashion.jpg")
                        
                        await callback.message.answer_photo(
                            demo_image,
                            caption=(
                                "🔄 Демо-режим (Gemini API недоступен)\n\n"
                                f"Ошибка: {error_msg}\n\n"
                                "Обратитесь к администратору для настройки сервиса."
                            )
                        )
                    except Exception as demo_error:
                        await callback.message.answer(
                            f"❌ Критическая ошибка:\n{error_msg}\n\n"
                            f"Демо-режим также не сработал: {demo_error}\n\n"
                            "Пожалуйста, обратитесь в поддержку."
                        )
                
            finally:
                # Удаляем временный файл
                if temp_photo_path and os.path.exists(temp_photo_path):
                    os.unlink(temp_photo_path)
            
        else:
            # Внести изменения - начинаем заново
            await self.create_photo_handler(callback)
            
        await state.clear()

    async def add_balance_handler(self, message: Message):
        """Добавление баланса пользователю (только для админа)"""
        if message.from_user.id != ADMIN_ID:
            return
            
        try:
            _, user_id_str, amount_str = message.text.split()
            user_id = int(user_id_str)
            amount = int(amount_str)
            
            current_balance = self.db.get_user_balance(user_id)
            new_balance = current_balance + amount
            self.db.update_user_balance(user_id, new_balance)
            
            await message.answer(f"✅ Пользователю {user_id} добавлено {amount} генераций. Всего: {new_balance}")
            
            try:
                await self.bot.send_message(
                    user_id, 
                    f"🎉 Вам добавлено {amount} генераций!\n"
                    f"Теперь у вас {new_balance} генераций."
                )
            except Exception as e:
                await message.answer(f"⚠️ Не удалось отправить уведомление пользователю: {e}")
            
        except Exception as e:
            await message.answer("❌ Использование: /add_balance <user_id> <amount>")

    async def stats_handler(self, message: Message):
        """Статистика (только для админа)"""
        if message.from_user.id != ADMIN_ID:
            return
            
        total_users, total_generations, total_balance = self.db.get_all_users_stats()
        
        stats_text = (
            f"📊 Статистика бота:\n\n"
            f"👥 Всего пользователей: {total_users}\n"
            f"🔄 Всего генераций: {total_generations}\n"
            f"💰 Общий баланс: {total_balance} генераций"
        )
        
        await message.answer(stats_text)

    async def generate_summary(self, data: Dict[str, Any]) -> str:
        """Генерация сводки выбранных параметров"""
        gender_text = {
            GenderType.WOMEN: "👚 Женская одежда",
            GenderType.MEN: "👔 Мужская одежда", 
            GenderType.KIDS: "👶 Детская одежда",
            GenderType.DISPLAY: "🖼️ Витринное фото"
        }
        
        if data['gender'] == GenderType.DISPLAY:
            return "🖼️ Витринное фото: товар на белом фоне"
        
        summary = (
            f"📦 Категория: {gender_text[data['gender']]}\n"
            f"📏 Рост: {data['height']} см\n"
            f"📍 Локация: {data['location'].value}\n"
            f"🎂 Возраст: {data['age']} лет\n"
        )
        
        if data['gender'] != GenderType.KIDS and 'size' in data:
            summary += f"📏 Размер: {data['size'].value}\n"
            
        summary += (
            f"🎨 Стиль локации: {data['location_style'].value}\n"
            f"🧘 Положение: {data['pose'].value}\n"
            f"👀 Вид: {data['view'].value}"
        )
        
        return summary

    async def generate_prompt(self, data: Dict[str, Any]) -> str:
        """Генерация промта на английском"""
        gender = data['gender']
        
        if gender == GenderType.DISPLAY:
            return (
                "Create a professional product display photo with pure white background. "
                "The product should be perfectly centered in the frame, well-lit with soft studio lighting. "
                "The image should be clean, crisp and high-resolution, suitable for e-commerce. "
                "No shadows, no props, no text, just the product on white background. "
                "The product must be an exact copy of the reference image provided."
            )
        
        gender_map = {
            GenderType.WOMEN: "woman",
            GenderType.MEN: "man", 
            GenderType.KIDS: "girl" if data['age'] in ["0.3-1", "2-4", "7-10"] else "boy"
        }
        
        location_map = {
            LocationType.STREET: "urban street",
            LocationType.STUDIO: "professional photo studio",
            LocationType.FLOOR_ZONE: "minimalist floor photo zone"
        }
        
        view_map = {
            ViewType.BACK: "back to camera, demonstrating the back of the product",
            ViewType.FRONT: "facing camera"
        }
        
        pose_map = {
            PoseType.SITTING: "sitting",
            PoseType.STANDING: "standing"
        }
        
        season_map = {
            LocationStyle.NEW_YEAR: "winter",
            LocationStyle.SUMMER: "summer", 
            LocationStyle.PARK_WINTER: "winter",
            LocationStyle.PARK_SUMMER: "summer",
            LocationStyle.REGULAR: "current season",
            LocationStyle.CAR: "current season",
            LocationStyle.NATURE: "spring"
        }
        
        prompt_parts = [
            f"Create a hyper-realistic, high-quality photo of a {gender_map[data['gender']]}",
            f"wearing my product, exactly replicating the reference image I provide.",
            f"The scene takes place at {location_map[data['location']]}.",
            f"The model is {data['age']} years old, height {data['height']} cm,",
        ]
        
        if data['gender'] != GenderType.KIDS and 'size' in data:
            prompt_parts.append(f"with body type {data['size'].value}.")
        else:
            prompt_parts.append("with appropriate body type for the age.")
        
        prompt_parts.extend([
            f"The photo should be taken from full-length view.",
            f"The season is {season_map[data['location_style']]},",
            f"and the model wears automatically selected footwear matching the style and weather.",
            f"The outfit must be an exact 100% copy of my product from the reference image",
            f"— do not create, modify, or add any details that are not visible.",
            f"If the product has a central zipper, it must be fully closed.",
            f"If there is no central zipper, do not include one.",
            f"The {gender_map[data['gender']]} should pose like a professional model",
            f"— expressive, confident, and natural, as in a real fashion photoshoot.",
            f"The posture should highlight the clothing's shape and fit,",
            f"but hands must never be in pockets.",
            f"The model should be {pose_map[data['pose']]} and {view_map[data['view']]}.",
            f"Lighting must look realistic and flattering,",
            f"emphasizing the texture, material, and true color of the product.",
            f"The background should complement the scene but never distract from the item.",
            f"The final image should appear as a professional fashion editorial photo,",
            f"photorealistic, clean, and perfectly balanced in composition and proportions."
        ])
        
        return " ".join(prompt_parts)

    async def run(self):
        """Запуск бота"""
        logger.info("🤖 Бот запускается...")
        try:
            await self.dp.start_polling(self.bot)
        except Exception as e:
            logger.error(f"❌ Ошибка при запуске бота: {e}")
        finally:
            await self.bot.session.close()

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