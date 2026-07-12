<#
Проверка готовности системы к запуску проекта в Docker (Windows).
Запуск из папки проекта:
    powershell -ExecutionPolicy Bypass -File scripts\preflight.ps1
Ничего не устанавливает и не меняет — только проверяет.
#>
$ErrorActionPreference = "SilentlyContinue"
$script:fail = 0
function Ok($m)   { Write-Host "  [ок]     $m" }
function Warn($m) { Write-Host "  [важно]  $m" }
function Bad($m)  { Write-Host "  [ошибка] $m"; $script:fail = 1 }

Write-Host "=== Проверка готовности к установке (Docker) ==="

if (Get-Command git -ErrorAction SilentlyContinue) { Ok "git установлен" }
else { Warn "git не найден (нужен для клонирования): https://git-scm.com/download/win" }

if (Get-Command docker -ErrorAction SilentlyContinue) {
    Ok ("docker установлен: " + (docker --version 2>$null))
    docker info 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Ok "движок Docker запущен" }
    else { Bad "Docker установлен, но движок не отвечает — запустите Docker Desktop и дождитесь статуса Running" }
    docker compose version 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Ok "docker compose доступен" }
    else { Bad "docker compose не найден — обновите Docker Desktop" }
} else {
    Bad "Docker не установлен — поставьте Docker Desktop: https://www.docker.com/products/docker-desktop/"
}

if ($env:PROCESSOR_ARCHITECTURE -eq "AMD64") { Ok "архитектура: AMD64" }
else { Warn "архитектура $($env:PROCESSOR_ARCHITECTURE): готовой сборки нейросетевого движка нет, при первом старте будет сборка из исходников (дольше)" }

$freeC = [math]::Floor((Get-PSDrive C).Free / 1GB)
if ($freeC -lt 8) { Bad "на C: свободно $freeC ГБ — нужно минимум 8 (виртуальный диск Docker живёт на C:; образ ~3 ГБ + модели ~5 ГБ)" }
elseif ($freeC -lt 15) { Warn "на C: свободно $freeC ГБ — рекомендуется 15+" }
else { Ok "диск C: свободно $freeC ГБ" }

$ram = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
if ($ram -lt 4) { Bad "ОЗУ $ram ГБ — нужно минимум 4" }
elseif ($ram -lt 8) { Warn "ОЗУ $ram ГБ: контейнерам Docker достаётся ~половина; нейросетевому движку может не хватить" }
else { Ok "ОЗУ: $ram ГБ (контейнерам достанется ~половина)" }

$port = Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue
if ($port) { Warn "порт 8501 уже занят (PID $($port[0].OwningProcess)) — освободите его или поменяйте левую часть ports в docker-compose.yml" }
else { Ok "порт 8501 свободен" }

foreach ($h in "hub.docker.com", "huggingface.co", "github.com") {
    try {
        Invoke-WebRequest "https://$h" -Method Head -TimeoutSec 8 -UseBasicParsing | Out-Null
        Ok "доступен $h"
    } catch {
        Warn "нет доступа к $h — загрузка образов/моделей может не пройти"
    }
}

Write-Host ""
if ($script:fail) {
    Write-Host "Есть ошибки — устраните их и запустите проверку снова."
    exit 1
}
Write-Host "Всё готово: можно выполнять docker compose up -d --build"
exit 0
