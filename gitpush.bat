@echo off
chcp 65001 >nul
cd /d "%~dp0"
"%~dp0venv\Scripts\python.exe" "%~dp0gitpush.py"
exit /b %errorlevel%
