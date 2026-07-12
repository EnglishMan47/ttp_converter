# Образ веб-версии системы пакетной обработки сканов.
#
# Образ собирается ЛЁГКИМ: в него входит только лёгкий движок (EasyOCR
# на CPU-сборке Torch), веб-интерфейс и обвязка. Компоненты
# нейросетевого движка (llama.cpp с CUDA, Torch cu132) НЕ ставятся при
# сборке: при каждом старте контейнер проверяет наличие видеокарты
# NVIDIA (наличие CUDA, не объём памяти) и только при её обнаружении
# один раз доустанавливает их в постоянный том /neural
# (см. scripts/entrypoint.sh и scripts/neural_setup.sh).
#
# Сборка:  docker compose up -d --build
# Клиент:  http://<адрес-сервера>:8501

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv \
        curl ca-certificates \
        tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

RUN python3 -m venv /srv/venv
ENV PATH="/srv/venv/bin:$PATH"

# CPU-сборка Torch: маленькая, достаточна для лёгкого движка.
# CUDA-сборка (индекс cu132) ставится entrypoint'ом только при
# обнаружении видеокарты.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir torch torchvision \
        --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
RUN chmod +x /srv/scripts/*.sh

# Веса Chandra и runtime нейросетевого движка живут в томах
# (models-cache и neural) — образ остаётся лёгким.

EXPOSE 8501
ENTRYPOINT ["/srv/scripts/entrypoint.sh"]
