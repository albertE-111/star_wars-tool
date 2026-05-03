from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Awaitable, Callable
from xml.etree import ElementTree

from article_fetcher import fetch_article
from batch_market_brief import (
    build_default_output_path,
    build_summary,
    filter_queries,
    format_result,
    load_queries,
    run_market_brief,
    summarize_stderr,
)
from batch_market_brief_client import load_categories, load_subcategories
from bot_monitoring import (
    HEARTBEAT_INTERVAL_SECONDS,
    append_event,
    configure_event_logging,
    get_support_bot_status,
    restart_support_bot_process,
    start_support_bot_process,
    stop_support_bot_process,
    write_heartbeat,
)
from gemini_article_summary import resolve_api_key, summarize_article_with_cache
from market_brief import (
    build_global_hot_topics_section,
    build_global_lead_section,
    fetch_market_brief,
    load_index_entry,
    print_text as print_market_brief_text,
)

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.error import BadRequest
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
LOGGER = logging.getLogger(__name__)
configure_event_logging("main_bot")

MAX_MESSAGE_LENGTH = 3900
CONFIG_PATH = Path("config/app_config.json")
LOCK_PATH = Path(".telegram_bot.lock")
STATE_BATCH_CATEGORY = 1
STATE_BATCH_SUBCATEGORY = 2
STATE_BATCH_ENTRY = 3
STATE_BATCH_SELECTION_MENU = 4
STATE_BATCH_NEWS = 5
STATE_BATCH_RESULT_MODE = 6
STATE_AUTO_MENU = 10
STATE_AUTO_CATEGORY = 11
STATE_AUTO_SUBCATEGORY = 12
STATE_AUTO_INTERVAL = 13
STATE_AUTO_WINDOW_FROM = 14
STATE_AUTO_WINDOW_TO = 15
STATE_CERTIFICATE_ISIN = 20
STATE_CERTIFICATE_MIN = 21
STATE_CERTIFICATE_MAX = 22
STATE_CERTIFICATE_DIRECTION = 23
STATE_LIST_ACTION = 30
STATE_LIST_ADD_CATEGORY = 31
STATE_LIST_ADD_SUBCATEGORY = 32
STATE_LIST_ADD_NAME = 33
STATE_LIST_ADD_TICKER = 34
STATE_LIST_ADD_ISIN = 35
STATE_LIST_ADD_WKN = 36
STATE_LIST_ADD_CONFIRM = 37
STATE_LIST_ADD_OPTIONAL_MENU = 38
STATE_LIST_ADD_OPTIONAL_VALUE = 39
STATE_LIST_EDIT_CATEGORY = 40
STATE_LIST_EDIT_SUBCATEGORY = 41
STATE_LIST_EDIT_ENTRY = 42
STATE_LIST_EDIT_FIELD = 43
STATE_LIST_EDIT_VALUE = 44
STATE_LIST_EDIT_CONFIRM = 45
STATE_LIST_DELETE_CATEGORY = 46
STATE_LIST_DELETE_SUBCATEGORY = 47
STATE_LIST_DELETE_ENTRY = 48
STATE_LIST_DELETE_CONFIRM = 49
STATE_SUPPORT_MENU = 50
STATE_LIST_ADD_TRADE_REPUBLIC_AKTIE = 51
STATE_LIST_ADD_TRADE_REPUBLIC_DERIVATE = 52
STATE_MAIN_MENU = 60
STATE_MARKETBRIEF_QUERY = 61
CALLBACK_PREFIX_CATEGORY = "mbc:"
CALLBACK_PREFIX_SUBCATEGORY = "mbs:"
CALLBACK_PREFIX_ENTRY = "mbe:"
CALLBACK_PREFIX_BATCH_SELECT = "mbx:"
CALLBACK_PREFIX_NEWS = "mbn:"
CALLBACK_PREFIX_BATCH_RESULT = "mbr:"
CALLBACK_PREFIX_AUTO_MENU = "abm:"
CALLBACK_PREFIX_AUTO_CATEGORY = "abc:"
CALLBACK_PREFIX_AUTO_SUBCATEGORY = "abs:"
CALLBACK_PREFIX_AUTO_INTERVAL = "abi:"
CALLBACK_PREFIX_AUTO_WINDOW = "abw:"
CALLBACK_PREFIX_LIST_ACTION = "lpa:"
CALLBACK_PREFIX_LIST_CATEGORY = "lpc:"
CALLBACK_PREFIX_LIST_SUBCATEGORY = "lps:"
CALLBACK_PREFIX_LIST_ENTRY = "lpe:"
CALLBACK_PREFIX_LIST_FIELD = "lpf:"
CALLBACK_PREFIX_LIST_CONFIRM = "lpy:"
CALLBACK_PREFIX_LIST_ADD_CATEGORY = "lpac:"
CALLBACK_PREFIX_LIST_ADD_SUBCATEGORY = "lpas:"
CALLBACK_PREFIX_LIST_ADD_NAME = "lpan:"
CALLBACK_PREFIX_LIST_OPTIONAL = "lpao:"
CALLBACK_PREFIX_SUPPORT = "sup:"
CALLBACK_PREFIX_MAIN_MENU = "mainmenu:"
AUTO_BRIEF_JOB_NAME = "auto_market_brief"

BASE_REQUIRED_STOCK_FIELDS = ["category", "subcategory", "name", "ticker", "isin", "wkn"]
TRADE_REPUBLIC_FIELD_NAMES = ["trade_republic_aktie", "trade_republic_derivate"]
TRADE_REPUBLIC_ALLOWED_VALUES = {"ja", "nein", "unbekannt"}
REQUIRED_STOCK_FIELDS = BASE_REQUIRED_STOCK_FIELDS + TRADE_REPUBLIC_FIELD_NAMES
OPTIONAL_STOCK_FIELDS = ["ticker_usa", "ticker_eu", "ticker_apac", "land", "tag", "description"]
TICKER_FIELD_NAMES = ["ticker", "ticker_usa", "ticker_eu", "ticker_apac"]
IDENTIFIER_FIELD_NAMES = ["isin", "wkn"]
STOCK_FIELD_LABELS = {
    "category": "Kategorie",
    "subcategory": "Subkategorie",
    "name": "Name",
    "ticker": "Ticker",
    "ticker_usa": "Ticker USA",
    "ticker_eu": "Ticker EU",
    "ticker_apac": "Ticker APAC",
    "isin": "ISIN",
    "wkn": "WKN",
    "trade_republic_aktie": "Trade Republic Aktie",
    "trade_republic_derivate": "Trade Republic Derivate",
    "land": "Land",
    "tag": "Tag",
    "description": "Beschreibung",
}


@dataclass
class AutoBriefSettings:
    enabled: bool = False
    start_time: str = "08:00"
    end_time: str = "18:00"
    interval_minutes: int = 60
    category: str = ""
    subcategory: str = ""
    with_news_summary: bool = True
    send_detailed_result_message: bool = True
    chat_id: int = 0
    last_run_at: str = ""

    @classmethod
    def from_config(cls, payload: dict | None) -> "AutoBriefSettings":
        payload = payload or {}
        interval_raw = payload.get("interval_minutes", 60)
        try:
            interval_minutes = int(interval_raw)
        except (TypeError, ValueError):
            interval_minutes = 60

        chat_raw = payload.get("chat_id", 0)
        try:
            chat_id = int(chat_raw)
        except (TypeError, ValueError):
            chat_id = 0

        return cls(
            enabled=bool(payload.get("enabled", False)),
            start_time=str(payload.get("start_time", "08:00")).strip() or "08:00",
            end_time=str(payload.get("end_time", "18:00")).strip() or "18:00",
            interval_minutes=max(1, interval_minutes),
            category=str(payload.get("category", "")).strip(),
            subcategory=str(payload.get("subcategory", "")).strip(),
            with_news_summary=bool(payload.get("with_news_summary", True)),
            send_detailed_result_message=bool(payload.get("send_detailed_result_message", True)),
            chat_id=chat_id,
            last_run_at=str(payload.get("last_run_at", "")).strip(),
        )

    def to_config(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "interval_minutes": self.interval_minutes,
            "category": self.category,
            "subcategory": self.subcategory,
            "with_news_summary": self.with_news_summary,
            "send_detailed_result_message": self.send_detailed_result_message,
            "chat_id": self.chat_id,
            "last_run_at": self.last_run_at,
        }


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise RuntimeError(
            f"Konfigurationsdatei fehlt: {CONFIG_PATH}. "
            "Lege die Datei an und trage den Bot-Token dort ein."
        )

    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Konfigurationsdatei ist ungueltig: {exc}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("Konfigurationsdatei muss ein JSON-Objekt enthalten.")

    return payload


def save_config(config: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=True, indent=2), encoding="utf-8")


class SingleInstanceLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                if self.handle.tell() == 0:
                    self.handle.write(b" ")
                    self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.release()
            raise RuntimeError(
                "telegram_bot.py laeuft bereits in einer anderen Instanz. "
                "Beende den vorhandenen Prozess, bevor du den Bot erneut startest."
            ) from exc

        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(
            (
                f"pid={os.getpid()}\n"
                f"executable={os.sys.executable}\n"
                f"started_at={datetime.now().isoformat(timespec='seconds')}\n"
            ).encode("utf-8")
        )
        self.handle.flush()

    def release(self) -> None:
        if self.handle is None:
            return

        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            self.handle.close()
            self.handle = None

    def __enter__(self) -> "SingleInstanceLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def config_or_env(config: dict, key: str, env_name: str = "") -> str:
    value = str(config.get(key, "")).strip()
    if value:
        return value

    if env_name:
        env_value = os.getenv(env_name, "").strip()
        if env_value:
            return env_value

    return ""


def config_required(config: dict, key: str, env_name: str = "") -> str:
    value = config_or_env(config, key, env_name)
    if not value:
        raise RuntimeError(f"Konfigurationswert {key} fehlt.")
    return value


def get_allowed_chat_ids(config: dict) -> set[int]:
    raw = config_or_env(config, "allowed_chat_ids", "TELEGRAM_ALLOWED_CHAT_IDS")
    if not raw:
        return set()

    chat_ids: set[int] = set()
    for part in raw.split(","):
        cleaned = part.strip()
        if cleaned:
            chat_ids.add(int(cleaned))
    return chat_ids


def get_allowed_user_ids(config: dict) -> set[int]:
    raw = config_or_env(config, "allowed_user_ids", "TELEGRAM_ALLOWED_USER_IDS")
    if not raw:
        return set()

    user_ids: set[int] = set()
    for part in raw.split(","):
        cleaned = part.strip()
        if cleaned:
            user_ids.add(int(cleaned))
    return user_ids


def load_stock_tree(xml_path: str = "config/stock_categories/stock_categories.xml") -> ElementTree.ElementTree:
    return ElementTree.parse(xml_path)


def backup_stock_categories(xml_path: str = "config/stock_categories/stock_categories.xml") -> Path:
    source = Path(xml_path)
    backup_dir = source.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / (
        f"{source.stem}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}{source.suffix}"
    )
    shutil.copy2(source, backup)
    return backup


def save_stock_tree(tree: ElementTree.ElementTree, xml_path: str = "config/stock_categories/stock_categories.xml") -> Path:
    backup_path = backup_stock_categories(xml_path)
    if hasattr(ElementTree, "indent"):
        ElementTree.indent(tree, space="  ")
    tree.write(xml_path, encoding="utf-8", xml_declaration=True)
    return backup_path


def collect_stock_entries(xml_path: str = "config/stock_categories/stock_categories.xml") -> list[dict[str, str]]:
    root = load_stock_tree(xml_path).getroot()
    entries: list[dict[str, str]] = []
    for category in root.findall("category"):
        category_name = category.attrib.get("name", "").strip()
        for subcategory in category.findall("subcategory"):
            subcategory_name = subcategory.attrib.get("name", "").strip()
            for index in subcategory.findall("index"):
                entry = {
                    "category": category_name,
                    "subcategory": subcategory_name,
                    "name": (index.findtext("name") or "").strip(),
                    "ticker": (index.findtext("ticker") or "").strip(),
                    "isin": (index.findtext("isin") or "").strip(),
                    "wkn": (index.findtext("wkn") or "").strip(),
                    "trade_republic_aktie": (index.findtext("trade_republic_aktie") or "").strip(),
                    "trade_republic_derivate": (index.findtext("trade_republic_derivate") or "").strip(),
                    "ticker_usa": (index.findtext("ticker_usa") or "").strip(),
                    "ticker_eu": (index.findtext("ticker_eu") or "").strip(),
                    "ticker_apac": (index.findtext("ticker_apac") or "").strip(),
                    "land": (index.findtext("land") or "").strip(),
                    "tag": (index.findtext("tag") or "").strip(),
                    "description": (index.findtext("description") or "").strip(),
                }
                entry["query"] = entry["ticker"] or entry["isin"] or entry["wkn"] or entry["name"]
                entries.append(entry)
    return entries


def ensure_text_child(node: ElementTree.Element, tag: str) -> ElementTree.Element:
    child = node.find(tag)
    if child is None:
        child = ElementTree.SubElement(node, tag)
    return child


def find_category_node(root: ElementTree.Element, name: str) -> ElementTree.Element | None:
    for category in root.findall("category"):
        if category.attrib.get("name", "").strip() == name:
            return category
    return None


def find_subcategory_node(category_node: ElementTree.Element, name: str) -> ElementTree.Element | None:
    for subcategory in category_node.findall("subcategory"):
        if subcategory.attrib.get("name", "").strip() == name:
            return subcategory
    return None


def get_or_create_category_node(root: ElementTree.Element, name: str) -> ElementTree.Element:
    existing = find_category_node(root, name)
    if existing is not None:
        return existing
    return ElementTree.SubElement(root, "category", {"name": name})


def get_or_create_subcategory_node(category_node: ElementTree.Element, name: str) -> ElementTree.Element:
    existing = find_subcategory_node(category_node, name)
    if existing is not None:
        return existing
    return ElementTree.SubElement(category_node, "subcategory", {"name": name})


def find_entry_node(
    root: ElementTree.Element,
    category_name: str,
    subcategory_name: str,
    query: str,
) -> tuple[ElementTree.Element | None, ElementTree.Element | None, ElementTree.Element | None]:
    category_node = find_category_node(root, category_name)
    if category_node is None:
        return None, None, None
    subcategory_node = find_subcategory_node(category_node, subcategory_name)
    if subcategory_node is None:
        return category_node, None, None
    for index in subcategory_node.findall("index"):
        name = (index.findtext("name") or "").strip()
        ticker = (index.findtext("ticker") or "").strip()
        isin = (index.findtext("isin") or "").strip()
        wkn = (index.findtext("wkn") or "").strip()
        current_query = ticker or isin or wkn or name
        if current_query == query:
            return category_node, subcategory_node, index
    return category_node, subcategory_node, None


def cleanup_empty_stock_nodes(root: ElementTree.Element) -> None:
    for category in list(root.findall("category")):
        for subcategory in list(category.findall("subcategory")):
            if not subcategory.findall("index"):
                category.remove(subcategory)
        if not category.findall("subcategory"):
            root.remove(category)


def normalize_stock_value(value: str, field_name: str = "") -> str:
    normalized = value.strip()
    if field_name in TICKER_FIELD_NAMES:
        return normalized.upper()
    if field_name in IDENTIFIER_FIELD_NAMES:
        return normalized.upper().replace(" ", "")
    if field_name in TRADE_REPUBLIC_FIELD_NAMES:
        return normalize_trade_republic_value(normalized)
    return normalized


def normalize_trade_republic_value(value: str) -> str:
    normalized = value.strip().casefold()
    mapping = {
        "j": "ja",
        "ja": "ja",
        "yes": "ja",
        "y": "ja",
        "true": "ja",
        "1": "ja",
        "n": "nein",
        "nein": "nein",
        "no": "nein",
        "false": "nein",
        "0": "nein",
        "?": "unbekannt",
        "unklar": "unbekannt",
        "unbekannt": "unbekannt",
        "unknown": "unbekannt",
    }
    return mapping.get(normalized, normalized)


def validate_stock_entry_payload(
    payload: dict[str, str],
    existing_entries: list[dict[str, str]],
    current_query: str = "",
    required_fields: list[str] | None = None,
) -> None:
    normalized_payload = {key: normalize_stock_value(str(value), key) for key, value in payload.items()}
    required_fields = required_fields or REQUIRED_STOCK_FIELDS

    for field in required_fields:
        if not normalized_payload.get(field, ""):
            raise RuntimeError(f"Feld fehlt oder ist leer: {field}")

    for field in TRADE_REPUBLIC_FIELD_NAMES:
        candidate = normalized_payload.get(field, "")
        if candidate and candidate not in TRADE_REPUBLIC_ALLOWED_VALUES:
            raise RuntimeError(
                f"Ungueltiger Wert fuer {STOCK_FIELD_LABELS[field]}: {candidate}. "
                "Erlaubt sind: ja, nein, unbekannt."
            )

    for entry in existing_entries:
        entry_query = entry.get("query", "")
        if current_query and entry_query == current_query:
            continue

        other_tickers = {
            normalize_stock_value(str(entry.get(field, "")), field).casefold()
            for field in TICKER_FIELD_NAMES
            if normalize_stock_value(str(entry.get(field, "")), field)
        }
        for field in TICKER_FIELD_NAMES:
            candidate = normalized_payload.get(field, "")
            if candidate and candidate.casefold() in other_tickers:
                raise RuntimeError(f"Ticker existiert bereits: {candidate}")

        for field in IDENTIFIER_FIELD_NAMES:
            candidate = normalized_payload.get(field, "")
            other = normalize_stock_value(str(entry.get(field, "")), field)
            if candidate and other and candidate.casefold() == other.casefold():
                raise RuntimeError(f"{field.upper()} existiert bereits: {candidate}")


def add_stock_entry(payload: dict[str, str], xml_path: str = "config/stock_categories/stock_categories.xml") -> Path:
    clean_payload = {key: normalize_stock_value(str(value), key) for key, value in payload.items()}
    validate_stock_entry_payload(clean_payload, collect_stock_entries(xml_path))

    tree = load_stock_tree(xml_path)
    root = tree.getroot()
    category_node = get_or_create_category_node(root, clean_payload["category"])
    subcategory_node = get_or_create_subcategory_node(category_node, clean_payload["subcategory"])
    index = ElementTree.SubElement(subcategory_node, "index")
    ensure_text_child(index, "name").text = clean_payload["name"]
    ensure_text_child(index, "ticker").text = clean_payload["ticker"]
    ensure_text_child(index, "isin").text = clean_payload["isin"]
    ensure_text_child(index, "wkn").text = clean_payload["wkn"]
    ensure_text_child(index, "trade_republic_aktie").text = clean_payload["trade_republic_aktie"]
    ensure_text_child(index, "trade_republic_derivate").text = clean_payload["trade_republic_derivate"]
    for field in OPTIONAL_STOCK_FIELDS:
        value = clean_payload.get(field, "")
        if value:
            ensure_text_child(index, field).text = value
    return save_stock_tree(tree, xml_path)


