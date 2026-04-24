from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests


DEFAULT_ISIN = "US6311011026"
DEFAULT_MIN_LEVERAGE = 8
DEFAULT_MAX_LEVERAGE = 12
DEFAULT_DIRECTION = "long"
REQUEST_TIMEOUT = 20
TRACE_ID_SALT = "w4ivc1ATTGta6njAZzMbkL3kJwxMfEAKDa3MNr"


def prompt_with_default(prompt: str, default: str) -> str:
    value = input(prompt).strip()
    return value or default


def prompt_int(prompt: str, default: int) -> int:
    raw = input(prompt).strip()
    if not raw:
        return default

    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"Ungueltige Zahl eingegeben: {raw}")


def prompt_direction(prompt: str, default: str) -> str:
    raw = input(prompt).strip().lower()
    if not raw:
        return default
    if raw not in {"long", "short"}:
        raise ValueError("Richtung muss 'long' oder 'short' sein.")
    return raw


def build_params(underlying_isin: str, min_leverage: int, max_leverage: int, direction: str) -> dict[str, Any]:
    return {
        "underlying_isin": underlying_isin,
        "min_leverage": min_leverage,
        "max_leverage": max_leverage,
        "direction": direction,
        "product_type": "knock-out",
    }


def build_boerse_frankfurt_headers(url: str) -> dict[str, str]:
    client_date = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    trace_id = hashlib.md5(f"{client_date}{url}{TRACE_ID_SALT}".encode("utf-8")).hexdigest()
    return {
        "Accept": "application/json, text/plain, */*",
        "Client-Date": client_date,
        "X-Client-TraceId": trace_id,
        "Referer": "https://live.deutsche-boerse.com/zertifikate",
        "Origin": "https://live.deutsche-boerse.com",
        "User-Agent": "CertificateScraper/1.0",
    }


def build_browser_headers(referer: str = "") -> dict[str, str]:
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
        ),
    }
    if referer:
        headers["Referer"] = referer
    return headers


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(build_browser_headers())
    return session


def normalize_direction(text: str | None) -> str:
    value = (text or "").strip().lower()
    if any(token in value for token in ("call", "long", "bull")):
        return "long"
    if any(token in value for token in ("put", "short", "bear")):
        return "short"
    return value


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return None

    text = text.replace("%", "").replace(" ", "")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", ".")

    try:
        return float(text)
    except ValueError:
        return None


def get_nested_value(data: Any, *paths: tuple[str, ...]) -> Any:
    for path in paths:
        current = data
        found = True
        for key in path:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                found = False
                break
        if found:
            return current
    return None


def collect_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        return []

    candidates = [
        payload.get("data"),
        payload.get("results"),
        payload.get("items"),
        payload.get("content"),
        payload.get("instruments"),
        payload.get("derivatives"),
        payload.get("products"),
    ]

    for candidate in candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
        if isinstance(candidate, dict):
            for key in ("items", "results", "content", "products"):
                nested = candidate.get(key)
                if isinstance(nested, list):
                    return [item for item in nested if isinstance(item, dict)]

    return []


