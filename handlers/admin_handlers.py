"""
Обработчики команд администратора
"""
from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command

from config import ADMIN_ID, logger
from database import Database

router = Router()
db = Database()


@router.message(Command("add_balance"))
async def add_balance_handler(message: Message):
    """Обработчик команды /add_balance (Только для ADMIN_ID)"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Эта команда доступна только администратору.")
        return

    try:
        parts = message.text.split()
        if len(parts) != 3:
            await message.answer(
                "⚠️ Использование: `/add_balance [user_id] [количество_генераций]`",
                parse_mode="Markdown"
            )
            return

        target_user_id = int(parts[1])
        amount = int(parts[2])

        if amount <= 0:
            await message.answer("❌ Количество генераций должно быть положительным числом.")
            return

        current_balance = db.get_user_balance(target_user_id)
        new_balance = current_balance + amount
        db.update_user_balance(target_user_id, new_balance)

        await message.answer(
            f"✅ Баланс пользователя `{target_user_id}` обновлен.\n"
            f"Добавлено: {amount} генераций.\n"
            f"Новый баланс: {new_balance} генераций.",
            parse_mode="Markdown"
        )

    except ValueError:
        await message.answer("❌ Неверный формат ID или количества. Используйте целые числа.")
    except Exception as e:
        logger.error(f"Ошибка в add_balance_handler: {e}")
        await message.answer(f"❌ Произошла ошибка при обновлении баланса: {e}")


@router.message(Command("stats"))
async def stats_handler(message: Message):
    """Обработчик команды /stats (Только для ADMIN_ID)"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Эта команда доступна только администратору.")
        return

    total_users, total_generations, total_balance = db.get_all_users_stats()

    stats_text = (
        "📊 **Статистика Бота**\n\n"
        f"👤 Всего пользователей: {total_users}\n"
        f"🎨 Всего генераций: {total_generations}\n"
        f"💰 Общий остаток баланса: {total_balance} генераций"
    )
    await message.answer(stats_text, parse_mode="Markdown")

