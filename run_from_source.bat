@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python Launcher not found. Install Python 3.12+ or build PcStorc.exe.
  pause
  exit /b 1
)
py -3 main.py
