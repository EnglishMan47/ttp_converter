#!/bin/bash
# Точка входа контейнера.
#
# При каждом старте контейнер доводит себя до полностью готового
# состояния. Все загрузки одноразовые — результаты сохраняются в
# постоянных томах и при следующих стартах пропускаются:
#   1) runtime нейросетевого движка (llama-mtmd-cli): с видеокартой
#      NVIDIA ставится CUDA-вариант, без неё — готовая CPU-сборка
#      llama.cpp (том neural);
#   2) веса модели Chandra, ~4.7 ГБ (том models-cache);
#   3) модели лёгкого движка EasyOCR, ~94 МБ (том models-cache).
# После подготовки печатается адрес веб-интерфейса и запускается сервер.
set -u

NEURAL_DIR="${NEURAL_DIR:-/neural}"
mkdir -p "$NEURAL_DIR"

gpu_present() {
    command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1
}

install_neural() {   # $1 = cuda | cpu
    if [ ! -x "$NEURAL_DIR/bin/llama-mtmd-cli" ]; then
        echo "[запуск] Установка runtime нейросетевого движка ($1)…"
        if bash /srv/scripts/neural_setup.sh "$NEURAL_DIR" "$1"; then
            echo "[запуск] Runtime установлен в том neural."
        else
            echo "[запуск] ВНИМАНИЕ: установка runtime не удалась — нейросетевой"
            echo "[запуск] движок будет недоступен (лёгкий работает). Повтор — при следующем старте."
            return 1
        fi
    else
        echo "[запуск] Runtime нейросетевого движка найден в томе."
    fi
    export PATH="$NEURAL_DIR/bin:$PATH"
    export LD_LIBRARY_PATH="$NEURAL_DIR/lib:${LD_LIBRARY_PATH:-}"
    export LLAMA_MTMD_BIN="$NEURAL_DIR/bin/llama-mtmd-cli"
}

# ── 1. runtime нейросетевого движка ──────────────────────────────────
if gpu_present; then
    echo "[запуск] Обнаружена видеокарта NVIDIA — ставится CUDA-вариант движка."
    install_neural cuda || true

    # Torch с CUDA (индекс cu132) — только при наличии GPU
    if ! python3 - <<'PY'
import sys
try:
    import torch
    sys.exit(0 if torch.version.cuda else 1)
except Exception:
    sys.exit(1)
PY
    then
        echo "[запуск] Установка Torch (CUDA, индекс cu132)…"
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

# ── 2. веса модели Chandra ───────────────────────────────────────────
# качаем curl'ом с докачкой (-C -) и повторами: загрузчик huggingface_hub
# на нестабильной сети обрывается и начинает файл заново
echo "[запуск] Проверка весов модели Chandra (первая загрузка ~4.7 ГБ)…"
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
    if [ ! -s "$MODELS_DIR/$f" ]; then
        echo "[запуск] Скачивание $f (займёт несколько минут; ход загрузки"
        echo "[запуск] виден по росту файла $f.part)…"
        if curl -fLsS --retry 30 --retry-all-errors --retry-delay 3 -C - \
                -o "$MODELS_DIR/$f.part" \
                "https://huggingface.co/$HF_REPO/resolve/main/$f"; then
            mv "$MODELS_DIR/$f.part" "$MODELS_DIR/$f"
            echo "[запуск] $f скачан."
        else
            weights_ok=0
            echo "[запуск] ВНИМАНИЕ: $f не скачался — повтор при следующем старте."
        fi
    fi
done
[ "$weights_ok" = "1" ] && echo "[запуск] Веса Chandra на месте: $MODELS_DIR"

# ── 3. модели лёгкого движка ─────────────────────────────────────────
echo "[запуск] Проверка моделей EasyOCR (первая загрузка ~94 МБ)…"
python3 - <<'PY' || echo "[запуск] ВНИМАНИЕ: модели EasyOCR не скачались — повтор при следующем старте."
import easyocr
easyocr.Reader(["ru", "en"], gpu=False, verbose=False)
print("[запуск] Модели EasyOCR на месте.")
PY

# ── 4. приглашение и запуск сервера ──────────────────────────────────
echo ""
echo "======================================================================"
echo "  Всё готово! Откройте веб-интерфейс в браузере:"
echo ""
echo "      http://localhost:8501           — с этого компьютера"
echo "      http://<адрес-сервера>:8501     — с других компьютеров сети"
echo "======================================================================"
echo ""

exec streamlit run app/web.py \
    --server.port 8501 --server.address 0.0.0.0 \
    --server.maxUploadSize 2048 \
    --browser.gatherUsageStats false
