
import logging
import os
from io import BytesIO
from PIL import Image

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from dotenv import load_dotenv

from google import genai
from google.genai.errors import APIError
from google.genai.types import GenerateImagesResponse, Image as GenAIImage

# Загружаем переменные окружения
load_dotenv()

# --- Константы и Настройки ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Модели
GEMINI_ANALYSIS_MODEL = "gemini-2.5-flash"
IMAGE_GENERATION_MODEL = "imagen-3.0-generate-002"

# Включаем логирование
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)

# Инициализация Google AI клиента
try:
    if not GEMINI_API_KEY:
        raise ValueError("Ключ GEMINI_API_KEY не найден.")
    client = genai.Client(api_key=GEMINI_API_KEY)
except Exception as e:
    logging.error(f"🚫 Ошибка инициализации клиента Google AI: {e}")
    client = None

# --- FSM States (Состояния) ---
class GenerationStates(StatesGroup):
    """Определяем состояния для многошагового диалога."""
    waiting_for_photo = State()
    waiting_for_description = State()

# Инициализация Router для обработки сообщений
router = Router()

# --- Основная логика Google AI (Функции остаются асинхронными) ---

async def generate_enhanced_prompt(image_data: BytesIO, user_description: str) -> str:
    """Использует Gemini 2.5 Flash для создания детализированного промпта."""

    # Сбрасываем указатель и открываем PIL Image
    image_data.seek(0)
    image = Image.open(image_data)

    system_instruction = (
        "Ты — эксперт по промпт-инжинирингу. Проанализируй изображение и "
        "объедини его визуальные характеристики с текстовым описанием пользователя. "
        "Верни единый, высокодетализированный, английский промпт, подходящий для генерации Imagen."
    )

    prompt = (
        f"Используй это фото как сильный референс. Создай новый промпт, который "
        f"будет включать следующее требование: '{user_description}'."
    )

    response = client.models.generate_content(
        model=GEMINI_ANALYSIS_MODEL,
        contents=[image, prompt],
        config={"system_instruction": system_instruction}
    )

    return response.text.strip()

async def generate_image_with_imagen(final_prompt: str, input_image_bytes: bytes) -> bytes:
    """Вызывает Imagen для генерации изображения."""

    input_image = GenAIImage.from_bytes(data=input_image_bytes, mime_type='image/jpeg')

    response: GenerateImagesResponse = client.models.generate_images(
        model=IMAGE_GENERATION_MODEL,
        prompt=final_prompt,
        config={
            "number_of_images": 1,
            "output_mime_type": "image/jpeg",
            "aspect_ratio": "1:1",
            "image": input_image # Использование исходного фото как визуального контекста
        }
    )

    if not response.generated_images:
        raise Exception("Imagen не сгенерировал изображение.")

    return response.generated_images[0].image.image_bytes


# --- Обработчики Telegram (aiogram) ---

@router.message(Command("start"))
async def command_start_handler(message: Message, state: FSMContext) -> None:
    """Обработка команды /start."""
    if not client:
        await message.answer("❌ Ошибка: Клиент Google AI не инициализирован. Проверьте ваш API ключ.")
        await state.clear()
        return

    await message.answer(
        "👋 Привет! Я использую **Gemini 2.5 Flash** (анализ) и **Imagen** (генерация).\n"
        "Пожалуйста, **отправь мне фото** (референс)."
    )
    # Переводим пользователя в состояние ожидания фото
    await state.set_state(GenerationStates.waiting_for_photo)


@router.message(GenerationStates.waiting_for_photo, F.photo)
async def process_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    """Получает фото и просит описание."""

    # Скачиваем файл в оперативную память
    file_info = await bot.get_file(message.photo[-1].file_id)
    photo_bytes_io = BytesIO()
    await bot.download_file(file_info.file_path, photo_bytes_io)

    # Сохраняем байты в FSMContext
    await state.update_data(original_image_bytes=photo_bytes_io.getvalue())

    await message.answer(
        "✅ Фото получено! Теперь **напиши текстовое описание** (промпт) для генерации вариации."
    )
    # Переводим пользователя в состояние ожидания описания
    await state.set_state(GenerationStates.waiting_for_description)

@router.message(GenerationStates.waiting_for_photo, F.text)
async def process_photo_invalid(message: Message) -> None:
    """Обработка неверного ввода в состоянии ожидания фото."""
    await message.answer("Пожалуйста, отправь именно фото.")


@router.message(GenerationStates.waiting_for_description, F.text)
async def process_description(message: Message, state: FSMContext) -> None:
    """Получает описание, выполняет генерацию и отправляет результат."""

    user_description = message.text
    data = await state.get_data()
    original_image_bytes = data.get("original_image_bytes")

    if not original_image_bytes:
        await message.answer("Ошибка: не удалось найти загруженное фото. Начните снова: /start")
        await state.clear()
        return

    await message.answer("⏳ Отлично, описание принято. **Gemini 2.5 Flash** анализирует фото...")

    try:
        # 1. Анализ и создание промпта (Gemini 2.5 Flash)
        photo_bytes_io = BytesIO(original_image_bytes)
        enhanced_prompt = await generate_enhanced_prompt(photo_bytes_io, user_description)

        await message.answer("📝 Промпт создан! Начинаю генерацию с **Imagen**...")

        # 2. Генерация изображения (Imagen)
        generated_image_bytes = await generate_image_with_imagen(enhanced_prompt, original_image_bytes)

        # 3. Отправляем изображение в Telegram
        await message.answer_photo(
            photo=generated_image_bytes,
            caption=(
                f"✨ **Результат генерации:**\n\n"
                f"📝 Исходное требование: *{user_description}*\n"
                f"⚙️ Модель: `{IMAGE_GENERATION_MODEL}`"
            ),
            parse_mode='Markdown'
        )

    except APIError as e:
        await message.answer(f"❌ Ошибка API Google AI: {e}. Проверьте ключ и лимиты.")
        logging.error(f"API Error: {e}")
    except Exception as e:
        await message.answer(f"❌ Произошла непредвиденная ошибка: {e}")
        logging.error(f"General Error: {e}")
    finally:
        # Завершаем диалог
        await state.clear()

@router.message(Command("cancel"))
async def command_cancel_handler(message: Message, state: FSMContext) -> None:
    """Обработка команды /cancel."""
    await state.clear()
    await message.answer("⛔️ Диалог отменен. Начать снова: /start")

# --- Главная функция запуска ---

async def main() -> None:
    """Инициализация и запуск диспетчера aiogram."""
    if not TELEGRAM_BOT_TOKEN:
        logging.error("🚫 Токен Telegram бота не найден.")
        return

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    logging.info("🚀 Бот запущен на aiogram! Готов к работе с Gemini и Imagen.")

    # Запуск бота: dp.start_polling() блокирует выполнение и обрабатывает входящие апдейты
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    # Запускаем асинхронную функцию main
    asyncio.run(main())