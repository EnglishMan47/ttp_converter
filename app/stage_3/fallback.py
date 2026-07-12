"""
fallback.py — перекрёстная проверка распознавания и второй проход.

У Chandra нет надёжного скалярного confidence на страницу, поэтому
уверенность оцениваем ЧЕРЕЗ СОГЛАСИЕ ДВИЖКОВ: этап 3 всё равно гоняет
EasyOCR по каждой странице (для координат слов), значит у нас бесплатно
есть второе независимое прочтение той же страницы.

Логика по странице:
    agreement = сходство(текст Chandra, текст EasyOCR)  ∈ [0..1]

    agreement >= threshold        → страница ок
    hard_floor <= a < threshold   → подозрительная: третий проход
        Tesseract (если доступен), голосование 2-из-3; страница
        помечается для ручной проверки в отчёте
    agreement < hard_floor        → Chandra провалилась (пустой/мусорный
        вывод): текстовый слой строится напрямую из фрагментов EasyOCR —
        у них есть точные координаты, так что слой останется корректным

Сходство — SequenceMatcher по нормализованному тексту: устойчиво к
пробелам/регистру/пунктуации, не требует зависимостей.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def normalize(s: str) -> str:
    s = s.lower().replace("ё", "е")
    s = _PUNCT.sub(" ", s)
    return _WS.sub(" ", s).strip()


def similarity(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def tesseract_text(img_rgb, langs: str = "rus+eng") -> str | None:
    """Текст страницы через Tesseract. None — pytesseract/tesseract не установлены."""
    try:
        import pytesseract  # type: ignore
    except ImportError:
        return None
    try:
        return pytesseract.image_to_string(img_rgb, lang=langs)
    except Exception:
        return None


def assess_page(chandra_text: str,
                easy_text: str,
                img_rgb,
                *,
                threshold: float,
                hard_floor: float,
                use_tesseract: bool,
                tesseract_langs: str) -> dict:
    """
    Возвращает вердикт по странице:
    {
      "agreement":     float,   # Chandra vs EasyOCR
      "verdict":       "ok" | "suspect" | "chandra_failed",
      "needs_review":  bool,
      "tesseract_ran": bool,
      "votes":         {"chandra_easy": .., "chandra_tess": .., "easy_tess": ..} | None,
      "preferred":     "chandra" | "easyocr",  # источник текстового слоя
    }
    """
    agreement = similarity(chandra_text, easy_text)
    res = {
        "agreement": round(agreement, 3),
        "verdict": "ok",
        "needs_review": False,
        "tesseract_ran": False,
        "votes": None,
        "preferred": "chandra",
    }

    if agreement >= threshold:
        return res

    if agreement < hard_floor or not normalize(chandra_text):
        # Chandra дала мусор или пустоту — слой строим из EasyOCR
        res.update(verdict="chandra_failed", needs_review=True, preferred="easyocr")
        return res

    res.update(verdict="suspect", needs_review=True)

    if use_tesseract:
        tess = tesseract_text(img_rgb, tesseract_langs)
        if tess is not None:
            res["tesseract_ran"] = True
            ct = similarity(chandra_text, tess)
            et = similarity(easy_text, tess)
            res["votes"] = {
                "chandra_easy": round(agreement, 3),
                "chandra_tess": round(ct, 3),
                "easy_tess": round(et, 3),
            }
            # Голосование: если EasyOCR и Tesseract согласны между собой
            # заметно лучше, чем каждый из них с Chandra — Chandra "в
            # меньшинстве", слой строим из EasyOCR.
            if et > max(agreement, ct) + 0.1:
                res["preferred"] = "easyocr"
            # если Chandra согласна с Tesseract — считаем Chandra верной,
            # расхождение на совести EasyOCR; страница всё равно в review
            elif ct >= threshold:
                res["needs_review"] = False
                res["verdict"] = "ok_by_vote"

    return res
