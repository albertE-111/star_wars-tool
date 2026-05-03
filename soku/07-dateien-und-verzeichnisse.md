# Dateien und Verzeichnisse

## Projektwurzel

### Zentrale Anwendungsdateien

- `telegram_bot.py`: Haupt-Bot
- `support_bot.py`: Support- und Monitoring-Bot
- `live_monitoring_bot.py`: separater Telegram-Bot fuer Live-Preisalarme
- `bot_monitoring.py`: gemeinsame Prozess- und Eventlogik
- `market_brief.py`: Einzel-Market-Brief
- `batch_market_brief.py`: Batch-Ausfuehrung
- `price_monitor.py`: lokale Preisregel- und yfinance-Logik fuer Live-Monitoring

### Hilfsskripte

- `batch_market_brief_client.py`
- `terminal_client.py`
- `article_fetcher.py`
- `article_client.py`
- `gemini_article_client.py`
- `gemini_article_summary.py`
- `certificate_scraper.py`
- `dax_stand.py`
- `ensure_app_config.py`

### Setup- und Startskripte

- `setup_local_files.bat`
- `start_support_bot.bat`

### Vorhandene Projektdokumente ausserhalb von `soku/`

- `README.md`
- `ARCHITECTURE.md`

Diese Dateien sind noch im Repository vorhanden. Die strukturierte Gesamtdokumentation fuer das Projekt liegt aber jetzt unter `soku/`.

## Verzeichnis `config/`

### `config/app_config.example.json`

Vorlage fuer die lokale Konfiguration ohne produktive Secrets.

### `config/app_config.json`

Aktive lokale Konfiguration. Enthaelt Tokens, IDs und Bot-Einstellungen.

### `config/stock_categories/stock_categories.xml`

Fachliche Instrumentenliste. Sie ist fuer Market Briefs, Batch-Laeufe und Listenpflege zentral.

### `config/live_settings.xml`

Lokale Preis-Alarme fuer den Live-Monitoring-Bot und `price_monitor.py`.
Diese Datei wird nicht versioniert und schuetzt private Alarmwerte vor Git-Updates.

## Verzeichnis `market_brief_results/`

Standardziel fuer Batch-Ausgaben und weitere Analyseergebnisse. `batch_market_brief.py` erzeugt darin monatsbasierte Unterordner.

## Wichtige Laufzeitdateien

- `.telegram_bot.lock`: Lock des Haupt-Bots
- `.support_bot.lock`: Lock des Support-Bots
- `.live_monitoring_bot.lock`: Lock des Live-Monitoring-Bots
- `.telegram_bot.heartbeat.json`: Heartbeat des Haupt-Bots
- `.live_monitoring_bot.heartbeat.json`: Heartbeat des Live-Monitoring-Bots
- `telegram_bot_events.jsonl`: Event-Log als JSON Lines
- `telegram_bot_process.log`: Prozess- oder Konsolenlog des Haupt-Bots
- `support_bot_process.log`: Prozess- oder Konsolenlog des Support-Bots
- `live_monitoring_bot_process.log`: Prozess- oder Konsolenlog des Live-Monitoring-Bots
- `support_bot_alert_state.json`: offener Incident-Status des Support-Bots
- `gemini_article_summary_cache.sqlite`: lokaler Cache fuer Artikel-Zusammenfassungen

## Welche Dateien typischerweise versioniert werden sollten

Im Regelfall versionierbar:

- Python-Quelltexte
- Batch-Skripte
- Beispielkonfiguration
- XML-Grunddaten, wenn fachlich gewollt
- Dokumentation unter `soku/`

## Welche Dateien typischerweise nicht versioniert werden sollten

Normalerweise lokal und nicht fuer ein oeffentliches Repository gedacht:

- `config/app_config.json`
- `config/live_settings.xml`
- Lock-Dateien
- Heartbeat-Dateien
- Log-Dateien
- Event-Dateien
- SQLite-Caches
- lokale Ergebnisdateien unter `market_brief_results/`
