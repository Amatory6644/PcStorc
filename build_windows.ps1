$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python Launcher (py) не найден. Установите Python 3.12+ с python.org."
}

if (-not (Test-Path .venv)) {
    py -3 -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean PcStorc.spec

Write-Host ""
Write-Host "Готово: $PSScriptRoot\dist\PcStorc.exe"
