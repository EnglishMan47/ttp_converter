"""
jsonl_store.py — общий формат чекпоинтов распознавания.

Оба движка пишут результат построчно (одна строка JSONL = одна
страница) с принудительным сбросом на диск: после сбоя или остановки
обработка продолжается с первой нераспознанной страницы. Модуль
не зависит ни от одного из движков.
"""

from __future__ import annotations

import json
from pathlib import Path


def done_pages(jsonl_path: Path) -> set[int]:
    """Номера страниц, уже записанных в JSONL (для продолжения)."""
    done = set()
    if Path(jsonl_path).exists():
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    done.add(json.loads(line)["page"])
                except (json.JSONDecodeError, KeyError):
                    continue  # оборванная строка после аварийного завершения
    return done


def load_pages(jsonl_path: Path) -> list[dict]:
    """Читает JSONL в список страниц, отсортированный по номеру."""
    pages, seen = [], set()
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                p = json.loads(line)
            except json.JSONDecodeError:
                continue
            if p["page"] not in seen:
                seen.add(p["page"])
                pages.append(p)
    pages.sort(key=lambda p: p["page"])
    return pages