def update_stock_entry(
    current_category: str,
    current_subcategory: str,
    current_query: str,
    updated_fields: dict[str, str],
    xml_path: str = "config/stock_categories/stock_categories.xml",
) -> tuple[Path, dict[str, str]]:
    tree = load_stock_tree(xml_path)
    root = tree.getroot()
    category_node, subcategory_node, entry_node = find_entry_node(root, current_category, current_subcategory, current_query)
    if entry_node is None or category_node is None or subcategory_node is None:
        raise RuntimeError("Eintrag wurde in config/stock_categories/stock_categories.xml nicht gefunden.")

    current_data = {
        "category": current_category,
        "subcategory": current_subcategory,
        "name": (entry_node.findtext("name") or "").strip(),
        "ticker": (entry_node.findtext("ticker") or "").strip(),
        "isin": (entry_node.findtext("isin") or "").strip(),
        "wkn": (entry_node.findtext("wkn") or "").strip(),
        "trade_republic_aktie": (entry_node.findtext("trade_republic_aktie") or "").strip(),
        "trade_republic_derivate": (entry_node.findtext("trade_republic_derivate") or "").strip(),
        "ticker_usa": (entry_node.findtext("ticker_usa") or "").strip(),
        "ticker_eu": (entry_node.findtext("ticker_eu") or "").strip(),
        "ticker_apac": (entry_node.findtext("ticker_apac") or "").strip(),
        "land": (entry_node.findtext("land") or "").strip(),
        "tag": (entry_node.findtext("tag") or "").strip(),
        "description": (entry_node.findtext("description") or "").strip(),
    }
    merged = {**current_data, **{key: normalize_stock_value(str(value), key) for key, value in updated_fields.items()}}
    validate_stock_entry_payload(merged, collect_stock_entries(xml_path), current_query=current_query)

    target_category = merged["category"]
    target_subcategory = merged["subcategory"]
    target_parent = subcategory_node
    if target_category != current_category or target_subcategory != current_subcategory:
        subcategory_node.remove(entry_node)
        target_category_node = get_or_create_category_node(root, target_category)
        target_parent = get_or_create_subcategory_node(target_category_node, target_subcategory)
        target_parent.append(entry_node)
        cleanup_empty_stock_nodes(root)

    ensure_text_child(entry_node, "name").text = merged["name"]
    ensure_text_child(entry_node, "ticker").text = merged["ticker"]
    ensure_text_child(entry_node, "isin").text = merged["isin"]
    ensure_text_child(entry_node, "wkn").text = merged["wkn"]
    ensure_text_child(entry_node, "trade_republic_aktie").text = merged["trade_republic_aktie"]
    ensure_text_child(entry_node, "trade_republic_derivate").text = merged["trade_republic_derivate"]
    for field in OPTIONAL_STOCK_FIELDS:
        value = merged.get(field, "")
        child = entry_node.find(field)
        if value:
            ensure_text_child(entry_node, field).text = value
        elif child is not None:
            entry_node.remove(child)

    backup_path = save_stock_tree(tree, xml_path)
    merged["query"] = merged["ticker"] or merged["isin"] or merged["wkn"] or merged["name"]
    return backup_path, merged


def delete_stock_entry(
    category_name: str,
    subcategory_name: str,
    query: str,
    xml_path: str = "config/stock_categories/stock_categories.xml",
) -> Path:
    tree = load_stock_tree(xml_path)
    root = tree.getroot()
    category_node, subcategory_node, entry_node = find_entry_node(root, category_name, subcategory_name, query)
    if entry_node is None or category_node is None or subcategory_node is None:
        raise RuntimeError("Eintrag wurde in config/stock_categories/stock_categories.xml nicht gefunden.")
    subcategory_node.remove(entry_node)
    cleanup_empty_stock_nodes(root)
    return save_stock_tree(tree, xml_path)


def ensure_allowed_chat(update: Update, allowed_chat_ids: set[int], allowed_user_ids: set[int]) -> bool:
    user = update.effective_user
    if allowed_user_ids:
        if user is None or user.id not in allowed_user_ids:
            return False

    if not allowed_chat_ids:
        return True

    chat = update.effective_chat
    if chat is None:
        return False
    return chat.id in allowed_chat_ids


def split_message(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


async def reply_long(update: Update, text: str) -> None:
    message = update.effective_message
    if message is None:
        return
    for chunk in split_message(text):
        await message.reply_text(chunk)


async def run_blocking(func: Callable, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)


async def send_output_document(
    bot,
    chat_id: int,
    output_path: Path,
    *,
    reply_message=None,
) -> bool:
    if not output_path.exists():
        LOGGER.error("Ausgabedatei fehlt vor Telegram-Upload: %s", output_path)
        if reply_message is not None:
            await reply_message.reply_text(f"Datei fuer Upload nicht gefunden: {output_path.name}")
        else:
            await bot.send_message(chat_id=chat_id, text=f"Datei fuer Upload nicht gefunden: {output_path.name}")
        return False

    file_size = output_path.stat().st_size
    LOGGER.info("Sende Ergebnisdatei an Telegram: %s (%d Bytes) -> Chat %s", output_path.name, file_size, chat_id)
    try:
        with output_path.open("rb") as handle:
            if reply_message is not None:
                await reply_message.reply_document(document=handle, filename=output_path.name)
            else:
                await bot.send_document(chat_id=chat_id, document=handle, filename=output_path.name)
    except Exception:
        LOGGER.exception("Telegram-Datei-Upload fehlgeschlagen: %s", output_path)
        message_text = (
            "Datei konnte nicht an Telegram gesendet werden.\n"
            f"Datei: {output_path.name}\n"
            f"Groesse: {file_size} Bytes"
        )
        if reply_message is not None:
            await reply_message.reply_text(message_text)
        else:
            await bot.send_message(chat_id=chat_id, text=message_text)
        return False

    LOGGER.info("Ergebnisdatei erfolgreich an Telegram gesendet: %s", output_path.name)
    return True


def find_latest_certificate_output(underlying_isin: str, created_after: datetime) -> Path | None:
    pattern = f"zertifikate_analyse_{underlying_isin}_*.json"
    candidates = sorted(Path(".").glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    for candidate in candidates:
        modified_at = datetime.fromtimestamp(candidate.stat().st_mtime)
        if modified_at >= created_after:
            return candidate
    return None


def format_market_brief(data: dict) -> str:
    from io import StringIO
    from contextlib import redirect_stdout

    buffer = StringIO()
    with redirect_stdout(buffer):
        print_market_brief_text(data)
    return buffer.getvalue().strip()


def parse_hhmm(value: str) -> time:
    return datetime.strptime(value.strip(), "%H:%M").time()


def is_within_time_window(now: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= now <= end
    return now >= start or now <= end


def parse_bool_flag(value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "y", "ja", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "nein", "off"}:
        return False
    raise ValueError("Boolescher Wert erwartet.")


def format_auto_brief_settings(settings: AutoBriefSettings) -> str:
    return (
        "Auto Market Brief\n"
        f"Aktiv: {'ja' if settings.enabled else 'nein'}\n"
        f"Zeitfenster: {settings.start_time} - {settings.end_time}\n"
        f"Intervall: {settings.interval_minutes} Minuten\n"
        f"Kategorie: {settings.category or 'alle'}\n"
        f"Subkategorie: {settings.subcategory or 'alle'}\n"
        f"News-Zusammenfassungen: {'ja' if settings.with_news_summary else 'nein'}\n"
        f"Ausfuehrliche Ergebnisnachricht: {'ja' if settings.send_detailed_result_message else 'nein'}\n"
        f"Ziel-Chat-ID: {settings.chat_id or '-'}\n"
        f"Letzter Lauf: {settings.last_run_at or '-'}"
    )


def build_auto_brief_menu_text(settings: AutoBriefSettings) -> str:
    next_run, reason = compute_next_auto_brief_run(settings)
    next_run_text = next_run.strftime("%Y-%m-%d %H:%M:%S") if next_run is not None else reason
    return (
        "Auto Market Brief Einstellungen\n\n"
        f"Aktiv: {'ja' if settings.enabled else 'nein'}\n"
        f"Kategorie: {settings.category or 'alle'}\n"
        f"Subkategorie: {settings.subcategory or 'alle'}\n"
        f"News-Zusammenfassungen: {'ja' if settings.with_news_summary else 'nein'}\n"
        f"Ausfuehrliche Ergebnisnachricht: {'ja' if settings.send_detailed_result_message else 'nein'}\n"
        f"Intervall: {settings.interval_minutes} Minuten\n"
        f"Zeitfenster: {settings.start_time} - {settings.end_time}\n"
        f"Naechster Lauf: {next_run_text}"
    )


def build_auto_brief_enabled_message(settings: AutoBriefSettings) -> str:
    next_run, reason = compute_next_auto_brief_run(settings)
    lines = [
        "Auto Market Brief aktiviert.",
        "",
        format_auto_brief_settings(settings),
        "",
    ]
    if next_run is not None:
        lines.append(f"Naechster geplanter Lauf: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
    else:
        lines.append(f"Naechster geplanter Lauf: {reason}")
    lines.append(
        "Hinweis: Nach dem Aktivieren wird ein alter last_run_at-Wert verworfen, damit ein neuer Lauf nicht blockiert wird."
    )
    return "\n".join(lines)


def build_auto_brief_menu_keyboard(settings: AutoBriefSettings) -> InlineKeyboardMarkup:
    toggle_label = "Deaktivieren" if settings.enabled else "Aktivieren"
    news_label = "News: An" if settings.with_news_summary else "News: Aus"
    result_label = "Ergebnis: Lang" if settings.send_detailed_result_message else "Ergebnis: Kurz"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(toggle_label, callback_data=f"{CALLBACK_PREFIX_AUTO_MENU}toggle_enabled"),
                InlineKeyboardButton(news_label, callback_data=f"{CALLBACK_PREFIX_AUTO_MENU}toggle_news"),
            ],
            [
                InlineKeyboardButton(result_label, callback_data=f"{CALLBACK_PREFIX_AUTO_MENU}toggle_result_message"),
            ],
            [
                InlineKeyboardButton("Kategorie", callback_data=f"{CALLBACK_PREFIX_AUTO_MENU}category"),
                InlineKeyboardButton("Subkategorie", callback_data=f"{CALLBACK_PREFIX_AUTO_MENU}subcategory"),
            ],
            [
                InlineKeyboardButton("Intervall", callback_data=f"{CALLBACK_PREFIX_AUTO_MENU}interval"),
                InlineKeyboardButton("Zeitfenster", callback_data=f"{CALLBACK_PREFIX_AUTO_MENU}window"),
            ],
            [
                InlineKeyboardButton("Status aktualisieren", callback_data=f"{CALLBACK_PREFIX_AUTO_MENU}refresh"),
                InlineKeyboardButton("Fertig", callback_data=f"{CALLBACK_PREFIX_AUTO_MENU}done"),
            ],
        ]
    )


def compute_next_auto_brief_run(settings: AutoBriefSettings, now: datetime | None = None) -> tuple[datetime | None, str]:
    if not settings.enabled:
        return None, "Auto Market Brief ist deaktiviert."
    if not settings.chat_id:
        return None, "Keine Ziel-Chat-ID gesetzt."

    try:
        start = parse_hhmm(settings.start_time)
        end = parse_hhmm(settings.end_time)
    except ValueError:
        return None, "Zeitfenster ist ungueltig konfiguriert."

    now = now or datetime.now()
    interval_seconds = max(1, settings.interval_minutes) * 60
    base_candidate = now.replace(second=0, microsecond=0) + timedelta(minutes=1)

    if settings.last_run_at:
        try:
            last_run = datetime.fromisoformat(settings.last_run_at)
        except ValueError:
            last_run = None
        if last_run is not None:
            by_interval = last_run + timedelta(seconds=interval_seconds)
            if by_interval > base_candidate:
                base_candidate = by_interval

    candidate = base_candidate
    for _ in range(60 * 24 * 8):
        if is_within_time_window(candidate.time(), start, end):
            return candidate, ""
        candidate += timedelta(minutes=1)

    return None, "Kein naechster Lauf innerhalb der naechsten 7 Tage gefunden."


@dataclass
class BotRuntime:
    allowed_chat_ids: set[int]
    allowed_user_ids: set[int]
    gemini_model: str
    config: dict
    auto_brief: AutoBriefSettings


def persist_auto_brief_settings(runtime: BotRuntime) -> None:
    runtime.config["auto_market_brief"] = runtime.auto_brief.to_config()
    save_config(runtime.config)


async def show_auto_brief_menu(message, runtime: BotRuntime) -> None:
    await message.reply_text(
        build_auto_brief_menu_text(runtime.auto_brief),
        reply_markup=build_auto_brief_menu_keyboard(runtime.auto_brief),
    )


async def update_auto_brief_menu(query, runtime: BotRuntime) -> bool:
    try:
        await query.edit_message_text(
            build_auto_brief_menu_text(runtime.auto_brief),
            reply_markup=build_auto_brief_menu_keyboard(runtime.auto_brief),
        )
        return True
    except BadRequest as exc:
        if "Message is not modified" in str(exc):
            return False
        raise


async def guarded(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    handler: Callable[[Update, ContextTypes.DEFAULT_TYPE, BotRuntime], Awaitable[None]],
) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    if not ensure_allowed_chat(update, runtime.allowed_chat_ids, runtime.allowed_user_ids):
        message = update.effective_message
        if message is not None:
            await message.reply_text("Dieser User oder Chat ist nicht freigeschaltet.")
        return

    try:
        await handler(update, context, runtime)
    except Exception as exc:
        LOGGER.exception("Telegram command failed")
        message = update.effective_message
        if message is not None:
            await message.reply_text(f"Fehler: {exc}")


def build_main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Einzelanalyse", callback_data=f"{CALLBACK_PREFIX_MAIN_MENU}marketbrief")],
            [InlineKeyboardButton("Batch Market Brief", callback_data=f"{CALLBACK_PREFIX_MAIN_MENU}batch")],
            [
                InlineKeyboardButton("Auto-Brief", callback_data=f"{CALLBACK_PREFIX_MAIN_MENU}autobrief"),
                InlineKeyboardButton("Listenpflege", callback_data=f"{CALLBACK_PREFIX_MAIN_MENU}listenpflege"),
            ],
            [
                InlineKeyboardButton("Support-Bot", callback_data=f"{CALLBACK_PREFIX_MAIN_MENU}support"),
                InlineKeyboardButton("Kategorien", callback_data=f"{CALLBACK_PREFIX_MAIN_MENU}categories"),
            ],
            [InlineKeyboardButton("Bot testen", callback_data=f"{CALLBACK_PREFIX_MAIN_MENU}ping")],
            [InlineKeyboardButton("Schliessen", callback_data=f"{CALLBACK_PREFIX_MAIN_MENU}done")],
        ]
    )


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str = "") -> int:
    message_text = text or "Market-Brief-Bot\n\nWaehle eine Aktion:"
    query = update.callback_query
    message = update.effective_message
    if query is not None:
        await query.edit_message_text(message_text, reply_markup=build_main_menu_keyboard())
    elif message is not None:
        await message.reply_text(message_text, reply_markup=build_main_menu_keyboard())
    return STATE_MAIN_MENU


def format_categories_text(mapping: dict[str, list[str]]) -> str:
    lines = ["Kategorien:"]
    for category, subcategories in mapping.items():
        lines.append(f"- {category}")
        for subcategory in subcategories:
            lines.append(f"  - {subcategory}")
    return "\n".join(lines)


async def marketbrief_menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE, runtime: BotRuntime) -> int:
    return await show_main_menu(update, context)


async def marketbrief_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None:
        return STATE_MAIN_MENU
    await query.answer()
    action = query.data.removeprefix(CALLBACK_PREFIX_MAIN_MENU)
    runtime: BotRuntime = context.application.bot_data["runtime"]

    if action == "done":
        await query.edit_message_text("Market-Brief-Menue geschlossen.")
        return ConversationHandler.END

    if action == "ping":
        await query.edit_message_text("pong", reply_markup=build_main_menu_keyboard())
        return STATE_MAIN_MENU

    if action == "categories":
        mapping = await run_blocking(load_subcategories)
        await query.edit_message_text(format_categories_text(mapping), reply_markup=build_main_menu_keyboard())
        return STATE_MAIN_MENU

    if action == "marketbrief":
        await query.edit_message_text("Name, Ticker, ISIN oder WKN eingeben:")
        if query.message is not None:
            await query.message.reply_text("Market-Brief Query:", reply_markup=build_text_navigation_keyboard())
        return STATE_MARKETBRIEF_QUERY

    if action == "batch":
        categories = await run_blocking(load_categories)
        context.user_data["batch_categories"] = categories
        context.user_data["batch_selected_queries"] = set()
        await query.edit_message_text(
            "Batch Market Brief starten.\nKategorie waehlen:",
            reply_markup=build_choice_keyboard(categories, CALLBACK_PREFIX_CATEGORY),
        )
        return STATE_BATCH_CATEGORY

    if action == "autobrief":
        context.user_data["auto_categories"] = await run_blocking(load_categories)
        context.user_data["auto_subcategories"] = await run_blocking(load_subcategories)
        if query.message is not None:
            await show_auto_brief_menu(query.message, runtime)
        return STATE_AUTO_MENU

    if action == "listenpflege":
        cleanup_listenpflege_context(context)
        context.user_data["list_categories"] = await run_blocking(load_categories)
        context.user_data["list_subcategories"] = await run_blocking(load_subcategories)
        if query.message is not None:
            await show_listenpflege_action_menu(query.message)
        return STATE_LIST_ACTION

    if action == "support":
        await query.edit_message_text(
            build_support_bot_menu_text(),
            reply_markup=build_support_bot_menu_keyboard(),
        )
        return STATE_SUPPORT_MENU

    await query.edit_message_text("Ungueltige Auswahl.", reply_markup=build_main_menu_keyboard())
    return STATE_MAIN_MENU


