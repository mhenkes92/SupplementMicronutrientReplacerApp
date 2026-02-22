import base64
import csv
import difflib
import functools
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from PIL import Image


load_dotenv()

# If you want to use your free OpenRouter key directly in code,
# paste it below between the quotes.
DIRECT_OPENROUTER_API_KEY = "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"
DIRECT_OPENAI_API_KEY = "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"
DIRECT_GITHUB_MODELS_TOKEN = "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"

# Priority: environment variable first, then direct key fallback.
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip() or DIRECT_OPENROUTER_API_KEY.strip()
OPENROUTER_MODEL_TEXT = os.getenv("OPENROUTER_MODEL_TEXT", "openai/gpt-4o-mini")
OPENROUTER_MODEL_VISION = os.getenv("OPENROUTER_MODEL_VISION", "openai/gpt-4o-mini")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip() or DIRECT_OPENAI_API_KEY.strip()
OPENAI_MODEL_TEXT = os.getenv("OPENAI_MODEL_TEXT", "gpt-4o-mini")
OPENAI_MODEL_VISION = os.getenv("OPENAI_MODEL_VISION", "gpt-4o-mini")
GITHUB_MODELS_TOKEN = os.getenv("GITHUB_MODELS_TOKEN", "").strip() or DIRECT_GITHUB_MODELS_TOKEN.strip()
GITHUB_MODELS_MODEL_TEXT = os.getenv("GITHUB_MODELS_MODEL_TEXT", "gpt-4o-mini")
GITHUB_MODELS_MODEL_VISION = os.getenv("GITHUB_MODELS_MODEL_VISION", "gpt-4o-mini")
TESSERACT_CMD = os.getenv("TESSERACT_CMD", "")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
GITHUB_MODELS_URL = os.getenv(
    "GITHUB_MODELS_URL",
    "https://models.inference.ai.azure.com/chat/completions",
).strip()
HTTP_TIMEOUT = 30
OPENROUTER_DEFAULT_MAX_TOKENS = 500

LAST_OPENROUTER_ERROR = ""
LAST_OPENAI_ERROR = ""
LAST_GITHUB_MODELS_ERROR = ""
LAST_VISION_PROVIDER = ""
LAST_TEXT_PROVIDER = ""
LAST_RAG_ERROR = ""

APP_DIR = Path(__file__).resolve().parent
USDA_RANK_DB_PATH = APP_DIR / "data" / "usda_rankings.db"
COMPONENT_ALIASES_PATH = APP_DIR / "data" / "component_aliases.csv"
COMPONENT_PROXY_RULES_PATH = APP_DIR / "data" / "component_proxy_rules.csv"
TOP_FOODS_PER_COMPONENT = 5
OVERVIEW_ALT_LIMIT = 100
RAG_TOP_K = 8
RAG_INDEX_PATH = APP_DIR / "data" / "fitness_rag_chunks.jsonl"
RAG_INDEX_META_PATH = APP_DIR / "data" / "fitness_rag_meta.json"
PRICE_DB_PATH = APP_DIR / "data" / "whole_food_prices.csv"
MEAL_RECIPES_DB_PATH = APP_DIR / "data" / "meal_recipes_local.json"
MEAL_RECIPES_FITNESS_PACK_PATH = APP_DIR / "data" / "meal_recipes_fitness_pack.json"
DIETARY_PROFILES_PATH = APP_DIR / "data" / "dietary_profiles.json"

COUNTRY_PRICE_CONFIG: dict[str, dict[str, str]] = {
    "Germany": {"currency": "EUR", "default_market": "Rewe"},
    "United States": {"currency": "USD", "default_market": "Walmart"},
    "United Kingdom": {"currency": "GBP", "default_market": "Auto"},
    "India": {"currency": "INR", "default_market": "Auto"},
    "Brazil": {"currency": "BRL", "default_market": "Auto"},
    "Global": {"currency": "USD", "default_market": "Auto"},
}

CURRENCY_SYMBOL: dict[str, str] = {
    "EUR": "€",
    "USD": "$",
    "GBP": "£",
    "INR": "₹",
    "BRL": "R$",
}

SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "").strip()
DATAFORSEO_LOGIN = os.getenv("DATAFORSEO_LOGIN", "").strip()
DATAFORSEO_PASSWORD = os.getenv("DATAFORSEO_PASSWORD", "").strip()

SOURCE_RELIABILITY_SCORE: dict[str, float] = {
    "local_db": 0.95,
    "official_stat_mapped": 0.90,
    "official_dataset": 0.92,
    "official_api": 0.92,
    "retailer_api": 0.88,
    "serpapi_google_shopping": 0.74,
    "dataforseo_google_shopping": 0.72,
    "market_scrape": 0.56,
    "llm_estimate": 0.30,
}

PRICE_RANKING_WEIGHTS: dict[str, float] = {
    "source_reliability": 0.26,
    "match_quality": 0.23,
    "freshness": 0.14,
    "geo": 0.10,
    "economics": 0.27,
}

COUNTRY_GL_MAP: dict[str, str] = {
    "Germany": "de",
    "United States": "us",
    "United Kingdom": "uk",
    "India": "in",
    "Brazil": "br",
    "Global": "us",
}

COUNTRY_DATAFORSEO_LOCATION: dict[str, str] = {
    "Germany": "Germany",
    "United States": "United States",
    "United Kingdom": "United Kingdom",
    "India": "India",
    "Brazil": "Brazil",
    "Global": "United States",
}

FITNESS_REFERENCE_DIR_CANDIDATES = [
    APP_DIR.parent / "fitness_reference",
    APP_DIR.parent / "Fitness_reference",
]


def normalize_lookup_key(value: str) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z0-9\s\-\+\(\)]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def load_component_aliases() -> dict[str, str]:
    aliases: dict[str, str] = {}
    if not COMPONENT_ALIASES_PATH.exists():
        return aliases

    try:
        with COMPONENT_ALIASES_PATH.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                alias = normalize_lookup_key(str(row.get("alias", "")))
                canonical = str(row.get("canonical_nutrient", "")).strip()
                if alias and canonical:
                    aliases[alias] = canonical
    except Exception:
        return {}

    return aliases


def load_component_proxy_rules() -> list[dict[str, str]]:
    rules: list[dict[str, str]] = []
    if not COMPONENT_PROXY_RULES_PATH.exists():
        return rules

    try:
        with COMPONENT_PROXY_RULES_PATH.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                component = normalize_lookup_key(str(row.get("component", "")))
                proxy_nutrient = str(row.get("proxy_nutrient", "")).strip()
                confidence = str(row.get("confidence", "medium")).strip().lower() or "medium"
                rationale = str(row.get("rationale", "")).strip()
                if component and proxy_nutrient:
                    rules.append(
                        {
                            "component": component,
                            "proxy_nutrient": proxy_nutrient,
                            "confidence": confidence,
                            "rationale": rationale,
                        }
                    )
    except Exception:
        return []

    return rules


def try_open_usda_db() -> sqlite3.Connection | None:
    if not USDA_RANK_DB_PATH.exists():
        return None
    try:
        return sqlite3.connect(str(USDA_RANK_DB_PATH))
    except Exception:
        return None


def _lookup_nutrient_row(
    conn: sqlite3.Connection,
    nutrient_name: str,
    nutrient_rows: list[tuple[Any, ...]] | None = None,
) -> dict[str, Any] | None:
    target_raw = (nutrient_name or "").strip().lower()
    target_norm = normalize_lookup_key(nutrient_name)
    if not target_raw:
        return None

    rows = nutrient_rows
    if rows is None:
        rows = conn.execute(
            """
            SELECT id, nutrient_name, unit_name
            FROM nutrients
            """
        ).fetchall()

    candidates: list[tuple[int, int, int, tuple[Any, ...]]] = []
    for row in rows:
        row_name = str(row[1] or "")
        row_raw = row_name.strip().lower()
        row_norm = normalize_lookup_key(row_name)

        score = 999
        if row_raw == target_raw:
            score = 0
        elif row_norm == target_norm:
            score = 1
        elif target_raw in row_raw:
            score = 2
        elif target_norm and target_norm in row_norm:
            score = 3

        if score == 999:
            continue

        added_penalty = 10 if "added" in row_raw and "added" not in target_raw else 0
        candidates.append((score, added_penalty, len(row_name), row))

    if candidates:
        candidates.sort(key=lambda x: (x[0], x[1], x[2]))
        best = candidates[0][3]
        return {"nutrient_id": int(best[0]), "nutrient_name": str(best[1]), "unit_name": str(best[2] or "")}

    return None


def resolve_component_to_nutrient(
    conn: sqlite3.Connection,
    component: str,
    aliases: dict[str, str],
    proxy_rules: list[dict[str, str]],
    nutrient_rows: list[tuple[Any, ...]] | None = None,
    nutrient_names: list[str] | None = None,
) -> dict[str, Any]:
    normalized_component = normalize_lookup_key(component)
    if not normalized_component:
        return {}

    alias_hit = aliases.get(normalized_component)
    if alias_hit:
        nutrient = _lookup_nutrient_row(conn, alias_hit, nutrient_rows=nutrient_rows)
        if nutrient:
            nutrient.update({"confidence": "high", "match_method": "alias"})
            return nutrient

    direct = _lookup_nutrient_row(conn, normalized_component, nutrient_rows=nutrient_rows)
    if direct:
        direct.update({"confidence": "high", "match_method": "direct"})
        return direct

    for rule in proxy_rules:
        target = rule.get("component", "")
        if not target:
            continue
        if normalized_component == target or target in normalized_component:
            nutrient = _lookup_nutrient_row(conn, str(rule.get("proxy_nutrient", "")), nutrient_rows=nutrient_rows)
            if nutrient:
                nutrient.update(
                    {
                        "confidence": str(rule.get("confidence", "medium") or "medium"),
                        "match_method": "curated_proxy",
                        "proxy_rationale": str(rule.get("rationale", "")),
                    }
                )
                return nutrient

    candidate_names = nutrient_names
    if candidate_names is None:
        names = conn.execute("SELECT nutrient_name FROM nutrients").fetchall()
        candidate_names = [str(row[0]) for row in names]

    close = difflib.get_close_matches(component, candidate_names, n=1, cutoff=0.80)
    if close:
        nutrient = _lookup_nutrient_row(conn, close[0], nutrient_rows=nutrient_rows)
        if nutrient:
            nutrient.update({"confidence": "medium", "match_method": "fuzzy"})
            return nutrient

    if OPENROUTER_API_KEY or OPENAI_API_KEY or GITHUB_MODELS_TOKEN:
        llm_prompt = (
            "Map this supplement component to one USDA nutrient name if clearly mappable. "
            "Return only the nutrient name or NONE."
        )
        llm_out = call_openrouter_text("You map supplement terms to nutrient names.", f"{llm_prompt}\n\nComponent: {component}")
        llm_candidate = clean_json_block(llm_out).strip().strip('"').strip()
        if llm_candidate and llm_candidate.upper() != "NONE":
            nutrient = _lookup_nutrient_row(conn, llm_candidate, nutrient_rows=nutrient_rows)
            if nutrient:
                nutrient.update({"confidence": "low", "match_method": "llm"})
                return nutrient

    return {}


