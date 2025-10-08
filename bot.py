
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
import time # Добавлено для генерации уникальных имен файлов

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
from google.api_core import exceptions
from google.genai import types # Добавлен импорт types для корректной работы с enums
from PIL import Image, ImageDraw, ImageFont # Добавлен ImageFont для демо-режима
import io

# Загрузка переменных окружения
load_dotenv()

# --- Конфигурация из .env ---

BOT_TOKEN = os.getenv("BOT_TOKEN")
# Преобразование ADMIN_ID в число
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@bnbslow")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not BOT_TOKEN:
    print("❌ BOT_TOKEN не установлен в .env файле")
    sys.exit(1)

# Проверка API-ключа Gemini
GEMINI_DEMO_MODE = False
if not GEMINI_API_KEY:
    print("❌ GEMINI_API_KEY не установлен в .env файле")
    print("⚠️ Бот будет работать в ДЕМО-РЕЖИМЕ для генерации (заглушка).")
    GEMINI_DEMO_MODE = True

# Определение имени для логгера
name = "FashionBot"

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(name)

# --- Инициализация базы данных ---

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

# --- Класс для работы с БД ---

class Database:
    def __init__(self):
        self.conn = sqlite3.connect('fashion_bot.db', check_same_thread=False)
        self.cursor = self.conn.cursor()

    def get_user_balance(self, user_id: int, username: str = None, full_name: str = None) -> int:
        self.cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
        result = self.cursor.fetchone()
        if result:
            # Обновляем username и full_name на случай, если они изменились
            self.cursor.execute(
                'UPDATE users SET username = ?, full_name = ? WHERE user_id = ?',
                (username, full_name, user_id)
            )
            self.conn.commit()
            return result[0]
        else:
            # Регистрация нового пользователя
            self.cursor.execute(
                'INSERT INTO users (user_id, username, full_name, balance) VALUES (?, ?, ?, ?)',
                (user_id, username, full_name, 0)
            )
            self.conn.commit()
            return 0

    def update_user_balance(self, user_id: int, new_balance: int):
        self.cursor.execute(
            'UPDATE users SET balance = ? WHERE user_id = ?',
            (new_balance, user_id)
        )
        self.conn.commit()

    def add_balance(self, user_id: int, amount: int):
        self.cursor.execute(
            'INSERT INTO users (user_id, balance) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET balance = balance + excluded.balance',
            (user_id, amount)
        )
        self.conn.commit()

    def deduct_balance(self, user_id: int, cost: int = 1):
        # Делаем проверку на баланс перед списанием
        current_balance = self.get_user_balance(user_id)
        if current_balance < cost:
            return False

        self.update_user_balance(user_id, current_balance - cost)
        return True

    def add_generation(self, user_id: int, prompt: str):
        self.cursor.execute(
            'INSERT INTO generations (user_id, prompt) VALUES (?, ?)',
            (user_id, prompt)
        )
        self.conn.commit()

    def get_all_users_stats(self) -> Tuple[int, int, int]:
        """Возвращает (total_users, total_generations, total_balance)"""
        self.cursor.execute('SELECT COUNT(*) FROM users')
        total_users = self.cursor.fetchone()[0]

        self.cursor.execute('SELECT COUNT(*) FROM generations')
        total_generations = self.cursor.fetchone()[0]

        self.cursor.execute('SELECT SUM(balance) FROM users')
        total_balance = self.cursor.fetchone()[0] or 0

        return total_users, total_generations, total_balance

# --- Функция вызова API Gemini (Исправлена для надежного парсинга) ---