async def marketbrief_menu_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if message is None:
        return STATE_MARKETBRIEF_QUERY
    nav = listenpflege_text_nav_choice(message)
    if nav == "cancel":
        await message.reply_text("Market Brief abgebrochen.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    if nav == "back":
        await message.reply_text("Zurueck zum Hauptmenue.", reply_markup=ReplyKeyboardRemove())
        await show_main_menu(update, context)
        return STATE_MAIN_MENU

    query_text = (message.text or "").strip()
    if not query_text:
        await message.reply_text("Bitte Name, Ticker, ISIN oder WKN eingeben.", reply_markup=build_text_navigation_keyboard())
        return STATE_MARKETBRIEF_QUERY

    runtime: BotRuntime = context.application.bot_data["runtime"]
    started_at = datetime.now()
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        entry = await run_blocking(load_index_entry, "config/stock_categories/stock_categories.xml", query_text)
        data = await run_blocking(fetch_market_brief, entry, True, runtime.gemini_model)
    except Exception as exc:
        await message.reply_text(f"Market Brief fehlgeschlagen: {exc}", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    elapsed_seconds = (datetime.now() - started_at).total_seconds()
    await message.reply_text("Market Brief fertig.", reply_markup=ReplyKeyboardRemove())
    await reply_long(update, format_market_brief(data) + f"\n\nErstellungsdauer: {elapsed_seconds:.1f} Sekunden")
    return ConversationHandler.END


async def main_menu_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    for key in [
        "batch_category",
        "batch_subcategory",
        "batch_entry_queries",
        "batch_selected_queries",
        "batch_send_full_result",
        "batch_with_news_summary",
        "batch_categories",
        "batch_subcategories",
        "batch_subcategory_options",
        "batch_entry_options",
    ]:
        context.user_data.pop(key, None)
    cleanup_autobrief_context(context)
    cleanup_listenpflege_context(context)
    message = update.effective_message
    if message is not None:
        await message.reply_text("Market-Brief-Menue abgebrochen.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE, runtime: BotRuntime) -> None:
    await show_main_menu(update, context)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE, runtime: BotRuntime) -> None:
    await show_main_menu(update, context)


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE, runtime: BotRuntime) -> None:
    await reply_long(update, "pong")


async def categories_command(update: Update, context: ContextTypes.DEFAULT_TYPE, runtime: BotRuntime) -> None:
    mapping = await run_blocking(load_subcategories)
    lines = ["Kategorien:"]
    for category, subcategories in mapping.items():
        lines.append(f"- {category}")
        for subcategory in subcategories:
            lines.append(f"  - {subcategory}")
    await reply_long(update, "\n".join(lines))


async def marketbrief_command(update: Update, context: ContextTypes.DEFAULT_TYPE, runtime: BotRuntime) -> None:
    if not context.args:
        await reply_long(update, "Verwendung: /marketbrief <query>")
        return

    query = " ".join(context.args).strip()
    started_at = datetime.now()
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    entry = await run_blocking(load_index_entry, "config/stock_categories/stock_categories.xml", query)
    data = await run_blocking(fetch_market_brief, entry, True, runtime.gemini_model)
    elapsed_seconds = (datetime.now() - started_at).total_seconds()
    await reply_long(update, format_market_brief(data) + f"\n\nErstellungsdauer: {elapsed_seconds:.1f} Sekunden")


async def article_summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE, runtime: BotRuntime) -> None:
    if not context.args:
        await reply_long(update, "Verwendung: /article_summary <url>")
        return

    url = context.args[0].strip()
    api_key = resolve_api_key("")
    if not api_key:
        await reply_long(update, "Gemini API Key fehlt.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    article_data = await run_blocking(fetch_article, url, "")
    result = await run_blocking(
        summarize_article_with_cache,
        article_data["url"],
        article_data["requested_title"] or article_data["page_title"],
        article_data["article_text"],
        api_key,
        runtime.gemini_model,
    )

    text = (
        f"Titel: {result.get('title') or 'Unbekannt'}\n"
        f"URL: {result['url']}\n"
        f"Modell: {result['model']}\n"
        f"Cache: {result.get('cache_status', '-')}\n\n"
        f"{result['summary']}"
    )
    await reply_long(update, text)


async def certificate_scraper_start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    runtime: BotRuntime,
) -> int:
    context.user_data.pop("certificate_scraper", None)
    await reply_long(update, "Certificate Scraper starten.\nBitte geben Sie die ISIN des Basiswerts ein:")
    return STATE_CERTIFICATE_ISIN


async def certificate_scraper_isin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if message is None or not message.text:
        await reply_long(update, "Bitte eine gueltige ISIN senden.")
        return STATE_CERTIFICATE_ISIN

    context.user_data["certificate_scraper"] = {"isin": message.text.strip()}
    await reply_long(update, "Minimaler Hebel?")
    return STATE_CERTIFICATE_MIN


async def certificate_scraper_min(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if message is None or not message.text:
        await reply_long(update, "Bitte eine Zahl senden.")
        return STATE_CERTIFICATE_MIN

    try:
        min_leverage = int(message.text.strip())
    except ValueError:
        await reply_long(update, "Ungueltige Zahl. Bitte den minimalen Hebel als ganze Zahl senden.")
        return STATE_CERTIFICATE_MIN

    context.user_data.setdefault("certificate_scraper", {})["min_leverage"] = min_leverage
    await reply_long(update, "Maximaler Hebel?")
    return STATE_CERTIFICATE_MAX


async def certificate_scraper_max(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if message is None or not message.text:
        await reply_long(update, "Bitte eine Zahl senden.")
        return STATE_CERTIFICATE_MAX

    try:
        max_leverage = int(message.text.strip())
    except ValueError:
        await reply_long(update, "Ungueltige Zahl. Bitte den maximalen Hebel als ganze Zahl senden.")
        return STATE_CERTIFICATE_MAX

    data = context.user_data.setdefault("certificate_scraper", {})
    min_leverage = int(data.get("min_leverage", 0))
    if max_leverage < min_leverage:
        await reply_long(update, "Der maximale Hebel darf nicht kleiner als der minimale Hebel sein.")
        return STATE_CERTIFICATE_MAX

    data["max_leverage"] = max_leverage
    await reply_long(update, "Richtung? Bitte 'long' oder 'short' senden.")
    return STATE_CERTIFICATE_DIRECTION


async def certificate_scraper_direction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if message is None or not message.text:
        await reply_long(update, "Bitte 'long' oder 'short' senden.")
        return STATE_CERTIFICATE_DIRECTION

    direction = message.text.strip().casefold()
    if direction not in {"long", "short"}:
        await reply_long(update, "Ungueltige Richtung. Bitte 'long' oder 'short' senden.")
        return STATE_CERTIFICATE_DIRECTION

    data = context.user_data.setdefault("certificate_scraper", {})
    data["direction"] = direction
    isin = str(data["isin"])
    min_leverage = int(data["min_leverage"])
    max_leverage = int(data["max_leverage"])
    chat = update.effective_chat
    if chat is None:
        await reply_long(update, "Chat nicht verfuegbar.")
        context.user_data.pop("certificate_scraper", None)
        return ConversationHandler.END

    status_message = await message.reply_text(
        "Certificate Scraper gestartet.\n"
        f"ISIN: {isin}\n"
        f"Hebel: {min_leverage} - {max_leverage}\n"
        f"Richtung: {direction}\n"
        "Status: Starte Prozess..."
    )

    started_at = datetime.now()
    stdin_payload = f"{isin}\n{min_leverage}\n{max_leverage}\n{direction}\n"
    process = await asyncio.create_subprocess_exec(
        os.sys.executable,
        "certificate_scraper.py",
        cwd=str(Path.cwd()),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_task = asyncio.create_task(process.stdout.read())
    stderr_task = asyncio.create_task(process.stderr.read())
    assert process.stdin is not None
    process.stdin.write(stdin_payload.encode("utf-8"))
    await process.stdin.drain()
    process.stdin.close()
    wait_task = asyncio.create_task(process.wait())

    tick = 0
    while not wait_task.done():
        await asyncio.sleep(2)
        tick += 1
        dots = "." * ((tick % 3) + 1)
        elapsed = int((datetime.now() - started_at).total_seconds())
        try:
            await status_message.edit_text(
                "Certificate Scraper laeuft"
                f"{dots}\nISIN: {isin}\nHebel: {min_leverage} - {max_leverage}\n"
                f"Richtung: {direction}\nLaufzeit: {elapsed}s"
            )
        except Exception:
            pass

    await wait_task

    stdout_text = (await stdout_task).decode("utf-8", errors="replace").strip()
    stderr_text = (await stderr_task).decode("utf-8", errors="replace").strip()

    output_file = await run_blocking(find_latest_certificate_output, isin, started_at)
    if process.returncode == 0 and output_file is not None and output_file.exists():
        await status_message.edit_text(
            "Certificate Scraper abgeschlossen.\n"
            f"ISIN: {isin}\nHebel: {min_leverage} - {max_leverage}\n"
            f"Richtung: {direction}\nDatei: {output_file.name}"
        )
        await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.UPLOAD_DOCUMENT)
        with output_file.open("rb") as handle:
            await message.reply_document(document=handle, filename=output_file.name)
        if stdout_text:
            await reply_long(update, stdout_text)
    else:
        error_text = stderr_text or stdout_text or "Unbekannter Fehler."
        await status_message.edit_text(
            "Certificate Scraper fehlgeschlagen.\n"
            f"ISIN: {isin}\nHebel: {min_leverage} - {max_leverage}\n"
            f"Richtung: {direction}\n"
            f"Exit Code: {process.returncode}"
        )
        await reply_long(update, f"Fehler: {error_text}")

    context.user_data.pop("certificate_scraper", None)
    return ConversationHandler.END


async def autobrief_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE, runtime: BotRuntime) -> int:
    message = update.effective_message
    if message is None:
        return ConversationHandler.END
    await show_auto_brief_menu(message, runtime)
    return STATE_AUTO_MENU


async def autobrief_next_command(update: Update, context: ContextTypes.DEFAULT_TYPE, runtime: BotRuntime) -> None:
    next_run, reason = compute_next_auto_brief_run(runtime.auto_brief)
    if next_run is None:
        await reply_long(update, f"Naechster Auto Market Brief: {reason}")
        return

    await reply_long(
        update,
        "Naechster Auto Market Brief: "
        + next_run.strftime("%Y-%m-%d %H:%M:%S")
        + f"\nIntervall: {runtime.auto_brief.interval_minutes} Minuten\n"
        + f"Zeitfenster: {runtime.auto_brief.start_time} - {runtime.auto_brief.end_time}",
    )


async def autobrief_set_command(update: Update, context: ContextTypes.DEFAULT_TYPE, runtime: BotRuntime) -> None:
    if len(context.args) < 3:
        await reply_long(update, "Verwendung: /autobrief_set <start HH:MM> <end HH:MM> <interval_min> [news on|off]")
        return

    start_text = context.args[0].strip()
    end_text = context.args[1].strip()
    interval_text = context.args[2].strip()
    news_flag = runtime.auto_brief.with_news_summary

    try:
        parse_hhmm(start_text)
        parse_hhmm(end_text)
        interval_minutes = int(interval_text)
        if interval_minutes < 1:
            raise ValueError
        if len(context.args) >= 4:
            news_flag = parse_bool_flag(context.args[3])
    except ValueError:
        await reply_long(update, "Ungueltige Werte. Beispiel: /autobrief_set 08:00 18:00 60 on")
        return

    chat = update.effective_chat
    runtime.auto_brief.start_time = start_text
    runtime.auto_brief.end_time = end_text
    runtime.auto_brief.interval_minutes = interval_minutes
    runtime.auto_brief.with_news_summary = news_flag
    runtime.auto_brief.chat_id = chat.id if chat is not None else runtime.auto_brief.chat_id
    persist_auto_brief_settings(runtime)
    configure_auto_brief_job(context.application, runtime)
    await reply_long(update, "Auto-Market-Brief-Konfiguration gespeichert.\n\n" + format_auto_brief_settings(runtime.auto_brief))


async def autobrief_filter_command(update: Update, context: ContextTypes.DEFAULT_TYPE, runtime: BotRuntime) -> None:
    if not context.args:
        runtime.auto_brief.category = ""
        runtime.auto_brief.subcategory = ""
        chat = update.effective_chat
        runtime.auto_brief.chat_id = chat.id if chat is not None else runtime.auto_brief.chat_id
        persist_auto_brief_settings(runtime)
        await reply_long(update, "Auto-Market-Brief-Filter entfernt. Es werden wieder alle Eintraege verwendet.")
        return

    category = context.args[0].strip()
    subcategory = " ".join(context.args[1:]).strip() if len(context.args) > 1 else ""
    available = await run_blocking(load_subcategories)
    if category not in available:
        await reply_long(update, f"Unbekannte Kategorie: {category}")
        return
    if subcategory and subcategory not in available.get(category, []):
        await reply_long(update, f"Unbekannte Subkategorie fuer {category}: {subcategory}")
        return

    chat = update.effective_chat
    runtime.auto_brief.category = category
    runtime.auto_brief.subcategory = subcategory
    runtime.auto_brief.chat_id = chat.id if chat is not None else runtime.auto_brief.chat_id
    persist_auto_brief_settings(runtime)
    await reply_long(update, "Auto-Market-Brief-Filter gespeichert.\n\n" + format_auto_brief_settings(runtime.auto_brief))


async def autobrief_on_command(update: Update, context: ContextTypes.DEFAULT_TYPE, runtime: BotRuntime) -> None:
    chat = update.effective_chat
    runtime.auto_brief.enabled = True
    runtime.auto_brief.chat_id = chat.id if chat is not None else runtime.auto_brief.chat_id
    runtime.auto_brief.last_run_at = ""
    persist_auto_brief_settings(runtime)
    configure_auto_brief_job(context.application, runtime)
    await reply_long(update, build_auto_brief_enabled_message(runtime.auto_brief))


async def autobrief_off_command(update: Update, context: ContextTypes.DEFAULT_TYPE, runtime: BotRuntime) -> None:
    runtime.auto_brief.enabled = False
    persist_auto_brief_settings(runtime)
    configure_auto_brief_job(context.application, runtime)
    await reply_long(update, "Auto Market Brief deaktiviert.\n\n" + format_auto_brief_settings(runtime.auto_brief))


async def autobrief_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE, runtime: BotRuntime) -> int:
    categories = await run_blocking(load_categories)
    context.user_data["auto_categories"] = categories
    context.user_data["auto_subcategories"] = await run_blocking(load_subcategories)
    return await autobrief_status_command(update, context, runtime)


async def autobrief_start_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None:
        return STATE_AUTO_MENU
    await query.answer()
    runtime: BotRuntime = context.application.bot_data["runtime"]
    action = query.data.removeprefix(CALLBACK_PREFIX_AUTO_MENU)

    if action == "toggle_enabled":
        was_enabled = runtime.auto_brief.enabled
        runtime.auto_brief.enabled = not runtime.auto_brief.enabled
        if query.message is not None and query.message.chat is not None:
            runtime.auto_brief.chat_id = query.message.chat.id
        if not was_enabled and runtime.auto_brief.enabled:
            runtime.auto_brief.last_run_at = ""
        persist_auto_brief_settings(runtime)
        configure_auto_brief_job(context.application, runtime)
        if not was_enabled and runtime.auto_brief.enabled:
            await query.edit_message_text(
                build_auto_brief_enabled_message(runtime.auto_brief),
                reply_markup=build_auto_brief_menu_keyboard(runtime.auto_brief),
            )
        else:
            await update_auto_brief_menu(query, runtime)
        return STATE_AUTO_MENU

    if action == "toggle_news":
        runtime.auto_brief.with_news_summary = not runtime.auto_brief.with_news_summary
        persist_auto_brief_settings(runtime)
        await update_auto_brief_menu(query, runtime)
        return STATE_AUTO_MENU

    if action == "toggle_result_message":
        runtime.auto_brief.send_detailed_result_message = not runtime.auto_brief.send_detailed_result_message
        persist_auto_brief_settings(runtime)
        await update_auto_brief_menu(query, runtime)
        return STATE_AUTO_MENU

    if action == "category":
        categories: list[str] = context.user_data.get("auto_categories", [])
        await query.edit_message_text(
            "Kategorie waehlen:",
            reply_markup=build_choice_keyboard(categories, CALLBACK_PREFIX_AUTO_CATEGORY),
        )
        return STATE_AUTO_CATEGORY

    if action == "subcategory":
        options = get_auto_subcategory_options(runtime, context)
        await query.edit_message_text(
            "Subkategorie waehlen:",
            reply_markup=build_choice_keyboard(options, CALLBACK_PREFIX_AUTO_SUBCATEGORY),
        )
        return STATE_AUTO_SUBCATEGORY

    if action == "interval":
        await query.edit_message_text(
            "Intervall waehlen:",
            reply_markup=build_option_keyboard(
                [
                    ("15 Minuten", f"{CALLBACK_PREFIX_AUTO_INTERVAL}15"),
                    ("30 Minuten", f"{CALLBACK_PREFIX_AUTO_INTERVAL}30"),
                    ("60 Minuten", f"{CALLBACK_PREFIX_AUTO_INTERVAL}60"),
                    ("120 Minuten", f"{CALLBACK_PREFIX_AUTO_INTERVAL}120"),
                    ("Zurueck", f"{CALLBACK_PREFIX_AUTO_INTERVAL}back"),
                ]
            ),
        )
        return STATE_AUTO_INTERVAL

    if action == "window":
        await query.edit_message_text(
            "Startstunde waehlen:",
            reply_markup=build_time_choice_keyboard(CALLBACK_PREFIX_AUTO_WINDOW, mode="from"),
        )
        return STATE_AUTO_WINDOW_FROM

    if action == "refresh":
        changed = await update_auto_brief_menu(query, runtime)
        if not changed:
            await query.answer("Status ist bereits aktuell.", show_alert=False)
        return STATE_AUTO_MENU

    if action == "done":
        await query.edit_message_text(
            "Auto-Market-Brief-Konfiguration gespeichert.\n\n"
            + build_auto_brief_menu_text(runtime.auto_brief)
        )
        cleanup_autobrief_context(context)
        return ConversationHandler.END

    await query.edit_message_text("Ungueltige Auswahl. Bitte /autobrief_start erneut ausfuehren.")
    cleanup_autobrief_context(context)
    return ConversationHandler.END


async def autobrief_start_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    categories: list[str] = context.user_data.get("auto_categories", [])
    query = update.callback_query
    if query is None:
        return STATE_AUTO_CATEGORY
    await query.answer()
    raw = query.data.removeprefix(CALLBACK_PREFIX_AUTO_CATEGORY)
    selection = "" if raw == "ALL" else raw
    if selection and selection not in categories:
        await query.edit_message_text("Ungueltige Auswahl. Bitte /autobrief_start erneut ausfuehren.")
        return ConversationHandler.END

    runtime: BotRuntime = context.application.bot_data["runtime"]
    runtime.auto_brief.category = selection
    if selection:
        available_subcategories = context.user_data.get("auto_subcategories", {}).get(selection, [])
        if runtime.auto_brief.subcategory and runtime.auto_brief.subcategory not in available_subcategories:
            runtime.auto_brief.subcategory = ""
    persist_auto_brief_settings(runtime)
    await update_auto_brief_menu(query, runtime)
    return STATE_AUTO_MENU


async def autobrief_start_subcategory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None:
        return STATE_AUTO_SUBCATEGORY
    await query.answer()
    raw = query.data.removeprefix(CALLBACK_PREFIX_AUTO_SUBCATEGORY)
    if raw == "back":
        runtime: BotRuntime = context.application.bot_data["runtime"]
        await update_auto_brief_menu(query, runtime)
        return STATE_AUTO_MENU

    options = get_auto_subcategory_options(context.application.bot_data["runtime"], context)
    selection = "" if raw == "ALL" else raw
    if selection and selection not in options:
        await query.edit_message_text("Ungueltige Auswahl. Bitte /autobrief_start erneut ausfuehren.")
        return ConversationHandler.END

    runtime: BotRuntime = context.application.bot_data["runtime"]
    runtime.auto_brief.subcategory = selection
    persist_auto_brief_settings(runtime)
    await update_auto_brief_menu(query, runtime)
    return STATE_AUTO_MENU


async def autobrief_start_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None:
        return STATE_AUTO_INTERVAL
    await query.answer()
    raw = query.data.removeprefix(CALLBACK_PREFIX_AUTO_INTERVAL).strip()
    if raw == "back":
        runtime: BotRuntime = context.application.bot_data["runtime"]
        await update_auto_brief_menu(query, runtime)
        return STATE_AUTO_MENU
    try:
        interval_minutes = int(raw)
        if interval_minutes < 1:
            raise ValueError
    except ValueError:
        await query.edit_message_text("Ungueltige Auswahl. Bitte /autobrief_start erneut ausfuehren.")
        return ConversationHandler.END

    runtime: BotRuntime = context.application.bot_data["runtime"]
    runtime.auto_brief.interval_minutes = interval_minutes
    persist_auto_brief_settings(runtime)
    await update_auto_brief_menu(query, runtime)
    return STATE_AUTO_MENU


