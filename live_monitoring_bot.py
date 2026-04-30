from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from batch_market_brief_client import load_categories, load_subcategories
from bot_monitoring import (
    HEARTBEAT_INTERVAL_SECONDS,
    LIVE_MONITORING_BOT_LOCK_PATH,
    SingleInstanceLock,
    append_event,
    configure_event_logging,
    write_live_monitoring_heartbeat,
)
from price_monitor import (
    MonitorItem,
    collect_monitor_entries,
    condition_label,
    condition_matches,
    fetch_live_price,
    load_monitor_items,
    load_yfinance,
    parse_enabled,
    parse_target_price,
    resolve_monitor_symbol,
    should_check,
    update_live_monitoring_config,
)
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)


logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
LOGGER = logging.getLogger(__name__)

CONFIG_PATH = Path("config/app_config.json")
XML_PATH = Path("config/stock_categories/stock_categories.xml")
DEFAULT_POLL_SECONDS = 30
MAX_MESSAGE_LENGTH = 3900
LIVE_PAGE_SIZE = 10
STATE_LIVE_CATEGORY = 1
STATE_LIVE_SUBCATEGORY = 2
STATE_LIVE_ENTRY = 3
STATE_LIVE_MENU = 4
STATE_LIVE_TARGET = 5
STATE_LIVE_CONDITION = 6
STATE_LIVE_INTERVAL = 7
STATE_LIVE_INTERVAL_CUSTOM = 8
STATE_MAIN_MENU = 20
CALLBACK_PREFIX_LIVE_CATEGORY = "lmc:"
CALLBACK_PREFIX_LIVE_SUBCATEGORY = "lms:"
CALLBACK_PREFIX_LIVE_ENTRY = "lme:"
CALLBACK_PREFIX_LIVE_MENU = "lmm:"
CALLBACK_PREFIX_LIVE_CONDITION = "lmco:"
CALLBACK_PREFIX_LIVE_INTERVAL = "lmi:"
CALLBACK_PREFIX_MAIN_MENU = "lmmain:"
INTERVAL_PRESETS = [1, 5, 15, 30, 60]


@dataclass
class LiveMonitoringRuntime:
    config: dict
    allowed_chat_ids: set[int]
    allowed_user_ids: set[int]
    chat_id: int
    poll_seconds: int
    last_checked: dict[str, datetime] = field(default_factory=dict)
    last_rules_count: int = 0
    last_trigger_count: int = 0


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise RuntimeError(f"Konfigurationsdatei fehlt: {CONFIG_PATH}")
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Konfiguration muss ein JSON-Objekt sein.")
    return payload


def save_config(config: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=True, indent=2), encoding="utf-8")


def config_or_env(config: dict, key: str, env_name: str = "") -> str:
    value = str(config.get(key, "")).strip()
    if value:
        return value
    if env_name:
        env_value = os.getenv(env_name, "").strip()
        if env_value:
            return env_value
    return ""


def parse_id_set(raw: object) -> set[int]:
    values: set[int] = set()
    if raw is None:
        return values
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            values.add(int(part))
        except ValueError:
            continue
    return values


def live_bot_config(config: dict) -> dict:
    payload = config.get("live_monitoring_bot")
    if not isinstance(payload, dict):
        payload = {}
        config["live_monitoring_bot"] = payload
    return payload


def build_runtime(config: dict) -> LiveMonitoringRuntime:
    live_config = live_bot_config(config)
    allowed_chat_ids = parse_id_set(config.get("live_monitoring_allowed_chat_ids") or config.get("allowed_chat_ids"))
    allowed_user_ids = parse_id_set(config.get("live_monitoring_allowed_user_ids") or config.get("allowed_user_ids"))
    try:
        chat_id = int(live_config.get("chat_id", 0) or 0)
    except (TypeError, ValueError):
        chat_id = 0
    try:
        poll_seconds = int(live_config.get("poll_seconds", DEFAULT_POLL_SECONDS) or DEFAULT_POLL_SECONDS)
    except (TypeError, ValueError):
        poll_seconds = DEFAULT_POLL_SECONDS

    return LiveMonitoringRuntime(
        config=config,
        allowed_chat_ids=allowed_chat_ids,
        allowed_user_ids=allowed_user_ids,
        chat_id=chat_id,
        poll_seconds=max(5, poll_seconds),
    )


def is_allowed(update: Update, runtime: LiveMonitoringRuntime) -> bool:
    if not runtime.allowed_chat_ids and not runtime.allowed_user_ids:
        return True

    chat = update.effective_chat
    user = update.effective_user
    if chat and chat.id in runtime.allowed_chat_ids:
        return True
    if user and user.id in runtime.allowed_user_ids:
        return True
    return False


async def require_access(update: Update, runtime: LiveMonitoringRuntime) -> bool:
    if is_allowed(update, runtime):
        return True
    message = update.effective_message
    if message is not None:
        await message.reply_text("Dieser Chat oder User ist fuer den Live-Monitoring-Bot nicht freigeschaltet.")
    return False


def save_target_chat(runtime: LiveMonitoringRuntime, chat_id: int) -> None:
    payload = live_bot_config(runtime.config)
    payload["chat_id"] = int(chat_id)
    payload.setdefault("poll_seconds", runtime.poll_seconds)
    save_config(runtime.config)
    runtime.chat_id = int(chat_id)


def format_monitor_item(item: MonitorItem) -> str:
    return (
        f"{item.name} ({item.symbol}) | "
        f"{condition_label(item.condition)} {item.target_price:.2f} | "
        f"Intervall {item.interval_min} min"
    )


