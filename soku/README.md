# Projektdokumentation

Diese Dokumentation beschreibt den aktuellen Stand des Repositories `star_wars-tool` und ist komplett unter `soku/` gebuendelt.

## Inhalt

- [01-ueberblick.md](C:/finance/star_wars-tool/soku/01-ueberblick.md): Zweck, Hauptfunktionen und Systembild
- [02-setup-und-start.md](C:/finance/star_wars-tool/soku/02-setup-und-start.md): lokale Einrichtung, Erststart und typische Startbefehle
- [03-konfiguration.md](C:/finance/star_wars-tool/soku/03-konfiguration.md): Aufbau von `config/app_config.json` und erklaerte Felder
- [04-architektur.md](C:/finance/star_wars-tool/soku/04-architektur.md): Module, Datenfluss und technische Verantwortung der Dateien
- [05-betrieb-und-monitoring.md](C:/finance/star_wars-tool/soku/05-betrieb-und-monitoring.md): Locking, Heartbeat, Logs, Fehlersuche und Betriebsablaeufe
- [06-kommandos-und-cli.md](C:/finance/star_wars-tool/soku/06-kommandos-und-cli.md): Telegram-Kommandos und lokale CLI-Skripte
- [07-dateien-und-verzeichnisse.md](C:/finance/star_wars-tool/soku/07-dateien-und-verzeichnisse.md): Datei- und Verzeichnisreferenz

## Zielgruppe

Die Doku richtet sich an Personen, die das Projekt:

- lokal aufsetzen
- betreiben oder ueberwachen
- konfigurieren
- erweitern oder debuggen

## Dokumentationsprinzip

Die Inhalte basieren auf dem aktuell vorhandenen Code im Repository, insbesondere auf:

- `telegram_bot.py`
- `support_bot.py`
- `bot_monitoring.py`
- `market_brief.py`
- `batch_market_brief.py`
- `ensure_app_config.py`
- `setup_local_files.bat`

Falls sich Verhalten oder Konfigurationsfelder spaeter aendern, sollten die entsprechenden Dateien in `soku/` zusammen mit dem Code aktualisiert werden.
