# Setup und Start

## Voraussetzungen

Laut `setup_local_files.bat` erwartet das Projekt:

- Windows-Umgebung
- Python 3.10 bis 3.13
- Internetzugriff fuer Paketinstallation und externe Datenquellen

Die lokale Standardstruktur nutzt eine virtuelle Umgebung unter `.venv/`.

## Erstinstallation

### Empfohlener Weg

Das Projekt bringt mit `setup_local_files.bat` ein Setup-Skript fuer die lokale Inbetriebnahme mit.

Ausgefuehrte Aufgaben:

- Python-Version pruefen
- `.venv` erstellen, falls nicht vorhanden
- `config/app_config.json` aus `config/app_config.example.json` ableiten
- Arbeitsverzeichnisse und Laufzeitdateien anlegen
- Python-Abhaengigkeiten aus `requirements.txt` installieren

Start:

```powershell
.\setup_local_files.bat
```

### Manueller Weg

Falls das Setup bewusst manuell erfolgen soll:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item config\app_config.example.json config\app_config.json
```

Danach muessen Tokens, IDs und Auto-Brief-Einstellungen in `config/app_config.json` eingetragen werden.

## Konfiguration vorbereiten

Es gibt zwei uebliche Wege:

### Direkte Bearbeitung der JSON-Datei

```powershell
notepad config\app_config.json
```

### Interaktive Pruefung fehlender Werte

```powershell
.venv\Scripts\python.exe ensure_app_config.py
```

`ensure_app_config.py` fordert fehlende oder leere Pflichtfelder interaktiv ab und schreibt die Datei danach zurueck.

## Haupt-Bot starten

Direkt:

```powershell
.venv\Scripts\python.exe telegram_bot.py
```

Der Haupt-Bot benoetigt mindestens:

- `bot_token`
- zulassungsrelevante IDs in `allowed_user_ids` und optional `allowed_chat_ids`

## Support-Bot starten

Variante mit Helferskript:

```powershell
.\start_support_bot.bat
```

Direkt:

```powershell
.venv\Scripts\python.exe support_bot.py
```

Der Support-Bot benoetigt mindestens:

- `support_bot_token`
- `support_bot.notify_chat_id`

## Typischer Betriebsablauf

1. `setup_local_files.bat` ausfuehren
2. `config/app_config.json` mit echten Werten befuellen
3. Haupt-Bot starten
4. Support-Bot starten
5. im Haupt-Bot `\autobrief` oder `\autobrief_start` pruefen
6. im Support-Bot `\status` pruefen

## Wichtige erzeugte Laufzeitdateien

Beim Setup oder waehrend des Betriebs entstehen unter anderem:

- `telegram_bot_process.log`
- `support_bot_process.log`
- `telegram_bot_events.jsonl`
- `support_bot_alert_state.json`
- `.telegram_bot.lock`
- `.support_bot.lock`
- `.telegram_bot.heartbeat.json`

Diese Dateien sind Teil des laufenden Betriebs und keine eigentlichen Quelldateien.