def call_nano_banana_api(
    input_image_path: str,
    prompt: str,
    extra_params: Dict[str, Any] = None
) -> bytes:
    """
    Отправляет изображение и промпт в Gemini 2.5 Flash Image и извлекает байты.
    """
    if GEMINI_DEMO_MODE:
        return generate_demo_image(prompt)

    client = genai.Client(api_key=GEMINI_API_KEY)

    try:
        input_image = Image.open(input_image_path)
    except Exception as e:
        raise ValueError(f"Ошибка чтения исходного изображения: {e}")

    # 1. Формируем 'config' для generate_content
    api_config = extra_params if extra_params is not None else {}

    if 'config' not in api_config:
        api_config['config'] = {
            "response_modalities": ['TEXT', 'IMAGE']
        }

    # 2. Вызов модели
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash-image",
            contents=[prompt, input_image],
            config=api_config.get('config') # Передаем только значение ключа 'config'
        )
    except exceptions.ResourceExhausted as e:
        raise Exception(f"Превышен лимит API (Resource Exhausted). Повторите позже. Детали: {e}")
    except exceptions.InternalServerError as e:
        raise Exception(f"Внутренняя ошибка API Gemini. Повторите позже. Детали: {e}")
    except Exception as e:
        raise Exception(f"Неизвестная ошибка API Gemini: {e}")

    # 3. Корректная обработка и извлечение изображения (Улучшенная логика)

    if not response.candidates:
        raise Exception("API не вернул кандидатов (candidates).")

    candidate = response.candidates[0]

    # 3.1. Ищем часть, которая содержит Изображение (inline_data с байтами 'data')
    image_part = next(
        (
            p for p in candidate.content.parts
            if hasattr(p, 'inline_data') and hasattr(p.inline_data, 'data')
        ),
        None
    )

    if image_part is None:
        # 3.2. Если изображение не найдено, ищем Текстовое объяснение (ошибка/блокировка)
        text_explanation = ""
        for part in candidate.content.parts:
            if hasattr(part, 'text'):
                text_explanation += part.text + "\n"

        # Проверяем причину завершения (используем импортированный types)
        finish_reason = candidate.finish_reason.name if hasattr(candidate, 'finish_reason') else "UNKNOWN"

        if finish_reason != types.FinishReason.STOP.name:
            # Если генерация остановлена по причине безопасности/другой причине
            safety_info = ", ".join([f"{r.category.name}: {r.probability.name}" for r in candidate.safety_ratings])

            # Если есть текст, возвращаем его
            if text_explanation.strip():
                raise Exception(
                    f"Генерация остановлена. Причина: {finish_reason}. Текст: {text_explanation.strip()}."
                    f" Safety: {safety_info}"
                )

            raise Exception(f"Генерация остановлена. Причина: {finish_reason}. Safety: {safety_info}")

        # Если нет изображения, но причина STOP, ищем текст (на всякий случай)
        if text_explanation.strip():
            raise Exception(f"Gemini вернул только текст, но не изображение: {text_explanation.strip()}")

        # Если нет ни изображения, ни текста
        raise Exception(f"Получен ответ с неизвестной структурой. Причина завершения: {finish_reason}.")

    # 3.3. Если изображение найдено, извлекаем данные
    inline_data = image_part.inline_data
    data_content = inline_data.data

    if isinstance(data_content, str):
        try:
            output_image_bytes = base64.b64decode(data_content)
        except Exception as e:
            raise Exception(f"Ошибка декодирования Base64. Ошибка: {e}")

    elif isinstance(data_content, bytes):
        output_image_bytes = data_content

    else:
        raise Exception(f"Объект inline_data.data имеет неожиданный тип: {type(data_content)}. Ожидались str (Base64) или bytes.")

    return output_image_bytes

# Альтернативная функция для демонстрации (заглушка)
def generate_demo_image(prompt: str) -> bytes:
    """Генерирует демо-изображение, когда Gemini недоступен"""
    # Попытка загрузить системный шрифт
    try:
        font = ImageFont.truetype("Arial.ttf", 40)
    except IOError:
        try:
            # Если системный шрифт не найден, используем шрифт по умолчанию
            font = ImageFont.load_default(size=40)
        except ImportError:
             font = None # Заглушка, если и load_default не работает

    img = Image.new('RGB', (1024, 1024), color=(73, 109, 137))
    d = ImageDraw.Draw(img)

    text = "ДЕМО-РЕЖИМ\n\nGemini API недоступен или произошла ошибка.\n\nПромпт:\n" + prompt[:300] + "..."

    lines = []
    current_line = ""
    for word in text.split():
        # Приблизительная длина строки для 1024x1024
        if len(current_line + word) > 40 and font:
            lines.append(current_line)
            current_line = word + " "
        else:
            current_line += word + " "
    lines.append(current_line)

    y = 50
    for line in lines:
        d.text((50, y), line, fill=(255, 255, 255), font=font)
        y += 45

    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='PNG')
    return img_byte_arr.getvalue()


# --- Главный класс бота ---

