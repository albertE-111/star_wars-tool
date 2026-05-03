from __future__ import annotations

import argparse
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from live_settings import LIVE_SETTINGS_PATH, apply_price_alert_settings, upsert_price_alert
from market_brief import XML_PATH, load_entries


DEFAULT_INTERVAL_MIN = 5
DEFAULT_POLL_SECONDS = 30
LIVE_MONITORING_DEFAULTS = {
    "enabled": "false",
    "target_price": "",
    "condition": "above",
    "interval_min": str(DEFAULT_INTERVAL_MIN),
}


@dataclass(frozen=True)
class MonitorItem:
    key: str
    name: str
    symbol: str
    target_price: float
    condition: str
    interval_min: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ueberwacht aktivierte Live-Preisregeln aus config/stock_categories/stock_categories.xml."
    )
    parser.add_argument(
        "--xml",
        default=str(XML_PATH),
        help="Pfad zur XML-Datei. Standard: config/stock_categories/stock_categories.xml",
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=DEFAULT_POLL_SECONDS,
        help=f"Wie oft die XML/Monitoring-Regeln geprueft werden. Standard: {DEFAULT_POLL_SECONDS}s.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Fuehrt nur eine Pruefrunde aus und beendet danach.",
    )
    return parser.parse_args()


def parse_enabled(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "y", "ja", "on"}


def parse_target_price(value: Any) -> float | None:
    normalized = str(value or "").strip().replace(",", ".")
    if not normalized:
        return None
    try:
        return float(normalized)
    except ValueError:
        return None


def parse_interval_min(value: Any) -> int:
    try:
        interval = int(str(value or "").strip())
    except ValueError:
        return DEFAULT_INTERVAL_MIN
    return interval if interval > 0 else DEFAULT_INTERVAL_MIN


def normalize_condition(value: Any) -> str:
    normalized = str(value or "").strip().casefold()
    mapping = {
        ">": "above",
        ">=": "above",
        "above": "above",
        "ueber": "above",
        "oberhalb": "above",
        "<": "below",
        "<=": "below",
        "below": "below",
        "unter": "below",
        "unterhalb": "below",
    }
    return mapping.get(normalized, "")


def normalize_enabled_value(value: Any) -> str:
    return "true" if parse_enabled(value) else "false"


def normalize_target_price_value(value: Any) -> str:
    target_price = parse_target_price(value)
    if target_price is None or target_price <= 0:
        raise ValueError("target_price muss eine Zahl groesser 0 sein.")
    formatted = f"{target_price:.6f}".rstrip("0").rstrip(".")
    return formatted or str(target_price)


def normalize_condition_value(value: Any) -> str:
    condition = normalize_condition(value)
    if condition not in {"above", "below"}:
        raise ValueError("condition muss 'above' oder 'below' sein.")
    return condition


def normalize_interval_min_value(value: Any) -> str:
    try:
        interval = int(str(value or "").strip())
    except ValueError as exc:
        raise ValueError("interval_min muss eine ganze Zahl sein.") from exc
    if interval <= 0:
        raise ValueError("interval_min muss groesser 0 sein.")
    return str(interval)


def get_live_monitoring_config(entry: dict[str, Any]) -> dict[str, Any]:
    config = entry.get("live_monitoring")
    return config if isinstance(config, dict) else {}


def resolve_monitor_symbol(entry: dict[str, Any]) -> str:
    candidates = (
        entry.get("ticker"),
        entry.get("ticker_usa"),
        entry.get("ticker_europe"),
        entry.get("ticker_eu"),
        entry.get("ticker_apac"),
    )
    for candidate in candidates:
        symbol = str(candidate or "").strip()
        if symbol:
            return symbol
    return ""


def resolve_monitor_query(entry: dict[str, Any]) -> str:
    candidates = (
        entry.get("ticker"),
        entry.get("ticker_usa"),
        entry.get("ticker_europe"),
        entry.get("ticker_eu"),
        entry.get("ticker_apac"),
        entry.get("isin"),
        entry.get("wkn"),
        entry.get("name"),
    )
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value:
            return value
    return ""


