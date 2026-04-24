# Market Brief Telegram Bots

Dieses Repository enthaelt den Haupt-Bot (`telegram_bot.py`) und den Support-Bot (`support_bot.py`) fuer Market-Brief-Automation und Monitoring.

## Projektstruktur

- `telegram_bot.py`: Haupt-Bot (Market Brief, Auto-Jobs, Bedienung)
- `support_bot.py`: Monitoring/Support (Heartbeat, Fehler-Handling)
- `bot_monitoring.py`: Prozesssteuerung, Locking, Event-Logik
- `market_brief.py`, `batch_market_brief.py`: Kernlogik fuer Brief-Erzeugung
- `config/app_config.example.json`: Beispiel-Konfiguration ohne Secrets

## Sicherheit vor erstem Git-Push

Die folgenden Dateien sind bewusst in `.gitignore`:

- `config/app_config.json` (enthaelt Tokens/Keys)
- `.env*` (falls genutzt)
- Logs/Locks/Heartbeat/Event-Dateien
- lokale Caches und Laufzeitdaten

## Lokales Setup

1. Abhaengigkeiten installieren:
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. Konfiguration anlegen:
```powershell
Copy-Item config\\app_config.example.json config\\app_config.json
```

3. Tokens/Keys in `config/app_config.json` eintragen:
```powershell
notepad config\\app_config.json
```

## Start

Haupt-Bot:
```powershell
.venv\Scripts\python.exe telegram_bot.py
```

Support-Bot:
```powershell
.venv\Scripts\python.exe support_bot.py
```

## Wichtiger Hinweis

Wenn Tokens schon einmal in einer Datei mitgespeichert oder geteilt wurden, sollten sie beim Anbieter rotiert (neu erzeugt) werden.


