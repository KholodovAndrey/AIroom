
import asyncio
import logging
import sqlite3
import os
import sys
import tempfile
import base64
from enum import Enum
from typing import Dict, Any, Tuple
from dataclasses import dataclass

# Импорт из dotenv для загрузки переменных окружения
from dotenv import load_dotenv

# Aiogram imports
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, FSInputFile, BufferedInputFile
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.utils.media_group import MediaGroupBuilder

# Gemini imports
from google import genai
from google.genai import types
from google.api_core import exceptions
from PIL import Image
import io

# Загрузка переменных окружения
load_dotenv()

# Конфигурация из .env
BOT_TOKEN = os.getenv("BOT_TOKEN")
# Преобразование ADMIN_ID в число, по умолчанию 0 (никто не админ)
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@bnbslow")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not BOT_TOKEN:
    print("❌ BOT_TOKEN не установлен в .env файле")
    sys.exit(1)

if not GEMINI_API_KEY:
    print("❌ GEMINI_API_KEY не установлен в .env файле")
    # Продолжаем, чтобы бот запустился в демо-режиме, но выводим предупреждение
    print("⚠️ GEMINI_API_KEY не установлен. Бот будет работать в демо-режиме для генерации.")

# Определение имени для логгера
name = "FashionBot"

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(name)

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('fashion_bot.db', check_same_thread=False)
    cursor = conn.cursor()

    # Таблица пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            balance INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Таблица генераций
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

# --- Константы и перечисления ---

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

# --- Класс для работы с БД (Исправленная структура) ---

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
            # Регистрация нового пользователя
            self.cursor.execute(
                'INSERT INTO users (user_id, balance) VALUES (?, ?)',
                (user_id, 0)
            )
            self.conn.commit()
            return 0

    def update_user_balance(self, user_id: int, balance: int):
        # Используем REPLACE, чтобы обновить баланс или вставить нового пользователя
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

    def get_all_users_stats(self) -> Tuple[int, int, int]:
        """Возвращает (total_users, total_generations, total_balance)"""
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
    client = genai.Client(api_key=GEMINI_API_KEY)

    # 2. Чтение изображения
    try:
        input_image = Image.open(input_image_path)
    except Exception as e:
        raise ValueError(f"Ошибка чтения исходного изображения: {e}")

    # 3. Вызов модели
    # 🌟 ИСПРАВЛЕНИЕ: Используем 'config' вместо 'generation_config'
    api_config = extra_params if extra_params is not None else {}

    # Формируем 'config', если он не передан
    if 'config' not in api_config:
        api_config['config'] = {
            # Запрашиваем как изображение, так и текст (хотя ожидаем изображение)
            # Примечание: для генерации изображений в API Gemini обычно не требуется
            # указывать response_modalities. Этот ключ может быть лишним.
            # Если возникнут проблемы, удалите этот блок полностью.
            "response_modalities": ['TEXT', 'IMAGE']
        }

    response = client.models.generate_content(
        model="gemini-2.5-flash-image",
        contents=[prompt, input_image],
        config=api_config.get('config') # Передаем только значение ключа 'config'
    )

    # 4. Корректная обработка и извлечение изображения

    if not response.candidates:
        raise Exception("API не вернул кандидатов (candidates).")

    candidate = response.candidates[0]

    # 1. Ищем часть, которая содержит Изображение (inline_data с байтами 'data')
    image_part = next(
        (
            p for p in candidate.content.parts
            if hasattr(p, 'inline_data') and hasattr(p.inline_data, 'data')
        ),
        None
    )

    if image_part is None:
        # 2. Если изображение не найдено, ищем Текстовое объяснение (например, ошибку или блокировку)
        text_explanation = ""
        for part in candidate.content.parts:
            if hasattr(part, 'text'):
                text_explanation += part.text + "\n"

        # Проверяем причину завершения (например, SAFETY)
        finish_reason = candidate.finish_reason.name if hasattr(candidate, 'finish_reason') else "UNKNOWN"

        # Если есть текст, возвращаем его
        if text_explanation.strip():
            # Если текст найден, возвращаем его как ошибку с указанием причины завершения
            raise Exception(
                f"Gemini вернул текстовое объяснение вместо изображения. Причина завершения: {finish_reason}. "
                f"Текст: {text_explanation.strip()}"
            )

        # Если нет ни изображения, ни текста, выбрасываем общую ошибку
        raise Exception(f"Получен ответ с неизвестной структурой. Причина завершения: {finish_reason}.")

    # 3. Если изображение найдено (image_part != None), извлекаем данные
    inline_data = image_part.inline_data
    data_content = inline_data.data

    if isinstance(data_content, str):
        # Если это строка, считаем, что это Base64 и декодируем
        try:
            output_image_bytes = base64.b64decode(data_content)
        except Exception as e:
            raise Exception(f"Ошибка декодирования Base64. Ошибка: {e}")

    elif isinstance(data_content, bytes):
        # Если это уже байты, используем их напрямую
        output_image_bytes = data_content

    else:
        # Неожиданный тип данных (хотя этот блок теперь менее вероятен)
        raise Exception(f"Объект inline_data.data имеет неожиданный тип: {type(data_content)}. Ожидались str (Base64) или bytes.")

    return output_image_bytes

