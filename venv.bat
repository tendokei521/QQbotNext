@echo off
rem ============================================================
rem  QQBot Next - open a shell with the venv activated
rem  This file sits next to venv/, so %~dp0 is the repository root.
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
cmd /k
exit /b 0

:no_root
echo [FAIL] cannot enter the repository root.
pause
exit /b 1

:no_python
echo [FAIL] could not create the venv. Is Python 3.10+ on PATH?
pause
exit /b 1