from __future__ import annotations

import sys

from gemini_article_summary import print_text, resolve_api_key, summarize_article_with_cache
from article_fetcher import fetch_article


def prompt_multiline_article_text() -> str:
    print("Artikeltext manuell einfuegen. Mit einer einzelnen Zeile END abschliessen:")
    lines: list[str] = []
    while True:
        line = input()
        if line.strip() == "END":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def main() -> int:
    try:
        url = input("Artikel-URL: ").strip()
        if not url:
            print("Fehler: URL ist erforderlich.", file=sys.stderr)
            return 1

        title = input("Artikelname (optional): ").strip()
        api_key = resolve_api_key("")
        if not api_key:
            print("Fehler: Gemini API Key fehlt.", file=sys.stderr)
            return 1

        article_data = fetch_article(url, title)
        article_text = article_data["article_text"]
        if len(article_text) < 1200:
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
            model="gemma-3-27b-it",
        )
    except KeyboardInterrupt:
        print("\nAbgebrochen.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1

    print()
    print_text(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