async def autobrief_start_window_from(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None:
        return STATE_AUTO_WINDOW_FROM
    await query.answer()
    raw = query.data.removeprefix(CALLBACK_PREFIX_AUTO_WINDOW).strip()
    if raw == "back":
        context.user_data.pop("auto_window_from_hour", None)
        runtime: BotRuntime = context.application.bot_data["runtime"]
        await update_auto_brief_menu(query, runtime)
        return STATE_AUTO_MENU
    if raw == "from_minute_back":
        context.user_data.pop("auto_window_from_hour", None)
        await query.edit_message_text(
            "Startstunde waehlen:",
            reply_markup=build_time_choice_keyboard(CALLBACK_PREFIX_AUTO_WINDOW, mode="from"),
        )
        return STATE_AUTO_WINDOW_FROM

    if raw.startswith("h:"):
        hour = raw.removeprefix("h:")
        context.user_data["auto_window_from_hour"] = hour
        await query.edit_message_text(
            f"Startstunde: {hour}\nJetzt Startminute waehlen:",
            reply_markup=build_time_minute_choice_keyboard(
                CALLBACK_PREFIX_AUTO_WINDOW,
                hour,
                back_value="from_minute_back",
            ),
        )
        return STATE_AUTO_WINDOW_FROM

    if raw.startswith("m:"):
        hour = str(context.user_data.get("auto_window_from_hour", "")).strip()
        minute = raw.removeprefix("m:")
        try:
            start_time = f"{hour}:{minute}"
            parse_hhmm(start_time)
        except ValueError:
            await query.edit_message_text("Ungueltige Auswahl. Bitte /autobrief_start erneut ausfuehren.")
            return ConversationHandler.END

        context.user_data["auto_window_start_time"] = start_time
        context.user_data.pop("auto_window_from_hour", None)
        await query.edit_message_text(
            f"Startzeit: {start_time}\nJetzt Endstunde waehlen:",
            reply_markup=build_time_choice_keyboard(CALLBACK_PREFIX_AUTO_WINDOW, mode="to"),
        )
        return STATE_AUTO_WINDOW_TO

    await query.edit_message_text("Ungueltige Auswahl. Bitte /autobrief_start erneut ausfuehren.")
    return ConversationHandler.END


async def autobrief_start_window_to(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None:
        return STATE_AUTO_WINDOW_TO
    await query.answer()
    raw = query.data.removeprefix(CALLBACK_PREFIX_AUTO_WINDOW).strip()
    if raw == "back":
        context.user_data.pop("auto_window_to_hour", None)
        context.user_data.pop("auto_window_start_time", None)
        await query.edit_message_text(
            "Startstunde waehlen:",
            reply_markup=build_time_choice_keyboard(CALLBACK_PREFIX_AUTO_WINDOW, mode="from"),
        )
        return STATE_AUTO_WINDOW_FROM
    if raw == "to_minute_back":
        context.user_data.pop("auto_window_to_hour", None)
        await query.edit_message_text(
            "Endstunde waehlen:",
            reply_markup=build_time_choice_keyboard(CALLBACK_PREFIX_AUTO_WINDOW, mode="to"),
        )
        return STATE_AUTO_WINDOW_TO

    if raw.startswith("h:"):
        hour = raw.removeprefix("h:")
        context.user_data["auto_window_to_hour"] = hour
        await query.edit_message_text(
            f"Endstunde: {hour}\nJetzt Endminute waehlen:",
            reply_markup=build_time_minute_choice_keyboard(
                CALLBACK_PREFIX_AUTO_WINDOW,
                hour,
                back_value="to_minute_back",
            ),
        )
        return STATE_AUTO_WINDOW_TO

    if not raw.startswith("m:"):
        await query.edit_message_text("Ungueltige Auswahl. Bitte /autobrief_start erneut ausfuehren.")
        return ConversationHandler.END

    minute = raw.removeprefix("m:")
    hour = str(context.user_data.get("auto_window_to_hour", "")).strip()
    try:
        end_time = f"{hour}:{minute}"
        parse_hhmm(end_time)
        start_time = str(context.user_data.get("auto_window_start_time", "08:00"))
        parse_hhmm(start_time)
    except ValueError:
        await query.edit_message_text("Ungueltige Auswahl. Bitte /autobrief_start erneut ausfuehren.")
        return ConversationHandler.END

    runtime: BotRuntime = context.application.bot_data["runtime"]
    runtime.auto_brief.start_time = start_time
    runtime.auto_brief.end_time = end_time
    persist_auto_brief_settings(runtime)
    context.user_data.pop("auto_window_to_hour", None)
    context.user_data.pop("auto_window_start_time", None)
    await update_auto_brief_menu(query, runtime)
    return STATE_AUTO_MENU


async def echo_command(update: Update, context: ContextTypes.DEFAULT_TYPE, runtime: BotRuntime) -> None:
    if not context.args:
        await reply_long(update, "Verwendung: /echo <text>")
        return
    await reply_long(update, " ".join(context.args))


def build_choice_keyboard(options: list[str], prefix: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("Alle", callback_data=f"{prefix}ALL")]]
    for option in options:
        rows.append([InlineKeyboardButton(option, callback_data=f"{prefix}{option}")])
    return InlineKeyboardMarkup(rows)


def build_support_bot_menu_text(action_message: str = "") -> str:
    status = get_support_bot_status()
    lines = [
        "Market Brief Support Status",
        f"Status: {'laeuft' if status['running'] else 'gestoppt'}",
        f"PID: {status['pid'] or '-'}",
        f"Lock-Datei: {'ja' if status['lock_exists'] else 'nein'}",
    ]
    if action_message:
        lines.extend(["", action_message])
    return "\n".join(lines)


def build_support_bot_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Start", callback_data=f"{CALLBACK_PREFIX_SUPPORT}start")],
            [InlineKeyboardButton("Stop", callback_data=f"{CALLBACK_PREFIX_SUPPORT}stop")],
            [InlineKeyboardButton("Restart", callback_data=f"{CALLBACK_PREFIX_SUPPORT}restart")],
            [InlineKeyboardButton("Fertig", callback_data=f"{CALLBACK_PREFIX_SUPPORT}done")],
        ]
    )


def build_option_keyboard(options: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    nav_labels = {"Zurueck", "Abbrechen", "Fertig"}
    rows: list[list[InlineKeyboardButton]] = []
    nav_row: list[InlineKeyboardButton] = []
    for label, value in options:
        button = InlineKeyboardButton(label, callback_data=value)
        if label in nav_labels:
            nav_row.append(button)
        else:
            rows.append([button])
    if nav_row:
        rows.append(nav_row)
    return InlineKeyboardMarkup(rows)


def build_entry_choice_keyboard(entries: list[dict[str, str]], prefix: str) -> InlineKeyboardMarkup:
    rows = []
    for index, entry in enumerate(entries):
        rows.append([InlineKeyboardButton(entry["name"], callback_data=f"{prefix}{index}")])
    rows.append(
        [
            InlineKeyboardButton("Zurueck", callback_data=f"{prefix}back"),
            InlineKeyboardButton("Abbrechen", callback_data=f"{prefix}cancel"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def format_stock_entry(entry: dict[str, str]) -> str:
    lines = [
        f"Kategorie: {entry.get('category', '-')}",
        f"Subkategorie: {entry.get('subcategory', '-')}",
        f"Name: {entry.get('name', '-')}",
        f"Ticker: {entry.get('ticker', '-')}",
        f"ISIN: {entry.get('isin', '-')}",
        f"WKN: {entry.get('wkn', '-')}",
        f"Trade Republic Aktie: {entry.get('trade_republic_aktie') or '-'}",
        f"Trade Republic Derivate: {entry.get('trade_republic_derivate') or '-'}",
    ]
    for field in OPTIONAL_STOCK_FIELDS:
        value = str(entry.get(field, "")).strip()
        if value:
            lines.append(f"{STOCK_FIELD_LABELS[field]}: {value}")
    return "\n".join(lines)


def build_text_navigation_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([["Zurueck", "Abbrechen"]], resize_keyboard=True, one_time_keyboard=False)


def build_list_add_category_keyboard(categories: list[str]) -> InlineKeyboardMarkup:
    options = [(category, f"{CALLBACK_PREFIX_LIST_ADD_CATEGORY}{category}") for category in categories]
    options.extend(
        [
            ("Neu hinzufuegen", f"{CALLBACK_PREFIX_LIST_ADD_CATEGORY}new"),
            ("Zurueck", f"{CALLBACK_PREFIX_LIST_ADD_CATEGORY}back"),
            ("Abbrechen", f"{CALLBACK_PREFIX_LIST_ADD_CATEGORY}cancel"),
        ]
    )
    return build_option_keyboard(options)


def build_list_add_subcategory_keyboard(subcategories: list[str]) -> InlineKeyboardMarkup:
    options = [(item, f"{CALLBACK_PREFIX_LIST_ADD_SUBCATEGORY}{item}") for item in subcategories]
    options.extend(
        [
            ("Neu hinzufuegen", f"{CALLBACK_PREFIX_LIST_ADD_SUBCATEGORY}new"),
            ("Zurueck", f"{CALLBACK_PREFIX_LIST_ADD_SUBCATEGORY}back"),
            ("Abbrechen", f"{CALLBACK_PREFIX_LIST_ADD_SUBCATEGORY}cancel"),
        ]
    )
    return build_option_keyboard(options)


def build_list_add_name_keyboard() -> InlineKeyboardMarkup:
    return build_option_keyboard(
        [
            ("Zurueck", f"{CALLBACK_PREFIX_LIST_ADD_NAME}back"),
            ("Abbrechen", f"{CALLBACK_PREFIX_LIST_ADD_NAME}cancel"),
        ]
    )


def build_stock_field_choice_keyboard() -> InlineKeyboardMarkup:
    options = [
        ("Name", f"{CALLBACK_PREFIX_LIST_FIELD}name"),
        ("Ticker", f"{CALLBACK_PREFIX_LIST_FIELD}ticker"),
        ("Ticker USA", f"{CALLBACK_PREFIX_LIST_FIELD}ticker_usa"),
        ("Ticker EU", f"{CALLBACK_PREFIX_LIST_FIELD}ticker_eu"),
        ("Ticker APAC", f"{CALLBACK_PREFIX_LIST_FIELD}ticker_apac"),
        ("ISIN", f"{CALLBACK_PREFIX_LIST_FIELD}isin"),
        ("WKN", f"{CALLBACK_PREFIX_LIST_FIELD}wkn"),
        ("TR Aktie", f"{CALLBACK_PREFIX_LIST_FIELD}trade_republic_aktie"),
        ("TR Derivate", f"{CALLBACK_PREFIX_LIST_FIELD}trade_republic_derivate"),
        ("Land", f"{CALLBACK_PREFIX_LIST_FIELD}land"),
        ("Tag", f"{CALLBACK_PREFIX_LIST_FIELD}tag"),
        ("Beschreibung", f"{CALLBACK_PREFIX_LIST_FIELD}description"),
        ("Kategorie", f"{CALLBACK_PREFIX_LIST_FIELD}category"),
        ("Subkategorie", f"{CALLBACK_PREFIX_LIST_FIELD}subcategory"),
        ("Zurueck", f"{CALLBACK_PREFIX_LIST_FIELD}back"),
        ("Abbrechen", f"{CALLBACK_PREFIX_LIST_FIELD}cancel"),
    ]
    return build_option_keyboard(options)


def build_list_optional_menu_keyboard(payload: dict[str, str]) -> InlineKeyboardMarkup:
    options = []
    for field in OPTIONAL_STOCK_FIELDS:
        marker = "[x]" if str(payload.get(field, "")).strip() else "[ ]"
        options.append((f"{marker} {STOCK_FIELD_LABELS[field]}", f"{CALLBACK_PREFIX_LIST_OPTIONAL}{field}"))
    options.extend(
        [
            ("Weiter zum Speichern", f"{CALLBACK_PREFIX_LIST_OPTIONAL}save"),
            ("Zurueck", f"{CALLBACK_PREFIX_LIST_OPTIONAL}back"),
            ("Abbrechen", f"{CALLBACK_PREFIX_LIST_OPTIONAL}cancel"),
        ]
    )
    return build_option_keyboard(options)


def stock_optional_field_prompt(field_name: str) -> str:
    prompts = {
        "ticker_usa": "US-Referenzticker eingeben, z.B. NVDA:",
        "ticker_eu": "Europa-Ticker eingeben, z.B. SAP.DE oder ASML.AS:",
        "ticker_apac": "APAC-Ticker eingeben, z.B. 7203.T oder 0700.HK:",
        "land": "Land eingeben, z.B. USA, Deutschland oder Japan:",
        "tag": "Tag/Sektor eingeben, z.B. KI, Halbleiter oder Energie:",
        "description": "Kurzbeschreibung eingeben:",
    }
    return prompts.get(field_name, f"Wert fuer {field_name} eingeben:")


def build_trade_republic_value_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["Ja", "Nein", "Unbekannt"], ["Zurueck", "Abbrechen"]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def trade_republic_field_prompt(field_name: str) -> str:
    prompts = {
        "trade_republic_aktie": "Ist dieser Wert bei Trade Republic als Aktie handelbar?",
        "trade_republic_derivate": "Sind bei Trade Republic Derivate auf diesen Wert handelbar?",
    }
    return prompts.get(field_name, "Trade-Republic-Status waehlen:")


def build_time_choice_keyboard(prefix: str, mode: str = "from") -> InlineKeyboardMarkup:
    if mode == "to":
        time_values = [f"h:{hour:02d}" for hour in range(1, 24)]
    else:
        time_values = [f"h:{hour:02d}" for hour in range(0, 24)]

    rows: list[list[InlineKeyboardButton]] = []
    current_row: list[InlineKeyboardButton] = []
    for value in time_values:
        hour = value.removeprefix("h:")
        current_row.append(InlineKeyboardButton(hour, callback_data=f"{prefix}{value}"))
        if len(current_row) == 3:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)

    rows.append([InlineKeyboardButton("Zurueck", callback_data=f"{prefix}back")])
    return InlineKeyboardMarkup(rows)


def build_time_minute_choice_keyboard(prefix: str, hour: str, back_value: str) -> InlineKeyboardMarkup:
    minute_values = ["00", "15", "30", "45"]
    rows: list[list[InlineKeyboardButton]] = []
    current_row: list[InlineKeyboardButton] = []
    for minute in minute_values:
        label = f"{hour}:{minute}"
        current_row.append(InlineKeyboardButton(label, callback_data=f"{prefix}m:{minute}"))
        if len(current_row) == 3:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)

    rows.append([InlineKeyboardButton("Zurueck", callback_data=f"{prefix}{back_value}")])
    return InlineKeyboardMarkup(rows)


def get_auto_subcategory_options(runtime: BotRuntime, context: ContextTypes.DEFAULT_TYPE) -> list[str]:
    subcategories_by_category: dict[str, list[str]] = context.user_data.get("auto_subcategories", {})
    if runtime.auto_brief.category:
        return list(subcategories_by_category.get(runtime.auto_brief.category, []))

    options: list[str] = []
    for values in subcategories_by_category.values():
        for value in values:
            if value not in options:
                options.append(value)
    return options


def cleanup_autobrief_context(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in [
        "auto_categories",
        "auto_subcategories",
        "auto_window_start_time",
        "auto_window_from_hour",
        "auto_window_to_hour",
    ]:
        context.user_data.pop(key, None)


def cleanup_listenpflege_context(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in [
        "list_action",
        "list_categories",
        "list_subcategories",
        "list_selected_category",
        "list_selected_subcategory",
        "list_entry_options",
        "list_add_payload",
        "list_add_category_mode",
        "list_add_subcategory_mode",
        "list_edit_entry",
        "list_original_entry",
        "list_edit_field",
    ]:
        context.user_data.pop(key, None)


def listenpflege_text_nav_choice(message) -> str:
    text = (message.text or "").strip().casefold()
    if text == "zurueck":
        return "back"
    if text == "abbrechen":
        return "cancel"
    return ""


def build_batch_entry_keyboard(
    options: list[dict[str, str]],
    selected_queries: set[str],
) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("Alle", callback_data=f"{CALLBACK_PREFIX_ENTRY}ALL")]]
    for index, item in enumerate(options):
        label = item["name"]
        if item["query"] in selected_queries:
            label = f"[x] {label}"
        rows.append([InlineKeyboardButton(label, callback_data=f"{CALLBACK_PREFIX_ENTRY}{index}")])
    rows.append([InlineKeyboardButton("Weiter", callback_data=f"{CALLBACK_PREFIX_ENTRY}DONE")])
    return InlineKeyboardMarkup(rows)


def build_batch_selection_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Weitere Auswahl", callback_data=f"{CALLBACK_PREFIX_BATCH_SELECT}more")],
            [InlineKeyboardButton("Weiter zu News", callback_data=f"{CALLBACK_PREFIX_BATCH_SELECT}news")],
        ]
    )


def build_batch_result_mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Ja", callback_data=f"{CALLBACK_PREFIX_BATCH_RESULT}full")],
            [InlineKeyboardButton("Nein", callback_data=f"{CALLBACK_PREFIX_BATCH_RESULT}short")],
        ]
    )


def prepare_batch_market_brief(
    category: str,
    subcategory: str,
    entry_queries: list[str] | None = None,
) -> tuple[Path, list[dict[str, str]]]:
    output_path = build_default_output_path()
    items = filter_queries(load_queries("config/stock_categories/stock_categories.xml"), category, subcategory)
    if entry_queries:
        needles = {query.strip().casefold() for query in entry_queries if query.strip()}
        items = [
            item for item in items
            if item["query"].casefold() in needles or item["name"].casefold() in needles
        ]
    if not items:
        raise RuntimeError("Keine Eintraege fuer die gewaehlte Kategorie/Subkategorie gefunden.")

    return output_path, items


async def run_batch_market_brief_parallel(
    items: list[dict[str, str]],
    with_news_summary: bool,
    progress_callback: Callable[[int, int, dict[str, str], int], Awaitable[None]] | None = None,
    max_concurrent: int = 5,
) -> tuple[list[str], list[dict[str, str | int]]]:
    semaphore = asyncio.Semaphore(max_concurrent)
    formatted_results: list[str] = [""] * len(items)
    result_meta: list[dict[str, str | int] | None] = [None] * len(items)
    completed = 0
    completed_lock = asyncio.Lock()

    async def worker(index: int, item: dict[str, str]) -> None:
        nonlocal completed
        async with semaphore:
            exit_code, stdout, stderr = await run_blocking(
                run_market_brief,
                os.sys.executable,
                item["query"],
                with_news_summary,
            )

        formatted_results[index] = format_result(item, exit_code, stdout, stderr)
        result_meta[index] = {
            "category": item["category"],
            "subcategory": item["subcategory"],
            "name": item["name"],
            "exit_code": exit_code,
            "stderr": stderr,
        }

        async with completed_lock:
            completed += 1
            current_completed = completed

        if progress_callback is not None:
            await progress_callback(current_completed, len(items), item, len(items) - current_completed)

    await asyncio.gather(*(worker(index, item) for index, item in enumerate(items)))
    return formatted_results, [item for item in result_meta if item is not None]


