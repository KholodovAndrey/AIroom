"""
Обработчики создания фотографий
"""
import os
import asyncio
import tempfile
from typing import Dict, Any
from io import BytesIO

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile, BufferedInputFile
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.utils.media_group import MediaGroupBuilder
from PIL import Image

from config import SUPPORT_USERNAME, GEMINI_DEMO_MODE, logger
from database import Database
from models import (
    GenderType,
    LocationType,
    SizeType,
    LocationStyle,
    PoseType,
    ViewType,
    ProductCreationStates
)
from keyboards import (
    get_location_keyboard,
    get_age_keyboard,
    get_size_keyboard,
    get_location_style_keyboard,
    get_pose_keyboard,
    get_view_keyboard,
    get_confirmation_keyboard,
    get_back_keyboard,
    get_white_bg_view_keyboard,
    get_after_generation_keyboard,
    get_regenerate_keyboard,
    get_length_keyboard
)
from gemini_api import call_gemini_api
from utils import show_progress_bar

router = Router()
db = Database()


async def generate_prompt(data: Dict[str, Any]) -> str:
    """
    Генерирует подробный промпт для Gemini API на основе выбранных параметров.
    """
    gender = data.get('gender', GenderType.DISPLAY)
    
    # Добавляем описание телосложения в зависимости от размера
    size = data.get('size')
    body_type_description = ""
    if size:
        size_value = size.value if hasattr(size, 'value') else str(size)
        if "42-46" in size_value:
            body_type_description = "Стройная фигура, худощавое телосложение."
        elif "50-54" in size_value:
            body_type_description = "Полная, но не сильно полная фигура, среднее телосложение, не худое и не очень толстое."
        elif "58-64" in size_value:
            body_type_description = "Полная фигура, крупное телосложение, крупные ноги и руки."
        elif "64-68" in size_value:
            body_type_description = "Очень полная фигура, гигантские размеры, очень толстое телосложение."

    if gender == GenderType.DISPLAY:
        base_prompt = (
            "Create a professional, high-quality, product-focused photo suitable for "
            "an online store's storefront/display (витринное фото). "
            "The clothing must be perfectly ironed, without any wrinkles or creases. "
            "Seamlessly replace the background of the input product image with a "
            "stylized, minimalist, and aesthetically pleasing background, while keeping the product clear and well-lit. "
            "Ensure the final image is visually appealing and studio-quality. "
            "Image aspect ratio: 4:3. "
            "If the clothing in the photo is wrinkled or has folds, they should not be visible in the final image. "
            "Avoid excessive retouching, keep natural appearance. "
            "European appearance for any human elements."
        )
        return base_prompt
    
    if gender == GenderType.WHITE_BG:
        view = data.get('white_bg_view', 'front')
        view_text = "back view" if view == "back" else "front view"
        
        white_bg_prompt = (
            f"Create a professional, high-quality product photograph on a pure white background. "
            f"Show the clothing item from {view_text} as a 3D product visualization. "
            f"The clothing must be perfectly ironed, without any wrinkles or creases. "
            f"The product should look like a 3D rendered object - clean, crisp, and professional. "
            f"The product should be the main focus, well-lit with soft shadows, "
            f"presented in a clean, commercial style suitable for an online store. "
            f"The background must be completely white (#FFFFFF). "
            f"Ensure the product looks professional and appealing, as if it's a 3D product visualization. "
            f"Image aspect ratio: 4:3. "
            f"If the clothing in the original photo is wrinkled or has folds, they must be completely removed in the final image. "
            f"Avoid excessive retouching, maintain natural fabric texture. "
            f"European appearance for any human elements."
        )
        return white_bg_prompt

    gender_text = gender.value
    height = data.get('height', '170')
    length = data.get('length', '70') 
    location = data.get('location', LocationType.STUDIO).value
    age = data.get('age', '25-35')
    size = data.get('size', SizeType.SIZE_42_46).value if gender != GenderType.KIDS else ""
    location_style = data.get('location_style', LocationStyle.REGULAR).value
    pose = data.get('pose', PoseType.STANDING).value
    view = data.get('view', ViewType.FRONT).value

    model_details = f"a professional, natural-looking model with European appearance, {gender_text} clothing, height {height} cm, age range {age}"
    if size:
        model_details += f", wearing size {size}"
    if body_type_description:
        model_details += f", {body_type_description}"

    scene_details = f"in a {location} setting, with a {location_style} atmosphere. Pose: {pose}, View: {view}."

    prompt = (
        f"Generate a hyper-realistic, high-definition (4k), professional fashion photograph with 4:3 aspect ratio. "
        f"The image must feature **{model_details}**. "
        f"The clothing on the model must be perfectly ironed, smooth, without any wrinkles, creases or folds. "
        f"If the clothing in the original photo is wrinkled or has folds, they must be completely removed in the final image. "
        f"The model should be perfectly integrated with the clothing from the input image. "
        f"Scene: **{scene_details}**. "
        f"The model should be well-lit, and the final image should look like it was taken by a top fashion photographer. "
        f"Focus on natural-looking hands and realistic facial features (if visible). "
        f"Avoid excessive retouching - keep natural skin texture and appearance. "
        f"European facial features and appearance. "
        f"Image aspect ratio: 4:3. "
        f"Exclude any watermarks or text overlays."
    )

    return prompt


