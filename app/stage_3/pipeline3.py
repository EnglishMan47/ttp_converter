"""
pipeline3.py — этап 3: промежуточный PDF + результаты распознавания →
итоговый PDF/A с невидимым текстовым слоем.

Работает с обоими движками распознавания:

    easyocr — слова с координатами уже сохранены на этапе 2, слой
              вставляется напрямую; заголовки для закладок определены
              эвристически.

    chandra — модель даёт текст и структуру блоков; координаты отдельных
              слов уточняются повторным проходом EasyOCR по странице.
              Дополнительно тексты двух движков сверяются между собой:
              страницы с сильным расхождением помечаются для ручной
              проверки, а при пустом или ошибочном ответе модели слой
              строится из фрагментов EasyOCR.

Масштаб координат текстового слоя вычисляется из фактических размеров
рендера каждой страницы, сохранённых на этапе 2, поэтому слой точно
совпадает с изображением при любых пропорциях исходных сканов.

Слова с низкой уверенностью распознавания подсвечиваются в отдельной
копии <книга>_review.pdf — основной архивный файл остаётся без
аннотаций, чтобы не нарушать соответствие PDF/A.
"""

from __future__ import annotations

from pathlib import Path

from stage_3 import fallback as fb
from stage_3.pdf_utils import (add_blank_page, finalize_pdf, make_pdfa,
                               sanitize_toc)
from stage_3.text_placement import (FONT_NAME, FONT_PATH, calc_fontsize,
                                    get_lines_in_block, get_word_boxes,
                                    insert_words)

WORD_CONF_THRESHOLD = 0.5
AGREEMENT_THRESHOLD = 0.75
HARD_FLOOR = 0.35

_HEADER_LEVELS = (
    ("title", 1),
    ("section-header", 1), ("sectionheader", 1), ("section", 1),
    ("subsection", 2), ("sub-header", 2), ("subheader", 2),
    ("header", 1), ("heading", 1),
)


def _header_level(label: str) -> int | None:
    ll = label.lower()
    for key, lvl in _HEADER_LEVELS:
        if key in ll:
            return lvl + (1 if "sub" in ll and lvl == 1 else 0)
    return None


def _insert_easy_words(page, words, px_to_pt, low_conf_rects, fitz):
    """Вставка невидимого слоя из слов EasyOCR (координаты точные)."""
    for x1, y1, x2, y2, text, conf in words:
        if not text.strip():
            continue
        hgt = max((y2 - y1) * px_to_pt, 4)
        page.insert_text(
            fitz.Point(x1 * px_to_pt, y1 * px_to_pt + hgt * 0.8),
            text, fontname=FONT_NAME, fontsize=hgt * 0.85, render_mode=3)
        if conf < WORD_CONF_THRESHOLD:
            low_conf_rects.append(fitz.Rect(
                x1 * px_to_pt, y1 * px_to_pt, x2 * px_to_pt, y2 * px_to_pt))


