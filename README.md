# Market Brief Telegram Bots

Dieses Repository enthaelt den Haupt-Bot (`telegram_bot.py`), den Support-Bot (`support_bot.py`) und den Live-Monitoring-Bot (`live_monitoring_bot.py`) fuer Market-Brief-Automation und Preisalarme.

## Projektstruktur

- `telegram_bot.py`: Haupt-Bot (Market Brief, Auto-Jobs, Bedienung)
- `support_bot.py`: Monitoring/Support (Heartbeat, Fehler-Handling)
- `live_monitoring_bot.py`: eigener Telegram-Bot fuer Live-Preisalarme aus `config/live_settings.xml`
- `bot_monitoring.py`: Prozesssteuerung, Locking, Event-Logik
- `market_brief.py`, `batch_market_brief.py`, `price_monitor.py`: Kernlogik fuer Brief-Erzeugung und Preisregeln
- `config/app_config.example.json`: Beispiel-Konfiguration ohne Secrets

## Sicherheit vor erstem Git-Push

Die folgenden Dateien sind bewusst in `.gitignore`:

- `config/app_config.json` (enthaelt Tokens/Keys)
- `config/live_settings.xml` (lokale Preis-Alarme)
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

Live-Monitoring-Bot direkt:
```powershell
.venv\Scripts\python.exe live_monitoring_bot.py
```

Alternativ ueber den Support-Bot mit `/support` oeffnen und dort `Live-Monitoring` waehlen.

## Wichtiger Hinweis

Wenn Tokens schon einmal in einer Datei mitgespeichert oder geteilt wurden, sollten sie beim Anbieter rotiert (neu erzeugt) werden.


