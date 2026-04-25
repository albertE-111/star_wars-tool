from __future__ import annotations

import json
import sys
from pathlib import Path


CONFIG_PATH = Path("config/app_config.json")

DEFAULT_CONFIG = {
    "bot_token": "",
    "support_bot_token": "",
    "gemini_api_key": "",
    "allowed_user_ids": "",
    "allowed_chat_ids": "",
    "gemini_model": "gemma-3-27b-it",
    "support_bot": {
        "notify_chat_id": 0,
        "heartbeat_timeout_seconds": 120,
    },
    "auto_market_brief": {
        "enabled": True,
        "start_time": "08:15",
        "end_time": "22:15",
        "interval_minutes": 60,
        "category": "",
        "subcategory": "",
        "with_news_summary": True,
        "send_detailed_result_message": False,
        "chat_id": 0,
        "last_run_at": "",
    },
}


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Konfigurationsdatei ist ungueltig: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Konfigurationsdatei muss ein JSON-Objekt enthalten.")
    return payload


def merge_defaults(config: dict) -> dict:
    merged = dict(config)

    support_bot = merged.get("support_bot")
    if not isinstance(support_bot, dict):
        support_bot = {}
    merged["support_bot"] = {
        "notify_chat_id": support_bot.get("notify_chat_id", DEFAULT_CONFIG["support_bot"]["notify_chat_id"]),
        "heartbeat_timeout_seconds": support_bot.get(
            "heartbeat_timeout_seconds",
            DEFAULT_CONFIG["support_bot"]["heartbeat_timeout_seconds"],
        ),
    }

    auto_market_brief = merged.get("auto_market_brief")
    if not isinstance(auto_market_brief, dict):
        auto_market_brief = {}
    merged["auto_market_brief"] = {
        "enabled": auto_market_brief.get("enabled", DEFAULT_CONFIG["auto_market_brief"]["enabled"]),
        "start_time": auto_market_brief.get("start_time", DEFAULT_CONFIG["auto_market_brief"]["start_time"]),
        "end_time": auto_market_brief.get("end_time", DEFAULT_CONFIG["auto_market_brief"]["end_time"]),
        "interval_minutes": auto_market_brief.get(
            "interval_minutes",
            DEFAULT_CONFIG["auto_market_brief"]["interval_minutes"],
        ),
        "category": auto_market_brief.get("category", DEFAULT_CONFIG["auto_market_brief"]["category"]),
        "subcategory": auto_market_brief.get("subcategory", DEFAULT_CONFIG["auto_market_brief"]["subcategory"]),
        "with_news_summary": auto_market_brief.get(
            "with_news_summary",
            DEFAULT_CONFIG["auto_market_brief"]["with_news_summary"],
        ),
        "send_detailed_result_message": auto_market_brief.get(
            "send_detailed_result_message",
            DEFAULT_CONFIG["auto_market_brief"]["send_detailed_result_message"],
        ),
        "chat_id": auto_market_brief.get("chat_id", DEFAULT_CONFIG["auto_market_brief"]["chat_id"]),
        "last_run_at": auto_market_brief.get("last_run_at", DEFAULT_CONFIG["auto_market_brief"]["last_run_at"]),
    }

    for key in ("bot_token", "support_bot_token", "gemini_api_key", "allowed_user_ids", "allowed_chat_ids", "gemini_model"):
        merged[key] = merged.get(key, DEFAULT_CONFIG[key])
    return merged


def prompt_required(label: str, current: object = "") -> str:
    current_text = str(current).strip()
    while True:
        suffix = " [vorhanden, Enter zum Behalten]" if current_text else ""
        value = input(f"{label}:{suffix} ").strip()
        if value:
            return value
        if current_text:
            return current_text
        print("Wert darf nicht leer sein.")


def prompt_optional(label: str, current: object, default: object) -> str:
    current_text = str(current).strip()
    default_text = str(default).strip()
    shown_default = current_text or default_text
    value = input(f"{label} (Enter fuer {shown_default}): ").strip()
    if value:
        return value
    if current_text:
        return current_text
    return default_text


