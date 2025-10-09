
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
from google.genai import types # 💡 ИСПРАВЛЕНИЕ 1: Импортируем типы для проверки block_reason
from PIL import Image, ImageDraw # 💡 ИСПРАВЛЕНИЕ 2: Импортируем ImageDraw для демо-режима
import io

# Загрузка переменных окружения
load_dotenv()

# Конфигурация из .env
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@bnbslow")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# 💡 ИСПРАВЛЕНИЕ 3: Определяем переменную демо-режима
GEMINI_DEMO_MODE = False # Установите True для включения заглушки вместо реального API

if not BOT_TOKEN:
    print("❌ BOT_TOKEN не установлен в .env файле")
    sys.exit(1)

if not GEMINI_API_KEY:
    print("❌ GEMINI_API_KEY не установлен в .env файле")
    # sys.exit(1) # Закомментировано для возможности тестирования в демо-режиме

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

# --- Функция вызова API Gemini (ИСПРАВЛЕНА для надежного извлечения) ---
def call_nano_banana_api(
    input_image_path: str,
    prompt: str,
    extra_params: Dict[str, Any] = None
) -> bytes:
    """
    Отправляет изображение и промпт в Gemini 2.5 Flash Image и извлекает байты.
    """
    if GEMINI_DEMO_MODE:
        # 💡 Использование ImageDraw для создания демо-изображения
        img = Image.new('RGB', (1024, 1024), color=(73, 109, 137))
        d = ImageDraw.Draw(img)
        d.text((50, 50), "ДЕМО-РЕЖИМ. Промпт: " + prompt[:100] + "...", fill=(255, 255, 255))
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='PNG')
        return img_byte_arr.getvalue()

    client = genai.Client(api_key=GEMINI_API_KEY)
    input_image = Image.open(input_image_path)

    api_config = extra_params if extra_params is not None else {}
    if 'config' not in api_config:
        api_config['config'] = {"response_modalities": ['TEXT', 'IMAGE']}

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash-image",
            contents=[prompt, input_image],
            config=api_config.get('config')
        )
    except exceptions.GoogleAPICallError as e:
        logger.error(f"Ошибка вызова API Gemini: {e}")
        raise Exception(f"Ошибка вызова API Gemini: {e}")
    except Exception as e:
        logger.error(f"Неизвестная ошибка API Gemini: {e}")
        raise Exception(f"Неизвестная ошибка API Gemini: {e}")

    # --- ИСПРАВЛЕННЫЙ БЛОК ИЗВЛЕЧЕНИЯ ИЗОБРАЖЕНИЯ ---

    if not response.candidates:
        # 💡 Использование types.BlockReason для корректного сравнения
        if hasattr(response, 'prompt_feedback') and response.prompt_feedback.block_reason != types.BlockReason.BLOCK_REASON_UNSPECIFIED:
             raise Exception(f"Запрос заблокирован по причине: {response.prompt_feedback.block_reason.name}")
        raise Exception("API не вернул кандидатов (candidates) и не указал причину блокировки.")

    candidate = response.candidates[0]

    # 💡 Надежный поиск части, содержащей inline_data.data (байты изображения)
    image_part = None
    for part in candidate.content.parts:
        if hasattr(part, 'inline_data') and hasattr(part.inline_data, 'data'):
            image_part = part
            break

    if image_part is None:
        # Если изображение не найдено, собираем весь текст для диагностики
        text_explanation = "\n".join([p.text for p in candidate.content.parts if hasattr(p, 'text') and p.text])
        finish_reason = candidate.finish_reason.name if hasattr(candidate, 'finish_reason') else "UNKNOWN"

        error_msg = f"API не вернул inline_data. Причина завершения: {finish_reason}. "
        if text_explanation.strip():
             error_msg += f"Модель вернула только текст: {text_explanation.strip()[:150]}..."

        logger.error(f"Ошибка извлечения inline_data: {error_msg}")
        raise Exception(error_msg)

    # --- Извлечение данных из найденной части ---
    inline_data = image_part.inline_data
    data_content = inline_data.data
    mime_type = getattr(inline_data, 'mime_type', 'N/A')

    logger.info(f"DEBUG: MIME Type from API: {mime_type}")
    logger.info(f"DEBUG: Data content type: {type(data_content)}")

    if isinstance(data_content, str):
        # Если данные Base64
        try:
            output_image_bytes = base64.b64decode(data_content)
        except Exception as e:
            raise Exception(f"Ошибка декодирования Base64. Ошибка: {e}")

    elif isinstance(data_content, bytes):
        # Если данные - сырые байты (как в вашем логе)
        output_image_bytes = data_content

    else:
        raise Exception(f"Объект inline_data.data имеет неожиданный тип: {type(data_content)}. Ожидались str (Base64) или bytes.")

    if len(output_image_bytes) == 0:
        logger.error("--- DEBUG: API вернул пустые байты изображения (длина 0). ---")
        raise Exception("API вернул пустые данные (длина 0).")

    logger.info(f"DEBUG: Successfully extracted bytes. Size: {len(output_image_bytes)} bytes.")

    return output_image_bytes

