"""
metadata.py — извлечение метаданных (название, автор, год) с первых страниц.

Работает по блокам Chandra (этап 2 уже дал структуру страницы):
    - название: блоки с меткой Title/Header на первой странице,
      берётся самый крупный/верхний содержательный;
    - год: последнее правдоподобное четырёхзначное число (1800–2035)
      на первых двух страницах — на титуле год издания обычно внизу;
    - автор(ы): строки вида "Фамилия И.О." / "И.О. Фамилия" в верхней
      части первой страницы.

Если установлена библиотека natasha (опционально, extras [ner]) —
имена дополнительно проверяются NER-моделью, это снижает ложные
срабатывания на названиях городов и издательств. Без natasha работает
чисто на эвристиках — деградация мягкая.

Результат пишется в XMP (dc:title, dc:creator, dc:date) и в docinfo PDF,
что автозаполняет карточку документа при каталогизации.
"""

from __future__ import annotations

import re

_YEAR_RE = re.compile(r"\b(1[89]\d{2}|20[0-3]\d)\b")
# "Иванов И.И." | "И.И. Иванов" | "Иванов И. И."
_AUTHOR_RE = re.compile(
    r"\b([А-ЯЁ][а-яё]+)\s+([А-ЯЁ]\.\s?[А-ЯЁ]\.)"      # Фамилия И.О.
    r"|([А-ЯЁ]\.\s?[А-ЯЁ]\.)\s+([А-ЯЁ][а-яё]+)\b"     # И.О. Фамилия
)

_TITLE_LABELS = ("title", "header", "heading", "section")
_STOP_TITLE = re.compile(
    r"министерство|федеральн|университет|институт|академи|isbn|удк|ббк",
    re.IGNORECASE,
)


def _try_natasha_persons(text: str) -> list[str] | None:
    """NER через natasha, если установлена. None = библиотека недоступна."""
    try:
        from natasha import (Doc, MorphVocab, NewsEmbedding,  # type: ignore
                             NewsNERTagger, Segmenter)
    except ImportError:
        return None
    try:
        seg = Segmenter()
        emb = NewsEmbedding()
        tagger = NewsNERTagger(emb)
        doc = Doc(text)
        doc.segment(seg)
        doc.tag_ner(tagger)
        return [s.text for s in doc.spans if s.type == "PER"]
    except Exception:
        return None


def extract_metadata(pages: list[dict], fallback_title: str) -> dict:
    """
    Args:
        pages: первые 1–3 страницы из JSON этапа 2 (ключи blocks, markdown)
        fallback_title: имя папки книги — если название не нашли

    Returns:
        {"title": str, "author": str, "year": str} (пустые строки если не найдено)
    """
    title, author, year = "", "", ""
    if not pages:
        return {"title": fallback_title, "author": "", "year": ""}

    first = pages[0]
    blocks = first.get("blocks", [])

    # ── название: title-подобные блоки первой страницы, без "шапки" вуза ──
    candidates = []
    for b in blocks:
        label = str(b.get("label", "")).lower()
        content = (b.get("content") or "").strip()
        if not content or len(content) < 4:
            continue
        if any(t in label for t in _TITLE_LABELS) and not _STOP_TITLE.search(content):
            y_top = b.get("bbox", [0, 0, 0, 0])[1]
            candidates.append((y_top, content))
    if candidates:
        candidates.sort(key=lambda c: c[0])
        title = max((c[1] for c in candidates[:4]), key=len)[:200]
    if not title:
        title = fallback_title

    # ── год: по первым двум страницам, берём последний найденный ──
    text12 = " ".join(p.get("markdown", "") or "" for p in pages[:2])
    years = _YEAR_RE.findall(text12)
    if years:
        year = years[-1]

    # ── автор: верхняя треть первой страницы, БЕЗ title-блоков —
    #    иначе на стыке "…России" + "И.И. Иванов" regex ловит ложное
    #    "России И.И." ──
    top_text_parts = []
    for b in blocks:
        content = (b.get("content") or "").strip()
        label = str(b.get("label", "")).lower()
        bbox = b.get("bbox", [0, 0, 0, 0])
        if any(t in label for t in _TITLE_LABELS) or content == title:
            continue
        # координаты Chandra нормированы 0–1000
        if content and bbox[1] <= 400:
            top_text_parts.append(content)
    top_text = " ".join(top_text_parts) or (first.get("markdown", "") or "")[:1000]

    title_words = {w.lower() for w in re.findall(r"[А-ЯЁа-яё]+", title)}
    m_authors = []
    for m in _AUTHOR_RE.finditer(top_text):
        g = m.groups()
        surname = g[0] if g[0] else g[3]
        if surname.lower() in title_words:
            continue  # "России И.И." и подобные ложные срабатывания
        name = f"{g[0]} {g[1]}" if g[0] else f"{g[3]} {g[2]}"
        m_authors.append(re.sub(r"\s+", " ", name))

    ner_persons = _try_natasha_persons(top_text)
    if ner_persons is not None and m_authors:
        # natasha доступна — оставляем только подтверждённые NER фамилии
        confirmed = [a for a in m_authors
                     if any(a.split()[0] in p for p in ner_persons)]
        m_authors = confirmed or m_authors

    if m_authors:
        author = "; ".join(dict.fromkeys(m_authors))[:200]

    return {"title": title, "author": author, "year": year}