def collect_monitor_entries(xml_path: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for entry in load_entries(xml_path):
        enriched = dict(entry)
        config = dict(LIVE_MONITORING_DEFAULTS)
        config.update(get_live_monitoring_config(entry))
        enriched["live_monitoring"] = config
        enriched["symbol"] = resolve_monitor_symbol(entry)
        enriched["query"] = resolve_monitor_query(entry)
        entries.append(enriched)
    return entries


def build_monitor_item(entry: dict[str, Any]) -> MonitorItem | None:
    config = get_live_monitoring_config(entry)
    if not parse_enabled(config.get("enabled")):
        return None

    target_price = parse_target_price(config.get("target_price"))
    condition = normalize_condition(config.get("condition"))
    symbol = resolve_monitor_symbol(entry)
    name = str(entry.get("name", symbol) or symbol).strip()

    if not symbol:
        print(f"Monitoring uebersprungen: {name or 'Unbekannt'} hat keinen Ticker.")
        return None
    if target_price is None:
        print(f"Monitoring uebersprungen: {name} ({symbol}) hat keinen gueltigen target_price.")
        return None
    if condition not in {"above", "below"}:
        print(f"Monitoring uebersprungen: {name} ({symbol}) hat keine gueltige condition.")
        return None

    return MonitorItem(
        key=f"{entry.get('category', '')}|{entry.get('subcategory', '')}|{name}|{symbol}",
        name=name,
        symbol=symbol,
        target_price=target_price,
        condition=condition,
        interval_min=parse_interval_min(config.get("interval_min")),
    )


def load_monitor_items(xml_path: str) -> list[MonitorItem]:
    items: list[MonitorItem] = []
    for entry in load_entries(xml_path):
        item = build_monitor_item(entry)
        if item is not None:
            items.append(item)
    return items


def ensure_text_child(node: ElementTree.Element, tag: str) -> ElementTree.Element:
    child = node.find(tag)
    if child is None:
        child = ElementTree.SubElement(node, tag)
    return child


def ensure_live_monitoring_node(index_node: ElementTree.Element) -> ElementTree.Element:
    live_monitoring = index_node.find("live_monitoring")
    if live_monitoring is None:
        live_monitoring = ElementTree.SubElement(index_node, "live_monitoring")

    for field, default_value in LIVE_MONITORING_DEFAULTS.items():
        child = ensure_text_child(live_monitoring, field)
        if child.text is None or (field != "target_price" and not child.text.strip()):
            child.text = default_value
    return live_monitoring


def backup_xml(xml_path: str) -> Path:
    source = Path(xml_path)
    backup_dir = source.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{source.stem}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}{source.suffix}"
    shutil.copy2(source, backup)
    return backup


def save_xml_tree(tree: ElementTree.ElementTree, xml_path: str) -> Path:
    backup_path = backup_xml(xml_path)
    if hasattr(ElementTree, "indent"):
        ElementTree.indent(tree, space="  ")
    tree.write(xml_path, encoding="utf-8", xml_declaration=True)
    return backup_path


def index_node_query(index_node: ElementTree.Element) -> str:
    candidates = (
        index_node.findtext("ticker"),
        index_node.findtext("ticker_usa"),
        index_node.findtext("ticker_europe"),
        index_node.findtext("ticker_eu"),
        index_node.findtext("ticker_apac"),
        index_node.findtext("isin"),
        index_node.findtext("wkn"),
        index_node.findtext("name"),
    )
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value:
            return value
    return ""


def find_monitor_entry_node(
    root: ElementTree.Element,
    category_name: str,
    subcategory_name: str,
    query: str,
) -> ElementTree.Element | None:
    normalized_query = str(query or "").strip()
    for category in root.findall("category"):
        if category.attrib.get("name", "").strip() != category_name:
            continue
        for subcategory in category.findall("subcategory"):
            if subcategory.attrib.get("name", "").strip() != subcategory_name:
                continue
            for index_node in subcategory.findall("index"):
                if index_node_query(index_node) == normalized_query:
                    return index_node
    return None


def read_live_monitoring_node_config(index_node: ElementTree.Element) -> dict[str, str]:
    config = dict(LIVE_MONITORING_DEFAULTS)
    live_monitoring = index_node.find("live_monitoring")
    if live_monitoring is None:
        return config
    for field in config:
        config[field] = (live_monitoring.findtext(field) or "").strip()
    return config


