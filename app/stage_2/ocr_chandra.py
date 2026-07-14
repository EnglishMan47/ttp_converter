"""
ocr_chandra.py — нейросетевой движок: квантованная модель Chandra (GGUF).

Используется квантованная версия Q4_K_M (~3 ГБ) вместо полной BF16
(~10 ГБ): качество при этом почти не страдает, а требования к
видеопамяти снижаются втрое. Инференс выполняет
утилита llama-mtmd-cli из llama.cpp (собирается в Docker-образе);
других вариантов нейросетевого движка в системе нет.

Файлы модели скачиваются ЛЕНИВО — только при первом реальном
использовании нейросетевого движка. Выбор лёгкого движка не приводит
ни к каким обращениям к весам Chandra.

Источник весов (Hugging Face):
    prithivMLmods/chandra-ocr-2-GGUF
        chandra-ocr-2.Q4_K_M.gguf        — веса модели
        chandra-ocr-2.mmproj-bf16.gguf   — визуальный проектор

Результат пишется построчно в JSONL (одна строка = одна страница) с
принудительным сбросом на диск: после сбоя обработка продолжается с
первой нераспознанной страницы.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from .jsonl_store import done_pages, load_pages  # noqa: F401

HF_REPO = "prithivMLmods/chandra-ocr-2-GGUF"
MODEL_FILE = "chandra-ocr-2.Q4_K_M.gguf"
MMPROJ_FILE = "chandra-ocr-2.mmproj-bf16.gguf"

# локальная установка движка внутри проекта: папка neural/ (на Windows её
# создаёт scripts/setup_neural_windows.ps1; может быть junction на другой
# диск). В Docker-образе этой папки нет — там работает том /neural.
_PROJECT_NEURAL = Path(__file__).resolve().parents[2] / "neural"


# путь без папки neural/ (а также «наследие»: веса, скачанные до того,
# как папка neural появилась, лежат здесь)
_HOME_MODELS_DIR = Path.home() / ".cache" / "tiff2pdf" / "models"


def _default_models_dir() -> Path:
    if _PROJECT_NEURAL.is_dir():
        return _PROJECT_NEURAL / "models"
    return _HOME_MODELS_DIR


MODELS_DIR = Path(os.environ.get("CHANDRA_MODELS_DIR")
                  or _default_models_dir())


def find_llama_bin() -> str | None:
    """
    Путь к llama-mtmd-cli или None.

    Порядок поиска: переменная LLAMA_MTMD_BIN (имя в PATH или полный
    путь) → PATH → локальная установка в neural/ проекта → том
    /neural Docker-контейнера.
    """
    env_bin = os.environ.get("LLAMA_MTMD_BIN")
    if env_bin:
        found = shutil.which(env_bin)
        if found:
            return found
        if Path(env_bin).is_file():
            return env_bin
        return None                      # задан явно, но не найден
    found = shutil.which("llama-mtmd-cli")
    if found:
        return found
    exe = "llama-mtmd-cli.exe" if os.name == "nt" else "llama-mtmd-cli"
    for cand in (_PROJECT_NEURAL / "llama.cpp" / exe,
                 _PROJECT_NEURAL / "bin" / exe,
                 Path("/neural/bin") / exe):
        if cand.is_file():
            return str(cand)
    return None

# Промпт режима ocr_layout: модель обучена выдавать блоки страницы в виде
# <div data-bbox="x1 y1 x2 y2" data-label="...">текст</div> с координатами,
# нормированными к 0–1000.
OCR_LAYOUT_PROMPT = (
    "OCR this page. Output the full text with layout structure as HTML "
    "divs. Each block must be wrapped as "
    '<div data-bbox="x1 y1 x2 y2" data-label="LABEL">text</div> '
    "where coordinates are normalized to a 0-1000 grid and LABEL is one "
    "of: Title, Section-header, Text, List-item, Table, Image, Caption, "
    "Footnote, Page-header, Page-footer. Preserve reading order."
)


class ChandraUnavailable(RuntimeError):
    """Нейросетевой движок недоступен; текст ошибки объясняет причину."""


def _check_runtime() -> str:
    """Возвращает путь к llama-mtmd-cli или бросает понятную ошибку."""
    path = find_llama_bin()
    if not path:
        hint = ("На Windows установите его скриптом "
                "scripts\\setup_neural_windows.ps1"
                if os.name == "nt" else
                "В Docker-образе проекта она устанавливается автоматически")
        raise ChandraUnavailable(
            f"Не найдена утилита llama-mtmd-cli (llama.cpp) — runtime "
            f"нейросетевого движка. {hint}, либо укажите путь к утилите "
            f"в переменной LLAMA_MTMD_BIN. Пока её нет, выберите лёгкий "
            f"движок.")
    return path


# примерные размеры весов (для статуса загрузки); точность не критична
_APPROX_BYTES = {MODEL_FILE: 3_066_000_000, MMPROJ_FILE: 676_000_000}


def _dir_bytes(root: Path) -> int:
    total = 0
    for p in root.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue                 # временный файл успел исчезнуть
    return total


def ensure_models(on_progress=None) -> tuple[Path, Path]:
    """
    Ленивая загрузка весов: скачивает Q4_K_M и mmproj при первом
    использовании. Повторные вызовы берут файлы из кэша.

    on_progress — колбэк со строкой статуса ("" — загрузка окончена).
    """
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    model_p = MODELS_DIR / MODEL_FILE
    mmproj_p = MODELS_DIR / MMPROJ_FILE

    # веса могли скачаться в домашний кэш, пока папки neural ещё не было
    # (MODELS_DIR выбирается при импорте) — забираем их переносом, чтобы
    # не качать несколько гигабайт повторно
    if MODELS_DIR != _HOME_MODELS_DIR:
        for fname in (MODEL_FILE, MMPROJ_FILE):
            src, dst = _HOME_MODELS_DIR / fname, MODELS_DIR / fname
            if src.is_file() and not dst.exists():
                try:
                    shutil.move(str(src), str(dst))
                except OSError:
                    pass             # файл занят — скачается заново

    missing = [f for f, p in ((MODEL_FILE, model_p), (MMPROJ_FILE, mmproj_p))
               if not p.exists()]
    if missing:
        # huggingface_hub (бэкенд xet) кэширует чанки в HF_HOME на
        # системном диске даже при local_dir на другом диске — при
        # больших весах это забивает диск C:. Держим кэш рядом с моделями.
        os.environ.setdefault("HF_HOME", str(MODELS_DIR / ".hf_cache"))
        try:
            from huggingface_hub import hf_hub_download
        except ImportError as e:
            raise ChandraUnavailable(
                "Пакет huggingface_hub не установлен — нечем скачать веса "
                "модели.") from e

        # фоновый опрос размера папки — статус «сколько скачано» для
        # карточки книги; иначе первый запуск выглядит зависшим
        stop = threading.Event()
        if on_progress:
            base = _dir_bytes(MODELS_DIR)
            need = sum(_APPROX_BYTES[f] for f in missing)

            def _watch():
                while not stop.wait(3.0):
                    got = max(0, min(_dir_bytes(MODELS_DIR) - base, need))
                    on_progress(
                        f"Скачиваются веса модели (первый запуск): "
                        f"{got // 2**20} МБ из ~{need // 2**20} МБ")

            threading.Thread(target=_watch, daemon=True).start()
        try:
            for fname in missing:
                hf_hub_download(repo_id=HF_REPO, filename=fname,
                                local_dir=str(MODELS_DIR))
        finally:
            stop.set()
            if on_progress:
                on_progress("")
    return model_p, mmproj_p


_BLOCK_RE = re.compile(
    r'<div\s+data-bbox="([^"]+)"\s+data-label="([^"]+)">(.*?)</div>',
    re.DOTALL,
)


def parse_raw_blocks(raw: str) -> list[dict]:
    """Блоки страницы из HTML-вывода модели: bbox (0–1000), метка, текст."""
    blocks = []
    for m in _BLOCK_RE.finditer(raw):
        try:
            bbox = [int(float(x)) for x in m.group(1).split()]
        except ValueError:
            continue
        content = re.sub(r"<[^>]+>", " ", m.group(3)).strip()
        content = re.sub(r"\s+", " ", content)
        if bbox and content:
            blocks.append({"bbox": bbox, "label": m.group(2),
                           "content": content})
    return blocks


def _markdown_from_blocks(blocks: list[dict]) -> str:
    return " ".join(b["content"] for b in blocks
                    if b["label"] not in ("Image",))




class OutOfMemory(RuntimeError):
    """Движку не хватило оперативной памяти."""


def _run_page(bin_path: str, model: Path, mmproj: Path,
              image_path: Path) -> str:
    """Один проход модели по изображению страницы."""
    cmd = [bin_path, "-m", str(model), "--mmproj", str(mmproj),
           "--image", str(image_path), "-p", OCR_LAYOUT_PROMPT,
           "--temp", "0", "-n", "4096"]
    # encoding обязателен: на Windows text=True по умолчанию берёт
    # локальную кодировку (cp1251) и портит русский вывод модели
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600)
    if proc.returncode != 0:
        err = proc.stderr.strip()
        low = err.lower()
        # признаки реально битого/неполного файла модели
        if any(s in low for s in ("invalid magic", "unexpected eof",
                                  "wrong number of tensors", "not a valid",
                                  "no such file")):
            raise RuntimeError(
                "не удалось прочитать файл модели (повреждён или скачан не "
                "полностью). Перезапустите контейнер, чтобы перекачать "
                "модель. Подробности: " + err[-200:])
        # вычислительный бэкенд ggml не загрузился: установка llama.cpp
        # неполная (нет DLL бэкендов рядом с утилитой) — память ни при чём
        if "ggml_backend_load" in low or "no backends loaded" in low:
            raise ChandraUnavailable(
                "runtime нейросетевого движка не смог загрузить "
                "вычислительный бэкенд ggml — установка llama.cpp неполная "
                "или повреждена. На Windows переустановите её скриптом "
                "scripts\\setup_neural_windows.ps1, в Docker пересоздайте "
                "том neural. Подробности: " + err[-200:])
        # целостность модели проверена до цикла, поэтому почти любой другой
        # сбой запуска на CPU — это нехватка оперативной памяти
        raise OutOfMemory(
            "нейросетевому движку не хватило оперативной памяти. Увеличьте "
            "память Docker (Settings → Resources → Memory) до 10–12 ГБ или "
            "выберите лёгкий движок. Подробности: " + err[-200:])
    return proc.stdout


def run_ocr(pdf_path: Path,
            output_jsonl: Path,
            dpi: int = 150,
            batch_size: int = 1,
            on_progress=None,
            on_status=None,
            is_cancelled=None) -> dict:
    """
    Returns:
        {"pages": int, "resumed_from": int, "page_errors": [...]}

    on_status — колбэк со строкой статуса (загрузка весов и т.п.).
    Параметр batch_size сохранён для совместимости настроек; текущий
    runtime обрабатывает страницы по одной.
    """
    import fitz
    from PIL import Image

    from stage_1.pipeline import ProcessingCancelled

    bin_path = _check_runtime()
    model_p, mmproj_p = ensure_models(on_progress=on_status)

    # защита от неполной загрузки: Q4_K_M ~3.07 ГБ, проектор ~0.68 ГБ;
    # заметно меньший файл — обрыв закачки, иначе llama падает загадочно
    for p, min_bytes in ((model_p, 2_700_000_000), (mmproj_p, 500_000_000)):
        if p.stat().st_size < min_bytes:
            raise ChandraUnavailable(
                f"файл модели {p.name} скачан не полностью "
                f"({p.stat().st_size // 2**20} МБ). Перезапустите контейнер, "
                f"чтобы докачать модель, затем повторите обработку.")

    pdf_path = Path(pdf_path)
    output_jsonl = Path(output_jsonl)
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(pdf_path))
    total = len(doc)
    done = done_pages(output_jsonl)
    todo = [i for i in range(total) if (i + 1) not in done]
    page_errors: list[dict] = []

    tmp_dir = Path(tempfile.mkdtemp(prefix="chandra_pages_"))
    try:
        with open(output_jsonl, "a", encoding="utf-8") as out:
            for pos, i in enumerate(todo):
                if is_cancelled and is_cancelled():
                    doc.close()
                    raise ProcessingCancelled()

                pix = doc[i].get_pixmap(dpi=dpi)
                w_px, h_px = pix.width, pix.height
                img_path = tmp_dir / f"p{i+1}.png"
                Image.frombytes("RGB", [w_px, h_px], pix.samples
                                ).save(img_path)
                del pix

                try:
                    raw = _run_page(bin_path, model_p, mmproj_p, img_path)
                    blocks = parse_raw_blocks(raw)
                    record = {"page": i + 1, "width_px": w_px,
                              "height_px": h_px, "engine": "chandra",
                              "markdown": _markdown_from_blocks(blocks),
                              "blocks": blocks, "error": ""}
                except (OutOfMemory, ChandraUnavailable) as e:
                    # с остальными страницами будет то же самое — нет смысла
                    # гонять их по одной; сразу останавливаемся с ясной ошибкой
                    img_path.unlink(missing_ok=True)
                    doc.close()
                    raise ChandraUnavailable(str(e)) from e
                except Exception as e:
                    record = {"page": i + 1, "width_px": w_px,
                              "height_px": h_px, "engine": "chandra",
                              "markdown": "", "blocks": [],
                              "error": str(e)}
                    page_errors.append({"page": i + 1, "error": str(e)})

                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                out.flush()
                img_path.unlink(missing_ok=True)

                if on_progress:
                    on_progress(int((len(done) + pos + 1) / total * 100))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    doc.close()
    return {"pages": total, "resumed_from": len(done),
            "page_errors": page_errors}
