"""
Модели данных: Enums и FSM состояния
"""
from enum import Enum
from aiogram.fsm.state import State, StatesGroup


class GenderType(Enum):
    """Типы категорий одежды"""
    WOMEN = "женская"
    MEN = "мужская"
    KIDS = "детская"
    FLAT_LAY = "фото на полу"
    WHITE_BG = "на белом фоне"


class LocationType(Enum):
    """Типы локаций для фотосессии"""
    STREET = "Улица"
    STUDIO = "Фотостудия"
    FLOOR_ZONE = "Фотозона на полу"


class AgeGroup(Enum):
    """Возрастные группы"""
    WOMEN_MEN = ["18-20", "22-28", "32-40", "42-55"]
    KIDS = ["0.3-1", "2-4", "7-10", "13-17"]


class SizeType(Enum):
    """Размеры одежды"""
    SIZE_42_46 = "42-46"
    SIZE_50_54 = "50-54"
    SIZE_58_64 = "58-64"
    SIZE_64_68 = "64-68"


class LocationStyle(Enum):
    """Стили локаций"""
    NEW_YEAR = "Новогодняя атмосфера"
    SUMMER = "Лето"
    NATURE = "Природа"
    PARK_WINTER = "Парк (зима)"
    PARK_SUMMER = "Парк (лето)"
    REGULAR = "обычный"
    CAR = "Рядом с машиной"


class PoseType(Enum):
    """Типы поз"""
    SITTING = "Сидя"
    STANDING = "Стоя"


class ViewType(Enum):
    """Типы ракурсов"""
    BACK = "Сзади"
    FRONT = "Передняя часть"


class ProductCreationStates(StatesGroup):
    """FSM состояния для создания продукта"""
    waiting_for_gender = State()
    waiting_for_photo = State()
    waiting_for_height = State()
    waiting_for_length = State()
    waiting_for_location = State()
    waiting_for_age = State()
    waiting_for_size = State()
    waiting_for_location_style = State()
    waiting_for_pose = State()
    waiting_for_view = State()
    waiting_for_white_bg_view = State()  # Для выбора ракурса на белом фоне
    waiting_for_confirmation = State()
    waiting_for_custom_prompt = State()  # Для ввода пользовательского промпта