def update_live_monitoring_config(
    category: str,
    subcategory: str,
    query: str,
    updates: dict[str, Any],
    xml_path: str = str(XML_PATH),
) -> tuple[Path, dict[str, str]]:
    tree = ElementTree.parse(xml_path)
    root = tree.getroot()
    index_node = find_monitor_entry_node(root, category, subcategory, query)
    if index_node is None:
        raise RuntimeError("Eintrag wurde in config/stock_categories/stock_categories.xml nicht gefunden.")

    current = read_live_monitoring_node_config(index_node)
    current_entry = {
        "category": category,
        "subcategory": subcategory,
        "query": query,
        "live_monitoring": current,
    }
    current = dict(apply_price_alert_settings(current_entry).get("live_monitoring", current))
    normalized_updates: dict[str, str] = {}

    for field, value in updates.items():
        if field == "enabled":
            normalized_updates[field] = normalize_enabled_value(value)
        elif field == "target_price":
            normalized_updates[field] = normalize_target_price_value(value)
        elif field == "condition":
            normalized_updates[field] = normalize_condition_value(value)
        elif field == "interval_min":
            normalized_updates[field] = normalize_interval_min_value(value)
        else:
            raise ValueError(f"Unbekanntes Live-Monitoring-Feld: {field}")

    merged = {**current, **normalized_updates}
    if parse_enabled(merged.get("enabled")):
        normalize_target_price_value(merged.get("target_price"))
        normalize_condition_value(merged.get("condition"))
        normalize_interval_min_value(merged.get("interval_min"))

    settings_path = upsert_price_alert(category, subcategory, query, merged, LIVE_SETTINGS_PATH)
    return settings_path, merged


def should_check(item: MonitorItem, last_checked: dict[str, datetime], now: datetime) -> bool:
    previous = last_checked.get(item.key)
    if previous is None:
        return True
    return now - previous >= timedelta(minutes=item.interval_min)


def load_yfinance():
    try:
        import yfinance as yf
    except ImportError as exc:
        raise SystemExit("yfinance ist nicht installiert. Installiere es mit: pip install yfinance") from exc
    return yf


def extract_price(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_live_price(yf: Any, symbol: str) -> tuple[float | None, str]:
    ticker = yf.Ticker(symbol)

    try:
        info = ticker.fast_info
        for key in ("lastPrice", "last_price", "regularMarketPrice"):
            price = extract_price(info.get(key))
            if price is not None:
                return price, str(info.get("currency") or "")
    except Exception:
        pass

    try:
        info = ticker.info or {}
        for key in ("regularMarketPrice", "currentPrice", "previousClose"):
            price = extract_price(info.get(key))
            if price is not None:
                return price, str(info.get("currency") or "")
    except Exception:
        pass

    try:
        history = ticker.history(period="1d", interval="1m")
        closes = history.get("Close") if history is not None else None
        if closes is not None:
            closes = closes.dropna()
            if len(closes) >= 1:
                return float(closes.iloc[-1]), ""
    except Exception:
        pass

    return None, ""


def condition_matches(price: float, item: MonitorItem) -> bool:
    if item.condition == "above":
        return price >= item.target_price
    if item.condition == "below":
        return price <= item.target_price
    return False


def condition_label(condition: str) -> str:
    if condition == "above":
        return "ueber oder gleich"
    if condition == "below":
        return "unter oder gleich"
    return condition


def run_monitor_round(
    yf: Any,
    items: list[MonitorItem],
    last_checked: dict[str, datetime],
    now: datetime | None = None,
) -> None:
    now = now or datetime.now()
    for item in items:
        if not should_check(item, last_checked, now):
            continue

        last_checked[item.key] = now
        price, currency = fetch_live_price(yf, item.symbol)
        if price is None:
            print(f"Kein Preis erhalten: {item.name} ({item.symbol})")
            continue

        if condition_matches(price, item):
            print(
                "PREIS-TRIGGER: "
                f"{item.name} ({item.symbol}) liegt bei {price:.2f} {currency or ''} "
                f"und ist {condition_label(item.condition)} Ziel {item.target_price:.2f}."
            )


def monitor_loop(xml_path: str, poll_seconds: int, once: bool = False) -> None:
    yf: Any | None = None
    last_checked: dict[str, datetime] = {}
    poll_seconds = max(1, poll_seconds)

    while True:
        items = load_monitor_items(xml_path)
        if items:
            if yf is None:
                yf = load_yfinance()
            run_monitor_round(yf, items, last_checked)
        else:
            print("Keine aktivierten Live-Monitoring-Regeln gefunden.")

        if once:
            return
        time.sleep(poll_seconds)


def main() -> int:
    args = parse_args()
    xml_path = str(Path(args.xml))
    try:
        monitor_loop(xml_path, args.poll_seconds, once=args.once)
    except KeyboardInterrupt:
        print("Preis-Monitoring beendet.")
        return 130
    except Exception as exc:
        print(f"Preis-Monitoring fehlgeschlagen: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