def prompt_bool(label: str, current: object, default: bool) -> bool:
    current_text = ""
    if isinstance(current, bool):
        current_text = "true" if current else "false"
    elif str(current).strip():
        current_text = str(current).strip().lower()
    default_text = current_text or ("true" if default else "false")
    while True:
        value = input(f"{label} (true/false, Enter fuer {default_text}): ").strip().lower()
        if not value:
            value = default_text
        if value in {"true", "false"}:
            return value == "true"
        print("Bitte true oder false eingeben.")


def prompt_int(label: str, current: object, default: int) -> int:
    current_text = str(current).strip() if str(current).strip() not in {"", "0"} else ""
    default_text = current_text or str(default)
    while True:
        value = input(f"{label} (Enter fuer {default_text}): ").strip()
        if not value:
            value = default_text
        try:
            return int(value)
        except ValueError:
            print("Bitte eine ganze Zahl eingeben.")


def save_config(config: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=True, indent=2), encoding="utf-8")


def main() -> int:
    try:
        config = merge_defaults(load_config())
    except RuntimeError as exc:
        print(f"Fehler: {exc}")
        return 1

    needs_prompt = not CONFIG_PATH.exists()
    required_checks = [
        ("bot_token", config["bot_token"]),
        ("support_bot_token", config["support_bot_token"]),
        ("gemini_api_key", config["gemini_api_key"]),
        ("allowed_user_ids", config["allowed_user_ids"]),
        ("support_bot.notify_chat_id", config["support_bot"]["notify_chat_id"]),
        ("auto_market_brief.chat_id", config["auto_market_brief"]["chat_id"]),
    ]
    if any(not str(value).strip() or str(value).strip() == "0" for _, value in required_checks):
        needs_prompt = True

    if not needs_prompt:
        return 0

    print("Es fehlen Konfigurationswerte. Die fehlenden oder leeren Felder werden jetzt abgefragt.")
    print()

    config["bot_token"] = prompt_required("Bot-Token", config["bot_token"])
    config["support_bot_token"] = prompt_required("Support-Bot-Token", config["support_bot_token"])
    config["gemini_api_key"] = prompt_required("Gemini-API-Key", config["gemini_api_key"])
    config["allowed_user_ids"] = prompt_required("Allowed User IDs", config["allowed_user_ids"])
    config["allowed_chat_ids"] = prompt_optional("Allowed Chat IDs", config["allowed_chat_ids"], "")
    config["gemini_model"] = prompt_optional("Gemini-Modell", config["gemini_model"], "gemma-3-27b-it")

    support_bot = config["support_bot"]
    support_bot["notify_chat_id"] = prompt_int(
        "Support Notify Chat ID",
        support_bot["notify_chat_id"],
        0,
    )
    support_bot["heartbeat_timeout_seconds"] = prompt_int(
        "Heartbeat Timeout in Sekunden",
        support_bot["heartbeat_timeout_seconds"],
        120,
    )

    auto_market_brief = config["auto_market_brief"]
    auto_market_brief["enabled"] = prompt_bool(
        "Auto Market Brief aktivieren?",
        auto_market_brief["enabled"],
        True,
    )
    auto_market_brief["start_time"] = prompt_optional(
        "Auto Market Brief Startzeit",
        auto_market_brief["start_time"],
        "08:15",
    )
    auto_market_brief["end_time"] = prompt_optional(
        "Auto Market Brief Endzeit",
        auto_market_brief["end_time"],
        "22:15",
    )
    auto_market_brief["interval_minutes"] = prompt_int(
        "Auto Market Brief Intervall Minuten",
        auto_market_brief["interval_minutes"],
        60,
    )
    auto_market_brief["category"] = prompt_optional(
        "Auto Market Brief Kategorie",
        auto_market_brief["category"],
        "",
    )
    auto_market_brief["subcategory"] = prompt_optional(
        "Auto Market Brief Unterkategorie",
        auto_market_brief["subcategory"],
        "",
    )
    auto_market_brief["with_news_summary"] = prompt_bool(
        "News Summary aktivieren?",
        auto_market_brief["with_news_summary"],
        True,
    )
    auto_market_brief["send_detailed_result_message"] = prompt_bool(
        "Detailed Result Message senden?",
        auto_market_brief["send_detailed_result_message"],
        False,
    )
    auto_market_brief["chat_id"] = prompt_int(
        "Auto Market Brief Chat ID",
        auto_market_brief["chat_id"],
        0,
    )

    save_config(config)
    print()
    print("config/app_config.json wurde aktualisiert.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
