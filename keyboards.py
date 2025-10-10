"""
Клавиатуры и кнопки для бота
"""
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup

from config import SUPPORT_USERNAME
from models import AgeGroup, GenderType


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Главное меню"""
    builder = InlineKeyboardBuilder()
    builder.button(text="📸 Создать фото", callback_data="create_photo")
    builder.button(text="💳 Пополнить баланс", callback_data="topup_balance")
    builder.button(text="🆘 Написать в поддержку", callback_data="support")
    builder.adjust(1)
    return builder.as_markup()


def get_accept_terms_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для принятия условий"""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Принять и продолжить", callback_data="accept_terms")
    builder.button(text="💬 Написать в поддержку", callback_data="support")
    builder.adjust(1)
    return builder.as_markup()


def get_back_keyboard() -> InlineKeyboardMarkup:
    """Кнопка назад в главное меню"""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data="back_to_main")
    return builder.as_markup()


def get_gender_keyboard() -> InlineKeyboardMarkup:
    """Выбор категории продукта"""
    builder = InlineKeyboardBuilder()
    builder.button(text="👚 Женская одежда", callback_data="gender_women")
    builder.button(text="👔 Мужская одежда", callback_data="gender_men")
    builder.button(text="👶 Детская одежда", callback_data="gender_kids")
    builder.button(text="🖼️ Витринное фото", callback_data="gender_display")
    builder.button(text="⚪ На белом фоне", callback_data="gender_white_bg")
    builder.button(text="🔙 Назад", callback_data="back_to_main")
    builder.adjust(1)
    return builder.as_markup()


def get_location_keyboard() -> InlineKeyboardMarkup:
    """Выбор локации"""
    builder = InlineKeyboardBuilder()
    builder.button(text="🏙️ Улица", callback_data="location_street")
    builder.button(text="📸 Фотостудия", callback_data="location_studio")
    builder.button(text="📐 Фотозона на полу", callback_data="location_floor")
    builder.adjust(1)
    return builder.as_markup()

def get_length_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для ввода длины изделия с опцией пропуска"""
    builder = InlineKeyboardBuilder()
    builder.button(text="⏩ Пропустить", callback_data="length_skip")
    builder.adjust(1)
    return builder.as_markup()

def get_age_keyboard(gender: GenderType) -> InlineKeyboardMarkup:
    """Выбор возраста"""
    builder = InlineKeyboardBuilder()
    
    if gender == GenderType.KIDS:
        age_groups = AgeGroup.KIDS.value
    else:
        age_groups = AgeGroup.WOMEN_MEN.value
    
    for age in age_groups:
        builder.button(text=age, callback_data=f"age_{age}")
    
    builder.adjust(2)
    return builder.as_markup()


def get_size_keyboard() -> InlineKeyboardMarkup:
    """Выбор размера"""
    builder = InlineKeyboardBuilder()
    builder.button(text="42-46", callback_data="size_42_46")
    builder.button(text="50-54", callback_data="size_50_54")
    builder.button(text="58-64", callback_data="size_58_64")
    builder.button(text="64-68", callback_data="size_64_68")
    builder.adjust(2)
    return builder.as_markup()


def get_location_style_keyboard(location_type: str = None) -> InlineKeyboardMarkup:
    """Выбор стиля локации с фильтрацией по типу локации"""
    builder = InlineKeyboardBuilder()
    
    # Базовые стили, доступные для всех локаций
    basic_styles = {
        "style_regular": "🏢 Обычный"
    }
    
    # Стили для улицы
    street_styles = {
        "style_new_year": "🎄 Новогодняя",
        "style_summer": "☀️ Лето", 
        "style_nature": "🌳 Природа",
        "style_park_winter": "🏞️ Парк (зима)",
        "style_park_summer": "🌲 Парк (лето)",
        "style_car": "🚗 Рядом с машиной"
    }
    
    # Стили для фотостудии (только нейтральные)
    studio_styles = {
        "style_regular": "🏢 Обычный",
        "style_new_year": "🎄 Новогодняя",
        "style_summer": "☀️ Лето"
    }
    
    # Стили для фотозоны на полу
    floor_styles = {
        "style_regular": "🏢 Обычный",
        "style_new_year": "🎄 Новогодняя"
    }
    
    # Выбираем стили в зависимости от локации
    if location_type == "street" or location_type == "Улица":
        styles = {**basic_styles, **street_styles}
    elif location_type == "studio" or location_type == "Фотостудия":
        styles = studio_styles
    elif location_type == "floor" or location_type == "Фотозона на полу":
        styles = floor_styles
    else:
        styles = {**basic_styles, **street_styles}  # По умолчанию все стили
    
    for callback_data, text in styles.items():
        builder.button(text=text, callback_data=callback_data)
    
    builder.adjust(2)
    return builder.as_markup()


def get_pose_keyboard() -> InlineKeyboardMarkup:
    """Выбор позы"""
    builder = InlineKeyboardBuilder()
    builder.button(text="🪑 Сидя", callback_data="pose_sitting")
    builder.button(text="🧍 Стоя", callback_data="pose_standing")
    builder.adjust(2)
    return builder.as_markup()


def get_view_keyboard() -> InlineKeyboardMarkup:
    """Выбор ракурса"""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Сзади", callback_data="view_back")
    builder.button(text="👤 Передняя часть", callback_data="view_front")
    builder.adjust(2)
    return builder.as_markup()


def get_white_bg_view_keyboard() -> InlineKeyboardMarkup:
    """Выбор ракурса для белого фона"""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Сзади", callback_data="white_bg_view_back")
    builder.button(text="👤 Спереди", callback_data="white_bg_view_front")
    builder.adjust(2)
    return builder.as_markup()


def get_confirmation_keyboard() -> InlineKeyboardMarkup:
    """Подтверждение генерации"""
    builder = InlineKeyboardBuilder()
    builder.button(text="🚀 Начать генерацию", callback_data="confirm_generate")
    builder.button(text="✏️ Внести изменения", callback_data="confirm_edit")
    builder.adjust(1)
    return builder.as_markup()


def get_after_generation_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура после успешной генерации"""
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Внести изменения", callback_data="after_gen_edit")
    builder.button(text="✅ Завершить", callback_data="after_gen_finish")
    builder.adjust(1)
    return builder.as_markup()


def get_regenerate_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура после регенерации"""
    builder = InlineKeyboardBuilder()
    builder.button(text="📸 Создать новое фото", callback_data="create_photo")
    builder.button(text="🏠 В главное меню", callback_data="back_to_main")
    builder.adjust(1)
    return builder.as_markup()


def get_topup_balance_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для пополнения баланса"""
    builder = InlineKeyboardBuilder()
    builder.button(text="📞 Написать менеджеру", url=f"tg://resolve?domain={SUPPORT_USERNAME[1:]}")
    builder.button(text="🔙 Назад", callback_data="back_to_main")
    builder.adjust(1)
    return builder.as_markup()


def get_insufficient_balance_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура при недостатке баланса"""
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Пополнить баланс", callback_data="topup_balance")
    builder.button(text="🔙 Назад", callback_data="back_to_main")
    builder.adjust(1)
    return builder.as_markup()

