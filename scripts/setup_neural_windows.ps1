<#
Установка runtime нейросетевого движка (llama.cpp / llama-mtmd-cli)
на Windows без Docker.

Скачивает официальную CPU-сборку llama.cpp (репозиторий
ggml-org/llama.cpp, GitHub Releases) в папку neural\llama.cpp проекта —
приложение находит её автоматически. Веса модели Chandra (~4.7 ГБ)
скачиваются либо сразу (ключ -DownloadModels), либо лениво при первом
использовании движка; они сохраняются в neural\models.

Если на системном диске мало места, ДО запуска создайте junction на
другой диск (не требует прав администратора):
    mkdir D:\tiff2pdf_neural
    mklink /J "<папка проекта>\neural" D:\tiff2pdf_neural

Запуск (из папки проекта):
    powershell -ExecutionPolicy Bypass -File scripts\setup_neural_windows.ps1
#>
param(
    [switch]$DownloadModels
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$proj = Split-Path $PSScriptRoot -Parent
$dest = Join-Path $proj "neural\llama.cpp"
New-Item -ItemType Directory -Force $dest | Out-Null

function Get-FreeGB([string]$path) {
    # если neural — junction на другой диск, место меряем на диске
    # назначения; LinkTarget — PowerShell 7, Target — PowerShell 5.1
    $item = Get-Item $path
    $real = $item.FullName
    if ($item.LinkTarget) { $real = $item.LinkTarget }
    elseif ($item.Target) { $real = @($item.Target)[0] }
    $root = [System.IO.Path]::GetPathRoot($real)
    [math]::Floor((Get-PSDrive -Name $root.Substring(0, 1)).Free / 1GB)
}

$neuralRoot = Split-Path $dest -Parent
$freeGb = Get-FreeGB $neuralRoot
$needGb = if ($DownloadModels) { 6 } else { 1 }
if ($freeGb -lt $needGb) {
    throw ("На диске с папкой neural свободно $freeGb ГБ, нужно минимум " +
           "$needGb ГБ. Перенесите папку neural на другой диск " +
           "(junction, команда в шапке скрипта) и запустите снова.")
}
Write-Host "Свободно на диске назначения: $freeGb ГБ."
if (-not $DownloadModels -and $freeGb -lt 6) {
    Write-Host ("ВНИМАНИЕ: весам модели при первом использовании движка " +
                "понадобится ещё ~5 ГБ на этом же диске.")
}

Write-Host "Ищу последний выпуск llama.cpp..."
$rel = Invoke-RestMethod "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
$asset = $rel.assets | Where-Object { $_.name -match "bin-win-cpu-x64\.zip$" } |
    Select-Object -First 1
if (-not $asset) {   # старое имя CPU-сборки
    $asset = $rel.assets | Where-Object { $_.name -match "bin-win-avx2-x64\.zip$" } |
        Select-Object -First 1
}
if (-not $asset) {
    throw "В выпуске $($rel.tag_name) не нашлось Windows CPU-сборки (*bin-win-cpu-x64.zip)."
}

$mb = [math]::Round($asset.size / 1MB, 1)
Write-Host "Скачиваю $($asset.name) ($mb МБ, выпуск $($rel.tag_name))..."
$zip = Join-Path $dest "_llama_download.zip"
Invoke-WebRequest $asset.browser_download_url -OutFile $zip
Expand-Archive $zip -DestinationPath $dest -Force
Remove-Item $zip

$exe = Get-ChildItem $dest -Recurse -Filter "llama-mtmd-cli.exe" | Select-Object -First 1
if (-not $exe) { throw "В архиве не оказалось llama-mtmd-cli.exe" }
if ($exe.DirectoryName -ne $dest) {
    # сборка распакована во вложенную папку — поднимаем файлы на уровень выше
    Get-ChildItem $exe.DirectoryName | Move-Item -Destination $dest -Force
}

# версия печатается в stderr; в PowerShell 5.1 при ErrorAction=Stop это
# стало бы ошибкой — на время проверки ослабляем режим
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$ver = (& (Join-Path $dest "llama-mtmd-cli.exe") --version 2>&1 |
    ForEach-Object { "$_" }) -join "`n"
$ErrorActionPreference = $prevEap
Write-Host ($ver.Trim())
if ($ver -notmatch "version:") {
    throw "llama-mtmd-cli.exe не запускается: $ver"
}
Write-Host "Runtime установлен: $dest"

if ($DownloadModels) {
    $py = Join-Path $proj "venv\Scripts\python.exe"
    if (-not (Test-Path $py)) {
        throw "Не найден venv\Scripts\python.exe — сначала выполните setup.bat"
    }
    Write-Host "Скачиваю веса Chandra Q6_K (~4.7 ГБ), это займёт время..."
    & $py -X utf8 -c "import sys, pathlib; sys.path.insert(0, str(pathlib.Path(r'$proj') / 'app')); from stage_2.ocr_chandra import ensure_models; print(ensure_models())"
    if ($LASTEXITCODE -ne 0) { throw "Не удалось скачать веса модели" }
}

Write-Host ""
Write-Host "Готово. Приложение найдёт движок автоматически (папка neural\)."
Write-Host "Запуск:  venv\Scripts\activate  →  streamlit run app\web.py"
