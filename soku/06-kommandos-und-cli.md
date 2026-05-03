# Kommandos und CLI

## Telegram-Menue des Haupt-Bots

Im Telegram-Bot-Menue des Haupt-Bots ist ein zentraler Command sichtbar:

- `/marketbrief_menu`: Market-Brief-Menue oeffnen

Nach dem Klick erscheinen Buttons fuer:

- `Einzelanalyse`: Name, Ticker, ISIN oder WKN eingeben und einzelnen Market Brief erstellen
- `Batch Market Brief`: interaktiven Batch-Workflow starten
- `Auto-Brief`: Auto-Market-Brief Status und Konfiguration per Buttons bearbeiten
- `Listenpflege`: XML-Instrumentenliste pflegen
- `Support-Bot`: Support-Bot Status und Steuerung aus dem Haupt-Bot heraus
- `Kategorien`: Kategorien und Subkategorien anzeigen
- `Bot testen`: Ping-Test ausfuehren

Die alten Direktbefehle wie `/marketbrief <query>`, `/marketbrief_start`, `/autobrief`, `/listenpflege` und `/supportbot` bleiben als Fallback im Code nutzbar, werden aber nicht mehr im Telegram-Command-Menue angezeigt.

### Support und Verwaltung

- `/listenpflege` fuehrt beim Hinzufuegen zuerst durch die Pflichtfelder `category`, `subcategory`, `name`, `ticker`, `isin`, `wkn`, `trade_republic_aktie`, `trade_republic_derivate`
- `/listenpflege` bietet danach per Button-Menue optionale Zusatzfelder wie `ticker_usa`, `ticker_eu`, `ticker_apac`, `land`, `tag` und `description`
- Beim Hinzufuegen kann zwischen `Manuell` und `Automatisch` gewaehlt werden.
- `Manuell` nutzt den bisherigen Eingabeablauf.
- `Automatisch` fragt ab, ob die Suche per Name, WKN oder ISIN laufen soll, und erzeugt per Gemini einen Vorschlag fuer alle Pflichtfelder und vorhandene Ticker USA/EU/APAC.
- `python price_monitor.py`: lokale Live-Preisregeln aus `config/live_settings.xml` pruefen
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

Die Regelbearbeitung kann Monitoring ein- oder ausschalten, Zielpreis, Bedingung und `interval_min` setzen sowie den aktuellen Kurs abfragen. Gespeichert wird in `config/live_settings.xml`, nicht in `stock_categories.xml`.

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
