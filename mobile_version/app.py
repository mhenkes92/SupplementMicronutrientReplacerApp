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
from pathlib import Path
from typing import Any

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
RAG_TOP_K = 5
RAG_INDEX_PATH = APP_DIR / "data" / "fitness_rag_chunks.jsonl"
RAG_INDEX_META_PATH = APP_DIR / "data" / "fitness_rag_meta.json"

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


def _lookup_nutrient_row(conn: sqlite3.Connection, nutrient_name: str) -> dict[str, Any] | None:
    target_raw = (nutrient_name or "").strip().lower()
    target_norm = normalize_lookup_key(nutrient_name)
    if not target_raw:
        return None

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
) -> dict[str, Any]:
    normalized_component = normalize_lookup_key(component)
    if not normalized_component:
        return {}

    alias_hit = aliases.get(normalized_component)
    if alias_hit:
        nutrient = _lookup_nutrient_row(conn, alias_hit)
        if nutrient:
            nutrient.update({"confidence": "high", "match_method": "alias"})
            return nutrient

    direct = _lookup_nutrient_row(conn, normalized_component)
    if direct:
        direct.update({"confidence": "high", "match_method": "direct"})
        return direct

    for rule in proxy_rules:
        target = rule.get("component", "")
        if not target:
            continue
        if normalized_component == target or target in normalized_component:
            nutrient = _lookup_nutrient_row(conn, str(rule.get("proxy_nutrient", "")))
            if nutrient:
                nutrient.update(
                    {
                        "confidence": str(rule.get("confidence", "medium") or "medium"),
                        "match_method": "curated_proxy",
                        "proxy_rationale": str(rule.get("rationale", "")),
                    }
                )
                return nutrient

    names = conn.execute("SELECT nutrient_name FROM nutrients").fetchall()
    nutrient_names = [str(row[0]) for row in names]
    close = difflib.get_close_matches(component, nutrient_names, n=1, cutoff=0.80)
    if close:
        nutrient = _lookup_nutrient_row(conn, close[0])
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
            nutrient = _lookup_nutrient_row(conn, llm_candidate)
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
        ORDER BY rank_desc ASC
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
    if u in {"mcg", "ug", "μg", "microgram", "micrograms"}:
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


def retrieve_rag_chunks(query: str, chunks: list[dict[str, str]], top_k: int = RAG_TOP_K) -> list[dict[str, str]]:
    query_terms = set(tokenize_for_rag(query))
    if not query_terms:
        return []

    scored: list[tuple[float, dict[str, str]]] = []
    for chunk in chunks:
        score = score_chunk(query_terms, chunk.get("text", ""))
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
        "If the context is insufficient, say so clearly."
    )
    user_prompt = (
        f"Question:\n{query}\n\n"
        f"Reference excerpts:\n{context}\n\n"
        "Return a concise answer followed by a short 'Sources:' line listing filenames used."
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
    summaries: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    seen: set[str] = set()

    try:
        for item in components:
            component = normalize_lookup_key(str(item.get("component", "")))
            if not component or component in seen:
                continue
            seen.add(component)

            nutrient = resolve_component_to_nutrient(conn, component, aliases, proxy_rules)
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

            top_foods = get_top_ranked_foods(conn, int(nutrient["nutrient_id"]), TOP_FOODS_PER_COMPONENT)
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
                    "foods": get_top_ranked_foods(conn, int(nutrient["nutrient_id"]), OVERVIEW_ALT_LIMIT),
                }
            )
    finally:
        conn.close()

    return summaries, details, "ok"


def normalize_component_name(raw_name: str) -> str:
    text = (raw_name or "").strip()
    if not text:
        return ""

    text = re.sub(r"\([^)]*\)", "", text)
    text = text.split("/")[0].strip()
    text = re.sub(r"^[\-•:;,.\s]+", "", text)
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
        r"(?P<name>.+?)\s+(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>mg|mcg|iu|g|kcal)\b",
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
            dose_value = float(match.group("val"))
        except Exception:
            continue

        dose_unit = match.group("unit").lower()
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
        "X-Title": "SuppSwap Mobile MVP",
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

    st.set_page_config(page_title="SuppSwap Mobile MVP", page_icon="🥗", layout="centered")

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
</style>
<div class="mfitness-watermark">© mfitness92</div>
""",
        unsafe_allow_html=True,
    )

    st.title("🥗 SuppSwap Mobile MVP")

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

    components = st.session_state.get("analysis_components", [])
    combined = st.session_state.get("analysis_combined_text", "")

    if not components:
        st.info("No structured components found yet. You can still proceed in the next build step.")
        st.text_area("Raw extracted text", combined[:6000], height=220)
        return

    st.subheader("Your Supplement vs Whole Food Alternative")
    usda_summary, usda_details, usda_status = build_usda_matches(components)
    detail_by_component = {normalize_lookup_key(str(d.get("component", ""))): d for d in usda_details}

    if usda_status != "ok":
        st.info(
            "Precomputed USDA DB not found yet. Build once with: "
            "`python build_usda_rankings_db.py`"
        )

    header_cols = st.columns([2.4, 1.4, 3.3, 2.9])
    header_cols[0].markdown("**Your supplement components**")
    header_cols[1].markdown("**Dose + unit**")
    header_cols[2].markdown("**Whole Food Alternative**")
    header_cols[3].markdown("**How much to eat of the selected whole food to match supplement dose**")

    for index, item in enumerate(components):
        row_cols = st.columns([2.4, 1.4, 3.3, 2.9])
        component_raw = str(item.get("component", "")).strip()
        component_key = normalize_lookup_key(component_raw)
        dose_value = item.get("dose_value")
        dose_unit = str(item.get("dose_unit") or "")

        row_cols[0].markdown(component_raw.title() if component_raw else "Not available")
        if dose_value is not None:
            row_cols[1].markdown(f"{format_float(float(dose_value))} {dose_unit}")
        else:
            row_cols[1].markdown("Not available")

        detail = detail_by_component.get(component_key)
        foods = detail.get("foods", []) if detail else []

        if usda_status != "ok":
            row_cols[2].markdown("USDA DB unavailable")
            row_cols[3].markdown("Not available")
            st.divider()
            continue

        if not detail:
            row_cols[2].markdown("No mapped USDA alternative")
            row_cols[3].markdown("Not available")
            st.divider()
            continue

        if not foods:
            row_cols[2].markdown("No ranked alternatives found")
            row_cols[3].markdown("Not available")
            st.divider()
            continue

        option_labels: list[str] = []
        for food in foods:
            amt = format_float(float(food.get("amount_per_100g", 0.0)))
            unit = str(food.get("unit", "")).upper()
            option_labels.append(
                f"#{food.get('rank', '')} {food.get('food_description', '')} ({amt} {unit}/100g)"
            )

        selected_label = row_cols[2].selectbox(
            "Whole Food Alternative",
            options=option_labels,
            index=0,
            key=f"alt_select_cell_{index}_{component_key}",
            label_visibility="collapsed",
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
        row_cols[3].markdown(match_label)
        st.divider()

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
