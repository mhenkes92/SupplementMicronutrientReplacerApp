import base64
import csv
import difflib
import functools
import hashlib
import html
import io
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import statistics
import sys
import subprocess
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from PIL import Image, ImageFilter, ImageOps

try:
    from pydantic import BaseModel, ValidationError
except Exception:
    BaseModel = None
    ValidationError = Exception



# -- Shared HTTP session -------------------------------------------------
_HTTP_SESSION = requests.Session()
_HTTP_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; SuppSwap/1.0; +https://example.local)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
})


def _http_get(url: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", HTTP_TIMEOUT)
    return _HTTP_SESSION.get(url, **kwargs)


def _http_post(url: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", HTTP_TIMEOUT)
    return _HTTP_SESSION.post(url, **kwargs)
# -------------------------------------------------------------------------

APP_DIR = Path(__file__).resolve().parent

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(APP_DIR / 'app.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

load_dotenv()
# Also load .env from the app directory so VS Code Play/Run works from any CWD.
load_dotenv(APP_DIR / ".env")

# Blockbrain-first configuration: all LLM and vision calls route through Blockbrain.
# Credentials are loaded from .streamlit/secrets.toml (preferred) or environment variables.
def _load_blockbrain_secrets() -> tuple[str, str, str]:
    """Load Blockbrain credentials from st.secrets or env at runtime.
    Returns (api_key, base_url, agent_id).
    agent_id is retained for backward compatibility with older configs.
    """
    _default_base = "https://agentic.theblockbrain.ai"
    # User-selected production agent for all Blockbrain LLM calls.
    _default_route_id = "6a4bc43653952e29ba6ef1d6"

    def _strip_path(base: str) -> str:
        """Return only the scheme+host, stripping any /v1/... path."""
        base = base.strip()
        for suffix in ["/v1/chat/completions", "/v1"]:
            if base.endswith(suffix):
                base = base[: -len(suffix)]
        return base.rstrip("/")

    try:
        import streamlit as st  # noqa: F401
        api_key = st.secrets.get("BLOCKBRAIN_API_KEY", "") or os.getenv("BLOCKBRAIN_API_KEY", "")
        base_url = (
            st.secrets.get("BLOCKBRAIN_BASE_URL", "")
            or st.secrets.get("BLOCKBRAIN_API_URL", "")  # legacy
            or os.getenv("BLOCKBRAIN_BASE_URL", "")
            or os.getenv("BLOCKBRAIN_API_URL", "")
            or _default_base
        )
        # Intentionally ignore legacy BLOCKBRAIN_AGENT_ID from secrets/env so all
        # app calls are forced through the pinned production agent.
        route_id = _default_route_id
        return str(api_key).strip(), _strip_path(str(base_url)), str(route_id).strip()
    except Exception:
        base_url = os.getenv("BLOCKBRAIN_BASE_URL") or os.getenv("BLOCKBRAIN_API_URL") or _default_base
        route_id = _default_route_id
        return (
            os.getenv("BLOCKBRAIN_API_KEY", ""),
            _strip_path(base_url),
            route_id,
        )


# Fastest verified vision model for nutrition-label OCR (agentic vision route).
# Benchmark on a real supplement label (downscaled to ~300 KB), per-call latency:
#   anthropic-claude-haiku-4.5     ~12.7s   <-- fastest, full valid OCR
#   gpt-4.1-nano                   ~15.6s
#   gpt-4o-mini                    ~16.4s
#   gpt-4.1-mini                   ~20.2s
#   google-gemini-2.5-flash-lite   ~36.9s
#   azure-gpt-4o-mini              ~39.3s
# Override via BLOCKBRAIN_MODEL_VISION (env or secrets) when needed.
BLOCKBRAIN_PINNED_VISION_MODEL = "anthropic-claude-haiku-4.5"

# Fastest verified text model for the "Resolving nutrient mappings" step
# (build_ai_food_matches -> strict JSON generation). Benchmarked on the real
# mapping prompt (10 components, up to 5 foods each):
#   gpt-4.1-nano / gpt-4o-mini / gemini-2.5-flash-lite   ~1-2s, valid JSON
#   platform default (bedrock claude sonnet, thinking)   >120s cold  <-- "takes forever"
# A fast non-thinking model is pinned so mapping is near-instant instead of
# running on the slow default reasoning backend. anthropic-claude-haiku-4.5 is
# NOT used for text (it was ~40s on this JSON task).
# Override via BLOCKBRAIN_MODEL_TEXT (env or secrets) when needed.
BLOCKBRAIN_PINNED_TEXT_MODEL = "gpt-4.1-nano"


def _load_blockbrain_model_defaults() -> tuple[str, str]:
    """Load default Blockbrain model preferences (text, vision).

    Both default to the fastest benchmarked models unless overridden, so the
    nutrient-mapping and label-OCR steps stay fast instead of using the slow
    default reasoning backend.
    """
    try:
        import streamlit as st  # noqa: F401
        text_model = (
            st.secrets.get("BLOCKBRAIN_MODEL_TEXT", "")
            or os.getenv("BLOCKBRAIN_MODEL_TEXT", "")
            or BLOCKBRAIN_PINNED_TEXT_MODEL
        )
        vision_model = (
            st.secrets.get("BLOCKBRAIN_MODEL_VISION", "")
            or os.getenv("BLOCKBRAIN_MODEL_VISION", "")
            or BLOCKBRAIN_PINNED_VISION_MODEL
        )
        return str(text_model).strip(), str(vision_model).strip()
    except Exception:
        return (
            str(os.getenv("BLOCKBRAIN_MODEL_TEXT", "") or BLOCKBRAIN_PINNED_TEXT_MODEL).strip(),
            str(os.getenv("BLOCKBRAIN_MODEL_VISION", "") or BLOCKBRAIN_PINNED_VISION_MODEL).strip(),
        )


BLOCKBRAIN_API_KEY = ""
BLOCKBRAIN_BOT_ID = ""

TESSERACT_CMD = os.getenv("TESSERACT_CMD", "")

BLOCKBRAIN_API_GATEWAY = ""
BLOCKBRAIN_CHAT_ENDPOINT = ""
HTTP_TIMEOUT = 120
LOCAL_URL_KEYWORD_WINDOW_CHARS = 260

# Magic number constants for fuzzy matching and thresholds
FUZZY_MATCH_CUTOFF_HIGH = 0.86
FUZZY_MATCH_CUTOFF_MEDIUM = 0.84
MAX_GRAMS_INFINITY_PLACEHOLDER = 1e18
DECIMAL_PRECISION_MIN = 2
DECIMAL_PRECISION_MAX = 8

EXTRACTION_DOSE_PATTERN = re.compile(
    r"\b\d+(?:[\.,]\d+)?\s*(?:mg|mcg|meg|ug|µg|μg|fg|g|iu|ui|ie|kcal)\b",
    re.I,
)
LOCAL_URL_KEYWORD_WINDOW_PATTERN = re.compile(
    rf"(?:supplement facts|nutrition facts|serving size|amount per serving|daily value|ingredients?).{{0,{LOCAL_URL_KEYWORD_WINDOW_CHARS}}}",
    re.I,
)
LOCAL_URL_SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[\.;])\s+")

# Session state keys for error tracking and provider info
# Using module-level fallbacks for contexts where Streamlit isn't available
_FALLBACK_STATE = {
    "last_blockbrain_error": "",
    "last_text_llm_error": "",
    "last_vision_provider": "",
    "last_text_provider": "",
    "last_url_parse_reason": "",
    "last_rag_error": "",
}

def _get_state(key: str, default: str = "") -> str:
    """Get state from session_state if available, otherwise use module fallback."""
    try:
        import streamlit as st
        if key not in st.session_state:
            st.session_state[key] = default
        return st.session_state[key]
    except (ImportError, RuntimeError):
        return _FALLBACK_STATE.get(key, default)

def _set_state(key: str, value: str) -> None:
    """Set state in session_state if available, otherwise use module fallback."""
    try:
        import streamlit as st
        st.session_state[key] = value
    except (ImportError, RuntimeError):
        _FALLBACK_STATE[key] = value

# Legacy global variable accessors (for backward compatibility during migration)
LAST_BLOCKBRAIN_ERROR = ""
LAST_BLOCKBRAIN_MODEL = ""
LAST_TEXT_LLM_ERROR = ""
LAST_VISION_PROVIDER = ""
LAST_TEXT_PROVIDER = ""
LAST_URL_PARSE_REASON = ""
LAST_RAG_ERROR = ""


def _text_llm_available() -> bool:
    # Activate text generation paths when Blockbrain is configured.
    try:
        api_key, _, _ = _load_blockbrain_secrets()
        return bool(api_key)
    except Exception:
        return False

ALLOWED_DOSE_UNITS: set[str] = {"", "mg", "mcg", "g", "iu", "kcal"}

# -- Centralized unit normalization --------------------------------------
_UNIT_ALIASES: dict[str, str] = {
    "mg": "mg", "milligram": "mg", "milligrams": "mg",
    "mcg": "mcg", "ug": "mcg", "µg": "mcg", "μg": "mcg",
    "microgram": "mcg", "micrograms": "mcg", "meg": "mcg", "fg": "mcg",
    "g": "g", "gram": "g", "grams": "g",
    "iu": "iu", "ui": "iu", "ie": "iu",
    "i.u": "iu", "i.u.": "iu", "u.i": "iu", "u.i.": "iu",
    "kcal": "kcal",
}
_TO_MG: dict[str, float] = {"mg": 1.0, "mcg": 0.001, "g": 1000.0}


def _canon_unit(unit: str) -> str:
    """Single source of truth for unit canonicalization."""
    return _UNIT_ALIASES.get(str(unit or "").strip().lower(), str(unit or "").strip().lower())
# -------------------------------------------------------------------------

MAX_REASONABLE_DOSE_BY_UNIT: dict[str, float] = {
    "g": 250.0,
    "mg": 100000.0,
    "mcg": 5000000.0,
    "iu": 2000000.0,
    "kcal": 10000.0,
}

if BaseModel is not None:
    class ParsedComponentModel(BaseModel):
        component: str
        dose_value: float | None = None
        dose_unit: str = ""

RAG_VITAMIN_LETTER_PATTERN = re.compile(r"\b(?:vitamin|vitmain)\s+([abcdehk])\b")
RAG_STOPWORDS: set[str] = {
    "what",
    "whats",
    "s",
    "is",
    "are",
    "the",
    "a",
    "an",
    "for",
    "to",
    "of",
    "and",
    "in",
    "on",
    "with",
    "good",
}

USDA_RANK_DB_PATH = APP_DIR / "data" / "usda_rankings.db"
TOP_FOODS_PER_COMPONENT = 50
OVERVIEW_ALT_LIMIT = 50
RAG_TOP_K = 8
FOOD_MATCH_CACHE_SCHEMA_VERSION = "2"
RAG_INDEX_PATH = APP_DIR / "data" / "fitness_rag_chunks.jsonl"
RAG_INDEX_META_PATH = APP_DIR / "data" / "fitness_rag_meta.json"
DIETARY_PROFILES_PATH = APP_DIR / "data" / "dietary_profiles.json"
DIETARY_RESTRICTION_RULES_PATH = APP_DIR / "data" / "dietary_restriction_rules.json"
UNMAPPED_COMPONENT_LOG_PATH = APP_DIR / "data" / "unmapped_components_log.csv"
FEEDBACK_REPORTS_PATH = APP_DIR / "data" / "feedback_reports.jsonl"
OFFICIAL_NUTRIENT_SOURCES_PATH = APP_DIR / "data" / "official_nutrient_sources.csv"

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
    "local_proxy_baseline": 0.42,
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

# USDA nutrient IDs for macros (used for macro-optimized meal scaling).
_MACRO_PROTEIN_NID: int = 1003   # Protein (G)
_MACRO_FAT_NID: int = 1004       # Total lipid / fat (G)
_MACRO_CARBS_NID: int = 1005     # Carbohydrate, by difference (G)

# Approximate edible whole-item weights (grams each) for mobile-friendly portion hints.
WHOLE_FOOD_UNIT_ESTIMATES: list[tuple[str, str, str, float]] = [
    ("kiwifruit", "kiwi", "kiwis", 100.0),
    ("kiwi", "kiwi", "kiwis", 100.0),
    ("banana", "banana", "bananas", 118.0),
    ("apple", "apple", "apples", 182.0),
    ("orange", "orange", "oranges", 140.0),
    ("mango", "mango", "mangoes", 200.0),
    ("avocado", "avocado", "avocados", 150.0),
    ("tomato", "tomato", "tomatoes", 123.0),
    ("carrot", "carrot", "carrots", 61.0),
    ("egg", "egg", "eggs", 50.0),
    ("peppers bell", "bell pepper", "bell peppers", 119.0),
]

# Approximate grams per cup for selected foods where cup-based measures are common.
VOLUME_FOOD_ESTIMATES: list[tuple[str, str, str, float]] = [
    ("spinach", "cup", "cups", 30.0),
    ("broccoli", "cup", "cups", 91.0),
    ("lentils", "cup", "cups", 198.0),
    ("quinoa", "cup", "cups", 185.0),
    ("oat", "cup", "cups", 80.0),
]

FITNESS_REFERENCE_DIR_CANDIDATES = [
    APP_DIR.parent / "fitness_reference",
    APP_DIR.parent / "Fitness_reference",
]


def normalize_lookup_key(value: str) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z0-9\s\-\+\(\)]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _file_mtime_or_minus_one(path: Path) -> float:
    try:
        return float(path.stat().st_mtime)
    except Exception as e:
        logger.debug(f"Unable to get mtime for {path}: {e}")
        return -1.0


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    # Keep only numeric punctuation; remove spaces used as thousand separators.
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[^0-9,\.-]", "", text)
    if not text:
        return None

    # Locale-aware parsing for common OCR/output variants:
    #  - 1,000 / 1.000 (thousand separator)
    #  - 1,5 / 1.5 (decimal separator)
    #  - 1,234.56 / 1.234,56 (mixed locale)
    if "," in text and "." in text:
        last_comma = text.rfind(",")
        last_dot = text.rfind(".")
        if last_dot > last_comma:
            # US style: 1,234.56
            text = text.replace(",", "")
        else:
            # EU style: 1.234,56
            text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        parts = text.split(",")
        if len(parts) > 2:
            text = "".join(parts)
        elif len(parts) == 2 and len(parts[1]) == 3 and parts[0] not in {"", "0", "-0"}:
            # Treat "1,000" as one thousand, but keep "0,125" as decimal.
            text = "".join(parts)
        else:
            text = text.replace(",", ".")
    elif "." in text:
        parts = text.split(".")
        if len(parts) > 2:
            text = "".join(parts)
        elif len(parts) == 2 and len(parts[1]) == 3 and parts[0] not in {"", "0", "-0"}:
            # Treat "1.000" as one thousand, but keep "0.125" as decimal.
            text = "".join(parts)

    try:
        return float(text)
    except (ValueError, TypeError) as e:
        logger.debug(f"Unable to parse float from '{value}': {e}")
        return None




def _normalize_reference_unit_token(unit: str) -> str:
    return _canon_unit(unit)


def _normalize_reference_row_units(
    nutrient: str,
    unit: str,
    source_agency: str,
    recommended_value: float | None,
    upper_limit_value: float | None,
) -> tuple[str, float | None, float | None]:
    del nutrient, source_agency
    from_unit = _normalize_reference_unit_token(unit)
    return from_unit, recommended_value, upper_limit_value


def load_official_nutrient_sources() -> list[dict[str, Any]]:
    return _load_official_nutrient_sources_cached(_file_mtime_or_minus_one(OFFICIAL_NUTRIENT_SOURCES_PATH))


@functools.lru_cache(maxsize=4)
def _load_official_nutrient_sources_cached(_mtime: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not OFFICIAL_NUTRIENT_SOURCES_PATH.exists():
        return rows

    try:
        with OFFICIAL_NUTRIENT_SOURCES_PATH.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                nutrient = str(row.get("nutrient", "") or "").strip()
                unit = str(row.get("unit", "") or "").strip()
                life_stage = str(row.get("life_stage", "Adults") or "Adults").strip()
                sex = str(row.get("sex", "All") or "All").strip()
                source_agency = str(row.get("source_agency", "") or "").strip()
                source_url = str(row.get("source_url", "") or "").strip()
                notes = str(row.get("notes", "") or "").strip()
                recommended_value = _parse_float(row.get("recommended_value"))
                upper_limit_value = _parse_float(row.get("upper_limit_value"))

                if not nutrient or not unit or not source_agency:
                    continue

                normalized_unit, recommended_value, upper_limit_value = _normalize_reference_row_units(
                    nutrient,
                    unit,
                    source_agency,
                    recommended_value,
                    upper_limit_value,
                )

                rows.append(
                    {
                        "nutrient": nutrient,
                        "unit": normalized_unit,
                        "life_stage": life_stage,
                        "sex": sex,
                        "source_agency": source_agency,
                        "source_url": source_url,
                        "recommended_value": recommended_value,
                        "upper_limit_value": upper_limit_value,
                        "notes": notes,
                    }
                )
    except Exception as e:
        logger.error(f"Error loading official nutrient sources from {OFFICIAL_NUTRIENT_SOURCES_PATH}: {e}")
        return []

    return rows


def build_official_nutrient_aggregate(
    source_rows: list[dict[str, Any]],
    life_stage: str,
    sex: str,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    target_stage = normalize_lookup_key(life_stage)
    target_sex = normalize_lookup_key(sex)

    for row in source_rows:
        row_stage = normalize_lookup_key(str(row.get("life_stage", "Adults") or "Adults"))
        row_sex = normalize_lookup_key(str(row.get("sex", "All") or "All"))

        stage_match = row_stage in {target_stage, "all", "general"}
        sex_match = row_sex in {target_sex, "all", "both", "any", "general"}
        if not stage_match or not sex_match:
            continue

        nutrient = str(row.get("nutrient", "") or "").strip()
        unit = str(row.get("unit", "") or "").strip()
        if not nutrient or not unit:
            continue

        grouped.setdefault((nutrient, unit), []).append(row)

    out_rows: list[dict[str, Any]] = []
    for (nutrient, unit), rows in grouped.items():
        rec_values = [float(v) for v in [r.get("recommended_value") for r in rows] if v is not None and float(v) > 0]
        ul_values = [float(v) for v in [r.get("upper_limit_value") for r in rows] if v is not None and float(v) > 0]

        if not rec_values and not ul_values:
            continue

        if len(rec_values) == 1:
            recommendation_value = rec_values[0]
        elif len(rec_values) > 1:
            recommendation_value = statistics.fmean(rec_values)
        else:
            recommendation_value = None
        ul_min = min(ul_values) if ul_values else None
        ul_max = max(ul_values) if ul_values else None
        if ul_min is not None and ul_max is not None:
            ul_average = (float(ul_min) + float(ul_max)) / 2.0
        else:
            ul_average = ul_min if ul_min is not None else ul_max
        used_sources = sorted({str(r.get("source_agency", "") or "").strip() for r in rows if str(r.get("source_agency", "") or "").strip()})

        out_rows.append(
            {
                "nutrient": nutrient,
                "unit": unit,
                "recommendation_value": recommendation_value,
                "recommendation_source_count": len(rec_values),
                "ul_conservative": ul_min,
                "ul_max": ul_max,
                "ul_average": ul_average,
                "sources_used": used_sources,
                "source_count": len(used_sources),
            }
        )

    out_rows.sort(key=lambda x: normalize_lookup_key(str(x.get("nutrient", ""))))
    return out_rows


def log_unmapped_component(component: str, dose_value: Any = None, dose_unit: str = "") -> None:
    normalized_component = normalize_lookup_key(component)
    if not normalized_component:
        return

    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    fieldnames = [
        "component",
        "first_seen_utc",
        "last_seen_utc",
        "hits",
        "last_dose_value",
        "last_dose_unit",
    ]

    existing: dict[str, dict[str, str]] = {}
    if UNMAPPED_COMPONENT_LOG_PATH.exists():
        try:
            with UNMAPPED_COMPONENT_LOG_PATH.open("r", encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f):
                    key = normalize_lookup_key(str(row.get("component", "") or ""))
                    if key:
                        existing[key] = {
                            "component": key,
                            "first_seen_utc": str(row.get("first_seen_utc", "") or "").strip(),
                            "last_seen_utc": str(row.get("last_seen_utc", "") or "").strip(),
                            "hits": str(row.get("hits", "1") or "1").strip(),
                            "last_dose_value": str(row.get("last_dose_value", "") or "").strip(),
                            "last_dose_unit": str(row.get("last_dose_unit", "") or "").strip(),
                        }
        except Exception:
            existing = {}

    current = existing.get(normalized_component)
    if current:
        try:
            hits = max(0, int(str(current.get("hits", "1") or "1"))) + 1
        except (ValueError, TypeError) as e:
            logger.debug(f"Error parsing hit count: {e}")
            hits = 2
        current["hits"] = str(hits)
        current["last_seen_utc"] = now_iso
        current["last_dose_value"] = "" if dose_value is None else str(dose_value)
        current["last_dose_unit"] = str(dose_unit or "")
        existing[normalized_component] = current
    else:
        existing[normalized_component] = {
            "component": normalized_component,
            "first_seen_utc": now_iso,
            "last_seen_utc": now_iso,
            "hits": "1",
            "last_dose_value": "" if dose_value is None else str(dose_value),
            "last_dose_unit": str(dose_unit or ""),
        }

    try:
        UNMAPPED_COMPONENT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with UNMAPPED_COMPONENT_LOG_PATH.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for key in sorted(existing.keys()):
                writer.writerow(existing[key])
    except Exception as e:
        logger.error(f"Error saving unmapped components log: {e}")
        return


def save_feedback_report(report: dict[str, Any]) -> bool:
    payload = dict(report)
    payload["created_at_utc"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    try:
        FEEDBACK_REPORTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with FEEDBACK_REPORTS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=True) + "\n")
        return True
    except Exception as e:
        logger.error(f"Error saving feedback report: {e}")
        return False


def try_open_usda_db() -> sqlite3.Connection | None:
    if not USDA_RANK_DB_PATH.exists():
        logger.warning(f"USDA database not found at {USDA_RANK_DB_PATH}")
        return None
    try:
        return sqlite3.connect(str(USDA_RANK_DB_PATH))
    except Exception as e:
        logger.error(f"Error connecting to USDA database: {e}")
        return None


def _persisted_usda_food_allowed(food_description: str, profile: dict[str, Any] | None) -> bool | None:
    del food_description, profile
    # AI-only runtime: skip persisted DB dietary-flag layer.
    return None


def unit_to_mg(unit: str) -> float | None:
    return _TO_MG.get(_canon_unit(unit))


def _iu_unit_to_mg_for_component(component_name: str | None) -> float | None:
    component_key = normalize_lookup_key(component_name or "")
    if not component_key:
        return None

    # Vitamin D supplement labels commonly use IU.
    # 1 IU vitamin D = 0.025 mcg = 0.000025 mg.
    if component_key.startswith("vitamin d") or component_key in {"d", "d2", "d3"}:
        return 0.000025

    # Vitamin A (retinol activity equivalent approximation for supplement labels).
    # 1 IU vitamin A = 0.3 mcg retinol equivalent = 0.0003 mg.
    if component_key.startswith("vitamin a") or component_key == "retinol":
        return 0.0003
    if "beta carotene" in component_key or "beta-carotene" in component_key:
        # Supplemental beta-carotene convention: 1 IU ~= 0.6 mcg.
        return 0.0006

    # Vitamin E IU conversion is form-dependent.
    # Use a practical default and handle explicit natural-form hints when available.
    # synthetic dl-alpha-tocopherol: 1 IU = 0.45 mg
    # natural d-alpha-tocopherol: 1 IU = 0.67 mg
    if component_key.startswith("vitamin e") or "tocopherol" in component_key:
        if any(token in component_key for token in ["natural", "d alpha", "d-alpha", "rrr"]):
            return 0.67
        return 0.45

    return None


def grams_needed_to_match_dose(
    supplement_dose_value: float | None,
    supplement_dose_unit: str | None,
    nutrient_amount_per_100g: float,
    nutrient_unit: str,
    component_name: str | None = None,
) -> float | None:
    if supplement_dose_value is None:
        return None

    supp_factor = unit_to_mg(supplement_dose_unit or "")
    if supp_factor is None:
        supp_unit_key = normalize_lookup_key(str(supplement_dose_unit or ""))
        if supp_unit_key in {"iu", "ui", "ie"}:
            supp_factor = _iu_unit_to_mg_for_component(component_name)
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


def _format_nonzero_value(value: float, min_decimals: int = DECIMAL_PRECISION_MIN, max_decimals: int = DECIMAL_PRECISION_MAX) -> str:
    if value <= 0:
        return ""
    for decimals in range(min_decimals, max_decimals + 1):
        txt = format_float(float(value), decimals)
        try:
            if float(txt) > 0:
                return txt
        except (ValueError, TypeError) as e:
            logger.debug(f"Error formatting value {value} with {decimals} decimals: {e}")
            continue
    return ""


def format_amount_unit_for_display(amount_per_100g: float, unit: str) -> tuple[str, str]:
    if amount_per_100g <= 0:
        raw = str(unit or "").strip().lower()
        if raw in {"mg", "milligram", "milligrams"}:
            return "", "mg"
        if raw in {"mcg", "ug", "μg", "µg", "microgram", "micrograms"}:
            return "", "mcg"
        if raw in {"g", "gram", "grams"}:
            return "", "g"
        if raw in {"iu", "ui", "ie"}:
            return "", "IU"
        return "", str(unit or "")

    # Keep source units (e.g., mg, mcg) to preserve concentration precision.
    raw = str(unit or "").strip().lower()
    if raw in {"mg", "milligram", "milligrams"}:
        source_unit = "mg"
    elif raw in {"mcg", "ug", "μg", "µg", "microgram", "micrograms"}:
        source_unit = "mcg"
    elif raw in {"g", "gram", "grams"}:
        source_unit = "g"
    elif raw in {"iu", "ui", "ie"}:
        source_unit = "IU"
    else:
        source_unit = str(unit or "")
    amount_txt = _format_nonzero_value(float(amount_per_100g), 2, 8)
    return amount_txt, source_unit


def format_amount_unit_for_dropdown(amount_per_100g: float, unit: str) -> tuple[str, str]:
    return format_amount_unit_for_display(amount_per_100g, unit)


def _whole_food_preparation_penalty(food_description: str) -> int:
    text = normalize_lookup_key(food_description)
    penalty = 0
    if any(flag in text for flag in ["peeled", "without peel", "without skin", "skin removed"]):
        penalty += 2
    if "drained" in text:
        penalty += 1
    return penalty


def estimate_whole_food_units(food_description: str, grams_needed: float | None) -> str:
    if grams_needed is None or grams_needed <= 0:
        return ""

    text = normalize_lookup_key(food_description)
    for keyword, singular, plural, avg_weight_g in WHOLE_FOOD_UNIT_ESTIMATES:
        if keyword in text and avg_weight_g > 0:
            units = float(grams_needed) / float(avg_weight_g)
            if units <= 0:
                return ""

            if units >= 2:
                shown_units = float(math.ceil(units))
                units_txt = format_float(shown_units, 0)
            else:
                shown_units = round(units, 1)
                units_txt = format_float(shown_units, 1)

            try:
                is_single = abs(float(units_txt) - 1.0) < 1e-9
            except Exception:
                is_single = False

            noun = singular if is_single else plural
            return (
                f"Approximate whole-food portion: ~{units_txt} {noun} "
                f"(assuming ~{format_float(float(avg_weight_g), 0)} g each)."
            )

    return ""


def estimate_volume_units(food_description: str, grams_needed: float | None) -> str:
    if grams_needed is None or grams_needed <= 0:
        return ""

    text = normalize_lookup_key(food_description)
    for keyword, singular, plural, grams_per_unit in VOLUME_FOOD_ESTIMATES:
        if keyword in text and grams_per_unit > 0:
            units = float(grams_needed) / float(grams_per_unit)
            if units <= 0:
                return ""

            if units >= 2:
                shown_units = round(units, 1)
                units_txt = format_float(shown_units, 1)
            else:
                shown_units = round(units, 2)
                units_txt = format_float(shown_units, 2)

            try:
                is_single = abs(float(units_txt) - 1.0) < 1e-9
            except Exception:
                is_single = False

            noun = singular if is_single else plural
            return (
                f"Approximate household portion: ~{units_txt} {noun} "
                f"(assuming ~{format_float(float(grams_per_unit), 0)} g per cup)."
            )

    return ""


def simplify_food_name_for_summary(food_description: str) -> str:
    raw = str(food_description or "").strip()
    if not raw:
        return "whole food"

    first_chunk = raw.split(",", 1)[0].strip()
    cleaned = re.sub(r"\b(raw|cooked|boiled|steamed|fried|roasted|grilled|peeled|drained|without skin|with skin)\b", "", first_chunk, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -")
    return cleaned or first_chunk or raw


def format_top_sentence_portion(food_description: str, grams_needed: float | None, typical_serving_g: float | None = None) -> str:
    simple_food = simplify_food_name_for_summary(food_description)
    if grams_needed is None or grams_needed <= 0:
        return f"a practical serving of {simple_food}"

    grams = float(grams_needed)
    text = normalize_lookup_key(food_description)

    for keyword, singular, plural, avg_weight_g in WHOLE_FOOD_UNIT_ESTIMATES:
        if keyword in text and avg_weight_g > 0:
            units = grams / float(avg_weight_g)
            if units >= 2:
                shown_units = float(math.ceil(units))
                units_txt = format_float(shown_units, 0)
            elif units >= 1:
                shown_units = round(units, 1)
                units_txt = format_float(shown_units, 1)
            else:
                shown_units = round(units, 2)
                units_txt = format_float(shown_units, 2)

            try:
                is_single = abs(float(units_txt) - 1.0) < 1e-9
            except Exception:
                is_single = False

            noun = singular if is_single else plural
            return f"about {units_txt} {noun}"

    for keyword, singular, plural, grams_per_unit in VOLUME_FOOD_ESTIMATES:
        if keyword in text and grams_per_unit > 0:
            units = grams / float(grams_per_unit)
            if units >= 2:
                shown_units = round(units, 1)
                units_txt = format_float(shown_units, 1)
            else:
                shown_units = round(units, 2)
                units_txt = format_float(shown_units, 2)
            try:
                is_single = abs(float(units_txt) - 1.0) < 1e-9
            except Exception:
                is_single = False
            noun = singular if is_single else plural
            return f"about {units_txt} {noun} of {simple_food}"

    if typical_serving_g is not None and typical_serving_g > 0:
        serving_count = grams / float(typical_serving_g)
        if serving_count >= 2:
            count_txt = format_float(round(serving_count, 1), 1)
            return f"about {count_txt} servings of {simple_food}"
        if serving_count >= 1:
            return f"about 1 serving of {simple_food}"

    return f"about 1 serving of {simple_food}"


def format_weight_equivalents(grams_needed: float | None) -> str:
    if grams_needed is None or grams_needed <= 0:
        return ""

    grams = float(grams_needed)
    parts: list[str] = [f"~{format_float(grams)} g"]

    if grams >= 1000:
        parts.append(f"~{format_float(grams / 1000.0)} kg")

    ounces = grams / 28.349523125
    parts.append(f"~{format_float(ounces)} oz")

    if ounces >= 16:
        pounds = ounces / 16.0
        parts.append(f"~{format_float(pounds)} lb")

    return "Equivalent measures: " + " | ".join(parts)


# Practical serving-size defaults used only for the top brief recommendation sentence.
DEFAULT_SERVING_TYPICAL_G = 120.0
DEFAULT_SERVING_MAX_G = 300.0

SERVING_SIZE_OVERRIDES: list[tuple[str, float, float, str]] = [
    ("kale", 70.0, 150.0, "leafy_green_override"),
    ("spinach", 70.0, 150.0, "leafy_green_override"),
    ("lettuce", 80.0, 180.0, "leafy_green_override"),
    ("arugula", 60.0, 130.0, "leafy_green_override"),
    ("collard", 80.0, 170.0, "leafy_green_override"),
    ("broccoli", 120.0, 300.0, "cruciferous_override"),
    ("cauliflower", 120.0, 300.0, "cruciferous_override"),
    ("brussels", 120.0, 260.0, "cruciferous_override"),
    ("liver", 30.0, 75.0, "organ_meat_override"),
    ("seaweed", 5.0, 15.0, "seaweed_override"),
    ("brazil nut", 10.0, 20.0, "nut_override"),
]

SERVING_SIZE_GROUP_RULES: list[tuple[tuple[str, ...], float, float, str]] = [
    (("kale", "spinach", "lettuce", "arugula", "collard", "chard"), 80.0, 180.0, "leafy_green_group"),
    (("broccoli", "cauliflower", "brussels", "cabbage"), 120.0, 320.0, "cruciferous_group"),
    (("blueberry", "strawberry", "raspberry", "blackberry"), 140.0, 300.0, "berries_group"),
    (("banana", "apple", "orange", "mango", "kiwi", "pear", "grape", "melon", "pineapple"), 150.0, 350.0, "fruit_group"),
    (("lentil", "bean", "chickpea", "pea"), 150.0, 350.0, "legume_group"),
    (("rice", "oat", "quinoa", "barley"), 150.0, 350.0, "grain_group"),
    (("almond", "walnut", "cashew", "pistachio", "seed", "flax", "chia"), 30.0, 70.0, "nuts_seeds_group"),
    (("salmon", "tuna", "sardine", "chicken", "beef", "pork", "egg"), 120.0, 320.0, "animal_protein_group"),
]


def get_practical_serving_limits(food_description: str) -> dict[str, Any]:
    key = normalize_lookup_key(food_description)
    if not key:
        return {
            "typical_g": DEFAULT_SERVING_TYPICAL_G,
            "max_g": DEFAULT_SERVING_MAX_G,
            "source": "default",
        }

    for token, typical_g, max_g, source in SERVING_SIZE_OVERRIDES:
        if token in key:
            return {
                "typical_g": float(typical_g),
                "max_g": float(max_g),
                "source": source,
            }

    for tokens, typical_g, max_g, source in SERVING_SIZE_GROUP_RULES:
        if any(tok in key for tok in tokens):
            return {
                "typical_g": float(typical_g),
                "max_g": float(max_g),
                "source": source,
            }

    return {
        "typical_g": DEFAULT_SERVING_TYPICAL_G,
        "max_g": DEFAULT_SERVING_MAX_G,
        "source": "default",
    }


def summarize_combined_food_coverage(selected_matches: list[dict[str, Any]]) -> dict[str, Any]:
    by_food: dict[str, dict[str, Any]] = {}
    all_components: set[str] = set()

    for row in selected_matches:
        component = str(row.get("component", "") or "").strip()
        food_name = str(row.get("food_description", "") or "").strip()
        grams_needed = row.get("grams_needed")
        price_per_kg = row.get("price_per_kg")
        currency = str(row.get("currency", "") or "").strip()

        if component:
            all_components.add(component)
        if not food_name or grams_needed is None:
            continue
        try:
            grams_value = float(grams_needed)
        except (ValueError, TypeError) as e:
            logger.debug(f"Invalid grams_needed value: {grams_needed}: {e}")
            continue
        if grams_value <= 0:
            continue

        food_key = normalize_lookup_key(food_name)
        if food_key not in by_food:
            by_food[food_key] = {
                "food": food_name,
                "components": set(),
                "required_grams": 0.0,
                "price_per_kg": None,
                "currency": currency,
            }

        food_entry = by_food[food_key]
        if component:
            food_entry["components"].add(component)
        # Sum requirements across covered nutrients so one food shown multiple times
        # is represented as a single combined serving estimate.
        food_entry["required_grams"] = float(food_entry["required_grams"]) + grams_value

        if food_entry.get("price_per_kg") is None and price_per_kg is not None:
            try:
                food_entry["price_per_kg"] = float(price_per_kg)
            except Exception:
                pass

    summary_rows: list[dict[str, Any]] = []
    covered_components: set[str] = set()
    for entry in by_food.values():
        components = sorted(list(entry.get("components", set())))
        covered_components.update(components)
        required_grams = float(entry.get("required_grams", 0.0) or 0.0)
        price_per_kg = entry.get("price_per_kg")
        estimated_cost = None
        if price_per_kg is not None and required_grams > 0:
            estimated_cost = (required_grams / 1000.0) * float(price_per_kg)

        summary_rows.append(
            {
                "food": str(entry.get("food", "") or ""),
                "components": components,
                "required_grams": required_grams,
                "estimated_cost": estimated_cost,
                "currency": str(entry.get("currency", "") or ""),
            }
        )

    summary_rows.sort(key=lambda r: (-len(r.get("components", [])), float(r.get("required_grams", 0.0))))

    return {
        "rows": summary_rows,
        "total_components": len(all_components),
        "covered_components": len(covered_components),
    }


def _sunlight_guidance_note_from_coverage(
    component_labels: dict[str, str],
    uncovered_components: set[str],
    min_single_food_grams: dict[str, float],
) -> str:
    vitamin_d_components: list[str] = []
    for comp_key, label in component_labels.items():
        norm = normalize_lookup_key(label)
        if norm.startswith("vitamin d") or norm in {"d", "d2", "d3"}:
            vitamin_d_components.append(comp_key)

    if not vitamin_d_components:
        return ""

    if any(comp_key in uncovered_components for comp_key in vitamin_d_components):
        return (
            "Vitamin D appears difficult to cover with practical food servings under current filters. "
            "For many people, discussing safe sunlight exposure timing with a clinician can be a practical complement."
        )

    large_threshold_g = 350.0
    if any(float(min_single_food_grams.get(comp_key, 0.0) or 0.0) >= large_threshold_g for comp_key in vitamin_d_components):
        return (
            "Vitamin D replacement may require large food portions. "
            "A practical option to discuss with a clinician is safe sunlight exposure as a complement."
        )

    return ""


def build_auto_consolidated_food_plan(component_candidates: list[dict[str, Any]], max_foods: int = 10) -> dict[str, Any]:
    # Joint optimization objective (heuristic): satisfy all component targets while
    # minimizing total grams by leveraging secondary nutrient contributions per food.
    food_pool: dict[str, dict[str, Any]] = {}
    component_labels: dict[str, str] = {}

    for cand in component_candidates:
        component_label = str(cand.get("component", "") or "").strip()
        component_key = normalize_lookup_key(component_label)
        dose_value = cand.get("dose_value")
        dose_unit = str(cand.get("dose_unit", "") or "")
        foods = cand.get("foods", []) or []

        if not component_key or dose_value is None:
            continue
        component_labels.setdefault(component_key, component_label)

        for food in foods[:25]:
            food_name = str(food.get("food_description", "") or "").strip()
            if not food_name:
                continue

            limits = get_practical_serving_limits(food_name)
            max_g = float(limits.get("max_g", DEFAULT_SERVING_MAX_G) or DEFAULT_SERVING_MAX_G)
            typical_g = float(limits.get("typical_g", DEFAULT_SERVING_TYPICAL_G) or DEFAULT_SERVING_TYPICAL_G)
            try:
                amt = float(food.get("amount_per_100g", 0.0) or 0.0)
            except (ValueError, TypeError) as e:
                logger.debug(f"Invalid amount_per_100g value: {e}")
                amt = 0.0
            unit = str(food.get("unit", "") or "")

            grams = grams_needed_to_match_dose(
                dose_value,
                dose_unit,
                amt,
                unit,
                component_name=component_label,
            )
            if grams is None or grams <= 0:
                continue
            if float(grams) > max_g:
                continue

            food_key = normalize_lookup_key(food_name)
            if food_key not in food_pool:
                food_pool[food_key] = {
                    "food": food_name,
                    "grams_by_component": {},
                    "serving_typical_g": typical_g,
                    "serving_max_g": max_g,
                }

            existing = food_pool[food_key]["grams_by_component"].get(component_key)
            if existing is None or float(grams) < float(existing):
                food_pool[food_key]["grams_by_component"][component_key] = float(grams)

    components_all = set(component_labels.keys())
    if not components_all:
        return {
            "rows": [],
            "total_components": 0,
            "covered_components": 0,
            "uncovered_components": [],
            "sunlight_note": "",
        }

    min_single_food_grams: dict[str, float] = {}
    for comp_key in components_all:
        best = None
        for entry in food_pool.values():
            grams_map = entry.get("grams_by_component", {})
            if comp_key not in grams_map:
                continue
            value = float(grams_map[comp_key])
            if best is None or value < best:
                best = value
        if best is not None:
            min_single_food_grams[comp_key] = float(best)

    deficits: dict[str, float] = {comp_key: 1.0 for comp_key in components_all}
    allocated_grams: dict[str, float] = {food_key: 0.0 for food_key in food_pool.keys()}

    max_foods = max(1, int(max_foods))
    max_iterations = 1200
    for _ in range(max_iterations):
        unmet = [comp for comp, deficit in deficits.items() if deficit > 1e-6]
        if not unmet:
            break

        used_food_count = sum(1 for grams in allocated_grams.values() if grams > 1e-6)
        best_choice: tuple[str, float, float, float] | None = None

        for food_key, entry in food_pool.items():
            current_g = float(allocated_grams.get(food_key, 0.0) or 0.0)
            remaining_g = float(entry.get("serving_max_g", DEFAULT_SERVING_MAX_G) or DEFAULT_SERVING_MAX_G) - current_g
            if remaining_g <= 1e-6:
                continue
            if current_g <= 1e-6 and used_food_count >= max_foods:
                continue

            grams_map: dict[str, float] = entry.get("grams_by_component", {})
            feasible = [comp for comp in unmet if comp in grams_map and float(grams_map[comp]) > 0]
            if not feasible:
                continue

            required_step = min(float(deficits[comp]) * float(grams_map[comp]) for comp in feasible)
            step_g = min(remaining_g, required_step)
            if step_g <= 1e-6:
                continue

            gain = 0.0
            for comp in feasible:
                gain += min(float(deficits[comp]), step_g / float(grams_map[comp]))
            if gain <= 1e-9:
                continue

            typical_g = float(entry.get("serving_typical_g", DEFAULT_SERVING_TYPICAL_G) or DEFAULT_SERVING_TYPICAL_G)
            burden = step_g / max(1.0, typical_g)
            score = (gain / step_g) / (1.0 + 0.2 * burden)

            if best_choice is None or score > best_choice[3] + 1e-12:
                best_choice = (food_key, step_g, gain, score)
            elif best_choice is not None and abs(score - best_choice[3]) <= 1e-12 and step_g < best_choice[1]:
                best_choice = (food_key, step_g, gain, score)

        if best_choice is None:
            break

        chosen_food_key, step_g, _, _ = best_choice
        allocated_grams[chosen_food_key] = float(allocated_grams.get(chosen_food_key, 0.0) or 0.0) + float(step_g)
        grams_map = food_pool[chosen_food_key].get("grams_by_component", {})
        for comp in list(deficits.keys()):
            grams_for_full = grams_map.get(comp)
            if grams_for_full is None or float(grams_for_full) <= 0:
                continue
            deficits[comp] = max(0.0, float(deficits[comp]) - (float(step_g) / float(grams_for_full)))

    selected_rows: list[dict[str, Any]] = []
    covered_components: set[str] = set()
    for food_key, grams in allocated_grams.items():
        if float(grams) <= 1e-6:
            continue
        entry = food_pool[food_key]
        grams_map: dict[str, float] = entry.get("grams_by_component", {})
        covered_for_food: list[str] = []
        for comp_key, target_grams in grams_map.items():
            if float(target_grams) <= 0:
                continue
            contribution_ratio = float(grams) / float(target_grams)
            if contribution_ratio >= 0.05:
                covered_for_food.append(component_labels.get(comp_key, comp_key))
            if contribution_ratio >= 1.0 - 1e-6:
                covered_components.add(comp_key)

        selected_rows.append(
            {
                "food": str(entry.get("food", "") or ""),
                "components": sorted(covered_for_food),
                "required_grams": float(grams),
                "serving_typical_g": float(entry.get("serving_typical_g", DEFAULT_SERVING_TYPICAL_G) or DEFAULT_SERVING_TYPICAL_G),
                "serving_max_g": float(entry.get("serving_max_g", DEFAULT_SERVING_MAX_G) or DEFAULT_SERVING_MAX_G),
            }
        )

    uncovered_components = {comp for comp in components_all if float(deficits.get(comp, 1.0)) > 0.02}
    covered_count = len(components_all) - len(uncovered_components)

    selected_rows.sort(key=lambda r: (-len(r.get("components", [])), float(r.get("required_grams", 0.0))))
    sunlight_note = _sunlight_guidance_note_from_coverage(component_labels, uncovered_components, min_single_food_grams)

    return {
        "rows": selected_rows,
        "total_components": len(components_all),
        "covered_components": covered_count,
        "uncovered_components": sorted([component_labels.get(comp, comp) for comp in uncovered_components]),
        "sunlight_note": sunlight_note,
    }


def build_food_summary_review(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        food = str(row.get("food", "") or "").strip()
        if not food:
            continue

        key = normalize_lookup_key(food)
        if not key:
            continue

        try:
            grams = float(row.get("required_grams", 0.0) or 0.0)
        except Exception:
            grams = 0.0
        try:
            serving_typical = float(row.get("serving_typical_g", 0.0) or 0.0)
        except Exception:
            serving_typical = 0.0

        servings = None
        if grams > 0 and serving_typical > 0:
            servings = grams / serving_typical

        if key not in grouped:
            grouped[key] = {
                "food": food,
                "components": set(),
                "required_grams": 0.0,
                "serving_typical_g": serving_typical,
                "occurrences": 0,
                "grams_list": [],
                "servings_list": [],
                "total_grams": 0.0,
                "total_servings": 0.0,
            }

        entry = grouped[key]
        entry["occurrences"] = int(entry.get("occurrences", 0)) + 1
        entry["grams_list"].append(grams)
        entry["required_grams"] = float(entry.get("required_grams", 0.0)) + max(0.0, grams)
        entry["total_grams"] = float(entry.get("total_grams", 0.0)) + max(0.0, grams)
        if float(entry.get("serving_typical_g", 0.0) or 0.0) <= 0 and serving_typical > 0:
            entry["serving_typical_g"] = serving_typical
        if servings is not None:
            entry["servings_list"].append(servings)
            entry["total_servings"] = float(entry.get("total_servings", 0.0)) + servings

        for comp in row.get("components", []) or []:
            comp_txt = str(comp or "").strip()
            if comp_txt:
                entry["components"].add(comp_txt)

    merged_rows = [
        {
            "food": str(entry.get("food", "") or "Unknown food"),
            "components": sorted(list(entry.get("components", set()))),
            "required_grams": float(entry.get("required_grams", 0.0) or 0.0),
            "serving_typical_g": float(entry.get("serving_typical_g", 0.0) or 0.0),
        }
        for entry in grouped.values()
    ]
    merged_rows.sort(key=lambda r: (-len(r.get("components", [])), float(r.get("required_grams", 0.0) or 0.0)))

    redundancy_report: list[dict[str, Any]] = []
    for entry in grouped.values():
        if int(entry.get("occurrences", 0)) <= 1:
            continue
        grams_items = [format_float(float(g), 1) for g in entry.get("grams_list", [])]
        servings_items = [format_float(float(s), 2) for s in entry.get("servings_list", [])]
        redundancy_report.append(
            {
                "food": str(entry.get("food", "") or ""),
                "occurrences": int(entry.get("occurrences", 0)),
                "per_row_grams": " + ".join(grams_items),
                "total_grams": format_float(float(entry.get("total_grams", 0.0)), 1),
                "per_row_servings": " + ".join(servings_items) if servings_items else "N/A",
                "total_servings": (
                    format_float(float(entry.get("total_servings", 0.0)), 2)
                    if float(entry.get("total_servings", 0.0)) > 0
                    else "N/A"
                ),
            }
        )
    redundancy_report.sort(key=lambda r: (-int(r.get("occurrences", 0)), str(r.get("food", "") or "")))

    signature = "|".join(
        sorted(
            [
                (
                    f"{normalize_lookup_key(str(r.get('food', '') or ''))}:"
                    f"{format_float(float(r.get('required_grams', 0.0) or 0.0), 3)}:"
                    f"{','.join(sorted([str(c or '').strip() for c in (r.get('components', []) or []) if str(c or '').strip()]))}"
                )
                for r in merged_rows
            ]
        )
    )

    return {
        "merged_rows": merged_rows,
        "redundancy_report": redundancy_report,
        "signature": signature,
    }


def format_top_recommendation_sentence(
    plan: dict[str, Any],
    max_foods_to_show: int = 10,
    prepared_rows: list[dict[str, Any]] | None = None,
) -> str:
    rows = list(prepared_rows or [])
    if not rows:
        prepared = build_food_summary_review(plan.get("rows", []) or [])
        rows = list(prepared.get("merged_rows", []) or [])

    total = int(plan.get("total_components", 0) or 0)
    covered = int(plan.get("covered_components", 0) or 0)
    if not rows or total <= 0:
        return "We could not build a reliable whole-food replacement yet, so please review the alternatives below."

    max_foods_to_show = max(1, int(max_foods_to_show))
    shown = rows[:max_foods_to_show]
    parts: list[str] = []
    component_names: set[str] = set()
    for row in shown:
        food = str(row.get("food", "Unknown food") or "Unknown food")
        grams_needed = float(row.get("required_grams", 0.0) or 0.0)
        typical_serving_g = float(row.get("serving_typical_g", 0.0) or 0.0)
        parts.append(format_top_sentence_portion(food, grams_needed, typical_serving_g))
        for comp in row.get("components", []) or []:
            comp_txt = str(comp or "").strip()
            if comp_txt:
                component_names.add(comp_txt)

    more_count = max(0, len(rows) - len(shown))
    foods_txt = ", ".join(parts)
    if more_count > 0:
        foods_txt += f", and {more_count} more"

    components_txt = ", ".join(sorted(component_names)) if component_names else "your listed nutrients"
    sunlight_note = str(plan.get("sunlight_note", "") or "").strip()

    sentence = ""
    if covered >= total:
        sentence = (
            "Instead of consuming your supplement containing "
            f"{components_txt}, you can simply eat {foods_txt}."
        )
    else:
        sentence = (
            "Instead of consuming your supplement containing "
            f"{components_txt}, you can simply eat {foods_txt}; you may still need extra foods for the remaining nutrients."
        )

    if sunlight_note:
        sentence = f"{sentence} {sunlight_note}".strip()
    return sentence




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


def _normalize_barcode_digits(value: str) -> str:
    digits = re.sub(r"\D+", "", str(value or ""))
    if 8 <= len(digits) <= 14:
        return digits
    return ""


def detect_barcode_from_image(image_bytes: bytes) -> tuple[str, str]:
    """Return (barcode, method). method is one of: pyzbar, ocr_fallback, none."""
    if not image_bytes:
        return "", "none"

    try:
        from pyzbar.pyzbar import decode as zbar_decode

        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        decoded = zbar_decode(image)
        candidates: list[str] = []
        for item in decoded:
            try:
                val = item.data.decode("utf-8", errors="ignore")
            except Exception:
                val = ""
            normalized = _normalize_barcode_digits(val)
            if normalized:
                candidates.append(normalized)
        if candidates:
            candidates.sort(key=len, reverse=True)
            return candidates[0], "pyzbar"
    except Exception:
        pass

    try:
        # Fallback path: OCR near-barcode text and extract best EAN-like token.
        ocr_text = try_tesseract_ocr(image_bytes)
        token = _normalize_barcode_digits(_extract_ean_from_text(ocr_text))
        if token:
            return token, "ocr_fallback"
    except Exception:
        pass

    return "", "none"




def _lookup_secondary_barcode_identity(barcode: str) -> tuple[str, str, str, str]:
    """
    Secondary lookup for product identity only when OFF is missing/incomplete.
    Returns (text, provider, reason, product_url).
    """
    normalized_barcode = _normalize_barcode_digits(barcode)
    if not normalized_barcode:
        return "", "", "", ""

    upcitemdb_url = f"https://api.upcitemdb.com/prod/trial/lookup?upc={normalized_barcode}"
    try:
        resp = _http_get(
            upcitemdb_url,
            timeout=HTTP_TIMEOUT,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; SuppSwap/1.0; +https://example.local)",
                "Accept": "application/json",
            },
        )
        if resp.status_code != 200:
            return "", "", "", ""
        data = resp.json() if resp.content else {}
        items = data.get("items", []) if isinstance(data, dict) else []
        if not isinstance(items, list) or not items:
            return "", "", "", ""

        item = items[0] if isinstance(items[0], dict) else {}
        title = str(item.get("title", "") or "").strip()
        brand = str(item.get("brand", "") or "").strip()
        if not title and not brand:
            return "", "", "", ""

        title_line = " ".join([x for x in [title, brand] if x]).strip()
        result_text = f"Product: {title_line}" if title_line else ""
        return (
            result_text,
            "UPCItemDB",
            "Barcode identity resolved from secondary provider (no structured micronutrient facts provided).",
            upcitemdb_url,
        )
    except Exception:
        return "", "", "", ""


EAN_WEB_TRUSTED_DOMAINS: tuple[str, ...] = (
    "optimumnutrition.com",
    "hsnstore.com",
    "hollandandbarrett.com",
    "boots.com",
    "superdrug.com",
    "amazon.",
    "iherb.com",
    "bodybuilding.com",
    "myprotein.",
    "world.openfoodfacts.org",
)


def _normalize_search_result_url(raw_url: str) -> str:
    url = str(raw_url or "").strip()
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        if "duckduckgo.com" in parsed.netloc.lower() and parsed.path.startswith("/l/"):
            q = parse_qs(parsed.query)
            uddg = str((q.get("uddg") or [""])[0] or "").strip()
            if uddg:
                return unquote(uddg)
    except Exception:
        return url
    return url


def _is_trusted_ean_source_url(url: str) -> bool:
    try:
        host = str(urlparse(str(url or "")).netloc or "").lower()
    except Exception:
        return False
    if not host:
        return False
    return any(token in host for token in EAN_WEB_TRUSTED_DOMAINS)


def _search_trusted_ean_urls(barcode: str, product_name: str = "") -> list[str]:
    normalized_barcode = _normalize_barcode_digits(barcode)
    if not normalized_barcode:
        return []

    queries = [
        f"{normalized_barcode} supplement facts",
        f"{normalized_barcode} nutrition label",
    ]
    if str(product_name or "").strip():
        queries.append(f"{product_name} {normalized_barcode} supplement facts")

    out: list[str] = []
    seen: set[str] = set()
    for query in queries:
        search_url = "https://duckduckgo.com/html/?q=" + quote_plus(query)
        try:
            response = _http_get(
                search_url,
                timeout=HTTP_TIMEOUT,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; SuppSwap/1.0; +https://example.local)",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )
            if response.status_code != 200:
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            for a in soup.find_all("a"):
                href = str(a.get("href", "") or "").strip()
                if not href:
                    continue
                resolved = _normalize_search_result_url(href)
                if not resolved or not resolved.startswith(("http://", "https://")):
                    continue
                if not _is_trusted_ean_source_url(resolved):
                    continue
                if resolved in seen:
                    continue
                seen.add(resolved)
                out.append(resolved)
                if len(out) >= 8:
                    return out
        except Exception:
            continue

    return out


def _is_micronutrient_component_name(component: str) -> bool:
    name = normalize_lookup_key(component)
    if not name:
        return False
    macro_terms = {
        "energy",
        "fat",
        "saturated fat",
        "carbohydrate",
        "carbohydrates",
        "sugar",
        "sugars",
        "fiber",
        "protein",
        "proteins",
        "salt",
        "sodium",
    }
    if name in macro_terms:
        return False
    if any(name.startswith(x) for x in ("vitamin ",)):
        return True
    return bool(
        re.search(
            r"\b(?:thiamin|riboflavin|niacin|folate|folic|biotin|calcium|iron|magnesium|zinc|selenium|"
            r"iodine|chromium|molybdenum|copper|manganese|potassium|phosphorus|fluoride|fluorine|cesium)\b",
            name,
            re.I,
        )
    )


def _rows_to_supplement_text(rows: list[dict[str, Any]], product_name: str = "") -> str:
    if not rows:
        return ""
    lines: list[str] = []
    seen: set[str] = set()
    for row in rows:
        component = str(row.get("component", "") or "").strip()
        if not _is_micronutrient_component_name(component):
            continue
        dose_value = _parse_float(row.get("dose_value"))
        if dose_value is None or dose_value <= 0:
            continue
        dose_unit = _normalize_component_unit_token(str(row.get("dose_unit", "") or ""))
        if dose_unit not in ALLOWED_DOSE_UNITS or dose_unit in {"", "g", "kcal"}:
            continue
        key = f"{normalize_lookup_key(component)}|{dose_value}|{dose_unit}"
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"{component} {format_float(float(dose_value))} {dose_unit}".strip())

    if len(lines) < 2:
        return ""

    out_parts: list[str] = []
    if str(product_name or "").strip():
        out_parts.append(f"Product: {product_name.strip()}")
    out_parts.append("Nutrition Information")
    out_parts.extend(lines)
    return "\n".join(out_parts)


def _extract_micronutrient_rows_from_url_deterministic(url: str) -> list[dict[str, Any]]:
    rows_out: list[dict[str, Any]] = []
    seen_components: set[str] = set()
    host = str(urlparse(str(url or "")).netloc or "").lower()

    # Page-text deterministic parsing.
    page_text = fetch_clean_page_text(url)
    if page_text:
        local_text = extract_supplement_text_from_page_text_local(page_text)
        if local_text:
            payload = build_structured_nutrients_json(local_text)
            for row in list(payload.get("nutrients", []) or []):
                component = str(row.get("component", "") or "").strip()
                if not _is_micronutrient_component_name(component):
                    continue
                key = normalize_lookup_key(component)
                if key in seen_components:
                    continue
                seen_components.add(key)
                rows_out.append(row)

    # Nutrition-image deterministic OCR parsing (expensive): only run on likely
    # product pages and skip OFF where we already have a dedicated table parser.
    should_try_image_ocr = (
        "openfoodfacts.org" not in host
        and len(rows_out) < 3
        and bool(re.search(r"\b(?:supplement\s+facts|nutrition\s+facts|ingredients|serving\s+size|vitamin)\b", page_text or "", re.I))
    )
    if should_try_image_ocr:
        image_rows = extract_nutrition_doses_from_product_image(url)
        for row in image_rows:
            component = str(row.get("component", "") or "").strip()
            if not _is_micronutrient_component_name(component):
                continue
            key = normalize_lookup_key(component)
            if key in seen_components:
                continue
            seen_components.add(key)
            rows_out.append(row)

    validated, _ = validate_parsed_components(rows_out)
    return validated


def _lookup_ean_micronutrients_from_web(barcode: str, product_name: str = "") -> tuple[str, str, str, str]:
    normalized_barcode = _normalize_barcode_digits(barcode)
    if not normalized_barcode:
        return "", "", "", ""

    candidate_urls = _search_trusted_ean_urls(normalized_barcode, product_name)
    if not candidate_urls:
        return "", "", "", ""

    best_text = ""
    best_url = ""
    best_count = 0
    for url in candidate_urls[:5]:
        rows = _extract_micronutrient_rows_from_url_deterministic(url)
        text = _rows_to_supplement_text(rows, product_name=product_name)
        if not text:
            continue
        row_count = len(rows)
        if row_count > best_count:
            best_count = row_count
            best_text = text
            best_url = url
        if row_count >= 8:
            break

    if not best_text:
        return "", "", "", ""
    if not _barcode_text_has_micronutrient_signal(best_text):
        return "", "", "", ""

    return (
        best_text,
        "EANWebFallback",
        "Barcode resolved; micronutrients extracted from trusted web source fallback.",
        best_url,
    )


def extract_supplement_text_from_barcode(barcode: str) -> tuple[str, str, str, str]:
    """
    Resolve product text from barcode using OpenFoodFacts.
    Returns (text, provider, reason, product_url).
    """
    normalized_barcode = _normalize_barcode_digits(barcode)
    if not normalized_barcode:
        return "", "", "Invalid barcode format. Expected 8-14 digits.", ""

    api_url = f"https://world.openfoodfacts.org/api/v2/product/{normalized_barcode}.json"
    product_url = f"https://world.openfoodfacts.org/product/{normalized_barcode}"

    try:
        response = _http_get(
            api_url,
            timeout=HTTP_TIMEOUT,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; SuppSwap/1.0; +https://example.local)",
                "Accept": "application/json",
            },
        )
        if response.status_code != 200:
            secondary = _lookup_secondary_barcode_identity(normalized_barcode)
            if secondary[0]:
                return secondary
            return "", "OpenFoodFacts", f"OpenFoodFacts HTTP {response.status_code}.", product_url

        data = response.json() if response.content else {}
        if int(data.get("status", 0) or 0) != 1:
            secondary = _lookup_secondary_barcode_identity(normalized_barcode)
            if secondary[0]:
                return secondary
            return "", "OpenFoodFacts", "Barcode not found in OpenFoodFacts.", product_url

        product = data.get("product", {}) if isinstance(data, dict) else {}
        name = str(product.get("product_name", "") or "").strip()
        brands = str(product.get("brands", "") or "").strip()
        ingredients = str(
            product.get("ingredients_text_en", "")
            or product.get("ingredients_text", "")
            or ""
        ).strip()

        nutriments = product.get("nutriments", {}) if isinstance(product.get("nutriments", {}), dict) else {}

        nutrient_aliases: list[tuple[str, str]] = [
            ("vitamin-a", "Vitamin A"),
            ("vitamin-c", "Vitamin C"),
            ("vitamin-d", "Vitamin D"),
            ("vitamin-e", "Vitamin E"),
            ("vitamin-k", "Vitamin K1"),
            ("vitamin-b1", "Vitamin B1"),
            ("thiamin", "Vitamin B1"),
            ("vitamin-b2", "Vitamin B2"),
            ("riboflavin", "Vitamin B2"),
            ("vitamin-b3", "Vitamin B3"),
            ("niacin", "Vitamin B3"),
            ("vitamin-b5", "Vitamin B5"),
            ("pantothenic-acid", "Vitamin B5"),
            ("vitamin-b6", "Vitamin B6"),
            ("vitamin-b9", "Folic Acid"),
            ("folates", "Folic Acid"),
            ("folic-acid", "Folic Acid"),
            ("vitamin-b12", "Vitamin B12"),
            ("biotin", "Biotin"),
            ("calcium", "Calcium"),
            ("phosphorus", "Phosphorus"),
            ("potassium", "Potassium"),
            ("magnesium", "Magnesium"),
            ("iron", "Iron"),
            ("copper", "Copper"),
            ("manganese", "Manganese"),
            ("boron", "Boron"),
            ("fluoride", "Fluoride"),
            ("fluorine", "Fluoride"),
            ("cesium", "Cesium"),
            ("iodine", "Iodine"),
            ("chromium", "Chromium"),
            ("selenium", "Selenium"),
            ("molybdenum", "Molybdenum"),
            ("zinc", "Zinc"),
        ]

        def _pick_nutriment_value(base_key: str) -> tuple[float | None, str]:
            candidates = [base_key, f"{base_key}_serving", f"{base_key}_100g"]
            for cand in candidates:
                raw_val = nutriments.get(cand)
                try:
                    val = float(str(raw_val).replace(",", "."))
                except Exception:
                    continue
                if val <= 0:
                    continue
                unit = str(
                    nutriments.get(f"{cand}_unit", "")
                    or nutriments.get(f"{base_key}_unit", "")
                    or ""
                ).strip()
                return val, unit
            return None, ""

        lines: list[str] = []
        used_page_table_fallback = False
        seen_names: set[str] = set()
        for base_key, label in nutrient_aliases:
            if label.lower() in seen_names:
                continue
            value, unit = _pick_nutriment_value(base_key)
            if value is None:
                continue
            seen_names.add(label.lower())
            unit_out = _normalize_component_unit_token(unit)
            lines.append(f"{label} {format_float(value)} {unit_out}".strip())

        # Deterministic trusted-web fallback for micronutrients before macro fallbacks.
        if not lines:
            web_fallback = _lookup_ean_micronutrients_from_web(normalized_barcode, name)
            if web_fallback[0]:
                return web_fallback

        # Fallback for products that expose only macro-style nutriments in OFF.
        if not lines:
            macro_aliases: list[tuple[str, str]] = [
                ("energy-kcal", "Energy"),
                ("energy", "Energy"),
                ("proteins", "Protein"),
                ("protein", "Protein"),
                ("fat", "Fat"),
                ("saturated-fat", "Saturated Fat"),
                ("carbohydrates", "Carbohydrates"),
                ("carbohydrate", "Carbohydrate"),
                ("sugars", "Sugars"),
                ("fiber", "Fiber"),
                ("salt", "Salt"),
                ("sodium", "Sodium"),
            ]
            for base_key, label in macro_aliases:
                if label.lower() in seen_names:
                    continue
                value, unit = _pick_nutriment_value(base_key)
                if value is None:
                    continue
                unit_out = _normalize_component_unit_token(unit)
                if unit_out not in ALLOWED_DOSE_UNITS or not unit_out:
                    continue
                seen_names.add(label.lower())
                lines.append(f"{label} {format_float(value)} {unit_out}".strip())

        # Deterministic OFF page-table fallback when API nutriments are sparse or zero.
        if not lines and product_url:
            page_rows = _extract_openfoodfacts_rows_from_product_page(product_url)
            if page_rows:
                lines.extend(page_rows)
                used_page_table_fallback = True

        serving_size = str(product.get("serving_size", "") or "").strip()

        out_parts: list[str] = []
        title = " ".join([x for x in [name, brands] if x]).strip()
        if title:
            out_parts.append(f"Product: {title}")
        if serving_size:
            out_parts.append(f"Serving Size: {serving_size}")
        if lines:
            out_parts.append("Nutrition Information")
            out_parts.extend(lines)
        if ingredients:
            out_parts.append(f"Ingredients: {ingredients}")

        result_text = "\n".join([x for x in out_parts if str(x).strip()]).strip()
        if not result_text:
            secondary = _lookup_secondary_barcode_identity(normalized_barcode)
            if secondary[0]:
                return secondary
            return "", "OpenFoodFacts", "Barcode resolved but no parseable product fields found.", product_url

        if used_page_table_fallback:
            return (
                result_text,
                "OpenFoodFacts+PageTable",
                "Barcode resolved; used OpenFoodFacts product-page nutrition table fallback.",
                product_url,
            )
        return result_text, "OpenFoodFacts", "Barcode resolved from OpenFoodFacts product data.", product_url
    except Exception as e:
        secondary = _lookup_secondary_barcode_identity(normalized_barcode)
        if secondary[0]:
            return secondary
        return "", "OpenFoodFacts", f"Barcode lookup failed: {e}", product_url


def _barcode_text_has_micronutrient_signal(text: str) -> bool:
    raw = str(text or "")
    if not raw.strip():
        return False

    micro_hint = re.compile(
        r"\b(?:vitamin\s+[abcekd](?:\d{0,2})?|thiamin|riboflavin|niacin|folate|folic\s+acid|biotin|"
        r"calcium|iron|magnesium|zinc|selenium|iodine|chromium|molybdenum|copper|manganese|potassium|phosphorus|fluoride|fluorine|cesium)\b",
        re.I,
    )
    dose_hint = re.compile(r"\b\d+(?:[\.,]\d+)?\s*(?:mg|mcg|ug|µg|μg|iu|ui|ie)\b", re.I)
    macro_hint = re.compile(
        r"\b(?:energy|kcal|fat|saturated\s+fat|carbohydrate|carbohydrates|sugar|sugars|fiber|protein|salt|sodium)\b",
        re.I,
    )

    micro_count = len(micro_hint.findall(raw))
    dose_count = len(dose_hint.findall(raw))
    macro_count = len(macro_hint.findall(raw))
    return micro_count >= 2 and dose_count >= 2 and micro_count >= macro_count


def _barcode_data_needs_label_retry(barcode_text: str, provider: str, reason: str) -> bool:
    provider_key = str(provider or "").strip().lower()
    reason_key = str(reason or "").strip().lower()
    has_micro_signal = _barcode_text_has_micronutrient_signal(barcode_text)
    if "upcitemdb" in provider_key and not has_micro_signal:
        return True
    if "pagetable" in provider_key or "sparse" in reason_key:
        return not has_micro_signal
    return False


def _extract_openfoodfacts_rows_from_product_page(product_url: str) -> list[str]:
    if not str(product_url or "").strip():
        return []

    try:
        response = _http_get(
            product_url,
            timeout=HTTP_TIMEOUT,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; SuppSwap/1.0; +https://example.local)",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        if response.status_code != 200:
            return []
        soup = BeautifulSoup(response.text, "html.parser")
        rows: list[str] = []
        seen_names: set[str] = set()
        nutrient_name_hint = re.compile(
            r"\b(?:energy|fat|saturated\s+fat|carbohydrate|carbohydrates|sugar|sugars|fiber|proteins?|salt|sodium|"
            r"vitamin|folate|folic|niacin|riboflavin|thiamin|biotin|iron|zinc|magnesium|calcium|iodine|selenium|"
            r"chromium|molybdenum|potassium|phosphorus|copper|manganese|fluoride|fluorine|cesium)\b",
            re.I,
        )

        for tr in soup.find_all("tr"):
            row_text = " ".join(str(tr.get_text(" ", strip=True) or "").split())
            if not row_text or len(row_text) > 180:
                continue
            if not nutrient_name_hint.search(row_text):
                continue

            dose_matches = list(
                re.finditer(r"(\d+(?:[\.,]\d+)?)\s*(mg|mcg|ug|µg|μg|g|iu|ui|ie|kcal|kj)\b", row_text, re.I)
            )
            if not dose_matches:
                continue

            chosen_match = None
            for m in reversed(dose_matches):
                try:
                    candidate_val = float(str(m.group(1)).replace(",", "."))
                except Exception:
                    continue
                if candidate_val > 0:
                    chosen_match = m
                    break
            if chosen_match is None:
                continue

            name_raw = row_text[: chosen_match.start()]
            name = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z\s\-]", " ", name_raw)).strip()
            name = re.sub(r"\b(?:mg|mcg|ug|g|iu|ui|ie|kcal|kj)\b\s*$", "", name, flags=re.I).strip()
            if not name:
                continue
            if not nutrient_name_hint.search(name):
                continue

            try:
                value = float(str(chosen_match.group(1)).replace(",", "."))
            except Exception:
                continue
            unit = _normalize_component_unit_token(str(chosen_match.group(2) or ""))
            if unit == "kj":
                value = value / 4.184
                unit = "kcal"
            if unit not in ALLOWED_DOSE_UNITS or not unit or value <= 0:
                continue

            normalized_name = normalize_lookup_key(name)
            if normalized_name in seen_names:
                continue
            seen_names.add(normalized_name)
            rows.append(f"{name} {format_float(value)} {unit}".strip())

        return rows
    except Exception:
        return []


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
        response = _http_get(
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
        response = _http_get("https://serpapi.com/search.json", params=params, timeout=18)
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
        response = _http_post(
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
    if not _text_llm_available():
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
    llm_out = call_text_llm(system_prompt, user_prompt)
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


def _looks_animal_food(text: str) -> bool:
    key = normalize_lookup_key(text)
    animal_tokens = [
        "salmon",
        "sardine",
        "fish",
        "tuna",
        "beef",
        "pork",
        "chicken",
        "turkey",
        "sausage",
        "egg",
        "meat",
    ]
    return any(tok in key for tok in animal_tokens)




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

    should_query_live = bool(enable_live)

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


def _ingredient_grams_for_food(recipe: dict[str, Any], food_name: str) -> float:
    target = normalize_lookup_key(food_name)
    if not target:
        return 0.0

    def _tokens(value: str) -> set[str]:
        toks = set()
        for t in normalize_lookup_key(value).split():
            if not t:
                continue
            if t in {"raw", "fresh", "cooked", "boiled", "steamed", "dried", "peeled", "without", "with", "skin"}:
                continue
            base = t[:-1] if len(t) > 3 and t.endswith("s") else t
            if len(base) >= 3:
                toks.add(base)
        return toks

    target_tokens = _tokens(target)
    best = 0.0
    total_match = 0.0
    for ing in recipe.get("ingredients", []) or []:
        ing_name = normalize_lookup_key(str(ing.get("name", "") or ""))
        if not ing_name:
            continue
        try:
            grams = float(ing.get("grams", 0) or 0)
        except Exception:
            grams = 0.0
        if grams <= 0:
            continue

        direct = target in ing_name or ing_name in target
        ing_tokens = _tokens(ing_name)
        overlap = len(target_tokens & ing_tokens)
        token_match = overlap >= 2 or (overlap >= 1 and len(target_tokens) <= 2)

        if direct or token_match:
            total_match += grams
            if grams > best:
                best = grams

    return total_match if total_match > 0 else best


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


def _dietary_profile_maps(
    profiles: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, str], dict[str, str]]:
    profile_by_id: dict[str, dict[str, Any]] = {}
    profile_id_by_label: dict[str, str] = {}
    profile_label_by_id: dict[str, str] = {}

    for profile in profiles:
        profile_id = normalize_lookup_key(str(profile.get("id", "") or ""))
        label = str(profile.get("label", "No restriction") or "No restriction").strip()
        if not profile_id:
            continue
        profile_by_id[profile_id] = profile
        profile_id_by_label[label] = profile_id
        profile_label_by_id[profile_id] = label

    return profile_by_id, profile_id_by_label, profile_label_by_id


def _default_dietary_profile_id(profiles: list[dict[str, Any]]) -> str:
    profile_by_id, _, _ = _dietary_profile_maps(profiles)
    if "none" in profile_by_id:
        return "none"
    return next(iter(profile_by_id), "")


def _resolve_dietary_profile_selection(
    profiles: list[dict[str, Any]],
    selected_value: Any,
) -> tuple[str, dict[str, Any] | None]:
    profile_by_id, profile_id_by_label, _ = _dietary_profile_maps(profiles)
    default_profile_id = _default_dietary_profile_id(profiles)

    raw_value = str(selected_value or "").strip()
    normalized_value = normalize_lookup_key(raw_value)

    selected_profile_id = ""
    if raw_value in profile_by_id:
        selected_profile_id = raw_value
    elif normalized_value in profile_by_id:
        selected_profile_id = normalized_value
    elif raw_value in profile_id_by_label:
        selected_profile_id = profile_id_by_label[raw_value]
    else:
        for label, profile_id in profile_id_by_label.items():
            if normalize_lookup_key(label) == normalized_value:
                selected_profile_id = profile_id
                break

    if not selected_profile_id:
        selected_profile_id = default_profile_id

    return selected_profile_id, profile_by_id.get(selected_profile_id)


def _resolve_results_dietary_profile_state(
    profiles: list[dict[str, Any]],
    session_state: dict[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    default_profile_id = _default_dietary_profile_id(profiles)
    global_value = str(session_state.get("global_diet_profile", default_profile_id) or default_profile_id)
    selector_value = session_state.get("results_dietary_profile_selector", global_value)

    selected_profile_id, selected_profile = _resolve_dietary_profile_selection(
        profiles,
        selector_value,
    )
    if not selected_profile_id:
        selected_profile_id = default_profile_id
        selected_profile = _resolve_dietary_profile_selection(profiles, default_profile_id)[1]

    # Keep the Results-tab selector and the global meal/profile state aligned.
    session_state["results_dietary_profile_selector"] = selected_profile_id
    session_state["global_diet_profile"] = selected_profile_id
    return selected_profile_id, selected_profile


@functools.lru_cache(maxsize=1)
def load_dietary_restriction_rules() -> dict[str, dict[str, Any]]:
    if not DIETARY_RESTRICTION_RULES_PATH.exists():
        return {}
    try:
        raw = json.loads(DIETARY_RESTRICTION_RULES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, list):
        return {}

    rules: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        rid = normalize_lookup_key(str(item.get("id", "") or ""))
        if not rid:
            continue
        avoid = item.get("avoid_keywords", [])
        if not isinstance(avoid, list):
            avoid = []
        rules[rid] = {
            "id": rid,
            "avoid_keywords": [normalize_lookup_key(str(x)) for x in avoid if str(x).strip()],
            "notes": str(item.get("notes", "") or "").strip(),
        }

    return rules


def _keyword_matches_food_blob(keyword: str, blob: str, blob_compact: str) -> bool:
    kw = normalize_lookup_key(keyword)
    if not kw:
        return False

    if " " in kw and kw in blob:
        return True

    pattern = rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])"
    if re.search(pattern, blob):
        return True

    compact_kw = kw.replace(" ", "")
    if compact_kw and compact_kw in blob_compact:
        return True

    return False


def _expanded_profile_avoid_keywords(profile: dict[str, Any] | None) -> list[str]:
    if not profile:
        return []

    profile_id = normalize_lookup_key(str(profile.get("id", "") or ""))
    profile_label = normalize_lookup_key(str(profile.get("label", "") or ""))
    profile_keywords = [normalize_lookup_key(str(x)) for x in (profile.get("avoid_keywords", []) or []) if str(x).strip()]

    rule_map = load_dietary_restriction_rules()
    rule_keywords: list[str] = []
    if profile_id and profile_id in rule_map:
        rule_keywords = [
            normalize_lookup_key(str(x))
            for x in (rule_map[profile_id].get("avoid_keywords", []) or [])
            if str(x).strip()
        ]

    avoid_keywords = {x for x in (profile_keywords + rule_keywords) if x}

    marine_animal_tokens = {
        "fish",
        "salmon",
        "sardine",
        "anchovy",
        "tuna",
        "trout",
        "mackerel",
        "cod",
        "herring",
        "shellfish",
        "mollusk",
        "mollusks",
        "shrimp",
        "prawn",
        "crab",
        "lobster",
        "clam",
        "mussel",
        "oyster",
        "scallop",
        "squid",
        "octopus",
        "whelk",
        "roe",
    }

    land_animal_tokens = {
        "beef",
        "veal",
        "pork",
        "ham",
        "bacon",
        "chicken",
        "turkey",
        "lamb",
        "mutton",
        "goat",
        "duck",
        "goose",
        "moose",
        "deer",
        "venison",
        "bison",
        "buffalo",
        "elk",
        "rabbit",
        "caribou",
        "emu",
        "ostrich",
        "boar",
        "pheasant",
        "quail",
        "seal",
        "whale",
        "walrus",
        "sea lion",
        "meat",
        "game meat",
    }

    organ_and_derivative_tokens = {
        "liver",
        "kidney",
        "heart",
        "tripe",
        "gizzard",
        "tongue",
        "sweetbread",
        "organ meat",
        "offal",
        "gelatin",
        "collagen",
    }

    if profile_id == "vegetarian" or profile_label == "vegetarian":
        avoid_keywords.update(land_animal_tokens)
        avoid_keywords.update(marine_animal_tokens)
        avoid_keywords.update(organ_and_derivative_tokens)

    if profile_id == "vegan" or profile_label == "vegan":
        avoid_keywords.update(land_animal_tokens)
        avoid_keywords.update(marine_animal_tokens)
        avoid_keywords.update(organ_and_derivative_tokens)
        avoid_keywords.update(
            {
                "egg",
                "eggs",
                "milk",
                "cream",
                "cheese",
                "yogurt",
                "butter",
                "honey",
                "whey",
                "casein",
            }
        )

    if profile_id == "pescatarian" or profile_label == "pescatarian":
        avoid_keywords.update(land_animal_tokens)
        avoid_keywords.update(organ_and_derivative_tokens)

    if profile_id == "nut free" or profile_label == "nut free":
        avoid_keywords.update(
            {
                "peanut",
                "almond",
                "almond butter",
                "walnut",
                "cashew",
                "hazelnut",
                "filbert",
                "pistachio",
                "pecan",
                "macadamia",
                "brazil nut",
                "brazilnut",
                "pine nut",
                "pinenut",
                "mixed nuts",
                "nut butter",
            }
        )

    if profile_id == "kosher style" or profile_label == "kosher style":
        avoid_keywords.update({"whelk", "mollusk", "mollusks"})

    return sorted(avoid_keywords)


DIETARY_LLM_CONFIDENCE_BLOCK_THRESHOLD = 0.85
DIETARY_LLM_MAX_FOOD_CHECKS = 10
DIETARY_LLM_MAX_MEAL_CHECKS = 12


def _dietary_profile_is_restrictive(profile: dict[str, Any] | None) -> bool:
    if not profile:
        return False
    profile_id = normalize_lookup_key(str(profile.get("id", "") or ""))
    if not profile_id or profile_id in {"none", "no restriction", "no_restriction"}:
        return False
    return True


def _trim_for_llm(text: str, max_chars: int = 480) -> str:
    raw = str(text or "").strip()
    if len(raw) <= max_chars:
        return raw
    return raw[:max_chars].rstrip() + "..."


def _normalize_dietary_llm_decision(decision: Any) -> str:
    token = normalize_lookup_key(str(decision or ""))
    if token in {"allow", "allowed", "compliant", "ok", "pass"}:
        return "allow"
    if token in {"block", "blocked", "noncompliant", "non-compliant", "fail", "avoid"}:
        return "block"
    return "uncertain"


@functools.lru_cache(maxsize=4096)
def _dietary_llm_adjudicate_cached(
    profile_signature: str,
    avoid_keywords_csv: str,
    item_blob: str,
    item_kind: str,
) -> tuple[str, float, str]:
    if not _text_llm_available():
        return "uncertain", 0.0, "Text LLM unavailable"

    safe_kind = normalize_lookup_key(item_kind) or "food"
    system_prompt = (
        "You are a strict dietary compliance checker. "
        "Return JSON only with keys decision, confidence, reason. "
        "decision must be one of: allow, block, uncertain. "
        "Use only the provided dietary profile and ingredient text."
    )
    user_prompt = (
        "Assess whether this item complies with the dietary profile.\n"
        f"Profile: {profile_signature}\n"
        f"Avoid keywords: {avoid_keywords_csv}\n"
        f"Item type: {safe_kind}\n"
        f"Ingredient/item text: {item_blob}\n\n"
        "Return strict JSON object only, format:\n"
        '{"decision":"allow|block|uncertain","confidence":0.0,"reason":"short reason"}'
    )

    raw = call_text_llm(system_prompt, user_prompt, model=BLOCKBRAIN_PINNED_TEXT_MODEL)
    candidate = clean_json_block(raw) or str(raw or "").strip()
    if not candidate:
        return "uncertain", 0.0, "Empty adjudication response"

    try:
        parsed = json.loads(candidate)
    except Exception:
        return "uncertain", 0.0, "Invalid adjudication JSON"

    if not isinstance(parsed, dict):
        return "uncertain", 0.0, "Unexpected adjudication payload"

    decision = _normalize_dietary_llm_decision(parsed.get("decision", ""))
    try:
        confidence = float(parsed.get("confidence", 0.0) or 0.0)
    except Exception:
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    reason = str(parsed.get("reason", "") or "").strip()
    return decision, confidence, reason


def _should_block_by_dietary_llm(
    profile: dict[str, Any] | None,
    avoid_keywords: list[str],
    item_blob: str,
    item_kind: str,
) -> bool:
    if not profile or not avoid_keywords:
        return False
    if not _dietary_profile_is_restrictive(profile):
        return False

    profile_signature = " | ".join(
        [
            str(profile.get("id", "") or "").strip(),
            str(profile.get("label", "") or "").strip(),
            str(profile.get("description", "") or "").strip(),
        ]
    ).strip()
    avoid_csv = ", ".join(avoid_keywords[:80])
    safe_blob = _trim_for_llm(normalize_lookup_key(item_blob), max_chars=480)
    if not safe_blob:
        return False

    decision, confidence, _ = _dietary_llm_adjudicate_cached(
        profile_signature,
        avoid_csv,
        safe_blob,
        item_kind,
    )
    return decision == "block" and confidence >= DIETARY_LLM_CONFIDENCE_BLOCK_THRESHOLD


def apply_food_filters(
    foods: list[dict[str, Any]],
    profile: dict[str, Any] | None,
    use_llm_adjudication: bool = False,
    llm_max_checks: int = DIETARY_LLM_MAX_FOOD_CHECKS,
) -> list[dict[str, Any]]:
    if not foods:
        return []
    if not profile:
        return foods

    avoid_keywords = _expanded_profile_avoid_keywords(profile)

    if not avoid_keywords:
        return foods

    filtered: list[dict[str, Any]] = []
    llm_checks = 0
    for food in foods:
        blob = normalize_lookup_key(str(food.get("food_description", "") or ""))
        blob_compact = blob.replace(" ", "")
        if not blob:
            continue

        persisted_allowed = _persisted_usda_food_allowed(str(food.get("food_description", "") or ""), profile)
        if persisted_allowed is True:
            filtered.append(food)
            continue
        if persisted_allowed is False:
            continue

        blocked = False
        for kw in avoid_keywords:
            if _keyword_matches_food_blob(kw, blob, blob_compact):
                blocked = True
                break
        if blocked:
            continue

        if use_llm_adjudication and llm_checks < max(0, int(llm_max_checks)):
            if _should_block_by_dietary_llm(profile, avoid_keywords, blob, "food"):
                llm_checks += 1
                continue
            llm_checks += 1

        filtered.append(food)
    return filtered


def _recipe_ingredient_text(recipe: dict[str, Any]) -> str:
    parts: list[str] = []
    for ing in recipe.get("ingredients", []) or []:
        parts.append(str(ing.get("name", "") or ""))
    return normalize_lookup_key(" ".join(parts))


def apply_meal_filters(
    meals: list[dict[str, Any]],
    profile: dict[str, Any] | None,
    must_exclude_ingredient: str,
    use_llm_adjudication: bool = False,
    llm_max_checks: int = DIETARY_LLM_MAX_MEAL_CHECKS,
) -> list[dict[str, Any]]:
    if not meals:
        return []

    avoid_keywords = _expanded_profile_avoid_keywords(profile)
    must_exclude_token = normalize_lookup_key(must_exclude_ingredient)

    filtered: list[dict[str, Any]] = []
    llm_checks = 0
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

        if use_llm_adjudication and llm_checks < max(0, int(llm_max_checks)):
            if _should_block_by_dietary_llm(profile, avoid_keywords, ingredient_blob, "meal"):
                llm_checks += 1
                continue
            llm_checks += 1

        filtered.append(meal)

    return filtered


def _evaluate_recipe_coverage(recipe: dict[str, Any], requirements: list[dict[str, Any]]) -> dict[str, Any]:
    covered: list[str] = []
    partial: list[str] = []
    uncovered: list[str] = []
    fulfillment_sum = 0.0
    considered = 0
    for req in requirements:
        component = str(req.get("component", "") or "")
        food_name = str(req.get("food_name", "") or "")
        grams_needed = req.get("grams_needed")
        try:
            needed = float(grams_needed)
        except (ValueError, TypeError) as e:
            logger.debug(f"Invalid grams_needed for consolidated plan: {grams_needed}: {e}")
            needed = 0.0
        if not component or not food_name or needed <= 0:
            continue

        considered += 1

        present_grams = _ingredient_grams_for_food(recipe, food_name)
        ratio = max(0.0, min(1.0, present_grams / needed)) if needed > 0 else 0.0
        fulfillment_sum += ratio

        if ratio >= 1.0:
            covered.append(component)
        elif ratio > 0:
            partial.append(f"{component} ({format_float(ratio * 100, 0)}%)")
        else:
            uncovered.append(component)

    denominator = max(1, considered)
    ratio = fulfillment_sum / denominator
    return {
        "covered_components": covered,
        "partial_components": partial,
        "uncovered_components": uncovered,
        "covered_count": len(covered),
        "coverage_ratio": ratio,
        "full_coverage": len(uncovered) == 0 and len(partial) == 0 and len(covered) > 0,
    }


def _recipe_contains_any_food(recipe: dict[str, Any], food_names: list[str]) -> bool:
    for food_name in food_names:
        if _ingredient_grams_for_food(recipe, food_name) > 0:
            return True
    return False


def _recipe_total_grams(recipe: dict[str, Any]) -> float:
    total = 0.0
    for ing in recipe.get("ingredients", []) or []:
        try:
            grams = float(ing.get("grams", 0) or 0)
        except Exception:
            grams = 0.0
        if grams > 0:
            total += grams
    return total


@functools.lru_cache(maxsize=1)
def load_whole_food_prices() -> list[dict[str, str]]:
    # AI-only runtime: local whole-food price table is intentionally disabled.
    return []


def _estimate_recipe_cost(recipe: dict[str, Any], country: str, currency: str) -> float | None:
    rows = load_whole_food_prices()
    if not rows:
        return None

    wanted_currency = str(currency or "USD").strip().upper()
    wanted_country = normalize_lookup_key(str(country or "").strip())
    country_rows: list[dict[str, str]] = []
    global_rows: list[dict[str, str]] = []

    for row in rows:
        if str(row.get("currency", "") or "").strip().upper() != wanted_currency:
            continue
        row_country = normalize_lookup_key(str(row.get("country", "") or ""))
        if wanted_country and row_country == wanted_country:
            country_rows.append(row)
        elif row_country in {"", "global", "world", "worldwide"}:
            global_rows.append(row)

    lookup_rows = country_rows if country_rows else global_rows
    if not lookup_rows:
        return None

    total_cost = 0.0
    matched_any = False
    for ing in recipe.get("ingredients", []) or []:
        ing_name = normalize_lookup_key(str(ing.get("name", "") or ""))
        if not ing_name:
            continue
        try:
            ing_grams = float(ing.get("grams", 0) or 0)
        except Exception:
            ing_grams = 0.0
        if ing_grams <= 0:
            continue

        best_price_per_kg: float | None = None
        for row in lookup_rows:
            keyword = normalize_lookup_key(str(row.get("food_keyword", "") or ""))
            if not keyword:
                continue
            if keyword not in ing_name and ing_name not in keyword:
                continue
            try:
                ppk = float(row.get("price_per_kg", "") or 0)
            except Exception:
                ppk = 0.0
            if ppk <= 0:
                continue
            if best_price_per_kg is None or ppk < best_price_per_kg:
                best_price_per_kg = ppk

        if best_price_per_kg is None:
            continue

        matched_any = True
        total_cost += (ing_grams / 1000.0) * best_price_per_kg

    if not matched_any:
        return None
    return total_cost


def generate_llm_meal_suggestions(requirements: list[dict[str, Any]], max_results: int = 3) -> list[dict[str, Any]]:
    if not requirements:
        return []
    if not _text_llm_available():
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
        "Return strict JSON only. Keep meals practical and ingredient-focused. "
        "Use ONLY common single-ingredient whole foods available in a normal "
        "supermarket (vegetables, fruit, eggs, common meat/fish, dairy, grains, "
        "legumes, common nuts/seeds). Never use exotic or game ingredients such as "
        "polar bear liver, whale, seal, or insects."
    )
    user_prompt = (
        "Return a strict JSON array with up to "
        f"{max_results} meal objects. Each object keys: "
        "name (string), meal_type (string), ingredients (array of {name, grams}), steps (string). "
        "Each meal should try to cover as many targets as possible; exceeding targets is allowed.\n\n"
        "Targets:\n"
        + "\n".join(target_lines)
    )

    raw = call_text_llm(system_prompt, user_prompt)
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

    # Patch: drop meals that contain any exotic / non-supermarket ingredient.
    common_meals: list[dict[str, Any]] = []
    for _m in meals:
        if all(
            classify_food_commonness(str(_ing.get("name", "") or ""))["tier"] >= 0
            for _ing in (_m.get("ingredients", []) or [])
        ):
            common_meals.append(_m)
    meals = common_meals if common_meals else meals
    meals.sort(
        key=lambda r: (
            1 if r.get("full_coverage") else 0,
            float(r.get("coverage_ratio", 0.0)),
            int(r.get("covered_count", 0)),
        ),
        reverse=True,
    )
    return meals[:max_results]


def _recipe_contains_all_anchor_foods(recipe: dict[str, Any], requirements: list[dict[str, Any]]) -> bool:
    if not requirements:
        return False
    for req in requirements:
        food_name = str(req.get("food_name", "") or "").strip()
        grams_needed = req.get("grams_needed")
        if not food_name or grams_needed is None:
            continue
        if _ingredient_grams_for_food(recipe, food_name) <= 0:
            return False
    return True


# -- Shared meal-scaling primitives --------------------------------------
def _scale_ingredients(
    ingredients: list[dict[str, Any]], multiplier: float
) -> list[dict[str, Any]]:
    """Scale ingredient grams by multiplier, skipping zero/missing rows."""
    out: list[dict[str, Any]] = []
    for ing in ingredients:
        name = str(ing.get("name", "") or "").strip()
        try:
            grams = float(ing.get("grams", 0) or 0)
        except Exception:
            grams = 0.0
        if name and grams > 0:
            out.append({"name": name, "grams": round(grams * multiplier, 1)})
    return out


def _required_multiplier(
    recipe: dict[str, Any],
    requirements: list[dict[str, Any]],
    headroom: float = 1.02,
) -> float | None:
    """Minimum uniform scale so every requirement is met. None if any food absent."""
    m = 1.0
    for req in requirements:
        food_name = str(req.get("food_name", "") or "").strip()
        try:
            needed = float(req.get("grams_needed") or 0)
        except Exception:
            continue
        if not food_name or needed <= 0:
            continue
        present = _ingredient_grams_for_food(recipe, food_name)
        if present <= 0:
            return None
        m = max(m, needed / present)
    return m * headroom
# -------------------------------------------------------------------------

def scale_recipe_to_requirements(
    recipe: dict[str, Any],
    requirements: list[dict[str, Any]],
    strategy_label: str,
) -> dict[str, Any] | None:
    if not requirements or not _recipe_contains_all_anchor_foods(recipe, requirements):
        return None
    multiplier = _required_multiplier(recipe, requirements)
    if multiplier is None:
        return None
    scaled_ingredients = _scale_ingredients(recipe.get("ingredients", []) or [], multiplier)
    if not scaled_ingredients:
        return None
    scaled = {
        "name": f"{str(recipe.get('name', 'Local recipe') or 'Local recipe')} (scaled)",
        "meal_type": str(recipe.get("meal_type", "meal") or "meal"),
        "ingredients": scaled_ingredients,
        "steps": (
            f"Use this recipe at approximately {format_float(multiplier, 2)}x portions "
            "to match selected nutrient targets. "
            + str(recipe.get("steps", "") or "")
        ).strip(),
        "source_type": "local_recipe_db_scaled",
        "source": "Local Recipe DB (scaled)",
        "strategy_label": strategy_label,
        "recipe_multiplier": float(multiplier),
        "scaled_from_name": str(recipe.get("name", "") or ""),
    }
    scaled.update(_evaluate_recipe_coverage(scaled, requirements))
    return scaled


def _selected_recipe_overlap_metrics(
    recipe: dict[str, Any],
    requirements: list[dict[str, Any]],
) -> dict[str, float]:
    if not requirements:
        return {
            "overlap_count": 0.0,
            "overlap_ratio": 0.0,
            "concentration_score": 0.0,
            "present_grams_total": 0.0,
        }

    recipe_total = max(1.0, _recipe_total_grams(recipe))
    overlap_count = 0
    concentration_score = 0.0
    present_grams_total = 0.0

    for req in requirements:
        food_name = str(req.get("food_name", "") or "").strip()
        if not food_name:
            continue
        present = _ingredient_grams_for_food(recipe, food_name)
        if present <= 0:
            continue
        overlap_count += 1
        present_grams_total += float(present)
        concentration_score += float(present) / recipe_total

    req_count = max(1, len(requirements))
    return {
        "overlap_count": float(overlap_count),
        "overlap_ratio": float(overlap_count) / float(req_count),
        "concentration_score": concentration_score,
        "present_grams_total": present_grams_total,
    }


def build_selected_whole_food_meal(
    recipe: dict[str, Any],
    requirements: list[dict[str, Any]],
    strategy_label: str,
) -> dict[str, Any] | None:
    if not requirements:
        return None

    working_ingredients: list[dict[str, Any]] = []
    for ing in recipe.get("ingredients", []) or []:
        ing_name = str(ing.get("name", "") or "").strip()
        try:
            ing_grams = float(ing.get("grams", 0) or 0)
        except Exception:
            ing_grams = 0.0
        if ing_name and ing_grams > 0:
            working_ingredients.append({"name": ing_name, "grams": ing_grams})

    if not working_ingredients:
        return None

    working_recipe = {
        "name": str(recipe.get("name", "Local recipe") or "Local recipe"),
        "meal_type": str(recipe.get("meal_type", "meal") or "meal"),
        "ingredients": working_ingredients,
        "steps": str(recipe.get("steps", "") or "").strip(),
    }

    # Ensure selected dropdown foods are represented, then scale to meet/exceed all selected doses.
    for req in requirements:
        food_name = str(req.get("food_name", "") or "").strip()
        grams_needed = req.get("grams_needed")
        if not food_name or grams_needed is None:
            continue
        try:
            needed = float(grams_needed)
        except Exception:
            continue
        if needed <= 0:
            continue
        present = _ingredient_grams_for_food(working_recipe, food_name)
        if present <= 0:
            working_recipe["ingredients"].append({"name": food_name, "grams": needed})

    multiplier = 1.0
    for req in requirements:
        food_name = str(req.get("food_name", "") or "").strip()
        grams_needed = req.get("grams_needed")
        if not food_name or grams_needed is None:
            continue
        try:
            needed = float(grams_needed)
        except Exception:
            continue
        if needed <= 0:
            continue
        present = _ingredient_grams_for_food(working_recipe, food_name)
        if present <= 0:
            return None
        ratio = needed / present
        if ratio > multiplier:
            multiplier = ratio

    multiplier *= 1.02

    scaled_ingredients: list[dict[str, Any]] = []
    for ing in working_recipe.get("ingredients", []) or []:
        ing_name = str(ing.get("name", "") or "").strip()
        try:
            ing_grams = float(ing.get("grams", 0) or 0)
        except Exception:
            ing_grams = 0.0
        if not ing_name or ing_grams <= 0:
            continue
        scaled_ingredients.append({"name": ing_name, "grams": round(ing_grams * multiplier, 1)})

    if not scaled_ingredients:
        return None

    selected_metrics = _selected_recipe_overlap_metrics(working_recipe, requirements)
    scaled = {
        "name": f"{str(recipe.get('name', 'Local recipe') or 'Local recipe')} (selected-food optimized)",
        "meal_type": str(recipe.get("meal_type", "meal") or "meal"),
        "ingredients": scaled_ingredients,
        "steps": (
            f"Optimized around your selected whole-food choices and scaled to about {format_float(multiplier, 2)}x portions so all selected nutrient targets are matched or exceeded. "
            + str(recipe.get("steps", "") or "")
        ).strip(),
        "source_type": "local_recipe_db_selected_scaled",
        "source": "Local Recipe DB (selected-food optimized)",
        "strategy_label": strategy_label,
        "recipe_multiplier": float(multiplier),
        "scaled_from_name": str(recipe.get("name", "") or ""),
        "selected_overlap_count": int(selected_metrics.get("overlap_count", 0.0) or 0.0),
        "selected_overlap_ratio": float(selected_metrics.get("overlap_ratio", 0.0) or 0.0),
        "selected_concentration_score": float(selected_metrics.get("concentration_score", 0.0) or 0.0),
    }
    scaled.update(_evaluate_recipe_coverage(scaled, requirements))
    return scaled


def choose_selected_whole_food_recipe(
    local_meals: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
    strategy_label: str,
) -> dict[str, Any] | None:
    if not local_meals or not requirements:
        return None

    ranked_pairs: list[tuple[dict[str, Any], dict[str, float]]] = []
    for meal in local_meals:
        ranked_pairs.append((meal, _selected_recipe_overlap_metrics(meal, requirements)))

    ranked_pairs.sort(
        key=lambda pair: (
            int(pair[1].get("overlap_count", 0.0) or 0.0),
            float(pair[1].get("concentration_score", 0.0) or 0.0),
            float(pair[1].get("present_grams_total", 0.0) or 0.0),
            float(pair[0].get("coverage_ratio", 0.0) or 0.0),
        ),
        reverse=True,
    )

    for candidate, _ in ranked_pairs:
        built = build_selected_whole_food_meal(candidate, requirements, strategy_label)
        if built and built.get("full_coverage"):
            return built
    return None


# ---------------------------------------------------------------------------
# Macro-optimised meal helpers
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _load_macro_table() -> dict[str, dict[str, float]]:
    """Load protein / fat / carbs per 100 g for all USDA foods.
    Returns {normalized_food_key: {protein_g, fat_g, carbs_g, kcal_per_100g}}.
    Result is process-cached after the first call.
    """
    conn = try_open_usda_db()
    if conn is None:
        return {}
    try:
        rows = conn.execute(
            """
            SELECT food_description, nutrient_id, amount_per_100g
            FROM nutrient_rankings
            WHERE nutrient_id IN (?, ?, ?)
              AND amount_per_100g IS NOT NULL
              AND amount_per_100g > 0
            """,
            (_MACRO_PROTEIN_NID, _MACRO_FAT_NID, _MACRO_CARBS_NID),
        ).fetchall()
    except Exception:
        return {}
    finally:
        conn.close()

    table: dict[str, dict[str, float]] = {}
    for row in rows:
        food_key = normalize_lookup_key(str(row[0] or ""))
        nid = int(row[1])
        val = float(row[2] or 0.0)
        if not food_key:
            continue
        entry = table.setdefault(food_key, {"protein_g": 0.0, "fat_g": 0.0, "carbs_g": 0.0})
        if nid == _MACRO_PROTEIN_NID:
            entry["protein_g"] = max(entry["protein_g"], val)
        elif nid == _MACRO_FAT_NID:
            entry["fat_g"] = max(entry["fat_g"], val)
        elif nid == _MACRO_CARBS_NID:
            entry["carbs_g"] = max(entry["carbs_g"], val)

    for entry in table.values():
        entry["kcal_per_100g"] = entry["protein_g"] * 4.0 + entry["carbs_g"] * 4.0 + entry["fat_g"] * 9.0

    return table


def get_ingredient_macros_per_100g(food_name: str) -> dict[str, float]:
    """Return {protein_g, fat_g, carbs_g, kcal_per_100g} per 100 g via USDA fuzzy match."""
    _empty: dict[str, float] = {"protein_g": 0.0, "fat_g": 0.0, "carbs_g": 0.0, "kcal_per_100g": 0.0}
    table = _load_macro_table()
    if not table:
        return _empty

    def _tokens(value: str) -> set[str]:
        toks: set[str] = set()
        for t in normalize_lookup_key(value).split():
            if t in {"raw", "fresh", "cooked", "boiled", "steamed", "dried", "peeled",
                     "without", "with", "skin", "and", "the", "or", "by"}:
                continue
            base = t[:-1] if len(t) > 3 and t.endswith("s") else t
            if len(base) >= 3:
                toks.add(base)
        return toks

    target = normalize_lookup_key(food_name)
    target_tokens = _tokens(target)

    best_entry: dict[str, float] | None = None
    best_score: tuple[int, int, int] = (0, 0, 0)

    for food_key, entry in table.items():
        direct = int(target in food_key or food_key in target)
        food_tokens = _tokens(food_key)
        overlap = len(target_tokens & food_tokens) if target_tokens else 0
        score: tuple[int, int, int] = (direct + min(overlap, 5), overlap, -len(food_key))
        if score > best_score:
            best_score = score
            best_entry = entry

    if best_entry and best_score[0] > 0:
        return dict(best_entry)
    return _empty


def _recipe_macro_totals(recipe: dict[str, Any]) -> dict[str, float]:
    """Sum protein_g, fat_g, carbs_g, kcal across all scaled ingredients."""
    total_protein = 0.0
    total_fat = 0.0
    total_carbs = 0.0
    for ing in recipe.get("ingredients", []) or []:
        ing_name = str(ing.get("name", "") or "").strip()
        try:
            ing_grams = float(ing.get("grams", 0) or 0)
        except Exception:
            ing_grams = 0.0
        if not ing_name or ing_grams <= 0:
            continue
        macros = get_ingredient_macros_per_100g(ing_name)
        factor = ing_grams / 100.0
        total_protein += macros["protein_g"] * factor
        total_fat += macros["fat_g"] * factor
        total_carbs += macros["carbs_g"] * factor
    kcal = total_protein * 4.0 + total_carbs * 4.0 + total_fat * 9.0
    return {"protein_g": total_protein, "fat_g": total_fat, "carbs_g": total_carbs, "kcal": kcal}


def _macro_profile_score(
    recipe: dict[str, Any],
    pct_protein: float,
    pct_carbs: float,
    pct_fat: float,
) -> float:
    """Return 0–1 closeness score; higher = recipe macro ratio is closer to the target split."""
    totals = _recipe_macro_totals(recipe)
    kcal = totals["kcal"]
    if kcal <= 0:
        return 0.0
    ap = (totals["protein_g"] * 4.0 / kcal) * 100.0
    ac = (totals["carbs_g"] * 4.0 / kcal) * 100.0
    af = (totals["fat_g"] * 9.0 / kcal) * 100.0
    dist = ((ap - pct_protein) ** 2 + (ac - pct_carbs) ** 2 + (af - pct_fat) ** 2) ** 0.5
    # Max possible Euclidean distance in 3-way % space ≈ 141.4; clamp to 0–1.
    return max(0.0, 1.0 - dist / 141.4)


def _macro_constraint_diagnostics(
    requirements: list[dict[str, Any]],
    target_kcal: float,
    pct_protein: float,
    pct_carbs: float,
    pct_fat: float,
) -> str | None:
    """Build a concrete explanation when macro+calorie limits conflict with micronutrient minimums.

    Uses requirement-level food anchors as a lower-bound estimate for what must be present in the meal.
    """
    if not requirements or target_kcal <= 0:
        return None

    # Macro caps implied by calorie + split.
    max_protein_g = (target_kcal * (pct_protein / 100.0)) / 4.0
    max_carbs_g = (target_kcal * (pct_carbs / 100.0)) / 4.0
    max_fat_g = (target_kcal * (pct_fat / 100.0)) / 9.0

    def _vitamin_d_to_iu(dose_value: float, dose_unit: str) -> float | None:
        unit = str(dose_unit or "").strip().lower()
        if dose_value <= 0:
            return None
        if unit in {"iu", "ui", "ie"}:
            return float(dose_value)
        if unit in {"mcg", "ug", "μg", "µg"}:
            return float(dose_value) * 40.0
        if unit in {"mg"}:
            return float(dose_value) * 40000.0
        if unit in {"g"}:
            return float(dose_value) * 40000000.0
        return None

    # Aggregate minimum required grams by food anchor (max across duplicated food keys).
    required_by_food: dict[str, tuple[str, float]] = {}
    component_breakdown: list[dict[str, Any]] = []
    vitamin_d_iu_target: float | None = None
    for req in requirements:
        component = str(req.get("component", "") or "").strip()
        food_name = str(req.get("food_name", "") or "").strip()
        grams_needed = req.get("grams_needed")
        if not component or not food_name or grams_needed is None:
            continue

        if "vitamin d" in normalize_lookup_key(component):
            dose_val = req.get("dose_value")
            dose_unit = str(req.get("dose_unit", "") or "")
            try:
                dose_num = float(dose_val)
            except Exception:
                dose_num = 0.0
            maybe_iu = _vitamin_d_to_iu(dose_num, dose_unit)
            if maybe_iu and maybe_iu > 0:
                if vitamin_d_iu_target is None or maybe_iu > vitamin_d_iu_target:
                    vitamin_d_iu_target = float(maybe_iu)
        try:
            needed = float(grams_needed)
        except Exception:
            continue
        if needed <= 0:
            continue

        key = normalize_lookup_key(food_name)
        prev = required_by_food.get(key)
        if prev is None or needed > prev[1]:
            required_by_food[key] = (food_name, needed)

        macros = get_ingredient_macros_per_100g(food_name)
        factor = needed / 100.0
        p_g = float(macros.get("protein_g", 0.0) or 0.0) * factor
        c_g = float(macros.get("carbs_g", 0.0) or 0.0) * factor
        f_g = float(macros.get("fat_g", 0.0) or 0.0) * factor
        kcal = p_g * 4.0 + c_g * 4.0 + f_g * 9.0
        component_breakdown.append(
            {
                "component": component,
                "food_name": food_name,
                "grams_needed": needed,
                "kcal": kcal,
                "protein_g": p_g,
                "carbs_g": c_g,
                "fat_g": f_g,
            }
        )

    if not required_by_food:
        return None

    # Lower-bound macro load from food anchors required for micronutrient matching.
    min_p = 0.0
    min_c = 0.0
    min_f = 0.0
    for food_name, needed in required_by_food.values():
        macros = get_ingredient_macros_per_100g(food_name)
        factor = needed / 100.0
        min_p += float(macros.get("protein_g", 0.0) or 0.0) * factor
        min_c += float(macros.get("carbs_g", 0.0) or 0.0) * factor
        min_f += float(macros.get("fat_g", 0.0) or 0.0) * factor
    min_kcal = min_p * 4.0 + min_c * 4.0 + min_f * 9.0

    violations: list[str] = []
    if min_kcal > target_kcal + 1e-6:
        violations.append(
            f"minimum required micronutrient foods already need about {format_float(min_kcal, 0)} kcal, above your {format_float(target_kcal, 0)} kcal cap"
        )
    if min_p > max_protein_g + 1e-6:
        violations.append(
            f"minimum required protein load is {format_float(min_p, 1)} g, above the macro cap {format_float(max_protein_g, 1)} g"
        )
    if min_c > max_carbs_g + 1e-6:
        violations.append(
            f"minimum required carbs load is {format_float(min_c, 1)} g, above the macro cap {format_float(max_carbs_g, 1)} g"
        )
    if min_f > max_fat_g + 1e-6:
        violations.append(
            f"minimum required fat load is {format_float(min_f, 1)} g, above the macro cap {format_float(max_fat_g, 1)} g"
        )

    if not violations:
        return None

    # Highlight strongest contributing component for transparency.
    lead = None
    if component_breakdown:
        lead = max(component_breakdown, key=lambda x: float(x.get("kcal", 0.0) or 0.0))

    if lead:
        lead_txt = (
            f" Largest driver: {lead['component']} via {lead['food_name']} "
            f"(~{format_float(lead['grams_needed'], 0)} g; ~{format_float(lead['kcal'], 0)} kcal lower-bound estimate)."
        )
    else:
        lead_txt = ""

    sunlight_txt = ""
    if vitamin_d_iu_target and vitamin_d_iu_target > 0:
        # Very rough conversion range under favorable UV conditions.
        # Assumes approximately 1,000-4,000 IU per hour equivalent effective exposure.
        sun_hours_high = vitamin_d_iu_target / 4000.0
        sun_hours_low = vitamin_d_iu_target / 1000.0
        sunlight_txt = (
            f" Rough Vitamin D sunlight-equivalent for {format_float(vitamin_d_iu_target, 0)} IU: "
            f"about {format_float(sun_hours_high, 2)}-{format_float(sun_hours_low, 2)} hours of effective strong-UV exposure. "
            "Actual synthesis varies widely by latitude/season/time/skin tone/clothing/sunscreen, so this is only a directional estimate."
        )

    return "Constraint conflict: " + "; ".join(violations) + "." + lead_txt + (" " + sunlight_txt if sunlight_txt else "")


def build_macro_optimized_meals(
    local_meals: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
    strategy_label: str,
    target_kcal: float,
    pct_protein: float,
    pct_carbs: float,
    pct_fat: float,
    max_results: int = 50,
) -> tuple[list[dict[str, Any]], str | None]:
    """Return a macro-optimised meal only when ALL hard constraints are feasible:
    1) micronutrient requirements matched/exceeded,
    2) calories do not exceed target,
    3) macro split stays near requested percentages.
    If infeasible, returns (None, reason).
    """
    if not local_meals:
        return [], "No local meal candidates available for macronutrient optimization."

    ranked = sorted(
        local_meals,
        key=lambda m: (
            _macro_profile_score(m, pct_protein, pct_carbs, pct_fat),
            float(m.get("coverage_ratio", 0.0) or 0.0),
        ),
        reverse=True,
    )

    # Slightly wider tolerance avoids false negatives from noisy ingredient matching and rounding.
    macro_tol_pct = 25.0
    infeasible_calorie_micro = 0
    infeasible_macro_split = 0
    no_macro_data = 0
    feasible: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    for base_recipe in ranked:
        working_ings = list(base_recipe.get("ingredients", []) or [])

        # Ensure all required anchor foods exist; missing ones are added at the exact required grams.
        for req in requirements:
            food_name = str(req.get("food_name", "") or "").strip()
            grams_needed = req.get("grams_needed")
            if not food_name or grams_needed is None:
                continue
            try:
                needed = float(grams_needed)
            except Exception:
                continue
            if needed <= 0:
                continue
            present = _ingredient_grams_for_food({"ingredients": working_ings}, food_name)
            if present <= 0:
                working_ings.append({"name": food_name, "grams": needed})

        working_recipe = {
            "ingredients": working_ings,
            "name": str(base_recipe.get("name", "") or ""),
            "meal_type": str(base_recipe.get("meal_type", "meal") or "meal"),
            "steps": str(base_recipe.get("steps", "") or ""),
        }

        totals = _recipe_macro_totals(working_recipe)
        base_kcal = float(totals.get("kcal", 0.0) or 0.0)
        if base_kcal <= 0:
            no_macro_data += 1
            continue

        # Macro partition of a uniformly scaled recipe is constant; reject if too far from target.
        base_p = (totals["protein_g"] * 4.0 / base_kcal) * 100.0
        base_c = (totals["carbs_g"] * 4.0 / base_kcal) * 100.0
        base_f = (totals["fat_g"] * 9.0 / base_kcal) * 100.0
        if (
            abs(base_p - pct_protein) > macro_tol_pct
            or abs(base_c - pct_carbs) > macro_tol_pct
            or abs(base_f - pct_fat) > macro_tol_pct
        ):
            infeasible_macro_split += 1
            continue

        # Determine feasible multiplier interval.
        micro_min_multiplier = 0.0
        for req in requirements:
            food_name = str(req.get("food_name", "") or "").strip()
            grams_needed = req.get("grams_needed")
            if not food_name or grams_needed is None:
                continue
            try:
                needed = float(grams_needed)
            except Exception:
                continue
            if needed <= 0:
                continue
            present = _ingredient_grams_for_food(working_recipe, food_name)
            if present <= 0:
                micro_min_multiplier = MAX_GRAMS_INFINITY_PLACEHOLDER
                break
            ratio = needed / present
            if ratio > micro_min_multiplier:
                micro_min_multiplier = ratio

        if micro_min_multiplier == MAX_GRAMS_INFINITY_PLACEHOLDER:
            infeasible_calorie_micro += 1
            continue

        calorie_max_multiplier = target_kcal / base_kcal if target_kcal > 0 else 0.0
        if calorie_max_multiplier <= 0 or micro_min_multiplier > calorie_max_multiplier:
            infeasible_calorie_micro += 1
            continue

        # Use as much of the calorie budget as feasible while respecting micronutrient minimum.
        multiplier = max(micro_min_multiplier, calorie_max_multiplier)

        scaled_ingredients: list[dict[str, Any]] = []
        for ing in working_ings:
            ing_name = str(ing.get("name", "") or "").strip()
            try:
                ing_grams = float(ing.get("grams", 0) or 0)
            except Exception:
                ing_grams = 0.0
            if not ing_name or ing_grams <= 0:
                continue
            scaled_ingredients.append({"name": ing_name, "grams": round(ing_grams * multiplier, 1)})

        if not scaled_ingredients:
            continue

        final_totals = _recipe_macro_totals({"ingredients": scaled_ingredients})
        final_kcal = final_totals["kcal"]
        if final_kcal <= 0 or final_kcal > target_kcal + 1e-6:
            infeasible_calorie_micro += 1
            continue

        if final_kcal > 0:
            prot_pct = round((final_totals["protein_g"] * 4.0 / final_kcal) * 100.0)
            carb_pct = round((final_totals["carbs_g"] * 4.0 / final_kcal) * 100.0)
            fat_pct = round((final_totals["fat_g"] * 9.0 / final_kcal) * 100.0)
            macro_summary = (
                f"{format_float(final_kcal, 0)} kcal  •  "
                f"Protein {format_float(final_totals['protein_g'], 1)} g ({prot_pct}%)  •  "
                f"Carbs {format_float(final_totals['carbs_g'], 1)} g ({carb_pct}%)  •  "
                f"Fat {format_float(final_totals['fat_g'], 1)} g ({fat_pct}%)"
            )
        else:
            macro_summary = "Macro totals unavailable (USDA macro data absent for these ingredients)"

        scaled: dict[str, Any] = {
            "name": f"{str(base_recipe.get('name', 'Local recipe') or 'Local recipe')} (macro-optimized)",
            "meal_type": str(base_recipe.get("meal_type", "meal") or "meal"),
            "ingredients": scaled_ingredients,
            "steps": (
                f"Serving scaled to ~{format_float(final_kcal, 0)} kcal "
                f"(target: {int(pct_protein)}% protein / {int(pct_carbs)}% carbs / {int(pct_fat)}% fat). "
                f"Supplement micronutrient targets are matched or exceeded. "
                + str(base_recipe.get("steps", "") or "")
            ).strip(),
            "source_type": "local_recipe_db_macro_scaled",
            "source": "Local Recipe DB (macro-optimized)",
            "strategy_label": strategy_label,
            "recipe_multiplier": float(multiplier),
            "scaled_from_name": str(base_recipe.get("name", "") or ""),
            "macro_summary": macro_summary,
            "macro_target_kcal": float(target_kcal),
            "macro_pct_protein": float(pct_protein),
            "macro_pct_carbs": float(pct_carbs),
            "macro_pct_fat": float(pct_fat),
        }
        scaled.update(_evaluate_recipe_coverage(scaled, requirements))
        if scaled.get("full_coverage"):
            key = normalize_lookup_key(str(scaled.get("name", "") or ""))
            if key and key not in seen_names:
                seen_names.add(key)
                feasible.append(scaled)
            if len(feasible) >= max(1, int(max_results)):
                break

    if feasible:
        return feasible[: max(1, int(max_results))], None

    if infeasible_calorie_micro > 0:
        diag = _macro_constraint_diagnostics(requirements, target_kcal, pct_protein, pct_carbs, pct_fat)
        if diag:
            return (
                [],
                "No adequate macronutrient-optimized meal can satisfy all constraints. "
                + diag
                + " Consider adding a targeted supplement for the limiting component while keeping the meal within your macro and calorie plan.",
            )
        return (
            [],
            "No adequate macronutrient-optimized meal can satisfy micronutrient matching within the calorie cap. "
            "Consider adding a targeted supplement for the limiting component while keeping this meal within your macro and calorie plan.",
        )
    if infeasible_macro_split > 0:
        return (
            [],
            "No adequate macronutrient-optimized meal can satisfy the requested macro split with the available local recipes while also covering the supplement-equivalent micronutrients.",
        )
    if no_macro_data > 0:
        return (
            [],
            "Macronutrient optimization is unavailable because macro composition data is missing for candidate ingredients.",
        )
    return [], "No adequate macronutrient-optimized meal could be generated under the current constraints."


def build_strategy_template_meal(
    requirements: list[dict[str, Any]],
    strategy_label: str,
    meal_name: str,
) -> dict[str, Any] | None:
    if not requirements:
        return None

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
        per_food_required[key] = float(per_food_required.get(key, 0.0)) + grams
        canonical_name[key] = food_name

    if not per_food_required:
        return None

    ingredients: list[dict[str, Any]] = []
    for key in sorted(per_food_required.keys(), key=lambda k: per_food_required[k], reverse=True):
        required = float(per_food_required[key])
        # Slightly exceed requirements to satisfy "match or exceed" policy.
        ingredients.append({"name": canonical_name[key], "grams": round(required * 1.03, 1)})

    meal = {
        "name": meal_name,
        "meal_type": "meal",
        "ingredients": ingredients,
        "steps": (
            "Prepare and combine the listed whole foods in portions shown. "
            "This strategy template is generated to meet or slightly exceed the selected target amounts."
        ),
        "source_type": "template_generated_recipe",
        "source": "SuppSwap strategy template",
        "strategy_label": strategy_label,
    }
    meal.update(_evaluate_recipe_coverage(meal, requirements))
    return meal


def resolve_selected_meal_requirements(component_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def _name_matches(target_food: str, candidate_food: str) -> bool:
        target = normalize_lookup_key(target_food)
        candidate = normalize_lookup_key(candidate_food)
        if not target or not candidate:
            return False
        if target == candidate or target in candidate or candidate in target:
            return True

        def _tokens(value: str) -> set[str]:
            toks: set[str] = set()
            for tok in value.split():
                if not tok:
                    continue
                base = tok[:-1] if len(tok) > 3 and tok.endswith("s") else tok
                if len(base) >= 3:
                    toks.add(base)
            return toks

        t1 = _tokens(target)
        t2 = _tokens(candidate)
        overlap = len(t1 & t2)
        return overlap >= 2 or (overlap >= 1 and len(t1) <= 2)

    def _grams_needed_for_component_food(cand: dict[str, Any], food_name: str) -> float | None:
        dose_value = cand.get("dose_value")
        dose_unit = str(cand.get("dose_unit", "") or "")
        foods = cand.get("foods", []) or []
        best: float | None = None
        for food in foods:
            db_food = str(food.get("food_description", "") or "").strip()
            if not db_food or not _name_matches(food_name, db_food):
                continue
            try:
                amount_per_100g = float(food.get("amount_per_100g", 0.0) or 0.0)
            except Exception:
                amount_per_100g = 0.0
            nutrient_unit = str(food.get("unit", "") or "")
            grams_needed = grams_needed_to_match_dose(
                dose_value,
                dose_unit,
                amount_per_100g,
                nutrient_unit,
                component_name=str(cand.get("component", "") or ""),
            )
            if grams_needed is None or grams_needed <= 0:
                continue
            if best is None or float(grams_needed) < float(best):
                best = float(grams_needed)
        return best

    # Build the selected-food pool from the Results tab user choices.
    selected_food_serving: dict[str, float] = {}
    selected_food_label: dict[str, str] = {}
    for cand in component_candidates:
        selected_food_name = str(cand.get("selected_food_name", "") or "").strip()
        selected_grams_needed = cand.get("selected_grams_needed")
        if not selected_food_name or selected_grams_needed is None:
            continue
        try:
            grams = float(selected_grams_needed)
        except Exception:
            continue
        if grams <= 0:
            continue
        key = normalize_lookup_key(selected_food_name)
        selected_food_serving[key] = max(float(selected_food_serving.get(key, 0.0)), grams)
        selected_food_label[key] = selected_food_name

    requirements: list[dict[str, Any]] = []
    for cand in component_candidates:
        component = str(cand.get("component", "") or "")
        selected_food_name = str(cand.get("selected_food_name", "") or "").strip()
        selected_grams_needed = cand.get("selected_grams_needed")
        if not component or not selected_food_name or selected_grams_needed is None:
            continue
        try:
            grams_needed = float(selected_grams_needed)
        except Exception:
            continue
        if grams_needed <= 0:
            continue
        chosen_food_name = selected_food_name
        chosen_grams_needed = grams_needed

        # Cross-micronutrient logic: if an already-selected food serving already covers this
        # component, use that food instead of forcing another ingredient.
        covering_options: list[tuple[float, str]] = []
        for pool_key, pool_serving in selected_food_serving.items():
            pool_food_name = selected_food_label.get(pool_key, pool_key)
            needed_for_pool = _grams_needed_for_component_food(cand, pool_food_name)
            if needed_for_pool is None or needed_for_pool <= 0:
                continue
            if pool_serving + 1e-9 >= needed_for_pool:
                covering_options.append((float(needed_for_pool), pool_food_name))

        if covering_options:
            covering_options.sort(key=lambda x: x[0])
            chosen_grams_needed, chosen_food_name = covering_options[0]
        else:
            # Otherwise pick the best (lowest-grams) food from the selected-food pool if available.
            best_pool: tuple[float, str] | None = None
            for pool_key in selected_food_serving.keys():
                pool_food_name = selected_food_label.get(pool_key, pool_key)
                needed_for_pool = _grams_needed_for_component_food(cand, pool_food_name)
                if needed_for_pool is None or needed_for_pool <= 0:
                    continue
                if best_pool is None or float(needed_for_pool) < float(best_pool[0]):
                    best_pool = (float(needed_for_pool), pool_food_name)
            if best_pool is not None:
                chosen_grams_needed, chosen_food_name = best_pool

        requirements.append(
            {
                "component": component,
                "food_name": chosen_food_name,
                "grams_needed": float(chosen_grams_needed),
            }
        )
    return requirements


def resolve_low_grams_meal_requirements(component_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []
    for cand in component_candidates:
        component = str(cand.get("component", "") or "")
        dose_value = cand.get("dose_value")
        dose_unit = str(cand.get("dose_unit", "") or "")
        foods = cand.get("foods", []) or []
        if not component:
            continue

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
            grams_needed = grams_needed_to_match_dose(
                dose_value,
                dose_unit,
                amount_per_100g,
                nutrient_unit,
                component_name=component,
            )
            if grams_needed is None or grams_needed <= 0:
                continue
            if best is None or float(grams_needed) < float(best.get("grams_needed", MAX_GRAMS_INFINITY_PLACEHOLDER)):
                best = {
                    "component": component,
                    "food_name": food_name,
                    "grams_needed": float(grams_needed),
                    "dose_value": dose_value,
                    "dose_unit": dose_unit,
                }

        if best:
            requirements.append(best)

    return requirements


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

        selected_food_name = str(cand.get("selected_food_name", "") or "").strip()
        selected_grams_needed = cand.get("selected_grams_needed")
        if selected_food_name and selected_grams_needed is not None:
            try:
                selected_grams = float(selected_grams_needed)
            except Exception:
                selected_grams = 0.0
            if selected_grams > 0:
                requirements.append(
                    {
                        "component": component,
                        "food_name": selected_food_name,
                        "grams_needed": float(selected_grams),
                    }
                )
                continue

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
            grams_needed = grams_needed_to_match_dose(
                dose_value,
                dose_unit,
                amount_per_100g,
                nutrient_unit,
                component_name=component,
            )
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

            if best is None or required_cost < float(best.get("required_cost", MAX_GRAMS_INFINITY_PLACEHOLDER)):
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
    text_l = (text or "").lower()
    tokens = re.findall(r"[a-z0-9]+", text_l)
    vitamin_letter_tokens = {
        m.group(1)
        for m in RAG_VITAMIN_LETTER_PATTERN.finditer(text_l)
    }

    filtered: list[str] = []
    for tok in tokens:
        if tok in RAG_STOPWORDS and tok not in vitamin_letter_tokens:
            continue
        # Drop single-character tokens (like isolated "b") to reduce noisy matches.
        if len(tok) == 1 and tok not in vitamin_letter_tokens:
            continue
        filtered.append(tok)
    return filtered


def expand_rag_query_terms(tokens: list[str]) -> set[str]:
    expanded = set(tokens)
    aliases: dict[str, set[str]] = {
        "b12": {"cobalamin", "vitamin", "b12"},
        "cobalamin": {"b12", "vitamin", "cobalamin"},
        "b9": {"folate", "folic", "acid", "b9"},
        "folate": {"b9", "folic", "acid", "folate"},
        "b6": {"pyridoxine", "b6"},
        "b1": {"thiamin", "thiamine", "b1"},
        "b2": {"riboflavin", "b2"},
        "b3": {"niacin", "b3"},
        "b7": {"biotin", "b7"},
        "complex": {"complex", "vitamin"},
        "vitmain": {"vitamin"},
    }
    for tok in tokens:
        if tok in aliases:
            expanded.update(aliases[tok])
    return expanded


def extract_rag_query_phrases(query: str) -> list[str]:
    normalized = normalize_lookup_key(query)
    if not normalized:
        return []

    phrases: list[str] = []
    if re.search(r"\b(vitamin|vitmain)?\s*b\s*-?\s*complex\b", normalized):
        phrases.append("vitamin b complex")
    if "folic acid" in normalized:
        phrases.append("folic acid")
    if "vitamin d" in normalized:
        phrases.append("vitamin d")

    # Generic vitamin-letter phrases such as "vitamin a" or "vitamin e".
    for match in re.finditer(r"\b(?:vitamin|vitmain)\s+([abcdehk])\b", normalized):
        phrases.append(f"vitamin {match.group(1)}")

    return phrases


def detect_rag_query_intent(query: str) -> dict[str, bool]:
    q = (query or "").lower()
    asks_daily_requirement = bool(
        re.search(
            r"\b(how much|recommended|daily|per day|rda|ai|ul|intake|dosage|dose|requirement|required)\b",
            q,
        )
    )
    asks_list = bool(re.search(r"\b(list|all|table|overview|complete)\b", q))
    asks_micronutrients = bool(re.search(r"\b(micronutrient|micronutrients|vitamin|minerals|mineral)\b", q))

    return {
        "asks_daily_requirement": asks_daily_requirement,
        "asks_list": asks_list,
        "asks_micronutrients": asks_micronutrients,
        "asks_guideline_table": asks_daily_requirement and (asks_list or asks_micronutrients),
    }


def chunk_has_guideline_signals(text: str) -> bool:
    t = (text or "").lower()
    if re.search(r"\b(rda|ai|ul|recommended dietary allowance|adequate intake|tolerable upper intake)\b", t):
        return True
    if re.search(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|ug|µg|μg)\s*/\s*(?:day|d)\b", t):
        return True
    if re.search(r"\b(?:men|women|male|female|adult|adults|pregnan|lactat|age)\b", t) and re.search(r"\b(?:mg|mcg|ug|µg|μg)\b", t):
        return True
    return False


def score_chunk(query_terms: set[str], chunk_text_value: str, query_text: str = "") -> float:
    chunk_terms = tokenize_for_rag(chunk_text_value)
    if not chunk_terms:
        return 0.0
    hits = sum(1 for term in chunk_terms if term in query_terms)
    unique_hits = len(set(chunk_terms).intersection(query_terms))
    score = float(hits + (2 * unique_hits))

    chunk_l = (chunk_text_value or "").lower()
    query_l = (query_text or "").lower()

    # Phrase-level boosts to reduce vitamin-family cross-talk (e.g., B-complex vs K).
    asks_b_complex = bool(re.search(r"\b(vitmain|vitamin)?\s*b\s*-?\s*complex\b", query_l))
    if asks_b_complex:
        if re.search(r"\b(vitmain|vitamin)?\s*b\s*-?\s*complex\b", chunk_l):
            score += 24.0
        b_hits = len(re.findall(r"\b(?:b1|b2|b3|b5|b6|b7|b9|b12|thiamin|riboflavin|niacin|folate|biotin|pantothenic|cobalamin)\b", chunk_l))
        if b_hits > 0:
            score += min(12.0, float(b_hits) * 1.8)
        if re.search(r"\bvitamin\s*k\b", chunk_l) and "b" not in chunk_l:
            score -= 6.0

        asks_benefit = bool(re.search(r"\b(good|benefit|benefits|help|helps|purpose|used for|why)\b", query_l))
        if asks_benefit and re.search(r"\b(why you should take|benefit|benefits|helps|used for|supports|important for)\b", chunk_l):
            score += 8.0

        # Down-rank index/navigation chunks that frequently pollute top retrieval.
        bullet_count = chunk_l.count("•")
        if bullet_count >= 6:
            score -= 12.0
        if chunk_l.startswith("back to:") or "also known as:" in chunk_l:
            score -= 10.0

    return score


def _chunk_mentions_b_complex_domain(text: str) -> bool:
    chunk_l = (text or "").lower()
    if re.search(r"\b(vitmain|vitamin)?\s*b\s*-?\s*complex\b", chunk_l):
        return True
    if re.search(r"\b(?:vitamin\s*b12|vitamin\s*b6|vitamin\s*b1|vitamin\s*b2|vitamin\s*b3|vitamin\s*b5|vitamin\s*b7|vitamin\s*b9)\b", chunk_l):
        return True
    if re.search(r"\b(?:thiamin|thiamine|riboflavin|niacin|folate|folic acid|biotin|pantothenic|cobalamin)\b", chunk_l):
        return True
    return False


def has_numeric_guidance(text: str) -> bool:
    return bool(
        re.search(
            r"\b\d+(?:\.\d+)?\s*(?:g|mg|mcg|ug|µg|μg|kg|g/kg|mg/kg|%)\b",
            (text or "").lower(),
        )
    )


def retrieve_rag_chunks(query: str, chunks: list[dict[str, str]], top_k: int = RAG_TOP_K) -> list[dict[str, Any]]:
    raw_tokens = tokenize_for_rag(query)
    query_terms = set(raw_tokens)
    if not query_terms:
        return []

    expanded_query_terms = expand_rag_query_terms(raw_tokens)
    query_phrases = extract_rag_query_phrases(query)

    query_l = (query or "").lower()
    asks_b_complex = bool(re.search(r"\b(vitmain|vitamin)?\s*b\s*-?\s*complex\b", query_l))

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

    asks_benefit = bool(re.search(r"\b(good|benefit|benefits|help|helps|purpose|used for|why|supports)\b", query_l))
    asks_safety = bool(re.search(r"\b(side effect|side effects|safe|safety|risk|contraindication|adverse)\b", query_l))
    intent = detect_rag_query_intent(query)
    asks_guideline_table = bool(intent.get("asks_guideline_table", False))

    scored: list[tuple[float, dict[str, Any]]] = []
    for chunk in chunks:
        chunk_text_value = chunk.get("text", "")
        chunk_l = (chunk_text_value or "").lower()

        if asks_guideline_table and not chunk_has_guideline_signals(chunk_text_value):
            # For dosage/list intents, suppress generic mention-only chunks.
            continue

        if asks_b_complex and not _chunk_mentions_b_complex_domain(chunk_text_value):
            continue
        score = score_chunk(expanded_query_terms, chunk_text_value, query_text=query)

        for phrase in query_phrases:
            if phrase and phrase in chunk_l:
                score += 16.0

        if asks_benefit and re.search(r"\b(why you should take|benefit|benefits|helps|supports|important for|used for)\b", chunk_l):
            score += 6.0
        if asks_safety and re.search(r"\b(side effect|side effects|risk|contraindication|adverse|safety|safe)\b", chunk_l):
            score += 6.0

        if asks_guideline_table:
            if chunk_has_guideline_signals(chunk_text_value):
                score += 14.0
            if re.search(r"\b(vitamin|mineral|micronutrient)s?\b", chunk_l):
                score += 5.0

        # Penalize navigation/index chunks regardless of nutrient type.
        if chunk_l.startswith("back to:"):
            score -= 10.0
        if "also known as:" in chunk_l and "why you should take" not in chunk_l:
            score -= 6.0
        if chunk_l.count("•") >= 8:
            score -= 10.0

        if wants_numeric and has_numeric_guidance(chunk_text_value):
            score += 6.0
        if score > 0:
            scored.append((score, {**chunk, "_score": score}))

    if not scored:
        return []

    scored.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in scored[:top_k]]


def answer_rag_question(query: str, chunks: list[dict[str, str]]) -> tuple[str, list[str], dict[str, Any]]:
    retrieved = retrieve_rag_chunks(query, chunks)
    if not retrieved:
        fallback_query = f"{query.strip()} evidence-based nutrition summary from NIH ODS, Examine, and peer-reviewed meta-analysis"
        return (
            "No relevant context found in the reference library.",
            [],
            {
                "needs_web_fallback": True,
                "reason": "no_retrieval",
                "fallback_query": fallback_query,
                "retrieval_confidence": 0.0,
            },
        )

    top_score = float(retrieved[0].get("_score", 0.0) or 0.0)
    second_score = float(retrieved[1].get("_score", 0.0) or 0.0) if len(retrieved) > 1 else 0.0
    score_gap = top_score - second_score
    retrieval_confidence = max(0.0, min(1.0, (top_score / 40.0) + (score_gap / 25.0)))

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

    answer = call_text_llm(system_prompt, user_prompt)
    if answer:
        return (
            answer,
            citations,
            {
                "needs_web_fallback": False,
                "reason": "answered_from_local_rag",
                "fallback_query": "",
                "retrieval_confidence": retrieval_confidence,
            },
        )

    intent = detect_rag_query_intent(query)
    asks_guideline_table = bool(intent.get("asks_guideline_table", False))
    source_note = ", ".join(citations[:3]) if citations else "the local reference set"
    if asks_guideline_table:
        fallback = (
            "I found relevant local guideline excerpts, but the local answer model is unavailable right now, "
            "so I cannot safely synthesize a complete daily micronutrient list from those excerpts yet. "
            f"Please use the web fallback resources below (prioritize NIH ODS / EFSA) or retry once the local model is available. "
            f"Top retrieved sources: {source_note}."
        )
    else:
        fallback = (
            "I retrieved local reference excerpts, but the answer model is unavailable right now, "
            "so I cannot reliably synthesize your requested answer yet. "
            f"Top retrieved sources: {source_note}."
        )
    fallback_query = f"{query.strip()} evidence-based nutrition summary from NIH ODS, Examine, and peer-reviewed meta-analysis"
    return (
        fallback,
        citations,
        {
            "needs_web_fallback": True,
            "reason": "llm_unavailable",
            "fallback_query": fallback_query,
            "retrieval_confidence": retrieval_confidence,
        },
    )


def build_web_fallback_package(question: str, fallback_query: str) -> dict[str, Any]:
    q = str(question or "").strip()
    fq = str(fallback_query or "").strip()
    base_query = fq if fq else q
    if not base_query:
        base_query = "evidence-based nutrition intake guidance"

    queries = [
        f"{base_query} site:ods.od.nih.gov",
        f"{base_query} site:examine.com",
        f"{base_query} site:efsa.europa.eu",
        f"{base_query} site:who.int nutrition guideline",
        f"{base_query} systematic review meta-analysis",
    ]

    trusted_urls = [
        "https://ods.od.nih.gov/factsheets/list-all/",
        "https://www.efsa.europa.eu/en/topics/topic/dietary-reference-values",
        "https://www.who.int/health-topics/nutrition",
        "https://pubmed.ncbi.nlm.nih.gov/",
        "https://www.examine.com/",
    ]

    search_url = "https://duckduckgo.com/?q=" + quote_plus(base_query)
    return {
        "base_query": base_query,
        "queries": queries,
        "trusted_urls": trusted_urls,
        "search_url": search_url,
    }


  
# ---------------------------------------------------------------------------  
# Exotic / non-retail food guardrail (agentic patcher.py)  
#  
# Discovery and "is this common" ranking are handled by the agent system  
# prompts (it prefers common supermarket single-ingredient foods and orders  
# by concentration). This block is ONLY a deterministic last-line safety net  
# that guarantees exotic / game / non-retail / heavily processed items never  
# reach the dropdowns, even if the model ignores its prompt.  
#  
# Tier  1 = allowed (passes guardrail)  
# Tier -1 = blocked (exotic / non-retail / processed)  
# ---------------------------------------------------------------------------

# Hard blocklist: exotic, game, or non-retail items we never suggest,  
# even when nutrient density is extreme (e.g. polar bear liver, whale, seal).  
EXOTIC_FOOD_BLOCK_KEYWORDS: set[str] = {  
    "bear", "polar bear", "seal", "whale", "walrus", "moose", "elk",  
    "venison", "deer", "caribou", "reindeer", "bison", "buffalo", "boar",  
    "emu", "ostrich", "pheasant", "quail", "kangaroo", "alligator",  
    "crocodile", "snake", "insect", "cricket", "locust", "horse", "camel",  
    "goose liver", "foie gras", "blubber", "muktuk", "game meat", "antelope",  
    "rabbit", "hare", "pigeon", "squab", "frog", "turtle", "octopus",  
    "sea lion", "wild boar", "elk liver", "moose liver",  
}

# Processed / multi-ingredient hints => not a single-ingredient whole food.  
PROCESSED_FOOD_HINT_KEYWORDS: set[str] = {  
    "fortified", "supplement", "powder", "bar", "drink mix", "formula",  
    "infant", "baby food", "candy", "snack", "fast food", "restaurant",  
    "sauce", "gravy", "fried", "breaded", "luncheon", "sausage", "nugget",  
}


def classify_food_commonness(food_description: str) -> dict[str, Any]:  
    """Return guardrail tier for a food.

    tier  1 = allowed (not on the blocklist)  
    tier -1 = blocked (exotic / non-retail / heavily processed / empty)  
    """  
    key = normalize_lookup_key(food_description)  
    if not key:  
        return {"tier": -1, "reason": "empty"}

    for tok in EXOTIC_FOOD_BLOCK_KEYWORDS:  
        if tok in key:  
            return {"tier": -1, "reason": f"exotic: {tok}"}

    for tok in PROCESSED_FOOD_HINT_KEYWORDS:  
        if tok in key:  
            return {"tier": -1, "reason": f"processed: {tok}"}

    return {"tier": 1, "reason": "allowed"}


def filter_and_rank_common_foods(  
    foods: list[dict[str, Any]],  
    limit: int,  
) -> list[dict[str, Any]]:  
    """Drop blocklisted foods, then rank the survivors by concentration.

    The agent already orders results by commonness via its system prompt;  
    this only removes exotic/processed items and re-sorts by amount_per_100g  
    (desc) with a small preparation penalty as a tie-breaker.  
    """  
    enriched: list[tuple[float, int, dict[str, Any]]] = []  
    for food in foods:  
        if classify_food_commonness(str(food.get("food_description", "") or ""))["tier"] < 0:  
            continue  # drop exotic / processed entirely  
        try:  
            amount = float(food.get("amount_per_100g", 0.0) or 0.0)  
        except Exception:  
            amount = 0.0  
        if amount <= 0:  
            continue  
        prep_penalty = _whole_food_preparation_penalty(  
            str(food.get("food_description", "") or "")  
        )  
        enriched.append((amount, prep_penalty, food))

    if not enriched:  
        return []

    enriched.sort(key=lambda e: (-e[0], e[1]))  
    return [e[2] for e in enriched[:limit]]  


@functools.lru_cache(maxsize=1)
def _load_usda_nutrients_index() -> list[dict[str, Any]]:
    conn = try_open_usda_db()
    if conn is None:
        return []
    try:
        rows = conn.execute(
            """
            SELECT id, nutrient_name, unit_name
            FROM nutrients
            """
        ).fetchall()
    except Exception:
        return []
    finally:
        conn.close()

    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            nutrient_id = int(row[0])
        except Exception:
            continue
        nutrient_name = str(row[1] or "").strip()
        unit_name = str(row[2] or "").strip()
        key = normalize_lookup_key(nutrient_name)
        if not nutrient_name or not key:
            continue
        out.append({
            "id": nutrient_id,
            "name": nutrient_name,
            "unit": unit_name,
            "key": key,
            "compact": re.sub(r"[^a-z0-9]+", "", key),
        })
    return out


def _resolve_local_nutrient_candidates(component_key: str, max_ids: int = 3) -> list[dict[str, Any]]:
    target = normalize_lookup_key(component_key)
    if not target:
        return []

    target_compact = re.sub(r"[^a-z0-9]+", "", target)
    target_tokens = {t for t in target.split() if len(t) >= 2}
    ranked: list[tuple[tuple[int, int, int], dict[str, Any]]] = []
    for nutrient in _load_usda_nutrients_index():
        nkey = str(nutrient.get("key", "") or "")
        ncompact = str(nutrient.get("compact", "") or "")
        ntokens = {t for t in nkey.split() if len(t) >= 2}
        overlap = len(target_tokens & ntokens)
        direct = int(target in nkey or nkey in target)
        compact_match = int(target_compact and ncompact and (target_compact in ncompact or ncompact in target_compact))
        score = (direct + compact_match + min(overlap, 4), overlap, -len(nkey))
        if score[0] <= 0:
            continue
        ranked.append((score, nutrient))

    ranked.sort(key=lambda item: item[0], reverse=True)
    out: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for _score, nutrient in ranked:
        nid = int(nutrient.get("id", 0) or 0)
        if nid <= 0 or nid in seen_ids:
            continue
        seen_ids.add(nid)
        out.append(nutrient)
        if len(out) >= max_ids:
            break
    return out


def _build_local_food_rows_for_component(component_key: str, limit: int = TOP_FOODS_PER_COMPONENT) -> list[dict[str, Any]]:
    nutrient_candidates = _resolve_local_nutrient_candidates(component_key, max_ids=3)
    if not nutrient_candidates:
        return []

    nutrient_ids = [int(x.get("id", 0) or 0) for x in nutrient_candidates if int(x.get("id", 0) or 0) > 0]
    if not nutrient_ids:
        return []

    conn = try_open_usda_db()
    if conn is None:
        return []
    try:
        placeholders = ",".join(["?"] * len(nutrient_ids))
        sql = (
            "SELECT nr.food_description, nr.food_category, nr.amount_per_100g, nr.nutrient_id, n.nutrient_name, n.unit_name "
            "FROM nutrient_rankings nr "
            "JOIN nutrients n ON n.id = nr.nutrient_id "
            f"WHERE nr.nutrient_id IN ({placeholders}) "
            "AND nr.amount_per_100g IS NOT NULL "
            "AND nr.amount_per_100g > 0 "
            "ORDER BY nr.amount_per_100g DESC "
            "LIMIT 120"
        )
        rows = conn.execute(sql, nutrient_ids).fetchall()
    except Exception:
        return []
    finally:
        conn.close()

    nutrient_by_id = {int(x["id"]): x for x in nutrient_candidates}
    foods: list[dict[str, Any]] = []
    seen_foods: set[str] = set()
    for idx, row in enumerate(rows, start=1):
        food_desc = str(row[0] or "").strip()
        if not food_desc:
            continue
        fkey = normalize_lookup_key(food_desc)
        if not fkey or fkey in seen_foods:
            continue
        seen_foods.add(fkey)
        try:
            amount = float(row[2] or 0.0)
        except Exception:
            amount = 0.0
        if amount <= 0:
            continue
        nutrient_id = int(row[3] or 0)
        candidate = nutrient_by_id.get(nutrient_id, {})
        unit_name = str(row[5] or candidate.get("unit", "") or "")
        foods.append(
            {
                "rank": idx,
                "food_description": food_desc,
                "food_category": str(row[1] or "Whole food"),
                "amount_per_100g": amount,
                "unit": _normalize_component_unit_token(unit_name),
                "source_db": "USDA Local DB",
            }
        )

    foods = filter_and_rank_common_foods(foods, limit)
    return foods[:limit]


def build_ai_food_matches(components: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """Single batched LLM call for ALL components (replaces N serial round-trips)."""
    if not components:
        return [], [], "ok"

    llm_enabled = _text_llm_available()
    if not llm_enabled:
        logger.warning("Blockbrain text model unavailable for whole-food matching; using local USDA fallback")

    seen_keys: set[str] = set()
    prompt_lines: list[str] = []
    ordered: list[dict[str, Any]] = []
    for item in components:
        key = normalize_lookup_key(str(item.get("component", "")))
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        dose_txt = ""
        dv = item.get("dose_value")
        du = str(item.get("dose_unit") or "")
        if dv is not None:
            try:
                dose_txt = f"{format_float(float(dv), 4)} {du}".strip()
            except Exception:
                dose_txt = str(dv)
        prompt_lines.append(
            f"- component: {key} | dose: {dose_txt or 'unknown'}"
        )
        ordered.append(item)

    system_prompt = (
        "You are a nutrition data assistant using USDA FoodData Central. "
        "Return strict JSON only - no markdown, no prose."
    )
    schema = (
        '{"components": [{"component": "str", "resolved_nutrient": "str", '
        '"confidence": "high|medium|low", "related_nutrients": ["str"], '
        '"foods": [{"food_description": "str", "food_category": "str", '
        '"amount_per_100g": number, "unit": "mg|mcg|g|IU", "source_db": "str"}]}]}'
    )
    nl = chr(10)
    user_prompt = (
        "For EACH supplement component, return whole-food replacements." + nl
        + f"Schema: {schema}" + nl
        + f"Rules: up to {TOP_FOODS_PER_COMPONENT} COMMON single-ingredient whole foods "
        + "that an average person can buy in a normal supermarket (e.g. spinach, eggs, "
        + "salmon, beef liver, almonds). Do NOT suggest exotic, game, or non-retail "
        + "items (e.g. polar bear liver, whale, seal, insects) even if their nutrient "
        + "density is very high. Prefer the most common food first; among common foods, "
        + "list highest concentration per 100g first. amount_per_100g must be > 0." + nl + nl
        + "Components:" + nl + nl.join(prompt_lines)
    )

    raw = call_text_llm(system_prompt, user_prompt) if llm_enabled else ""
    candidate = clean_json_block(raw)
    parsed_components: list[dict[str, Any]] = []
    try:
        data = json.loads(candidate)
        if isinstance(data, dict):
            parsed_components = data.get("components", []) or []
    except Exception:
        logger.warning("build_ai_food_matches: failed to parse batch JSON response")

    parsed_by_key: dict[str, dict[str, Any]] = {}
    for entry in parsed_components:
        if not isinstance(entry, dict):
            continue
        k = normalize_lookup_key(str(entry.get("component", "") or ""))
        if k:
            parsed_by_key[k] = entry

    summaries: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []

    for item in ordered:
        component_key = normalize_lookup_key(str(item.get("component", "") or ""))
        dose_value = item.get("dose_value")
        dose_unit = str(item.get("dose_unit") or "")
        parsed = parsed_by_key.get(component_key, {})

        foods_in = parsed.get("foods", []) if isinstance(parsed.get("foods"), list) else []
        deduped_foods: list[dict[str, Any]] = []
        seen_foods: set[str] = set()

        for idx, row in enumerate(foods_in, start=1):
            if not isinstance(row, dict):
                continue
            food_desc = str(row.get("food_description", "") or "").strip()
            if not food_desc:
                continue
            fkey = normalize_lookup_key(food_desc)
            if not fkey or fkey in seen_foods:
                continue
            try:
                amount = float(row.get("amount_per_100g", 0.0) or 0.0)
            except Exception:
                amount = 0.0
            if amount <= 0:
                continue
            seen_foods.add(fkey)
            raw_unit = str(row.get("unit", "") or "")
            deduped_foods.append({
                "rank": idx,
                "food_description": food_desc,
                "food_category": str(row.get("food_category", "Whole food") or "Whole food"),
                "amount_per_100g": amount,
                "unit": _normalize_component_unit_token(raw_unit),
                "source_db": str(row.get("source_db", "USDA FoodData Central") or "USDA FoodData Central"),
            })

        # Patch: drop exotic/non-retail foods, then rank by concentration.
        deduped_foods = filter_and_rank_common_foods(deduped_foods, OVERVIEW_ALT_LIMIT)
        top_foods = deduped_foods[:TOP_FOODS_PER_COMPONENT]

        related = ", ".join([
            str(x or "") for x in (parsed.get("related_nutrients", []) or [])
            if str(x or "").strip()
        ])
        confidence = str(parsed.get("confidence", "medium") or "medium")
        resolved = str(parsed.get("resolved_nutrient", "") or "")

        if not top_foods:
            # Deterministic local fallback so Results still show alternatives when
            # LLM mapping is unavailable or returns sparse/invalid JSON.
            local_foods = _build_local_food_rows_for_component(component_key, limit=TOP_FOODS_PER_COMPONENT)
            if local_foods:
                top_foods = local_foods
                deduped_foods = local_foods

        if not top_foods:
            log_unmapped_component(component_key, dose_value=dose_value, dose_unit=dose_unit)
            summaries.append({
                "component": component_key,
                "supplement_dose_value": dose_value,
                "supplement_dose_unit": dose_unit,
                "resolved_nutrient": resolved or "Not mapped",
                "confidence": confidence,
                "top_food": "",
                "top_amount_per_100g": "",
                "related_nutrients": related,
            })
            continue

        top_amt = float(top_foods[0].get("amount_per_100g", 0.0) or 0.0)
        top_unit = str(top_foods[0].get("unit", "") or "")
        top_amt_txt, top_unit_txt = format_amount_unit_for_display(top_amt, top_unit)

        summaries.append({
            "component": component_key,
            "supplement_dose_value": dose_value,
            "supplement_dose_unit": dose_unit,
            "resolved_nutrient": resolved,
            "confidence": confidence,
            "top_food": str(top_foods[0].get("food_description", "") or ""),
            "top_amount_per_100g": f"{top_amt_txt} {top_unit_txt}/100g".strip() if top_foods else "",
            "related_nutrients": related,
        })
        details.append({
            "component": component_key,
            "supplement_dose_value": dose_value,
            "supplement_dose_unit": dose_unit,
            "resolved_nutrient": resolved,
            "confidence": confidence,
            "match_method": (
                "llm_official_db_batched"
                if llm_enabled and bool(foods_in)
                else "local_usda_fallback"
            ),
            "proxy_rationale": (
                "Resolved via single batched LLM call with USDA references."
                if llm_enabled and bool(foods_in)
                else "Resolved via deterministic USDA local fallback."
            ),
            "related_nutrients": related,
            "foods": deduped_foods,
        })

    return summaries, details, "ok"



def normalize_component_name(raw_name: str) -> str:
    text = (raw_name or "").strip()
    if not text:
        return ""

    text = text.replace("Öl", "Oil").replace("öl", "oil")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.replace("*", " ")
    text = text.replace("|", " ")
    text = re.sub(r"\([^)]*\)", "", text)
    text = text.split("/")[0].strip()
    text = re.sub(r"^[\-•:;,.|\s]+", "", text)
    text = re.sub(r"[\-•:;,.|\s]+$", "", text)
    text = re.sub(r"\s*:+\s*$", "", text)
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

    # Common OCR/German transliteration variant: oel -> oil (e.g., MCT-OEl).
    lowered = re.sub(r"\boel\b", "oil", lowered)

    return lowered


OCR_VITAMIN_PREFIX_PATTERN = re.compile(r"^(vitamin\s+[a-z](?:\d{1,2})?)\b", re.I)
OCR_VITAMIN_TOKEN_PATTERN = re.compile(r"\b(?:vit(?:amin|main)|vitarnin)\s*([a-z])\s*(\d{0,2})\b", re.I)
OCR_VITAMIN_SHORTHAND_PATTERN = re.compile(r"\b([bdk])\s*[-:]?\s*(12|[1-9])\b", re.I)
OCR_DOSE_TOKEN_PATTERN = re.compile(r"(?P<val>(?:\d|[lI|])[0-9oO]*(?:[\.,][0-9oO]+)?)\s*(?P<unit>mg|mcg|meg|ug|µg|μg|fg|iu|ui|ie|g)\b", re.I)
OCR_VITAMIN_INLINE_DOSE_PATTERN = re.compile(
    r"\b(?:vit(?:amin|main)|vitarnin)\s*(?P<letter>[a-z])\s*(?P<suffix>\d{0,2})\b"
    r"[^\n]{0,24}?"
    r"(?P<val>(?:\d|[lI|])[0-9oO]*(?:[\.,][0-9oO]+)?)\s*"
    r"(?P<unit>mg|mcg|meg|ug|µg|μg|fg|iu|ui|ie|g)\b",
    re.I,
)
OCR_COMPONENT_NEAR_DOSE_PATTERN = re.compile(
    r"\b(?:"
    r"vit(?:amin|main)?\s*[a-z](?:\d{1,2})?"
    r"|amino\s+blend"
    r"|enzyme\s+blend"
    r"|phyto\s+blend"
    r"|viri\s+blend"
    r"|thiamin(?:e)?"
    r"|riboflavin"
    r"|niacin"
    r"|pantothenic\s+acid"
    r"|pyridoxine"
    r"|biotin"
    r"|folic\s+acid"
    r"|folate"
    r"|cobalamin"
    r"|choline"
    r"|calcium"
    r"|phosph(?:or(?:us|ous)|orous|0rous|0rus)"
    r"|potass(?:ium|um|lum)"
    r"|magnes(?:ium|lum|iurn)"
    r"|iron"
    r"|copper"
    r"|manganese"
    r"|boron"
    r"|fluoride"
    r"|fluorine"
    r"|iodine"
    r"|jodine"
    r"|l[o0]dine"
    r"|chromium"
    r"|selen(?:ium|iurn)"
    r"|molybdenum"
    r"|alpha\s+lipoic\s+acid"
    r"|paba"
    r"|para\s*-?\s*aminobenzoic\s+acid"
    r"|inositol"
    r"|silica"
    r"|alpha\s*-?\s*carotene"
    r"|vanadium"
    r"|cryptoxanthin"
    r"|zeaxanthin"
    r"|zinc"
    r"|beta\s*-?\s*carotene"
    r"|lutein"
    r"|lycopene"
    r")\b",
    re.I,
)
OCR_MICROGRAM_COMPONENTS: set[str] = {
    "vitamin a",
    "vitamin d",
    "vitamin d2",
    "vitamin d3",
    "vitamin k",
    "vitamin k1",
    "vitamin k2",
    "vitamin b12",
    "biotin",
    "folate",
    "folic acid",
    "selenium",
    "iodine",
    "chromium",
    "molybdenum",
}

OCR_MINERAL_FORM_COMPONENTS: set[str] = {
    "calcium",
    "iron",
    "magnesium",
    "zinc",
    "selenium",
    "copper",
    "manganese",
    "iodine",
    "chromium",
    "molybdenum",
    "potassium",
    "phosphorus",
    "boron",
    "fluoride",
    "fluorine",
    "cesium",
}

OCR_MINERAL_FORM_SUFFIX_PATTERN = re.compile(
    r"^(?P<base>calcium|iron|magnesium|zinc|selenium|copper|manganese|iodine|chromium|molybdenum|potassium|phosphorus|boron|fluoride|fluorine|cesium)\s+"
    r"(?:l-|d-|dl-)?(?:acid|arginine|lysine|methionine|citrate|chloride|iodide|phosphate|carbonate|oxide|"
    r"sulfate|sulphate|fumarate|gluconate|chelate|picolinate|molybdate|selenate|borate|fluoride|pantothenate)\b",
    re.I,
)


def _repair_ocr_component_name(component: str) -> str:
    text = normalize_component_name(component)
    if not text:
        return ""

    # Common OCR misspellings for vitamin prefix and nutrient names.
    text = re.sub(r"\bvit(?:amn|main|arnin)\b", "vitamin", text, flags=re.I)
    text = re.sub(r"\bpotassum\b", "potassium", text, flags=re.I)
    text = re.sub(r"\bphosphours\b", "phosphorus", text, flags=re.I)
    text = re.sub(r"\bion\b", "iron", text, flags=re.I)

    # OCR confusion: capital I misread as lowercase l (lodine → iodine).
    if re.match(r"^l[o0]dine?$", text, re.I):
        text = "iodine"

    # OCR variants for l-methionine (LMethionne, lmethionine etc.)
    text = re.sub(r"^l[\s\-]?methion\w*$", "l-methionine", text, flags=re.I)

    # Strip trailing percentage/extract-concentration notations: "lycopene 10%*" → "lycopene"
    text = re.sub(r"\s+\d+%?[\*\^]?\s*$", "", text).strip()

    component_alias_map: dict[str, str] = {
        "vitamin b1": "thiamin",
        "vitamin b2": "riboflavin",
        "vitamin b3": "niacin",
        "vitamin b5": "pantothenic acid",
        "para-aminobenzoic acid": "paba",
        "para aminobenzoic acid": "paba",
    }
    if text in component_alias_map:
        return component_alias_map[text]

    # OCR confusion on curved bottle labels: "vitamin k2" can be read as
    # "vitamin ka" when the numeral is degraded.
    if re.match(r"^vitamin\s+ka$", text, re.I):
        return "vitamin k2"

    mineral_form_match = OCR_MINERAL_FORM_SUFFIX_PATTERN.match(text)
    if mineral_form_match:
        return str(mineral_form_match.group("base") or "").lower()

    # Common OCR variants for MCT-Oel/Oil in curved bottle photos.
    if text in {"mct-ol", "mct-oi", "mct-oi.", "uct-ol", "uct-oi", "nct-ol", "nct-oi"}:
        return "mct-oil"

    shorthand_with_suffix = re.match(r"^([bdk])\s*[-:]?\s*(\d{1,2})$", text, re.I)
    if shorthand_with_suffix:
        return f"vitamin {shorthand_with_suffix.group(1).lower()}{shorthand_with_suffix.group(2)}"

    shorthand_single = re.match(r"^([adek])$", text, re.I)
    if shorthand_single:
        return f"vitamin {shorthand_single.group(1).lower()}"

    vitamin_match = OCR_VITAMIN_PREFIX_PATTERN.match(text)
    if vitamin_match:
        return vitamin_match.group(1).lower()

    text = re.sub(r"\b(?:we|ve|wv|nrv|rv|iv)\b$", "", text, flags=re.I).strip()
    return text


def _parse_ocr_numeric_value(raw_value: str) -> float | None:
    token = str(raw_value or "").strip()
    if not token:
        return None
    # Conservative OCR digit correction: only apply high-confidence substitutions
    # and avoid broad letter->digit replacements that can inflate values.
    if re.search(r"[A-Za-z|$]", token):
        corrections = [
            (r"[Oo]", "0"),
            (r"[lI|]", "1"),
            (r"[Zz]", "2"),
            (r"[Ss$]", "5"),
            (r"[Bb]", "8"),
        ]
        for pattern, repl in corrections:
            token = re.sub(pattern, repl, token)
    # OCR often confuses 1 with l, I, or | in small table fonts.
    if token and token[0] in {"l", "I", "|"}:
        token = "1" + token[1:]
    token = re.sub(r"[^0-9,\.-]", "", token)
    if not token:
        return None
    return _parse_float(token)


def _extract_last_component_before_dose(prefix_text: str) -> str:
    """Pick the nearest nutrient-like token before a dose within dense OCR text."""
    text = str(prefix_text or "").strip()
    if not text:
        return ""

    text = re.sub(r"\b\d+(?:[\.,]\d+)?\s*%\s*(?:dv|nrv|ri|we)?\b", " ", text, flags=re.I)
    matches = list(OCR_COMPONENT_NEAR_DOSE_PATTERN.finditer(text))
    if not matches:
        return ""
    candidate = str(matches[-1].group(0) or "").strip()
    return _repair_ocr_component_name(candidate)


def _is_plausible_component_name(component: str) -> bool:
    c = normalize_lookup_key(component)
    if not c:
        return False

    if len(c) < 3:
        return False

    # Header/footer leakage from OCR should never be treated as a nutrient row.
    junk_tokens = {
        "inhaltsstoffe",
        "tagesdosis",
        "referenzmengen",
        "internationale",
        "einheiten",
        "herstellung",
        "vertrieb",
        "nrv",
        "fur",
        "durchschnittlichen",
    }
    words = c.split()
    if any(w in junk_tokens for w in words):
        return False

    if len(words) > 6:
        return False

    # Reject mostly single-letter fragments such as "a l".
    short_words = sum(1 for w in words if len(w) <= 1)
    if short_words >= 2 and not c.startswith("vitamin "):
        return False

    # OCR often creates glued garbage such as "vaamm vitamin b2 b10".
    # If a vitamin token appears, enforce canonical vitamin-leading format.
    if "vitamin" in c and not c.startswith("vitamin "):
        return False
    if len(re.findall(r"\bvit(?:amin|amn|main)\b", c, flags=re.I)) > 1:
        return False

    # Vitamin tokens should match canonical forms like vitamin a, vitamin d3, vitamin k2.
    # Reject malformed OCR fragments such as "vitamin ka 2".
    if c.startswith("vitamin "):
        if not re.match(r"^vitamin\s+[abcdek](?:\d{1,2})?$", c):
            return False

    # Block ingredient chemical compound forms — manufacturing/salt forms that appear in
    # the INGREDIENTS section, never as standalone nutrient names in the nutrition table.
    _INGR_COMPOUND_PAT = re.compile(
        r"\b(?:oxide|sulphate|sulfate|molybdate|trichloride|selenate|borate|"
        r"carbonate|phosphate|fumarate|stearate|tocopheryl|hydrochloride|"
        r"mononitrate|glycolate|ascorbate|gluconate)\b",
        re.I,
    )
    if _INGR_COMPOUND_PAT.search(c):
        return False

    # Block chemical d- prefixed names (d-biotin, d-alpha-tocopherol etc.).
    # Legitimate nutrient names never start with "d-" as a chemical-form prefix.
    if re.match(r"^d-[a-z]", c, re.I):
        return False

    # Block names ending with a single dangling letter — these are OCR fragments
    # (e.g. "calcium d" from "Calcium D-Pantothenate").  Vitamin names are already
    # validated above and don't reach this check.
    if not c.startswith("vitamin ") and re.search(r"\s+[a-f]$", c):
        return False

    return True


def _has_structured_table_cues(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:nutrition\s+information|supplement\s+facts|quantity\s+per\s+serving|%\s*rda|nrv|tagesdosis|inhaltsstoffe)\b",
            text or "",
            re.I,
        )
    )


def _prepare_text_for_structured_parsing(input_text: str) -> str:
    text = str(input_text or "")
    if not text.strip():
        return ""
    if not _has_structured_table_cues(text):
        return text

    lines = [str(x or "").strip() for x in text.splitlines() if str(x or "").strip()]
    if not lines:
        return text

    start_idx = 0
    for i, line in enumerate(lines):
        if re.search(r"\b(?:nutrition\s+information|supplement\s+facts|quantity\s+per\s+serving|tagesdosis|inhaltsstoffe)\b", line, re.I):
            start_idx = i
            break

    hard_stop_markers = re.compile(
        r"(?:^\s*ingredients\s*[:\-]|\bingredients\s+full\s+list\b)",
        re.I,
    )
    soft_skip_markers = re.compile(
        r"(?:\brecommended\s+usage\b|\busage\s+level\b|\bprocessed\s+in\s+a\s+plant\b|\bvisit\b|www\.|\bmanufactured\b|\bins\s*\d{2,4}\b)",
        re.I,
    )
    row_hint = re.compile(
        r"\b(?:vit(?:amin|main)|biotin|folic|folate|iodine|l[o0]dine|selenium|chromium|molybdenum|zinc|iron|copper|manganese|magnesium|calcium|potassium|phosphorus|boron|fluoride|fluorine|cesium|l-arginine|l-methionine|l-lysine|green\s+tea\s+extract|beta-?carotene|lutein|lycopene|alpha\s+lipoic\s+acid|inositol|choline|paba|para-?aminobenzoic\s+acid|amino\s+blend|enzyme\s+blend|phyto\s+blend|viri\s+blend|amino\s+acids|botanicals)\b",
        re.I,
    )
    # "meg" is a common OCR misread of "mcg" — include it so those lines are selected.
    dose_hint = re.compile(r"\b\d+(?:[\.,]\d+)?\s*(?:mg|mcg|meg|ug|µg|μg|fg|g|iu|ui|ie|kcal)\b", re.I)

    selected: list[str] = []
    selected_dose_rows = 0
    for line in lines[start_idx:]:
        if hard_stop_markers.search(line):
            # Do not stop scanning: OCR often interleaves ingredients/prose and nutrition
            # rows out of order on multi-column labels.
            continue
        # --- Per-line OCR pre-cleaning (before soft_skip check) ---
        # Specific artifact: multi-column OCR reads "L-Methionine 10 mg" as "LMethionne Og".
        line = re.sub(r"\bL[\s\-]?Methion\w*\s+Og\b", "l-methionine 10 mg", line, flags=re.I)
        # Strip ingredient-list contamination that gets appended to dose rows in multi-column
        # OCR: "Name dose, IngredientWord ..." → "Name dose".  Only truncate when the prefix
        # already contains a letter + digit (i.e., a dose value), so pure ingredient lines
        # are left untouched and filtered normally.
        _m_comma = re.search(r",\s+[A-Z][a-z]{2,}", line)
        if _m_comma:
            _prefix = line[: _m_comma.start()]
            if re.search(r"[a-zA-Z]\s+\d", _prefix):
                line = _prefix
        if soft_skip_markers.search(line):
            continue
        if len(line) > 110 and not dose_hint.search(line):
            continue
        if row_hint.search(line) or dose_hint.search(line) or re.search(r"\b(?:nutrition\s+information|quantity\s+per\s+serving|%\s*rda|nrv|tagesdosis|inhaltsstoffe)\b", line, re.I):
            selected.append(line)
            if dose_hint.search(line):
                selected_dose_rows += 1

    # Safety fallback: if filtering became too strict and kept too little dose structure,
    # return original OCR text so rule-based parsing still has full context.
    if selected and selected_dose_rows >= 3:
        return "\n".join(selected)
    return text


def _component_prefers_microgram_unit(component: str) -> bool:
    key = normalize_lookup_key(component)
    if not key:
        return False
    if key in OCR_MICROGRAM_COMPONENTS:
        return True
    return bool(re.match(r"^vitamin\s+[adk](?:\d{1,2})?$", key))


def _repair_ocr_dose_entry(component: str, dose_value: float | None, dose_unit: str) -> tuple[str, float | None, str]:
    repaired_component = _repair_ocr_component_name(component)
    repaired_unit = _normalize_component_unit_token(dose_unit)

    if dose_value is None:
        return repaired_component, None, repaired_unit

    repaired_value = float(dose_value)

    if repaired_unit == "g" and repaired_value <= 5000 and _component_prefers_microgram_unit(repaired_component):
        # µg OCR artifacts: trailing symbol can leak into the numeric token as 1 or 4,
        # e.g. "20 µg" → "201g" or "204g". For these high, integer-like values,
        # strip one trailing artifact digit before mapping bare "g" to "mcg".
        # Threshold ≥ 100 prevents truncating small legitimate values (e.g., 54 µg).
        v_int = round(repaired_value)
        if v_int >= 100 and (v_int % 10 in {1, 4}) and abs(repaired_value - v_int) < 0.5:
            repaired_value = float(v_int // 10)
        return repaired_component, repaired_value, "mcg"

    # OCR can misread mcg as mg for trace micronutrients (e.g., folate 600 mcg
    # read as 600 mg). For known microgram-oriented nutrients, large mg values
    # are far more likely to be mcg.
    if repaired_unit == "mg" and _component_prefers_microgram_unit(repaired_component) and repaired_value >= 100:
        return repaired_component, repaired_value, "mcg"

    return repaired_component, repaired_value, repaired_unit


def _extract_vitamin_dose_candidates_from_text(input_text: str) -> dict[str, tuple[float, str]]:
    """Extract vitamin dose anchors from OCR text using targeted regex patterns."""
    anchors: dict[str, tuple[float, str]] = {}
    text = str(input_text or "")
    if not text.strip():
        return anchors

    for line in text.splitlines():
        raw_line = str(line or "").strip()
        if not raw_line:
            continue

        for match in OCR_VITAMIN_INLINE_DOSE_PATTERN.finditer(raw_line):
            letter = str(match.group("letter") or "").lower()
            suffix = str(match.group("suffix") or "").strip()
            if letter not in {"a", "b", "c", "d", "e", "k"}:
                continue
            if suffix and letter not in {"b", "d", "k"}:
                suffix = ""
            component = _repair_ocr_component_name(f"vitamin {letter}{suffix}")
            if not component:
                continue
            value = _parse_ocr_numeric_value(str(match.group("val") or ""))
            unit = _normalize_component_unit_token(str(match.group("unit") or ""))
            component, value, unit = _repair_ocr_dose_entry(component, value, unit)
            if value is None or not unit:
                continue
            anchors[normalize_lookup_key(component)] = (float(value), str(unit))

    return anchors


def _apply_contextual_vitamin_dose_corrections(
    rows: list[dict[str, Any]],
    source_text: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Use regex-extracted OCR anchors to correct obviously mismatched vitamin doses."""
    anchors = _extract_vitamin_dose_candidates_from_text(source_text)
    if not anchors:
        return rows, []

    corrected: list[dict[str, Any]] = []
    warnings: list[str] = []
    for row in rows:
        out = dict(row)
        comp = normalize_lookup_key(str(out.get("component", "") or ""))
        anchor = anchors.get(comp)
        if not anchor:
            corrected.append(out)
            continue

        try:
            cur_val = float(out.get("dose_value")) if out.get("dose_value") is not None else None
        except Exception:
            cur_val = None
        cur_unit = _normalize_component_unit_token(str(out.get("dose_unit", "") or ""))
        anc_val, anc_unit = anchor

        if cur_val is None or not cur_unit:
            out["dose_value"] = anc_val
            out["dose_unit"] = anc_unit
            warnings.append(f"context_correction: filled missing dose for {comp} from OCR anchor")
            corrected.append(out)
            continue

        if cur_unit != anc_unit:
            corrected.append(out)
            continue

        larger = max(cur_val, anc_val)
        smaller = max(1e-9, min(cur_val, anc_val))
        ratio = larger / smaller
        # Correct only clear mismatches to avoid overfitting.
        if ratio >= 1.5:
            out["dose_value"] = anc_val
            warnings.append(
                f"context_correction: replaced {comp} {format_float(cur_val)} {cur_unit} with "
                f"{format_float(anc_val)} {anc_unit}"
            )
        corrected.append(out)

    return corrected, warnings


def _recover_missing_vitamin_rows_from_text(
    input_text: str,
    existing_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    allowed_letters = {"a", "b", "c", "d", "e", "k"}
    existing_components = {
        normalize_lookup_key(str(row.get("component", "") or ""))
        for row in existing_rows
    }
    recovered: list[dict[str, Any]] = []

    for raw_line in input_text.splitlines():
        line = str(raw_line or "").strip()
        if not line:
            continue
        line = re.split(r"\b(?:other\s+ingredients|ingredients)\b", line, maxsplit=1, flags=re.I)[0].strip()
        if not line:
            continue

        for match in OCR_VITAMIN_TOKEN_PATTERN.finditer(line):
            letter = str(match.group(1) or "").lower()
            suffix = str(match.group(2) or "").strip()
            if letter not in allowed_letters:
                continue

            # Numeric vitamin subtypes are valid mainly for B/D/K families.
            if suffix and letter not in {"b", "d", "k"}:
                suffix = ""

            component = f"vitamin {letter}{suffix}".strip()
            component = _repair_ocr_component_name(component)
            if not component:
                continue

            normalized_component = normalize_lookup_key(component)
            if not normalized_component or normalized_component in existing_components:
                continue

            dose_value: float | None = None
            dose_unit = ""
            right_window = line[match.end():match.end() + 30]
            left_window = line[max(0, match.start() - 20):match.start()]
            dose_match = OCR_DOSE_TOKEN_PATTERN.search(right_window) or OCR_DOSE_TOKEN_PATTERN.search(left_window)
            if dose_match:
                raw_value = str(dose_match.group("val") or "")
                dose_value = _parse_ocr_numeric_value(raw_value)
                dose_unit = str(dose_match.group("unit") or "")

            component, dose_value, dose_unit = _repair_ocr_dose_entry(component, dose_value, dose_unit)
            recovered.append(
                {
                    "component": component,
                    "dose_value": dose_value,
                    "dose_unit": dose_unit,
                }
            )
            existing_components.add(normalized_component)

        # Recovery path for OCR lines that keep subtype token (e.g., K2, D3)
        # but lose the leading word "Vitamin".
        for short_match in OCR_VITAMIN_SHORTHAND_PATTERN.finditer(line):
            letter = str(short_match.group(1) or "").lower()
            suffix = str(short_match.group(2) or "").strip()
            if not suffix:
                continue

            component = _repair_ocr_component_name(f"vitamin {letter}{suffix}")
            normalized_component = normalize_lookup_key(component)
            if not normalized_component or normalized_component in existing_components:
                continue

            dose_value: float | None = None
            dose_unit = ""
            right_window = line[short_match.end():short_match.end() + 24]
            left_window = line[max(0, short_match.start() - 20):short_match.start()]
            dose_match = OCR_DOSE_TOKEN_PATTERN.search(right_window) or OCR_DOSE_TOKEN_PATTERN.search(left_window)
            if dose_match:
                raw_value = str(dose_match.group("val") or "")
                dose_value = _parse_ocr_numeric_value(raw_value)
                dose_unit = str(dose_match.group("unit") or "")

            component, dose_value, dose_unit = _repair_ocr_dose_entry(component, dose_value, dose_unit)
            recovered.append(
                {
                    "component": component,
                    "dose_value": dose_value,
                    "dose_unit": dose_unit,
                }
            )
            existing_components.add(normalized_component)

    return recovered


STRUCTURED_CORE_COMPONENT_GROUPS: set[str] = {
    "vitamin a",
    "vitamin c",
    "vitamin d",
    "vitamin e",
    "vitamin k_family",
    "vitamin b1",
    "vitamin b2",
    "vitamin b3",
    "vitamin b5",
    "vitamin b6",
    "biotin",
    "folate_family",
    "vitamin b12",
    "calcium",
    "phosphorus",
    "potassium",
    "magnesium",
    "iron",
    "copper",
    "manganese",
    "boron",
    "fluoride",
    "cesium",
    "iodine",
    "chromium",
    "selenium",
    "molybdenum",
    "zinc",
}

STRUCTURED_CORE_RECOVERY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("vitamin a", re.compile(r"\bvit(?:amin|main)?\s*a\b", re.I)),
    ("vitamin c", re.compile(r"\b(?:vit(?:amin|main)?\s*c|ascorbic\s+acid)\b", re.I)),
    ("vitamin d", re.compile(r"\bvit(?:amin|main)?\s*d(?:\d)?\b", re.I)),
    ("vitamin e", re.compile(r"\bvit(?:amin|main)?\s*e\b", re.I)),
    ("vitamin k1", re.compile(r"\bvit(?:amin|main)?\s*k1\b|\bvit(?:amin|main)?\s*k\b", re.I)),
    ("vitamin b1", re.compile(r"\b(?:vit(?:amin|main)?\s*b1|thiamin(?:e)?)\b", re.I)),
    ("vitamin b2", re.compile(r"\b(?:vit(?:amin|main)?\s*b2|riboflavin)\b", re.I)),
    ("vitamin b3", re.compile(r"\b(?:vit(?:amin|main)?\s*b3|niacin)\b", re.I)),
    ("vitamin b5", re.compile(r"\b(?:vit(?:amin|main)?\s*b5|pantothenic\s+acid)\b", re.I)),
    ("vitamin b6", re.compile(r"\b(?:vit(?:amin|main)?\s*b6|pyridoxine)\b", re.I)),
    ("biotin", re.compile(r"\b(?:vit(?:amin|main)?\s*b7|biotin)\b", re.I)),
    ("folic acid", re.compile(r"\b(?:folic\s+acid|folate|vit(?:amin|main)?\s*b9)\b", re.I)),
    ("vitamin b12", re.compile(r"\b(?:vit(?:amin|main)?\s*b12|cobalamin)\b", re.I)),
    ("calcium", re.compile(r"\bcalcium\b", re.I)),
    ("phosphorus", re.compile(r"\bphosph(?:or(?:us|ous)|orous|0rous|0rus)\b", re.I)),
    ("potassium", re.compile(r"\bpotass(?:ium|um|lum)\b", re.I)),
    ("magnesium", re.compile(r"\bmagnes(?:ium|lum|iurn)\b", re.I)),
    ("iron", re.compile(r"\b(?:iron|ion)\b", re.I)),
    ("copper", re.compile(r"\b(?:copper|coper|copp?r|cupr(?:ic)?)\b", re.I)),
    ("manganese", re.compile(r"\bmanganese\b", re.I)),
    ("boron", re.compile(r"\bboron\b", re.I)),
    ("fluoride", re.compile(r"\b(?:fluoride|fluorine)\b", re.I)),
    ("cesium", re.compile(r"\b(?:cesium|caesium)\b", re.I)),
    ("iodine", re.compile(r"\b(?:iodine|jodine|l[o0]dine)\b", re.I)),
    ("chromium", re.compile(r"\bchromium\b", re.I)),
    ("selenium", re.compile(r"\bselen(?:ium|iurn)\b", re.I)),
    ("molybdenum", re.compile(r"\bmolybdenum\b", re.I)),
    ("zinc", re.compile(r"\bzinc\b", re.I)),
]

STRUCTURED_MG_REPEAT_SUSPICIOUS_GROUPS: set[str] = {
    "calcium",
    "magnesium",
    "zinc",
    "iron",
    "phosphorus",
    "potassium",
}


def _is_suspicious_structured_group_dose(group_key: str, dose_value: float | None, dose_unit: str, repeated_count: int) -> bool:
    if dose_value is None:
        return False
    unit = _normalize_component_unit_token(dose_unit)
    value = float(dose_value)
    if value <= 0:
        return True
    if unit == "mg" and group_key in STRUCTURED_MG_REPEAT_SUSPICIOUS_GROUPS and repeated_count >= 3 and value <= 5:
        return True
    if group_key == "zinc" and unit == "mg" and value < 5:
        return True
    if group_key == "zinc" and unit == "mcg" and value >= 100:
        return True
    if group_key == "iron" and unit == "mg" and value >= 12 and abs((value * 10.0) - round(value * 10.0)) < 1e-9 and abs((value % 1.0) - 0.5) < 1e-9:
        return True
    if group_key == "vitamin d" and unit == "mcg" and value > 50:
        return True
    if group_key in {"vitamin k_family", "vitamin k"} and unit == "mg":
        return True
    if group_key in {"vitamin k_family", "vitamin k"} and unit == "iu" and value >= 100:
        return True
    if group_key == "vitamin e" and unit == "mcg":
        return True
    if group_key == "vitamin e" and unit == "iu" and value >= 250:
        return True
    if group_key == "biotin" and unit == "mcg" and value > 300:
        return True
    if group_key == "vitamin b6" and unit == "mg" and value > 5:
        return True
    return False


def _recover_core_micronutrient_rows_from_text(
    input_text: str,
    existing_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    recovered: list[dict[str, Any]] = []
    seen: set[tuple[str, float | None, str]] = set()
    existing_group_keys: set[str] = set()
    suspicious_existing_group_keys: set[str] = set()

    dose_bucket_counts: dict[tuple[float, str], int] = {}
    for row in existing_rows or []:
        try:
            dv = row.get("dose_value")
            if dv is None:
                continue
            dose_value = round(float(dv), 2)
        except Exception:
            continue
        dose_unit = _normalize_component_unit_token(str(row.get("dose_unit", "") or ""))
        if not dose_unit:
            continue
        key = (dose_value, dose_unit)
        dose_bucket_counts[key] = int(dose_bucket_counts.get(key, 0)) + 1

    for row in existing_rows or []:
        group_key = _structured_component_group_key(str(row.get("component", "") or ""))
        if group_key:
            existing_group_keys.add(group_key)
            dose_value_raw = row.get("dose_value")
            try:
                dose_value = float(dose_value_raw) if dose_value_raw is not None else None
            except Exception:
                dose_value = None
            dose_unit = _normalize_component_unit_token(str(row.get("dose_unit", "") or ""))
            repeated_count = 0
            if dose_value is not None and dose_unit:
                repeated_count = int(dose_bucket_counts.get((round(float(dose_value), 2), dose_unit), 0))
            if _is_suspicious_structured_group_dose(group_key, dose_value, dose_unit, repeated_count):
                suspicious_existing_group_keys.add(group_key)

    lines = [str(raw_line or "").strip() for raw_line in input_text.splitlines()]
    for idx, line in enumerate(lines):
        if not line:
            continue
        if len(line) > 6000:
            continue
        line = re.split(r"\b(?:other\s+ingredients|ingredients)\b", line, maxsplit=1, flags=re.I)[0].strip()
        if not line:
            continue
        if re.search(r"\bdaily\s+value\s+not\s+established\b", line, re.I):
            continue
        if re.search(r"\bserving\s+size\b", line, re.I):
            if len(OCR_DOSE_TOKEN_PATTERN.findall(line)) <= 2:
                continue

        candidate_line = line
        if idx + 1 < len(lines):
            next_line = str(lines[idx + 1] or "").strip()
            if next_line and len(next_line) <= 90 and re.search(r"\b\d+(?:[\.,]\d+)?\s*(?:mg|mcg|meg|ug|µg|μg|fg|iu|g)\b", next_line, re.I):
                candidate_line = f"{line} {next_line}".strip()

        for canonical_component, pattern in STRUCTURED_CORE_RECOVERY_PATTERNS:
            target_group_key = _structured_component_group_key(canonical_component)
            if (
                target_group_key
                and target_group_key in existing_group_keys
                and target_group_key not in suspicious_existing_group_keys
            ):
                continue
            for match in pattern.finditer(candidate_line):
                right_window = candidate_line[match.end():match.end() + 72]
                left_window = candidate_line[max(0, match.start() - 18):match.start()]
                dose_match = OCR_DOSE_TOKEN_PATTERN.search(right_window) or OCR_DOSE_TOKEN_PATTERN.search(left_window)
                if not dose_match:
                    continue
                raw_value = str(dose_match.group("val") or "").replace("O", "0").replace("o", "0")
                dose_value = _parse_ocr_numeric_value(raw_value)
                if dose_value is None:
                    continue
                dose_unit = str(dose_match.group("unit") or "")
                component, dose_value, dose_unit = _repair_ocr_dose_entry(canonical_component, dose_value, dose_unit)
                if dose_value is None or not dose_unit:
                    continue
                if dose_value <= 0:
                    continue
                key = (component, dose_value, dose_unit)
                if key in seen:
                    continue
                seen.add(key)
                recovered.append(
                    {
                        "component": component,
                        "dose_value": dose_value,
                        "dose_unit": dose_unit,
                        "_structured_recovery_score": 2,
                    }
                )
    return recovered


ECOMMERCE_NOISE_PATTERN = re.compile(
    r"\b(?:reviews?|regular\s+price|sale\s+price|mrp|inclusive\s+of\s+all\s+taxes|unit\s+price|buy\s+now|add\s+to\s+cart|wishlist|in\s+stock|out\s+of\s+stock|kg|lbs?)\b",
    re.I,
)
ECOMMERCE_COMPONENT_REJECTION_PATTERN = re.compile(
    r"\b(?:reviews?|regular\s+price|sale\s+price|mrp|inclusive|unit\s+price|taxes|pre-workout|collagen|powder|standard)\b",
    re.I,
)


def _looks_like_ecommerce_noise(text: str) -> bool:
    normalized = normalize_lookup_key(text)
    if not normalized:
        return False
    if ECOMMERCE_NOISE_PATTERN.search(normalized):
        return True
    digit_count = sum(1 for ch in normalized if ch.isdigit())
    if digit_count >= 6:
        return True
    if len(normalized.split()) >= 8 and digit_count >= 3:
        return True
    return False


def _is_valid_component_candidate(component: str) -> bool:
    normalized = normalize_component_name(component)
    if not normalized:
        return False
    if _looks_like_ecommerce_noise(normalized):
        return False
    if ECOMMERCE_COMPONENT_REJECTION_PATTERN.search(normalized):
        return False
    if len(normalized) < 3:
        return False
    if len(normalized.split()) > 6:
        return False
    return bool(re.search(r"[a-z]", normalized))


def _looks_like_nutrient_component(component: str) -> bool:
    c = normalize_lookup_key(component)
    if not c:
        return False
    if _looks_like_ecommerce_noise(c):
        return False

    nutrient_hints = [
        "vitamin",
        "mineral",
        "magnesium",
        "calcium",
        "zinc",
        "iron",
        "selenium",
        "iodine",
        "potassium",
        "sodium",
        "folate",
        "folic acid",
        "niacin",
        "riboflavin",
        "thiamin",
        "thiamine",
        "biotin",
        "pantothenic",
        "cobalamin",
        "choline",
        "omega",
        "epa",
        "dha",
        "b complex",
        "vitamin b",
    ]
    return any(h in c for h in nutrient_hints)


def parse_components_from_ingredient_list(input_text: str) -> list[dict[str, Any]]:
    """
    Extract nutrient components from long comma-separated ingredient lists.
    Handles cases like: "L-Ascorbic Acid, Magnesium Oxide, Ferrous fumarate, ..."
    """
    if not input_text.strip():
        return []
    
    # Nutrient/vitamin/mineral name patterns
    nutrient_patterns = [
        # Direct vitamin names
        r'\b(vitamin\s*[a-k]\d*(?:\s*[-/]\s*\w+)?)\b',
        r'\b(beta\s*carotene|lycopene|lutein)\b',
        r'\b(retinyl\s*acetate|retinol)\b',
        r'\b(ergocalciferol|cholecalciferol)\b',
        r'\b(tocopherol|tocopheryl)\b',
        r'\b(phytomenadione|phylloquinone|menaquinone)\b',
        r'\b(thiamine?|thiamin)\b',
        r'\b(riboflavin)\b',
        r'\b(niacin|nicotinamide|nicotinic\s*acid)\b',
        r'\b(pantothenic\s*acid|pantothenate|d-pantothenate)\b',
        r'\b(pyridoxine|pyridoxal)\b',
        r'\b(biotin|d-biotin)\b',
        r'\b(folic\s*acid|folate|pteroyl.*glutamic)\b',
        r'\b(cobalamin|cyanocobalamin|methylcobalamin)\b',
        r'\b(ascorbic\s*acid)\b',
        r'\b(choline)\b',
        # Minerals with compounds (more specific to avoid false matches)
        r'\b((?:di)?calcium)\s+(?:carbonate|phosphate|citrate|d-pantothenate)\b',
        r'\b(magnesium)\s+(?:oxide|citrate|chloride|sulfate|sulphate)\b',
        r'\b(iron|ferrous)\s+(?:fumarate|sulfate|sulphate|gluconate|bisglycinate)\b',
        r'\b(zinc)\s+(?:oxide|citrate|gluconate|picolinate)\b',
        r'\b(copper|cupric)\s+(?:oxide|sulfate|sulphate|gluconate)\b',
        r'\b(manganese)\s+(?:sulfate|sulphate|gluconate)\b',
        r'\b(sodium\s+(?:selenate|molybdate|borate))\b',
        r'\b(selenium|selenomethionine)\b',
        r'\b(chromium)(?:\s+(?:picolinate|chloride|trichloride))?\b',
        r'\b(molybdenum)\b',
        r'\b(potassium)\s+(?:chloride|citrate|iodide)\b',
        r'\b(iodine)\b',
        r'\b(boron)\b',
        r'\b(phosphorus|phosphate)\b',
        # Amino acids
        r'\b(l-arginine|arginine)\b',
        r'\b(l-lysine|lysine)\b',
        r'\b(l-methionine|methionine)\b',
        r'\b(l-leucine|leucine)\b',
        r'\b(l-isoleucine|isoleucine)\b',
        r'\b(l-valine|valine)\b',
        r'\b(l-glutamine|glutamine)\b',
        r'\b(l-carnitine|carnitine)\b',
        r'\b(l-taurine|taurine)\b',
        r'\b(l-cysteine|cysteine)\b',
    ]
    
    # Compile all patterns
    combined_pattern = '|'.join(f'(?:{p})' for p in nutrient_patterns)
    pattern = re.compile(combined_pattern, re.IGNORECASE)
    
    parsed: list[dict[str, Any]] = []
    seen: set[str] = set()
    
    # Split by common separators (commas, semicolons, OR newlines)
    # Handle both comma-separated and bullet-point formats
    items = re.split(r'[,;\n]\s*', input_text)
    
    for item in items:
        item = item.strip()
        # Strip bullet markers (-, •, *, number., etc.)
        item = re.sub(r'^[-•*\d]+[\.\)]\s*', '', item).strip()
        
        if len(item) < 3 or len(item) > 150:
            continue
        if _looks_like_ecommerce_noise(item):
            continue
            
        # Try to find nutrient pattern
        match = pattern.search(item)
        if match:
            # Extract the matched nutrient name
            matched_text = match.group(0)
            component = normalize_component_name(matched_text)
            
            if not _is_valid_component_candidate(component):
                continue
                
            # Avoid duplicates
            if component in seen:
                continue
            seen.add(component)
            
            parsed.append({
                "component": component,
                "dose_value": None,
                "dose_unit": "",
            })
    
    if parsed:
        logger.info(f"Extracted {len(parsed)} nutrients from ingredient list")
    return parsed


def parse_components_rule_based(input_text: str) -> list[dict[str, Any]]:
    if not input_text.strip():
        return []

    lines = [ln.strip() for ln in input_text.splitlines() if ln.strip()]
    dose_pattern = re.compile(
        r"(?P<val>(?:\d|[lI|])[0-9oO]*(?:[\.,][0-9oO]+)?)\s*(?P<unit>mg|mcg|meg|ug|µg|μg|fg|iu|ui|ie|g|kcal)\b",
        re.I,
    )
    nutrient_line_pattern = re.compile(
        r"\b(vit(?:amin|main)|minerals?|magnesium|calcium|zinc|iron|selenium|iodine|potassium|sodium|folate|folic|niacin|riboflavin|thiamin|thiamine|biotin|pantothenic|cobalamin|omega|epa|dha|b\d{1,2})\b",
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
        "product weight",
        "net weight",
        "total weight",
        "weight",
    )

    parsed: list[dict[str, Any]] = []
    seen: set[tuple[str, float, str]] = set()
    pending_component_from_previous_line = ""

    def _looks_like_component_candidate(text: str) -> bool:
        candidate = _repair_ocr_component_name(text)
        if not candidate:
            return False
        if _looks_like_nutrient_component(candidate):
            return True
        return bool(
            re.search(
                r"\b(vit(?:amin|main)|mineral|b\d{1,2}|folic|folate|niacin|riboflavin|thiamin|biotin|iodine|selenium|zinc|iron|magnesium|calcium|potassium|sodium|choline|inositol|lutein|lycopene|alpha\s+lipoic|paba|amino\s+blend|enzyme\s+blend|phyto\s+blend|viri\s+blend)\b",
                candidate,
                re.I,
            )
        )

    def _extract_component_from_segment(segment_text: str, match_start: int) -> str:
        name_raw = segment_text[:match_start]
        name_raw = re.sub(r"[\.:_\-]{2,}", " ", name_raw)
        name_raw = re.sub(r"\b\d+(?:[\.,]\d+)?\s*%\s*(?:dv|nrv|ri|we)?\b", " ", name_raw, flags=re.I)
        name_raw = re.sub(r"\s+", " ", name_raw).strip(" -:|,*#_")
        return _repair_ocr_component_name(name_raw)

    for line in lines:
        lowered = line.lower()
        line_for_parse = line
        if lowered.startswith(ignored_starts):
            # Do not drop dense inline supplement-facts rows just because they start
            # with header labels such as "Supplement Facts" or "Serving Size".
            if not (dose_pattern.search(line_for_parse) and nutrient_line_pattern.search(line_for_parse)):
                continue
            first_nutrient = nutrient_line_pattern.search(line_for_parse)
            if first_nutrient:
                line_for_parse = line_for_parse[first_nutrient.start():].strip()
            if not line_for_parse:
                continue
        if _looks_like_ecommerce_noise(line_for_parse) and not (
            dose_pattern.search(line_for_parse) and nutrient_line_pattern.search(line_for_parse)
        ):
            continue

        # Split list-style lines by separators that usually delimit components,
        # while preserving decimal commas (e.g., 1,5 mg).
        segments = re.split(r"\s*[;|]\s*|\s*,\s*(?=[a-zA-Z])", line_for_parse)
        for seg in segments:
            segment = seg.strip()
            if not segment:
                continue

            matches = list(dose_pattern.finditer(segment))
            if not matches:
                if _looks_like_component_candidate(segment):
                    pending_component_from_previous_line = _repair_ocr_component_name(segment)
                continue

            previous_match_end = 0
            for match in matches:
                # When OCR collapses many nutrients onto one line, bind each dose to the
                # nearest preceding text span instead of the full prefix from line start.
                local_prefix = segment[previous_match_end:match.start()]
                component = _extract_last_component_before_dose(local_prefix)
                if not component:
                    component = _extract_component_from_segment(local_prefix, len(local_prefix))
                if not component:
                    component = _extract_last_component_before_dose(segment[:match.start()])
                if not component:
                    component = _extract_component_from_segment(segment, match.start())
                if not component:
                    component = pending_component_from_previous_line
                if not component:
                    previous_match_end = match.end()
                    continue
                if not _is_valid_component_candidate(component):
                    previous_match_end = match.end()
                    continue

                # Skip metadata/packaging info that looks like doses
                metadata_keywords = {"weight", "size", "servings", "serving", "container", "pack", "tablets", "capsules"}
                component_words = set(component.lower().split())
                if component_words & metadata_keywords:
                    continue

                try:
                    dose_value = _parse_ocr_numeric_value(str(match.group("val") or ""))
                except Exception:
                    continue
                if dose_value is None:
                    continue

                dose_unit = match.group("unit").lower()
                component, dose_value, dose_unit = _repair_ocr_dose_entry(component, dose_value, dose_unit)
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

                previous_match_end = match.end()

            pending_component_from_previous_line = ""

    return parsed


def parse_components_name_only(input_text: str) -> list[dict[str, Any]]:
    if not input_text.strip():
        return []

    lines = [ln.strip() for ln in input_text.splitlines() if ln.strip()]
    ignored_starts = (
        "supplement facts",
        "serving size",
        "servings per container",
        "% daily value",
        "*percent daily values",
        "daily value not established",
        "proprietary blend",
        "other ingredients",
    )
    ignored_exact = {
        "ingredients",
        "nutrition facts",
        "amount per serving",
        "suggested use",
    }

    parsed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in lines:
        lowered = line.lower()
        if lowered.startswith(ignored_starts):
            continue
        if _looks_like_ecommerce_noise(line):
            continue

        # Skip dense ingredient-list style lines.
        if "," in line and len(line.split(",")) >= 3:
            continue
        if len(line) > 80:
            continue

        component = normalize_component_name(line)
        if not component or component in ignored_exact:
            continue
        if not _is_valid_component_candidate(component):
            continue
        if not _looks_like_nutrient_component(component):
            continue

        if component in seen:
            continue
        seen.add(component)
        parsed.append(
            {
                "component": component,
                "dose_value": None,
                "dose_unit": "",
            }
        )

    return parsed


def merge_component_rows(
    primary: list[dict[str, Any]],
    secondary: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected_by_component: dict[str, dict[str, Any]] = {}

    def _to_float(value: Any) -> float | None:
        try:
            return float(value) if value is not None else None
        except Exception:
            return None

    def _unit_priority(component: str, unit: str) -> int:
        normalized_unit = _normalize_component_unit_token(str(unit or ""))
        comp_key = normalize_lookup_key(component)
        if _component_prefers_microgram_unit(comp_key):
            if normalized_unit == "mcg":
                return 3
            if normalized_unit == "iu":
                return 2
            if normalized_unit == "mg":
                return 1
            return 0
        if normalized_unit == "mg":
            return 3
        if normalized_unit == "mcg":
            return 2
        if normalized_unit == "iu":
            return 1
        return 0

    def _choose_better(component: str, existing: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        existing_value = _to_float(existing.get("dose_value"))
        candidate_value = _to_float(candidate.get("dose_value"))

        existing_has_dose = existing_value is not None and bool(str(existing.get("dose_unit", "") or "").strip())
        candidate_has_dose = candidate_value is not None and bool(str(candidate.get("dose_unit", "") or "").strip())

        if candidate_has_dose and not existing_has_dose:
            return candidate
        if existing_has_dose and not candidate_has_dose:
            return existing
        if not existing_has_dose and not candidate_has_dose:
            return existing

        existing_unit = _normalize_component_unit_token(str(existing.get("dose_unit", "") or ""))
        candidate_unit = _normalize_component_unit_token(str(candidate.get("dose_unit", "") or ""))

        if existing_unit == candidate_unit:
            if (candidate_value or 0.0) > (existing_value or 0.0):
                return candidate
            return existing

        existing_unit_rank = _unit_priority(component, existing_unit)
        candidate_unit_rank = _unit_priority(component, candidate_unit)
        if candidate_unit_rank > existing_unit_rank:
            return candidate
        if existing_unit_rank > candidate_unit_rank:
            return existing

        # Final deterministic fallback: keep larger comparable dose if unit preference ties.
        if (candidate_value or 0.0) > (existing_value or 0.0):
            return candidate
        return existing

    for row in primary + secondary:
        component = normalize_lookup_key(str(row.get("component", "") or ""))
        if not component:
            continue

        candidate = {
            "component": component,
            "dose_value": row.get("dose_value"),
            "dose_unit": _normalize_component_unit_token(str(row.get("dose_unit", "") or "")),
        }

        existing = selected_by_component.get(component)
        if existing is None:
            selected_by_component[component] = candidate
            continue

        selected_by_component[component] = _choose_better(component, existing, candidate)

    merged = [selected_by_component[key] for key in selected_by_component.keys()]
    return merged


def expand_umbrella_components(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []

    b_complex_tokens = {
        "vitamin b complex",
        "vitamin b-complex",
        "b complex",
        "b-complex",
    }
    b_complex_expansions = [
        "vitamin b1",
        "vitamin b2",
        "vitamin b3",
        "vitamin b5",
        "vitamin b6",
        "vitamin b7",
        "vitamin b9",
        "vitamin b12",
    ]

    expanded: list[dict[str, Any]] = []
    seen_components: set[str] = set()

    def is_b_complex_component(component_name: str) -> bool:
        key = normalize_lookup_key(component_name)
        if not key:
            return False
        if key in b_complex_tokens:
            return True

        # OCR/typo tolerant checks, e.g. "vitmain b complex".
        compact = key.replace("-", " ")
        if re.search(r"\bb\s*complex\b", compact):
            if "vitamin" in compact or "vitmain" in compact or compact.startswith("b complex"):
                return True
        return False

    def append_row(component_name: str, dose_value: Any = None, dose_unit: str = "") -> None:
        key = normalize_lookup_key(component_name)
        if not key or key in seen_components:
            return
        seen_components.add(key)
        expanded.append(
            {
                "component": key,
                "dose_value": dose_value,
                "dose_unit": str(dose_unit or ""),
            }
        )

    for row in rows:
        component = normalize_lookup_key(str(row.get("component", "") or ""))
        dose_value = row.get("dose_value")
        dose_unit = str(row.get("dose_unit", "") or "")

        if is_b_complex_component(component):
            for name in b_complex_expansions:
                append_row(name, None, "")
            continue

        append_row(component, dose_value, dose_unit)

    return expanded




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
















@functools.lru_cache(maxsize=1)
def _get_blockbrain_models_catalog() -> list[dict[str, Any]]:
    """Fetch model catalog once per app process for model pickers."""
    api_key, base_url, _ = _load_blockbrain_secrets()
    if not api_key or not base_url:
        return []
    try:
        response = _http_get(
            f"{base_url}/v1/api/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=20,
        )
        if response.status_code != 200:
            return []
        payload = response.json() if response.content else {}
        items = payload.get("items", []) if isinstance(payload, dict) else []
        return [x for x in items if isinstance(x, dict)]
    except Exception:
        return []


def _list_blockbrain_chat_model_ids(*, vision_required: bool = False) -> list[str]:
    items = _get_blockbrain_models_catalog()
    out: list[str] = []
    for item in items:
        model_id = str(item.get("id", "") or "").strip()
        mode = str(item.get("mode", "") or "").strip().lower()
        supports_vision = bool(item.get("supportsVision", False))
        if not model_id:
            continue
        if mode not in {"chat", "responses"}:
            continue
        if vision_required and not supports_vision:
            continue
        out.append(model_id)
    # Keep stable ordering while removing duplicates.
    deduped: list[str] = []
    seen: set[str] = set()
    for model_id in out:
        key = model_id.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(model_id)
    return deduped


def _get_selected_blockbrain_models() -> tuple[str, str]:
    """Return selected text/vision model IDs from session state or defaults."""
    default_text_model, default_vision_model = _load_blockbrain_model_defaults()
    try:
        import streamlit as st
        text_model = str(st.session_state.get("analyze_blockbrain_text_model", default_text_model) or "").strip()
        vision_model = str(st.session_state.get("analyze_blockbrain_vision_model", default_vision_model) or "").strip()
        return text_model, vision_model
    except Exception:
        return default_text_model, default_vision_model


def _blockbrain_chat(payload: dict[str, Any]) -> str:
    """Send a request to Blockbrain and return the assistant text.

    Primary transport is the Blockbrain agent stream endpoint (v2 first, v1
    fallback). If BLOCKBRAIN_CHAT_ENDPOINT is set (for example an
    OpenAI-compatible /v1/chat/completions route), that is tried first.
    """
    global LAST_BLOCKBRAIN_ERROR
    global LAST_BLOCKBRAIN_MODEL
    LAST_BLOCKBRAIN_ERROR = ""
    LAST_BLOCKBRAIN_MODEL = ""
    api_key, base_url, agent_id = _load_blockbrain_secrets()
    if not api_key:
        LAST_BLOCKBRAIN_ERROR = "Blockbrain API key not configured"
        return ""
    if not base_url:
        LAST_BLOCKBRAIN_ERROR = "Blockbrain base URL not configured"
        return ""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    def _coerce_content_to_text(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, dict):
            for key in ("text", "content", "value"):
                value = content.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return ""
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    if item.strip():
                        parts.append(item.strip())
                    continue
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type", "") or "").strip().lower()
                if item_type == "text" and isinstance(item.get("text"), str):
                    text_part = item.get("text", "").strip()
                    if text_part:
                        parts.append(text_part)
                    continue
                nested_text = item.get("text")
                if isinstance(nested_text, dict):
                    value = nested_text.get("value")
                    if isinstance(value, str) and value.strip():
                        parts.append(value.strip())
            return "\n".join(parts).strip()
        return ""

    def _try_openai_chat(endpoint_path: str) -> tuple[bool, str]:
        """Try an OpenAI-compatible chat completions endpoint. Returns (handled, text)."""
        url = f"{base_url}{endpoint_path}"
        request_payload = dict(payload or {})
        request_payload["stream"] = False
        try:
            resp = _http_post(url, headers=headers, json=request_payload, timeout=HTTP_TIMEOUT)
        except Exception as exc:
            LAST_BLOCKBRAIN_ERROR = f"Blockbrain request error: {exc}"
            return False, ""
        if resp.status_code == 404:
            return False, ""  # endpoint not available; fall back to agent stream
        if resp.status_code != 200:
            LAST_BLOCKBRAIN_ERROR = f"Blockbrain HTTP {resp.status_code}: {resp.text[:200]}"
            return True, ""
        payload_json = resp.json() if resp.content else {}
        if isinstance(payload_json, dict):
            runtime_model = str(payload_json.get("model", "") or payload_json.get("resolved_model", "") or "").strip()
            if runtime_model:
                LAST_BLOCKBRAIN_MODEL = runtime_model
            choices = payload_json.get("choices", [])
            if isinstance(choices, list) and choices:
                message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
                content = message.get("content", "") if isinstance(message, dict) else ""
                text = _coerce_content_to_text(content)
                if text:
                    return True, text
            top_level = payload_json.get("output", payload_json.get("content", ""))
            text = _coerce_content_to_text(top_level)
            if text:
                return True, text
            for key in ("reply", "message", "response", "text"):
                text = _coerce_content_to_text(payload_json.get(key, ""))
                if text:
                    return True, text
        LAST_BLOCKBRAIN_ERROR = "Blockbrain response did not include assistant text"
        return True, ""

    def _collect_text_chunks(value: Any) -> list[str]:
        chunks: list[str] = []
        if isinstance(value, str):
            text = value.strip()
            if text and text != "[DONE]":
                chunks.append(text)
            return chunks
        if isinstance(value, list):
            for item in value:
                chunks.extend(_collect_text_chunks(item))
            return chunks
        if not isinstance(value, dict):
            return chunks

        # Common event-level fields across v1/v2 stream variants.
        for key in ("text", "delta", "content", "value"):
            raw = value.get(key)
            if isinstance(raw, str) and raw.strip():
                chunks.append(raw.strip())

        # OpenAI-style delta/messages payloads.
        choices = value.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                chunks.extend(_collect_text_chunks(choice.get("delta")))
                chunks.extend(_collect_text_chunks(choice.get("message")))

        # Recurse through nested structures where streaming payload text often lives.
        for nested_key in ("data", "payload", "message", "messages", "parts", "output"):
            nested = value.get(nested_key)
            if nested is not None:
                chunks.extend(_collect_text_chunks(nested))

        return chunks

    def _fast_stream_payload(src: dict[str, Any], endpoint_path: str) -> dict[str, Any]:
        out = dict(src or {})
        fast_mode = str(os.getenv("BLOCKBRAIN_FAST_STREAM", "1") or "1").strip().lower() not in {"0", "false", "off", "no"}
        if not fast_mode:
            return out
        # Only add these to v2 stream calls where they are expected.
        if "/v2/" in endpoint_path:
            out.setdefault("maxSteps", 1)
            out.setdefault("activeTools", [])
            out.setdefault("toolChoice", "none")
            out.setdefault("trigger", "submit-message")
        return out

    # Optional OpenAI-compatible endpoint override (tried first when configured).
    custom_endpoint = os.getenv("BLOCKBRAIN_CHAT_ENDPOINT", "").strip()
    if custom_endpoint:
        endpoint_path = custom_endpoint if custom_endpoint.startswith("/") else f"/{custom_endpoint}"
        handled, text = _try_openai_chat(endpoint_path)
        if handled:
            return text

    # Primary transport: Blockbrain agent stream endpoint (SSE), preferring v2.
    stream_endpoints = [
        f"{base_url}/v2/api/agents/{agent_id}/stream",
        f"{base_url}/v1/api/agents/{agent_id}/stream",
    ]
    last_error = ""
    for stream_url in stream_endpoints:
        endpoint_path = stream_url[len(base_url):] if stream_url.startswith(base_url) else stream_url
        try:
            resp = _http_post(
                stream_url,
                headers=headers,
                json=_fast_stream_payload(dict(payload or {}), endpoint_path),
                timeout=HTTP_TIMEOUT,
                stream=True,
            )
            if resp.status_code == 404:
                continue
            if resp.status_code != 200:
                last_error = f"Blockbrain HTTP {resp.status_code}: {resp.text[:200]}"
                continue

            text_parts: list[str] = []
            for raw_line in resp.iter_lines():
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else raw_line
                if not line.startswith("data:"):
                    continue
                json_str = line[len("data:"):].strip()
                if not json_str or json_str == "[DONE]":
                    continue
                try:
                    event = json.loads(json_str)
                except Exception:
                    continue

                if isinstance(event, dict):
                    runtime_model = str(event.get("model", "") or event.get("resolved_model", "") or "").strip()
                    if not runtime_model:
                        try:
                            runtime_model = str(
                                event.get("data", {})
                                .get("payload", {})
                                .get("request", {})
                                .get("body", {})
                                .get("model", "")
                                or ""
                            ).strip()
                        except Exception:
                            runtime_model = ""
                    if runtime_model:
                        LAST_BLOCKBRAIN_MODEL = runtime_model

                    chunks = _collect_text_chunks(event)
                    if chunks:
                        text_parts.extend(chunks)

                    event_type = str(event.get("type", "") or "").strip().lower()
                    if event_type in {"finish", "done", "response.completed", "response.done", "message.stop"}:
                        break

            merged = "\n".join([c for c in text_parts if str(c).strip()]).strip()
            if merged:
                return merged
        except Exception as exc:
            last_error = f"Blockbrain request error: {exc}"

    LAST_BLOCKBRAIN_ERROR = last_error or "Blockbrain response did not include assistant text"
    return ""



def call_blockbrain_text(system_prompt: str, user_prompt: str, model: str | None = None) -> str:
    """Send a text-only request to Blockbrain chat completions."""
    selected_text_model, _ = _get_selected_blockbrain_models()
    requested_model = str(model or selected_text_model or "").strip()
    payload = {
        "messages": [
            {"role": "system", "content": system_prompt.strip()},
            {"role": "user", "content": user_prompt.strip()},
        ],
    }
    if requested_model:
        payload["model"] = requested_model
    return _blockbrain_chat(payload)


def call_blockbrain_vision(image_bytes: bytes, model: str | None = None) -> str:
    """Send an image to Blockbrain vision model; returns extracted label text."""
    _, selected_vision_model = _get_selected_blockbrain_models()
    requested_model = str(model or selected_vision_model or "").strip()
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    vision_prompt = (
        "You are a strict OCR extractor for supplement and nutrition labels. "
        "Read all visible text from this label image exactly as printed. "
        "Preserve nutrient names, numeric doses, and units (mg, mcg, IU, g). "
        "Output plain text lines only — no markdown, no commentary."
    )
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": vision_prompt},
                    {"type": "image", "image": f"data:image/jpeg;base64,{b64}"},
                ],
            }
        ],
    }
    if requested_model:
        payload["model"] = requested_model
    out = _blockbrain_chat(payload)
    if out and not _is_blockbrain_image_missing_response(out):
        return out

    # Alternate multimodal shape for backends expecting OpenAI-style image_url.
    alt_payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": vision_prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                ],
            }
        ],
    }
    if requested_model:
        alt_payload["model"] = requested_model
    alt_out = _blockbrain_chat(alt_payload)
    if alt_out:
        return alt_out
    return out


def _is_blockbrain_image_missing_response(text: str) -> bool:
    raw = str(text or "").strip().lower()
    if not raw:
        return False
    markers = [
        "no image has been attached",
        "no file, image, or document has been attached",
        "no label image",
        "there is nothing for me to extract",
        "please attach",
        "i do not see any image",
    ]
    return any(marker in raw for marker in markers)


def call_text_llm(system_prompt: str, user_prompt: str, model: str | None = None) -> str:
    """Route all text LLM calls through Blockbrain chat completions."""
    global LAST_TEXT_PROVIDER
    LAST_TEXT_PROVIDER = ""

    bb_reply = call_blockbrain_text(system_prompt, user_prompt, model=model)
    if bb_reply:
        runtime_model = str(LAST_BLOCKBRAIN_MODEL or "").strip()
        if runtime_model:
            LAST_TEXT_PROVIDER = f"Blockbrain model ({runtime_model})"
        else:
            LAST_TEXT_PROVIDER = "Blockbrain model"
        return bb_reply

    return ""


# Backward-compat alias
call_openrouter_text = call_text_llm


# ---------------------------------------------------------------------------
# Stage 3 — Fuzzy nutrient dictionary (core of domain-specific post-processing)
# ---------------------------------------------------------------------------

# Full ~60-entry nutrition-label nutrient vocabulary.
_NUTRIENT_DICTIONARY: list[str] = [
    # Energy
    "energy", "calories",
    # Macros
    "protein", "fat", "total fat", "saturated fat", "saturated fatty acids",
    "trans fat", "trans fatty acids", "monounsaturated fat", "polyunsaturated fat",
    "carbohydrate", "total carbohydrate", "carbohydrates", "sugar", "total sugar",
    "added sugar", "dietary fiber", "fiber",
    # Minerals
    "sodium", "salt", "potassium", "calcium", "iron", "magnesium", "zinc",
    "phosphorus", "selenium", "iodine", "copper", "manganese", "chromium",
    "molybdenum", "fluoride", "chloride",
    # Vitamins
    "vitamin a", "vitamin c", "vitamin d", "vitamin d2", "vitamin d3", "vitamin e", "vitamin k",
    "vitamin k1", "vitamin k2",
    "vitamin b1", "thiamin", "thiamine",
    "vitamin b2", "riboflavin",
    "vitamin b3", "niacin",
    "vitamin b5", "pantothenic acid",
    "vitamin b6",
    "vitamin b7", "biotin",
    "vitamin b9", "folate", "folic acid",
    "vitamin b12",
    # Fatty acids / others
    "omega 3", "omega 6", "epa", "dha", "cholesterol",
    # Common supplement label terms
    "choline", "inositol", "taurine", "l-carnitine", "coenzyme q10", "lutein",
    "lycopene", "beta-carotene",
]
# Pre-compute lowercase version once.
_NUTRIENT_DICT_LOWER: list[str] = [n.lower() for n in _NUTRIENT_DICTIONARY]

# OCR-specific label corrections applied before fuzzy matching.
_OCR_LABEL_CORRECTIONS: dict[str, str] = {
    # protein variants
    "proteln": "protein", "protien": "protein", "proten": "protein",
    "proteín": "protein", "protelm": "protein",
    # carbohydrate variants
    "carbohydrat": "carbohydrate", "carbohydates": "carbohydrates",
    "carboh": "carbohydrate", "carbs": "carbohydrates",
    # fat variants
    "saturatd fat": "saturated fat", "saturatedfat": "saturated fat",
    # fiber
    "dietaryfiber": "dietary fiber", "dietry fiber": "dietary fiber",
    # sodium / salt
    "sodlum": "sodium", "sodiurn": "sodium",
    # vitamins
    "vltamin": "vitamin", "vlitamin": "vitamin", "vitarnin": "vitamin",
    "vit c": "vitamin c", "vit d": "vitamin d", "vit a": "vitamin a",
    "vit b6": "vitamin b6", "vit b12": "vitamin b12",
    "vit k1": "vitamin k1", "vit k2": "vitamin k2",
    # minerals
    "calclum": "calcium", "calcíum": "calcium",
    "magneslum": "magnesium", "magnesiurn": "magnesium",
    "phosphours": "phosphorus",
    "potassum": "potassium", "potasslum": "potassium",
    "lodine": "iodine", "lodlne": "iodine",
    "seleniurn": "selenium",
    "zincl": "zinc",
    # energy
    "enery": "energy", "eneray": "energy", "kcals": "calories",
    # sugar
    "sugars": "sugar", "suger": "sugar",
    # cholesterol
    "cholestrol": "cholesterol", "cholesteral": "cholesterol",
}

_OCR_LABEL_CORRECTIONS.update(
    {
        # Additional vitamin misspellings
        "vitanin": "vitamin",
        "vitmain": "vitamin",
        "vitmin": "vitamin",
        "vitamim": "vitamin",
        # Unit confusions
        "rng": "mg",
        "rncg": "mcg",
        "mq": "mg",
        "mcq": "mcg",
        # Mineral OCR confusions
        "rnagnesium": "magnesium",
        "calciurn": "calcium",
        "chromiurn": "chromium",
    }
)


def _fuzzy_match_nutrient(raw_name: str, cutoff: float = 0.78) -> str:
    """
    Given a raw OCR nutrient name, return the canonical nutrient name from the
    dictionary.  Steps:
    1. Apply direct OCR-correction lookup.
    2. Exact match against dictionary.
    3. difflib fuzzy match (very tolerant — 0.78 — to handle OCR noise).
    Returns the matched canonical name, or the normalized original if no match.
    """
    if not raw_name:
        return raw_name
    normalized = raw_name.strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)

    # Step 1: direct correction map.
    if normalized in _OCR_LABEL_CORRECTIONS:
        return _OCR_LABEL_CORRECTIONS[normalized]

    # Step 2: exact dictionary match.
    if normalized in _NUTRIENT_DICT_LOWER:
        return normalized

    # Step 3a: RapidFuzz (if installed) for stronger OCR-noise tolerance.
    try:
        from rapidfuzz import fuzz, process

        rf_match = process.extractOne(
            normalized,
            _NUTRIENT_DICT_LOWER,
            scorer=fuzz.WRatio,
            score_cutoff=max(0.0, min(100.0, float(cutoff) * 100.0)),
        )
        if rf_match:
            return str(rf_match[0])
    except Exception:
        pass

    # Step 3b: fallback fuzzy match.
    matches = difflib.get_close_matches(normalized, _NUTRIENT_DICT_LOWER, n=1, cutoff=cutoff)
    if matches:
        return matches[0]

    return normalized


def _apply_fuzzy_nutrient_correction_to_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Run fuzzy nutrient name correction on a list of parsed component rows.
    Only replaces the component name when the fuzzy match is confident.
    """
    corrected: list[dict[str, Any]] = []
    for row in rows:
        component = str(row.get("component", "") or "")
        matched = _fuzzy_match_nutrient(component)
        corrected.append({**row, "component": matched})
    return corrected


STRUCTURED_COMPONENT_GROUP_ALIASES: dict[str, str] = {
    "thiamin": "vitamin b1",
    "thiamine": "vitamin b1",
    "riboflavin": "vitamin b2",
    "niacin": "vitamin b3",
    "pantothenic acid": "vitamin b5",
    "folate": "folate_family",
    "folic acid": "folate_family",
    "vitamin b9": "folate_family",
    "vitamin d": "vitamin d_family",
    "vitamin d2": "vitamin d_family",
    "vitamin d3": "vitamin d_family",
    "vitamin k": "vitamin k_family",
    "vitamin k1": "vitamin k_family",
    "vitamin k2": "vitamin k_family",
}

STRUCTURED_COMPONENT_PREFERRED_NAME_RANK: dict[str, dict[str, int]] = {
    "folate_family": {
        "folic acid": 3,
        "folate": 2,
        "vitamin b9": 1,
    },
    "vitamin d_family": {
        "vitamin d3": 3,
        "vitamin d2": 2,
        "vitamin d": 1,
    },
    "vitamin k_family": {
        "vitamin k2": 4,
        "vitamin k1": 3,
        "vitamin k": 2,
    },
    "vitamin b1": {
        "vitamin b1": 3,
        "thiamin": 2,
        "thiamine": 2,
    },
    "vitamin b2": {
        "vitamin b2": 3,
        "riboflavin": 2,
    },
    "vitamin b3": {
        "vitamin b3": 3,
        "niacin": 2,
    },
    "vitamin b5": {
        "vitamin b5": 3,
        "pantothenic acid": 2,
    },
}

STRUCTURED_DECIMAL_SHIFT_MAX_MG: dict[str, float] = {
    "iron": 65.0,
}

STRUCTURED_NONCORE_KEEP_COMPONENTS: set[str] = {
    "alpha lipoic acid",
    "paba",
    "choline",
    "inositol",
    "silica",
    "lycopene",
    "lutein",
    "alpha carotene",
    "vanadium",
    "cryptoxanthin",
    "zeaxanthin",
    "amino blend",
    "enzyme blend",
    "phyto blend",
    "viri blend",
    "mct-oil",
}


def _structured_component_group_key(component: str) -> str:
    key = normalize_lookup_key(component)
    if not key:
        return ""
    return STRUCTURED_COMPONENT_GROUP_ALIASES.get(key, key)


def _component_looks_like_mineral_form_noise(component: str) -> bool:
    key = normalize_lookup_key(component)
    if not key:
        return False
    if key in OCR_MINERAL_FORM_COMPONENTS:
        return False
    return OCR_MINERAL_FORM_SUFFIX_PATTERN.match(key) is not None


def _normalize_structured_candidate_component(component: str) -> str:
    key = normalize_lookup_key(component)
    if not key:
        return ""
    mineral_form_match = OCR_MINERAL_FORM_SUFFIX_PATTERN.match(key)
    if mineral_form_match:
        return str(mineral_form_match.group("base") or "").lower()
    return key


def _structured_preferred_name_rank(group_key: str, component: str) -> int:
    ranking = STRUCTURED_COMPONENT_PREFERRED_NAME_RANK.get(group_key, {})
    return int(ranking.get(normalize_lookup_key(component), 0))


def _structured_unit_rank(group_key: str, dose_unit: str) -> int:
    unit = _normalize_component_unit_token(dose_unit)
    if not unit:
        return 0
    if group_key == "vitamin e":
        if unit == "iu":
            return 3
        if unit == "mg":
            return 2
        if unit == "mcg":
            return 1
        return 0
    if group_key in {"folate_family", "vitamin k_family"} or group_key in _MICROGRAM_PREFERRED_NUTRIENTS:
        if unit == "mcg":
            return 3
        if unit == "iu":
            return 2
        if unit == "mg":
            return 1
        return 0
    if unit == "mg":
        return 3
    if unit == "mcg":
        return 1
    return 0


def _apply_structured_decimal_shift_fix(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fixed: list[dict[str, Any]] = []
    for row in rows:
        updated = dict(row)
        component = _structured_component_group_key(str(updated.get("component", "") or ""))
        unit = _normalize_component_unit_token(str(updated.get("dose_unit", "") or ""))
        dose_raw = updated.get("dose_value")
        try:
            dose_value = float(dose_raw) if dose_raw is not None else None
        except Exception:
            dose_value = None
        max_expected = STRUCTURED_DECIMAL_SHIFT_MAX_MG.get(component)
        if dose_value is not None and unit == "mg" and max_expected is not None:
            if dose_value > max_expected and (dose_value / 10.0) <= max_expected:
                updated["dose_value"] = round(dose_value / 10.0, 4)
        fixed.append(updated)
    return fixed


def _collapse_structured_label_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []

    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        component = _normalize_structured_candidate_component(str(row.get("component", "") or ""))
        if not component:
            continue
        dose_raw = row.get("dose_value")
        try:
            dose_value = float(dose_raw) if dose_raw is not None else None
        except Exception:
            dose_value = None
        dose_unit = _normalize_component_unit_token(str(row.get("dose_unit", "") or ""))
        component, dose_value, dose_unit = _repair_ocr_dose_entry(component, dose_value, dose_unit)
        normalized_rows.append(
            {
                **row,
                "component": component,
                "dose_value": dose_value,
                "dose_unit": dose_unit,
            }
        )

    normalized_rows = _apply_structured_decimal_shift_fix(normalized_rows)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in normalized_rows:
        group_key = _structured_component_group_key(str(row.get("component", "") or ""))
        if not group_key:
            continue
        grouped.setdefault(group_key, []).append(row)

    collapsed: list[dict[str, Any]] = []
    for group_key, group_rows in grouped.items():
        def _dose_sort_value(candidate_row: dict[str, Any]) -> float:
            try:
                value = float(candidate_row.get("dose_value")) if candidate_row.get("dose_value") is not None else 0.0
            except Exception:
                value = 0.0
            unit = str(candidate_row.get("dose_unit", "") or "")
            if _is_suspicious_structured_group_dose(group_key, value, unit, 1):
                return -1.0
            return value

        decimal_shift_larger_ids: set[int] = set()
        for idx, candidate in enumerate(group_rows):
            cand_value = candidate.get("dose_value")
            cand_unit = str(candidate.get("dose_unit", "") or "")
            try:
                cand_num = float(cand_value) if cand_value is not None else None
            except Exception:
                cand_num = None
            if cand_num is None or cand_num <= 0:
                continue
            for other_idx, other in enumerate(group_rows):
                if idx == other_idx:
                    continue
                if normalize_lookup_key(str(other.get("component", "") or "")) != normalize_lookup_key(str(candidate.get("component", "") or "")):
                    continue
                if str(other.get("dose_unit", "") or "") != cand_unit:
                    continue
                try:
                    other_num = float(other.get("dose_value")) if other.get("dose_value") is not None else None
                except Exception:
                    other_num = None
                if other_num is None or other_num <= 0:
                    continue
                larger = max(cand_num, other_num)
                smaller = min(cand_num, other_num)
                if smaller > 0 and 9.5 <= (larger / smaller) <= 10.5 and cand_num == larger:
                    decimal_shift_larger_ids.add(idx)

        best = max(
            enumerate(group_rows),
            key=lambda item: (
                int(item[1].get("_structured_recovery_score", 0) or 0),
                0
                if _is_suspicious_structured_group_dose(
                    group_key,
                    (
                        float(item[1].get("dose_value"))
                        if item[1].get("dose_value") is not None
                        else None
                    ),
                    str(item[1].get("dose_unit", "") or ""),
                    1,
                )
                else 1,
                _structured_preferred_name_rank(group_key, str(item[1].get("component", "") or "")),
                _structured_unit_rank(group_key, str(item[1].get("dose_unit", "") or "")),
                _dose_sort_value(item[1]),
                0 if item[0] in decimal_shift_larger_ids else 1,
                0 if _component_looks_like_mineral_form_noise(str(item[1].get("component", "") or "")) else 1,
                0 if item[1].get("dose_value") is None else 1,
                -len(str(item[1].get("component", "") or "")),
            ),
        )[1]
        collapsed.append(best)

    collapsed.sort(key=lambda row: normalize_lookup_key(str(row.get("component", "") or "")))
    component_keys = {normalize_lookup_key(str(row.get("component", "") or "")) for row in collapsed}

    filtered: list[dict[str, Any]] = []
    for row in collapsed:
        component = normalize_lookup_key(str(row.get("component", "") or ""))
        if component == "beta-carotene" and "vitamin a" in component_keys:
            continue
        filtered.append(row)

    core_count = sum(
        1
        for row in filtered
        if _structured_component_group_key(str(row.get("component", "") or "")) in STRUCTURED_CORE_COMPONENT_GROUPS
    )
    if core_count >= 4:
        filtered = [
            row
            for row in filtered
            if (
                _structured_component_group_key(str(row.get("component", "") or "")) in STRUCTURED_CORE_COMPONENT_GROUPS
                or normalize_lookup_key(str(row.get("component", "") or "")) in STRUCTURED_NONCORE_KEEP_COMPONENTS
            )
        ]
    cleaned: list[dict[str, Any]] = []
    for row in filtered:
        cleaned.append({k: v for k, v in row.items() if not str(k).startswith("_")})
    return cleaned


# ---------------------------------------------------------------------------
# Stage 4 — Unit sanity + energy cross-check validation
# ---------------------------------------------------------------------------

# Expected unit domains per nutrient group.
_MACRO_NUTRIENTS: set[str] = {
    "protein", "fat", "total fat", "saturated fat", "trans fat",
    "monounsaturated fat", "polyunsaturated fat",
    "carbohydrate", "total carbohydrate", "carbohydrates",
    "sugar", "total sugar", "added sugar", "dietary fiber", "fiber",
    "cholesterol",
}
_MICROGRAM_PREFERRED_NUTRIENTS: set[str] = {
    "vitamin a", "vitamin d", "vitamin k", "vitamin b12",
    "biotin", "folate", "folic acid", "selenium", "iodine",
    "chromium", "molybdenum",
}


def _detect_dosage_outliers(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Detect simple statistical outliers for key vitamins (normalized to mcg)."""
    warnings: list[str] = []
    grouped: dict[str, list[float]] = {
        "vitamin d": [],
        "vitamin b12": [],
        "vitamin c": [],
        "folate": [],
    }

    for row in rows:
        component = normalize_lookup_key(str(row.get("component", "") or ""))
        dose_value = row.get("dose_value")
        dose_unit = _normalize_component_unit_token(str(row.get("dose_unit", "") or ""))
        if dose_value is None or dose_unit not in {"mg", "mcg"}:
            continue
        try:
            value = float(dose_value)
        except Exception:
            continue
        value_mcg = value * 1000.0 if dose_unit == "mg" else value

        if "vitamin d" in component:
            grouped["vitamin d"].append(value_mcg)
        elif "vitamin b12" in component or "cobalamin" in component:
            grouped["vitamin b12"].append(value_mcg)
        elif "vitamin c" in component or "ascorbic" in component:
            grouped["vitamin c"].append(value_mcg)
        elif "folate" in component or "folic" in component:
            grouped["folate"].append(value_mcg)

    expected_ranges = {
        "vitamin d": (5.0, 100.0),
        "vitamin b12": (2.0, 1000.0),
        "vitamin c": (10000.0, 2000000.0),
        "folate": (100.0, 1000.0),
    }

    for nutrient, values in grouped.items():
        if len(values) < 1:
            continue
        try:
            median_val = float(statistics.median(values))
        except Exception:
            continue
        low, high = expected_ranges.get(nutrient, (0.0, float("inf")))
        for value in values:
            if median_val > 0 and value > (median_val * 5.0):
                warnings.append(
                    f"outlier: {nutrient} {format_float(value)} mcg is >5x median {format_float(median_val)} mcg"
                )
            if value < low or value > high:
                warnings.append(
                    f"range: {nutrient} {format_float(value)} mcg outside expected {format_float(low)}-{format_float(high)} mcg"
                )

    return rows, warnings


def _apply_context_aware_unit_correction(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply conservative unit corrections for commonly misread vitamin rows."""
    corrected: list[dict[str, Any]] = []
    for row in rows:
        out = dict(row)
        component = normalize_lookup_key(str(out.get("component", "") or ""))
        dose_unit = _normalize_component_unit_token(str(out.get("dose_unit", "") or ""))
        try:
            dose_value = float(out.get("dose_value")) if out.get("dose_value") is not None else None
        except Exception:
            dose_value = None

        if dose_value is None:
            corrected.append(out)
            continue

        if "vitamin c" in component and dose_unit == "mcg" and dose_value < 10:
            out["dose_unit"] = "mg"
            out["dose_value"] = dose_value
        elif "vitamin d" in component and dose_unit == "mg" and dose_value <= 1:
            out["dose_unit"] = "mcg"
            out["dose_value"] = dose_value * 1000.0

        corrected.append(out)
    return corrected


def _validate_nutrition_label_sanity(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    rows, outlier_warnings = _detect_dosage_outliers(rows)
    warnings: list[str] = list(outlier_warnings)
    return rows, warnings


def _is_strong_ocr_candidate(text: str) -> bool:
    gate = extraction_gate_report(text)
    return bool(
        gate.get("passed")
        and int(gate.get("dose_hits", 0)) >= 4
        and int(gate.get("nutrient_hint_hits", 0)) >= 2
    )


def extract_image_text_with_tesseract(image_bytes: bytes) -> str:
    def preprocess_for_tesseract(image: Image.Image, scale: float = 1.0) -> Image.Image:
        gray = image.convert("L")
        width, height = gray.size
        if scale > 1.0:
            gray = gray.resize((int(width * scale), int(height * scale)), Image.Resampling.LANCZOS)
        elif min(width, height) < 1200:
            gray = gray.resize((width * 2, height * 2), Image.Resampling.LANCZOS)
        gray = ImageOps.autocontrast(gray)
        gray = gray.filter(ImageFilter.MedianFilter(size=3))
        return gray.point(lambda px: 255 if px > 150 else 0)

    def preprocess_high_contrast(image: Image.Image, scale: float = 1.0) -> Image.Image:
        gray = image.convert("L")
        width, height = gray.size
        if scale > 1.0:
            gray = gray.resize((int(width * scale), int(height * scale)), Image.Resampling.LANCZOS)
        elif min(width, height) < 900:
            gray = gray.resize((width * 2, height * 2), Image.Resampling.LANCZOS)
        gray = ImageOps.autocontrast(gray, cutoff=5)
        gray = gray.filter(ImageFilter.SHARPEN)
        return gray.point(lambda px: 255 if px > 120 else 0)

    try:
        import pytesseract

        resolved_cmd = resolve_tesseract_cmd()
        if resolved_cmd:
            pytesseract.pytesseract.tesseract_cmd = resolved_cmd

        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

        try:
            available_langs = set(pytesseract.get_languages(config=""))
            lang_config = "deu+eng" if "deu" in available_langs else "eng"
        except Exception:
            lang_config = "eng"

        attempts: list[tuple[Image.Image, int, int]] = [
            (preprocess_for_tesseract(image, scale=1.0), 6, 1),
            (preprocess_high_contrast(image, scale=1.0), 6, 1),
        ]

        best_text = ""
        best_score = -1
        for variant, psm, timeout_sec in attempts:
            cfg = f"--oem 3 --psm {psm} -l {lang_config}"
            try:
                raw_text = pytesseract.image_to_string(variant, config=cfg, timeout=timeout_sec)
                candidate_text = str(raw_text or "").strip()
                gate = extraction_gate_report(candidate_text)
                score = (
                    int(gate.get("score", 0)) * 10
                    + int(gate.get("dose_hits", 0)) * 3
                    + int(gate.get("nutrient_hint_hits", 0))
                )
                if score > best_score:
                    best_score = score
                    best_text = candidate_text
                if _is_strong_ocr_candidate(candidate_text):
                    break
            except Exception:
                pass

        return best_text.strip()
    except Exception:
        return ""


def extract_image_text_with_blockbrain(image_bytes: bytes, model: str | None = None) -> str:
    """Extract nutrition label text from an image via Blockbrain vision only."""
    global LAST_TEXT_LLM_ERROR
    global LAST_VISION_PROVIDER
    LAST_VISION_PROVIDER = ""
    LAST_TEXT_LLM_ERROR = ""

    bb_text = call_blockbrain_vision(image_bytes, model=model)
    if bb_text and bb_text.strip() and not _is_blockbrain_image_missing_response(bb_text):
        runtime_model = str(LAST_BLOCKBRAIN_MODEL or model or "").strip()
        if runtime_model:
            LAST_VISION_PROVIDER = f"Blockbrain vision model ({runtime_model})"
        else:
            LAST_VISION_PROVIDER = "Blockbrain vision model"
        return bb_text.strip()

    if _is_blockbrain_image_missing_response(bb_text):
        LAST_TEXT_LLM_ERROR = "Blockbrain vision did not receive an image payload"
    elif not bb_text or not bb_text.strip():
        LAST_TEXT_LLM_ERROR = "Blockbrain vision returned no text"
    return ""


def _build_blockbrain_ocr_image_variants(image_bytes: bytes) -> list[tuple[str, bytes]]:
    """Build lightweight image variants to improve first-pass OCR reliability."""
    variants: list[tuple[str, bytes]] = [("original", image_bytes)]
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        image = ImageOps.exif_transpose(image)

        max_side = 1800
        width, height = image.size
        if max(width, height) > max_side:
            scale = max_side / float(max(width, height))
            image = image.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))),
                Image.Resampling.LANCZOS,
            )

        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=88, optimize=True)
        resized_payload = buffer.getvalue()
        if resized_payload and resized_payload != image_bytes:
            variants.append(("resized_jpeg", resized_payload))
    except Exception:
        pass

    deduped: list[tuple[str, bytes]] = []
    seen_hashes: set[str] = set()
    for variant_name, payload in variants:
        payload_hash = hashlib.sha1(payload).hexdigest() if payload else ""
        if payload_hash in seen_hashes:
            continue
        seen_hashes.add(payload_hash)
        deduped.append((variant_name, payload))
    return deduped


def extract_image_text_with_blockbrain_best_effort(image_bytes: bytes, model: str | None = None) -> tuple[str, str]:
    """Try fast image variants and return (text, route label)."""
    best_text = ""
    best_route = ""
    best_score = -1

    for variant_name, variant_bytes in _build_blockbrain_ocr_image_variants(image_bytes):
        candidate_text = extract_image_text_with_blockbrain(variant_bytes, model=model)
        if not candidate_text:
            continue

        route_label = str(LAST_VISION_PROVIDER or "Blockbrain vision model").strip()
        if variant_name != "original":
            route_label = f"{route_label} ({variant_name})"

        gate = extraction_gate_report(candidate_text)
        candidate_score = int(gate.get("score", 0)) * 100 + int(gate.get("dose_hits", 0))
        if candidate_score > best_score:
            best_score = candidate_score
            best_text = candidate_text
            best_route = route_label

        if gate.get("passed"):
            return candidate_text, route_label

    return best_text, best_route


# Backward-compat alias
extract_image_text_with_local_stack = extract_image_text_with_blockbrain


def try_tesseract_ocr(image_bytes: bytes) -> str:
    return extract_image_text_with_tesseract(image_bytes)


def _count_nutrient_hints(text: str) -> int:
    if not text:
        return 0
    nutrient_hints = [
        "vitamin",
        "mineral",
        "magnesium",
        "calcium",
        "zinc",
        "iron",
        "selenium",
        "iodine",
        "potassium",
        "sodium",
        "folate",
        "niacin",
        "riboflavin",
        "thiamin",
        "biotin",
        "pantothenic",
        "choline",
        "omega",
        "epa",
        "dha",
    ]
    lowered = text.lower()
    return sum(1 for hint in nutrient_hints if hint in lowered)


def extraction_gate_report(text: str) -> dict[str, Any]:
    if not text:
        return {
            "char_count": 0,
            "word_count": 0,
            "dose_hits": 0,
            "nutrient_hint_hits": 0,
            "score": 0,
            "passed": False,
        }

    compact = re.sub(r"\s+", " ", text).strip()
    words = re.findall(r"[A-Za-z][A-Za-z0-9\-/%]*", compact)
    dose_hits = len(EXTRACTION_DOSE_PATTERN.findall(compact))
    nutrient_hint_hits = _count_nutrient_hints(compact)

    score = 0
    if len(compact) >= 40:
        score += 1
    if len(words) >= 8:
        score += 1
    if dose_hits >= 1:
        score += 2
    if nutrient_hint_hits >= 1:
        score += 1

    passed = score >= 2
    return {
        "char_count": len(compact),
        "word_count": len(words),
        "dose_hits": dose_hits,
        "nutrient_hint_hits": nutrient_hint_hits,
        "score": score,
        "passed": passed,
    }


def passes_extraction_gate(text: str) -> bool:
    return bool(extraction_gate_report(text).get("passed"))


def build_gate_result(
    stage: str,
    passed: bool,
    checks: list[str],
    metrics: dict[str, Any] | None = None,
    issues: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "stage": str(stage or "unknown"),
        "passed": bool(passed),
        "checks": [str(x) for x in (checks or [])],
        "metrics": dict(metrics or {}),
        "issues": [str(x) for x in (issues or [])],
    }


def _normalize_component_unit_token(unit: str) -> str:
    return _canon_unit(unit)


def _validate_component_row_with_pydantic(item: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    if BaseModel is None:
        return item, ""
    try:
        if hasattr(ParsedComponentModel, "model_validate"):
            obj = ParsedComponentModel.model_validate(item)
            out = obj.model_dump()
        else:
            obj = ParsedComponentModel.parse_obj(item)
            out = obj.dict()
        return out, ""
    except ValidationError as exc:
        return None, f"schema validation failed: {exc}"
    except Exception as exc:
        return None, f"schema validation failed: {exc}"


def validate_parsed_components(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    accepted: list[dict[str, Any]] = []
    issues: list[str] = []
    seen: set[tuple[str, float | None, str]] = set()
    rejected = 0
    dose_confidence = 1.0

    for item in rows:
        if not isinstance(item, dict):
            rejected += 1
            issues.append("row is not an object")
            continue

        validated_item, schema_error = _validate_component_row_with_pydantic(item)
        if validated_item is None:
            rejected += 1
            issues.append(schema_error)
            continue

        component = _repair_ocr_component_name(str(validated_item.get("component", "")))
        if not component:
            rejected += 1
            issues.append("missing component")
            continue
        if not _is_plausible_component_name(component):
            rejected += 1
            issues.append(f"implausible component name '{component}'")
            continue

        dose_raw = validated_item.get("dose_value")
        try:
            dose_value = float(dose_raw) if dose_raw is not None else None
        except Exception:
            dose_value = None

        dose_unit = str(validated_item.get("dose_unit", "") or "")
        component, dose_value, dose_unit = _repair_ocr_dose_entry(component, dose_value, dose_unit)
        if dose_unit not in ALLOWED_DOSE_UNITS:
            rejected += 1
            issues.append(f"unsupported dose unit '{dose_unit}'")
            continue

        if dose_value is None:
            dose_unit = ""
            dose_confidence *= 0.85
        else:
            if not math.isfinite(dose_value) or dose_value <= 0:
                rejected += 1
                issues.append(f"non-positive or invalid dose for {component}")
                continue
            if not dose_unit:
                rejected += 1
                issues.append(f"missing dose unit for {component}")
                continue
            upper = MAX_REASONABLE_DOSE_BY_UNIT.get(dose_unit)
            if upper is not None and dose_value > upper:
                rejected += 1
                issues.append(f"dose too large for {component}: {dose_value} {dose_unit}")
                continue

        key = (component, dose_value, dose_unit)
        if key in seen:
            continue
        seen.add(key)
        accepted.append(
            {
                "component": component,
                "dose_value": dose_value,
                "dose_unit": dose_unit,
            }
        )

    passed = bool(accepted)
    overall_confidence = max(0.0, min(1.0, dose_confidence * (len(accepted) / max(1, len(rows)))))
    
    metrics = {
        "input_rows": len(rows),
        "accepted_rows": len(accepted),
        "rejected_rows": rejected,
        "pydantic_enabled": BaseModel is not None,
        "confidence_score": round(overall_confidence, 2),
    }
    result = build_gate_result(
        stage="component_schema_validation",
        passed=passed,
        checks=["format", "content", "logic"],
        metrics=metrics,
        issues=issues[:12],
    )
    return accepted, result


def fetch_clean_page_text(url: str) -> str:
    try:
        response = _http_get(
            url,
            timeout=HTTP_TIMEOUT,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; SuppSwap/1.0; +https://example.local)",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        if response.status_code != 200:
            return ""
        content_type = str(response.headers.get("Content-Type", "") or "").lower()
        if "html" not in content_type and "xml" not in content_type and "text" not in content_type:
            return ""
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.extract()
        text = " ".join(soup.get_text(separator=" ").split())
        return text[:18000]
    except Exception:
        return ""


def extract_supplement_text_from_page_text_local(page_text: str) -> str:
    if not page_text:
        return ""

    compact = re.sub(r"\s+", " ", page_text).strip()
    if not compact:
        return ""

    candidates: list[str] = []

    for match in LOCAL_URL_KEYWORD_WINDOW_PATTERN.finditer(compact):
        segment = match.group(0).strip(" -;:,.")
        if len(segment) >= 20:
            candidates.append(segment)

    sentence_like_parts = LOCAL_URL_SENTENCE_SPLIT_PATTERN.split(compact)
    for part in sentence_like_parts:
        piece = part.strip(" -;:,.")
        if len(piece) < 8:
            continue
        lowered = piece.lower()
        if EXTRACTION_DOSE_PATTERN.search(piece):
            candidates.append(piece)
            continue
        if any(k in lowered for k in ("serving", "supplement facts", "ingredients", "daily value")):
            candidates.append(piece)

    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = re.sub(r"\s+", " ", candidate).strip()
        key = normalized.lower()
        if not normalized or key in seen:
            continue
        seen.add(key)
        unique.append(normalized)
        if len(unique) >= 60:
            break

    return "\n".join(unique)


def extract_supplement_text_from_url(url: str) -> str:
    global LAST_URL_PARSE_REASON
    global LAST_TEXT_PROVIDER
    LAST_URL_PARSE_REASON = ""
    LAST_TEXT_PROVIDER = ""

    page_text = fetch_clean_page_text(url)
    if not page_text:
        LAST_URL_PARSE_REASON = "Failed to download page text or blocked by target website."
        return ""

    local_fallback_text = extract_supplement_text_from_page_text_local(page_text)

    if local_fallback_text and passes_extraction_gate(local_fallback_text):
        LAST_TEXT_PROVIDER = "Local URL parser"
        LAST_URL_PARSE_REASON = "Local parser passed deterministic quality gates."
        return local_fallback_text

    if local_fallback_text and not _text_llm_available():
        LAST_TEXT_PROVIDER = "Local URL parser"
        LAST_URL_PARSE_REASON = "Local parser used because no local text-model runtime is enabled."
        return local_fallback_text

    prompt_source = local_fallback_text if local_fallback_text else page_text[:4000]

    system_prompt = (
        "You extract supplement facts from web page text. "
        "Return plain text only with ingredients/components, serving size, and doses."
    )
    user_prompt = f"Extract supplement facts from this page content:\n\n{prompt_source}"
    llm_text = call_text_llm(system_prompt, user_prompt)

    if llm_text:
        if passes_extraction_gate(llm_text):
            LAST_TEXT_PROVIDER = str(LAST_TEXT_PROVIDER or "Blockbrain text route")
            LAST_URL_PARSE_REASON = "LLM output passed deterministic quality gates."
            return llm_text

    if local_fallback_text:
        LAST_TEXT_PROVIDER = "Local URL parser"
        if llm_text:
            LAST_URL_PARSE_REASON = "LLM output failed deterministic quality gates; local parser used."
        else:
            LAST_URL_PARSE_REASON = "No LLM output available; local parser used."
        return local_fallback_text

    if llm_text:
        LAST_URL_PARSE_REASON = "LLM returned low-confidence text; no local fallback candidates found."

    return llm_text


def clean_json_block(raw: str) -> str:
    if not raw:
        return ""
    txt = raw.strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```(?:json)?", "", txt).strip()
        txt = re.sub(r"```$", "", txt).strip()
    return txt


def _parse_structured_nutrition_json(json_text: str) -> list[dict[str, Any]]:
    """
    Parse structured nutrition JSON from vision LLM.
    Handles format: {"nutrients": [{"name": "...", "amount": ..., "unit": "..."}]}
    """
    out: list[dict[str, Any]] = []
    try:
        data = json.loads(json_text)
        if isinstance(data, dict) and "nutrients" in data:
            nutrients = data.get("nutrients", [])
            if isinstance(nutrients, list):
                for item in nutrients:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name", "") or "").strip().lower()
                    if not name:
                        continue
                    try:
                        amount = float(item.get("amount")) if item.get("amount") is not None else None
                    except (ValueError, TypeError):
                        amount = None
                    unit = str(item.get("unit", "") or "").strip().lower()
                    if amount is None or not unit:
                        continue
                    out.append({
                        "component": name,
                        "dose_value": amount,
                        "dose_unit": unit,
                    })
        elif isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "") or "").strip().lower()
                if not name:
                    continue
                try:
                    amount = float(item.get("amount")) if item.get("amount") is not None else None
                except (ValueError, TypeError):
                    amount = None
                unit = str(item.get("unit", "") or "").strip().lower()
                if amount is None or not unit:
                    continue
                out.append({
                    "component": name,
                    "dose_value": amount,
                    "dose_unit": unit,
                })
    except Exception:
        pass
    return out


def extract_nutrition_doses_from_product_image(product_url: str) -> list[dict[str, Any]]:
    """
    Extract nutrition facts with doses from product images on the webpage.
    LLM vision is attempted first; local OCR (Tesseract) is fallback.

    Returns list of dicts: {"component": name, "dose_value": number, "dose_unit": "mg"|"mcg"|etc}
    """
    try:
        import requests
        from bs4 import BeautifulSoup
        from urllib.parse import urljoin

        def _rows_with_doses(text: str) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            
            # Try first to parse as structured JSON (from improved vision prompts)
            json_block = clean_json_block(text)
            if json_block.startswith("{") or json_block.startswith("["):
                out = _parse_structured_nutrition_json(json_block)
                if out:
                    validated, _ = validate_parsed_components(out)
                    return validated
            
            # Fallback to text parsing
            for row in parse_components(text):
                component = normalize_component_name(str(row.get("component", "") or ""))
                if not component:
                    continue
                dose_value = row.get("dose_value")
                dose_unit = _normalize_component_unit_token(str(row.get("dose_unit", "") or ""))
                if dose_value is None or not dose_unit:
                    continue
                out.append(
                    {
                        "component": component,
                        "dose_value": dose_value,
                        "dose_unit": dose_unit,
                    }
                )
            validated, _ = validate_parsed_components(out)
            return validated

        def _score_image_candidate(src: str, alt_text: str, title_text: str) -> int:
            blob = f"{src} {alt_text} {title_text}".lower()
            score = 0
            keyword_weights = {
                "nutrition": 6,
                "supplement facts": 8,
                "facts": 5,
                "label": 5,
                "ingredients": 4,
                "serving": 3,
                "table": 2,
                "back": 2,
            }
            for key, w in keyword_weights.items():
                if key in blob:
                    score += w
            return score

        resp = _http_get(product_url, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "html.parser")

        candidates: list[tuple[int, str]] = []
        for img_tag in soup.find_all("img"):
            img_src = img_tag.get("src", "") or img_tag.get("data-src", "")
            if not img_src:
                continue
            if not ("http" in img_src or img_src.startswith("/")):
                continue
            absolute = urljoin(product_url, img_src)
            score = _score_image_candidate(
                img_src,
                str(img_tag.get("alt", "") or ""),
                str(img_tag.get("title", "") or ""),
            )
            candidates.append((score, absolute))

        if not candidates:
            return []

        candidates = sorted(candidates, key=lambda x: x[0], reverse=True)
        tried_urls: set[str] = set()
        best_rows: list[dict[str, Any]] = []

        for _, image_url in candidates[:6]:
            if image_url in tried_urls:
                continue
            tried_urls.add(image_url)

            try:
                resp_img = _http_get(image_url, timeout=10)
                resp_img.raise_for_status()
                image_bytes = resp_img.content
            except Exception:
                continue

            logger.info("Trying nutrition extraction from image: %s", image_url[:120])

            vision_rows: list[dict[str, Any]] = []
            vision_text, _vision_route = extract_image_text_with_blockbrain_best_effort(image_bytes)
            if vision_text:
                vision_rows = _rows_with_doses(vision_text)

            tesseract_rows: list[dict[str, Any]] = []
            if len(vision_rows) < 8:
                local_ocr_text = try_tesseract_ocr(image_bytes)
                if local_ocr_text:
                    tesseract_rows = _rows_with_doses(local_ocr_text)

            image_best = vision_rows if len(vision_rows) >= len(tesseract_rows) else tesseract_rows
            if len(image_best) > len(best_rows):
                best_rows = image_best

            if len(best_rows) >= 12:
                break

        if best_rows:
            logger.info("Extracted %d nutrients with doses from product images", len(best_rows))
        return best_rows

    except Exception as e:
        logger.warning(f"Image dose extraction failed: {e}")
        return []

def _score_component_rows(rows: list[dict[str, Any]]) -> float:
    if not rows:
        return 0.0
    total = len(rows)
    with_dose = sum(1 for row in rows if row.get("dose_value") is not None and row.get("dose_unit"))
    nutrient_like = sum(1 for row in rows if _looks_like_nutrient_component(str(row.get("component", "") or "")))
    return max(0.0, min(1.0, (0.6 * (with_dose / total)) + (0.4 * (nutrient_like / total))))


def build_structured_nutrients_json(input_text: str) -> dict[str, Any]:
    global LAST_TEXT_PROVIDER

    # --- Vitamin plausibility check (warning-only, unit-aware) ---
    def _plausibility_check_vitamins(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
        """Flag implausible vitamin values using per-vitamin, unit-aware absolute ranges.
        Never cross-compares numeric values across different units (mg vs mcg).
        Warnings only — no auto-correction avoids overwrite regressions (e.g. E mg vs A mcg)."""
        VITAMIN_RANGES: dict[tuple[str, str], tuple[float, float]] = {
            ("vitamin a",   "mcg"): (10.0,    3000.0),
            ("vitamin a",   "mg"):  (0.01,    3.0),
            ("vitamin a",   "iu"):  (100.0,   30000.0),
            ("vitamin d3",  "mcg"): (1.0,     500.0),
            ("vitamin d3",  "mg"):  (0.001,   0.5),
            ("vitamin d3",  "iu"):  (40.0,    50000.0),
            ("vitamin e",   "mg"):  (0.5,     1200.0),
            ("vitamin e",   "iu"):  (1.0,     1800.0),
            ("vitamin e",   "mcg"): (500.0,   1200000.0),
            ("vitamin k2",  "mcg"): (1.0,     1000.0),
            ("vitamin k2",  "mg"):  (0.001,   1.0),
        }
        warnings: list[str] = []
        for row in rows:
            component = str(row.get("component", "")).strip().lower()
            unit = str(row.get("dose_unit", "")).strip().lower()
            key = (component, unit)
            if key in VITAMIN_RANGES:
                lo, hi = VITAMIN_RANGES[key]
                try:
                    val = float(row.get("dose_value", 0) or 0)
                except Exception:
                    continue
                if val < lo or val > hi:
                    warnings.append(
                        f"implausible {component}: {val} {unit} (expected {lo}–{hi})"
                    )
        return rows, warnings

    if not input_text.strip():
        LAST_TEXT_PROVIDER = ""
        return {
            "nutrients": [],
            "confidence": 0.0,
            "source": "none",
            "warnings": ["empty_input"],
        }

    parse_input_text = _prepare_text_for_structured_parsing(input_text)
    has_structured_table_cues = _has_structured_table_cues(parse_input_text)

    regex_fallback = parse_components_rule_based(parse_input_text)
    name_only_fallback = parse_components_name_only(parse_input_text)
    ingredient_list_fallback = [] if has_structured_table_cues else parse_components_from_ingredient_list(parse_input_text)

    local_with_dose = regex_fallback
    if has_structured_table_cues and len(local_with_dose) >= 5:
        # For table-style labels, prioritize rows with explicit doses to avoid
        # ingredient-list hallucinations and OCR duplicates flooding Results.
        local_name_pool = []
    elif ingredient_list_fallback:
        local_name_pool = ingredient_list_fallback
    else:
        local_name_pool = name_only_fallback
    local_rows = merge_component_rows(local_with_dose, local_name_pool)
    local_expanded = expand_umbrella_components(local_rows)
    local_validated, local_meta = validate_parsed_components(local_expanded)
    local_score = _score_component_rows(local_validated)

    best_source = "local_deterministic"
    best_rows = local_validated
    best_meta = local_meta
    best_score = local_score

    if has_structured_table_cues:
        dosed_rows = [
            row for row in best_rows
            if row.get("dose_value") is not None and str(row.get("dose_unit", "") or "").strip()
        ]
        if len(dosed_rows) >= 5:
            best_rows = dosed_rows
            best_score = max(best_score, _score_component_rows(best_rows))

    recovered_vitamin_rows = _recover_missing_vitamin_rows_from_text(parse_input_text, best_rows)
    if recovered_vitamin_rows:
        recovered_merged = merge_component_rows(best_rows, recovered_vitamin_rows)
        recovered_validated, recovered_meta = validate_parsed_components(recovered_merged)
        if len(recovered_validated) >= len(best_rows):
            best_rows = recovered_validated
            best_score = max(best_score, _score_component_rows(best_rows))
            recovery_issues = [str(x) for x in (recovered_meta.get("issues", []) or [])[:3]]
            best_meta = {
                "issues": ["vitamin_token_recovery_applied", *recovery_issues, *(best_meta.get("issues", []) or [])]
            }

    if has_structured_table_cues:
        dosed_rows_after_recovery = [
            row for row in best_rows
            if row.get("dose_value") is not None and str(row.get("dose_unit", "") or "").strip()
        ]
        if len(dosed_rows_after_recovery) >= 5:
            best_rows = dosed_rows_after_recovery
            best_score = max(best_score, _score_component_rows(best_rows))

    warnings: list[str] = []


    # Stage 3+: apply fuzzy nutrient-name correction to resolve OCR label errors.
    best_rows = _apply_fuzzy_nutrient_correction_to_rows(best_rows)

    # Context-aware correction: use regex anchors from OCR text to fix clear vitamin mismatches.
    best_rows, context_warnings = _apply_contextual_vitamin_dose_corrections(best_rows, parse_input_text)
    if context_warnings:
        warnings.extend(context_warnings)

    # Vitamin plausibility check/correction
    best_rows, plaus_warnings = _plausibility_check_vitamins(best_rows)
    if plaus_warnings:
        warnings.extend(plaus_warnings)

    if has_structured_table_cues:
        best_rows.extend(_recover_core_micronutrient_rows_from_text(parse_input_text, existing_rows=best_rows))
        best_rows = _collapse_structured_label_rows(best_rows)

    best_rows = _apply_context_aware_unit_correction(best_rows)

    # Stage 4: unit domain + energy sanity validation (non-destructive — adds warnings).
    best_rows, sanity_warnings = _validate_nutrition_label_sanity(best_rows)
    warnings.extend(sanity_warnings)

    if best_rows:
        LAST_TEXT_PROVIDER = "Local deterministic parser"
    else:
        LAST_TEXT_PROVIDER = "Local deterministic parser"
        warnings.append("no_components_extracted")

    warnings.extend([str(x) for x in (best_meta.get("issues", []) or [])[:6]])

    return {
        "nutrients": best_rows,
        "confidence": round(best_score, 2),
        "source": best_source,
        "warnings": warnings,
    }


def parse_components(input_text: str) -> list[dict[str, Any]]:
    structured = build_structured_nutrients_json(input_text)
    return list(structured.get("nutrients", []) or [])


  
# ---------------------------------------------------------------------------  
# Meal nutrition panel (meal_nutrition_patch.py)  
# Additive per-nutrient summation across ALL ingredients + UX macro display.  
# ---------------------------------------------------------------------------

def _meal_ingredient_matches_food(ingredient_name: str, food_name: str) -> bool:  
    a = normalize_lookup_key(ingredient_name)  
    b = normalize_lookup_key(food_name)  
    if not a or not b:  
        return False  
    if a == b or a in b or b in a:  
        return True

    def _toks(value: str) -> set[str]:  
        out: set[str] = set()  
        for t in value.split():  
            base = t[:-1] if len(t) > 3 and t.endswith("s") else t  
            if len(base) >= 3:  
                out.add(base)  
        return out

    ta, tb = _toks(a), _toks(b)  
    overlap = len(ta & tb)  
    return overlap >= 2 or (overlap >= 1 and len(ta) <= 2)


def _dose_value_to_mg(value: float, unit: str, component: str) -> float | None:  
    factor = unit_to_mg(unit)  
    if factor is None:  
        if normalize_lookup_key(unit) in ("iu", "ui", "ie"):  
            factor = _iu_unit_to_mg_for_component(component)  
    if factor is None:  
        return None  
    return float(value) * float(factor)


def _mg_to_dose_value(mg: float, unit: str, component: str) -> float | None:  
    factor = unit_to_mg(unit)  
    if factor is None:  
        if normalize_lookup_key(unit) in ("iu", "ui", "ie"):  
            factor = _iu_unit_to_mg_for_component(component)  
    if factor is None or factor <= 0:  
        return None  
    return float(mg) / float(factor)


def compute_meal_nutrient_breakdown(  
    meal: dict[str, Any],  
    component_candidates: list[dict[str, Any]],  
) -> dict[str, Any]:  
    """Additive nutrition breakdown for a meal.

    For each micronutrient, sums the contribution of EVERY ingredient that  
    contains it (amount_per_100g * grams / 100) and compares the summed dose  
    against the supplement target. Also computes macros per serving + per 100 g.  
    """  
    ingredients: list[tuple[str, float]] = []  
    for ing in meal.get("ingredients", []) or []:  
        name = str(ing.get("name", "") or "").strip()  
        try:  
            grams = float(ing.get("grams", 0) or 0)  
        except Exception:  
            grams = 0.0  
        if name and grams > 0:  
            ingredients.append((name, grams))

    total_grams = sum(g for _, g in ingredients) or 0.0

    macro_totals = _recipe_macro_totals(meal)  
    kcal = float(macro_totals.get("kcal", 0.0) or 0.0)  
    protein_g = float(macro_totals.get("protein_g", 0.0) or 0.0)  
    carbs_g = float(macro_totals.get("carbs_g", 0.0) or 0.0)  
    fat_g = float(macro_totals.get("fat_g", 0.0) or 0.0)

    scale_100 = (100.0 / total_grams) if total_grams > 0 else 0.0

    nutrient_rows: list[dict[str, Any]] = []  
    for cand in component_candidates:  
        component = str(cand.get("component", "") or "").strip()  
        if not component:  
            continue  
        dose_value = cand.get("dose_value")  
        dose_unit = str(cand.get("dose_unit", "") or "").strip()  
        foods = cand.get("foods", []) or []

        delivered_mg = 0.0  
        any_match = False  
        for ing_name, grams in ingredients:  
            best_amt: float | None = None  
            best_unit = ""  
            for food in foods:  
                food_name = str(food.get("food_description", "") or "")  
                if not _meal_ingredient_matches_food(ing_name, food_name):  
                    continue  
                try:  
                    amt = float(food.get("amount_per_100g", 0.0) or 0.0)  
                except Exception:  
                    amt = 0.0  
                if amt <= 0:  
                    continue  
                if best_amt is None or amt > best_amt:  
                    best_amt = amt  
                    best_unit = str(food.get("unit", "") or "")  
            if best_amt is None:  
                continue  
            contrib_mg = _dose_value_to_mg(best_amt, best_unit, component)  
            if contrib_mg is None:  
                continue  
            delivered_mg += contrib_mg * (grams / 100.0)  
            any_match = True

        target_mg = None  
        if dose_value is not None and dose_unit:  
            try:  
                target_mg = _dose_value_to_mg(float(dose_value), dose_unit, component)  
            except Exception:  
                target_mg = None

        delivered_display = _mg_to_dose_value(delivered_mg, dose_unit, component) if dose_unit else None  
        pct = None  
        if target_mg is not None and target_mg > 0:  
            pct = max(0.0, (delivered_mg / target_mg) * 100.0)

        nutrient_rows.append(  
            {  
                "component": component,  
                "delivered_value": delivered_display,  
                "delivered_unit": dose_unit,  
                "target_value": dose_value,  
                "target_unit": dose_unit,  
                "pct": pct,  
                "matched": any_match,  
            }  
        )

    nutrient_rows.sort(key=lambda r: (r.get("pct") is None, -(r.get("pct") or 0.0)))

    return {  
        "total_grams": total_grams,  
        "kcal": kcal,  
        "protein_g": protein_g,  
        "carbs_g": carbs_g,  
        "fat_g": fat_g,  
        "kcal_100": kcal * scale_100,  
        "protein_100": protein_g * scale_100,  
        "carbs_100": carbs_g * scale_100,  
        "fat_100": fat_g * scale_100,  
        "nutrients": nutrient_rows,  
    }


def _macro_split_bar_html(protein_g: float, carbs_g: float, fat_g: float) -> str:  
    p_kcal = protein_g * 4.0  
    c_kcal = carbs_g * 4.0  
    f_kcal = fat_g * 9.0  
    total = p_kcal + c_kcal + f_kcal  
    if total <= 0:  
        return ""  
    p_pct = round(p_kcal / total * 100.0)  
    c_pct = round(c_kcal / total * 100.0)  
    f_pct = max(0, 100 - p_pct - c_pct)  
    return (  
        "<div style='display:flex;height:14px;border-radius:7px;overflow:hidden;"  
        "margin:6px 0 4px 0;border:1px solid #e2e8f0;'>"  
        f"<div style='width:{p_pct}%;background:#0f766e;'></div>"  
        f"<div style='width:{c_pct}%;background:#f59e0b;'></div>"  
        f"<div style='width:{f_pct}%;background:#ef4444;'></div>"  
        "</div>"  
        "<div style='display:flex;gap:14px;font-size:0.74rem;color:#475569;'>"  
        f"<span><span style='color:#0f766e;'>&#9632;</span> Protein {p_pct}%</span>"  
        f"<span><span style='color:#f59e0b;'>&#9632;</span> Carbs {c_pct}%</span>"  
        f"<span><span style='color:#ef4444;'>&#9632;</span> Fat {f_pct}%</span>"  
        "</div>"  
    )


def _nutrient_coverage_table_html(nutrient_rows: list[dict[str, Any]]) -> str:  
    if not nutrient_rows:  
        return ""  
    rows_html: list[str] = []  
    for row in nutrient_rows:  
        comp = str(row.get("component", "") or "").title()  
        unit = str(row.get("delivered_unit", "") or "")  
        delivered = row.get("delivered_value")  
        target = row.get("target_value")  
        pct = row.get("pct")

        delivered_txt = f"{format_float(float(delivered))} {unit}" if delivered is not None else "n/a"  
        target_txt = f"{format_float(float(target))} {unit}" if target is not None else "n/a"

        if pct is None:  
            chip_color = "#94a3b8"  
            chip_text = "no target"  
            bar_pct = 0.0  
        else:  
            bar_pct = min(100.0, float(pct))  
            if pct >= 100.0:  
                chip_color = "#16a34a"  
            elif pct >= 50.0:  
                chip_color = "#f59e0b"  
            else:  
                chip_color = "#ef4444"  
            chip_text = f"{format_float(float(pct), 0)}%"

        rows_html.append(  
            "<tr>"  
            f"<td style='padding:6px 8px;font-weight:600;color:#0f172a;'>{comp}</td>"  
            f"<td style='padding:6px 8px;text-align:right;color:#0f172a;white-space:nowrap;'>{delivered_txt}</td>"  
            f"<td style='padding:6px 8px;text-align:right;color:#64748b;white-space:nowrap;'>{target_txt}</td>"  
            "<td style='padding:6px 8px;min-width:90px;'>"  
            "<div style='background:#eef2f7;border-radius:6px;height:8px;overflow:hidden;'>"  
            f"<div style='width:{bar_pct}%;height:8px;background:{chip_color};'></div>"  
            "</div></td>"  
            "<td style='padding:6px 8px;text-align:right;'>"  
            f"<span style='background:{chip_color};color:#fff;border-radius:999px;"  
            f"padding:2px 8px;font-size:0.72rem;font-weight:700;white-space:nowrap;'>{chip_text}</span>"  
            "</td></tr>"  
        )

    return (  
        "<div style='overflow-x:auto;'>"  
        "<table style='width:100%;border-collapse:collapse;font-size:0.82rem;'>"  
        "<thead><tr style='background:#f8fafc;'>"  
        "<th style='padding:6px 8px;text-align:left;color:#475569;font-weight:600;'>Nutrient</th>"  
        "<th style='padding:6px 8px;text-align:right;color:#475569;font-weight:600;'>In meal</th>"  
        "<th style='padding:6px 8px;text-align:right;color:#475569;font-weight:600;'>Supp dose</th>"  
        "<th style='padding:6px 8px;text-align:left;color:#475569;font-weight:600;'>Coverage</th>"  
        "<th style='padding:6px 8px;text-align:right;color:#475569;font-weight:600;'>%</th>"  
        "</tr></thead><tbody>"  
                + "".join(rows_html)  
                + "</tbody></table></div>"  
    )


def render_meal_nutrition_panel(  
    meal: dict[str, Any],  
    component_candidates: list[dict[str, Any]],  
) -> None:  
    import streamlit as st

    data = compute_meal_nutrient_breakdown(meal, component_candidates)

    st.markdown("**Nutrition at a glance** (per serving)")  
    metric_cols = st.columns(4)  
    metric_cols[0].metric("Calories", f"{format_float(data['kcal'], 0)} kcal")  
    metric_cols[1].metric("Protein", f"{format_float(data['protein_g'], 1)} g")  
    metric_cols[2].metric("Carbs", f"{format_float(data['carbs_g'], 1)} g")  
    metric_cols[3].metric("Fat", f"{format_float(data['fat_g'], 1)} g")

    bar_html = _macro_split_bar_html(data["protein_g"], data["carbs_g"], data["fat_g"])  
    if bar_html:  
        st.markdown(bar_html, unsafe_allow_html=True)

    if data["total_grams"] > 0:  
        st.caption(  
            "Per 100 g: "  
            f"{format_float(data['kcal_100'], 0)} kcal | "  
            f"P {format_float(data['protein_100'], 1)} g | "  
            f"C {format_float(data['carbs_100'], 1)} g | "  
            f"F {format_float(data['fat_100'], 1)} g "  
            f"(meal total ~{format_float(data['total_grams'], 0)} g)"  
        )

    nutrient_rows = data.get("nutrients", []) or []  
    if nutrient_rows:  
        st.markdown("**Micronutrients delivered** (added across all selected whole foods)")  
        st.markdown(_nutrient_coverage_table_html(nutrient_rows), unsafe_allow_html=True)  
        st.caption(  
            "Each nutrient is summed across every ingredient that contains it, "  
            "then compared to your supplement dose. Green = dose met or exceeded; "  
            "amber = partial; red = low."  
        )  


def _render_research_tab() -> None:
    import streamlit as st

    st.subheader("Ask a RAG question")
    rag_chunks, rag_status = build_rag_index()
    rag_available = bool(rag_chunks) and str(rag_status).lower().startswith("ok")
    if rag_available:
        st.caption("Ask questions using your local fitness reference library.")
    else:
        st.warning(f"RAG currently unavailable: {rag_status}")

    rag_query = st.text_area(
        "Your question",
        placeholder="Ask me any supplement-related question and I will search your local reference database.",
        height=110,
        key="rag_query_input",
    )
    ask_rag = st.button("Ask RAG", use_container_width=True, key="ask_rag_btn")
    if ask_rag:
        if not rag_query.strip():
            st.warning("Please enter a question.")
        elif not rag_available:
            st.info("RAG index is not ready yet. Please refresh after indexing or check parser setup.")
        else:
            with st.status("Searching reference library", expanded=False) as rag_status_box:
                rag_status_box.write("Retrieving relevant excerpts")
                rag_answer, rag_sources, rag_meta = answer_rag_question(rag_query.strip(), rag_chunks)
                rag_status_box.update(label="Done", state="complete")

            st.markdown("**Answer**")
            st.write(rag_answer)
            if rag_sources:
                st.caption("Sources: " + ", ".join(rag_sources))

            retrieval_conf = float(rag_meta.get("retrieval_confidence", 0.0) or 0.0)
            st.caption(f"Retrieval confidence: {format_float(retrieval_conf * 100, 0)}%")

            needs_web_fallback = bool(rag_meta.get("needs_web_fallback", False))
            if needs_web_fallback:
                reason = str(rag_meta.get("reason", "") or "")
                fallback_query = str(rag_meta.get("fallback_query", "") or "").strip()
                if reason == "no_retrieval":
                    st.info("No reliable local answer found for this question. You can run a web-search fallback.")
                elif reason == "llm_unavailable":
                    st.info("Local evidence was retrieved, but the LLM answer step is unavailable right now.")

                if fallback_query:
                    st.caption("Suggested web-search query")
                    st.code(fallback_query)

                fallback_pack = build_web_fallback_package(rag_query.strip(), fallback_query)
                st.link_button(
                    "Open web search now",
                    str(fallback_pack.get("search_url", "https://duckduckgo.com")),
                    use_container_width=True,
                )

                with st.expander("Web fallback resources", expanded=True):
                    st.markdown("**Ready-to-use search queries**")
                    for q in fallback_pack.get("queries", []):
                        st.code(str(q))

                    st.markdown("**Trusted sources to prioritize**")
                    for url in fallback_pack.get("trusted_urls", []):
                        st.markdown(f"- {url}")

                if st.button("Generate optional LLM research plan", key="rag_websearch_fallback_btn", use_container_width=True):
                    web_fallback = call_text_llm(
                        "You are a nutrition research assistant.",
                        (
                            "Create a concise web-search plan for this nutrition question. "
                            "Return: 1) 5 high-quality search queries, 2) trusted sources to prioritize, "
                            "3) quick checklist to validate claims. "
                            f"Question: {rag_query.strip()}"
                        ),
                    )
                    if web_fallback:
                        st.markdown("**Optional LLM research plan**")
                        st.write(web_fallback)
                    else:
                        st.info("LLM planner unavailable. Use the working fallback resources above.")


def _render_reference_tab() -> None:
    import streamlit as st

    st.subheader("Official Nutrient Reference Guide")
    st.caption(
        "Official adult nutrient intake recommendation averaged across several official sources."
    )

    source_rows = load_official_nutrient_sources()
    if not source_rows:
        st.info(
            "No official nutrient source table found yet. Add rows to `data/official_nutrient_sources.csv` "
            "using the provided template format."
        )
        st.code(
            "nutrient,unit,life_stage,sex,source_agency,recommended_value,upper_limit_value,source_url,notes",
            language="text",
        )
    else:
        life_stage_options = sorted(
            {
                str(row.get("life_stage", "Adults") or "Adults")
                for row in source_rows
                if str(row.get("life_stage", "") or "").strip()
            }
        )
        sex_options_all = sorted(
            {
                str(row.get("sex", "All") or "All")
                for row in source_rows
                if str(row.get("sex", "") or "").strip()
            }
        )
        sex_options = [
            value
            for value in sex_options_all
            if normalize_lookup_key(value) in {"male", "female"}
        ]
        if not sex_options:
            sex_options = sex_options_all
        if "Adults" in life_stage_options:
            life_stage_default = life_stage_options.index("Adults")
        else:
            life_stage_default = 0
        if "Male" in sex_options:
            sex_default = sex_options.index("Male")
        elif "Female" in sex_options:
            sex_default = sex_options.index("Female")
        elif "All" in sex_options:
            sex_default = sex_options.index("All")
        else:
            sex_default = 0

        selected_life_stage = life_stage_options[life_stage_default]
        selected_sex = sex_options[sex_default]
        if len(life_stage_options) > 1:
            filter_cols = st.columns(2)
            selected_life_stage = filter_cols[0].selectbox(
                "Life stage",
                options=life_stage_options,
                index=life_stage_default,
                key="official_ref_life_stage",
            )
            if len(sex_options) > 1:
                selected_sex = filter_cols[1].selectbox(
                    "Sex",
                    options=sex_options,
                    index=sex_default,
                    key="official_ref_sex",
                )
            else:
                filter_cols[1].caption(f"Sex: {selected_sex}")
        else:
            st.caption(f"Life stage: {selected_life_stage}")
            if len(sex_options) > 1:
                selected_sex = st.selectbox(
                    "Sex",
                    options=sex_options,
                    index=sex_default,
                    key="official_ref_sex",
                )
            else:
                st.caption(f"Sex: {selected_sex}")

        aggregate_rows = build_official_nutrient_aggregate(source_rows, selected_life_stage, selected_sex)
        if not aggregate_rows:
            st.warning("No matching rows for the selected life stage/sex filters.")
        else:
            display_rows: list[dict[str, Any]] = []
            csv_buffer = io.StringIO()
            csv_writer = csv.writer(csv_buffer)
            csv_writer.writerow(
                [
                    "nutrient",
                    "unit",
                    "recommendation",
                    "max_upper_level_intake",
                    "source_count",
                    "sources_used",
                ]
            )

            for row in aggregate_rows:
                recommendation_value = row.get("recommendation_value")
                ul_avg = row.get("ul_average")
                sources_used = row.get("sources_used", []) or []

                display_rows.append(
                    {
                        "Nutrient": row.get("nutrient", ""),
                        "Unit": row.get("unit", ""),
                        "Recommendation": (
                            format_float(float(recommendation_value), 2)
                            if recommendation_value is not None
                            else "-"
                        ),
                        "Max upper level intake": format_float(float(ul_avg), 2) if ul_avg is not None else "-",
                        "Sources": int(row.get("source_count", 0) or 0),
                    }
                )

                csv_writer.writerow(
                    [
                        row.get("nutrient", ""),
                        row.get("unit", ""),
                        (
                            format_float(float(recommendation_value), 6)
                            if recommendation_value is not None
                            else ""
                        ),
                        format_float(float(ul_avg), 6) if ul_avg is not None else "",
                        int(row.get("source_count", 0) or 0),
                        " | ".join([str(s) for s in sources_used]),
                    ]
                )

            st.dataframe(display_rows, use_container_width=True, hide_index=True)
            st.download_button(
                "Download aggregated table (CSV)",
                data=csv_buffer.getvalue(),
                file_name=f"official_nutrient_reference_{normalize_lookup_key(selected_life_stage)}_{normalize_lookup_key(selected_sex)}.csv",
                mime="text/csv",
                use_container_width=True,
            )

            with st.expander("Show source-level rows", expanded=False):
                for agg in aggregate_rows:
                    nutrient = str(agg.get("nutrient", "") or "").strip()
                    unit = str(agg.get("unit", "") or "").strip()
                    if not nutrient or not unit:
                        continue
                    st.markdown(f"**{nutrient} ({unit})**")
                    for src in source_rows:
                        src_n = str(src.get("nutrient", "") or "").strip()
                        src_u = str(src.get("unit", "") or "").strip()
                        if normalize_lookup_key(src_n) != normalize_lookup_key(nutrient):
                            continue
                        if normalize_lookup_key(src_u) != normalize_lookup_key(unit):
                            continue

                        src_stage = normalize_lookup_key(str(src.get("life_stage", "Adults") or "Adults"))
                        src_sex = normalize_lookup_key(str(src.get("sex", "All") or "All"))
                        if src_stage not in {normalize_lookup_key(selected_life_stage), "all", "general"}:
                            continue
                        if src_sex not in {normalize_lookup_key(selected_sex), "all", "both", "any", "general"}:
                            continue

                        rec = src.get("recommended_value")
                        ul = src.get("upper_limit_value")
                        source_agency = str(src.get("source_agency", "") or "").strip()
                        source_url = str(src.get("source_url", "") or "").strip()
                        notes = str(src.get("notes", "") or "").strip()

                        line = (
                            f"- {source_agency}: rec {format_float(float(rec), 2) if rec is not None else '-'} {unit}, "
                            f"UL {format_float(float(ul), 2) if ul is not None else '-'} {unit}"
                        )
                        st.markdown(line)
                        if source_url:
                            st.caption(source_url)
                        if notes:
                            st.caption(notes)
                    st.markdown("---")

            st.caption(
                "Upper intake level = highest daily intake unlikely to cause adverse effects in healthy people over long-term use."
            )

def _render_analyze_tab(tab_analyze: Any) -> None:
    import streamlit as st
    import streamlit.components.v1 as st_components
    global LAST_TEXT_PROVIDER
    global LAST_URL_PARSE_REASON

    selected_text_model, selected_vision_model = _get_selected_blockbrain_models()
    with tab_analyze:
        st.subheader("Provide your supplement input")

        camera_mode_key = "analyze_camera_mode"
        label_capture_key = "analyze_label_capture_bytes"
        uploaded_label_bytes_key = "analyze_uploaded_label_bytes"
        barcode_capture_key = "analyze_barcode_capture_bytes"
        label_confirmed_key = "analyze_label_capture_confirmed"
        barcode_scanned_value_key = "analyze_barcode_scanned_value"
        barcode_scan_method_key = "analyze_barcode_scan_method"
        active_input_source_key = "analyze_active_input_source"
        barcode_fail_notice_key = "analyze_barcode_autounlock_notice"
        auto_signature_key = "analyze_last_auto_signature"
        analyze_is_running_key = "analyze_is_running"
        analyze_pending_request_key = "analyze_pending_request"
        if camera_mode_key not in st.session_state:
            st.session_state[camera_mode_key] = ""
        if active_input_source_key not in st.session_state:
            st.session_state[active_input_source_key] = ""
        if auto_signature_key not in st.session_state:
            st.session_state[auto_signature_key] = ""
        if analyze_is_running_key not in st.session_state:
            st.session_state[analyze_is_running_key] = False
        if analyze_pending_request_key not in st.session_state:
            st.session_state[analyze_pending_request_key] = None

        def _reset_analyze_inputs_after_submit() -> None:
            st.session_state[active_input_source_key] = ""
            st.session_state[camera_mode_key] = ""
            st.session_state[label_capture_key] = None
            st.session_state[uploaded_label_bytes_key] = None
            st.session_state[label_confirmed_key] = False
            st.session_state[barcode_capture_key] = None
            st.session_state[barcode_scanned_value_key] = ""
            st.session_state[barcode_scan_method_key] = ""
            for widget_key in [
                "supp_camera",
                "supp_upload",
                "supp_barcode",
                "supp_barcode_camera",
                "supp_barcode_upload",
                "supp_url",
                "supp_single_input",
            ]:
                if widget_key in st.session_state:
                    st.session_state.pop(widget_key)

        def _clear_barcode_lock_after_failure() -> None:
            st.session_state[active_input_source_key] = ""
            st.session_state[camera_mode_key] = ""
            st.session_state[barcode_capture_key] = None
            st.session_state[barcode_scanned_value_key] = ""
            st.session_state[barcode_scan_method_key] = ""
            for widget_key in ["supp_barcode", "supp_barcode_upload", "supp_barcode_camera"]:
                if widget_key in st.session_state:
                    st.session_state.pop(widget_key)
            st.session_state[barcode_fail_notice_key] = True

        if st.session_state.get(barcode_fail_notice_key, False):
            st.info("Barcode input was reset after a failed/low-confidence lookup. You can now use another input source.")
            st.session_state[barcode_fail_notice_key] = False

        active_input_source = str(st.session_state.get(active_input_source_key, "") or "")
        allow_label_source = active_input_source in {"", "label"}
        allow_barcode_source = active_input_source in {"", "barcode"}
        allow_url_source = active_input_source in {"", "url"}
        allow_manual_source = active_input_source in {"", "manual"}

        if active_input_source:
            st.caption(f"Active input source: {active_input_source}. Other input options are temporarily disabled.")
            if st.button("Unlock input selection", use_container_width=True, key="unlock_input_source_btn"):
                st.session_state[active_input_source_key] = ""
                st.session_state[camera_mode_key] = ""
                st.session_state[label_capture_key] = None
                st.session_state[uploaded_label_bytes_key] = None
                st.session_state[label_confirmed_key] = False
                st.session_state[barcode_capture_key] = None
                st.session_state[barcode_scanned_value_key] = ""
                st.session_state[barcode_scan_method_key] = ""
                # Reset widget-bound values so unlocking fully clears Analyze inputs.
                for widget_key in [
                    "supp_camera",
                    "supp_upload",
                    "supp_barcode",
                    "supp_barcode_camera",
                    "supp_barcode_upload",
                    "supp_url",
                    "supp_single_input",
                ]:
                    if widget_key in st.session_state:
                        st.session_state.pop(widget_key)
                st.rerun()

        camera_image = None
        barcode_camera = None

        label_capture_bytes = st.session_state.get(label_capture_key)
        label_confirmed = bool(st.session_state.get(label_confirmed_key, False))
        scanned_barcode_value = _normalize_barcode_digits(str(st.session_state.get(barcode_scanned_value_key, "") or ""))

        if label_capture_bytes and allow_label_source:
            st.caption("Nutrition label photo preview")
            st.image(label_capture_bytes, use_column_width=True)
            label_action_col_use, label_action_col_retake = st.columns(2)
            with label_action_col_use:
                if st.button("Use this nutrition label photo", use_container_width=True, key="confirm_label_photo_btn"):
                    st.session_state[active_input_source_key] = "label"
                    st.session_state[label_confirmed_key] = True
                    st.rerun()
            with label_action_col_retake:
                if st.button("Retake nutrition label photo", use_container_width=True, key="retake_label_photo_btn"):
                    st.session_state[active_input_source_key] = "label"
                    st.session_state[label_capture_key] = None
                    st.session_state[label_confirmed_key] = False
                    st.session_state[camera_mode_key] = "label"
                    st.rerun()
            if label_confirmed:
                st.success("Nutrition label photo confirmed.")
            else:
                st.info("Is this nutrition label photo okay, or do you want to retake it?")

        if scanned_barcode_value and allow_barcode_source:
            st.success(f"Barcode scan detected: {scanned_barcode_value}")
            clear_scan_col, _ = st.columns(2)
            with clear_scan_col:
                if st.button("Scan barcode again", use_container_width=True, key="scan_barcode_again_btn"):
                    st.session_state[active_input_source_key] = "barcode"
                    st.session_state[barcode_scanned_value_key] = ""
                    st.session_state[barcode_scan_method_key] = ""
                    st.session_state[camera_mode_key] = "barcode"
                    st.rerun()

        # Patch: single smart uploader (label OR barcode, auto-detected).
        uploaded_image = st.file_uploader(
            "",
            type=["png", "jpg", "jpeg", "webp"],
            key="supp_upload",
        )
        st_components.html(
            """
    <script>
    const doc = window.parent.document;
    function patchUploaderAccept() {
    const inputs = doc.querySelectorAll('input[type="file"]');
    inputs.forEach((el) => {
        el.setAttribute('accept', 'image/*');
        el.removeAttribute('capture');
     });
    }

    patchUploaderAccept();
    setInterval(patchUploaderAccept, 800);
    </script>
    """,
            height=0,
        )
        uploaded_label_bytes = st.session_state.get(uploaded_label_bytes_key)
        _upload_barcode = ""
        if uploaded_image is not None:
            _upload_bytes = uploaded_image.getvalue()
            _det_barcode, _det_method = detect_barcode_from_image(_upload_bytes)
            _upload_barcode = _normalize_barcode_digits(_det_barcode)
            # Only auto-route upload as barcode when a real scanner decoded it.
            # OCR fallback can produce false 8-14 digit tokens from nutrition tables.
            if _upload_barcode and str(_det_method or "").strip().lower() == "pyzbar":
                st.session_state[barcode_scan_method_key] = _det_method or "upload"
                uploaded_label_bytes = None
                st.session_state[uploaded_label_bytes_key] = None
                st.success("Barcode detected in upload - routing to barcode lookup.")
            else:
                uploaded_label_bytes = _upload_bytes
                st.session_state[uploaded_label_bytes_key] = uploaded_label_bytes
                if _upload_barcode:
                    st.info("Possible barcode text detected via OCR fallback; treating upload as nutrition label to avoid false barcode routing.")
                else:
                    st.info("No barcode found in upload - treating image as a nutrition label.")
        elif active_input_source not in {"", "label"}:
            uploaded_label_bytes = None
            st.session_state[uploaded_label_bytes_key] = None

        # Patch: single smart text field (barcode digits / product URL / manual text).
        supp_single_input = st.text_area(
            "Or type details: barcode (EAN/UPC), product link, or supplement text",
            placeholder="Examples:\n4006381333931\nhttps://example.com/product\nVitamin C 500 mg",
            height=120,
            key="supp_single_input",
        )
        barcode_upload = None
        _single_raw = str(supp_single_input or "").strip()
        # Treat as barcode only when user entered digits/separators only.
        # Otherwise, supplement text like "D3 2000 IU + K2 100 mcg" could be
        # incorrectly collapsed into an 8-14 digit token and misrouted.
        _single_is_digits_only = bool(re.fullmatch(r"[\d\s\-_.]+", _single_raw))
        _single_digits = _normalize_barcode_digits(_single_raw) if _single_is_digits_only else ""
        barcode_input = _single_digits or _upload_barcode
        if _single_digits:
            product_url = ""
            manual_text = ""
        elif _single_raw.startswith(("http://", "https://")):
            product_url = _single_raw
            manual_text = ""
        else:
            product_url = ""
            manual_text = _single_raw

        # First non-empty source locks the tab into single-source mode until unlocked.
        if not active_input_source:
            if label_capture_bytes or uploaded_label_bytes:
                st.session_state[active_input_source_key] = "label"
                st.rerun()
            if scanned_barcode_value or barcode_upload is not None or _normalize_barcode_digits(barcode_input):
                st.session_state[active_input_source_key] = "barcode"
                st.rerun()
            if product_url.strip():
                st.session_state[active_input_source_key] = "url"
                st.rerun()
            if manual_text.strip():
                st.session_state[active_input_source_key] = "manual"
                st.rerun()

        active_input_source = str(st.session_state.get(active_input_source_key, "") or "")
        # Be permissive at extraction time: if users provide multiple sources (for
        # example image + manual fallback), evaluate all non-empty sources instead
        # of dropping inputs because of a transient source lock.
        effective_label_capture_bytes = label_capture_bytes
        effective_label_confirmed = bool(label_confirmed)
        effective_uploaded_image_bytes = uploaded_label_bytes
        effective_scanned_barcode_value = scanned_barcode_value
        effective_barcode_input = barcode_input
        effective_barcode_upload = barcode_upload
        effective_product_url = product_url
        effective_manual_text = manual_text

        if effective_label_capture_bytes and not effective_label_confirmed:
            st.caption("Confirm or retake the nutrition label photo before it is used for analysis.")

        pass  # LLM route caption hidden for simpler UX

        # AI model selection is pinned for best UX: the vision model defaults to the
        # fastest benchmarked label-OCR model (anthropic-claude-haiku-4.5) and the
        # text model to the fastest mapping model (gpt-4.1-nano), so "Resolving
        # nutrient mappings" and label OCR stay fast instead of using the slow
        # default reasoning backend. The manual picker is intentionally hidden;
        # advanced users can override via BLOCKBRAIN_MODEL_VISION /
        # BLOCKBRAIN_MODEL_TEXT (env or secrets).
        default_text_model, default_vision_model = _load_blockbrain_model_defaults()
        st.session_state["analyze_blockbrain_text_model"] = default_text_model
        st.session_state["analyze_blockbrain_vision_model"] = default_vision_model
        selected_text_model, selected_vision_model = _get_selected_blockbrain_models()

        camera_bytes = b""
        upload_bytes = uploaded_image.getvalue() if uploaded_image is not None else b""
        has_any_input = bool(
            camera_bytes
            or upload_bytes
            or effective_label_capture_bytes
            or effective_uploaded_image_bytes
            or effective_product_url.strip()
            or effective_manual_text.strip()
            or _normalize_barcode_digits(effective_barcode_input)
            or effective_scanned_barcode_value
        )
        auto_signature_blob = "|".join(
            [
                f"cam:{len(camera_bytes)}",
                f"upl:{len(upload_bytes)}",
                f"lbl:{len(effective_label_capture_bytes or b'')}",
                f"upb:{len(effective_uploaded_image_bytes or b'')}",
                f"bar:{_normalize_barcode_digits(effective_barcode_input)}",
                f"scan:{effective_scanned_barcode_value}",
                f"url:{effective_product_url.strip()}",
                f"txt:{effective_manual_text.strip()}",
            ]
        )
        auto_signature = hashlib.sha1(auto_signature_blob.encode("utf-8", errors="ignore")).hexdigest()

        analyze_clicked = st.button("💊 Analyze Supp → 🥗 Swap With Whole Food", type="primary", use_container_width=True, key="analyze_input_btn")
        auto_trigger = has_any_input and auto_signature != str(st.session_state.get(auto_signature_key, "") or "")
        if auto_trigger and not analyze_clicked:
            st.caption("Input detected. Starting analysis automatically...")
        if analyze_clicked or auto_trigger:
            st.session_state[auto_signature_key] = auto_signature
            st.session_state[analyze_pending_request_key] = {
                "camera_bytes": camera_bytes,
                "uploaded_image_bytes": upload_bytes,
                "label_capture_bytes": effective_label_capture_bytes,
                "label_confirmed": bool(effective_label_confirmed),
                "uploaded_label_bytes": effective_uploaded_image_bytes,
                "scanned_barcode_value": effective_scanned_barcode_value,
                "barcode_input": effective_barcode_input,
                "barcode_upload": effective_barcode_upload,
                "product_url": effective_product_url,
                "manual_text": effective_manual_text,
            }
            st.session_state[analyze_is_running_key] = True
            st.rerun()

        analyze = bool(st.session_state.get(analyze_is_running_key, False)) and isinstance(
            st.session_state.get(analyze_pending_request_key),
            dict,
        )

        if analyze:
            pending_req = dict(st.session_state.get(analyze_pending_request_key) or {})
            pending_camera_bytes = pending_req.get("camera_bytes") if isinstance(pending_req.get("camera_bytes"), (bytes, bytearray)) else b""
            pending_uploaded_image_bytes = pending_req.get("uploaded_image_bytes") if isinstance(pending_req.get("uploaded_image_bytes"), (bytes, bytearray)) else b""
            pending_label_capture_bytes = pending_req.get("label_capture_bytes") if isinstance(pending_req.get("label_capture_bytes"), (bytes, bytearray)) else b""
            pending_label_confirmed = bool(pending_req.get("label_confirmed", False))
            pending_uploaded_label_bytes = pending_req.get("uploaded_label_bytes") if isinstance(pending_req.get("uploaded_label_bytes"), (bytes, bytearray)) else b""
            pending_scanned_barcode_value = str(pending_req.get("scanned_barcode_value", "") or "")
            pending_barcode_input = str(pending_req.get("barcode_input", "") or "")
            pending_barcode_upload = pending_req.get("barcode_upload")
            pending_product_url = str(pending_req.get("product_url", "") or "")
            pending_manual_text = str(pending_req.get("manual_text", "") or "")

            extracted_chunks: list[tuple[str, str]] = []
            source_details: dict[str, dict[str, str]] = {}
            image_locked_payload: dict[str, Any] | None = None
            image_locked_text = ""
            image_ocr_text_fallback = ""
            image_gate_failed = False

            with st.status("Processing", expanded=True) as status:
                status.write("Step 1/4: Collecting input")

                image_bytes = None
                image_provider_label = ""
                image_fallback_used = False
                if pending_camera_bytes:
                    image_bytes = bytes(pending_camera_bytes)
                elif pending_label_capture_bytes and pending_label_confirmed:
                    image_bytes = bytes(pending_label_capture_bytes)
                elif pending_uploaded_label_bytes:
                    image_bytes = bytes(pending_uploaded_label_bytes)
                elif pending_uploaded_image_bytes:
                    image_bytes = bytes(pending_uploaded_image_bytes)

                if image_bytes:
                    status.write("Step 2/4: Extracting label text via Blockbrain vision model")
                    ocr_text, image_provider_label = extract_image_text_with_blockbrain_best_effort(
                        image_bytes,
                        model=selected_vision_model or None,
                    )
                    image_fallback_used = "(resized_jpeg)" in image_provider_label
                    if not ocr_text:
                        status.write("Vision extraction failed; trying local OCR fallback")
                        local_ocr_text = try_tesseract_ocr(image_bytes)
                        if local_ocr_text and local_ocr_text.strip():
                            ocr_text = local_ocr_text.strip()
                            image_provider_label = "Local OCR fallback (Tesseract)"
                            image_fallback_used = True
                    if ocr_text:
                        image_ocr_text_fallback = ocr_text
                        ocr_gate = extraction_gate_report(ocr_text)
                        if ocr_gate["passed"]:
                            extracted_chunks.append(("image", ocr_text))
                            source_details["image"] = {
                                "provider": str(LAST_VISION_PROVIDER or image_provider_label or "Image OCR").strip(),
                                "reason": "Image OCR passed deterministic quality gates.",
                                "url": "",
                            }
                            # Hard guard: if the image parse already yields a strong structured table,
                            # lock analysis to image-only to prevent URL/manual contamination.
                            image_payload_probe = build_structured_nutrients_json(ocr_text)
                            image_rows_probe = list(image_payload_probe.get("nutrients", []) or [])
                            image_dosed_rows = sum(
                                1
                                for row in image_rows_probe
                                if row.get("component")
                                and row.get("dose_value") is not None
                                and str(row.get("dose_unit", "") or "").strip().lower() in ALLOWED_DOSE_UNITS
                            )
                            if _has_structured_table_cues(ocr_text) and image_dosed_rows >= 12:
                                image_locked_payload = image_payload_probe
                                image_locked_payload["selected_input_source"] = "image"
                                image_locked_payload["selected_input_provider"] = str(
                                    LAST_VISION_PROVIDER or image_provider_label or "Image OCR"
                                ).strip()
                                image_locked_payload["selected_input_reason"] = (
                                    "Image-only lock enabled after strong structured table extraction."
                                )
                                image_locked_payload["selected_input_url"] = ""
                                image_locked_text = ocr_text
                                status.write(
                                    "Image-only lock enabled "
                                    f"(structured_table=True, dosed_rows={image_dosed_rows})"
                                )
                            if LAST_VISION_PROVIDER:
                                st.success(f"Image text extracted via {LAST_VISION_PROVIDER}")
                            else:
                                st.success("Image text extracted")
                            status.write(
                                f"Image gate check passed (score={ocr_gate['score']}, doses={ocr_gate['dose_hits']})"
                            )
                        else:
                            image_gate_failed = True
                            st.warning(
                                "Image extraction looked low-quality by deterministic gates; it was not used."
                            )
                    else:
                        _bb_err = LAST_BLOCKBRAIN_ERROR
                        st.warning("Could not extract text from image" + (f" — {_bb_err}" if _bb_err else ""))

                    if image_provider_label:
                        if image_fallback_used:
                            st.caption(f"Image OCR route: {image_provider_label} (retry)")
                        else:
                            st.caption(f"Image OCR route: {image_provider_label} (primary)")

                if image_locked_payload is None:
                    manual_barcode_value = _normalize_barcode_digits(pending_barcode_input)
                    barcode_value = manual_barcode_value or pending_scanned_barcode_value
                    barcode_method = "manual" if manual_barcode_value else str(st.session_state.get(barcode_scan_method_key, "camera") or "camera")
                    barcode_image_bytes = None
                    if barcode_camera is not None:
                        barcode_image_bytes = barcode_camera.getvalue()
                    elif pending_barcode_upload is not None:
                        try:
                            barcode_image_bytes = pending_barcode_upload.read()
                        except Exception:
                            barcode_image_bytes = None

                    if not barcode_value and barcode_image_bytes:
                        detected_barcode, barcode_method = detect_barcode_from_image(barcode_image_bytes)
                        barcode_value = _normalize_barcode_digits(detected_barcode)

                    if barcode_value:
                        status.write("Step 3/5: Resolving product from barcode")
                        barcode_cache = st.session_state.setdefault("barcode_parse_cache", {})
                        cached_barcode_item = barcode_cache.get(barcode_value, {})

                        barcode_text = ""
                        barcode_provider = ""
                        barcode_reason = ""
                        barcode_product_url = ""
                        if isinstance(cached_barcode_item, dict):
                            barcode_text = str(cached_barcode_item.get("text", "") or "").strip()
                            barcode_provider = str(cached_barcode_item.get("provider", "") or "").strip()
                            barcode_reason = str(cached_barcode_item.get("reason", "") or "").strip()
                            barcode_product_url = str(cached_barcode_item.get("product_url", "") or "").strip()

                        if not barcode_text:
                            barcode_text, barcode_provider, barcode_reason, barcode_product_url = extract_supplement_text_from_barcode(barcode_value)
                            if barcode_text:
                                barcode_cache[barcode_value] = {
                                    "text": barcode_text,
                                    "provider": barcode_provider,
                                    "reason": barcode_reason,
                                    "product_url": barcode_product_url,
                                }

                        if barcode_text:
                            if _barcode_data_needs_label_retry(barcode_text, barcode_provider, barcode_reason):
                                st.warning(
                                    "Barcode was resolved, but the nutrition data looked incomplete for micronutrient analysis. "
                                    "Please scan or upload the supplement facts label for accurate results."
                                )
                                status.write(
                                    "Barcode resolved with low-confidence nutrient detail; waiting for label image input."
                                )
                                if barcode_product_url:
                                    st.caption(f"Barcode product source: {barcode_product_url}")
                                _clear_barcode_lock_after_failure()
                            else:
                                extracted_chunks.append(("barcode", barcode_text))
                                source_details["barcode"] = {
                                    "provider": str(barcode_provider or "barcode lookup").strip(),
                                    "reason": str(barcode_reason or "").strip(),
                                    "url": str(barcode_product_url or "").strip(),
                                }
                                provider_label = barcode_provider or "barcode lookup"
                                st.success(f"Barcode {barcode_value} resolved via {provider_label}")
                                status.write(f"Barcode source accepted (method={barcode_method})")
                                if barcode_product_url:
                                    st.caption(f"Barcode product source: {barcode_product_url}")
                        else:
                            st.warning(f"Could not resolve product from barcode {barcode_value}.")
                            if barcode_reason:
                                status.write(f"Barcode lookup note: {barcode_reason}")
                            _clear_barcode_lock_after_failure()

                if pending_product_url.strip() and image_locked_payload is None:
                    status.write("Step 4/5: Parsing product link (local deterministic parser)")
                    url_key = pending_product_url.strip()
                    url_parse_cache = st.session_state.setdefault("url_parse_cache", {})
                    cached_item = url_parse_cache.get(url_key, "")
                    cached_url_text = ""
                    cached_provider = ""
                    cached_reason = ""
                    if isinstance(cached_item, dict):
                        cached_url_text = str(cached_item.get("text", "") or "").strip()
                        cached_provider = str(cached_item.get("provider", "") or "").strip()
                        cached_reason = str(cached_item.get("reason", "") or "").strip()
                    else:
                        cached_url_text = str(cached_item or "").strip()
                    if cached_url_text:
                        url_text = cached_url_text
                        if cached_provider:
                            LAST_TEXT_PROVIDER = cached_provider
                        if cached_reason:
                            LAST_URL_PARSE_REASON = cached_reason
                        st.caption("Reused cached parsing for this link")
                    else:
                        url_text = extract_supplement_text_from_url(url_key)
                        if url_text:
                            url_parse_cache[url_key] = {
                                "text": url_text,
                                "provider": LAST_TEXT_PROVIDER,
                                "reason": LAST_URL_PARSE_REASON,
                            }
                    if url_text:
                        url_gate = extraction_gate_report(url_text)
                        if url_gate["passed"]:
                            extracted_chunks.append(("url", url_text))
                            source_details["url"] = {
                                "provider": str(LAST_TEXT_PROVIDER or "Link parser").strip(),
                                "reason": str(LAST_URL_PARSE_REASON or "Link content passed deterministic quality gates.").strip(),
                                "url": url_key,
                            }
                            if LAST_TEXT_PROVIDER:
                                st.success(f"Link content parsed via {LAST_TEXT_PROVIDER}")
                            else:
                                st.success("Link content parsed")
                            status.write(
                                f"Link gate check passed (score={url_gate['score']}, doses={url_gate['dose_hits']})"
                            )
                            if LAST_URL_PARSE_REASON:
                                escaped_reason = html.escape(LAST_URL_PARSE_REASON)
                                st.markdown(
                                    (
                                        "<span title='"
                                        f"{escaped_reason}"
                                        "' style='cursor:help; text-decoration: underline dotted;'>"
                                        "Link parse QA detail"
                                        "</span>"
                                    ),
                                    unsafe_allow_html=True,
                                )
                        else:
                            st.warning(
                                "Link extraction failed deterministic quality gates and was not used."
                            )
                    else:
                        st.warning("Could not parse link content")

                if pending_manual_text.strip() and image_locked_payload is None:
                    manual_text_clean = pending_manual_text.strip()
                    manual_gate = extraction_gate_report(manual_text_clean)
                    extracted_chunks.append(("manual", manual_text_clean))
                    source_details["manual"] = {
                        "provider": "Manual input",
                        "reason": "User-entered supplement details.",
                        "url": "",
                    }
                    if manual_gate["passed"]:
                        status.write(
                            f"Manual text gate check passed (score={manual_gate['score']}, doses={manual_gate['dose_hits']})"
                        )
                    else:
                        status.write(
                            "Manual text gate check flagged low structure; continuing because it is user-entered input."
                        )

                combined = "\n\n".join([x for _, x in extracted_chunks if x.strip()])

                if (
                    not combined
                    and image_locked_payload is None
                    and image_ocr_text_fallback.strip()
                    and image_gate_failed
                ):
                    status.write("Image text did not pass quality gates; using it as a fallback source.")
                    extracted_chunks.append(("image_fallback", image_ocr_text_fallback.strip()))
                    source_details["image_fallback"] = {
                        "provider": str(image_provider_label or "Image OCR fallback").strip(),
                        "reason": "Used fallback image OCR text because no stronger source was available.",
                        "url": "",
                    }
                    combined = "\n\n".join([x for _, x in extracted_chunks if x.strip()])

                if image_locked_payload is not None:
                    status.write("URL/manual inputs skipped because image-only lock is active.")
                    combined = image_locked_text

                if not combined:
                    st.session_state["analysis_ready"] = False
                    st.session_state["analysis_components"] = []
                    st.session_state["analysis_combined_text"] = ""
                    st.session_state["analysis_structured_debug"] = {}
                    st.session_state[analyze_is_running_key] = False
                    st.session_state[analyze_pending_request_key] = None
                    status.update(label="No input detected", state="error")
                    st.caption(
                        "Input diagnostics: "
                        f"pending_uploaded_image_bytes={len(pending_uploaded_image_bytes or b'')}, "
                        f"pending_uploaded_label_bytes={len(pending_uploaded_label_bytes or b'')}, "
                        f"pending_label_capture_bytes={len(pending_label_capture_bytes or b'')}, "
                        f"pending_scanned_barcode={'yes' if pending_scanned_barcode_value else 'no'}, "
                        f"pending_manual_text={'yes' if pending_manual_text.strip() else 'no'}, "
                        f"pending_product_url={'yes' if pending_product_url.strip() else 'no'}"
                    )
                    st.error("Please provide at least one input source: image, barcode, link, or text.")
                else:
                    status.write("Step 5/5: Extracting supplement components")
                    if image_locked_payload is not None:
                        structured_payload = image_locked_payload
                        status.write("Selected extraction source: image (locked)")
                    else:
                    # Evaluate each source independently first to avoid cross-source contamination.
                    # This prevents stale URL/manual content from overriding clean image OCR rows.
                        source_candidates: list[dict[str, Any]] = []
                        for source_label, source_text in extracted_chunks:
                            if not str(source_text or "").strip():
                                continue
                            payload = build_structured_nutrients_json(source_text)
                            nutrients = list(payload.get("nutrients", []) or [])
                            dosed_rows = sum(
                                1
                                for row in nutrients
                                if row.get("component")
                                and row.get("dose_value") is not None
                                and str(row.get("dose_unit", "") or "").strip().lower() in ALLOWED_DOSE_UNITS
                            )
                            try:
                                conf = float(payload.get("confidence", 0.0) or 0.0)
                            except Exception:
                                conf = 0.0
                            source_candidates.append(
                                {
                                    "label": source_label,
                                    "text": source_text,
                                    "payload": payload,
                                    "dosed_rows": dosed_rows,
                                    "confidence": conf,
                                    "has_structured_table": _has_structured_table_cues(source_text),
                                }
                            )

                        if source_candidates:
                            source_priority = {"barcode": 4, "image": 3, "manual": 2, "url": 1}
                            source_candidates.sort(
                                key=lambda c: (
                                    int(c.get("dosed_rows", 0) or 0),
                                    int(bool(c.get("has_structured_table", False))),
                                    float(c.get("confidence", 0.0) or 0.0),
                                    source_priority.get(str(c.get("label", "")), 0),
                                ),
                                reverse=True,
                            )
                            best = source_candidates[0]
                            structured_payload = dict(best.get("payload", {}) or {})
                            combined = str(best.get("text", "") or "")
                            best_label = str(best.get("label", "") or "")
                            best_detail = source_details.get(best_label, {})
                            structured_payload["selected_input_source"] = best_label
                            structured_payload["selected_input_provider"] = str(
                                best_detail.get("provider", best_label)
                            ).strip()
                            structured_payload["selected_input_reason"] = str(
                                best_detail.get("reason", "")
                            ).strip()
                            structured_payload["selected_input_url"] = str(
                                best_detail.get("url", "")
                            ).strip()
                            status.write(
                                "Selected extraction source: "
                                f"{best.get('label', 'n/a')} "
                                f"(dosed_rows={best.get('dosed_rows', 0)}, "
                                f"confidence={best.get('confidence', 0.0)})"
                            )
                        else:
                            structured_payload = build_structured_nutrients_json(combined)
                    components = list(structured_payload.get("nutrients", []) or [])
                    if LAST_TEXT_PROVIDER:
                        status.write(f"Testing info: component parsing used {LAST_TEXT_PROVIDER}")
                    status.write(
                        "Extraction summary: "
                        f"{len(components)} components, source={structured_payload.get('source', 'n/a')}, "
                        f"confidence={structured_payload.get('confidence', 'n/a')}"
                    )
                    status.update(label="Done", state="complete")

                    structured_payload["blockbrain_routing_mode"] = "direct_llm"
                    structured_payload["blockbrain_preferred_text_model"] = selected_text_model
                    structured_payload["blockbrain_preferred_vision_model"] = selected_vision_model
                    structured_payload["blockbrain_runtime_model"] = str(LAST_BLOCKBRAIN_MODEL or "").strip()

                    st.session_state["analysis_ready"] = True
                    st.session_state["analysis_components"] = components
                    st.session_state["analysis_combined_text"] = combined
                    st.session_state["analysis_structured_debug"] = structured_payload
                    st.session_state["analysis_food_match_cache_key"] = ""
                    st.session_state["analysis_food_match_summary"] = []
                    st.session_state["analysis_food_match_details"] = []
                    st.session_state["analysis_food_match_status"] = ""
                    st.session_state["results_show_prices"] = False
                    st.session_state["show_inline_results_fallback"] = False
                    st.session_state["target_tab"] = "📊 Results"
                    st.session_state[analyze_is_running_key] = False
                    st.session_state[analyze_pending_request_key] = None

                    # Always unlock/reset Analyze inputs after submit so all fields are interactive again.
                    _reset_analyze_inputs_after_submit()
                    st.rerun()

        if st.session_state.get("analysis_ready"):
            st.success("Input analyzed. Open the Results tab to review alternatives and cost estimates.")

        go_to_results = st.button(
            "Go to Results",
            use_container_width=True,
            key="analyze_go_results",
            disabled=not bool(st.session_state.get("analysis_ready", False)),
            help="Becomes available after Analyze input completes.",
        )
        if go_to_results:
            st.session_state["target_tab"] = "📊 Results"
            st.rerun()

        if st.session_state.get("analysis_ready", False):
            with st.expander("Results fallback (for mobile/tab-switch issues)", expanded=False):
                st.caption(
                    "If your browser does not switch tabs after tapping 'Go to Results', "
                    "load your alternatives directly here."
                )
                if st.button(
                    "Load alternatives here",
                    key="analyze_load_inline_results",
                    use_container_width=True,
                ):
                    st.session_state["show_inline_results_fallback"] = True

                if st.session_state.get("show_inline_results_fallback", False):
                    inline_components = st.session_state.get("analysis_components", [])
                    if not inline_components:
                        st.info("No extracted components yet. Run Analyze first.")
                    else:
                        inline_cache_key = json.dumps(
                            {
                                "schema_version": FOOD_MATCH_CACHE_SCHEMA_VERSION,
                                "components": [
                                    {
                                        "component": normalize_lookup_key(str(c.get("component", ""))),
                                        "dose_value": c.get("dose_value"),
                                        "dose_unit": str(c.get("dose_unit", "") or "").lower(),
                                    }
                                    for c in inline_components
                                ],
                            },
                            sort_keys=True,
                        )
                        if st.session_state.get("analysis_food_match_cache_key", "") != inline_cache_key:
                            food_match_summary, food_match_details, food_match_status = build_ai_food_matches(inline_components)
                            st.session_state["analysis_food_match_cache_key"] = inline_cache_key
                            st.session_state["analysis_food_match_summary"] = food_match_summary
                            st.session_state["analysis_food_match_details"] = food_match_details
                            st.session_state["analysis_food_match_status"] = food_match_status
                        else:
                            food_match_summary = st.session_state.get("analysis_food_match_summary", [])
                            food_match_details = st.session_state.get("analysis_food_match_details", [])
                            food_match_status = st.session_state.get("analysis_food_match_status", "")

                        if food_match_status != "ok":
                            st.warning("AI mapping is unavailable. Showing local USDA fallback alternatives when possible.")

                        if not food_match_details:
                            st.info("No whole-food alternatives found yet.")
                        else:
                            shown = 0
                            for entry in food_match_details:
                                foods = entry.get("foods", []) if isinstance(entry.get("foods"), list) else []
                                if not foods:
                                    continue
                                shown += 1
                                component_name = str(entry.get("component", "") or "").title() or "Component"
                                st.markdown(f"**{component_name}**")
                                for food in foods[:3]:
                                    try:
                                        amt = float(food.get("amount_per_100g", 0.0) or 0.0)
                                    except Exception:
                                        amt = 0.0
                                    unit = str(food.get("unit", "") or "")
                                    amt_txt, unit_txt = format_amount_unit_for_display(amt, unit)
                                    st.write(
                                        f"- {str(food.get('food_description', '') or '').strip()} "
                                        f"({amt_txt} {unit_txt}/100g)"
                                    )
                            if shown == 0:
                                st.info("No whole-food alternatives found yet.")

    _analysis_components = st.session_state.get("analysis_components", [])
    _combined = st.session_state.get("analysis_combined_text", "")


def _render_results_tab(tab_results: Any) -> None:
    import streamlit as st

    components = st.session_state.get("analysis_components", [])
    combined = st.session_state.get("analysis_combined_text", "")
    with tab_results:
        st.markdown("### Brief Recommendation Summary")
        st.caption("Quick, user-friendly guidance first. Detailed nutrient matching remains below.")
        summary_placeholder = st.container()
        analysis_debug = st.session_state.get("analysis_structured_debug", {})
        analysis_source = str(analysis_debug.get("selected_input_source", "") or "").strip()
        analysis_provider = str(analysis_debug.get("selected_input_provider", "") or "").strip()
        analysis_reason = str(analysis_debug.get("selected_input_reason", "") or "").strip()
        analysis_url = str(analysis_debug.get("selected_input_url", "") or "").strip()
        analysis_route_mode = str(analysis_debug.get("blockbrain_routing_mode", "") or "").strip()
        analysis_runtime_model = str(analysis_debug.get("blockbrain_runtime_model", "") or "").strip()
        analysis_text_model_pref = str(analysis_debug.get("blockbrain_preferred_text_model", "") or "").strip()
        analysis_vision_model_pref = str(analysis_debug.get("blockbrain_preferred_vision_model", "") or "").strip()

        if analysis_source:
            source_caption = f"Analysis source: {analysis_source}"
            if analysis_provider:
                source_caption += f" via {analysis_provider}"
            if analysis_reason:
                source_caption += f". {analysis_reason}"
            st.caption(source_caption)
            if analysis_url:
                st.caption(f"Analysis source URL: {analysis_url}")
            if analysis_route_mode or analysis_runtime_model:
                routing_bits: list[str] = []
                if analysis_route_mode:
                    routing_bits.append(f"mode={analysis_route_mode}")
                if analysis_runtime_model:
                    routing_bits.append(f"resolved_model={analysis_runtime_model}")
                st.caption("Blockbrain runtime: " + ", ".join(routing_bits))
            if analysis_text_model_pref or analysis_vision_model_pref:
                st.caption(
                    "Blockbrain preferences: "
                    f"text={analysis_text_model_pref or 'platform-default'}, "
                    f"vision={analysis_vision_model_pref or 'platform-default'}"
                )

        if combined:
            if st.button(
                "Re-parse extracted text with latest OCR rules",
                key="reparse_current_analysis_text",
                use_container_width=True,
            ):
                refreshed_payload = build_structured_nutrients_json(combined)
                refreshed_components = list(refreshed_payload.get("nutrients", []) or [])
                st.session_state["analysis_components"] = refreshed_components
                st.session_state["analysis_structured_debug"] = refreshed_payload
                st.session_state["analysis_food_match_cache_key"] = ""
                st.session_state["analysis_food_match_summary"] = []
                st.session_state["analysis_food_match_details"] = []
                st.session_state["analysis_food_match_status"] = ""
                st.rerun()

        st.divider()
        st.markdown("### More Detailed Analysis")
        st.subheader("Your Supplement vs Whole Food Alternative")

        if not components:
            st.info("Run Analyze first to populate this tab.")
            if st.session_state.get("analysis_ready", False) and combined:
                st.warning(
                    "Input text was captured but no usable nutrient rows were extracted. "
                    "Try a clearer label photo or add manual text for best results."
                )
        else:
            components_cache_key = json.dumps(
                {
                    "schema_version": FOOD_MATCH_CACHE_SCHEMA_VERSION,
                    "components": [
                        {
                            "component": normalize_lookup_key(str(c.get("component", ""))),
                            "dose_value": c.get("dose_value"),
                            "dose_unit": str(c.get("dose_unit", "") or "").lower(),
                        }
                        for c in components
                    ],
                },
                sort_keys=True,
            )

            if st.session_state.get("analysis_food_match_cache_key", "") != components_cache_key:
                map_progress = st.progress(0, text="Mapping supplement components to whole-food nutrients...")
                map_progress.progress(35, text="Resolving nutrient mappings...")
                food_match_summary, food_match_details, food_match_status = build_ai_food_matches(components)
                map_progress.progress(100, text="Mapping ready")
                map_progress.empty()
                st.session_state["analysis_food_match_cache_key"] = components_cache_key
                st.session_state["analysis_food_match_summary"] = food_match_summary
                st.session_state["analysis_food_match_details"] = food_match_details
                st.session_state["analysis_food_match_status"] = food_match_status
            else:
                food_match_summary = st.session_state.get("analysis_food_match_summary", [])
                food_match_details = st.session_state.get("analysis_food_match_details", [])
                food_match_status = st.session_state.get("analysis_food_match_status", "")

            detail_by_component = {normalize_lookup_key(str(d.get("component", ""))): d for d in food_match_details}

            profiles = load_dietary_profiles()
            profile_by_id, _, profile_label_by_id = _dietary_profile_maps(profiles)
            profile_ids = list(profile_by_id.keys())
            default_profile_id = _default_dietary_profile_id(profiles)

            selected_profile_id, selected_profile = _resolve_results_dietary_profile_state(
                profiles,
                st.session_state,
            )
            selected_profile_label = profile_label_by_id.get(selected_profile_id, "No restriction")
            use_llm_dietary_adjudication = bool(st.session_state.get("dietary_use_llm_adjudication", False))

            selected_country = str(st.session_state.get("price_region", "Germany"))
            if selected_country not in COUNTRY_PRICE_CONFIG:
                selected_country = "Germany"
            default_currency = COUNTRY_PRICE_CONFIG.get(selected_country, {}).get("currency", "USD")
            selected_currency = str(st.session_state.get("price_currency", default_currency))
            if selected_currency not in ["EUR", "USD", "GBP", "INR", "BRL"]:
                selected_currency = default_currency if default_currency in ["EUR", "USD", "GBP", "INR", "BRL"] else "USD"
            selected_market = str(st.session_state.get("price_market", "Auto"))
            if selected_market not in ["Auto", "Rewe", "Walmart"]:
                selected_market = "Auto"
            enable_live_price_fallback = bool(st.session_state.get("enable_live_price_fallback", False))
            use_serpapi = bool(st.session_state.get("use_serpapi_pricing", bool(SERPAPI_API_KEY)))
            use_dataforseo = bool(st.session_state.get("use_dataforseo_pricing", bool(DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD)))

            if food_match_status != "ok":
                st.info("AI whole-food mapping is currently unavailable. Check Blockbrain/API configuration and try again.")

            mapped_components = 0
            for item in components:
                ckey = normalize_lookup_key(str(item.get("component", "")))
                d = detail_by_component.get(ckey)
                if d and d.get("foods"):
                    mapped_components += 1

            overview_cols = st.columns(2)
            overview_cols[0].metric("Components", len(components))
            overview_cols[1].metric("Whole-food alternatives found", f"{mapped_components}/{len(components)}")
            st.caption(
                f"{mapped_components}/{len(components)} whole food alternatives found."
            )
            st.caption("Alternatives are sorted by highest nutrient concentration per 100g.")
            st.caption(
                "Legend: ✅ whole-food alternative found (mapped) • ❌ no whole-food alternative found (unmapped) "
                "• ⚠️ alternatives exist but are filtered by your profile"
            )

            # Pricing is opt-in so whole-food alternatives render instantly. Price
            # lookups (local DB miss -> optional live web/LLM) only run on demand.
            show_prices = bool(st.session_state.get("results_show_prices", False))
            if not show_prices:
                if st.button(
                    "💶 Estimate whole-food costs",
                    use_container_width=True,
                    key="results_estimate_costs_btn",
                    help="Optional. Loads price estimates for the selected whole foods.",
                ):
                    st.session_state["results_show_prices"] = True
                    st.rerun()
                st.caption("Cost estimates are optional and load on demand — alternatives above are ready now.")

            total_cost = 0.0
            priced_rows = 0
            meal_component_candidates: list[dict[str, Any]] = []
            selected_component_matches: list[dict[str, Any]] = []
            pending_price_rows: list[dict[str, Any]] = []
            unmapped_components: list[dict[str, str]] = []
            prep_progress = st.progress(0, text="Preparing whole-food matches and estimated costs...")
            total_rows = max(1, len(components))

            no_whole_food_count = 0
            if food_match_status == "ok":
                for item in components:
                    component_key_probe = normalize_lookup_key(str(item.get("component", "")))
                    detail_probe = detail_by_component.get(component_key_probe)
                    foods_raw_probe = detail_probe.get("foods", []) if detail_probe else []
                    if not detail_probe or not foods_raw_probe:
                        no_whole_food_count += 1

            with st.expander("1) Whole Food Alternative Found", expanded=False):
                mapped_section = st.container()

            unmapped_section = None
            if no_whole_food_count > 0:
                with st.expander("2) No Whole Food Alternative Found", expanded=False):
                    unmapped_section = st.container()

            for index, item in enumerate(components):
                prep_progress.progress(int((index / total_rows) * 100), text=f"Preparing component {index + 1}/{len(components)}...")
                component_raw = str(item.get("component", "")).strip()
                component_key = normalize_lookup_key(component_raw)
                dose_value = item.get("dose_value")
                dose_unit = str(item.get("dose_unit") or "")

                component_display = component_raw.title() if component_raw else "Not available"
                dose_label = f"{format_float(float(dose_value))} {dose_unit}" if dose_value is not None else "Not available"
                detail = detail_by_component.get(component_key)
                foods_raw = detail.get("foods", []) if detail else []
                foods = apply_food_filters(
                    foods_raw,
                    selected_profile,
                    use_llm_adjudication=use_llm_dietary_adjudication,
                )
                status_chip = (
                    "Whole-food alternative found"
                    if foods
                    else ("Alternatives filtered by your profile" if foods_raw else "No whole-food alternative")
                )
                status_symbol = "✅" if foods else ("⚠️" if foods_raw else "❌")

                if food_match_status == "ok" and (not detail or not foods_raw):
                    unmapped_components.append(
                        {
                            "component": component_display,
                            "dose": dose_label,
                            "reason": "No whole-food alternative found from AI retrieval",
                        }
                    )

                is_no_whole_food = food_match_status == "ok" and (not detail or not foods_raw)
                target_section = unmapped_section if (is_no_whole_food and unmapped_section is not None) else mapped_section
                with target_section:
                    with st.expander(f"{status_symbol} {component_display} • {dose_label}", expanded=False):
                        st.markdown(
                            f"**Supplement dose:** <span class='linked-value-chip'>{dose_label}</span>",
                            unsafe_allow_html=True,
                        )
                        if detail and detail.get("proxy_rationale"):
                            st.caption(f"Proxy note: {detail['proxy_rationale']}")

                        if food_match_status != "ok":
                            st.warning("AI mapping unavailable")
                            continue

                        if not detail:
                            st.info("No whole-food alternative found from AI retrieval")
                            continue

                        if not foods:
                            if foods_raw:
                                st.info("No alternatives left after dietary restriction filtering")
                            else:
                                st.info("No ranked alternatives found")
                            continue

                        removed_count = max(0, len(foods_raw) - len(foods))
                        if removed_count > 0:
                            st.caption(f"Dietary filtering removed {removed_count} option(s) for this component.")

                        option_labels: list[str] = []
                        display_foods: list[dict[str, Any]] = []
                        for food in foods:
                            try:
                                amount_per_100g = float(food.get("amount_per_100g", 0.0) or 0.0)
                            except Exception:
                                amount_per_100g = 0.0
                            unit_raw = str(food.get("unit", "") or "")
                            amt, display_unit = format_amount_unit_for_dropdown(amount_per_100g, unit_raw)
                            if not amt:
                                continue
                            option_labels.append(
                                f"{food.get('food_description', '')} ({amt} {display_unit}/100g)"
                            )
                            display_foods.append(food)

                        if not option_labels:
                            st.info("No alternatives with non-zero measurable concentration per 100g")
                            continue

                        selected_label = st.selectbox(
                            "Whole food alternative",
                            options=option_labels,
                            index=0,
                            key=(
                                f"alt_select_cell_{index}_{component_key}_"
                                f"{normalize_lookup_key(selected_profile_id)}"
                            ),
                        )
                        selected_idx = option_labels.index(selected_label)
                        selected_food = display_foods[selected_idx]
                        selected_amt = float(selected_food.get("amount_per_100g", 0.0))
                        selected_unit = str(selected_food.get("unit", ""))

                        grams_needed = grams_needed_to_match_dose(
                            dose_value,
                            dose_unit,
                            selected_amt,
                            selected_unit,
                            component_name=component_raw,
                        )

                        match_label = "Not available"
                        if grams_needed is not None:
                            match_label = f"~{format_float(grams_needed)} g"
                        st.markdown(
                            f"**Amount needed to match dose:** <span class='linked-value-chip'>{match_label}</span>",
                            unsafe_allow_html=True,
                        )
                        if grams_needed is not None:
                            st.caption(format_weight_equivalents(float(grams_needed)))

                            whole_units_hint = estimate_whole_food_units(
                                str(selected_food.get("food_description", "") or ""),
                                float(grams_needed),
                            )
                            if whole_units_hint:
                                st.caption(whole_units_hint)

                            volume_units_hint = estimate_volume_units(
                                str(selected_food.get("food_description", "") or ""),
                                float(grams_needed),
                            )
                            if volume_units_hint:
                                st.caption(volume_units_hint)

                        meal_component_candidates.append(
                            {
                                "component": component_display,
                                "dose_value": dose_value,
                                "dose_unit": dose_unit,
                                "foods": foods,
                                "selected_food_name": str(selected_food.get("food_description", "") or ""),
                                "selected_grams_needed": float(grams_needed) if grams_needed is not None else None,
                            }
                        )

                        selected_match_row = {
                            "component": component_display,
                            "food_description": str(selected_food.get("food_description", "") or ""),
                            "grams_needed": grams_needed,
                            "price_per_kg": None,
                            "currency": selected_currency,
                        }
                        selected_component_matches.append(selected_match_row)

                        if show_prices:
                            cost_slot = st.empty()
                            audit_slot = st.empty()

                            if grams_needed is None or grams_needed <= 0:
                                cost_slot.markdown("**Estimated cost for required amount:** Not available")
                            else:
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
                                pending_price_rows.append(
                                    {
                                        "cache_key": cache_key,
                                        "food_name": str(selected_food.get("food_description", "")),
                                        "country": selected_country,
                                        "currency": selected_currency,
                                        "market": selected_market,
                                        "enable_live": enable_live_price_fallback,
                                        "grams_needed": float(grams_needed),
                                        "ean_hint": ean_hint,
                                        "use_serpapi": use_serpapi,
                                        "use_dataforseo": use_dataforseo,
                                        "cost_slot": cost_slot,
                                        "audit_slot": audit_slot,
                                        "selected_row": selected_match_row,
                                    }
                                )

            if show_prices and pending_price_rows:
                price_cache: dict[str, Any] = st.session_state.get("price_cache", {})
                futures: dict[Any, dict[str, Any]] = {}

                rows_to_fetch: list[dict[str, Any]] = []
                for row in pending_price_rows:
                    cached = price_cache.get(row["cache_key"])
                    if cached and cached.get("price_per_kg") is not None:
                        row["price_info"] = cached
                    else:
                        rows_to_fetch.append(row)

                if rows_to_fetch:
                    worker_count = min(8, max(1, len(rows_to_fetch)))
                    with ThreadPoolExecutor(max_workers=worker_count) as executor:
                        for row in rows_to_fetch:
                            future = executor.submit(
                                get_food_price_estimate,
                                row["food_name"],
                                row["country"],
                                row["currency"],
                                row["market"],
                                row["enable_live"],
                                row["grams_needed"],
                                row["ean_hint"],
                                row["use_serpapi"],
                                row["use_dataforseo"],
                            )
                            futures[future] = row

                        for future in as_completed(futures):
                            row = futures[future]
                            try:
                                price_info = future.result()
                            except Exception:
                                price_info = None
                            row["price_info"] = price_info
                            if isinstance(price_info, dict) and price_info.get("price_per_kg") is not None:
                                price_cache[row["cache_key"]] = price_info

                    st.session_state["price_cache"] = price_cache

                for row in pending_price_rows:
                    price_info = row.get("price_info")
                    cost_slot = row.get("cost_slot")
                    audit_slot = row.get("audit_slot")
                    grams_needed = float(row.get("grams_needed", 0.0) or 0.0)
                    selected_row = row.get("selected_row") or {}

                    cost_label = "Not available"
                    if isinstance(price_info, dict) and price_info.get("price_per_kg") is not None:
                        try:
                            price_per_kg = float(price_info.get("price_per_kg"))
                            required_cost = (grams_needed / 1000.0) * price_per_kg
                            symbol = CURRENCY_SYMBOL.get(
                                str(price_info.get("currency", selected_currency)),
                                str(price_info.get("currency", selected_currency)),
                            )
                            source = str(price_info.get("source", "price db"))
                            cost_label = f"~{symbol}{format_float(required_cost)} ({source})"
                            total_cost += required_cost
                            priced_rows += 1
                            selected_row["price_per_kg"] = price_per_kg
                            selected_row["currency"] = str(price_info.get("currency", selected_currency) or selected_currency)
                        except Exception:
                            cost_label = "Not available"

                    cost_slot.markdown(f"**Estimated cost for required amount:** {cost_label}")

                    if cost_label != "Not available" and isinstance(price_info, dict):
                        score = format_float(float(price_info.get("final_score", 0.0)), 3)
                        match_method = str(price_info.get("match_method", "title_similarity") or "title_similarity")
                        confidence = str(price_info.get("confidence", "low") or "low")
                        ppk = format_float(float(price_info.get("price_per_kg", 0.0)), 2)
                        curr = str(price_info.get("currency", selected_currency) or selected_currency)
                        audit_slot.caption(
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
                            audit_slot.caption(" | ".join(short))

            prep_progress.progress(100, text="Alternative matching and pricing ready")
            prep_progress.empty()

            auto_consolidated = build_auto_consolidated_food_plan(meal_component_candidates, max_foods=10)
            combined_summary = summarize_combined_food_coverage(selected_component_matches)
            if int(combined_summary.get("covered_components", 0) or 0) > int(auto_consolidated.get("covered_components", 0) or 0):
                manual_rows = combined_summary.get("rows", []) or []
                existing_sunlight_note = str(auto_consolidated.get("sunlight_note", "") or "")
                auto_consolidated = {
                    "rows": [
                        {
                            "food": str(r.get("food", "") or ""),
                            "components": list(r.get("components", []) or []),
                            "required_grams": float(r.get("required_grams", 0.0) or 0.0),
                        }
                        for r in manual_rows[:10]
                    ],
                    "total_components": int(combined_summary.get("total_components", 0) or 0),
                    "covered_components": int(combined_summary.get("covered_components", 0) or 0),
                    "uncovered_components": [],
                    "sunlight_note": existing_sunlight_note,
                }

            summary_rows = auto_consolidated.get("rows", []) or []
            summary_review = build_food_summary_review(summary_rows)
            redundancy_report = summary_review.get("redundancy_report", []) or []
            merged_rows_for_summary = summary_review.get("merged_rows", []) or []
            review_signature = str(summary_review.get("signature", "") or "")
            if st.session_state.get("summary_review_signature") != review_signature:
                st.session_state["summary_review_signature"] = review_signature
                st.session_state["summary_review_confirmed"] = False

            with summary_placeholder:
                if redundancy_report:
                    st.markdown("### Summary review (human-in-the-loop)")
                    st.warning(
                        "Detected duplicate/redundant food rows. Please confirm these merged totals before generating the summary sentence."
                    )
                    st.dataframe(redundancy_report, use_container_width=True, hide_index=True)
                    review_confirmed = st.checkbox(
                        "I reviewed the duplicate/redundant rows and approve generating the final summary sentence.",
                        key="summary_review_confirmed",
                    )
                    if review_confirmed:
                        top_sentence = format_top_recommendation_sentence(
                            auto_consolidated,
                            max_foods_to_show=10,
                            prepared_rows=merged_rows_for_summary,
                        )
                        st.info(top_sentence)
                    else:
                        st.info("Summary sentence is paused until you confirm the review above.")
                else:
                    top_sentence = format_top_recommendation_sentence(
                        auto_consolidated,
                        max_foods_to_show=10,
                        prepared_rows=merged_rows_for_summary,
                    )
                    st.info(top_sentence)

            if unmapped_components:
                st.caption("Unmapped components are listed above in section 2 with their reason details.")

            st.session_state["meal_component_candidates"] = meal_component_candidates

            with mapped_section:
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
                elif not show_prices:
                    st.caption("Tap 'Estimate whole-food costs' above to load price estimates and the cost comparison.")
                else:
                    st.caption("Total cost is unavailable until at least one row has both dose-match grams and a price source.")

                st.divider()
                st.caption("Adjust filters and pricing options below to recalculate results.")

                with st.expander("Dietary restriction", expanded=False):
                    def _sync_results_dietary_llm_toggle() -> None:
                        st.session_state["dietary_use_llm_adjudication"] = bool(
                            st.session_state.get("results_dietary_use_llm_adjudication_toggle", False)
                        )

                    def _sync_results_dietary_profile() -> None:
                        selected_id, _ = _resolve_dietary_profile_selection(
                            profiles,
                            st.session_state.get("results_dietary_profile_selector", default_profile_id),
                        )
                        st.session_state["results_dietary_profile_selector"] = selected_id
                        st.session_state["global_diet_profile"] = selected_id

                    st.selectbox(
                        "Apply restriction to whole-food alternatives",
                        options=profile_ids,
                        format_func=lambda profile_id: profile_label_by_id.get(profile_id, str(profile_id)),
                        key="results_dietary_profile_selector",
                        on_change=_sync_results_dietary_profile,
                    )
                    selected_profile_id_after = str(st.session_state.get("results_dietary_profile_selector", selected_profile_id) or selected_profile_id)
                    selected_profile_id_after, selected_profile_after = _resolve_dietary_profile_selection(
                        profiles,
                        selected_profile_id_after,
                    )
                    if st.session_state.get("global_diet_profile") != selected_profile_id_after:
                        st.session_state["global_diet_profile"] = selected_profile_id_after
                    if selected_profile_after and selected_profile_after.get("description"):
                        st.caption(f"Profile note: {selected_profile_after.get('description')}")
                    use_llm_dietary_adjudication = st.toggle(
                        "Use Blockbrain AI cross-check for ambiguous dietary matches",
                        value=use_llm_dietary_adjudication,
                        key="results_dietary_use_llm_adjudication_toggle",
                        on_change=_sync_results_dietary_llm_toggle,
                        help="Deterministic keyword/rule blocks stay primary; AI cross-check adds a secondary block layer for uncertain items.",
                    )
                    st.session_state["dietary_use_llm_adjudication"] = bool(use_llm_dietary_adjudication)
                    if use_llm_dietary_adjudication:
                        st.caption(
                            "Mode: Hybrid (deterministic hard-block + limited Blockbrain adjudication for ambiguous items). Slightly slower."
                        )
                    else:
                        st.caption("Mode: Fast deterministic filter (keyword/rule screening only).")
                    st.caption("Filtering is not a medical, allergy, halal, or kosher certification.")

                with st.expander("Pricing settings", expanded=False):
                    country_options = list(COUNTRY_PRICE_CONFIG.keys())
                    country_index = country_options.index(selected_country) if selected_country in country_options else 0
                    st.selectbox(
                        "Price region",
                        country_options,
                        index=country_index,
                        key="price_region",
                    )
                    selected_country_after = str(st.session_state.get("price_region", selected_country))
                    default_currency_after = COUNTRY_PRICE_CONFIG.get(selected_country_after, {}).get("currency", "USD")
                    currency_options = ["EUR", "USD", "GBP", "INR", "BRL"]
                    current_currency = str(st.session_state.get("price_currency", selected_currency))
                    currency_index = currency_options.index(current_currency) if current_currency in currency_options else (
                        currency_options.index(default_currency_after) if default_currency_after in currency_options else 1
                    )
                    st.selectbox(
                        "Currency",
                        currency_options,
                        index=currency_index,
                        key="price_currency",
                    )
                    market_options = ["Auto", "Rewe", "Walmart"]
                    market_index = market_options.index(selected_market) if selected_market in market_options else 0
                    st.selectbox(
                        "Market fallback",
                        market_options,
                        index=market_index,
                        key="price_market",
                    )
                    enable_live_after = st.toggle(
                        "Enable live web/LLM fallback when local price DB has no match",
                        value=enable_live_price_fallback,
                        key="enable_live_price_fallback",
                    )
                    if enable_live_after:
                        st.caption("Mode: Advanced (live web/API fallback enabled; richer but potentially slower)")
                    else:
                        st.caption("Mode: Fast (local price DB only; fastest results)")

                    st.toggle(
                        "Use SerpApi",
                        value=bool(st.session_state.get("use_serpapi_pricing", use_serpapi)),
                        key="use_serpapi_pricing",
                        help="Google Shopping offers via SerpApi",
                        disabled=not enable_live_after,
                    )
                    st.toggle(
                        "Use DataForSEO",
                        value=bool(st.session_state.get("use_dataforseo_pricing", use_dataforseo)),
                        key="use_dataforseo_pricing",
                        help="Google Shopping offers via DataForSEO",
                        disabled=not enable_live_after,
                    )
                    if st.button("Refresh price lookups", key="refresh_price_cache", use_container_width=True):
                        st.session_state["price_cache"] = {}
                        st.caption("Price cache cleared. Next render will fetch fresh prices.")


def _render_meals_tab(tab_meals: Any) -> None:
    import streamlit as st
    with tab_meals:
        st.subheader("Meal ideas from suggested whole foods")
        st.caption(
            "Meals target at least the supplement-equivalent amounts for suggested components; exceeding targets is allowed. The third meal is macro-optimized to your chosen calorie and macro split."
        )

        meal_component_candidates: list[dict[str, Any]] = st.session_state.get("meal_component_candidates", [])
        if not meal_component_candidates:
            st.info("Generate results first so meal anchors can be derived from your selected alternatives.")
        else:
            profiles = load_dietary_profiles()
            _, _, profile_label_by_id = _dietary_profile_maps(profiles)
            selected_profile_id, selected_profile = _resolve_dietary_profile_selection(
                profiles,
                st.session_state.get("global_diet_profile", _default_dietary_profile_id(profiles)),
            )
            selected_profile_label = profile_label_by_id.get(selected_profile_id, "No restriction")
            use_llm_dietary_adjudication = bool(st.session_state.get("dietary_use_llm_adjudication", False))

            selected_country = str(st.session_state.get("price_region", "Germany"))
            selected_currency = str(st.session_state.get("price_currency", "EUR"))
            selected_market = str(st.session_state.get("price_market", "Auto"))
            enable_live_price_fallback = bool(st.session_state.get("enable_live_price_fallback", False))
            use_serpapi = bool(st.session_state.get("use_serpapi_pricing", bool(SERPAPI_API_KEY)))
            use_dataforseo = bool(st.session_state.get("use_dataforseo_pricing", bool(DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD)))

            filter_cols = st.columns([1.4, 1.0])
            filter_cols[0].markdown(f"**Dietary profile:** {selected_profile_label}")
            must_exclude_ingredient = filter_cols[1].text_input(
                "Must exclude ingredient",
                value="",
                key="meal_must_exclude",
                placeholder="e.g. pork",
            )
            if selected_profile and selected_profile.get("description"):
                st.caption(f"Profile note: {selected_profile.get('description')}")

            def _sync_meals_dietary_llm_toggle() -> None:
                st.session_state["dietary_use_llm_adjudication"] = bool(
                    st.session_state.get("meals_dietary_use_llm_adjudication_toggle", False)
                )

            use_llm_dietary_adjudication = st.toggle(
                "Use Blockbrain AI cross-check for ambiguous dietary matches",
                value=use_llm_dietary_adjudication,
                key="meals_dietary_use_llm_adjudication_toggle",
                on_change=_sync_meals_dietary_llm_toggle,
                help="Deterministic keyword/rule blocks stay primary; AI cross-check adds a secondary block layer for uncertain recipes.",
            )
            st.session_state["dietary_use_llm_adjudication"] = bool(use_llm_dietary_adjudication)
            st.caption(
                "Dietary profiles use practical ingredient-keyword screening; optional AI cross-check can further block uncertain recipes."
            )

            st.caption("AI-only mode active: meal suggestions are generated by the selected LLM.")

            st.caption("The meal tab uses three strategy dropdowns: selected whole-food optimized, price optimized, and macronutrient optimized.")

            build_meals = st.button("Generate meal ideas", use_container_width=True, key="build_meal_ideas")

            macro_target_kcal = int(st.session_state.get("macro_target_kcal", 500) or 500)
            macro_pct_protein = int(st.session_state.get("macro_pct_protein", 30) or 30)
            macro_pct_carbs   = int(st.session_state.get("macro_pct_carbs", 50) or 50)
            macro_pct_fat     = int(st.session_state.get("macro_pct_fat", 20) or 20)
            macro_sum_valid   = (macro_pct_protein + macro_pct_carbs + macro_pct_fat) == 100
            price_max_meal_cost = float(st.session_state.get("price_optimized_max_meal_cost", 12.0) or 0.0)

            meal_cache_key = json.dumps(
                {
                    "requirements": [
                        {
                            "component": normalize_lookup_key(str(r.get("component", ""))),
                            "dose_value": r.get("dose_value"),
                            "dose_unit": normalize_lookup_key(str(r.get("dose_unit", ""))),
                            "selected_food": normalize_lookup_key(str(r.get("selected_food_name", ""))),
                            "selected_grams": round(float(r.get("selected_grams_needed", 0.0) or 0.0), 3),
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
                    "profile": normalize_lookup_key(str(selected_profile.get("id", "none") if selected_profile else "none")),
                    "must_exclude": normalize_lookup_key(must_exclude_ingredient),
                    "use_llm_dietary_adjudication": bool(use_llm_dietary_adjudication),
                    "country": normalize_lookup_key(selected_country),
                    "currency": normalize_lookup_key(selected_currency),
                    "macro_kcal": macro_target_kcal,
                    "macro_p": macro_pct_protein,
                    "macro_c": macro_pct_carbs,
                    "macro_f": macro_pct_fat,
                    "price_max_meal_cost": round(price_max_meal_cost, 2),
                },
                sort_keys=True,
            )

            if build_meals and not macro_sum_valid:
                st.warning("Macro-optimized meal settings are invalid. Protein, carbs, and fat must sum to 100 %. Selected whole-food and price-optimized meals will still be generated, but the macro-optimized meal will be skipped.")

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
                        meal_status.write("Generating AI meal candidates")

                        selected_requirements = resolve_selected_meal_requirements(meal_component_candidates)
                        low_grams_requirements = resolve_low_grams_meal_requirements(meal_component_candidates)

                        selected_food_names = [
                            str(r.get("food_name", "") or "").strip() for r in selected_requirements if str(r.get("food_name", "") or "").strip()
                        ]

                        strategy_sets: list[dict[str, Any]] = [
                            {
                                "label": "Selected whole-food meal",
                                "requirements": selected_requirements,
                                "require_selected_food": True,
                                "objective": "selected",
                            },
                            {
                                "label": "Price-optimized meal",
                                "requirements": meal_requirements,
                                "require_selected_food": False,
                                "objective": "price",
                                "price_params": {
                                    "max_meal_cost": float(price_max_meal_cost),
                                    "currency": str(selected_currency),
                                },
                            },
                            {
                                "label": "Macro-optimized meal",
                                "requirements": low_grams_requirements,
                                "require_selected_food": False,
                                "objective": "macro",
                                "macro_params": {
                                    "target_kcal": float(macro_target_kcal),
                                    "pct_protein": float(macro_pct_protein),
                                    "pct_carbs": float(macro_pct_carbs),
                                    "pct_fat": float(macro_pct_fat),
                                    "valid": macro_sum_valid,
                                },
                            },
                        ]

                        strategy_meal_options: dict[str, list[dict[str, Any]]] = {}
                        strategy_fail_reasons: dict[str, str] = {}
                        strategy_ai_meal_cache: dict[str, list[dict[str, Any]]] = {}
                        max_options_per_strategy = 12

                        for strategy in strategy_sets:
                            strategy_label = str(strategy.get("label", "") or "")
                            reqs = strategy.get("requirements", []) or []
                            if not reqs:
                                continue

                            req_signature = json.dumps(
                                [
                                    {
                                        "component": normalize_lookup_key(str(r.get("component", ""))),
                                        "food_name": normalize_lookup_key(str(r.get("food_name", ""))),
                                        "grams_needed": round(float(r.get("grams_needed", 0.0) or 0.0), 4),
                                    }
                                    for r in reqs
                                ],
                                sort_keys=True,
                            )
                            if req_signature in strategy_ai_meal_cache:
                                ai_meals_raw = strategy_ai_meal_cache[req_signature]
                            else:
                                ai_meals_raw = generate_llm_meal_suggestions(reqs, max_results=12)
                                strategy_ai_meal_cache[req_signature] = ai_meals_raw
                            ai_meals = apply_meal_filters(
                                ai_meals_raw,
                                selected_profile,
                                must_exclude_ingredient,
                                use_llm_adjudication=use_llm_dietary_adjudication,
                            )
                            if strategy.get("require_selected_food") and selected_food_names:
                                ai_meals = [m for m in ai_meals if _recipe_contains_any_food(m, selected_food_names)]

                            strategy_options: list[dict[str, Any]] = []
                            objective = str(strategy.get("objective", "") or "")

                            if objective == "selected":
                                ranked_pairs: list[tuple[dict[str, Any], dict[str, float]]] = []
                                for meal in ai_meals:
                                    ranked_pairs.append((meal, _selected_recipe_overlap_metrics(meal, reqs)))
                                ranked_pairs.sort(
                                    key=lambda pair: (
                                        int(pair[1].get("overlap_count", 0.0) or 0.0),
                                        float(pair[1].get("concentration_score", 0.0) or 0.0),
                                        float(pair[1].get("present_grams_total", 0.0) or 0.0),
                                        float(pair[0].get("coverage_ratio", 0.0) or 0.0),
                                    ),
                                    reverse=True,
                                )
                                seen_sel: set[str] = set()
                                for candidate, _ in ranked_pairs:
                                    built = build_selected_whole_food_meal(candidate, reqs, strategy_label)
                                    if not built or not built.get("full_coverage"):
                                        continue
                                    key = normalize_lookup_key(str(built.get("name", "") or ""))
                                    if key in seen_sel:
                                        continue
                                    seen_sel.add(key)
                                    strategy_options.append(built)
                                    if len(strategy_options) >= max_options_per_strategy:
                                        break
                                if not strategy_options:
                                    strategy_fail_reasons[strategy_label] = (
                                        "No AI meal could satisfy the selected whole-food constraints under current filters."
                                    )
                            elif objective == "macro":
                                macro_params = strategy.get("macro_params", {}) or {}
                                if macro_params.get("valid", False):
                                    macro_options, macro_reason = build_macro_optimized_meals(
                                        ai_meals,
                                        reqs,
                                        strategy_label,
                                        float(macro_params.get("target_kcal", 500.0) or 500.0),
                                        float(macro_params.get("pct_protein", 30.0) or 30.0),
                                        float(macro_params.get("pct_carbs", 50.0) or 50.0),
                                        float(macro_params.get("pct_fat", 20.0) or 20.0),
                                        max_results=max_options_per_strategy,
                                    )
                                    strategy_options.extend(macro_options[:max_options_per_strategy])
                                    if not strategy_options and macro_reason:
                                        strategy_fail_reasons[strategy_label] = str(macro_reason)
                                else:
                                    strategy_fail_reasons[strategy_label] = (
                                        "Macronutrient optimization is inactive because protein, carbs, and fat do not sum to 100%."
                                    )
                            elif objective == "price":
                                scaled_candidates: list[dict[str, Any]] = []
                                for candidate in ai_meals:
                                    scaled = scale_recipe_to_requirements(candidate, reqs, strategy_label)
                                    if scaled and scaled.get("full_coverage"):
                                        scaled_candidates.append(scaled)

                                price_params = strategy.get("price_params", {}) or {}
                                max_cost = float(price_params.get("max_meal_cost", 0.0) or 0.0)
                                currency = str(price_params.get("currency", selected_currency) or selected_currency)
                                symbol = CURRENCY_SYMBOL.get(currency, currency)
                                priced_candidates: list[tuple[float, dict[str, Any]]] = []
                                for meal in scaled_candidates:
                                    est = _estimate_recipe_cost(meal, selected_country, selected_currency)
                                    if est is None:
                                        continue
                                    est_cost = float(est)
                                    if max_cost > 0 and est_cost > max_cost:
                                        continue
                                    priced_candidates.append((est_cost, meal))

                                priced_candidates.sort(key=lambda x: (x[0], _recipe_total_grams(x[1])))
                                seen_price: set[str] = set()
                                for est_cost, meal in priced_candidates:
                                    key = normalize_lookup_key(str(meal.get("name", "") or ""))
                                    if key in seen_price:
                                        continue
                                    seen_price.add(key)
                                    strategy_options.append(
                                        {
                                            **meal,
                                            "estimated_recipe_cost": float(est_cost),
                                            "price_cap": float(max_cost),
                                            "price_currency": currency,
                                        }
                                    )
                                    if len(strategy_options) >= max_options_per_strategy:
                                        break

                                if not strategy_options:
                                    strategy_fail_reasons[strategy_label] = (
                                        "No adequate AI price-optimized meal can be generated within your budget cap "
                                        f"({symbol}{format_float(max_cost, 2)}). Increase the cap or relax constraints."
                                    )

                            if not strategy_options:
                                _tpl = build_strategy_template_meal(
                                    reqs, strategy_label, f"{strategy_label} (auto template)"
                                )
                                if _tpl:
                                    strategy_options = [_tpl]
                            strategy_meal_options[strategy_label] = strategy_options[:max_options_per_strategy]

                            if not strategy_meal_options.get(strategy_label) and strategy_label not in strategy_fail_reasons:
                                strategy_fail_reasons[strategy_label] = (
                                    "No recipes were found that satisfy this strategy's constraints under the current filters."
                                )

                        final_meals: list[dict[str, Any]] = []
                        for strategy in strategy_sets:
                            lbl = str(strategy.get("label", "") or "")
                            for meal in strategy_meal_options.get(lbl, [])[:max_options_per_strategy]:
                                final_meals.append({**meal, "strategy_label": lbl})

                        source_mode = "llm"

                        st.session_state["meal_suggestion_cache"][meal_cache_key] = {
                            "meals": final_meals,
                            "source_mode": source_mode,
                            "strategy_fail_reasons": strategy_fail_reasons,
                        }
                        meal_status.update(label="Meal ideas ready", state="complete")

            cached_meal_pack = st.session_state["meal_suggestion_cache"].get(
                meal_cache_key,
                {"meals": [], "source_mode": "none", "strategy_fail_reasons": {}},
            )
            meals = cached_meal_pack.get("meals", []) or []
            source_mode = str(cached_meal_pack.get("source_mode", "none") or "none")
            strategy_fail_reasons = cached_meal_pack.get("strategy_fail_reasons", {}) or {}

            if meals:
                st.success("Meal ideas sourced from AI generation.")

            strategy_panels = [
                {
                    "strategy": "Selected whole-food meal",
                    "title": "1. Ingredient / whole-food selected optimized",
                    "help_text": "Generates AI meals where your selected whole foods appear with strong concentration, then scales serving size so supplement-equivalent micronutrient targets are matched or exceeded.",
                    "show_macro_controls": False,
                },
                {
                    "strategy": "Price-optimized meal",
                    "title": "2. Price optimized",
                    "help_text": "Chooses the qualifying AI-generated meal path that best satisfies supplement-equivalent micronutrient targets at the lowest estimated recipe cost.",
                    "show_macro_controls": False,
                },
                {
                    "strategy": "Macro-optimized meal",
                    "title": "3. Macronutrient optimized",
                    "help_text": "Adjust the calories and macro split here. This third meal is then selected and scaled to fit that macro target while still matching or exceeding the supplement-equivalent micronutrients.",
                    "show_macro_controls": True,
                },
            ]
            meals_by_strategy: dict[str, list[dict[str, Any]]] = {}
            for meal in meals:
                lbl = str(meal.get("strategy_label", "") or "").strip()
                if not lbl:
                    continue
                meals_by_strategy.setdefault(lbl, []).append(meal)

            for idx, panel in enumerate(strategy_panels, start=1):
                strategy_name = str(panel["strategy"])
                strategy_meals = meals_by_strategy.get(strategy_name, [])
                title = str(panel["title"])
                if strategy_meals:
                    title += f" • {len(strategy_meals)} meal option(s)"

                with st.expander(title, expanded=(idx == 1)):
                    st.caption(str(panel["help_text"]))

                    if bool(panel.get("show_macro_controls", False)):
                        st.caption("These settings affect only this macronutrient-optimized meal.")
                        st.number_input(
                            "Target calories for this meal (kcal)",
                            min_value=100,
                            max_value=3000,
                            step=50,
                            key="macro_target_kcal",
                            help="Total energy the macronutrient-optimized meal should deliver.",
                        )
                        macro_cols = st.columns(3)
                        macro_cols[0].number_input(
                            "Protein %",
                            min_value=0,
                            max_value=100,
                            step=1,
                            key="macro_pct_protein",
                            help="Share of calories from protein (4 kcal/g).",
                        )
                        macro_cols[1].number_input(
                            "Carbs %",
                            min_value=0,
                            max_value=100,
                            step=1,
                            key="macro_pct_carbs",
                            help="Share of calories from carbohydrates (4 kcal/g).",
                        )
                        macro_cols[2].number_input(
                            "Fat %",
                            min_value=0,
                            max_value=100,
                            step=1,
                            key="macro_pct_fat",
                            help="Share of calories from fat (9 kcal/g).",
                        )
                        macro_sum = (
                            int(st.session_state.get("macro_pct_protein", 30) or 30)
                            + int(st.session_state.get("macro_pct_carbs", 50) or 50)
                            + int(st.session_state.get("macro_pct_fat", 20) or 20)
                        )
                        if macro_sum != 100:
                            st.warning(f"Protein + Carbs + Fat must sum to 100 % (currently {macro_sum} %).")
                        else:
                            st.caption(
                                f"Split: {st.session_state.get('macro_pct_protein', 30)}% protein / "
                                f"{st.session_state.get('macro_pct_carbs', 50)}% carbs / "
                                f"{st.session_state.get('macro_pct_fat', 20)}% fat  •  "
                                f"{st.session_state.get('macro_target_kcal', 500)} kcal target"
                            )
                    elif strategy_name == "Price-optimized meal":
                        st.caption("Set a hard max budget for this meal. Recipes above this cap are rejected.")
                        st.number_input(
                            "Max price for this meal",
                            min_value=0.5,
                            max_value=500.0,
                            step=0.5,
                            key="price_optimized_max_meal_cost",
                            help="Price-optimized meal must not exceed this total estimated cost.",
                        )
                        symbol = CURRENCY_SYMBOL.get(selected_currency, selected_currency)
                        st.caption(f"Current cap: {symbol}{format_float(float(st.session_state.get('price_optimized_max_meal_cost', 12.0) or 0.0), 2)}")

                    if not strategy_meals:
                        if strategy_name == "Macro-optimized meal" and not macro_sum_valid:
                            st.info("Meal not generated yet because the macro split must sum to 100%.")
                        elif strategy_name in strategy_fail_reasons:
                            st.warning(str(strategy_fail_reasons.get(strategy_name, "")))
                        else:
                            st.info("Generate meal ideas to populate this strategy.")
                        continue

                    meal_name_options = [str(m.get("name", "Meal idea") or "Meal idea") for m in strategy_meals]
                    if len(strategy_meals) >= 50:
                        st.caption("Showing 50 recipes that satisfy this strategy's constraints.")
                    else:
                        st.caption(
                            f"Found {len(strategy_meals)} recipe(s) that satisfy this strategy's constraints; fewer than 50 are currently available under these restrictions."
                        )
                    selected_meal_name = st.selectbox(
                        "Generated meal",
                        options=meal_name_options,
                        index=0,
                        key=f"meal_select_{idx}_{normalize_lookup_key(strategy_name)}",
                    )
                    meal = next(
                        (m for m in strategy_meals if str(m.get("name", "Meal idea") or "Meal idea") == selected_meal_name),
                        strategy_meals[0],
                    )

                    coverage_ratio = float(meal.get("coverage_ratio", 0.0) or 0.0)
                    if meal.get("full_coverage"):
                        st.caption("Coverage: full target coverage")
                    else:
                        st.caption(f"Coverage: {format_float(coverage_ratio * 100, 0)}%")

                    macro_summary = str(meal.get("macro_summary", "") or "").strip()
                    if macro_summary:
                        st.caption(macro_summary)
                    # Patch: UX macro + additive micronutrient panel (all strategies).
                    try:
                        render_meal_nutrition_panel(meal, meal_component_candidates)
                    except Exception as _np_exc:
                        st.caption(f"Nutrition panel unavailable:  _np_exc ")
                    est_cost = meal.get("estimated_recipe_cost")
                    if est_cost is not None:
                        try:
                            currency = str(meal.get("price_currency", selected_currency) or selected_currency)
                            symbol = CURRENCY_SYMBOL.get(currency, currency)
                            cap = float(meal.get("price_cap", 0.0) or 0.0)
                            cost_txt = f"Estimated meal cost: {symbol}{format_float(float(est_cost), 2)}"
                            if cap > 0:
                                cost_txt += f" (cap: {symbol}{format_float(cap, 2)})"
                            st.caption(cost_txt)
                        except Exception:
                            pass

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

                    covered = meal.get("covered_components", []) or []
                    partial = meal.get("partial_components", []) or []
                    uncovered = meal.get("uncovered_components", []) or []
                    if covered:
                        st.caption("Covered components: " + ", ".join([str(x) for x in covered]))
                    if partial:
                        st.caption("Partially covered components: " + ", ".join([str(x) for x in partial]))
                    if uncovered:
                        st.caption("Not fully covered in this meal: " + ", ".join([str(x) for x in uncovered]))


def build_mobile_ui() -> None:
    import streamlit as st
    import streamlit.components.v1 as components
    global LAST_VISION_PROVIDER
    global LAST_TEXT_PROVIDER
    global LAST_URL_PARSE_REASON

    st.set_page_config(page_title="SuppSwap", page_icon="🥗", layout="centered")

    # -- File uploader button CSS override --------------------------------
    st.markdown(
        """<style>
        /* Hide drag-and-drop instruction text above the button */
        [data-testid='stFileUploaderDropzoneInstructions'] {
            display: none !important;
        }
        /* Hide original Browse files span text */
        [data-testid='stFileUploaderDropzone'] button span {
            display: none !important;
        }
        /* Inject custom label using actual UTF-8 emoji */
        [data-testid='stFileUploaderDropzone'] button::before {
            content: '📷📤🗂 Capture Supplement Info';
            font-size: 0.95rem;
            font-weight: 500;
        }
        </style>""",
        unsafe_allow_html=True,
    )
    # -----------------------------------------------------------------------

    st.markdown(
        """
<style>
.mfitness-watermark {
    position: fixed;
    right: 14px;
    bottom: 74px;
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

div[data-testid="stTabs"] button[role="tab"] p {
    margin: 0;
    white-space: normal;
    overflow: visible;
    text-overflow: clip;
    line-height: 1.08;
    text-align: center;
    word-break: break-word;
}

div[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
    background: #0f766e;
    color: #ffffff;
    border-color: #0f766e;
    box-shadow: 0 4px 12px rgba(15, 118, 110, 0.22);
}

.linked-value-chip {
    display: inline-block;
    padding: 0.12rem 0.42rem;
    border-radius: 999px;
    background: #e7f5f2;
    border: 1px solid #b7e2d8;
    color: #0f4d46;
    font-weight: 700;
}

/* BaseWeb select fallback: hide raw icon ligature text (e.g., "arrow_down") and draw a chevron. */
div[data-baseweb="select"] [role="button"] > div:last-child {
    position: relative;
    min-width: 1rem;
}

div[data-baseweb="select"] [role="button"] > div:last-child span[class*="material"],
div[data-baseweb="select"] [role="button"] > div:last-child span[class*="icon"] {
    display: inline-block !important;
    width: 1rem !important;
    height: 1rem !important;
    min-width: 1rem !important;
    overflow: hidden !important;
    text-indent: -9999px !important;
    white-space: nowrap !important;
    font-size: 0 !important;
    line-height: 0 !important;
    color: transparent !important;
}

div[data-baseweb="select"] [role="button"] > div:last-child::after {
    content: "";
    position: absolute;
    left: 50%;
    top: 48%;
    width: 0.42rem;
    height: 0.42rem;
    border-right: 2px solid rgba(55, 65, 81, 0.9);
    border-bottom: 2px solid rgba(55, 65, 81, 0.9);
    transform: translate(-50%, -50%) rotate(45deg);
    pointer-events: none;
}

/* Bottom tab bar navigation on all screen sizes */
div[data-testid="stTabs"] div[role="tablist"] {
    position: fixed;
    left: 0;
    right: 0;
    bottom: 0;
    z-index: 10000;
    margin: 0;
    padding: 0.42rem 0.56rem calc(0.42rem + env(safe-area-inset-bottom));
    background: rgba(255, 255, 255, 0.96);
    border-top: 1px solid rgba(0, 0, 0, 0.08);
    backdrop-filter: blur(6px);
    gap: 0.28rem;
    display: grid;
    grid-template-columns: repeat(7, minmax(0, 1fr));
    width: 100%;
}

div[data-testid="stTabs"] button[role="tab"] {
    width: 100%;
    min-height: 2.7rem;
    font-size: clamp(0.6rem, 1.2vw, 0.76rem);
    font-weight: 600;
    line-height: 1.08;
    padding: 0.32rem 0.18rem;
    border-radius: 10px;
    border: 1px solid rgba(0, 0, 0, 0.08);
    background: rgba(250, 250, 250, 0.95);
}

.block-container {
    padding-bottom: 6.4rem;
}

/* Ensure page content stays scrollable with fixed bottom chrome */
html,
body,
[data-testid="stAppViewContainer"],
[data-testid="stMain"] {
    overflow: auto !important;
}

@media (max-width: 768px) {
    .block-container {
        padding-top: 0.8rem;
        padding-left: 0.75rem;
        padding-right: 0.75rem;
        padding-bottom: 6.4rem;
    }

    div[data-testid="stTabs"] div[role="tablist"] {
        gap: 0.2rem;
    }

    div[data-testid="stTabs"] button[role="tab"] {
        min-height: 2.85rem;
        font-size: clamp(0.56rem, 2.35vw, 0.68rem);
        padding: 0.28rem 0.16rem;
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

    st.title("🥗 SuppSwap 🟢")
    # Patch: rebrand uploader button to "Capture" + camera icon.
    st.markdown(
        """
<style>
/* Replace the "Browse files" button text with a camera + Capture label */
section[data-testid="stFileUploaderDropzone"] button {
    font-size: 0 !important;
    display: inline-flex !important;
    align-items: center !important;
    gap: 0.4rem !important;
}

section[data-testid="stFileUploaderDropzone"] button::after {
    content: "\01F4F7 \01F4C1 \02B06  Capture Supplement Info";
    font-size: 0.9rem !important;
    font-weight: 700 !important;
}

/* Show a camera symbol alongside the existing upload symbol */
[data-testid="stFileUploaderDropzoneInstructions"]::before {
    content: "\01F4F7 / ";
    font-size: 1.3rem;
    margin-right: 0.35rem;
    vertical-align: middle;
}

</style>
""",
        unsafe_allow_html=True,
    )

    # -- Session state defaults ------------------------------------------
    _SESSION_DEFAULTS: dict = {
        "analysis_ready": False,
        "analysis_components": [],
        "analysis_combined_text": "",
        "analysis_structured_debug": {},
        "analysis_food_match_cache_key": "",
        "analysis_food_match_summary": [],
        "analysis_food_match_details": [],
        "analysis_food_match_status": "",
        "price_cache": {},
        "meal_component_candidates": [],
        "meal_suggestion_cache": {},
        "macro_target_kcal": 500,
        "macro_pct_protein": 30,
        "macro_pct_carbs": 50,
        "macro_pct_fat": 20,
        "price_optimized_max_meal_cost": 12.0,
        "target_tab": "",
        "barcode_parse_cache": {},
        "url_parse_cache": {},
        "summary_review_signature": "",
        "summary_review_confirmed": False,
        "global_diet_profile": "",
        "results_show_prices": False,
        "dietary_use_llm_adjudication": False,
        "show_inline_results_fallback": False,
    }
    for _k, _v in _SESSION_DEFAULTS.items():
        st.session_state.setdefault(_k, _v)
    # -- Mobile expander dropdown scroll fix ----------------------------
    st.markdown(
        """<style>
        /* Force BaseWeb portal/popover above everything including tab bar */
        [data-baseweb='popover'],
        [data-baseweb='tooltip'],
        div[role='listbox'],
        ul[role='listbox'] {
            z-index: 999999 !important;
            position: fixed !important;
            overflow-y: auto !important;
            -webkit-overflow-scrolling: touch !important;
            overscroll-behavior: contain !important;
            max-height: 45vh !important;
        }
        /* Prevent the expander itself from clipping the dropdown */
        details, details > summary ~ div {
            overflow: visible !important;
        }
        /* Each option row — large enough touch target */
        [role='option'] {
            min-height: 44px !important;
            padding: 10px 16px !important;
            display: flex !important;
            align-items: center !important;
            touch-action: pan-y !important;
        }
        /* The select input trigger */
        [data-baseweb='select'] > div:first-child {
            min-height: 44px !important;
            touch-action: manipulation !important;
        }
        /* Scrollable list container */
        [data-baseweb='menu'] {
            overflow-y: auto !important;
            -webkit-overflow-scrolling: touch !important;
            overscroll-behavior: contain !important;
            max-height: 45vh !important;
        }
        /* Stop parent containers stealing touch events */
        .stExpander {
            touch-action: pan-y !important;
            overflow: visible !important;
        }
        section[data-testid='stSidebar'],
        .main .block-container {
            overflow-x: hidden !important;
        }
        </style>""",
        unsafe_allow_html=True,
    )
    # -----------------------------------------------------------------------
    # Patch: make selectbox/multiselect dropdown menus scrollable and above the tab bar.  
    st.markdown(  
        """  
<style>  
div[data-baseweb="popover"]    
    z-index: 10050 !important;  
    overflow: visible !important;  
   
div[data-baseweb="popover"] ul[role="listbox"],  
div[data-baseweb="popover"] ul[data-baseweb="menu"],  
ul[data-baseweb="menu"]    
    max-height: 45vh !important;  
    overflow-y: auto !important;  
    -webkit-overflow-scrolling: touch !important;  
    overscroll-behavior: contain !important;  
   
</style>  
""",  
        unsafe_allow_html=True,  
    )  

    # ---------------------------------------------------------------------

    tab_analyze, tab_results, tab_meals, tab_research, tab_reference, tab_about = st.tabs(
        ["🔎 Analyze", "📊 Results", "🍽 Meals", "📚 Research", "📘 Nutrient Guide", "ℹ️ About & Feedback"]
    )
    # Patch: Welcome content now lives at the end inside the About & Feedback tab.
    tab_welcome = tab_about
    tab_feedback = tab_about

    components.html(
        """
<script>
const parentDoc = window.parent.document;
const parentWin = window.parent;

function suppswapScrollToTop() {
    parentWin.scrollTo({ top: 0, left: 0, behavior: 'auto' });
    const main = parentDoc.querySelector('[data-testid="stMain"]');
    if (main) {
        main.scrollTo({ top: 0, left: 0, behavior: 'auto' });
    }
}

if (!parentWin.__suppswapTabScrollHookInstalled) {
    parentWin.__suppswapTabScrollHookInstalled = true;
    parentDoc.addEventListener('click', (event) => {
        const tabButton = event.target && event.target.closest
            ? event.target.closest('button[role="tab"]')
            : null;
        if (!tabButton) {
            return;
        }
        setTimeout(suppswapScrollToTop, 0);
    }, true);
}
</script>
""",
        height=0,
    )

    def _render_tab_activation_script(target_tab_name: str) -> None:
        safe_target = json.dumps(str(target_tab_name or "").strip())
        components.html(
            rf"""
<script>
const target = {safe_target};
const parentDoc = window.parent.document;
const parentWin = window.parent;

const canonicalize = (value) => {{
    const raw = String(value || '').trim().toLowerCase();
    if (!raw) return '';
    return raw
        .replace(/^\s*[0-9]+\s*[\)\].:\-]*\s*/, '')
        .replace(/^[^a-z0-9]+/, '')
        .replace(/\s+/g, ' ')
        .trim();
}};

const targetCanonical = canonicalize(target);

const scrollToTop = () => {{
    parentWin.scrollTo({{ top: 0, left: 0, behavior: 'auto' }});
    const main = parentDoc.querySelector('[data-testid="stMain"]');
    if (main) {{
        main.scrollTo({{ top: 0, left: 0, behavior: 'auto' }});
    }}
}};

const matchesTarget = (label) => {{
    const plain = String(label || '').trim().toLowerCase();
    const canon = canonicalize(label);
    if (!canon) return false;
    if (plain === String(target || '').trim().toLowerCase()) return true;
    if (canon === targetCanonical) return true;
    if (canon.startsWith(targetCanonical)) return true;
    if (canon.includes(targetCanonical)) return true;
    return false;
}};

const tryActivateTab = (attempt = 0) => {{
    const tabButtons = parentDoc.querySelectorAll('button[role="tab"]');
    let clicked = false;

    for (const btn of tabButtons) {{
        const label = (btn.innerText || '').trim();
        const matches = matchesTarget(label);
        if (!matches) {{
            continue;
        }}
        btn.click();
        clicked = true;
        setTimeout(scrollToTop, 0);
        break;
    }}

    if (!clicked && attempt < 80) {{
        setTimeout(() => tryActivateTab(attempt + 1), 100);
    }}
}};

setTimeout(() => tryActivateTab(0), 60);
</script>
""",
            height=0,
        )

    if st.session_state.get("target_tab"):
        target_tab = str(st.session_state.get("target_tab") or "").strip()
        _render_tab_activation_script(target_tab)
        st.session_state["target_tab"] = ""

    with tab_welcome:
        st.subheader("Purpose")
        st.markdown(
            """
SuppSwap helps you make practical nutrition decisions so you do not overpay for supplements or miss better whole-food options.

It is designed for both:
- beginners who are still learning nutrition basics, and
- advanced users who want faster evidence-oriented decisions.

Instead of manually searching micronutrient tables for each food, you can scan a supplement label and get food-equivalent comparisons, costs, and meal ideas in one flow.

The goal is simple: compare supplement doses against real foods, costs, and meal plans using transparent calculations.
"""
        )

        st.subheader("Why Whole Foods Usually Win")
        st.markdown(
            """
- Whole foods deliver micronutrients together with fiber, water, protein, fats, and food matrix effects that can change absorption and satiety.
- Whole foods contain many bioactive compounds (polyphenols, carotenoids, peptides, phytochemicals) that are not fully captured by standard supplement labels.
- Chemical synthesis can target known measurable compounds, but nutrition science still cannot fully quantify all interacting compounds present in real foods.
- Food patterns are associated with better long-term health outcomes than isolated-pill strategies in many populations.
- Whole foods improve diet quality and meal structure, which supports adherence better than stack-heavy supplement routines.
- Supplements can still be useful in specific deficiencies or clinical contexts, but they are usually a targeted tool, not a full replacement for food quality.
"""
        )

        st.subheader("How To Use This App")
        st.markdown(
            """
1. `Analyze`: Enter your supplement details by camera, upload, URL, or text.
2. `Results`: Review mapped whole-food alternatives, concentration per 100 g, cost comparison, and practical portion estimates.
3. `Meals`: Generate meal ideas based on the selected alternatives and your dietary profile.
4. `Research (RAG)`: Ask evidence-focused questions; answers are retrieved from your local fitness reference library.

RAG source context:
The local RAG library is built from curated expert nutrition notes and evidence summaries, with heavy emphasis on meta-study style synthesis and sources aligned with evidence-tracking approaches (including material in your Examine-style reference stack).
"""
        )

        st.caption("Educational tool only. It is not a diagnosis or medical treatment service.")

        if st.button("Analyze my supplement", type="primary", use_container_width=True, key="welcome_go_analyze"):
            st.session_state["target_tab"] = "Analyze"
            st.rerun()

    _render_analyze_tab(tab_analyze)
    _render_results_tab(tab_results)
    _render_meals_tab(tab_meals)

    with tab_research:
        _render_research_tab()

    with tab_reference:
        _render_reference_tab()

    with tab_feedback:
        st.subheader("Report incorrect or unsafe output")
        st.caption("This feedback helps improve mapping quality and prevent repeated recommendation mistakes.")

        feedback_type = st.selectbox(
            "Feedback type",
            [
                "Wrong nutrient mapping",
                "Wrong dose match",
                "Unsafe suggestion",
                "Cost estimate issue",
                "Meal suggestion issue",
                "Other",
            ],
            key="feedback_type",
        )
        expected_output = st.text_area(
            "What did you expect instead?",
            placeholder="Example: For vitamin D, suggest fatty fish and explain when supplementation may still be needed.",
            height=110,
            key="feedback_expected_output",
        )
        observed_issue = st.text_area(
            "What was wrong in the app output?",
            placeholder="Describe the incorrect recommendation, mismatch, or safety concern.",
            height=130,
            key="feedback_observed_issue",
        )
        include_context = st.checkbox(
            "Attach latest analyzed input and parsed components",
            value=True,
            key="feedback_include_context",
        )

        submit_feedback = st.button("Submit feedback", type="primary", use_container_width=True, key="submit_feedback_btn")
        if submit_feedback:
            if not observed_issue.strip() and not expected_output.strip():
                st.warning("Please describe what was wrong or what you expected.")
            else:
                report_payload: dict[str, Any] = {
                    "feedback_type": feedback_type,
                    "observed_issue": observed_issue.strip(),
                    "expected_output": expected_output.strip(),
                }
                if include_context:
                    report_payload["raw_input_excerpt"] = str(st.session_state.get("analysis_combined_text", ""))[:3000]
                    report_payload["parsed_components"] = st.session_state.get("analysis_components", [])
                    report_payload["food_match_status"] = st.session_state.get("analysis_food_match_status", "")

                if save_feedback_report(report_payload):
                    st.success("Feedback submitted. Thank you - this will directly support content quality improvements.")
                else:
                    st.error("Could not save feedback locally. Please try again.")


def is_streamlit_runtime() -> bool:
    return "streamlit" in sys.modules


if __name__ == "__main__":
    if is_streamlit_runtime():
        build_mobile_ui()
    else:
        script_path = os.path.abspath(__file__)
        # Use a dedicated local port by default to avoid collisions with other
        # Streamlit apps (for example the swipe app) that often run on 8501.
        streamlit_port = str(os.getenv("BLOCKBRAIN_STREAMLIT_PORT", "8511") or "8511").strip()
        cmd = [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            script_path,
            "--server.port",
            streamlit_port,
            "--browser.gatherUsageStats",
            "false",
        ]
        print(f"Launching Blockbrain Streamlit app from: {script_path}")
        print(f"Local URL (expected): http://localhost:{streamlit_port}")
        try:
            subprocess.run(cmd, check=False)
        except KeyboardInterrupt:
            print("Streamlit launcher interrupted by user.")
        except Exception as exc:
            print(f"Failed to launch Streamlit automatically: {exc}")
            print(f"Please run: python -m streamlit run {script_path} --server.port {streamlit_port}")