def process_book(pdf_path: Path,
                 pages_data: list[dict],
                 output_path: Path,
                 settings: dict,
                 on_progress=None,
                 is_cancelled=None) -> dict:
    """
    Returns:
        {"pages", "review_pages", "low_conf_words", "fallback_used",
         "page_errors", "bookmarks", "review_pdf", "metadata"}
    """
    import fitz
    import numpy as np
    from PIL import Image

    from stage_1.pipeline import ProcessingCancelled
    from stage_3.metadata import extract_metadata

    pdf_path, output_path = Path(pdf_path), Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fitz_font = fitz.Font(fontfile=FONT_PATH)
    doc = fitz.open(str(pdf_path))
    by_page = {p["page"]: p for p in pages_data}
    engine = pages_data[0].get("engine", "chandra") if pages_data else "chandra"

    easy_reader = None
    if engine == "chandra":
        from stage_2.ocr_easy import get_reader
        easy_reader = get_reader()

    toc: list[list] = []
    review_pages: list[dict] = []
    page_errors: list[dict] = []
    review_rects: dict[int, list] = {}
    low_conf_words = 0
    fallback_used = 0
    total_pages = len(doc)

    for i in range(total_pages):
        if is_cancelled and is_cancelled():
            doc.close()
            raise ProcessingCancelled()

        page_num = i + 1
        page = doc[i]
        page_data = by_page.get(page_num)
        if not page_data or page_data.get("error"):
            if page_data and page_data.get("error"):
                page_errors.append({"page": page_num,
                                    "error": f"OCR: {page_data['error']}"})
            # даже пропущенная страница должна двигать прогресс, иначе при
            # сбое распознавания на всех страницах этап 3 навсегда 0%
            if on_progress:
                on_progress(int(page_num / total_pages * 100))
            continue

        try:
            page.insert_font(fontname=FONT_NAME, fontfile=FONT_PATH)

            w_px = page_data.get("width_px") or 1240
            h_px = page_data.get("height_px") or 1748
            px_to_pt = page.rect.width / w_px
            rects_here: list = []

            if engine == "easyocr":
                words = page_data.get("words", [])
                _insert_easy_words(page, words, px_to_pt, rects_here, fitz)
                low_conf_words += sum(1 for w in words
                                      if w[5] < WORD_CONF_THRESHOLD)
            else:
                sx_px = w_px / 1000.0
                sy_px = h_px / 1000.0

                pix = page.get_pixmap(dpi=int(settings.get("dpi", 150)))
                img = np.array(Image.frombytes(
                    "RGB", [pix.width, pix.height], pix.samples))
                if pix.width != w_px:
                    sx_px, sy_px = pix.width / 1000.0, pix.height / 1000.0
                    px_to_pt = page.rect.width / pix.width
                del pix

                results_full = easy_reader.readtext(
                    img, width_ths=0.001, paragraph=False,
                    min_size=3, text_threshold=0.4, low_text=0.3)

                blocks = [b for b in page_data.get("blocks", [])
                          if b["label"] != "Image" and b["content"].strip()]
                chandra_text = " ".join(b["content"] for b in blocks)
                easy_text = " ".join(r[1] for r in results_full)

                verdict = fb.assess_page(
                    chandra_text, easy_text, img,
                    threshold=AGREEMENT_THRESHOLD,
                    hard_floor=HARD_FLOOR,
                    use_tesseract=True,
                    tesseract_langs="rus+eng")
                if verdict["needs_review"]:
                    review_pages.append({"page": page_num,
                                         "agreement": verdict["agreement"],
                                         "verdict": verdict["verdict"]})

                for bbox_e, _text, conf in results_full:
                    if conf < WORD_CONF_THRESHOLD:
                        low_conf_words += 1
                        rects_here.append(fitz.Rect(
                            bbox_e[0][0] * px_to_pt, bbox_e[0][1] * px_to_pt,
                            bbox_e[2][0] * px_to_pt, bbox_e[2][1] * px_to_pt))

                if verdict["preferred"] == "easyocr":
                    fallback_used += 1
                    words = [[b[0][0], b[0][1], b[2][0], b[2][1], t, c]
                             for b, t, c in results_full]
                    _insert_easy_words(page, words, px_to_pt, [], fitz)
                else:
                    for block in blocks:
                        chandra_words = block["content"].split()
                        if not chandra_words:
                            continue
                        rows, bx1, by1, bx2, by2 = get_lines_in_block(
                            block, results_full, sx_px, sy_px)
                        if not rows:
                            fontsize = 8
                            x = bx1 * px_to_pt
                            cy = (by1 + by2) / 2 * px_to_pt
                            for word in chandra_words:
                                page.insert_text(
                                    fitz.Point(x, cy), word,
                                    fontname=FONT_NAME,
                                    fontsize=fontsize, render_mode=3)
                                x += fitz_font.text_length(
                                    word + " ", fontsize=fontsize)
                        else:
                            fontsize = calc_fontsize(chandra_words, rows,
                                                     px_to_pt, fitz_font)
                            word_boxes = get_word_boxes(rows, fontsize,
                                                        fitz_font, px_to_pt)
                            insert_words(chandra_words, word_boxes, rows,
                                         page, px_to_pt, fitz_font)

            if rects_here:
                review_rects[i] = rects_here

            # закладки по заголовкам (для обоих движков)
            for block in page_data.get("blocks", []):
                lvl = _header_level(block.get("label", ""))
                if lvl is not None and block.get("content", "").strip():
                    bbox = block.get("bbox", [0, 0, 0, 0])
                    toc.append([lvl, block["content"][:80],
                                page_num, bbox[3] - bbox[1]])
        except Exception as e:
            page_errors.append({"page": page_num, "error": str(e)})

        if on_progress:
            on_progress(int(page_num / total_pages * 100))

    # уровни закладок: заметно более мелкие заголовки — подразделы
    if toc:
        hh = sorted(h for *_, h in toc if h > 0)
        med_h = hh[len(hh) // 2] if hh else 0
        final_toc = []
        for lvl, title, pnum, row_h in toc:
            if med_h and row_h and row_h < med_h * 0.8 and lvl == 1:
                lvl = 2
            final_toc.append([lvl, title, pnum])
        doc.set_toc(sanitize_toc(final_toc))
        bookmarks = len(final_toc)
    else:
        bookmarks = 0

    if settings.get("two_page_view", True):
        if len(doc) % 2 == 0:
            add_blank_page(doc, "start")
        if len(doc) % 2 != 0:
            add_blank_page(doc, "end")

    meta = {"title": pdf_path.stem, "author": "", "year": ""}
    if settings.get("extract_metadata", True):
        meta = extract_metadata(pages_data[:3], fallback_title=pdf_path.stem)

    make_pdfa(doc, meta)
    doc.save(str(output_path), garbage=4, deflate=True)
    doc.close()

    finalize_pdf(output_path,
                 two_page_view=settings.get("two_page_view", True),
                 linearize=settings.get("linearize", True))

    review_pdf = ""
    if settings.get("make_review_pdf", True) and review_rects:
        review_path = output_path.with_name(output_path.stem + "_review.pdf")
        rdoc = fitz.open(str(output_path))
        for pidx, rects in review_rects.items():
            if pidx < len(rdoc):
                rpage = rdoc[pidx]
                for r in rects:
                    annot = rpage.add_highlight_annot(r)
                    annot.set_colors(stroke=(1, 0.85, 0.2))
                    annot.update()
        rdoc.save(str(review_path))
        rdoc.close()
        review_pdf = str(review_path)

    return {
        "pages": total_pages,
        "review_pages": review_pages,
        "low_conf_words": low_conf_words,
        "fallback_used": fallback_used,
        "page_errors": page_errors,
        "bookmarks": bookmarks,
        "review_pdf": review_pdf,
        "metadata": meta,
    }
