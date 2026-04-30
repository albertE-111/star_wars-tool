from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from bot_monitoring import (
    BOT_EVENT_LOG_PATH,
    HEARTBEAT_INTERVAL_SECONDS,
    SUPPORT_BOT_LOCK_PATH,
    SingleInstanceLock,
    append_event,
    get_live_monitoring_bot_status,
    get_main_bot_status,
    read_events_after,
    read_recent_events,
    restart_live_monitoring_bot_process,
    restart_main_bot_process,
    start_live_monitoring_bot_process,
    start_main_bot_process,
    stop_live_monitoring_bot_process,
    stop_main_bot_process,
)
from telegram import BotCommand, Update
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
LOGGER = logging.getLogger(__name__)
CONFIG_PATH = Path("config/app_config.json")
SUPPORT_ALERT_STATE_PATH = Path("support_bot_alert_state.json")
SUPPORT_MONITOR_JOB = "support_bot_monitor"
MAX_MESSAGE_LENGTH = 3900

MARKET_BRIEF_FUNCTION_HINTS = {
    "auto_market_brief_job",
    "send_batch_market_brief",
    "marketbrief_command",
    "marketbrief_start_command",
}


@dataclass
class SupportRuntime:
    config: dict
    allowed_chat_ids: set[int]
    allowed_user_ids: set[int]
    notify_chat_id: int
    heartbeat_timeout_seconds: int
    alert_state: dict
    last_event_id: int = 0
    last_health_state: str = ""
    last_auto_brief_state: str = ""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


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


def default_alert_state() -> dict:
    return {"next_incident_id": 1, "open_incidents": {}}


