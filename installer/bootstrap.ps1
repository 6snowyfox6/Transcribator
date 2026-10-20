# Доустановка Транскрибатора после копирования файлов.
#
# Установщик намеренно компактный: Python, библиотеки и модель весят около
# 6 ГБ, и тащить их в самом .exe неудобно. Поэтому здесь мы их скачиваем.
#
# Скрипт можно запускать повторно — уже сделанные шаги он пропускает.
#
# Окно установщика запускает нас скрыто и следит за ходом работы по файлу
# progress.txt: строка вида «18|62|Ставлю PyTorch» — текущий процент, потолок
# шага (до него окно плавно подводит полосу) и подпись. В конце появляется
# install.done, по которому окно понимает, что мы закончили.
#
# ВАЖНО: файл обязан быть в UTF-8 С BOM. Windows PowerShell 5.1 без BOM
# читает .ps1 в системной кодировке, кириллица разваливает разбор, и установка
# падает молча. BOM проставляет installer\build.py при каждой сборке.

param(
    [string]$AppDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonVersion = "3.12.10",
    [switch]$SkipModels,
    [switch]$ForceCpu,
    [switch]$Unattended
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # иначе Invoke-WebRequest ползёт медленно

# Без этого русский вывод питоновских скриптов попадает в лог кракозябрами.
$env:PYTHONIOENCODING = "utf-8"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$LogFile = Join-Path $AppDir "install.log"
$ProgressFile = Join-Path $AppDir "progress.txt"
$DoneFile = Join-Path $AppDir "install.done"
$VenvDir = Join-Path $AppDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$OwnPythonDir = Join-Path $AppDir "python"

function Say([string]$Text, [string]$Color = "Gray") {
    Write-Host $Text -ForegroundColor $Color
    Add-Content -LiteralPath $LogFile -Value "$(Get-Date -Format 'HH:mm:ss')  $Text" -Encoding utf8
}

function Step([int]$Percent, [int]$Ceiling, [string]$Text) {
    Say $Text "White"
    Set-Content -LiteralPath $ProgressFile -Value "$Percent|$Ceiling|$Text" -Encoding utf8
}

function Fail([string]$Text) {
    Say $Text "Red"
    Say "Подробности: $LogFile"
    Set-Content -LiteralPath $ProgressFile -Value "-1|-1|$Text" -Encoding utf8
    Set-Content -LiteralPath $DoneFile -Value "fail" -Encoding utf8
    if (-not $Unattended) {
        Write-Host ""
        Write-Host "Установка не завершена. Нажмите Enter, чтобы закрыть." -ForegroundColor Red
        try { Read-Host | Out-Null } catch { }
    }
    exit 1
}

function Run([string]$Exe, [string[]]$Arguments, [string]$What) {
    Say "  $What"
    & $Exe @Arguments 2>&1 | ForEach-Object {
        Add-Content -LiteralPath $LogFile -Value "    $_" -Encoding utf8
    }
    if ($LASTEXITCODE -ne 0) { Fail "Не удалось: $What (код $LASTEXITCODE)" }
}

# Подходящий Python: версия 3.10-3.13 и обязательно с tkinter — на нём окно.
function Test-Python([string]$Exe) {
    if (-not $Exe) { return $false }
    $probe = @'
import sys, tkinter
assert (3, 10) <= sys.version_info < (3, 14), sys.version
print(sys.executable)
'@
    try {
        $result = & $Exe -c $probe 2>$null
        return ($LASTEXITCODE -eq 0 -and $result)
    } catch {
        return $false
    }
}

function Find-SystemPython() {
    foreach ($version in @("3.12", "3.11", "3.13", "3.10")) {
        try {
            $found = & py "-$version" -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $found -and (Test-Python $found.Trim())) {
                return $found.Trim()
            }
        } catch { }
    }
    foreach ($name in @("python.exe", "python3.exe")) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command -and (Test-Python $command.Source)) { return $command.Source }
    }
    return $null
}

Remove-Item -LiteralPath $DoneFile -Force -ErrorAction SilentlyContinue
Set-Content -LiteralPath $LogFile -Value "Установка Транскрибатора $(Get-Date)" -Encoding utf8

# Внутри PyTorch есть файлы с очень длинными именами, и на глубоко вложенной
# папке pip упирается в предел длины пути Windows (260 символов). Лучше сказать
# об этом сразу, чем на середине загрузки в 2.5 ГБ.
if ($AppDir.Length -gt 90) {
    Say "Слишком длинный путь установки ($($AppDir.Length) символов): $AppDir" "Red"
    Fail "Слишком длинный путь установки. Выберите папку ближе к корню диска, например C:\Transcribator"
}

Write-Host ""
Write-Host "  Настройка Транскрибатора" -ForegroundColor White
Write-Host ""

# --------------------------------------------------------------------------- #
# 1. Python
# --------------------------------------------------------------------------- #
# Сначала ищем уже установленный: скачанный установщик python.org при наличии
# той же версии в системе не ставит вторую копию, а «чинит» существующую и
# молча игнорирует TargetDir. Своё окружение всё равно получаем — через venv.

