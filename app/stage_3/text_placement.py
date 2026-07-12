"""
text_placement.py — функции позиционирования и вставки текстового слоя.

Используется в pipeline3.py для размещения невидимого текста
поверх изображений страниц PDF.

Логика работы:
    1. EasyOCR определяет координаты фрагментов текста на странице
    2. Фрагменты сопоставляются с блоками Chandra и группируются в строки
    3. Для каждого блока подбирается размер шрифта
    4. Слова распределяются по координатам и вставляются с render_mode=3
"""

from pathlib import Path

import fitz


FONT_PATH = str(Path(__file__).parent.parent / "resources" / "DejaVuSans.ttf")
FONT_NAME = "DejaVu"


def get_lines_in_block(block: dict,
                       results_full: list,
                       sx_px: float,
                       sy_px: float) -> tuple[list, int, int, int, int]:
    """
    Находит фрагменты EasyOCR внутри блока Chandra и группирует их в строки.

    Координаты Chandra в диапазоне 0–1000, поэтому умножаем на sx_px/sy_px
    чтобы перевести в пиксели изображения.

    Args:
        block:        блок из JSON Chandra с ключами bbox, label, content
        results_full: список фрагментов EasyOCR — [(bbox, text, conf), ...]
        sx_px:        масштаб по X (ширина_изображения / 1000)
        sy_px:        масштаб по Y (высота_изображения / 1000)

    Returns:
        кортеж (rows, bx1, by1, bx2, by2) где rows — список строк,
        каждая строка содержит x1, y1, x2, y2 и список фрагментов
    """
    bx1 = int(block["bbox"][0] * sx_px)
    by1 = int(block["bbox"][1] * sy_px)
    bx2 = int(block["bbox"][2] * sx_px)
    by2 = int(block["bbox"][3] * sy_px)

    fragments = []
    for bbox_e, text_e, conf in results_full:
        ex1, ey1 = int(bbox_e[0][0]), int(bbox_e[0][1])
        ex2, ey2 = int(bbox_e[2][0]), int(bbox_e[2][1])
        cx, cy = (ex1 + ex2) / 2, (ey1 + ey2) / 2
        if bx1 <= cx <= bx2 and by1 <= cy <= by2:
            ex1, ey1 = max(ex1, bx1), max(ey1, by1)
            ex2, ey2 = min(ex2, bx2), min(ey2, by2)
            if ex2 > ex1 and ey2 > ey1:
                fragments.append({
                    "text":    text_e,
                    "n_words": len(text_e.split()),
                    "x1": ex1, "y1": ey1,
                    "x2": ex2, "y2": ey2,
                })

    if not fragments:
        return [], bx1, by1, bx2, by2

    fragments.sort(key=lambda f: f["y1"])

    rows = []
    for f in fragments:
        placed = False
        for row in rows:
            if abs((row["y1"] + row["y2"]) / 2 - (f["y1"] + f["y2"]) / 2) < 15:
                row["x1"] = min(row["x1"], f["x1"])
                row["x2"] = max(row["x2"], f["x2"])
                row["y1"] = min(row["y1"], f["y1"])
                row["y2"] = max(row["y2"], f["y2"])
                row["fragments"].append(f)
                placed = True
                break
        if not placed:
            rows.append({"x1": f["x1"], "y1": f["y1"],
                         "x2": f["x2"], "y2": f["y2"],
                         "fragments": [f]})

    rows.sort(key=lambda r: r["y1"])

    avg_h = sum(r["y2"] - r["y1"] for r in rows) / len(rows)
    for r in rows:
        cy = (r["y1"] + r["y2"]) / 2
        r["y1"] = int(cy - avg_h / 2)
        r["y2"] = int(cy + avg_h / 2)

    for i in range(1, len(rows)):
        overlap = rows[i - 1]["y2"] - rows[i]["y1"]
        if overlap > 0:
            rows[i - 1]["y2"] -= overlap // 2
            rows[i]["y1"]     += overlap // 2

    for row in rows:
        row["fragments"].sort(key=lambda f: f["x1"])

    return rows, bx1, by1, bx2, by2


