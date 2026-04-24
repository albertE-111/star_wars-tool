from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from article_fetcher import fetch_article

DEFAULT_GEMINI_API_KEY = ""
DEFAULT_MODEL = "gemma-3-27b-it"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
PROJECT_CONFIG_PATH = Path("config/app_config.json")
LEGACY_CACHE_PATH = Path("gemini_article_summary_cache.json")
CACHE_PATH = Path("gemini_article_summary_cache.sqlite")
CACHE_TABLE_NAME = "article_summary_cache"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ruft einen Artikel ab und erstellt mit Gemini eine kurze Zusammenfassung."
    )
    parser.add_argument("url", nargs="?", help="Link zum Artikel")
    parser.add_argument(
        "--title",
        default="",
        help="Optionaler Artikeltitel",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Gemini-Modell. Standard: {DEFAULT_MODEL}",
    )
    parser.add_argument(
        "--api-key",
        default="",
        help="Optionaler Gemini API Key. Standard: GEMINI_API_KEY.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Gibt das Ergebnis als JSON aus.",
    )
    parser.add_argument(
        "--cache-file",
        default=str(CACHE_PATH),
        help="Pfad zur SQLite-Cache-Datei. Standard: gemini_article_summary_cache.sqlite",
    )
    return parser.parse_args()


def utc_now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def resolve_cache_db_path(cache_file: str) -> Path:
    path = Path(cache_file)
    if path.suffix.lower() == ".json":
        return path.with_suffix(".sqlite")
    return path


def load_legacy_json_cache(cache_file: str) -> dict[str, dict[str, Any]]:
    path = Path(cache_file)
    if not path.exists():
        return {}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    return payload if isinstance(payload, dict) else {}


