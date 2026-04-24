from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from xml.etree.ElementTree import Element, ElementTree, SubElement

INSTRUMENTS = (
    {"symbol": "^GDAXI", "name": "DAX"},
    {"symbol": "CG1G.DE", "name": "Amundi DAX ETF"},
    {"symbol": "DBXD.DE", "name": "Xtrackers DAX ETF"},
    {"symbol": "EXS1.DE", "name": "iShares Core DAX ETF"},
)

STOCK_CATEGORIES = [
    {
        "category": "Indizes",
        "subcategories": [
            {
                "name": "Laender-Indizes",
                "items": [
                    {
                        "name": "Nasdaq 100",
                        "land": "USA",
                        "ticker": "^NDX",
                        "isin": "US6311011026",
                        "wkn": "A0AE1X",
                    },
                    {
                        "name": "S&P 500",
                        "land": "USA",
                        "ticker": "^GSPC",
                        "isin": "US78378X1072",
                        "wkn": "A0AET0",
                    },
                    {
                        "name": "Dow Jones",
                        "land": "USA",
                        "ticker": "^DJI",
                        "isin": "US2605661048",
                        "wkn": "969420",
                    },
                    {
                        "name": "Russell 2000",
                        "land": "USA",
                        "ticker": "^RUT",
                        "isin": "US7827001089",
                        "wkn": "A1EZTD",
                    },
                    {
                        "name": "DAX 40",
                        "land": "Deutschland",
                        "ticker": "^GDAXI",
                        "isin": "DE0008469008",
                        "wkn": "846900",
                    },
                    {
                        "name": "CAC 40",
                        "land": "Frankreich",
                        "ticker": "^FCHI",
                        "isin": "FR0003500008",
                        "wkn": "969400",
                    },
                    {
                        "name": "FTSE 100",
                        "land": "UK",
                        "ticker": "^FTSE",
                        "isin": "GB0001383545",
                        "wkn": "969378",
                    },
                    {
                        "name": "AEX",
                        "land": "Niederlande",
                        "ticker": "^AEX",
                        "isin": "NL0000000107",
                        "wkn": "969241",
                    },
                    {
                        "name": "Nikkei 225",
                        "land": "Japan",
                        "ticker": "^N225",
                        "isin": "JP9010C00002",
                        "wkn": "A1RRF6",
                    },
                    {
                        "name": "Hang Seng",
                        "land": "Hongkong",
                        "ticker": "^HSI",
                        "isin": "HK0000004322",
                        "wkn": "145733",
                    },
                ],
            },
            {
                "name": "Branchen-Indizes",
                "items": [
                    {
                        "name": "MSCI World Information Tech",
                        "ticker": "XDWT.DE",
                        "isin": "EU000A2G9V11",
                        "wkn": "A2G9V1",
                        "tag": "Technologie",
                    },
                    {
                        "name": "EURO STOXX Banks",
                        "ticker": "LYBK.DE",
                        "isin": "EU0009658426",
                        "wkn": "965842",
                        "tag": "Banking",
                    },
                    {
                        "name": "MSCI World Energy",
                        "ticker": "5MVW.DE",
                        "isin": "EU000A2G9UZ5",
                        "wkn": "A2G9UZ",
                        "tag": "Energy & Oil",
                    },
                    {
                        "name": "MSCI World Health Care",
                        "ticker": "LYPE.DE",
                        "isin": "EU000A2G9V03",
                        "wkn": "A2G9V0",
                        "tag": "Healthcare",
                    },
                    {
                        "name": "STOXX Europe Luxury 10",
                        "ticker": "EXH7.DE",
                        "isin": "CH1187488874",
                        "wkn": "A3DL3G",
                        "tag": "Luxury Goods",
                    },
                ],
            }
        ],
    },
    {
        "category": "Einzelaktien",
        "subcategories": [
            {
                "name": "Big Tech",
                "items": [
                    {
                        "name": "Nvidia",
                        "ticker": "NVDA",
                        "isin": "US67066G1040",
                        "wkn": "918422",
                        "description": "Das Herz der KI. Extrem volatil, perfekt fuer Hebel-Trades.",
                    },
                    {
                        "name": "Tesla",
                        "ticker": "TSLA",
                        "isin": "US88160R1014",
                        "wkn": "A1CX3T",
                        "description": 'Die "Trader-Aktie" schlechthin. Reagiert extrem auf News von Elon Musk.',
                    },
                    {
                        "name": "Apple",
                        "ticker": "AAPL",
                        "isin": "US0378331005",
                        "wkn": "865985",
                        "description": "Oft stabiler, aber bei iPhone-Releases oder KI-Updates sehr trendstark.",
                    },
                    {
                        "name": "Microsoft",
                        "ticker": "MSFT",
                        "isin": "US5949181045",
                        "wkn": "870747",
                        "description": "Der Profiteur von Software-KI (Copilot).",
                    },
                    {
                        "name": "Meta Platforms",
                        "ticker": "META",
                        "isin": "US30303M1027",
                        "wkn": "A1JWVX",
                        "description": "Reagiert heftig auf Werbeeinnahmen und VR-News.",
                    },
                    {
                        "name": "Alphabet",
                        "ticker": "GOOGL",
                        "isin": "US02079K1079",
                        "wkn": "A14Y6H",
                        "description": "Google. Oft fuer Short-Sells interessant, wenn sie den KI-Anschluss verlieren.",
                    },
                    {
                        "name": "Amazon",
                        "ticker": "AMZN",
                        "isin": "US0231351067",
                        "wkn": "906866",
                        "description": "Wichtig fuer Cloud (AWS) und Konsumdaten.",
                    },
                    {
                        "name": "Broadcom",
                        "ticker": "AVGO",
                        "isin": "US11135F1012",
                        "wkn": "A2JG9Z",
                        "description": 'Der "stille Riese" der Vernetzung.',
                    },
                    {
                        "name": "AMD",
                        "ticker": "AMD",
                        "isin": "US0079031078",
                        "wkn": "863186",
                        "description": "Die aggressive Alternative zu Nvidia. Schwankt oft noch staerker.",
                    },
                    {
                        "name": "Netflix",
                        "ticker": "NFLX",
                        "isin": "US64110L1061",
                        "wkn": "552484",
                        "description": "Sehr volatil nach Abonnentenzahlen.",
                    },
                ],
            },
            {
                "name": "KI-Infrastruktur",
                "items": [
                    {
                        "name": "Super Micro Computer",
                        "ticker": "SMCI",
                        "isin": "US86800U3023",
                        "wkn": "A40MRM",
                        "description": "Extrem hohe Volatilitaet. Baut die Server-Schraenke fuer KI.",
                    },
                    {
                        "name": "Vertiv Holdings",
                        "ticker": "VRT",
                        "isin": "US92537N1081",
                        "wkn": "A2PZ5A",
                        "description": "Spezialist fuer Kuehlung von Rechenzentren. KI-Infrastruktur braucht Strom und Thermomanagement.",
                    },
                    {
                        "name": "Arm Holdings",
                        "ticker": "ARM",
                        "isin": "US0420682058",
                        "wkn": "A3EUCD",
                        "description": "Chip-Architektur mit oft massiven Kursspruengen.",
                    },
                ],
            },
            {
                "name": "Energy Storage",
                "items": [
                    {
                        "name": "Enphase Energy",
                        "ticker": "ENPH",
                        "isin": "US29355A1079",
                        "wkn": "A1JC82",
                        "description": "Solar-Wechselrichter. Sehr volatil bei Zinsaenderungen.",
                    },
                    {
                        "name": "First Solar",
                        "ticker": "FSLR",
                        "isin": "US3364331070",
                        "wkn": "A0LEKM",
                        "description": "US-Marktfuehrer fuer Solarpanels.",
                    },
                    {
                        "name": "QuantumScape",
                        "ticker": "QS",
                        "isin": "US74767V1098",
                        "wkn": "A2QJX9",
                        "description": "Wette auf die Feststoffbatterie. Extrem hohes Risiko.",
                    },
                ],
            },
            {
                "name": "Cybersecurity",
                "items": [
                    {
                        "name": "CrowdStrike",
                        "ticker": "CRWD",
                        "isin": "US22788C1053",
                        "wkn": "A2PK2R",
                        "description": "Marktfuehrer bei Cloud-Sicherheit.",
                    },
                    {
                        "name": "Palo Alto Networks",
                        "ticker": "PANW",
                        "isin": "US6974351057",
                        "wkn": "A1JZ0Q",
                        "description": "Der groesste Player im Bereich Firewalls.",
                    },
                    {
                        "name": "Palantir",
                        "ticker": "PLTR",
                        "isin": "US69608A1088",
                        "wkn": "A2QA4J",
                        "description": "Datenanalyse fuer Militaer und Firmen. Kult-Aktie bei Tradern.",
                    },
                    {
                        "name": "Zscaler",
                        "ticker": "ZS",
                        "isin": "US98980G1022",
                        "wkn": "A2JF28",
                        "description": "Spezialist fuer sicheren Fernzugriff.",
                    },
                ],
            }
        ],
    },
    {
        "category": "Rohstoffe",
        "subcategories": [
            {
                "name": "Edelmetalle",
                "items": [
                    {
                        "name": "Gold",
                        "ticker": "GC=F",
                        "description": "Gold-Futures als Yahoo-kompatibler Proxy fuer den aktuellen Goldpreis.",
                    },
                    {
                        "name": "Silber",
                        "ticker": "SI=F",
                        "description": 'Silber-Futures als Yahoo-kompatibler Proxy fuer den aktuellen Silberpreis.',
                    },
                ],
            },
            {
                "name": "Energie",
                "items": [
                    {
                        "name": "Brent Crude",
                        "ticker": "BZ=F",
                        "description": "Das Oel aus der Nordsee. Wichtig fuer Europa.",
                    },
                    {
                        "name": "WTI",
                        "ticker": "CL=F",
                        "description": "West Texas Intermediate. Das US-Leichtoel.",
                    },
                ],
            },
            {
                "name": "Industriemetalle",
                "items": [
                    {
                        "name": "Freeport-McMoRan",
                        "ticker": "FCX",
                        "isin": "US35671D8570",
                        "wkn": "896476",
                        "tag": "Kupfer",
                        "description": "Einer der groessten Kupferproduzenten.",
                    },
                    {
                        "name": "Rio Tinto",
                        "ticker": "RIO",
                        "isin": "GB0007188757",
                        "wkn": "852147",
                        "tag": "Kupfer",
                        "description": "Gigantischer Bergbaukonzern mit Kupfer und Eisenerz.",
                    },
                    {
                        "name": "Southern Copper",
                        "ticker": "SCCO",
                        "isin": "US84265V1052",
                        "wkn": "A0HG1Y",
                        "tag": "Kupfer",
                        "description": "Sitzt auf riesigen Kupferreserven in Peru und Mexiko.",
                    },
                    {
                        "name": "Albemarle",
                        "ticker": "ALB",
                        "isin": "US0126531013",
                        "wkn": "890167",
                        "tag": "Lithium",
                        "description": "Weltmarktfuehrer bei Lithium fuer Batterien.",
                    },
                    {
                        "name": "SQM",
                        "ticker": "SQM",
                        "isin": "US8336351056",
                        "wkn": "895007",
                        "tag": "Lithium",
                        "description": "Grosser Lithium-Player aus Chile.",
                    },
                    {
                        "name": "Arcadium Lithium",
                        "ticker": "ALTM",
                        "isin": "AU0000305724",
                        "wkn": "A3E13Q",
                        "tag": "Lithium",
                        "description": "Neuer grosser Zusammenschluss der Branche. Uebernahme durch Rio Tinto am 6. Maerz 2025 abgeschlossen, daher kein aktueller Boersenkurs mehr verfuegbar.",
                    },
                ],
            },
        ],
    }
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ruft den aktuellen DAX-Stand ueber yfinance ab."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Gibt die Antwort als JSON aus.",
    )
    parser.add_argument(
        "--categories",
        action="store_true",
        help="Gibt die Aktien-Kategorien aus.",
    )
    parser.add_argument(
        "--categories-xml",
        nargs="?",
        const="config/stock_categories/stock_categories.xml",
        metavar="DATEI",
        help="Speichert die Aktien-Kategorien als XML. Standard: config/stock_categories/stock_categories.xml",
    )
    return parser.parse_args()


