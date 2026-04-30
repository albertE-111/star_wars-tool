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

- `/supportbot`: Status und Steuerung des Support-Bots aus dem Haupt-Bot heraus
- `/listenpflege`: XML-Instrumentenliste pflegen
- `/listenpflege` fuehrt beim Hinzufuegen zuerst durch die Pflichtfelder `category`, `subcategory`, `name`, `ticker`, `isin`, `wkn`, `trade_republic_aktie`, `trade_republic_derivate`
- `/listenpflege` bietet danach per Button-Menue optionale Zusatzfelder wie `ticker_usa`, `ticker_eu`, `ticker_apac`, `land`, `tag` und `description`
- `python price_monitor.py`: lokale Live-Preisregeln aus `live_monitoring` pruefen
- `/start`: Bot-Startnachricht und Uebersicht
- `/cancel`: laufende Konversation abbrechen

### Zertifikate

- `/certificate_scraper_start`: interaktiven Zertifikate-Workflow starten

## Telegram-Menue des Support-Bots

Im Telegram-Bot-Menue des Support-Bots ist nur ein Command sichtbar:

- `/support`: Support-Menue oeffnen

Nach dem Klick erscheinen Buttons fuer:

- `Gesamtstatus`: Status von Haupt-Bot, Auto-Brief und Live-Monitoring anzeigen
- `Haupt-Bot`: Start, Stop, Restart und Status
- `Live-Monitoring`: Start, Stop, Restart und Status des Live-Monitoring-Bots
- `Auto-Brief`: Zielchat anzeigen und aktuellen Support-Chat als Ziel setzen
- `Fehler`: letzte Fehler und offene Market-Brief-Fehler anzeigen
- `Diesen Chat speichern`: aktuellen Chat als Support-Ziel speichern
- `Bot testen`: Ping-Test ausfuehren

Hinweis: Die durch `/autobrief_chat_here` oder `/autobrief_chat_set` gesetzte Ziel-Chat-ID wird dauerhaft in `config/app_config.json` unter `auto_market_brief.chat_id` gespeichert.

## Telegram-Menue des Live-Monitoring-Bots

Im separaten Live-Monitoring-Bot `live_monitoring_bot.py` ist im Telegram-Bot-Menue nur ein Command sichtbar:

- `/live_monitoring`: Live-Monitoring-Menue oeffnen

Nach dem Klick erscheinen Buttons fuer die eigentlichen Aktionen:

- `Preisregel bearbeiten`: fuehrt ueber Kategorie, Subkategorie und Aktie zur Regelbearbeitung
- `Aktive Regeln`: Anzahl aktiver Regeln anzeigen, je Regel mit Button `Aktienname / Kurs` zum Bearbeiten und Button `Aus` zum Ausschalten
- `Status`: Ziel-Chat, Poll-Intervall und aktive Regeln anzeigen
- `Diesen Chat speichern`: aktuellen Chat als Ziel fuer Preis-Trigger speichern
- `Bot testen`: Ping-Test ausfuehren

Die Regelbearbeitung kann Monitoring ein- oder ausschalten, Zielpreis, Bedingung und `interval_min` setzen sowie den aktuellen Kurs abfragen.

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
