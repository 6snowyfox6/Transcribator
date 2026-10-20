@echo off
rem ---------------------------------------------------------------------
rem  Launches the graphical interface (no console window).
rem
rem  Keep this file ASCII-only: cmd.exe reads .bat in the OEM codepage
rem  and mangles Cyrillic comments into broken commands.
rem ---------------------------------------------------------------------
cd /d "%~dp0"

rem If setup never finished, the launcher would otherwise do nothing at all.
if not exist ".venv\Scripts\pythonw.exe" (
    echo.
    echo   Transcribator is not set up yet - finishing installation.
    echo   Nastroyka ne zavershena, zapuskayu doustanovku.
    echo.
    powershell -ExecutionPolicy Bypass -NoProfile -File "installer\bootstrap.ps1" -AppDir "%CD%"
    if not exist ".venv\Scripts\pythonw.exe" exit /b 1
)

start "Transcribator" ".venv\Scripts\pythonw.exe" "gui.py"