def cleanup_live_monitoring_context(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in [
        "live_categories",
        "live_subcategories",
        "live_selected_category",
        "live_selected_subcategory",
        "live_entry_options",
        "live_entry_page",
        "live_monitor_selection",
        "live_monitor_entry",
        "live_enable_after_target",
    ]:
        context.user_data.pop(key, None)


def build_text_navigation_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([["Zurueck", "Abbrechen"]], resize_keyboard=True, one_time_keyboard=False)


def live_text_nav_choice(message) -> str:
    text = (message.text or "").strip().casefold()
    if text == "zurueck":
        return "back"
    if text == "abbrechen":
        return "cancel"
    return ""


def build_index_keyboard(
    options: list[str],
    prefix: str,
    *,
    include_all: bool = False,
    include_back: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if include_all:
        rows.append([InlineKeyboardButton("Alle", callback_data=f"{prefix}all")])
    for index, option in enumerate(options):
        rows.append([InlineKeyboardButton(option, callback_data=f"{prefix}{index}")])

    nav_row: list[InlineKeyboardButton] = []
    if include_back:
        nav_row.append(InlineKeyboardButton("Zurueck", callback_data=f"{prefix}back"))
    nav_row.append(InlineKeyboardButton("Abbrechen", callback_data=f"{prefix}cancel"))
    rows.append(nav_row)
    return InlineKeyboardMarkup(rows)


def build_entry_keyboard(entries: list[dict], page: int = 0) -> InlineKeyboardMarkup:
    page = max(0, page)
    total_pages = max(1, (len(entries) + LIVE_PAGE_SIZE - 1) // LIVE_PAGE_SIZE)
    page = min(page, total_pages - 1)
    start = page * LIVE_PAGE_SIZE
    end = min(start + LIVE_PAGE_SIZE, len(entries))
    rows: list[list[InlineKeyboardButton]] = []

    for index in range(start, end):
        entry = entries[index]
        config = entry.get("live_monitoring", {})
        marker = "[AN]" if parse_enabled(config.get("enabled")) else "[AUS]"
        symbol = entry.get("symbol") or resolve_monitor_symbol(entry) or "-"
        label = f"{marker} {entry.get('name', '-')} ({symbol})"
        rows.append([InlineKeyboardButton(label[:60], callback_data=f"{CALLBACK_PREFIX_LIVE_ENTRY}{index}")])

    page_row: list[InlineKeyboardButton] = []
    if page > 0:
        page_row.append(InlineKeyboardButton("Vorherige", callback_data=f"{CALLBACK_PREFIX_LIVE_ENTRY}page:{page - 1}"))
    if page < total_pages - 1:
        page_row.append(InlineKeyboardButton("Naechste", callback_data=f"{CALLBACK_PREFIX_LIVE_ENTRY}page:{page + 1}"))
    if page_row:
        rows.append(page_row)

    rows.append(
        [
            InlineKeyboardButton("Zurueck", callback_data=f"{CALLBACK_PREFIX_LIVE_ENTRY}back"),
            InlineKeyboardButton("Abbrechen", callback_data=f"{CALLBACK_PREFIX_LIVE_ENTRY}cancel"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def build_monitor_menu_keyboard(entry: dict) -> InlineKeyboardMarkup:
    config = entry.get("live_monitoring", {})
    enabled = parse_enabled(config.get("enabled"))
    toggle_label = "Monitoring ausschalten" if enabled else "Monitoring einschalten"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(toggle_label, callback_data=f"{CALLBACK_PREFIX_LIVE_MENU}toggle")],
            [
                InlineKeyboardButton("Zielpreis", callback_data=f"{CALLBACK_PREFIX_LIVE_MENU}target"),
                InlineKeyboardButton("Bedingung", callback_data=f"{CALLBACK_PREFIX_LIVE_MENU}condition"),
            ],
            [
                InlineKeyboardButton("Intervall", callback_data=f"{CALLBACK_PREFIX_LIVE_MENU}interval"),
                InlineKeyboardButton("Kurs anzeigen", callback_data=f"{CALLBACK_PREFIX_LIVE_MENU}price"),
            ],
            [
                InlineKeyboardButton("Andere Aktie", callback_data=f"{CALLBACK_PREFIX_LIVE_MENU}entries"),
                InlineKeyboardButton("Fertig", callback_data=f"{CALLBACK_PREFIX_LIVE_MENU}done"),
            ],
        ]
    )


def build_condition_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Kurs >= Zielpreis", callback_data=f"{CALLBACK_PREFIX_LIVE_CONDITION}above")],
            [InlineKeyboardButton("Kurs <= Zielpreis", callback_data=f"{CALLBACK_PREFIX_LIVE_CONDITION}below")],
            [
                InlineKeyboardButton("Zurueck", callback_data=f"{CALLBACK_PREFIX_LIVE_CONDITION}back"),
                InlineKeyboardButton("Abbrechen", callback_data=f"{CALLBACK_PREFIX_LIVE_CONDITION}cancel"),
            ],
        ]
    )


def build_interval_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    current_row: list[InlineKeyboardButton] = []
    for value in INTERVAL_PRESETS:
        current_row.append(InlineKeyboardButton(f"{value} min", callback_data=f"{CALLBACK_PREFIX_LIVE_INTERVAL}set:{value}"))
        if len(current_row) == 3:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)
    rows.append([InlineKeyboardButton("Eigener Wert", callback_data=f"{CALLBACK_PREFIX_LIVE_INTERVAL}custom")])
    rows.append(
        [
            InlineKeyboardButton("Zurueck", callback_data=f"{CALLBACK_PREFIX_LIVE_INTERVAL}back"),
            InlineKeyboardButton("Abbrechen", callback_data=f"{CALLBACK_PREFIX_LIVE_INTERVAL}cancel"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def monitor_entry_matches(entry: dict, selection: dict) -> bool:
    return (
        entry.get("category") == selection.get("category")
        and entry.get("subcategory") == selection.get("subcategory")
        and entry.get("query") == selection.get("query")
    )


def refresh_selected_monitor_entry(context: ContextTypes.DEFAULT_TYPE) -> dict | None:
    selection = context.user_data.get("live_monitor_selection")
    if not isinstance(selection, dict):
        return None
    for entry in collect_monitor_entries(str(XML_PATH)):
        if monitor_entry_matches(entry, selection):
            context.user_data["live_monitor_entry"] = entry
            return entry
    return None


def format_live_config(entry: dict, action_message: str = "", price_line: str = "") -> str:
    config = entry.get("live_monitoring", {})
    enabled = "an" if parse_enabled(config.get("enabled")) else "aus"
    target = str(config.get("target_price") or "-").strip() or "-"
    condition = condition_label(str(config.get("condition") or "above").strip())
    interval = str(config.get("interval_min") or "5").strip() or "5"
    symbol = entry.get("symbol") or resolve_monitor_symbol(entry) or "-"

    lines = [
        "Live-Monitoring bearbeiten",
        f"{entry.get('name', '-')} ({symbol})",
        f"{entry.get('category', '-')} / {entry.get('subcategory', '-')}",
        "",
        f"Monitoring: {enabled}",
        f"Zielpreis: {target}",
        f"Bedingung: {condition}",
        f"Intervall: {interval} min",
    ]
    if price_line:
        lines.extend(["", price_line])
    if action_message:
        lines.extend(["", action_message])
    return "\n".join(lines)


def filter_monitor_entries(category: str, subcategory: str) -> list[dict]:
    entries = collect_monitor_entries(str(XML_PATH))
    filtered = []
    for entry in entries:
        if category and entry.get("category") != category:
            continue
        if subcategory and entry.get("subcategory") != subcategory:
            continue
        filtered.append(entry)
    return sorted(filtered, key=lambda item: (item.get("category", ""), item.get("subcategory", ""), item.get("name", "")))


async def update_selected_monitoring_config(
    context: ContextTypes.DEFAULT_TYPE,
    updates: dict[str, object],
) -> tuple[Path, dict[str, str], dict | None]:
    selection = context.user_data.get("live_monitor_selection")
    if not isinstance(selection, dict):
        raise RuntimeError("Bearbeitungskontext fehlt. Bitte /live_monitoring neu starten.")

    backup_path, config = await asyncio.to_thread(
        update_live_monitoring_config,
        str(selection.get("category", "")),
        str(selection.get("subcategory", "")),
        str(selection.get("query", "")),
        updates,
        str(XML_PATH),
    )
    context.user_data["live_entry_options"] = filter_monitor_entries(
        str(context.user_data.get("live_selected_category", "")),
        str(context.user_data.get("live_selected_subcategory", "")),
    )
    return backup_path, config, refresh_selected_monitor_entry(context)


async def show_monitor_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    action_message: str = "",
    price_line: str = "",
) -> int:
    entry = refresh_selected_monitor_entry(context)
    query = update.callback_query
    message = update.effective_message
    if entry is None:
        cleanup_live_monitoring_context(context)
        if query is not None:
            await query.edit_message_text("Eintrag wurde nicht gefunden. Bitte /live_monitoring neu starten.")
        elif message is not None:
            await message.reply_text("Eintrag wurde nicht gefunden. Bitte /live_monitoring neu starten.")
        return ConversationHandler.END

    text = format_live_config(entry, action_message=action_message, price_line=price_line)
    keyboard = build_monitor_menu_keyboard(entry)
    if query is not None:
        await query.edit_message_text(text, reply_markup=keyboard)
    elif message is not None:
        await message.reply_text(text, reply_markup=keyboard)
    return STATE_LIVE_MENU


async def show_monitor_update_error(update: Update, context: ContextTypes.DEFAULT_TYPE, exc: Exception) -> int:
    return await show_monitor_menu(update, context, action_message=f"Speichern fehlgeschlagen: {exc}")


def format_status(runtime: LiveMonitoringRuntime) -> str:
    items = load_monitor_items(str(XML_PATH))
    runtime.last_rules_count = len(items)
    lines = [
        "Live-Monitoring-Bot Status",
        f"Ziel-Chat-ID: {runtime.chat_id or '-'}",
        f"Poll-Intervall: {runtime.poll_seconds}s",
        f"Aktive Regeln: {len(items)}",
        f"Letzte Trigger im letzten Lauf: {runtime.last_trigger_count}",
    ]
    if items:
        lines.append("")
        lines.extend(format_monitor_item(item) for item in items[:10])
        if len(items) > 10:
            lines.append(f"... plus {len(items) - 10} weitere Regeln")
    return "\n".join(lines)


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


async def send_long(bot, chat_id: int, text: str) -> None:
    for chunk in split_message(text):
        await bot.send_message(chat_id=chat_id, text=chunk)


def format_command_overview() -> str:
    return (
        "Live-Monitoring-Bot\n\n"
        "Waehle eine Aktion:"
    )


def build_main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Preisregel bearbeiten", callback_data=f"{CALLBACK_PREFIX_MAIN_MENU}settings")],
            [
                InlineKeyboardButton("Aktive Regeln", callback_data=f"{CALLBACK_PREFIX_MAIN_MENU}rules"),
                InlineKeyboardButton("Status", callback_data=f"{CALLBACK_PREFIX_MAIN_MENU}status"),
            ],
            [
                InlineKeyboardButton("Diesen Chat speichern", callback_data=f"{CALLBACK_PREFIX_MAIN_MENU}target_chat"),
                InlineKeyboardButton("Bot testen", callback_data=f"{CALLBACK_PREFIX_MAIN_MENU}ping"),
            ],
            [InlineKeyboardButton("Schliessen", callback_data=f"{CALLBACK_PREFIX_MAIN_MENU}done")],
        ]
    )


