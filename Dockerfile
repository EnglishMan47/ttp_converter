# Образ веб-версии системы пакетной обработки сканов.
#
# Образ собирается лёгким: код, веб-интерфейс и CPU-сборка Torch.
# Всё тяжёлое (runtime нейросетевого движка llama.cpp, веса Chandra
# ~4.7 ГБ, модели EasyOCR ~94 МБ) устанавливается ОДИН РАЗ при первом
# старте контейнера в постоянные тома neural и models-cache — при
# пересоздании контейнера ничего не скачивается заново
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
# нормализуем возможные CRLF (если репозиторий склонирован на Windows):
# иначе Linux не запустит скрипт с shebang, оканчивающимся на \r
RUN sed -i 's/\r$//' /srv/scripts/*.sh && chmod +x /srv/scripts/*.sh

# модели EasyOCR храним в /root/.cache (том models-cache), а не в
# домашнем каталоге по умолчанию — переживают пересоздание контейнера
ENV EASYOCR_MODULE_PATH=/root/.cache/easyocr

EXPOSE 8501
ENTRYPOINT ["/srv/scripts/entrypoint.sh"]