async def generate_summary(data: Dict[str, Any]) -> str:
    """
    Генерирует текстовую сводку выбранных параметров для подтверждения.
    """
    summary_parts = []

    gender = data.get('gender', GenderType.DISPLAY)
    summary_parts.append(f"📦 **Категория**: {gender.value.capitalize()}")

    if gender == GenderType.WHITE_BG:
        view = data.get('white_bg_view', 'front')
        view_text = "Сзади" if view == "back" else "Спереди"
        summary_parts.append(f"👀 **Ракурс**: {view_text}")
    elif gender != GenderType.DISPLAY:
        summary_parts.append(f"📏 **Рост модели**: {data.get('height', 'Не указан')} см")
        summary_parts.append(f"📐 **Длина изделия**: {data.get('length', 'Не указана')} см")
        summary_parts.append(f"📍 **Локация**: {data.get('location', LocationType.STUDIO).value}")
        summary_parts.append(f"🎂 **Возраст модели**: {data.get('age', 'Не указан')}")

        if gender != GenderType.KIDS:
            summary_parts.append(f"📐 **Размер**: {data.get('size', SizeType.SIZE_42_46).value}")

        summary_parts.append(f"🎨 **Стиль локации**: {data.get('location_style', LocationStyle.REGULAR).value}")
        summary_parts.append(f"🧘 **Положение тела**: {data.get('pose', PoseType.STANDING).value}")
        summary_parts.append(f"👀 **Вид**: {data.get('view', ViewType.FRONT).value}")

    return "\n".join(summary_parts)


