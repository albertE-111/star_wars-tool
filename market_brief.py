from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import io
import json
import logging
import os
import subprocess
import sys
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from article_fetcher import fetch_article
from gemini_article_summary import resolve_api_key, summarize_article_with_cache

XML_PATH = Path("config/stock_categories/stock_categories.xml")
GLOBAL_MARKET_SLOTS = {
    "apac": "TSE (Asien)",
    "europe": "XETRA/Tradegate (Europa)",
    "usa": "NASDAQ/NYSE (USA)",
}
FX_TO_USD_TICKERS = {
    "EUR": ("EURUSD=X", False),
    "GBP": ("GBPUSD=X", False),
    "JPY": ("USDJPY=X", True),
}
APAC_SENTIMENT_TICKERS = {
    "^N225": "Nikkei 225",
    "^HSI": "Hang Seng",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Liest einen Eintrag aus config/stock_categories/stock_categories.xml und zeigt yfinance-Daten an."
    )
    parser.add_argument(
        "query",
        help="Name, Ticker, ISIN oder WKN des Eintrags aus der XML.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Gibt die Antwort als JSON aus.",
    )
    parser.add_argument(
        "--xml",
        default=str(XML_PATH),
        help="Pfad zur XML-Datei. Standard: config/stock_categories/stock_categories.xml",
    )
    parser.add_argument(
        "--no-news-summary",
        action="store_true",
        help="Deaktiviert Gemini-Zusammenfassungen fuer Nachrichtenartikel.",
    )
    parser.add_argument(
        "--gemini-model",
        default="gemma-3-27b-it",
        help="Gemini-Modell fuer News-Zusammenfassungen.",
    )
    return parser.parse_args()


def load_yfinance():
    try:
        import yfinance as yf
    except ImportError as exc:
        raise SystemExit(
            "yfinance ist nicht installiert. Installiere es mit: pip install yfinance"
        ) from exc
    return yf


def load_pandas_ta():
    try:
        import pandas_ta as ta
    except ImportError as exc:
        raise SystemExit(
            "pandas_ta ist nicht installiert. Installiere es mit: pip install pandas_ta"
        ) from exc
    return ta


def normalize(value: str | None) -> str:
    return (value or "").strip().casefold()


def load_entries(xml_path: str) -> list[dict[str, str]]:
    root = ElementTree.parse(xml_path).getroot()
    entries: list[dict[str, str]] = []

    for category in root.findall("category"):
        category_name = category.attrib.get("name", "")
        for subcategory in category.findall("subcategory"):
            subcategory_name = subcategory.attrib.get("name", "")
            for item in subcategory.findall("index"):
                entry = {
                    "category": category_name,
                    "subcategory": subcategory_name,
                }
                for child in item:
                    entry[child.tag] = (child.text or "").strip()
                entries.append(entry)

    return entries


def resolve_entry_query(entry: dict[str, str]) -> str:
    candidates = [
        entry.get("ticker"),
        entry.get("ticker_usa"),
        entry.get("ticker_europe"),
        entry.get("ticker_eu"),
        entry.get("ticker_apac"),
        entry.get("isin"),
        entry.get("wkn"),
        entry.get("name"),
    ]
    for value in candidates:
        if value and str(value).strip():
            return str(value).strip()
    return ""


def load_index_entry(xml_path: str, query: str) -> dict[str, str]:
    needle = normalize(query)

    for entry in load_entries(xml_path):
        searchable = (
            entry.get("name"),
            entry.get("ticker"),
            entry.get("ticker_usa"),
            entry.get("ticker_europe"),
            entry.get("ticker_eu"),
            entry.get("ticker_apac"),
            entry.get("isin"),
            entry.get("wkn"),
        )
        if any(normalize(value) == needle for value in searchable):
            return entry

    raise RuntimeError(f"Kein XML-Eintrag fuer '{query}' gefunden.")


def to_datetime(value: Any) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    if hasattr(value, "to_pydatetime"):
        converted = value.to_pydatetime()
        if isinstance(converted, datetime):
            return converted if converted.tzinfo else converted.replace(tzinfo=UTC)

    if hasattr(value, "item"):
        try:
            return to_datetime(value.item())
        except Exception:
            return None

    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    return None


def normalize_timestamp(value: Any) -> str | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, UTC).isoformat()

    parsed = to_datetime(value)
    if parsed is not None:
        return parsed.astimezone(UTC).isoformat()

    if isinstance(value, str) and value.strip():
        return value.strip()

    return None


def first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def extract_calendar_items(calendar_data: Any) -> list[tuple[str, datetime]]:
    items: list[tuple[str, datetime]] = []

    if calendar_data is None:
        return items

    if isinstance(calendar_data, dict):
        iterator = calendar_data.items()
    elif hasattr(calendar_data, "to_dict"):
        try:
            iterator = calendar_data.to_dict().items()
        except Exception:
            return items
    else:
        return items

    for label, raw_value in iterator:
        values = raw_value if isinstance(raw_value, list) else [raw_value]
        for value in values:
            parsed = to_datetime(value)
            if parsed is not None:
                items.append((str(label), parsed.astimezone(UTC)))

    return items


def extract_earnings_dates(ticker: Any) -> list[tuple[str, datetime]]:
    items: list[tuple[str, datetime]] = []

    try:
        earnings_dates = ticker.get_earnings_dates(limit=6)
    except Exception:
        return items

    index = getattr(earnings_dates, "index", None)
    if index is None:
        return items

    for raw_value in index:
        parsed = to_datetime(raw_value)
        if parsed is not None:
            items.append(("Earnings Date", parsed.astimezone(UTC)))

    return items


def extract_info_dates(ticker: Any) -> list[tuple[str, datetime]]:
    items: list[tuple[str, datetime]] = []

    try:
        info = ticker.get_info()
    except Exception:
        return items

    candidates = [
        ("Earnings Start", info.get("earningsTimestampStart")),
        ("Earnings End", info.get("earningsTimestampEnd")),
        ("Ex-Dividend Date", info.get("exDividendDate")),
        ("Dividend Date", info.get("dividendDate")),
    ]

    for label, raw_value in candidates:
        parsed = to_datetime(raw_value)
        if parsed is None and isinstance(raw_value, (int, float)):
            parsed = datetime.fromtimestamp(raw_value, UTC)
        if parsed is not None:
            items.append((label, parsed.astimezone(UTC)))

    return items


def parse_news_item(item: dict[str, Any]) -> dict[str, str | None]:
    content = item.get("content") or {}
    canonical_url = content.get("canonicalUrl") or item.get("canonicalUrl") or {}
    click_through_url = content.get("clickThroughUrl") or item.get("clickThroughUrl") or {}

    title = first_non_empty(
        item.get("title"),
        content.get("title"),
    )
    publisher = first_non_empty(
        item.get("publisher"),
        content.get("provider", {}).get("displayName"),
        content.get("publisher"),
    )
    link = first_non_empty(
        item.get("link"),
        canonical_url.get("url") if isinstance(canonical_url, dict) else canonical_url,
        click_through_url.get("url") if isinstance(click_through_url, dict) else click_through_url,
    )
    published_at = normalize_timestamp(
        first_non_empty(
            item.get("providerPublishTime"),
            item.get("pubDate"),
            content.get("pubDate"),
            content.get("displayTime"),
        )
    )

    return {
        "title": title,
        "publisher": publisher,
        "link": link,
        "published_at_utc": published_at,
    }


