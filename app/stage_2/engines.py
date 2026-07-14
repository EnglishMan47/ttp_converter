"""
engines.py — выбор движка распознавания под возможности компьютера.

Система поддерживает два движка:

    chandra — нейросетевая модель с пониманием структуры страницы
              (заголовки, абзацы, таблицы). Используется квантованная
              версия Q4_K_M (~3 ГБ, качество почти как у полной):
              достаточно видеокарты NVIDIA от 6 ГБ видеопамяти либо
              12+ ГБ ОЗУ (на процессоре — медленно).

    easyocr — лёгкие модели детекции и распознавания (порядка сотни
              мегабайт). Работает на любом компьютере от 8 ГБ ОЗУ,
              видеокарта не обязательна. Координаты слов даёт напрямую,
              что упрощает построение текстового слоя.

В режиме «auto» движок выбирается автоматически: chandra — только если
железо заведомо тянет, иначе easyocr. Это позволяет одной и той же
программе работать и на офисном ноутбуке, и на рабочей станции с GPU.
"""

from __future__ import annotations

MIN_VRAM_GB = 6      # квантованная Q4_K_M занимает ~3 ГБ + контекст
MIN_RAM_GB_FOR_CPU = 12  # GGUF работает и на CPU (медленно)


def hardware_summary() -> dict:
    """Сведения о доступном железе (для окна настроек и логов)."""
    info = {"cuda": False, "gpu_name": "", "vram_gb": 0.0, "ram_gb": 0.0}
    try:
        import psutil
        info["ram_gb"] = round(psutil.virtual_memory().total / 2**30, 1)
    except Exception:
        pass
    try:
        import torch
        if torch.cuda.is_available():
            info["cuda"] = True
            info["gpu_name"] = torch.cuda.get_device_name(0)
            props = torch.cuda.get_device_properties(0)
            info["vram_gb"] = round(props.total_memory / 2**30, 1)
    except Exception:
        pass
    return info


def chandra_runtime_available() -> bool:
    """Установлена ли утилита инференса llama-mtmd-cli (llama.cpp)."""
    from stage_2.ocr_chandra import find_llama_bin
    return find_llama_bin() is not None


def resolve_engine(preferred: str = "auto") -> tuple[str, str]:
    """
    Args:
        preferred: "auto" | "chandra" | "easyocr"

    Returns:
        (движок, пояснение для пользователя)
    """
    if preferred == "chandra":
        return "chandra", ("Выбран вручную: нейросетевой движок. Без "
                           "видеокарты работает на процессоре — медленно.")
    if preferred == "easyocr":
        return "easyocr", "Выбран вручную: лёгкий движок."

    hw = hardware_summary()
    runtime = chandra_runtime_available()
    if runtime and hw["cuda"] and hw["vram_gb"] >= MIN_VRAM_GB:
        return "chandra", (f"Найдена видеокарта {hw['gpu_name']} "
                           f"({hw['vram_gb']} ГБ) — используется "
                           f"нейросетевой движок (квантованная модель).")
    if runtime and hw["ram_gb"] >= MIN_RAM_GB_FOR_CPU:
        return "chandra", (f"Оперативной памяти {hw['ram_gb']} ГБ достаточно "
                           f"для нейросетевого движка (без видеокарты будет "
                           f"медленно).")
    if not runtime:
        return "easyocr", ("Компоненты нейросетевого движка (llama-mtmd-cli) "
                           "не установлены — используется лёгкий движок. "
                           "Установка на Windows: "
                           "scripts\\setup_neural_windows.ps1; в Docker-"
                           "образе проекта ставится автоматически.")
    return "easyocr", (f"Видеокарта NVIDIA с {MIN_VRAM_GB}+ ГБ не найдена, "
                       f"ОЗУ {hw['ram_gb']} ГБ — используется лёгкий движок. "
                       f"Качество распознавания хорошее, структура страницы "
                       f"определяется эвристически.")


def gpu_available() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False
