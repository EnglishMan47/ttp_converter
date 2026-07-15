#!/bin/bash
# Однократная установка runtime нейросетевого движка (llama-mtmd-cli)
# в постоянный том. Вызывается entrypoint'ом.
#
# Аргументы: $1 = каталог назначения; $2 = режим (cuda | cpu).
#
# Пути установки:
#   cpu   — скачивается готовая CPU-сборка llama.cpp с GitHub Releases
#           (быстро, ~1 минута); не вышло — сборка из исходников.
#   cuda  — собираем из исходников с -DGGML_CUDA=ON. Если сборка не
#           удалась — откатываемся на готовую CPU-сборку, чтобы движок всё
#           же работал (медленнее, но работает).
#
# Режим выбирает entrypoint.sh: видна ли видеокарта внутри контейнера
# (nvidia-smi). Чтобы контейнер её увидел, в docker-compose.yml нужно
# раскомментировать блок deploy.
set -uo pipefail
DEST="$1"
MODE="${2:-cuda}"
mkdir -p "$DEST/bin"

install_from_dir() {   # $1 = каталог с распакованной сборкой
    local found
    found=$(find "$1" -name llama-mtmd-cli -type f | head -1)
    [ -n "$found" ] || return 1
    # Бинарник и ВСЕ .so кладём в ОДНУ папку ($DEST/bin). Готовые сборки
    # llama.cpp модульные: CPU-бэкенд лежит в отдельных libggml-cpu-*.so,
    # а ggml_backend_load_all() ищет их РЯДОМ С БИНАРНИКОМ (и в текущем
    # каталоге), НЕ по LD_LIBRARY_PATH. Раздельные bin/ и lib/ приводят к
    # «failed to load a backend / failed to load model».
    cp "$found" "$DEST/bin/"
    find "$1" -name "*.so*" -exec cp {} "$DEST/bin/" \; 2>/dev/null || true
    chmod +x "$DEST/bin/llama-mtmd-cli"
}

download_prebuilt_cpu() {
    [ "$(uname -m)" = "x86_64" ] || return 1
    local url tmp rc=0
    echo "[neural] Поиск готовой CPU-сборки llama.cpp (GitHub Releases)…"
    url=$(curl -fsSL https://api.github.com/repos/ggml-org/llama.cpp/releases/latest \
          | grep -o '"browser_download_url": *"[^"]*bin-ubuntu-x64\.tar\.gz"' \
          | head -1 | sed 's/.*"\(https[^"]*\)".*/\1/') || url=""
    [ -n "$url" ] || return 1
    echo "[neural] Скачивание: $url"
    tmp=$(mktemp -d)
    curl -fL "$url" -o "$tmp/llama.tar.gz" \
        && mkdir -p "$tmp/x" \
        && tar -xzf "$tmp/llama.tar.gz" -C "$tmp/x" \
        && install_from_dir "$tmp/x" || rc=1
    rm -rf "$tmp"
    return $rc
}

build_from_source() {   # $1 = cuda | cpu
    local pkgs="git cmake build-essential ca-certificates"
    local flags="-DCMAKE_BUILD_TYPE=Release"
    if [ "$1" = "cuda" ]; then
        pkgs="$pkgs nvidia-cuda-toolkit"
        # native — код под ту видеокарту, что реально стоит в машине
        flags="$flags -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=native"
        echo "[neural] Сборка из исходников с CUDA (готовых CUDA-сборок"
        echo "[neural] под Linux upstream не публикует). Это займёт время."
    else
        echo "[neural] Сборка из исходников в CPU-режиме (без CUDA)."
    fi
    export DEBIAN_FRONTEND=noninteractive
    apt-get update || return 1
    apt-get install -y --no-install-recommends $pkgs || return 1
    rm -rf /tmp/llama.cpp
    git clone --depth 1 https://github.com/ggml-org/llama.cpp /tmp/llama.cpp || return 1
    cmake -S /tmp/llama.cpp -B /tmp/llama.cpp/build $flags || return 1
    cmake --build /tmp/llama.cpp/build --target llama-mtmd-cli -j"$(nproc)" || return 1
    install_from_dir /tmp/llama.cpp/build || return 1
    rm -rf /tmp/llama.cpp
    return 0
}

# ── 1. CPU: готовая сборка с GitHub
if [ "$MODE" = "cpu" ]; then
    if download_prebuilt_cpu; then
        echo "[neural] Готовая CPU-сборка установлена."
        exit 0
    fi
    echo "[neural] Готовую сборку получить не удалось — сборка из исходников."
fi

# ── 2. сборка из исходников (для CUDA — основной и единственный путь)
if build_from_source "$MODE"; then
    echo "[neural] Сборка завершена ($MODE)."
    exit 0
fi

# ── 3. CUDA не собралась
if [ "$MODE" = "cuda" ]; then
    echo "[neural] CUDA-сборка не удалась. Ставлю готовую CPU-сборку:"
    echo "[neural] движок будет работать на процессоре (медленнее)."
    if download_prebuilt_cpu; then
        echo "[neural] Готовая CPU-сборка установлена (без ускорения на GPU)."
        exit 0
    fi
fi

echo "[neural] Установить runtime не удалось."
exit 1