def enrich_news_with_summaries(
    news_items: list[dict[str, str | None]],
    model: str,
) -> list[dict[str, str | None]]:
    api_key = resolve_api_key("")
    if not api_key:
        for item in news_items:
            item["summary_error"] = "Gemini API Key fehlt."
        return news_items

    def process_item(item: dict[str, str | None]) -> dict[str, str | None]:
        enriched = dict(item)
        link = item.get("link")
        if not link:
            enriched["summary_error"] = "Kein Artikellink verfuegbar."
            return enriched

        try:
            article_data = fetch_article(link, item.get("title") or "")
            summary_data = summarize_article_with_cache(
                article_url=article_data["url"],
                article_title=article_data["requested_title"] or article_data["page_title"],
                article_text=article_data["article_text"],
                api_key=api_key,
                model=model,
            )
            enriched["summary"] = summary_data["summary"]
        except Exception as exc:
            error_text = str(exc)
            if "Gemini API Fehler 429" in error_text:
                enriched["summary_error"] = (
                    "Gemini-Kontingent ueberschritten (HTTP 429). "
                    "Pruefe Plan/Billing und versuche es spaeter erneut."
                )
            else:
                enriched["summary_error"] = error_text

        return enriched

    if len(news_items) <= 1:
        return [process_item(item) for item in news_items]

    max_workers = min(4, len(news_items))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(process_item, news_items))


def fetch_global_market_news(yf: Any, query: str = "financial markets", limit: int = 7) -> list[dict[str, str | None]]:
    candidates: list[dict[str, str | None]] = []
    seen_links: set[str] = set()

    search_queries = [query, ""]
    for search_query in search_queries:
        try:
            search = yf.Search(search_query, news_count=10)
            raw_news = getattr(search, "news", []) or []
        except Exception:
            raw_news = []

        for item in raw_news:
            parsed_item = parse_news_item(item)
            link = parsed_item.get("link")
            if not link or link in seen_links:
                continue
            seen_links.add(link)
            if any(parsed_item.values()):
                candidates.append(parsed_item)

        if len(candidates) >= limit:
            break

    def published_sort_key(item: dict[str, str | None]) -> str:
        return item.get("published_at_utc") or ""

    return sorted(candidates, key=published_sort_key, reverse=True)[:limit]


def enrich_global_news_with_summaries(
    news_items: list[dict[str, str | None]],
    model: str,
) -> list[dict[str, str | None]]:
    api_key = resolve_api_key("")
    if not api_key:
        return [dict(item) for item in news_items]

    def process_item(item: dict[str, str | None]) -> dict[str, str | None]:
        enriched = dict(item)
        link = item.get("link")
        if not link:
            return enriched

        try:
            article_data = fetch_article(link, item.get("title") or "")
            summary_data = summarize_article_with_cache(
                article_url=article_data["url"],
                article_title=article_data["requested_title"] or article_data["page_title"],
                article_text=article_data["article_text"],
                api_key=api_key,
                model=model,
                prompt_style="macro_news",
            )
            enriched["summary"] = summary_data["summary"]
        except Exception as exc:
            error_text = str(exc)
            if "Gemini API Fehler 429" in error_text:
                enriched["summary_error"] = "Gemini-Limit erreicht."
            else:
                enriched["summary_error"] = error_text
        return enriched

    if len(news_items) <= 1:
        return [process_item(item) for item in news_items]

    max_workers = min(4, len(news_items))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(process_item, news_items))


def format_global_hot_topics_section(global_hot_topics: list[dict[str, str | None]]) -> str:
    lines = ["GLOBAL HOT TOPICS & MARKT-SENTIMENT"]
    if global_hot_topics:
        for item in global_hot_topics:
            title = item.get("title") or "Ohne Titel"
            publisher = item.get("publisher") or "Unbekannt"
            lines.append(f"  - {title} | {publisher}")
            if item.get("summary"):
                lines.append(f"    {item['summary']}")
            if item.get("link"):
                lines.append(f"    {item['link']}")
    else:
        lines.append("  Keine globalen Marktnachrichten verfuegbar.")
    return "\n".join(lines)


def build_global_hot_topics_section(
    include_news_summaries: bool = True,
    gemini_model: str = "gemma-3-27b-it",
) -> str:
    yf = load_yfinance()
    global_hot_topics = fetch_global_market_news(yf)
    if include_news_summaries and global_hot_topics:
        global_hot_topics = enrich_global_news_with_summaries(global_hot_topics, gemini_model)
    return format_global_hot_topics_section(global_hot_topics)


def infer_market_slot_from_ticker(ticker: str) -> str:
    normalized = ticker.strip().upper()
    if not normalized:
        return ""
    if normalized.startswith("^"):
        return ""
    if normalized.endswith(".T"):
        return "apac"
    if normalized.endswith((".DE", ".TG", ".L", ".PA", ".AS", ".MI", ".SW")):
        return "europe"
    if normalized.endswith(".O") or "." not in normalized:
        return "usa"
    return ""


def resolve_market_tickers(entry: dict[str, str]) -> dict[str, str]:
    tickers = {
        "apac": str(entry.get("ticker_apac", "")).strip(),
        "europe": str(entry.get("ticker_europe", "") or entry.get("ticker_eu", "")).strip(),
        "usa": str(entry.get("ticker_usa", "")).strip(),
    }

    primary_ticker = str(entry.get("ticker", "")).strip()
    primary_slot = infer_market_slot_from_ticker(primary_ticker)
    if primary_ticker and primary_slot and not tickers[primary_slot]:
        tickers[primary_slot] = primary_ticker

    if primary_ticker and not tickers["usa"] and primary_slot == "usa":
        tickers["usa"] = primary_ticker

    return tickers


def get_global_lead(now: datetime | None = None) -> dict[str, str]:
    utc_now = now.astimezone(UTC) if now is not None else datetime.now(UTC)
    current_time = utc_now.time()

    if time(0, 0) <= current_time < time(7, 0):
        active_slot = "apac"
        phase = "asia_window"
    elif time(7, 0) <= current_time < time(13, 30):
        active_slot = "europe"
        phase = "europe_window"
    elif time(13, 30) <= current_time < time(21, 0):
        active_slot = "usa"
        phase = "usa_window"
    else:
        active_slot = "usa"
        phase = "usa_recent_close"

    return {
        "active_slot": active_slot,
        "active_label": GLOBAL_MARKET_SLOTS[active_slot],
        "phase": phase,
        "utc_timestamp": utc_now.isoformat(timespec="minutes"),
    }


def build_market_priority(lead_context: dict[str, str]) -> list[str]:
    active_slot = lead_context["active_slot"]
    if active_slot == "apac":
        return ["apac", "europe", "usa"]
    if active_slot == "europe":
        return ["europe", "apac", "usa"]
    return ["usa", "europe", "apac"]


def select_primary_market_ticker(entry: dict[str, str], now: datetime | None = None) -> dict[str, str]:
    lead_context = get_global_lead(now)
    market_tickers = resolve_market_tickers(entry)
    selected_slot = ""
    selected_ticker = ""

    for slot in build_market_priority(lead_context):
        ticker = market_tickers.get(slot, "")
        if ticker:
            selected_slot = slot
            selected_ticker = ticker
            break

    if not selected_ticker:
        selected_ticker = str(entry.get("ticker", "")).strip()
        selected_slot = infer_market_slot_from_ticker(selected_ticker) or "usa"

    return {
        "selected_slot": selected_slot,
        "selected_ticker": selected_ticker,
        "lead_slot": lead_context["active_slot"],
        "lead_label": lead_context["active_label"],
        "lead_phase": lead_context["phase"],
        "utc_timestamp": lead_context["utc_timestamp"],
    }


