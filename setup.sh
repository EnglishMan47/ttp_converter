#!/bin/bash
# Установка веб-версии (Linux, без Docker). Запуск: ./setup.sh
# Только проверки, без установки:       ./setup.sh check
set -e

MIN_FREE_GB=5   # pip на Linux ставит CUDA-сборку torch — она большая

echo "=== Checking prerequisites ==="

# git нужен только для клонирования репозитория — не блокирует
if ! command -v git &> /dev/null; then
    echo "WARNING: git is not installed (needed only to clone the repository)."
    echo "         Install it: sudo apt install git"
fi

if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python not found. Install it:"
    echo "       sudo apt install python3 python3-venv python3-pip"
    exit 1
fi

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "ERROR: Python 3.10+ is required, found: $(python3 -V 2>&1)"
    exit 1
fi

# на Debian/Ubuntu модуль venv ставится отдельным пакетом
if ! python3 -c 'import venv, ensurepip' &> /dev/null; then
    echo "ERROR: the Python venv module is missing. Install it:"
    echo "       sudo apt install python3-venv python3-pip"
    exit 1
fi

free_kb=$(df -Pk . | awk 'NR==2 {print $4}')
free_gb=$((free_kb / 1024 / 1024))
if [ "$free_gb" -lt 2 ]; then
    echo "ERROR: only ${free_gb} GB free on this disk; at least 2 GB is required."
    exit 1
fi
if [ "$free_gb" -lt "$MIN_FREE_GB" ]; then
    echo "WARNING: only ${free_gb} GB free; ${MIN_FREE_GB}+ GB recommended"
    echo "         (on Linux pip installs the large CUDA build of torch)."
fi

# Docker для этой установки не нужен — информация для серверного варианта
if command -v docker &> /dev/null; then
    echo "INFO: docker found — the container deployment is also available:"
    echo "      docker compose up -d --build"
else
    echo "INFO: docker is not installed — only this manual install is available."
    echo "      For the server deployment: https://docs.docker.com/engine/install/"
fi

echo "All checks passed."
if [ "${1:-}" = "check" ]; then
    exit 0
fi

echo "=== Creating virtual environment ==="
python3 -m venv venv
source venv/bin/activate

echo "=== Installing packages ==="
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "=== Done ==="
echo "Run the web interface:"
echo "    source venv/bin/activate"
echo "    streamlit run app/web.py"
echo ""
echo "NOTE: the neural engine (Chandra) additionally requires the"
echo "llama-mtmd-cli binary from llama.cpp. The Docker image builds it"
echo "automatically; for manual installs build llama.cpp yourself or"
echo "use the light engine."
