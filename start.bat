@echo off
rem ============================================================
rem  QQBot Next - launcher (thin shell)
rem  All real logic lives in start.py: create venv -> upgrade pip
rem  and setuptools -> check/install deps -> launch main.py.
rem  This file only locates an interpreter and hands over, so the
rem  bat and sh wrappers can never drift apart.
rem
rem  KEEP THIS FILE ASCII-ONLY WITH CRLF LINE ENDINGS.
rem  cmd.exe reads .bat in the console codepage (cp936 on Chinese
rem  Windows): UTF-8 Chinese here becomes garbage commands, and an
rem  LF-only .bat breaks parsing of parenthesised blocks.
rem ============================================================
setlocal EnableExtensions
cd /d "%~dp0"

set "LAUNCHER="
where py >nul 2>&1
if not errorlevel 1 set "LAUNCHER=py -3"
if not defined LAUNCHER where python >nul 2>&1
if not defined LAUNCHER if not errorlevel 1 set "LAUNCHER=python"
if not defined LAUNCHER if exist "venv\Scripts\python.exe" set "LAUNCHER=venv\Scripts\python.exe"
if not defined LAUNCHER goto :no_python

%LAUNCHER% start.py %*
set "RC=%errorlevel%"

rem Ctrl+C is a normal way to stop the bot: do not ask for another key.
if "%RC%"=="3221225786" exit /b 0
if "%RC%"=="130" exit /b 0
if not "%RC%"=="0" goto :failed
exit /b %RC%

:failed
echo.
echo   start.py exited with code %RC%
pause
exit /b %RC%

:no_python
echo.
echo   [FAIL] Python 3.10+ not found.
echo          Install it with "Add python.exe to PATH" ticked, then rerun start.bat.
pause
exit /b 1