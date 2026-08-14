@echo off
cd /d "%~dp0.."
if exist "venv\" (
    call venv\Scripts\activate.bat
) else (
    echo No venv, creating...
    python -m venv venv
    call venv\Scripts\activate.bat
)

pip install -r requirements.txt
cmd /k