# Альтернативная функция для демонстрации (заглушка)
def generate_demo_image(prompt: str) -> bytes:
    """Генерирует демо-изображение когда Gemini недоступен"""
    from PIL import Image, ImageDraw
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
                # ❗ Здесь предполагается, что файлы 'photo/example1.jpg' и 'photo/example2.jpg' существуют
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
        temp_path = None
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
            # Удаляем временный файл, если он был создан
            if temp_path and os.path.exists(temp_path):
                 os.unlink(temp_path)
            return

        data = await state.get_data()
        gender = data['gender']

        if gender == GenderType.DISPLAY:
            # Для витринного фото сразу переходим к подтверждению
            # ❗ Здесь нужны функции generate_prompt и generate_summary, которые отсутствуют в предоставленном коде
            # Добавим заглушки, чтобы избежать ошибок
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
        # ❗ Здесь нужны функции generate_prompt и generate_summary, которые отсутствуют в предоставленном коде
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

    # --- Административные функции ---

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

            await message.answer(f"✅ Пользователю {user_id} добавлен баланс: +{amount}. Новый баланс: {new_balance}")

        except ValueError:
             await message.answer("❌ Неверный формат. Используйте: `/add_balance [ID пользователя] [кол-во]`")
        except Exception as e:
            await message.answer(f"❌ Ошибка при добавлении баланса: {e}")

    async def stats_handler(self, message: Message):
        """Вывод статистики (только для админа)"""
        if message.from_user.id != ADMIN_ID:
            return

        total_users, total_generations, total_balance = self.db.get_all_users_stats()

        stats_text = (
            "📊 **Статистика Бота**\n"
            "-----------------------------\n"
            f"👤 Всего пользователей: `{total_users}`\n"
            f"📸 Всего генераций: `{total_generations}`\n"
            f"💰 Общий баланс (неиспользовано): `{total_balance}`"
        )
        await message.answer(stats_text, parse_mode="Markdown")

    # --- Функции-заглушки для формирования промпта и сводки (нужны для завершения логики) ---
    async def generate_summary(self, data: Dict[str, Any]) -> str:
        """Генерирует сводку параметров из FSMContext."""
        summary = ""
        for key, value in data.items():
             if key not in ['photo_file_id', 'temp_photo_path', 'prompt'] and value:
                 # Если значение - Enum, берем его .value или .name
                 display_value = value.value if hasattr(value, 'value') and isinstance(value.value, str) else value.name if hasattr(value, 'name') else str(value)
                 summary += f"• **{key.capitalize()}**: {display_value}\n"
        return summary or "Нет выбранных параметров (только фото)."

    async def generate_prompt(self, data: Dict[str, Any]) -> str:
        """Формирует финальный промпт для Gemini."""
        gender = data.get('gender', GenderType.DISPLAY).value
        location = data.get('location', 'Studio').value if hasattr(data.get('location'), 'value') else 'Studio'
        height = data.get('height', '170')
        age = data.get('age', '25')
        size = data.get('size', '44')
        style = data.get('location_style', LocationStyle.REGULAR).value if hasattr(data.get('location_style'), 'value') else 'regular style'
        pose = data.get('pose', PoseType.STANDING).value if hasattr(data.get('pose'), 'value') else 'standing'
        view = data.get('view', ViewType.FRONT).value if hasattr(data.get('view'), 'value') else 'front view'

        if data.get('gender') == GenderType.DISPLAY:
             return "Create a product display photo on a clean, light background with soft shadows. Focus on the clothing item, making it look professional and appealing for e-commerce."

        # Формируем сложный промпт для inpainting/remixing
        prompt = (
            f"Replace the clothing item on the model in the provided image with the new item. "
            f"The final image should show a {gender} model, {age} years old, wearing the new garment. "
            f"Model characteristics: height {height} cm, clothes size {size}. "
            f"Setting: **{location}** in a **{style}** atmosphere. "
            f"Model Pose: **{pose}** with a **{view}**. "
            "Ensure the garment fits naturally and realistically, maintaining high photo quality, realistic lighting, and professional photography style. Do not change the model's face or hair, only the clothing and background/setting according to the prompt."
        )
        return prompt

# --- Запуск бота ---
async def main():
    bot_instance = FashionBot(token=BOT_TOKEN)
    logger.info("🚀 Бот запущен!")
    # Здесь добавлен импорт для types для корректного запуска
    await bot_instance.dp.start_polling(bot_instance.bot)

if __name__ == "__main__":
    try:
        # Убедитесь, что для демо-режима есть заглушка ImageDraw
        if GEMINI_DEMO_MODE:
             try:
                 from PIL import ImageDraw
             except ImportError:
                 logger.error("❌ Для демо-режима требуется установить Pillow: pip install Pillow")
                 sys.exit(1)

        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Бот остановлен вручную.")
    except Exception as e:
        logger.error(f"Критическая ошибка запуска бота: {e}")