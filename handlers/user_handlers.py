"""
Основные обработчики для пользователей
"""
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from config import SUPPORT_USERNAME, GEMINI_DEMO_MODE
from database import Database
from keyboards import (
    get_accept_terms_keyboard,
    get_main_menu_keyboard,
    get_back_keyboard,
    get_gender_keyboard,
    get_topup_balance_keyboard,
    get_insufficient_balance_keyboard
)

router = Router()
db = Database()


@router.message(Command("start"))
async def start_handler(message: Message):
    """Обработчик команды /start"""
    welcome_text = (
        "👋 Добро пожаловать в Fashion AI Generator!\n\n"
        "Превращаем фотографии вашей одежды в профессиональные снимки на моделях.\n\n"
        "📋 Перед использованием ознакомьтесь с:\n"
        "1. Условиями использования\n"
        "2. Согласием на обработку данных"
    )

    await message.answer(welcome_text, reply_markup=get_accept_terms_keyboard())


@router.callback_query(F.data == "accept_terms")
async def accept_terms_handler(callback: CallbackQuery):
    """Обработчик принятия условий"""
    await callback.message.delete()
    await show_main_menu(callback.message)
    await callback.answer()


@router.callback_query(F.data == "back_to_main")
async def back_to_main_handler(callback: CallbackQuery):
    """Обработчик возврата в главное меню"""
    await callback.message.delete()
    await show_main_menu(callback.message)
    await callback.answer()


@router.callback_query(F.data == "support")
async def support_handler(callback: CallbackQuery):
    """Обработчик связи с поддержкой"""
    support_text = f"📞 Для связи с поддержкой напишите: {SUPPORT_USERNAME}"
    await callback.message.answer(support_text, reply_markup=get_back_keyboard())
    await callback.answer()


@router.callback_query(F.data == "topup_balance")
async def topup_balance_handler(callback: CallbackQuery):
    """Обработчик пополнения баланса"""
    user_id = callback.from_user.id
    current_balance = db.get_user_balance(user_id)

    balance_text = (
        f"💳 Пополнение баланса\n\n"
        f"Текущий баланс: {current_balance} генераций\n\n"
        "Для пополнения баланса напишите нашему менеджеру:\n"
        f"{SUPPORT_USERNAME}\n\n"
        f"Укажите ваш ID для зачисления: `{user_id}`"
    )

    await callback.message.answer(
        balance_text,
        reply_markup=get_topup_balance_keyboard(user_id),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data == "create_photo")
async def create_photo_handler(callback: CallbackQuery, state: FSMContext = None):
    """Обработчик начала создания фото"""
    # Очищаем предыдущее состояние если есть
    if state:
        import os
        data = await state.get_data()
        temp_photo_path = data.get('temp_photo_path')
        if temp_photo_path and os.path.exists(temp_photo_path):
            os.unlink(temp_photo_path)
        await state.clear()
    
    user_id = callback.from_user.id
    current_balance = db.get_user_balance(user_id)

    if current_balance <= 0 and not GEMINI_DEMO_MODE:
        await callback.message.answer(
            "❌ Недостаточно генераций. Пожалуйста, пополните баланс.",
            reply_markup=get_insufficient_balance_keyboard()
        )
        await callback.answer()
        return

    gender_text = "Выберите пожалуйста какой продукт вы хотите создать?"
    await callback.message.answer(gender_text, reply_markup=get_gender_keyboard())
    await callback.answer()


async def show_main_menu(message: Message):
    """Показать главное меню"""
    main_menu_text = "🎯 Главное меню:"
    await message.answer(main_menu_text, reply_markup=get_main_menu_keyboard())