def finalize_batch_market_brief(
    output_path: Path,
    items: list[dict[str, str]],
    result_meta: list[dict[str, str | int]],
    formatted_results: list[str],
    category: str,
    subcategory: str,
    with_news_summary: bool,
) -> str:
    progress_lines = [
        f"Kategorie-Filter: {category or 'alle'}",
        f"Subkategorie-Filter: {subcategory or 'alle'}",
        f"Anzahl Eintraege: {len(items)}",
        f"News-Zusammenfassungen aktiv: {'ja' if with_news_summary else 'nein'}",
    ]

    for index, item in enumerate(items, start=1):
        result = result_meta[index - 1]
        progress_lines.append(
            f"[{index}/{len(items)}] {item['category']} / {item['subcategory']} / {item['name']} -> Exit {result['exit_code']}"
        )
        if result.get("stderr"):
            progress_lines.append(f"STDERR: {summarize_stderr(str(result['stderr']))}")

    summary = build_summary(result_meta)
    try:
        global_hot_topics_section = build_global_hot_topics_section(
            include_news_summaries=with_news_summary,
        )
    except Exception as exc:
        global_hot_topics_section = (
            "GLOBAL HOT TOPICS & MARKT-SENTIMENT\n"
            f"  Abruf fehlgeschlagen: {exc}"
        )
    try:
        global_lead_section = build_global_lead_section(items)
    except Exception as exc:
        global_lead_section = (
            "🌍 GLOBALER VORLAUF (Pre-Market Check)\n"
            f"  Abruf fehlgeschlagen: {exc}"
        )

    output_path.write_text(
        global_hot_topics_section + "\n\n" + global_lead_section + "\n\n" + summary + "\n" + "\n".join(formatted_results),
        encoding="utf-8",
    )
    progress_lines.append(f"Datei erstellt: {output_path}")
    return "\n".join(progress_lines)


async def send_batch_market_brief(
    application: Application,
    chat_id: int,
    category: str,
    subcategory: str,
    with_news_summary: bool,
    send_detailed_result_message: bool = True,
    announce_start: bool = True,
) -> str:
    output_path, items = await run_blocking(prepare_batch_market_brief, category, subcategory)
    status_message = None
    if announce_start:
        status_message = await application.bot.send_message(
            chat_id=chat_id,
            text=(
                "Auto Market Brief gestartet.\n"
                f"Kategorie: {category or 'alle'}\n"
                f"Subkategorie: {subcategory or 'alle'}\n"
                f"News-Zusammenfassungen: {'ja' if with_news_summary else 'nein'}\n"
                f"Eintraege: {len(items)}"
            ),
        )

    async def progress_callback(completed: int, total: int, item: dict[str, str], remaining: int) -> None:
        if status_message is None:
            return
        await status_message.edit_text(
            "Auto Market Brief laeuft.\n"
            f"Fortschritt: {completed}/{total}\n"
            f"Zuletzt fertig: {item['category']} / {item['subcategory']} / {item['name']}\n"
            f"Query: {item['query']}\n"
            f"Verbleibend: {remaining}\n"
            "Parallelitaet: 7"
        )

    formatted_results, result_meta = await run_batch_market_brief_parallel(
        items,
        with_news_summary,
        progress_callback=progress_callback,
        max_concurrent=7,
    )

    progress = await run_blocking(
        finalize_batch_market_brief,
        output_path,
        items,
        result_meta,
        formatted_results,
        category,
        subcategory,
        with_news_summary,
    )

    if status_message is not None:
        await status_message.edit_text(
            "Auto Market Brief abgeschlossen.\n"
            f"Eintraege: {len(items)}\n"
            f"Datei: {output_path.name}"
        )

    await application.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
    if send_detailed_result_message:
        for chunk in split_message(progress):
            await application.bot.send_message(chat_id=chat_id, text=chunk)
    else:
        failed_count = sum(1 for item in result_meta if int(item["exit_code"]) != 0)
        short_text = (
            f"Auto Market Brief Ergebnis: erfolgreich.\nEintraege: {len(items)}\nDatei: {output_path.name}"
            if failed_count == 0
            else (
                "Auto Market Brief Ergebnis: mit Fehlern.\n"
                f"Eintraege: {len(items)}\n"
                f"Fehlerhafte Eintraege: {failed_count}\n"
                f"Datei: {output_path.name}"
            )
        )
        await application.bot.send_message(chat_id=chat_id, text=short_text)
    await send_output_document(application.bot, chat_id, output_path)
    return output_path.name


def configure_auto_brief_job(application: Application, runtime: BotRuntime) -> None:
    job_queue = application.job_queue
    if job_queue is None:
        LOGGER.warning("JobQueue nicht verfuegbar; Auto Market Brief ist deaktiviert.")
        return

    for job in job_queue.get_jobs_by_name(AUTO_BRIEF_JOB_NAME):
        job.schedule_removal()

    if runtime.auto_brief.enabled:
        job_queue.run_repeating(auto_market_brief_job, interval=60, first=5, name=AUTO_BRIEF_JOB_NAME)
    job_queue.run_repeating(main_bot_heartbeat_job, interval=HEARTBEAT_INTERVAL_SECONDS, first=0)


async def main_bot_heartbeat_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    write_heartbeat(
        "running",
        {
            "auto_brief_enabled": runtime.auto_brief.enabled,
            "auto_brief_last_run_at": runtime.auto_brief.last_run_at,
        },
    )


async def auto_market_brief_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    settings = runtime.auto_brief
    if not settings.enabled or not settings.chat_id:
        return

    try:
        start = parse_hhmm(settings.start_time)
        end = parse_hhmm(settings.end_time)
    except ValueError:
        LOGGER.error("Auto Market Brief ungueltige Zeitangaben: %s - %s", settings.start_time, settings.end_time)
        return

    now = datetime.now()
    if not is_within_time_window(now.time(), start, end):
        return

    if settings.last_run_at:
        try:
            last_run = datetime.fromisoformat(settings.last_run_at)
        except ValueError:
            last_run = None
        if last_run is not None:
            elapsed_seconds = (now - last_run).total_seconds()
            if elapsed_seconds < settings.interval_minutes * 60:
                return

    try:
        await send_batch_market_brief(
            application=context.application,
            chat_id=settings.chat_id,
            category=settings.category,
            subcategory=settings.subcategory,
            with_news_summary=settings.with_news_summary,
            send_detailed_result_message=settings.send_detailed_result_message,
            announce_start=True,
        )
        settings.last_run_at = now.isoformat(timespec="seconds")
        persist_auto_brief_settings(runtime)
    except Exception:
        LOGGER.exception("Auto Market Brief fehlgeschlagen")
        await context.application.bot.send_message(
            chat_id=settings.chat_id,
            text="Auto Market Brief fehlgeschlagen. Details stehen im Bot-Log.",
        )


async def marketbrief_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE, runtime: BotRuntime) -> int:
    categories = await run_blocking(load_categories)
    context.user_data["batch_categories"] = categories
    context.user_data["batch_selected_queries"] = set()
    message = update.effective_message
    if message is not None:
        await message.reply_text(
            "Batch Market Brief starten.\nKategorie waehlen:",
            reply_markup=build_choice_keyboard(categories, CALLBACK_PREFIX_CATEGORY),
        )
    return STATE_BATCH_CATEGORY


async def marketbrief_start_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    categories: list[str] = context.user_data.get("batch_categories", [])
    query = update.callback_query
    if query is None:
        return STATE_BATCH_CATEGORY
    await query.answer()
    raw = query.data.removeprefix(CALLBACK_PREFIX_CATEGORY)
    selection = "" if raw == "ALL" else raw
    if selection and selection not in categories:
        await query.edit_message_text("Ungueltige Auswahl. Bitte /marketbrief_start erneut ausfuehren.")
        return ConversationHandler.END

    if raw == "ALL":
        context.user_data["batch_category"] = ""
        context.user_data["batch_subcategory"] = ""
        context.user_data["batch_selected_queries"] = set()
        await query.edit_message_text(
            "Alle Eintraege ausgewaehlt.\nNews-Zusammenfassungen aktivieren?",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("Ja", callback_data=f"{CALLBACK_PREFIX_NEWS}yes")],
                    [InlineKeyboardButton("Nein", callback_data=f"{CALLBACK_PREFIX_NEWS}no")],
                ]
            ),
        )
        return STATE_BATCH_NEWS

    context.user_data["batch_category"] = selection
    subcategories_by_category = await run_blocking(load_subcategories)
    context.user_data["batch_subcategories"] = subcategories_by_category

    if selection:
        options = subcategories_by_category.get(selection, [])
    else:
        options = []
        for values in subcategories_by_category.values():
            for value in values:
                if value not in options:
                    options.append(value)

    context.user_data["batch_subcategory_options"] = options
    await query.edit_message_text(
        "Subkategorie waehlen:",
        reply_markup=build_choice_keyboard(options, CALLBACK_PREFIX_SUBCATEGORY),
    )
    return STATE_BATCH_SUBCATEGORY


async def marketbrief_start_subcategory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None:
        return STATE_BATCH_SUBCATEGORY
    await query.answer()
    options: list[str] = context.user_data.get("batch_subcategory_options", [])
    raw = query.data.removeprefix(CALLBACK_PREFIX_SUBCATEGORY)
    selection = "" if raw == "ALL" else raw
    if selection and selection not in options:
        await query.edit_message_text("Ungueltige Auswahl. Bitte /marketbrief_start erneut ausfuehren.")
        return ConversationHandler.END

    context.user_data["batch_subcategory"] = selection
    all_items = filter_queries(load_queries("config/stock_categories/stock_categories.xml"), context.user_data.get("batch_category", ""), selection)
    context.user_data["batch_entry_options"] = all_items
    context.user_data["batch_entry_queries"] = set(context.user_data.get("batch_selected_queries", set()))
    await query.edit_message_text(
        "Eintrag waehlen. Du kannst mehrere Eintraege markieren und dann auf Weiter gehen:",
        reply_markup=build_batch_entry_keyboard(all_items, context.user_data["batch_entry_queries"]),
    )
    return STATE_BATCH_ENTRY


async def marketbrief_start_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None:
        return STATE_BATCH_ENTRY
    await query.answer()
    raw = query.data.removeprefix(CALLBACK_PREFIX_ENTRY)
    options: list[dict[str, str]] = context.user_data.get("batch_entry_options", [])
    selected_queries: set[str] = set(context.user_data.get("batch_selected_queries", set()))
    if raw == "ALL":
        selected_queries.update(item["query"] for item in options)
        context.user_data["batch_selected_queries"] = selected_queries
        await query.edit_message_text(
            f"Aktuell ausgewaehlte Eintraege: {len(selected_queries)}",
            reply_markup=build_batch_selection_menu_keyboard(),
        )
        return STATE_BATCH_SELECTION_MENU

    if raw == "DONE":
        context.user_data["batch_selected_queries"] = selected_queries
        await query.edit_message_text(
            f"Aktuell ausgewaehlte Eintraege: {len(selected_queries)}",
            reply_markup=build_batch_selection_menu_keyboard(),
        )
        return STATE_BATCH_SELECTION_MENU

    try:
        selected = options[int(raw)]
    except (ValueError, IndexError):
        await query.edit_message_text("Ungueltige Auswahl. Bitte /marketbrief_start erneut ausfuehren.")
        return ConversationHandler.END

    selected_query = selected["query"]
    if selected_query in selected_queries:
        selected_queries.remove(selected_query)
    else:
        selected_queries.add(selected_query)
    context.user_data["batch_selected_queries"] = selected_queries
    await query.edit_message_text(
        "Eintrag waehlen. Du kannst mehrere Eintraege markieren und dann auf Weiter gehen:",
        reply_markup=build_batch_entry_keyboard(options, selected_queries),
    )
    return STATE_BATCH_ENTRY


async def marketbrief_start_selection_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None:
        return STATE_BATCH_SELECTION_MENU
    await query.answer()
    action = query.data.removeprefix(CALLBACK_PREFIX_BATCH_SELECT)
    selected_queries: set[str] = context.user_data.get("batch_selected_queries", set())

    if action == "more":
        categories: list[str] = context.user_data.get("batch_categories", [])
        await query.edit_message_text(
            f"Weitere Auswahl. Bisher markiert: {len(selected_queries)}\nKategorie waehlen:",
            reply_markup=build_choice_keyboard(categories, CALLBACK_PREFIX_CATEGORY),
        )
        return STATE_BATCH_CATEGORY

    if action == "news":
        if not selected_queries:
            await query.edit_message_text(
                "Es ist noch kein Eintrag ausgewaehlt. Bitte waehle Eintraege oder nutze 'Alle'.",
                reply_markup=build_batch_selection_menu_keyboard(),
            )
            return STATE_BATCH_SELECTION_MENU
        await query.edit_message_text(
            "News-Zusammenfassungen aktivieren?",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("Ja", callback_data=f"{CALLBACK_PREFIX_NEWS}yes")],
                    [InlineKeyboardButton("Nein", callback_data=f"{CALLBACK_PREFIX_NEWS}no")],
                ]
            ),
        )
        return STATE_BATCH_NEWS

    await query.edit_message_text("Ungueltige Auswahl. Bitte /marketbrief_start erneut ausfuehren.")
    return ConversationHandler.END


async def marketbrief_start_news(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None:
        return STATE_BATCH_NEWS
    await query.answer()
    answer = query.data.removeprefix(CALLBACK_PREFIX_NEWS).strip().casefold()
    if answer == "yes":
        with_news_summary = True
    elif answer == "no":
        with_news_summary = False
    else:
        await query.edit_message_text("Ungueltige Auswahl. Bitte /marketbrief_start erneut ausfuehren.")
        return ConversationHandler.END

    context.user_data["batch_send_full_result"] = True
    context.user_data["batch_with_news_summary"] = with_news_summary
    await query.edit_message_text(
        "Ausfuehrliche Ergebnisnachricht senden?",
        reply_markup=build_batch_result_mode_keyboard(),
    )
    return STATE_BATCH_RESULT_MODE


async def marketbrief_start_result_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None:
        return STATE_BATCH_RESULT_MODE
    await query.answer()
    mode = query.data.removeprefix(CALLBACK_PREFIX_BATCH_RESULT).strip().casefold()
    if mode == "full":
        send_full_result = True
    elif mode == "short":
        send_full_result = False
    else:
        await query.edit_message_text("Ungueltige Auswahl. Bitte /marketbrief_start erneut ausfuehren.")
        return ConversationHandler.END

    context.user_data["batch_send_full_result"] = send_full_result
    with_news_summary = bool(context.user_data.get("batch_with_news_summary", True))
    selected_queries = sorted(context.user_data.get("batch_selected_queries", set()))
    started_at = datetime.now()
    output_path, items = await run_blocking(prepare_batch_market_brief, "", "", selected_queries)
    await query.edit_message_text("Batch-Lauf gestartet. Fortschritt wird laufend aktualisiert.")

    status_message = query.message

    async def progress_callback(completed: int, total: int, item: dict[str, str], remaining: int) -> None:
        if status_message is None:
            return
        await status_message.edit_text(
            "Batch Market Brief laeuft.\n"
            f"Fortschritt: {completed}/{total}\n"
            f"Zuletzt fertig: {item['category']} / {item['subcategory']} / {item['name']}\n"
            f"Query: {item['query']}\n"
            f"Verbleibend: {remaining}\n"
            "Parallelitaet: 7"
        )

    formatted_results, result_meta = await run_batch_market_brief_parallel(
        items,
        with_news_summary,
        progress_callback=progress_callback,
        max_concurrent=7,
    )

    progress = await run_blocking(
        finalize_batch_market_brief,
        output_path,
        items,
        result_meta,
        formatted_results,
        "",
        "",
        with_news_summary,
    )

    if status_message is not None:
        elapsed_seconds = (datetime.now() - started_at).total_seconds()
        await status_message.edit_text(
            "Batch-Lauf abgeschlossen.\n"
            f"Eintraege: {len(items)}\n"
            f"Datei: {output_path.name}\n"
            f"Dauer: {elapsed_seconds:.1f} Sekunden"
        )

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_DOCUMENT)
    if send_full_result:
        await reply_long(update, progress + f"\nDauer: {elapsed_seconds:.1f} Sekunden")
    else:
        all_ok = all(int(item["exit_code"]) == 0 for item in result_meta)
        short_text = (
            f"Batch erfolgreich: ja | Eintraege: {len(items)} | Dauer: {elapsed_seconds:.1f}s"
            if all_ok
            else (
                "Batch erfolgreich: nein | "
                f"Fehlerhafte Eintraege: {sum(1 for item in result_meta if int(item['exit_code']) != 0)} | "
                f"Dauer: {elapsed_seconds:.1f}s"
            )
        )
        await reply_long(update, short_text)
    message = update.effective_message or status_message
    if message is not None:
        await send_output_document(context.bot, update.effective_chat.id, output_path, reply_message=message)

    context.user_data.pop("batch_category", None)
    context.user_data.pop("batch_subcategory", None)
    context.user_data.pop("batch_entry_queries", None)
    context.user_data.pop("batch_selected_queries", None)
    context.user_data.pop("batch_send_full_result", None)
    context.user_data.pop("batch_with_news_summary", None)
    context.user_data.pop("batch_categories", None)
    context.user_data.pop("batch_subcategories", None)
    context.user_data.pop("batch_subcategory_options", None)
    context.user_data.pop("batch_entry_options", None)
    return ConversationHandler.END


async def marketbrief_start_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if message is not None:
        await message.reply_text("Batch Market Brief abgebrochen.", reply_markup=ReplyKeyboardRemove())
    context.user_data.pop("batch_category", None)
    context.user_data.pop("batch_subcategory", None)
    context.user_data.pop("batch_entry_queries", None)
    context.user_data.pop("batch_selected_queries", None)
    context.user_data.pop("batch_send_full_result", None)
    context.user_data.pop("batch_with_news_summary", None)
    context.user_data.pop("batch_categories", None)
    context.user_data.pop("batch_subcategories", None)
    context.user_data.pop("batch_subcategory_options", None)
    context.user_data.pop("batch_entry_options", None)
    return ConversationHandler.END


async def autobrief_start_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if message is not None:
        await message.reply_text("Auto Market Brief Konfiguration abgebrochen.", reply_markup=ReplyKeyboardRemove())
    cleanup_autobrief_context(context)
    return ConversationHandler.END


async def supportbot_command(update: Update, context: ContextTypes.DEFAULT_TYPE, runtime: BotRuntime) -> int:
    message = update.effective_message
    if message is not None:
        await message.reply_text(
            build_support_bot_menu_text(),
            reply_markup=build_support_bot_menu_keyboard(),
        )
    return STATE_SUPPORT_MENU