def fetch_parallel_market_data(
    tickers: set[str],
    intraday_tickers: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    normalized_tickers = {ticker.strip() for ticker in tickers if ticker and ticker.strip()}
    if not normalized_tickers:
        return {}

    intraday_tickers = {ticker.strip() for ticker in (intraday_tickers or set()) if ticker and ticker.strip()}
    yf = load_yfinance()

    def worker(ticker: str) -> tuple[str, dict[str, Any]]:
        ticker_obj = yf.Ticker(ticker)
        try:
            daily_history = ticker_obj.history(period="7d", interval="1d")
        except Exception:
            daily_history = None
        try:
            monthly_daily_history = ticker_obj.history(period="1mo", interval="1d")
        except Exception:
            monthly_daily_history = None
        if ticker in intraday_tickers:
            try:
                intraday_history = ticker_obj.history(period="5d", interval="1h")
            except Exception:
                intraday_history = None
        else:
            intraday_history = None

        currency = ""
        try:
            fast_info = ticker_obj.fast_info
            currency = str(fast_info.get("currency") or "").strip().upper()
        except Exception:
            currency = ""

        return ticker, {
            "daily_history": daily_history,
            "monthly_daily_history": monthly_daily_history,
            "intraday_history": intraday_history,
            "currency": currency,
        }

    max_workers = min(12, len(normalized_tickers))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        return dict(executor.map(worker, sorted(normalized_tickers)))


def extract_latest_close(history: Any) -> float | None:
    if history is None or getattr(history, "empty", True):
        return None
    closes = history.get("Close")
    if closes is None:
        return None
    try:
        return float(closes.dropna().iloc[-1])
    except Exception:
        return None


def extract_latest_market_price(snapshot: dict[str, Any]) -> float | None:
    intraday_history = snapshot.get("intraday_history")
    intraday_price = extract_latest_close(intraday_history)
    if intraday_price is not None:
        return intraday_price
    return extract_latest_close(snapshot.get("daily_history"))


def currency_to_usd_rate(currency: str, snapshots: dict[str, dict[str, Any]]) -> float | None:
    normalized = currency.strip().upper()
    if not normalized:
        return None
    if normalized == "USD":
        return 1.0

    pair = FX_TO_USD_TICKERS.get(normalized)
    if pair is None:
        return None

    pair_ticker, invert = pair
    snapshot = snapshots.get(pair_ticker, {})
    rate = extract_latest_close(snapshot.get("daily_history"))
    if rate in (None, 0):
        return None
    if invert:
        return 1.0 / rate
    return rate


def compute_apac_sentiment_transfer(
    snapshots: dict[str, dict[str, Any]],
    lead_context: dict[str, str],
) -> dict[str, Any]:
    sentiment_items: list[dict[str, Any]] = []

    for ticker, label in APAC_SENTIMENT_TICKERS.items():
        snapshot = snapshots.get(ticker, {})
        history = snapshot.get("daily_history")
        if history is None or getattr(history, "empty", True):
            continue
        closes = history.get("Close")
        if closes is None:
            continue
        try:
            clean_closes = closes.dropna()
        except Exception:
            continue
        if len(clean_closes) < 2:
            continue
        previous_close = clean_closes.iloc[-2]
        latest_close = clean_closes.iloc[-1]
        if previous_close in (None, 0):
            continue
        change_percent = ((float(latest_close) - float(previous_close)) / float(previous_close)) * 100
        sentiment_items.append(
            {
                "ticker": ticker,
                "label": label,
                "change_percent": change_percent,
            }
        )

    if not sentiment_items:
        return {
            "score": None,
            "message": "APAC-Sentiment nicht verfuegbar.",
            "items": [],
        }

    score = sum(float(item["change_percent"]) for item in sentiment_items) / len(sentiment_items)
    if lead_context["active_slot"] == "europe":
        if score >= 1.0:
            message = "APAC schliesst klar positiv. Konstruktiver Bias fuer das EU-Opening."
        elif score <= -1.0:
            message = "APAC schliesst klar negativ. Erhoehter Druck fuer das EU-Opening."
        else:
            message = "APAC gemischt bis neutral. Kein klares Vorlauf-Signal fuer Europa."
    else:
        message = "APAC-Sentiment als Kontextsignal verfuegbar."

    return {
        "score": score,
        "message": message,
        "items": sentiment_items,
    }


def build_global_lead_items(
    entries: list[dict[str, str]],
    now: datetime | None = None,
) -> tuple[dict[str, str], list[dict[str, Any]], dict[str, Any]]:
    lead_context = get_global_lead(now)
    active_slot = lead_context["active_slot"]

    sentiment_tickers = set(APAC_SENTIMENT_TICKERS.keys())

    candidates: list[dict[str, Any]] = []
    active_tickers: set[str] = set()
    us_tickers: set[str] = set()

    for entry in entries:
        if entry.get("category") not in {"Einzelaktien", "Aktien"}:
            continue
        market_tickers = resolve_market_tickers(entry)
        active_ticker = market_tickers.get(active_slot, "")
        us_ticker = market_tickers.get("usa", "")
        if not active_ticker or not us_ticker or active_ticker == us_ticker:
            continue
        candidates.append(
            {
                "entry": entry,
                "market_tickers": market_tickers,
                "active_ticker": active_ticker,
                "us_ticker": us_ticker,
            }
        )
        active_tickers.add(active_ticker)
        us_tickers.add(us_ticker)

    if active_slot == "usa":
        sentiment_snapshots = fetch_parallel_market_data(sentiment_tickers)
        return lead_context, [], compute_apac_sentiment_transfer(sentiment_snapshots, lead_context)

    if not candidates:
        sentiment_snapshots = fetch_parallel_market_data(sentiment_tickers)
        return lead_context, [], compute_apac_sentiment_transfer(sentiment_snapshots, lead_context)

    snapshots = fetch_parallel_market_data(active_tickers | us_tickers, intraday_tickers=active_tickers)
    needed_currencies = {
        snapshots.get(candidate["active_ticker"], {}).get("currency", "").strip().upper()
        for candidate in candidates
    } | {
        snapshots.get(candidate["us_ticker"], {}).get("currency", "").strip().upper()
        for candidate in candidates
    }
    fx_tickers = {
        FX_TO_USD_TICKERS[currency][0]
        for currency in needed_currencies
        if currency and currency != "USD" and currency in FX_TO_USD_TICKERS
    }
    if fx_tickers:
        snapshots.update(fetch_parallel_market_data(fx_tickers))
    sentiment_snapshots = snapshots | fetch_parallel_market_data(sentiment_tickers - set(snapshots.keys()))
    apac_sentiment = compute_apac_sentiment_transfer(sentiment_snapshots, lead_context)

    leads: list[dict[str, Any]] = []
    for candidate in candidates:
        active_snapshot = snapshots.get(candidate["active_ticker"], {})
        us_snapshot = snapshots.get(candidate["us_ticker"], {})

        active_price = extract_latest_market_price(active_snapshot)
        us_close = extract_latest_close(us_snapshot.get("daily_history"))
        active_currency = str(active_snapshot.get("currency", "")).strip().upper() or "USD"
        us_currency = str(us_snapshot.get("currency", "")).strip().upper() or "USD"
        active_rate = currency_to_usd_rate(active_currency, snapshots)
        us_rate = currency_to_usd_rate(us_currency, snapshots)

        if active_price in (None, 0) or us_close in (None, 0) or active_rate in (None, 0) or us_rate in (None, 0):
            continue

        active_price_usd = float(active_price) * float(active_rate)
        us_close_usd = float(us_close) * float(us_rate)
        if us_close_usd == 0:
            continue

        difference_percent = ((active_price_usd - us_close_usd) / us_close_usd) * 100
        if abs(difference_percent) < 1.5:
            continue

        leads.append(
            {
                "name": candidate["entry"].get("name", candidate["active_ticker"]),
                "active_market_label": GLOBAL_MARKET_SLOTS[active_slot],
                "active_ticker": candidate["active_ticker"],
                "us_ticker": candidate["us_ticker"],
                "difference_percent": difference_percent,
                "active_price_usd": active_price_usd,
                "us_close_usd": us_close_usd,
            }
        )

    leads.sort(key=lambda item: abs(float(item["difference_percent"])), reverse=True)
    return lead_context, leads, apac_sentiment


def build_global_lead_section(
    entries: list[dict[str, str]],
    now: datetime | None = None,
) -> str:
    lead_context, leads, apac_sentiment = build_global_lead_items(entries, now=now)
    lines = [
        "🌍 GLOBALER VORLAUF (Pre-Market Check)",
        f"Leitmarkt: {lead_context['active_label']} | Stand (UTC): {lead_context['utc_timestamp']}",
    ]
    if apac_sentiment.get("score") is not None:
        lines.append(
            f"APAC-Sentiment: {float(apac_sentiment['score']):+.2f}% | {apac_sentiment['message']}"
        )
    else:
        lines.append(f"APAC-Sentiment: {apac_sentiment['message']}")

    if lead_context["active_slot"] == "usa":
        lines.append("  US-Markt ist aktiv oder juengst geschlossen. Kein auslaendischer Vorlauf aktiv.")
        return "\n".join(lines)

    if not leads:
        lines.append("  Keine Aktien mit Vorlauf-Signal > 1.5% gegenueber dem letzten US-Schluss.")
        return "\n".join(lines)

    for item in leads:
        direction = "ueber" if float(item["difference_percent"]) >= 0 else "unter"
        lines.append(
            f"  - {item['name']} | {item['active_market_label']} {item['active_ticker']} | "
            f"{abs(float(item['difference_percent'])):.2f}% {direction} US-Schluss"
        )
        lines.append(
            f"    Aktuell: {float(item['active_price_usd']):.2f} USD | "
            f"US-Schluss: {float(item['us_close_usd']):.2f} USD ({item['us_ticker']})"
        )

    return "\n".join(lines)


def should_fetch_event_data(entry: dict[str, str]) -> bool:
    return entry.get("category") in {"Einzelaktien", "Aktien"}


def should_fetch_info_data(entry: dict[str, str]) -> bool:
    return entry.get("category") in {"Einzelaktien", "Aktien"}


def is_stock_entry(entry: dict[str, str]) -> bool:
    return entry.get("category") in {"Einzelaktien", "Aktien"}


def detect_market_profile(symbol: str) -> dict[str, Any]:
    suffix = ""
    if "." in symbol:
        suffix = "." + symbol.rsplit(".", 1)[1].upper()

    profiles = {
        "USA": {
            "benchmark_symbol": "^NDX",
            "benchmark_name": "Nasdaq 100",
            "exchange_name": "NYSE/NASDAQ",
            "timezone": ZoneInfo("America/New_York"),
            "sessions": [(time(9, 30), time(16, 0))],
            "suffixes": {""},
        },
        "Europa_DE": {
            "benchmark_symbol": "^STOXX50E",
            "benchmark_name": "Euro Stoxx 50",
            "exchange_name": "Xetra",
            "timezone": ZoneInfo("Europe/Berlin"),
            "sessions": [(time(9, 0), time(17, 30))],
            "suffixes": {".DE"},
        },
        "Europa_AS": {
            "benchmark_symbol": "^STOXX50E",
            "benchmark_name": "Euro Stoxx 50",
            "exchange_name": "Amsterdam",
            "timezone": ZoneInfo("Europe/Amsterdam"),
            "sessions": [(time(9, 0), time(17, 30))],
            "suffixes": {".AS"},
        },
        "Europa_L": {
            "benchmark_symbol": "^STOXX50E",
            "benchmark_name": "Euro Stoxx 50",
            "exchange_name": "London",
            "timezone": ZoneInfo("Europe/London"),
            "sessions": [(time(8, 0), time(16, 30))],
            "suffixes": {".L"},
        },
        "Europa_PA": {
            "benchmark_symbol": "^STOXX50E",
            "benchmark_name": "Euro Stoxx 50",
            "exchange_name": "Paris",
            "timezone": ZoneInfo("Europe/Paris"),
            "sessions": [(time(9, 0), time(17, 30))],
            "suffixes": {".PA"},
        },
        "Asien_HK": {
            "benchmark_symbol": "^HSI",
            "benchmark_name": "Hang Seng",
            "exchange_name": "Hong Kong",
            "timezone": ZoneInfo("Asia/Hong_Kong"),
            "sessions": [(time(9, 30), time(12, 0)), (time(13, 0), time(16, 0))],
            "suffixes": {".HK"},
        },
        "Asien_T": {
            "benchmark_symbol": "^HSI",
            "benchmark_name": "Hang Seng",
            "exchange_name": "Tokio",
            "timezone": ZoneInfo("Asia/Tokyo"),
            "sessions": [(time(9, 0), time(11, 30)), (time(12, 30), time(15, 0))],
            "suffixes": {".T"},
        },
        "Asien_SS": {
            "benchmark_symbol": "^HSI",
            "benchmark_name": "Hang Seng",
            "exchange_name": "Shanghai",
            "timezone": ZoneInfo("Asia/Shanghai"),
            "sessions": [(time(9, 30), time(11, 30)), (time(13, 0), time(15, 0))],
            "suffixes": {".SS"},
        },
    }

    for profile in profiles.values():
        if suffix in profile["suffixes"]:
            return profile
    return profiles["USA"]


def compute_market_session_state(profile: dict[str, Any], now_utc: datetime | None = None) -> dict[str, Any]:
    now_utc = now_utc or datetime.now(UTC)
    local_now = now_utc.astimezone(profile["timezone"])
    current_time = local_now.time()
    is_weekend = local_now.weekday() >= 5
    total_minutes = 0.0
    elapsed_minutes = 0.0

    for start, end in profile["sessions"]:
        session_minutes = (
            datetime.combine(local_now.date(), end) - datetime.combine(local_now.date(), start)
        ).total_seconds() / 60
        total_minutes += session_minutes
        if current_time <= start:
            continue
        if current_time >= end:
            elapsed_minutes += session_minutes
            continue
        elapsed_minutes += (
            datetime.combine(local_now.date(), current_time) - datetime.combine(local_now.date(), start)
        ).total_seconds() / 60

    is_open = (not is_weekend) and any(start <= current_time <= end for start, end in profile["sessions"])
    progress_ratio = 1.0
    if is_open and total_minutes > 0:
        progress_ratio = max(elapsed_minutes / total_minutes, 0.05)

    return {
        "is_open": is_open,
        "status_text": "GEOEFFNET" if is_open else "GESCHLOSSEN",
        "local_time": local_now.isoformat(),
        "progress_ratio": progress_ratio,
    }


def safe_fast_info_value(info: Any, key: str) -> Any:
    try:
        return info.get(key)
    except Exception:
        return None


@contextlib.contextmanager
def suppress_process_output() -> Any:
    previous_disable_level = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    stderr_fd = os.dup(2)
    stdout_fd = os.dup(1)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 2)
        os.dup2(devnull, 1)
        yield
    finally:
        os.dup2(stderr_fd, 2)
        os.dup2(stdout_fd, 1)
        os.close(devnull)
        os.close(stderr_fd)
        os.close(stdout_fd)
        logging.disable(previous_disable_level)


