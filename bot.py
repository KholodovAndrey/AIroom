"""
Fashion AI Generator Bot - Главный модуль
"""
import asyncio

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN, logger
from handlers import admin_handlers, user_handlers, creation_handlers


async def main():
    """Основная функция запуска бота"""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не установлен. Завершение работы.")
        return

    # Инициализация бота и диспетчера
    bot = Bot(token=BOT_TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Регистрация роутеров
    dp.include_router(admin_handlers.router)
    dp.include_router(user_handlers.router)
    
    # Для creation_handlers нужно передать bot в контекст
    # Добавляем middleware для передачи bot в хэндлеры
    @dp.message.middleware()
    async def bot_middleware(handler, event, data):
        data['bot'] = bot
        return await handler(event, data)
    
    @dp.callback_query.middleware()
    async def bot_callback_middleware(handler, event, data):
        data['bot'] = bot
        return await handler(event, data)
    
    dp.include_router(creation_handlers.router)

    logger.info("🤖 Бот запущен!")
    
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Бот остановлен вручную.")
