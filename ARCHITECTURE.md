# Projektarchitektur

## Zweck

Das Projekt betreibt einen Telegram-Haupt-Bot fuer Market-Brief-Ausgaben, einen separaten Support-Bot fuer Monitoring, Alerting und Prozesssteuerung sowie einen eigenen Live-Monitoring-Bot fuer Preisalarme. Die Fachlogik fuer Marktanalyse, globale Vorlauf-Signale, Artikelzusammenfassungen, Preisregeln und Batch-Laeufe ist in eigenstaendigen Python-Modulen gekapselt.

## Kernkomponenten

- `telegram_bot.py`
  Hauptanwendung fuer Telegram.
  Enthält:
  - Market-Brief-Kommandos
  - Auto-Market-Brief-Jobs
  - Support-Bot-Steuerung aus dem Haupt-Bot heraus
  - Pflege der `stock_categories.xml`
  - Batch-Market-Brief-Versand

- `support_bot.py`
  Separater Telegram-Support-Bot.
  Enthält:
  - Heartbeat-Ueberwachung des Haupt-Bots
  - Event-/Fehlerauswertung
  - Incident-Tracking fuer Market-Brief-Fehler
  - Alerting bei Bot-Ausfall, stale Heartbeat und deaktiviertem Auto-Market-Brief
  - Start/Stop/Restart fuer Haupt-Bot und Live-Monitoring-Bot

- `live_monitoring_bot.py`
  Separater Telegram-Bot fuer Live-Preisalarme.
  Liest Preisregeln aus `config/live_settings.xml`, ruft Preise ueber yfinance ab und sendet Trigger an den konfigurierten Zielchat.

- `bot_monitoring.py`
  Gemeinsame Laufzeit- und Monitoring-Basis.
  Verantwortlich fuer:
  - Lock-Dateien
  - Heartbeat-Dateien
  - JSONL-Event-Log
  - Start/Stop/Restart von Haupt-, Support- und Live-Monitoring-Bot
  - Prozessstatus-Ermittlung

- `market_brief.py`
  Zentrale Fachlogik fuer einen einzelnen Market Brief.
  Verantwortlich fuer:
  - Laden der XML-Eintraege
  - Kurs-, Volumen- und News-Daten via `yfinance`
  - RSI-, SMA-, RVOL- und Relative-Strength-Berechnungen
  - duales RSI-System (`RSI Markt/14d` und `RSI Tool/Short`)
  - globale Vorlauf-Logik ueber APAC/EU/USA
  - USD-Normalisierung ueber FX-Ticker
  - Formatierung der Textausgabe

- `batch_market_brief.py`
  Fuehrt `market_brief.py` fuer viele XML-Eintraege aus und schreibt eine Sammelausgabe.
  Enthält:
  - Laden und Filtern von XML-Queries
  - Subprozess-Aufrufe fuer Einzel-Briefs
  - Ergebniszusammenfassung
  - Einbau von `GLOBAL HOT TOPICS` und `GLOBALER VORLAUF`

## Datenquellen

- `config/stock_categories/stock_categories.xml`
  Fachliche Stammdaten fuer Indizes, Aktien und Themenlisten.
  Die Datei wird auch interaktiv ueber `/listenpflege` gepflegt. Der Add-Flow fragt zuerst
  Pflichtfelder ab und bietet danach per Button-Menue optionale Zusatzfelder an.

- `config/live_settings.xml`
  Lokale Live-Preisalarme fuer `price_monitor.py` und `live_monitoring_bot.py`.
  Diese Datei enthaelt private Alarmwerte wie `enabled`, `target_price`, `condition` und `interval_min`
  und ist nicht fuer Git gedacht.

- `config/app_config.json`
  Laufzeitkonfiguration fuer Tokens, Zugriffsrechte, Auto-Brief-Einstellungen und Support-Bot-Werte.

- Yahoo Finance via `yfinance`
  Primäre Marktdatenquelle fuer Kurse, Historien, News und FX-Ticker.

- Gemini API
  Optional fuer News-Zusammenfassungen.

## XML-Struktur

`stock_categories.xml` ist hierarchisch aufgebaut:

```xml
<stockCategories>
  <category name="...">
    <subcategory name="...">
      <index>
        <name>...</name>
        <ticker>...</ticker>
        <ticker_apac>...</ticker_apac>
        <ticker_eu>...</ticker_eu>
        <ticker_usa>...</ticker_usa>
        <isin>...</isin>
        <wkn>...</wkn>
        <trade_republic_aktie>ja|nein|unbekannt</trade_republic_aktie>
        <trade_republic_derivate>ja|nein|unbekannt</trade_republic_derivate>
        <land>...</land>
        <tag>...</tag>
        <description>...</description>
      </index>
    </subcategory>
  </category>
</stockCategories>
```

Verwendung der wichtigsten Felder:

- `ticker`
  Primär-Ticker/Fallback fuer Einzelabfragen.

- `ticker_apac`, `ticker_eu`, `ticker_usa`
  Markt-spezifische Ticker fuer die Global-Lead-Logik.
  Diese Felder sind fuer einfache Abfragen nicht zwingend, aber wichtig fuer saubere
  marktuebergreifende `market_brief`-Laeufe mit Zeit-Bruecke und Cross-Market-Vergleich.

- `isin`, `wkn`, `name`
  Alternative Such- und Identifikationsfelder.