def run_isolated_yfinance_json(code: str, symbol: str) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [sys.executable, "-c", code, symbol],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except Exception:
        return {}

    stdout = (result.stdout or "").strip()
    if not stdout:
        return {}

    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def safe_info_dict(ticker: Any) -> dict[str, Any]:
    symbol = getattr(ticker, "ticker", "")
    if not symbol:
        return {}

    code = (
        "import json, sys, yfinance as yf\n"
        "symbol = sys.argv[1]\n"
        "data = {'bid': None, 'ask': None, 'volume': None, 'averageVolume': None}\n"
        "try:\n"
        "    info = yf.Ticker(symbol).info or {}\n"
        "    for key in data:\n"
        "        value = info.get(key)\n"
        "        if value is not None:\n"
        "            data[key] = value\n"
        "except Exception:\n"
        "    pass\n"
        "print(json.dumps(data))\n"
    )
    return run_isolated_yfinance_json(code, symbol)


def fetch_quote_data(symbol: str) -> dict[str, Any]:
    code = (
        "import json, sys, yfinance as yf\n"
        "symbol = sys.argv[1]\n"
        "data = {'price': None, 'previous_close': None, 'currency': None, 'source': None}\n"
        "ticker = yf.Ticker(symbol)\n"
        "try:\n"
        "    info = ticker.fast_info\n"
        "    price = info.get('lastPrice')\n"
        "    previous_close = info.get('previousClose')\n"
        "    currency = info.get('currency')\n"
        "    if price is not None:\n"
        "        data.update({'price': price, 'previous_close': previous_close, 'currency': currency, 'source': 'fast_info'})\n"
        "except Exception:\n"
        "    pass\n"
        "if data['price'] is None:\n"
        "    try:\n"
        "        info = ticker.info or {}\n"
        "        price = info.get('regularMarketPrice') or info.get('currentPrice') or info.get('previousClose')\n"
        "        previous_close = info.get('regularMarketPreviousClose') or info.get('previousClose')\n"
        "        currency = info.get('currency')\n"
        "        if price is not None:\n"
        "            data.update({'price': price, 'previous_close': previous_close, 'currency': currency, 'source': 'info'})\n"
        "    except Exception:\n"
        "        pass\n"
        "if data['price'] is None:\n"
        "    try:\n"
        "        history = ticker.history(period='7d', interval='1d')\n"
        "        closes = history.get('Close') if history is not None else None\n"
        "        if closes is not None:\n"
        "            closes = closes.dropna()\n"
        "            if len(closes) >= 1:\n"
        "                data['price'] = float(closes.iloc[-1])\n"
        "                data['source'] = 'history'\n"
        "            if len(closes) >= 2:\n"
        "                data['previous_close'] = float(closes.iloc[-2])\n"
        "        if not data['currency']:\n"
        "            try:\n"
        "                data['currency'] = ticker.fast_info.get('currency')\n"
        "            except Exception:\n"
        "                pass\n"
        "    except Exception:\n"
        "        pass\n"
        "print(json.dumps(data))\n"
    )
    return run_isolated_yfinance_json(code, symbol)