# Альтернативная функция для демонстрации (заглушка)
def generate_demo_image(prompt: str) -> bytes:
    """Генерирует демо-изображение, когда Gemini недоступен"""
    from PIL import Image, ImageDraw

    # Создаем простое изображение с текстом
    img = Image.new('RGB', (1024, 1024), color=(73, 109, 137))
    d = ImageDraw.Draw(img)

    try:
        # Попытка использовать системный шрифт или стандартный
        font = ImageFont.load_default(size=40)
    except ImportError:
        font = None

    # Простой текст вместо изображения
    text = "ДЕМО-РЕЖИМ\n\nGemini API недоступен или произошла ошибка.\n\nПромпт:\n" + prompt[:300] + "..."

    # Разбиваем текст на строки
    lines = []
    current_line = ""
    for word in text.split():
        if len(current_line + word) > 40: # Примерное ограничение длины
            lines.append(current_line)
            current_line = word + " "
        else:
            current_line += word + " "
    lines.append(current_line)

    # Рисуем текст
    y = 50
    for line in lines:
        d.text((50, y), line, fill=(255, 255, 255), font=font)
        y += 45

    # Сохраняем в bytes
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='PNG')
    return img_byte_arr.getvalue()


# --- Главный класс бота ---

class FashionBot:
    def __init__(self, token: str): # Изменено init на __init__
        self.bot = Bot(token=token)
        self.dp = Dispatcher()
        self.db = Database()
        self.setup_handlers()

    def setup_handlers(self):
        # Команда старт
        self.dp.message.register(self.start_handler, Command("start"))

        # Команды администратора (Исправлено и дополнено)
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
        self.dp.message.register(self.photo_handler, StateFilter(ProductCreationStates.waiting_for_photo), F.photo)
        self.dp.message.register(self.handle_wrong_photo_input, StateFilter(ProductCreationStates.waiting_for_photo)) # Обработка не-фото
        self.dp.message.register(self.height_handler, StateFilter(ProductCreationStates.waiting_for_height))
        self.dp.callback_query.register(self.location_handler, F.data.startswith("location_"))
        self.dp.callback_query.register(self.age_handler, F.data.startswith("age_"))
        self.dp.callback_query.register(self.size_handler, F.data.startswith("size_"))
        self.dp.callback_query.register(self.location_style_handler, F.data.startswith("style_"))
        self.dp.callback_query.register(self.pose_handler, F.data.startswith("pose_"))
        self.dp.callback_query.register(self.view_handler, F.data.startswith("view_"))
        self.dp.callback_query.register(self.confirmation_handler, F.data.startswith("confirm_"))

    # --- Вспомогательные функции FSM ---

    async def generate_summary(self, data: Dict[str, Any]) -> str:
        """Генерирует сводку параметров для пользователя."""
        gender = data.get('gender')

        summary = []
        summary.append(f"Тип: **{gender.value}**")

        if gender == GenderType.DISPLAY:
            return "\n".join(summary)

        summary.append(f"Рост: **{data.get('height', 'N/A')} см**")
        summary.append(f"Локация: **{data.get('location', LocationType.STREET).value}**")
        summary.append(f"Возраст: **{data.get('age', 'N/A')}**")
        if data.get('size'):
            summary.append(f"Размер: **{data.get('size', SizeType.SIZE_42_46).value}**")
        summary.append(f"Стиль: **{data.get('location_style', LocationStyle.REGULAR).value}**")
        summary.append(f"Поза: **{data.get('pose', PoseType.STANDING).value}**")
        summary.append(f"Вид: **{data.get('view', ViewType.FRONT).value}**")

        return "\n".join(summary)

    async def generate_prompt(self, data: Dict[str, Any]) -> str:
        """Генерирует финальный промпт для Gemini API на английском."""
        gender = data.get('gender')

        if gender == GenderType.DISPLAY:
            return (
                "Replace the background of this product image with a stylish, modern, minimalist display zone, "
                "like a smooth white floor or a light-colored wooden table, focused on the product. "
                "Ensure the product is clearly the main subject. "
                "Remove any distractions like shadows or wrinkles from the background. "
                "The image should be high-resolution and professionally lit for e-commerce. "
                "Do not add any human model."
            )

        prompt = "Generate a hyper-realistic, high-quality professional fashion photo for e-commerce. "

        # Модель
        gender_map = {
            GenderType.WOMEN: "a realistic female model wearing the garment",
            GenderType.MEN: "a realistic male model wearing the garment",
            GenderType.KIDS: f"a realistic child model (age {data.get('age', '5')}) wearing the garment"
        }

        prompt += gender_map.get(gender, "a model wearing the garment") + ". "

        # Детали
        if data.get('height'):
            prompt += f"Model height: {data['height']}cm. "
        if data.get('size'):
            prompt += f"Model garment size: {data['size'].value}. "
        if data.get('age') and gender != GenderType.KIDS:
            prompt += f"Model appearance age: {data['age']}. "

        # Поза и Вид
        prompt += f"Pose: {data.get('pose', PoseType.STANDING).value.lower()}. "
        prompt += f"View: {data.get('view', ViewType.FRONT).value.lower()}. "

        # Локация и Стиль
        location = data.get('location', LocationType.STUDIO)
        style = data.get('location_style', LocationStyle.REGULAR)

        location_desc = {
            LocationType.STREET: "Location: urban street photography style background, high-end commercial photo.",
            LocationType.STUDIO: "Location: professional photo studio, solid light background, soft lighting.",
            LocationType.FLOOR_ZONE: "Location: styled floor photo zone, flat lay composition or model sitting on the floor."
        }.get(location, "Location: professional photo environment.")

        prompt += location_desc + " "

        style_desc = {
            LocationStyle.NEW_YEAR: "Theme: luxury Christmas/New Year setting, festive decorations, warm lights.",
            LocationStyle.SUMMER: "Theme: vibrant summer atmosphere, bright sunlight, beach or sunny city setting.",
            LocationStyle.NATURE: "Theme: natural outdoor setting, green foliage, soft natural light.",
            LocationStyle.PARK_WINTER: "Theme: winter park scene, soft snow, cold colors.",
            LocationStyle.PARK_SUMMER: "Theme: lush green park, sunny day.",
            LocationStyle.REGULAR: "Theme: neutral fashion theme.",
            LocationStyle.CAR: "Theme: next to a luxury car, high-fashion editorial style."
        }.get(style, "")

        prompt += style_desc + " "

        # Инструкции для Gemini
        prompt += (
            "Use the input image only as a reference for the garment and texture. The generated photo must look "
            "like a real photograph taken by a professional fashion photographer. "
            "Natural skin, realistic hands, perfect lighting. "
            "Remove the background from the input image and perfectly integrate the garment onto the model. "
            "Do not use cartoon, 3D render, or drawing styles. "
            "The final image should be a single, stunning photograph."
        )

        return prompt.strip()

    # --- Обработчики команд ---

    async def start_handler(self, message: Message):
        """Обработчик команды /start"""
        welcome_text = (
            "👋 Добро пожаловать в **Fashion AI Generator**!\n\n"
            "Превращаем фотографии вашей одежды в профессиональные снимки на моделях.\n\n"
            "📋 Перед использованием ознакомьтесь с:\n"
            "1. Условиями использования\n"
            "2. Согласием на обработку данных"
        )

        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Принять и продолжить", callback_data="accept_terms")
        builder.button(text="💬 Написать в поддержку", callback_data="support")
        builder.adjust(1)

        # Обновляем данные пользователя в БД
        self.db.get_user_balance(message.from_user.id)

        await message.answer(welcome_text, reply_markup=builder.as_markup(), parse_mode="Markdown")

    async def accept_terms_handler(self, callback: CallbackQuery):
        """Обработчик принятия условий"""
        await callback.message.delete()
        await self.show_main_menu(callback.message)

    async def show_main_menu(self, message: Message):
        """Показать главное меню"""
        user_id = message.from_user.id
        current_balance = self.db.get_user_balance(user_id)

        main_menu_text = (
            f"🎯 Главное меню:\n\n"
            f"Текущий баланс: **{current_balance} генераций**"
        )
        builder = InlineKeyboardBuilder()
        builder.button(text="📸 Создать фото", callback_data="create_photo")
        builder.button(text="💳 Пополнить баланс", callback_data="topup_balance")
        builder.button(text="🆘 Написать в поддержку", callback_data="support")
        builder.adjust(1)

        await message.answer(main_menu_text, reply_markup=builder.as_markup(), parse_mode="Markdown")

    async def back_to_main_handler(self, callback: CallbackQuery, state: FSMContext):
        """Обработчик возврата в главное меню"""
        # Сбрасываем FSM состояние и удаляем временный файл, если он есть
        data = await state.get_data()
        temp_photo_path = data.get('temp_photo_path')
        if temp_photo_path and os.path.exists(temp_photo_path):
            os.unlink(temp_photo_path)
            logger.info(f"Удален временный файл: {temp_photo_path}")

        await state.clear()

        await callback.message.delete()
        await self.show_main_menu(callback.message)

    async def support_handler(self, callback: CallbackQuery):
        """Обработчик связи с поддержкой"""
        support_text = f"📞 Для связи с поддержкой напишите: **{SUPPORT_USERNAME}**"
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 Назад", callback_data="back_to_main")
        await callback.message.answer(support_text, reply_markup=builder.as_markup(), parse_mode="Markdown")

    async def topup_balance_handler(self, callback: CallbackQuery):
        """Обработчик пополнения баланса"""
        user_id = callback.from_user.id
        current_balance = self.db.get_user_balance(user_id)

        balance_text = (
            f"💳 Пополнение баланса\n\n"
            f"Текущий баланс: **{current_balance} генераций**\n\n"
            "Для пополнения баланса напишите нашему менеджеру:\n"
            f"**{SUPPORT_USERNAME}**\n\n"
            f"Укажите ваш ID для зачисления: `{user_id}`"
        )

        builder = InlineKeyboardBuilder()
        # Ссылка на телеграм-пользователя (удаляем @)
        builder.button(text="📞 Написать менеджеру", url=f"tg://resolve?domain={SUPPORT_USERNAME.lstrip('@')}")
        builder.button(text="🔙 Назад", callback_data="back_to_main")
        builder.adjust(1)

        await callback.message.answer(balance_text, reply_markup=builder.as_markup(), parse_mode="Markdown")

    async def create_photo_handler(self, callback: CallbackQuery):
        """Обработчик начала создания фото"""
        user_id = callback.from_user.id
        current_balance = self.db.get_user_balance(user_id)

        if current_balance <= 0 and ADMIN_ID != user_id: # Для админа разрешаем даже с 0
            builder = InlineKeyboardBuilder()
            builder.button(text="💳 Пополнить баланс", callback_data="topup_balance")
            builder.button(text="🔙 Назад", callback_data="back_to_main")
            builder.adjust(1)

            await callback.message.answer(
                "❌ **Недостаточно генераций**. Пожалуйста, пополните баланс.",
                reply_markup=builder.as_markup(),
                parse_mode="Markdown"
            )
            return

        gender_text = "Выберите пожалуйста какой продукт вы хотите создать?"
        builder = InlineKeyboardBuilder()
        builder.button(text="👚 Женская одежда", callback_data="gender_women")
        builder.button(text="👔 Мужская одежда", callback_data="gender_men")
        builder.button(text="👶 Детская одежда", callback_data="gender_kids")
        builder.button(text="🖼 Витринное фото", callback_data="gender_display")
        builder.button(text="🔙 Назад", callback_data="back_to_main")
        builder.adjust(1)

        await callback.message.answer(gender_text, reply_markup=builder.as_markup())

    async def gender_select_handler(self, callback: CallbackQuery, state: FSMContext):
        """Обработчик выбора пола/категории"""
        await callback.message.delete()

        gender_map = {
            "gender_women": GenderType.WOMEN,
            "gender_men": GenderType.MEN,
            "gender_kids": GenderType.KIDS,
            "gender_display": GenderType.DISPLAY
        }

        gender = gender_map[callback.data]
        await state.update_data(gender=gender)

        # Для не-витринного фото отправляем примеры
        if gender != GenderType.DISPLAY:
            try:
                media_group = MediaGroupBuilder()
                # Предполагаем, что файлы 'photo/example1.jpg' и 'photo/example2.jpg' существуют
                photo1 = FSInputFile("photo/example1.jpg")
                photo2 = FSInputFile("photo/example2.jpg")
                media_group.add_photo(media=photo1, caption="Пример 1: Фотография товара, преобразованная в студийный снимок на модели.")
                media_group.add_photo(media=photo2, caption="Пример 2: Фотография товара, преобразованная в уличный снимок на модели.")
                await callback.message.answer_media_group(media=media_group.build())
            except FileNotFoundError:
                logger.warning("Не удалось найти примеры фото (photo/example*.jpg). Продолжаем без них.")
            except Exception as e:
                logger.warning(f"Не удалось загрузить или отправить примеры фото: {e}")


        instruction_text = (
            f"📸 Пожалуйста пришлите **фотографию вашего товара** для создания {gender.value.lower()}.\n\n"
            "⚠️ **Требования к фото:** Товар должен быть четко виден, без лишних бликов и размытостей, "
            "желательно на нейтральном фоне, без модели.\n\n"
            f"Если остались вопросы - пишите в поддержку {SUPPORT_USERNAME}"
        )

        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 Назад", callback_data="create_photo")

        await callback.message.answer(instruction_text, reply_markup=builder.as_markup(), parse_mode="Markdown")
        await state.set_state(ProductCreationStates.waiting_for_photo)

    async def handle_wrong_photo_input(self, message: Message):
        """Обрабатывает ввод, отличный от фото, в состоянии ожидания фото."""
        await message.answer("❌ Пожалуйста, отправьте фотографию товара, а не текст или другой тип файла.")

    async def photo_handler(self, message: Message, state: FSMContext):
        """Обработчик загрузки фото"""
        # F.photo в регистре гарантирует, что здесь мы имеем дело с фото
        photo_file_id = message.photo[-1].file_id

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
            prompt = await self.generate_prompt(data)
            await state.update_data(prompt=prompt)

            summary = await self.generate_summary(data)
            summary_text = f"📋 Проверьте выбранные параметры:\n\n{summary}"

            builder = InlineKeyboardBuilder()
            builder.button(text="🚀 Начать генерацию", callback_data="confirm_generate")
            builder.button(text="✏️ Внести изменения", callback_data="confirm_edit")
            builder.adjust(1)

            await message.answer(summary_text, reply_markup=builder.as_markup(), parse_mode="Markdown")
            await state.set_state(ProductCreationStates.waiting_for_confirmation)
        else:
            await state.set_state(ProductCreationStates.waiting_for_height)
            await message.answer("📏 Напишите **рост модели** (в см):", parse_mode="Markdown")

    async def height_handler(self, message: Message, state: FSMContext):
        """Обработчик ввода роста"""
        height_str = message.text.strip()
        if not height_str.isdigit():
            await message.answer("❌ Пожалуйста, введите **числовое значение роста** в см (например, 175):", parse_mode="Markdown")
            return

        height = int(height_str)
        if not (50 <= height <= 220): # Простая проверка на адекватность
            await message.answer("❌ Рост должен быть в диапазоне от 50 до 220 см. Попробуйте снова:")
            return

        await state.update_data(height=height)
        await state.set_state(ProductCreationStates.waiting_for_location)

        builder = InlineKeyboardBuilder()
        builder.button(text="🏙 Улица", callback_data="location_street")
        builder.button(text="📸 Фотостудия", callback_data="location_studio")
        builder.button(text="📐 Фотозона на полу", callback_data="location_floor")
        builder.adjust(1)

        await message.answer("📍 Пожалуйста выберите **локацию**:", reply_markup=builder.as_markup(), parse_mode="Markdown")

    async def location_handler(self, callback: CallbackQuery, state: FSMContext):
        """Обработчик выбора локации"""
        await callback.message.delete()

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

        await callback.message.answer("🎂 Пожалуйста выберите **возраст модели**:", reply_markup=builder.as_markup(), parse_mode="Markdown")
        await state.set_state(ProductCreationStates.waiting_for_age)

    async def age_handler(self, callback: CallbackQuery, state: FSMContext):
        """Обработчик выбора возраста"""
        await callback.message.delete()

        age = callback.data.replace("age_", "")
        await state.update_data(age=age)

        data = await state.get_data()
        gender = data['gender']

        if gender == GenderType.KIDS:
            # Детская одежда не требует выбора размера
            await self.skip_size_and_go_to_style(callback, state)
        else:
            await state.set_state(ProductCreationStates.waiting_for_size)

            builder = InlineKeyboardBuilder()
            builder.button(text="42-46", callback_data="size_42_46")
            builder.button(text="50-54", callback_data="size_50_54")
            builder.button(text="58-64", callback_data="size_58_64")
            builder.button(text="64-68", callback_data="size_64_68")
            builder.adjust(2)

            await callback.message.answer("📏 Пожалуйста выберите **размер одежды**:", reply_markup=builder.as_markup(), parse_mode="Markdown")

    async def size_handler(self, callback: CallbackQuery, state: FSMContext):
        """Обработчик выбора размера"""
        await callback.message.delete()

        size_map = {
            "size_42_46": SizeType.SIZE_42_46,
            "size_50_54": SizeType.SIZE_50_54,
            "size_58_64": SizeType.SIZE_58_64,
            "size_64_68": SizeType.SIZE_64_68
        }

        size = size_map[callback.data]
        await state.update_data(size=size)

        await self.go_to_location_style(callback, state)

    async def skip_size_and_go_to_style(self, callback: CallbackQuery, state: FSMContext):
        """Переход к выбору стиля (для детской одежды, где размер пропущен)"""
        await self.go_to_location_style(callback, state)


    async def go_to_location_style(self, callback: CallbackQuery, state: FSMContext):
        """Общая функция для перехода к выбору стиля локации"""
        await state.set_state(ProductCreationStates.waiting_for_location_style)

        builder = InlineKeyboardBuilder()
        builder.button(text="🎄 Новогодняя", callback_data="style_new_year")
        builder.button(text="☀️ Лето", callback_data="style_summer")
        builder.button(text="🌳 Природа", callback_data="style_nature")
        builder.button(text="🏞 Парк (зима)", callback_data="style_park_winter")
        builder.button(text="🌲 Парк (лето)", callback_data="style_park_summer")
        builder.button(text="🏢 Обычный (студия/город)", callback_data="style_regular")
        builder.button(text="🚗 Рядом с машиной", callback_data="style_car")
        builder.adjust(2)

        await callback.message.answer("🎨 Пожалуйста, выберите **стиль локации**:", reply_markup=builder.as_markup(), parse_mode="Markdown")

    async def location_style_handler(self, callback: CallbackQuery, state: FSMContext):
        """Обработчик выбора стиля локации"""
        await callback.message.delete()

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

        await callback.message.answer("🧘 Пожалуйста выберите **положение тела**:", reply_markup=builder.as_markup(), parse_mode="Markdown")

    async def pose_handler(self, callback: CallbackQuery, state: FSMContext):
        """Обработчик выбора позы"""
        await callback.message.delete()

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

        await callback.message.answer("👀 Пожалуйста выберите **вид**:", reply_markup=builder.as_markup(), parse_mode="Markdown")

    async def view_handler(self, callback: CallbackQuery, state: FSMContext):
        """Обработчик выбора вида"""
        await callback.message.delete()

        view_map = {
            "view_back": ViewType.BACK,
            "view_front": ViewType.FRONT
        }

        view = view_map[callback.data]
        await state.update_data(view=view)

        # Формируем сводку и промпт
        data = await state.get_data()
        summary = await self.generate_summary(data)
        prompt = await self.generate_prompt(data)

        await state.update_data(prompt=prompt)

        summary_text = (
            f"📋 Проверьте выбранные параметры:\n\n"
            f"{summary}\n\n"
            f"**Стоимость: 1 генерация**"
        )

        builder = InlineKeyboardBuilder()
        builder.button(text="🚀 Начать генерацию", callback_data="confirm_generate")
        builder.button(text="✏️ Внести изменения", callback_data="confirm_edit")
        builder.adjust(1)

        await callback.message.answer(summary_text, reply_markup=builder.as_markup(), parse_mode="Markdown")
        await state.set_state(ProductCreationStates.waiting_for_confirmation)

    async def confirmation_handler(self, callback: CallbackQuery, state: FSMContext):
        """Обработчик подтверждения генерации"""
        await callback.message.delete()
        user_id = callback.from_user.id
        current_balance = self.db.get_user_balance(user_id)

        data = await state.get_data()
        temp_photo_path = data.get('temp_photo_path')

        if callback.data == "confirm_generate":

            # Проверка баланса перед тратой
            if current_balance <= 0 and ADMIN_ID != user_id:
                await callback.message.answer("❌ **Недостаточно генераций**. Пополните баланс.", parse_mode="Markdown")
                await state.clear()
                return

            if current_balance > 0:
                new_balance = current_balance - 1
                self.db.update_user_balance(user_id, new_balance)
            else: # Это может быть только админ с балансом 0
                new_balance = 0

            prompt = data.get('prompt', '')

            # Отправляем сообщение о начале генерации
            generating_msg = await callback.message.answer(
                f"🎨 Генерация изображения началась... Это может занять до 20 секунд.\n\n"
                f"Использовано 1 генерация\n"
                f"Осталось генераций: **{new_balance}**"
            )

            try:
                # Генерируем изображение через Gemini API или демо-режим
                if GEMINI_API_KEY:
                    processed_image_bytes = call_nano_banana_api(temp_photo_path, prompt)
                else:
                    processed_image_bytes = generate_demo_image(prompt)

                # Отправляем сгенерированное изображение
                generated_image = BufferedInputFile(processed_image_bytes, filename="generated_fashion.png")

                await callback.message.answer_photo(
                    generated_image,
                    caption="✨ Генерация завершена **успешно**!"
                )

                # Добавляем запись в историю
                self.db.add_generation(user_id, prompt)

            except Exception as e:
                logger.error(f"Ошибка при генерации изображения: {e}")

                # Возвращаем баланс, если была ошибка API и это не демо-режим
                if GEMINI_API_KEY and current_balance > 0:
                    self.db.update_user_balance(user_id, current_balance)
                    error_footer = "Ваш баланс был **возвращен**."
                else:
                    error_footer = "Баланс не был списан (демо-режим или нулевой баланс)."

                await callback.message.answer(
                    f"❌ **Ошибка генерации**\n\n"
                    f"Произошла ошибка при обращении к сервису: `{str(e)[:150]}...`\n\n"
                    f"Пожалуйста, попробуйте еще раз или обратитесь в поддержку {SUPPORT_USERNAME}.\n\n"
                    f"{error_footer}",
                    parse_mode="Markdown"
                )

            finally:
                # Удаляем сообщение о генерации
                try:
                    await generating_msg.delete()
                except Exception:
                    pass # Игнорируем ошибку удаления

                # Удаляем временный файл
                if temp_photo_path and os.path.exists(temp_photo_path):
                    os.unlink(temp_photo_path)

        elif callback.data == "confirm_edit":
            # Внести изменения - возвращаемся к началу FSM (выбору пола)
            # При этом сохраняем фото и удаляем его в back_to_main_handler
            await self.back_to_main_handler(callback, state)
            return

        # Завершение FSM
        await state.clear()

        # Показываем главное меню после генерации или ошибки
        await self.show_main_menu(callback.message)

    # --- Административные обработчики (Дополнено) ---

    async def add_balance_handler(self, message: Message):
        """Добавление баланса пользователю (только для админа)"""
        if message.from_user.id != ADMIN_ID:
            await message.answer("❌ У вас нет прав администратора для этой команды.")
            return

        try:
            # Ожидаемый формат: /add_balance <user_id> <amount>
            _, user_id_str, amount_str = message.text.split()
            user_id = int(user_id_str)
            amount = int(amount_str)

            current_balance = self.db.get_user_balance(user_id)
            new_balance = current_balance + amount
            self.db.update_user_balance(user_id, new_balance)

            await message.answer(
                f"✅ Пользователю **{user_id}** добавлено **{amount}** генераций.\n"
                f"Текущий баланс: **{new_balance}**.",
                parse_mode="Markdown"
            )
        except ValueError:
            await message.answer("❌ Ошибка формата. Используйте: `/add_balance ID Количество`", parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Ошибка при добавлении баланса: {e}")
            await message.answer(f"❌ Произошла ошибка при работе с БД: {e}")


    async def stats_handler(self, message: Message):
        """Статистика бота (только для админа)"""
        if message.from_user.id != ADMIN_ID:
            await message.answer("❌ У вас нет прав администратора для этой команды.")
            return

        try:
            total_users, total_generations, total_balance = self.db.get_all_users_stats()

            stats_text = (
                "📊 **Статистика Бота**\n\n"
                f"👤 Всего пользователей: **{total_users}**\n"
                f"📸 Всего генераций: **{total_generations}**\n"
                f"💰 Общий баланс (остаток): **{total_balance}** генераций"
            )

            await message.answer(stats_text, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Ошибка при получении статистики: {e}")
            await message.answer(f"❌ Произошла ошибка при получении статистики: {e}")

    # --- Запуск бота ---

    async def run(self):
        """Запуск бота"""
        logger.info("⚡️ Бот запускается...")
        # Проверка, что бот может получить информацию о себе
        try:
            me = await self.bot.get_me()
            logger.info(f"🤖 Бот @{me.username} успешно запущен!")
        except Exception as e:
            logger.error(f"❌ Не удалось получить информацию о боте. Проверьте BOT_TOKEN: {e}")
            return

        # Запуск цикла обработки событий
        await self.dp.start_polling(self.bot)

if __name__ == "__main__":
    if BOT_TOKEN:
        bot_instance = FashionBot(token=BOT_TOKEN)
        try:
            # Убедитесь, что папка для примеров фото существует (для демо-файлов)
            if not os.path.exists('photo'):
                os.makedirs('photo')
                logger.warning("Создана папка 'photo/'. Поместите туда example1.jpg и example2.jpg для работы примеров.")

            asyncio.run(bot_instance.run())
        except KeyboardInterrupt:
            logger.info("👋 Бот остановлен вручную.")
        except Exception as e:
            logger.critical(f"Критическая ошибка при работе бота: {e}")