async def supportbot_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None:
        return STATE_SUPPORT_MENU
    await query.answer()
    action = query.data.removeprefix(CALLBACK_PREFIX_SUPPORT).strip().casefold()

    if action == "start":
        result = start_support_bot_process(python_executable=os.sys.executable)
        append_event("main_bot", "INFO", result["message"])
        await query.edit_message_text(
            build_support_bot_menu_text(result["message"]),
            reply_markup=build_support_bot_menu_keyboard(),
        )
        return STATE_SUPPORT_MENU

    if action == "stop":
        result = stop_support_bot_process()
        append_event("main_bot", "INFO", result["message"])
        await query.edit_message_text(
            build_support_bot_menu_text(result["message"]),
            reply_markup=build_support_bot_menu_keyboard(),
        )
        return STATE_SUPPORT_MENU

    if action == "restart":
        result = restart_support_bot_process(python_executable=os.sys.executable)
        append_event("main_bot", "INFO", result["message"])
        await query.edit_message_text(
            build_support_bot_menu_text(result["message"]),
            reply_markup=build_support_bot_menu_keyboard(),
        )
        return STATE_SUPPORT_MENU

    if action == "done":
        await query.edit_message_text(build_support_bot_menu_text("Support-Menue beendet."))
        return ConversationHandler.END

    await query.edit_message_text("Ungueltige Auswahl. Bitte /supportbot erneut ausfuehren.")
    return ConversationHandler.END


