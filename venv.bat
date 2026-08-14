@echo off
if exist "venv\" (
    call venv\Scripts\activate.bat
) else (
    echo 未找到 venv，正在创建虚拟环境...
    python -m venv venv
    call venv\Scripts\activate.bat
)

cmd /k