Step 2 8 "Проверяю Python"
if (Test-Path $VenvPython) {
    Say "  окружение уже создано"
} else {
    $base = $null
    if (Test-Path (Join-Path $OwnPythonDir "python.exe")) {
        $base = Join-Path $OwnPythonDir "python.exe"
        Say "  беру Python из папки программы"
    } else {
        $base = Find-SystemPython
        if ($base) { Say "  найден в системе: $base" "Green" }
    }

    if (-not $base) {
        Step 2 8 "Скачиваю Python $PythonVersion (25 МБ)"
        $url = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe"
        $installer = Join-Path $env:TEMP "python-$PythonVersion-amd64.exe"
        try {
            Invoke-WebRequest -Uri $url -OutFile $installer -UseBasicParsing
        } catch {
            Fail "Не удалось скачать Python: $_"
        }
        # Только для текущего пользователя и прямо в папку программы:
        # прав администратора не нужно, в системе ничего не меняется.
        Run $installer @(
            "/quiet", "InstallAllUsers=0", "TargetDir=$OwnPythonDir", "PrependPath=0",
            "Include_launcher=0", "AssociateFiles=0", "Shortcuts=0",
            "Include_test=0", "Include_doc=0"
        ) "устанавливаю"
        Remove-Item $installer -Force -ErrorAction SilentlyContinue

        $base = Join-Path $OwnPythonDir "python.exe"
        if (-not (Test-Path $base)) {
            # Такая версия уже была в системе — установщик её только обновил.
            $base = Find-SystemPython
        }
        if (-not $base) { Fail "Python установить не удалось" }
    }

    Step 8 14 "Создаю окружение программы"
    Run $base @("-m", "venv", $VenvDir) "venv"
    if (-not (Test-Path $VenvPython)) { Fail "Окружение не создалось в $VenvDir" }
}

Run $VenvPython @("-m", "pip", "install", "--upgrade", "pip", "--quiet") "обновляю pip"

# --------------------------------------------------------------------------- #
# 2. Видеокарта
# --------------------------------------------------------------------------- #

Step 14 18 "Определяю видеокарту"
$vram = 0
$hasGpu = $false
if (-not $ForceCpu) {
    try {
        $smi = & nvidia-smi --query-gpu=memory.total,name --format=csv,noheader,nounits 2>$null
        if ($LASTEXITCODE -eq 0 -and $smi) {
            $first = @($smi)[0] -split ","
            $vram = [int]$first[0].Trim()
            $hasGpu = $true
            Say "  найдена: $($first[1].Trim()), $vram МБ памяти" "Green"
        }
    } catch { }
}
if (-not $hasGpu) {
    Say "  NVIDIA не найдена — ставлю процессорный вариант" "Yellow"
}

# --------------------------------------------------------------------------- #
# 3. PyTorch
# --------------------------------------------------------------------------- #

$torchIndex = if ($hasGpu) { "https://download.pytorch.org/whl/cu128" }
              else { "https://download.pytorch.org/whl/cpu" }
$size = if ($hasGpu) { "2.5 ГБ" } else { "250 МБ" }
Step 18 62 "Скачиваю и ставлю PyTorch ($size) — самый долгий шаг"
Run $VenvPython @("-m", "pip", "install", "torch", "torchaudio",
                  "--index-url", $torchIndex) "pip install torch"

# --------------------------------------------------------------------------- #
# 4. Остальные библиотеки
# --------------------------------------------------------------------------- #

Step 62 82 "Ставлю библиотеки распознавания"
Run $VenvPython @("-m", "pip", "install", "-r",
                  (Join-Path $AppDir "requirements.txt")) "pip install -r requirements.txt"

# Сведения о машине: окно по ним подбирает модель по умолчанию.
$machine = [ordered]@{
    gpu = $hasGpu
    vram_mb = $vram
    torch = if ($hasGpu) { "cu128" } else { "cpu" }
    installed = (Get-Date -Format "s")
} | ConvertTo-Json
Set-Content -LiteralPath (Join-Path $AppDir "machine.json") -Value $machine -Encoding utf8

# --------------------------------------------------------------------------- #
# 5. Модель распознавания
# --------------------------------------------------------------------------- #

if ($SkipModels) {
    Step 82 98 "Модель скачается при первой расшифровке"
} else {
    Step 82 98 "Скачиваю модель распознавания речи"
    & $VenvPython (Join-Path $AppDir "installer\prefetch.py") $AppDir 2>&1 |
        ForEach-Object { Say "  $_" }
    if ($LASTEXITCODE -ne 0) {
        Say "  не получилось — модель скачается при первой расшифровке" "Yellow"
    }
}

$final = if ($hasGpu) { "Готово. Транскрибатор установлен." }
         else { "Готово. Видеокарты NVIDIA нет — расшифровка пойдёт на процессоре." }
Step 100 100 $final
Set-Content -LiteralPath $DoneFile -Value "ok" -Encoding utf8
Write-Host ""
