from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from market_brief import fetch_market_brief, load_index_entry, print_text

XML_PATH = Path("config/stock_categories/stock_categories.xml")


def prompt_choice(title: str, options: list[dict[str, Any]]) -> dict[str, Any]:
    while True:
        print()
        print(title)
        for index, option in enumerate(options, start=1):
            print(f"{index}. {option['label']}")

        raw = input("Auswahl: ").strip()
        if not raw.isdigit():
            print("Bitte eine Zahl eingeben.")
            continue

        selected = int(raw)
        if 1 <= selected <= len(options):
            return options[selected - 1]

        print("Ungueltige Auswahl.")


def load_tree(xml_path: Path) -> list[dict[str, Any]]:
    root = ElementTree.parse(xml_path).getroot()
    categories: list[dict[str, Any]] = []

    for category in root.findall("category"):
        subcategories = []
        for subcategory in category.findall("subcategory"):
            items = []
            for item in subcategory.findall("index"):
                entry = {}
                for child in item:
                    entry[child.tag] = (child.text or "").strip()
                items.append(entry)
            subcategories.append(
                {
                    "name": subcategory.attrib.get("name", "Ohne Name"),
                    "items": items,
                }
            )
        categories.append(
            {
                "name": category.attrib.get("name", "Ohne Name"),
                "subcategories": subcategories,
            }
        )

    return categories


def select_entry(xml_path: Path) -> str:
    tree = load_tree(xml_path)

    category = prompt_choice(
        "Kategorie waehlen:",
        [{"label": item["name"], "value": item} for item in tree if item["subcategories"]],
    )["value"]

    subcategory = prompt_choice(
        "Subkategorie waehlen:",
        [
            {"label": item["name"], "value": item}
            for item in category["subcategories"]
            if item["items"]
        ],
    )["value"]

    instrument = prompt_choice(
        "Eintrag waehlen:",
        [
            {
                "label": build_item_label(item),
                "value": item,
            }
            for item in subcategory["items"]
        ],
    )["value"]

    query = instrument.get("ticker") or instrument.get("name")
    if not query:
        raise RuntimeError("Der ausgewaehlte XML-Eintrag hat weder Ticker noch Namen.")

    return query


def build_item_label(item: dict[str, str]) -> str:
    label = item.get("name", "Ohne Name")
    if item.get("ticker"):
        label += f" ({item['ticker']})"
    if item.get("tag"):
        label += f" [{item['tag']}]"
    if item.get("land"):
        label += f" [{item['land']}]"
    return label


def main() -> int:
    try:
        query = select_entry(XML_PATH)
        entry = load_index_entry(str(XML_PATH), query)
        data = fetch_market_brief(entry)
    except KeyboardInterrupt:
        print("\nAbgebrochen.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1

    print()
    print_text(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