def calc_fontsize(chandra_words: list[str],
                  rows: list[dict],
                  px_to_pt: float,
                  fitz_font: fitz.Font) -> float:
    """
    Подбирает размер шрифта чтобы текст блока поместился в его ширину.

    Args:
        chandra_words: список слов из блока Chandra
        rows:          список строк EasyOCR внутри блока
        px_to_pt:      коэффициент перевода пикселей в points (page.rect.width / 1240)
        fitz_font:     шрифт PyMuPDF для измерения ширины текста

    Returns:
        подобранный размер шрифта в points
    """
    total_chars    = sum(len(w) + 1 for w in chandra_words)
    total_width_pt = sum((r["x2"] - r["x1"]) * px_to_pt for r in rows)
    if total_chars == 0 or total_width_pt == 0:
        return 8

    fontsize = 12
    for _ in range(50):
        char_w    = fitz_font.text_length("a", fontsize=fontsize)
        estimated = total_chars * char_w
        if abs(estimated - total_width_pt) < total_width_pt * 0.1:
            break
        fontsize *= total_width_pt / estimated
    return fontsize


def get_word_boxes(rows: list[dict],
                   fontsize: float,
                   fitz_font: fitz.Font,
                   px_to_pt: float) -> list[dict]:
    """
    Делит фрагменты EasyOCR на отдельные слова с координатами.

    Ширина каждого слова рассчитывается пропорционально количеству слов
    во фрагменте.

    Args:
        rows:      список строк EasyOCR внутри блока
        fontsize:  размер шрифта в points
        fitz_font: шрифт PyMuPDF
        px_to_pt:  коэффициент перевода пикселей в points

    Returns:
        список словарей с координатами каждого слова:
        x1, x2, y1, y2, fontsize, is_last_in_row, row_idx, px_to_pt
    """
    word_boxes = []
    for row_idx, row in enumerate(rows):
        for frag in row["fragments"]:
            n      = frag["n_words"]
            frag_w = frag["x2"] - frag["x1"]
            word_w = frag_w / max(n, 1)
            for i in range(n):
                is_last_in_row = (frag == row["fragments"][-1] and i == n - 1)
                word_boxes.append({
                    "x1": frag["x1"] + i * word_w,
                    "x2": frag["x1"] + (i + 1) * word_w,
                    "y1": row["y1"],
                    "y2": row["y2"],
                    "fontsize":      fontsize,
                    "is_last_in_row": is_last_in_row,
                    "row_idx":       row_idx,
                    "px_to_pt":      px_to_pt,
                })
    return word_boxes


