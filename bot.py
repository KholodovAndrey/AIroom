
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
from google.genai import types
from PIL import Image, ImageDraw
from io import BytesIO # Используем BytesIO как в документации
import io

# Загрузка переменных окружения
load_dotenv()

# Конфигурация из .env
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@bnbslow")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Демо-режим
GEMINI_DEMO_MODE = False

if not BOT_TOKEN:
    print("❌ BOT_TOKEN не установлен в .env файле")
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

# --- Константы (Enum) ---

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

# --- Класс Database ---

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

# --- Функция вызова API Gemini (Обновлена согласно документации) ---
def call_nano_banana_api(
    input_image_path: str,
    prompt: str,
    extra_params: Dict[str, Any] = None
) -> bytes:
    """
    Отправляет изображение и промпт в Gemini 2.5 Flash Image и возвращает байты изображения.
    """
    if GEMINI_DEMO_MODE:
        # Код демо-режима
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
        # Указываем, что ждем текст И изображение
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

    # --- ИЗВЛЕЧЕНИЕ ИЗОБРАЖЕНИЯ (МЕТОД ИЗ ДОКУМЕНТАЦИИ) ---

    if not response.candidates:
        if hasattr(response, 'prompt_feedback') and response.prompt_feedback.block_reason != types.BlockReason.BLOCK_REASON_UNSPECIFIED:
             raise Exception(f"Запрос заблокирован по причине: {response.prompt_feedback.block_reason.name}")
        raise Exception("API не вернул кандидатов.")

    candidate = response.candidates[0]

    output_image_bytes = None

    # Итерируемся по частям контента
    for part in candidate.content.parts:
        if part.inline_data is not None:
            # Найдена часть с inline_data (изображение)
            data_content = part.inline_data.data

            if isinstance(data_content, str):
                # Если Base64 строка, декодируем
                output_image_bytes = base64.b64decode(data_content)
            elif isinstance(data_content, bytes):
                # Если сырые байты, берем их
                output_image_bytes = data_content

            logger.info(f"DEBUG: Successfully extracted bytes. Size: {len(output_image_bytes)} bytes.")
            break # Прерываем, как только нашли изображение
        elif part.text is not None:
            logger.info(f"DEBUG: Text part received: {part.text[:50]}...")

    if output_image_bytes is None or len(output_image_bytes) == 0:
        text_explanation = "\n".join([p.text for p in candidate.content.parts if hasattr(p, 'text') and p.text])
        error_msg = f"API не вернул inline_data. Модель вернула только текст: {text_explanation.strip()[:150]}..."
        logger.error(error_msg)
        raise Exception(error_msg)

    return output_image_bytes


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

    # --- МЕТОДЫ ГЕНЕРАЦИИ ПРОМПТА И СВОДКИ ---

    async def generate_prompt(self, data: Dict[str, Any]) -> str:
        """
        Генерирует подробный промпт для Gemini API на основе выбранных параметров.
        """
        gender = data.get('gender', GenderType.DISPLAY)

        if gender == GenderType.DISPLAY:
            base_prompt = (
                "Create a professional, high-quality, product-focused photo suitable for "
                "an online store's storefront/display (витринное фото). "
                "Seamlessly replace the background of the input product image with a "
                "stylized, minimalist, and aesthetically pleasing background, while keeping the product clear and well-lit. "
                "Ensure the final image is visually appealing and studio-quality."
            )
            return base_prompt

        gender_text = gender.value
        height = data.get('height', '170')
        location = data.get('location', LocationType.STUDIO).value
        age = data.get('age', '25-35')
        size = data.get('size', SizeType.SIZE_42_46).value if gender != GenderType.KIDS else ""
        location_style = data.get('location_style', LocationStyle.REGULAR).value
        pose = data.get('pose', PoseType.STANDING).value
        view = data.get('view', ViewType.FRONT).value

        model_details = f"a professional, natural-looking model, {gender_text} clothing, height {height} cm, age range {age}"
        if size:
             model_details += f", wearing size {size}"

        scene_details = f"in a {location} setting, with a {location_style} atmosphere. Pose: {pose}, View: {view}."

        prompt = (
            f"Generate a hyper-realistic, high-definition (4k), professional fashion photograph. "
            f"The image must feature **{model_details}**. "
            f"The model should be perfectly integrated with the clothing from the input image. "
            f"Scene: **{scene_details}**. "
            f"The model should be well-lit, and the final image should look like it was taken by a top fashion photographer. "
            f"Focus on natural-looking hands and realistic facial features (if visible). "
            f"Exclude any watermarks or text overlays."
        )

        return prompt

    async def generate_summary(self, data: Dict[str, Any]) -> str:
        """
        Генерирует текстовую сводку выбранных параметров для подтверждения.
        """
        summary_parts = []

        gender = data.get('gender', GenderType.DISPLAY)
        summary_parts.append(f"📦 **Категория**: {gender.value.capitalize()}")

        if gender != GenderType.DISPLAY:
            summary_parts.append(f"📏 **Рост модели**: {data.get('height', 'Не указан')} см")
            summary_parts.append(f"📍 **Локация**: {data.get('location', LocationType.STUDIO).value}")
            summary_parts.append(f"🎂 **Возраст модели**: {data.get('age', 'Не указан')}")

            if gender != GenderType.KIDS:
                summary_parts.append(f"📐 **Размер**: {data.get('size', SizeType.SIZE_42_46).value}")

            summary_parts.append(f"🎨 **Стиль локации**: {data.get('location_style', LocationStyle.REGULAR).value}")
            summary_parts.append(f"🧘 **Положение тела**: {data.get('pose', PoseType.STANDING).value}")
            summary_parts.append(f"👀 **Вид**: {data.get('view', ViewType.FRONT).value}")

        return "\n".join(summary_parts)

    # --- ОБРАБОТЧИКИ АДМИНИСТРАТОРА ---

    async def add_balance_handler(self, message: Message):
        """Обработчик команды /add_balance (Только для ADMIN_ID)"""
        if message.from_user.id != ADMIN_ID:
            await message.answer("❌ Эта команда доступна только администратору.")
            return

        try:
            parts = message.text.split()
            if len(parts) != 3:
                await message.answer("⚠️ Использование: `/add_balance [user_id] [количество_генераций]`", parse_mode="Markdown")
                return

            target_user_id = int(parts[1])
            amount = int(parts[2])

            if amount <= 0:
                await message.answer("❌ Количество генераций должно быть положительным числом.")
                return

            current_balance = self.db.get_user_balance(target_user_id)
            new_balance = current_balance + amount
            self.db.update_user_balance(target_user_id, new_balance)

            await message.answer(
                f"✅ Баланс пользователя `{target_user_id}` обновлен.\n"
                f"Добавлено: {amount} генераций.\n"
                f"Новый баланс: {new_balance} генераций."
            , parse_mode="Markdown")

        except ValueError:
            await message.answer("❌ Неверный формат ID или количества. Используйте целые числа.")
        except Exception as e:
            logger.error(f"Ошибка в add_balance_handler: {e}")
            await message.answer(f"❌ Произошла ошибка при обновлении баланса: {e}")

    async def stats_handler(self, message: Message):
        """Обработчик команды /stats (Только для ADMIN_ID)"""
        if message.from_user.id != ADMIN_ID:
            await message.answer("❌ Эта команда доступна только администратору.")
            return

        total_users, total_generations, total_balance = self.db.get_all_users_stats()

        stats_text = (
            "📊 **Статистика Бота**\n\n"
            f"👤 Всего пользователей: {total_users}\n"
            f"🎨 Всего генераций: {total_generations}\n"
            f"💰 Общий остаток баланса: {total_balance} генераций"
        )
        await message.answer(stats_text, parse_mode="Markdown")

    # --- ОСНОВНЫЕ ОБРАБОТЧИКИ ---

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

        if current_balance <= 0 and not GEMINI_DEMO_MODE:
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

        if gender != GenderType.DISPLAY:
            try:
                media_group = MediaGroupBuilder()
                photo1 = FSInputFile("photo/example1.jpg")
                photo2 = FSInputFile("photo/example2.jpg")
                media_group.add_photo(media=photo1)
                media_group.add_photo(media=photo2)
                await callback.message.answer_media_group(media=media_group.build())
            except Exception as e:
                logger.warning(f"Не удалось загрузить примеры фото: {e}")

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

        temp_path = None
        try:
            file = await self.bot.get_file(photo_file_id)
            file_path = file.file_path

            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
            temp_path = temp_file.name
            temp_file.close()

            await self.bot.download_file(file_path, temp_path)
            await state.update_data(temp_photo_path=temp_path)

        except Exception as e:
            logger.error(f"Ошибка при сохранении фото: {e}")
            await message.answer("❌ Ошибка при обработке фото. Попробуйте еще раз.")
            if temp_path and os.path.exists(temp_path):
                 os.unlink(temp_path)
            return

        data = await state.get_data()
        gender = data['gender']

        if gender == GenderType.DISPLAY:
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

            if current_balance <= 0 and not GEMINI_DEMO_MODE:
                await callback.message.answer("❌ Недостаточно генераций. Пополните баланс.")
                await state.clear()
                return

            if not GEMINI_DEMO_MODE:
                new_balance = current_balance - 1
                self.db.update_user_balance(user_id, new_balance)
            else:
                new_balance = current_balance

            data = await state.get_data()
            prompt = data.get('prompt', '')
            temp_photo_path = data.get('temp_photo_path')

            generating_msg = await callback.message.answer(
                f"🎨 Генерация изображения началась... Это может занять 10-20 секунд.\n\n"
                f"Использовано 1 генерация ({'Демо-режим' if GEMINI_DEMO_MODE else 'Боевой режим'})\n"
                f"Осталось генераций: {new_balance}"
            )

            try:
                # 1. Генерируем изображение через Gemini API
                processed_image_bytes = call_nano_banana_api(temp_photo_path, prompt)

                # 2. Пересохранение через PIL для гарантии совместимости с Telegram
                # Pillow открывает байты, полученные из call_nano_banana_api
                image_stream = BytesIO(processed_image_bytes)
                img = Image.open(image_stream)

                output_stream = BytesIO()
                # Сохраняем в JPEG, что обычно устраняет проблемы "IMAGE_PROCESS_FAILED"
                img.save(output_stream, format='JPEG', quality=90)
                final_image_bytes = output_stream.getvalue()

                # 3. Отправляем сгенерированное изображение
                generated_image = BufferedInputFile(final_image_bytes, filename="generated_fashion.jpg")

                await callback.message.answer_photo(
                    generated_image,
                    caption="✨ Генерация завершена успешно!"
                )

                await generating_msg.delete()

            except Exception as e:
                logger.error(f"Ошибка при генерации изображения: {e}")
                await generating_msg.delete()

                error_msg = str(e)
                if "location is not supported" in error_msg.lower() and not GEMINI_DEMO_MODE:
                    await callback.message.answer(
                        "❌ Сервис генерации изображений недоступен в вашем регионе.\n\n"
                        "Ваш баланс был возвращен."
                    )
                    self.db.update_user_balance(user_id, current_balance)
                else:
                    await callback.message.answer(
                        f"❌ Произошла ошибка при генерации изображения:\n\n"
                        f"`{error_msg}`\n\n"
                        f"Попробуйте изменить параметры или обратитесь в поддержку.\n"
                    )
                    if not GEMINI_DEMO_MODE:
                        self.db.update_user_balance(user_id, current_balance)

            finally:
                # Очищаем состояние и удаляем временный файл
                await state.clear()
                if temp_photo_path and os.path.exists(temp_photo_path):
                    os.unlink(temp_photo_path)

        elif callback.data == "confirm_edit":
            await state.clear()
            await self.create_photo_handler(callback)

# --- ЗАПУСК БОТА ---

async def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не установлен. Завершение работы.")
        return

    bot_instance = FashionBot(token=BOT_TOKEN)
    logger.info("🤖 Бот запущен!")
    await bot_instance.dp.start_polling(bot_instance.bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Бот остановлен вручную.")