def compute_rsi(closes: Any, period: int = 14) -> float | None:
    if closes is None or len(closes) < period + 1:
        return None

    ta = load_pandas_ta()
    try:
        clean_closes = closes.dropna()
    except Exception:
        clean_closes = closes
    if clean_closes is None or len(clean_closes) < period + 1:
        return None

    try:
        rsi = ta.rsi(clean_closes, length=period)
    except Exception:
        return None

    try:
        latest = rsi.dropna().iloc[-1]
    except Exception:
        return None
    return float(latest)


def compute_history_metrics(history: Any) -> dict[str, float | None]:
    if history is None or getattr(history, "empty", True):
        return {
            "sma_50": None,
            "sma_200": None,
            "high_52w": None,
            "low_52w": None,
        }

    closes = history.get("Close")
    highs = history.get("High")
    lows = history.get("Low")

    if closes is None or len(closes) == 0:
        return {
            "sma_50": None,
            "sma_200": None,
            "high_52w": None,
            "low_52w": None,
        }

    sma_50 = closes.rolling(window=50, min_periods=50).mean()
    sma_200 = closes.rolling(window=200, min_periods=200).mean()

    def latest_or_none(series: Any) -> float | None:
        try:
            return float(series.dropna().iloc[-1])
        except Exception:
            return None

    try:
        high_52w = float(highs.max()) if highs is not None else None
    except Exception:
        high_52w = None
    try:
        low_52w = float(lows.min()) if lows is not None else None
    except Exception:
        low_52w = None

    return {
        "sma_50": latest_or_none(sma_50),
        "sma_200": latest_or_none(sma_200),
        "high_52w": high_52w,
        "low_52w": low_52w,
    }


def build_dual_rsi_metrics(
    *,
    intraday_history: Any,
    daily_history: Any,
) -> dict[str, Any]:
    intraday_closes = intraday_history.get("Close") if intraday_history is not None else None
    daily_closes = daily_history.get("Close") if daily_history is not None else None

    intraday_rsi = compute_rsi(intraday_closes, 14)
    standard_rsi = compute_rsi(daily_closes, 14)
    daily_history_available = standard_rsi is not None
    note = "" if daily_history_available else "(nur Intraday-Daten verfuegbar)"

    difference = None
    if standard_rsi is not None and intraday_rsi is not None:
        difference = intraday_rsi - standard_rsi

    warning = ""
    if difference is not None and difference > 15:
        warning = (
            "⚠️ ACHTUNG: Kurzfristige Ueberhitzung (Blow-off). "
            "Markt-RSI ist noch deutlich niedriger. Hohes Rueckschlagrisiko!"
        )
    elif difference is not None and difference < -15:
        warning = (
            "ℹ️ INFO: Kurzfristiger Panik-Verkauf bei stabilem Gesamttrend. "
            "Moegliche Kaufgelegenheit."
        )

    return {
        "market_rsi_14d": standard_rsi,
        "intraday_rsi_short": intraday_rsi,
        "difference": difference,
        "warning": warning,
        "daily_history_available": daily_history_available,
        "note": note,
    }


def compute_history_volume_metrics(history: Any) -> dict[str, int | None]:
    if history is None or getattr(history, "empty", True):
        return {"volume": None, "average_volume": None}

    volumes = history.get("Volume")
    if volumes is None or len(volumes) == 0:
        return {"volume": None, "average_volume": None}

    try:
        latest_volume = volumes.dropna().iloc[-1]
    except Exception:
        latest_volume = None

    try:
        average_volume = volumes.dropna().tail(50).mean()
    except Exception:
        average_volume = None

    return {
        "volume": int(latest_volume) if latest_volume not in (None, "") else None,
        "average_volume": int(average_volume) if average_volume not in (None, "") else None,
    }


def compute_benchmark_change_percent(yf: Any, symbol: str = "^NDX") -> float | None:
    ticker = yf.Ticker(symbol)
    history = ticker.history(period="20d")
    if history is None or getattr(history, "empty", True):
        return None

    closes = history.get("Close")
    if closes is None:
        return None

    closes = closes.dropna()
    if len(closes) < 2:
        return None

    previous_close = closes.iloc[-2]
    latest_close = closes.iloc[-1]
    if previous_close in (None, 0):
        return None

    return float(((latest_close - previous_close) / previous_close) * 100)