- `trade_republic_aktie`, `trade_republic_derivate`
  Pflichtfelder fuer die Trade-Republic-Handelbarkeit als Aktie bzw. Derivat.
  Erlaubte Werte sind `ja`, `nein` und `unbekannt`.

- `land`
  Hilft bei automatischer Marktzuordnung vorhandener Primär-Ticker.

- `tag`, `description`
  Fachliche Zusatzinformationen fuer Einordnung, Pflege und spaetere Erweiterungen.

Live-Preisregeln liegen nicht in `stock_categories.xml`, sondern lokal in `config/live_settings.xml`.
Die Zuordnung erfolgt ueber Kategorie, Subkategorie und Query/Ticker des Instruments.

## Wichtige Datenfluesse

### 1. Einzelner Market Brief

1. `telegram_bot.py` oder CLI ruft `fetch_market_brief()` aus `market_brief.py` auf.
2. `market_brief.py` laedt den Eintrag aus der XML.
3. `yfinance` liefert Kurs-, Historien-, News- und FX-Daten.
4. Der Brief berechnet:
   - Markt-/Short-RSI
   - RVOL
   - Relative Strength
   - SMA/52W/Spread
   - globale Vorlauf-Signale
5. `print_text()` oder der Telegram-Bot liefert die formatierte Antwort aus.

### 2. Batch Market Brief

1. `batch_market_brief.py` laedt alle passenden XML-Eintraege.
2. Fuer jeden Eintrag wird `market_brief.py` als Subprozess gestartet.
3. Die Ergebnisse werden gesammelt, zusammengefasst und in eine Datei geschrieben.
4. Vor den Einzelresultaten stehen:
   - `GLOBAL HOT TOPICS & MARKT-SENTIMENT`
   - `GLOBALER VORLAUF (Pre-Market Check)`

### 3. Monitoring

1. `telegram_bot.py` schreibt regelmaessig Heartbeats ueber `bot_monitoring.py`.
2. Fehler im Haupt-Bot werden als JSONL-Events protokolliert.
3. `support_bot.py` liest Heartbeat und Events periodisch.
4. Der Support-Bot meldet:
   - Haupt-Bot gestoppt
   - Heartbeat veraltet
    - Auto-Market-Brief deaktiviert
    - neue Market-Brief-Fehler
5. Offene Fehler werden als Incidents gespeichert und koennen im Support-Bot auf geloest gesetzt werden.
6. `live_monitoring_bot.py` schreibt einen eigenen Heartbeat und kann im Support-Bot ueber `/support` -> `Live-Monitoring` gesteuert werden.

### 4. Listenpflege

1. `/listenpflege` startet einen interaktiven Pflege-Workflow fuer `stock_categories.xml`.
2. Kategorie und Subkategorie werden bevorzugt per Button gewaehlt, koennen bei Bedarf aber neu angelegt werden.
3. Beim Hinzufuegen werden zuerst die Pflichtfelder abgefragt:
   - `category`
   - `subcategory`
   - `name`
   - `ticker`
   - `isin`
   - `wkn`
   - `trade_republic_aktie`
   - `trade_republic_derivate`
4. Danach folgt ein optionales Button-Menue fuer Zusatzfelder:
   - `ticker_usa`
   - `ticker_eu`
   - `ticker_apac`
   - `land`
   - `tag`
   - `description`
5. Vor dem Speichern validiert der Bot Pflichtfelder sowie Duplikate bei Ticker, ISIN und WKN.
6. Beim Speichern wird die XML aktualisiert und zuvor ein Backup angelegt.

## Laufzeitdateien

- `.telegram_bot.lock`
- `.support_bot.lock`
- `.live_monitoring_bot.lock`
- `.telegram_bot.heartbeat.json`
- `.live_monitoring_bot.heartbeat.json`
- `telegram_bot_events.jsonl`
- `telegram_bot_process.log`
- `support_bot_process.log`
- `live_monitoring_bot_process.log`
- `support_bot_alert_state.json`

Diese Dateien sind Laufzeit-/Monitoring-Artefakte und gehoeren nicht in ein oeffentliches Repo.

## Nebenmodule

- `article_fetcher.py`
  Holt und bereinigt Artikeltext.

- `gemini_article_summary.py`
  Baut Prompts, ruft Gemini auf und cached Ergebnisse in SQLite.

- `gemini_article_client.py`
  Kleiner CLI-Client fuer Summaries.

- `batch_market_brief_client.py`
  Interaktive CLI fuer Batch-Laeufe.

- `terminal_client.py`
  Terminal-Auswahl fuer XML-Eintraege.

- `certificate_scraper.py`
  Eigenstaendiges Tool fuer Zertifikate-Suche.

- `dax_stand.py`
  Separates Tool fuer DAX-/Watchlist-Daten und XML-Erzeugung.

## Technische Leitlinien im aktuellen Stand

- Einzelfachlogik liegt in `market_brief.py`, nicht im Telegram-Handler.
- Monitoring ist zentralisiert in `bot_monitoring.py`.
- Support-Bot und Haupt-Bot laufen bewusst getrennt.
- XML ist die zentrale fachliche Stammdatenquelle.
- Batch-Ausgaben nutzen denselben Kern wie Einzelabfragen.
- Marktuebergreifende Vorlauf-Signale werden in USD normalisiert.