def get_top_ranked_foods(conn: sqlite3.Connection, nutrient_id: int, top_n: int = TOP_FOODS_PER_COMPONENT) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT rank_desc, food_description, food_category, amount_per_100g, unit_name
        FROM nutrient_rankings
        WHERE nutrient_id = ?
          AND amount_per_100g IS NOT NULL
          AND amount_per_100g > 0
                ORDER BY amount_per_100g DESC, rank_desc ASC
        LIMIT ?
        """,
        (nutrient_id, top_n),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append(
            {
                "rank": int(row[0]),
                "food_description": str(row[1]),
                "food_category": str(row[2] or ""),
                "amount_per_100g": float(row[3] or 0.0),
                "unit": str(row[4] or ""),
            }
        )
    return result


def unit_to_mg(unit: str) -> float | None:
    u = (unit or "").strip().lower()
    if u in {"mg", "milligram", "milligrams"}:
        return 1.0
    if u in {"mcg", "ug", "μg", "µg", "microgram", "micrograms"}:
        return 0.001
    if u in {"g", "gram", "grams"}:
        return 1000.0
    return None


def grams_needed_to_match_dose(
    supplement_dose_value: float | None,
    supplement_dose_unit: str | None,
    nutrient_amount_per_100g: float,
    nutrient_unit: str,
) -> float | None:
    if supplement_dose_value is None:
        return None

    supp_factor = unit_to_mg(supplement_dose_unit or "")
    food_factor = unit_to_mg(nutrient_unit or "")
    if supp_factor is None or food_factor is None:
        return None

    dose_mg = float(supplement_dose_value) * supp_factor
    food_mg_per_100g = float(nutrient_amount_per_100g) * food_factor
    if food_mg_per_100g <= 0:
        return None

    return (dose_mg / food_mg_per_100g) * 100.0


def format_float(value: float, decimals: int = 2) -> str:
    txt = f"{value:.{decimals}f}"
    return txt.rstrip("0").rstrip(".") if "." in txt else txt


@functools.lru_cache(maxsize=1)
def load_whole_food_prices() -> list[dict[str, str]]:
    if not PRICE_DB_PATH.exists():
        return []
    rows: list[dict[str, str]] = []
    try:
        with PRICE_DB_PATH.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                keyword = normalize_lookup_key(str(row.get("food_keyword", "")))
                if not keyword:
                    continue
                rows.append(
                    {
                        "food_keyword": keyword,
                        "country": str(row.get("country", "Global") or "Global").strip(),
                        "currency": str(row.get("currency", "USD") or "USD").strip().upper(),
                        "price_per_kg": str(row.get("price_per_kg", "") or "").strip(),
                        "source_name": str(row.get("source_name", "") or "").strip(),
                        "source_type": str(row.get("source_type", "") or "").strip(),
                        "source_url": str(row.get("source_url", "") or "").strip(),
                        "last_updated": str(row.get("last_updated", "") or "").strip(),
                        "ean": str(row.get("ean", "") or "").strip(),
                    }
                )
    except Exception:
        return []
    return rows


def _confidence_label(score: float) -> str:
    if score >= 0.8:
        return "high"
    if score >= 0.55:
        return "medium"
    return "low"


def _parse_amount(value: str) -> float | None:
    txt = (value or "").strip().replace(",", ".")
    if not txt:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", txt)
    if not match:
        return None
    try:
        return float(match.group(0))
    except Exception:
        return None


def _extract_pack_kg(text: str) -> float | None:
    source = (text or "").lower()
    kg_match = re.search(r"(\d+(?:[\.,]\d+)?)\s*kg", source)
    if kg_match:
        return float(kg_match.group(1).replace(",", "."))

    g_match = re.search(r"(\d+(?:[\.,]\d+)?)\s*g\b", source)
    if g_match:
        return float(g_match.group(1).replace(",", ".")) / 1000.0

    lb_match = re.search(r"(\d+(?:[\.,]\d+)?)\s*lb\b", source)
    if lb_match:
        return float(lb_match.group(1).replace(",", ".")) * 0.45359237

    oz_match = re.search(r"(\d+(?:[\.,]\d+)?)\s*oz\b", source)
    if oz_match:
        return float(oz_match.group(1).replace(",", ".")) * 0.0283495231

    return None


def _extract_ean_from_text(text: str) -> str:
    tokens = re.findall(r"\b\d{8,14}\b", text or "")
    if not tokens:
        return ""
    tokens.sort(key=len, reverse=True)
    return tokens[0]


def _extract_price_per_kg_from_text(text: str, currency: str) -> float | None:
    text = re.sub(r"\s+", " ", text or "")
    if not text:
        return None

    if currency == "EUR":
        per_kg = re.search(r"(\d{1,4}(?:[\.,]\d{1,3})?)\s*€\s*/\s*(?:1\s*)?kg", text, re.I)
        if per_kg:
            return float(per_kg.group(1).replace(",", "."))
        per_100g = re.search(r"(\d{1,4}(?:[\.,]\d{1,3})?)\s*€\s*/\s*100\s*g", text, re.I)
        if per_100g:
            return float(per_100g.group(1).replace(",", ".")) * 10.0

    if currency == "USD":
        per_lb = re.search(r"\$(\d{1,4}(?:\.\d{1,3})?)\s*/\s*lb", text, re.I)
        if per_lb:
            return float(per_lb.group(1)) * 2.20462
        per_kg = re.search(r"\$(\d{1,4}(?:\.\d{1,3})?)\s*/\s*kg", text, re.I)
        if per_kg:
            return float(per_kg.group(1))

    if currency == "GBP":
        per_kg = re.search(r"(\d{1,4}(?:[\.,]\d{1,3})?)\s*£\s*/\s*(?:1\s*)?kg", text, re.I)
        if per_kg:
            return float(per_kg.group(1).replace(",", "."))
        per_100g = re.search(r"(\d{1,4}(?:[\.,]\d{1,3})?)\s*£\s*/\s*100\s*g", text, re.I)
        if per_100g:
            return float(per_100g.group(1).replace(",", ".")) * 10.0

    generic_per_kg = re.search(r"(\d{1,4}(?:[\.,]\d{1,3})?)\s*/\s*(?:1\s*)?kg", text, re.I)
    if generic_per_kg:
        return float(generic_per_kg.group(1).replace(",", "."))

    generic_per_100g = re.search(r"(\d{1,4}(?:[\.,]\d{1,3})?)\s*/\s*100\s*g", text, re.I)
    if generic_per_100g:
        return float(generic_per_100g.group(1).replace(",", ".")) * 10.0

    return None


def _build_offer(
    *,
    food_name: str,
    title: str,
    country: str,
    currency: str,
    price_per_kg: float,
    source_name: str,
    source_type: str,
    source_url: str = "",
    ean: str = "",
    last_updated: str = "",
    pack_kg: float | None = None,
    note: str = "",
) -> dict[str, Any]:
    return {
        "canonical_food": food_name,
        "title": title or food_name,
        "ean": ean,
        "pack_kg": pack_kg,
        "price_per_kg": float(price_per_kg),
        "currency": (currency or "USD").upper(),
        "country": country,
        "source": source_name,
        "source_type": source_type,
        "source_url": source_url,
        "last_updated": last_updated,
        "note": note,
    }


def lookup_local_price_offers(food_name: str, country: str, currency: str) -> list[dict[str, Any]]:
    food_key = normalize_lookup_key(food_name)
    if not food_key:
        return []

    offers: list[dict[str, Any]] = []
    for row in load_whole_food_prices():
        keyword = row.get("food_keyword", "")
        if not keyword or (keyword not in food_key and food_key not in keyword):
            continue
        price = _parse_amount(str(row.get("price_per_kg", "")))
        if price is None or price <= 0:
            continue

        row_country = str(row.get("country", "Global") or "Global")
        row_currency = str(row.get("currency", currency) or currency).upper()
        if row_country not in {country, "Global"}:
            continue

        offers.append(
            _build_offer(
                food_name=food_name,
                title=keyword,
                country=row_country,
                currency=row_currency,
                price_per_kg=price,
                source_name=str(row.get("source_name", "Local DB") or "Local DB"),
                source_type=str(row.get("source_type", "local_db") or "local_db"),
                source_url=str(row.get("source_url", "") or ""),
                ean=str(row.get("ean", "") or ""),
                last_updated=str(row.get("last_updated", "") or ""),
            )
        )

    return offers


def fetch_market_price_offers(food_name: str, country: str, currency: str, market: str) -> list[dict[str, Any]]:
    market_choice = market
    if market_choice == "Auto":
        market_choice = COUNTRY_PRICE_CONFIG.get(country, {}).get("default_market", "Auto")

    url = ""
    if market_choice == "Rewe":
        url = f"https://shop.rewe.de/search/{quote_plus(food_name)}"
    elif market_choice == "Walmart":
        url = f"https://www.walmart.com/search?q={quote_plus(food_name)}"
    else:
        return []

    try:
        response = requests.get(
            url,
            timeout=12,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
    except Exception:
        return []

    if response.status_code != 200:
        return []

    price_per_kg = _extract_price_per_kg_from_text(response.text, currency)
    if price_per_kg is None:
        return []

    return [
        _build_offer(
            food_name=food_name,
            title=food_name,
            country=country,
            currency=currency,
            price_per_kg=float(price_per_kg),
            source_name=market_choice,
            source_type="market_scrape",
            source_url=url,
            last_updated=datetime.now(timezone.utc).date().isoformat(),
        )
    ]


def fetch_serpapi_shopping_offers(food_name: str, country: str, currency: str) -> list[dict[str, Any]]:
    if not SERPAPI_API_KEY:
        return []

    params = {
        "engine": "google_shopping",
        "q": food_name,
        "hl": "en",
        "gl": COUNTRY_GL_MAP.get(country, "us"),
        "num": "10",
        "api_key": SERPAPI_API_KEY,
    }
    try:
        response = requests.get("https://serpapi.com/search.json", params=params, timeout=18)
    except Exception:
        return []
    if response.status_code != 200:
        return []

    try:
        payload = response.json()
    except Exception:
        return []

    offers: list[dict[str, Any]] = []
    for item in payload.get("shopping_results", []) or []:
        title = str(item.get("title", "") or "").strip()
        if not title:
            continue

        extracted_price = item.get("extracted_price")
        if isinstance(extracted_price, (int, float)):
            total_price = float(extracted_price)
        else:
            total_price = _parse_amount(str(item.get("price", "") or ""))

        blob = " ".join(
            [
                title,
                str(item.get("price", "") or ""),
                str(item.get("delivery", "") or ""),
                str(item.get("extensions", "") or ""),
                str(item.get("snippet", "") or ""),
            ]
        )
        unit_price_per_kg = _extract_price_per_kg_from_text(blob, currency)
        pack_kg = _extract_pack_kg(blob)
        if unit_price_per_kg is None and total_price is not None and pack_kg and pack_kg > 0:
            unit_price_per_kg = total_price / pack_kg

        if unit_price_per_kg is None or unit_price_per_kg <= 0:
            continue

        currency_out = str(item.get("currency", "") or "").upper() or currency
        link = str(item.get("link", "") or "")
        ean = _extract_ean_from_text(f"{title} {link} {item.get('product_id', '')}")
        offers.append(
            _build_offer(
                food_name=food_name,
                title=title,
                country=country,
                currency=currency_out,
                price_per_kg=unit_price_per_kg,
                source_name="SerpApi Google Shopping",
                source_type="serpapi_google_shopping",
                source_url=link,
                ean=ean,
                last_updated=datetime.now(timezone.utc).date().isoformat(),
                pack_kg=pack_kg,
            )
        )

    return offers


def fetch_dataforseo_shopping_offers(food_name: str, country: str, currency: str) -> list[dict[str, Any]]:
    if not DATAFORSEO_LOGIN or not DATAFORSEO_PASSWORD:
        return []

    payload = [
        {
            "keyword": food_name,
            "location_name": COUNTRY_DATAFORSEO_LOCATION.get(country, "United States"),
            "language_name": "English",
            "device": "desktop",
            "os": "windows",
            "depth": 20,
        }
    ]
    try:
        response = requests.post(
            "https://api.dataforseo.com/v3/serp/google/shopping/live/advanced",
            auth=(DATAFORSEO_LOGIN, DATAFORSEO_PASSWORD),
            json=payload,
            timeout=20,
        )
    except Exception:
        return []
    if response.status_code != 200:
        return []

    try:
        data = response.json()
    except Exception:
        return []

    offers: list[dict[str, Any]] = []
    tasks = data.get("tasks", []) or []
    for task in tasks:
        results = task.get("result", []) or []
        for result in results:
            items = result.get("items", []) or []
            for item in items:
                title = str(item.get("title", "") or "").strip()
                if not title:
                    continue
                total_price = _parse_amount(str(item.get("price", "") or item.get("current_price", "") or ""))
                unit_blob = " ".join(
                    [
                        title,
                        str(item.get("description", "") or ""),
                        str(item.get("price", "") or ""),
                        str(item.get("price_from", "") or ""),
                        str(item.get("price_to", "") or ""),
                    ]
                )
                unit_price_per_kg = _extract_price_per_kg_from_text(unit_blob, currency)
                pack_kg = _extract_pack_kg(unit_blob)
                if unit_price_per_kg is None and total_price is not None and pack_kg and pack_kg > 0:
                    unit_price_per_kg = total_price / pack_kg

                if unit_price_per_kg is None or unit_price_per_kg <= 0:
                    continue

                url = str(item.get("url", "") or "")
                ean = _extract_ean_from_text(f"{title} {url}")
                offers.append(
                    _build_offer(
                        food_name=food_name,
                        title=title,
                        country=country,
                        currency=currency,
                        price_per_kg=unit_price_per_kg,
                        source_name="DataForSEO Google Shopping",
                        source_type="dataforseo_google_shopping",
                        source_url=url,
                        ean=ean,
                        last_updated=datetime.now(timezone.utc).date().isoformat(),
                        pack_kg=pack_kg,
                    )
                )

    return offers


def estimate_price_with_llm(food_name: str, country: str, currency: str) -> dict[str, Any] | None:
    if not (OPENROUTER_API_KEY or OPENAI_API_KEY or GITHUB_MODELS_TOKEN):
        return None

    system_prompt = (
        "You estimate grocery prices conservatively for a specific country. "
        "Return JSON only."
    )
    user_prompt = (
        "Return strict JSON object only with keys: "
        "price_per_kg (number), currency (string), assumptions (string).\n\n"
        f"Food: {food_name}\nCountry: {country}\nCurrency: {currency}\n"
        "Use realistic mainstream supermarket pricing."
    )
    llm_out = call_openrouter_text(system_prompt, user_prompt)
    candidate = clean_json_block(llm_out)
    if not candidate:
        return None
    try:
        parsed = json.loads(candidate)
        price = float(parsed.get("price_per_kg"))
        if price <= 0:
            return None
        return {
            "price_per_kg": price,
            "currency": str(parsed.get("currency", currency) or currency),
            "source": "LLM estimate",
            "source_type": "llm_estimate",
            "confidence": "low",
            "note": str(parsed.get("assumptions", "")),
        }
    except Exception:
        return None


def _offer_match_features(food_name: str, offer: dict[str, Any], ean_hint: str = "") -> dict[str, Any]:
    normalized_food = normalize_lookup_key(food_name)
    title = str(offer.get("title", "") or "")
    normalized_title = normalize_lookup_key(title)
    offer_ean = str(offer.get("ean", "") or "")

    if ean_hint and offer_ean and ean_hint == offer_ean:
        return {"method": "ean_exact", "score": 1.0}

    food_tokens = set([tok for tok in normalized_food.split() if tok])
    title_tokens = set([tok for tok in normalized_title.split() if tok])
    overlap = (len(food_tokens & title_tokens) / len(food_tokens)) if food_tokens else 0.0
    sequence = difflib.SequenceMatcher(None, normalized_food, normalized_title).ratio()
    pack_bonus = 0.1 if offer.get("pack_kg") else 0.0
    score = min(1.0, max(overlap, sequence) + pack_bonus)
    method = "pack_title" if offer.get("pack_kg") else "title_similarity"
    return {"method": method, "score": score}


def _freshness_score(last_updated: str) -> float:
    if not last_updated:
        return 0.5
    try:
        dt = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        days = max(0, (datetime.now(timezone.utc) - dt).days)
    except Exception:
        return 0.5

    if days <= 7:
        return 1.0
    if days <= 30:
        return 0.85
    if days <= 90:
        return 0.65
    return 0.45


def _geo_score(offer_country: str, target_country: str) -> float:
    if offer_country == target_country:
        return 1.0
    if offer_country == "Global":
        return 0.75
    return 0.55


def _rank_price_offers(
    offers: list[dict[str, Any]],
    food_name: str,
    country: str,
    currency: str,
    grams_needed: float | None,
    ean_hint: str = "",
) -> list[dict[str, Any]]:
    valid: list[dict[str, Any]] = []
    for offer in offers:
        try:
            ppk = float(offer.get("price_per_kg"))
        except Exception:
            continue
        if ppk <= 0:
            continue
        normalized = dict(offer)
        normalized["price_per_kg"] = ppk
        valid.append(normalized)

    if not valid:
        return []

    min_price = min(float(o["price_per_kg"]) for o in valid)
    for offer in valid:
        price_per_kg = float(offer["price_per_kg"])
        cost_to_meet = None
        if grams_needed is not None and grams_needed > 0:
            cost_to_meet = (float(grams_needed) / 1000.0) * price_per_kg
        offer["cost_to_meet_dose"] = cost_to_meet

    min_cost = min(
        [float(o["cost_to_meet_dose"]) for o in valid if o.get("cost_to_meet_dose") is not None] or [min_price]
    )

    for offer in valid:
        source_type = str(offer.get("source_type", "") or "")
        source_rel = SOURCE_RELIABILITY_SCORE.get(source_type, 0.5)
        match_meta = _offer_match_features(food_name, offer, ean_hint=ean_hint)
        freshness = _freshness_score(str(offer.get("last_updated", "") or ""))
        geo = _geo_score(str(offer.get("country", "Global") or "Global"), country)

        econ_price = min_price / float(offer["price_per_kg"])
        offer_cost = offer.get("cost_to_meet_dose")
        if offer_cost is None or offer_cost <= 0:
            econ_dose = econ_price
        else:
            econ_dose = min_cost / float(offer_cost)
        economics = max(0.0, min(1.0, (econ_price + econ_dose) / 2.0))

        currency_penalty = 0.08 if str(offer.get("currency", currency)).upper() != currency.upper() else 0.0
        ean_bonus = 0.12 if str(match_meta.get("method", "")) == "ean_exact" else 0.0
        final_score = (
            (PRICE_RANKING_WEIGHTS["source_reliability"] * source_rel)
            + (PRICE_RANKING_WEIGHTS["match_quality"] * float(match_meta["score"]))
            + (PRICE_RANKING_WEIGHTS["freshness"] * freshness)
            + (PRICE_RANKING_WEIGHTS["geo"] * geo)
            + (PRICE_RANKING_WEIGHTS["economics"] * economics)
            + ean_bonus
            - currency_penalty
        )

        offer["match_method"] = match_meta["method"]
        offer["match_score"] = round(float(match_meta["score"]), 4)
        offer["source_reliability"] = round(source_rel, 4)
        offer["freshness_score"] = round(freshness, 4)
        offer["geo_score"] = round(geo, 4)
        offer["economics_score"] = round(economics, 4)
        offer["final_score"] = round(final_score, 4)
        offer["confidence"] = _confidence_label(final_score)

    valid.sort(key=lambda o: (float(o.get("final_score", 0.0)), -float(o.get("price_per_kg", 1e9))), reverse=True)
    return valid


def _has_strong_local_offer(offers: list[dict[str, Any]], country: str, currency: str) -> bool:
    for offer in offers:
        offer_country = str(offer.get("country", "") or "")
        offer_currency = str(offer.get("currency", "") or "").upper()
        source_type = str(offer.get("source_type", "") or "")
        reliability = SOURCE_RELIABILITY_SCORE.get(source_type, 0.5)
        if offer_country in {country, "Global"} and offer_currency == currency.upper() and reliability >= 0.88:
            return True
    return False


def get_food_price_estimate(
    food_name: str,
    country: str,
    currency: str,
    market: str,
    enable_live: bool,
    grams_needed: float | None,
    ean_hint: str = "",
    use_serpapi: bool = True,
    use_dataforseo: bool = True,
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    local_candidates = lookup_local_price_offers(food_name, country, currency)
    candidates.extend(local_candidates)

    should_query_live = enable_live and not _has_strong_local_offer(local_candidates, country, currency)

    if should_query_live:
        candidates.extend(fetch_market_price_offers(food_name, country, currency, market))
        if use_serpapi:
            candidates.extend(fetch_serpapi_shopping_offers(food_name, country, currency))
        if use_dataforseo:
            candidates.extend(fetch_dataforseo_shopping_offers(food_name, country, currency))

    if should_query_live:
        llm_est = estimate_price_with_llm(food_name, country, currency)
        if llm_est and llm_est.get("price_per_kg") is not None:
            candidates.append(
                _build_offer(
                    food_name=food_name,
                    title=food_name,
                    country=country,
                    currency=str(llm_est.get("currency", currency) or currency),
                    price_per_kg=float(llm_est.get("price_per_kg")),
                    source_name=str(llm_est.get("source", "LLM estimate") or "LLM estimate"),
                    source_type=str(llm_est.get("source_type", "llm_estimate") or "llm_estimate"),
                    source_url="",
                    note=str(llm_est.get("note", "") or ""),
                    last_updated=datetime.now(timezone.utc).date().isoformat(),
                )
            )

    ranked = _rank_price_offers(
        candidates,
        food_name=food_name,
        country=country,
        currency=currency,
        grams_needed=grams_needed,
        ean_hint=ean_hint,
    )
    if not ranked:
        return None

    best = ranked[0]
    best["audit_top_candidates"] = ranked[:3]
    return best


@functools.lru_cache(maxsize=1)
def load_local_meal_recipes() -> list[dict[str, Any]]:
    all_raw_items: list[dict[str, Any]] = []
    for path in [MEAL_RECIPES_DB_PATH, MEAL_RECIPES_FITNESS_PACK_PATH]:
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(raw, list):
            all_raw_items.extend([x for x in raw if isinstance(x, dict)])

    if not all_raw_items:
        return []

    recipes: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for item in all_raw_items:
        name = str(item.get("name", "") or "").strip()
        if not name:
            continue
        dedupe_key = normalize_lookup_key(name)
        if dedupe_key in seen_names:
            continue
        seen_names.add(dedupe_key)
        ingredients = item.get("ingredients", [])
        if not isinstance(ingredients, list):
            continue
        normalized_ingredients: list[dict[str, Any]] = []
        for ing in ingredients:
            if not isinstance(ing, dict):
                continue
            ing_name = str(ing.get("name", "") or "").strip()
            try:
                ing_grams = float(ing.get("grams", 0) or 0)
            except Exception:
                ing_grams = 0.0
            if ing_name and ing_grams > 0:
                normalized_ingredients.append({"name": ing_name, "grams": ing_grams})
        if not normalized_ingredients:
            continue
        recipes.append(
            {
                "name": name,
                "meal_type": str(item.get("meal_type", "meal") or "meal"),
                "ingredients": normalized_ingredients,
                "steps": str(item.get("steps", "") or "").strip(),
            }
        )
    return recipes


def _ingredient_grams_for_food(recipe: dict[str, Any], food_name: str) -> float:
    target = normalize_lookup_key(food_name)
    if not target:
        return 0.0
    best = 0.0
    for ing in recipe.get("ingredients", []) or []:
        ing_name = normalize_lookup_key(str(ing.get("name", "") or ""))
        if not ing_name:
            continue
        if target in ing_name or ing_name in target:
            try:
                grams = float(ing.get("grams", 0) or 0)
            except Exception:
                grams = 0.0
            if grams > best:
                best = grams
    return best


@functools.lru_cache(maxsize=1)
def load_dietary_profiles() -> list[dict[str, Any]]:
    if not DIETARY_PROFILES_PATH.exists():
        return [
            {
                "id": "none",
                "label": "No restriction",
                "description": "No dietary filtering",
                "avoid_keywords": [],
            }
        ]
    try:
        raw = json.loads(DIETARY_PROFILES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return [{"id": "none", "label": "No restriction", "description": "No dietary filtering", "avoid_keywords": []}]
    if not isinstance(raw, list):
        return [{"id": "none", "label": "No restriction", "description": "No dietary filtering", "avoid_keywords": []}]

    profiles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        pid = normalize_lookup_key(str(item.get("id", "") or ""))
        label = str(item.get("label", "") or "").strip()
        if not pid or not label or pid in seen:
            continue
        seen.add(pid)
        avoid = item.get("avoid_keywords", [])
        if not isinstance(avoid, list):
            avoid = []
        profiles.append(
            {
                "id": pid,
                "label": label,
                "description": str(item.get("description", "") or "").strip(),
                "avoid_keywords": [normalize_lookup_key(str(x)) for x in avoid if str(x).strip()],
            }
        )

    if not profiles:
        profiles.append({"id": "none", "label": "No restriction", "description": "No dietary filtering", "avoid_keywords": []})
    return profiles


def _recipe_ingredient_text(recipe: dict[str, Any]) -> str:
    parts: list[str] = []
    for ing in recipe.get("ingredients", []) or []:
        parts.append(str(ing.get("name", "") or ""))
    return normalize_lookup_key(" ".join(parts))


def apply_meal_filters(
    meals: list[dict[str, Any]],
    profile: dict[str, Any] | None,
    must_exclude_ingredient: str,
) -> list[dict[str, Any]]:
    if not meals:
        return []

    avoid_keywords = []
    if profile:
        avoid_keywords = [normalize_lookup_key(str(x)) for x in (profile.get("avoid_keywords", []) or []) if str(x).strip()]
    must_exclude_token = normalize_lookup_key(must_exclude_ingredient)

    filtered: list[dict[str, Any]] = []
    for meal in meals:
        ingredient_blob = _recipe_ingredient_text(meal)
        if not ingredient_blob:
            continue

        blocked = False
        for kw in avoid_keywords:
            if kw and kw in ingredient_blob:
                blocked = True
                break
        if blocked:
            continue

        if must_exclude_token and must_exclude_token in ingredient_blob:
            continue

        filtered.append(meal)

    return filtered


def _evaluate_recipe_coverage(recipe: dict[str, Any], requirements: list[dict[str, Any]]) -> dict[str, Any]:
    covered: list[str] = []
    uncovered: list[str] = []
    for req in requirements:
        component = str(req.get("component", "") or "")
        food_name = str(req.get("food_name", "") or "")
        grams_needed = req.get("grams_needed")
        try:
            needed = float(grams_needed)
        except Exception:
            needed = 0.0
        if not component or not food_name or needed <= 0:
            continue

        present_grams = _ingredient_grams_for_food(recipe, food_name)
        if present_grams >= needed:
            covered.append(component)
        else:
            uncovered.append(component)

    denominator = max(1, len(covered) + len(uncovered))
    ratio = len(covered) / denominator
    return {
        "covered_components": covered,
        "uncovered_components": uncovered,
        "covered_count": len(covered),
        "coverage_ratio": ratio,
        "full_coverage": len(uncovered) == 0 and len(covered) > 0,
    }


def find_local_meal_suggestions(requirements: list[dict[str, Any]], max_results: int = 3) -> list[dict[str, Any]]:
    recipes = load_local_meal_recipes()
    if not recipes:
        return []

    scored: list[dict[str, Any]] = []
    for recipe in recipes:
        coverage = _evaluate_recipe_coverage(recipe, requirements)
        if coverage["covered_count"] <= 0:
            continue
        scored.append(
            {
                **recipe,
                **coverage,
                "source_type": "local_recipe_db",
                "source": "Local Recipe DB",
            }
        )

    if not scored:
        return []

    scored.sort(
        key=lambda r: (
            1 if r.get("full_coverage") else 0,
            float(r.get("coverage_ratio", 0.0)),
            int(r.get("covered_count", 0)),
        ),
        reverse=True,
    )
    return scored[:max_results]


def generate_llm_meal_suggestions(requirements: list[dict[str, Any]], max_results: int = 3) -> list[dict[str, Any]]:
    if not requirements:
        return []
    if not (OPENROUTER_API_KEY or OPENAI_API_KEY or GITHUB_MODELS_TOKEN):
        return []

    target_lines: list[str] = []
    for req in requirements:
        component = str(req.get("component", "") or "").strip()
        food_name = str(req.get("food_name", "") or "").strip()
        grams_needed = req.get("grams_needed")
        if not component or not food_name or grams_needed is None:
            continue
        try:
            grams_txt = format_float(float(grams_needed), 1)
        except Exception:
            continue
        target_lines.append(f"- {component}: include at least {grams_txt} g of {food_name}")

    if not target_lines:
        return []

    system_prompt = (
        "You are a nutrition-focused meal planner. "
        "Return strict JSON only. Keep meals practical and ingredient-focused."
    )
    user_prompt = (
        "Return a strict JSON array with up to "
        f"{max_results} meal objects. Each object keys: "
        "name (string), meal_type (string), ingredients (array of {name, grams}), steps (string). "
        "Each meal should try to cover as many targets as possible; exceeding targets is allowed.\n\n"
        "Targets:\n"
        + "\n".join(target_lines)
    )

    raw = call_openrouter_text(system_prompt, user_prompt)
    candidate = clean_json_block(raw)
    if not candidate:
        return []
    try:
        parsed = json.loads(candidate)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []

    meals: list[dict[str, Any]] = []
    for item in parsed[:max_results]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "") or "").strip()
        ingredients = item.get("ingredients", [])
        if not name or not isinstance(ingredients, list):
            continue
        normalized_ingredients: list[dict[str, Any]] = []
        for ing in ingredients:
            if not isinstance(ing, dict):
                continue
            ing_name = str(ing.get("name", "") or "").strip()
            try:
                ing_grams = float(ing.get("grams", 0) or 0)
            except Exception:
                ing_grams = 0.0
            if ing_name and ing_grams > 0:
                normalized_ingredients.append({"name": ing_name, "grams": ing_grams})
        if not normalized_ingredients:
            continue

        meal = {
            "name": name,
            "meal_type": str(item.get("meal_type", "meal") or "meal"),
            "ingredients": normalized_ingredients,
            "steps": str(item.get("steps", "") or "").strip(),
            "source_type": "llm_generated_recipe",
            "source": "AI generated",
        }
        meal.update(_evaluate_recipe_coverage(meal, requirements))
        meals.append(meal)

    meals.sort(
        key=lambda r: (
            1 if r.get("full_coverage") else 0,
            float(r.get("coverage_ratio", 0.0)),
            int(r.get("covered_count", 0)),
        ),
        reverse=True,
    )
    return meals[:max_results]


def generate_template_meal_suggestions(requirements: list[dict[str, Any]], max_results: int = 3) -> list[dict[str, Any]]:
    if not requirements:
        return []

    per_food_required: dict[str, float] = {}
    canonical_name: dict[str, str] = {}
    for req in requirements:
        food_name = str(req.get("food_name", "") or "").strip()
        grams_needed = req.get("grams_needed")
        if not food_name or grams_needed is None:
            continue
        try:
            grams = float(grams_needed)
        except Exception:
            continue
        if grams <= 0:
            continue
        key = normalize_lookup_key(food_name)
        per_food_required[key] = max(per_food_required.get(key, 0.0), grams)
        canonical_name[key] = food_name

    if not per_food_required:
        return []

    keys = sorted(per_food_required.keys(), key=lambda k: per_food_required[k], reverse=True)
    groups: list[list[str]] = []
    groups.append(keys[: min(4, len(keys))])
    groups.append(keys[: min(3, len(keys))])
    if len(keys) > 1:
        groups.append(keys[1 : min(5, len(keys))])

    meals: list[dict[str, Any]] = []
    for i, group in enumerate(groups[:max_results], start=1):
        ingredients: list[dict[str, Any]] = []
        for k in group:
            grams = round(per_food_required.get(k, 0.0) * 1.05, 1)
            if grams <= 0:
                continue
            ingredients.append({"name": canonical_name.get(k, k), "grams": grams})
        if not ingredients:
            continue

        meal = {
            "name": f"SuppSwap quick meal {i}",
            "meal_type": "meal",
            "ingredients": ingredients,
            "steps": "Prepare and combine all listed ingredients into one meal; adjust seasoning and cooking style as preferred.",
            "source_type": "template_generated_recipe",
            "source": "SuppSwap template fallback",
        }
        meal.update(_evaluate_recipe_coverage(meal, requirements))
        meals.append(meal)

    return meals[:max_results]


def resolve_cheapest_meal_requirements(
    component_candidates: list[dict[str, Any]],
    country: str,
    currency: str,
    market: str,
    enable_live: bool,
    use_serpapi: bool,
    use_dataforseo: bool,
    price_cache: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    requirements: list[dict[str, Any]] = []

    for cand in component_candidates:
        component = str(cand.get("component", "") or "")
        dose_value = cand.get("dose_value")
        dose_unit = str(cand.get("dose_unit", "") or "")
        foods = cand.get("foods", []) or []
        ean_hint = _extract_ean_from_text(component)

        best: dict[str, Any] | None = None
        for food in foods:
            food_name = str(food.get("food_description", "") or "").strip()
            if not food_name:
                continue
            try:
                amount_per_100g = float(food.get("amount_per_100g", 0.0) or 0.0)
            except Exception:
                amount_per_100g = 0.0
            nutrient_unit = str(food.get("unit", "") or "")
            grams_needed = grams_needed_to_match_dose(dose_value, dose_unit, amount_per_100g, nutrient_unit)
            if grams_needed is None or grams_needed <= 0:
                continue

            cache_key = (
                "meal_cheapest",
                normalize_lookup_key(food_name),
                country,
                currency,
                market,
                str(enable_live),
                str(use_serpapi),
                str(use_dataforseo),
                format_float(float(grams_needed), 3),
                ean_hint,
            )

            cached = price_cache.get(cache_key)
            if cached and cached.get("price_per_kg") is not None:
                price_info = cached
            else:
                price_info = get_food_price_estimate(
                    food_name,
                    country,
                    currency,
                    market,
                    enable_live,
                    grams_needed,
                    ean_hint,
                    use_serpapi,
                    use_dataforseo,
                )
                if price_info and price_info.get("price_per_kg") is not None:
                    price_cache[cache_key] = price_info

            if not price_info or price_info.get("price_per_kg") is None:
                continue

            try:
                required_cost = (float(grams_needed) / 1000.0) * float(price_info.get("price_per_kg"))
            except Exception:
                continue

            if best is None or required_cost < float(best.get("required_cost", 1e18)):
                best = {
                    "component": component,
                    "food_name": food_name,
                    "grams_needed": float(grams_needed),
                    "required_cost": float(required_cost),
                }

        if best:
            requirements.append(
                {
                    "component": best["component"],
                    "food_name": best["food_name"],
                    "grams_needed": best["grams_needed"],
                }
            )

    return requirements, price_cache


def render_overview_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""

    style = """
