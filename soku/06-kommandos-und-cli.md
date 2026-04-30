# Kommandos und CLI

## Telegram-Kommandos des Haupt-Bots

Aus dem Code ersichtlich sind unter anderem folgende Commands:

### Marktbriefing

- `/marketbrief <query>`: einzelnen Market Brief fuer Name, Ticker, ISIN oder WKN abrufen
- `/marketbrief_start`: interaktiven Batch-Workflow starten

### Auto Market Brief

- `/autobrief`: Status und Konfiguration anzeigen
- `/autobrief_start`: interaktive Konfiguration starten
- `/autobrief_next`: naechsten geplanten Lauf anzeigen
- `/autobrief_set <start> <end> <interval_min> [news on|off]`: Zeitfenster und Intervall direkt setzen
- `/autobrief_filter [category] [subcategory]`: Filter setzen oder loeschen
- `/autobrief_on`: Automatik aktivieren
- `/autobrief_off`: Automatik deaktivieren

### Support und Verwaltung

- `/supportbot`: Status und Steuerung des Support-Bots
- `/listenpflege`: XML-Instrumentenliste pflegen
- `/listenpflege` fuehrt beim Hinzufuegen zuerst durch die Pflichtfelder `category`, `subcategory`, `name`, `ticker`, `isin`, `wkn`, `trade_republic_aktie`, `trade_republic_derivate`
- `/listenpflege` bietet danach per Button-Menue optionale Zusatzfelder wie `ticker_usa`, `ticker_eu`, `ticker_apac`, `land`, `tag` und `description`
- `python price_monitor.py`: lokale Live-Preisregeln aus `live_monitoring` pruefen
- `/start`: Bot-Startnachricht und Uebersicht
- `/cancel`: laufende Konversation abbrechen

### Zertifikate

- `/certificate_scraper_start`: interaktiven Zertifikate-Workflow starten

## Telegram-Kommandos des Support-Bots

Im Support-Bot sind unter anderem diese Commands registriert:

- `/status`: Status und Heartbeat des Haupt-Bots
- `/main_on`: Haupt-Bot starten
- `/main_off`: Haupt-Bot stoppen
- `/main_restart`: Haupt-Bot neu starten
- `/live_status`: Live-Monitoring-Bot Status anzeigen
- `/live_on`: Live-Monitoring-Bot starten
- `/live_off`: Live-Monitoring-Bot stoppen
- `/live_restart`: Live-Monitoring-Bot neu starten
- `/autobrief_chat`: aktuell konfigurierte Auto-Market-Brief-Chat-ID und zentrale Auto-Brief-Einstellungen anzeigen
- `/autobrief_chat_here`: den aktuellen Support-Bot-Chat als Ziel fuer den Auto-Market-Brief setzen
- `/autobrief_chat_set <chat_id>`: Ziel-Chat-ID fuer den Auto-Market-Brief manuell auf einen Zahlenwert setzen
- `/errors`: letzte Fehlermeldungen anzeigen
- `/open_errors`: offene Market-Brief-Fehler anzeigen
- `/resolve_error`: Fehler als geloest markieren
- `/start`: Startnachricht des Support-Bots

Hinweis: Die durch `/autobrief_chat_here` oder `/autobrief_chat_set` gesetzte Ziel-Chat-ID wird dauerhaft in `config/app_config.json` unter `auto_market_brief.chat_id` gespeichert.

## Telegram-Kommandos des Live-Monitoring-Bots

Im separaten Live-Monitoring-Bot `live_monitoring_bot.py` sind die Commands nach Arbeitsablauf sortiert:

Einrichtung:

- `/start`: aktuellen Chat als Ziel fuer Preis-Trigger speichern
- `/monitoring_setting`: interaktive Preisregel bearbeiten

Uebersicht:

- `/rules`: aktive Preisregeln anzeigen
- `/status`: Ziel-Chat, Poll-Intervall und aktive Regeln anzeigen

Bedienung:

- `/cancel`: laufende Bearbeitung abbrechen
- `/help`: Befehlsuebersicht anzeigen
- `/ping`: Bot testen

Der Dialog von `/monitoring_setting` fuehrt ueber Kategorie, Subkategorie und Aktie zur Regelbearbeitung. Dort koennen Monitoring ein- oder ausgeschaltet, Zielpreis, Bedingung und `interval_min` gesetzt sowie der aktuelle Kurs abgefragt werden.

## Wichtige lokale CLI-Skripte

### `market_brief.py`

Einzelanalyse eines XML-Eintrags:

```powershell
.venv\Scripts\python.exe market_brief.py "NVDA"
```

Wichtige Optionen:

- `--json`
- `--xml <pfad>`
- `--no-news-summary`
- `--gemini-model <modell>`

### `batch_market_brief.py`

Batch-Auswertung ueber mehrere XML-Eintraege:

```powershell
.venv\Scripts\python.exe batch_market_brief.py --category "Einzelaktien" --subcategory "Big Tech"
```

Wichtige Optionen:

- `--xml <pfad>`
- `--category <name>`
- `--subcategory <name>`
- `--output <datei>`
- `--limit <n>`
- `--with-news-summary`
- `--no-news-summary`

### `batch_market_brief_client.py`

Interaktive Terminalsteuerung fuer Batch-Laeufe:

```powershell
.venv\Scripts\python.exe batch_market_brief_client.py
```

### `terminal_client.py`

Interaktive lokale Auswahl eines Instruments aus der XML:

```powershell
.venv\Scripts\python.exe terminal_client.py
```

### `article_fetcher.py`

Artikeltext aus URL extrahieren:

```powershell
.venv\Scripts\python.exe article_fetcher.py "https://beispiel.de/artikel"
```

Optionen:

- `--title <titel>`
- `--json`

### `article_client.py`

Interaktive Variante fuer den Artikelabruf:

```powershell
.venv\Scripts\python.exe article_client.py
```

### `gemini_article_client.py`

Artikel laden und zusammenfassen:

```powershell
.venv\Scripts\python.exe gemini_article_client.py
```

### `certificate_scraper.py`

Lokaler Zertifikate-Scraper fuer einen Basiswert.

Je nach Implementierung wird das Skript im Haupt-Bot typischerweise interaktiv angestossen und schreibt Ergebnisdateien mit dem Muster:

`zertifikate_analyse_<isin>_<timestamp>.json`

### `ensure_app_config.py`

Fehlende Pflichtkonfiguration interaktiv abfragen:

```powershell
.venv\Scripts\python.exe ensure_app_config.py
```

### `dax_stand.py`

Hilfsskript fuer DAX-bezogene XML-Inhalte oder Stammdatenpflege. Vor produktiver Nutzung sollte geprueft werden, welche Ausgabedatei oder Zielstruktur im konkreten Lauf verwendet wird.
