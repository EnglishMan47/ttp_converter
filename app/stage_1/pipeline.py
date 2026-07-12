"""
pipeline.py — этап 1: папка TIFF → промежуточный PDF.

Каждая страница проходит: исправление ориентации, выравнивание наклона,
обрезку полей сканера, нормализацию яркости, масштабирование под общий
канвас формата A4. Ошибка на отдельной странице не прерывает обработку
книги: такая страница помещается в PDF без предобработки и попадает в
итоговый отчёт.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
# Pillow >= 12.2 регистрирует кодеки лениво: PdfImagePlugin при сохранении
# PDF обращается к JPEG-кодеку напрямую (Image.SAVE["JPEG"]) и без этого
# импорта падает с KeyError: 'JPEG' (python-pillow/Pillow#9545).
from PIL import JpegImagePlugin  # noqa: F401

from stage_1.image_processing import (
    deskew,
    fix_orientation,
    get_page_bg_brightness,
    load_image_downscaled,
    normalize_brightness,
    should_trim,
    trim_scan_borders,
)


class ProcessingCancelled(Exception):
    """Обработка остановлена пользователем."""


def extract_page_number(filename: str) -> int:
    match = re.search(r"(\d+)(?=\D*$)", filename)
    return int(match.group(1)) if match else 10**9


def find_tiff_files(folder: Path) -> list[Path]:
    files = [p for p in Path(folder).iterdir()
             if p.suffix.lower() in {".tif", ".tiff"}]
    files.sort(key=lambda x: (extract_page_number(x.name), x.name.lower()))
    return files


def preprocess_page(path: Path, settings: dict) -> np.ndarray:
    img = load_image_downscaled(path, max_side=3000)
    img = fix_orientation(img)
    if settings.get("deskew", True):
        img = deskew(img, max_angle=5.0)
    if settings.get("trim_borders", True) and should_trim(img):
        img = trim_scan_borders(img)
    return img


def run_pipeline(input_folder: Path,
                 output_pdf: Path,
                 settings: dict,
                 on_progress=None,
                 is_cancelled=None) -> dict:
    """
    Returns:
        {"pages": int, "page_errors": [{"file": str, "error": str}, ...]}
    """
    files = find_tiff_files(input_folder)
    total = len(files)
    if total == 0:
        raise ValueError(f"TIFF файлы не найдены в {input_folder}")

    dpi = int(settings.get("dpi", 150))
    target_h = int(11.7 * dpi)
    target_w = int(8.27 * dpi)
    tmp_dir = Path(tempfile.mkdtemp(prefix="scan_stage1_"))
    page_errors: list[dict] = []

    try:
        tmp_files, brightnesses = [], []

        for idx, f in enumerate(files):
            if is_cancelled and is_cancelled():
                raise ProcessingCancelled()
            try:
                img = preprocess_page(f, settings)
            except Exception as e:
                page_errors.append({"file": f.name, "error": str(e)})
                img = load_image_downscaled(f, max_side=3000)

            h, w = img.shape[:2]
            pre_scale = min(target_h / h, target_w / w)
            if pre_scale < 1.0:
                img = cv2.resize(
                    img,
                    (max(1, int(w * pre_scale)), max(1, int(h * pre_scale))),
                    interpolation=cv2.INTER_AREA)

            brightnesses.append(get_page_bg_brightness(img))
            tmp_path = tmp_dir / f"{idx:06d}.png"
            cv2.imwrite(str(tmp_path), img)
            tmp_files.append(tmp_path)
            del img
            if on_progress:
                on_progress(int((idx + 1) / total * 50))

        # единый размер листа по медианным пропорциям страниц
        sizes = [cv2.imread(str(f)).shape[:2] for f in tmp_files]
        hs = sorted(s[0] for s in sizes)
        ws = sorted(s[1] for s in sizes)
        med_h, med_w = hs[len(hs) // 2], ws[len(ws) // 2]
        a4_scale = min(target_h / med_h, target_w / med_w)
        canvas_h = max(1, int(med_h * a4_scale))
        canvas_w = max(1, int(med_w * a4_scale))

        imgs = [cv2.imread(str(f)) for f in tmp_files]
        if settings.get("normalize_brightness", True):
            imgs = normalize_brightness(imgs, brightnesses)

        pil_pages = []
        for idx, img in enumerate(imgs):
            if is_cancelled and is_cancelled():
                raise ProcessingCancelled()
            h, w = img.shape[:2]
            scale = min(canvas_h / h, canvas_w / w)
            img = cv2.resize(
                img,
                (max(1, int(w * scale)), max(1, int(h * scale))),
                interpolation=cv2.INTER_AREA)
            if img.shape[1] != canvas_w or img.shape[0] != canvas_h:
                img = cv2.resize(img, (canvas_w, canvas_h),
                                 interpolation=cv2.INTER_AREA)
            pil_pages.append(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))
            del img
            if on_progress:
                on_progress(50 + int((idx + 1) / total * 50))

        output_pdf = Path(output_pdf)
        output_pdf.parent.mkdir(parents=True, exist_ok=True)
        # запись через временный файл: недописанный после сбоя PDF не
        # должен засчитаться как чекпоинт этапа при повторном запуске
        tmp_pdf = output_pdf.with_name(output_pdf.name + ".part")
        pil_pages[0].save(tmp_pdf, format="PDF", save_all=True,
                          append_images=pil_pages[1:], resolution=dpi)
        os.replace(tmp_pdf, output_pdf)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return {"pages": total, "page_errors": page_errors}
