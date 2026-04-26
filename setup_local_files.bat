@echo off
setlocal

cd /d "%~dp0"

set "PYTHON_VERSION_OK="
set "VENV_PYTHON_VERSION_OK="

echo [1/6] Pruefe Python-Virtualenv...
python -c "import sys; raise SystemExit(0 if ((3, 10) <= sys.version_info[:2] < (3, 14)) else 1)"
if errorlevel 1 (
    echo Fehler: Dieses Projekt benoetigt Python 3.10 bis 3.13.
    python --version
    echo Installiere eine passende Python-Version und starte das Setup danach erneut.
    goto Fail
)
if not exist ".venv\Scripts\python.exe" (
    echo Erstelle .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo Fehler: Konnte .venv nicht erstellen. Ist Python installiert und im PATH?
        goto Fail
    )
) else (
    echo .venv existiert bereits.
)

call ".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if ((3, 10) <= sys.version_info[:2] < (3, 14)) else 1)"
if errorlevel 1 (
    echo Fehler: Die vorhandene .venv verwendet keine unterstuetzte Python-Version.
    call ".venv\Scripts\python.exe" --version
    echo Loesche .venv und erstelle sie mit Python 3.10 bis 3.13 neu.
    goto Fail
)

echo [2/6] Erstelle lokale Konfiguration...
if not exist "config" mkdir "config"
if not exist "config\app_config.json" (
    if exist "config\app_config.example.json" (
        copy /Y "config\app_config.example.json" "config\app_config.json" >nul
        echo config\app_config.json wurde aus der Vorlage erstellt.
    ) else (
        echo Fehler: config\app_config.example.json fehlt.
        goto Fail
    )
) else (
    echo config\app_config.json existiert bereits.
)

echo [3/6] Erstelle lokale Arbeitsordner...
if not exist "market_brief_results" mkdir "market_brief_results"
if not exist "config\stock_categories" mkdir "config\stock_categories"
if not exist "config\stock_categories\backups" mkdir "config\stock_categories\backups"

echo [4/6] Initialisiere Laufzeitdateien...
if not exist "telegram_bot_process.log" type nul > "telegram_bot_process.log"
if not exist "support_bot_process.log" type nul > "support_bot_process.log"
if not exist "telegram_bot_events.jsonl" type nul > "telegram_bot_events.jsonl"
if not exist "support_bot_alert_state.json" (
    > "support_bot_alert_state.json" echo {
    >> "support_bot_alert_state.json" echo   "next_incident_id": 1,
    >> "support_bot_alert_state.json" echo   "open_incidents": {}
    >> "support_bot_alert_state.json" echo }
)

echo [5/6] Installiere Python-Abhaengigkeiten...
call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Fehler: Abhaengigkeiten konnten nicht installiert werden.
    goto Fail
)

call ".venv\Scripts\python.exe" -c "import telegram" >nul 2>nul
if errorlevel 1 (
    echo Fehler: Modul 'telegram' ist trotz Installation nicht verfuegbar.
    echo Pruefe Internetverbindung, Python-Version und pip-Ausgabe oben.
    goto Fail
)

echo [6/6] Fertig.
echo Jetzt config\app_config.json mit echten Tokens und IDs befuellen.
echo Danach starten mit:
echo   .venv\Scripts\python.exe telegram_bot.py
echo   .venv\Scripts\python.exe support_bot.py
echo.
echo Success: setup_local_files.bat wurde erfolgreich abgeschlossen.
goto EndWithPrompt

:Fail
echo.
echo Fehler: setup_local_files.bat wurde nicht erfolgreich abgeschlossen.

:EndWithPrompt
echo.
set /p EXIT_CONFIRM=Zum Schliessen Enter druecken...
endlocal
exit /b 0