@router.callback_query(F.data.startswith("gender_"))
async def gender_select_handler(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора пола/категории"""
    # Проверяем, есть ли у пользователя бесплатная генерация
    user_id = callback.from_user.id
    user_generations = db.get_user_generations_count(user_id)
    
    # Если это первая генерация пользователя, даем бесплатную
    if user_generations == 0:
        current_balance = db.get_user_balance(user_id)
        if current_balance == 0:  # Даем бесплатную генерацию только если баланс 0
            db.update_user_balance(user_id, 1)
            await callback.message.answer(
                "🎉 **Вам предоставлена 1 бесплатная генерация!**\n\n"
                "Вы можете создать свое первое фото бесплатно. "
                "Для последующих генераций потребуется пополнение баланса."
            )

    gender_map = {
        "gender_women": GenderType.WOMEN,
        "gender_men": GenderType.MEN,
        "gender_kids": GenderType.KIDS,
        "gender_display": GenderType.DISPLAY,
        "gender_white_bg": GenderType.WHITE_BG
    }

    gender = gender_map[callback.data]
    await state.update_data(gender=gender)

    # Попытка загрузить примеры фото (опционально)
    if gender not in [GenderType.DISPLAY, GenderType.WHITE_BG]:
        import os
        if os.path.exists("photo/example1.jpg") and os.path.exists("photo/example2.jpg"):
            try:
                media_group = MediaGroupBuilder()
                photo1 = FSInputFile("photo/example1.jpg")
                photo2 = FSInputFile("photo/example2.jpg")
                media_group.add_photo(media=photo1)
                media_group.add_photo(media=photo2)
                await callback.message.answer_media_group(media=media_group.build())
            except Exception as e:
                logger.warning(f"Не удалось загрузить примеры фото: {e}")
        else:
            logger.info("Примеры фото не найдены, пропускаем")

    if gender == GenderType.DISPLAY:
        instruction_text = (
            "📸 Пожалуйста пришлите фотографию вашего товара для создания витринного фото.\n\n"
            "⚠️ Обратите внимание: фотография вашего товара должна быть четко видна "
            "без лишних бликов и размытостей.\n\n"
            f"Если остались вопросы - пишите в поддержку {SUPPORT_USERNAME}"
        )
    elif gender == GenderType.WHITE_BG:
        instruction_text = (
            "📸 Пожалуйста пришлите фотографию вашего товара для фото на белом фоне.\n\n"
            "⚠️ Обратите внимание: фотография должна быть четкой "
            "без лишних бликов и размытостей.\n\n"
            f"Если остались вопросы - пишите в поддержку {SUPPORT_USERNAME}"
        )
    else:
        instruction_text = (
            "📸 Пожалуйста пришлите фотографию вашего товара.\n\n"
            "⚠️ Обратите внимание: фотография вашего товара должна быть четко видна "
            "без лишних бликов и размытостей.\n\n"
            f"Если остались вопросы - пишите в поддержку {SUPPORT_USERNAME}"
        )

    await callback.message.answer(instruction_text)
    await state.set_state(ProductCreationStates.waiting_for_photo)
    await callback.answer()


@router.message(StateFilter(ProductCreationStates.waiting_for_photo))
async def photo_handler(message: Message, state: FSMContext, bot):
    """Обработчик загрузки фото"""
    if not message.photo:
        await message.answer("📸 Пожалуйста, отправьте фотографию товара.")
        return

    photo_file_id = message.photo[-1].file_id
    await state.update_data(photo_file_id=photo_file_id)

    temp_path = None
    try:
        file = await bot.get_file(photo_file_id)
        file_path = file.file_path

        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
        temp_path = temp_file.name
        temp_file.close()

        await bot.download_file(file_path, temp_path)
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
        prompt = await generate_prompt(data)
        await state.update_data(prompt=prompt)

        user_id = message.from_user.id
        db.add_generation(user_id, prompt)

        summary = await generate_summary(data)
        summary_text = f"📋 Проверьте выбранные параметры:\n\n{summary}"

        await message.answer(summary_text, reply_markup=get_confirmation_keyboard())
        await state.set_state(ProductCreationStates.waiting_for_confirmation)
    elif gender == GenderType.WHITE_BG:
        # Для белого фона - сразу выбор ракурса
        await state.set_state(ProductCreationStates.waiting_for_white_bg_view)
        await message.answer(
            "👀 Выберите ракурс для фото на белом фоне:",
            reply_markup=get_white_bg_view_keyboard()
        )
    else:
        await state.set_state(ProductCreationStates.waiting_for_height)
        await message.answer("📏 Напишите рост модели (в см):")


@router.message(StateFilter(ProductCreationStates.waiting_for_height))
async def height_handler(message: Message, state: FSMContext):
    """Обработчик ввода роста"""
    height = message.text
    if not height.isdigit():
        await message.answer("❌ Пожалуйста, введите числовое значение роста в см:")
        return

    await state.update_data(height=height)
    await state.set_state(ProductCreationStates.waiting_for_length)
    
    await message.answer(
        "📏 Теперь введите длину изделия в см (или пропустите):",
        reply_markup=get_length_keyboard()
    )


@router.message(StateFilter(ProductCreationStates.waiting_for_length))
async def length_handler(message: Message, state: FSMContext):
    """Обработчик ввода длины изделия"""
    length = message.text
    if not length.isdigit():
        await message.answer("❌ Пожалуйста, введите числовое значение длины в см:")
        return

    await state.update_data(length=length)
    await state.set_state(ProductCreationStates.waiting_for_location)

    await message.answer("📍 Пожалуйста выберите локацию:", reply_markup=get_location_keyboard())

@router.callback_query(F.data == "length_skip")
async def length_skip_handler(callback: CallbackQuery, state: FSMContext):
    """Обработчик пропуска ввода длины"""
    await state.update_data(length="70")  # Значение по умолчанию
    await state.set_state(ProductCreationStates.waiting_for_location)

    await callback.message.answer("📍 Пожалуйста выберите локацию:", reply_markup=get_location_keyboard())
    await callback.answer()

@router.callback_query(F.data.startswith("location_"))
async def location_handler(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора локации"""
    location_map = {
        "location_street": LocationType.STREET,
        "location_studio": LocationType.STUDIO, 
        "location_floor": LocationType.FLOOR_ZONE
    }

    location = location_map[callback.data]
    await state.update_data(location=location)

    data = await state.get_data()
    gender = data['gender']

    await callback.message.answer(
        "🎂 Пожалуйста выберите возраст модели:",
        reply_markup=get_age_keyboard(gender)
    )
    await state.set_state(ProductCreationStates.waiting_for_age)
    await callback.answer()


@router.callback_query(F.data.startswith("age_"))
async def age_handler(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора возраста"""
    age = callback.data.replace("age_", "")
    await state.update_data(age=age)

    data = await state.get_data()
    gender = data['gender']
    location = data.get('location')  # Получаем выбранную локацию

    if gender == GenderType.KIDS:
        await state.set_state(ProductCreationStates.waiting_for_location_style)
        await callback.message.answer(
            "🎨 Пожалуйста, выберите стиль локации:",
            reply_markup=get_location_style_keyboard(location)  # Передаем локацию
        )
    else:
        await state.set_state(ProductCreationStates.waiting_for_size)
        await callback.message.answer(
            "📏 Пожалуйста выберите размер одежды:",
            reply_markup=get_size_keyboard()
        )
    
    await callback.answer()


@router.callback_query(F.data.startswith("size_"))
async def size_handler(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора размера"""
    size_map = {
        "size_42_46": SizeType.SIZE_42_46,
        "size_50_54": SizeType.SIZE_50_54,
        "size_58_64": SizeType.SIZE_58_64,
        "size_64_68": SizeType.SIZE_64_68
    }

    size = size_map[callback.data]
    await state.update_data(size=size)
    await state.set_state(ProductCreationStates.waiting_for_location_style)

    # Получаем выбранную локацию из состояния
    data = await state.get_data()
    location = data.get('location')

    await callback.message.answer(
        "🎨 Пожалуйста, выберите стиль локации:",
        reply_markup=get_location_style_keyboard(location)  # Передаем локацию
    )
    await callback.answer()


@router.callback_query(F.data.startswith("style_"))
async def location_style_handler(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора стиля локации"""
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

    await callback.message.answer(
        "🧘 Пожалуйста выберите положение тела:",
        reply_markup=get_pose_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pose_"))
async def pose_handler(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора позы"""
    pose_map = {
        "pose_sitting": PoseType.SITTING,
        "pose_standing": PoseType.STANDING
    }

    pose = pose_map[callback.data]
    await state.update_data(pose=pose)
    await state.set_state(ProductCreationStates.waiting_for_view)

    await callback.message.answer("👀 Пожалуйста выберите вид:", reply_markup=get_view_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("view_"))
async def view_handler(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора вида"""
    view_map = {
        "view_back": ViewType.BACK,
        "view_front": ViewType.FRONT
    }

    view = view_map[callback.data]
    await state.update_data(view=view)

    data = await state.get_data()
    summary = await generate_summary(data)
    prompt = await generate_prompt(data)

    await state.update_data(prompt=prompt)

    user_id = callback.from_user.id
    db.add_generation(user_id, prompt)

    summary_text = f"📋 Проверьте выбранные параметры:\n\n{summary}"

    await callback.message.answer(summary_text, reply_markup=get_confirmation_keyboard())
    await state.set_state(ProductCreationStates.waiting_for_confirmation)
    await callback.answer()


@router.callback_query(F.data.startswith("white_bg_view_"))
async def white_bg_view_handler(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора ракурса для белого фона"""
    view_map = {
        "white_bg_view_back": "back",
        "white_bg_view_front": "front"
    }
    
    view = view_map[callback.data]
    await state.update_data(white_bg_view=view)
    
    data = await state.get_data()
    prompt = await generate_prompt(data)
    await state.update_data(prompt=prompt)
    
    user_id = callback.from_user.id
    db.add_generation(user_id, prompt)
    
    summary = await generate_summary(data)
    summary_text = f"📋 Проверьте выбранные параметры:\n\n{summary}"
    
    await callback.message.answer(summary_text, reply_markup=get_confirmation_keyboard())
    await state.set_state(ProductCreationStates.waiting_for_confirmation)
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_"))
async def confirmation_handler(callback: CallbackQuery, state: FSMContext):
    """Обработчик подтверждения генерации"""
    from handlers.user_handlers import create_photo_handler
    
    user_id = callback.from_user.id
    current_balance = db.get_user_balance(user_id)

    if callback.data == "confirm_generate":
        if current_balance <= 0 and not GEMINI_DEMO_MODE:
            await callback.message.answer("❌ Недостаточно генераций. Пополните баланс.")
            await state.clear()
            await callback.answer()
            return

        if not GEMINI_DEMO_MODE:
            new_balance = current_balance - 1
            db.update_user_balance(user_id, new_balance)
        else:
            new_balance = current_balance

        data = await state.get_data()
        prompt = data.get('prompt', '')
        temp_photo_path = data.get('temp_photo_path')
        
        # Сохраняем оригинальный промпт для возможности изменений
        if 'original_prompt' not in data:
            await state.update_data(original_prompt=prompt)
        
        # Проверка наличия временного файла
        if not temp_photo_path:
            await callback.message.answer(
                "❌ Ошибка: фото товара не найдено. Пожалуйста, начните заново.",
                reply_markup=get_back_keyboard()
            )
            await state.clear()
            await callback.answer()
            return

        generating_msg = await callback.message.answer(
            f"🎨 Генерация началась...\n\n"
            f"[▱▱▱▱▱▱▱▱▱▱] 0%\n\n"
            f"⏱️ Пожалуйста, подождите..."
        )

        try:
            # Запускаем прогресс-бар и генерацию параллельно
            progress_task = asyncio.create_task(show_progress_bar(generating_msg, duration=15))
            
            # Генерация изображения через Gemini API
            processed_image_bytes = call_gemini_api(temp_photo_path, prompt)
            
            # Отменяем прогресс-бар после завершения генерации
            progress_task.cancel()
            try:
                await progress_task
            except asyncio.CancelledError:
                pass

            # Пересохранение через PIL для гарантии совместимости с Telegram
            image_stream = BytesIO(processed_image_bytes)
            img = Image.open(image_stream)

            output_stream = BytesIO()
            img.save(output_stream, format='JPEG', quality=90)
            final_image_bytes = output_stream.getvalue()

            # Отправка сгенерированного изображения
            generated_image = BufferedInputFile(final_image_bytes, filename="generated_fashion.jpg")

            await callback.message.answer_photo(
                generated_image,
                caption="✨ Генерация завершена успешно!",
                reply_markup=get_after_generation_keyboard()
            )

            await generating_msg.delete()

        except Exception as e:
            logger.error(f"Ошибка при генерации изображения: {e}")
            await generating_msg.delete()

            error_msg = str(e)
            if "location is not supported" in error_msg.lower() and not GEMINI_DEMO_MODE:
                await callback.message.answer(
                    "❌ Сервис генерации изображений недоступен в вашем регионе.\n\n"
                    "Ваш баланс был возвращен."
                )
                db.update_user_balance(user_id, current_balance)
            else:
                await callback.message.answer(
                    f"❌ Произошла ошибка при генерации изображения:\n\n"
                    f"{error_msg[:200]}\n\n"
                    f"Попробуйте изменить параметры или обратитесь в поддержку.",
                    parse_mode=None
                )
                if not GEMINI_DEMO_MODE:
                    db.update_user_balance(user_id, current_balance)

        finally:
            # НЕ очищаем состояние и НЕ удаляем файл - они нужны для возможности изменений
            # Состояние и файл будут очищены при выборе "Завершить" или при создании нового фото
            pass

    elif callback.data == "confirm_edit":
        await state.clear()
        await create_photo_handler(callback)
    
    await callback.answer()


@router.callback_query(F.data == "after_gen_edit")
async def after_generation_edit_handler(callback: CallbackQuery, state: FSMContext):
    """Обработчик внесения изменений после генерации"""
    await callback.message.answer(
        "✏️ Опишите желаемые изменения:\n\n"
        "Например:\n"
        "• Измените цвет фона на голубой\n"
        "• Добавьте более яркое освещение\n"
        "• Сделайте модель более улыбчивой\n\n"
        "Ваши пожелания будут добавлены к исходному запросу."
    )
    await state.set_state(ProductCreationStates.waiting_for_custom_prompt)
    await callback.answer()


@router.callback_query(F.data == "after_gen_finish")
async def after_generation_finish_handler(callback: CallbackQuery, state: FSMContext):
    """Обработчик завершения после генерации"""
    from handlers.user_handlers import show_main_menu
    
    # Очищаем состояние и временные файлы
    data = await state.get_data()
    temp_photo_path = data.get('temp_photo_path')
    
    if temp_photo_path and os.path.exists(temp_photo_path):
        os.unlink(temp_photo_path)
    
    await state.clear()
    await callback.message.delete()
    await show_main_menu(callback.message)
    await callback.answer()


@router.message(StateFilter(ProductCreationStates.waiting_for_custom_prompt))
async def custom_prompt_handler(message: Message, state: FSMContext):
    """Обработчик пользовательского промпта для изменений"""
    user_id = message.from_user.id
    current_balance = db.get_user_balance(user_id)
    
    if current_balance <= 0 and not GEMINI_DEMO_MODE:
        await message.answer("❌ Недостаточно генераций. Пополните баланс.")
        await state.clear()
        return
    
    # Получаем данные
    data = await state.get_data()
    original_prompt = data.get('original_prompt', data.get('prompt', ''))
    user_additions = message.text
    temp_photo_path = data.get('temp_photo_path')
    
    # Проверка наличия временного файла
    if not temp_photo_path or not os.path.exists(temp_photo_path):
        await message.answer(
            "❌ Ошибка: исходное фото не найдено. Пожалуйста, начните создание фото заново.",
            reply_markup=get_back_keyboard()
        )
        await state.clear()
        return
    
    # Сохраняем оригинальный промпт, если еще не сохранен
    if 'original_prompt' not in data:
        await state.update_data(original_prompt=original_prompt)
    
    # Объединяем промпты
    combined_prompt = f"{original_prompt}\n\nAdditional user requirements: {user_additions}"
    
    # Списываем генерацию
    if not GEMINI_DEMO_MODE:
        new_balance = current_balance - 1
        db.update_user_balance(user_id, new_balance)
    else:
        new_balance = current_balance
    
    db.add_generation(user_id, combined_prompt)
    
    generating_msg = await message.answer(
        f"🎨 Генерация с изменениями...\n\n"
        f"[▱▱▱▱▱▱▱▱▱▱] 0%\n\n"
        f"⏱️ Пожалуйста, подождите..."
    )
    
    try:
        # Запускаем прогресс-бар и генерацию параллельно
        progress_task = asyncio.create_task(show_progress_bar(generating_msg, duration=12))
        
        # Генерация с измененным промптом
        processed_image_bytes = call_gemini_api(temp_photo_path, combined_prompt)
        
        # Отменяем прогресс-бар
        progress_task.cancel()
        try:
            await progress_task
        except asyncio.CancelledError:
            pass
        
        # Пересохранение через PIL
        image_stream = BytesIO(processed_image_bytes)
        img = Image.open(image_stream)
        
        output_stream = BytesIO()
        img.save(output_stream, format='JPEG', quality=90)
        final_image_bytes = output_stream.getvalue()
        
        # Отправка
        generated_image = BufferedInputFile(final_image_bytes, filename="generated_fashion.jpg")
        
        await message.answer_photo(
            generated_image,
            caption="✨ Генерация с изменениями завершена!",
            reply_markup=get_regenerate_keyboard()
        )
        
        await generating_msg.delete()
        
    except Exception as e:
        logger.error(f"Ошибка при регенерации: {e}")
        await generating_msg.delete()
        
        error_msg = str(e)
        await message.answer(
            f"❌ Произошла ошибка при генерации:\n\n"
            f"{error_msg[:200]}\n\n"
            f"Попробуйте изменить описание или начните заново.",
            parse_mode=None
        )
        
        # Возвращаем баланс
        if not GEMINI_DEMO_MODE:
            db.update_user_balance(user_id, current_balance)
    
    finally:
        # Очищаем состояние и файл
        await state.clear()
        if temp_photo_path and os.path.exists(temp_photo_path):
            os.unlink(temp_photo_path)