<style>
.overview-wrap { overflow-x: auto; margin-top: 0.25rem; }
.overview-table { width: 100%; border-collapse: collapse; font-size: 0.92rem; }
.overview-table th, .overview-table td { border: 1px solid #d9d9d9; padding: 8px; vertical-align: top; text-align: left; }
.overview-table th { background: #f4f4f4; font-weight: 600; }
.alt-scroll { max-height: 180px; overflow-y: auto; white-space: pre-wrap; line-height: 1.3; }
</style>
"""

    headers = [
        "What your supplement product contains",
        "How much is in there per 100g",
        "Whole food alternatives",
        "How much to eat of this whole food to match the dose in the supplement",
    ]

    html_rows: list[str] = []
    for row in rows:
        html_rows.append(
            "<tr>"
            f"<td>{row['component_label']}</td>"
            f"<td>{row['amount_per_100g_label']}</td>"
            f"<td><div class='alt-scroll'>{row['alternatives_html']}</div></td>"
            f"<td>{row['match_amount_label']}</td>"
            "</tr>"
        )

    html = (
        style
        + "<div class='overview-wrap'><table class='overview-table'>"
        + "<thead><tr>"
        + "".join([f"<th>{h}</th>" for h in headers])
        + "</tr></thead><tbody>"
        + "".join(html_rows)
        + "</tbody></table></div>"
    )
    return html


def resolve_fitness_reference_dir() -> Path | None:
    for candidate in FITNESS_REFERENCE_DIR_CANDIDATES:
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None


@functools.lru_cache(maxsize=1)
def build_rag_index() -> tuple[list[dict[str, str]], str]:
    if not RAG_INDEX_PATH.exists():
        return [], (
            "RAG index file missing. Build once with: "
            "python build_fitness_rag_index.py"
        )

    chunks: list[dict[str, str]] = []
    try:
        with RAG_INDEX_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                text = str(obj.get("text", "")).strip()
                if not text:
                    continue
                chunks.append(
                    {
                        "source": str(obj.get("source", "")),
                        "chunk_id": str(obj.get("chunk_id", "")),
                        "text": text,
                    }
                )
    except Exception as exc:
        return [], f"Failed to load RAG index: {exc}"

    if not chunks:
        return [], "RAG index is empty. Rebuild with: python build_fitness_rag_index.py"

    status = f"ok (loaded {len(chunks)} chunks)"
    if RAG_INDEX_META_PATH.exists():
        try:
            meta = json.loads(RAG_INDEX_META_PATH.read_text(encoding="utf-8"))
            files_count = int(meta.get("processed_pdf_files", 0))
            if files_count:
                status = f"ok (loaded {len(chunks)} chunks from {files_count} PDFs)"
        except Exception:
            pass
    return chunks, status


def tokenize_for_rag(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def score_chunk(query_terms: set[str], chunk_text_value: str) -> float:
    chunk_terms = tokenize_for_rag(chunk_text_value)
    if not chunk_terms:
        return 0.0
    hits = sum(1 for term in chunk_terms if term in query_terms)
    unique_hits = len(set(chunk_terms).intersection(query_terms))
    return float(hits + (2 * unique_hits))


def has_numeric_guidance(text: str) -> bool:
    return bool(
        re.search(
            r"\b\d+(?:\.\d+)?\s*(?:g|mg|mcg|ug|µg|μg|kg|g/kg|mg/kg|%)\b",
            (text or "").lower(),
        )
    )


def retrieve_rag_chunks(query: str, chunks: list[dict[str, str]], top_k: int = RAG_TOP_K) -> list[dict[str, str]]:
    query_terms = set(tokenize_for_rag(query))
    if not query_terms:
        return []

    wants_numeric = bool(
        query_terms.intersection(
            {
                "optimal",
                "dose",
                "dosing",
                "dosage",
                "intake",
                "recommended",
                "amount",
                "grams",
                "gram",
                "mg",
                "mcg",
                "ug",
                "microgram",
            }
        )
    )

    scored: list[tuple[float, dict[str, str]]] = []
    for chunk in chunks:
        chunk_text_value = chunk.get("text", "")
        score = score_chunk(query_terms, chunk_text_value)
        if wants_numeric and has_numeric_guidance(chunk_text_value):
            score += 6.0
        if score > 0:
            scored.append((score, chunk))

    if not scored:
        return []

    scored.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in scored[:top_k]]


def answer_rag_question(query: str, chunks: list[dict[str, str]]) -> tuple[str, list[str]]:
    retrieved = retrieve_rag_chunks(query, chunks)
    if not retrieved:
        return "No relevant context found in the reference library.", []

    citations = sorted({chunk.get("source", "") for chunk in retrieved if chunk.get("source")})
    context_blocks = []
    for chunk in retrieved:
        source = chunk.get("source", "unknown")
        text = chunk.get("text", "")
        context_blocks.append(f"SOURCE: {source}\n{text}")

    context = "\n\n".join(context_blocks)
    system_prompt = (
        "You are a practical fitness and nutrition evidence assistant. "
        "Answer only using the provided reference excerpts. "
        "If the context is insufficient, say so clearly. "
        "If numeric dosage/intake values are present in excerpts, include them explicitly with units."
    )
    user_prompt = (
        f"Question:\n{query}\n\n"
        f"Reference excerpts:\n{context}\n\n"
        "Return a concise answer only. "
        "Do not output a separate Sources line. "
        "Do not say values are missing if numeric values are present in the excerpts."
    )

    answer = call_openrouter_text(system_prompt, user_prompt)
    if answer:
        return answer, citations

    fallback = retrieved[0].get("text", "")[:600]
    return f"Could not use LLM provider. Top retrieved excerpt:\n\n{fallback}", citations


def build_usda_matches(components: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    conn = try_open_usda_db()
    if conn is None:
        return [], [], "USDA ranking DB missing"

    aliases = load_component_aliases()
    proxy_rules = load_component_proxy_rules()
    nutrient_rows = conn.execute(
        """
        SELECT id, nutrient_name, unit_name
        FROM nutrients
        """
    ).fetchall()
    nutrient_names = [str(row[1] or "") for row in nutrient_rows]

    foods_cache: dict[int, list[dict[str, Any]]] = {}

    def get_cached_foods(nutrient_id: int) -> list[dict[str, Any]]:
        if nutrient_id not in foods_cache:
            foods_cache[nutrient_id] = get_top_ranked_foods(conn, nutrient_id, OVERVIEW_ALT_LIMIT)
        return foods_cache[nutrient_id]

    summaries: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    seen: set[str] = set()

    try:
        for item in components:
            component = normalize_lookup_key(str(item.get("component", "")))
            if not component or component in seen:
                continue
            seen.add(component)

            nutrient = resolve_component_to_nutrient(
                conn,
                component,
                aliases,
                proxy_rules,
                nutrient_rows=nutrient_rows,
                nutrient_names=nutrient_names,
            )
            if not nutrient:
                summaries.append(
                    {
                        "component": component,
                        "supplement_dose_value": item.get("dose_value"),
                        "supplement_dose_unit": item.get("dose_unit") or "",
                        "resolved_nutrient": "Not mapped",
                        "confidence": "low",
                        "top_food": "",
                        "top_amount_per_100g": "",
                    }
                )
                continue

            nutrient_id = int(nutrient["nutrient_id"])
            foods_overview = get_cached_foods(nutrient_id)
            top_foods = foods_overview[:TOP_FOODS_PER_COMPONENT]

            # Vitamin K2 (MK-4) data can be sparse in USDA for many common foods.
            # If no positive-food rows remain after filtering, fall back to K1 (phylloquinone)
            # as a transparent whole-food proxy to avoid misleading 0-value dropdown options.
            component_text = str(component or "")
            resolved_nutrient_name = str(nutrient.get("nutrient_name", "") or "")
            should_apply_k2_proxy = (
                ("k2" in component_text or "menaquinone" in resolved_nutrient_name.lower())
                and not top_foods
            )
            if should_apply_k2_proxy:
                k1_proxy = _lookup_nutrient_row(conn, "Vitamin K (phylloquinone)", nutrient_rows=nutrient_rows)
                if k1_proxy:
                    proxy_foods_preview = get_cached_foods(int(k1_proxy["nutrient_id"]))[:TOP_FOODS_PER_COMPONENT]
                    if proxy_foods_preview:
                        nutrient = {
                            **k1_proxy,
                            "confidence": "medium",
                            "match_method": "k2_to_k1_proxy_fallback",
                            "proxy_rationale": (
                                "K2 (MK-4) whole-food coverage is sparse in USDA for many items; "
                                "showing vitamin K1 (phylloquinone) rich foods as practical proxy."
                            ),
                        }
                        top_foods = proxy_foods_preview

            top_food_name = top_foods[0]["food_description"] if top_foods else ""
            top_food_amt = top_foods[0]["amount_per_100g"] if top_foods else ""
            top_food_unit = top_foods[0]["unit"] if top_foods else ""

            summaries.append(
                {
                    "component": component,
                    "supplement_dose_value": item.get("dose_value"),
                    "supplement_dose_unit": item.get("dose_unit") or "",
                    "resolved_nutrient": nutrient["nutrient_name"],
                    "confidence": nutrient["confidence"],
                    "top_food": top_food_name,
                    "top_amount_per_100g": f"{top_food_amt} {top_food_unit}" if top_food_name else "",
                }
            )
            details.append(
                {
                    "component": component,
                    "supplement_dose_value": item.get("dose_value"),
                    "supplement_dose_unit": item.get("dose_unit") or "",
                    "resolved_nutrient": nutrient["nutrient_name"],
                    "confidence": nutrient["confidence"],
                    "match_method": nutrient["match_method"],
                    "proxy_rationale": nutrient.get("proxy_rationale", ""),
                    "foods": get_cached_foods(int(nutrient["nutrient_id"])),
                }
            )
    finally:
        conn.close()

    return summaries, details, "ok"


def normalize_component_name(raw_name: str) -> str:
    text = (raw_name or "").strip()
    if not text:
        return ""

    text = text.replace("|", " ")
    text = re.sub(r"\([^)]*\)", "", text)
    text = text.split("/")[0].strip()
    text = re.sub(r"^[\-•:;,.|\s]+", "", text)
    text = re.sub(r"[\-•:;,.|\s]+$", "", text)
    text = re.sub(r"\s+", " ", text)

    lowered = text.lower()
    for prefix in [
        "includes ",
        "include ",
        "total ",
        "contains ",
        "amount per serving ",
    ]:
        if lowered.startswith(prefix):
            text = text[len(prefix):].strip()
            lowered = text.lower()

    return lowered


def parse_components_rule_based(input_text: str) -> list[dict[str, Any]]:
    if not input_text.strip():
        return []

    lines = [ln.strip() for ln in input_text.splitlines() if ln.strip()]
    dose_pattern = re.compile(
        r"(?P<name>.+?)\s+(?P<val>\d+(?:[\.,]\d+)?)\s*(?P<unit>mg|mcg|ug|µg|μg|iu|g|kcal)\b",
        re.I,
    )
    ignored_starts = (
        "supplement facts",
        "serving size",
        "servings per container",
        "% daily value",
        "*percent daily values",
        "daily value not established",
        "proprietary blend",
    )

    parsed: list[dict[str, Any]] = []
    seen: set[tuple[str, float, str]] = set()

    for line in lines:
        lowered = line.lower()
        if lowered.startswith(ignored_starts):
            continue

        match = dose_pattern.search(line)
        if not match:
            continue

        component = normalize_component_name(match.group("name"))
        if not component:
            continue

        try:
            dose_value = float(match.group("val").replace(",", "."))
        except Exception:
            continue

        dose_unit = match.group("unit").lower()
        if dose_unit in {"ug", "µg", "μg"}:
            dose_unit = "mcg"
        key = (component, dose_value, dose_unit)
        if key in seen:
            continue
        seen.add(key)

        parsed.append(
            {
                "component": component,
                "dose_value": dose_value,
                "dose_unit": dose_unit,
            }
        )

    return parsed


def merge_component_rows(
    primary: list[dict[str, Any]],
    secondary: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_components: set[str] = set()

    for row in primary + secondary:
        component = str(row.get("component", "")).strip().lower()
        if not component or component in seen_components:
            continue
        seen_components.add(component)
        merged.append(
            {
                "component": component,
                "dose_value": row.get("dose_value"),
                "dose_unit": row.get("dose_unit"),
            }
        )

    return merged


def check_openrouter_key_status() -> tuple[bool, str]:
    if not OPENROUTER_API_KEY:
        return False, "OPENROUTER_API_KEY is missing"

    reply = call_openrouter_text(
        "You are a health check endpoint.",
        "Reply with exactly: ok",
        model=OPENROUTER_MODEL_TEXT,
    )
    if reply:
        return True, "OpenRouter key/token is working"

    if LAST_OPENROUTER_ERROR:
        return False, LAST_OPENROUTER_ERROR
    return False, "OpenRouter request failed"


def check_provider_status() -> tuple[bool, str]:
    if OPENROUTER_API_KEY:
        ok, message = check_openrouter_key_status()
        if ok:
            return True, f"OpenRouter: {message}"

    if OPENAI_API_KEY:
        reply = _openai_chat(
            {
                "model": OPENAI_MODEL_TEXT,
                "temperature": 0,
                "max_tokens": 64,
                "messages": [
                    {"role": "system", "content": "You are a health check endpoint."},
                    {"role": "user", "content": "Reply with exactly: ok"},
                ],
            }
        )
        if reply:
            return True, "OpenAI: key/token is working"
        if LAST_OPENAI_ERROR:
            openai_message = LAST_OPENAI_ERROR
    else:
        openai_message = ""

    if GITHUB_MODELS_TOKEN:
        reply = _github_models_chat(
            {
                "model": GITHUB_MODELS_MODEL_TEXT,
                "temperature": 0,
                "max_tokens": 64,
                "messages": [
                    {"role": "system", "content": "You are a health check endpoint."},
                    {"role": "user", "content": "Reply with exactly: ok"},
                ],
            }
        )
        if reply:
            return True, "GitHub Models: token is working"
        if LAST_GITHUB_MODELS_ERROR:
            github_message = LAST_GITHUB_MODELS_ERROR
        else:
            github_message = ""
    else:
        github_message = ""

    if LAST_OPENROUTER_ERROR:
        return False, LAST_OPENROUTER_ERROR
    if openai_message:
        return False, openai_message
    if github_message:
        return False, github_message
    return False, "No working provider key found (OpenRouter/OpenAI/GitHub Models)"


def resolve_tesseract_cmd() -> str:
    if TESSERACT_CMD.strip():
        return TESSERACT_CMD.strip()

    from_path = shutil.which("tesseract")
    if from_path:
        return from_path

    windows_candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for candidate in windows_candidates:
        if os.path.exists(candidate):
            return candidate

    return ""


def openrouter_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost",
        "X-Title": "SuppSwap",
    }


def openai_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }


def github_models_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {GITHUB_MODELS_TOKEN}",
        "Content-Type": "application/json",
    }


def _extract_affordable_tokens(error_text: str, fallback: int = OPENROUTER_DEFAULT_MAX_TOKENS) -> int:
    m = re.search(r"can only afford (\d+)", error_text or "")
    if not m:
        return fallback
    try:
        afford = int(m.group(1))
        return max(64, afford - 50)
    except Exception:
        return fallback


def _openrouter_chat(payload: dict[str, Any]) -> str:
    global LAST_OPENROUTER_ERROR
    LAST_OPENROUTER_ERROR = ""

    payload = dict(payload)
    payload.setdefault("max_tokens", OPENROUTER_DEFAULT_MAX_TOKENS)

    try:
        response = requests.post(
            OPENROUTER_URL,
            headers=openrouter_headers(),
            json=payload,
            timeout=HTTP_TIMEOUT,
        )
    except Exception as exc:
        LAST_OPENROUTER_ERROR = f"Connection error: {exc}"
        return ""

    if response.status_code == 402:
        affordable = _extract_affordable_tokens(response.text)
        payload["max_tokens"] = affordable
        try:
            response = requests.post(
                OPENROUTER_URL,
                headers=openrouter_headers(),
                json=payload,
                timeout=HTTP_TIMEOUT,
            )
        except Exception as exc:
            LAST_OPENROUTER_ERROR = f"Connection error after 402 retry: {exc}"
            return ""

    if response.status_code != 200:
        LAST_OPENROUTER_ERROR = f"OpenRouter error {response.status_code}: {response.text[:220]}"
        return ""

    try:
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        LAST_OPENROUTER_ERROR = f"Invalid API response: {exc}"
        return ""


def _openai_chat(payload: dict[str, Any]) -> str:
    global LAST_OPENAI_ERROR
    LAST_OPENAI_ERROR = ""

    if not OPENAI_API_KEY:
        LAST_OPENAI_ERROR = "OPENAI_API_KEY is missing"
        return ""

    payload = dict(payload)
    payload.setdefault("max_tokens", OPENROUTER_DEFAULT_MAX_TOKENS)

    try:
        response = requests.post(
            OPENAI_URL,
            headers=openai_headers(),
            json=payload,
            timeout=HTTP_TIMEOUT,
        )
    except Exception as exc:
        LAST_OPENAI_ERROR = f"OpenAI connection error: {exc}"
        return ""

    if response.status_code != 200:
        LAST_OPENAI_ERROR = f"OpenAI error {response.status_code}: {response.text[:220]}"
        return ""

    try:
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        LAST_OPENAI_ERROR = f"Invalid OpenAI response: {exc}"
        return ""


def _github_models_chat(payload: dict[str, Any]) -> str:
    global LAST_GITHUB_MODELS_ERROR
    LAST_GITHUB_MODELS_ERROR = ""

    if not GITHUB_MODELS_TOKEN:
        LAST_GITHUB_MODELS_ERROR = "GITHUB_MODELS_TOKEN is missing"
        return ""

    payload = dict(payload)
    payload.setdefault("max_tokens", OPENROUTER_DEFAULT_MAX_TOKENS)

    try:
        response = requests.post(
            GITHUB_MODELS_URL,
            headers=github_models_headers(),
            json=payload,
            timeout=HTTP_TIMEOUT,
        )
    except Exception as exc:
        LAST_GITHUB_MODELS_ERROR = f"GitHub Models connection error: {exc}"
        return ""

    if response.status_code != 200:
        LAST_GITHUB_MODELS_ERROR = f"GitHub Models error {response.status_code}: {response.text[:220]}"
        return ""

    try:
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        LAST_GITHUB_MODELS_ERROR = f"Invalid GitHub Models response: {exc}"
        return ""


def call_openrouter_text(system_prompt: str, user_prompt: str, model: str | None = None) -> str:
    global LAST_TEXT_PROVIDER
    LAST_TEXT_PROVIDER = ""

    payload = {
        "model": model or OPENROUTER_MODEL_TEXT,
        "temperature": 0,
        "max_tokens": OPENROUTER_DEFAULT_MAX_TOKENS,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    if OPENROUTER_API_KEY:
        openrouter_reply = _openrouter_chat(payload)
        if openrouter_reply:
            LAST_TEXT_PROVIDER = "OpenRouter"
            return openrouter_reply

    if not OPENAI_API_KEY:
        if not GITHUB_MODELS_TOKEN:
            return ""
    else:
        openai_payload = dict(payload)
        openai_payload["model"] = OPENAI_MODEL_TEXT
        openai_reply = _openai_chat(openai_payload)
        if openai_reply:
            LAST_TEXT_PROVIDER = "OpenAI"
            return openai_reply

    if not GITHUB_MODELS_TOKEN:
        return ""

    github_payload = dict(payload)
    github_payload["model"] = GITHUB_MODELS_MODEL_TEXT
    github_reply = _github_models_chat(github_payload)
    if github_reply:
        LAST_TEXT_PROVIDER = "GitHub Models"
    return github_reply


def call_openrouter_vision_ocr(image_bytes: bytes) -> str:
    global LAST_VISION_PROVIDER
    LAST_VISION_PROVIDER = ""

    b64 = base64.b64encode(image_bytes).decode("utf-8")
    image_url = f"data:image/jpeg;base64,{b64}"

    payload = {
        "model": OPENROUTER_MODEL_VISION,
        "temperature": 0,
        "max_tokens": OPENROUTER_DEFAULT_MAX_TOKENS,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a strict OCR extractor for supplement labels. "
                    "Return only the readable label text."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Extract the full supplement facts text from this image.",
                    },
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            },
        ],
    }
    if OPENROUTER_API_KEY:
        openrouter_reply = _openrouter_chat(payload)
        if openrouter_reply:
            LAST_VISION_PROVIDER = "OpenRouter"
            return openrouter_reply

    if not OPENAI_API_KEY:
        if not GITHUB_MODELS_TOKEN:
            return ""
    else:
        openai_payload = dict(payload)
        openai_payload["model"] = OPENAI_MODEL_VISION
        openai_reply = _openai_chat(openai_payload)
        if openai_reply:
            LAST_VISION_PROVIDER = "OpenAI"
            return openai_reply

    if not GITHUB_MODELS_TOKEN:
        return ""

    github_payload = dict(payload)
    github_payload["model"] = GITHUB_MODELS_MODEL_VISION
    github_reply = _github_models_chat(github_payload)
    if github_reply:
        LAST_VISION_PROVIDER = "GitHub Models"
    return github_reply


def try_tesseract_ocr(image_bytes: bytes) -> str:
    try:
        import pytesseract

        resolved_cmd = resolve_tesseract_cmd()
        if resolved_cmd:
            pytesseract.pytesseract.tesseract_cmd = resolved_cmd

        image = Image.open(io.BytesIO(image_bytes))
        text = pytesseract.image_to_string(image)
        return text.strip()
    except Exception:
        return ""


def fetch_clean_page_text(url: str) -> str:
    try:
        response = requests.get(url, timeout=HTTP_TIMEOUT)
        if response.status_code != 200:
            return ""
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.extract()
        text = " ".join(soup.get_text(separator=" ").split())
        return text[:18000]
    except Exception:
        return ""


def extract_supplement_text_from_url(url: str) -> str:
    page_text = fetch_clean_page_text(url)
    if not page_text:
        return ""

    system_prompt = (
        "You extract supplement facts from web page text. "
        "Return plain text only with ingredients/components, serving size, and doses."
    )
    user_prompt = f"Extract supplement facts from this page content:\n\n{page_text}"
    return call_openrouter_text(system_prompt, user_prompt)


def clean_json_block(raw: str) -> str:
    if not raw:
        return ""
    txt = raw.strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```(?:json)?", "", txt).strip()
        txt = re.sub(r"```$", "", txt).strip()
    return txt


def parse_components(input_text: str) -> list[dict[str, Any]]:
    if not input_text.strip():
        return []

    system_prompt = (
        "You are a strict data extraction system for Supplement Facts labels. "
        "Extract one row per nutrient/component with numeric dose and unit into JSON only."
    )
    user_prompt = f"""
Return STRICT JSON array only.
Each item format:
{{"component":"name","dose_value":number_or_null,"dose_unit":"mg/mcg/IU/g/softgel/capsule/or null"}}

Rules:
- Component must be the nutrient or ingredient name itself, not header text.
- For bilingual lines, use the English name before '/'.
- Do not reuse the same component for all rows.
- Ignore Daily Value percentages.
- Ignore serving-size metadata headers.
- Keep only entries where dose_value is numeric and dose_unit exists.

Input:
{input_text}
"""

    regex_fallback = parse_components_rule_based(input_text)

    llm_out = call_openrouter_text(system_prompt, user_prompt)
    candidate = clean_json_block(llm_out)
    if candidate:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, list):
                normalized = []
                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    component = normalize_component_name(str(item.get("component", "")))
                    if not component:
                        continue
                    dose_raw = item.get("dose_value")
                    try:
                        dose_value = float(dose_raw)
                    except Exception:
                        continue
                    unit = str(item.get("dose_unit", "")).strip().lower()
                    if not unit:
                        continue
                    normalized.append(
                        {
                            "component": component,
                            "dose_value": dose_value,
                            "dose_unit": unit,
                        }
                    )
                normalized = [x for x in normalized if x["component"]]
                unique_components = {x["component"] for x in normalized}
                suspicious_repeated_name = len(normalized) >= 3 and len(unique_components) == 1
                if normalized and not suspicious_repeated_name:
                    return merge_component_rows(normalized, regex_fallback)
        except Exception:
            pass

    return regex_fallback


def preliminary_recommendation(component: str, budget_priority: int) -> tuple[str, str]:
    c = component.lower()

    food_first = {
        "vitamin c", "magnesium", "zinc", "iron", "potassium", "folate", "calcium", "curcumin", "curcuma"
    }
    supplement_likely = {"vitamin d", "vitamin b12", "ashwagandha", "fish oil", "omega-3", "omega 3"}

    if c in food_first:
        if budget_priority >= 4:
            return (
                "Switch to food first",
                "High chance of affordable whole-food replacement with added health synergy.",
            )
        return (
            "Likely switch to food",
            "Whole-food replacement is usually strong for this component.",
        )

    if c in supplement_likely:
        return (
            "Case-by-case: often keep supplement",
            "For this component, food-only replacement can be inconsistent or impractical for many users.",
        )

    return (
        "Case-by-case",
        "Need deeper dose and food matching in the next step of the pipeline.",
    )


def build_mobile_ui() -> None:
    import streamlit as st
    global LAST_VISION_PROVIDER

    st.set_page_config(page_title="SuppSwap", page_icon="🥗", layout="centered")

    st.markdown(
        """
<style>
.mfitness-watermark {
    position: fixed;
    right: 14px;
    bottom: 10px;
    z-index: 99999;
    pointer-events: none;
    font-size: 0.85rem;
    font-weight: 600;
    letter-spacing: 0.2px;
    color: rgba(120, 120, 120, 0.75);
    background: rgba(255, 255, 255, 0.55);
    padding: 4px 8px;
    border-radius: 8px;
}

@media (max-width: 768px) {
    .block-container {
        padding-top: 0.8rem;
        padding-left: 0.75rem;
        padding-right: 0.75rem;
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.15rem;
    }
    div[data-testid="stMetricLabel"] {
        font-size: 0.78rem;
    }
}
</style>
<div class="mfitness-watermark">© mfitness92</div>
""",
        unsafe_allow_html=True,
    )

    st.title("🥗 SuppSwap")

    st.markdown(
        """
### Welcome
I’ll guide you like a practical nutrition expert.

This app helps you replace supplement products with **single-ingredient, unprocessed foods** whenever it makes sense.
You can submit micronutrients (like vitamin C or magnesium) and also non-micronutrient supplement components
like **curcumin, fish oil, or ashwagandha**.

Then the app estimates whether you should:
- switch to food,
- combine food + supplement,
- or keep supplement,

based on practicality and economics, not hype.

Why this matters:
- Whole foods usually outperform isolated supplement compounds due to nutrient synergy.
- Whole foods often provide better long-term value and broader health effects.
- Supplements are common partly because of convenience and marketing, not always because they are superior.
"""
    )

    st.subheader("Provide your supplement input")

    if st.button("Check API key/token", use_container_width=True):
        ok, message = check_provider_status()
        if ok:
            st.success(message)
        else:
            st.error(message)

    budget_priority = st.slider(
        "How important is budget/economics for your decision?",
        min_value=1,
        max_value=5,
        value=4,
        help="Higher means you prefer the most cost-effective whole-food options.",
    )

    camera_image = st.camera_input("Take a photo of supplement label")
    uploaded_image = st.file_uploader("Upload label image from device", type=["png", "jpg", "jpeg", "webp"])
    product_url = st.text_input("Or paste a product link")
    manual_text = st.text_area(
        "Or type/paste supplement details (product or component + dose)",
        placeholder="Example: Vitamin C 500 mg\nFish oil 1000 mg\nAshwagandha 300 mg",
        height=150,
    )

    if "analysis_ready" not in st.session_state:
        st.session_state["analysis_ready"] = False
    if "analysis_components" not in st.session_state:
        st.session_state["analysis_components"] = []
    if "analysis_combined_text" not in st.session_state:
        st.session_state["analysis_combined_text"] = ""
    if "analysis_usda_cache_key" not in st.session_state:
        st.session_state["analysis_usda_cache_key"] = ""
    if "analysis_usda_summary" not in st.session_state:
        st.session_state["analysis_usda_summary"] = []
    if "analysis_usda_details" not in st.session_state:
        st.session_state["analysis_usda_details"] = []
    if "analysis_usda_status" not in st.session_state:
        st.session_state["analysis_usda_status"] = ""

    analyze = st.button("Analyze input", type="primary", use_container_width=True)

    if not analyze and not st.session_state["analysis_ready"]:
        return

    if analyze:
        extracted_chunks: list[str] = []

        with st.status("Processing", expanded=True) as status:
            status.write("Step 1/4: Collecting input")

            image_bytes = None
            if camera_image is not None:
                image_bytes = camera_image.getvalue()
            elif uploaded_image is not None:
                image_bytes = uploaded_image.read()

            if image_bytes:
                status.write("Step 2/4: OCR from image (OpenRouter → OpenAI → GitHub Models → Tesseract)")
                ocr_text = call_openrouter_vision_ocr(image_bytes)
                if not ocr_text:
                    if LAST_OPENROUTER_ERROR:
                        status.write(f"OpenRouter vision issue: {LAST_OPENROUTER_ERROR}")
                    if LAST_OPENAI_ERROR:
                        status.write(f"OpenAI vision issue: {LAST_OPENAI_ERROR}")
                    if LAST_GITHUB_MODELS_ERROR:
                        status.write(f"GitHub Models vision issue: {LAST_GITHUB_MODELS_ERROR}")
                    status.write("Cloud OCR unavailable, trying local Tesseract fallback")
                    ocr_text = try_tesseract_ocr(image_bytes)
                    if ocr_text:
                        LAST_VISION_PROVIDER = "Tesseract"
                if ocr_text:
                    extracted_chunks.append(ocr_text)
                    if LAST_VISION_PROVIDER:
                        st.success(f"Image text extracted via {LAST_VISION_PROVIDER}")
                    else:
                        st.success("Image text extracted")
                else:
                    st.warning("Could not extract text from image")

            if product_url.strip():
                status.write("Step 3/4: Parsing product link with OpenRouter")
                url_text = extract_supplement_text_from_url(product_url.strip())
                if url_text:
                    extracted_chunks.append(url_text)
                    if LAST_TEXT_PROVIDER:
                        st.success(f"Link content parsed via {LAST_TEXT_PROVIDER}")
                    else:
                        st.success("Link content parsed")
                else:
                    st.warning("Could not parse link content")

            if manual_text.strip():
                extracted_chunks.append(manual_text.strip())

            combined = "\n\n".join([x for x in extracted_chunks if x.strip()])

            if not combined:
                st.session_state["analysis_ready"] = False
                st.session_state["analysis_components"] = []
                st.session_state["analysis_combined_text"] = ""
                status.update(label="No input detected", state="error")
                st.error("Please provide at least one input source: image, link, or text.")
                return

            status.write("Step 4/4: Extracting supplement components")
            components = parse_components(combined)
            if LAST_TEXT_PROVIDER:
                status.write(f"Testing info: component parsing used {LAST_TEXT_PROVIDER}")
            else:
                status.write("Testing info: component parsing used local fallback logic")
            status.update(label="Done", state="complete")

        st.session_state["analysis_ready"] = True
        st.session_state["analysis_components"] = components
        st.session_state["analysis_combined_text"] = combined
        st.session_state["analysis_usda_cache_key"] = ""
        st.session_state["analysis_usda_summary"] = []
        st.session_state["analysis_usda_details"] = []
        st.session_state["analysis_usda_status"] = ""

    components = st.session_state.get("analysis_components", [])
    combined = st.session_state.get("analysis_combined_text", "")

    if not components:
        st.info("No structured components found yet. You can still proceed in the next build step.")
        st.text_area("Raw extracted text", combined[:6000], height=220)
        return

    st.subheader("Your Supplement vs Whole Food Alternative")
    components_cache_key = json.dumps(
        [
            {
                "component": normalize_lookup_key(str(c.get("component", ""))),
                "dose_value": c.get("dose_value"),
                "dose_unit": str(c.get("dose_unit", "") or "").lower(),
            }
            for c in components
        ],
        sort_keys=True,
    )

    if st.session_state.get("analysis_usda_cache_key", "") != components_cache_key:
        map_progress = st.progress(0, text="Mapping supplement components to whole-food nutrients...")
        map_progress.progress(35, text="Resolving nutrient mappings...")
        usda_summary, usda_details, usda_status = build_usda_matches(components)
        map_progress.progress(100, text="Mapping ready")
        map_progress.empty()
        st.session_state["analysis_usda_cache_key"] = components_cache_key
        st.session_state["analysis_usda_summary"] = usda_summary
        st.session_state["analysis_usda_details"] = usda_details
        st.session_state["analysis_usda_status"] = usda_status
    else:
        usda_summary = st.session_state.get("analysis_usda_summary", [])
        usda_details = st.session_state.get("analysis_usda_details", [])
        usda_status = st.session_state.get("analysis_usda_status", "")

    detail_by_component = {normalize_lookup_key(str(d.get("component", ""))): d for d in usda_details}

    with st.expander("Pricing settings", expanded=False):
        price_cfg_cols = st.columns([1.5, 1.2, 1.2])
        selected_country = price_cfg_cols[0].selectbox(
            "Price region",
            list(COUNTRY_PRICE_CONFIG.keys()),
            index=0,
            key="price_region",
        )
        default_currency = COUNTRY_PRICE_CONFIG.get(selected_country, {}).get("currency", "USD")
        selected_currency = price_cfg_cols[1].selectbox(
            "Currency",
            ["EUR", "USD", "GBP", "INR", "BRL"],
            index=["EUR", "USD", "GBP", "INR", "BRL"].index(default_currency) if default_currency in ["EUR", "USD", "GBP", "INR", "BRL"] else 1,
            key="price_currency",
        )
        selected_market = price_cfg_cols[2].selectbox(
            "Market fallback",
            ["Auto", "Rewe", "Walmart"],
            index=0,
            key="price_market",
        )
        enable_live_price_fallback = st.toggle(
            "Enable live web/LLM fallback when local price DB has no match",
            value=False,
            key="enable_live_price_fallback",
        )
        if enable_live_price_fallback:
            st.caption("Mode: ⚙️ Advanced (live web/API fallback enabled; richer but potentially slower)")
        else:
            st.caption("Mode: ⚡ Fast (local price DB only; fastest results)")

        provider_cols = st.columns([1.2, 1.2])
        use_serpapi = provider_cols[0].toggle(
            "Use SerpApi",
            value=bool(SERPAPI_API_KEY),
            key="use_serpapi_pricing",
            help="Google Shopping offers via SerpApi",
            disabled=not enable_live_price_fallback,
        )
        use_dataforseo = provider_cols[1].toggle(
            "Use DataForSEO",
            value=bool(DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD),
            key="use_dataforseo_pricing",
            help="Google Shopping offers via DataForSEO",
            disabled=not enable_live_price_fallback,
        )
        if st.button("Refresh price lookups", key="refresh_price_cache", use_container_width=True):
            st.session_state["price_cache"] = {}
            st.caption("Price cache cleared. Next render will fetch fresh prices.")

    if usda_status != "ok":
        st.info(
            "Precomputed USDA DB not found yet. Build once with: "
            "`python build_usda_rankings_db.py`"
        )

    mapped_components = 0
    for item in components:
        ckey = normalize_lookup_key(str(item.get("component", "")))
        d = detail_by_component.get(ckey)
        if d and d.get("foods"):
            mapped_components += 1

    overview_cols = st.columns(3)
    overview_cols[0].metric("Components", len(components))
    overview_cols[1].metric("Mapped", mapped_components)
    overview_cols[2].metric("Unmapped", max(0, len(components) - mapped_components))
    st.caption("Alternatives are sorted by highest nutrient concentration per 100g.")

    if "price_cache" not in st.session_state:
        st.session_state["price_cache"] = {}

    total_cost = 0.0
    priced_rows = 0
    meal_component_candidates: list[dict[str, Any]] = []
    prep_progress = st.progress(0, text="Preparing whole-food matches and estimated costs...")
    total_rows = max(1, len(components))

    for index, item in enumerate(components):
        prep_progress.progress(int((index / total_rows) * 100), text=f"Preparing component {index + 1}/{len(components)}...")
        component_raw = str(item.get("component", "")).strip()
        component_key = normalize_lookup_key(component_raw)
        dose_value = item.get("dose_value")
        dose_unit = str(item.get("dose_unit") or "")

        component_display = component_raw.title() if component_raw else "Not available"
        dose_label = f"{format_float(float(dose_value))} {dose_unit}" if dose_value is not None else "Not available"
        detail = detail_by_component.get(component_key)
        foods = detail.get("foods", []) if detail else []
        status_chip = "Mapped" if foods else "Unmapped"

        with st.expander(f"{component_display} • {dose_label} • {status_chip}", expanded=(index == 0)):
            st.markdown(f"**Supplement dose:** {dose_label}")
            if detail and detail.get("proxy_rationale"):
                st.caption(f"Proxy note: {detail['proxy_rationale']}")

            if usda_status != "ok":
                st.warning("USDA DB unavailable")
                continue

            if not detail:
                st.info("No mapped USDA alternative")
                continue

            if not foods:
                st.info("No ranked alternatives found")
                continue

            option_labels: list[str] = []
            for food in foods:
                amt = format_float(float(food.get("amount_per_100g", 0.0)))
                unit = str(food.get("unit", "")).upper()
                option_labels.append(
                    f"{food.get('food_description', '')} ({amt} {unit}/100g)"
                )

            selected_label = st.selectbox(
                "Whole food alternative",
                options=option_labels,
                index=0,
                key=f"alt_select_cell_{index}_{component_key}",
            )
            selected_idx = option_labels.index(selected_label)
            selected_food = foods[selected_idx]
            selected_amt = float(selected_food.get("amount_per_100g", 0.0))
            selected_unit = str(selected_food.get("unit", ""))

            grams_needed = grams_needed_to_match_dose(
                dose_value,
                dose_unit,
                selected_amt,
                selected_unit,
            )

            match_label = "Not available"
            if grams_needed is not None:
                match_label = f"~{format_float(grams_needed)} g"
            st.markdown(f"**Amount needed to match dose:** {match_label}")

            meal_component_candidates.append(
                {
                    "component": component_display,
                    "dose_value": dose_value,
                    "dose_unit": dose_unit,
                    "foods": foods,
                }
            )

            cost_label = "Not available"
            price_info: dict[str, Any] | None = None
            if grams_needed is not None and grams_needed > 0:
                ean_hint = _extract_ean_from_text(component_raw)
                cache_key = (
                    normalize_lookup_key(str(selected_food.get("food_description", ""))),
                    selected_country,
                    selected_currency,
                    selected_market,
                    str(enable_live_price_fallback),
                    str(use_serpapi),
                    str(use_dataforseo),
                    format_float(float(grams_needed), 3),
                    ean_hint,
                )
                price_cache: dict[str, Any] = st.session_state.get("price_cache", {})
                cached = price_cache.get(cache_key)
                if cached and cached.get("price_per_kg") is not None:
                    price_info = cached
                else:
                    price_info = get_food_price_estimate(
                        str(selected_food.get("food_description", "")),
                        selected_country,
                        selected_currency,
                        selected_market,
                        enable_live_price_fallback,
                        grams_needed,
                        ean_hint,
                        use_serpapi,
                        use_dataforseo,
                    )
                    if price_info and price_info.get("price_per_kg") is not None:
                        price_cache[cache_key] = price_info
                        st.session_state["price_cache"] = price_cache

                if price_info and price_info.get("price_per_kg") is not None:
                    try:
                        price_per_kg = float(price_info.get("price_per_kg"))
                        required_cost = (float(grams_needed) / 1000.0) * price_per_kg
                        symbol = CURRENCY_SYMBOL.get(str(price_info.get("currency", selected_currency)), str(price_info.get("currency", selected_currency)))
                        source = str(price_info.get("source", "price db"))
                        cost_label = f"~{symbol}{format_float(required_cost)} ({source})"
                        total_cost += required_cost
                        priced_rows += 1
                    except Exception:
                        cost_label = "Not available"

            st.markdown(f"**Estimated cost for required amount:** {cost_label}")
            if cost_label != "Not available" and isinstance(price_info, dict):
                score = format_float(float(price_info.get("final_score", 0.0)), 3)
                match_method = str(price_info.get("match_method", "title_similarity") or "title_similarity")
                confidence = str(price_info.get("confidence", "low") or "low")
                ppk = format_float(float(price_info.get("price_per_kg", 0.0)), 2)
                curr = str(price_info.get("currency", selected_currency) or selected_currency)
                st.caption(
                    f"{curr}/{ppk} per kg • confidence: {confidence} • match: {match_method} • score: {score}"
                )
                top_candidates = price_info.get("audit_top_candidates") or []
                if top_candidates:
                    short = []
                    for cand in top_candidates[:3]:
                        c_src = str(cand.get("source", "unknown") or "unknown")
                        c_score = format_float(float(cand.get("final_score", 0.0)), 2)
                        c_ppk = format_float(float(cand.get("price_per_kg", 0.0)), 2)
                        c_cur = str(cand.get("currency", selected_currency) or selected_currency)
                        short.append(f"{c_src}: {c_cur} {c_ppk}/kg (score {c_score})")
                    st.caption(" | ".join(short))

    prep_progress.progress(100, text="Alternative matching and pricing ready")
    prep_progress.empty()

    if priced_rows > 0:
        symbol = CURRENCY_SYMBOL.get(selected_currency, selected_currency)
        summary_cols = st.columns([1.4, 1.0])
        summary_cols[0].markdown(
            f"**Estimated total whole-food cost for selected alternatives: {symbol}{format_float(total_cost)}**"
        )
        supplement_paid = summary_cols[1].number_input(
            "What did you pay for the supplement?",
            min_value=0.0,
            value=0.0,
            step=0.5,
            format="%.2f",
            key="supplement_paid_price",
            help="Enter the supplement purchase price in the selected currency for a direct cost comparison.",
        )

        if supplement_paid > 0:
            difference = abs(float(supplement_paid) - float(total_cost))
            if total_cost < supplement_paid:
                st.success(
                    f"For matched component concentrations, selected whole foods are cheaper by {symbol}{format_float(difference)}."
                )
            elif total_cost > supplement_paid:
                st.info(
                    f"For matched component concentrations, the supplement is cheaper by {symbol}{format_float(difference)}."
                )
            else:
                st.info("For matched component concentrations, both options cost about the same.")

            if priced_rows < len(components):
                st.caption(
                    f"Cost comparison currently covers {priced_rows} of {len(components)} components with available price matches."
                )

            st.caption(
                "Health framing: whole foods remain the preferred baseline choice because they provide broader nutrient synergy and dietary quality beyond isolated supplement economics."
            )
        else:
            st.caption("Enter your supplement price to compare supplement vs selected whole-food costs.")
    else:
        st.caption("Total cost is unavailable until at least one row has both dose-match grams and a price source.")

    st.subheader("Meal ideas from suggested whole foods")
    st.caption(
        "Meals target at least the supplement-equivalent amounts for suggested components; exceeding targets is allowed."
    )

    if meal_component_candidates:
        profiles = load_dietary_profiles()
        profile_labels = [str(p.get("label", "No restriction") or "No restriction") for p in profiles]
        profile_by_label = {str(p.get("label", "No restriction") or "No restriction"): p for p in profiles}

        filter_cols = st.columns([1.4, 1.0])
        selected_profile_label = filter_cols[0].selectbox(
            "Dietary profile",
            options=profile_labels,
            index=0,
            key="meal_diet_profile",
        )
        must_exclude_ingredient = filter_cols[1].text_input(
            "Must exclude ingredient",
            value="",
            key="meal_must_exclude",
            placeholder="e.g. pork",
        )
        selected_profile = profile_by_label.get(selected_profile_label, profiles[0] if profiles else None)
        if selected_profile and selected_profile.get("description"):
            st.caption(f"Profile note: {selected_profile.get('description')}")
        st.caption(
            "Dietary profiles use practical ingredient-keyword screening and are not medical advice, halal/kosher certification, or allergy safety guarantees."
        )

        allow_llm_recipe_fallback = st.toggle(
            "Allow AI fallback if local recipe DB cannot fully cover targets",
            value=True,
            key="allow_llm_recipe_fallback",
        )
        build_meals = st.button("Generate meal ideas", use_container_width=True, key="build_meal_ideas")

        if "meal_suggestion_cache" not in st.session_state:
            st.session_state["meal_suggestion_cache"] = {}

        meal_cache_key = json.dumps(
            {
                "requirements": [
                    {
                        "component": normalize_lookup_key(str(r.get("component", ""))),
                        "dose_value": r.get("dose_value"),
                        "dose_unit": normalize_lookup_key(str(r.get("dose_unit", ""))),
                        "foods": [
                            {
                                "food": normalize_lookup_key(str(f.get("food_description", ""))),
                                "amt": round(float(f.get("amount_per_100g", 0) or 0), 4),
                                "unit": normalize_lookup_key(str(f.get("unit", ""))),
                            }
                            for f in (r.get("foods", []) or [])
                        ],
                    }
                    for r in meal_component_candidates
                ],
                "allow_llm": bool(allow_llm_recipe_fallback),
                "profile": normalize_lookup_key(str(selected_profile.get("id", "none") if selected_profile else "none")),
                "must_exclude": normalize_lookup_key(must_exclude_ingredient),
            },
            sort_keys=True,
        )

        if build_meals:
            with st.status("Generating meal ideas", expanded=False) as meal_status:
                meal_status.write("Selecting cheapest qualifying foods for meal anchors")
                price_cache: dict[str, Any] = st.session_state.get("price_cache", {})
                meal_requirements, updated_cache = resolve_cheapest_meal_requirements(
                    meal_component_candidates,
                    selected_country,
                    selected_currency,
                    selected_market,
                    enable_live_price_fallback,
                    use_serpapi,
                    use_dataforseo,
                    price_cache,
                )
                st.session_state["price_cache"] = updated_cache

                if not meal_requirements:
                    st.session_state["meal_suggestion_cache"][meal_cache_key] = {
                        "meals": [],
                        "source_mode": "none",
                    }
                    meal_status.update(label="No qualifying meal anchors found", state="error")
                else:
                    meal_status.write("Searching local recipe database")
                    local_meals_raw = find_local_meal_suggestions(meal_requirements, max_results=12)
                    local_meals = apply_meal_filters(local_meals_raw, selected_profile, must_exclude_ingredient)
                    full_local = [m for m in local_meals if m.get("full_coverage")]

                    final_meals = local_meals[:3]
                    source_mode = "local"

                    if not full_local and allow_llm_recipe_fallback:
                        meal_status.write("No full local coverage found, generating AI fallback ideas")
                        llm_meals_raw = generate_llm_meal_suggestions(meal_requirements, max_results=8)
                        llm_meals = apply_meal_filters(llm_meals_raw, selected_profile, must_exclude_ingredient)
                        if llm_meals:
                            final_meals = llm_meals[:3]
                            source_mode = "llm"
                        else:
                            meal_status.write("AI fallback empty, creating template meal fallback")
                            template_meals_raw = generate_template_meal_suggestions(meal_requirements, max_results=6)
                            template_meals = apply_meal_filters(template_meals_raw, selected_profile, must_exclude_ingredient)
                            if template_meals:
                                final_meals = template_meals[:3]
                                source_mode = "template"

                    st.session_state["meal_suggestion_cache"][meal_cache_key] = {
                        "meals": final_meals,
                        "source_mode": source_mode,
                    }
                    meal_status.update(label="Meal ideas ready", state="complete")

        cached_meal_pack = st.session_state["meal_suggestion_cache"].get(meal_cache_key)
        if cached_meal_pack:
            meals = cached_meal_pack.get("meals", [])
            source_mode = str(cached_meal_pack.get("source_mode", "local") or "local")

            if meals:
                if source_mode == "local":
                    st.success("Meal ideas sourced from local recipe database.")
                elif source_mode == "template":
                    st.info("Showing SuppSwap template fallback because local and AI results were empty after filters.")
                else:
                    st.info("Showing AI-generated meal fallback because local DB had no full-coverage match.")

                for idx, meal in enumerate(meals, start=1):
                    coverage_ratio = float(meal.get("coverage_ratio", 0.0) or 0.0)
                    covered = meal.get("covered_components", []) or []
                    uncovered = meal.get("uncovered_components", []) or []
                    title = f"{idx}. {meal.get('name', 'Meal idea')}"
                    if meal.get("full_coverage"):
                        title += " • full target coverage"
                    else:
                        title += f" • coverage {format_float(coverage_ratio * 100, 0)}%"

                    with st.expander(title, expanded=(idx == 1)):
                        ingredients = meal.get("ingredients", []) or []
                        ing_lines: list[str] = []
                        for ing in ingredients:
                            ing_name = str(ing.get("name", "") or "").strip()
                            grams = float(ing.get("grams", 0) or 0)
                            if ing_name and grams > 0:
                                ing_lines.append(f"- {ing_name}: {format_float(grams, 0)} g")
                        if ing_lines:
                            st.markdown("**Ingredients (per serving)**")
                            st.markdown("\n".join(ing_lines))

                        steps = str(meal.get("steps", "") or "").strip()
                        if steps:
                            st.markdown("**Preparation**")
                            st.write(steps)

                        if covered:
                            st.caption("Covered components: " + ", ".join([str(x) for x in covered]))
                        if uncovered:
                            st.caption("Not fully covered in this meal: " + ", ".join([str(x) for x in uncovered]))
            else:
                st.warning("No meal ideas found yet. Try relaxing dietary filters or changing the excluded ingredient.")
    else:
        st.caption("Meal ideas appear after at least one component has a valid whole-food amount match.")

    st.subheader("Ask a RAG question")
    rag_chunks, rag_status = build_rag_index()
    rag_available = bool(rag_chunks) and str(rag_status).lower().startswith("ok")
    if rag_available:
        st.caption("Ask questions using your local fitness reference library.")
    else:
        st.warning(f"RAG currently unavailable: {rag_status}")

    rag_query = st.text_area(
        "Your question",
        placeholder="Example: What does the reference library say about creatine and muscle gain?",
        height=100,
        key="rag_query_input",
    )
    ask_rag = st.button("Ask RAG", use_container_width=True)
    if ask_rag:
        if not rag_query.strip():
            st.warning("Please enter a question.")
        elif not rag_available:
            st.info("RAG index is not ready yet. Please refresh after indexing or check parser setup.")
        else:
            with st.status("Searching reference library", expanded=False) as rag_status_box:
                rag_status_box.write("Retrieving relevant excerpts")
                rag_answer, rag_sources = answer_rag_question(rag_query.strip(), rag_chunks)
                rag_status_box.update(label="Done", state="complete")

            st.markdown("**Answer**")
            st.write(rag_answer)
            if rag_sources:
                st.caption("Sources: " + ", ".join(rag_sources))


def is_streamlit_runtime() -> bool:
    return "streamlit" in sys.modules


if __name__ == "__main__":
    if is_streamlit_runtime():
        build_mobile_ui()
    else:
        script_path = os.path.abspath(__file__)
        cmd = [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            script_path,
            "--browser.gatherUsageStats",
            "false",
        ]
        print("Launching Streamlit app...")
        try:
            subprocess.run(cmd, check=False)
        except Exception as exc:
            print(f"Failed to launch Streamlit automatically: {exc}")
            print("Please run: python -m streamlit run app.py")
