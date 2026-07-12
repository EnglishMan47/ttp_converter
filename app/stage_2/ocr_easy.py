"""
ocr_easy.py — распознавание лёгким движком EasyOCR.

Движок возвращает фрагменты текста сразу с координатами и уверенностью,
поэтому текстовый слой строится напрямую, без сопоставления с другой
моделью. Структура страницы (заголовки для закладок и метаданных)
определяется эвристически: строки заметно крупнее медианной высоты в
верхней части страницы считаются заголовками.

Результат пишется построчно в JSONL с теми же гарантиями продолжения
после сбоя, что и у нейросетевого движка.

Формат строки JSONL:
    {"page": N, "width_px": W, "height_px": H, "engine": "easyocr",
     "words": [[x1, y1, x2, y2, "текст", conf], ...],
     "blocks": [...], "markdown": "...", "error": ""}
"""

from __future__ import annotations

import gc
import json
from pathlib import Path

from stage_2.jsonl_store import done_pages, load_pages  # noqa: F401

_reader = None


def get_reader():
    global _reader
    if _reader is None:
        import easyocr
        from stage_2.engines import gpu_available
        _reader = easyocr.Reader(["ru", "en"], gpu=gpu_available())
    return _reader


def _structure_blocks(fragments: list, w_px: int, h_px: int) -> list[dict]:
    """
    Эвристическая структура страницы из фрагментов EasyOCR.

    Каждый фрагмент становится блоком с bbox в нормированных координатах
    0–1000 (общий формат с нейросетевым движком). Метка "Header"
    присваивается строкам, чья высота не меньше 1.35 медианной и которые
    расположены в верхних двух третях страницы.
    """
    if not fragments:
        return []
    heights = sorted(f[0][2][1] - f[0][0][1] for f in fragments)
    med_h = heights[len(heights) // 2] or 1

    blocks = []
    for bbox, text, conf in fragments:
        x1, y1 = bbox[0]
        x2, y2 = bbox[2]
        frag_h = y2 - y1
        label = "Text"
        if frag_h >= med_h * 1.35 and y1 < h_px * 0.67 and len(text) >= 4:
            label = "Header"
        blocks.append({
            "bbox": [int(x1 / w_px * 1000), int(y1 / h_px * 1000),
                     int(x2 / w_px * 1000), int(y2 / h_px * 1000)],
            "label": label,
            "content": text.strip(),
        })
    return blocks


def run_ocr(pdf_path: Path,
            output_jsonl: Path,
            dpi: int = 150,
            on_progress=None,
            is_cancelled=None) -> dict:
    """
    Returns:
        {"pages": int, "resumed_from": int, "page_errors": [...]}
    """
    import fitz
    import numpy as np
    from PIL import Image

    from stage_1.pipeline import ProcessingCancelled

    pdf_path = Path(pdf_path)
    output_jsonl = Path(output_jsonl)
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(pdf_path))
    total = len(doc)

    done = done_pages(output_jsonl)
    todo = [i for i in range(total) if (i + 1) not in done]
    page_errors: list[dict] = []

    reader = get_reader() if todo else None

    with open(output_jsonl, "a", encoding="utf-8") as out:
        for i in todo:
            if is_cancelled and is_cancelled():
                doc.close()
                raise ProcessingCancelled()

            pix = doc[i].get_pixmap(dpi=dpi)
            w_px, h_px = pix.width, pix.height
            img = np.array(Image.frombytes("RGB", [w_px, h_px], pix.samples))
            del pix

            try:
                fragments = reader.readtext(
                    img, width_ths=0.001, paragraph=False,
                    min_size=3, text_threshold=0.4, low_text=0.3)
                words = [[float(b[0][0]), float(b[0][1]),
                          float(b[2][0]), float(b[2][1]),
                          text, round(float(conf), 3)]
                         for b, text, conf in fragments if text.strip()]
                blocks = _structure_blocks(fragments, w_px, h_px)
                record = {
                    "page": i + 1, "width_px": w_px, "height_px": h_px,
                    "engine": "easyocr", "words": words, "blocks": blocks,
                    "markdown": " ".join(w[4] for w in words),
                    "error": "",
                }
            except Exception as e:
                record = {"page": i + 1, "width_px": w_px, "height_px": h_px,
                          "engine": "easyocr", "words": [], "blocks": [],
                          "markdown": "", "error": str(e)}
                page_errors.append({"page": i + 1, "error": str(e)})

            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()
            del img
            gc.collect()

            if on_progress:
                on_progress(int((len(done) + todo.index(i) + 1) / total * 100))

    doc.close()
    return {"pages": total, "resumed_from": len(done),
            "page_errors": page_errors}
