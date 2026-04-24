from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ruft einen Artikel per URL ab und gibt den Artikeltext zurueck."
    )
    parser.add_argument("url", nargs="?", help="Link zum Artikel")
    parser.add_argument(
        "--title",
        default="",
        help="Optionaler Artikelname zur besseren Ausgabe",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Gibt das Ergebnis als JSON aus.",
    )
    return parser.parse_args()


def load_dependencies() -> tuple[Any, Any]:
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise SystemExit(
            "Fehlende Abhaengigkeiten. Installiere sie mit: pip install requests beautifulsoup4"
        ) from exc
    return requests, BeautifulSoup


def normalize_whitespace(text: str) -> str:
    text = re.sub(r"\r", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def extract_text_from_soup(soup: Any) -> str:
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside"]):
        tag.decompose()

    candidates = []
    selectors = [
        "article",
        "main",
        "[role='main']",
        ".article",
        ".article-content",
        ".post-content",
        ".entry-content",
        ".content",
    ]

    for selector in selectors:
        candidates.extend(soup.select(selector))

    for candidate in candidates:
        paragraphs = [p.get_text(" ", strip=True) for p in candidate.find_all("p")]
        text = "\n\n".join(part for part in paragraphs if part)
        text = normalize_whitespace(text)
        if len(text) > 300:
            return text

    paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
    return normalize_whitespace("\n\n".join(part for part in paragraphs if part))


def fetch_article(url: str, title: str = "") -> dict[str, str]:
    requests, BeautifulSoup = load_dependencies()

    response = requests.get(
        url,
        timeout=20,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            )
        },
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    page_title = soup.title.get_text(strip=True) if soup.title else ""
    article_text = extract_text_from_soup(soup)

    if not article_text:
        raise RuntimeError("Kein Artikeltext gefunden.")

    return {
        "requested_title": title,
        "page_title": page_title,
        "url": url,
        "article_text": article_text,
    }


def print_text(data: dict[str, str]) -> None:
    title = data["requested_title"] or data["page_title"] or "Artikel"
    print(f"Titel: {title}")
    print(f"URL: {data['url']}")
    print()
    print(data["article_text"])


def main() -> int:
    args = parse_args()

    try:
        url = args.url or input("Artikel-URL: ").strip()
        if not url:
            print("Fehler beim Abruf des Artikels: URL ist erforderlich.", file=sys.stderr)
            return 1

        title = args.title
        if not title:
            title = input("Artikelname (optional): ").strip()

        data = fetch_article(url, title)
    except Exception as exc:
        print(f"Fehler beim Abruf des Artikels: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(data, ensure_ascii=True, indent=2))
    else:
        print_text(data)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
