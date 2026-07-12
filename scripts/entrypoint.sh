#!/bin/bash
# Точка входа контейнера.
#
# Образ собирается ЛЁГКИМ: без CUDA и без компонентов нейросетевого
# движка. При каждом старте проверяется НАЛИЧИЕ (не объём) видеокарты
# NVIDIA с CUDA:
#   - видеокарта есть  -> компоненты нейросетевого движка (llama.cpp с
#     CUDA + Torch cu132) устанавливаются один раз в постоянный том
#     /neural и переиспользуются;
#   - видеокарты нет   -> контейнер работает с лёгким движком.
#
# ПРИНУДИТЕЛЬНЫЙ РЕЖИМ: переменная окружения NEURAL_FORCE=1 ставит
# компоненты нейросетевого движка ДАЖЕ без видеокарты — llama.cpp
# собирается в CPU-варианте. Модель будет работать на процессоре
# (медленно: порядка минут на страницу), но полностью функционально.
set -u

NEURAL_DIR="${NEURAL_DIR:-/neural}"
NEURAL_FORCE="${NEURAL_FORCE:-0}"
mkdir -p "$NEURAL_DIR"

gpu_present() {
    command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1
}

install_neural() {   # $1 = cuda | cpu
    if [ ! -x "$NEURAL_DIR/bin/llama-mtmd-cli" ]; then
        echo "[neural] Установка компонентов нейросетевого движка ($1)…"
        echo "[neural] Выполняется однократно, займёт время."
        if bash /srv/scripts/neural_setup.sh "$NEURAL_DIR" "$1"; then
            echo "[neural] Компоненты установлены в $NEURAL_DIR."
        else
            echo "[neural] ВНИМАНИЕ: установка не удалась — нейросетевой"
            echo "[neural] движок будет недоступен. Повтор при следующем старте."
            return 1
        fi
    else
        echo "[neural] Компоненты нейросетевого движка найдены в томе."
    fi
    export PATH="$NEURAL_DIR/bin:$PATH"
    export LD_LIBRARY_PATH="$NEURAL_DIR/lib:${LD_LIBRARY_PATH:-}"
    export LLAMA_MTMD_BIN="$NEURAL_DIR/bin/llama-mtmd-cli"
}

if gpu_present; then
    echo "[neural] Обнаружена видеокарта NVIDIA — CUDA доступна."
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
        echo "[neural] Установка Torch (CUDA, индекс cu132)…"
        pip install --no-cache-dir torch torchvision \
            --index-url https://download.pytorch.org/whl/cu132 \
            || echo "[neural] Torch(CUDA) не установился — лёгкий движок останется на CPU."
    fi
elif [ "$NEURAL_FORCE" = "1" ]; then
    echo "[neural] Видеокарта NVIDIA не обнаружена, но задан NEURAL_FORCE=1."
    echo "[neural] Принудительная установка нейросетевого движка в CPU-режиме."
    echo "[neural] ПРЕДУПРЕЖДЕНИЕ: распознавание на процессоре медленное"
    echo "[neural] (порядка минут на страницу)."
    install_neural cpu || true
else
    echo "[neural] Видеокарта NVIDIA не обнаружена."
    echo "[neural] Доступен лёгкий движок; компоненты нейросетевого не устанавливаются."
    echo "[neural] Принудительная установка без видеокарты: NEURAL_FORCE=1"
    echo "[neural] (docker-compose.yml, секция environment)."
fi

exec streamlit run app/web.py \
    --server.port 8501 --server.address 0.0.0.0 \
    --server.maxUploadSize 2048
