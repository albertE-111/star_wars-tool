from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


LIVE_SETTINGS_PATH = Path("config/live_settings.xml")
LIVE_SETTINGS_FIELDS = {"enabled", "target_price", "condition", "interval_min"}


def price_alert_key(category: str, subcategory: str, query: str) -> str:
    return "\x1f".join(
        (
            str(category or "").strip(),
            str(subcategory or "").strip(),
            str(query or "").strip(),
        )
    )


def current_timestamp() -> str:
    return datetime.now(UTC).isoformat()


def load_live_settings(path: str | Path = LIVE_SETTINGS_PATH) -> ElementTree.Element:
    settings_path = Path(path)
    if not settings_path.exists():
        return ElementTree.Element("liveSettings")

    root = ElementTree.parse(settings_path).getroot()
    if root.tag != "liveSettings":
        raise RuntimeError(f"{settings_path} muss ein liveSettings-XML-Dokument sein.")
    return root


def save_live_settings(root: ElementTree.Element, path: str | Path = LIVE_SETTINGS_PATH) -> Path:
    settings_path = Path(path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    tree = ElementTree.ElementTree(root)
    if hasattr(ElementTree, "indent"):
        ElementTree.indent(tree, space="  ")
    tree.write(settings_path, encoding="utf-8", xml_declaration=True)
    return settings_path


def normalize_settings(settings: dict[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for field in LIVE_SETTINGS_FIELDS:
        if field in settings:
            normalized[field] = str(settings.get(field, "")).strip()
    return normalized


def alert_key(alert: ElementTree.Element) -> str:
    return price_alert_key(
        str(alert.attrib.get("category", "")),
        str(alert.attrib.get("subcategory", "")),
        str(alert.attrib.get("query", "")),
    )


def load_price_alerts(path: str | Path = LIVE_SETTINGS_PATH) -> dict[str, dict[str, str]]:
    root = load_live_settings(path)
    alerts: dict[str, dict[str, str]] = {}
    for alert in root.findall("priceAlert"):
        settings = {field: (alert.findtext(field) or "").strip() for field in LIVE_SETTINGS_FIELDS}
        normalized = normalize_settings(settings)
        query = str(alert.attrib.get("query", "") or "").strip()
        key = alert_key(alert)
        if query and normalized:
            alerts[key] = normalized
    return alerts


def resolve_entry_query(entry: dict[str, Any]) -> str:
    for field in ("query", "ticker", "ticker_usa", "ticker_europe", "ticker_eu", "ticker_apac", "isin", "wkn", "name"):
        value = str(entry.get(field, "") or "").strip()
        if value:
            return value
    return ""


def apply_price_alert_settings(
    entry: dict[str, Any],
    alerts: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    alerts = alerts if alerts is not None else load_price_alerts()
    key = price_alert_key(
        str(entry.get("category", "")),
        str(entry.get("subcategory", "")),
        resolve_entry_query(entry),
    )
    settings = alerts.get(key)
    if not settings:
        return entry

    enriched = dict(entry)
    live_monitoring = dict(enriched.get("live_monitoring", {}))
    live_monitoring.update(settings)
    enriched["live_monitoring"] = live_monitoring
    return enriched


def upsert_price_alert(
    category: str,
    subcategory: str,
    query: str,
    settings: dict[str, Any],
    path: str | Path = LIVE_SETTINGS_PATH,
) -> Path:
    root = load_live_settings(path)
    key = price_alert_key(category, subcategory, query)
    normalized_settings = normalize_settings(settings)
    for alert in root.findall("priceAlert"):
        if alert_key(alert) == key:
            alert.attrib.update(
                {
                    "category": str(category or "").strip(),
                    "subcategory": str(subcategory or "").strip(),
                    "query": str(query or "").strip(),
                    "updated_at": current_timestamp(),
                }
            )
            for field, value in normalized_settings.items():
                child = alert.find(field)
                if child is None:
                    child = ElementTree.SubElement(alert, field)
                child.text = value
            return save_live_settings(root, path)

    alert = ElementTree.SubElement(
        root,
        "priceAlert",
        {
            "category": str(category or "").strip(),
            "subcategory": str(subcategory or "").strip(),
            "query": str(query or "").strip(),
            "updated_at": current_timestamp(),
        },
    )
    for field, value in normalized_settings.items():
        ElementTree.SubElement(alert, field).text = value
    return save_live_settings(root, path)