def open_cache_connection(cache_file: str) -> sqlite3.Connection:
    db_path = resolve_cache_db_path(cache_file)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path), timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {CACHE_TABLE_NAME} (
            cache_key TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            success INTEGER NOT NULL,
            failure_count INTEGER NOT NULL DEFAULT 0,
            output_json TEXT NOT NULL,
            last_error TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        )
        """
    )
    migrate_legacy_cache_if_needed(connection, cache_file)
    return connection


def migrate_legacy_cache_if_needed(connection: sqlite3.Connection, cache_file: str) -> None:
    row = connection.execute(f"SELECT COUNT(*) AS count FROM {CACHE_TABLE_NAME}").fetchone()
    if row is not None and int(row["count"]) > 0:
        return

    db_path = resolve_cache_db_path(cache_file)
    legacy_candidates: list[Path] = []
    source_path = Path(cache_file)
    if source_path.suffix.lower() == ".json":
        legacy_candidates.append(source_path)
    if LEGACY_CACHE_PATH not in legacy_candidates:
        legacy_candidates.append(LEGACY_CACHE_PATH)

    legacy_cache: dict[str, dict[str, Any]] = {}
    for candidate in legacy_candidates:
        if candidate == db_path:
            continue
        legacy_cache = load_legacy_json_cache(str(candidate))
        if legacy_cache:
            break

    if not legacy_cache:
        return

    rows = [
        (
            cache_key,
            str(entry.get("url", "")),
            1 if entry.get("success") else 0,
            int(entry.get("failure_count", 0)),
            json.dumps(entry.get("output", {}), ensure_ascii=True),
            str(entry.get("last_error", "")),
            str(entry.get("updated_at", "")) or utc_now_iso(),
        )
        for cache_key, entry in legacy_cache.items()
    ]
    connection.executemany(
        f"""
        INSERT OR REPLACE INTO {CACHE_TABLE_NAME}
        (cache_key, url, success, failure_count, output_json, last_error, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    connection.commit()


def build_no_information_result(article_url: str, article_title: str, model: str) -> dict[str, Any]:
    return {
        "url": article_url,
        "title": article_title,
        "model": model,
        "summary": "Keine Information verfuegbar.",
        "finish_reason": None,
        "usage_metadata": None,
        "cache_status": "blocked_after_retries",
    }


def build_cache_key(article_url: str, prompt_style: str) -> str:
    if prompt_style == "article":
        return article_url
    return f"{article_url}::{prompt_style}"


def get_cached_result(
    connection: sqlite3.Connection,
    article_url: str,
    article_title: str,
    model: str,
    prompt_style: str = "article",
) -> dict[str, Any] | None:
    row = connection.execute(
        f"""
        SELECT success, failure_count, output_json
        FROM {CACHE_TABLE_NAME}
        WHERE cache_key = ?
        """,
        (build_cache_key(article_url, prompt_style),),
    ).fetchone()
    if row is None:
        return None

    output = {}
    if row["output_json"]:
        try:
            parsed_output = json.loads(row["output_json"])
        except Exception:
            parsed_output = {}
        if isinstance(parsed_output, dict):
            output = parsed_output

    if bool(row["success"]) and output:
        result = dict(output)
        result["cache_status"] = "hit_success"
        return result

    if int(row["failure_count"]) >= 4:
        return build_no_information_result(article_url, article_title, model)

    return None


def update_cache_success(
    connection: sqlite3.Connection,
    article_url: str,
    result: dict[str, Any],
    prompt_style: str = "article",
) -> None:
    connection.execute(
        f"""
        INSERT INTO {CACHE_TABLE_NAME}
        (cache_key, url, success, failure_count, output_json, last_error, updated_at)
        VALUES (?, ?, 1, 0, ?, '', ?)
        ON CONFLICT(cache_key) DO UPDATE SET
            url = excluded.url,
            success = 1,
            failure_count = 0,
            output_json = excluded.output_json,
            last_error = '',
            updated_at = excluded.updated_at
        """,
        (
            build_cache_key(article_url, prompt_style),
            article_url,
            json.dumps(result, ensure_ascii=True),
            utc_now_iso(),
        ),
    )
    connection.commit()


def update_cache_failure(
    connection: sqlite3.Connection,
    article_url: str,
    article_title: str,
    model: str,
    error_message: str,
    prompt_style: str = "article",
) -> dict[str, Any]:
    cache_key = build_cache_key(article_url, prompt_style)
    row = connection.execute(
        f"SELECT failure_count FROM {CACHE_TABLE_NAME} WHERE cache_key = ?",
        (cache_key,),
    ).fetchone()
    failure_count = (int(row["failure_count"]) if row is not None else 0) + 1
    result = {
        "url": article_url,
        "title": article_title,
        "model": model,
        "summary": "Keine Information verfuegbar." if failure_count >= 4 else "",
        "finish_reason": None,
        "usage_metadata": None,
        "cache_status": "failure_recorded",
    }
    connection.execute(
        f"""
        INSERT INTO {CACHE_TABLE_NAME}
        (cache_key, url, success, failure_count, output_json, last_error, updated_at)
        VALUES (?, ?, 0, ?, ?, ?, ?)
        ON CONFLICT(cache_key) DO UPDATE SET
            url = excluded.url,
            success = 0,
            failure_count = excluded.failure_count,
            output_json = excluded.output_json,
            last_error = excluded.last_error,
            updated_at = excluded.updated_at
        """,
        (
            cache_key,
            article_url,
            failure_count,
            json.dumps(result if failure_count >= 4 else {}, ensure_ascii=True),
            error_message,
            utc_now_iso(),
        ),
    )
    connection.commit()
    return result


def summarize_article_with_cache(
    article_url: str,
    article_title: str,
    article_text: str,
    api_key: str,
    model: str,
    cache_file: str = str(CACHE_PATH),
    prompt_style: str = "article",
) -> dict[str, Any]:
    connection = open_cache_connection(cache_file)
    try:
        cached_result = get_cached_result(
            connection,
            article_url,
            article_title,
            model,
            prompt_style=prompt_style,
        )
        if cached_result is not None:
            return cached_result

        try:
            result = summarize_with_gemini(
                article_url=article_url,
                article_title=article_title,
                article_text=article_text,
                api_key=api_key,
                model=model,
                prompt_style=prompt_style,
            )
            result["cache_status"] = "miss_success"
            update_cache_success(connection, article_url, result, prompt_style=prompt_style)
            return result
        except Exception as exc:
            failure_result = update_cache_failure(
                connection=connection,
                article_url=article_url,
                article_title=article_title,
                model=model,
                error_message=str(exc),
                prompt_style=prompt_style,
            )
            if get_failure_count(connection, article_url, prompt_style) >= 4:
                return failure_result
            raise
    finally:
        connection.close()


def get_failure_count(connection: sqlite3.Connection, article_url: str, prompt_style: str = "article") -> int:
    row = connection.execute(
        f"SELECT failure_count FROM {CACHE_TABLE_NAME} WHERE cache_key = ?",
        (build_cache_key(article_url, prompt_style),),
    ).fetchone()
    return int(row["failure_count"]) if row is not None else 0


def load_requests() -> Any:
    try:
        import requests
    except ImportError as exc:
        raise SystemExit(
            "requests ist nicht installiert. Installiere es mit: pip install -r requirements.txt"
        ) from exc
    return requests


def build_prompt(article_title: str, article_text: str, article_url: str, prompt_style: str = "article") -> str:
    if prompt_style == "macro_news":
        return f"""
Du bist ein praeziser Makro-Redakteur.
Fasse den folgenden Artikel auf Deutsch in maximal 2 Saetzen zusammen.

Anforderungen:
- Erklaere kurz: Was ist passiert?
- Erklaere kurz: Wie beeinflusst das die Marktstimmung auf Makro-Ebene?
- Fokus auf Zinsen, Inflation, Liquiditaet, Geopolitik, Risikoappetit, Oel, USD, Angst/Gier.
- Keine Stichpunkte.
- Keine Einleitung, kein Fazit, keine Erfindungen.

Artikelname: {article_title or "Unbekannt"}
Artikel-URL: {article_url}

Artikeltext:
{article_text}
""".strip()

    return f"""
Du bist ein praeziser Redakteur.
Fasse den folgenden Artikel auf Deutsch kurz und sachlich in Stichpunkten zusammen.

Anforderungen:
- Gib 4 bis 6 kurze Stichpunkte aus.
- Jeder Stichpunkt soll mit "- " beginnen.
- Jeder Stichpunkt soll kurz, konkret und gut lesbar sein.
- Nenne die wichtigste Kernaussage zuerst.
- Behalte wichtige Namen, Zahlen, Daten und Orte bei.
- Erfinde keine Fakten.
- Wenn der Artikel unklar oder unvollstaendig ist, sage das knapp.
- Keine Einleitung und kein Fazit ausserhalb der Stichpunkte.

Artikelname: {article_title or "Unbekannt"}
Artikel-URL: {article_url}

Artikeltext:
{article_text}
""".strip()


def truncate_article_text(article_text: str, limit: int = 20000) -> str:
    if len(article_text) <= limit:
        return article_text
    return article_text[:limit].rsplit(" ", 1)[0] + "\n\n[Artikel gekuerzt]"


def extract_response_text(payload: dict[str, Any]) -> tuple[str, str | None]:
    candidates = payload.get("candidates") or []
    collected_texts: list[str] = []
    final_finish_reason: str | None = None

    for candidate in candidates:
        finish_reason = candidate.get("finishReason")
        content = candidate.get("content") or {}
        parts = content.get("parts") or []
        texts = [part.get("text", "") for part in parts if isinstance(part, dict)]
        merged = "\n".join(text for text in texts if text).strip()
        if merged:
            collected_texts.append(merged)
            final_finish_reason = finish_reason

    if collected_texts:
        return "\n".join(collected_texts).strip(), final_finish_reason

    prompt_feedback = payload.get("promptFeedback") or {}
    block_reason = prompt_feedback.get("blockReason")
    if block_reason:
        raise RuntimeError(f"Gemini hat die Antwort blockiert: {block_reason}")

    raise RuntimeError("Gemini hat keine verwendbare Textantwort geliefert.")


def looks_truncated(text: str, finish_reason: str | None) -> bool:
    if finish_reason == "MAX_TOKENS":
        return True
    return bool(text) and text[-1] not in ".!?:"


def build_continuation_prompt(previous_text: str) -> str:
    return (
        "Die vorherige Antwort wurde abgeschnitten. "
        "Fuehre exakt diese deutsche Artikel-Zusammenfassung fort, ohne neu anzufangen, "
        "ohne Wiederholungen und ohne Einleitung. "
        "Beende sie mit einem vollstaendigen letzten Satz.\n\n"
        f"Bisherige Antwort:\n{previous_text}"
    )


def call_gemini(
    requests: Any,
    prompt: str,
    api_key: str,
    model: str,
    max_output_tokens: int,
) -> dict[str, Any]:
    normalized_model = model.strip()
    if normalized_model.startswith("models/"):
        normalized_model = normalized_model[len("models/") :]
    url = GEMINI_API_URL.format(model=normalized_model)
    response = requests.post(
        url,
        headers={
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
        },
        timeout=60,
        json={
            "contents": [
                {
                    "parts": [
                        {
                            "text": prompt,
                        }
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": max_output_tokens,
            },
        },
    )

    if response.status_code >= 400:
        try:
            error_payload = response.json()
        except Exception:
            error_payload = response.text
        raise RuntimeError(f"Gemini API Fehler {response.status_code}: {error_payload}")

    return response.json()


def summarize_with_gemini(
    article_url: str,
    article_title: str,
    article_text: str,
    api_key: str,
    model: str,
    prompt_style: str = "article",
) -> dict[str, Any]:
    requests = load_requests()

    shortened_text = truncate_article_text(article_text)
    prompt = build_prompt(article_title, shortened_text, article_url, prompt_style=prompt_style)
    payload = call_gemini(
        requests=requests,
        prompt=prompt,
        api_key=api_key,
        model=model,
        max_output_tokens=800,
    )
    summary, finish_reason = extract_response_text(payload)

    attempts = 0
    while looks_truncated(summary, finish_reason) and attempts < 3:
        continuation_prompt = build_continuation_prompt(summary)
        continuation_payload = call_gemini(
            requests=requests,
            prompt=continuation_prompt,
            api_key=api_key,
            model=model,
            max_output_tokens=800,
        )
        continuation_text, continuation_finish_reason = extract_response_text(continuation_payload)
        if not continuation_text:
            break

        payload = continuation_payload
        summary = f"{summary.rstrip()} {continuation_text.lstrip()}".strip()
        finish_reason = continuation_finish_reason
        attempts += 1

    if looks_truncated(summary, finish_reason):
        retry_prompt = (
            prompt
            + "\n\nWichtig: Gib eine vollstaendige deutsche Antwort in kurzen Stichpunkten aus. "
              "Jeder Stichpunkt muss mit '- ' beginnen und die Antwort darf nicht mitten im Satz enden."
        )
        retry_payload = call_gemini(
            requests=requests,
            prompt=retry_prompt,
            api_key=api_key,
            model=model,
            max_output_tokens=1600,
        )
        retry_summary, retry_finish_reason = extract_response_text(retry_payload)
        if retry_summary:
            payload = retry_payload
            summary = retry_summary
            finish_reason = retry_finish_reason

    return {
        "url": article_url,
        "title": article_title,
        "model": model,
        "summary": summary,
        "finish_reason": finish_reason,
        "usage_metadata": payload.get("usageMetadata"),
    }


def resolve_api_key(cli_key: str) -> str:
    if cli_key:
        return cli_key

    config_key = load_api_key_from_project_config()
    if config_key:
        return config_key

    return os.getenv("GEMINI_API_KEY") or DEFAULT_GEMINI_API_KEY


def load_api_key_from_project_config() -> str:
    if not PROJECT_CONFIG_PATH.exists():
        return ""
    try:
        payload = json.loads(PROJECT_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("gemini_api_key", "")).strip()


def prompt_multiline_article_text() -> str:
    print("Artikeltext manuell einfuegen. Mit einer einzelnen Zeile END abschliessen:")
    lines: list[str] = []
    while True:
        line = input()
        if line.strip() == "END":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def print_text(data: dict[str, Any]) -> None:
    print(f"Titel: {data.get('title') or 'Unbekannt'}")
    print(f"URL: {data['url']}")
    print(f"Modell: {data['model']}")
    print()
    print("Zusammenfassung:")
    print(data["summary"])


def main() -> int:
    args = parse_args()

    try:
        article_url = args.url or input("Artikel-URL: ").strip()
        if not article_url:
            print("Fehler: URL ist erforderlich.", file=sys.stderr)
            return 1

        article_title = args.title
        if not article_title:
            article_title = input("Artikelname (optional): ").strip()

        api_key = resolve_api_key(args.api_key)
        if not api_key:
            print("Fehler: Gemini API Key fehlt.", file=sys.stderr)
            return 1

        article_data = fetch_article(article_url, article_title)
        article_text = article_data["article_text"]

        if not args.url and len(article_text) < 1200:
            print(
                "Hinweis: Der automatisch extrahierte Artikeltext ist sehr kurz. "
                "Das passiert oft bei dynamischen oder geschuetzten Artikelseiten."
            )
            manual_text = input("Volltext manuell einfuegen? (j/N): ").strip().lower()
            if manual_text in {"j", "ja", "y", "yes"}:
                pasted_text = prompt_multiline_article_text()
                if pasted_text:
                    article_text = pasted_text

        result = summarize_article_with_cache(
            article_url=article_data["url"],
            article_title=article_data["requested_title"] or article_data["page_title"],
            article_text=article_text,
            api_key=api_key,
            model=args.model,
            cache_file=args.cache_file,
        )
    except KeyboardInterrupt:
        print("\nAbgebrochen.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=True, indent=2))
    else:
        print_text(result)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