def get_active_monitor_entries() -> list[dict]:
    entries = []
    for entry in collect_monitor_entries(str(XML_PATH)):
        if parse_enabled(entry.get("live_monitoring", {}).get("enabled")):
            entries.append(entry)
    return sorted(entries, key=lambda item: (item.get("category", ""), item.get("subcategory", ""), item.get("name", "")))


def build_active_rules_text(entries: list[dict], action_message: str = "") -> str:
    count = len(entries)
    label = "aktive Regel" if count == 1 else "aktive Regeln"
    lines = [f"{count} {label}"]
    if action_message:
        lines.extend(["", action_message])
    return "\n".join(lines)


def build_active_rules_keyboard(entries: list[dict], labels: list[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for index, entry in enumerate(entries):
        label = labels[index] if index < len(labels) else str(entry.get("name") or f"Regel {index + 1}")
        rows.append(
            [
                InlineKeyboardButton(label[:58], callback_data=f"{CALLBACK_PREFIX_MAIN_MENU}rule_edit:{index}"),
                InlineKeyboardButton("Aus", callback_data=f"{CALLBACK_PREFIX_MAIN_MENU}rule_off:{index}"),
            ]
        )
    rows.append([InlineKeyboardButton("Zurueck", callback_data=f"{CALLBACK_PREFIX_MAIN_MENU}back")])
    return InlineKeyboardMarkup(rows)


async def build_active_rule_button_labels(context: ContextTypes.DEFAULT_TYPE, entries: list[dict]) -> list[str]:
    if not entries:
        return []
    if "yfinance" not in context.application.bot_data:
        context.application.bot_data["yfinance"] = load_yfinance()
    yf = context.application.bot_data["yfinance"]

    async def build_label(entry: dict) -> str:
        name = str(entry.get("name") or entry.get("symbol") or "Unbekannt").strip()
        symbol = entry.get("symbol") or resolve_monitor_symbol(entry)
        if not symbol:
            return f"{name} / Kurs -"
        try:
            price, currency = await asyncio.to_thread(fetch_live_price, yf, symbol)
        except Exception as exc:
            append_event(
                "live_monitoring_bot",
                "ERROR",
                f"Kursabruf fuer aktive Regel fehlgeschlagen fuer {symbol}: {exc}",
                {"symbol": symbol, "name": name},
            )
            return f"{name} / Kurs -"
        if price is None:
            return f"{name} / Kurs -"
        suffix = f" {currency}" if currency else ""
        return f"{name} / {price:.2f}{suffix}"

    return await asyncio.gather(*(build_label(entry) for entry in entries))


async def show_active_rules_menu(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    action_message: str = "",
) -> int:
    entries = get_active_monitor_entries()
    context.user_data["active_rule_entries"] = entries
    if not entries:
        await query.edit_message_text(
            ("Keine aktiven Live-Monitoring-Regeln gefunden." if not action_message else action_message),
            reply_markup=build_main_menu_keyboard(),
        )
        return STATE_MAIN_MENU

    labels = await build_active_rule_button_labels(context, entries)
    await query.edit_message_text(
        build_active_rules_text(entries, action_message=action_message),
        reply_markup=build_active_rules_keyboard(entries, labels),
    )
    return STATE_MAIN_MENU


async def send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str = "") -> None:
    runtime: LiveMonitoringRuntime = context.application.bot_data["runtime"]
    if not await require_access(update, runtime):
        return

    message_text = text or format_command_overview()
    query = update.callback_query
    message = update.effective_message
    if query is not None:
        await query.edit_message_text(message_text, reply_markup=build_main_menu_keyboard())
    elif message is not None:
        await message.reply_text(message_text, reply_markup=build_main_menu_keyboard())


async def live_monitoring_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await send_main_menu(update, context)
    return STATE_MAIN_MENU


async def save_current_chat_as_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    runtime: LiveMonitoringRuntime = context.application.bot_data["runtime"]
    chat = update.effective_chat
    if chat is None:
        return "Kein Chat gefunden."
    save_target_chat(runtime, chat.id)
    return "Dieser Chat wurde als Ziel fuer Preis-Trigger gespeichert."


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: LiveMonitoringRuntime = context.application.bot_data["runtime"]
    if not await require_access(update, runtime):
        return

    message = update.effective_message
    result = await save_current_chat_as_target(update, context)
    if message is not None:
        await message.reply_text(
            "Live-Monitoring-Bot aktiv.\n"
            f"{result}\n\n"
            "Oeffne das Menue mit /live_monitoring.",
            reply_markup=build_main_menu_keyboard(),
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_main_menu(update, context)


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: LiveMonitoringRuntime = context.application.bot_data["runtime"]
    if not await require_access(update, runtime):
        return
    message = update.effective_message
    if message is not None:
        await message.reply_text("pong")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: LiveMonitoringRuntime = context.application.bot_data["runtime"]
    if not await require_access(update, runtime):
        return
    message = update.effective_message
    if message is not None:
        for chunk in split_message(format_status(runtime)):
            await message.reply_text(chunk)


async def rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: LiveMonitoringRuntime = context.application.bot_data["runtime"]
    if not await require_access(update, runtime):
        return
    message = update.effective_message
    if message is None:
        return

    items = load_monitor_items(str(XML_PATH))
    if not items:
        await message.reply_text("Keine aktiven Live-Monitoring-Regeln gefunden.")
        return
    text = "Aktive Live-Monitoring-Regeln:\n" + "\n".join(format_monitor_item(item) for item in items)
    for chunk in split_message(text):
        await message.reply_text(chunk)


async def send_rules_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    items = load_monitor_items(str(XML_PATH))
    if not items:
        await message.reply_text("Keine aktiven Live-Monitoring-Regeln gefunden.")
        return
    text = "Aktive Live-Monitoring-Regeln:\n" + "\n".join(format_monitor_item(item) for item in items)
    for chunk in split_message(text):
        await message.reply_text(chunk)


async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    runtime: LiveMonitoringRuntime = context.application.bot_data["runtime"]
    if not await require_access(update, runtime):
        return ConversationHandler.END

    query = update.callback_query
    if query is None:
        return STATE_MAIN_MENU
    await query.answer()
    action = query.data.removeprefix(CALLBACK_PREFIX_MAIN_MENU)

    if action == "done":
        await query.edit_message_text("Live-Monitoring-Menue geschlossen.")
        return ConversationHandler.END

    if action == "back":
        await query.edit_message_text(format_command_overview(), reply_markup=build_main_menu_keyboard())
        return STATE_MAIN_MENU

    if action == "target_chat":
        result = await save_current_chat_as_target(update, context)
        await query.edit_message_text(
            result + "\n\nWaehle eine Aktion:",
            reply_markup=build_main_menu_keyboard(),
        )
        return STATE_MAIN_MENU

    if action == "status":
        await query.edit_message_text(
            format_status(runtime),
            reply_markup=build_main_menu_keyboard(),
        )
        return STATE_MAIN_MENU

    if action == "rules":
        return await show_active_rules_menu(query, context)

    if action.startswith("rule_edit:") or action.startswith("rule_off:"):
        entries: list[dict] = context.user_data.get("active_rule_entries", [])
        try:
            selected = entries[int(action.rsplit(":", 1)[1])]
        except (ValueError, IndexError):
            return await show_active_rules_menu(query, context, "Auswahl ist nicht mehr gueltig.")

        context.user_data["live_monitor_selection"] = {
            "category": selected.get("category", ""),
            "subcategory": selected.get("subcategory", ""),
            "query": selected.get("query", ""),
        }
        context.user_data["live_monitor_entry"] = selected
        context.user_data["live_selected_category"] = selected.get("category", "")
        context.user_data["live_selected_subcategory"] = selected.get("subcategory", "")
        context.user_data["live_entry_options"] = filter_monitor_entries(
            str(selected.get("category", "")),
            str(selected.get("subcategory", "")),
        )
        context.user_data["live_entry_page"] = 0

        if action.startswith("rule_edit:"):
            return await show_monitor_menu(update, context)

        try:
            backup_path, _, _ = await update_selected_monitoring_config(context, {"enabled": False})
        except Exception as exc:
            return await show_active_rules_menu(query, context, f"Ausschalten fehlgeschlagen: {exc}")
        return await show_active_rules_menu(
            query,
            context,
            f"{selected.get('name', 'Regel')} ausgeschaltet.\nBackup: {backup_path.name}",
        )

    if action == "ping":
        await query.edit_message_text("pong", reply_markup=build_main_menu_keyboard())
        return STATE_MAIN_MENU

    if action == "settings":
        cleanup_live_monitoring_context(context)
        categories = await asyncio.to_thread(load_categories)
        context.user_data["live_categories"] = categories
        context.user_data["live_subcategories"] = await asyncio.to_thread(load_subcategories)
        await query.edit_message_text(
            "Live-Monitoring einrichten.\nKategorie waehlen:",
            reply_markup=build_index_keyboard(categories, CALLBACK_PREFIX_LIVE_CATEGORY, include_all=True),
        )
        return STATE_LIVE_CATEGORY

    await query.edit_message_text("Ungueltige Auswahl.", reply_markup=build_main_menu_keyboard())
    return STATE_MAIN_MENU


async def monitoring_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    runtime: LiveMonitoringRuntime = context.application.bot_data["runtime"]
    if not await require_access(update, runtime):
        return ConversationHandler.END

    query = update.callback_query
    if query is None:
        return STATE_LIVE_CATEGORY
    await query.answer()
    raw = query.data.removeprefix(CALLBACK_PREFIX_LIVE_CATEGORY)
    categories: list[str] = context.user_data.get("live_categories", [])

    if raw == "cancel":
        await query.edit_message_text("Live-Monitoring Bearbeitung abgebrochen.")
        cleanup_live_monitoring_context(context)
        return ConversationHandler.END

    if raw == "all":
        context.user_data["live_selected_category"] = ""
        context.user_data["live_selected_subcategory"] = ""
        entries = filter_monitor_entries("", "")
        context.user_data["live_entry_options"] = entries
        context.user_data["live_entry_page"] = 0
        await query.edit_message_text(
            f"Aktie waehlen ({len(entries)} Eintraege):",
            reply_markup=build_entry_keyboard(entries, 0),
        )
        return STATE_LIVE_ENTRY

    try:
        category = categories[int(raw)]
    except (ValueError, IndexError):
        await query.edit_message_text("Ungueltige Auswahl. Bitte /live_monitoring neu starten.")
        cleanup_live_monitoring_context(context)
        return ConversationHandler.END

    context.user_data["live_selected_category"] = category
    subcategories_map: dict[str, list[str]] = context.user_data.get("live_subcategories", {})
    subcategories = subcategories_map.get(category, [])
    if not subcategories:
        entries = filter_monitor_entries(category, "")
        context.user_data["live_selected_subcategory"] = ""
        context.user_data["live_entry_options"] = entries
        context.user_data["live_entry_page"] = 0
        await query.edit_message_text(
            f"Aktie waehlen ({len(entries)} Eintraege):",
            reply_markup=build_entry_keyboard(entries, 0),
        )
        return STATE_LIVE_ENTRY

    await query.edit_message_text(
        "Subkategorie waehlen:",
        reply_markup=build_index_keyboard(
            subcategories,
            CALLBACK_PREFIX_LIVE_SUBCATEGORY,
            include_all=True,
            include_back=True,
        ),
    )
    return STATE_LIVE_SUBCATEGORY


async def monitoring_subcategory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    runtime: LiveMonitoringRuntime = context.application.bot_data["runtime"]
    if not await require_access(update, runtime):
        return ConversationHandler.END

    query = update.callback_query
    if query is None:
        return STATE_LIVE_SUBCATEGORY
    await query.answer()
    raw = query.data.removeprefix(CALLBACK_PREFIX_LIVE_SUBCATEGORY)

    if raw == "cancel":
        await query.edit_message_text("Live-Monitoring Bearbeitung abgebrochen.")
        cleanup_live_monitoring_context(context)
        return ConversationHandler.END

    if raw == "back":
        categories: list[str] = context.user_data.get("live_categories", [])
        await query.edit_message_text(
            "Kategorie waehlen:",
            reply_markup=build_index_keyboard(categories, CALLBACK_PREFIX_LIVE_CATEGORY, include_all=True),
        )
        return STATE_LIVE_CATEGORY

    category = context.user_data.get("live_selected_category", "")
    subcategories_map: dict[str, list[str]] = context.user_data.get("live_subcategories", {})
    subcategories = subcategories_map.get(category, [])
    if raw == "all":
        subcategory = ""
    else:
        try:
            subcategory = subcategories[int(raw)]
        except (ValueError, IndexError):
            await query.edit_message_text("Ungueltige Auswahl. Bitte /live_monitoring neu starten.")
            cleanup_live_monitoring_context(context)
            return ConversationHandler.END

    context.user_data["live_selected_subcategory"] = subcategory
    entries = filter_monitor_entries(category, subcategory)
    context.user_data["live_entry_options"] = entries
    context.user_data["live_entry_page"] = 0
    await query.edit_message_text(
        f"Aktie waehlen ({len(entries)} Eintraege):",
        reply_markup=build_entry_keyboard(entries, 0),
    )
    return STATE_LIVE_ENTRY


async def monitoring_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    runtime: LiveMonitoringRuntime = context.application.bot_data["runtime"]
    if not await require_access(update, runtime):
        return ConversationHandler.END

    query = update.callback_query
    if query is None:
        return STATE_LIVE_ENTRY
    await query.answer()
    raw = query.data.removeprefix(CALLBACK_PREFIX_LIVE_ENTRY)
    entries: list[dict] = context.user_data.get("live_entry_options", [])

    if raw == "cancel":
        await query.edit_message_text("Live-Monitoring Bearbeitung abgebrochen.")
        cleanup_live_monitoring_context(context)
        return ConversationHandler.END

    if raw == "back":
        category = context.user_data.get("live_selected_category", "")
        if not category:
            categories: list[str] = context.user_data.get("live_categories", [])
            await query.edit_message_text(
                "Kategorie waehlen:",
                reply_markup=build_index_keyboard(categories, CALLBACK_PREFIX_LIVE_CATEGORY, include_all=True),
            )
            return STATE_LIVE_CATEGORY
        subcategories_map: dict[str, list[str]] = context.user_data.get("live_subcategories", {})
        subcategories = subcategories_map.get(category, [])
        await query.edit_message_text(
            "Subkategorie waehlen:",
            reply_markup=build_index_keyboard(
                subcategories,
                CALLBACK_PREFIX_LIVE_SUBCATEGORY,
                include_all=True,
                include_back=True,
            ),
        )
        return STATE_LIVE_SUBCATEGORY

    if raw.startswith("page:"):
        try:
            page = int(raw.removeprefix("page:"))
        except ValueError:
            page = 0
        context.user_data["live_entry_page"] = page
        await query.edit_message_text(
            f"Aktie waehlen ({len(entries)} Eintraege):",
            reply_markup=build_entry_keyboard(entries, page),
        )
        return STATE_LIVE_ENTRY

    try:
        entry = entries[int(raw)]
    except (ValueError, IndexError):
        await query.edit_message_text("Ungueltige Auswahl. Bitte /live_monitoring neu starten.")
        cleanup_live_monitoring_context(context)
        return ConversationHandler.END

    context.user_data["live_monitor_selection"] = {
        "category": entry.get("category", ""),
        "subcategory": entry.get("subcategory", ""),
        "query": entry.get("query", ""),
    }
    context.user_data["live_monitor_entry"] = entry
    context.user_data["live_enable_after_target"] = False
    return await show_monitor_menu(update, context)


async def monitoring_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    runtime: LiveMonitoringRuntime = context.application.bot_data["runtime"]
    if not await require_access(update, runtime):
        return ConversationHandler.END

    query = update.callback_query
    if query is None:
        return STATE_LIVE_MENU
    await query.answer()
    action = query.data.removeprefix(CALLBACK_PREFIX_LIVE_MENU)
    entry = refresh_selected_monitor_entry(context)
    if entry is None:
        await query.edit_message_text("Eintrag wurde nicht gefunden. Bitte /live_monitoring neu starten.")
        cleanup_live_monitoring_context(context)
        return ConversationHandler.END

    if action == "done":
        await query.edit_message_text("Live-Monitoring Bearbeitung beendet.")
        cleanup_live_monitoring_context(context)
        return ConversationHandler.END

    if action == "entries":
        entries: list[dict] = context.user_data.get("live_entry_options", [])
        page = int(context.user_data.get("live_entry_page", 0) or 0)
        await query.edit_message_text(
            f"Aktie waehlen ({len(entries)} Eintraege):",
            reply_markup=build_entry_keyboard(entries, page),
        )
        return STATE_LIVE_ENTRY

    if action == "toggle":
        config = entry.get("live_monitoring", {})
        enable = not parse_enabled(config.get("enabled"))
        current_target = parse_target_price(config.get("target_price"))
        if enable and (current_target is None or current_target <= 0):
            context.user_data["live_enable_after_target"] = True
            await query.edit_message_text("Zielpreis eingeben, z.B. 125.50 oder 125,50:")
            if query.message is not None:
                await query.message.reply_text(
                    "Zielpreis eingeben:",
                    reply_markup=build_text_navigation_keyboard(),
                )
            return STATE_LIVE_TARGET

        try:
            backup_path, _, _ = await update_selected_monitoring_config(context, {"enabled": enable})
        except Exception as exc:
            return await show_monitor_update_error(update, context, exc)
        message = "Monitoring eingeschaltet." if enable else "Monitoring ausgeschaltet."
        return await show_monitor_menu(update, context, action_message=f"{message}\nBackup: {backup_path.name}")

    if action == "target":
        context.user_data["live_enable_after_target"] = False
        await query.edit_message_text("Zielpreis eingeben, z.B. 125.50 oder 125,50:")
        if query.message is not None:
            await query.message.reply_text("Zielpreis eingeben:", reply_markup=build_text_navigation_keyboard())
        return STATE_LIVE_TARGET

    if action == "condition":
        await query.edit_message_text("Bedingung waehlen:", reply_markup=build_condition_keyboard())
        return STATE_LIVE_CONDITION

    if action == "interval":
        await query.edit_message_text("Pruefintervall waehlen:", reply_markup=build_interval_keyboard())
        return STATE_LIVE_INTERVAL

    if action == "price":
        symbol = resolve_monitor_symbol(entry)
        if not symbol:
            return await show_monitor_menu(update, context, price_line="Kein Ticker fuer den Kursabruf gefunden.")
        try:
            if "yfinance" not in context.application.bot_data:
                context.application.bot_data["yfinance"] = load_yfinance()
            yf = context.application.bot_data["yfinance"]
            price, currency = await asyncio.to_thread(fetch_live_price, yf, symbol)
        except Exception as exc:
            append_event(
                "live_monitoring_bot",
                "ERROR",
                f"Kursabruf im Monitoring-Menue fehlgeschlagen fuer {symbol}: {exc}",
                {"symbol": symbol, "name": entry.get("name", "")},
            )
            return await show_monitor_menu(update, context, price_line=f"Kursabruf fehlgeschlagen: {exc}")
        if price is None:
            return await show_monitor_menu(update, context, price_line=f"Kein Kurs fuer {symbol} erhalten.")
        return await show_monitor_menu(update, context, price_line=f"Aktueller Kurs: {price:.2f} {currency or ''}")

    await query.edit_message_text("Ungueltige Auswahl. Bitte /live_monitoring neu starten.")
    cleanup_live_monitoring_context(context)
    return ConversationHandler.END


async def monitoring_target_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    runtime: LiveMonitoringRuntime = context.application.bot_data["runtime"]
    if not await require_access(update, runtime):
        return ConversationHandler.END

    message = update.effective_message
    if message is None:
        return STATE_LIVE_TARGET
    nav = live_text_nav_choice(message)
    if nav == "cancel":
        await message.reply_text("Live-Monitoring Bearbeitung abgebrochen.", reply_markup=ReplyKeyboardRemove())
        cleanup_live_monitoring_context(context)
        return ConversationHandler.END
    if nav == "back":
        context.user_data["live_enable_after_target"] = False
        await message.reply_text("Zurueck zur Regel.", reply_markup=ReplyKeyboardRemove())
        return await show_monitor_menu(update, context)

    value = (message.text or "").strip()
    target_price = parse_target_price(value)
    if target_price is None or target_price <= 0:
        await message.reply_text(
            "Bitte einen Zielpreis groesser 0 eingeben, z.B. 125.50 oder 125,50.",
            reply_markup=build_text_navigation_keyboard(),
        )
        return STATE_LIVE_TARGET

    updates: dict[str, object] = {"target_price": value}
    enable_after_target = bool(context.user_data.pop("live_enable_after_target", False))
    if enable_after_target:
        updates["enabled"] = True

    try:
        backup_path, _, _ = await update_selected_monitoring_config(context, updates)
    except Exception as exc:
        return await show_monitor_update_error(update, context, exc)
    await message.reply_text("Zielpreis gespeichert.", reply_markup=ReplyKeyboardRemove())
    action_message = f"Zielpreis gespeichert.\nBackup: {backup_path.name}"
    if enable_after_target:
        action_message = f"Monitoring eingeschaltet.\n{action_message}"
    return await show_monitor_menu(update, context, action_message=action_message)


async def monitoring_condition(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    runtime: LiveMonitoringRuntime = context.application.bot_data["runtime"]
    if not await require_access(update, runtime):
        return ConversationHandler.END

    query = update.callback_query
    if query is None:
        return STATE_LIVE_CONDITION
    await query.answer()
    action = query.data.removeprefix(CALLBACK_PREFIX_LIVE_CONDITION)

    if action == "cancel":
        await query.edit_message_text("Live-Monitoring Bearbeitung abgebrochen.")
        cleanup_live_monitoring_context(context)
        return ConversationHandler.END
    if action == "back":
        return await show_monitor_menu(update, context)
    if action not in {"above", "below"}:
        await query.edit_message_text("Ungueltige Auswahl. Bitte /live_monitoring neu starten.")
        cleanup_live_monitoring_context(context)
        return ConversationHandler.END

    try:
        backup_path, _, _ = await update_selected_monitoring_config(context, {"condition": action})
    except Exception as exc:
        return await show_monitor_update_error(update, context, exc)
    return await show_monitor_menu(update, context, action_message=f"Bedingung gespeichert.\nBackup: {backup_path.name}")


async def monitoring_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    runtime: LiveMonitoringRuntime = context.application.bot_data["runtime"]
    if not await require_access(update, runtime):
        return ConversationHandler.END

    query = update.callback_query
    if query is None:
        return STATE_LIVE_INTERVAL
    await query.answer()
    action = query.data.removeprefix(CALLBACK_PREFIX_LIVE_INTERVAL)

    if action == "cancel":
        await query.edit_message_text("Live-Monitoring Bearbeitung abgebrochen.")
        cleanup_live_monitoring_context(context)
        return ConversationHandler.END
    if action == "back":
        return await show_monitor_menu(update, context)
    if action == "custom":
        await query.edit_message_text("Eigenes Intervall in Minuten eingeben, z.B. 10:")
        if query.message is not None:
            await query.message.reply_text(
                "Intervall in Minuten eingeben:",
                reply_markup=build_text_navigation_keyboard(),
            )
        return STATE_LIVE_INTERVAL_CUSTOM
    if not action.startswith("set:"):
        await query.edit_message_text("Ungueltige Auswahl. Bitte /live_monitoring neu starten.")
        cleanup_live_monitoring_context(context)
        return ConversationHandler.END

    interval = action.removeprefix("set:")
    try:
        backup_path, _, _ = await update_selected_monitoring_config(context, {"interval_min": interval})
    except Exception as exc:
        return await show_monitor_update_error(update, context, exc)
    return await show_monitor_menu(update, context, action_message=f"Intervall gespeichert.\nBackup: {backup_path.name}")


async def monitoring_interval_custom(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    runtime: LiveMonitoringRuntime = context.application.bot_data["runtime"]
    if not await require_access(update, runtime):
        return ConversationHandler.END

    message = update.effective_message
    if message is None:
        return STATE_LIVE_INTERVAL_CUSTOM
    nav = live_text_nav_choice(message)
    if nav == "cancel":
        await message.reply_text("Live-Monitoring Bearbeitung abgebrochen.", reply_markup=ReplyKeyboardRemove())
        cleanup_live_monitoring_context(context)
        return ConversationHandler.END
    if nav == "back":
        await message.reply_text("Zurueck zur Intervallauswahl.", reply_markup=ReplyKeyboardRemove())
        await message.reply_text("Pruefintervall waehlen:", reply_markup=build_interval_keyboard())
        return STATE_LIVE_INTERVAL

    value = (message.text or "").strip()
    try:
        interval = int(value)
    except ValueError:
        await message.reply_text("Bitte eine ganze Zahl groesser 0 eingeben.", reply_markup=build_text_navigation_keyboard())
        return STATE_LIVE_INTERVAL_CUSTOM
    if interval <= 0:
        await message.reply_text("Bitte eine ganze Zahl groesser 0 eingeben.", reply_markup=build_text_navigation_keyboard())
        return STATE_LIVE_INTERVAL_CUSTOM

    try:
        backup_path, _, _ = await update_selected_monitoring_config(context, {"interval_min": interval})
    except Exception as exc:
        return await show_monitor_update_error(update, context, exc)
    await message.reply_text("Intervall gespeichert.", reply_markup=ReplyKeyboardRemove())
    return await show_monitor_menu(update, context, action_message=f"Intervall gespeichert.\nBackup: {backup_path.name}")


async def monitoring_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if message is not None:
        await message.reply_text("Live-Monitoring Bearbeitung abgebrochen.", reply_markup=ReplyKeyboardRemove())
    cleanup_live_monitoring_context(context)
    return ConversationHandler.END


def build_trigger_message(item: MonitorItem, price: float, currency: str) -> str:
    return (
        "PREIS-TRIGGER\n"
        f"{item.name} ({item.symbol})\n"
        f"Aktueller Preis: {price:.2f} {currency or ''}\n"
        f"Bedingung: {condition_label(item.condition)} {item.target_price:.2f}"
    )


async def run_live_monitoring_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: LiveMonitoringRuntime = context.application.bot_data["runtime"]
    items = load_monitor_items(str(XML_PATH))
    runtime.last_rules_count = len(items)
    trigger_count = 0

    write_live_monitoring_heartbeat(
        "running",
        {
            "active_rules": len(items),
            "target_chat_id": runtime.chat_id,
            "poll_seconds": runtime.poll_seconds,
        },
    )

    if not items:
        runtime.last_trigger_count = 0
        return
    if "yfinance" not in context.application.bot_data:
        context.application.bot_data["yfinance"] = load_yfinance()
    yf = context.application.bot_data["yfinance"]
    now = datetime.now()

    for item in items:
        if not should_check(item, runtime.last_checked, now):
            continue

        runtime.last_checked[item.key] = now
        try:
            price, currency = await asyncio.to_thread(fetch_live_price, yf, item.symbol)
        except Exception as exc:
            append_event(
                "live_monitoring_bot",
                "ERROR",
                f"Preisabruf fehlgeschlagen fuer {item.symbol}: {exc}",
                {"symbol": item.symbol, "name": item.name},
            )
            continue

        if price is None or not condition_matches(price, item):
            continue

        trigger_count += 1
        message = build_trigger_message(item, price, currency)
        print(message.replace("\n", " | "))
        if runtime.chat_id:
            await send_long(context.application.bot, runtime.chat_id, message)

    runtime.last_trigger_count = trigger_count
    write_live_monitoring_heartbeat(
        "running",
        {
            "active_rules": len(items),
            "target_chat_id": runtime.chat_id,
            "poll_seconds": runtime.poll_seconds,
            "last_trigger_count": trigger_count,
        },
    )


async def live_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOGGER.error("Unhandled Live-Monitoring-Bot error", exc_info=context.error)
    append_event("live_monitoring_bot", "ERROR", "Unhandled Live-Monitoring-Bot error", {"error": repr(context.error)})


async def post_init(application: Application) -> None:
    await application.bot.set_my_commands(
        [
            BotCommand("live_monitoring", "Live-Monitoring oeffnen"),
        ]
    )


def build_application() -> Application:
    config = load_config()
    token = config_or_env(config, "live_monitoring_bot_token", "TELEGRAM_LIVE_MONITORING_BOT_TOKEN")
    if not token:
        raise RuntimeError("live_monitoring_bot_token fehlt (JSON oder TELEGRAM_LIVE_MONITORING_BOT_TOKEN).")

    runtime = build_runtime(config)
    application = ApplicationBuilder().token(token).post_init(post_init).build()
    application.bot_data["runtime"] = runtime
    application.add_handler(
        ConversationHandler(
            entry_points=[
                CommandHandler("live_monitoring", live_monitoring_command),
            ],
            states={
                STATE_MAIN_MENU: [
                    CallbackQueryHandler(main_menu_callback, pattern=f"^{CALLBACK_PREFIX_MAIN_MENU}")
                ],
                STATE_LIVE_CATEGORY: [
                    CallbackQueryHandler(monitoring_category, pattern=f"^{CALLBACK_PREFIX_LIVE_CATEGORY}")
                ],
                STATE_LIVE_SUBCATEGORY: [
                    CallbackQueryHandler(monitoring_subcategory, pattern=f"^{CALLBACK_PREFIX_LIVE_SUBCATEGORY}")
                ],
                STATE_LIVE_ENTRY: [
                    CallbackQueryHandler(monitoring_entry, pattern=f"^{CALLBACK_PREFIX_LIVE_ENTRY}")
                ],
                STATE_LIVE_MENU: [
                    CallbackQueryHandler(monitoring_menu, pattern=f"^{CALLBACK_PREFIX_LIVE_MENU}")
                ],
                STATE_LIVE_TARGET: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, monitoring_target_value)
                ],
                STATE_LIVE_CONDITION: [
                    CallbackQueryHandler(monitoring_condition, pattern=f"^{CALLBACK_PREFIX_LIVE_CONDITION}")
                ],
                STATE_LIVE_INTERVAL: [
                    CallbackQueryHandler(monitoring_interval, pattern=f"^{CALLBACK_PREFIX_LIVE_INTERVAL}")
                ],
                STATE_LIVE_INTERVAL_CUSTOM: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, monitoring_interval_custom)
                ],
            },
            fallbacks=[CommandHandler("cancel", monitoring_cancel)],
        )
    )
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("ping", ping_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("rules", rules_command))
    application.add_error_handler(live_error_handler)
    if application.job_queue is None:
        raise RuntimeError("JobQueue ist nicht verfuegbar. Pruefe python-telegram-bot/apscheduler Installation.")
    application.job_queue.run_repeating(
        run_live_monitoring_job,
        interval=runtime.poll_seconds,
        first=5,
        name="live_monitoring_job",
    )
    return application


def main() -> int:
    configure_event_logging("live_monitoring_bot")
    with SingleInstanceLock(
        LIVE_MONITORING_BOT_LOCK_PATH,
        "live_monitoring_bot.py laeuft bereits in einer anderen Instanz. "
        "Beende den vorhandenen Prozess, bevor du den Live-Monitoring-Bot erneut startest.",
    ):
        write_live_monitoring_heartbeat("starting", {"pid": os.getpid()})
        append_event("live_monitoring_bot", "INFO", f"Live-Monitoring-Bot wird gestartet. pid={os.getpid()}")
        application = build_application()
        LOGGER.info("Starting live monitoring bot | pid=%s | executable=%s", os.getpid(), sys.executable)
        try:
            write_live_monitoring_heartbeat("running", {"pid": os.getpid()})
            application.run_polling(allowed_updates=Update.ALL_TYPES)
        finally:
            write_live_monitoring_heartbeat("stopped", {"pid": os.getpid()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
