# Betrieb und Monitoring

## Single-Instance-Schutz

Sowohl Haupt-Bot als auch Support-Bot verwenden Lock-Dateien, damit nicht versehentlich mehrere Instanzen parallel laufen.

Relevante Dateien:

- `.telegram_bot.lock`
- `.support_bot.lock`

Die Lock-Dateien enthalten PID-Informationen. `bot_monitoring.py` kann diese lesen und bei inkonsistentem Zustand bereinigen.

## Heartbeat

Der Haupt-Bot schreibt periodisch einen Heartbeat nach:

- `.telegram_bot.heartbeat.json`

Die Datei enthaelt:

- `pid`
- `status`
- `updated_at`
- `details`

Das Heartbeat-Intervall ist im Code derzeit auf `30` Sekunden gesetzt.

Der Support-Bot bewertet den Zustand des Haupt-Bots anhand von:

- laufendem Prozess
- vorhandener Lock-Datei
- Aktualitaet des Heartbeats

## Event-Logging

Fehler und Betriebsereignisse werden als JSON Lines protokolliert:

- `telegram_bot_events.jsonl`

Jeder Eintrag enthaelt mindestens:

- `id`
- `timestamp`
- `source`
- `level`
- `message`
- `details`

Der Support-Bot liest neue Events inkrementell und kann daraus Incidents ableiten.

## Incident-Tracking

Der Support-Bot pflegt offene Fehler in:

- `support_bot_alert_state.json`

Enthalten sind insbesondere:

- `next_incident_id`
- `open_incidents`

Ein Incident entsteht, wenn der Support-Bot ein passendes Fehler-Event als Market-Brief-Problem klassifiziert.

## Prozesssteuerung

`bot_monitoring.py` stellt Funktionen bereit fuer:

- Haupt-Bot starten
- Haupt-Bot stoppen
- Haupt-Bot neu starten
- Support-Bot starten
- Support-Bot stoppen
- Support-Bot neu starten

Diese Funktionen werden vom Code genutzt, damit Bots nicht nur passiv melden, sondern den Betrieb aktiv beeinflussen koennen.

## Typische Betriebspruefung

### Haupt-Bot reagiert nicht

Pruefen:

- existiert `.telegram_bot.lock`
- wird `.telegram_bot.heartbeat.json` aktualisiert
- gibt es neue Eintraege in `telegram_bot_events.jsonl`
- steht etwas Relevantes in `telegram_bot_process.log`

Danach:

- ueber den Support-Bot `\status` abfragen
- falls noetig `\main_restart` ausfuehren

### Auto-Market-Brief kommt nicht an

Pruefen:

- ist `auto_market_brief.enabled` aktiv
- sind `category` und `subcategory` gueltig
- ist `chat_id` korrekt
- liegt die aktuelle Zeit innerhalb von `start_time` und `end_time`
- gibt es Fehler-Events zum Batch-Lauf

Hilfreiche Support-Bot-Kommandos:

- `\autobrief_chat` zeigt Ziel-Chat-ID und Kernkonfiguration des Auto-Briefs
- `\autobrief_chat_here` setzt den aktuellen Support-Bot-Chat direkt als Ziel
- `\autobrief_chat_set <chat_id>` setzt die Ziel-Chat-ID manuell und speichert sie in `config/app_config.json`

### Support-Bot meldet stale Heartbeat

Das weist meist auf eines von drei Problemen hin:

- Haupt-Bot ist abgestuerzt
- Haupt-Bot blockiert und aktualisiert den Heartbeat nicht mehr
- Lock-Datei und Prozessstatus sind inkonsistent

## Betriebsrisiken

Das Projekt hat aktuell einige betriebliche Annahmen:

- Windows-spezifische Prozesse und Batch-Skripte sind Teil des Standardbetriebs
- externe Dienste wie Yahoo Finance oder Gemini muessen erreichbar sein
- es gibt keine separate Persistenzschicht ausser Dateien
- es gibt keine automatisierte Selbstheilung ausser ueber die vorhandenen Restart-Funktionen

## Empfohlene Wartungsroutine

Taeglich oder bei Stoerungen sinnvoll:

- `telegram_bot_events.jsonl` auf neue Fehler pruefen
- `support_bot_alert_state.json` auf offene Incidents pruefen
- sicherstellen, dass `market_brief_results/` nicht unkontrolliert anwachsen
- alte Logs bei Bedarf archivieren oder rotieren
