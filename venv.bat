@echo off
rem ============================================================
rem  QQBot Next - 打开一个已激活 venv 的 shell
rem  本文件与 venv/ 同在仓库根目录，所以 cd 到 %~dp0 就够；
rem  若一层 .. 写成两级，会跑到仓库外去白建一个空 venv。
rem ============================================================
cd /d "%~dp0"
if errorlevel 1 (
    echo [FAIL] cannot enter the repository root.
    pause
    exit /b 1
)

if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else (
    echo No venv, creating...
    python -m venv venv
    call venv\Scripts\activate.bat
)

cmd /k
