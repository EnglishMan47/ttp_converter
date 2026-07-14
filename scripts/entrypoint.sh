#!/bin/bash
# Точка входа контейнера.
#
# Веб-сервер запускается СРАЗУ, а тяжёлая подготовка (runtime движка и
# веса моделей) идёт в фоне: пока модели скачиваются, страница показывает
# статус подготовки, а не «сервер недоступен». Все загрузки одноразовые —
# результаты сохраняются в постоянных томах и при следующих стартах
# пропускаются:
#   1) runtime нейросетевого движка (llama-mtmd-cli): с видеокартой
#      NVIDIA — CUDA-вариант, без неё — готовая CPU-сборка (том neural);
#   2) веса модели Chandra, ~4.7 ГБ (том models-cache);
#   3) модели лёгкого движка EasyOCR, ~94 МБ (том models-cache).
#
# Источники весов Chandra задаёт MODELS_ENDPOINTS (по умолчанию сначала
# зеркало hf-mirror.com — доступно в РФ без VPN, затем официальный
# huggingface.co). Скрипт пробует их по очереди, пока загрузка не пойдёт.
set -u

NEURAL_DIR="${NEURAL_DIR:-/neural}"
mkdir -p "$NEURAL_DIR"

# файлы статуса подготовки — их читает веб-интерфейс (страница подготовки)
PREP_STATUS="$NEURAL_DIR/prepare.status"
PREP_DONE="$NEURAL_DIR/prepare.done"
export TIFF2PDF_PREP_STATUS="$PREP_STATUS"
export TIFF2PDF_PREP_DONE="$PREP_DONE"

MODELS_ENDPOINTS="${MODELS_ENDPOINTS:-https://hf-mirror.com https://huggingface.co}"

set_status() { printf '%s\n' "$1" > "$PREP_STATUS"; echo "[запуск] $1"; }

gpu_present() {
    command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1
}

install_neural() {   # $1 = cuda | cpu
    if [ -x "$NEURAL_DIR/bin/llama-mtmd-cli" ]; then
        echo "[запуск] Runtime нейросетевого движка найден в томе."
        return 0
    fi
    set_status "Устанавливается нейросетевой движок ($1)…"
    if bash /srv/scripts/neural_setup.sh "$NEURAL_DIR" "$1"; then
        echo "[запуск] Runtime установлен в том neural."
    else
        echo "[запуск] ВНИМАНИЕ: runtime нейросетевого движка не установился"
        echo "[запуск] (лёгкий движок работает). Повтор — при следующем старте."
        return 1
    fi
}

download_weight() {   # $1 = имя файла в репозитории HF
    local f="$1" ep
    [ -s "$MODELS_DIR/$f" ] && return 0
    for ep in $MODELS_ENDPOINTS; do
        set_status "Скачивается модель распознавания: $f (источник: $ep)…"
        # --connect-timeout и --speed-limit заставляют быстро перейти к
        # следующему источнику, если этот недоступен или загрузка не идёт;
        # -C - продолжает начатый файл, не начиная заново
        if curl -fL -C - \
                --retry 3 --retry-delay 3 --retry-all-errors \
                --connect-timeout 20 --speed-limit 2048 --speed-time 30 \
                -o "$MODELS_DIR/$f.part" \
                "$ep/$HF_REPO/resolve/main/$f"; then
            mv "$MODELS_DIR/$f.part" "$MODELS_DIR/$f"
            echo "[запуск] $f скачан."
            return 0
        fi
        echo "[запуск] Источник $ep не сработал для $f — пробую следующий…"
    done
    return 1
}

prepare() {
    # ── 1. runtime нейросетевого движка ──
    if gpu_present; then
        echo "[запуск] Обнаружена видеокарта NVIDIA — ставится CUDA-вариант движка."
        install_neural cuda || true
        if ! python3 - <<'PY'
import sys
try:
    import torch
    sys.exit(0 if torch.version.cuda else 1)
except Exception:
    sys.exit(1)
PY
        then
            set_status "Установка Torch (CUDA)…"
            pip install --no-cache-dir torch torchvision \
                --index-url https://download.pytorch.org/whl/cu132 \
                || echo "[запуск] Torch(CUDA) не установился — распознавание останется на CPU."
        fi
    else
        echo "[запуск] Видеокарта NVIDIA не обнаружена — ставится CPU-вариант движка."
        echo "[запуск] (на процессоре нейросетевой движок работает медленно:"
        echo "[запуск] порядка минут на страницу; лёгкий движок быстрый)"
        install_neural cpu || true
    fi

    # ── 2. веса модели Chandra ──
    eval "$(python3 - <<'PY'
import sys
sys.path.insert(0, "/srv/app")
from stage_2 import ocr_chandra as c
print(f'MODELS_DIR="{c.MODELS_DIR}"')
print(f'HF_REPO="{c.HF_REPO}"')
print(f'MODEL_FILE="{c.MODEL_FILE}"')
print(f'MMPROJ_FILE="{c.MMPROJ_FILE}"')
PY
)"
    mkdir -p "$MODELS_DIR"
    weights_ok=1
    for f in "$MODEL_FILE" "$MMPROJ_FILE"; do
        download_weight "$f" || { weights_ok=0; echo "[запуск] ВНИМАНИЕ: $f не скачался."; }
    done
    [ "$weights_ok" = "1" ] && echo "[запуск] Веса Chandra на месте: $MODELS_DIR"

    # ── 3. лёгкий движок EasyOCR (модели встроены в образ, не качаются) ──
    set_status "Проверка лёгкого движка EasyOCR…"
    python3 - <<'PY' || echo "[запуск] ВНИМАНИЕ: лёгкий движок не инициализировался."
import sys
sys.path.insert(0, "/srv/app")
from stage_2.ocr_easy import get_reader
get_reader()
print("[запуск] Лёгкий движок EasyOCR готов (модели встроены в образ).")
PY

    # ── готово ──
    if [ "$weights_ok" = "1" ]; then
        set_status "Готово"
    else
        set_status "Готово (нейросетевой движок недоступен, работает лёгкий)"
    fi
    : > "$PREP_DONE"
    echo ""
    echo "======================================================================"
    echo "  Всё готово! Откройте веб-интерфейс в браузере:"
    echo ""
    echo "      http://localhost:8501           — с этого компьютера"
    echo "      http://<адрес-сервера>:8501     — с других компьютеров сети"
    echo "======================================================================"
    echo ""
}

# Пути к движку экспортируем СРАЗУ (том neural), чтобы веб-сервер увидел
# бинарник и библиотеки, как только фоновая установка их положит.
export PATH="$NEURAL_DIR/bin:$PATH"
export LD_LIBRARY_PATH="$NEURAL_DIR/lib:${LD_LIBRARY_PATH:-}"
export LLAMA_MTMD_BIN="$NEURAL_DIR/bin/llama-mtmd-cli"

rm -f "$PREP_DONE"
set_status "Идёт первоначальная подготовка…"

echo "[запуск] Веб-сервер запускается; подготовка моделей идёт в фоне."
prepare &

exec streamlit run app/web.py \
    --server.port 8501 --server.address 0.0.0.0 \
    --server.maxUploadSize 2048 \
    --browser.gatherUsageStats false