def compute_analysis_metrics(
    *,
    entry: dict[str, str],
    history: Any,
    price: Any,
    change_percent: Any,
    sma_50: Any,
    benchmark_change_percent: float | None,
    market_profile: dict[str, Any] | None,
    market_session: dict[str, Any] | None,
) -> dict[str, Any]:
    metrics = {
        "relative_strength": None,
        "rvol": None,
        "volume_check": "Index/Kein Volumen",
        "volume_status": "Index/Kein Volumen",
        "sma_50_distance_percent": None,
        "volume_current": None,
        "volume_average_10d": None,
        "home_exchange_status": None,
        "home_exchange_name": None,
        "benchmark_name": None,
    }

    if market_profile is not None:
        metrics["home_exchange_name"] = market_profile["exchange_name"]
        metrics["benchmark_name"] = market_profile["benchmark_name"]
    if market_session is not None:
        metrics["home_exchange_status"] = market_session["status_text"]

    if change_percent not in (None, "") and benchmark_change_percent not in (None, ""):
        try:
            metrics["relative_strength"] = float(change_percent) - float(benchmark_change_percent)
        except (TypeError, ValueError):
            metrics["relative_strength"] = None

    if price not in (None, "") and sma_50 not in (None, "", 0):
        try:
            metrics["sma_50_distance_percent"] = ((float(price) - float(sma_50)) / float(sma_50)) * 100
        except (TypeError, ValueError, ZeroDivisionError):
            metrics["sma_50_distance_percent"] = None

    if not is_stock_entry(entry):
        return metrics

    if history is None or getattr(history, "empty", True):
        return metrics

    volumes = history.get("Volume")
    if volumes is None:
        return metrics

    clean_volumes = volumes.dropna()
    if len(clean_volumes) == 0:
        return metrics

    try:
        latest_volume = float(clean_volumes.iloc[-1])
    except Exception:
        return metrics
    metrics["volume_current"] = int(latest_volume)

    baseline = clean_volumes.iloc[-11:-1] if len(clean_volumes) >= 11 else clean_volumes.iloc[:-1].tail(10)
    if len(baseline) == 0:
        return metrics

    try:
        average_10d = float(baseline.mean())
    except Exception:
        return metrics

    if average_10d <= 0:
        return metrics
    adjusted_average_10d = average_10d
    if market_session and market_session.get("is_open"):
        adjusted_average_10d = average_10d * float(market_session.get("progress_ratio", 1.0))
    metrics["volume_average_10d"] = int(adjusted_average_10d)

    if adjusted_average_10d <= 0:
        return metrics

    rvol = latest_volume / adjusted_average_10d
    metrics["rvol"] = float(rvol)
    if rvol > 1.5:
        status = "Massives Volumen / Institutioneller Druck"
    elif rvol < 0.7:
        status = "Geringes Interesse / Konsolidierung"
    else:
        status = "Normales Handelsinteresse"

    metrics["volume_check"] = f"RVOL {rvol:.2f} ({status})"
    metrics["volume_status"] = status
    return metrics


def compute_spread(bid: Any, ask: Any) -> float | str:
    if bid in (None, "", 0, 0.0) or ask in (None, "", 0, 0.0):
        return "N/A"
    try:
        return float(ask) - float(bid)
    except (TypeError, ValueError):
        return "N/A"


def extract_next_earnings_date(ticker: Any) -> str | None:
    calendar_data = getattr(ticker, "calendar", None)
    for _, when in sorted(extract_calendar_items(calendar_data), key=lambda item: item[1]):
        return when.isoformat()
    return None


def fetch_market_volatility(yf: Any) -> dict[str, dict[str, Any]]:
    volatility_symbols = {
        "vix_us": "^VIX",
        "vstoxx_eu": "^V1X",
    }
    result: dict[str, dict[str, Any]] = {}

    for label, symbol in volatility_symbols.items():
        code = (
            "import json, sys, yfinance as yf\n"
            "symbol = sys.argv[1]\n"
            "data = {'price': None, 'previous_close': None}\n"
            "try:\n"
            "    info = yf.Ticker(symbol).fast_info\n"
            "    data['price'] = info.get('lastPrice')\n"
            "    data['previous_close'] = info.get('previousClose')\n"
            "except Exception:\n"
            "    pass\n"
            "print(json.dumps(data))\n"
        )
        quote_data = run_isolated_yfinance_json(code, symbol)
        price = quote_data.get("price")
        previous_close = quote_data.get("previous_close")
        change = None
        change_percent = None
        if price is not None and previous_close not in (None, 0):
            change = price - previous_close
            change_percent = (change / previous_close) * 100

        if price is None:
            result[label] = {
                "ticker": symbol,
                "price": None,
                "previous_close": None,
                "change": None,
                "change_percent": None,
            }
            continue

        result[label] = {
            "ticker": symbol,
            "price": float(price) if price is not None else None,
            "previous_close": float(previous_close) if previous_close is not None else None,
            "change": float(change) if change is not None else None,
            "change_percent": float(change_percent) if change_percent is not None else None,
        }

    return result


def format_optional_number(value: Any, suffix: str = "") -> str:
    if value in (None, "", "N/A"):
        return "N/A"
    try:
        return f"{float(value):,.2f}{suffix}"
    except (TypeError, ValueError):
        return str(value)


def format_dual_rsi_line(label: str, value: Any, note: str = "") -> str:
    text = format_optional_number(value)
    if note:
        text = f"{text} {note}".strip()
    return f"  - {label:<24} {text}"


