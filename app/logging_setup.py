"""
logging_setup.py — единая настройка логирования проекта.

Логи пишутся в файлы с ротацией в каталог logs/ (в корне проекта) и
дублируются в stdout, поэтому видны и в `docker logs`. Каталог можно
переопределить переменной окружения TIFF2PDF_LOG_DIR; по умолчанию это
папка logs/ рядом с app/. Папка logs/ добавлена в .gitignore — сами
логи в репозиторий не попадают.

Использование в модулях:
    from logging_setup import get_logger
    log = get_logger("chandra")
    log.info("…")

Ценность: сюда пишется ПОЛНЫЙ вывод внешних утилит (например, весь
stderr llama-mtmd-cli) и трассировки ошибок обработки — то, чего не
видно в коротком сообщении на карточке книги.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_ROOT_NAME = "tiff2pdf"
_configured = False


def log_dir() -> Path:
    env = os.environ.get("TIFF2PDF_LOG_DIR")
    if env:
        return Path(env)
    # app/logging_setup.py → корень проекта на уровень выше app/
    return Path(__file__).resolve().parent.parent / "logs"


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Настраивает корневой логгер проекта (идемпотентно)."""
    global _configured
    root = logging.getLogger(_ROOT_NAME)
    if _configured:
        return root

    root.setLevel(level)
    root.propagate = False
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S")

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    stream.setLevel(level)
    root.addHandler(stream)

    try:
        d = log_dir()
        d.mkdir(parents=True, exist_ok=True)
        app_h = RotatingFileHandler(
            d / "app.log", maxBytes=5_000_000, backupCount=3,
            encoding="utf-8")
        app_h.setFormatter(fmt)
        app_h.setLevel(level)
        root.addHandler(app_h)

        err_h = RotatingFileHandler(
            d / "errors.log", maxBytes=2_000_000, backupCount=3,
            encoding="utf-8")
        err_h.setFormatter(fmt)
        err_h.setLevel(logging.ERROR)
        root.addHandler(err_h)
    except OSError:
        # каталог логов недоступен (права/только чтение) — работаем в stdout
        root.warning("Не удалось создать файлы логов — логи только в stdout")

    _configured = True
    return root


def get_logger(name: str) -> logging.Logger:
    """Дочерний логгер проекта; настраивает логирование при первом вызове."""
    setup_logging()
    return logging.getLogger(_ROOT_NAME).getChild(name)
