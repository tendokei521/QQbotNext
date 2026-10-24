@echo off
rem ============================================================
rem  QQBot Next - install dependencies (first-run / offline fallback)
rem  pyproject.toml is the single source of truth; this script only
rem  goes through requirements.txt, which is a pointer to it.
rem  The one-click entry point is start.py; this is the fallback.
rem  KEEP THIS FILE ASCII-ONLY WITH CRLF LINE ENDINGS (cmd.exe reads
rem  .bat in the console codepage).
rem ============================================================
cd /d "%~dp0"
if errorlevel 1 goto :no_root

if exist "venv\Scripts\activate.bat" goto :activate
echo No venv, creating...
python -m venv venv
if errorlevel 1 goto :no_python

:activate
call venv\Scripts\activate.bat

rem Old pip / setuptools cannot install this project's pyproject
rem backend (needs setuptools>=68), so upgrade them first.
python -m pip install --upgrade pip setuptools
python -m pip install -r requirements.txt

echo.
echo Other ways to do the same thing:
echo   python start.py                (one-click: venv + deps + launch)
echo   python check_deps.py --dev     (checks and installs only what is missing)
pause
exit /b 0

:no_root
echo [FAIL] cannot enter the repository root.
pause
exit /b 1

:no_python
echo [FAIL] could not create the venv. Is Python 3.10+ on PATH?
pause
exit /b 1