def fetch_market_brief(
    entry: dict[str, str],
    include_news_summaries: bool = True,
    gemini_model: str = "gemma-3-27b-it",
) -> dict[str, Any]:
    yf = load_yfinance()
    primary_market = select_primary_market_ticker(entry)
    market_tickers = resolve_market_tickers(entry)
    symbol = primary_market["selected_ticker"] or entry.get("ticker")
    if not symbol:
        raise RuntimeError(f"Der XML-Eintrag '{entry.get('name', 'Unbekannt')}' hat keinen Ticker.")

    ticker = yf.Ticker(symbol)
    global_hot_topics = enrich_global_news_with_summaries(fetch_global_market_news(yf), gemini_model)
    quote_data = fetch_quote_data(symbol)
    price = quote_data.get("price")
    previous_close = quote_data.get("previous_close")
    currency = quote_data.get("currency") or "USD"

    if price is None:
        raise RuntimeError(
            f"Kein aktueller Kurs fuer {entry.get('name', symbol)} ({symbol}) erhalten. "
            "Yahoo Finance hat weder aus fast_info, info noch history einen verwertbaren Wert geliefert."
        )

    change = None
    change_percent = None
    if previous_close not in (None, 0):
        change = price - previous_close
        change_percent = (change / previous_close) * 100

    history = ticker.history(period="1y")
    analysis_history = ticker.history(period="20d")
    intraday_history = ticker.history(period="5d", interval="1h")
    try:
        daily_rsi_history = ticker.history(period="1mo", interval="1d")
    except Exception:
        daily_rsi_history = None
    history_metrics = compute_history_metrics(history)
    history_volume_metrics = compute_history_volume_metrics(history)
    dual_rsi_metrics = build_dual_rsi_metrics(
        intraday_history=intraday_history,
        daily_history=daily_rsi_history,
    )
    market_profile = detect_market_profile(symbol) if is_stock_entry(entry) else None
    market_session = compute_market_session_state(market_profile) if market_profile is not None else None
    benchmark_change_percent = (
        compute_benchmark_change_percent(yf, market_profile["benchmark_symbol"])
        if market_profile is not None
        else compute_benchmark_change_percent(yf, "^NDX")
    )
    bid = None
    ask = None
    volume = history_volume_metrics["volume"]
    average_volume = history_volume_metrics["average_volume"]

    if should_fetch_info_data(entry):
        info_dict = safe_info_dict(ticker)
        bid = info_dict.get("bid")
        ask = info_dict.get("ask")
        volume = info_dict.get("volume") or volume
        average_volume = info_dict.get("averageVolume") or average_volume

    if bid in (0, 0.0):
        bid = None
    if ask in (0, 0.0):
        ask = None
    spread = compute_spread(bid, ask)
    analysis_metrics = compute_analysis_metrics(
        entry=entry,
        history=analysis_history,
        price=price,
        change_percent=change_percent,
        sma_50=history_metrics["sma_50"],
        benchmark_change_percent=benchmark_change_percent,
        market_profile=market_profile,
        market_session=market_session,
    )
    next_earnings_date = extract_next_earnings_date(ticker) if should_fetch_event_data(entry) else None
    volatility = fetch_market_volatility(yf)

    market_snapshot_tickers = {ticker_value for ticker_value in market_tickers.values() if ticker_value}
    if symbol:
        market_snapshot_tickers.add(symbol)
    market_snapshots = fetch_parallel_market_data(
        market_snapshot_tickers,
        intraday_tickers={symbol},
    )
    needed_currencies = {
        str(snapshot.get("currency", "")).strip().upper()
        for snapshot in market_snapshots.values()
        if isinstance(snapshot, dict)
    }
    fx_tickers = {
        FX_TO_USD_TICKERS[currency][0]
        for currency in needed_currencies
        if currency and currency != "USD" and currency in FX_TO_USD_TICKERS
    }
    if fx_tickers:
        market_snapshots.update(fetch_parallel_market_data(fx_tickers))

    selected_snapshot = market_snapshots.get(symbol, {})
    selected_currency = str(selected_snapshot.get("currency", currency)).strip().upper() or str(currency).strip().upper() or "USD"
    selected_rate = currency_to_usd_rate(selected_currency, market_snapshots)
    selected_price_usd = float(price) * float(selected_rate) if price not in (None, 0) and selected_rate not in (None, 0) else None
    us_reference_ticker = market_tickers.get("usa", "")
    us_reference_snapshot = market_snapshots.get(us_reference_ticker, {}) if us_reference_ticker else {}
    us_reference_close = extract_latest_close(us_reference_snapshot.get("daily_history")) if us_reference_ticker else None
    us_reference_currency = str(us_reference_snapshot.get("currency", "")).strip().upper() or "USD"
    us_reference_rate = currency_to_usd_rate(us_reference_currency, market_snapshots) if us_reference_ticker else None
    us_reference_close_usd = (
        float(us_reference_close) * float(us_reference_rate)
        if us_reference_close not in (None, 0) and us_reference_rate not in (None, 0)
        else None
    )
    cross_market_difference_percent = (
        ((float(selected_price_usd) - float(us_reference_close_usd)) / float(us_reference_close_usd)) * 100
        if selected_price_usd not in (None, 0) and us_reference_close_usd not in (None, 0)
        else None
    )
    apac_sentiment = compute_apac_sentiment_transfer(
        market_snapshots | fetch_parallel_market_data(set(APAC_SENTIMENT_TICKERS.keys()) - set(market_snapshots.keys())),
        get_global_lead(),
    )

    news_items = []
    for item in (ticker.news or [])[:4]:
        parsed_item = parse_news_item(item)
        if any(parsed_item.values()):
            news_items.append(parsed_item)

    if include_news_summaries and news_items:
        news_items = enrich_news_with_summaries(news_items, gemini_model)

    upcoming: list[tuple[str, datetime]] = []
    if should_fetch_event_data(entry):
        upcoming.extend(extract_calendar_items(getattr(ticker, "calendar", None)))
        upcoming.extend(extract_earnings_dates(ticker))
        upcoming.extend(extract_info_dates(ticker))

    now = datetime.now(UTC)
    seen: set[tuple[str, str]] = set()
    next_events = []
    for label, when in sorted(upcoming, key=lambda item: item[1]):
        if when < now:
            continue
        key = (label, when.isoformat())
        if key in seen:
            continue
        seen.add(key)
        next_events.append(
            {
                "label": label,
                "date_utc": when.isoformat(),
            }
        )
        if len(next_events) == 3:
            break

    return {
        "global_lead_section": build_global_lead_section([entry]),
        "global_hot_topics": global_hot_topics,
        "entry": entry,
        "market_data": {
            "selected_symbol": symbol,
            "selected_market_slot": primary_market["selected_slot"],
            "selected_market_label": GLOBAL_MARKET_SLOTS.get(primary_market["selected_slot"], primary_market["selected_slot"]),
            "lead_market_slot": primary_market["lead_slot"],
            "lead_market_label": primary_market["lead_label"],
            "lead_phase": primary_market["lead_phase"],
            "lead_timestamp_utc": primary_market["utc_timestamp"],
            "price": price,
            "previous_close": previous_close,
            "change": change,
            "change_percent": change_percent,
            "currency": currency,
            "price_usd": selected_price_usd,
            "us_reference_ticker": us_reference_ticker,
            "us_reference_close_usd": us_reference_close_usd,
            "cross_market_difference_percent": cross_market_difference_percent,
            "fetched_at_utc": datetime.now(UTC).isoformat(),
        },
        "analysis_metrics": {
            "rsi_market_14d": dual_rsi_metrics["market_rsi_14d"],
            "rsi_tool_short": dual_rsi_metrics["intraday_rsi_short"],
            "rsi_difference": dual_rsi_metrics["difference"],
            "rsi_warning": dual_rsi_metrics["warning"],
            "rsi_note": dual_rsi_metrics["note"],
            "rsi_daily_history_available": dual_rsi_metrics["daily_history_available"],
            "relative_strength": analysis_metrics["relative_strength"],
            "benchmark_change_percent": benchmark_change_percent,
            "benchmark_name": analysis_metrics["benchmark_name"],
            "rvol": analysis_metrics["rvol"],
            "volume_check": analysis_metrics["volume_check"],
            "volume_status": analysis_metrics["volume_status"],
            "volume_current": analysis_metrics["volume_current"],
            "volume_average_10d": analysis_metrics["volume_average_10d"],
            "home_exchange_name": analysis_metrics["home_exchange_name"],
            "home_exchange_status": analysis_metrics["home_exchange_status"],
            "home_exchange_local_time": market_session["local_time"] if market_session else None,
            "sma_50_distance_percent": analysis_metrics["sma_50_distance_percent"],
        },
        "data_check": {
            "sma_50": history_metrics["sma_50"],
            "sma_200": history_metrics["sma_200"],
            "high_52w": history_metrics["high_52w"],
            "low_52w": history_metrics["low_52w"],
            "bid": float(bid) if bid not in (None, "") else "N/A",
            "ask": float(ask) if ask not in (None, "") else "N/A",
            "spread": spread,
            "volume": int(volume) if volume not in (None, "") else None,
            "average_volume": int(average_volume) if average_volume not in (None, "") else None,
            "next_earnings_date_utc": next_earnings_date,
            "market_volatility": volatility,
        },
        "bridge_signal": {
            "apac_sentiment_score": apac_sentiment["score"],
            "apac_sentiment_message": apac_sentiment["message"],
            "apac_sentiment_items": apac_sentiment["items"],
        },
        "news": news_items,
        "next_important_dates": next_events,
    }


