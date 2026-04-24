from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

XML_PATH = Path("config/stock_categories/stock_categories.xml")


def load_categories() -> list[str]:
    root = ElementTree.parse(XML_PATH).getroot()
    categories = []
    for category in root.findall("category"):
        name = category.attrib.get("name", "").strip()
        if name:
            categories.append(name)
    return categories


def load_subcategories() -> dict[str, list[str]]:
    root = ElementTree.parse(XML_PATH).getroot()
    mapping: dict[str, list[str]] = {}
    for category in root.findall("category"):
        category_name = category.attrib.get("name", "").strip()
        if not category_name:
            continue
        mapping[category_name] = []
        for subcategory in category.findall("subcategory"):
            sub_name = subcategory.attrib.get("name", "").strip()
            if sub_name:
                mapping[category_name].append(sub_name)
    return mapping


def prompt_category(categories: list[str]) -> str:
    print("Kategorie waehlen:")
    print("0. Alle")
    for index, category in enumerate(categories, start=1):
        print(f"{index}. {category}")

    while True:
        raw = input("Auswahl: ").strip()
        if not raw:
            return ""
        if raw.isdigit():
            selected = int(raw)
            if selected == 0:
                return ""
            if 1 <= selected <= len(categories):
                return categories[selected - 1]
        print("Bitte eine gueltige Zahl eingeben.")


def prompt_subcategory(category: str, subcategories_by_category: dict[str, list[str]]) -> str:
    if category:
        options = subcategories_by_category.get(category, [])
    else:
        seen = []
        for values in subcategories_by_category.values():
            for value in values:
                if value not in seen:
                    seen.append(value)
        options = seen

    if not options:
        return ""

    print("Subkategorie waehlen:")
    print("0. Alle")
    for index, subcategory in enumerate(options, start=1):
        print(f"{index}. {subcategory}")

    while True:
        raw = input("Auswahl: ").strip()
        if not raw:
            return ""
        if raw.isdigit():
            selected = int(raw)
            if selected == 0:
                return ""
            if 1 <= selected <= len(options):
                return options[selected - 1]
        print("Bitte eine gueltige Zahl eingeben.")


def main() -> int:
    try:
        categories = load_categories()
        subcategories_by_category = load_subcategories()
        category = prompt_category(categories)
        subcategory = prompt_subcategory(category, subcategories_by_category)
        output = input("Ausgabedatei (Enter fuer automatische Datei mit Zeitstempel): ").strip()
        news_summary = input("News-Zusammenfassungen aktivieren? (J/n): ").strip().lower()

        python_executable = sys.executable
        command = [python_executable, "batch_market_brief.py"]

        if category:
            command.extend(["--category", category])
        if subcategory:
            command.extend(["--subcategory", subcategory])

        if output:
            command.extend(["--output", output])

        if news_summary in {"n", "no", "nein"}:
            command.append("--no-news-summary")
        else:
            command.append("--with-news-summary")

        print()
        print("Starte Batch-Lauf...")
        print("Befehl:", " ".join(command))
        print()

        result = subprocess.run(command, cwd=Path.cwd())
        return result.returncode
    except KeyboardInterrupt:
        print("\nAbgebrochen.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())