def load_alert_state() -> dict:
    if not SUPPORT_ALERT_STATE_PATH.exists():
        return default_alert_state()
    try:
        payload = json.loads(SUPPORT_ALERT_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return default_alert_state()
    if not isinstance(payload, dict):
        return default_alert_state()
    open_incidents = payload.get("open_incidents")
    if not isinstance(open_incidents, dict):
        payload["open_incidents"] = {}
    next_id = payload.get("next_incident_id")
    if not isinstance(next_id, int) or next_id < 1:
        payload["next_incident_id"] = 1
    return payload


def save_alert_state(runtime: SupportRuntime) -> None:
    SUPPORT_ALERT_STATE_PATH.write_text(
        json.dumps(runtime.alert_state, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def parse_id_set(raw: object) -> set[int]:
    values = set()
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


def build_runtime(config: dict) -> SupportRuntime:
    support_config = config.get("support_bot") if isinstance(config.get("support_bot"), dict) else {}
    allowed_chat_ids = parse_id_set(config.get("support_allowed_chat_ids") or config.get("allowed_chat_ids"))
    allowed_user_ids = parse_id_set(config.get("support_allowed_user_ids") or config.get("allowed_user_ids"))
    notify_chat_id = int(support_config.get("notify_chat_id", 0) or 0)
    timeout = int(support_config.get("heartbeat_timeout_seconds", 120) or 120)
    recent_events = read_recent_events(limit=1)
    last_event_id = int(recent_events[-1]["id"]) if recent_events else 0
    return SupportRuntime(
        config=config,
        allowed_chat_ids=allowed_chat_ids,
        allowed_user_ids=allowed_user_ids,
        notify_chat_id=notify_chat_id,
        heartbeat_timeout_seconds=max(timeout, HEARTBEAT_INTERVAL_SECONDS * 2),
        alert_state=load_alert_state(),
        last_event_id=last_event_id,
    )


def is_allowed(update: Update, runtime: SupportRuntime) -> bool:
    if not runtime.allowed_chat_ids and not runtime.allowed_user_ids:
        return True

    chat = update.effective_chat
    user = update.effective_user
    if chat and chat.id in runtime.allowed_chat_ids:
        return True
    if user and user.id in runtime.allowed_user_ids:
        return True
    return False


def support_config(runtime: SupportRuntime) -> dict:
    payload = runtime.config.get("support_bot")
    if not isinstance(payload, dict):
        payload = {}
        runtime.config["support_bot"] = payload
    return payload


def auto_market_brief_config(runtime: SupportRuntime) -> dict:
    payload = runtime.config.get("auto_market_brief")
    if not isinstance(payload, dict):
        payload = {}
        runtime.config["auto_market_brief"] = payload
    return payload


def save_notify_chat_id(runtime: SupportRuntime, chat_id: int) -> None:
    payload = support_config(runtime)
    payload["notify_chat_id"] = chat_id
    payload.setdefault("heartbeat_timeout_seconds", runtime.heartbeat_timeout_seconds)
    save_config(runtime.config)
    runtime.notify_chat_id = chat_id


def get_auto_brief_chat_id(runtime: SupportRuntime) -> int:
    payload = auto_market_brief_config(runtime)
    raw = payload.get("chat_id", 0)
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def save_auto_brief_chat_id(runtime: SupportRuntime, chat_id: int) -> None:
    payload = auto_market_brief_config(runtime)
    payload["chat_id"] = int(chat_id)
    save_config(runtime.config)


def format_auto_brief_target(runtime: SupportRuntime) -> str:
    payload = auto_market_brief_config(runtime)
    chat_id = get_auto_brief_chat_id(runtime)
    enabled = bool(payload.get("enabled", False))
    start_time = str(payload.get("start_time", "08:15")).strip() or "08:15"
    end_time = str(payload.get("end_time", "22:15")).strip() or "22:15"
    interval = payload.get("interval_minutes", 60)
    category = str(payload.get("category", "")).strip() or "alle"
    subcategory = str(payload.get("subcategory", "")).strip() or "alle"
    last_run_at = str(payload.get("last_run_at", "")).strip() or "-"
    return "\n".join(
        [
            "Auto-Market-Brief Zielchat",
            f"Aktiv: {'ja' if enabled else 'nein'}",
            f"Chat-ID: {chat_id or '-'}",
            f"Kategorie: {category}",
            f"Subkategorie: {subcategory}",
            f"Intervall: {interval} Minuten",
            f"Zeitfenster: {start_time} - {end_time}",
            f"Letzter Lauf: {last_run_at}",
        ]
    )


def apply_auto_brief_chat_change(runtime: SupportRuntime, chat_id: int) -> dict[str, str]:
    previous_chat_id = get_auto_brief_chat_id(runtime)
    save_auto_brief_chat_id(runtime, chat_id)
    main_status = get_main_bot_status()
    restart_message = ""
    if main_status.get("running"):
        result = restart_main_bot_process(python_executable=sys.executable)
        restart_message = result["message"]
    else:
        restart_message = (
            "Haupt-Bot laeuft aktuell nicht. Die neue Chat-ID wird beim naechsten Start uebernommen."
        )

    append_event(
        "support_bot",
        "INFO",
        "Auto-Market-Brief-Chat-ID aktualisiert.",
        {
            "old_chat_id": previous_chat_id,
            "new_chat_id": chat_id,
            "main_bot_running_before_change": bool(main_status.get("running")),
        },
    )
    return {
        "previous_chat_id": str(previous_chat_id or "-"),
        "new_chat_id": str(chat_id),
        "restart_message": restart_message,
    }


def get_open_incidents(runtime: SupportRuntime) -> list[dict]:
    open_incidents = runtime.alert_state.get("open_incidents")
    if not isinstance(open_incidents, dict):
        return []
    incidents: list[dict] = []
    for key in sorted(open_incidents.keys(), key=lambda value: int(value) if str(value).isdigit() else 10**9):
        payload = open_incidents.get(key)
        if isinstance(payload, dict) and payload.get("status") == "open":
            incidents.append(payload)
    return incidents


def classify_market_brief_error(event: dict) -> dict | None:
    if str(event.get("level", "")).upper() != "ERROR":
        return None
    if str(event.get("source", "")) != "main_bot":
        return None

    details = event.get("details") if isinstance(event.get("details"), dict) else {}
    message = str(event.get("message", "")).strip()
    message_first_line = message.splitlines()[0].strip() if message else ""
    function_name = str(details.get("function", "")).strip()

    searchable = "\n".join([message.lower(), function_name.lower()])
    if "market brief" not in searchable and "marketbrief" not in searchable and function_name not in MARKET_BRIEF_FUNCTION_HINTS:
        return None

    summary = message_first_line or "Market Brief Fehler"
    fingerprint = f"{function_name}|{summary}".lower().strip()
    return {
        "summary": summary,
        "fingerprint": fingerprint,
        "function": function_name,
        "logger": str(details.get("logger", "")).strip(),
    }


def upsert_incident(runtime: SupportRuntime, event: dict, classification: dict) -> tuple[dict, bool]:
    open_incidents = runtime.alert_state.setdefault("open_incidents", {})
    incident_key: str | None = None
    incident_payload: dict | None = None

    for key, payload in open_incidents.items():
        if not isinstance(payload, dict):
            continue
        if payload.get("status") != "open":
            continue
        if payload.get("fingerprint") == classification["fingerprint"]:
            incident_key = str(key)
            incident_payload = payload
            break

    event_id = int(event.get("id", 0) or 0)
    timestamp = str(event.get("timestamp", now_iso()))

    if incident_payload is None:
        next_id = int(runtime.alert_state.get("next_incident_id", 1) or 1)
        incident_key = str(next_id)
        incident_payload = {
            "incident_id": next_id,
            "status": "open",
            "fingerprint": classification["fingerprint"],
            "summary": classification["summary"],
            "function": classification.get("function", ""),
            "logger": classification.get("logger", ""),
            "first_event_id": event_id,
            "last_event_id": event_id,
            "first_seen": timestamp,
            "last_seen": timestamp,
            "occurrences": 1,
        }
        open_incidents[incident_key] = incident_payload
        runtime.alert_state["next_incident_id"] = next_id + 1
        save_alert_state(runtime)
        return incident_payload, True

    incident_payload["last_event_id"] = event_id
    incident_payload["last_seen"] = timestamp
    incident_payload["occurrences"] = int(incident_payload.get("occurrences", 1) or 1) + 1
    open_incidents[incident_key] = incident_payload
    save_alert_state(runtime)
    return incident_payload, False


def format_status(runtime: SupportRuntime) -> str:
    status = get_main_bot_status()
    live_status = get_live_monitoring_bot_status()
    heartbeat = status.get("heartbeat") or {}
    heartbeat_age = status.get("heartbeat_age_seconds")
    heartbeat_details = heartbeat.get("details") if isinstance(heartbeat.get("details"), dict) else {}
    state = "laeuft" if status["running"] else "gestoppt"
    live_heartbeat = live_status.get("heartbeat") or {}
    live_heartbeat_age = live_status.get("heartbeat_age_seconds")
    live_details = live_heartbeat.get("details") if isinstance(live_heartbeat.get("details"), dict) else {}
    live_state = "laeuft" if live_status["running"] else "gestoppt"
    auto_brief_enabled = heartbeat_details.get("auto_brief_enabled")
    if auto_brief_enabled is True:
        auto_brief_state = "aktiv"
    elif auto_brief_enabled is False:
        auto_brief_state = "deaktiviert"
    else:
        auto_brief_state = "-"
    lines = [
        f"Haupt-Bot Status: {state}",
        f"PID: {status.get('pid') or '-'}",
        f"Lock-Datei: {'ja' if status.get('lock_exists') else 'nein'}",
        f"Heartbeat Status: {heartbeat.get('status', '-')}",
        f"Heartbeat Zeit: {heartbeat.get('updated_at', '-')}",
        f"Heartbeat Alter: {heartbeat_age:.1f}s" if heartbeat_age is not None else "Heartbeat Alter: -",
        f"Heartbeat Timeout: {runtime.heartbeat_timeout_seconds}s",
        f"Auto Market Brief: {auto_brief_state}",
        f"Auto Market Brief letzter Lauf: {heartbeat_details.get('auto_brief_last_run_at', '-') or '-'}",
        f"Auto Market Brief Ziel-Chat-ID: {get_auto_brief_chat_id(runtime) or '-'}",
        "",
        f"Live-Monitoring-Bot Status: {live_state}",
        f"Live-Monitoring PID: {live_status.get('pid') or '-'}",
        f"Live-Monitoring Lock-Datei: {'ja' if live_status.get('lock_exists') else 'nein'}",
        f"Live-Monitoring Heartbeat: {live_heartbeat.get('status', '-')}",
        f"Live-Monitoring Heartbeat Alter: {live_heartbeat_age:.1f}s" if live_heartbeat_age is not None else "Live-Monitoring Heartbeat Alter: -",
        f"Live-Monitoring aktive Regeln: {live_details.get('active_rules', '-')}",
        f"Live-Monitoring Ziel-Chat-ID: {live_details.get('target_chat_id', '-') or '-'}",
        "",
        f"Event-Datei: {BOT_EVENT_LOG_PATH.name}",
        f"Benachrichtigungs-Chat: {runtime.notify_chat_id or '-'}",
        f"Offene Market-Brief-Fehler: {len(get_open_incidents(runtime))}",
    ]
    return "\n".join(lines)


def format_live_monitoring_status() -> str:
    status = get_live_monitoring_bot_status()
    heartbeat = status.get("heartbeat") or {}
    heartbeat_age = status.get("heartbeat_age_seconds")
    details = heartbeat.get("details") if isinstance(heartbeat.get("details"), dict) else {}
    return "\n".join(
        [
            f"Live-Monitoring-Bot Status: {'laeuft' if status.get('running') else 'gestoppt'}",
            f"PID: {status.get('pid') or '-'}",
            f"Lock-Datei: {'ja' if status.get('lock_exists') else 'nein'}",
            f"Heartbeat Status: {heartbeat.get('status', '-')}",
            f"Heartbeat Zeit: {heartbeat.get('updated_at', '-')}",
            f"Heartbeat Alter: {heartbeat_age:.1f}s" if heartbeat_age is not None else "Heartbeat Alter: -",
            f"Aktive Regeln: {details.get('active_rules', '-')}",
            f"Ziel-Chat-ID: {details.get('target_chat_id', '-') or '-'}",
            f"Poll-Intervall: {details.get('poll_seconds', '-')}",
            f"Letzte Trigger: {details.get('last_trigger_count', '-')}",
        ]
    )


def format_event(event: dict) -> str:
    details = event.get("details") if isinstance(event.get("details"), dict) else {}
    lines = [
        f"[{event.get('timestamp', '-')}] {event.get('source', '-')} {event.get('level', '-')}",
        str(event.get("message", "")),
    ]
    if details.get("logger"):
        lines.append(f"Logger: {details['logger']}")
    if details.get("traceback"):
        lines.append(str(details["traceback"])[:1500])
    return "\n".join(lines)


def format_incident(incident: dict) -> str:
    return "\n".join(
        [
            f"ID: {incident.get('incident_id', '-')}",
            f"Status: {incident.get('status', '-')}",
            f"Zusammenfassung: {incident.get('summary', '-')}",
            f"Funktion: {incident.get('function', '-')}",
            f"Auftreten: {incident.get('occurrences', 1)}",
            f"Erstmalig: {incident.get('first_seen', '-')}",
            f"Zuletzt: {incident.get('last_seen', '-')}",
        ]
    )


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


async def reply_long(message, text: str) -> None:
    for chunk in split_message(text):
        await message.reply_text(chunk)


async def send_long(bot, chat_id: int, text: str) -> None:
    for chunk in split_message(text):
        await bot.send_message(chat_id=chat_id, text=chunk)


async def require_access(update: Update, runtime: SupportRuntime) -> bool:
    if is_allowed(update, runtime):
        return True
    message = update.effective_message
    if message is not None:
        await message.reply_text("Dieser Chat oder User ist fuer den Support-Bot nicht freigeschaltet.")
    return False


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: SupportRuntime = context.application.bot_data["runtime"]
    if not await require_access(update, runtime):
        return
    chat = update.effective_chat
    if chat is not None:
        save_notify_chat_id(runtime, chat.id)
    message = update.effective_message
    if message is not None:
        await message.reply_text(
            "Support-Bot aktiv.\n"
            "Befehle:\n"
            "/status - Haupt-Bot Status und Heartbeat\n"
            "/autobrief_chat - Auto-Market-Brief Ziel anzeigen\n"
            "/autobrief_chat_here - diesen Chat als Auto-Brief-Ziel setzen\n"
            "/autobrief_chat_set <chat_id> - Auto-Brief-Ziel manuell setzen\n"
            "/main_on - Haupt-Bot starten\n"
            "/main_off - Haupt-Bot stoppen\n"
            "/main_restart - Haupt-Bot neu starten\n"
            "/live_status - Live-Monitoring-Bot Status\n"
            "/live_on - Live-Monitoring-Bot starten\n"
            "/live_off - Live-Monitoring-Bot stoppen\n"
            "/live_restart - Live-Monitoring-Bot neu starten\n"
            "/errors - letzte Fehlermeldungen anzeigen\n"
            "/open_errors - offene Market-Brief-Fehler\n"
            "/resolve_error <id> - Fehler als geloest markieren\n"
            "/ping - Support-Bot testen"
        )


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: SupportRuntime = context.application.bot_data["runtime"]
    if not await require_access(update, runtime):
        return
    message = update.effective_message
    if message is not None:
        await message.reply_text("pong")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: SupportRuntime = context.application.bot_data["runtime"]
    if not await require_access(update, runtime):
        return
    message = update.effective_message
    if message is not None:
        await message.reply_text(format_status(runtime))


async def errors_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: SupportRuntime = context.application.bot_data["runtime"]
    if not await require_access(update, runtime):
        return
    events = read_recent_events(limit=10, min_level="ERROR")
    message = update.effective_message
    if message is None:
        return
    if not events:
        await message.reply_text("Keine Fehlermeldungen vorhanden.")
        return
    for event in events:
        await reply_long(message, format_event(event))


async def open_errors_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: SupportRuntime = context.application.bot_data["runtime"]
    if not await require_access(update, runtime):
        return
    message = update.effective_message
    if message is None:
        return
    incidents = get_open_incidents(runtime)
    if not incidents:
        await message.reply_text("Keine offenen Market-Brief-Fehler.")
        return

    header = f"Offene Market-Brief-Fehler: {len(incidents)}"
    body = "\n\n".join(format_incident(incident) for incident in incidents[:10])
    await reply_long(message, f"{header}\n\n{body}")


async def resolve_error_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: SupportRuntime = context.application.bot_data["runtime"]
    if not await require_access(update, runtime):
        return
    message = update.effective_message
    if message is None:
        return

    if not context.args:
        await message.reply_text("Bitte eine Incident-ID angeben, z. B. /resolve_error 3")
        return

    try:
        incident_id = int(str(context.args[0]).strip())
    except ValueError:
        await message.reply_text("Incident-ID muss eine Zahl sein, z. B. /resolve_error 3")
        return

    open_incidents = runtime.alert_state.setdefault("open_incidents", {})
    payload = open_incidents.get(str(incident_id))
    if not isinstance(payload, dict) or payload.get("status") != "open":
        await message.reply_text(f"Kein offener Fehler mit ID {incident_id} gefunden.")
        return

    user = update.effective_user
    payload["status"] = "resolved"
    payload["resolved_at"] = now_iso()
    payload["resolved_by"] = int(user.id) if user is not None else 0
    open_incidents[str(incident_id)] = payload
    save_alert_state(runtime)
    append_event(
        "support_bot",
        "INFO",
        f"Market-Brief-Fehler {incident_id} als geloest markiert.",
        {"incident_id": incident_id, "summary": payload.get("summary", "")},
    )
    await message.reply_text(f"Fehler-ID {incident_id} wurde als geloest markiert.")


async def main_on_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: SupportRuntime = context.application.bot_data["runtime"]
    if not await require_access(update, runtime):
        return
    result = start_main_bot_process(python_executable=sys.executable)
    append_event("support_bot", "INFO", result["message"])
    message = update.effective_message
    if message is not None:
        await message.reply_text(result["message"])


async def main_off_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: SupportRuntime = context.application.bot_data["runtime"]
    if not await require_access(update, runtime):
        return
    result = stop_main_bot_process()
    append_event("support_bot", "INFO", result["message"])
    message = update.effective_message
    if message is not None:
        await message.reply_text(result["message"])


async def main_restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: SupportRuntime = context.application.bot_data["runtime"]
    if not await require_access(update, runtime):
        return
    result = restart_main_bot_process(python_executable=sys.executable)
    append_event("support_bot", "INFO", result["message"])
    message = update.effective_message
    if message is not None:
        await message.reply_text(result["message"])


async def live_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: SupportRuntime = context.application.bot_data["runtime"]
    if not await require_access(update, runtime):
        return
    message = update.effective_message
    if message is not None:
        await message.reply_text(format_live_monitoring_status())


async def live_on_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: SupportRuntime = context.application.bot_data["runtime"]
    if not await require_access(update, runtime):
        return
    result = start_live_monitoring_bot_process(python_executable=sys.executable)
    append_event("support_bot", "INFO", result["message"])
    message = update.effective_message
    if message is not None:
        await message.reply_text(result["message"])


async def live_off_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: SupportRuntime = context.application.bot_data["runtime"]
    if not await require_access(update, runtime):
        return
    result = stop_live_monitoring_bot_process()
    append_event("support_bot", "INFO", result["message"])
    message = update.effective_message
    if message is not None:
        await message.reply_text(result["message"])


async def live_restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: SupportRuntime = context.application.bot_data["runtime"]
    if not await require_access(update, runtime):
        return
    result = restart_live_monitoring_bot_process(python_executable=sys.executable)
    append_event("support_bot", "INFO", result["message"])
    message = update.effective_message
    if message is not None:
        await message.reply_text(result["message"])


async def autobrief_chat_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: SupportRuntime = context.application.bot_data["runtime"]
    if not await require_access(update, runtime):
        return
    message = update.effective_message
    if message is None:
        return
    help_text = "\n".join(
        [
            format_auto_brief_target(runtime),
            "",
            "Befehle:",
            "/autobrief_chat - aktuelles Ziel anzeigen",
            "/autobrief_chat_here - diesen Support-Bot-Chat als Ziel setzen",
            "/autobrief_chat_set <chat_id> - Ziel manuell auf eine Chat-ID setzen",
            "",
            "Hinweis: Nach einer Aenderung wird der Haupt-Bot automatisch neu gestartet, damit die neue ID sofort aktiv ist.",
        ]
    )
    await reply_long(message, help_text)


async def autobrief_chat_here_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: SupportRuntime = context.application.bot_data["runtime"]
    if not await require_access(update, runtime):
        return
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return

    result = apply_auto_brief_chat_change(runtime, chat.id)
    title = chat.title or ""
    target_label = title if title else (chat.username or chat.full_name or str(chat.id))
    response = "\n".join(
        [
            "Auto-Market-Brief Zielchat wurde aktualisiert.",
            f"Vorherige Chat-ID: {result['previous_chat_id']}",
            f"Neue Chat-ID: {result['new_chat_id']}",
            f"Zielchat: {target_label}",
            "",
            result["restart_message"],
        ]
    )
    await reply_long(message, response)


async def autobrief_chat_set_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: SupportRuntime = context.application.bot_data["runtime"]
    if not await require_access(update, runtime):
        return
    message = update.effective_message
    if message is None:
        return
    if not context.args:
        await message.reply_text("Bitte eine Chat-ID angeben, z. B. /autobrief_chat_set 123456789")
        return

    raw = str(context.args[0]).strip()
    try:
        chat_id = int(raw)
    except ValueError:
        await message.reply_text("Chat-ID muss eine Zahl sein, z. B. /autobrief_chat_set 123456789")
        return

    result = apply_auto_brief_chat_change(runtime, chat_id)
    response = "\n".join(
        [
            "Auto-Market-Brief Zielchat wurde aktualisiert.",
            f"Vorherige Chat-ID: {result['previous_chat_id']}",
            f"Neue Chat-ID: {result['new_chat_id']}",
            "",
            result["restart_message"],
            "",
            "Wichtig: Stelle sicher, dass der Haupt-Bot diesem Chat bereits schreiben darf. Bei privaten Chats muss der Nutzer den Haupt-Bot vorher mit /start aktiviert haben.",
        ]
    )
    await reply_long(message, response)


async def support_monitor_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: SupportRuntime = context.application.bot_data["runtime"]
    if not runtime.notify_chat_id:
        return

    new_events = read_events_after(runtime.last_event_id, limit=20)
    for event in new_events:
        runtime.last_event_id = max(runtime.last_event_id, int(event.get("id", 0)))
        classification = classify_market_brief_error(event)
        if classification is None:
            continue
        incident, is_new = upsert_incident(runtime, event, classification)
        if not is_new:
            continue
        alert_text = "\n".join(
            [
                "Alarm: Neuer Market-Brief-Fehler erkannt.",
                format_incident(incident),
                f"Aktion: /resolve_error {incident.get('incident_id', '-')}",
            ]
        )
        await send_long(context.application.bot, runtime.notify_chat_id, alert_text)

    status = get_main_bot_status()
    heartbeat_age = status.get("heartbeat_age_seconds")
    if not status["running"]:
        current_state = "stopped"
    elif heartbeat_age is not None and heartbeat_age > runtime.heartbeat_timeout_seconds:
        current_state = "stale"
    else:
        current_state = "healthy"

    if current_state == runtime.last_health_state:
        return

    runtime.last_health_state = current_state
    if current_state == "stopped":
        await context.application.bot.send_message(
            chat_id=runtime.notify_chat_id,
            text="Alarm: Haupt-Bot laeuft nicht.",
        )
    elif current_state == "stale":
        await context.application.bot.send_message(
            chat_id=runtime.notify_chat_id,
            text=(
                "Alarm: Heartbeat des Haupt-Bots ist veraltet.\n"
                f"Letztes Update vor {heartbeat_age:.1f} Sekunden."
            ),
        )
    elif current_state == "healthy":
        await context.application.bot.send_message(
            chat_id=runtime.notify_chat_id,
            text="Heartbeat ok: Haupt-Bot ist erreichbar.",
        )

    if current_state != "healthy":
        runtime.last_auto_brief_state = ""
        return

    heartbeat = status.get("heartbeat") or {}
    heartbeat_details = heartbeat.get("details") if isinstance(heartbeat.get("details"), dict) else {}
    auto_brief_enabled = heartbeat_details.get("auto_brief_enabled")
    if auto_brief_enabled is True:
        auto_brief_state = "enabled"
    elif auto_brief_enabled is False:
        auto_brief_state = "disabled"
    else:
        auto_brief_state = "unknown"

    if auto_brief_state == runtime.last_auto_brief_state:
        return

    runtime.last_auto_brief_state = auto_brief_state
    if auto_brief_state == "disabled":
        await context.application.bot.send_message(
            chat_id=runtime.notify_chat_id,
            text=(
                "Alarm: Auto Market Brief ist deaktiviert.\n"
                "Aktiviere ihn im Haupt-Bot, wenn wieder automatische Market-Brief-Laeufe erfolgen sollen."
            ),
        )
    elif auto_brief_state == "enabled":
        await context.application.bot.send_message(
            chat_id=runtime.notify_chat_id,
            text="Info: Auto Market Brief ist aktiv.",
        )


async def support_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOGGER.error("Unhandled Support-Bot error", exc_info=context.error)
    append_event("support_bot", "ERROR", "Unhandled Support-Bot error", {"error": repr(context.error)})


async def post_init(application: Application) -> None:
    await application.bot.set_my_commands(
        [
            BotCommand("status", "Status und Heartbeat des Haupt-Bots"),
            BotCommand("autobrief_chat", "Auto-Market-Brief Zielchat anzeigen"),
            BotCommand("autobrief_chat_here", "Diesen Chat als Auto-Brief-Ziel setzen"),
            BotCommand("main_on", "Haupt-Bot starten"),
            BotCommand("main_off", "Haupt-Bot stoppen"),
            BotCommand("main_restart", "Haupt-Bot neu starten"),
            BotCommand("live_status", "Live-Monitoring-Bot Status"),
            BotCommand("live_on", "Live-Monitoring-Bot starten"),
            BotCommand("live_off", "Live-Monitoring-Bot stoppen"),
            BotCommand("live_restart", "Live-Monitoring-Bot neu starten"),
            BotCommand("errors", "Letzte Fehlermeldungen"),
            BotCommand("open_errors", "Offene Market-Brief-Fehler"),
            BotCommand("resolve_error", "Market-Brief-Fehler als geloest markieren"),
            BotCommand("start", "Support-Bot aktivieren"),
        ]
    )


def build_application() -> Application:
    config = load_config()
    token = config_or_env(config, "support_bot_token", "TELEGRAM_SUPPORT_BOT_TOKEN")
    if not token:
        raise RuntimeError("support_bot_token fehlt (JSON oder TELEGRAM_SUPPORT_BOT_TOKEN).")

    runtime = build_runtime(config)
    application = ApplicationBuilder().token(token).post_init(post_init).build()
    application.bot_data["runtime"] = runtime
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("ping", ping_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("autobrief_chat", autobrief_chat_command))
    application.add_handler(CommandHandler("autobrief_chat_here", autobrief_chat_here_command))
    application.add_handler(CommandHandler("autobrief_chat_set", autobrief_chat_set_command))
    application.add_handler(CommandHandler("errors", errors_command))
    application.add_handler(CommandHandler("open_errors", open_errors_command))
    application.add_handler(CommandHandler("resolve_error", resolve_error_command))
    application.add_handler(CommandHandler("main_on", main_on_command))
    application.add_handler(CommandHandler("main_off", main_off_command))
    application.add_handler(CommandHandler("main_restart", main_restart_command))
    application.add_handler(CommandHandler("live_status", live_status_command))
    application.add_handler(CommandHandler("live_on", live_on_command))
    application.add_handler(CommandHandler("live_off", live_off_command))
    application.add_handler(CommandHandler("live_restart", live_restart_command))
    application.add_error_handler(support_error_handler)
    if application.job_queue is not None:
        application.job_queue.run_repeating(
            support_monitor_job,
            interval=HEARTBEAT_INTERVAL_SECONDS,
            first=5,
            name=SUPPORT_MONITOR_JOB,
        )
    return application


def main() -> int:
    with SingleInstanceLock(
        SUPPORT_BOT_LOCK_PATH,
        "support_bot.py laeuft bereits in einer anderen Instanz. "
        "Beende den vorhandenen Prozess, bevor du den Support-Bot erneut startest.",
    ):
        append_event("support_bot", "INFO", f"Support-Bot wird gestartet. pid={os.getpid()}")
        application = build_application()
        LOGGER.info("Starting support bot | pid=%s | executable=%s", os.getpid(), sys.executable)
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
