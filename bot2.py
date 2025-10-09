
import logging
import os
from io import BytesIO
from PIL import Image

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
from dotenv import load_dotenv
from google import genai
from google.genai.errors import APIError
from google.genai.types import GenerateImagesResponse, Image as GenAIImage

# Загружаем переменные окружения
load_dotenv()

# --- Константы и Настройки ---
TELEGRAM_BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Модели
GEMINI_ANALYSIS_MODEL = "gemini-2.5-flash"  # Для анализа и создания промпта (ваша "flash image")
IMAGE_GENERATION_MODEL = "imagen-3.0-generate-002" # Для реальной генерации изображения

# Состояния для ConversationHandler
PHOTO, DESCRIPTION = range(2)

# Включаем логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Инициализация Gemini/Imagen клиента
try:
    if not GEMINI_API_KEY:
        raise ValueError("Ключ GEMINI_API_KEY не найден.")
    client = genai.Client(api_key=GEMINI_API_KEY)
except Exception as e:
    logger.error(f"🚫 Ошибка инициализации клиента Google AI: {e}")
    client = None

# --- Шаг 1: Анализ фото и создание промпта с Gemini 2.5 Flash ---

async def generate_enhanced_prompt(image_data: BytesIO, user_description: str) -> str:
    """
    Использует Gemini 2.5 Flash для анализа изображения и создания детализированного
    английского промпта для модели Imagen.
    """
    image_data.seek(0)
    image = Image.open(image_data)

    system_instruction = (
        "Ты — эксперт по промпт-инжинирингу. Твоя задача — проанализировать "
        "предоставленное изображение и объединить его визуальные характеристики (стиль, "
        "композицию, освещение) с текстовым описанием пользователя. Верни единый, "
        "высокодетализированный, английский промпт, подходящий для фотореалистичной генерации Imagen."
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

# --- Шаг 2: Генерация изображения с Imagen ---

async def generate_image_with_imagen(final_prompt: str, input_image_bytes: bytes) -> bytes:
    """
    Отправляет финальный промпт и исходное изображение в Imagen для генерации.
    Возвращает байты сгенерированного изображения.
    """

    # Загружаем исходное изображение как GenAIImage для референса
    input_image = GenAIImage.from_bytes(data=input_image_bytes, mime_type='image/jpeg')

    response: GenerateImagesResponse = client.models.generate_images(
        model=IMAGE_GENERATION_MODEL,
        prompt=final_prompt,
        config={
            "number_of_images": 1,
            "output_mime_type": "image/jpeg",
            "aspect_ratio": "1:1",
            "image": input_image # Использование исходного фото как визуального контекста/референса
        }
    )

    if not response.generated_images:
        raise Exception("Imagen не сгенерировал изображение.")

    return response.generated_images[0].image.image_bytes

# --- Обработчики Telegram ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начинает диалог и просит отправить фото."""
    if not client:
        await update.message.reply_text("❌ Ошибка: Клиент Google AI не инициализирован. Проверьте ваш API ключ.")
        return ConversationHandler.END

    await update.message.reply_text(
        "👋 Привет! Я использую **Gemini 2.5 Flash** (анализ) и **Imagen** (генерация).\n"
        "Пожалуйста, **отправь мне фото** (референс)."
    )
    context.user_data.clear()
    return PHOTO

async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получает фото и просит описание."""
    if not update.message.photo:
        await update.message.reply_text("Пожалуйста, отправь именно фото.")
        return PHOTO

    photo_file = update.message.photo[-1]
    context.user_data['photo_file_id'] = photo_file.file_id

    await update.message.reply_text(
        "✅ Фото получено! Теперь **напиши текстовое описание** (промпт) для генерации вариации."
    )
    return DESCRIPTION

async def receive_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Завершает диалог: Анализ -> Генерация -> Отправка результата."""
    user_description = update.message.text
    await update.message.reply_text("⏳ Получаю фото. **Gemini 2.5 Flash** анализирует его и создает промпт...")

    # 1. Получаем файл фото
    photo_file_id = context.user_data['photo_file_id']
    new_file = await context.bot.get_file(photo_file_id)

    photo_bytes_io = BytesIO()
    await new_file.download_to_memory(photo_bytes_io)

    # Сохраняем байты для Imagen
    original_image_bytes = photo_bytes_io.getvalue()

    try:
        # 2. Анализ и создание промпта с помощью Gemini
        enhanced_prompt = await generate_enhanced_prompt(photo_bytes_io, user_description)
        logger.info(f"Сгенерированный промпт: {enhanced_prompt}")
        await update.message.reply_text(f"📝 Промпт создан! Начинаю генерацию с **Imagen**...")

        # 3. Генерация изображения с помощью Imagen
        generated_image_bytes = await generate_image_with_imagen(enhanced_prompt, original_image_bytes)

        # 4. Отправляем изображение в Telegram
        await update.message.reply_photo(
            photo=generated_image_bytes,
            caption=(
                f"✨ **Результат генерации:**\n\n"
                f"📝 Исходное требование: *{user_description}*\n"
                f"⚙️ Модель: `{IMAGE_GENERATION_MODEL}`"
            ),
            parse_mode='Markdown'
        )

    except APIError as e:
        await update.message.reply_text(f"❌ Ошибка API Google AI: {e}. Проверьте ключ и лимиты.")
        logger.error(f"API Error: {e}")
    except Exception as e:
        await update.message.reply_text(f"❌ Произошла непредвиденная ошибка: {e}")
        logger.error(f"General Error: {e}")
    finally:
        context.user_data.clear()
        return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Завершает диалог по команде /cancel."""
    context.user_data.clear()
    await update.message.reply_text('⛔️ Диалог отменен. Начать снова: /start')
    return ConversationHandler.END


# --- Главная функция ---

def main():
    """Запускает бота."""
    if not TELEGRAM_BOT_TOKEN or not GEMINI_API_KEY:
        logger.error("🚫 Токен Telegram бота или ключ Gemini не найдены в .env.")
        return

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            PHOTO: [MessageHandler(filters.PHOTO & ~filters.COMMAND, receive_photo)],
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_description)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(conv_handler)

    logger.info("🚀 Бот запущен! Готов к работе с Gemini и Imagen.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()