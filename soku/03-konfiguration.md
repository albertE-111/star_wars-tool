# Konfiguration

## Grunddatei

Die zentrale Laufzeitkonfiguration liegt in:

`config/app_config.json`

Als Vorlage dient:

`config/app_config.example.json`

## Beispielstruktur

```json
{
  "bot_token": "",
  "support_bot_token": "",
  "live_monitoring_bot_token": "",
  "gemini_api_key": "",
  "allowed_user_ids": "123456789",
  "allowed_chat_ids": "",
  "gemini_model": "gemma-3-27b-it",
  "support_bot": {
    "notify_chat_id": 123456789,
    "heartbeat_timeout_seconds": 120
  },
  "auto_market_brief": {
    "enabled": true,
    "start_time": "08:15",
    "end_time": "22:15",
    "interval_minutes": 60,
    "category": "",
    "subcategory": "",
    "with_news_summary": true,
    "send_detailed_result_message": false,
    "chat_id": 123456789,
    "last_run_at": ""
  },
  "live_monitoring_bot": {
    "chat_id": 123456789,
    "poll_seconds": 30
  }
}
```

## Top-Level-Felder

### `bot_token`

Telegram-Token des Haupt-Bots `telegram_bot.py`.

### `support_bot_token`

Telegram-Token des Support-Bots `support_bot.py`.

### `live_monitoring_bot_token`

Telegram-Token des separaten Live-Monitoring-Bots `live_monitoring_bot.py`.

### `gemini_api_key`

API-Key fuer Gemini-Zusammenfassungen. Wird fuer News- und Artikel-Summaries benoetigt.

### `allowed_user_ids`

Kommagetrennte Liste erlaubter Telegram-User-IDs.

Beispiel:

```json
"allowed_user_ids": "123456789,987654321"
```

### `allowed_chat_ids`

Optionale kommagetrennte Liste erlaubter Chat-IDs. Wenn gesetzt, werden nur diese Chats akzeptiert.

### `gemini_model`

Standardmodell fuer Gemini-Zusammenfassungen. Aktuell ist in der Vorlage `gemma-3-27b-it` gesetzt.

## Block `support_bot`

### `notify_chat_id`

Chat-ID, an die der Support-Bot Alerts und Incident-Meldungen senden darf.

### `heartbeat_timeout_seconds`

Timeout fuer die Bewertung, ob der Haupt-Bot noch gesund ist. Der Support-Bot verwendet mindestens das Doppelte des Heartbeat-Intervalls als Untergrenze.

## Block `auto_market_brief`

Diese Sektion steuert automatische Batch-Market-Briefs aus dem Haupt-Bot.

### `enabled`

Aktiviert oder deaktiviert die automatische Ausfuehrung.

### `start_time`

Start des erlaubten Zeitfensters im Format `HH:MM`.

### `end_time`

Ende des erlaubten Zeitfensters im Format `HH:MM`.

### `interval_minutes`

Intervall zwischen automatischen Laeufen.

### `category`

Optionaler Filter auf eine Kategorie aus der XML-Datei.

### `subcategory`

Optionaler Filter auf eine Unterkategorie aus der XML-Datei.

### `with_news_summary`

Steuert, ob News per Gemini zusammengefasst werden sollen.

### `send_detailed_result_message`

Wenn aktiv, verschickt der Bot zusaetzlich eine detailreichere Rueckmeldung zum Batch-Ergebnis.

### `chat_id`

Ziel-Chat fuer den automatischen Versand.

Die ID kann entweder direkt in `config/app_config.json` gepflegt oder ueber den Support-Bot gesetzt werden. Die Support-Bot-Kommandos `/autobrief_chat_here` und `/autobrief_chat_set <chat_id>` schreiben den neuen Wert dauerhaft in diese Datei.

### `last_run_at`

Zeitstempel des letzten Auto-Laufs. Wird vom Bot selbst gepflegt.

## Block `live_monitoring_bot`

Diese Sektion steuert den separaten Telegram-Bot fuer Live-Preisalarme.

### `chat_id`

Ziel-Chat fuer Preis-Trigger. Der Wert kann durch `/start` im Live-Monitoring-Bot gesetzt werden.

### `poll_seconds`

Takt, in dem der Live-Monitoring-Bot die XML-Regeln prueft. Der eigentliche Kursabruf pro Wert wird zusaetzlich durch `live_monitoring.interval_min` im XML-Eintrag begrenzt.

## Pflichtfelder fuer einen funktionierenden Betrieb

Praktisch unverzichtbar sind:

- `bot_token` fuer den Haupt-Bot `telegram_bot.py`
- `support_bot_token` fuer den Support-Bot `support_bot.py`
- `live_monitoring_bot_token` fuer `live_monitoring_bot.py`, wenn Live-Monitoring genutzt wird
- `gemini_api_key`, sofern Summaries genutzt werden sollen
- `allowed_user_ids`
- `support_bot.notify_chat_id`
- `auto_market_brief.chat_id`, wenn Auto-Brief aktiv genutzt wird

## Sicherheits- und Betriebsregeln

- `config/app_config.json` enthaelt Secrets und gehoert nicht in ein oeffentliches Repository.
- Nach versehentlicher Offenlegung muessen Tokens und API-Keys rotiert werden.
- Aenderungen an `auto_market_brief` koennen direkt das Laufzeitverhalten des Haupt-Bots aendern.
- Laufzeitaenderungen ueber den Support-Bot, insbesondere an `auto_market_brief.chat_id`, werden in `config/app_config.json` persistiert und bleiben nach einem Neustart erhalten.
- Der Live-Monitoring-Bot nutzt ein eigenes Telegram-Token, weil Telegram Polling pro Bot-Token nur von einem Prozess stabil betrieben werden sollte.
