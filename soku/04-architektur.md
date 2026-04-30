# Architektur

## Hauptkomponenten

### `telegram_bot.py`

Zentrale Anwendung fuer den produktiven Telegram-Betrieb.

Verantwortung:

- Initialisierung des Haupt-Bots
- Laden und Speichern der Konfiguration
- Definition von Commands und Conversation-Handlern
- Einzelabruf von Market Briefs
- interaktive Batch-Auswahl
- Auto-Market-Brief-Konfiguration und Ausfuehrung
- Support-Bot-Steuerung aus dem Haupt-Bot heraus
- Listenpflege fuer `stock_categories.xml`
- Start des Zertifikate-Scrapers
- Heartbeat-Schreiben und Event-Logging

### `support_bot.py`

Separater Betriebs- und Monitoring-Bot.

Verantwortung:

- Freigabepruefung fuer User und Chats
- Ueberwachung von Heartbeat und Prozessstatus
- Lesen neuer Events aus `telegram_bot_events.jsonl`
- Klassifizierung von Market-Brief-Fehlern
- Fuehren offener Incidents in `support_bot_alert_state.json`
- Start, Stop und Restart des Haupt-Bots

### `bot_monitoring.py`

Gemeinsame technische Basis fuer beide Bots.

Verantwortung:

- Lock-Dateien
- Heartbeat-Datei
- JSON-Lese- und Schreibhelfer
- Prozessstatusermittlung
- Event-Append und Event-Read
- Start/Stop/Restart der Bot-Prozesse

## Fachmodule

### `market_brief.py`

Fachliche Einzelanalyse fuer einen XML-Eintrag.

Leistungen:

- Laden der XML-Instrumente
- Aufloesen eines Eintrags ueber Name, Ticker, ISIN oder WKN
- Abruf von Kurs- und News-Daten ueber `yfinance`
- technische Kennzahlen wie RSI und SMA
- globale Vorlauf-Logik ueber APAC, Europa und USA
- News-Zusammenfassung per Gemini
- Text- oder JSON-Ausgabe

### `batch_market_brief.py`

Batch-Ausfuehrung mehrerer Market Briefs per Subprozess.

Leistungen:

- Laden aller XML-Queries
- Filtern nach Kategorie und Unterkategorie
- optionales Limit fuer Testlaeufe
- Start einzelner `market_brief.py`-Laeufe
- Schreiben einer Sammelausgabe mit Auswertung

### `batch_market_brief_client.py`

Einfache lokale Bedienoberflaeche fuer Batch-Laeufe im Terminal.

### `article_fetcher.py`

Artikelabruf und Text-Extraktion ueber `requests` und `BeautifulSoup`.

### `gemini_article_summary.py`

Zusammenfassung von Artikeln mit lokaler Cache-Datei `gemini_article_summary_cache.sqlite`.

### `certificate_scraper.py`

Spezialskript fuer Zertifikate beziehungsweise Knock-Out-Produkte.

### `dax_stand.py`

Hilfsskript fuer DAX-bezogene Stammdaten beziehungsweise XML-Erzeugung.

## Datenhaltung

### Statische oder semistatische Daten

- `config/stock_categories/stock_categories.xml`
- Die Datei wird auch interaktiv ueber `/listenpflege` gepflegt.
- Der Add-Flow fuehrt zuerst durch die Pflichtfelder `category`, `subcategory`, `name`, `ticker`, `isin`, `wkn`, `trade_republic_aktie`, `trade_republic_derivate`.
- Danach koennen per Button-Menue optionale Zusatzfelder wie `ticker_usa`, `ticker_eu`, `ticker_apac`, `land`, `tag` und `description` gesetzt werden.
- `config/app_config.example.json`

### Laufzeitdaten

- `config/app_config.json`
- `.telegram_bot.heartbeat.json`
- `.telegram_bot.lock`
- `.support_bot.lock`
- `telegram_bot_events.jsonl`
- `support_bot_alert_state.json`
- `market_brief_results/`
- `telegram_bot_process.log`
- `support_bot_process.log`

## Datenfluss eines Market Briefs

```text
Telegram-Command /marketbrief oder Batch-Workflow
        |
        v
telegram_bot.py
        |
        v
market_brief.py oder batch_market_brief.py
        |
        +--> XML-Eintrag aus stock_categories.xml
        +--> Marktdaten/News via yfinance
        +--> optional Artikelabruf
        +--> optional Gemini Summary
        |
        v
Textausgabe im Chat oder Datei unter market_brief_results/
```

## Datenfluss des Monitorings

```text
telegram_bot.py ---- schreibt Heartbeat und Events ----> Dateien im Projektroot
                                                         |
                                                         v
                                                  support_bot.py
                                                         |
                                                         v
                                            Alerts, Incidents, Restart-Aktionen
```

## XML-Modell

Die Instrumentenliste ist hierarchisch aufgebaut:

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
        <live_monitoring>
          <enabled>false</enabled>
          <target_price></target_price>
          <condition>above|below</condition>
          <interval_min>5</interval_min>
        </live_monitoring>
        <land>...</land>
        <tag>...</tag>
        <description>...</description>
      </index>
    </subcategory>
  </category>
</stockCategories>
```

`trade_republic_aktie` und `trade_republic_derivate` sind Pflichtfelder mit den Werten `ja`, `nein` oder `unbekannt`.
`live_monitoring` steuert optionale Preisregeln fuer `price_monitor.py`; `condition` ist `above` oder `below`.
Eintraege koennen je nach Instrument zusaetzliche Felder wie `ticker_apac`, `ticker_eu`, `ticker_europe` oder `ticker_usa` enthalten.
Fuer einen sauberen marktuebergreifenden `market_brief` sind insbesondere `ticker_usa`, `ticker_eu` und `ticker_apac` sinnvoll, weil sie fuer Global-Lead-Logik, Zeit-Bruecke und Cross-Market-Vergleiche genutzt werden.