def load_yfinance():
    try:
        import yfinance as yf
    except ImportError as exc:
        raise SystemExit(
            "yfinance ist nicht installiert. Installiere es mit: pip install yfinance"
        ) from exc
    return yf


def fetch_quote(yf, symbol: str, name: str) -> dict[str, object]:
    ticker = yf.Ticker(symbol)
    info = ticker.fast_info

    last_price = info.get("lastPrice")
    previous_close = info.get("previousClose")
    currency = info.get("currency") or "EUR"

    if last_price is None:
        raise RuntimeError(f"Kein aktueller Kurs fuer {name} ({symbol}) von yfinance erhalten.")

    change = None
    change_percent = None
    if previous_close not in (None, 0):
        change = last_price - previous_close
        change_percent = (change / previous_close) * 100

    return {
        "symbol": symbol,
        "name": name,
        "price": last_price,
        "previous_close": previous_close,
        "change": change,
        "change_percent": change_percent,
        "currency": currency,
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def fetch_quotes() -> list[dict[str, object]]:
    yf = load_yfinance()
    return [fetch_quote(yf, item["symbol"], item["name"]) for item in INSTRUMENTS]


def print_text(quotes: list[dict[str, object]]) -> None:
    print("Aktuelle DAX Kurse:")
    for quote in quotes:
        price = float(quote["price"])
        currency = str(quote["currency"])
        print(f"{quote['name']}: {price:,.2f} {currency}")


def print_categories_text() -> None:
    print("Aktien-Kategorien:")
    for category in STOCK_CATEGORIES:
        print(category["category"])
        for subcategory in category["subcategories"]:
            print(f"  {subcategory['name']}")
            for item in subcategory["items"]:
                details = f"{item['name']}"
                if "land" in item:
                    details += f" [{item['land']}]"
                if "tag" in item:
                    details += f" [{item['tag']}]"
                if "ticker" in item:
                    details += f" ({item['ticker']})"
                if "isin" in item:
                    details += f" ISIN:{item['isin']}"
                if "wkn" in item:
                    details += f" WKN:{item['wkn']}"
                print(f"    - {details}")


def write_categories_xml(output_path: str) -> Path:
    root = Element("stockCategories")

    for category in STOCK_CATEGORIES:
        category_element = SubElement(root, "category", name=category["category"])
        for subcategory in category["subcategories"]:
            subcategory_element = SubElement(
                category_element,
                "subcategory",
                name=subcategory["name"],
            )
            for item in subcategory["items"]:
                item_element = SubElement(subcategory_element, "index")
                SubElement(item_element, "name").text = item["name"]
                if "land" in item:
                    SubElement(item_element, "land").text = item["land"]
                if "tag" in item:
                    SubElement(item_element, "tag").text = item["tag"]
                if "ticker" in item:
                    SubElement(item_element, "ticker").text = item["ticker"]
                if "isin" in item:
                    SubElement(item_element, "isin").text = item["isin"]
                if "wkn" in item:
                    SubElement(item_element, "wkn").text = item["wkn"]
                if "description" in item:
                    SubElement(item_element, "description").text = item["description"]

    path = Path(output_path)
    ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path


def main() -> int:
    args = parse_args()

    if args.categories_xml:
        output_path = write_categories_xml(args.categories_xml)
        print(f"XML gespeichert: {output_path}")
        return 0

    if args.categories:
        if args.json:
            print(json.dumps(STOCK_CATEGORIES, ensure_ascii=True, indent=2))
        else:
            print_categories_text()
        return 0

    try:
        data = fetch_quotes()
    except Exception as exc:
        print(f"Fehler beim Abruf der DAX-Kurse: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(data, ensure_ascii=True, indent=2))
    else:
        print_text(data)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