class FashionBot:
    def __init__(self, token: str):
        self.bot = Bot(token=token)
        self.dp = Dispatcher()
        self.db = Database()
        self.setup_handlers()

    def setup_handlers(self):
        # Команда старт
        self.dp.message.register(self.start_handler, Command("start"))

        # Команды администратора (проверка на ADMIN_ID)
        self.dp.message.register(self.add_balance_handler, Command("add_balance"), F.from_user.id == ADMIN_ID)
        self.dp.message.register(self.stats_handler, Command("stats"), F.from_user.id == ADMIN_ID)

        # Основные обработчики
        self.dp.callback_query.register(self.accept_terms_handler, F.data == "accept_terms")
        self.dp.callback_query.register(self.support_handler, F.data == "support")
        self.dp.callback_query.register(self.create_photo_handler, F.data == "create_photo")
        self.dp.callback_query.register(self.topup_balance_handler, F.data == "topup_balance")
        self.dp.callback_query.register(self.back_to_main_handler, F.data == "back_to_main")

        # Обработчики создания фото
        self.dp.callback_query.register(self.gender_select_handler, F.data.startswith("gender_"), StateFilter(None))
        self.dp.message.register(self.photo_handler, StateFilter(ProductCreationStates.waiting_for_photo), F.photo)
        self.dp.message.register(self.handle_wrong_photo_input, StateFilter(ProductCreationStates.waiting_for_photo)) # Обработка не-фото
        self.dp.message.register(self.height_handler, StateFilter(ProductCreationStates.waiting_for_height))
        self.dp.callback_query.register(self.location_handler, F.data.startswith("location_"), StateFilter(ProductCreationStates.waiting_for_location))
        self.dp.callback_query.register(self.age_handler, F.data.startswith("age_"), StateFilter(ProductCreationStates.waiting_for_age))
        self.dp.callback_query.register(self.size_handler, F.data.startswith("size_"), StateFilter(ProductCreationStates.waiting_for_size))
        self.dp.callback_query.register(self.location_style_handler, F.data.startswith("style_"), StateFilter(ProductCreationStates.waiting_for_location_style))
        self.dp.callback_query.register(self.pose_handler, F.data.startswith("pose_"), StateFilter(ProductCreationStates.waiting_for_pose))
        self.dp.callback_query.register(self.view_handler, F.data.startswith("view_"), StateFilter(ProductCreationStates.waiting_for_view))
        self.dp.callback_query.register(self.confirmation_handler, F.data.startswith("confirm_"), StateFilter(ProductCreationStates.waiting_for_confirmation))

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
        # Обновляем данные пользователя в БД
        self.db.get_user_balance(
            message.from_user.id,
            message.from_user.username,
            message.from_user.full_name
        )

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
        # Отправляем новое сообщение с меню, чтобы избежать ошибок с удаленным callback.message
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
        # Ссылка на телеграм-пользователя
        builder.button(text="📞 Написать менеджеру", url=f"tg://resolve?domain={SUPPORT_USERNAME.lstrip('@')}")
        builder.button(text="🔙 Назад", callback_data="back_to_main")
        builder.adjust(1)

        await callback.message.answer(balance_text, reply_markup=builder.as_markup(), parse_mode="Markdown")

    # --- Администраторские команды ---

    async def add_balance_handler(self, message: Message):
        """Обработчик команды /add_balance <user_id> <amount>"""
        if message.from_user.id != ADMIN_ID:
            return await message.answer("❌ У вас нет прав для выполнения этой команды.")

        try:
            _, target_id_str, amount_str = message.text.split()
            target_id = int(target_id_str)
            amount = int(amount_str)

            if amount <= 0:
                return await message.answer("❌ Сумма должна быть положительной.")

            # Добавление баланса
            self.db.add_balance(target_id, amount)
            new_balance = self.db.get_user_balance(target_id)

            await message.answer(
                f"✅ Баланс пользователя **{target_id}** пополнен на **{amount}** генераций. "
                f"Новый баланс: **{new_balance}**.",
                parse_mode="Markdown"
            )

            # Опционально: уведомить пользователя
            try:
                await self.bot.send_message(
                    target_id,
                    f"🎉 Ваш баланс пополнен на **{amount}** генераций! Текущий баланс: **{new_balance}**.",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.warning(f"Не удалось уведомить пользователя {target_id}: {e}")

        except ValueError:
            await message.answer("❌ Неверный формат. Используйте: `/add_balance <user_id> <amount>`")
        except Exception as e:
            await message.answer(f"❌ Произошла ошибка: {e}")

    async def stats_handler(self, message: Message):
        """Обработчик команды /stats"""
        if message.from_user.id != ADMIN_ID:
            return await message.answer("❌ У вас нет прав для выполнения этой команды.")

        try:
            total_users, total_generations, total_balance = self.db.get_all_users_stats()

            stats_text = (
                "📊 **Статистика Бота** 📊\n\n"
                f"👤 Всего пользователей: **{total_users}**\n"
                f"🖼 Всего генераций: **{total_generations}**\n"
                f"💰 Суммарный баланс: **{total_balance}** генераций"
            )

            await message.answer(stats_text, parse_mode="Markdown")

        except Exception as e:
            await message.answer(f"❌ Произошла ошибка при получении статистики: {e}")

    # --- Логика FSM для создания фото ---

    async def create_photo_handler(self, callback: CallbackQuery):
        """Обработчик начала создания фото"""
        user_id = callback.from_user.id
        current_balance = self.db.get_user_balance(user_id)

        if current_balance <= 0 and ADMIN_ID != user_id:
            # ... (логика проверки баланса) ...
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
        builder.button(text="🖼 Витринное фото (без модели)", callback_data="gender_display")
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

        # Отправка примеров
        if gender != GenderType.DISPLAY:
            try:
                media_group = MediaGroupBuilder()
                # Предполагаем, что файлы 'photo/example1.jpg' и 'photo/example2.jpg' существуют
                photo1 = FSInputFile("photo/example1.jpg")
                photo2 = FSInputFile("photo/example2.jpg")
                media_group.add_photo(media=photo1, caption="Пример 1: Фотография товара, преобразованная в студийный снимок на модели.")
                media_group.add_photo(media=photo2, caption="Пример 2: Фотография товара, преобразованная в уличный снимок на модели.")
                await callback.message.answer_media_group(media=media_group.build())
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
        photo_file_id = message.photo[-1].file_id

        temp_path = None
        try:
            file = await self.bot.get_file(photo_file_id)
            file_path = file.file_path

            # Создаем временный файл с уникальным именем
            temp_file_name = f"temp_{message.from_user.id}_{int(time.time())}.jpg"
            temp_path = os.path.join(tempfile.gettempdir(), temp_file_name)

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
            # Для витринного фото сразу переходим к подтверждению
            final_prompt = await self.generate_prompt(data)
            await state.update_data(prompt=final_prompt)

            summary = await self.generate_summary(data)
            summary_text = f"📋 Проверьте выбранные параметры:\n\n{summary}"

            builder = InlineKeyboardBuilder()
            builder.button(text="🚀 Начать генерацию (1 генерация)", callback_data="confirm_generate")
            builder.button(text="❌ Отменить", callback_data="back_to_main")
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
        if not (50 <= height <= 220):
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
            await self.go_to_location_style(callback, state)
        else:
            await state.set_state(ProductCreationStates.waiting_for_size)

            builder = InlineKeyboardBuilder()
            builder.button(text=SizeType.SIZE_42_46.value, callback_data="size_42_46")
            builder.button(text=SizeType.SIZE_50_54.value, callback_data="size_50_54")
            builder.button(text=SizeType.SIZE_58_64.value, callback_data="size_58_64")
            builder.button(text=SizeType.SIZE_64_68.value, callback_data="size_64_68")
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

        style = style_map[callback.data]
        await state.update_data(location_style=style)

        await state.set_state(ProductCreationStates.waiting_for_pose)
        builder = InlineKeyboardBuilder()
        builder.button(text=PoseType.SITTING.value, callback_data="pose_sitting")
        builder.button(text=PoseType.STANDING.value, callback_data="pose_standing")
        builder.adjust(2)

        await callback.message.answer("💃 Пожалуйста, выберите **позу модели**:", reply_markup=builder.as_markup(), parse_mode="Markdown")

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
        builder.button(text=ViewType.FRONT.value, callback_data="view_front")
        builder.button(text=ViewType.BACK.value, callback_data="view_back")
        builder.adjust(2)

        await callback.message.answer("👀 Пожалуйста, выберите **вид** (передняя/задняя часть):", reply_markup=builder.as_markup(), parse_mode="Markdown")

    async def view_handler(self, callback: CallbackQuery, state: FSMContext):
        """Обработчик выбора вида и переход к подтверждению"""
        await callback.message.delete()

        view_map = {
            "view_front": ViewType.FRONT,
            "view_back": ViewType.BACK
        }
        view = view_map[callback.data]
        await state.update_data(view=view)

        data = await state.get_data()
        final_prompt = await self.generate_prompt(data)
        await state.update_data(prompt=final_prompt)

        summary = await self.generate_summary(data)
        summary_text = f"📋 Проверьте выбранные параметры:\n\n{summary}"

        builder = InlineKeyboardBuilder()
        builder.button(text="🚀 Начать генерацию (1 генерация)", callback_data="confirm_generate")
        builder.button(text="❌ Отменить", callback_data="back_to_main")
        builder.adjust(1)

        await callback.message.answer(summary_text, reply_markup=builder.as_markup(), parse_mode="Markdown")
        await state.set_state(ProductCreationStates.waiting_for_confirmation)


    async def confirmation_handler(self, callback: CallbackQuery, state: FSMContext):
        """Обработчик подтверждения генерации"""
        await callback.message.edit_reply_markup(reply_markup=None) # Удаляем кнопки

        if callback.data == "confirm_edit":
            # Возвращаемся к выбору пола для начала редактирования
            await state.clear()
            return await self.create_photo_handler(callback)

        if callback.data != "confirm_generate":
            return

        user_id = callback.from_user.id
        data = await state.get_data()
        temp_photo_path = data.get('temp_photo_path')
        final_prompt = data.get('prompt')

        if not temp_photo_path or not final_prompt:
            await state.clear()
            return await callback.message.answer("❌ Ошибка: Недостаточно данных для генерации. Начните сначала.", reply_markup=InlineKeyboardBuilder().button(text="🔙 Назад", callback_data="back_to_main").as_markup())

        # Проверка баланса (дублируем на всякий случай, если пользователь долго не нажимал)
        if user_id != ADMIN_ID and not self.db.deduct_balance(user_id, cost=1):
            await state.clear()
            return await callback.message.answer("❌ Недостаточно генераций. Пожалуйста, пополните баланс.", reply_markup=InlineKeyboardBuilder().button(text="💳 Пополнить", callback_data="topup_balance").as_markup())


        sent_message = await callback.message.answer("⏳ **Генерация запущена...** Это может занять до 30 секунд.", parse_mode="Markdown")

        output_image_bytes = None
        try:
            # 1. Вызов API
            output_image_bytes = call_nano_banana_api(temp_photo_path, final_prompt)

            # 2. Проверка целостности изображения (PIL)
            image_stream = io.BytesIO(output_image_bytes)
            Image.open(image_stream)

            # 3. Добавление записи о генерации
            self.db.add_generation(user_id, final_prompt)

            # 4. Отправка результата
            result_photo = BufferedInputFile(output_image_bytes, filename="fashion_ai_result.png")

            success_caption = (
                f"✅ **Генерация завершена!**\n\n"
                f"Текущий баланс: **{self.db.get_user_balance(user_id)}** генераций."
            )

            builder = InlineKeyboardBuilder()
            builder.button(text="📸 Сделать еще фото", callback_data="create_photo")
            builder.button(text="🔙 Главное меню", callback_data="back_to_main")
            builder.adjust(1)

            await self.bot.send_photo(
                chat_id=callback.message.chat.id,
                photo=result_photo,
                caption=success_caption,
                reply_markup=builder.as_markup(),
                parse_mode="Markdown"
            )

        except Exception as e:
            # Если это не админ, возвращаем ему списанную генерацию
            if user_id != ADMIN_ID and output_image_bytes is None:
                self.db.add_balance(user_id, 1)
                await callback.message.answer("⚠️ Генерация не удалась, баланс возвращен.")

            error_message = f"❌ **Ошибка генерации**\n\nПроизошла ошибка при обращении к сервису: {e}"
            logger.error(f"Ошибка при генерации изображения: {e}")

            builder = InlineKeyboardBuilder()
            builder.button(text="🔙 Назад", callback_data="back_to_main")
            await callback.message.answer(error_message, reply_markup=builder.as_markup(), parse_mode="Markdown")

        finally:
            # Удаление временного файла и очистка FSM
            if temp_photo_path and os.path.exists(temp_photo_path):
                os.unlink(temp_photo_path)

            # Удаление сообщения "Генерация запущена..."
            await self.bot.delete_message(chat_id=sent_message.chat.id, message_id=sent_message.message_id)

            await state.clear()


# --- Запуск бота ---

async def main():
    bot_instance = FashionBot(token=BOT_TOKEN)
    logger.info("🤖 Бот запущен!")
    await bot_instance.dp.start_polling(bot_instance.bot)

if __name__ == "__main__":
    try:
        # Убедимся, что директория для tempfile существует
        if not os.path.exists(tempfile.gettempdir()):
            os.makedirs(tempfile.gettempdir())

        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен вручную.")