async def certificate_scraper_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("certificate_scraper", None)
    message = update.effective_message
    if message is not None:
        await message.reply_text("Certificate Scraper abgebrochen.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def listenpflege_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE, runtime: BotRuntime) -> int:
    cleanup_listenpflege_context(context)
    context.user_data["list_categories"] = await run_blocking(load_categories)
    context.user_data["list_subcategories"] = await run_blocking(load_subcategories)
    message = update.effective_message
    if message is not None:
        await show_listenpflege_action_menu(message)
    return STATE_LIST_ACTION


async def show_listenpflege_action_menu(message) -> None:
    await message.reply_text(
        "Listenpflege fuer config/stock_categories/stock_categories.xml\nAktion waehlen:",
        reply_markup=build_option_keyboard(
            [
                ("Eintrag hinzufuegen", f"{CALLBACK_PREFIX_LIST_ACTION}add"),
                ("Eintrag bearbeiten", f"{CALLBACK_PREFIX_LIST_ACTION}edit"),
                ("Eintrag loeschen", f"{CALLBACK_PREFIX_LIST_ACTION}delete"),
                ("Abbrechen", f"{CALLBACK_PREFIX_LIST_ACTION}cancel"),
            ]
        ),
    )


async def show_list_add_optional_menu(message, context: ContextTypes.DEFAULT_TYPE, *, reply_markup=ReplyKeyboardRemove()) -> None:
    payload = context.user_data.get("list_add_payload", {})
    await message.reply_text(
        "Pflichtfelder sind gesetzt. Optionale Zusatzfelder auswaehlen.\n"
        "Empfohlen fuer saubere Market-Brief-Laeufe: Ticker USA, Ticker EU, Ticker APAC.\n\n"
        + format_stock_entry(payload),
        reply_markup=reply_markup,
    )
    await message.reply_text(
        "Zusatzfeld waehlen oder direkt speichern:",
        reply_markup=build_list_optional_menu_keyboard(payload),
    )


async def listenpflege_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None:
        return STATE_LIST_ACTION
    await query.answer()
    action = query.data.removeprefix(CALLBACK_PREFIX_LIST_ACTION).strip()
    context.user_data["list_action"] = action

    if action == "add":
        categories = context.user_data.get("list_categories", [])
        context.user_data["list_add_category_mode"] = "select"
        context.user_data["list_add_subcategory_mode"] = "select"
        await query.edit_message_text(
            "Kategorie waehlen oder neu anlegen:",
            reply_markup=build_list_add_category_keyboard(categories),
        )
        context.user_data["list_add_payload"] = {}
        return STATE_LIST_ADD_CATEGORY

    if action in {"edit", "delete"}:
        categories: list[str] = context.user_data.get("list_categories", [])
        if not categories:
            await query.edit_message_text("Keine Kategorien in config/stock_categories/stock_categories.xml gefunden.")
            cleanup_listenpflege_context(context)
            return ConversationHandler.END
        await query.edit_message_text(
            "Kategorie waehlen:",
            reply_markup=build_option_keyboard(
                [(category, f"{CALLBACK_PREFIX_LIST_CATEGORY}{category}") for category in categories]
                + [("Zurueck", f"{CALLBACK_PREFIX_LIST_CATEGORY}back"), ("Abbrechen", f"{CALLBACK_PREFIX_LIST_CATEGORY}cancel")]
            ),
        )
        return STATE_LIST_EDIT_CATEGORY if action == "edit" else STATE_LIST_DELETE_CATEGORY

    await query.edit_message_text("Listenpflege abgebrochen.")
    cleanup_listenpflege_context(context)
    return ConversationHandler.END


async def listenpflege_add_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = getattr(update, "callback_query", None)
    if query is not None:
        await query.answer()
        value = query.data.removeprefix(CALLBACK_PREFIX_LIST_ADD_CATEGORY)
        if value == "cancel":
            await query.edit_message_text("Listenpflege abgebrochen.")
            cleanup_listenpflege_context(context)
            return ConversationHandler.END
        if value == "back":
            await query.edit_message_text(
                "Listenpflege fuer config/stock_categories/stock_categories.xml\nAktion waehlen:",
                reply_markup=build_option_keyboard(
                    [
                        ("Eintrag hinzufuegen", f"{CALLBACK_PREFIX_LIST_ACTION}add"),
                        ("Eintrag bearbeiten", f"{CALLBACK_PREFIX_LIST_ACTION}edit"),
                        ("Eintrag loeschen", f"{CALLBACK_PREFIX_LIST_ACTION}delete"),
                        ("Abbrechen", f"{CALLBACK_PREFIX_LIST_ACTION}cancel"),
                    ]
                ),
            )
            return STATE_LIST_ACTION
        if value == "new":
            context.user_data["list_add_category_mode"] = "text"
            await query.edit_message_text("Neue Kategorie eingeben:", reply_markup=None)
            if query.message is not None:
                await query.message.reply_text("Neue Kategorie eingeben:", reply_markup=build_text_navigation_keyboard())
            return STATE_LIST_ADD_CATEGORY

        context.user_data["list_add_category_mode"] = "select"
        context.user_data.setdefault("list_add_payload", {})["category"] = value
        subcategories = context.user_data.get("list_subcategories", {}).get(value, [])
        context.user_data["list_add_subcategory_mode"] = "select"
        await query.edit_message_text(
            "Subkategorie waehlen oder neu anlegen:",
            reply_markup=build_list_add_subcategory_keyboard(subcategories),
        )
        return STATE_LIST_ADD_SUBCATEGORY

    message = update.effective_message
    if message is None:
        return STATE_LIST_ADD_CATEGORY
    nav = listenpflege_text_nav_choice(message)
    if nav == "cancel":
        await message.reply_text("Listenpflege abgebrochen.", reply_markup=ReplyKeyboardRemove())
        cleanup_listenpflege_context(context)
        return ConversationHandler.END
    if nav == "back":
        await message.reply_text("Zurueck zum Aktionsmenue.", reply_markup=ReplyKeyboardRemove())
        await show_listenpflege_action_menu(message)
        return STATE_LIST_ACTION
    if context.user_data.get("list_add_category_mode") != "text":
        await message.reply_text("Bitte eine Kategorie per Button waehlen oder 'Neu hinzufuegen' nutzen.")
        return STATE_LIST_ADD_CATEGORY
    value = message.text.strip()
    if not value:
        await message.reply_text("Kategorie darf nicht leer sein. Bitte erneut eingeben.", reply_markup=build_text_navigation_keyboard())
        return STATE_LIST_ADD_CATEGORY
    context.user_data.setdefault("list_add_payload", {})["category"] = value
    subcategories = context.user_data.get("list_subcategories", {}).get(value, [])
    context.user_data["list_add_subcategory_mode"] = "select"
    await message.reply_text(
        "Subkategorie waehlen oder neu anlegen:",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.reply_text(
        "Subkategorieauswahl:",
        reply_markup=build_list_add_subcategory_keyboard(subcategories),
    )
    return STATE_LIST_ADD_SUBCATEGORY


async def listenpflege_add_subcategory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = getattr(update, "callback_query", None)
    if query is not None:
        await query.answer()
        value = query.data.removeprefix(CALLBACK_PREFIX_LIST_ADD_SUBCATEGORY)
        if value == "cancel":
            await query.edit_message_text("Listenpflege abgebrochen.")
            cleanup_listenpflege_context(context)
            return ConversationHandler.END
        if value == "back":
            categories = context.user_data.get("list_categories", [])
            context.user_data.setdefault("list_add_payload", {}).pop("category", None)
            context.user_data["list_add_category_mode"] = "select"
            await query.edit_message_text(
                "Kategorie waehlen oder neu anlegen:",
                reply_markup=build_list_add_category_keyboard(categories),
            )
            return STATE_LIST_ADD_CATEGORY
        if value == "new":
            context.user_data["list_add_subcategory_mode"] = "text"
            await query.edit_message_text("Neue Subkategorie eingeben:", reply_markup=None)
            if query.message is not None:
                await query.message.reply_text("Neue Subkategorie eingeben:", reply_markup=build_text_navigation_keyboard())
            return STATE_LIST_ADD_SUBCATEGORY

        context.user_data["list_add_subcategory_mode"] = "done"
        context.user_data.setdefault("list_add_payload", {})["subcategory"] = value
        if query.message is not None:
            await query.message.reply_text("Name der Aktie eingeben:", reply_markup=build_text_navigation_keyboard())
            await query.message.reply_text(reply_markup=build_list_add_name_keyboard(), text=" ")
        else:
            await query.edit_message_text("Name der Aktie eingeben:")
        return STATE_LIST_ADD_NAME

    message = update.effective_message
    if message is None:
        return STATE_LIST_ADD_SUBCATEGORY
    nav = listenpflege_text_nav_choice(message)
    if nav == "cancel":
        await message.reply_text("Listenpflege abgebrochen.", reply_markup=ReplyKeyboardRemove())
        cleanup_listenpflege_context(context)
        return ConversationHandler.END
    if nav == "back":
        categories = context.user_data.get("list_categories", [])
        context.user_data.setdefault("list_add_payload", {}).pop("category", None)
        context.user_data["list_add_category_mode"] = "select"
        await message.reply_text("Kategorieauswahl:", reply_markup=ReplyKeyboardRemove())
        await message.reply_text(
            "Kategorie waehlen oder neu anlegen:",
            reply_markup=build_list_add_category_keyboard(categories),
        )
        return STATE_LIST_ADD_CATEGORY
    payload = context.user_data.setdefault("list_add_payload", {})
    value = message.text.strip()
    if context.user_data.get("list_add_subcategory_mode") != "text":
        # Fallback: if the subcategory was already chosen, treat free text here as the entry name.
        if payload.get("subcategory"):
            if not value:
                await message.reply_text("Name der Aktie darf nicht leer sein.", reply_markup=build_text_navigation_keyboard())
                await message.reply_text(" ", reply_markup=build_list_add_name_keyboard())
                return STATE_LIST_ADD_NAME
            payload["name"] = value
            await message.reply_text("Ticker eingeben:", reply_markup=build_text_navigation_keyboard())
            return STATE_LIST_ADD_TICKER
        await message.reply_text("Bitte eine Subkategorie per Button waehlen oder 'Neu hinzufuegen' nutzen.")
        return STATE_LIST_ADD_SUBCATEGORY
    if not value:
        await message.reply_text("Subkategorie darf nicht leer sein. Bitte erneut eingeben.", reply_markup=build_text_navigation_keyboard())
        return STATE_LIST_ADD_SUBCATEGORY
    payload["subcategory"] = value
    context.user_data["list_add_subcategory_mode"] = "done"
    await message.reply_text("Name der Aktie eingeben:", reply_markup=build_text_navigation_keyboard())
    await message.reply_text(" ", reply_markup=build_list_add_name_keyboard())
    return STATE_LIST_ADD_NAME


async def listenpflege_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = getattr(update, "callback_query", None)
    if query is not None:
        await query.answer()
        action = query.data.removeprefix(CALLBACK_PREFIX_LIST_ADD_NAME)
        if action == "cancel":
            await query.edit_message_text("Listenpflege abgebrochen.")
            cleanup_listenpflege_context(context)
            return ConversationHandler.END
        if action == "back":
            payload = context.user_data.setdefault("list_add_payload", {})
            payload.pop("subcategory", None)
            subcategories = context.user_data.get("list_subcategories", {}).get(payload.get("category", ""), [])
            await query.edit_message_text(
                "Subkategorie waehlen oder neu anlegen:",
                reply_markup=build_list_add_subcategory_keyboard(subcategories),
            )
            return STATE_LIST_ADD_SUBCATEGORY
        return STATE_LIST_ADD_NAME

    message = update.effective_message
    if message is None:
        return STATE_LIST_ADD_NAME
    nav = listenpflege_text_nav_choice(message)
    if nav == "cancel":
        await message.reply_text("Listenpflege abgebrochen.", reply_markup=ReplyKeyboardRemove())
        cleanup_listenpflege_context(context)
        return ConversationHandler.END
    if nav == "back":
        payload = context.user_data.setdefault("list_add_payload", {})
        payload.pop("subcategory", None)
        subcategories = context.user_data.get("list_subcategories", {}).get(payload.get("category", ""), [])
        await message.reply_text("Zurueck zur Subkategorieauswahl.", reply_markup=ReplyKeyboardRemove())
        await message.reply_text(
            "Subkategorie waehlen oder neu anlegen:",
            reply_markup=build_list_add_subcategory_keyboard(subcategories),
        )
        return STATE_LIST_ADD_SUBCATEGORY
    value = message.text.strip()
    if not value:
        await message.reply_text("Name darf nicht leer sein. Bitte erneut eingeben.", reply_markup=build_text_navigation_keyboard())
        await message.reply_text("Name-Eingabe:", reply_markup=build_list_add_name_keyboard())
        return STATE_LIST_ADD_NAME
    context.user_data.setdefault("list_add_payload", {})["name"] = value
    await message.reply_text("Ticker eingeben:", reply_markup=build_text_navigation_keyboard())
    return STATE_LIST_ADD_TICKER


async def listenpflege_add_ticker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if message is None:
        return STATE_LIST_ADD_TICKER
    nav = listenpflege_text_nav_choice(message)
    if nav == "cancel":
        await message.reply_text("Listenpflege abgebrochen.", reply_markup=ReplyKeyboardRemove())
        cleanup_listenpflege_context(context)
        return ConversationHandler.END
    if nav == "back":
        context.user_data.setdefault("list_add_payload", {}).pop("name", None)
        await message.reply_text("Name der Aktie eingeben:", reply_markup=build_text_navigation_keyboard())
        return STATE_LIST_ADD_NAME
    value = message.text.strip()
    if not value:
        await message.reply_text("Ticker darf nicht leer sein. Bitte erneut eingeben.", reply_markup=build_text_navigation_keyboard())
        return STATE_LIST_ADD_TICKER
    context.user_data.setdefault("list_add_payload", {})["ticker"] = normalize_stock_value(value, "ticker")
    await message.reply_text("ISIN eingeben:", reply_markup=build_text_navigation_keyboard())
    return STATE_LIST_ADD_ISIN


async def listenpflege_add_isin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if message is None:
        return STATE_LIST_ADD_ISIN
    nav = listenpflege_text_nav_choice(message)
    if nav == "cancel":
        await message.reply_text("Listenpflege abgebrochen.", reply_markup=ReplyKeyboardRemove())
        cleanup_listenpflege_context(context)
        return ConversationHandler.END
    if nav == "back":
        context.user_data.setdefault("list_add_payload", {}).pop("ticker", None)
        await message.reply_text("Ticker eingeben:", reply_markup=build_text_navigation_keyboard())
        return STATE_LIST_ADD_TICKER
    value = message.text.strip()
    if not value:
        await message.reply_text("ISIN darf nicht leer sein. Bitte erneut eingeben.", reply_markup=build_text_navigation_keyboard())
        return STATE_LIST_ADD_ISIN
    context.user_data.setdefault("list_add_payload", {})["isin"] = normalize_stock_value(value, "isin")
    await message.reply_text("WKN eingeben:", reply_markup=build_text_navigation_keyboard())
    return STATE_LIST_ADD_WKN


async def listenpflege_add_wkn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if message is None:
        return STATE_LIST_ADD_WKN
    nav = listenpflege_text_nav_choice(message)
    if nav == "cancel":
        await message.reply_text("Listenpflege abgebrochen.", reply_markup=ReplyKeyboardRemove())
        cleanup_listenpflege_context(context)
        return ConversationHandler.END
    if nav == "back":
        context.user_data.setdefault("list_add_payload", {}).pop("isin", None)
        await message.reply_text("ISIN eingeben:", reply_markup=build_text_navigation_keyboard())
        return STATE_LIST_ADD_ISIN
    value = message.text.strip()
    if not value:
        await message.reply_text("WKN darf nicht leer sein. Bitte erneut eingeben.", reply_markup=build_text_navigation_keyboard())
        return STATE_LIST_ADD_WKN
    payload = context.user_data.setdefault("list_add_payload", {})
    payload["wkn"] = normalize_stock_value(value, "wkn")
    try:
        validate_stock_entry_payload(
            payload,
            await run_blocking(collect_stock_entries),
            required_fields=BASE_REQUIRED_STOCK_FIELDS,
        )
    except Exception as exc:
        await message.reply_text(f"Validierung fehlgeschlagen: {exc}\nBitte /listenpflege neu starten.")
        cleanup_listenpflege_context(context)
        return ConversationHandler.END

    await message.reply_text(
        trade_republic_field_prompt("trade_republic_aktie"),
        reply_markup=build_trade_republic_value_keyboard(),
    )
    return STATE_LIST_ADD_TRADE_REPUBLIC_AKTIE


async def listenpflege_add_trade_republic_aktie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if message is None:
        return STATE_LIST_ADD_TRADE_REPUBLIC_AKTIE
    nav = listenpflege_text_nav_choice(message)
    if nav == "cancel":
        await message.reply_text("Listenpflege abgebrochen.", reply_markup=ReplyKeyboardRemove())
        cleanup_listenpflege_context(context)
        return ConversationHandler.END
    if nav == "back":
        context.user_data.setdefault("list_add_payload", {}).pop("wkn", None)
        await message.reply_text("WKN eingeben:", reply_markup=build_text_navigation_keyboard())
        return STATE_LIST_ADD_WKN

    value = normalize_trade_republic_value(message.text or "")
    if value not in TRADE_REPUBLIC_ALLOWED_VALUES:
        await message.reply_text(
            "Bitte Ja, Nein oder Unbekannt waehlen.",
            reply_markup=build_trade_republic_value_keyboard(),
        )
        return STATE_LIST_ADD_TRADE_REPUBLIC_AKTIE

    context.user_data.setdefault("list_add_payload", {})["trade_republic_aktie"] = value
    await message.reply_text(
        trade_republic_field_prompt("trade_republic_derivate"),
        reply_markup=build_trade_republic_value_keyboard(),
    )
    return STATE_LIST_ADD_TRADE_REPUBLIC_DERIVATE


async def listenpflege_add_trade_republic_derivate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if message is None:
        return STATE_LIST_ADD_TRADE_REPUBLIC_DERIVATE
    nav = listenpflege_text_nav_choice(message)
    if nav == "cancel":
        await message.reply_text("Listenpflege abgebrochen.", reply_markup=ReplyKeyboardRemove())
        cleanup_listenpflege_context(context)
        return ConversationHandler.END
    if nav == "back":
        context.user_data.setdefault("list_add_payload", {}).pop("trade_republic_aktie", None)
        await message.reply_text(
            trade_republic_field_prompt("trade_republic_aktie"),
            reply_markup=build_trade_republic_value_keyboard(),
        )
        return STATE_LIST_ADD_TRADE_REPUBLIC_AKTIE

    value = normalize_trade_republic_value(message.text or "")
    if value not in TRADE_REPUBLIC_ALLOWED_VALUES:
        await message.reply_text(
            "Bitte Ja, Nein oder Unbekannt waehlen.",
            reply_markup=build_trade_republic_value_keyboard(),
        )
        return STATE_LIST_ADD_TRADE_REPUBLIC_DERIVATE

    payload = context.user_data.setdefault("list_add_payload", {})
    payload["trade_republic_derivate"] = value
    try:
        validate_stock_entry_payload(payload, await run_blocking(collect_stock_entries))
    except Exception as exc:
        await message.reply_text(f"Validierung fehlgeschlagen: {exc}\nBitte /listenpflege neu starten.")
        cleanup_listenpflege_context(context)
        return ConversationHandler.END

    await show_list_add_optional_menu(message, context, reply_markup=build_text_navigation_keyboard())
    return STATE_LIST_ADD_OPTIONAL_MENU


async def listenpflege_add_optional_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None:
        return STATE_LIST_ADD_OPTIONAL_MENU
    await query.answer()
    action = query.data.removeprefix(CALLBACK_PREFIX_LIST_OPTIONAL)
    if action == "cancel":
        await query.edit_message_text("Listenpflege abgebrochen.")
        cleanup_listenpflege_context(context)
        return ConversationHandler.END
    if action == "back":
        payload = context.user_data.setdefault("list_add_payload", {})
        payload.pop("trade_republic_derivate", None)
        await query.edit_message_text(trade_republic_field_prompt("trade_republic_derivate"))
        if query.message is not None:
            await query.message.reply_text(
                trade_republic_field_prompt("trade_republic_derivate"),
                reply_markup=build_trade_republic_value_keyboard(),
            )
        return STATE_LIST_ADD_TRADE_REPUBLIC_DERIVATE
    if action == "save":
        payload = context.user_data.get("list_add_payload", {})
        try:
            validate_stock_entry_payload(payload, await run_blocking(collect_stock_entries))
        except Exception as exc:
            await query.edit_message_text(f"Validierung fehlgeschlagen: {exc}")
            cleanup_listenpflege_context(context)
            return ConversationHandler.END
        await query.edit_message_text(
            "Neuen Eintrag speichern?\n\n" + format_stock_entry(payload),
            reply_markup=build_option_keyboard(
                [
                    ("Speichern", f"{CALLBACK_PREFIX_LIST_CONFIRM}add_save"),
                    ("Zurueck", f"{CALLBACK_PREFIX_LIST_CONFIRM}add_back"),
                    ("Abbrechen", f"{CALLBACK_PREFIX_LIST_CONFIRM}cancel"),
                ]
            ),
        )
        return STATE_LIST_ADD_CONFIRM

    context.user_data["list_add_optional_field"] = action
    prompt = stock_optional_field_prompt(action)
    await query.edit_message_text(prompt)
    if query.message is not None:
        await query.message.reply_text(prompt, reply_markup=build_text_navigation_keyboard())
    return STATE_LIST_ADD_OPTIONAL_VALUE


async def listenpflege_add_optional_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if message is None:
        return STATE_LIST_ADD_OPTIONAL_VALUE
    nav = listenpflege_text_nav_choice(message)
    if nav == "cancel":
        await message.reply_text("Listenpflege abgebrochen.", reply_markup=ReplyKeyboardRemove())
        cleanup_listenpflege_context(context)
        return ConversationHandler.END
    if nav == "back":
        await show_list_add_optional_menu(message, context, reply_markup=ReplyKeyboardRemove())
        return STATE_LIST_ADD_OPTIONAL_MENU

    field_name = context.user_data.get("list_add_optional_field", "")
    if field_name not in OPTIONAL_STOCK_FIELDS:
        await message.reply_text("Bearbeitungskontext fehlt. Bitte /listenpflege neu starten.", reply_markup=ReplyKeyboardRemove())
        cleanup_listenpflege_context(context)
        return ConversationHandler.END

    value = message.text.strip()
    if not value:
        await message.reply_text("Wert darf nicht leer sein. Bitte erneut eingeben.", reply_markup=build_text_navigation_keyboard())
        return STATE_LIST_ADD_OPTIONAL_VALUE

    payload = context.user_data.setdefault("list_add_payload", {})
    payload[field_name] = normalize_stock_value(value, field_name)
    await show_list_add_optional_menu(message, context, reply_markup=ReplyKeyboardRemove())
    return STATE_LIST_ADD_OPTIONAL_MENU


async def listenpflege_add_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None:
        return STATE_LIST_ADD_CONFIRM
    await query.answer()
    action = query.data.removeprefix(CALLBACK_PREFIX_LIST_CONFIRM)
    if action == "add_back":
        payload = context.user_data.get("list_add_payload", {})
        await query.edit_message_text(
            "Zusatzfelder waehlen oder speichern:",
            reply_markup=build_list_optional_menu_keyboard(payload),
        )
        if query.message is not None:
            await query.message.reply_text(
                "Zurueck zum Zusatzfelder-Menue.",
                reply_markup=ReplyKeyboardRemove(),
            )
        return STATE_LIST_ADD_OPTIONAL_MENU
    if action == "cancel":
        await query.edit_message_text("Listenpflege abgebrochen.")
        cleanup_listenpflege_context(context)
        return ConversationHandler.END

    payload = context.user_data.get("list_add_payload", {})
    backup_path = await run_blocking(add_stock_entry, payload)
    await query.edit_message_text(
        "Eintrag gespeichert.\n\n"
        + format_stock_entry(payload)
        + f"\n\nBackup: {backup_path.name}",
        reply_markup=None,
    )
    if query.message is not None:
        await query.message.reply_text("Listenpflege abgeschlossen.", reply_markup=ReplyKeyboardRemove())
    cleanup_listenpflege_context(context)
    return ConversationHandler.END


async def listenpflege_pick_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None:
        return STATE_LIST_EDIT_CATEGORY
    await query.answer()
    value = query.data.removeprefix(CALLBACK_PREFIX_LIST_CATEGORY)
    if value == "back":
        await query.edit_message_text(
            "Listenpflege fuer config/stock_categories/stock_categories.xml\nAktion waehlen:",
            reply_markup=build_option_keyboard(
                [
                    ("Eintrag hinzufuegen", f"{CALLBACK_PREFIX_LIST_ACTION}add"),
                    ("Eintrag bearbeiten", f"{CALLBACK_PREFIX_LIST_ACTION}edit"),
                    ("Eintrag loeschen", f"{CALLBACK_PREFIX_LIST_ACTION}delete"),
                    ("Abbrechen", f"{CALLBACK_PREFIX_LIST_ACTION}cancel"),
                ]
            ),
        )
        return STATE_LIST_ACTION
    if value == "cancel":
        await query.edit_message_text("Listenpflege abgebrochen.")
        cleanup_listenpflege_context(context)
        return ConversationHandler.END

    context.user_data["list_selected_category"] = value
    subcategories_map: dict[str, list[str]] = context.user_data.get("list_subcategories", {})
    options = subcategories_map.get(value, [])
    if not options:
        await query.edit_message_text("Keine Subkategorien in dieser Kategorie gefunden.")
        cleanup_listenpflege_context(context)
        return ConversationHandler.END
    await query.edit_message_text(
        "Subkategorie waehlen:",
        reply_markup=build_option_keyboard(
            [(item, f"{CALLBACK_PREFIX_LIST_SUBCATEGORY}{item}") for item in options]
            + [("Zurueck", f"{CALLBACK_PREFIX_LIST_SUBCATEGORY}back"), ("Abbrechen", f"{CALLBACK_PREFIX_LIST_SUBCATEGORY}cancel")]
        ),
    )
    action = context.user_data.get("list_action")
    return STATE_LIST_EDIT_SUBCATEGORY if action == "edit" else STATE_LIST_DELETE_SUBCATEGORY


async def listenpflege_pick_subcategory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None:
        return STATE_LIST_EDIT_SUBCATEGORY
    await query.answer()
    value = query.data.removeprefix(CALLBACK_PREFIX_LIST_SUBCATEGORY)
    if value == "back":
        categories: list[str] = context.user_data.get("list_categories", [])
        await query.edit_message_text(
            "Kategorie waehlen:",
            reply_markup=build_option_keyboard(
                [(category, f"{CALLBACK_PREFIX_LIST_CATEGORY}{category}") for category in categories]
                + [("Zurueck", f"{CALLBACK_PREFIX_LIST_CATEGORY}back"), ("Abbrechen", f"{CALLBACK_PREFIX_LIST_CATEGORY}cancel")]
            ),
        )
        action = context.user_data.get("list_action")
        return STATE_LIST_EDIT_CATEGORY if action == "edit" else STATE_LIST_DELETE_CATEGORY
    if value == "cancel":
        await query.edit_message_text("Listenpflege abgebrochen.")
        cleanup_listenpflege_context(context)
        return ConversationHandler.END

    category = context.user_data.get("list_selected_category", "")
    context.user_data["list_selected_subcategory"] = value
    entries = filter_queries(load_queries("config/stock_categories/stock_categories.xml"), category, value)
    context.user_data["list_entry_options"] = entries
    if not entries:
        await query.edit_message_text("Keine Eintraege in dieser Subkategorie gefunden.")
        cleanup_listenpflege_context(context)
        return ConversationHandler.END
    await query.edit_message_text(
        "Eintrag waehlen:",
        reply_markup=build_entry_choice_keyboard(entries, CALLBACK_PREFIX_LIST_ENTRY),
    )
    action = context.user_data.get("list_action")
    return STATE_LIST_EDIT_ENTRY if action == "edit" else STATE_LIST_DELETE_ENTRY


async def listenpflege_pick_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None:
        return STATE_LIST_EDIT_ENTRY
    await query.answer()
    raw = query.data.removeprefix(CALLBACK_PREFIX_LIST_ENTRY)
    if raw == "back":
        category = context.user_data.get("list_selected_category", "")
        subcategories_map: dict[str, list[str]] = context.user_data.get("list_subcategories", {})
        options = subcategories_map.get(category, [])
        await query.edit_message_text(
            "Subkategorie waehlen:",
            reply_markup=build_option_keyboard(
                [(item, f"{CALLBACK_PREFIX_LIST_SUBCATEGORY}{item}") for item in options]
                + [("Zurueck", f"{CALLBACK_PREFIX_LIST_SUBCATEGORY}back"), ("Abbrechen", f"{CALLBACK_PREFIX_LIST_SUBCATEGORY}cancel")]
            ),
        )
        action = context.user_data.get("list_action")
        return STATE_LIST_EDIT_SUBCATEGORY if action == "edit" else STATE_LIST_DELETE_SUBCATEGORY
    if raw == "cancel":
        await query.edit_message_text("Listenpflege abgebrochen.")
        cleanup_listenpflege_context(context)
        return ConversationHandler.END
    entries: list[dict[str, str]] = context.user_data.get("list_entry_options", [])
    try:
        entry = entries[int(raw)]
    except (ValueError, IndexError):
        await query.edit_message_text("Ungueltige Auswahl.")
        cleanup_listenpflege_context(context)
        return ConversationHandler.END

    context.user_data["list_edit_entry"] = dict(entry)
    context.user_data["list_original_entry"] = dict(entry)
    action = context.user_data.get("list_action")
    if action == "delete":
        await query.edit_message_text(
            "Eintrag loeschen?\n\n" + format_stock_entry(entry),
            reply_markup=build_option_keyboard(
                [
                    ("Loeschen", f"{CALLBACK_PREFIX_LIST_CONFIRM}delete_save"),
                    ("Zurueck", f"{CALLBACK_PREFIX_LIST_CONFIRM}delete_back"),
                    ("Abbrechen", f"{CALLBACK_PREFIX_LIST_CONFIRM}cancel"),
                ]
            ),
        )
        return STATE_LIST_DELETE_CONFIRM

    await query.edit_message_text(
        "Welches Feld bearbeiten?\n\n" + format_stock_entry(entry),
        reply_markup=build_stock_field_choice_keyboard(),
    )
    return STATE_LIST_EDIT_FIELD


async def listenpflege_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None:
        return STATE_LIST_EDIT_FIELD
    await query.answer()
    field_name = query.data.removeprefix(CALLBACK_PREFIX_LIST_FIELD)
    if field_name == "back":
        entries: list[dict[str, str]] = context.user_data.get("list_entry_options", [])
        await query.edit_message_text(
            "Eintrag waehlen:",
            reply_markup=build_entry_choice_keyboard(entries, CALLBACK_PREFIX_LIST_ENTRY),
        )
        return STATE_LIST_EDIT_ENTRY
    if field_name == "cancel":
        await query.edit_message_text("Listenpflege abgebrochen.")
        cleanup_listenpflege_context(context)
        return ConversationHandler.END
    context.user_data["list_edit_field"] = field_name
    prompt = (
        trade_republic_field_prompt(field_name)
        if field_name in TRADE_REPUBLIC_FIELD_NAMES
        else f"Neuen Wert fuer {STOCK_FIELD_LABELS.get(field_name, field_name)} eingeben:"
    )
    await query.edit_message_text(prompt)
    if query.message is not None:
        await query.message.reply_text(
            prompt,
            reply_markup=(
                build_trade_republic_value_keyboard()
                if field_name in TRADE_REPUBLIC_FIELD_NAMES
                else build_text_navigation_keyboard()
            ),
        )
    return STATE_LIST_EDIT_VALUE


async def listenpflege_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if message is None:
        return STATE_LIST_EDIT_VALUE
    nav = listenpflege_text_nav_choice(message)
    if nav == "cancel":
        await message.reply_text("Listenpflege abgebrochen.", reply_markup=ReplyKeyboardRemove())
        cleanup_listenpflege_context(context)
        return ConversationHandler.END
    if nav == "back":
        entry = dict(context.user_data.get("list_edit_entry", {}))
        await message.reply_text(
            "Welches Feld bearbeiten?\n\n" + format_stock_entry(entry),
            reply_markup=ReplyKeyboardRemove(),
        )
        await message.reply_text(
            "Feld waehlen:",
            reply_markup=build_stock_field_choice_keyboard(),
        )
        return STATE_LIST_EDIT_FIELD
    entry = dict(context.user_data.get("list_edit_entry", {}))
    field_name = context.user_data.get("list_edit_field", "")
    if not field_name or not entry:
        await message.reply_text("Bearbeitungskontext fehlt. Bitte /listenpflege neu starten.")
        cleanup_listenpflege_context(context)
        return ConversationHandler.END

    new_value = message.text.strip()
    if not new_value and field_name not in OPTIONAL_STOCK_FIELDS:
        await message.reply_text("Wert darf nicht leer sein. Bitte erneut eingeben.", reply_markup=build_text_navigation_keyboard())
        return STATE_LIST_EDIT_VALUE

    updated_preview = dict(entry)
    updated_preview[field_name] = normalize_stock_value(new_value, field_name)
    try:
        validate_stock_entry_payload(updated_preview, await run_blocking(collect_stock_entries), current_query=entry["query"])
    except Exception as exc:
        await message.reply_text(
            f"Validierung fehlgeschlagen: {exc}\nBitte neuen Wert eingeben.",
            reply_markup=(
                build_trade_republic_value_keyboard()
                if field_name in TRADE_REPUBLIC_FIELD_NAMES
                else build_text_navigation_keyboard()
            ),
        )
        return STATE_LIST_EDIT_VALUE

    context.user_data["list_edit_entry"] = updated_preview
    await message.reply_text(
        "Aenderung speichern?\n\n" + format_stock_entry(updated_preview),
        reply_markup=build_option_keyboard(
            [
                ("Speichern", f"{CALLBACK_PREFIX_LIST_CONFIRM}edit_save"),
                ("Zurueck", f"{CALLBACK_PREFIX_LIST_CONFIRM}edit_back"),
                ("Abbrechen", f"{CALLBACK_PREFIX_LIST_CONFIRM}cancel"),
            ]
        ),
    )
    return STATE_LIST_EDIT_CONFIRM


async def listenpflege_edit_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None:
        return STATE_LIST_EDIT_CONFIRM
    await query.answer()
    action = query.data.removeprefix(CALLBACK_PREFIX_LIST_CONFIRM)
    if action == "edit_back":
        entry = dict(context.user_data.get("list_edit_entry", {}))
        await query.edit_message_text(
            "Welches Feld bearbeiten?\n\n" + format_stock_entry(entry),
            reply_markup=build_stock_field_choice_keyboard(),
        )
        if query.message is not None:
            await query.message.reply_text("Navigation beendet.", reply_markup=ReplyKeyboardRemove())
        return STATE_LIST_EDIT_FIELD
    if action == "cancel":
        await query.edit_message_text("Listenpflege abgebrochen.")
        cleanup_listenpflege_context(context)
        return ConversationHandler.END

    updated_entry = dict(context.user_data.get("list_edit_entry", {}))
    original_entry = dict(context.user_data.get("list_original_entry", {}))
    original_query = original_entry.get("query", "")
    original_category = original_entry.get("category", "")
    original_subcategory = original_entry.get("subcategory", "")

    backup_path, merged = await run_blocking(
        update_stock_entry,
        original_category,
        original_subcategory,
        original_query or updated_entry.get("query", ""),
        {key: updated_entry.get(key, "") for key in REQUIRED_STOCK_FIELDS + OPTIONAL_STOCK_FIELDS},
    )
    await query.edit_message_text(
        "Eintrag aktualisiert.\n\n" + format_stock_entry(merged) + f"\n\nBackup: {backup_path.name}"
    )
    if query.message is not None:
        await query.message.reply_text("Listenpflege abgeschlossen.", reply_markup=ReplyKeyboardRemove())
    cleanup_listenpflege_context(context)
    return ConversationHandler.END


async def listenpflege_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None:
        return STATE_LIST_DELETE_CONFIRM
    await query.answer()
    action = query.data.removeprefix(CALLBACK_PREFIX_LIST_CONFIRM)
    if action == "delete_back":
        entries: list[dict[str, str]] = context.user_data.get("list_entry_options", [])
        await query.edit_message_text(
            "Eintrag waehlen:",
            reply_markup=build_entry_choice_keyboard(entries, CALLBACK_PREFIX_LIST_ENTRY),
        )
        return STATE_LIST_DELETE_ENTRY
    if action == "cancel":
        await query.edit_message_text("Listenpflege abgebrochen.")
        cleanup_listenpflege_context(context)
        return ConversationHandler.END

    entry = context.user_data.get("list_edit_entry", {})
    backup_path = await run_blocking(
        delete_stock_entry,
        entry.get("category", ""),
        entry.get("subcategory", ""),
        entry.get("query", ""),
    )
    await query.edit_message_text(
        "Eintrag geloescht.\n\n" + format_stock_entry(entry) + f"\n\nBackup: {backup_path.name}"
    )
    cleanup_listenpflege_context(context)
    return ConversationHandler.END


async def listenpflege_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if message is not None:
        await message.reply_text("Listenpflege abgebrochen.", reply_markup=ReplyKeyboardRemove())
    cleanup_listenpflege_context(context)
    return ConversationHandler.END


async def fallback_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    if not ensure_allowed_chat(update, runtime.allowed_chat_ids, runtime.allowed_user_ids):
        return
    message = update.effective_message
    if message is not None:
        await message.reply_text("Unbekannte Nachricht. Nutze /help.")


async def application_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOGGER.error("Unhandled Telegram update error", exc_info=context.error)


def wrap(
    func: Callable[[Update, ContextTypes.DEFAULT_TYPE, BotRuntime], Awaitable[None]]
) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]:
    async def _wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
        runtime: BotRuntime = context.application.bot_data["runtime"]
        if not ensure_allowed_chat(update, runtime.allowed_chat_ids, runtime.allowed_user_ids):
            message = update.effective_message
            if message is not None:
                await message.reply_text("Dieser User oder Chat ist nicht freigeschaltet.")
            return ConversationHandler.END
        return await func(update, context, runtime)

    return _wrapped


async def post_init(application: Application) -> None:
    await application.bot.set_my_commands(
        [
            BotCommand("marketbrief_menu", "Market-Brief-Menue oeffnen"),
        ]
    )


def build_application() -> Application:
    config = load_config()
    token = config_required(config, "bot_token", "TELEGRAM_BOT_TOKEN")
    runtime = BotRuntime(
        allowed_chat_ids=get_allowed_chat_ids(config),
        allowed_user_ids=get_allowed_user_ids(config),
        gemini_model=config_or_env(config, "gemini_model", "TELEGRAM_GEMINI_MODEL") or "gemini-2.0-flash",
        config=config,
        auto_brief=AutoBriefSettings.from_config(config.get("auto_market_brief")),
    )

    application = ApplicationBuilder().token(token).post_init(post_init).build()
    application.bot_data["runtime"] = runtime
    configure_auto_brief_job(application, runtime)

    application.add_handler(CommandHandler("ping", wrap(ping_command)))
    application.add_handler(CommandHandler("categories", wrap(categories_command)))
    application.add_handler(CommandHandler("marketbrief", wrap(marketbrief_command)))
    application.add_handler(CommandHandler("autobrief_next", wrap(autobrief_next_command)))
    application.add_handler(CommandHandler("autobrief_set", wrap(autobrief_set_command)))
    application.add_handler(CommandHandler("autobrief_filter", wrap(autobrief_filter_command)))
    application.add_handler(CommandHandler("autobrief_on", wrap(autobrief_on_command)))
    application.add_handler(CommandHandler("autobrief_off", wrap(autobrief_off_command)))
    application.add_handler(CommandHandler("article_summary", wrap(article_summary_command)))
    application.add_handler(CommandHandler("echo", wrap(echo_command)))
    application.add_handler(
        ConversationHandler(
            entry_points=[
                CommandHandler("marketbrief_menu", wrap(marketbrief_menu_command)),
                CommandHandler("start", wrap(start_command)),
                CommandHandler("help", wrap(help_command)),
            ],
            states={
                STATE_MAIN_MENU: [
                    CallbackQueryHandler(marketbrief_menu_callback, pattern=f"^{CALLBACK_PREFIX_MAIN_MENU}")
                ],
                STATE_MARKETBRIEF_QUERY: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, marketbrief_menu_query)
                ],
                STATE_SUPPORT_MENU: [
                    CallbackQueryHandler(supportbot_menu, pattern=f"^{CALLBACK_PREFIX_SUPPORT}")
                ],
                STATE_LIST_ACTION: [
                    CallbackQueryHandler(listenpflege_action, pattern=f"^{CALLBACK_PREFIX_LIST_ACTION}")
                ],
                STATE_LIST_ADD_CATEGORY: [
                    CallbackQueryHandler(listenpflege_add_category, pattern=f"^{CALLBACK_PREFIX_LIST_ADD_CATEGORY}"),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, listenpflege_add_category),
                ],
                STATE_LIST_ADD_SUBCATEGORY: [
                    CallbackQueryHandler(
                        listenpflege_add_subcategory,
                        pattern=f"^{CALLBACK_PREFIX_LIST_ADD_SUBCATEGORY}",
                    ),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, listenpflege_add_subcategory),
                ],
                STATE_LIST_ADD_NAME: [
                    CallbackQueryHandler(listenpflege_add_name, pattern=f"^{CALLBACK_PREFIX_LIST_ADD_NAME}"),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, listenpflege_add_name),
                ],
                STATE_LIST_ADD_TICKER: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, listenpflege_add_ticker)
                ],
                STATE_LIST_ADD_ISIN: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, listenpflege_add_isin)
                ],
                STATE_LIST_ADD_WKN: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, listenpflege_add_wkn)
                ],
                STATE_LIST_ADD_TRADE_REPUBLIC_AKTIE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, listenpflege_add_trade_republic_aktie)
                ],
                STATE_LIST_ADD_TRADE_REPUBLIC_DERIVATE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, listenpflege_add_trade_republic_derivate)
                ],
                STATE_LIST_ADD_OPTIONAL_MENU: [
                    CallbackQueryHandler(listenpflege_add_optional_menu, pattern=f"^{CALLBACK_PREFIX_LIST_OPTIONAL}")
                ],
                STATE_LIST_ADD_OPTIONAL_VALUE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, listenpflege_add_optional_value)
                ],
                STATE_LIST_ADD_CONFIRM: [
                    CallbackQueryHandler(listenpflege_add_confirm, pattern=f"^{CALLBACK_PREFIX_LIST_CONFIRM}")
                ],
                STATE_LIST_EDIT_CATEGORY: [
                    CallbackQueryHandler(listenpflege_pick_category, pattern=f"^{CALLBACK_PREFIX_LIST_CATEGORY}")
                ],
                STATE_LIST_EDIT_SUBCATEGORY: [
                    CallbackQueryHandler(
                        listenpflege_pick_subcategory,
                        pattern=f"^{CALLBACK_PREFIX_LIST_SUBCATEGORY}",
                    )
                ],
                STATE_LIST_EDIT_ENTRY: [
                    CallbackQueryHandler(listenpflege_pick_entry, pattern=f"^{CALLBACK_PREFIX_LIST_ENTRY}")
                ],
                STATE_LIST_EDIT_FIELD: [
                    CallbackQueryHandler(listenpflege_edit_field, pattern=f"^{CALLBACK_PREFIX_LIST_FIELD}")
                ],
                STATE_LIST_EDIT_VALUE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, listenpflege_edit_value)
                ],
                STATE_LIST_EDIT_CONFIRM: [
                    CallbackQueryHandler(listenpflege_edit_confirm, pattern=f"^{CALLBACK_PREFIX_LIST_CONFIRM}")
                ],
                STATE_LIST_DELETE_CATEGORY: [
                    CallbackQueryHandler(listenpflege_pick_category, pattern=f"^{CALLBACK_PREFIX_LIST_CATEGORY}")
                ],
                STATE_LIST_DELETE_SUBCATEGORY: [
                    CallbackQueryHandler(
                        listenpflege_pick_subcategory,
                        pattern=f"^{CALLBACK_PREFIX_LIST_SUBCATEGORY}",
                    )
                ],
                STATE_LIST_DELETE_ENTRY: [
                    CallbackQueryHandler(listenpflege_pick_entry, pattern=f"^{CALLBACK_PREFIX_LIST_ENTRY}")
                ],
                STATE_LIST_DELETE_CONFIRM: [
                    CallbackQueryHandler(listenpflege_delete_confirm, pattern=f"^{CALLBACK_PREFIX_LIST_CONFIRM}")
                ],
                STATE_BATCH_CATEGORY: [
                    CallbackQueryHandler(marketbrief_start_category, pattern=f"^{CALLBACK_PREFIX_CATEGORY}")
                ],
                STATE_BATCH_SUBCATEGORY: [
                    CallbackQueryHandler(marketbrief_start_subcategory, pattern=f"^{CALLBACK_PREFIX_SUBCATEGORY}")
                ],
                STATE_BATCH_ENTRY: [
                    CallbackQueryHandler(marketbrief_start_entry, pattern=f"^{CALLBACK_PREFIX_ENTRY}")
                ],
                STATE_BATCH_SELECTION_MENU: [
                    CallbackQueryHandler(marketbrief_start_selection_menu, pattern=f"^{CALLBACK_PREFIX_BATCH_SELECT}")
                ],
                STATE_BATCH_NEWS: [
                    CallbackQueryHandler(marketbrief_start_news, pattern=f"^{CALLBACK_PREFIX_NEWS}")
                ],
                STATE_BATCH_RESULT_MODE: [
                    CallbackQueryHandler(marketbrief_start_result_mode, pattern=f"^{CALLBACK_PREFIX_BATCH_RESULT}")
                ],
                STATE_AUTO_MENU: [
                    CallbackQueryHandler(autobrief_start_menu, pattern=f"^{CALLBACK_PREFIX_AUTO_MENU}")
                ],
                STATE_AUTO_CATEGORY: [
                    CallbackQueryHandler(autobrief_start_category, pattern=f"^{CALLBACK_PREFIX_AUTO_CATEGORY}")
                ],
                STATE_AUTO_SUBCATEGORY: [
                    CallbackQueryHandler(autobrief_start_subcategory, pattern=f"^{CALLBACK_PREFIX_AUTO_SUBCATEGORY}")
                ],
                STATE_AUTO_INTERVAL: [
                    CallbackQueryHandler(autobrief_start_interval, pattern=f"^{CALLBACK_PREFIX_AUTO_INTERVAL}")
                ],
                STATE_AUTO_WINDOW_FROM: [
                    CallbackQueryHandler(autobrief_start_window_from, pattern=f"^{CALLBACK_PREFIX_AUTO_WINDOW}")
                ],
                STATE_AUTO_WINDOW_TO: [
                    CallbackQueryHandler(autobrief_start_window_to, pattern=f"^{CALLBACK_PREFIX_AUTO_WINDOW}")
                ],
            },
            fallbacks=[CommandHandler("cancel", main_menu_cancel)],
        )
    )
    application.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("supportbot", wrap(supportbot_command))],
            states={
                STATE_SUPPORT_MENU: [
                    CallbackQueryHandler(supportbot_menu, pattern=f"^{CALLBACK_PREFIX_SUPPORT}")
                ],
            },
            fallbacks=[CommandHandler("cancel", wrap(marketbrief_start_cancel))],
        )
    )
    application.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("listenpflege", wrap(listenpflege_start_command))],
            states={
                STATE_LIST_ACTION: [
                    CallbackQueryHandler(listenpflege_action, pattern=f"^{CALLBACK_PREFIX_LIST_ACTION}")
                ],
                STATE_LIST_ADD_CATEGORY: [
                    CallbackQueryHandler(listenpflege_add_category, pattern=f"^{CALLBACK_PREFIX_LIST_ADD_CATEGORY}"),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, listenpflege_add_category),
                ],
                STATE_LIST_ADD_SUBCATEGORY: [
                    CallbackQueryHandler(
                        listenpflege_add_subcategory,
                        pattern=f"^{CALLBACK_PREFIX_LIST_ADD_SUBCATEGORY}",
                    ),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, listenpflege_add_subcategory),
                ],
                STATE_LIST_ADD_NAME: [
                    CallbackQueryHandler(listenpflege_add_name, pattern=f"^{CALLBACK_PREFIX_LIST_ADD_NAME}"),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, listenpflege_add_name)
                ],
                STATE_LIST_ADD_TICKER: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, listenpflege_add_ticker)
                ],
                STATE_LIST_ADD_ISIN: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, listenpflege_add_isin)
                ],
                STATE_LIST_ADD_WKN: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, listenpflege_add_wkn)
                ],
                STATE_LIST_ADD_TRADE_REPUBLIC_AKTIE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, listenpflege_add_trade_republic_aktie)
                ],
                STATE_LIST_ADD_TRADE_REPUBLIC_DERIVATE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, listenpflege_add_trade_republic_derivate)
                ],
                STATE_LIST_ADD_OPTIONAL_MENU: [
                    CallbackQueryHandler(listenpflege_add_optional_menu, pattern=f"^{CALLBACK_PREFIX_LIST_OPTIONAL}")
                ],
                STATE_LIST_ADD_OPTIONAL_VALUE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, listenpflege_add_optional_value)
                ],
                STATE_LIST_ADD_CONFIRM: [
                    CallbackQueryHandler(listenpflege_add_confirm, pattern=f"^{CALLBACK_PREFIX_LIST_CONFIRM}")
                ],
                STATE_LIST_EDIT_CATEGORY: [
                    CallbackQueryHandler(listenpflege_pick_category, pattern=f"^{CALLBACK_PREFIX_LIST_CATEGORY}")
                ],
                STATE_LIST_EDIT_SUBCATEGORY: [
                    CallbackQueryHandler(
                        listenpflege_pick_subcategory,
                        pattern=f"^{CALLBACK_PREFIX_LIST_SUBCATEGORY}",
                    )
                ],
                STATE_LIST_EDIT_ENTRY: [
                    CallbackQueryHandler(listenpflege_pick_entry, pattern=f"^{CALLBACK_PREFIX_LIST_ENTRY}")
                ],
                STATE_LIST_EDIT_FIELD: [
                    CallbackQueryHandler(listenpflege_edit_field, pattern=f"^{CALLBACK_PREFIX_LIST_FIELD}")
                ],
                STATE_LIST_EDIT_VALUE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, listenpflege_edit_value)
                ],
                STATE_LIST_EDIT_CONFIRM: [
                    CallbackQueryHandler(listenpflege_edit_confirm, pattern=f"^{CALLBACK_PREFIX_LIST_CONFIRM}")
                ],
                STATE_LIST_DELETE_CATEGORY: [
                    CallbackQueryHandler(listenpflege_pick_category, pattern=f"^{CALLBACK_PREFIX_LIST_CATEGORY}")
                ],
                STATE_LIST_DELETE_SUBCATEGORY: [
                    CallbackQueryHandler(
                        listenpflege_pick_subcategory,
                        pattern=f"^{CALLBACK_PREFIX_LIST_SUBCATEGORY}",
                    )
                ],
                STATE_LIST_DELETE_ENTRY: [
                    CallbackQueryHandler(listenpflege_pick_entry, pattern=f"^{CALLBACK_PREFIX_LIST_ENTRY}")
                ],
                STATE_LIST_DELETE_CONFIRM: [
                    CallbackQueryHandler(listenpflege_delete_confirm, pattern=f"^{CALLBACK_PREFIX_LIST_CONFIRM}")
                ],
            },
            fallbacks=[CommandHandler("cancel", wrap(listenpflege_cancel))],
        )
    )
    application.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("marketbrief_start", wrap(marketbrief_start_command))],
            states={
                STATE_BATCH_CATEGORY: [
                    CallbackQueryHandler(marketbrief_start_category, pattern=f"^{CALLBACK_PREFIX_CATEGORY}")
                ],
                STATE_BATCH_SUBCATEGORY: [
                    CallbackQueryHandler(marketbrief_start_subcategory, pattern=f"^{CALLBACK_PREFIX_SUBCATEGORY}")
                ],
                STATE_BATCH_ENTRY: [
                    CallbackQueryHandler(marketbrief_start_entry, pattern=f"^{CALLBACK_PREFIX_ENTRY}")
                ],
                STATE_BATCH_SELECTION_MENU: [
                    CallbackQueryHandler(marketbrief_start_selection_menu, pattern=f"^{CALLBACK_PREFIX_BATCH_SELECT}")
                ],
                STATE_BATCH_NEWS: [
                    CallbackQueryHandler(marketbrief_start_news, pattern=f"^{CALLBACK_PREFIX_NEWS}")
                ],
                STATE_BATCH_RESULT_MODE: [
                    CallbackQueryHandler(marketbrief_start_result_mode, pattern=f"^{CALLBACK_PREFIX_BATCH_RESULT}")
                ],
            },
            fallbacks=[CommandHandler("cancel", wrap(marketbrief_start_cancel))],
        )
    )
    application.add_handler(
        ConversationHandler(
            entry_points=[
                CommandHandler("autobrief", wrap(autobrief_status_command)),
                CommandHandler("autobrief_start", wrap(autobrief_start_command)),
            ],
            states={
                STATE_AUTO_MENU: [
                    CallbackQueryHandler(autobrief_start_menu, pattern=f"^{CALLBACK_PREFIX_AUTO_MENU}")
                ],
                STATE_AUTO_CATEGORY: [
                    CallbackQueryHandler(autobrief_start_category, pattern=f"^{CALLBACK_PREFIX_AUTO_CATEGORY}")
                ],
                STATE_AUTO_SUBCATEGORY: [
                    CallbackQueryHandler(autobrief_start_subcategory, pattern=f"^{CALLBACK_PREFIX_AUTO_SUBCATEGORY}")
                ],
                STATE_AUTO_INTERVAL: [
                    CallbackQueryHandler(autobrief_start_interval, pattern=f"^{CALLBACK_PREFIX_AUTO_INTERVAL}")
                ],
                STATE_AUTO_WINDOW_FROM: [
                    CallbackQueryHandler(autobrief_start_window_from, pattern=f"^{CALLBACK_PREFIX_AUTO_WINDOW}")
                ],
                STATE_AUTO_WINDOW_TO: [
                    CallbackQueryHandler(autobrief_start_window_to, pattern=f"^{CALLBACK_PREFIX_AUTO_WINDOW}")
                ],
            },
            fallbacks=[CommandHandler("cancel", wrap(autobrief_start_cancel))],
        )
    )
    application.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("certificate_scraper_start", wrap(certificate_scraper_start_command))],
            states={
                STATE_CERTIFICATE_ISIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, certificate_scraper_isin)],
                STATE_CERTIFICATE_MIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, certificate_scraper_min)],
                STATE_CERTIFICATE_MAX: [MessageHandler(filters.TEXT & ~filters.COMMAND, certificate_scraper_max)],
                STATE_CERTIFICATE_DIRECTION: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, certificate_scraper_direction)
                ],
            },
            fallbacks=[CommandHandler("cancel", wrap(certificate_scraper_cancel))],
        )
    )
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_message))
    application.add_error_handler(application_error_handler)
    return application


def main() -> int:
    with SingleInstanceLock(LOCK_PATH):
        write_heartbeat("starting", {"pid": os.getpid()})
        append_event("main_bot", "INFO", "Telegram-Haupt-Bot wird gestartet.", {"pid": os.getpid()})
        application = build_application()
        LOGGER.info("Starting Telegram bot | pid=%s | executable=%s", os.getpid(), os.sys.executable)
        try:
            write_heartbeat("running", {"pid": os.getpid()})
            application.run_polling(allowed_updates=Update.ALL_TYPES)
        finally:
            write_heartbeat("stopped", {"pid": os.getpid()})
            append_event("main_bot", "INFO", "Telegram-Haupt-Bot wurde beendet.", {"pid": os.getpid()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


