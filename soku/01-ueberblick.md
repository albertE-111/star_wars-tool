# Ueberblick

## Zweck des Projekts

Das Repository betreibt zwei Telegram-Bots und mehrere Hilfsskripte fuer marktbezogene Auswertungen:

- einen Haupt-Bot fuer Market Briefs, Batch-Laeufe, Auto-Ausfuehrungen und Listenpflege
- einen Support-Bot fuer Ueberwachung, Restart und Fehlerbearbeitung
- mehrere CLI-Skripte fuer lokale Analyse, Artikelabruf, Gemini-Zusammenfassungen und Zertifikate

Der Schwerpunkt liegt auf der halb- oder vollautomatischen Erstellung von Marktbriefings auf Basis einer XML-Instrumentenliste und externer Datenquellen.

## Kernfunktionen

### Haupt-Bot

Der Haupt-Bot in `telegram_bot.py` bietet unter anderem:

- Einzelabfrage eines Market Briefs per Query
- interaktive Batch-Auswahl fuer mehrere Eintraege
- automatische, zeitgesteuerte Batch-Laeufe
- Steuerung des Support-Bots
- Pflege von `config/stock_categories/stock_categories.xml`
- interaktiven Start des Zertifikate-Scrapers

### Support-Bot

Der Support-Bot in `support_bot.py` uebernimmt:

- Heartbeat-Ueberwachung des Haupt-Bots
- Auswertung von Fehler-Events
- Incident-Tracking fuer Market-Brief-Fehler
- Benachrichtigung in einen definierten Telegram-Chat
- Start, Stop und Restart des Haupt-Bots

### Analyse- und Hilfsskripte

Weitere Skripte decken Spezialaufgaben ab:

- `market_brief.py`: Einzelanalyse eines XML-Eintrags
- `batch_market_brief.py`: Batch-Ausfuehrung ueber viele XML-Eintraege
- `article_fetcher.py`: Artikeltext aus URL extrahieren
- `gemini_article_summary.py` und `gemini_article_client.py`: Artikel mit Gemini zusammenfassen
- `certificate_scraper.py`: Hebelprodukt-/Zertifikate-Suche
- `dax_stand.py`: Generierung oder Aktualisierung von DAX-bezogenen XML-Inhalten

## Fachliche Datenquellen

Das Projekt arbeitet mit mehreren Arten von Daten:

- lokale Konfiguration aus `config/app_config.json`
- Instrumentenstammdaten aus `config/stock_categories/stock_categories.xml`
- Marktdaten und News ueber `yfinance`
- Artikelinhalte ueber HTTP-Abruf mit `requests` und `BeautifulSoup`
- optionale KI-Zusammenfassungen ueber Gemini

## Systembild

```text
config/app_config.json
        |
        v
telegram_bot.py <----> bot_monitoring.py <----> support_bot.py
        |
        +----> batch_market_brief.py ----> market_brief.py
        |                                      |
        |                                      +--> yfinance
        |                                      +--> Artikelabruf
        |                                      +--> Gemini Summary
        |
        +----> certificate_scraper.py
        |
        +----> config/stock_categories/stock_categories.xml
```

## Was im Projekt nicht enthalten ist

Das Repository enthaelt aktuell keine:

- automatisierten Tests
- Docker-Konfiguration
- CI/CD-Pipeline
- Datenbankmigrationen oder separates Backend

Der Betrieb ist derzeit auf ein lokal gestartetes Python-Projekt unter Windows ausgerichtet.
