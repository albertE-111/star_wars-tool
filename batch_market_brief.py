from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from market_brief import build_global_hot_topics_section, build_global_lead_section, load_entries, resolve_entry_query

XML_PATH = Path("config/stock_categories/stock_categories.xml")
DEFAULT_OUTPUT_PREFIX = "market_brief_results"
DEFAULT_OUTPUT_DIR = Path("market_brief_results")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fuehrt market_brief.py fuer alle XML-Eintraege aus und speichert die Antworten in einer Datei."
    )
    parser.add_argument(
        "--xml",
        default=str(XML_PATH),
        help="Pfad zur XML-Datei. Standard: config/stock_categories/stock_categories.xml",
    )
    parser.add_argument(
        "--category",
        default="",
        help="Optional nur eine bestimmte Kategorie ausfuehren, z. B. Indizes.",
    )
    parser.add_argument(
        "--subcategory",
        default="",
        help="Optional nur eine bestimmte Subkategorie ausfuehren, z. B. Big Tech.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optionaler Pfad zur Ausgabedatei. Standard: automatisch neuer Dateiname mit Zeitstempel.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional nur die ersten N Eintraege nach dem Filtern ausfuehren.",
    )
    parser.add_argument(
        "--with-news-summary",
        action="store_true",
        help="Aktiviert Gemini-News-Zusammenfassungen waehrend des Batch-Laufs.",
    )
    parser.add_argument(
        "--no-news-summary",
        action="store_true",
        help="Deaktiviert Gemini-News-Zusammenfassungen waehrend des Batch-Laufs.",
    )
    return parser.parse_args()


def timestamp_now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def build_default_output_path() -> Path:
    now = datetime.now()
    monthly_dir = DEFAULT_OUTPUT_DIR / now.strftime("%Y-%m")
    monthly_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{DEFAULT_OUTPUT_PREFIX}_{now.strftime('%Y%m%d_%H%M%S')}.txt"
    return monthly_dir / filename


def log(message: str) -> None:
    print(f"[{timestamp_now()}] {message}")


def load_queries(xml_path: str) -> list[dict[str, str]]:
    queries: list[dict[str, str]] = []

    for entry in load_entries(xml_path):
        query = resolve_entry_query(entry)
        queries.append(
            {
                **entry,
                "name": str(entry.get("name", "")).strip(),
                "query": query,
            }
        )

    return queries


def filter_queries(items: list[dict[str, str]], category: str, subcategory: str) -> list[dict[str, str]]:
    filtered = items

    if category.strip():
        category_needle = category.strip().casefold()
        filtered = [item for item in filtered if item["category"].casefold() == category_needle]

    if subcategory.strip():
        subcategory_needle = subcategory.strip().casefold()
        filtered = [item for item in filtered if item["subcategory"].casefold() == subcategory_needle]

    return filtered


def summarize_stderr(stderr: str, limit: int = 400) -> str:
    text = stderr.strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def run_market_brief(
    python_executable: str,
    query: str,
    with_news_summary: bool,
) -> tuple[int, str, str]:
    command = [python_executable, "-X", "utf8", "market_brief.py", query]
    if not with_news_summary:
        command.append("--no-news-summary")

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    return result.returncode, stdout, stderr


def format_result(item: dict[str, str], exit_code: int, stdout: str, stderr: str) -> str:
    lines = [
        "=" * 80,
        f"Kategorie: {item['category']}",
        f"Subkategorie: {item['subcategory']}",
        f"Name: {item['name']}",
        f"Query: {item['query']}",
        f"Exit Code: {exit_code}",
        "",
        "STDOUT:",
        stdout or "<leer>",
        "",
        "STDERR:",
        stderr or "<leer>",
        "",
    ]
    return "\n".join(lines)


def build_summary(results: list[dict[str, str | int]]) -> str:
    success_items = [item for item in results if item["exit_code"] == 0]
    failed_items = [item for item in results if item["exit_code"] != 0]
    missing_ticker_items = [
        item
        for item in failed_items
        if "hat keinen Ticker" in str(item["stderr"])
    ]

    failed_inline = (
        "; ".join(
            f"{item['category']} / {item['subcategory']} / {item['name']}"
            for item in failed_items
        )
        if failed_items
        else "keine"
    )

    lines = [
        "AUSWERTUNG",
        f"1. Erfolgreiche Eintraege (Exit Code 0): {len(success_items)} von {len(results)}",
        f"2. Fehlgeschlagene Eintraege: {len(failed_items)} | {failed_inline}",
        f"3. Fehlende Ticker als Ursache: {len(missing_ticker_items)}",
        "",
    ]

    if failed_items:
        lines.append("Fehlgeschlagene Eintraege:")
        for item in failed_items:
            lines.append(
                f"- {item['category']} / {item['subcategory']} / {item['name']} (Exit Code {item['exit_code']})"
            )
        lines.append("")

    if missing_ticker_items:
        lines.append("Eintraege mit fehlendem Ticker:")
        for item in missing_ticker_items:
            lines.append(f"- {item['category']} / {item['subcategory']} / {item['name']}")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    output_path = Path(args.output) if args.output else build_default_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with_news_summary = True
    if args.no_news_summary:
        with_news_summary = False
    elif args.with_news_summary:
        with_news_summary = True

    try:
        items = filter_queries(load_queries(args.xml), args.category, args.subcategory)
    except Exception as exc:
        print(f"Fehler beim Laden der XML-Datei: {exc}", file=sys.stderr)
        return 1

    if not items:
        print("Keine Eintraege fuer die gewaehlte Kategorie gefunden.", file=sys.stderr)
        return 1
    if args.limit > 0:
        items = items[: args.limit]

    python_executable = sys.executable
    formatted_results: list[str] = []
    result_meta: list[dict[str, str | int]] = []

    log(f"XML geladen: {args.xml}")
    log(f"Ausgabedatei: {output_path}")
    log(f"Kategorie-Filter: {args.category or 'alle'}")
    log(f"Subkategorie-Filter: {args.subcategory or 'alle'}")
    log(f"Anzahl Eintraege: {len(items)}")
    log(f"News-Zusammenfassungen aktiv: {'ja' if with_news_summary else 'nein'}")

    for index, item in enumerate(items, start=1):
        print("-" * 80)
        log(f"[{index}/{len(items)}] Starte: {item['category']} / {item['subcategory']} / {item['name']}")
        log(f"Query: {item['query']}")
        exit_code, stdout, stderr = run_market_brief(
            python_executable=python_executable,
            query=item["query"],
            with_news_summary=with_news_summary,
        )
        log(f"Fertig mit Exit Code: {exit_code}")
        if stderr:
            log("STDERR erkannt.")
            log(f"STDERR Grund: {summarize_stderr(stderr)}")
        formatted_results.append(format_result(item, exit_code, stdout, stderr))
        result_meta.append(
            {
                "category": item["category"],
                "subcategory": item["subcategory"],
                "name": item["name"],
                "exit_code": exit_code,
                "stderr": stderr,
            }
        )

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
    log(f"Ergebnisse gespeichert in: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


