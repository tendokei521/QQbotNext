@echo off
rem ============================================================
rem  QQBot Next - 手动装依赖（离线/首次安装的兜底入口）
rem  依赖清单的唯一事实源是 pyproject.toml；本脚本一律经由
rem  requirements.txt 这层"指针"安装，不再自己列包名。
rem
rem  一键入口是 start.py（建 venv → 查/装依赖 → 启动）；
rem  本脚本是它跑不动时的兜底，两者都在仓库根目录。
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

rem 老 pip / 老 setuptools 装不了本项目的 pyproject 后端（需要 setuptools>=68），
rem 先各自升级一步再装依赖。
python -m pip install --upgrade pip setuptools
python -m pip install -r requirements.txt

echo.
echo Other ways to do the same thing:
echo   python start.py                (one-click: venv + deps + launch)
echo   python check_deps.py --dev     (checks and installs only what is missing)
echo   venv.bat                       (just open a shell inside the venv)
pause
