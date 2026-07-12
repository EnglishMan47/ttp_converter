#!/bin/bash
# Проверка готовности системы к запуску проекта в Docker (Linux/macOS).
# Запуск из папки проекта:  bash scripts/preflight.sh
# Ничего не устанавливает и не меняет — только проверяет.

ok=1
say()  { echo "  [ок]     $1"; }
warn() { echo "  [важно]  $1"; }
fail() { echo "  [ошибка] $1"; ok=0; }

echo "=== Проверка готовности к установке (Docker) ==="

# git — нужен для клонирования и обновления
if command -v git >/dev/null 2>&1; then
    say "git установлен"
else
    warn "git не найден (нужен для клонирования): sudo apt install git"
fi

# docker + демон + compose
if command -v docker >/dev/null 2>&1; then
    say "docker установлен: $(docker --version 2>/dev/null)"
    if docker info >/dev/null 2>&1; then
        say "движок Docker запущен"
    else
        fail "движок Docker не отвечает: запустите службу (sudo systemctl start docker); если docker установлен только что — перелогиньтесь после usermod -aG docker"
    fi
    if docker compose version >/dev/null 2>&1; then
        say "docker compose доступен"
    else
        fail "плагин docker compose не найден — установите Docker по инструкции из README (curl -fsSL https://get.docker.com | sudo sh)"
    fi
else
    fail "docker не установлен — см. шаг установки Docker в README"
fi

# архитектура: для x86_64 есть готовая CPU-сборка нейросетевого движка
arch=$(uname -m)
if [ "$arch" = "x86_64" ] || [ "$arch" = "amd64" ]; then
    say "архитектура: $arch"
else
    warn "архитектура $arch: готовой сборки нейросетевого движка нет, при первом старте будет сборка из исходников (заметно дольше)"
fi

# диск: образ ~3 ГБ + модели ~5 ГБ + запас
free_gb=$(df -Pk . | awk 'NR==2 {print int($4/1024/1024)}')
if [ "$free_gb" -lt 8 ]; then
    fail "свободно ${free_gb} ГБ на диске — нужно минимум 8 (образ ~3 ГБ + модели ~5 ГБ)"
elif [ "$free_gb" -lt 15 ]; then
    warn "свободно ${free_gb} ГБ на диске — рекомендуется 15+"
else
    say "диск: свободно ${free_gb} ГБ"
fi

# память: лёгкому движку хватает 4 ГБ, нейросетевому нужно 8+
ram_kb=$(grep -i '^MemTotal' /proc/meminfo 2>/dev/null | awk '{print $2}')
if [ -n "$ram_kb" ]; then
    ram_gb=$((ram_kb / 1024 / 1024))
    if [ "$ram_gb" -lt 4 ]; then
        fail "ОЗУ ${ram_gb} ГБ — нужно минимум 4"
    elif [ "$ram_gb" -lt 8 ]; then
        warn "ОЗУ ${ram_gb} ГБ: лёгкий движок работает; нейросетевому нужно 8+"
    else
        say "ОЗУ: ${ram_gb} ГБ"
    fi
fi

# порт веб-интерфейса
if (exec 3<>/dev/tcp/127.0.0.1/8501) 2>/dev/null; then
    exec 3>&- 3<&-
    warn "порт 8501 уже занят — освободите его или поменяйте левую часть ports в docker-compose.yml"
else
    say "порт 8501 свободен"
fi

# интернет: реестр образов, веса моделей, готовые сборки движка
for host in hub.docker.com huggingface.co github.com; do
    if curl -m 8 -sI "https://$host" >/dev/null 2>&1; then
        say "доступен $host"
    else
        warn "нет доступа к $host — загрузка образов/моделей может не пройти"
    fi
done

echo ""
if [ "$ok" = "1" ]; then
    echo "Всё готово: можно выполнять docker compose up -d --build"
    exit 0
else
    echo "Есть ошибки — устраните их и запустите проверку снова."
    exit 1
fi
