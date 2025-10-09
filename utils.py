"""
Вспомогательные утилиты для бота
"""
import asyncio
from aiogram.types import Message


async def show_progress_bar(message: Message, duration: int = 15):
    """
    Показывает анимированный прогресс-бар во время генерации
    
    Args:
        message: Сообщение для редактирования
        duration: Ожидаемая длительность в секундах
    """
    progress_symbols = ["▱", "▰"]
    steps = 20  # Количество шагов прогресса
    
    for i in range(steps + 1):
        progress = int((i / steps) * 100)
        filled = int((i / steps) * 10)
        bar = "▰" * filled + "▱" * (10 - filled)
        
        # Меняем текст в зависимости от прогресса
        if progress < 30:
            status_text = "🎨 Анализ изображения..."
        elif progress < 60:
            status_text = "🖼️ Генерация изображения..."
        elif progress < 90:
            status_text = "✨ Финальная обработка..."
        else:
            status_text = "🎉 Почти готово..."
        
        try:
            await message.edit_text(
                f"{status_text}\n\n"
                f"[{bar}] {progress}%\n\n"
                f"⏱️ Пожалуйста, подождите..."
            )
        except Exception:
            # Игнорируем ошибки редактирования (например, если сообщение не изменилось)
            pass
        
        # Задержка между обновлениями
        await asyncio.sleep(duration / steps)


async def show_simple_progress(message: Message, total_steps: int = 10):
    """
    Показывает простой прогресс с точками
    
    Args:
        message: Сообщение для редактирования
        total_steps: Количество шагов
    """
    dots = ["⚪", "⚫"]
    
    for step in range(total_steps):
        filled_dots = "⚫" * (step + 1)
        empty_dots = "⚪" * (total_steps - step - 1)
        
        try:
            await message.edit_text(
                f"🎨 Генерация изображения...\n\n"
                f"{filled_dots}{empty_dots}\n\n"
                f"⏱️ Ожидайте, это займет 10-20 секунд"
            )
        except Exception:
            pass
        
        await asyncio.sleep(1.5)

