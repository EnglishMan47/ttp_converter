#!/bin/bash
# Однократная установка runtime нейросетевого движка (llama-mtmd-cli)
# в постоянный том. Вызывается entrypoint'ом.
#
# Аргументы: $1 = каталог назначения; $2 = режим (cuda | cpu).
#
# Пути установки:
#   cpu:  скачивается готовая CPU-сборка llama.cpp с GitHub Releases
#         (быстро, ~1 минута); если не получилось (нет доступа к
#         github.com, редкая архитектура) — сборка из исходников;
#   cuda: LLAMA_CUDA_URL задан — скачать готовую CUDA-сборку (tar.gz
#         с bin/ и lib/); иначе сборка из исходников с cuda-toolkit
#         (долго).
set -euo pipefail
DEST="$1"
MODE="${2:-cuda}"
mkdir -p "$DEST/bin" "$DEST/lib"

install_from_dir() {   # $1 = каталог с распакованной сборкой
    local found
    found=$(find "$1" -name llama-mtmd-cli -type f | head -1)
    [ -n "$found" ] || return 1
    cp "$found" "$DEST/bin/"
    find "$1" -name "*.so*" -exec cp {} "$DEST/lib/" \; 2>/dev/null || true
    chmod +x "$DEST/bin/llama-mtmd-cli"
}

if [ "$MODE" = "cuda" ] && [ -n "${LLAMA_CUDA_URL:-}" ]; then
    echo "[neural] Скачивание готовой сборки: $LLAMA_CUDA_URL"
    tmp=$(mktemp -d)
    curl -fL "$LLAMA_CUDA_URL" -o "$tmp/llama.tar.gz"
    tar -xzf "$tmp/llama.tar.gz" -C "$tmp"
    install_from_dir "$tmp" || { echo "[neural] в архиве нет llama-mtmd-cli"; exit 1; }
    rm -rf "$tmp"
    exit 0
fi

if [ "$MODE" = "cpu" ] && [ "$(uname -m)" = "x86_64" ]; then
    echo "[neural] Поиск готовой CPU-сборки llama.cpp (GitHub Releases)…"
    url=$(curl -fsSL https://api.github.com/repos/ggml-org/llama.cpp/releases/latest \
          | grep -o '"browser_download_url": *"[^"]*bin-ubuntu-x64\.tar\.gz"' \
          | head -1 | sed 's/.*"\(https[^"]*\)".*/\1/') || url=""
    if [ -n "$url" ]; then
        echo "[neural] Скачивание: $url"
        tmp=$(mktemp -d)
        if curl -fL "$url" -o "$tmp/llama.tar.gz" \
           && mkdir -p "$tmp/x" \
           && tar -xzf "$tmp/llama.tar.gz" -C "$tmp/x" \
           && install_from_dir "$tmp/x"; then
            rm -rf "$tmp"
            echo "[neural] Готовая CPU-сборка установлена."
            exit 0
        fi
        rm -rf "$tmp"
    fi
    echo "[neural] Готовую сборку получить не удалось — сборка из исходников."
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

install_from_dir /tmp/llama.cpp/build || { echo "[neural] сборка не дала llama-mtmd-cli"; exit 1; }
rm -rf /tmp/llama.cpp
echo "[neural] Сборка завершена ($MODE)."