def find_json_snippets(text: str) -> list[str]:
    snippets: list[str] = []

    next_data_match = re.search(
        r'<script[^>]+id="__NEXT_DATA__"[^>]*>\s*(\{.*?\})\s*</script>',
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if next_data_match:
        snippets.append(next_data_match.group(1))

    for match in re.finditer(r"<script[^>]*>\s*(\{.*?\})\s*</script>", text, flags=re.DOTALL | re.IGNORECASE):
        snippet = match.group(1)
        if any(token in snippet for token in ('"products"', '"derivatives"', '"instruments"', '"results"')):
            snippets.append(snippet)

    for match in re.finditer(r"(\{[^{}]*(?:\"products\"|\"derivatives\"|\"instruments\"|\"results\")[\s\S]*?\})", text):
        snippets.append(match.group(1))

    return snippets


def collect_items_from_html(text: str) -> list[dict[str, Any]]:
    for snippet in find_json_snippets(text):
        try:
            payload = json.loads(snippet)
        except Exception:
            continue
        items = collect_items(payload)
        if items:
            return items
    return []


def extract_product_data(item: dict[str, Any]) -> dict[str, Any]:
    underlying_price = to_float(
        get_nested_value(
            item,
            ("underlying", "price"),
            ("underlyingPrice",),
            ("baseValue", "price"),
            ("underlying", "lastPrice"),
        )
    )
    knock_out_barrier = to_float(
        get_nested_value(
            item,
            ("knockOutBarrier",),
            ("knockoutBarrier",),
            ("barrier",),
            ("strike",),
            ("keyFigures", "knockOutBarrier"),
            ("keyFigures", "barrier"),
        )
    )
    current_price = to_float(
        get_nested_value(
            item,
            ("price",),
            ("lastPrice",),
            ("quote", "lastPrice"),
            ("quote", "price"),
            ("keyFigures", "price"),
        )
    )
    leverage = to_float(
        get_nested_value(
            item,
            ("leverage",),
            ("keyFigures", "leverage"),
            ("statistics", "leverage"),
        )
    )
    direction = normalize_direction(
        get_nested_value(
            item,
            ("direction",),
            ("type",),
            ("productType",),
            ("name",),
        )
    )

    distance_pct = to_float(
        get_nested_value(
            item,
            ("distanceToKnockOutPercent",),
            ("distance_to_knock_out_percent",),
            ("keyFigures", "distanceToKnockOutPercent"),
        )
    )
    if distance_pct is None and underlying_price and knock_out_barrier:
        distance_pct = abs((underlying_price - knock_out_barrier) / underlying_price) * 100

    return {
        "isin": get_nested_value(item, ("isin",), ("securityIsin",), ("instrument", "isin")),
        "name": get_nested_value(item, ("name",), ("shortName",), ("instrument", "name")) or "",
        "direction": direction,
        "leverage": leverage,
        "knock_out_barrier": knock_out_barrier,
        "current_price": current_price,
        "distance_to_knock_out_percent": distance_pct,
        "underlying_price": underlying_price,
        "raw": item,
    }


def filter_products(products: list[dict[str, Any]], params: dict[str, Any]) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    wanted_direction = params["direction"]

    for product in products:
        leverage = product.get("leverage")
        direction = product.get("direction")
        if not product.get("isin") or leverage is None:
            continue
        if direction != wanted_direction:
            continue
        if leverage < params["min_leverage"] or leverage > params["max_leverage"]:
            continue
        filtered.append(product)

    filtered.sort(key=lambda item: item.get("leverage") or 0.0)
    return filtered


def fetch_from_boerse_frankfurt(params: dict[str, Any]) -> list[dict[str, Any]]:
    search_payloads = [
        {
            "underlyings": [params["underlying_isin"]],
            "productGroup": "LeverageProducts",
            "productType": "Knock-Outs",
            "lang": "de",
            "offset": 0,
            "limit": 250,
        },
        {
            "underlyingIsins": [params["underlying_isin"]],
            "productCategory": "hebelprodukte",
            "subCategory": "knock-outs",
            "lang": "de",
            "offset": 0,
            "limit": 250,
        },
    ]
    endpoints = [
        ("POST", "https://api.boerse-frankfurt.de/v1/search/derivative_search"),
        ("POST", "https://api.boerse-frankfurt.de/v1/search/derivatives"),
    ]

    errors: list[str] = []
    session = build_session()
    for payload in search_payloads:
        for method, endpoint in endpoints:
            try:
                headers = build_boerse_frankfurt_headers(endpoint)
                response = session.request(
                    method=method,
                    url=endpoint,
                    headers=headers,
                    json=payload,
                    timeout=REQUEST_TIMEOUT,
                )
                if response.status_code >= 400:
                    errors.append(f"{endpoint} -> HTTP {response.status_code}")
                    continue

                items = collect_items(response.json())
                if items:
                    return items
                errors.append(f"{endpoint} -> leere Antwort")
            except Exception as exc:
                errors.append(f"{endpoint} -> {exc}")

    html_urls = [
        "https://live.deutsche-boerse.com/zertifikate",
        f"https://live.deutsche-boerse.com/zertifikate?search={params['underlying_isin']}",
        f"https://live.deutsche-boerse.com/zertifikate?isin={params['underlying_isin']}",
    ]
    for url in html_urls:
        try:
            response = session.get(url, headers=build_browser_headers("https://live.deutsche-boerse.com/"), timeout=REQUEST_TIMEOUT)
            if response.status_code >= 400:
                errors.append(f"{url} -> HTTP {response.status_code}")
                continue
            items = collect_items_from_html(response.text)
            if items:
                return items
            errors.append(f"{url} -> keine eingebetteten Produktdaten")
        except Exception as exc:
            errors.append(f"{url} -> {exc}")

    raise RuntimeError("Keine Daten von Boerse Frankfurt abrufbar: " + " | ".join(errors))


def fetch_from_stuttgart(params: dict[str, Any]) -> list[dict[str, Any]]:
    base_url = "https://www.boerse-stuttgart.de/de-de/tools/produktsuche/"
    query_params = {
        "searchTerm": params["underlying_isin"],
        "productType": "Knock-Outs",
        "assetClass": "hebelprodukte",
        "order": "desc",
        "sort": "leverage",
        "limit": 250,
    }
    url = base_url + "?" + urlencode(query_params)
    session = build_session()
    session.get("https://www.boerse-stuttgart.de/", timeout=REQUEST_TIMEOUT)
    response = session.get(
        url,
        headers=build_browser_headers("https://www.boerse-stuttgart.de/"),
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    text = response.text
    marker = "__NEXT_DATA__"
    if marker not in text:
        raise RuntimeError("Keine maschinenlesbaren Daten im HTML gefunden.")

    start = text.find("<script id=\"__NEXT_DATA__\"")
    if start < 0:
        raise RuntimeError("NEXT_DATA Script nicht gefunden.")
    start = text.find(">", start)
    end = text.find("</script>", start)
    if start < 0 or end < 0:
        raise RuntimeError("NEXT_DATA Script konnte nicht extrahiert werden.")

    payload = json.loads(text[start + 1:end])
    items = collect_items(payload)
    if items:
        return items

    raise RuntimeError("Keine Produkte in der Antwort gefunden.")


def extract_html_number(html: str, patterns: list[str]) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        value = to_float(match.group(1))
        if value is not None:
            return value
    return None


def extract_hsbc_detail_data(html: str, default_name: str, direction: str) -> dict[str, Any]:
    isin_match = re.search(r"\b([A-Z]{2}[A-Z0-9]{10})\b", html)
    name_match = re.search(r"<title>\s*(.*?)\s*</title>", html, flags=re.IGNORECASE | re.DOTALL)

    return {
        "isin": isin_match.group(1) if isin_match else "",
        "name": re.sub(r"\s+", " ", name_match.group(1)).strip() if name_match else default_name,
        "direction": normalize_direction(direction),
        "leverage": extract_html_number(html, [r"Hebel[^0-9]{0,40}([0-9]+(?:[.,][0-9]+)?)"]),
        "knockOutBarrier": extract_html_number(
            html,
            [
                r"Knock[\-\s]?Out(?:[-\s]?Barriere)?[^0-9]{0,40}([0-9]+(?:[.,][0-9]+)?)",
                r"Barriere[^0-9]{0,40}([0-9]+(?:[.,][0-9]+)?)",
            ],
        ),
        "price": extract_html_number(
            html,
            [
                r"(?:Brief|Preis|Kurs)[^0-9]{0,40}([0-9]+(?:[.,][0-9]+)?)",
            ],
        ),
        "distanceToKnockOutPercent": extract_html_number(
            html,
            [
                r"Abstand[^%]{0,40}([0-9]+(?:[.,][0-9]+)?)\s*%",
            ],
        ),
    }


def fetch_from_hsbc(params: dict[str, Any]) -> list[dict[str, Any]]:
    session = build_session()
    errors: list[str] = []
    search_urls = [
        f"https://www.hsbc-zertifikate.de/home/suche?query={params['underlying_isin']}",
        f"https://www.hsbc-zertifikate.de/home/details?isin={params['underlying_isin']}",
    ]

    html = ""
    for url in search_urls:
        try:
            response = session.get(url, headers=build_browser_headers("https://www.hsbc-zertifikate.de/"), timeout=REQUEST_TIMEOUT)
            if response.status_code >= 400:
                errors.append(f"{url} -> HTTP {response.status_code}")
                continue
            html = response.text
            if html:
                break
        except Exception as exc:
            errors.append(f"{url} -> {exc}")

    if not html:
        raise RuntimeError("HSBC-Suche nicht abrufbar: " + " | ".join(errors))

    links = re.findall(r'href="([^"]*?/home/details[^"]+)"', html, flags=re.IGNORECASE)
    unique_links: list[str] = []
    for link in links:
        absolute = link if link.startswith("http") else f"https://www.hsbc-zertifikate.de{link}"
        if absolute not in unique_links:
            unique_links.append(absolute)

    products: list[dict[str, Any]] = []
    guessed_direction = "call" if params["direction"] == "long" else "put"
    for link in unique_links[:80]:
        try:
            detail_response = session.get(
                link,
                headers=build_browser_headers("https://www.hsbc-zertifikate.de/"),
                timeout=REQUEST_TIMEOUT,
            )
            if detail_response.status_code >= 400:
                continue
            product = extract_hsbc_detail_data(detail_response.text, default_name=link, direction=guessed_direction)
            if product.get("isin"):
                products.append(product)
        except Exception:
            continue

    if products:
        return products

    raise RuntimeError("Keine HSBC-Produktdetails extrahierbar.")


def fetch_products(params: dict[str, Any]) -> list[dict[str, Any]]:
    errors: list[str] = []
    fetchers = [fetch_from_boerse_frankfurt, fetch_from_stuttgart, fetch_from_hsbc]

    for fetcher in fetchers:
        try:
            return fetcher(params)
        except Exception as exc:
            errors.append(f"{fetcher.__name__}: {exc}")

    raise RuntimeError("Produktdaten konnten nicht abgerufen werden: " + " | ".join(errors))


def build_output_path(underlying_isin: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(f"zertifikate_analyse_{underlying_isin}_{timestamp}.json")


def write_output(path: Path, params: dict[str, Any], products: list[dict[str, Any]]) -> None:
    simplified_products = [
        {
            "isin": product["isin"],
            "name": product["name"],
            "direction": product["direction"],
            "leverage": product["leverage"],
            "knock_out_barrier": product["knock_out_barrier"],
            "current_price": product["current_price"],
            "distance_to_knock_out_percent": product["distance_to_knock_out_percent"],
            "underlying_price": product["underlying_price"],
        }
        for product in products
    ]

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "params": params,
        "count": len(simplified_products),
        "products": simplified_products,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    try:
        underlying_isin = prompt_with_default(
            "Bitte geben Sie die ISIN des Basiswerts ein (Vorgabe: US6311011026): ",
            DEFAULT_ISIN,
        )
        min_leverage = prompt_int(
            "Minimaler Hebel (Vorgabe: 8): ",
            DEFAULT_MIN_LEVERAGE,
        )
        max_leverage = prompt_int(
            "Maximaler Hebel (Vorgabe: 12): ",
            DEFAULT_MAX_LEVERAGE,
        )
        direction = prompt_direction(
            "Richtung (long/short): ",
            DEFAULT_DIRECTION,
        )
    except ValueError as exc:
        print(f"Fehler bei der Eingabe: {exc}", file=sys.stderr)
        return 1

    if min_leverage > max_leverage:
        print("Fehler: Der minimale Hebel darf nicht groesser als der maximale Hebel sein.", file=sys.stderr)
        return 1

    params = build_params(
        underlying_isin=underlying_isin,
        min_leverage=min_leverage,
        max_leverage=max_leverage,
        direction=direction,
    )

    print("Suche passende Zertifikate...")
    try:
        raw_items = fetch_products(params)
        extracted_products = [extract_product_data(item) for item in raw_items]
        filtered_products = filter_products(extracted_products, params)
    except requests.RequestException as exc:
        print(f"HTTP-Fehler bei der Abfrage: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Fehler bei der Verarbeitung: {exc}", file=sys.stderr)
        return 1

    output_path = build_output_path(underlying_isin)
    write_output(output_path, params, filtered_products)

    print(f"{len(filtered_products)} passende Produkte gespeichert in: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
