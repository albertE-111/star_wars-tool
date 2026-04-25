@echo off
setlocal

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Fehler: .venv\Scripts\python.exe wurde nicht gefunden.
    echo Fuehre zuerst setup_local_files.bat aus.
    goto WaitForUser
)

if not exist "config" mkdir "config"

call ".venv\Scripts\python.exe" "ensure_app_config.py"
if errorlevel 1 goto WaitForUser

call ".venv\Scripts\python.exe" "support_bot.py"
if errorlevel 1 (
    echo.
    echo Support-Bot wurde nicht erfolgreich gestartet.
    goto WaitForUser
)

endlocal
exit /b 0

:WaitForUser
echo.
set /p EXIT_CONFIRM=Zum Schliessen Enter druecken...
endlocal
exit /b 1
