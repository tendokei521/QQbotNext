@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ===== 1) 虚拟环境：没有就建（首次运行即自举） =====
if not exist "venv\Scripts\python.exe" (
    echo [start] 未找到 venv，正在创建虚拟环境...
    python -m venv venv
    if errorlevel 1 (
        echo [start] 创建 venv 失败：请确认已安装 Python 3.10+ 并已加入 PATH。
        exit /b 1
    )
)

rem ===== 2) 依赖自检：缺什么装什么（判定逻辑只在 check_deps.py 里） =====
"venv\Scripts\python.exe" "scripts\ops\check_deps.py"
if errorlevel 1 (
    echo [start] 依赖自检未通过，仍尝试启动。
    echo [start] 若启动报 ModuleNotFoundError，请联网后运行 scripts\ops\requirements.bat
)

rem ===== 3) 启动 =====
cmd /k "venv\Scripts\python.exe main.py"