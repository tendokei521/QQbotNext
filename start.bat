@echo off
rem ============================================================
rem  QQBot Next - launcher（薄壳）
rem  真正的逻辑全在 start.py：建 venv → 升 pip/setuptools →
rem  查/装依赖 → 启动 main.py。这里只负责找到解释器并转交，
rem  所以 bat / sh 不可能再各写一套而漂移。
rem
rem  用系统 Python 跑 start.py（而不是 venv 里的）：venv 还没建、
rem  或者已经坏掉时，venv 解释器根本起不来，而 start.py 恰恰要负责
rem  把它重建出来。start.py 内部会自动切到 venv 解释器装依赖和启动。
rem ============================================================
setlocal EnableExtensions
cd /d "%~dp0"

set "LAUNCHER="
where py >nul 2>&1 && set "LAUNCHER=py -3"
if not defined LAUNCHER where python >nul 2>&1 && set "LAUNCHER=python"
if not defined LAUNCHER if exist "venv\Scripts\python.exe" set "LAUNCHER=venv\Scripts\python.exe"

if not defined LAUNCHER (
    echo.
    echo   [FAIL] 找不到 Python 3.10+。
    echo          安装时请勾选 "Add python.exe to PATH"，然后重跑 start.bat。
    pause
    exit /b 1
)

%LAUNCHER% start.py %*
set "RC=%errorlevel%"

rem 用户按 Ctrl+C 是正常退出方式（框架会自己顺序关闭），不该再要一次回车。
if "%RC%"=="3221225786" exit /b 0
if "%RC%"=="130" exit /b 0
if not "%RC%"=="0" (
    echo.
    echo   start.py 退出码 %RC%
    pause
)
pause
exit /b %RC%
