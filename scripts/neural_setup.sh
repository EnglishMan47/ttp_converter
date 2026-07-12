#!/bin/bash
# Однократная установка runtime нейросетевого движка (llama-mtmd-cli)
# в постоянный том. Вызывается entrypoint'ом.
#
# Аргументы: $1 = каталог назначения; $2 = режим (cuda | cpu).
#
# Пути установки:
#   1) LLAMA_CUDA_URL задан (и режим cuda) — скачать готовую
#      CUDA-сборку llama.cpp (tar.gz с bin/ и lib/). Быстро.
#   2) иначе — сборка из исходников:
#        cuda: ставится cuda-toolkit, сборка с -DGGML_CUDA=ON (долго);
#        cpu:  сборка без CUDA — легче и быстрее, работает на процессоре.
set -euo pipefail
DEST="$1"
MODE="${2:-cuda}"
mkdir -p "$DEST/bin" "$DEST/lib"

if [ "$MODE" = "cuda" ] && [ -n "${LLAMA_CUDA_URL:-}" ]; then
    echo "[neural] Скачивание готовой сборки: $LLAMA_CUDA_URL"
    tmp=$(mktemp -d)
    curl -fL "$LLAMA_CUDA_URL" -o "$tmp/llama.tar.gz"
    tar -xzf "$tmp/llama.tar.gz" -C "$tmp"
    found=$(find "$tmp" -name llama-mtmd-cli -type f | head -1)
    [ -n "$found" ] || { echo "[neural] в архиве нет llama-mtmd-cli"; exit 1; }
    cp "$found" "$DEST/bin/"
    find "$(dirname "$found")/.." -name "*.so*" -exec cp {} "$DEST/lib/" \; 2>/dev/null || true
    chmod +x "$DEST/bin/llama-mtmd-cli"
    rm -rf "$tmp"
    exit 0
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
PKGS="git cmake build-essential ca-certificates"
CMAKE_FLAGS="-DCMAKE_BUILD_TYPE=Release"
if [ "$MODE" = "cuda" ]; then
    PKGS="$PKGS nvidia-cuda-toolkit"
    CMAKE_FLAGS="$CMAKE_FLAGS -DGGML_CUDA=ON"
    echo "[neural] Сборка из исходников с CUDA."
else
    echo "[neural] Сборка из исходников в CPU-режиме (без CUDA)."
fi
apt-get install -y --no-install-recommends $PKGS

git clone --depth 1 https://github.com/ggml-org/llama.cpp /tmp/llama.cpp
cmake -S /tmp/llama.cpp -B /tmp/llama.cpp/build $CMAKE_FLAGS
cmake --build /tmp/llama.cpp/build --target llama-mtmd-cli -j"$(nproc)"

cp /tmp/llama.cpp/build/bin/llama-mtmd-cli "$DEST/bin/"
find /tmp/llama.cpp/build -name "*.so*" -exec cp {} "$DEST/lib/" \; 2>/dev/null || true
rm -rf /tmp/llama.cpp
echo "[neural] Сборка завершена ($MODE)."