def plan_insertions(chandra_words: list[str],
                    word_boxes: list[dict],
                    fitz_font: fitz.Font) -> list[tuple]:
    """
    Планирует вставку слов Chandra в координатные боксы EasyOCR.

    Если слово слишком маленькое для бокса — объединяет со следующим словом.
    Если слово слишком большое — расширяет на соседние боксы (до 5).
    Если последнее слово в строке не помещается — переносит на следующую строку.

    Args:
        chandra_words: список слов из блока Chandra
        word_boxes:    список координатных боксов из get_word_boxes
        fitz_font:     шрифт PyMuPDF для измерения ширины текста

    Returns:
        список кортежей (слово, бокс) где бокс может быть None
        если слово не нашло места (вставляется после последней строки)
    """
    to_insert = []
    word_idx  = 0
    box_idx   = 0

    while box_idx < len(word_boxes) and word_idx < len(chandra_words):
        current_words = [chandra_words[word_idx]]
        current_boxes = [word_boxes[box_idx]]

        for _ in range(10):
            combined_str = " ".join(current_words)
            total_box_w  = sum(b["x2"] - b["x1"] for b in current_boxes)
            fontsize     = current_boxes[0]["fontsize"]
            px_to_pt     = current_boxes[0]["px_to_pt"]
            combined_w_px = fitz_font.text_length(combined_str, fontsize=fontsize) / px_to_pt

            if combined_w_px < total_box_w * 0.7:
                next_w_idx = word_idx + len(current_words)
                if next_w_idx >= len(chandra_words):
                    break
                current_words.append(chandra_words[next_w_idx])
                new_w_px = fitz_font.text_length(" ".join(current_words), fontsize=fontsize) / px_to_pt
                if new_w_px > total_box_w * 1.2:
                    next_b_idx = box_idx + len(current_boxes)
                    if (next_b_idx < len(word_boxes) and
                            word_boxes[next_b_idx]["row_idx"] == current_boxes[0]["row_idx"]):
                        current_boxes.append(word_boxes[next_b_idx])

            elif combined_w_px > total_box_w * 1.2:
                if len(current_boxes) >= 5:
                    break
                next_b_idx = box_idx + len(current_boxes)
                if (next_b_idx >= len(word_boxes) or
                        word_boxes[next_b_idx]["row_idx"] != current_boxes[0]["row_idx"]):
                    break
                current_boxes.append(word_boxes[next_b_idx])
            else:
                break

        combined_str  = " ".join(current_words)
        total_box_w   = sum(b["x2"] - b["x1"] for b in current_boxes)
        fontsize      = current_boxes[0]["fontsize"]
        px_to_pt      = current_boxes[0]["px_to_pt"]
        combined_w_px = fitz_font.text_length(combined_str, fontsize=fontsize) / px_to_pt
        last_box      = current_boxes[-1]

        if combined_w_px > total_box_w * 1.2 and last_box["is_last_in_row"]:
            next_b_idx = box_idx + len(current_boxes)
            next_box   = word_boxes[next_b_idx] if next_b_idx < len(word_boxes) else None
            total_w    = total_box_w + (next_box["x2"] - next_box["x1"] if next_box else 0)
            cut        = max(1, int(len(combined_str) * (total_box_w / total_w)))
            merged_box = dict(current_boxes[0])
            merged_box["x2"] = current_boxes[-1]["x2"]
            to_insert.append((combined_str[:cut], merged_box))
            if next_box:
                to_insert.append((combined_str[cut:], next_box))
                box_idx += len(current_boxes) + 1
            else:
                box_idx += len(current_boxes)
            word_idx += len(current_words)
        else:
            merged_box = dict(current_boxes[0])
            merged_box["x2"]          = current_boxes[-1]["x2"]
            merged_box["is_last_in_row"] = current_boxes[-1]["is_last_in_row"]
            to_insert.append((combined_str, merged_box))
            word_idx += len(current_words)
            box_idx  += len(current_boxes)

    while word_idx < len(chandra_words):
        to_insert.append((chandra_words[word_idx], None))
        word_idx += 1

    return to_insert


def insert_words(chandra_words: list[str],
                 word_boxes: list[dict],
                 rows: list[dict],
                 page: fitz.Page,
                 px_to_pt: float,
                 fitz_font: fitz.Font) -> None:
    """
    Вставляет слова Chandra в PDF страницу как невидимый текстовый слой.

    Слова вставляются с render_mode=3 (невидимый текст) — они не видны
    но доступны для поиска и копирования.

    Args:
        chandra_words: список слов из блока Chandra
        word_boxes:    список координатных боксов из get_word_boxes
        rows:          список строк EasyOCR (для позиции слов без бокса)
        page:          страница PDF для вставки текста
        px_to_pt:      коэффициент перевода пикселей в points
        fitz_font:     шрифт PyMuPDF для измерения ширины текста
    """
    to_insert = plan_insertions(chandra_words, word_boxes, fitz_font)

    last_row      = rows[-1]
    last_h        = (last_row["y2"] - last_row["y1"]) * px_to_pt
    last_fontsize = last_h * 0.85
    last_x        = last_row["x1"] * px_to_pt
    last_cy       = last_row["y1"] * px_to_pt + last_h / 2 + last_fontsize / 3

    for word, box in to_insert:
        if box is None:
            page.insert_text(fitz.Point(last_x, last_cy), word,
                             fontname=FONT_NAME, fontsize=last_fontsize,
                             render_mode=3)
            last_x += fitz_font.text_length(word + " ", fontsize=last_fontsize)
            continue

        wx1, wx2 = box["x1"] * px_to_pt, box["x2"] * px_to_pt
        wy1, wy2 = box["y1"] * px_to_pt, box["y2"] * px_to_pt
        box_w, box_h = wx2 - wx1, wy2 - wy1
        fontsize = box["fontsize"]
        cy       = wy1 + box_h / 2 + fontsize / 3
        cx       = wx1 + (box_w - fitz_font.text_length(word, fontsize=fontsize)) / 2

        page.insert_text(fitz.Point(cx, cy), word,
                         fontname=FONT_NAME, fontsize=fontsize,
                         render_mode=3)