"""
Интеграция с Google Gemini API

Использует прямой REST API для генерации изображений через модель gemini-2.5-flash-image.
Этот подход работает напрямую с HTTP запросами, без необходимости устанавливать 
устаревшие версии google-generativeai.
"""
import base64
import io
from typing import Dict, Any

import requests
from PIL import Image, ImageDraw

from config import GEMINI_API_KEY, GEMINI_DEMO_MODE, logger


def call_gemini_api(
    input_image_path: str,
    prompt: str,
    extra_params: Dict[str, Any] = None
) -> bytes:
    """
    Отправляет изображение и промпт в Gemini 2.5 Flash Image API и возвращает байты изображения.
    
    Args:
        input_image_path: Путь к входному изображению (одежда)
        prompt: Текстовый промпт для генерации (описание модели и сцены)
        extra_params: Дополнительные параметры API
        
    Returns:
        bytes: Байты сгенерированного изображения
        
    Raises:
        Exception: При ошибках API или отсутствии результата
    """
    if GEMINI_DEMO_MODE:
        return _generate_demo_image(prompt)

    # Конфигурация endpoint
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    
    try:
        # Загрузка и кодирование входного изображения
        with open(input_image_path, 'rb') as img_file:
            input_image_base64 = base64.b64encode(img_file.read()).decode('utf-8')
        
        # Формирование payload с промптом и изображением
        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {
                        "inlineData": {
                            "mimeType": "image/jpeg",
                            "data": input_image_base64
                        }
                    }
                ]
            }]
        }
        
        logger.info("Отправка запроса к Gemini 2.5 Flash Image API...")
        
        # Отправка запроса
        response = requests.post(endpoint, headers=headers, json=payload, timeout=60)
        
        if response.status_code != 200:
            error_msg = f"API вернул код {response.status_code}: {response.text}"
            logger.error(error_msg)
            raise Exception(error_msg)
        
        result = response.json()
        
        # Извлечение изображения из ответа
        for candidate in result.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                # Проверяем оба варианта ключа (camelCase и snake_case)
                inline = part.get("inlineData") or part.get("inline_data")
                
                if inline and "data" in inline:
                    # Декодирование base64 изображения
                    image_bytes = base64.b64decode(inline["data"])
                    logger.info(f"✅ Успешно получено изображение ({len(image_bytes)} байт)")
                    return image_bytes
        
        # Если изображение не найдено в ответе
        if "candidates" not in result:
            error_msg = f"API не вернул кандидатов. Ответ: {result}"
            logger.error(error_msg)
            raise Exception(error_msg)
        
        # Если есть текст вместо изображения
        text_parts = []
        for candidate in result.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                if "text" in part:
                    text_parts.append(part["text"])
        
        if text_parts:
            error_msg = f"API вернул текст вместо изображения: {' '.join(text_parts[:200])}"
            logger.warning(error_msg)
            raise Exception(error_msg)
        
        raise Exception("API не вернул изображение в ожидаемом формате")
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка сетевого запроса: {e}")
        raise Exception(f"Ошибка сетевого запроса к Gemini API: {e}")
    except Exception as e:
        logger.error(f"Ошибка генерации изображения: {e}")
        raise Exception(f"Ошибка генерации: {e}")


def _generate_demo_image(prompt: str) -> bytes:
    """Генерирует демо-изображение для тестирования"""
    img = Image.new('RGB', (1024, 1024), color=(73, 109, 137))
    d = ImageDraw.Draw(img)
    d.text((50, 50), "ДЕМО-РЕЖИМ. Промпт: " + prompt[:100] + "...", fill=(255, 255, 255))
    
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='PNG')
    return img_byte_arr.getvalue()