def print_text(data: dict[str, Any]) -> None:
    entry = data["entry"]
    global_lead_section = data.get("global_lead_section", "")
    market_data = data["market_data"]
    analysis_metrics = data["analysis_metrics"]
    data_check = data["data_check"]
    bridge_signal = data.get("bridge_signal", {})

    print(f"Eintrag: {entry.get('name')} ({entry.get('ticker', '-')})")
    print(f"Kategorie: {entry.get('category')} / {entry.get('subcategory')}")
    if entry.get("isin"):
        print(f"ISIN: {entry['isin']}")
    if entry.get("wkn"):
        print(f"WKN: {entry['wkn']}")
    print()
    if global_lead_section:
        print(global_lead_section)
        print()

    print("Zeit-Bruecke:")
    print(
        f"  Primaerquelle: {market_data.get('selected_symbol') or '-'} "
        f"({market_data.get('selected_market_label') or '-'})"
    )
    print(
        f"  Leitmarkt jetzt: {market_data.get('lead_market_label') or '-'} "
        f"| Phase: {market_data.get('lead_phase') or '-'} "
        f"| Stand (UTC): {market_data.get('lead_timestamp_utc') or '-'}"
    )
    if market_data.get("cross_market_difference_percent") is not None:
        print(
            f"  Cross-Market gegen US-Schluss: "
            f"{float(market_data['cross_market_difference_percent']):+.2f}% "
            f"vs. {market_data.get('us_reference_ticker') or '-'}"
        )
    if bridge_signal.get("apac_sentiment_score") is not None:
        print(
            f"  APAC->EU Sentiment: {float(bridge_signal['apac_sentiment_score']):+.2f}% "
            f"| {bridge_signal.get('apac_sentiment_message') or '-'}"
        )
    else:
        print(f"  APAC->EU Sentiment: {bridge_signal.get('apac_sentiment_message') or '-'}")
    print()

    print("Aktueller Kurs:")
    print(f"  Preis: {float(market_data['price']):,.2f} {market_data['currency']}")
    if market_data["previous_close"] is not None:
        print(f"  Vortagesschluss: {float(market_data['previous_close']):,.2f} {market_data['currency']}")
    if market_data["change"] is not None and market_data["change_percent"] is not None:
        print(
            f"  Veraenderung: {float(market_data['change']):+,.2f} "
            f"({float(market_data['change_percent']):+.2f}%)"
        )
    print("  ANALYSE-METRIKEN:")
    print(
        "  - Rel. Staerke: "
        f"{format_optional_number(analysis_metrics['relative_strength'], '%')}"
    )
    if analysis_metrics["rvol"] is None:
        print("  - Volumen-Check: Index/Kein Volumen")
    else:
        print(
            "  - Volumen-Check: "
            f"RVOL {analysis_metrics['rvol']:.2f} ({analysis_metrics['volume_status']})"
        )
    print(
        "  - Kurs-Location: "
        f"{format_optional_number(analysis_metrics['sma_50_distance_percent'], '%')}"
    )
    print("  METRIK-CHECK:")
    print(
        format_dual_rsi_line(
            "RSI (Markt/14d):",
            analysis_metrics["rsi_market_14d"],
            analysis_metrics["rsi_note"],
        )
    )
    print(format_dual_rsi_line("RSI (Tool/Short):", analysis_metrics["rsi_tool_short"]))
    print(format_dual_rsi_line("DIFFERENZ:", analysis_metrics["rsi_difference"]))
    if analysis_metrics["rsi_warning"]:
        print(f"  {analysis_metrics['rsi_warning']}")
    if is_stock_entry(entry):
        print(
            f"  [Heimatboerse: {analysis_metrics['home_exchange_status']}] "
            f"{analysis_metrics['home_exchange_name'] or 'Unbekannt'}"
        )
        print(
            "  VOLUMEN: "
            f"{format_optional_number(analysis_metrics['volume_current'])} vs. "
            f"{format_optional_number(analysis_metrics['volume_average_10d'])}"
            f" | RVOL: {format_optional_number(analysis_metrics['rvol'])} "
            f"({analysis_metrics['volume_status'] or 'Index/Kein Volumen'})"
        )
        print(
            "  REL. STAERKE: "
            f"{format_optional_number(analysis_metrics['relative_strength'], '%')} "
            f"gegenueber {analysis_metrics['benchmark_name'] or 'Benchmark'}"
        )
    print(f"  Abrufzeit (UTC): {market_data['fetched_at_utc']}")
    print()

    print("DATEN-CHECK:")
    print(f"  SMA 50: {format_optional_number(data_check['sma_50'], f' {market_data['currency']}')}")
    print(f"  SMA 200: {format_optional_number(data_check['sma_200'], f' {market_data['currency']}')}")
    print(f"  52-Wochen-Hoch: {format_optional_number(data_check['high_52w'], f' {market_data['currency']}')}")
    print(f"  52-Wochen-Tief: {format_optional_number(data_check['low_52w'], f' {market_data['currency']}')}")
    print(f"  Bid: {format_optional_number(data_check['bid'], f' {market_data['currency']}')}")
    print(f"  Ask: {format_optional_number(data_check['ask'], f' {market_data['currency']}')}")
    print(f"  Spread: {format_optional_number(data_check['spread'], f' {market_data['currency']}')}")
    print(f"  Volumen: {format_optional_number(data_check['volume'])}")
    print(f"  Durchschnittsvolumen: {format_optional_number(data_check['average_volume'])}")
    print(f"  Naechstes Earnings-Datum (UTC): {data_check['next_earnings_date_utc'] or 'N/A'}")

    vix_us = data_check["market_volatility"].get("vix_us", {})
    vix_eu = data_check["market_volatility"].get("vstoxx_eu", {})
    print(
        "  Marktvolatilitaet US (^VIX): "
        f"{format_optional_number(vix_us.get('price'))}"
        f" | Veraenderung: {format_optional_number(vix_us.get('change'))}"
        f" ({format_optional_number(vix_us.get('change_percent'), '%')})"
    )
    print(
        "  Marktvolatilitaet Europa (^V1X): "
        f"{format_optional_number(vix_eu.get('price'))}"
        f" | Veraenderung: {format_optional_number(vix_eu.get('change'))}"
        f" ({format_optional_number(vix_eu.get('change_percent'), '%')})"
    )
    print()

    print("Aktuelle Nachrichten:")
    if data["news"]:
        for item in data["news"]:
            title = item.get("title") or "Ohne Titel"
            publisher = item.get("publisher") or "Unbekannt"
            published_at = item.get("published_at_utc") or "unbekannt"
            print(f"  - {title}")
            print(f"    {publisher} | {published_at}")
            if item.get("link"):
                print(f"    {item['link']}")
            if item.get("summary"):
                print("    Gemini-Zusammenfassung:")
                for line in str(item["summary"]).splitlines():
                    print(f"    {line}")
            if item.get("summary_error"):
                print(f"    Zusammenfassung fehlgeschlagen: {item['summary_error']}")
    else:
        print("  Keine Nachrichten von yfinance verfuegbar.")
    print()

    print("Naechste wichtige Termine:")
    if data["next_important_dates"]:
        for item in data["next_important_dates"]:
            print(f"  - {item['label']}: {item['date_utc']}")
    else:
        print("  Keine kommenden Termine von yfinance verfuegbar.")


def main() -> int:
    args = parse_args()

    try:
        entry = load_index_entry(args.xml, args.query)
        data = fetch_market_brief(
            entry,
            include_news_summaries=not args.no_news_summary,
            gemini_model=args.gemini_model,
        )
    except Exception as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(data, ensure_ascii=True, indent=2))
    else:
        print_text(data)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


