@echo off
if exist "venv\" (
    call venv\Scripts\activate.bat
) else (
    echo No venv, creating...
    python -m venv venv
    call venv\Scripts\activate.bat
)

cmd /k 