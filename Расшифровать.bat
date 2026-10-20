@echo off
rem ---------------------------------------------------------------------
rem  Drag files or a folder onto this .bat, or just double-click it
rem  to process everything inside the "input" folder.
rem
rem  Keep this file ASCII-only: cmd.exe reads .bat in the OEM codepage
rem  and mangles Cyrillic comments into broken commands.
rem ---------------------------------------------------------------------
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   Setup is not finished. Run Transcribator shortcut - it will finish it.
    echo   Ustanovka ne zavershena. Zapustite yarlyk Transkribator.
    echo.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" transcribe.py %*
set CODE=%ERRORLEVEL%

rem Pause only when launched by double-click, not from an open terminal.
echo %cmdcmdline% | find /i "%~nx0" >nul
if not errorlevel 1 (
    echo.
    pause
)
exit /b %CODE%
