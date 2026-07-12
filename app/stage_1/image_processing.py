"""
image_processing.py — функции предобработки изображений страниц.

Используется в pipeline.py для подготовки TIFF страниц перед сохранением в PDF.
"""

import cv2
import numpy as np
from PIL import Image
from pathlib import Path


def load_image_downscaled(path: Path, max_side: int = 3000) -> np.ndarray:
    """
    Загружает изображение и уменьшает его если оно слишком большое.

    Args:
        path:     путь к файлу изображения
        max_side: максимальная сторона в пикселях (по умолчанию 3000)

    Returns:
        изображение в формате BGR numpy array
    """
    img = Image.open(path)
    if img.mode == "1":
        img = img.convert("L").convert("RGB")
    else:
        img = img.convert("RGB")

    w, h = img.size
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def fix_orientation(image: np.ndarray) -> np.ndarray:
    """
    Поворачивает изображение если оно горизонтальное.

    Args:
        image: изображение в формате BGR numpy array

    Returns:
        изображение с исправленной ориентацией
    """
    h, w = image.shape[:2]
    if w > h * 1.2:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    return image


def should_trim(image: np.ndarray, min_border_px: int = 5) -> bool:
    """
    Определяет есть ли рамка сканера по краям изображения.

    Анализирует среднюю полосу страницы — ищет подряд идущие
    полностью белые столбцы/строки от каждого края.

    Args:
        image:          изображение в формате BGR numpy array
        min_border_px:  минимальная ширина рамки в пикселях

    Returns:
        True если рамка обнаружена, False иначе
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape

    y0, y1 = int(h * 0.40), int(h * 0.60)
    x0, x1 = int(w * 0.40), int(w * 0.60)
    hstrip = gray[y0:y1, :]
    vstrip = gray[:, x0:x1]

    def find_border(strips: list) -> int:
        white_count = 0
        for s in strips:
            if np.all(s == 255):
                white_count += 1
            else:
                if white_count >= min_border_px and np.all(s != 255):
                    return white_count
                return 0
        return 0

    max_check   = int(w * 0.15)
    max_check_v = int(h * 0.15)

    left   = find_border([hstrip[:, x]     for x in range(min(max_check, w))])
    right  = find_border([hstrip[:, w-1-x] for x in range(min(max_check, w))])
    top    = find_border([vstrip[y, :]     for y in range(min(max_check_v, h))])
    bottom = find_border([vstrip[h-1-y, :] for y in range(min(max_check_v, h))])

    return max(left, right, top, bottom) >= min_border_px


def trim_scan_borders(image: np.ndarray,
                      dark_threshold: int = 255,
                      content_ratio: float = 0.01,
                      max_crop_ratio: float = 0.15) -> np.ndarray:
    """
    Обрезает белую рамку сканера по краям изображения.

    Args:
        image:           изображение в формате BGR numpy array
        dark_threshold:  порог яркости для определения контента
        content_ratio:   минимальная доля тёмных пикселей в полосе
        max_crop_ratio:  максимальная доля обрезки от каждого края

    Returns:
        обрезанное изображение, или оригинал если обрезка невозможна
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    strip_h = max(3, h // 200)
    strip_w = max(3, w // 200)
    max_crop_h = int(h * max_crop_ratio)
    max_crop_w = int(w * max_crop_ratio)

    top = 0
    for y in range(0, min(max_crop_h, h - strip_h), strip_h):
        if np.mean(gray[y:y + strip_h, :] < dark_threshold) > content_ratio:
            top = y
            break

    bottom = h
    for y in range(h - strip_h, max(h - max_crop_h, strip_h), -strip_h):
        if np.mean(gray[y:y + strip_h, :] < dark_threshold) > content_ratio:
            bottom = y + strip_h
            break

    left = 0
    for x in range(0, min(max_crop_w, w - strip_w), strip_w):
        if np.mean(gray[:, x:x + strip_w] < dark_threshold) > content_ratio:
            left = x
            break

    right = w
    for x in range(w - strip_w, max(w - max_crop_w, strip_w), -strip_w):
        if np.mean(gray[:, x:x + strip_w] < dark_threshold) > content_ratio:
            right = x + strip_w
            break

    if bottom <= top or right <= left or (bottom - top) < 10 or (right - left) < 10:
        return image

    return image[top:bottom, left:right]


def get_page_bg_brightness(image: np.ndarray) -> float:
    """
    Оценивает яркость фона страницы по угловым областям изображения.

    Args:
        image: изображение в формате BGR numpy array

    Returns:
        медианная яркость фона (0–255)
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    margin = max(10, min(h, w) // 20)

    corners = [
        gray[:margin, :margin],
        gray[:margin, w - margin:],
        gray[h - margin:, :margin],
        gray[h - margin:, w - margin:],
    ]
    samples = np.concatenate([c.flatten() for c in corners])
    bright = samples[samples > 100]
    return float(np.median(bright)) if len(bright) > 0 else float(np.median(samples))


def normalize_brightness(images: list[np.ndarray],
                         brightnesses: list[float],
                         max_diff_ratio: float = 0.5) -> list[np.ndarray]:
    """
    Выравнивает яркость фона между страницами книги.

    Страницы с яркостью слишком далёкой от средней остаются без изменений.

    Args:
        images:          список изображений в формате BGR numpy array
        brightnesses:    список значений яркости фона для каждой страницы
        max_diff_ratio:  максимальное допустимое отклонение яркости от средней

    Returns:
        список изображений с выровненной яркостью
    """
    mean_bg = float(np.mean(brightnesses))
    result = []
    for img, bg in zip(images, brightnesses):
        if (mean_bg == 0
                or abs(bg - mean_bg) / mean_bg > max_diff_ratio
                or abs(bg - mean_bg) < 3):
            result.append(img)
            continue
        scale = mean_bg / bg if bg > 0 else 1.0
        result.append(np.clip(img.astype(np.float32) * scale, 0, 255).astype(np.uint8))
    return result

def deskew(image: np.ndarray, max_angle: float = 5.0) -> np.ndarray:
    """
    Коррекция наклона страницы (настоящий deskew, а не поворот на 90°).

    Оценивает угол по минимальному охватывающему прямоугольнику тёмных
    пикселей (текста). Углы больше max_angle игнорируются — это почти
    наверняка ошибка оценки, а не реальный перекос сканера.

    Args:
        image:     изображение BGR
        max_angle: максимальный корректируемый угол в градусах

    Returns:
        выровненное изображение (или оригинал, если угол ~0 или подозрительный)
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    binary = cv2.threshold(gray, 0, 255,
                           cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    coords = cv2.findNonZero(binary)
    if coords is None or len(coords) < 100:
        return image

    angle = cv2.minAreaRect(coords)[-1]
    if angle > 45:
        angle -= 90
    elif angle < -45:
        angle += 90

    if abs(angle) < 0.2 or abs(angle) > max_angle:
        return image

    h, w = image.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(image, m, (w, h),
                          flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE)
