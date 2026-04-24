from __future__ import annotations

import sys

from article_fetcher import fetch_article, print_text


def main() -> int:
    try:
        url = input("Artikel-URL: ").strip()
        if not url:
            print("Fehler: URL ist erforderlich.", file=sys.stderr)
            return 1

        title = input("Artikelname (optional): ").strip()
        data = fetch_article(url, title)
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
