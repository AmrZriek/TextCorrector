@echo off
cd /d "%~dp0"

:: Auto-elevate to admin (required for global hotkeys)
net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

call venv\Scripts\activate.bat
start "" pythonw text_corrector.py
