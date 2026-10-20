@echo off
rem ============================================================
rem  QQBot Next - launcher
rem    [1/3] bootstrap virtualenv
rem    [2/3] verify dependencies   -> scripts\ops\check_deps.py
rem    [3/3] hand control to the bot
rem  Dependency logic lives in check_deps.py only: this shell is
rem  a thin wrapper so bat/sh can never drift apart.
rem ============================================================
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo   QQBot Next  ::  launcher
echo   ----------------------------------------------------------

rem ---------- [1/3] interpreter ----------
if exist "venv\Scripts\python.exe" (
    call :stage 1 interpreter "venv\Scripts\python.exe"
) else (
    call :stage 1 interpreter "creating venv (first run)"
    python -m venv venv
    if errorlevel 1 (
        echo.
        echo   [FAIL] could not create the virtualenv.
        echo          Install Python 3.10+ and make sure it is on PATH.
        endlocal
        exit /b 1
    )
)

rem ---------- [2/3] dependencies ----------
"venv\Scripts\python.exe" "scripts\ops\check_deps.py" --quiet
if errorlevel 1 (
    call :stage 2 dependencies "incomplete (see above)"
    echo.
    echo   Continuing anyway. If the bot exits with ModuleNotFoundError,
    echo   go online and run:  scripts\ops\requirements.bat
    echo.
) else (
    call :stage 2 dependencies "ok"
)

rem ---------- [3/3] launch ----------
call :stage 3 launch "main.py"
echo.
cmd /k "venv\Scripts\python.exe main.py"

endlocal
exit /b 0

rem ---------- helper: aligned status line ----------
:stage
set "qqn_label=%~2                "
echo   [%~1/3] !qqn_label:~0,16!  %~3
exit /b 0