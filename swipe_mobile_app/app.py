from __future__ import annotations

import datetime
import hmac
import html
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - older runtimes
    tomllib = None  # type: ignore

import streamlit as st
import streamlit.components.v1 as components
from PIL import Image, ImageOps

# Make sibling package imports work when running this app directly.
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def _bootstrap_blockbrain_env_from_secrets() -> None:
    if tomllib is None:
        return
    secrets_path = ROOT_DIR / "blockbrain" / ".streamlit" / "secrets.toml"
    if not secrets_path.exists():
        return
    try:
        raw = tomllib.loads(secrets_path.read_text(encoding="utf-8"))
    except Exception:
        return

    # What blockbrain_llm_client.py reads (see blockbrain/app.py): never a value in code, only these names.
    key_map = {
        "BLOCKBRAIN_API_KEY": "BLOCKBRAIN_API_KEY",
        "BLOCKBRAIN_ORG_ID": "BLOCKBRAIN_ORG_ID",
        "BLOCKBRAIN_MODEL": "BLOCKBRAIN_MODEL",
        "BLOCKBRAIN_BOT_ID": "BLOCKBRAIN_BOT_ID",
        "BLOCKBRAIN_OCR_ROUTE": "BLOCKBRAIN_OCR_ROUTE",
    }
    for secret_key, env_key in key_map.items():
        if os.getenv(env_key, "").strip():
            continue
        value = str(raw.get(secret_key, "") or "").strip()
        if value:
            os.environ[env_key] = value


_bootstrap_blockbrain_env_from_secrets()

def _load_current(name: str, watch: str | os.PathLike[str] | None = None):
    """Import our own module `name`, reloading it when this process holds an outdated copy.

    A running Streamlit process keeps every module it imported. When a redeploy
    replaces the files on disk, this entry script is read again (new code) but
    `blockbrain.app` and `llm_cache` stay the OLD objects, so the new script calls
    something the old module doesn't have and the app dies on open until someone
    reboots it. A module is outdated when
      * the file changed since this guard last loaded or checked it (the file's
        mtime, ctime and size are compared: tar / `cp -p` / rsync restore the mtime
        but never the ctime), or a data file in `watch` did (the module's cached
        loaders read those: the weekly USDA refresh changes data only), or
      * this guard has never seen it but it was already imported: an earlier entry
        script (one without this guard) loaded it, so its version is unknown.
    The file is stat-ed BEFORE the source is read, so a deploy that lands while the
    module body runs shows up as a change on the next run instead of being
    recorded as current. A reload that raises forgets the module (it is half new),
    so every following run retries it."""
    import importlib
    import importlib.util
    import types

    registry = sys.modules.get("_suppswipe_imports")
    if registry is None:
        registry = sys.modules.setdefault("_suppswipe_imports", types.ModuleType("_suppswipe_imports"))
    with registry.__dict__.setdefault("lock", threading.RLock()):
        seen: dict[str, tuple[Any, Any]] = registry.__dict__.setdefault("seen", {})
        preloaded = name in sys.modules
        try:
            spec = getattr(sys.modules.get(name), "__spec__", None) or importlib.util.find_spec(name)
        except (ImportError, ValueError):
            spec = None
        path = getattr(spec, "origin", None)

        def data_fingerprint() -> tuple[Any, ...]:
            if watch is None:
                return ()
            try:
                rows = []
                for entry in os.scandir(watch):
                    # Data the app itself rewrites at run time (logs, feedback reports) must not count as a deploy.
                    if (
                        entry.is_file()
                        and entry.name.rsplit(".", 1)[-1] in ("db", "csv", "json", "jsonl")
                        and not entry.name.endswith("_log.csv")
                        and not entry.name.startswith("feedback")
                    ):
                        info = entry.stat()
                        rows.append((entry.name, info.st_mtime_ns, info.st_ctime_ns, info.st_size))
                return tuple(sorted(rows))
            except OSError:
                return ()

        def fingerprint() -> tuple[Any, ...] | None:
            try:
                st = os.stat(path)  # type: ignore[arg-type]
            except (OSError, TypeError, ValueError):
                return None
            return (st.st_mtime_ns, st.st_ctime_ns, st.st_size, data_fingerprint())

        before = fingerprint()
        module = importlib.import_module(name)
        last = seen.get(name)
        if last is None:
            stale = preloaded  # imported by something else earlier: which version is unknown
        elif last[0] is not module:
            stale = False  # replaced behind our back (Streamlit evicts changed modules): fresh from disk
        else:
            stale = before is not None and last[1] != before
        if stale:
            try:
                importlib.invalidate_caches()
                try:  # same mtime second + same size would let the import system reuse the old bytecode
                    os.unlink(importlib.util.cache_from_source(path))  # type: ignore[arg-type]
                except (OSError, TypeError, ValueError, NotImplementedError):
                    pass
                module = importlib.reload(module)
            except BaseException:
                seen.pop(name, None)  # half reloaded: no record, so the next run reloads again
                raise
            registry.__dict__["generation"] = registry.__dict__.get("generation", 0) + 1
        seen[name] = (module, before)
        return module


_load_current("blockbrain_llm_client")  # before blockbrain.app, which imports it
bb = _load_current("blockbrain.app", watch=ROOT_DIR / "blockbrain" / "data")
llm_cache = _load_current("llm_cache")

# st.cache_data / st.cache_resource key on the cached function's own source, not on the
# modules it calls: their results were produced by the code a reload just replaced
# (an OCR text or page text an older build accepted would be served for hours). Start
# them over once per reload.
_registry_state = sys.modules["_suppswipe_imports"].__dict__
_CODE_RELOADED = _registry_state.get("cleared_generation", 0) != _registry_state.get("generation", 0)
if _CODE_RELOADED:
    _registry_state["cleared_generation"] = _registry_state.get("generation", 0)
    st.cache_data.clear()

# A Blockbrain error is never served or stored as an answer (also catches
# entries an older build cached before this check existed).
llm_cache.set_reject(bb.looks_like_agent_error)


st.set_page_config(page_title="SuppSwipe", page_icon="🥗", layout="centered")

# Real Tinder-style swipe card: a bidirectional custom component served from a
# static HTML file (no npm/build step needed, works on Streamlit Cloud).
_SWIPE_COMPONENT_DIR = Path(__file__).resolve().parent / "swipe_component"
try:
    _tinder_swipe = components.declare_component("tinder_swipe", path=str(_SWIPE_COMPONENT_DIR))
except Exception:
    _tinder_swipe = None


def tinder_swipe(**kwargs: Any):
    """Render the draggable swipe card; returns {'dir': 'left'|'right', ...} on swipe."""
    if _tinder_swipe is None:
        return None
    try:
        return _tinder_swipe(**kwargs)
    except Exception:
        return None


# Back-camera capture component (getUserMedia facingMode 'environment').
_CAMERA_COMPONENT_DIR = Path(__file__).resolve().parent / "camera_component"
try:
    _back_camera = components.declare_component("back_camera", path=str(_CAMERA_COMPONENT_DIR))
except Exception:
    _back_camera = None


# The upload limit (server.maxUploadSize, 10 MB) does not apply to a component
# value, so the camera's data URL gets the same cap (10 MB of base64-decoded image).
_CAMERA_MAX_DATA_URL_CHARS = 14_000_000


def _decode_camera_image(value: Any) -> bytes:
    """Decode the {'image': dataURL} value from the camera component into JPEG bytes."""
    if not isinstance(value, dict):
        return b""
    data_url = str(value.get("image", "") or "")
    if "," not in data_url or len(data_url) > _CAMERA_MAX_DATA_URL_CHARS:
        return b""
    try:
        import base64 as _b64
        return _b64.b64decode(data_url.split(",", 1)[1])
    except Exception:
        return b""


LEFT_SWIPE_ICON = "💊"
TITLE_WHOLE_FOOD_ICON = "🥗"
WHOLE_FOOD_ICONS = ["🥦", "🥕", "🥚", "🍓", "🐟", "🥜", "🍠", "🥬"]

# A deep candidate pool is fetched per nutrient so dietary filtering still leaves
# real options for restrictive diets (e.g. vegan Vitamin B1, where the top foods
# by concentration are all animal). The visible dropdown is then capped so the
# list stays manageable for unrestricted users.
SWIPE_CARD_FOOD_POOL = 250
SWIPE_CARD_DROPDOWN_MAX = 40

# Bumped on notable releases so we can confirm which build is actually live on
# Streamlit Cloud (shown as a tiny stamp under the title).
def _build_commit() -> str:
    """Short commit of this checkout (Streamlit Cloud runs from a git clone), or "". Plain file reads, no git."""
    try:
        git_dir = ROOT_DIR / ".git"
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            ref = head[5:]
            ref_file = git_dir / ref
            if ref_file.exists():
                head = ref_file.read_text(encoding="utf-8").strip()
            else:
                head = next(
                    (line.split(" ")[0] for line in (git_dir / "packed-refs").read_text(encoding="utf-8").splitlines() if line.endswith(" " + ref)),
                    "",
                )
        return head[:7] if re.fullmatch(r"[0-9a-f]{40}", head) else ""
    except Exception:
        return ""


BUILD_TAG = "2026-10-02 · deploy-safe" + (f" · {_commit}" if (_commit := _build_commit()) else "")

# Shared formatting contract appended to LLM prompts whose reply is rendered with
# st.markdown / st.write on a narrow mobile screen, so answers come back as clean,
# consistent Markdown instead of run-on text or code blocks.
_MARKDOWN_STYLE = (
    " Format the reply as clean GitHub-flavored Markdown for a narrow mobile screen: "
    "use short '- ' bullet points (each on its own line), '**bold**' for headings and labels "
    "(never ALL-CAPS headings or a plain 'Meal 1:' prefix), and a blank line between sections. "
    "Do not wrap the answer in a code block, keep it concise, and add no intro, preamble or "
    "sign-off — reply with the content only."
)


def _whole_food_icon(component_key: str) -> str:
    _ = component_key
    return TITLE_WHOLE_FOOD_ICON


# Food-type icons, most specific first. Whole words only, so "eggplant" is not
# an egg, "nutmeg" not a nut and a legume's "mature seeds" not a seed.
_FOOD_ICON_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:soy ?milk|soymilk|almond milk|oat milk|rice milk|plant[- ]based (?:milk|beverage))\b"), "🥛"),
    (re.compile(r"\b(?:seaweed|kelp|nori|wakame|spirulina|agar|laver|irishmoss)\b"), "🌿"),
    (re.compile(r"\bmushrooms?\b|\b(?:shiitake|maitake|chanterelle|morel|portabella|portobello)\b"), "🍄"),
    (re.compile(
        r"\b(?:beans?|lentils?|chickpeas?|garbanzo|cowpeas?|black-?eyed|blackeyes|peas|split peas|soybeans?|"
        r"tofu|tempeh|edamame|natto|miso|legumes?|hummus|pulses|lupins?)\b"
    ), "🫘"),
    # Before the seed, grain, root and meat rules: "turnip greens", "amaranth
    # leaves" and "coconut meat" are none of those.
    (re.compile(r"\b(?:greens|leaves)\b"), "🥬"),
    (re.compile(r"\bcoconut\b"), "🥥"),
    (re.compile(r"\b(?:oysters?|clams?|mussels?|scallops?|mollusks?|whelk|abalone|octopus|squid)\b"), "🦪"),
    (re.compile(r"\b(?:shrimp|prawns?|crab|lobster|crayfish|crustaceans?|krill)\b"), "🦐"),
    (re.compile(
        r"\b(?:fish|salmon|sardines?|tuna|mackerel|anchov(?:y|ies)|herring|trout|cod|halibut|pollock|"
        r"tilapia|caviar|roe|eel|carp|catfish|haddock|sprat|pike|perch|swordfish|snapper|bass|smelt)\b"
    ), "🐟"),
    (re.compile(r"\beggs?\b|\begg (?:yolk|white)\b"), "🥚"),
    (re.compile(r"\b(?:cheese|cheddar|parmesan|mozzarella|gouda|emmental|ricotta|quark|cottage)\b"), "🧀"),
    (re.compile(r"\b(?:milk|yogh?urt|kefir|buttermilk|whey)\b"), "🥛"),
    (re.compile(r"\b(?:chicken|turkey|duck|goose|poultry)\b"), "🍗"),
    (re.compile(
        r"\b(?:beef|pork|lamb|veal|venison|mutton|goat|bison|liver|kidneys?|heart|ham|bacon|sausage|"
        r"meat|game|rabbit|elk|moose|caribou)\b"
    ), "🥩"),
    (re.compile(r"\b(?:seeds?|sunflower|pumpkin seeds?|chia|flaxseeds?|linseeds?|sesame|hemp|tahini)\b"), "🌻"),
    (re.compile(
        r"\b(?:nuts?|almonds?|cashews?|walnuts?|pistachios?|hazelnuts?|filberts?|pecans?|peanuts?|"
        r"brazilnuts?|brazil nuts?|macadamias?|pine nuts?|peanut butter)\b"
    ), "🥜"),
    (re.compile(
        r"\b(?:oats?|oatmeal|wheat|bran|germ|rice|quinoa|amaranth|millet|buckwheat|barley|rye|spelt|"
        r"teff|sorghum|cereals?|bread|pasta|flour|muesli|granola)\b"
    ), "🌾"),
    (re.compile(r"\b(?:avocados?)\b"), "🥑"),
    (re.compile(r"\b(?:sweet potato(?:es)?|yams?)\b"), "🍠"),
    (re.compile(r"\b(?:potato(?:es)?)\b"), "🥔"),
    (re.compile(r"\b(?:carrots?|beets?|beetroot|turnips?|radish(?:es)?|parsnips?|celeriac)\b"), "🥕"),
    (re.compile(r"\b(?:broccoli|cauliflower|brussels|cabbage|kohlrabi)\b"), "🥦"),
    (re.compile(
        r"\b(?:spinach|kale|chard|lettuce|collards?|leafy|greens|arugula|rocket|cress|watercress|"
        r"parsley|dandelion|amaranth leaves|purslane|turnip greens|mustard greens)\b"
    ), "🥬"),
    (re.compile(r"\b(?:peppers?|paprika)\b"), "🫑"),
    (re.compile(r"\b(?:eggplants?|aubergines?)\b"), "🍆"),
    (re.compile(r"\btomato(?:es)?\b"), "🍅"),
    (re.compile(r"\b(?:kiwi(?:fruit)?s?)\b"), "🥝"),
    (re.compile(r"\b(?:oranges?|lemons?|limes?|grapefruit|citrus|mandarins?|tangerines?|clementines?)\b"), "🍊"),
    (re.compile(r"\b(?:bananas?|plantains?)\b"), "🍌"),
    (re.compile(r"\b(?:berry|berries|strawberr(?:y|ies)|blueberr(?:y|ies)|raspberr(?:y|ies)|currants?|"
                r"blackberr(?:y|ies)|cranberr(?:y|ies)|acerola|cherries|grapes?)\b"), "🍓"),
    (re.compile(r"\b(?:apricots?|mango(?:es)?|papayas?|guavas?|peach(?:es)?|figs?|dates?|prunes?|raisins?)\b"), "🥭"),
    (re.compile(r"\b(?:apples?|pears?)\b"), "🍎"),
    (re.compile(r"\b(?:spices?|herbs?|basil|thyme|oregano|dill|cumin|turmeric)\b"), "🌿"),
]

# Fallback by USDA category when the description names nothing specific.
_FOOD_CATEGORY_ICONS: list[tuple[str, str]] = [
    ("legume", "🫘"),
    ("finfish and shellfish", "🐟"),
    ("dairy and egg", "🧀"),
    ("poultry", "🍗"),
    ("beef", "🥩"),
    ("pork", "🥩"),
    ("lamb, veal", "🥩"),
    ("sausages", "🥩"),
    ("nut and seed", "🥜"),
    ("breakfast cereals", "🥣"),
    ("cereal grains", "🌾"),
    ("baked", "🍞"),
    ("vegetables", "🥦"),
    ("fruits", "🍓"),
    ("spices and herbs", "🌿"),
    ("beverages", "🥤"),
]


def _whole_food_icon_from_food(food: dict[str, Any] | None, component_key: str = "") -> str:
    if not food:
        return _whole_food_icon(component_key)

    desc = str(food.get("food_description", "") or "").lower()
    for pattern, icon in _FOOD_ICON_RULES:
        if pattern.search(desc):
            return icon
    category = str(food.get("food_category", "") or "").lower()
    for needle, icon in _FOOD_CATEGORY_ICONS:
        if needle in category:
            return icon
    return _whole_food_icon(component_key)


def _dietary_profile_lookup() -> tuple[list[str], dict[str, dict[str, Any]]]:
    profiles = bb.load_dietary_profiles()
    profile_by_id: dict[str, dict[str, Any]] = {}
    ordered_ids: list[str] = []
    for profile in profiles:
        pid = bb.normalize_lookup_key(str(profile.get("id", "") or ""))
        if not pid or pid in profile_by_id:
            continue
        profile_by_id[pid] = profile
        ordered_ids.append(pid)

    if "none" not in profile_by_id:
        profile_by_id["none"] = {
            "id": "none",
            "label": "No restriction",
            "description": "No dietary filtering",
            "avoid_keywords": [],
        }
        ordered_ids.insert(0, "none")

    return ordered_ids, profile_by_id


def _selected_dietary_profile() -> dict[str, Any] | None:
    ordered_ids, profile_by_id = _dietary_profile_lookup()
    selected_id = bb.normalize_lookup_key(str(st.session_state.get("swipe_diet_profile_id", "none") or "none"))
    if selected_id not in profile_by_id:
        selected_id = "none" if "none" in profile_by_id else (ordered_ids[0] if ordered_ids else "")
        st.session_state["swipe_diet_profile_id"] = selected_id
    return profile_by_id.get(selected_id)


def _init_state() -> None:
    defaults: dict[str, Any] = {
        "swipe_cards": [],
        "swipe_index": 0,
        "swipe_decisions": {},
        "swipe_analysis_text": "",
        "swipe_components": [],
        "swipe_rag_chats": {},
        "swipe_diet_profile_id": "none",
        "swipe_pregnant": False,
        "swipe_reset_nonce": 0,
        "swipe_is_analyzing": False,
        "swipe_pending_request": None,
        "swipe_analysis_kicked": False,
        "swipe_show_input_methods": False,
        "swipe_progress_pct": 0,
        "swipe_last_auto_signature": "",
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _analysis_input_signature(upload_bytes: bytes, camera_bytes: bytes, manual: str) -> str:
    upload_head = upload_bytes[:32] if isinstance(upload_bytes, (bytes, bytearray)) else b""
    camera_head = camera_bytes[:32] if isinstance(camera_bytes, (bytes, bytearray)) else b""
    manual_norm = str(manual or "").strip()
    return "|".join(
        [
            f"u:{len(upload_bytes)}:{upload_head.hex()}",
            f"c:{len(camera_bytes)}:{camera_head.hex()}",
            f"m:{manual_norm}",
        ]
    )


def _reset_swipe_app() -> None:
    next_nonce = int(st.session_state.get("swipe_reset_nonce", 0)) + 1
    keys_to_clear = [key for key in list(st.session_state.keys()) if key.startswith("swipe_")]
    for key in keys_to_clear:
        st.session_state.pop(key, None)
    st.session_state["swipe_reset_nonce"] = next_nonce
    _init_state()
    st.rerun()


@st.cache_data(show_spinner=False, ttl=6 * 3600, max_entries=64)
def _cached_extract_from_url(url: str, _llm_allowed: Any = None) -> str:
    # Raise instead of returning "": st.cache_data doesn't cache exceptions, so a
    # transient fetch/LLM failure is retried next time instead of sticking.
    # `_llm_allowed` (not part of the cache key) meters the LLM step: a cached
    # page or one the local parser reads costs no quota.
    text = str(bb.extract_supplement_text_from_url(url, llm_allowed=_llm_allowed) or "")
    if not text.strip() or bb.looks_like_agent_error(text):
        raise RuntimeError("couldn't read supplement facts from that page")
    return text


# A vision model's refusal or apology ("I'm sorry, I can't read the text in
# this image") is not label text: never cached (a transient refusal would
# stick for 6 h) and never treated as a label.
_OCR_REFUSAL_RE = re.compile(
    r"\b(?:i'?m sorry|i am sorry|i apologi[sz]e|sorry, (?:but )?i|i (?:can ?not|can'?t|am unable to|'m unable to|"
    r"was unable to|could ?n[o']t)\b|unable to (?:read|extract|see|process|identify)|as an ai\b|"
    r"es tut mir leid|leider (?:kann|konnte) ich|ich kann (?:den|die|das|keinen?)\b.{0,40}\bnicht)",
    re.IGNORECASE,
)


def _is_ocr_refusal(text: str) -> bool:
    """True for vision output that is a refusal / apology rather than label text
    (a short reply with a refusal phrase and no dose)."""
    raw = str(text or "").strip()
    if not raw or not _OCR_REFUSAL_RE.search(raw[:300]):
        return False
    return not re.search(r"\d\s*(?:mg|mcg|µg|ug|iu|i\.?e\.?|%)", raw, re.IGNORECASE)


def _ocr_has_product_words(text: str) -> bool:
    """True when OCR text holds at least two words a product search could use."""
    return len(re.findall(r"[A-Za-zÄÖÜäöüß]{3,}", str(text or ""))) >= 2


@st.cache_data(show_spinner=False, ttl=6 * 3600, max_entries=64)
def _cached_ocr(image_bytes: bytes) -> str:
    text = str(bb.extract_image_text_with_blockbrain(image_bytes) or "")
    if not text.strip():
        raise RuntimeError("vision OCR returned no text")
    if _is_ocr_refusal(text):
        raise RuntimeError("vision OCR returned a refusal, not label text")
    if bb.looks_like_agent_error(text):
        raise RuntimeError("vision OCR returned a Blockbrain error, not label text")
    return text


def _ocr_quality_score(text: str) -> tuple[int, int]:
    raw = str(text or "").strip()
    if not raw:
        return 0, 0
    try:
        gate = bb.extraction_gate_report(raw)
        score = int(gate.get("score", 0) or 0)
        dose_hits = int(gate.get("dose_hits", 0) or 0)
        nutrient_hits = int(gate.get("nutrient_hint_hits", 0) or 0)
        return score * 100 + dose_hits * 10 + nutrient_hits, len(raw)
    except Exception:
        dose_hits = len(re.findall(r"\b\d+(?:[\.,]\d+)?\s*(?:mg|mcg|ug|µg|g|iu|kcal)\b", raw, flags=re.I))
        return dose_hits, len(raw)


def _build_ocr_image_variants(image_bytes: bytes) -> list[tuple[str, bytes]]:
    """Small, fast variant first (~1400px JPEG, the size the vision benchmarks
    used), then a sharper ~2000px variant that is only sent when the first read
    is empty or weak. Both come from a single, size-limited decode; the
    full-resolution original is never uploaded and oversized images are refused."""
    try:
        return list(bb.build_vision_image_variants(image_bytes))
    except Exception:
        return []


def _extract_image_text_best_effort(image_bytes: bytes) -> tuple[str, str]:
    """Return the first OCR read that passes the label-quality gate; otherwise the
    best-scoring read across variants (so a weak small-image read still gets a
    second chance at higher resolution)."""
    if not _consume_llm_quota("vision"):
        raise RuntimeError(_QUOTA_MESSAGE)
    best_text, best_route, best_score = "", "", (-1, -1)
    for variant_name, variant_bytes in _build_ocr_image_variants(image_bytes):
        try:
            text = str(_cached_ocr(variant_bytes) or "").strip()
        except Exception:
            text = ""
        if not text:
            if bb.last_call_error():
                break  # the call itself failed (down, refused, timed out): a second try would only cost the budget again
            continue
        route = f"Blockbrain vision OCR ({variant_name})"
        try:
            if bb.extraction_gate_report(text).get("passed"):
                return text, route
        except Exception:
            return text, route
        score = _ocr_quality_score(text)
        if score > best_score:
            best_text, best_route, best_score = text, route, score
    return best_text, best_route


def _classify_image_kind(image_bytes: bytes, extracted_text: str) -> tuple[str, str]:
    text = str(extracted_text or "").strip()
    lower = text.lower()

    barcode = ""
    try:
        barcode, _method = bb.detect_barcode_from_image(image_bytes)
    except Exception:
        barcode = ""

    if barcode:
        return "ean_code", f"Detected EAN/barcode: {barcode}"

    if not lower:
        return "supplement_product", "Image has little readable text; treating as supplement product photo."

    label_signals = [
        "supplement facts",
        "serving size",
        "servings per container",
        "amount per serving",
        "% daily value",
        "daily value",
    ]
    if any(signal in lower for signal in label_signals):
        return "supplement_label", "Detected supplement label layout text."

    dose_hits = len(re.findall(r"\b\d+(?:[\.,]\d+)?\s*(?:mg|mcg|ug|µg|g|iu)\b", lower, flags=re.I))
    if dose_hits >= 3:
        return "supplement_label", "Detected multiple dose-like nutrient lines."

    return "supplement_product", "Detected product/front-pack style image."


# What visitors read when the AI helper is switched off (the settings detail is for the owner: ?debug=1 -> Diagnostics).
_AI_OFF_NOTE = (
    "Reading photos and product links is switched off right now. "
    "You can still paste the label text or a barcode number."
)
_AI_OFF_LINK = "Product links need the AI helper, which is switched off right now. Paste the label text instead."
_AI_OFF_PHOTO = "Photo reading is switched off right now. Paste the label text or a barcode number instead."


def _ai_is_on() -> bool:
    """False while the app has no usable Blockbrain configuration (every AI surface is then hidden or explained)."""
    try:
        return not bb.blockbrain_config_error()
    except Exception:
        return False


def _blockbrain_ready_error() -> str:
    missing = bb.blockbrain_config_error()
    if missing:
        return (
            f"Blockbrain is not configured ({missing}). Set these environment variables "
            "(or Streamlit secrets) — see swipe_mobile_app/README.md."
        )
    return ""


def _blockbrain_text_probe() -> tuple[bool, str]:
    try:
        reply = bb.call_blockbrain_text(
            "You are a connectivity checker.",
            "Reply with the exact word OK.",
        )
    except Exception as exc:
        return False, f"Blockbrain probe failed: {exc}"
    if not str(reply or "").strip():
        details = str(getattr(bb, "LAST_BLOCKBRAIN_ERROR", "") or "").strip()
        if details:
            return False, f"Blockbrain returned no text. {details}"
        return False, "Blockbrain returned no text response."
    return True, ""


@st.cache_resource(show_spinner=False)
def _cached_rag_chunks() -> list[dict[str, str]]:
    chunks, _status = bb.build_rag_index()
    return chunks


if _CODE_RELOADED:
    _cached_rag_chunks.clear()


# Per-session limits on LLM work so one anonymous visitor (or a stuck
# button) can't burn the app owner's Blockbrain credits. Cached answers are
# free. Override with SUPPSWIPE_MAX_GENERATIONS_PER_HOUR /
# SUPPSWIPE_MAX_SCANS_PER_HOUR.
_LLM_QUOTA_WINDOW_S = 3600


def _llm_quota_limit(kind: str) -> int:
    env_name = "SUPPSWIPE_MAX_SCANS_PER_HOUR" if kind == "vision" else "SUPPSWIPE_MAX_GENERATIONS_PER_HOUR"
    default = 15 if kind == "vision" else 40
    try:
        return max(1, int(os.getenv(env_name, "") or default))
    except ValueError:
        return default


def _global_llm_quota_limit() -> int:
    try:
        return max(1, int(os.getenv("SUPPSWIPE_MAX_LLM_CALLS_PER_HOUR_GLOBAL", "") or 600))
    except ValueError:
        return 600


def _global_llm_quota_limit_per_day() -> int:
    try:
        return max(1, int(os.getenv("SUPPSWIPE_MAX_LLM_CALLS_PER_DAY_GLOBAL", "") or 3000))
    except ValueError:
        return 3000


def _consume_global_llm_quota(now: float) -> bool:
    """The process-wide backstop (all sessions; per hour and per day). The ledger is in llm_cache, out of reach of the
    websocket `clear_cache` message that empties every st.cache_* store."""
    return llm_cache.consume_global(now, _LLM_QUOTA_WINDOW_S, _global_llm_quota_limit(), _global_llm_quota_limit_per_day())


def _consume_llm_quota(kind: str) -> bool:
    """Record one LLM use of `kind` ("vision" or "generate"); False when this
    session already used its hourly allowance, or the whole app its hourly
    backstop (SUPPSWIPE_MAX_LLM_CALLS_PER_HOUR_GLOBAL, default 600)."""
    import time as _time

    now = _time.time()
    store = st.session_state.setdefault("_suppswipe_llm_usage", {})
    recent = [t for t in store.get(kind, []) if now - t < _LLM_QUOTA_WINDOW_S]
    if len(recent) >= _llm_quota_limit(kind):
        store[kind] = recent
        return False
    if not _consume_global_llm_quota(now):
        store[kind] = recent
        return False
    recent.append(now)
    store[kind] = recent
    return True


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, "") or default))
    except ValueError:
        return default


def _consume_barcode_quota() -> bool:
    """One product-database look-up (OpenFoodFacts and friends): per session and per hour (SUPPSWIPE_MAX_BARCODE_LOOKUPS_PER_HOUR,
    default 30), and for the whole app (..._GLOBAL, default 900), so a script cannot use the app as a free relay to those services."""
    import time as _time

    now = _time.time()
    store = st.session_state.setdefault("_suppswipe_llm_usage", {})
    recent = [t for t in store.get("barcode", []) if now - t < _LLM_QUOTA_WINDOW_S]
    if len(recent) >= _env_int("SUPPSWIPE_MAX_BARCODE_LOOKUPS_PER_HOUR", 30):
        store["barcode"] = recent
        return False
    if not llm_cache.consume_counter(
        "barcode", now, _LLM_QUOTA_WINDOW_S, _env_int("SUPPSWIPE_MAX_BARCODE_LOOKUPS_PER_HOUR_GLOBAL", 900)
    ):
        store["barcode"] = recent
        return False
    recent.append(now)
    store["barcode"] = recent
    return True


_QUOTA_MESSAGE = (
    "You've reached this session's limit for AI answers. Please try again in a "
    "little while — saved answers still work."
)


def _generation_model() -> str:
    """Names the Blockbrain model (or a hash of the bot) that writes long-form answers: BLOCKBRAIN_MODEL / BLOCKBRAIN_BOT_ID.

    The model is a property of the bot, not of a request, so this is only part of the cache keys: answers
    written by a model that has since been replaced are never served for the new one."""
    try:
        return bb.blockbrain_model_fingerprint()
    except Exception:
        return ""


def _feature_model(feature: str) -> str:
    """Optional per-feature Blockbrain model id (BLOCKBRAIN_MODEL_MEAL / _BENEFITS / _ASK); used on the cortex route only."""
    try:
        bb._sync_blockbrain_env_from_secrets()
    except Exception:
        pass
    return str(os.environ.get(f"BLOCKBRAIN_MODEL_{feature.upper()}", "") or "").strip()


def _stream_llm_text(
    cache_key: str,
    system_prompt: str,
    user_prompt: str,
    placeholder: Any = None,
    history: list[dict[str, str]] | None = None,
    budget_s: float | None = None,
    consume_quota: bool = True,
    model: str = "",
) -> str:
    """Generate text, streaming partial output into `placeholder` (an st.empty()).

    Reuses a cached answer or a background prefetch for the same prompt when one
    exists; only non-empty answers are cached. `consume_quota=False` when the
    caller already counted this request against the session's allowance.
    """
    cached = llm_cache.get(cache_key)
    if cached:
        if placeholder is not None:
            placeholder.markdown(cached)
        return cached
    pending = llm_cache.inflight(cache_key)
    if pending is not None:
        text = _await_background_text(cache_key, pending, placeholder)
        if text:
            if placeholder is not None:
                placeholder.markdown(text)
            return text

    if bb.blockbrain_config_error():  # nothing can be asked: no allowance used, and the caller's message says why
        bb.note_config_error()
        if placeholder is not None:
            placeholder.empty()
        return ""
    if consume_quota and not _consume_llm_quota("generate"):
        if placeholder is not None:
            placeholder.info(_QUOTA_MESSAGE)
        return ""

    def _show(partial: str) -> None:
        if placeholder is not None and partial:
            placeholder.markdown(partial + " \u258c")

    try:
        text = str(
            bb.call_blockbrain_text(
                system_prompt,
                user_prompt,
                model=model or None,
                on_text=_show,
                history=history,
                budget_s=budget_s,
            )
            or ""
        ).strip()
    except Exception:
        text = ""
    if bb.looks_like_agent_error(text):  # a Blockbrain error is never an answer
        text = ""
    if text:
        llm_cache.put(cache_key, text)
        if placeholder is not None:
            placeholder.markdown(text)
    elif placeholder is not None:
        placeholder.empty()
    return text


def _await_background_text(cache_key: str, pending: Any, placeholder: Any = None) -> str:
    """Wait for a background generation, showing its partial text meanwhile."""
    deadline = time.monotonic() + float(bb.BLOCKBRAIN_TOTAL_BUDGET_S)
    shown = ""
    while not pending.done() and time.monotonic() < deadline:
        partial = llm_cache.partial(cache_key)
        if placeholder is not None and partial and partial != shown:
            placeholder.markdown(partial + " \u258c")
            shown = partial
        time.sleep(0.25)
    try:
        text = str(pending.result(timeout=0) or "").strip()
    except Exception:
        return ""
    return "" if bb.looks_like_agent_error(text) else text


_ASK_AI_HISTORY_MESSAGES = 6  # most recent chat messages sent as memory
_ASK_AI_MAX_CHARS = 500  # one question = one quota unit, so its size is capped (the websocket would take 25 MB)
# Under every Ask AI answer: where it came from. (The Examine knowledge-base bot is gone: every
# model answer now comes from blockbrain_llm_client.py.)
_SOURCE_AGENT = "\n\n_🤖 General AI answer (not medical advice)_"
_SOURCE_KB = "\n\n_📚 From the Examine knowledge base (not medical advice)_"
_RESULTS_DISCLAIMER = (
    "General nutrition information, not medical advice. Talk to a doctor or pharmacist before stopping "
    "a supplement you were prescribed, or if you are pregnant, ill or on medication."
)


_SOURCE_LABEL_RE = re.compile(r"\n\n_(?:📚|🤖)[^\n]*_\s*$")


def _chat_without_error_turns(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """A chat that was open while an older build stored a Blockbrain error as the answer: drop that
    "answer" and the question it was for (it is neither shown again nor sent to the model as history)."""
    kept: list[dict[str, str]] = []
    for msg in messages:
        is_answer = str(msg.get("role", "")).lower() != "user"
        if is_answer and bb.looks_like_agent_error(str(msg.get("content", "") or "")):
            if kept and str(kept[-1].get("role", "")).lower() == "user":
                kept.pop()
            continue
        kept.append(msg)
    return kept


def _ask_ai_history(component_key: str) -> list[dict[str, str]]:
    """Recent turns of this card's chat, without the appended 'Sources:' line and answer labels."""
    chat_store = st.session_state.get("swipe_rag_chats", {}) or {}
    turns: list[dict[str, str]] = []
    for msg in _chat_without_error_turns(list(chat_store.get(component_key, [])))[-_ASK_AI_HISTORY_MESSAGES:]:
        role = "user" if str(msg.get("role", "")).lower() == "user" else "assistant"
        content = re.sub(r"\n\nSources: .*$", "", str(msg.get("content", "") or ""), flags=re.S).strip()
        content = _SOURCE_LABEL_RE.sub("", content).strip()
        if content:
            turns.append({"role": role, "content": content[:1500]})
    return turns


def _answer_ask_ai_question(
    component_name: str,
    question: str,
    history: list[dict[str, str]] | None = None,
    placeholder: Any = None,
    dose_label: str = "",
) -> tuple[str | None, str]:
    """Answer an "Ask AI" question.

    Order of preference:
      1) the Blockbrain model (general nutrition reasoning; the answer appears when it is complete);
      2) the local research RAG index (no LLM).

    `history` carries the earlier turns of this chat so follow-up questions
    ("and for vegans?") are understood. First questions (no history) are cached.

    Returns (answer, sources_line). answer is None only when nothing at all is
    available (the model failed and no local index produced a response).
    """
    history = list(history or [])
    question = str(question or "")[:_ASK_AI_MAX_CHARS]
    dose_label = str(dose_label or "").strip()
    if dose_label.lower().startswith("dose not"):
        dose_label = ""
    dose_line = f"Dose in the user's supplement: {dose_label}\n" if dose_label else ""
    scoped_question = f"{component_name}: {question}".strip(": ").strip()
    cache_key = ""
    if not history:
        cache_key = llm_cache.make_key("ask_ai", component_name.strip().lower(), dose_label.lower(), question.strip().lower())
        cached = llm_cache.get(cache_key)
        if cached:
            return cached, _SOURCE_AGENT

    if bb.blockbrain_config_error():
        bb.note_config_error()
        return _local_rag_answer(scoped_question)

    # One question = one unit of the session's generation allowance (a cached first question above is free).
    if not _consume_llm_quota("generate"):
        if placeholder is not None:
            placeholder.info(_QUOTA_MESSAGE)
        return _local_rag_answer(scoped_question)

    # 1) The model.
    system_prompt = (
        "You are a supplement and micronutrient research assistant for the "
        "SuppSwipe app. Answer the user's question using established "
        "nutrition science. Be concise, evidence-based, and practical. If "
        "the evidence is unclear or the question is outside "
        "nutrition/supplementation, say so plainly. Do not give individual "
        "medical advice; speak in general terms."
        + _MARKDOWN_STYLE
    )
    user_prompt = (
        f"Micronutrient / supplement component: {component_name or 'unspecified'}\n"
        f"{dose_line}"
        f"Question: {question}"
    )
    # 1) The Examine knowledge-base bot (BLOCKBRAIN_KB_BOT_ID), when configured: answers with figures and sources.
    ask_model = _feature_model("ask")  # also syncs the Streamlit secrets into the environment
    if str(os.environ.get("BLOCKBRAIN_KB_BOT_ID", "") or "").strip():
        if placeholder is not None:
            placeholder.markdown("_Looking it up in the knowledge base…_")
        kb_answer, kb_sources = bb.call_blockbrain_ask(
            system_prompt + " Reply in the language of the question.", user_prompt, history=history,
            model=ask_model or None, budget_s=90,
        )
        if kb_answer and not bb.looks_like_agent_error(kb_answer):
            if cache_key:
                llm_cache.put(cache_key, kb_answer)
            if placeholder is not None:
                placeholder.markdown(kb_answer)
            return kb_answer, (_SOURCE_KB if kb_sources else _SOURCE_AGENT)
    # 2) The general model.
    answer = _stream_llm_text(
        cache_key or llm_cache.make_key("ask_ai_followup", component_name, question, history),
        system_prompt,
        user_prompt,
        placeholder=placeholder,
        history=history,
        budget_s=90,
        consume_quota=False,  # already counted for this question
        model=ask_model,
    )
    if answer:
        return answer, _SOURCE_AGENT

    # 2) Fallback: local research RAG index.
    return _local_rag_answer(scoped_question)


def _local_rag_answer(scoped_question: str) -> tuple[str | None, str]:
    """(answer, sources line) from the local research index (no LLM), or (None, "")."""
    try:
        chunks = _cached_rag_chunks()
    except Exception:
        chunks = []
    if not chunks:
        return None, ""
    # No model here: this is the fallback for an exhausted allowance or a switched-off AI (an LLM call would bypass the quota).
    answer, sources, _meta = bb.answer_rag_question(scoped_question, chunks, use_llm=False)
    sources_line = ""
    if sources:
        sources_line = "\n\nSources: " + ", ".join(sources[:4])
    return (answer or "No answer available."), sources_line


def _dose_label(component: dict[str, Any]) -> str:
    dose_value = component.get("dose_value")
    dose_unit = str(component.get("dose_unit", "") or "").strip()
    if dose_value is None:
        return "Dose not found"
    days = int(component.get("intake_days") or 1)
    if days > 1 and component.get("intake_dose_value") is not None:
        # A weekly product: the label's dose, then the daily average the card uses.
        per_intake = _dose_label({**component, "dose_value": component["intake_dose_value"],
                                  "dose_max": component.get("intake_dose_max"), "intake_days": 1})
        every = "once a week" if days == 7 else f"every {days} days"
        per_day = float(dose_value)
        digits = 0 if per_day >= 10 else 1 if per_day >= 1 else 3
        unit_txt = "IU" if bb.normalize_lookup_key(dose_unit) in bb._IU_UNIT_KEYS else dose_unit
        return f"{per_intake} {every} (~{bb.format_float(per_day, digits)} {unit_txt}/day)".replace("  ", " ")
    if bb.normalize_lookup_key(dose_unit) in bb._IU_UNIT_KEYS:
        dose_unit = "IU"  # "1000 IU", never the parser's lowercase "iu"
    try:
        # 3 decimals so small label doses stay exact ("0.025 mg", not "0.03 mg").
        low = bb.format_float(float(dose_value), 3)
        if component.get("dose_max") is not None and float(component["dose_max"]) > float(dose_value):
            # A label range ("Vitamin C 100-200 mg"): portions use the lower bound.
            return f"{low}–{bb.format_float(float(component['dose_max']), 3)} {dose_unit}".strip()
        return f"{low} {dose_unit}".strip()
    except Exception:
        return str(dose_value)


def _food_name(food: dict[str, Any] | None) -> str:
    """Short shopper-friendly name of a food row ("Lamb liver"); the full USDA
    name stays in food["food_description"] (shown as caption/tooltip)."""
    full = str((food or {}).get("food_description", "") or "").strip()
    if not full:
        return ""
    try:
        return bb.food_display_name(full) or full
    except Exception:
        return full


# Card names for nutrients the lexicon spells as abbreviations or merged rows.
_NUTRIENT_TITLE_OVERRIDES = {
    "omega-3 (epa+dha)": "Omega-3 (EPA+DHA)",
    "epa": "EPA",
    "dha": "DHA",
}


def _nutrient_title(name: Any) -> str:
    """Display name of a nutrient for cards, results, share text, history and
    prompts: the lexicon's card name, capitalised ("folsäure" -> "Folic acid",
    "vitamin d3" -> "Vitamin D3", "jod" -> "Iodine"). Unknown names are kept
    as written, only the first letter is raised. Idempotent."""
    raw = str(name or "").strip()
    if not raw:
        return ""
    override = _NUTRIENT_TITLE_OVERRIDES.get(raw.lower())
    if override:
        return override
    try:
        display = bb.nutrient_display_name(raw) or raw
    except Exception:
        display = raw
    override = _NUTRIENT_TITLE_OVERRIDES.get(display.lower())
    if override:
        return override
    if display != display.lower():
        return display[:1].upper() + display[1:]  # unknown name, already cased
    display = re.sub(r"\bvitamin ([a-z]\d{0,2})\b", lambda m: "Vitamin " + m.group(1).upper(), display)
    display = re.sub(r"\b(epa|dha|ala)\b", lambda m: m.group(1).upper(), display)
    return display[:1].upper() + display[1:]


def _food_label(food: dict[str, Any]) -> str:
    try:
        amount_per_100g = float(food.get("amount_per_100g", 0.0) or 0.0)
    except Exception:
        amount_per_100g = 0.0
    unit_raw = str(food.get("unit", "") or "")
    amount_txt, unit_txt = bb.format_amount_unit_for_dropdown(amount_per_100g, unit_raw)
    food_name = _food_name(food) or "Unknown food"
    if amount_txt and unit_txt:
        return f"{food_name} ({amount_txt} {unit_txt}/100g)"
    return food_name


def _amount_to_match_dose(decision: dict[str, Any]) -> str:
    """How much of the chosen whole food is needed to match the supplement dose.

    Returns a short label like "eat ~85 g" (plus a portion estimate such as
    "~2 eggs" when one is available), or "" when it can't be computed.
    """
    food = decision.get("selected_food") or {}
    core = _portion_for_target(
        food,
        decision.get("dose_value"),
        str(decision.get("dose_unit", "") or ""),
        str(decision.get("component", "") or ""),
        str(decision.get("form", "") or ""),
    )
    if not core:
        return ""
    return core if core.startswith("not practical") else f"eat {core}"


# Daily portions above these sizes are flagged instead of presented as a normal
# serving: 400-1000 g is a lot of food, more than 1 kg/day is not practical.
# Energy-dense foods are judged by energy too: a portion of 600 kcal or more
# for one nutrient (~110 g of nuts) is a lot of food whatever it weighs.
_PORTION_LARGE_G = 400.0
_PORTION_IMPRACTICAL_G = 1000.0
_PORTION_LARGE_KCAL = 600.0

# Realistic daily maximum of foods that a weight threshold alone would call a
# normal portion (USDA names / categories): chia (pre-packed chia sold in the
# EU must state a 15 g/day maximum), nuts and seeds (~70 g, a generous
# handful; bb.SERVING_SIZE_GROUP_RULES "nuts_seeds_group"), egg yolks (~3 a
# day at ~17 g), whole eggs (~4 a day), garlic (~3 cloves), hot chili peppers
# (a spice, ~2 peppers) and fish roe / caviar.
_NUT_SEED_NAME_RE = re.compile(
    r"^\s*(?:nuts|seeds|peanuts?|almonds?|walnuts?|hazelnuts?|cashews?|pistachios?|pecans?|macadamias?|"
    r"brazil ?nuts?|pine nuts?|sunflower seeds?|pumpkin seeds?|flaxseeds?|linseeds?|sesame seeds?)\b",
    re.IGNORECASE,
)
_NUT_SEED_NOT_SOLID_RE = re.compile(r"\b(?:water|milk|cream|beverage|drink|oil)\b", re.IGNORECASE)
_FOOD_DAILY_MAX_RULES: tuple[tuple[re.Pattern[str], float], ...] = (
    (re.compile(r"\bchia\b", re.IGNORECASE), 15.0),
    (re.compile(r"\byolks?\b", re.IGNORECASE), 51.0),
    (re.compile(r"^\s*eggs?\b", re.IGNORECASE), 200.0),
    (re.compile(r"\bgarlic\b", re.IGNORECASE), 10.0),
    (re.compile(r"\bpeppers?, hot chili\b|\bhot chili peppers?\b", re.IGNORECASE), 15.0),
    (re.compile(r"\b(?:roe|caviar)\b", re.IGNORECASE), 50.0),
)
_NUTS_SEEDS_MAX_DAILY_G = 70.0


def _food_max_daily_g(food: dict[str, Any] | None) -> float:
    """A food's own realistic daily maximum in grams ("max_daily_g", else the
    rules above), or 0 when it has none."""
    if not isinstance(food, dict):
        return 0.0
    try:
        own = float(food.get("max_daily_g") or 0.0)
    except Exception:
        own = 0.0
    if own > 0:
        return own
    name, category = _food_name_and_category(food)
    for pattern, max_g in _FOOD_DAILY_MAX_RULES:
        if pattern.search(name):
            return max_g
    nut_or_seed = "nut and seed" in category.lower() or _NUT_SEED_NAME_RE.search(name)
    if nut_or_seed and not _NUT_SEED_NOT_SOLID_RE.search(name):
        return _NUTS_SEEDS_MAX_DAILY_G
    return 0.0


def _portion_kcal(grams: float | None, food: dict[str, Any] | None) -> float | None:
    """Energy of `grams` of a USDA food (kcal), or None when unknown."""
    if not isinstance(food, dict) or not grams:
        return None
    try:
        kcal_100g = bb.food_energy_kcal_per_100g(str(food.get("food_description", "") or ""))
    except Exception:
        kcal_100g = None
    return float(grams) * float(kcal_100g) / 100.0 if kcal_100g else None


def _portion_practicality(grams: float | None, food: dict[str, Any] | None = None) -> str:
    """"ok" | "large" (400-1000 g/day, or >= 600 kcal) | "impractical" (> 1 kg/day)
    for a daily food amount.

    A food with its own realistic daily maximum (_food_max_daily_g: ~30 g of
    fortified yeast flakes, ~750 ml of a fortified plant drink, 15 g of chia,
    ~70 g of nuts, ~3 egg yolks) is "impractical" above it."""
    try:
        value = float(grams) if grams is not None else 0.0
    except Exception:
        value = 0.0
    own_max = _food_max_daily_g(food)
    if value > _PORTION_IMPRACTICAL_G or (own_max > 0 and value > own_max):
        return "impractical"
    if value >= _PORTION_LARGE_G:
        return "large"
    kcal = _portion_kcal(value, food)
    if kcal is not None and kcal >= _PORTION_LARGE_KCAL:
        return "large"
    return "ok"


def _format_grams(grams: float) -> str:
    if grams < 1:
        return "<1 g"
    if grams < 10:
        return f"{bb.format_float(grams, 1)} g"
    if grams < 1000:
        return f"{bb.format_float(grams, 0)} g"
    return f"{bb.format_float(grams / 1000.0, 1)} kg"


def _portion_for_target(
    food: dict[str, Any],
    target_value: Any,
    target_unit: str,
    component: str,
    form: str = "",
    note: bool = True,
) -> str:
    """How much of `food` supplies `target_value target_unit` of the nutrient.

    Returns a short label like "~85 g (~2 eggs)", "<1 g", "a lot of food (~450
    g/day)", "not practical from food alone (~23.5 kg/day)" or, past a food's
    own realistic daily amount, "not practical from food alone (~40 g/day;
    realistic max ~30 g/day)", or "" when it can't be computed. Units of the target and the food need not match — both
    are normalised by bb.grams_needed_to_match_dose; `form` (the label's "(as
    ...)" text) selects the IU / folic-acid conversions for a PILL dose.
    With `note`, a UV-treated mushroom on a vitamin D card says that only
    UV-treated ones count.
    """
    core = _portion_core_for_target(food, target_value, target_unit, component, form)
    if note and core and not core.startswith("not practical"):
        key = bb.canonical_nutrient_key(component)
        if _is_uv_mushroom(food) and key == "vitamin d":
            core += f" {_UV_MUSHROOM_NOTE}"
        elif key == "vitamin b12" and _is_b12_fortified_food(food):
            core += f" {_FORTIFIED_B12_NOTE}"
    return core


_UV_MUSHROOM_NOTE = "(only UV-treated mushrooms — regular mushrooms contain almost no vitamin D)"
# EU-organic (Bio) foods may not be fortified, so a "soy drink" alone is no B12 source.
_FORTIFIED_B12_NOTE = "(B12-fortified only — check the label; Bio/organic products contain no added B12)"


def _portion_core_for_target(
    food: dict[str, Any],
    target_value: Any,
    target_unit: str,
    component: str,
    form: str = "",
) -> str:
    if not isinstance(food, dict):
        return ""
    try:
        amount_per_100g = float(food.get("amount_per_100g", 0.0) or 0.0)
    except Exception:
        amount_per_100g = 0.0
    unit = str(food.get("unit", "") or "")
    food_name = str(food.get("food_description", "") or "")

    try:
        grams = bb.grams_needed_to_match_dose(target_value, target_unit, amount_per_100g, unit, component, form)
    except Exception:
        grams = None
    if grams is None or grams <= 0:
        return ""

    practicality = _portion_practicality(grams, food)
    if practicality == "impractical":
        if grams > _PORTION_IMPRACTICAL_G:
            return f"not practical from food alone (~{bb.format_float(grams / 1000.0, 1)} kg/day)"
        # Past the food's own realistic daily amount (~30 g of yeast flakes):
        # grams, never "~0 kg/day".
        return f"not practical from food alone (~{_format_grams(grams)}/day; realistic max ~{_format_grams(_food_max_daily_g(food))}/day)"
    if practicality == "large":
        kcal = _portion_kcal(grams, food)
        if kcal is not None and kcal >= _PORTION_LARGE_KCAL:
            return f"a lot of food (~{bb.format_float(grams, 0)} g/day, ~{_round_total(kcal)} kcal)"
        return f"a lot of food (~{bb.format_float(grams, 0)} g/day)"

    grams_txt = _format_grams(grams)
    portion = ""
    try:
        portion_full = bb.estimate_whole_food_units(food_name, grams)
        # estimate_whole_food_units returns a long sentence; extract just the "~N unit" part.
        m = re.search(r"~[^()]+", str(portion_full or ""))
        if m:
            portion = m.group(0).strip().rstrip(".").strip()
    except Exception:
        portion = ""

    core = grams_txt if grams_txt.startswith("<") else f"~{grams_txt}"
    if portion:
        return f"{core} ({portion})"
    return core


# --- Daily micronutrient targets ---------------------------------------------
# One authoritative table used by both the per-card portion hint and the final
# "Athlete RDA guide". "rda" = general adult RDA/AI (NIH ODS); "athlete" = a
# representative daily target for active people (ISSN 2017; ACSM/AND/DC 2016),
# raised where training increases needs or sweat losses. Units are chosen so
# they normalise cleanly against USDA food units for the portion math. "keys"
# are bb canonical nutrient keys; optional "match" words are a whole-word fallback
# only for partial names the lexicon does not know ("ascorbic", "folic").
# General guidance only — not individualised medical advice.
_MICRONUTRIENT_RDA: list[dict[str, Any]] = [
    {"display": "Vitamin B12", "unit": "mcg", "rda": 2.4, "athlete": 4.0, "keys": ["vitamin b12"]},
    {"display": "Vitamin B9 (Folate)", "unit": "mcg", "rda": 400, "athlete": 600, "keys": ["folate"], "match": ["folic"]},
    {"display": "Vitamin B7 (Biotin)", "unit": "mcg", "rda": 30, "athlete": 30, "keys": ["biotin"]},
    {"display": "Vitamin B6", "unit": "mg", "rda": 1.3, "athlete": 2.0, "keys": ["vitamin b6"]},
    {"display": "Vitamin B5 (Pantothenic)", "unit": "mg", "rda": 5, "athlete": 7, "keys": ["pantothenic acid"], "match": ["pantothenic"]},
    {"display": "Vitamin B3 (Niacin)", "unit": "mg", "rda": 16, "athlete": 20, "keys": ["niacin"], "match": ["nicotinic"]},
    {"display": "Vitamin B2 (Riboflavin)", "unit": "mg", "rda": 1.3, "athlete": 2.0, "keys": ["riboflavin"]},
    {"display": "Vitamin B1 (Thiamin)", "unit": "mg", "rda": 1.2, "athlete": 2.0, "keys": ["thiamin"]},
    {"display": "Vitamin A", "unit": "mcg", "rda": 900, "athlete": 1000, "keys": ["vitamin a"]},
    {"display": "Vitamin C", "unit": "mg", "rda": 90, "athlete": 200, "keys": ["vitamin c"], "match": ["ascorbic"]},
    {"display": "Vitamin D", "unit": "mcg", "rda": 15, "athlete": 25, "keys": ["vitamin d"]},
    {"display": "Vitamin E", "unit": "mg", "rda": 15, "athlete": 20, "keys": ["vitamin e"]},
    {"display": "Vitamin K", "unit": "mcg", "rda": 120, "athlete": 120, "keys": ["vitamin k", "vitamin k2"]},
    {"display": "Calcium", "unit": "mg", "rda": 1000, "athlete": 1300, "keys": ["calcium"]},
    {"display": "Phosphorus", "unit": "mg", "rda": 700, "athlete": 1000, "keys": ["phosphorus"], "match": ["phosphate"]},
    {"display": "Magnesium", "unit": "mg", "rda": 400, "athlete": 500, "keys": ["magnesium"]},
    {"display": "Potassium", "unit": "mg", "rda": 3400, "athlete": 3500, "keys": ["potassium"]},
    {"display": "Sodium", "unit": "mg", "rda": 1500, "athlete": 2300, "keys": ["sodium"]},
    {"display": "Chloride", "unit": "mg", "rda": 2300, "athlete": 2300, "keys": ["chloride"]},
    {"display": "Iron", "unit": "mg", "rda": 8, "athlete": 18, "keys": ["iron"]},
    {"display": "Zinc", "unit": "mg", "rda": 11, "athlete": 15, "keys": ["zinc"]},
    {"display": "Copper", "unit": "mg", "rda": 0.9, "athlete": 1.2, "keys": ["copper"]},
    {"display": "Manganese", "unit": "mg", "rda": 2.3, "athlete": 2.3, "keys": ["manganese"]},
    {"display": "Iodine", "unit": "mcg", "rda": 150, "athlete": 150, "keys": ["iodine"]},
    {"display": "Selenium", "unit": "mcg", "rda": 55, "athlete": 70, "keys": ["selenium"]},
    {"display": "Molybdenum", "unit": "mcg", "rda": 45, "athlete": 45, "keys": ["molybdenum"]},
    {"display": "Chromium", "unit": "mcg", "rda": 35, "athlete": 35, "keys": ["chromium"]},
    {"display": "Fluoride", "unit": "mg", "rda": 4, "athlete": 4, "keys": ["fluoride"]},
    {"display": "Choline", "unit": "mg", "rda": 550, "athlete": 550, "keys": ["choline"]},
    # EPA+DHA target; single EPA or DHA cards have no matching target of their own.
    {"display": "Omega-3 (EPA+DHA)", "unit": "g", "rda": 0.25, "athlete": 2.0, "keys": ["omega 3", "fish oil"]},
    {"display": "Omega-3 ALA", "unit": "g", "rda": 1.6, "athlete": 1.6, "keys": ["ala"]},
]
_RDA_BY_NUTRIENT_KEY: dict[str, dict[str, Any]] = {
    key: entry for entry in _MICRONUTRIENT_RDA for key in entry["keys"]
}


def _nutrient_name_head(name: str) -> str:
    """Normalised nutrient name BEFORE any "(as ...)" form ("Iodine (as
    potassium iodide)" -> "iodine"); the whole name if nothing precedes it."""
    raw = str(name or "")
    head = re.split(r"[(\[]", raw, maxsplit=1)[0]
    return bb.normalize_lookup_key(head if head.strip() else raw).replace("-", " ")


def _whole_word_in(needle: str, text: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])", text) is not None


def _rda_for_component(component_key: str) -> dict[str, Any] | None:
    key = bb.canonical_nutrient_key(component_key)
    if key:
        return _RDA_BY_NUTRIENT_KEY.get(key)
    head = _nutrient_name_head(component_key)
    if not head:
        return None
    for entry in _MICRONUTRIENT_RDA:
        if any(_whole_word_in(m, head) for m in entry.get("match", ())):
            return entry
    return None


def _format_rda_target(entry: dict[str, Any]) -> str:
    return f"{bb.format_float(float(entry['athlete']))} {entry['unit']}"


# --- Safe upper intake levels (adult ULs) -------------------------------------
# Applied to the PILL dose. For each nutrient the STRICTER of EFSA and NIH ODS
# is used (EFSA Scientific Committee ULs incl. the 2022-2024 updates for B6,
# selenium and iron; NIH ODS fact sheets):
#   nutrient      EFSA UL                         NIH ODS UL              used
#   vitamin A     3000 µg RE (preformed only)     3000 µg RAE (preformed) 3000 µg  - retinol/retinyl, NOT beta-carotene
#   vitamin D     100 µg                          100 µg (4000 IU)        100 µg
#   vitamin E     300 mg                          1000 mg                 300 mg   EFSA
#   vitamin C     not set                         2000 mg                 2000 mg  NIH
#   niacin        nicotinic acid 10 mg,           35 mg (supplements)     nicotinic acid 10 mg (EFSA, the flushing
#                 nicotinamide 900 mg                                     form); otherwise 35 mg (NIH)
#   vitamin B6    12 mg (2023)                    100 mg                  12 mg    EFSA
#   folic acid    1000 µg (synthetic only)        1000 µg                 1000 µg  - folic acid only, not food folate
#   calcium       2500 mg                         2500 mg                 2500 mg
#   iron          40 mg (2024)                    45 mg                   40 mg    EFSA
#   zinc          25 mg                           40 mg                   25 mg    EFSA
#   copper        5 mg                            10 mg                   5 mg     EFSA
#   selenium      255 µg (2023)                   400 µg                  255 µg   EFSA
#   iodine        600 µg                          1100 µg                 600 µg   EFSA
#   magnesium     250 mg (supplements)            350 mg (supplements)    250 mg   EFSA
#   manganese     not set                         11 mg                   11 mg    NIH
#   molybdenum    600 µg                          2000 µg                 600 µg   EFSA
#   fluoride      7 mg                            10 mg                   7 mg     EFSA
#   phosphorus    not set                         4000 mg                 4000 mg  NIH
#   choline       not set                         3500 mg                 3500 mg  NIH
# No UL is set for vitamin K, B1, B2, B5, biotin, B12, chromium, potassium or
# beta-carotene (EFSA: <15 mg/day supplemental beta-carotene is of no concern,
# even for smokers; above that smokers should avoid it).
_UPPER_LIMITS: dict[str, dict[str, Any]] = {
    "vitamin a": {"name": "vitamin A", "limit": 3000.0, "unit": "mcg", "source": "EFSA & NIH"},
    "vitamin d": {"name": "vitamin D", "limit": 100.0, "unit": "mcg", "source": "EFSA & NIH"},
    "vitamin e": {"name": "vitamin E", "limit": 300.0, "unit": "mg", "source": "EFSA"},
    "vitamin c": {"name": "vitamin C", "limit": 2000.0, "unit": "mg", "source": "NIH"},
    "niacin": {"name": "niacin", "limit": 35.0, "unit": "mg", "source": "NIH, from supplements"},
    "vitamin b6": {"name": "vitamin B6", "limit": 12.0, "unit": "mg", "source": "EFSA"},
    "folate": {"name": "folic acid", "limit": 1000.0, "unit": "mcg", "source": "EFSA & NIH"},
    "calcium": {"name": "calcium", "limit": 2500.0, "unit": "mg", "source": "EFSA & NIH"},
    "iron": {"name": "iron", "limit": 40.0, "unit": "mg", "source": "EFSA"},
    "zinc": {"name": "zinc", "limit": 25.0, "unit": "mg", "source": "EFSA"},
    "copper": {"name": "copper", "limit": 5.0, "unit": "mg", "source": "EFSA"},
    "selenium": {"name": "selenium", "limit": 255.0, "unit": "mcg", "source": "EFSA"},
    "iodine": {"name": "iodine", "limit": 600.0, "unit": "mcg", "source": "EFSA"},
    "magnesium": {"name": "magnesium", "limit": 250.0, "unit": "mg", "source": "EFSA, from supplements"},
    "manganese": {"name": "manganese", "limit": 11.0, "unit": "mg", "source": "NIH"},
    "molybdenum": {"name": "molybdenum", "limit": 600.0, "unit": "mcg", "source": "EFSA"},
    "fluoride": {"name": "fluoride", "limit": 7.0, "unit": "mg", "source": "EFSA"},
    "phosphorus": {"name": "phosphorus", "limit": 4000.0, "unit": "mg", "source": "NIH"},
    "choline": {"name": "choline", "limit": 3500.0, "unit": "mg", "source": "NIH"},
}
_VITAMIN_E_UL_MG_PER_IU = 0.67
_NICOTINIC_ACID_UPPER_LIMIT = {"name": "niacin as nicotinic acid", "limit": 10.0, "unit": "mg", "source": "EFSA — the form that causes flushing"}
_BETA_CAROTENE_SMOKER_MG = 15.0


def _dose_in_unit(component: str, value: Any, unit: str, target_unit: str, form: str = "") -> float | None:
    """`value unit` of a pill dose expressed in `target_unit` (mg/mcg/g), IU included."""
    try:
        amount = float(value)
    except Exception:
        return None
    factor = bb.unit_to_mg(str(unit or ""))
    if factor is None and bb.normalize_lookup_key(str(unit or "")) in bb._IU_UNIT_KEYS:
        factor = bb._iu_unit_to_mg_for_component(component, form)
    target_factor = bb.unit_to_mg(target_unit)
    if factor is None or not target_factor:
        return None
    return amount * factor / target_factor


def _dose_text(value: Any, unit: str) -> str:
    unit_txt = "IU" if bb.normalize_lookup_key(str(unit or "")) in bb._IU_UNIT_KEYS else str(unit or "")
    try:
        return f"{bb.format_float(float(value))} {unit_txt}".strip()
    except Exception:
        return f"{value} {unit_txt}".strip()


def _form_dose_parts(form: str) -> list[tuple[str, float, str]]:
    """(form, value, unit) parts of a merged multi-form dose
    ("retinyl palmitate 450 mcg + beta carotene 450 mcg")."""
    parts: list[tuple[str, float, str]] = []
    for chunk in str(form or "").split(" + "):
        m = re.search(r"^(?P<form>.*?)\s+(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>mcg|mg|g|iu)\s*$", chunk.strip(), re.I)
        if m:
            parts.append((m.group("form"), float(m.group("num")), m.group("unit").lower()))
    return parts


def _upper_limit_dose(key: str, component: str, value: Any, unit: str, form: str) -> tuple[float, str, dict[str, Any]] | None:
    """(amount in the UL unit, dose text, UL entry) for the part of the dose a UL applies to."""
    entry = _UPPER_LIMITS.get(key)
    if entry is None or value is None:
        return None
    form_l = bb.normalize_lookup_key(str(form or "")).replace("-", " ")
    if key == "niacin" and re.search(r"\bnicotinic acid|\bnicotinsaure", form_l):
        entry = _NICOTINIC_ACID_UPPER_LIMIT
    if key == "vitamin a":
        parts = _form_dose_parts(form)
        if parts:  # merged retinyl + beta-carotene: only the preformed share counts
            total = 0.0
            for part_form, part_value, part_unit in parts:
                if "carot" not in part_form.lower():
                    total += _dose_in_unit(component, part_value, part_unit, entry["unit"], part_form) or 0.0
            return total, f"{bb.format_float(total)} {entry['unit']} of preformed vitamin A", entry
        share = bb.vitamin_a_beta_carotene_share(component or "vitamin a", form)
        if share >= 1.0:
            return None  # beta-carotene has no UL
        if share > 0.0:  # "(50% as beta-carotene)": only the preformed share counts
            if bb.normalize_lookup_key(str(unit or "")) in bb._IU_UNIT_KEYS:
                preformed = _dose_in_unit("vitamin a", float(value) * (1.0 - share), unit, entry["unit"], "retinyl")
            else:
                full = _dose_in_unit(component, value, unit, entry["unit"], form)
                preformed = full * (1.0 - share) if full is not None else None
            if preformed is None:
                return None
            return preformed, f"{_dose_text(value, unit)} ({bb.format_float(preformed)} {entry['unit']} preformed vitamin A)", entry
    if key == "folate":
        share = re.search(r"(\d+(?:\.\d+)?)\s*(mcg|mg)\s+folic acid", form_l)
        if share:
            amount = _dose_in_unit(component, share.group(1), share.group(2), entry["unit"])
            return (amount, f"{bb.format_float(float(share.group(1)))} {share.group(2)} folic acid", entry) if amount is not None else None
        if re.search(r"\bdfe\b", form_l) and re.search(r"\bfolic acid\b|\bfolsaure\b", form_l):
            # "Folate 2,000 mcg DFE (as folic acid)": 1 µg folic acid = 1.7 µg DFE.
            dfe = _dose_in_unit(component, value, unit, entry["unit"])
            if dfe is None:
                return None
            folic = dfe / bb._FOLIC_ACID_TO_DFE
            return folic, f"{_dose_text(value, unit)} DFE (~{bb.format_float(folic, 0)} {entry['unit']} folic acid)", entry
        if not bb._is_folic_acid_dose(component, form):
            return None  # food folate / methylfolate / DFE without a folic-acid share
    if key == "vitamin e" and bb.normalize_lookup_key(str(unit or "")) in bb._IU_UNIT_KEYS:
        # The EFSA limit is 300 mg alpha-TE, and 1 IU of any vitamin E form is
        # ~0.67 mg alpha-TE (the 0.45 mg/IU of dl-alpha is an RDA activity
        # factor, kept only for the food portions).
        try:
            amount = float(value) * _VITAMIN_E_UL_MG_PER_IU
        except Exception:
            return None
        return amount, f"{_dose_text(value, unit)} (~{bb.format_float(amount, 0)} mg alpha-TE)", entry
    amount = _dose_in_unit(component, value, unit, entry["unit"], form)
    if amount is None:
        return None
    text = _dose_text(value, unit)
    if bb.normalize_lookup_key(str(unit or "")) in bb._IU_UNIT_KEYS:
        text += f" ({bb.format_float(amount)} {entry['unit']})"
    return amount, text, entry


def _upper_limit_warning(component_key: str, dose_value: Any, dose_unit: str, form: str = "") -> str:
    """Warning text when the PILL dose is above the adult upper intake level, else "".

    e.g. "⚠️ 50 mg is above the upper intake level for vitamin B6 (12 mg/day,
    EFSA) — ask your doctor or pharmacist whether this dose suits you."
    """
    component = str(component_key or "")
    key = bb.canonical_nutrient_key(component)
    if key in ("beta carotene", "vitamin a") and dose_value is not None:
        beta_mg = None
        if key == "beta carotene":
            beta_mg = _dose_in_unit(component, dose_value, dose_unit, "mg", form)
        if beta_mg is not None and beta_mg >= _BETA_CAROTENE_SMOKER_MG:
            return (
                f"⚠️ {_dose_text(dose_value, dose_unit)} beta-carotene: EFSA advises smokers not to take "
                f"{bb.format_float(_BETA_CAROTENE_SMOKER_MG)} mg/day or more from supplements."
            )
    found = _upper_limit_dose(key, component, dose_value, dose_unit, form)
    if found is None:
        return ""
    amount, dose_txt, entry = found
    if amount <= float(entry["limit"]) * (1 + 1e-9):
        return ""
    return (
        f"⚠️ {dose_txt} is above the upper intake level for {entry['name']} "
        f"({bb.format_float(float(entry['limit']))} {entry['unit']}/day, {entry['source']}) — "
        "ask your doctor or pharmacist whether this dose suits you."
    )


# --- The food on the card: co-nutrient limits and the default choice ----------
# A food that matches one nutrient can push ANOTHER past its upper intake level:
# liver's preformed vitamin A (fish livers only have USDA IU rows), Brazil-nut
# selenium, oyster zinc, liver copper, cod iodine. The check covers both
# portions the card shows — for the pill dose and for the athlete target.
_CO_NUTRIENT_LIMIT_KEYS = ("selenium", "iodine", "copper", "zinc")


def _food_name_and_category(food: dict[str, Any] | None) -> tuple[str, str]:
    food = food if isinstance(food, dict) else {}
    return str(food.get("food_description", "") or ""), str(food.get("food_category", "") or "")


def _food_portion_grams(
    food: dict[str, Any] | None, target_value: Any, target_unit: str, component: str, form: str = ""
) -> float | None:
    """Grams of `food` that supply `target_value target_unit`, or None."""
    if not isinstance(food, dict) or target_value is None:
        return None
    try:
        grams = bb.grams_needed_to_match_dose(
            target_value, target_unit, float(food.get("amount_per_100g", 0.0) or 0.0),
            str(food.get("unit", "") or ""), component, form,
        )
    except Exception:
        return None
    return grams if grams and grams > 0 else None


def _card_portions(food: dict[str, Any] | None, dose_value: Any, dose_unit: str, component: str, form: str = "") -> list[float]:
    """The daily amounts of `food` the card shows: for the pill dose and for
    the athlete daily target."""
    grams = [g for g in (_food_portion_grams(food, dose_value, dose_unit, component, form),) if g]
    entry = _rda_for_component(component)
    if entry is not None:
        athlete = _food_portion_grams(food, entry["athlete"], str(entry["unit"]), str(entry["display"]))
        if athlete:
            grams.append(athlete)
    return grams


def _edible_portion(grams: float, food: dict[str, Any] | None) -> float | None:
    """The part of a card portion someone could actually eat: none of one over
    1 kg/day, at most the food's realistic daily maximum otherwise (70 g of
    Brazil nuts still hold far too much selenium)."""
    if grams > _PORTION_IMPRACTICAL_G:
        return None
    own_max = _food_max_daily_g(food)
    return min(grams, own_max) if own_max > 0 else grams


def _card_portion_grams(food: dict[str, Any] | None, dose_value: Any, dose_unit: str, component: str, form: str = "") -> float | None:
    """The largest of the card's portions that someone could actually eat
    (_edible_portion: none over 1 kg/day, capped at the food's realistic daily
    maximum). None if there is none."""
    edible = [e for g in _card_portions(food, dose_value, dose_unit, component, form) if (e := _edible_portion(g, food))]
    return max(edible) if edible else None


def _all_portion_grams(food: dict[str, Any] | None, dose_value: Any, dose_unit: str, component: str, form: str = "") -> float | None:
    """The largest of the card's portions, a "not practical" one included."""
    return max(_card_portions(food, dose_value, dose_unit, component, form), default=None)


def _is_liver(food: dict[str, Any] | None) -> bool:
    name, category = _food_name_and_category(food)
    return bb.food_is_organ_meat(name, category) and re.search(r"\bliver\b", name.lower()) is not None


def _liver_vitamin_a_warning(food: dict[str, Any] | None, grams: float | None, component: str = "") -> str:
    """Liver tops the B2 / B12 / folate / copper lists, but its vitamin A is
    preformed retinol: warn when the portion passes the vitamin A upper limit
    (e.g. ~92 g duck liver for 680 µg folate = ~11,000 µg). Liver is to be
    avoided in pregnancy — always said on folate cards, as folic acid is THE
    pregnancy supplement."""
    if not _is_liver(food):
        return ""
    name, category = _food_name_and_category(food)
    limit = float(_UPPER_LIMITS["vitamin a"]["limit"])
    preformed = bb.food_preformed_vitamin_a(name, category)
    if preformed is None:
        return (
            "⚠️ Liver is very rich in preformed vitamin A (USDA has no exact value for this one) — keep it "
            "to a small portion about once a week and avoid liver during pregnancy."
        )
    if grams is not None and grams > 0 and preformed * grams / 100.0 > limit:
        return (
            f"⚠️ ~{_format_grams(grams)} of this liver also gives ~{bb.format_float(preformed * grams / 100.0, 0)} mcg "
            f"vitamin A — above the {bb.format_float(limit)} mcg/day upper intake level. Pick another food or keep "
            "liver to about once a week, and avoid liver during pregnancy."
        )
    if bb.canonical_nutrient_key(component) == "folate":
        return (
            "⚠️ Liver is very high in vitamin A: avoid it during pregnancy (when folic acid is usually taken) — "
            "legumes and leafy greens are the safer folate foods."
        )
    return ""


def _co_nutrient_excesses(food: dict[str, Any] | None, grams: float | None, component: str) -> list[tuple[str, float, dict[str, Any]]]:
    """(nutrient key, amount in the UL unit, UL entry) for every OTHER nutrient
    the portion would push past its upper intake level (liver vitamin A is
    covered by _liver_vitamin_a_warning)."""
    name, category = _food_name_and_category(food)
    if not name or grams is None or grams <= 0:
        return []
    card_key = bb.canonical_nutrient_key(component)
    out: list[tuple[str, float, dict[str, Any]]] = []
    for key in _CO_NUTRIENT_LIMIT_KEYS:
        if key == card_key:
            continue
        per_100g = bb.food_nutrient_amount(name, key)
        entry = _UPPER_LIMITS[key]
        if per_100g and per_100g * grams / 100.0 > float(entry["limit"]):
            out.append((key, per_100g * grams / 100.0, entry))
    if card_key != "vitamin a" and not _is_liver(food):
        preformed = bb.food_preformed_vitamin_a(name, category)
        entry = _UPPER_LIMITS["vitamin a"]
        if preformed and preformed * grams / 100.0 > float(entry["limit"]):
            out.append(("vitamin a", preformed * grams / 100.0, entry))
    return out


def _algae_with_unknown_iodine(food: dict[str, Any] | None, component: str) -> bool:
    """Seaweed / algae (the B12 algae regex of bb._NUTRIENT_FOOD_EXCLUSIONS)
    without a USDA iodine value, on a card for another nutrient: its iodine
    is unknown and can be far above the upper limit (a few grams of kelp), so
    the co-nutrient check cannot clear it."""
    name, _category = _food_name_and_category(food)
    algae = bb._NUTRIENT_FOOD_EXCLUSIONS.get("vitamin b12")
    if not name or algae is None or not algae.search(name) or bb.canonical_nutrient_key(component) == "iodine":
        return False
    return bb.food_nutrient_amount(name, "iodine") is None


def _food_exceeds_a_limit(food: dict[str, Any] | None, grams: float | None, component: str, liver_grams: float | None = None) -> bool:
    """True when the portion breaks a co-nutrient upper limit, or the liver
    portion (`liver_grams`, default `grams`) its vitamin A limit (liver of
    unknown vitamin A content counts as over), or the food is seaweed of
    unknown iodine content (counts as over)."""
    if _algae_with_unknown_iodine(food, component):
        return True
    if _is_liver(food):
        name, category = _food_name_and_category(food)
        preformed = bb.food_preformed_vitamin_a(name, category)
        portion = liver_grams if liver_grams is not None else grams
        if preformed is None or (portion and preformed * portion / 100.0 > float(_UPPER_LIMITS["vitamin a"]["limit"])):
            return True
    return bool(_co_nutrient_excesses(food, grams, component))


def _selected_food_warning(food: dict[str, Any] | None, dose_value: Any, dose_unit: str, component: str, form: str = "") -> str:
    """Safety note for the food picked to replace the pill (portion-dependent:
    the larger of the pill-dose and athlete-target portions)."""
    if not isinstance(food, dict):
        return ""
    grams = _card_portion_grams(food, dose_value, dose_unit, component, form)
    # Liver: any portion the card names, even a "not practical" one.
    parts = [_liver_vitamin_a_warning(food, _all_portion_grams(food, dose_value, dose_unit, component, form), component)]
    if _algae_with_unknown_iodine(food, component):
        iodine = _UPPER_LIMITS["iodine"]
        parts.append(
            f"⚠️ Seaweed can hold far more iodine than the {bb.format_float(float(iodine['limit']))} "
            f"{iodine['unit']}/day upper intake level, and USDA lists no iodine value for this one — keep it to "
            "small, occasional portions, not a daily staple."
        )
    for key, amount, entry in _co_nutrient_excesses(food, grams, component):
        what = "preformed vitamin A" if key == "vitamin a" else entry["name"]
        parts.append(
            f"⚠️ ~{_format_grams(grams or 0.0)} of this food also gives ~{bb.format_float(amount, 0 if amount >= 10 else 1)} "
            f"{entry['unit']} {what} — above the {bb.format_float(float(entry['limit']))} {entry['unit']}/day upper intake "
            "level. Pick another food or a smaller portion."
        )
    parts.append(_own_limit_food_warning(food, dose_value, dose_unit, component, form))
    return " ".join(p for p in parts if p)


def _own_limit_target_grams(
    food: dict[str, Any] | None, dose_value: Any, dose_unit: str, component: str, form: str = ""
) -> float | None:
    """When matching an over-the-limit pill dose from food would itself pass the
    card nutrient's upper intake level (zinc, iodine, copper, selenium: EFSA
    limits for total intake, food included), the portion for the daily target
    instead (or 0 when there is no target); None when the limit is not passed."""
    key = bb.canonical_nutrient_key(component)
    if key not in _CO_NUTRIENT_LIMIT_KEYS or not isinstance(food, dict):
        return None
    entry = _UPPER_LIMITS[key]
    grams = _food_portion_grams(food, dose_value, dose_unit, component, form)
    edible = _edible_portion(grams, food) if grams else None
    dose = _dose_in_unit(component, dose_value, dose_unit, str(entry["unit"]), form)
    if not edible or dose is None or dose * edible / grams <= float(entry["limit"]) * (1 + 1e-9):
        return None
    target = _rda_for_component(component)
    if target is None:
        return 0.0
    return _food_portion_grams(food, target["athlete"], str(target["unit"]), str(target["display"])) or 0.0


def _own_limit_food_warning(food: dict[str, Any] | None, dose_value: Any, dose_unit: str, component: str, form: str = "") -> str:
    """"Matching this dose from food is also above the limit" (see _own_limit_target_grams)."""
    target_grams = _own_limit_target_grams(food, dose_value, dose_unit, component, form)
    if target_grams is None:
        return ""
    entry = _UPPER_LIMITS[bb.canonical_nutrient_key(component)]
    advice = f"aim for the daily target (~{_format_grams(target_grams)}) instead" if target_grams else "eat a normal portion instead"
    return (
        f"⚠️ Matching this dose from food is also above the {bb.format_float(float(entry['limit']))} "
        f"{entry['unit']}/day upper intake level for {entry['name']} — {advice}."
    )


# The food pre-selected on a card (index 0 of the dropdown is the richest, not
# necessarily the most sensible one). _default_food_index picks an everyday
# choice; the whole ranked list stays in the dropdown:
#   1. no food whose portion breaks a co-nutrient upper limit (liver vitamin A,
#      Brazil-nut selenium, ...) or seaweed of unknown iodine content;
#   1a. in pregnancy mode, no raw shellfish, roe or tuna (_pregnancy_caution_food);
#   1b. no food the card's Replace soft-block refuses (vegan / vegetarian B12:
#      a B12-fortified food first; see _replace_block_reason);
#   2. no organ meat (liver, kidney, heart, giblets) when a non-organ food can
#      supply the dose at a practical portion;
#   2b. likewise no food German shops don't sell in that form (raw hearts of
#      palm - only canned, ~1/10 of the potassium - and fresh acerola);
#   3. on vitamin D cards, no mushroom (vitamin D2; UV-treated ones only) when
#      a common fish is listed (salmon, herring, mackerel, sardines, trout) or
#      another vitamin D3 food (fish, eggs) offers an equally practical portion;
#   4. a whole food before a B12-fortified one (curated, or a plant food with
#      B12: _is_b12_fortified_food), unless the diet is vegan / vegetarian
#      (then the fortified foods are the reliable B12 source);
#   5. a practical portion ("ok", < 400 g/day) before "large" / "impractical";
#   6. on vitamin D cards, an everyday vitamin D3 food first (salmon, herring,
#      mackerel, sardines, trout, eggs);
#   7. then the ranking order (most nutrient per 100 g first).
_EVERYDAY_VITAMIN_D3_RE = re.compile(r"\b(?:salmon|herring|mackerel|sardines?|trout|eggs?)\b", re.IGNORECASE)
_EVERYDAY_VITAMIN_D3_FISH_RE = re.compile(r"\b(?:salmon|herring|mackerel|sardines?|trout)\b", re.IGNORECASE)
# Any fish (USDA "Fish, ...") or egg supplies vitamin D3.
_VITAMIN_D3_SOURCE_RE = re.compile(r"^\s*fish\b|\b(?:salmon|herring|mackerel|sardines?|trout|eggs?)\b", re.IGNORECASE)
_MUSHROOM_RE = re.compile(r"\bmushrooms?\b", re.IGNORECASE)
_NOT_SOLD_FRESH_IN_DE_RE = re.compile(
    r"^\s*(?:hearts of palm|palm hearts?),?\s*raw\b|^\s*acerola\b.*\braw\b|^\s*grape leaves,?\s*raw\b",
    re.IGNORECASE,
)
_UV_TREATED_RE = re.compile(r"\b(?:ultraviolet|uv)\b", re.IGNORECASE)
_PRACTICALITY_RANK = {"ok": 0, "large": 1, "impractical": 2}


def _is_uv_mushroom(food: dict[str, Any] | None) -> bool:
    name, _category = _food_name_and_category(food)
    return bool(_MUSHROOM_RE.search(name) and _UV_TREATED_RE.search(name))


def _default_food_index(
    foods: list[dict[str, Any]], card: dict[str, Any], profile: dict[str, Any] | None = None, pregnant: bool | None = None
) -> int:
    """Index (into `foods`, the ranked dropdown) of the food pre-selected on the
    card. `pregnant` (default: the pregnancy toggle) demotes raw shellfish, roe
    and tuna (see _pregnancy_caution_food)."""
    if not foods:
        return 0
    if pregnant is None:
        pregnant = _pregnancy_mode()
    component = str(card.get("component", "") or card.get("component_key", "") or "")
    key = str(card.get("nutrient_key", "") or "") or bb.canonical_nutrient_key(component)
    dose_value, dose_unit, form = card.get("dose_value"), str(card.get("dose_unit", "") or ""), str(card.get("form", "") or "")
    entry = _rda_for_component(component) or _rda_for_component(key)
    plant = _plant_based_diet(profile)

    def _target_grams(food: dict[str, Any]) -> float | None:
        grams = _food_portion_grams(food, dose_value, dose_unit, component, form)
        if grams is None and entry is not None:  # "Dose not found": size by the daily target
            grams = _food_portion_grams(food, entry["athlete"], str(entry["unit"]), str(entry["display"]))
        return grams

    facts = []
    for food in foods:
        name, category = _food_name_and_category(food)
        grams = _target_grams(food)
        portions = _card_portions(food, dose_value, dose_unit, component, form) or ([grams] if grams else [])
        # Co-nutrient limits on the portions someone could eat; liver vitamin A on all of them.
        edible = [e for g in portions if (e := _edible_portion(g, food))]
        facts.append({
            "unsafe": _food_exceeds_a_limit(food, max(edible, default=None), component, max(portions, default=None)),
            "organ": bb.food_is_organ_meat(name, category),
            "practical": _PRACTICALITY_RANK.get(_portion_practicality(grams, food), 3) if grams else 3,
            "d3": key == "vitamin d" and bool(_EVERYDAY_VITAMIN_D3_RE.search(name)) and not _MUSHROOM_RE.search(name),
            "d3_source": key == "vitamin d" and bool(_VITAMIN_D3_SOURCE_RE.search(name)) and not _MUSHROOM_RE.search(name),
            "mushroom": key == "vitamin d" and bool(_MUSHROOM_RE.search(name)),
            "common_fish": key == "vitamin d" and bool(_EVERYDAY_VITAMIN_D3_FISH_RE.search(name)) and not _MUSHROOM_RE.search(name),
            "fortified": (_is_b12_fortified_food(food) if key == "vitamin b12" else bool(food.get("fortified"))) and not plant,
            # Plant-based B12 / vegan iodine: a food Replace would refuse is never the default.
            "blocked": bool(_replace_block_reason(card, food, profile)),
            # Pregnancy: oysters / clams / mussels, roe and tuna are no daily default.
            "pregnancy": bool(pregnant) and _pregnancy_caution_food(food),
            "not_in_de": bool(_NOT_SOLD_FRESH_IN_DE_RE.search(name)),
        })
    # The best portion a non-organ whole food offers: an organ meat is only the
    # default when it is strictly more practical than every other food.
    best_non_organ = min((f["practical"] for f in facts if not f["organ"] and not f["unsafe"] and not f["fortified"]), default=None)
    d3_available = any(f["d3"] for f in facts)
    best_buyable = min((f["practical"] for f in facts if not f["not_in_de"] and not f["unsafe"]), default=None)
    # A mushroom gives way to a common fish (salmon, herring, mackerel,
    # sardines, trout) whatever its portion, and to any other D3 food (eggs,
    # other fish) whose portion is as practical (25 egg yolks a day do not
    # beat ~80 g of UV-treated mushrooms on a vegetarian card).
    best_d3 = min((f["practical"] for f in facts if f["d3_source"] and not f["unsafe"]), default=None)
    common_fish = any(f["common_fish"] and not f["unsafe"] for f in facts)

    def _rank(i: int) -> tuple[int, ...]:
        f = facts[i]
        return (
            int(f["unsafe"]),
            int(f["pregnancy"]),
            int(f["blocked"]),
            int(f["organ"] and best_non_organ is not None and best_non_organ <= f["practical"]),
            int(f["not_in_de"] and best_buyable is not None and best_buyable <= f["practical"]),
            int(f["mushroom"] and (common_fish or (best_d3 is not None and best_d3 <= f["practical"]))),
            int(f["fortified"]),
            f["practical"],
            int(d3_available and not f["d3"]),
            i,
        )

    return min(range(len(foods)), key=_rank)


# Plants hold no natural vitamin B12 (EFSA; DGE): a plant food (by its USDA
# category, not its name) with a real B12 amount is fortified — USDA soy milks,
# fortified cereals. Trace amounts from soil bacteria (tempeh, mushrooms,
# kiwi: < 0.1 µg/100 g) are not; algae hold inactive analogues (see
# bb._NUTRIENT_FOOD_EXCLUSIONS). 0.35 µg/100 g is just below the lowest EU
# fortification level of plant drinks (0.38 µg/100 ml = 15% NRV).
_PLANT_FOOD_CATEGORY_RE = re.compile(
    r"\b(?:legumes?|vegetables?|cereals?|grains?|pasta|fruits?|nuts?|seeds?|spices?|herbs?|beverages?|fortified)\b",
    re.IGNORECASE,
)
_B12_FORTIFIED_MIN_MCG_PER_100G = 0.35
# Long-chain omega-3 cards (EPA / DHA, as fish or algal oil): no plant food
# supplies them (blockbrain drops plant rows from these pools), so on a vegan
# diet only algal oil - a supplement - does.
_OMEGA3_LONG_CHAIN_KEYS = frozenset({"omega 3", "fish oil", "epa", "dha"})


def _is_b12_fortified_food(food: dict[str, Any] | None) -> bool:
    """True for a B12-fortified food from a vitamin B12 card's list: a curated
    fortified option, or any non-algae plant-category food whose B12
    (amount_per_100g, in the card's unit) reaches a fortification level."""
    if not isinstance(food, dict):
        return False
    if food.get("fortified"):
        return True
    name, category = _food_name_and_category(food)
    algae = bb._NUTRIENT_FOOD_EXCLUSIONS.get("vitamin b12")
    if not _PLANT_FOOD_CATEGORY_RE.search(category) or (algae is not None and algae.search(name)):
        return False
    mcg = _dose_in_unit("vitamin b12", food.get("amount_per_100g"), str(food.get("unit", "") or ""), "mcg")
    return mcg is not None and mcg >= _B12_FORTIFIED_MIN_MCG_PER_100G


def _is_us_fortified_b12_row(food: dict[str, Any] | None) -> bool:
    """A USDA plant food whose B12 is US fortification (soy milk 1.33 µg/100 g,
    fortified cereals): German B12-fortified plant drinks carry ~0.38 µg/100 ml
    and EU-organic (Bio) ones none, so sizing a portion on the US value would
    under-supply B12 3-fold. The curated EU-level foods stand in for them."""
    return isinstance(food, dict) and not food.get("fortified") and _is_b12_fortified_food(food)


def _with_fortified_options(foods: list[dict[str, Any]], card: dict[str, Any], profile: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The card's dropdown plus, on vegan / vegetarian B12 cards, the
    B12-fortified foods at EU fortification levels (yeast flakes, plant
    drinks), ranked in by amount; US-fortified USDA rows are dropped."""
    if not _plant_based_diet(profile):
        return foods
    extra = bb.fortified_food_options(str(card.get("nutrient_key", "") or card.get("component", "") or ""))
    if not extra:
        return foods
    foods = [f for f in foods if not _is_us_fortified_b12_row(f)]
    names = {str(f.get("food_description", "")) for f in foods}
    merged = list(foods) + [f for f in extra if f["food_description"] not in names]
    merged.sort(key=lambda f: float(f.get("amount_per_100g", 0) or 0), reverse=True)
    return merged[:SWIPE_CARD_DROPDOWN_MAX]


def _replace_block_reason(card: dict[str, Any], food: dict[str, Any] | None, profile: dict[str, Any] | None) -> str:
    """Why "replace with food" is soft-blocked on this card ("" when it is not):
    vegan / vegetarian B12 unless a B12-fortified food is picked (a curated
    one or any plant food with B12, see _is_b12_fortified_food), vegan
    iodine (no reliable plant source; iodised salt is not a food portion) and
    vegan EPA / DHA (only algal oil, itself a supplement, supplies them)."""
    diet = _plant_based_diet(profile)
    key = str(card.get("nutrient_key", "") or "") or bb.canonical_nutrient_key(str(card.get("component", "") or ""))
    if diet and key == "vitamin b12" and not _is_b12_fortified_food(food):
        return f"On a {diet} diet only a B12-fortified food can replace a B12 pill."
    if diet == "vegan" and key == "iodine":
        return "On a vegan diet no food replaces an iodine pill reliably."
    if diet == "vegan" and key in _OMEGA3_LONG_CHAIN_KEYS:
        return "On a vegan diet only algal oil supplies EPA+DHA — no whole food replaces this pill."
    return ""


def _final_food_warnings(items: list[dict[str, Any]]) -> list[str]:
    """Food-side safety notes for replaced pills (decision dicts), for the results screen."""
    out = []
    for d in items:
        warning = _selected_food_warning(
            d.get("selected_food"), d.get("dose_value"), str(d.get("dose_unit", "") or ""),
            str(d.get("component", "") or ""), str(d.get("form", "") or ""),
        )
        if warning:
            out.append(f"{_nutrient_title(d.get('component'))}: {warning}")
    return out


def _final_upper_limit_warnings(items: list[dict[str, Any]]) -> list[str]:
    """Upper-limit warnings for kept pills (decision dicts), for the results screen."""
    out = []
    for d in items:
        warning = _upper_limit_warning(
            str(d.get("component", "") or d.get("component_key", "") or ""),
            d.get("dose_max") if d.get("dose_max") is not None else d.get("dose_value"),
            str(d.get("dose_unit", "") or ""),
            str(d.get("form", "") or ""),
        )
        if warning:
            out.append(f"{_nutrient_title(d.get('component'))}: {warning}")
    return out


# --- Deficiency risk, bioavailability, pricing, meal plan, share, history -----
# Lightweight feature helpers layered on top of the swipe flow. Anything that
# calls the LLM is wrapped in try/except and degrades to a curated fallback so a
# flaky network never breaks the results screen.

# Nutrients most commonly under-consumed by active people (see the Athlete RDA
# guide caption): a neutral info line on the card while the pill gives under
# 100% of the EU NRV (_athlete_info_note), never a red warning.
_HIGH_RISK_NUTRIENT_KEYS = {"vitamin d", "iron", "vitamin b12", "zinc", "omega 3", "fish oil", "epa", "dha"}


def _dose_vs_athlete_ratio(component_key: str, dose_value: Any, dose_unit: str, form: str = "") -> float | None:
    """Kept pill dose as a fraction of the athlete daily target (1.0 == meets it).

    Folic acid counts as DFE (x1.7) and a fish-oil weight as ~30% EPA+DHA, the
    same food equivalents the portion math uses."""
    entry = _rda_for_component(component_key)
    if entry is None or dose_value is None:
        return None
    try:
        dose = _dose_in_unit(component_key, dose_value, dose_unit, str(entry["unit"]), form)
        target = float(entry["athlete"])
        if dose is None or target <= 0:
            return None
        return dose * bb.supplement_dose_food_factor(component_key, form) / target
    except Exception:
        return None


# EU nutrient reference values (NRV) for adults, Regulation (EU) 1169/2011
# Annex XIII Part A — the "%NRV" (German "% NRV / Nährstoffbezugswert") column
# of EU supplement labels. Keyed by bb canonical nutrient key; units match the
# dose conversion in _dose_in_unit. Folic acid is compared as printed on the
# label (µg folic acid, no DFE factor), the way the %NRV column counts it.
# Omega-3, choline, sodium and beta-carotene have no NRV.
_EU_NRV: dict[str, tuple[float, str]] = {
    "vitamin a": (800.0, "mcg"),
    "vitamin d": (5.0, "mcg"),
    "vitamin e": (12.0, "mg"),
    "vitamin k": (75.0, "mcg"),
    "vitamin k2": (75.0, "mcg"),
    "vitamin c": (80.0, "mg"),
    "thiamin": (1.1, "mg"),
    "riboflavin": (1.4, "mg"),
    "niacin": (16.0, "mg"),
    "vitamin b6": (1.4, "mg"),
    "folate": (200.0, "mcg"),
    "vitamin b12": (2.5, "mcg"),
    "biotin": (50.0, "mcg"),
    "pantothenic acid": (6.0, "mg"),
    "potassium": (2000.0, "mg"),
    "chloride": (800.0, "mg"),
    "calcium": (800.0, "mg"),
    "phosphorus": (700.0, "mg"),
    "magnesium": (375.0, "mg"),
    "iron": (14.0, "mg"),
    "zinc": (10.0, "mg"),
    "copper": (1.0, "mg"),
    "manganese": (2.0, "mg"),
    "fluoride": (3.5, "mg"),
    "selenium": (55.0, "mcg"),
    "chromium": (40.0, "mcg"),
    "molybdenum": (50.0, "mcg"),
    "iodine": (150.0, "mcg"),
}
# The low-dose note fires below this share of the NRV.
_LOW_DOSE_NRV_RATIO = 0.5


def _dose_vs_nrv_ratio(component_key: str, dose_value: Any, dose_unit: str, form: str = "") -> float | None:
    """Pill dose as a fraction of the EU NRV (1.0 == 100% NRV), or None when the
    nutrient has no NRV or the dose can't be converted."""
    nrv = _EU_NRV.get(bb.canonical_nutrient_key(component_key))
    if nrv is None or dose_value is None:
        return None
    try:
        dose = _dose_in_unit(component_key, dose_value, dose_unit, nrv[1], form)
    except Exception:
        return None
    if dose is None or nrv[0] <= 0:
        return None
    return dose / nrv[0]


def _deficiency_flag(component_key: str, dose_value: Any, dose_unit: str, form: str = "") -> str:
    """Neutral low-dose note when the pill gives under half the EU NRV, else "".

    Measured against the EU reference intake printed on German labels, not the
    athlete target (that row stays on the card): a 100% NRV pill is not "low"."""
    ratio = _dose_vs_nrv_ratio(component_key, dose_value, dose_unit, form)
    if ratio is not None and ratio < _LOW_DOSE_NRV_RATIO:
        # Rounded like the label's %NRV column (14.9% -> 15%), but never up to
        # the 50% threshold itself (49.6% -> "about 49%").
        pct = max(1, min(int(_LOW_DOSE_NRV_RATIO * 100) - 1, int(round(ratio * 100))))
        return f"ℹ️ Low dose: about {pct}% of the EU daily reference intake (NRV)."
    return ""


def _athlete_info_note(component_key: str, dose_value: Any, dose_unit: str, form: str = "") -> str:
    """Neutral info line for nutrients athletes are often low in (vitamin D, iron,
    B12, zinc, omega-3); never shown once the pill gives >= 100% NRV, nor next
    to an over-the-upper-limit warning."""
    if bb.canonical_nutrient_key(component_key) not in _HIGH_RISK_NUTRIENT_KEYS:
        return ""
    ratio = _dose_vs_nrv_ratio(component_key, dose_value, dose_unit, form)
    if ratio is not None and ratio >= 1.0:
        return ""
    if _upper_limit_warning(component_key, dose_value, dose_unit, form):
        return ""
    return "Often low in active people — worth keeping an eye on your intake."


def _plant_based_diet(profile: dict[str, Any] | None) -> str:
    """"vegan" / "vegetarian" for those dietary profiles, else ""."""
    if not isinstance(profile, dict):
        return ""
    blob = bb.normalize_lookup_key(f"{profile.get('id', '')} {profile.get('label', '')}")
    if "vegan" in blob:
        return "vegan"
    if "vegetarian" in blob:
        return "vegetarian"
    return ""


def _diet_specific_warning(component_key: str, profile: dict[str, Any] | None) -> str:
    """Diet-specific advice that overrides "replace with food" (vegan /
    vegetarian B12, vegan iodine, vegan / vegetarian EPA+DHA); Replace is
    soft-blocked for these cards except vegetarian EPA+DHA, where eggs are a
    (small) real source (see _replace_block_reason)."""
    diet = _plant_based_diet(profile)
    key = bb.canonical_nutrient_key(component_key)
    if diet and key == "vitamin b12":
        return (
            f"⚠️ On a {diet} diet whole foods aren't a reliable vitamin B12 source — "
            "keeping the supplement is recommended. Only a B12-fortified food (yeast flakes, fortified "
            "plant drinks) can stand in for it; seaweed and spirulina don't count."
        )
    if diet == "vegan" and key == "iodine":
        return (
            "⚠️ On a vegan diet no food supplies iodine reliably — keeping the supplement is recommended. "
            "Cook with iodised salt (Jodsalz); seaweed iodine is erratic and can be far too high."
        )
    if diet == "vegan" and key in _OMEGA3_LONG_CHAIN_KEYS:
        return (
            "⚠️ On a vegan diet no whole food supplies EPA+DHA — algal oil is the only plant source, so "
            "keeping it is recommended. Flax, chia, hemp and walnuts give ALA, of which the body converts "
            "only a few percent."
        )
    if diet == "vegetarian" and key in _OMEGA3_LONG_CHAIN_KEYS:
        return (
            "⚠️ On a vegetarian diet only eggs give a little DHA, far from a capsule's dose — algal oil is "
            "the practical EPA+DHA source, so keeping it is recommended."
        )
    return ""


# October-March the sun in Germany is too low for the skin to make vitamin D
# (DGE / RKI); food alone rarely reaches the 20 µg/day reference intake.
_VITAMIN_D_WINTER_MONTHS = frozenset({10, 11, 12, 1, 2, 3})


def _winter_vitamin_d_note(component_key: str, today: Any = None) -> str:
    """Seasonal advice on vitamin D cards from October to March ("" otherwise)."""
    if bb.canonical_nutrient_key(component_key) != "vitamin d":
        return ""
    month = (today or datetime.date.today()).month
    if month not in _VITAMIN_D_WINTER_MONTHS:
        return ""
    return (
        "ℹ️ October–March the sun in Germany is too weak for your skin to make vitamin D, and food alone "
        "rarely reaches the 20 µg/day reference. Many people in Germany take vitamin D in winter — ask your doctor "
        "whether that suits you."
    )


def _card_warning_text(
    component_key: str,
    dose_value: Any,
    dose_unit: str,
    form: str = "",
    profile: dict[str, Any] | None = None,
    dose_max: Any = None,
    today: Any = None,
) -> str:
    """The card's warn text (the red box): real warnings only — an
    over-upper-limit warning and diet advice that overrides "replace with
    food". Neutral ℹ️ notes (low dose, fish-oil weight, winter vitamin D) go
    to the blue info line instead (_card_info_notes). A label range ("100-200
    mg") is checked against the upper limit with its upper bound (`dose_max`).
    `today` is unused (kept for callers that pass it)."""
    parts = []
    upper = _upper_limit_warning(component_key, dose_max if dose_max is not None else dose_value, dose_unit, form)
    if upper:
        parts.append(upper)
    diet = _diet_specific_warning(component_key, profile)
    if diet:
        parts.append(diet)
    return " ".join(parts)


def _unit_corrected_note(card: dict[str, Any]) -> str:
    """Info line for a dose whose unit was corrected from the printed %NRV."""
    if not card.get("unit_corrected"):
        return ""
    return (
        "ℹ️ Read as µg, not mg: only µg fits the %NRV printed on the label (photos often turn µ into m) — "
        "check your pack."
    )


def _card_info_notes(
    component_key: str,
    dose_value: Any,
    dose_unit: str,
    form: str = "",
    profile: dict[str, Any] | None = None,
    dose_max: Any = None,
    today: Any = None,
) -> list[str]:
    """Neutral ℹ️ notes for the card's blue info line: the low-dose note (not
    next to an upper-limit warning or diet advice), the fish-oil weight
    assumption and the October–March vitamin D note."""
    notes = []
    upper = _upper_limit_warning(component_key, dose_max if dose_max is not None else dose_value, dose_unit, form)
    if not upper and not _diet_specific_warning(component_key, profile):
        notes.append(_deficiency_flag(component_key, dose_value, dose_unit, form))
    if bb.canonical_nutrient_key(component_key) == "fish oil" and dose_value is not None:
        notes.append("ℹ️ The label gives the fish-oil weight; portions assume ~30% of it is EPA+DHA.")
    notes.append(_winter_vitamin_d_note(component_key, today))
    return [n for n in notes if n]


# --- Pregnancy & medication guardrails ----------------------------------------
# "🤰 Pregnant or breastfeeding" (a toggle next to the dietary filter, kept in
# swipe_pregnant like the diet filter) hides organ meats from the food options,
# marks the nutrients usually kept as a supplement in pregnancy and adds
# food-safety rules to the meal plan. Medication notes show on every relevant
# card. Short, general lines only — the card points to the doctor / midwife.

# Organ words, matched whole on the USDA name. Plant foods that share a word
# (kidney beans, hearts of palm, artichoke hearts) are excluded by category and
# by the false-friend words below.
_ORGAN_MEAT_RE = re.compile(
    r"\b(?:liver|livers|liverwurst|leberwurst|braunschweiger|kidneys?|hearts?|gizzards?|tongues?|"
    r"sweetbreads?|giblets|brains?|tripe|spleen|lungs?|pancreas|thymus|offal|chitterlings|foie gras|pate|pâté)\b"
)
_ORGAN_FALSE_FRIEND_RE = re.compile(r"\b(?:beans?|palm|artichokes?|celery|lettuce|romaine|cabbage)\b")
# High-mercury fish, never offered in pregnancy (BfR / FDA): swordfish, shark,
# king mackerel, marlin, tilefish, orange roughy, bigeye and bluefin tuna.
_HIGH_MERCURY_FISH_RE = re.compile(
    r"\b(?:swordfish|shark|marlin|tilefish|roughy|bigeye|bluefin)\b|\bmackerel,? king\b|\bking mackerel\b",
    re.IGNORECASE,
)
# Offered in pregnancy, but never the daily default and only well cooked:
# oysters, clams and mussels (USDA rows are "raw"), fish roe, and tuna (at most
# twice a week).
_PREGNANCY_COOKED_ONLY_RE = re.compile(r"\b(?:oysters?|clams?|mussels?|scallops?|roe|caviar)\b", re.IGNORECASE)
_TUNA_RE = re.compile(r"\btuna\b", re.IGNORECASE)
_PLANT_CATEGORY_WORDS = ("legume", "vegetable", "fruit", "nut and seed", "cereal", "spice", "beverage")

# Nutrients usually kept as a supplement in pregnancy (folic acid, iodine,
# vitamin D, iron; B12 and DHA too on a vegan / vegetarian diet).
_PREGNANCY_SUPPLEMENT_KEYS = {"folate", "iodine", "vitamin d", "iron"}
_PREGNANCY_NOTE = (
    "🤰 Folic acid and iodine are often recommended in pregnancy — ask your doctor or midwife before changing this supplement."
)
_PREGNANCY_NOTE_IF_LOW = (
    "🤰 In pregnancy this is often only supplemented when a blood test shows a shortfall — ask your doctor or midwife "
    "before changing this supplement."
)
_PREGNANCY_NOTE_PLANT_B12 = (
    "🤰 On a plant-based diet vitamin B12 is usually supplemented, in pregnancy too — ask your doctor or midwife "
    "before changing this supplement."
)
# German guidance (DGE / Netzwerk Gesund ins Leben): ~200 mg DHA a day in
# pregnancy and while breastfeeding; without oily fish, from a supplement.
_PREGNANCY_DHA_NOTE = (
    "🤰 In pregnancy and while breastfeeding about 200 mg DHA a day is advised — without oily fish, "
    "keep the (algal-oil) supplement. Check with your doctor or midwife."
)
_PREGNANCY_MEAL_RULES = (
    " The user is pregnant or breastfeeding, so follow pregnancy food-safety rules: no liver or "
    "liver products (pâté, liver sausage) and no other organ meats, no raw or undercooked meat, fish "
    "or eggs (no sushi, tartare, runny eggs), no unpasteurised (raw-milk) soft cheese, and no "
    "high-mercury fish (swordfish, shark, king mackerel, bigeye tuna; tuna at most twice a week)."
)
_MEDICATION_NOTES: dict[str, str] = {
    "vitamin k": "💊 On blood thinners like warfarin or phenprocoumon (Marcumar)? Keep your vitamin K intake steady and ask your doctor before changing it.",
    "vitamin k2": "💊 On blood thinners like warfarin or phenprocoumon (Marcumar)? Keep your vitamin K intake steady and ask your doctor before changing it.",
    "potassium": "💊 Kidney disease or certain blood-pressure drugs (e.g. ACE inhibitors, potassium-sparing diuretics)? Ask your doctor before adding potassium.",
    "iodine": "💊 Thyroid condition? Ask your doctor before changing your iodine intake.",
}


def _pregnancy_mode() -> bool:
    """True while the "Pregnant or breastfeeding" toggle is on."""
    try:
        return bool(st.session_state.get("swipe_pregnant", False))
    except Exception:
        return False


def _is_organ_meat(food: dict[str, Any] | None) -> bool:
    """True for liver, kidney, heart and other organ meats (incl. liver products
    and fish-liver oil); False for plant foods such as kidney beans."""
    if not isinstance(food, dict):
        return False
    desc = str(food.get("food_description", "") or "").lower()
    if not _ORGAN_MEAT_RE.search(desc):
        return False
    category = str(food.get("food_category", "") or "").lower()
    if any(word in category for word in _PLANT_CATEGORY_WORDS):
        return False
    return not _ORGAN_FALSE_FRIEND_RE.search(desc)


def _is_high_mercury_fish(food: dict[str, Any] | None) -> bool:
    return isinstance(food, dict) and bool(_HIGH_MERCURY_FISH_RE.search(str(food.get("food_description", "") or "")))


def _pregnancy_caution_food(food: dict[str, Any] | None) -> bool:
    """Shellfish, roe or tuna: fine in pregnancy only cooked / now and then."""
    name = str((food or {}).get("food_description", "") or "") if isinstance(food, dict) else ""
    return bool(_PREGNANCY_COOKED_ONLY_RE.search(name) or _TUNA_RE.search(name))


def _pregnancy_food_note(food: dict[str, Any] | None) -> str:
    """The card's pregnancy note for the selected food ("" when none applies)."""
    name = str((food or {}).get("food_description", "") or "") if isinstance(food, dict) else ""
    if _TUNA_RE.search(name):
        return "🤰 In pregnancy eat tuna at most twice a week, not daily."
    if _PREGNANCY_COOKED_ONLY_RE.search(name):
        return "🤰 In pregnancy eat shellfish and fish roe only well cooked — never raw."
    return ""


def _card_food_options(
    foods: list[dict[str, Any]], profile: dict[str, Any] | None, pregnant: bool | None = None
) -> list[dict[str, Any]]:
    """The card's dropdown: the pool filtered by the dietary profile, without
    organ meats and high-mercury fish in pregnancy mode, capped to
    SWIPE_CARD_DROPDOWN_MAX."""
    options = bb.apply_food_filters(foods, profile, use_llm_adjudication=False)
    if _pregnancy_mode() if pregnant is None else pregnant:
        options = [food for food in options if not _is_organ_meat(food) and not _is_high_mercury_fish(food)]
    return options[:SWIPE_CARD_DROPDOWN_MAX]


def _pregnancy_note(component_key: str, profile: dict[str, Any] | None = None) -> str:
    key = bb.canonical_nutrient_key(component_key)
    if key in {"folate", "iodine"}:
        return _PREGNANCY_NOTE
    if key in {"vitamin d", "iron"}:
        return _PREGNANCY_NOTE_IF_LOW
    if key == "vitamin b12" and _plant_based_diet(profile):
        return _PREGNANCY_NOTE_PLANT_B12
    if key in _OMEGA3_LONG_CHAIN_KEYS and _plant_based_diet(profile):
        return _PREGNANCY_DHA_NOTE
    return ""


def _medication_note(component_key: str) -> str:
    return _MEDICATION_NOTES.get(bb.canonical_nutrient_key(component_key), "")


def _not_advised_in_pregnancy(food: dict[str, Any] | None) -> bool:
    """Organ meats and high-mercury fish (hidden from the cards in pregnancy)."""
    return _is_organ_meat(food) or _is_high_mercury_fish(food)


def _pregnancy_food_warnings(items: list[dict[str, Any]], pregnant: bool | None = None) -> list[str]:
    """Results-screen notes in pregnancy mode: organ meats and high-mercury
    fish picked before it was on, and the cooked-only / twice-a-week notes."""
    if not (_pregnancy_mode() if pregnant is None else pregnant):
        return []
    out = []
    for d in items:
        food = d.get("selected_food")
        title = _nutrient_title(d.get("component"))
        if _not_advised_in_pregnancy(food):
            out.append(f"🤰 {title}: {_food_name(food)} isn't advised in pregnancy — tap it in your plan to choose another food.")
        elif _pregnancy_food_note(food):
            out.append(f"{title}: {_pregnancy_food_note(food)}")
    return out


def _card_extra_info(
    component_key: str,
    dose_value: Any,
    dose_unit: str,
    form: str = "",
    profile: dict[str, Any] | None = None,
    pregnant: bool | None = None,
    dose_max: Any = None,
    today: Any = None,
) -> str:
    """Extra lines appended to the card's info (after the curated
    _bioavailability_note): the neutral ℹ️ notes (_card_info_notes), the
    "often low in athletes" remark, the pregnancy note (pregnancy mode only)
    and the medication note."""
    if pregnant is None:
        pregnant = _pregnancy_mode()
    lines = [
        *_card_info_notes(component_key, dose_value, dose_unit, form, profile, dose_max, today),
        _athlete_info_note(component_key, dose_value, dose_unit, form),
        _pregnancy_note(component_key, profile) if pregnant else "",
        _medication_note(component_key),
    ]
    return " ".join(line for line in lines if line)


# Why the whole food generally beats the isolated pill — one concise, curated
# line per nutrient (keyed by bb canonical nutrient key), worded so it never
# contradicts the foods the card ranks (e.g. liver tops vitamin A, seaweed and
# soy top iron, a 5 g Brazil nut holds ~95 µg selenium). General guidance only.
_BIOAVAILABILITY_NOTES: dict[str, str] = {
    "vitamin a": "Liver is extremely high in preformed vitamin A — keep it to a small portion about once a week (avoid it in pregnancy). Orange and dark-green vegetables give beta-carotene, which the body converts only as needed.",
    "beta carotene": "Orange and dark-green vegetables deliver beta-carotene in its natural carotenoid mix; eat them with a little fat to absorb it.",
    "vitamin c": "Whole foods pair vitamin C with bioflavonoids that support its absorption and antioxidant action.",
    "vitamin d": "Few foods are rich in vitamin D: UV-exposed mushrooms give D2, which raises blood levels less than D3; oily fish and egg yolk give D3. Sunlight is the main source.",
    "vitamin e": "Food vitamin E is the full tocopherol/tocotrienol family, not just the single alpha form in most pills.",
    "vitamin k": "Leafy greens supply vitamin K1 — eat them with a little fat to absorb it.",
    "vitamin k2": "USDA has no vitamin K2 data, so these are literature values (Schurgers & Vermeer 2000). Natto is the only rich MK-7 source; cheese K2 is mostly MK-8/MK-9.",
    "folate": "Natural food folate is better balanced than high-dose folic acid, which can mask a B12 deficiency.",
    "vitamin b12": "Only animal foods reliably supply active vitamin B12, bound to protein; liver and shellfish are the richest sources.",
    "iron": "Heme iron from meat, fish and liver is absorbed far better (~15–35%) than non-heme iron from plants and seaweed (~2–20%) — pair plant sources with vitamin C.",
    "calcium": "Food calcium comes in smaller amounts spread over meals, which the body absorbs more efficiently than one large pill dose.",
    "magnesium": "Food magnesium comes bound to fibre and other minerals, so it's better tolerated than high-dose salts.",
    "zinc": "Food zinc is balanced with copper; isolated zinc pills can deplete copper over time.",
    "iodine": "Sea fish, dairy and eggs supply iodine; in Germany iodised salt is the main everyday source.",
    "potassium": "Food potassium is well-absorbed and unrestricted, unlike dose-capped supplements.",
    "omega 3": "Oily fish delivers EPA+DHA with protein, selenium and vitamin D, and is fresher than long-stored capsules.",
    "fish oil": "Oily fish delivers EPA+DHA with protein, selenium and vitamin D, and is fresher than long-stored capsules.",
    "epa": "Oily fish delivers EPA+DHA with protein, selenium and vitamin D, and is fresher than long-stored capsules.",
    "dha": "Oily fish delivers EPA+DHA with protein, selenium and vitamin D, and is fresher than long-stored capsules.",
    "ala": "Flax, chia, hemp and walnuts give ALA; the body converts only a few percent of it into EPA/DHA.",
    "choline": "Eggs and liver supply choline with phospholipids and other B-vitamins that work together.",
    "chromium": "Chromium values are approximate (NIH ODS) — USDA has no per-food chromium data.",
}
# The same notes for plant-based diets, where the default note would point at
# foods the diet excludes ("Only animal foods...", "Heme iron from meat...").
# Keyed by (diet, nutrient key); a "plant" entry covers vegan AND vegetarian.
_PLANT_BIOAVAILABILITY_NOTES: dict[tuple[str, str], str] = {
    ("plant", "vitamin b12"): "Plants, seaweed and spirulina hold no reliable active B12 — on a plant-based diet only B12-fortified foods (yeast flakes, fortified plant drinks) or a supplement cover it.",
    ("vegetarian", "vitamin b12"): "Eggs and dairy give some B12, but rarely enough on their own — B12-fortified foods or a supplement are the reliable sources on a vegetarian diet.",
    ("plant", "iron"): "Plant (non-heme) iron is absorbed less (~2–20%) than iron from meat — pair legumes, whole grains and seeds with vitamin C (peppers, citrus) and keep tea and coffee away from meals.",
    ("plant", "vitamin a"): "Orange and dark-green vegetables give beta-carotene, which the body turns into vitamin A as needed — eat them with a little fat.",
    ("vegan", "vitamin d"): "On a vegan diet UV-treated mushrooms are the only notable food source, and their D2 raises blood levels less than D3 — sunlight (and in winter a supplement) is the main source.",
    ("vegan", "choline"): "Soy, legumes, quinoa and cruciferous vegetables supply choline; without eggs it takes larger portions.",
    ("vegetarian", "choline"): "Eggs are the richest vegetarian choline source; soy and legumes add more.",
    ("plant", "zinc"): "Plant zinc is partly bound by phytate — soaked, sprouted or fermented legumes, whole grains and seeds release more of it.",
    ("vegan", "calcium"): "Calcium-set tofu, fortified plant drinks, kale, broccoli and almonds supply well-absorbed calcium; spinach calcium is poorly absorbed.",
    ("vegan", "iodine"): "Plant foods hold little iodine — iodised salt (Jodsalz) is the everyday source in Germany; seaweed iodine is erratic and can be excessive.",
    ("plant", "omega 3"): "Algal oil is the only plant source of EPA+DHA; flax, chia, hemp and walnuts give ALA, of which the body converts only a few percent.",
    ("plant", "fish oil"): "Algal oil is the only plant source of EPA+DHA; flax, chia, hemp and walnuts give ALA, of which the body converts only a few percent.",
    ("plant", "epa"): "Algal oil is the only plant source of EPA+DHA; flax, chia, hemp and walnuts give ALA, of which the body converts only a few percent.",
    ("plant", "dha"): "Algal oil is the only plant source of EPA+DHA; flax, chia, hemp and walnuts give ALA, of which the body converts only a few percent.",
}
_FOLIC_ACID_NOTE = (
    "Your pill is folic acid, which is absorbed ~1.7× better than food folate — so the food "
    "portion is sized to 1.7× the label amount (µg DFE)."
)


_BRAZIL_NUT = "Nuts, brazilnuts, raw"
_BRAZIL_NUT_GRAMS = 5.0


def _selenium_note(dose_value: Any = None, dose_unit: str = "") -> str:
    """Brazil-nut advice that agrees with the nut count on the card and the UL:
    a 5 g nut holds ~96 µg (USDA reference 1917 µg/100 g), so 3 nuts (~290 µg)
    already pass the 255 µg/day EFSA limit."""
    per_100g = bb.food_nutrient_amount(_BRAZIL_NUT, "selenium") or 1917.0
    per_nut = per_100g * _BRAZIL_NUT_GRAMS / 100.0
    limit = float(_UPPER_LIMITS["selenium"]["limit"])
    too_many = int(limit // per_nut) + 1
    dose = _dose_in_unit("selenium", dose_value, dose_unit, "mcg") if dose_value is not None else None
    nuts = round(dose / per_nut * 2) / 2 if dose else None
    head = (
        f"One Brazil nut (~{bb.format_float(_BRAZIL_NUT_GRAMS, 0)} g) holds roughly 50–100 µg selenium "
        f"(it varies with soil; USDA reference ~{bb.format_float(per_nut, 0)} µg)"
    )
    if nuts is None or nuts <= 1:
        advice = "one nut a day is plenty"
    elif nuts < too_many:
        advice = f"the ~{bb.format_float(nuts, 1)} nuts that match this dose are fine, but don't eat more"
    else:
        advice = f"matching this dose would take ~{bb.format_float(nuts, 1)} nuts, more than is safe"
    return (
        f"{head} — {advice}; {too_many} or more nuts a day can pass the "
        f"{bb.format_float(limit)} µg/day upper intake level."
    )


def _bioavailability_note(
    component_key: str,
    form: str = "",
    dose_value: Any = None,
    dose_unit: str = "",
    profile: dict[str, Any] | None = None,
) -> str:
    """One curated line on why the whole food helps; `dose_value`/`dose_unit`
    (the pill dose) make dose-dependent notes (Brazil-nut selenium) match the
    portion on the card, and a vegan / vegetarian `profile` gets the note for
    that diet."""
    key = bb.canonical_nutrient_key(component_key)
    if key == "folate" and bb._is_folic_acid_dose(component_key, form):
        return _FOLIC_ACID_NOTE
    if key == "selenium":
        return _selenium_note(dose_value, dose_unit)
    diet = _plant_based_diet(profile)
    if diet:
        note = _PLANT_BIOAVAILABILITY_NOTES.get((diet, key)) or _PLANT_BIOAVAILABILITY_NOTES.get(("plant", key))
        if note:
            return note
    note = _BIOAVAILABILITY_NOTES.get(key)
    if note:
        return note
    return (
        "Food also brings fibre, protein and other nutrients. How well the body absorbs a nutrient can differ "
        "between food and pills."
    )


# Approximate German shelf prices (EUR/kg, typical ALDI/Lidl/REWE/EDEKA
# own-brand prices in 2025) live in blockbrain/data/german_food_prices.csv, one
# row per food with its basis (dry weight, fillet, meat weight ...). Used only
# for a rough basket estimate that the UI labels as approximate.
_GERMAN_FOOD_PRICES_PATH = ROOT_DIR / "blockbrain" / "data" / "german_food_prices.csv"
# A swap needing more than this much of one food per day (or more than the
# food's own realistic daily maximum, see _portion_practicality) is not a
# realistic replacement: it is listed as "not practical from food" instead of
# priced.
_BASKET_MAX_PRACTICAL_G_PER_DAY = _PORTION_IMPRACTICAL_G


_GERMAN_FOOD_PRICES_CACHE: list[tuple[tuple[Any, ...], float, str]] = []


def _german_food_prices() -> tuple[tuple[tuple[Any, ...], float, str], ...]:
    """((word regexes per alternative), EUR/kg, matched phrase) rows in file order."""
    import csv

    if _GERMAN_FOOD_PRICES_CACHE:
        return tuple(_GERMAN_FOOD_PRICES_CACHE)
    rows: list[tuple[tuple[Any, ...], float, str]] = []
    try:
        with _GERMAN_FOOD_PRICES_PATH.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                try:
                    price = float(row.get("eur_per_kg", "") or 0)
                except Exception:
                    continue
                if price <= 0:
                    continue
                for alternative in str(row.get("match", "") or "").split("|"):
                    words = bb.normalize_lookup_key(alternative).split()
                    regexes = tuple(bb._keywords_regex((w,)) for w in words)
                    if words and all(rx is not None for rx in regexes):
                        rows.append((regexes, price, " ".join(words)))
    except Exception:
        return ()
    _GERMAN_FOOD_PRICES_CACHE[:] = rows
    return tuple(rows)


def _german_price_per_kg(food_name: str) -> tuple[float, str] | None:
    """(EUR/kg, matched phrase) for a USDA food name, or None if unpriced.

    Words match whole (simple plurals), so "Goat" is not "oat" and butternut
    squash is not "nut". The most specific row wins: more matched words first,
    then where the match is - the food's head (first two USDA segments, so
    "Fish, roughy, orange" is fish, not oranges), a later segment, and last a
    word found only inside a parenthetical synonym ("Salsify, (vegetable
    oyster)", "Custard-apple, (bullock's-heart)") - then file order, which
    lists specific rows before generic ones.
    """
    segments = [bb.normalize_lookup_key(s) for s in bb._split_usda_segments(str(food_name or ""))]
    segments = [s for s in segments if s]
    if not segments:
        return None
    outside = [re.sub(r"\([^)]*\)?", " ", seg) for seg in segments]
    key = " ".join(segments)

    def _tier(rx: Any) -> int:
        first = next((i for i, text in enumerate(outside) if rx.search(text)), None)
        if first is None:
            return 0  # only inside a parenthetical synonym
        return 2 if first <= 1 else 1

    best: tuple[tuple[int, int, int], float, str] | None = None
    for order, (regexes, price, phrase) in enumerate(_german_food_prices()):
        if not all(rx.search(key) for rx in regexes):
            continue
        score = (len(regexes), max(_tier(rx) for rx in regexes), -order)
        if best is None or score > best[0]:
            best = (score, price, phrase)
    return (best[1], best[2]) if best else None


def _estimate_food_price_eur(food_name: str, grams: float | None) -> float | None:
    if grams is None or grams <= 0:
        return None
    match = _german_price_per_kg(food_name)
    if match is None:
        return None
    return (grams / 1000.0) * match[0]


def _grams_to_match_dose(decision: dict[str, Any]) -> float | None:
    food = decision.get("selected_food") or {}
    try:
        amount_per_100g = float(food.get("amount_per_100g", 0.0) or 0.0)
    except Exception:
        amount_per_100g = 0.0
    try:
        return bb.grams_needed_to_match_dose(
            decision.get("dose_value"),
            str(decision.get("dose_unit", "") or ""),
            amount_per_100g,
            str(food.get("unit", "") or ""),
            str(decision.get("component", "") or ""),
            str(decision.get("form", "") or ""),
        )
    except Exception:
        return None


def _swap_grams(decision: dict[str, Any]) -> float | None:
    """The daily amount of a swap's food the results count: the match-dose
    portion, or the daily-target portion when matching an over-the-limit pill
    from food would pass the upper intake level too (_own_limit_target_grams)."""
    grams = _grams_to_match_dose(decision)
    target = _own_limit_target_grams(
        decision.get("selected_food"), decision.get("dose_value"), str(decision.get("dose_unit", "") or ""),
        str(decision.get("component", "") or ""), str(decision.get("form", "") or ""),
    )
    return target if target else grams


def _swap_foods(replace_items: list[dict[str, Any]]) -> list[tuple[str, float | None, dict[str, Any]]]:
    """(display name, grams/day, food) per distinct food of the swaps: a food
    chosen for several nutrients (also under two USDA names, "Nuts, almonds"
    and "Almonds") counts once, at its largest amount."""
    by_name: dict[str, tuple[str, float | None, dict[str, Any]]] = {}
    for d in replace_items:
        food = d.get("selected_food") or {}
        name = _food_name(food)
        if not name:
            continue
        grams = _swap_grams(d)
        key = bb.normalize_lookup_key(name)
        if key not in by_name or (grams or 0.0) > (by_name[key][1] or 0.0):
            by_name[key] = (name, grams, food)
    return list(by_name.values())


def _basket_cost_breakdown(replace_items: list[dict[str, Any]]) -> dict[str, Any]:
    """Daily cost of the whole-food swaps.

    Returns {"total": EUR/day, "rows": [(name, EUR/day)], "unknown": [name],
    "impractical": [(name, grams/day)]}. Each food is priced once
    (_swap_foods). Swaps the card calls not practical from food (more than
    1 kg a day, or past the food's realistic daily maximum, see
    _portion_practicality) are not priced but listed separately.
    """
    rows: list[tuple[str, float]] = []
    unknown: list[str] = []
    impractical: list[tuple[str, float]] = []
    total = 0.0
    for name, grams, food in _swap_foods(replace_items):
        if grams is not None and _portion_practicality(grams, food) == "impractical":
            impractical.append((name, grams))
            continue
        cost = _estimate_food_price_eur(str(food.get("food_description", "") or ""), grams)
        if cost is not None and cost > 0:
            rows.append((name, cost))
            total += cost
        else:
            unknown.append(name)
    return {"total": total, "rows": rows, "unknown": unknown, "impractical": impractical}


def _basket_cost_summary(replace_items: list[dict[str, Any]]) -> tuple[float, list[tuple[str, float]], list[str]]:
    breakdown = _basket_cost_breakdown(replace_items)
    return breakdown["total"], breakdown["rows"], breakdown["unknown"]


# Daily totals of the swaps on the results screen. Above either limit the swaps
# are a lot of food on top of a normal diet, so the screen suggests keeping
# some supplements.
_SWAP_TOTAL_MAX_G = 1000.0
_SWAP_TOTAL_MAX_KCAL = 1200.0


def _swap_totals(replace_items: list[dict[str, Any]]) -> dict[str, Any]:
    """How much food the whole-food swaps add per day.

    Uses the match-dose grams of each replaced item (_swap_grams); a food chosen
    for several nutrients counts once, at its largest amount (_swap_foods).
    Energy is USDA kcal per 100 g (bb.food_energy_kcal_per_100g). Items whose
    amount is impractical (> 1 kg a day or past the food's realistic daily
    maximum, see _portion_practicality) are listed separately and not summed.

    Returns {"grams", "kcal", "foods": [(name, grams, kcal or None)],
    "no_energy": [name], "impractical": [(name, grams)], "too_much": bool}.
    """
    by_food: list[tuple[str, float, str]] = []
    impractical: list[tuple[str, float]] = []
    for name, grams, food in _swap_foods(replace_items):
        usda_name = str(food.get("food_description", "") or "").strip()
        if not usda_name or grams is None or grams <= 0:
            continue
        # The same rule as the card: past a food's own realistic daily amount
        # (~30 g of yeast flakes) is not practical, whatever it weighs.
        if _portion_practicality(grams, food) == "impractical":
            impractical.append((name, grams))
            continue
        by_food.append((name, grams, usda_name))

    foods: list[tuple[str, float, float | None]] = []
    no_energy: list[str] = []
    total_g = total_kcal = 0.0
    for name, grams, usda_name in by_food:
        try:
            kcal_100g = bb.food_energy_kcal_per_100g(usda_name)
        except Exception:
            kcal_100g = None
        kcal = grams * float(kcal_100g) / 100.0 if kcal_100g is not None else None
        foods.append((name, grams, kcal))
        total_g += grams
        if kcal is None:
            no_energy.append(name)
        else:
            total_kcal += kcal
    return {
        "grams": total_g,
        "kcal": total_kcal,
        "foods": foods,
        "no_energy": no_energy,
        "impractical": impractical,
        "too_much": total_g > _SWAP_TOTAL_MAX_G or total_kcal > _SWAP_TOTAL_MAX_KCAL,
    }


def _round_total(value: float) -> str:
    """"1,250" / "85" — whole numbers, to the nearest 10 from 100 up."""
    rounded = round(value, -1) if value >= 100 else round(value)
    return f"{int(rounded):,}"


def _swap_totals_lines(replace_items: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(kind, text) lines for the results screen: "total", "warning", "caption"."""
    totals = _swap_totals(replace_items)
    lines: list[tuple[str, str]] = []
    if totals["foods"]:
        grams_txt = _round_total(totals["grams"])
        if len(totals["no_energy"]) == len(totals["foods"]):
            lines.append(("total", f"🍽️ Your swaps add about {grams_txt} g of food a day."))
        else:
            lines.append(
                ("total", f"🍽️ Your swaps add about {grams_txt} g of food and ~{_round_total(totals['kcal'])} kcal a day.")
            )
            if totals["no_energy"]:
                lines.append(("caption", "kcal without " + ", ".join(totals["no_energy"]) + " (no USDA energy value)."))
        if totals["too_much"]:
            lines.append(("warning", "⚠️ This is a lot of food — consider keeping some supplements."))
    if totals["impractical"]:
        lines.append(
            (
                "caption",
                "Not counted, not practical from food: "
                + ", ".join(f"{name} (~{_format_grams(grams)}/day)" for name, grams in totals["impractical"])
                + ".",
            )
        )
    return lines


def _render_swap_totals(replace_items: list[dict[str, Any]]) -> None:
    for kind, text in _swap_totals_lines(replace_items):
        if kind == "total":
            st.markdown(f"**{text}**")
        elif kind == "warning":
            st.warning(text)
        else:
            st.caption(text)


# Amounts the meal plan asks for instead of the match-dose portion: a normal
# serving when the full dose would take a large / impractical amount of food
# (the plan must not ask for 1.5 kg of spinach), and the once-a-week limit for
# organ meats (vitamin A, see _liver_vitamin_a_warning).
_MEAL_PLAN_NORMAL_PORTION = "a normal portion (about 150 g); the full dose isn't practical from food"


def _meal_plan_normal_portion(food: dict[str, Any] | None) -> str:
    """"A normal portion", capped at the food's own realistic daily amount
    (~30 g of yeast flakes, 15 g of chia) when it has one."""
    own_max = _food_max_daily_g(food)
    if 0 < own_max < 150:
        return f"a normal portion (about {_format_grams(own_max)}); the full dose isn't practical from food"
    return _MEAL_PLAN_NORMAL_PORTION
_MEAL_PLAN_ORGAN_PORTION = "at most one small portion (~50 g) per week"


def _meal_plan_amount(decision: dict[str, Any]) -> str:
    """The daily amount of a replaced item's food as written into the meal-plan prompt."""
    food = decision.get("selected_food") or {}
    if _is_organ_meat(food) and not re.search(r"\boil\b", str(food.get("food_description", "") or "").lower()):
        return _MEAL_PLAN_ORGAN_PORTION
    # An over-the-limit pill matched from food is over the limit too: the daily target.
    target_grams = _own_limit_target_grams(
        food, decision.get("dose_value"), str(decision.get("dose_unit", "") or ""),
        str(decision.get("component", "") or ""), str(decision.get("form", "") or ""),
    )
    if target_grams is not None:
        if target_grams and _portion_practicality(target_grams, food) == "ok":
            return f"eat ~{_format_grams(target_grams)} (the daily target; the full dose would pass the upper intake level)"
        return _meal_plan_normal_portion(food)
    if _portion_practicality(_grams_to_match_dose(decision), food) in ("large", "impractical"):
        return _meal_plan_normal_portion(food)
    return _amount_to_match_dose(decision)


def _meal_plan_prompts(
    replace_items: list[dict[str, Any]], diet_label: str, num_meals: int = 3, pregnant: bool | None = None
) -> tuple[str, str, str]:
    """(system_prompt, user_prompt, cache_key) for the meal-plan generation.
    `pregnant` (default: the pregnancy toggle) adds pregnancy food-safety rules."""
    n = max(1, min(3, int(num_meals or 3)))
    if pregnant is None:
        pregnant = _pregnancy_mode()
    lines = []
    for d in replace_items:
        food = _food_name(d.get("selected_food"))
        nutrient = _nutrient_title(d.get("component"))
        if pregnant and _not_advised_in_pregnancy(d.get("selected_food")):
            # Picked before pregnancy mode was on (the results flag it): never
            # put liver or swordfish into a pregnancy meal plan.
            lines.append(f"- a pregnancy-safe food rich in {nutrient} instead of {food} (not advised in pregnancy)")
            continue
        amount = _meal_plan_amount(d)
        lines.append(f"- {food} ({amount}) for {nutrient}")
    diet_clause = ""
    if diet_label and diet_label.strip().lower() not in ("no restriction", "none", ""):
        diet_clause = f" Every meal must fit a {diet_label} diet."
    meal_word = "meal" if n == 1 else "meals"
    system_prompt = (
        "You are a practical sports nutritionist and recipe writer for the SuppSwipe app. "
        f"Design exactly {n} {meal_word} that TOGETHER incorporate ALL of the given whole foods "
        "at roughly the daily amounts provided (spread the foods across the meals so every food is "
        "used at least once). Use common German-supermarket ingredients, keep it budget-friendly and "
        "realistic, and write the meal plan in English. Give each meal a short **bold** title followed by 3-5 short bullet points "
        "(ingredients with gram amounts, then one line on preparation). Keep each meal under 80 words. "
        "Respect safe intakes: if an amount is unrealistic (more than about 500 g of one food per day) "
        "or would exceed an upper intake level (for example liver at most one small portion per week "
        "because of vitamin A, at most 2 Brazil nuts per day because of selenium), use a sensible "
        "amount instead and add one short note that a supplement may be the practical choice for that "
        "nutrient. General guidance only; no medical advice."
        + (_PREGNANCY_MEAL_RULES if pregnant else "")
        + _MARKDOWN_STYLE
    )
    user_prompt = (
        "Whole foods to include, with the daily amount to aim for:\n"
        + "\n".join(lines)
        + diet_clause
        + f"\n\nWrite exactly {n} {meal_word} now."
    )
    return system_prompt, user_prompt, llm_cache.make_key("meal_plan", system_prompt, user_prompt, _generation_model(), _feature_model("meal"))


def _generate_meal_plan(
    replace_items: list[dict[str, Any]],
    diet_label: str,
    num_meals: int = 3,
    placeholder: Any = None,
) -> str:
    if not replace_items:
        return ""
    system_prompt, user_prompt, key = _meal_plan_prompts(replace_items, diet_label, num_meals)
    return _stream_llm_text(key, system_prompt, user_prompt, placeholder=placeholder, model=_feature_model("meal"))


# Diets whose meal-plan prompt may be prepared in the background. A religious
# or health-related filter (Halal, Kosher, gluten- / lactose-free, nut-free,
# low-sodium) and the pregnancy setting are sensitive (GDPR Art. 9): they are
# sent to Blockbrain only when the user taps "Generate meals".
_PREFETCH_DIET_IDS = frozenset({"none", "vegetarian", "vegan", "pescatarian"})


def _prefetch_allowed(diet_id: Any = None, pregnant: bool | None = None) -> bool:
    if pregnant is None:
        pregnant = _pregnancy_mode()
    if diet_id is None:
        diet_id = st.session_state.get("swipe_diet_profile_id", "none")
    return not pregnant and bb.normalize_lookup_key(str(diet_id or "none")) in _PREFETCH_DIET_IDS


def _prefetch_meal_plan(replace_items: list[dict[str, Any]], diet_label: str, num_meals: int = 3) -> None:
    """Start writing the default meal plan in the background as soon as the
    results screen opens, so "Generate meals" is instant (or nearly) when tapped.
    Not with the pregnancy setting or a religious / health diet (see
    _PREFETCH_DIET_IDS). Disable with SUPPSWIPE_PREFETCH_MEALS=0."""
    if not replace_items or not _prefetch_allowed():
        return
    if str(os.getenv("SUPPSWIPE_PREFETCH_MEALS", "1") or "1").strip().lower() in {"0", "false", "off", "no"}:
        return
    if bb.blockbrain_config_error():
        return
    system_prompt, user_prompt, key = _meal_plan_prompts(replace_items, diet_label, num_meals)
    if llm_cache.get(key) is not None or llm_cache.inflight(key) is not None:
        return
    # Once per plan and session: a failed prefetch is not retried by itself (the
    # live view's re-run would otherwise start it again and again); "Generate my
    # meals" retries on request.
    tried = st.session_state.setdefault("_suppswipe_prefetched", [])
    if key in tried:
        return
    tried.append(key)
    if not _consume_llm_quota("generate"):
        return
    llm_cache.submit(
        key,
        lambda: bb.call_blockbrain_text(
            system_prompt, user_prompt, model=_feature_model("meal") or None, on_text=lambda t: llm_cache.set_partial(key, t)
        ),
    )


def _benefits_prompts(replace_items: list[dict[str, Any]]) -> tuple[str, str, str] | None:
    lines = []
    for d in replace_items:
        nutrient = _nutrient_title(d.get("component"))
        food = _food_name(d.get("selected_food"))
        if nutrient and food:
            lines.append(f"- Isolated pill nutrient: {nutrient}  |  Whole food chosen instead: {food}")
    if not lines:
        return None
    system_prompt = (
        "You are a nutrition educator for the SuppSwipe app. For each pairing the user gives "
        "(an isolated supplement micronutrient vs. the whole food they chose to replace it with), "
        "contrast two things: (1) what the ISOLATED pill nutrient does on its own, and (2) the fuller "
        "set of health benefits and extra nutrients/compounds they ALSO gain by eating that whole food "
        "instead (co-nutrients, fibre, healthy fats, phytochemicals/antioxidants, protein, satiety, gut "
        "health, etc.). Make the added value of the whole food obvious, but stay accurate: mention it "
        "briefly when the food also has a downside (e.g. liver is very high in vitamin A, Brazil nuts in "
        "selenium), and say plainly when the supplement remains the standard advice (vitamin B12 on a "
        "vegan diet, folic acid before and in early pregnancy, vitamin D in winter or with little sun, "
        "iron or other nutrients prescribed for a diagnosed deficiency). For each item use this compact structure: a bold heading '<Nutrient> → <Food>', "
        "then '💊 Pill alone:' with one short line, then '🥗 Whole food also gives:' "
        "with 3-4 short bullets. Be concise and evidence-based. General guidance only; no individual "
        "medical advice."
        + _MARKDOWN_STYLE
    )
    user_prompt = "Pairings:\n" + "\n".join(lines) + "\n\nWrite the comparison now."
    return system_prompt, user_prompt, llm_cache.make_key("benefits", system_prompt, user_prompt, _generation_model(), _feature_model("benefits"))


def _start_whole_food_benefits(replace_items: list[dict[str, Any]]) -> Any:
    """Start writing the pill-vs-whole-food comparison for these swaps in the background, as the default meal plan is
    (llm_cache.submit: one job per prompt, its answer lands in the cache even if nobody waits for it any more).
    The Future, or None when it is already cached or there is nothing to compare. The caller counts the quota unit."""
    prompts = _benefits_prompts(replace_items)
    if prompts is None:
        return None
    system_prompt, user_prompt, key = prompts
    model = _feature_model("benefits") or None

    def generate() -> str:
        text = bb.call_blockbrain_text(
            system_prompt, user_prompt, model=model, on_text=lambda text: llm_cache.set_partial(key, text)
        )
        if not str(text or "").strip() or bb.looks_like_agent_error(str(text)):
            llm_cache.set_failure(key, bb.last_call_error())  # on this worker thread, where the adapter noted it
        return text

    return llm_cache.submit(key, generate)


def _supplement_search_links(keep_items: list[dict[str, Any]]) -> tuple[str, dict[str, str]]:
    import urllib.parse

    names = [_nutrient_title(d.get("component")) for d in keep_items if d.get("component")]
    names = list(dict.fromkeys([n for n in names if n]))
    if not names:
        return "", {}
    query = " ".join(names)
    enc = urllib.parse.quote_plus(query + " Kombipräparat Multivitamin")
    links = {
        "Compare prices on idealo.de": f"https://www.idealo.de/preisvergleich/MainSearchProductCategory.html?q={enc}",
        "Search on Amazon.de": f"https://www.amazon.de/s?k={enc}",
        "Google Shopping": f"https://www.google.com/search?tbm=shop&q={enc}",
    }
    return query, links


def _build_share_text(
    keep_items: list[dict[str, Any]],
    replace_items: list[dict[str, Any]],
    meal_plan: str,
) -> str:
    out = ["SuppSwipe — my results", ""]
    out.append(f"🥗 Replaced with whole foods ({len(replace_items)}):")
    if replace_items:
        for d in replace_items:
            food = _food_name(d.get("selected_food"))
            amount = _amount_to_match_dose(d)
            out.append(f"  • {_nutrient_title(d.get('component'))}: {food}" + (f" — {amount}" if amount else ""))
    else:
        out.append("  • (none)")
    out.append("")
    out.append(f"💊 Kept as a supplement ({len(keep_items)}):")
    if keep_items:
        for d in keep_items:
            out.append(f"  • {_nutrient_title(d.get('component'))} {d.get('dose_label', '')}".rstrip())
    else:
        out.append("  • (none)")
    if meal_plan.strip():
        out += ["", "🍽️ Meal plan:", meal_plan.strip()]
    out += ["", "Made with SuppSwipe. General nutrition information, not medical advice: https://suppswipe.streamlit.app"]
    return "\n".join(out)


# --- Scan history (stored in the visitor's own browser) ----------------------
# Streamlit Cloud serves every visitor from ONE server process, so a file on the
# server would be shared by everyone. History therefore lives in the browser's
# localStorage via a tiny invisible component and is mirrored into this
# session's state; nothing about a visitor's scans is stored server-side.
_HISTORY_COMPONENT_DIR = APP_DIR / "history_component"
try:
    _history_store = components.declare_component("scan_history", path=str(_HISTORY_COMPONENT_DIR))
except Exception:
    _history_store = None
_HISTORY_MAX = 30


def _load_scan_history() -> list[dict[str, Any]]:
    history = st.session_state.get("suppswipe_scan_history")
    if not isinstance(history, list):
        history = []
        st.session_state["suppswipe_scan_history"] = history
    return history


def _save_scan_history(history: list[dict[str, Any]]) -> None:
    history = list(history)[-_HISTORY_MAX:]
    st.session_state["suppswipe_scan_history"] = history
    st.session_state["_suppswipe_history_save"] = history


def _scan_content(snapshot: dict[str, Any]) -> dict[str, Any]:
    """A saved-scan snapshot without its timestamp (to compare two snapshots)."""
    return {k: v for k, v in snapshot.items() if k != "ts"}


def _saved_scan_args(state: Any) -> dict[str, Any] | None:
    """The current scan to mirror into the browser, re-stamped only when its
    content changes (so unchanged runs send identical props and the component
    isn't re-rendered). Nothing is sent for a scan the visitor just cleared
    (Clear history) until it changes again (next swipe or filter change)."""
    snapshot = _scan_snapshot(state)
    if snapshot is None:
        return None
    content = _scan_content(snapshot)
    suppressed = state.get("_suppswipe_scan_suppressed")
    if suppressed is not None:
        if suppressed == content:
            return None
        state.pop("_suppswipe_scan_suppressed", None)
    previous = state.get("_suppswipe_scan_snapshot")
    if isinstance(previous, dict) and _scan_content(previous) == content:
        return previous
    state["_suppswipe_scan_snapshot"] = snapshot
    return snapshot


# A supplement label is a few hundred characters; this bounds what one visitor can make the shared
# process parse (pasted text, or a scan restored from the browser's storage).
_MAX_LABEL_CHARS = 20_000


def _clean_history_entry(entry: Any) -> dict[str, Any] | None:
    """A scan-history entry from the browser's storage, in the shape the popover needs, or None.
    Whatever a device holds (an older build's data, hand-edited or damaged values) must never
    make every run of the app fail."""
    if not isinstance(entry, dict):
        return None
    clean = {str(k): v for k, v in entry.items() if k not in ("kept", "replaced")}
    for key in ("kept", "replaced"):
        value = entry.get(key)
        clean[key] = [
            {str(k): (v if isinstance(v, (str, int, float)) else str(v)) for k, v in item.items()}
            for item in (value if isinstance(value, list) else [])
            if isinstance(item, dict)
        ]
    return clean


def _sync_scan_history_with_browser() -> None:
    """Render the invisible storage component (once per run, at the end of the
    page): persist any pending change (history and the scan in progress) and
    pull the device's stored history and saved scan in on first load, merged
    with anything recorded before it arrived."""
    if _history_store is None:
        return
    pending = st.session_state.pop("_suppswipe_history_save", None)
    clear = bool(st.session_state.pop("_suppswipe_history_clear", False))
    clear_scan = bool(st.session_state.pop("_suppswipe_scan_clear", False))
    if clear_scan:
        st.session_state.pop("_suppswipe_scan_snapshot", None)
    token = st.session_state.get("_suppswipe_history_token")
    if not token:
        token = st.session_state["_suppswipe_history_token"] = uuid.uuid4().hex
    try:
        stored = _history_store(
            save=pending,
            clear=clear,
            saveScan=_saved_scan_args(st.session_state),
            clearScan=clear_scan,
            session=token,  # the iframe sends the stored data once per session
            key="suppswipe_history_store",
            default=None,
        )
    except Exception:
        return
    stored_history: Any = stored
    if isinstance(stored, dict):
        stored_history = stored.get("history") if isinstance(stored.get("history"), list) else []
    if isinstance(stored_history, list) and not st.session_state.get("_suppswipe_history_loaded"):
        st.session_state["_suppswipe_history_loaded"] = True
        saved_scan = stored.get("scan") if isinstance(stored, dict) else None
        # A first read that arrives after Start over / Clear history may still
        # carry the scan the visitor just dropped: don't offer it again.
        if st.session_state.get("_suppswipe_scan_forgotten"):
            saved_scan = None
        elif saved_scan is not None and _resumable_scan(saved_scan) is None:
            # Older than a week, an older build's format or damaged: never offered, so don't keep the label text on the device.
            saved_scan = None
            st.session_state["_suppswipe_scan_clear"] = True
        st.session_state["_suppswipe_saved_scan"] = saved_scan if isinstance(saved_scan, dict) else None
        current = _load_scan_history()
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for entry in [c for c in (_clean_history_entry(e) for e in stored_history) if c] + current:
            sig = repr(sorted((k, repr(v)) for k, v in entry.items()))
            if sig in seen:
                continue
            seen.add(sig)
            merged.append(entry)
        st.session_state["suppswipe_scan_history"] = merged[-_HISTORY_MAX:]
        if merged[-_HISTORY_MAX:] != stored_history:  # merged, cleaned or dropped something: write the repaired list back
            st.session_state["_suppswipe_history_save"] = merged[-_HISTORY_MAX:]
        st.rerun()


def _record_scan_to_history(decisions: dict[str, dict[str, Any]], diet_label: str) -> None:
    if not decisions:
        return
    if str((st.session_state.get("swipe_label_source") or {}).get("kind", "") or "") == "sample":
        return  # the demo label is not one of the visitor's scans
    sig = str(st.session_state.get("swipe_last_auto_signature", "") or "")
    if sig and sig == str(st.session_state.get("swipe_history_recorded_sig", "") or ""):
        return
    import time

    entry = {
        "ts": time.strftime("%Y-%m-%d %H:%M"),
        "diet": diet_label,
        "kept": [
            {"component": _nutrient_title(d.get("component")), "dose": str(d.get("dose_label", "") or "")}
            for d in decisions.values()
            if d.get("decision") == "keep"
        ],
        "replaced": [
            {
                "component": _nutrient_title(d.get("component")),
                "food": _food_name(d.get("selected_food")),
                "amount": _amount_to_match_dose(d),
            }
            for d in decisions.values()
            if d.get("decision") == "replace"
        ],
    }
    history = _load_scan_history()
    history.append(entry)
    _save_scan_history(history)
    st.session_state["swipe_history_recorded_sig"] = sig


def _render_scan_history_popover() -> None:
    history = _load_scan_history()
    if not history:
        return
    with st.popover(f"🕘 Recent scans ({len(history)})", width="stretch"):
        st.caption("Your past scans, saved only in this browser — newest first.")
        for entry in reversed(history[-15:]):
            ts = str(entry.get("ts", "") or "")
            diet = str(entry.get("diet", "") or "")
            kept = entry.get("kept", []) or []
            replaced = entry.get("replaced", []) or []
            head = f"**{ts}** · {len(replaced)} swapped / {len(kept)} kept"
            if diet and diet.lower() not in ("no restriction", "none"):
                head += f" · {diet}"
            st.markdown(head)
            for r in replaced:
                st.markdown(f"- 🥗 {_nutrient_title(r.get('component'))} → {r.get('food', '')} ({r.get('amount', '')})")
            for k in kept:
                st.markdown(f"- 💊 {_nutrient_title(k.get('component'))} {k.get('dose', '')}".rstrip())
            st.divider()
        if st.button("Clear history", width="stretch", key="swipe_clear_history"):
            st.session_state["suppswipe_scan_history"] = []
            st.session_state["_suppswipe_history_clear"] = True
            _forget_saved_scan()
            st.rerun()


def _excluded_swaps_caption(excluded: list[dict[str, Any]], diet_label: str) -> None:
    """Note which flagged swaps (no longer fitting the filter) are left out."""
    if not excluded:
        return
    names = ", ".join(dict.fromkeys(_nutrient_title(d.get("component")) for d in excluded if d.get("component")))
    st.caption(f"Not included until you choose another food: {names} (doesn't fit {diet_label}).")


def _on_meal_count_change() -> None:
    st.session_state["swipe_meal_count_choice"] = int(st.session_state.get("swipe_meal_count", 3) or 3)


# --- Micronutrient allow-list -------------------------------------------------
# Only scientifically recognised nutrients become swipe cards: the 13 essential
# vitamins + the essential minerals, plus choline and the omega-3 fatty acids
# (EPA/DHA/ALA), i.e. exactly the nutrients of the bb canonical nutrient
# lexicon. Recognition is whole-word on the name BEFORE "(as ...)": "Ashwagandha"
# is not DHA, "environmental" is not iron, omega-6 / GLA are not omega-3, while
# "Vitamin B-12", "B12" and "Zinc (as zinc amino acid chelate)" are accepted.

# Checked FIRST (whole words, on the name before "(as ...)"): fillers,
# excipients and label metadata are never cards even when they name a mineral
# ("magnesium stearate", "sodium benzoate").
_NON_MICRONUTRIENT_DENY = [
    "stearate", "stearic", "gelatin", "cellulose", "microcrystalline", "croscarmellose", "povidone",
    "benzoate", "lauryl", "polysorbate", "silica", "silicate", "silicon dioxide", "titanium dioxide",
    "maltodextrin", "dextrose", "starch", "glycolate", "rice flour", "rice concentrate", "sucralose",
    "sorbitol", "xylitol", "sweetener", "flavor", "flavour", "coloring", "colouring",
    "serving size", "servings per", "daily value", "container", "proprietary",
]


def _is_micronutrient(name: str) -> bool:
    """True for scientifically recognised nutrients (vitamins, minerals, choline,
    omega-3); False for macronutrients, botanicals, fillers and label metadata."""
    head = _nutrient_name_head(str(name or ""))
    if not head:
        return False
    if any(_whole_word_in(bad, head) for bad in _NON_MICRONUTRIENT_DENY):
        return False
    key = bb.canonical_nutrient_key(str(name or ""))
    # An umbrella name ("Vitamin B complex") has no foods or dose of its own;
    # the parser turns it into the B vitamins it stands for.
    return bool(key) and not bb._is_umbrella_key(key)


def _filter_to_micronutrients(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop anything that is not a micronutrient so users only swipe real nutrients."""
    return [c for c in components if _is_micronutrient(str(c.get("component", "") or ""))]


# --- What the rest of the label says about a dose ------------------------------------
# Applied to the parsed micronutrients of a scan (and of a resumed one):
#  - a µ read as m (photo OCR): "Vitamin D3 20 mg 400%" — the printed %NRV
#    only fits µg (20 µg = 400% of 5 µg), so the unit is corrected;
#  - weekly products ("Einnahme: 1 Tablette pro Woche"): the limits and
#    portions use the daily average (500 µg a week ~ 71 µg a day).
_PRINTED_PERCENT_RE = re.compile(r"(\d+(?:[.,]\d+)*)\s*%(?!\s*(?:as|als|aus|from|of|beta|davon)\b)", re.IGNORECASE)
# The mg reading must be this many times the printed %NRV (a 1000x misread
# leaves no doubt) while the µg reading is within a factor of 2 of it.
_MISREAD_UNIT_MIN_RATIO = 300.0
_MISREAD_UNIT_MATCH = 2.0
_WEEKLY_INTAKE_RE = re.compile(
    r"\b(?:pro|je|per|a|each|every|einmal\s+(?:pro|die|in\s+der|je))\s+woche\b|\bw(?:ö|oe|o)chentlich\b|"
    r"\bweekly\b|\bonce\s+a\s+week\b|\bper\s+week\b",
    re.IGNORECASE,
)
_EVERY_N_DAYS_RE = re.compile(r"\balle\s+(\d{1,2})\s+tage\b|\bevery\s+(\d{1,2})\s+days\b", re.IGNORECASE)
_EVERY_N_WEEKS_RE = re.compile(r"\balle\s+(\d)\s+wochen\b|\bevery\s+(\d)\s+weeks\b", re.IGNORECASE)
_DAILY_INTAKE_RE = re.compile(
    r"\bt(?:ä|ae|a)glich\b|\b(?:pro|je)\s+tag\b|\btagesdosis\b|\bdaily\b|\bper\s+day\b|\ba\s+day\b",
    re.IGNORECASE,
)


def _printed_nrv_percent(label_line: Any) -> float | None:
    """The %NRV / %DV printed on a label line (the last percentage outside
    brackets that is not a share such as "50% as beta-carotene"), or None."""
    line = str(label_line or "")
    depth, depths = 0, []
    for ch in line:
        depth += ch in "(["
        depths.append(depth)
        depth -= ch in ")]"
        depth = max(depth, 0)
    found = [m for m in _PRINTED_PERCENT_RE.finditer(line) if depths[m.start()] == 0]
    if not found:
        return None
    value = bb._parse_float(found[-1].group(1))
    return value if value and value > 0 else None


def _correct_misread_unit(component: dict[str, Any]) -> dict[str, Any]:
    """`component` with "mg" turned into "mcg" (and "unit_corrected": True) when
    the line's printed %NRV proves the unit was misread (see above)."""
    if str(component.get("dose_unit", "") or "").lower() != "mg" or component.get("dose_value") is None:
        return component
    name = str(component.get("component", "") or "")
    key = str(component.get("nutrient_key", "") or "") or bb.canonical_nutrient_key(name)
    nrv = _EU_NRV.get(key)
    printed = _printed_nrv_percent(component.get("label_line"))
    if nrv is None or nrv[1] != "mcg" or printed is None:
        return component
    form = str(component.get("form", "") or "")
    as_mg = _dose_vs_nrv_ratio(key, component["dose_value"], "mg", form)
    as_mcg = _dose_vs_nrv_ratio(key, component["dose_value"], "mcg", form)
    if as_mg is None or as_mcg is None:
        return component
    if as_mg * 100 / printed >= _MISREAD_UNIT_MIN_RATIO and 1 / _MISREAD_UNIT_MATCH <= as_mcg * 100 / printed <= _MISREAD_UNIT_MATCH:
        return {**component, "dose_unit": "mcg", "unit_corrected": True}
    return component


def _intake_interval_days(text: str) -> int:
    """7 for a weekly product ("1 Tablette pro Woche", "once a week"), N for
    "alle N Tage" / "every N days" (or N weeks), else 1. A label that also
    speaks of a daily intake ("täglich", "Tagesdosis", "per day") stays daily."""
    raw = str(text or "")
    if _DAILY_INTAKE_RE.search(raw):
        return 1
    weeks = _EVERY_N_WEEKS_RE.search(raw)
    if weeks:
        return 7 * int(weeks.group(1) or weeks.group(2))
    days = _EVERY_N_DAYS_RE.search(raw)
    if days:
        n = int(days.group(1) or days.group(2))
        return n if 2 <= n <= 60 else 1
    return 7 if _WEEKLY_INTAKE_RE.search(raw) else 1


def _apply_label_context(components: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    """The scan's micronutrients with misread units corrected and, for a
    weekly (every-N-days) product, the daily average as the dose
    ("intake_days" and the label's own "intake_dose_value" kept for the card)."""
    out = [_correct_misread_unit(c) for c in components]
    days = _intake_interval_days(text)
    if days <= 1:
        return out
    spread = []
    for c in out:
        if c.get("dose_value") is None:
            spread.append(c)
            continue
        c = {**c, "intake_days": days, "intake_dose_value": c["dose_value"], "intake_dose_max": c.get("dose_max")}
        c["dose_value"] = float(c["dose_value"]) / days
        if c.get("dose_max") is not None:
            c["dose_max"] = float(c["dose_max"]) / days
        spread.append(c)
    return spread


# --- One card per nutrient ----------------------------------------------------
# Cards that resolve to the same nutrient are merged (they would otherwise
# overwrite each other's swipe decision). One dosed row is kept: the most
# authoritative one (bb.label_row_preference — the nutrient-table line with
# %NRV / %DV, else the row naming a chemical form, else the first). A product
# title ("Vitamin D3 1000 I.E. Tabletten"), a marketing line or a
# second-language line (DE/FR labels) repeats the dose; it never adds to it.
# Doses are summed ONLY for the one whitelisted pair of chemically distinct
# forms listed on separate lines: vitamin A as preformed retinol / retinyl
# ester + as beta-carotene (the UL applies to the preformed share only). Other
# "two forms" (magnesium citrate + oxide on separate lines) are too rare to
# tell apart from a title or a translation, so they are not summed.
# Omega-3 rows ("Fish oil 1000 mg / EPA 180 mg / DHA 120 mg") become ONE
# EPA+DHA card; the fish-oil weight is the carrier, not an omega-3 amount.
_OMEGA3_FAMILY_KEYS = ("omega 3", "fish oil", "epa", "dha")


def _component_nutrient_key(item: dict[str, Any]) -> str:
    name = str(item.get("component", "") or "")
    return str(item.get("nutrient_key", "") or "") or bb.canonical_nutrient_key(name) or bb.normalize_lookup_key(name)


def _sum_distinct_form_doses(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """One row with the summed dose of several forms, or None if not summable."""
    first = rows[0]
    unit = bb._normalize_component_unit_token(str(first.get("dose_unit", "") or ""))
    target_unit = unit if unit in ("mg", "mcg", "g") else "mcg"
    total = 0.0
    parts = []
    for row in rows:
        component = str(row.get("component", "") or "")
        form = str(row.get("form", "") or "")
        amount = _dose_in_unit(component, row.get("dose_value"), str(row.get("dose_unit", "") or ""), target_unit, form)
        if amount is None:
            return None
        total += amount
        parts.append(f"{form} {_dose_text(row.get('dose_value'), str(row.get('dose_unit', '') or '')).lower()}")
    merged = dict(first)
    merged.update({"dose_value": round(total, 6), "dose_unit": target_unit, "form": " + ".join(parts)})
    if int(first.get("intake_days") or 1) > 1:  # a weekly product: the label's own summed dose
        merged.update({"intake_dose_value": round(total * int(first["intake_days"]), 6), "intake_dose_max": None})
    return merged


def _vitamin_a_form_pair(rows: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """[preformed row, beta-carotene row] when vitamin A is listed on separate
    label lines in both forms, else None."""
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if _component_nutrient_key(row) != "vitamin a" or not row.get("label_line"):
            continue
        kind = bb.vitamin_a_form_kind(str(row.get("component", "") or ""), str(row.get("form", "") or ""))
        if kind:
            by_kind.setdefault(kind, []).append(row)
    if set(by_kind) != {"preformed", "carotenoid"}:
        return None
    pair = [max(by_kind[kind], key=bb.label_row_preference) for kind in ("preformed", "carotenoid")]
    if pair[0].get("label_line") == pair[1].get("label_line"):
        return None
    return pair


def _merge_same_nutrient_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    dosed = [r for r in rows if r.get("dose_value") is not None]
    if not dosed:
        return dict(rows[0])
    if len(dosed) > 1:
        pair = _vitamin_a_form_pair(dosed)
        summed = _sum_distinct_form_doses(pair) if pair else None
        if summed is not None:
            return summed
    return dict(max(dosed, key=bb.label_row_preference))  # first of the best


def _merge_omega3_family(rows_by_key: dict[str, dict[str, Any]]) -> dict[str, Any]:
    epa, dha = rows_by_key.get("epa"), rows_by_key.get("dha")
    if epa and dha and epa.get("dose_value") is not None and dha.get("dose_value") is not None:
        epa_mg = _dose_in_unit("epa", epa.get("dose_value"), str(epa.get("dose_unit", "") or ""), "mg")
        dha_mg = _dose_in_unit("dha", dha.get("dose_value"), str(dha.get("dose_unit", "") or ""), "mg")
        if epa_mg is not None and dha_mg is not None:
            return {
                "component": "omega-3 (epa+dha)",
                "dose_value": round(epa_mg + dha_mg, 6),
                "dose_unit": "mg",
                "form": f"EPA {bb.format_float(epa_mg)} mg + DHA {bb.format_float(dha_mg)} mg",
                "nutrient_key": "omega 3",
            }
    for key in ("omega 3", "epa", "dha", "fish oil"):
        row = rows_by_key.get(key)
        if row and row.get("dose_value") is not None:
            return row
    return next(iter(rows_by_key.values()))


def _merge_duplicate_components(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One component per nutrient, in label order (see the comment above)."""
    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for item in components:
        key = _component_nutrient_key(item)
        if not key:
            continue
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(item)
    merged = {key: _merge_same_nutrient_rows(rows) for key, rows in groups.items()}
    family = [key for key in order if key in _OMEGA3_FAMILY_KEYS]
    if len(family) > 1:
        merged[family[0]] = _merge_omega3_family({key: merged[key] for key in family})
        order = [key for key in order if key not in family[1:]]
    return [merged[key] for key in order]


def _whole_food_pool(component: str) -> list[dict[str, Any]]:
    """The ranked USDA whole-food pool of a card. B12-fortified foods are left
    out here; _with_fortified_options offers them on vegan / vegetarian cards
    (USDA's US-fortified plant drinks never: see _is_us_fortified_b12_row)."""
    try:
        pool = list(bb._build_local_food_rows_for_component(component, limit=SWIPE_CARD_FOOD_POOL) or [])
    except Exception:
        return []
    b12 = bb.canonical_nutrient_key(component) == "vitamin b12"
    return [food for food in pool if not food.get("fortified") and not (b12 and _is_us_fortified_b12_row(food))]


def _build_swipe_cards(components: list[dict[str, Any]], details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    detail_by_component = {
        bb.normalize_lookup_key(str(d.get("component", ""))): d for d in details
    }
    cards: list[dict[str, Any]] = []
    for item in _merge_duplicate_components(components):
        comp_name = str(item.get("component", "") or "").strip()
        comp_key = bb.normalize_lookup_key(comp_name)
        # Primary source: USDA single-ingredient whole foods, ranked by the
        # amount of THIS nutrient per 100 g (highest dose on top, one unit per
        # list). A deep pool is kept so dietary filtering downstream still
        # leaves options for restrictive diets (e.g. vegan B1/B12).
        foods = _whole_food_pool(comp_name)
        # Fallback to LLM-generated matches only if USDA has nothing.
        if not foods:
            detail = detail_by_component.get(comp_key, {})
            d_foods = detail.get("foods", []) if isinstance(detail, dict) else []
            foods = list(d_foods) if isinstance(d_foods, list) else []
        # Guarantee highest dose first.
        try:
            foods.sort(key=lambda f: float(f.get("amount_per_100g", 0) or 0), reverse=True)
        except Exception:
            pass
        cards.append(
            {
                "component": comp_name,
                "component_key": comp_key,
                "nutrient_key": _component_nutrient_key(item),
                "dose_label": _dose_label(item),
                "dose_value": item.get("dose_value"),
                "dose_max": item.get("dose_max"),
                "dose_unit": str(item.get("dose_unit", "") or ""),
                "form": str(item.get("form", "") or ""),
                "foods": foods,
                "unit_corrected": bool(item.get("unit_corrected")),
            }
        )
    return cards


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = str(hex_color or "").lstrip("#")
    if len(h) != 6:
        return (100, 116, 139)
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except Exception:
        return (100, 116, 139)


def _mix_hex(hex_a: str, hex_b: str, t: float) -> str:
    ar, ag, ab = _hex_to_rgb(hex_a)
    br, bg, bb = _hex_to_rgb(hex_b)
    t = max(0.0, min(1.0, t))
    return "#{:02x}{:02x}{:02x}".format(
        round(ar + (br - ar) * t),
        round(ag + (bg - ag) * t),
        round(ab + (bb - ab) * t),
    )


# Card colours reflect the real-world colour associated with each micronutrient
# (physical compound colour, or its classic food/branding hue):
#   riboflavin/B2 & D = yellow-gold, B12/cobalamin & iron = red, folate & K =
#   leafy green (foliage / "Koagulation"), A & C = orange (carotene/citrus),
#   iodine = violet (iodine vapour), copper = copper, magnesium = teal, zinc =
#   metallic slate, potassium = lilac (flame test), omega-3/EPA/DHA = ocean blue.
_NUTRIENT_BASE_COLORS: list[tuple[str, str]] = [
    ("vitamin b12", "#e11d48"), ("cobalamin", "#e11d48"),
    ("vitamin b9", "#22c55e"), ("folate", "#22c55e"), ("folic", "#22c55e"),
    ("vitamin b7", "#d97706"), ("biotin", "#d97706"),
    ("vitamin b6", "#f59e0b"), ("pyridoxine", "#f59e0b"),
    ("vitamin b5", "#eab308"), ("pantothenic", "#eab308"),
    ("vitamin b3", "#f59e0b"), ("niacin", "#f59e0b"),
    ("vitamin b2", "#f59e0b"), ("riboflavin", "#f59e0b"),
    ("vitamin b1", "#eab308"), ("thiamin", "#eab308"),
    ("vitamin a", "#f97316"), ("beta-carotene", "#f97316"), ("beta carotene", "#f97316"),
    ("vitamin c", "#f97316"), ("ascorbic", "#f97316"),
    ("vitamin d", "#f59e0b"),
    ("vitamin e", "#ca8a04"), ("tocopherol", "#ca8a04"),
    ("vitamin k", "#16a34a"),
    ("calcium", "#94a3b8"),
    ("iron", "#dc2626"),
    ("magnesium", "#14b8a6"),
    ("zinc", "#64748b"),
    ("iodine", "#7c3aed"),
    ("selenium", "#a16207"),
    ("copper", "#c2410c"),
    ("manganese", "#db2777"),
    ("chromium", "#059669"),
    ("molybdenum", "#2563eb"),
    ("potassium", "#8b5cf6"),
    ("sodium", "#eab308"),
    ("phosphorus", "#a855f7"),
    ("chloride", "#22c55e"),
    ("choline", "#0ea5e9"),
    ("omega", "#0ea5e9"), ("epa", "#0ea5e9"), ("dha", "#0ea5e9"), ("fish oil", "#0ea5e9"),
    ("vitamin", "#6366f1"),
]


def _component_card_theme(component_name: str) -> dict[str, str]:
    key = bb.normalize_lookup_key(component_name)
    base = "#64748b"
    for needle, color in _NUTRIENT_BASE_COLORS:
        if needle in key:
            base = color
            break
    r, g, b = _hex_to_rgb(base)
    return {
        "accent": base,
        "accent2": _mix_hex(base, "#0f172a", 0.55),
        "bg": (
            "linear-gradient(160deg, #ffffff 0%, "
            f"{_mix_hex(base, '#ffffff', 0.9)} 55%, {_mix_hex(base, '#ffffff', 0.82)} 100%)"
        ),
        "chip_bg": f"rgba({r}, {g}, {b}, 0.14)",
        "chip_text": _mix_hex(base, "#0f172a", 0.4),
    }


def _render_header() -> None:
    # Only rules whose selectors match something on a screen (welcome, analyzing,
    # card, results, dialogs) live here; the swipe card styles itself inside its
    # iframe (swipe_component/index.html).
    st.markdown(
        """
        <style>
            /* Keep the Streamlit top bar visible but transparent, and push content below it. */
            [data-testid="stHeader"] {
                background: transparent;
            }
            [data-testid="stAppViewContainer"] {
                background:
                    radial-gradient(circle at 0% 0%, #fff4de 0%, rgba(255, 244, 222, 0.22) 45%, transparent 70%),
                    radial-gradient(circle at 100% 0%, #dff7ef 0%, rgba(223, 247, 239, 0.20) 42%, transparent 70%),
                    linear-gradient(180deg, #fefcf8 0%, #f8fbff 100%);
            }
            .block-container {
                max-width: 440px;
                padding-top: 0.8rem;
                /* Room for Streamlit Cloud's floating "Manage app" pill and the phone's home indicator. */
                padding-bottom: calc(5rem + env(safe-area-inset-bottom, 0px));
            }
            /* Readability (WCAG AA 4.5:1) and comfortable touch targets. */
            [data-testid="stCaptionContainer"],
            [data-testid="stCaptionContainer"] p {
                color: #475569 !important;
                opacity: 1 !important;
            }
            /* A clear keyboard focus ring on every control. */
            button:focus-visible,
            [data-baseweb="tab"]:focus-visible,
            a:focus-visible,
            summary:focus-visible,
            [role="button"]:focus-visible {
                outline: 3px solid #1d4ed8 !important;
                outline-offset: 2px !important;
            }
            /* Tooltips never run wider than a phone screen. */
            [data-testid="stTooltipContent"] {
                max-width: min(320px, 88vw);
                overflow-wrap: anywhere;
            }
            .stButton button,
            .stDownloadButton button,
            .stFormSubmitButton button,
            [data-testid="stPopover"] > div > button {
                min-height: 44px;
            }
            [data-testid="stButtonGroup"] button,
            [data-baseweb="tab"] {
                min-height: 44px;
            }
            label[data-baseweb="checkbox"] {
                min-height: 44px;
                align-items: center;
            }
            @media (max-width: 380px) {
                [data-baseweb="tab"] {
                    padding-left: 0.5rem;
                    padding-right: 0.5rem;
                }
                [data-baseweb="tab"] p {
                    font-size: 0.85rem;
                }
            }
            @media (prefers-reduced-motion: reduce) {
                .analyze-loading-arrow,
                .plan-dots i {
                    animation: none !important;
                }
            }
            @media (max-width: 360px) {
                [data-testid="stButtonGroup"] button {
                    padding-left: 0.55rem;
                    padding-right: 0.55rem;
                }
            }
            /* Long labels (results items, "doesn't fit Vegan — tap to choose
               another") wrap instead of ending in an ellipsis. */
            .stButton button [data-testid="stMarkdownContainer"],
            .stButton button [data-testid="stMarkdownContainer"] p {
                white-space: normal;
            }
            /* Results dashboard ("Your plan"). */
            .plan-hero {
                background: linear-gradient(135deg, #064e3b 0%, #065f46 50%, #047857 100%);
                color: #ffffff;
                border-radius: 20px;
                padding: 18px 18px 16px 18px;
                margin: 0.2rem 0 0.8rem 0;
                box-shadow: 0 10px 24px rgba(4, 120, 87, 0.22);
            }
            .plan-kicker {
                font-size: 0.78rem;
                font-weight: 800;
                letter-spacing: 0.08em;
                text-transform: uppercase;
                opacity: 0.9;
            }
            .plan-title {
                font-size: 1.35rem;
                font-weight: 900;
                line-height: 1.2;
                margin-top: 4px;
            }
            .plan-bar {
                height: 8px;
                border-radius: 999px;
                background: rgba(255, 255, 255, 0.28);
                margin: 12px 0 14px 0;
                overflow: hidden;
            }
            .plan-bar > span {
                display: block;
                height: 100%;
                border-radius: 999px;
                background: #ffffff;
            }
            .plan-stats {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(68px, 1fr));
                gap: 8px;
            }
            .plan-stat {
                background: rgba(255, 255, 255, 0.16);
                border-radius: 12px;
                padding: 8px 3px 7px 3px;
                text-align: center;
            }
            .plan-stat b {
                display: block;
                font-size: 1.05rem;
                font-weight: 900;
            }
            .plan-stat span {
                display: block;
                font-size: 0.78rem;
            }
            .plan-warn {
                background: #fff7ed;
                border: 1px solid #fed7aa;
                border-radius: 14px;
                padding: 10px 12px;
                margin-bottom: 0.8rem;
                color: #7c2d12;
                font-size: 0.84rem;
                line-height: 1.4;
            }
            .plan-warn-h {
                font-weight: 900;
                margin-bottom: 4px;
            }
            .plan-warn-i + .plan-warn-i {
                margin-top: 6px;
            }
            .plan-h {
                font-size: 0.8rem;
                font-weight: 900;
                letter-spacing: 0.05em;
                text-transform: uppercase;
                color: #334155;
                margin: 0.9rem 0 0.4rem 0;
            }
            /* The plan lists: each row is a keyed container holding the .plan-row HTML and a transparent
               button of the same size, so the whole row is the tap target. Without these rules the button
               would just show as an ordinary one under its row. */
            [class*="st-key-planlist_"] {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 16px;
                overflow: hidden;
                gap: 0;
            }
            [class*="st-key-planrow_"] {
                position: relative;
                gap: 0;
            }
            /* Streamlit pulls every markdown block up by 1rem (it cancels a paragraph's margin); a .plan-row has none. */
            [class*="st-key-planrow_"] [data-testid="stMarkdownContainer"] {
                margin-bottom: 0;
            }
            [class*="st-key-planbtn_"] {
                position: absolute !important;
                inset: 0;
                z-index: 2;
                width: 100% !important;
                height: 100% !important;
                margin: 0;
            }
            [class*="st-key-planbtn_"] [data-testid="stButton"] {
                width: 100%;
                height: 100%;
            }
            [class*="st-key-planbtn_"] button {
                width: 100%;
                height: 100%;
                min-height: 44px;
                padding: 0;
                border: 0;
                border-radius: 0;
                background: transparent;
            }
            /* The label stays for screen readers; the row itself is what is seen. */
            [class*="st-key-planbtn_"] button p {
                position: absolute;
                width: 1px;
                height: 1px;
                margin: -1px;
                overflow: hidden;
                clip: rect(0, 0, 0, 0);
                white-space: nowrap;
            }
            [class*="st-key-planbtn_"] button:hover {
                background: transparent;
            }
            @media (hover: hover) {
                [class*="st-key-planbtn_"] button:hover {
                    background: rgba(16, 185, 129, 0.07);
                }
            }
            [class*="st-key-planbtn_"] button:active {
                background: rgba(16, 185, 129, 0.16);
            }
            [class*="st-key-planbtn_"] button:focus-visible {
                outline-offset: -3px !important;
            }
            .plan-sep {
                border-top: 1px solid #f1f5f9;
            }
            /* The options window's close X is a 44 px touch target (Streamlit draws it 18 px). Where :has() is not
               supported the X simply keeps its size. */
            [data-testid="stDialog"]:has([class*="st-key-plandlg"]) [role="dialog"] button[aria-label="Close"] {
                min-width: 44px;
                min-height: 44px;
            }
            .plan-row {
                display: flex;
                gap: 12px;
                align-items: flex-start;
                padding: 11px 12px;
                min-height: 44px;
                box-sizing: border-box;
            }
            .plan-chev {
                flex: 0 0 auto;
                align-self: center;
                color: #475569;
                font-size: 1.5rem;
                font-weight: 800;
                line-height: 1;
            }
            .plan-ico {
                font-size: 1.5rem;
                line-height: 1;
                min-width: 34px;
                text-align: center;
                flex: 0 0 auto;
            }
            .plan-main {
                flex: 1 1 auto;
                min-width: 0;
            }
            .plan-name {
                font-weight: 800;
                color: #0f172a;
                display: flex;
                flex-wrap: wrap;
                gap: 6px;
                align-items: center;
                overflow-wrap: anywhere;
            }
            .plan-amt {
                font-size: 0.75rem;
                font-weight: 800;
                color: #065f46;
                background: #d1fae5;
                border-radius: 999px;
                padding: 2px 8px;
            }
            .plan-dose {
                font-size: 0.75rem;
                font-weight: 800;
                color: #7f1d1d;
                background: #fee2e2;
                border-radius: 999px;
                padding: 2px 8px;
            }
            .plan-sub {
                font-size: 0.8rem;
                color: #475569;
                margin-top: 2px;
            }
            .plan-bonus {
                font-size: 0.78rem;
                color: #047857;
                margin-top: 2px;
            }
            .plan-idea {
                font-size: 0.86rem;
                color: #1e293b;
                padding: 8px 0;
                border-bottom: 1px solid #f1f5f9;
            }
            .plan-sr {
                position: absolute;
                width: 1px;
                height: 1px;
                overflow: hidden;
                clip: rect(0 0 0 0);
                white-space: nowrap;
            }
            .plan-writing {
                display: flex;
                align-items: center;
                gap: 10px;
                color: #475569;
                font-size: 0.88rem;
                padding: 12px 14px;
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 14px;
            }
            .plan-dots {
                display: inline-flex;
                gap: 4px;
            }
            .plan-dots i {
                width: 6px;
                height: 6px;
                border-radius: 50%;
                background: #10b981;
                animation: plan-dot 1.2s infinite ease-in-out;
            }
            .plan-dots i:nth-child(2) {
                animation-delay: 0.15s;
            }
            .plan-dots i:nth-child(3) {
                animation-delay: 0.3s;
            }
            @keyframes plan-dot {
                0%, 80%, 100% { opacity: 0.25; transform: translateY(0); }
                40% { opacity: 1; transform: translateY(-3px); }
            }
            .shop-list {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 16px;
                padding: 4px 12px;
            }
            .shop-row {
                display: flex;
                gap: 8px;
                padding: 9px 0;
                font-size: 0.88rem;
                color: #0f172a;
            }
            .shop-row + .shop-row {
                border-top: 1px solid #f1f5f9;
            }
            .shop-name {
                flex: 1 1 auto;
                font-weight: 700;
            }
            .shop-qty {
                color: #475569;
                min-width: 64px;
                text-align: right;
            }
            .shop-cost {
                min-width: 60px;
                text-align: right;
                font-weight: 800;
            }
            .shop-total {
                display: flex;
                gap: 8px;
                padding: 10px 0 9px 0;
                border-top: 2px solid #e2e8f0;
                font-size: 0.92rem;
                font-weight: 900;
                color: #0f172a;
            }
            .diet-strip-label {
                font-size: 0.72rem;
                font-weight: 800;
                letter-spacing: 0.05em;
                text-transform: uppercase;
                color: #475569;
                margin: 0.25rem 0 0.3rem 0;
            }
            .chip {
                display: inline-block;
                font-size: 0.76rem;
                padding: 5px 10px;
                border-radius: 999px;
                background: #f3f7fc;
                border: 1px solid #d7e4f1;
                margin-bottom: 8px;
                font-weight: 700;
                color: #233243;
            }
            .analyze-loading-wrap {
                min-height: 360px;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                text-align: center;
                gap: 0.75rem;
            }
            .analyze-loading-arrow {
                width: 54px;
                height: 54px;
                border-radius: 999px;
                display: flex;
                align-items: center;
                justify-content: center;
                color: #0f766e;
                background: #e9fbf4;
                border: 1px solid #8dd9c0;
                font-size: 1.45rem;
                font-weight: 900;
                animation: suppswipe-spin 0.95s linear infinite;
            }
            .analyze-loading-title {
                font-size: 1rem;
                font-weight: 800;
                color: #152739;
            }
            .analyze-loading-sub {
                font-size: 0.86rem;
                color: #4f6274;
                max-width: 280px;
            }
            .brand {
                display: flex;
                align-items: center;
                gap: 8px;
                font-weight: 900;
                font-size: 1.05rem;
                letter-spacing: -0.01em;
                color: #064e3b;
                margin: 0 0 0.4rem 0;
            }
            .brand-foot {
                text-align: center;
                font-size: 0.75rem;
                font-weight: 600;
                color: #64748b;
                margin: 1.2rem 0 0.4rem 0;
            }
            .brand-mark {
                display: inline-flex;
                align-items: center;
                justify-content: center;
                width: 28px;
                height: 28px;
                border-radius: 9px;
                background: #047857;
                color: #ffffff;
                font-size: 0.9rem;
            }
            .hero {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 22px;
                padding: 22px 18px 18px 18px;
                text-align: center;
                box-shadow: 0 10px 28px rgba(15, 23, 42, 0.06);
                margin-bottom: 0.6rem;
            }
            .hero-art {
                font-size: 2.4rem;
                line-height: 1.1;
            }
            .hero-art span {
                font-size: 1.4rem;
                color: #94a3b8;
                margin: 0 10px;
                vertical-align: middle;
            }
            .hero-title {
                font-size: 1.55rem;
                font-weight: 900;
                line-height: 1.15;
                letter-spacing: -0.02em;
                color: #0f172a;
                margin-top: 10px;
            }
            .hero-sub {
                font-size: 0.92rem;
                line-height: 1.45;
                color: #475569;
                margin: 10px auto 0 auto;
                max-width: 340px;
            }
            .steps {
                display: grid;
                grid-template-columns: repeat(3, 1fr);
                gap: 8px;
                margin-top: 16px;
            }
            .step {
                background: #f0fdf4;
                border-radius: 14px;
                padding: 10px 4px 9px 4px;
            }
            .step span {
                display: block;
                font-size: 1.35rem;
            }
            .step b {
                display: block;
                font-size: 0.88rem;
                color: #064e3b;
                margin-top: 2px;
            }
            .step small {
                display: block;
                font-size: 0.76rem;
                color: #475569;
            }
            @keyframes suppswipe-spin {
                from { transform: rotate(0deg); }
                to { transform: rotate(360deg); }
            }
        </style>
        <div class="brand" role="heading" aria-level="1"><span class="brand-mark" aria-hidden="true">S</span>SuppSwipe</div>
        """,
        unsafe_allow_html=True,
    )


def _reset_swipe_state() -> None:
    """Clear swipe session state, but keep the chosen dietary filter and the
    pregnancy toggle."""
    saved_diet = st.session_state.get("swipe_diet_profile_id", "none")
    saved_pregnant = bool(st.session_state.get("swipe_pregnant", False))
    next_nonce = int(st.session_state.get("swipe_reset_nonce", 0)) + 1
    for key in [k for k in list(st.session_state.keys()) if k.startswith("swipe_")]:
        st.session_state.pop(key, None)
    st.session_state["swipe_reset_nonce"] = next_nonce
    _init_state()
    st.session_state["swipe_diet_profile_id"] = saved_diet
    st.session_state["swipe_pregnant"] = saved_pregnant


def _selected_session_in_progress() -> bool:
    if not (st.session_state.get("swipe_cards") or []):
        return False
    return bool(int(st.session_state.get("swipe_index", 0)) > 0 or (st.session_state.get("swipe_decisions") or {}))


def _extract_ean_from_text(text: str) -> str:
    """First barcode number in the text with a valid GS1 check digit (PZNs,
    phone and lot numbers are ignored)."""
    found = bb.extract_valid_gtins(str(text or ""))
    return found[0] if found else ""


def _camera_barcode(value: Any) -> str:
    """Barcode the camera component decoded in the browser, if it is valid."""
    if not isinstance(value, dict):
        return ""
    digits = re.sub(r"\D", "", str(value.get("barcode", "") or ""))
    return digits if bb.gtin_is_valid(digits) else ""


def _research_barcode_label(barcode: str) -> str:
    """Retrieve a supplement label for an EAN/UPC barcode via trusted product
    databases (OpenFoodFacts + secondary lookups only).

    We deliberately do NOT ask the LLM to guess a product from a bare barcode
    number: language models have no reliable barcode→product mapping and will
    confidently hallucinate an unrelated label (e.g. returning a generic
    multivitamin — whose vitamin K then maps to parsley — for a turmeric
    product). When the databases don't know the code we return "" so the caller
    can ask the user to photograph the nutrition table instead.
    """
    barcode = re.sub(r"\D", "", str(barcode or ""))
    if not bb.gtin_is_valid(barcode):
        return ""
    st.session_state["swipe_barcode_throttled"] = False
    if not _consume_barcode_quota():
        st.session_state["swipe_barcode_throttled"] = True
        return ""
    try:
        text, _name, _provider, _reason = bb.extract_supplement_text_from_barcode(barcode)
        if text and text.strip():
            return text.strip()
    except Exception:
        pass
    return ""


def _on_diet_profile_change() -> None:
    """Persist the dietary filter into a plain (non-widget) session key.

    Streamlit clears a widget's keyed state whenever that widget isn't rendered
    on a run. Runs that stop early (st.rerun() before the dietary pills render)
    garbage-collected the radio's own key and reset the filter to "No
    restriction". Mirroring the choice into `swipe_diet_profile_id` (never used
    as a widget key) keeps it across swipes.
    """
    # Tapping the selected chip again clears it (st.pills returns None): treat
    # that as "No restriction".
    st.session_state["swipe_diet_profile_id"] = str(
        st.session_state.get("swipe_diet_pills", "none") or "none"
    )


def _on_pregnancy_change() -> None:
    """Mirror the pregnancy toggle into `swipe_pregnant` (see _on_diet_profile_change)."""
    st.session_state["swipe_pregnant"] = bool(st.session_state.get("swipe_pregnant_toggle", False))


def _render_dietary_pills() -> None:
    ordered_ids, profile_by_id = _dietary_profile_lookup()
    if not ordered_ids:
        return
    selected_id = bb.normalize_lookup_key(str(st.session_state.get("swipe_diet_profile_id", "none") or "none"))
    if selected_id not in profile_by_id:
        selected_id = "none" if "none" in profile_by_id else ordered_ids[0]
        st.session_state["swipe_diet_profile_id"] = selected_id

    st.markdown("<div class='diet-strip-label'>Dietary filter</div>", unsafe_allow_html=True)
    label_for = lambda pid: str(profile_by_id.get(pid, {}).get("label", pid)).strip() or pid  # noqa: E731
    if hasattr(st, "pills"):
        # Native chips wrap onto several lines on a phone; the old horizontal
        # radio squeezed every label into a one-letter-wide column.
        # The key is the chips' identity, so `default` only seeds them when they
        # have no state yet. Passing it while the state is set (e.g. by Resume)
        # makes Streamlit log a default-vs-state warning.
        st.pills(
            "Dietary filter",
            options=ordered_ids,
            selection_mode="single",
            default=None if "swipe_diet_pills" in st.session_state else selected_id,
            key="swipe_diet_pills",
            on_change=_on_diet_profile_change,
            label_visibility="collapsed",
            format_func=label_for,
        )
    else:  # pragma: no cover - Streamlit < 1.40
        st.selectbox(
            "Dietary filter",
            options=ordered_ids,
            index=ordered_ids.index(selected_id),
            key="swipe_diet_pills",
            on_change=_on_diet_profile_change,
            label_visibility="collapsed",
            format_func=label_for,
        )
    st.toggle(
        "🤰 Pregnant or breastfeeding",
        # As with the chips: no value while the key has state (set by Resume),
        # which would log a default-vs-state warning.
        value=False if "swipe_pregnant_toggle" in st.session_state else _pregnancy_mode(),
        key="swipe_pregnant_toggle",
        on_change=_on_pregnancy_change,
        help=(
            "Hides liver and other organ meats, marks nutrients usually kept as a supplement "
            "in pregnancy and adds food-safety rules to the meal plan."
        ),
    )


def _ai_service_problem(error: str | None = None) -> str:
    """A sentence for a failure that retrying cannot fix (the app's connection to the AI service), else "".
    Visitors get plain words; the details are in ?debug=1 -> Diagnostics for the owner.
    `error`: the failure text noted by a background job (last_call_error() is per thread); default: this thread's."""
    if error is None:
        error = bb.last_call_error()
    if re.search(
        r"missing configuration|not a known model key|HTTP (?:401|403|404)\b|unauthori[sz]ed|forbidden", error, re.IGNORECASE
    ):
        return "The AI helper is unavailable right now. "
    return ""


def _ai_retry_note(default: str, error: str | None = None) -> str:
    """`default` ("… — please try again."), or the plain note when retrying cannot help."""
    problem = _ai_service_problem(error)
    return f"{problem}Please try again later." if problem else default


def _ai_unavailable_message(what: str) -> str:
    """The analysis error when an AI step failed (not the user's input)."""
    problem = _ai_service_problem() if what in {"photo", "link"} else ""
    if problem:
        return f"{problem}Paste the nutrition table as text instead (🔗 Paste — that works without AI)."
    if what == "quota":
        return (
            f"{_QUOTA_MESSAGE} Until then, paste the nutrition table as text (🔗 Paste — that "
            "works without AI)."
        )
    if what == "front":
        return (
            "We read the photo, but it doesn't show the nutrition table (the front of the pack "
            "has no doses). Photograph the Supplement Facts / nutrition table, scan the barcode, "
            "or paste the table as text (🔗 Paste — that works without AI)."
        )
    if what == "page":
        return (
            "That page couldn't be read: it may block automated access or hold no supplement "
            "facts table. Paste the nutrition table as text instead (🔗 Paste — that works "
            "without AI), or try another link."
        )
    if what == "link":
        return (
            "That product page couldn't be read: the AI page reader didn't respond. "
            "Paste the nutrition table as text instead (🔗 Paste — that works without AI), "
            "or try again in a few minutes."
        )
    return (
        "Your photo couldn't be read: the AI label reader returned no text (it may be busy, "
        "or the photo too blurry). Try a sharp, straight photo of the nutrition table, paste "
        "the table as text (🔗 Paste — that works without AI), or try again in a few minutes."
    )


def _run_pending_analysis() -> None:
    req = dict(st.session_state.get("swipe_pending_request") or {})

    # First pass right after the dialog closes: paint the progress skeleton fast
    # and immediately rerun. This guarantees the Analyze dialog is fully gone and
    # the user sees the progress card BEFORE the slow OCR/analysis work begins,
    # instead of staring at a frozen dialog while the request runs.
    if not bool(st.session_state.get("swipe_analysis_kicked", False)):
        st.session_state["swipe_analysis_kicked"] = True
        st.session_state["swipe_progress_pct"] = 3
        with st.container(border=True):
            st.markdown("<div class='chip'>Analyzing…</div>", unsafe_allow_html=True)
            st.markdown(
                """
                <div class='analyze-loading-wrap'>
                    <div class='analyze-loading-arrow'>↻</div>
                    <div class='analyze-loading-title'>Finding Whole Food Alternatives</div>
                    <div class='analyze-loading-sub'>Starting analysis…</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.progress(3)
            st.markdown("**3%**")
        st.rerun()

    with st.container(border=True):
        chip = st.empty()
        chip.markdown("<div class='chip'>Analyzing…</div>", unsafe_allow_html=True)
        loading_block = st.empty()
        progress_bar = st.progress(int(st.session_state.get("swipe_progress_pct", 0) or 0))
        progress_text = st.empty()

        def _set_progress(pct: int, sub: str) -> None:
            pct_clamped = max(0, min(100, int(pct)))
            st.session_state["swipe_progress_pct"] = pct_clamped
            loading_block.markdown(
                f"""
                <div class='analyze-loading-wrap'>
                    <div class='analyze-loading-arrow'>↻</div>
                    <div class='analyze-loading-title'>Finding Whole Food Alternatives</div>
                    <div class='analyze-loading-sub'>{sub}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            progress_bar.progress(pct_clamped)
            progress_text.markdown(f"**{pct_clamped}%**")

        def _abort(message: str) -> None:
            st.session_state["swipe_is_analyzing"] = False
            st.session_state["swipe_pending_request"] = None
            st.session_state["swipe_analysis_kicked"] = False
            st.session_state["swipe_progress_pct"] = 0
            # Allow retrying the exact same input (it was deduplicated by signature).
            st.session_state["swipe_last_auto_signature"] = ""
            chip.empty()  # no "Analyzing…" pill above the error
            loading_block.empty()
            progress_bar.empty()
            progress_text.empty()
            # What the user pasted comes back when the dialog is reopened (nobody retypes a label).
            st.session_state["swipe_paste_draft"] = str(req.get("manual", "") or "")[:_MAX_LABEL_CHARS]
            st.error(message)
            _request_scroll_top()  # the error is at the top; the Analyze button far below

        _set_progress(6, "Preparing your analysis…")
        text_parts: list[str] = []
        # Where the doses came from; "ai_research" is surfaced on every card so the
        # user knows the values were looked up, not read from their own photo.
        label_source: dict[str, str] = {"kind": "input", "url": ""}
        if str(req.get("manual", "") or "").strip() == _SAMPLE_LABEL_TEXT.strip():
            label_source = {"kind": "sample", "url": ""}  # not the visitor's product: marked, and not saved as a scan
        # Set when an AI step (photo reading, product research, page reading)
        # failed: an empty result is then the AI's fault, not the user's input.
        ai_failed = ""
        # Why a product link gave nothing when the AI isn't to blame (shown as a
        # warning next to other input, or as the one message when it is all there is).
        url_error = ""
        # Why a typed barcode gave nothing: the ONE message when it is all there is, else a toast next to other input.
        barcode_note = ""
        # True once label text came from a photo (not from a barcode database, pasted text or a link).
        photo_read = False

        with st.spinner("Extracting and parsing supplement info…"):
            # A barcode the phone decoded in the browser is looked up first; if
            # the product database has its nutrients, the photo isn't OCR'd.
            camera_barcode = str(req.get("camera_barcode", "") or "")
            if camera_barcode:
                _set_progress(20, f"Looking up barcode {camera_barcode}…")
                researched = _research_barcode_label(camera_barcode)
                if researched and bb.extraction_gate_report(researched).get("passed"):
                    text_parts.append(researched)
                    label_source = {"kind": "barcode_db", "url": f"https://world.openfoodfacts.org/product/{camera_barcode}"}
                    req["camera_bytes"] = b""
            for label, key, pct in (("uploaded image", "upload_bytes", 26), ("camera image", "camera_bytes", 42)):
                img = req.get(key)
                if isinstance(img, (bytes, bytearray)) and img:
                    _set_progress(pct, f"Reading the {label} with AI — this usually takes 5–15 seconds…")
                    try:
                        ocr_text, _route = _extract_image_text_best_effort(bytes(img))
                        if ocr_text.strip():
                            text_parts.append(ocr_text)
                            photo_read = True
                        else:
                            ai_failed = "photo"
                        # Barcode fallback: if the label text is not strong, try to
                        # read an EAN from the OCR text and research the product.
                        if not bb.extraction_gate_report("\n".join(text_parts)).get("passed"):
                            ean = _extract_ean_from_text(ocr_text)
                            if ean:
                                _set_progress(min(96, pct + 8), f"Researching barcode {ean}…")
                                researched = _research_barcode_label(ean)
                                if researched:
                                    text_parts.append(researched)
                        # A product shot without a readable facts panel: nothing to analyze. Looking the
                        # product up by name needs web access, which a plain LLM conversation doesn't have
                        # (a model answering from memory invents doses), so ask for the table instead.
                        if (
                            ocr_text.strip()
                            and not _is_ocr_refusal(ocr_text)
                            and _ocr_has_product_words(ocr_text)
                            and not bb.extraction_gate_report("\n".join(text_parts)).get("passed")
                        ):
                            ai_failed = "front"
                    except Exception as exc:
                        ai_failed = "quota" if str(exc) == _QUOTA_MESSAGE else "photo"

            manual = str(req.get("manual", "") or "").strip()
            if manual:
                digits = re.sub(r"\D", "", manual)
                if re.fullmatch(r"[\d\s\-]{8,18}", manual) and 8 <= len(digits) <= 14 and not bb.gtin_is_valid(digits):
                    barcode_note = (
                        "That number isn't a valid barcode (its check digit doesn't match). "
                        "Please re-type it, or paste the label text instead."
                    )
                elif re.fullmatch(r"[\d\s\-]{8,18}", manual) and 8 <= len(digits) <= 14:
                    _set_progress(56, "Researching barcode…")
                    researched = _research_barcode_label(manual)
                    if researched:
                        text_parts.append(researched)
                    elif st.session_state.get("swipe_barcode_throttled"):
                        barcode_note = (
                            "That's a lot of barcode look-ups for now — please try again in a little while, "
                            "or snap a photo of the nutrition table / paste the table as text."
                        )
                    else:
                        barcode_note = (
                            "We couldn't find that barcode in the product databases. "
                            "Snap a photo of the Supplement Facts / nutrition table instead, "
                            "or paste the table as text."
                        )
                elif re.match(r"https?://", manual, re.I):
                    _set_progress(56, "Fetching product page…")
                    bb.reset_call_error()
                    quota_refused: list[bool] = []

                    def _llm_allowed() -> bool:
                        allowed = _consume_llm_quota("generate")
                        if not allowed:
                            quota_refused.append(True)
                        return allowed

                    try:
                        url_text = _cached_extract_from_url(manual, _llm_allowed)
                        if url_text.strip():
                            text_parts.append(url_text)
                    except Exception as exc:
                        if quota_refused:  # the page needs the AI and this session's allowance is used up
                            ai_failed = "quota"
                        elif bb.last_call_error():  # the page was fetched; its AI read failed
                            ai_failed = "link"
                        else:
                            url_error = str(exc)
                else:
                    _set_progress(58, "Processing text input…")
                    text_parts.append(manual)

            combined = "\n\n".join([x for x in text_parts if str(x).strip()]).strip()
            if not combined:
                _abort(
                    _ai_unavailable_message(ai_failed or ("page" if url_error else ""))
                    if (ai_failed or url_error)
                    else barcode_note
                    or "Nothing to analyze yet. Add a photo, a barcode, a product link or the supplement facts text."
                )
                return
            if barcode_note:
                st.toast(f"{barcode_note.split('. ')[0].rstrip('.')} — using the rest of your input.", icon="⚠️")
            if url_error:
                # A toast: it survives the st.rerun() that opens the first card (a warning wouldn't).
                st.toast(f"The link couldn't be read ({url_error}) — using the rest of your input.", icon="⚠️")

            if "[unreadable]" in combined.lower():
                # The model marked parts it could not read (never guessed): a toast survives the st.rerun() below.
                st.toast("Part of the label was unreadable — some values may be missing. Retake a sharper photo to be sure.", icon="⚠️")
            _set_progress(72, "Parsing micronutrients…")
            components = bb.parse_components(combined)
            if not components:
                _abort(_ai_unavailable_message(ai_failed) if ai_failed else (
                    "We couldn't find any vitamins or minerals in that. Paste the 'Supplement Facts' lines, "
                    "for example 'Vitamin D3 20 µg', or take a clearer photo of the table."
                ))
                return

            # Keep only scientifically recognised micronutrients (vitamins +
            # minerals, plus choline / omega-3 unless _STRICT_MICRONUTRIENTS_ONLY).
            # This drops macronutrients (protein/fat/carbs/sugar/calories),
            # fillers and label metadata so the user only swipes real nutrients.
            components = _apply_label_context(_filter_to_micronutrients(components), combined)
            if (
                components
                and photo_read
                and not manual
                and label_source.get("kind") == "input"
                and all(c.get("dose_value") is None for c in components)
                and not bb.extraction_gate_report(combined).get("passed")
            ):
                # A product shot that names nutrients but gives no doses (the front of the pack): swipe cards without a single
                # dose would be useless, and looking the product up needs web access we don't have.
                _abort(_ai_unavailable_message("front"))
                return
            if not components and ai_failed:
                _abort(_ai_unavailable_message(ai_failed))
                return
            if not components:
                _abort(
                    "No micronutrients found. The label's non-nutrient lines "
                    "(protein, fats, carbs, fillers, etc.) were skipped — try a "
                    "clearer photo of the Supplement Facts panel."
                )
                return

            _set_progress(86, "Ranking whole-food alternatives from the USDA database…")
            details: list[dict[str, Any]] = []

        _set_progress(100, "Opening your first card…")

        st.session_state["swipe_cards"] = _build_swipe_cards(components, details)
        st.session_state["swipe_analysis_text"] = combined
        st.session_state["swipe_label_source"] = label_source
        st.session_state["swipe_components"] = components
        st.session_state["swipe_decisions"] = {}
        st.session_state["swipe_rag_chats"] = {}
        st.session_state["swipe_index"] = 0
        st.session_state["swipe_is_analyzing"] = False
        st.session_state["swipe_pending_request"] = None
        st.session_state["swipe_analysis_kicked"] = False
        st.session_state["swipe_progress_pct"] = 0
        _request_scroll_top()  # the first card renders at the top
        st.rerun()


def _request_scroll_top() -> None:
    """Show the top of the page on the next render (a scan started, finished
    or failed): the card or the error renders at the top, while the buttons
    that start a scan sit far below on a phone."""
    st.session_state["_suppswipe_scroll_top"] = True


def _scroll_to_top() -> None:
    """Scroll Streamlit's main container (and the window) to the top once."""
    nonce = int(st.session_state.get("_suppswipe_scroll_nonce", 0) or 0) + 1
    st.session_state["_suppswipe_scroll_nonce"] = nonce
    components.html(
        "<script>/* scroll %d */(function(){try{var d=window.parent.document;"
        "['[data-testid=stMain]','[data-testid=stAppViewContainer]','section.main'].forEach(function(s){"
        "var el=d.querySelector(s);if(el){el.scrollTo({top:0});}});window.parent.scrollTo(0,0);}catch(e){}})();</script>"
        % nonce,
        height=0,
    )


def _stage_analysis_from_inputs(
    upload_bytes: bytes, camera_bytes: bytes, manual_text: str, camera_barcode: str = ""
) -> bool:
    """Stage a pending analysis if new input is present. Returns True if staged."""
    if not (upload_bytes or camera_bytes or manual_text):
        return False
    sig = _analysis_input_signature(upload_bytes, camera_bytes, manual_text)
    if sig == str(st.session_state.get("swipe_last_auto_signature", "") or ""):
        return False
    st.session_state["swipe_pending_request"] = {
        "upload_bytes": upload_bytes,
        "camera_bytes": camera_bytes,
        "manual": manual_text,
        "camera_barcode": camera_barcode,
    }
    st.session_state["swipe_last_auto_signature"] = sig
    st.session_state["swipe_progress_pct"] = 1
    st.session_state["swipe_analysis_kicked"] = False
    st.session_state["swipe_is_analyzing"] = True
    _request_scroll_top()
    return True


# The dialogs stay open across reruns until they are answered or dismissed:
# their session flag is cleared by the dialog's own buttons and by on_dismiss,
# not by the run that opens them. A stray extra rerun (e.g. a component iframe
# re-sending its value) would otherwise close a one-shot dialog right after
# "Start over" opened it.
def _close_analyze_dialog() -> None:
    st.session_state["swipe_open_analyze"] = False


def _close_restart_dialog() -> None:
    st.session_state["swipe_confirm_restart"] = False


@st.dialog("Analyze my supplement", on_dismiss=_close_analyze_dialog)
def _analyze_dialog() -> None:
    nonce = int(st.session_state.get("swipe_reset_nonce", 0))
    precheck_error = _blockbrain_ready_error()
    if precheck_error:
        st.info(_AI_OFF_NOTE)
    default_method = "🔗 Paste" if precheck_error else "📷 Camera"  # no point starting on a photo the AI cannot read
    method = st.segmented_control(
        "How would you like to add your supplement?",
        options=["📷 Camera", "🖼️ Upload", "🔗 Paste"],
        default=default_method,
        required=True,
        key=f"dlg_method_{nonce}",
        label_visibility="collapsed",
        width="stretch",
    ) or default_method
    st.caption("A photo of the nutrition table or barcode — or paste a link, a barcode number or the label text.")

    upload_bytes = b""
    camera_bytes = b""
    camera_barcode = ""
    manual_text = ""

    if "Camera" in method:
        # Custom back-camera component (getUserMedia facingMode 'environment').
        # Falls back to Streamlit's default camera if the component is unavailable.
        if _back_camera is not None:
            cam_value = _back_camera(key=f"dlg_backcam_{nonce}", default=None)
            camera_bytes = _decode_camera_image(cam_value)
            camera_barcode = _camera_barcode(cam_value)
        else:
            camera = st.camera_input(
                "Take a photo of the label or barcode",
                key=f"dlg_camera_{nonce}",
                label_visibility="collapsed",
            )
            camera_bytes = camera.getvalue() if camera is not None else b""
    elif "Upload" in method:
        upload = st.file_uploader(
            "Choose an image from your files or gallery",
            type=["png", "jpg", "jpeg", "webp"],
            key=f"dlg_upload_{nonce}",
            label_visibility="collapsed",
        )
        upload_bytes = upload.getvalue() if upload is not None else b""
    else:
        # A form, so typing (or tapping Cancel, which blurs the box) never starts
        # an analysis with half-typed text; only the Analyze button does.
        draft = str(st.session_state.pop("swipe_paste_draft", "") or "")  # what the user typed before a failed analysis
        if draft and f"dlg_manual_{nonce}" not in st.session_state:
            st.session_state[f"dlg_manual_{nonce}"] = draft
        with st.form(key=f"dlg_text_form_{nonce}", border=False):
            manual = st.text_area(
                "Paste a product URL, a barcode number, or the supplement facts text",
                height=120,
                key=f"dlg_manual_{nonce}",
                max_chars=_MAX_LABEL_CHARS,
                placeholder="e.g. https://… or 4006040000000 or 'Vitamin D3 20 µg, Zink 10 mg …'",
            )
            submitted = st.form_submit_button("Analyze", type="primary", width="stretch")
        manual_text = str(manual or "").strip()[:_MAX_LABEL_CHARS] if submitted else ""
        if submitted and not manual_text:
            st.warning("Paste a link, a barcode number or the label text first.")

    # Photos and links are read by the AI; pasted label text and barcodes are not (they must work on a fresh deploy,
    # before the Blockbrain settings exist).
    is_link = bool(re.match(r"https?://", manual_text, re.I))
    needs_ai = bool(upload_bytes or (camera_bytes and not camera_barcode)) or is_link  # a decoded barcode needs no AI
    if precheck_error and needs_ai:
        st.warning(_AI_OFF_LINK if is_link else _AI_OFF_PHOTO)
    if (not precheck_error or not needs_ai) and _stage_analysis_from_inputs(
        upload_bytes, camera_bytes, manual_text, camera_barcode
    ):
        # Close the dialog and let the main app run the analysis immediately.
        _close_analyze_dialog()
        st.rerun(scope="app")

    if st.button("Cancel", width="stretch", key=f"dlg_cancel_{nonce}"):
        _close_analyze_dialog()
        st.rerun(scope="app")


@st.dialog("Start over?", on_dismiss=_close_restart_dialog)
def _confirm_restart_dialog() -> None:
    st.write(
        "You've already started swiping. Analyzing a new supplement will clear your "
        "current cards and decisions."
    )
    col_cancel, col_ok = st.columns(2)
    with col_cancel:
        if st.button("Cancel", width="stretch", key="swipe_restart_cancel"):
            _close_restart_dialog()
            st.rerun(scope="app")
    with col_ok:
        if st.button("Start over", type="primary", width="stretch", key="swipe_restart_confirm"):
            _reset_swipe_state()  # also closes this dialog (its flag is a swipe_ key)
            _forget_saved_scan()
            st.session_state["swipe_open_analyze"] = True
            st.rerun(scope="app")


def _on_results_screen() -> bool:
    """True once every card is decided and the plan dashboard is showing."""
    cards = st.session_state.get("swipe_cards") or []
    return bool(cards) and int(st.session_state.get("swipe_index", 0) or 0) >= len(cards)


def _render_results_settings() -> None:
    """The diet filter and pregnancy toggle, folded away under the plan.

    Changing them still re-checks the swaps and the plan updates above."""
    diet = _active_diet_label(_selected_dietary_profile()) or "no restriction"
    label = f"Diet: {diet}" + (" · pregnant / breastfeeding" if _pregnancy_mode() else "")
    with st.expander(label, icon="⚙️", key="swipe_results_settings"):
        _render_dietary_pills()


def _render_analyze_bar(results: bool = False, button: bool = True) -> None:
    if button:
        _render_analyze_button(results=results, primary=results)
    _render_scan_history_popover()
    _render_privacy_popover()


def _render_analyze_button(results: bool = False, primary: bool = False) -> None:
    if results:
        label = "📸 Scan another supplement"
    else:
        label = "📸 Analyze my supplement"
    kind = "primary" if primary else "secondary"
    if st.button(label, type=kind, width="stretch", key="swipe_analyze_btn"):
        if results:
            # A finished plan is already in Recent scans: nothing to lose.
            _reset_swipe_state()
            _forget_saved_scan()
            st.session_state["swipe_open_analyze"] = True
        elif _selected_session_in_progress():
            st.session_state["swipe_confirm_restart"] = True
        else:
            st.session_state["swipe_open_analyze"] = True
        st.rerun()


def _render_privacy_popover() -> None:
    """Plain-language notice of what the app does with a visitor's input."""
    with st.popover("🔒 About & privacy", width="stretch"):
        st.markdown(
            "**SuppSwipe** gives general nutrition information — it is not medical advice. "
            "Talk to a doctor or pharmacist before stopping a supplement you were prescribed, "
            "or if you are pregnant, ill or take medication.\n\n"
            "**What happens to your input**\n"
            "- Label photos, pasted text or links and *Ask AI* questions are sent to "
            "[Blockbrain](https://theblockbrain.ai), the AI service that reads labels and writes answers. "
            "Don't include personal details.\n"
            "- Meal plans and the benefit comparison send your chosen foods, your dietary filter and the "
            "pregnancy setting to Blockbrain. A default meal plan is prepared in the background when your "
            "results open — but not with the pregnancy setting or a religious or health-related filter "
            "(Halal, Kosher, gluten-, lactose- or nut-free, low-sodium): those are sent only when you tap "
            "*Generate meals*.\n"
            "- Barcode numbers are looked up in public product databases and web search "
            "(Open Food Facts, UPCitemdb, DuckDuckGo). Pasted links are fetched by the app's server.\n"
            "- Your scan history and the scan you're working on (with your dietary filter and pregnancy "
            "setting) are stored only in this browser, so you can resume after a refresh; *Clear history* "
            "deletes both, *Start over* deletes the scan in progress.\n"
            "- There are no accounts. Label text and generated answers may be kept in the server's "
            "memory for a few hours so repeat requests are faster.\n"
            "- The app runs on Streamlit Community Cloud, which has its own privacy notice.\n\n"
            "**Sources:** food data from USDA FoodData Central; upper limits from EFSA and NIH ODS."
        )
        st.caption(f"Build {BUILD_TAG}")


def _render_label_source_notice() -> None:
    """Warn when the doses were researched online by AI instead of read from the
    user's own photo (front-of-pack photos without a readable facts panel)."""
    source = st.session_state.get("swipe_label_source") or {}
    if str(source.get("kind", "") or "") == "sample":
        st.caption("🧪 Sample label — not your product. Scan your own supplement any time.")
        return
    if str(source.get("kind", "") or "") != "ai_research":
        return
    url = str(source.get("url", "") or "")
    where = f" ([source]({url}))" if url else ""
    st.caption(
        f"⚠️ These doses were looked up online by AI from the product name{where}, "
        "not read from your photo — check them against your pack."
    )


# A typical EU multivitamin label (in the app's own language), for "Try it with a sample label".
_SAMPLE_LABEL_TEXT = """Nutrition information per daily dose (1 tablet) %NRV*
Vitamin C 80 mg 100%
Vitamin D3 20 µg (800 IU) 400%
Vitamin B12 2.5 µg 100%
Folic acid 200 µg 100%
Magnesium 56 mg 15%
Zinc 10 mg 100%
Selenium 55 µg 100%
*NRV = Nutrient Reference Value"""


# "🚩 Report a problem with this card": one structured warning line in the
# blockbrain log per tap, for review. Only what the card shows: nutrient, dose,
# the name-and-dose part of the label line it was read from, the chosen food
# and the dietary filter — no free text and nothing personal (the rest of the
# label line and the pregnancy toggle are not logged).
_REPORT_LABEL_LINE_MAX = 160


# Tokens that may follow the dose in the reported span: more numbers, units,
# brackets and the %NRV ("(800 I.E.) 400%"), nothing with other words.
_REPORT_TAIL_TOKEN_RE = re.compile(
    r"^(?:[\d.,()\[\]%*:;/+-]|µg|μg|ug|mcg|mg|g|iu|i\.e\.|ie|nrv|nrv\*|dv|rm)+$", re.IGNORECASE
)


def _label_nutrient_span(label_line: str, nutrient_key: str = "") -> str:
    """The part of a label line from the nutrient's name through its dose (and a
    following "(800 I.E.) 400%"), e.g. "Vitamin D3 20 µg" out of "Vitamin D3 20
    µg für Max Mustermann, Tel ..." — never the free text around it; "" when
    no name-and-dose span is found."""
    raw = re.sub(r"\s+", " ", str(label_line or "")).strip()
    tokens = list(re.finditer(r"\S+", raw))
    start = None
    for i in range(len(tokens)):
        window = bb._fold_label_text(raw[tokens[i].start():tokens[min(len(tokens), i + 4) - 1].end()])
        m = bb._NUTRIENT_ALIAS_RE.match(window)
        if not m:
            continue
        key = bb._NUTRIENT_ALIAS_INDEX.get(re.sub(r"\s+", " ", m.group(0)), ("",))[0]
        if start is None:
            start = i
        if not nutrient_key or key == nutrient_key:
            start = i
            break
    if start is None:
        return ""
    end = None
    for j in range(start + 1, len(tokens) + 1):
        if bb._LABEL_DOSE_RE.search(bb._fold_label_text(raw[tokens[start].start():tokens[j - 1].end()])):
            end = j
            break
    if end is None:
        return ""
    while end < len(tokens) and _REPORT_TAIL_TOKEN_RE.match(tokens[end].group(0)):
        end += 1
    return raw[tokens[start].start():tokens[end - 1].end()]


def _card_label_line(card: dict[str, Any]) -> str:
    """The name-and-dose part of the supplement-label line a card's dose was
    read from (_label_nutrient_span), or ""."""
    key = str(card.get("nutrient_key", "") or "")
    try:
        rows = list(st.session_state.get("swipe_components", []) or [])
    except Exception:
        rows = []
    for row in rows:
        if not isinstance(row, dict) or _component_nutrient_key(row) != key:
            continue
        if row.get("dose_value") == card.get("dose_value") and row.get("label_line"):
            return _label_nutrient_span(str(row["label_line"]), key)[:_REPORT_LABEL_LINE_MAX]
    return ""


def _card_report_payload(
    card: dict[str, Any], selected_food: dict[str, Any] | None, profile: dict[str, Any] | None
) -> dict[str, str]:
    return {
        "nutrient": _nutrient_title(card.get("component")),
        "nutrient_key": str(card.get("nutrient_key", "") or ""),
        "dose": str(card.get("dose_label", "") or ""),
        "label_line": _card_label_line(card),
        "food": str((selected_food or {}).get("food_description", "") or ""),
        "diet": str((profile or {}).get("label", "") or "No restriction"),
    }


def _report_card_problem(
    card: dict[str, Any], selected_food: dict[str, Any] | None, profile: dict[str, Any] | None
) -> dict[str, str]:
    """Log one structured "card report" warning line and return its payload."""
    payload = _card_report_payload(card, selected_food, profile)
    bb.logger.warning("SuppSwipe card report: %s", json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return payload


# --- Swipe handling: one script run per swipe ---------------------------------
# The card component keeps ONE key per scan (per reset nonce), so Streamlit
# reuses its iframe and only sends the next card's props. A swipe arrives as
# that key's value ({"dir", "id", "card", "index"}) at the start of the run it
# triggered; _render_card applies it there and draws the next card in the same
# run, without st.rerun(). The value stays in session state afterwards, so
# `swipe_last_swipe_id` makes sure each swipe is applied exactly once.


def _swipe_component_key(nonce: Any) -> str:
    try:
        return f"tinder_{int(nonce or 0)}"
    except Exception:
        return "tinder_0"


def _decision_record(card: dict[str, Any], index: int, decision: str, selected_food: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "component_key": str(card.get("component_key", "") or ""),
        "component": card.get("component", ""),
        "dose_label": card.get("dose_label", ""),
        "dose_value": card.get("dose_value"),
        "dose_max": card.get("dose_max"),
        "dose_unit": card.get("dose_unit", ""),
        "form": str(card.get("form", "") or ""),
        "decision": decision,
        "selected_food": selected_food,
        "card_index": index,
    }


def _shown_food(state: Any, index: int, component_key: str) -> dict[str, Any] | None:
    """The whole food selected on card `index` when it was swiped: the dropdown's
    current value (sent with the swipe), else the food the card was drawn with."""
    view = state.get("swipe_card_view") or {}
    if view.get("index") != index or view.get("component_key") != component_key:
        return None
    options = view.get("options") or {}
    select_key = str(view.get("select_key", "") or "")
    label = state.get(select_key) if select_key else None
    if label in options:
        return options[label]
    return view.get("selected")


def _apply_card_swipe(state: Any, value: Any) -> bool:
    """Apply a keep / replace / back reported by the card component to `state`
    (st.session_state or a plain dict). True when it changed the screen."""
    if not isinstance(value, dict):
        return False
    swipe_id = str(value.get("id", "") or "")
    if not swipe_id or swipe_id == str(state.get("swipe_last_swipe_id", "") or ""):
        return False
    state["swipe_last_swipe_id"] = swipe_id
    cards = list(state.get("swipe_cards") or [])
    index = int(state.get("swipe_index", 0) or 0)
    if not 0 <= index < len(cards):
        return False
    card = cards[index]
    component_key = str(card.get("component_key", "") or "")
    # A value made on another card (stale) never lands on this one.
    if "card" in value and str(value.get("card") or "") != component_key:
        return False
    if "index" in value and str(value.get("index")) != str(index):
        return False
    editing = bool(state.get("swipe_edit_return", False))
    direction = value.get("dir")
    if direction == "back":
        # Opened from the results: Back returns there unchanged.
        if editing:
            state["swipe_edit_return"] = False
            state["swipe_index"] = len(cards)
            return True
        if index > 0:
            state["swipe_index"] = index - 1
            return True
        return False
    if direction not in ("left", "right"):
        return False
    decision = "keep" if direction == "left" else "replace"
    selected_food = _shown_food(state, index, component_key)
    # Can't replace with a whole food that doesn't exist (or a soft-blocked one).
    blocked = str((state.get("swipe_card_view") or {}).get("replace_block", "") or "")
    if decision == "replace" and (selected_food is None or blocked):
        return False
    decisions = dict(state.get("swipe_decisions") or {})
    decisions[component_key] = _decision_record(card, index, decision, selected_food)
    state["swipe_decisions"] = decisions
    # Edit mode goes straight back to the results instead of the next card.
    state["swipe_index"] = len(cards) if editing else index + 1
    state["swipe_edit_return"] = False
    return True


def _open_card(index: int, edit: bool = False) -> None:
    """Button callback: show card `index`; `edit` (from the results screen)
    returns to the results after the next keep / replace / back."""
    st.session_state["swipe_index"] = int(index)
    st.session_state["swipe_edit_return"] = bool(edit)


def _restore_previous_food(
    state: Any, select_key: str, option_labels: list[str], foods: list[dict[str, Any]], decision: dict[str, Any] | None
) -> None:
    """Re-select the food chosen earlier (matched by USDA description) when a
    card is reopened, instead of resetting the dropdown to the first food. Only
    seeds a dropdown that isn't on screen yet, so the user's own change wins."""
    if not isinstance(decision, dict) or select_key in state:
        return
    wanted = str((decision.get("selected_food") or {}).get("food_description", "") or "")
    if not wanted:
        return
    for label, food in zip(option_labels, foods):
        if str(food.get("food_description", "") or "") == wanted:
            state[select_key] = label
            return


# --- Dietary filter re-check ----------------------------------------------------


def _active_diet_label(profile: dict[str, Any] | None) -> str:
    """The filter's label ("Vegan"), or "" for "No restriction"."""
    label = str((profile or {}).get("label", "") or "").strip()
    pid = bb.normalize_lookup_key(str((profile or {}).get("id", "") or ""))
    if not label or pid == "none" or label.lower() in ("no restriction", "none"):
        return ""
    return label


def _food_fits_diet(food: dict[str, Any] | None, profile: dict[str, Any] | None) -> bool:
    """True when `food` passes the dietary filter (the same rules the dropdown uses)."""
    if not isinstance(food, dict) or not str(food.get("food_description", "") or "").strip():
        return True
    try:
        return bool(bb.apply_food_filters([food], profile, use_llm_adjudication=False))
    except Exception:
        return True


def _split_replacements_by_diet(
    replace_items: list[dict[str, Any]], profile: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(fitting, misfitting) "replace" decisions. A swap chosen before the filter
    changed (e.g. salmon, then "Vegan") no longer fits and is left out of the meal
    plan, grocery cost and share text until the user picks another food."""
    fits: list[dict[str, Any]] = []
    misfits: list[dict[str, Any]] = []
    for d in replace_items:
        (fits if _food_fits_diet(d.get("selected_food"), profile) else misfits).append(d)
    return fits, misfits


# --- Resume after refresh ---------------------------------------------------------
# The current scan is mirrored into the visitor's browser (localStorage, via the
# scan-history component): the label text, the decisions by card, the diet
# filter and the current card. Resuming re-runs the same deterministic parse and
# USDA ranking on the saved text (no LLM call) and re-applies the decisions.
_SAVED_SCAN_VERSION = 1
_SAVED_SCAN_MAX_AGE_S = 7 * 24 * 3600


def _scan_snapshot(state: Any, now: float | None = None) -> dict[str, Any] | None:
    import time as _time

    cards = list(state.get("swipe_cards") or [])
    text = str(state.get("swipe_analysis_text", "") or "")
    if not cards or not text.strip():
        return None
    decisions: dict[str, dict[str, str]] = {}
    for key, d in dict(state.get("swipe_decisions") or {}).items():
        if isinstance(d, dict) and d.get("decision") in ("keep", "replace"):
            decisions[str(key)] = {
                "decision": str(d["decision"]),
                "food_description": str((d.get("selected_food") or {}).get("food_description", "") or ""),
            }
    index = int(state.get("swipe_index", 0) or 0)
    if state.get("swipe_edit_return"):
        index = len(cards)  # mid-edit: resume on the results
    return {
        "v": _SAVED_SCAN_VERSION,
        "text": text,
        "decisions": decisions,
        "diet": str(state.get("swipe_diet_profile_id", "none") or "none"),
        # Kept on this device like the rest of the snapshot (never logged).
        "pregnant": bool(state.get("swipe_pregnant", False)),
        "index": max(0, min(len(cards), index)),
        "total": len(cards),
        "label_source": dict(state.get("swipe_label_source") or {}),
        "recorded": bool(state.get("swipe_history_recorded_sig")),
        "ts": float(_time.time() if now is None else now),
    }


def _resumable_scan(saved: Any, now: float | None = None) -> dict[str, Any] | None:
    """The saved scan if it is usable and less than 7 days old, else None."""
    import time as _time

    if not isinstance(saved, dict) or saved.get("v") != _SAVED_SCAN_VERSION:
        return None
    text = str(saved.get("text", "") or "")
    if not text.strip() or len(text) > _MAX_LABEL_CHARS:
        return None
    try:
        age = float(_time.time() if now is None else now) - float(saved.get("ts"))
        total = int(saved.get("total") or 0)
    except Exception:
        return None
    if total <= 0 or age > _SAVED_SCAN_MAX_AGE_S or age < -300:
        return None
    if isinstance(saved.get("decisions"), dict) and isinstance(saved.get("label_source"), dict):
        return saved
    # The fields the restore indexes, in the shape it expects (a damaged value would raise on every run).
    return {
        **saved,
        "decisions": saved.get("decisions") if isinstance(saved.get("decisions"), dict) else {},
        "label_source": saved.get("label_source") if isinstance(saved.get("label_source"), dict) else {},
    }


def _resume_label(saved: dict[str, Any]) -> str:
    total = int(saved.get("total") or 0)
    done = min(total, len(saved.get("decisions") or {}))
    return f"↩ Resume your last scan ({done} of {total} cards done)"


def _restore_scan(state: Any, saved: dict[str, Any]) -> bool:
    """Rebuild the cards from the saved label text and re-apply the decisions.
    A saved swap whose food is no longer among the card's foods is dropped (the
    card then simply asks again)."""
    text = str(saved.get("text", "") or "")
    try:
        components = _apply_label_context(_filter_to_micronutrients(bb.parse_components(text)), text)
        cards = _build_swipe_cards(components, []) if components else []
    except Exception:
        return False
    if not cards:
        return False
    saved_decisions = saved.get("decisions") or {}
    decisions: dict[str, dict[str, Any]] = {}
    for i, card in enumerate(cards):
        key = str(card.get("component_key", "") or "")
        sd = saved_decisions.get(key) if isinstance(saved_decisions, dict) else None
        if not isinstance(sd, dict) or sd.get("decision") not in ("keep", "replace"):
            continue
        wanted = str(sd.get("food_description", "") or "")
        food = next(
            (f for f in (card.get("foods") or []) if wanted and str(f.get("food_description", "") or "") == wanted),
            None,
        )
        if sd["decision"] == "replace" and food is None:
            continue
        decisions[key] = _decision_record(card, i, str(sd["decision"]), food)
    try:
        index = int(saved.get("index") or 0)
    except Exception:
        index = 0
    sig = _analysis_input_signature(b"", b"", text)
    diet = str(saved.get("diet", "none") or "none")
    state["swipe_cards"] = cards
    state["swipe_analysis_text"] = text
    state["swipe_components"] = components
    state["swipe_label_source"] = dict(saved.get("label_source") or {"kind": "input", "url": ""})
    state["swipe_decisions"] = decisions
    state["swipe_rag_chats"] = {}
    state["swipe_index"] = max(0, min(len(cards), index))
    state["swipe_edit_return"] = False
    state["swipe_diet_profile_id"] = diet
    state["swipe_diet_pills"] = diet  # keep the filter chips in step
    pregnant = saved.get("pregnant", False) is True  # strictly a JSON true: bool("false") would be True
    state["swipe_pregnant"] = pregnant
    state["swipe_pregnant_toggle"] = pregnant  # and the toggle
    state["swipe_last_auto_signature"] = sig
    if saved.get("recorded"):
        state["swipe_history_recorded_sig"] = sig  # already in the scan history
    return True


def _resume_saved_scan() -> None:
    """Button callback for "Resume your last scan"."""
    saved = _resumable_scan(st.session_state.get("_suppswipe_saved_scan"))
    if saved is None or not _restore_scan(st.session_state, saved):
        st.session_state["swipe_resume_failed"] = True


def _forget_saved_scan() -> None:
    """Drop the saved scan here and in the browser (Start over / Clear history).
    A scan still on screen (Clear history on the results, or mid-way through a
    later scan) is not saved again until it changes, else the same run would
    write it straight back and a reload would still offer to resume it."""
    st.session_state["_suppswipe_saved_scan"] = None
    st.session_state["_suppswipe_scan_clear"] = True
    st.session_state["_suppswipe_scan_forgotten"] = True
    snapshot = _scan_snapshot(st.session_state)
    if snapshot is None:
        st.session_state.pop("_suppswipe_scan_suppressed", None)
    else:
        st.session_state["_suppswipe_scan_suppressed"] = _scan_content(snapshot)


def _previous_choice_label(decision: dict[str, Any] | None) -> str:
    """Short label of an earlier choice for this card (shown after going back)."""
    if not decision:
        return ""
    if decision.get("decision") == "keep":
        return "kept the pill"
    food = _food_name(decision.get("selected_food"))
    return f"replaced with {food}" if food else "replaced"


def _render_card() -> None:
    cards: list[dict[str, Any]] = st.session_state.get("swipe_cards", [])
    nonce = int(st.session_state.get("swipe_reset_nonce", 0))
    swipe_key = _swipe_component_key(nonce)
    # The swipe that triggered this run (if any) is applied first, so the next
    # card renders in this same run (see "Swipe handling" above).
    if cards:
        _apply_card_swipe(st.session_state, st.session_state.get(swipe_key))
    index = int(st.session_state.get("swipe_index", 0))
    decisions: dict[str, dict[str, Any]] = st.session_state.get("swipe_decisions", {})

    if not cards:
        st.markdown(
            "<div class='hero'>"
            "<div class='hero-art' aria-hidden='true'>💊<span>→</span>🥦</div>"
            "<div class='hero-title' role='heading' aria-level='2'>Ditch the pill.<br>Eat the real thing.</div>"
            "<div class='hero-sub'>Scan your supplement and see which nutrients everyday foods can "
            "cover — with fibre, protein and co-nutrients the pill doesn't have — and which are "
            "worth keeping (e.g. vitamin D in winter, B12 on a vegan diet).</div>"
            "<div class='steps' role='list'>"
            "<div class='step' role='listitem'><span aria-hidden='true'>📸</span><b>Scan</b><small>your label</small></div>"
            "<div class='step' role='listitem'><span aria-hidden='true'>👆</span><b>Swipe</b><small>keep or replace</small></div>"
            "<div class='step' role='listitem'><span aria-hidden='true'>🥗</span><b>Eat</b><small>your food plan</small></div>"
            "</div></div>",
            unsafe_allow_html=True,
        )
        _render_analyze_button(primary=True)
        saved_scan = _resumable_scan(st.session_state.get("_suppswipe_saved_scan"))
        if saved_scan is not None:
            st.button(
                _resume_label(saved_scan),
                width="stretch",
                key="swipe_resume_scan",
                on_click=_resume_saved_scan,
            )
        if st.session_state.pop("swipe_resume_failed", False):
            st.caption("Couldn't restore your last scan — please scan the label again.")
        if st.button("✨ Try it with a sample label", width="stretch", key="swipe_try_sample"):
            if _stage_analysis_from_inputs(b"", b"", _SAMPLE_LABEL_TEXT):
                st.rerun()
        st.caption(
            "General information, not medical advice. Talk to a doctor before stopping a supplement "
            "you were prescribed, or if you are pregnant, ill or on medication."
        )
        return

    if index >= len(cards):
        _render_final_card(cards, decisions)
        return

    card = cards[index]
    component_key = str(card.get("component_key", "") or "")
    foods_raw: list[dict[str, Any]] = card.get("foods", []) if isinstance(card.get("foods", []), list) else []
    selected_profile = _selected_dietary_profile()
    # Filter the (possibly deep) pool by the dietary profile, then cap the
    # visible dropdown (highest concentration first) so the list stays manageable.
    foods = _card_food_options(foods_raw, selected_profile)
    # Self-heal: if there is nothing to show (the stored pool was empty, OR a
    # stale/shallow pool built by an older version got filtered away by the
    # dietary profile), re-fetch the deep pool live and retry. This applies the
    # resolver + deeper-pool fixes (e.g. Vitamin E) to already-built cards
    # without re-analysing the supplement.
    if not foods and component_key:
        deep_pool = _whole_food_pool(component_key)
        if deep_pool and deep_pool != foods_raw:
            card["foods"] = deep_pool
            foods_raw = deep_pool
            foods = _card_food_options(deep_pool, selected_profile)
    # Vegan / vegetarian B12: the fortified foods are the reliable option.
    foods = _with_fortified_options(foods, card, selected_profile)

    # No colour-only progress dots: the card itself says "Card i of N".
    _render_label_source_notice()

    # The swipe card and its controls (whole-food dropdown + Ask AI) share one
    # bordered container so they read as a single card.
    theme = _component_card_theme(str(card.get("component", "") or ""))
    selected_food = None
    replace_block = ""
    option_labels: list[str] = []
    select_key = f"swipe_food_select_{component_key}_{index}"
    match_dose_txt = ""
    rda_amount_txt = ""
    rda_label_txt = ""
    with st.container(border=True):
        # Computed here but shown INSIDE the swipe card (passed as `warn` below),
        # so only the dropdown / Ask AI / dietary filter sit below the card.
        card_form = str(card.get("form", "") or "")
        warn_text = _card_warning_text(
            component_key, card.get("dose_value"), str(card.get("dose_unit", "") or ""), card_form, selected_profile,
            dose_max=card.get("dose_max"),
        )
        stage = st.container()  # draggable swipe card sits at the top of this card

        # --- On-card controls ---
        if foods:
            option_labels = [_food_label(food) for food in foods]
            # Reopened card (Back / edit from the results): keep the earlier food.
            _restore_previous_food(st.session_state, select_key, option_labels, foods, decisions.get(component_key))
            selected_label = st.selectbox(
                "Prefer another food?",
                options=option_labels,
                # An everyday choice, not simply the richest food (no liver when
                # another food works, D3 fish before UV mushrooms, ...). A reopened
                # card already has its earlier food in session state, which wins;
                # index 0 then avoids Streamlit's default-vs-state warning.
                index=0 if select_key in st.session_state else _default_food_index(foods, card, selected_profile),
                key=f"swipe_food_select_{component_key}_{index}",
            )
            selected_food = foods[option_labels.index(selected_label)]
            full_name = str(selected_food.get("food_description", "") or "").strip()
            if full_name and full_name != _food_name(selected_food):
                st.caption(f"USDA: {full_name}")
            diet_name = _active_diet_label(selected_profile)
            if diet_name:
                st.caption(f"Filter: {diet_name}")

            # For the selected whole food, compute how much to eat to (a) match
            # the supplement dose and (b) reach the athlete daily target. These
            # are rendered INSIDE the swipe card (passed as props below).
            comp_name = str(card.get("component", "") or "")
            match_dose_txt = _portion_for_target(
                selected_food, card.get("dose_value"), str(card.get("dose_unit", "") or ""), comp_name, card_form
            )
            food_warning = _selected_food_warning(
                selected_food, card.get("dose_value"), str(card.get("dose_unit", "") or ""), comp_name, card_form
            )
            if food_warning:
                warn_text = f"{warn_text} {food_warning}".strip()
            if _pregnancy_mode():
                pregnancy_food = _pregnancy_food_note(selected_food)
                if pregnancy_food:
                    warn_text = f"{warn_text} {pregnancy_food}".strip()
            rda_entry = _rda_for_component(component_key)
            if rda_entry is not None:
                # The target is a food amount (e.g. folate in DFE), so it is
                # named by the RDA entry, not by the pill's form.
                rda_amount_txt = _portion_for_target(
                    selected_food, rda_entry["athlete"], str(rda_entry["unit"]), str(rda_entry["display"]), note=False
                )
                if rda_amount_txt:
                    rda_label_txt = _format_rda_target(rda_entry)
        else:
            diet_block = _replace_block_reason(card, None, selected_profile)
            if diet_block:
                # Vegan EPA/DHA: no whole food exists, so another filter is no answer.
                st.caption(f"{diet_block} Keeping the supplement is recommended.")
            elif foods_raw:
                prof = selected_profile or {}
                prof_label = str(prof.get("label", "") or "").strip()
                if prof_label and prof_label.lower() not in ("no restriction", "none"):
                    st.caption(
                        f"No whole-food alternatives fit the “{prof_label}” filter. "
                        "Switch the dietary filter below to see options."
                    )
                else:
                    st.caption("No whole-food alternatives available for this card.")
            else:
                st.caption("No whole-food alternatives found for this card.")

        # Portion guidance, the bioavailability tip and the deficiency warning all
        # render INSIDE the swipe card (passed as props below). Only the dropdown,
        # Ask AI and dietary filter stay below the card.
        bio_note = (
            _bioavailability_note(
                component_key, card_form, card.get("dose_value"), str(card.get("dose_unit", "") or ""), selected_profile
            )
            if selected_food is not None
            else ""
        )
        # Soft block (the card's diet warning says why): vegan / vegetarian B12
        # unless a B12-fortified food is picked, vegan iodine.
        replace_block = _replace_block_reason(card, selected_food, selected_profile) if selected_food is not None else ""
        extra_info = " ".join(
            line for line in (
                _unit_corrected_note(card),
                _card_extra_info(
                    component_key, card.get("dose_value"), str(card.get("dose_unit", "") or ""), card_form,
                    selected_profile, dose_max=card.get("dose_max"),
                ),
            ) if line
        )
        if extra_info:
            bio_note = f"{bio_note} {extra_info}".strip()

        _render_rag_chat_popup(card, component_key, index)
        if st.button(
            "🚩 Report a problem with this card",
            type="tertiary",
            key=f"swipe_report_{component_key}_{index}_{nonce}",
        ):
            _report_card_problem(card, selected_food, selected_profile)
            st.toast("Thanks — logged for review")

        food_label = _food_name(selected_food)
        # What this card offers, so the swipe (applied at the start of the next
        # run) records the food the user actually had selected.
        st.session_state["swipe_card_view"] = {
            "index": index,
            "component_key": component_key,
            "select_key": select_key,
            # reversed(): of two equal labels the first wins, as with option_labels.index().
            "options": dict(reversed(list(zip(option_labels, foods)))),
            "selected": selected_food,
            # A soft-blocked replace (e.g. vegan B12 without a fortified food)
            # must not be applied even if a stale "right" value arrives.
            "replace_block": replace_block,
        }
        with stage:
            tinder_swipe(
                name=_nutrient_title(card.get("component")) or "Unknown micronutrient",
                dose=str(card.get("dose_label", "Not available")),
                food=food_label,
                foodIcon=_whole_food_icon_from_food(selected_food) if selected_food is not None else "",
                matchDose=match_dose_txt,
                rdaAmount=rda_amount_txt,
                rdaLabel=rda_label_txt,
                warn=warn_text,
                bioNote=bio_note,
                index=index,
                total=len(cards),
                accent=theme["accent"],
                ink=theme["accent2"],
                bg=theme["bg"],
                canReplace=selected_food is not None and not replace_block,
                previous=_previous_choice_label(decisions.get(component_key)),
                editing=bool(st.session_state.get("swipe_edit_return", False)),
                cardId=component_key,
                # Changes after every handled swipe, so the card always gets
                # fresh props (and resets) even when it stays on the same card.
                ack=str(st.session_state.get("swipe_last_swipe_id", "") or ""),
                # Minimum frame height; the frame grows to the tallest card of the scan and never shrinks.
                height=440,
                key=swipe_key,
                default=None,
            )


# --- Results dashboard ---------------------------------------------------------
# The last screen is one plan instead of a stack of buttons and popovers: a hero
# summary (how much now comes from food, grams / kcal / cost per day), one
# heads-up box for every warning, and tabs for the plan, meals, shopping, Ask AI
# and sharing. Everything in "Plan", "Shopping" and the quick meal ideas is
# computed locally (USDA data, price table) so it shows instantly; only the
# personal meal plan, the benefit write-up and Ask AI use the LLM.

_RESULT_TABS = ["🥗 Plan", "🍽️ Meals", "🛒 Shopping", "💬 Ask AI", "📤 Share"]

# Other nutrients a swapped food brings, shown as % of the EU NRV under the food.
_BONUS_NUTRIENTS = [
    "vitamin a", "vitamin c", "vitamin d", "vitamin e", "vitamin k", "thiamin", "riboflavin", "niacin",
    "vitamin b6", "folate", "vitamin b12", "calcium", "magnesium", "iron", "zinc", "selenium", "iodine",
    "potassium", "copper", "omega 3",
]
_BONUS_MIN_PCT = 15
# EPA+DHA has no EU NRV; EFSA's adequate intake (250 mg/day) stands in for it.
_OMEGA3_REFERENCE_G = 0.25

# Instant serving ideas by food type (no LLM), first match wins.
_SERVING_IDEAS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(liver|kidneys?|heart)\b"), "a small portion about once a week, e.g. pan-fried with onions"),
    (re.compile(r"\b(oysters?|mussels?|clams?|shrimps?|prawns?|scallops?)\b"), "in pasta or risotto, or steamed with garlic"),
    (re.compile(r"\b(salmon|mackerel|herring|sardines?|trout|tuna|anchov\w*|cod|pollock|haddock|fish)\b"),
     "baked or pan-fried twice a week, or canned on rye bread"),
    (re.compile(r"\b(beef|pork|lamb|chicken|turkey|veal|venison)\b"), "grilled or in a stir-fry with vegetables"),
    (re.compile(r"\b(eggs?|egg yolks?)\b"), "boiled, scrambled or as an omelette"),
    (re.compile(r"\b(milk|yogh?urt|quark|cheese|kefir|skyr)\b"), "with breakfast or as a snack"),
    (re.compile(r"\b(peas|cowpeas|black-?eyed|blackeyes|beans?|lentils?|chickpeas?|soybeans?|tofu|tempeh|edamame|"
                r"kidney|pinto|navy|lima|mung|adzuki|fava|pulses|mature seeds)\b"),
     "in a curry, soup or salad (soak dry beans overnight)"),
    (re.compile(r"\b(nuts?|almonds?|cashews?|walnuts?|hazelnuts?|pistachios?|pecans?|peanuts?|macadamias?)\b"),
     "as a snack or chopped over muesli"),
    (re.compile(r"\b(seeds?|flaxseeds?|linseeds?|chia|hemp|sesame|tahini)\b"), "sprinkled over yogurt, muesli or salad"),
    (re.compile(r"\b(oats?|oatmeal|wheat germ|bran|rice|quinoa|buckwheat|millet|bread|muesli|cereals?)\b"),
     "as porridge, muesli or a grain bowl"),
    (re.compile(r"\b(seaweed|nori|kelp|wakame|algae)\b"), "only in small amounts (iodine varies a lot)"),
    (re.compile(r"\b(mushrooms?)\b"), "fried with eggs or in a pasta sauce"),
    (re.compile(r"\b(spinach|kale|chard|collards?|greens|lettuce|parsley|cress|grape leaves|herbs?|basil|dill)\b"),
     "in a salad or smoothie, or sautéed with garlic"),
    (re.compile(r"\b(broccoli|cabbage|sprouts|peppers?|carrots?|pumpkin|squash|sweet potato(es)?|potato(es)?|tomato(es)?)\b"),
     "roasted, steamed, or raw as a snack"),
    (re.compile(r"\b(kiwi\w*|oranges?|berries|blueberries|strawberries|acerola|guavas?|mangos?|papayas?|lemons?|"
                r"grapefruits?|bananas?|apples?|cherr(y|ies)|apricots?|figs?|dates?|raisins?|fruits?)\b"),
     "fresh as a snack or in muesli"),
]


def _serving_idea(food: dict[str, Any] | None) -> str:
    text = " ".join(
        [_food_name(food), str((food or {}).get("food_description", "") or "")]
    ).lower()
    for pattern, idea in _SERVING_IDEAS:
        if pattern.search(text):
            return idea
    return "as part of a regular meal"


def _food_bonus(
    food: dict[str, Any] | None, grams: float | None, exclude: str | list[str] = "", limit: int = 3
) -> list[tuple[str, int]]:
    """Other nutrients this portion supplies: [(name, % of EU NRV)], best first.

    Read from the bundled USDA data (bb.food_nutrient_amount), so it is instant
    and factual; only nutrients reaching _BONUS_MIN_PCT are listed."""
    desc = str((food or {}).get("food_description", "") or "")
    if not desc or not grams or grams <= 0:
        return []
    names = [exclude] if isinstance(exclude, str) else list(exclude or [])
    skip = {bb.canonical_nutrient_key(n) for n in names if n}
    if skip & {"omega 3", "epa", "dha", "fish oil"}:
        skip.add("omega 3")
    out: list[tuple[str, int]] = []
    for key in _BONUS_NUTRIENTS:
        if key in skip:
            continue
        try:
            per_100g = bb.food_nutrient_amount(desc, key)
        except Exception:
            per_100g = None
        if not per_100g:
            continue
        unit = str(bb._NUTRIENT_LEXICON.get(key, {}).get("unit", "") or "")
        if key == "omega 3":
            ref_value, ref_unit = _OMEGA3_REFERENCE_G, "g"
        elif key in _EU_NRV:
            ref_value, ref_unit = _EU_NRV[key]
        else:
            continue
        src, dst = bb.unit_to_mg(unit), bb.unit_to_mg(ref_unit)
        if not src or not dst or ref_value <= 0:
            continue
        pct = (grams * float(per_100g) / 100.0) * src / dst / ref_value * 100.0
        if pct >= _BONUS_MIN_PCT:
            out.append((_nutrient_title(key), int(round(pct))))
    out.sort(key=lambda item: -item[1])
    return out[:limit]


def _format_need_share(pct: int) -> str:
    """Share of the daily need: "45%", or "2.4×" / "11×" once it is double or more."""
    if pct < 200:
        return f"{pct}%"
    times = pct / 100.0
    return f"{times:.1f}×".replace(".0×", "×") if times < 10 else f"{round(times)}×"


def _format_need_phrase(pct: int) -> str:
    """The share in words that read right for both forms: "45% of the daily need" / "11× the daily need"."""
    share = _format_need_share(pct)
    return f"{share} the daily need" if share.endswith("×") else f"{share} of the daily need"


def _format_plan_grams(grams: float | None) -> str:
    if grams is None or grams <= 0:
        return ""
    if grams >= 1000:
        return f"{bb.format_float(grams / 1000.0, 1)} kg"
    if grams >= 10:
        return f"{int(round(grams))} g"
    return f"{bb.format_float(grams, 1)} g"


def _plan_rows(replace_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per swapped food (a food chosen for several nutrients is listed
    once, at its largest daily amount, with all the nutrients it covers)."""
    # Keyed and sized like _swap_foods / _swap_grams, so the rows, the summary
    # tiles and the shopping list always agree.
    rows: dict[str, dict[str, Any]] = {}
    for d in replace_items:
        food = d.get("selected_food") or {}
        name = _food_name(food)
        if not name or not str(food.get("food_description", "") or ""):
            continue
        grams = _swap_grams(d)
        key = bb.normalize_lookup_key(name)
        row = rows.setdefault(key, {"food": food, "grams": grams, "nutrients": [], "items": []})
        row["nutrients"].append(_nutrient_title(d.get("component")))
        row["items"].append(d)
        if grams is not None and (row["grams"] is None or grams > row["grams"]):
            row["grams"], row["food"] = grams, food
    out = list(rows.values())
    for row in out:
        row["practicality"] = _portion_practicality(row["grams"], row["food"]) if row["grams"] else "ok"
        covered = [str(d.get("component", "") or "") for d in row["items"]]
        row["bonus"] = _food_bonus(row["food"], row["grams"], exclude=covered)
    return out


def _plan_context_text(replace_items: list[dict[str, Any]], keep_items: list[dict[str, Any]]) -> str:
    """The plan in one line for Ask AI: each nutrient with its dose and choice."""
    parts = []
    for d in replace_items:
        dose = _dose_for_context(d)
        parts.append(
            f"{_nutrient_title(d.get('component'))}{dose} -> food: {_food_name(d.get('selected_food'))}"
            + (f" ({_amount_to_match_dose(d)})" if _amount_to_match_dose(d) else "")
        )
    for d in keep_items:
        parts.append(f"{_nutrient_title(d.get('component'))}{_dose_for_context(d)} -> kept as a supplement")
    return "the user's plan: " + "; ".join(parts) if parts else "the user's plan"


def _dose_for_context(d: dict[str, Any]) -> str:
    dose = str(d.get("dose_label", "") or "").strip()
    return f" {dose}" if dose and not dose.lower().startswith("dose not") else ""


def _render_plan_hero(
    cards: list[dict[str, Any]],
    replace_items: list[dict[str, Any]],
    keep_items: list[dict[str, Any]],
    misfit_items: list[dict[str, Any]] | None = None,
) -> None:
    total = max(1, len(cards))
    swapped = len(replace_items)
    totals = _swap_totals(replace_items)
    basket = _basket_cost_breakdown(replace_items)
    pct = int(round(100.0 * swapped / total))
    misfits = len(misfit_items or [])
    if swapped:
        title = f"{swapped} of {len(cards)} nutrients now come from food"
    elif misfits:
        title = f"{misfits} swap{'s' if misfits != 1 else ''} need{'' if misfits != 1 else 's'} a new food"
    else:
        title = "You kept all your supplements"
    stats: list[tuple[str, str]] = []
    if totals["foods"]:
        stats.append((f"{_round_total(totals['grams'])} g", "food / day"))
        if len(totals["no_energy"]) < len(totals["foods"]):
            stats.append((f"~{_round_total(totals['kcal'])}", "kcal / day"))
    if basket["total"] > 0:
        stats.append((f"€{basket['total']:.2f}", "per day"))
    stats.append((str(len(keep_items)), "kept as supplements" if len(keep_items) != 1 else "kept as supplement"))
    tiles = "".join(
        f"<div class='plan-stat'><b>{html.escape(value)}</b><span>{html.escape(label)}</span></div>"
        for value, label in stats[:4]
    )
    st.markdown(
        "<div class='plan-hero'>"
        "<div class='plan-kicker'>Your plan</div>"
        f"<div class='plan-title' role='heading' aria-level='2'>{html.escape(title)}</div>"
        f"<div class='plan-bar' role='progressbar' aria-label='Share of nutrients from food' "
        f"aria-valuenow='{pct}' aria-valuemin='0' aria-valuemax='100'>"
        f"<span style='width:{pct}%'></span></div>"
        f"<div class='plan-stats'>{tiles}</div>"
        "</div>",
        unsafe_allow_html=True,
    )


def _plan_warnings(
    replace_items: list[dict[str, Any]], keep_items: list[dict[str, Any]]
) -> list[str]:
    warnings: list[str] = []
    warnings += _final_upper_limit_warnings(keep_items)
    warnings += _final_food_warnings(replace_items)
    warnings += _pregnancy_food_warnings(replace_items)
    for kind, text in _swap_totals_lines(replace_items):
        if kind == "warning":
            warnings.append(text)
    return list(dict.fromkeys(w for w in warnings if w))


# --- Plan items: tap a food or a kept pill, get its options window -------------
# Every food row and kept-pill row of the Plan tab is HTML (.plan-row) with a transparent button over it
# (CSS: planrow_ / planbtn_). A tap opens a dialog for THAT item with the options that used to sit at the
# bottom of the page: change the choice, what the whole food adds (AI), the Athlete RDA guide.
# Misfit rows ("doesn't fit <diet>") keep their own button: their one action is to choose another food.


_TAP_HINT = "Tap a food or a supplement for options."


def _md_escape(text: Any) -> str:
    """Backslash-escape what Streamlit's markdown (button labels, dialog titles) would turn into formatting."""
    return re.sub(r"([\\`*_\[\]~$:])", r"\\\1", str(text or ""))


def _plan_row_amount(row: dict[str, Any]) -> str:
    if row["practicality"] == "impractical":
        return "not practical from food"
    return f"{_format_plan_grams(row['grams'])}/day" if row["grams"] else ""


def _plan_row_key(row: dict[str, Any]) -> str:
    """The identity of a food row: the same normalised name _plan_rows groups by."""
    return bb.normalize_lookup_key(_food_name(row["food"]))


def _open_plan_item(kind: str, key: str) -> None:
    """Row button callback: request the options window of a food ("food", normalised food name) or of a kept pill
    ("keep", component_key). The dialog is drawn at the end of the same run by _build_mobile_ui."""
    st.session_state["swipe_plan_item"] = {"kind": kind, "key": key}


def _close_plan_item_dialog() -> None:
    """Also the dialog's on_dismiss (X, Esc, tap outside): the request stays until one of them clears it."""
    st.session_state["swipe_plan_item"] = None


def _plan_item_row(kind: str, index: int, key: str, row_html: str, label: str) -> None:
    """One tappable row. Keys are numbered, never built from food names, so they cannot collide."""
    with st.container(key=f"planrow_{kind}_{index}"):
        st.markdown(row_html, unsafe_allow_html=True)
        st.button(_md_escape(label), key=f"planbtn_{kind}_{index}", on_click=_open_plan_item, args=(kind, key))


def _resolve_plan_item(request: Any) -> dict[str, Any] | None:
    """The requested item, read again from the CURRENT decisions: {"kind", "items", "row"}. `items` are decision
    dicts in card order; `row` is the food's plan row (None for a pill). None once it is gone: a decision was
    changed, or the diet filter now makes the food a misfit."""
    if not isinstance(request, dict):
        return None
    kind, key = request.get("kind"), str(request.get("key", "") or "")
    decisions = st.session_state.get("swipe_decisions") or {}
    if kind == "keep":
        d = decisions.get(key)
        return {"kind": "keep", "items": [d], "row": None} if isinstance(d, dict) and d.get("decision") == "keep" else None
    if kind == "food":
        replace_items, _misfits = _split_replacements_by_diet(
            [d for d in decisions.values() if d.get("decision") == "replace"], _selected_dietary_profile()
        )
        row = next((r for r in _plan_rows(replace_items) if _plan_row_key(r) == key), None)
        if row:
            return {"kind": "food", "items": sorted(row["items"], key=lambda d: int(d.get("card_index", 0))), "row": row}
    return None


def _plan_item_title(item: dict[str, Any]) -> str:
    """Plain text (a screen reader announces it as the dialog's name), markdown-escaped. _food_name is already short."""
    title = (
        _nutrient_title(item["items"][0].get("component")) or "Supplement"
        if item["kind"] == "keep"
        else _food_name(item["row"]["food"]) or "Whole food"
    )
    return _md_escape(title if len(title) <= 60 else title[:59].rstrip() + "…")


def _plan_item_dialog_requested() -> bool:
    request = st.session_state.get("swipe_plan_item")
    if request is None:
        return False
    if not isinstance(request, dict) or not _on_results_screen():  # a stale request must not pop up when the results come back
        _close_plan_item_dialog()
        return False
    return True


def _show_plan_item_dialog() -> None:
    item = _resolve_plan_item(st.session_state.get("swipe_plan_item"))
    if item is None:
        _close_plan_item_dialog()
        return
    # st.dialog fixes its title when it is decorated, so the decorator is applied per run.
    st.dialog(_plan_item_title(item), on_dismiss=_close_plan_item_dialog)(_plan_item_dialog_body)()


def _plan_item_dialog_body() -> None:
    # A button in a dialog reruns only this function (with the arguments of the last full run): read the request fresh.
    item = _resolve_plan_item(st.session_state.get("swipe_plan_item"))
    if item is None:
        _close_plan_item_dialog()
        st.rerun(scope="app")
        return
    with st.container(key="plandlg"):
        _render_plan_item_summary(item)
        _render_plan_item_change(item)
        if item["kind"] == "food":
            with st.expander("\U0001F331 What the whole food adds (AI)"):
                _render_plan_item_adds(item)
        with st.expander("\U0001F3C3 Athlete RDA guide"):
            _render_plan_item_athlete(item)
        if st.button("Done", width="stretch", key="plandlg_close"):
            _close_plan_item_dialog()
            st.rerun(scope="app")


def _render_plan_item_summary(item: dict[str, Any]) -> None:
    """What was tapped, in the chips of its row, then the safety notes that belong to this item alone."""
    if item["kind"] == "keep":
        dose = str(item["items"][0].get("dose_label", "") or "")
        st.markdown(
            (f"<span class='plan-dose'>{html.escape(dose)}</span> " if dose else "") + "kept as a supplement",
            unsafe_allow_html=True,
        )
        _render_heads_up(_final_upper_limit_warnings(item["items"]))
        return
    row = item["row"]
    sub = "for " + ", ".join(dict.fromkeys(n for n in row["nutrients"] if n))
    amount = _plan_row_amount(row)
    st.markdown(
        (f"<span class='plan-amt'>{html.escape(amount)}</span> " if amount else "") + html.escape(sub), unsafe_allow_html=True
    )
    full_name = str(row["food"].get("food_description", "") or "").strip()
    if full_name and full_name != _food_name(row["food"]):
        st.caption(f"USDA: {_md_escape(full_name)}")
    _render_heads_up(_final_food_warnings(item["items"]) + _pregnancy_food_warnings(item["items"]))


def _change_choice_from_dialog(card_index: int) -> None:
    """Leave the options window for card `card_index`; deciding it again takes the user straight back to the results.
    Always from the dialog body: a widget in a dialog reruns the dialog alone, so the page needs the app-wide rerun."""
    _close_plan_item_dialog()
    _open_card(card_index, True)
    _request_scroll_top()
    st.rerun(scope="app")


def _render_plan_item_change(item: dict[str, Any]) -> None:
    st.markdown("<div class='plan-h' role='heading' aria-level='3'>✎ Change a choice</div>", unsafe_allow_html=True)
    for index, d in enumerate(item["items"]):  # a food chosen for two nutrients: one card, so one button, per nutrient
        nutrient = _md_escape(_nutrient_title(d.get("component")) or "Unknown")
        if d.get("decision") == "keep":
            label = f"💊 {nutrient} → kept as a supplement"
        else:
            label = f"🥗 {nutrient} → {_md_escape(_food_name(d.get('selected_food')))}"
        if st.button(label, width="stretch", key=f"plandlg_change_{index}"):
            _change_choice_from_dialog(int(d.get("card_index", 0)))
    st.caption("Reopens the card — after you decide, you come straight back to your plan.")


def _benefits_box(key: str) -> None:
    """The comparison for cache key `key` as far as it is: the finished text, the text so far (or dots), or why it failed.
    Drawn as a fragment (see _render_plan_item_adds): while the job runs it polls by itself, and no script run ever sleeps
    waiting for the model, so Done, Change a choice and every other tap in the window answer at once.
    The status line is a live region that a screen reader announces; the visible dots are hidden from it."""
    ready = llm_cache.get(key)
    pending = None if ready else llm_cache.inflight(key)
    failed = None if ready or pending is not None else llm_cache.failure(key)
    status = "Comparison ready" if ready else "Gathering whole-food benefits…" if pending is not None else ""
    if failed is not None:
        status = "Couldn't fetch the comparison"
    st.markdown(f"<div class='plan-sr' role='status' aria-live='polite'>{status}</div>", unsafe_allow_html=True)
    if ready:
        st.markdown(ready)
    elif pending is not None:
        partial = llm_cache.partial(key)
        if partial:
            st.markdown(partial + " ▌")
        else:
            st.markdown(
                "<div class='plan-writing' aria-hidden='true'><span class='plan-dots'><i></i><i></i><i></i></span>"
                "Gathering whole-food benefits…</div>",
                unsafe_allow_html=True,
            )
    elif failed is not None:
        st.warning(_ai_retry_note("Couldn't fetch the comparison right now — please try again.", failed))


def _ask_whole_food_benefits(items: list[dict[str, Any]], key: str) -> None:
    """The comparison button's callback (it runs before the window is drawn again)."""
    if llm_cache.get(key) is not None or llm_cache.inflight(key) is not None:
        return
    if _consume_llm_quota("generate"):  # counted here, so a spent allowance is not reported as a failure
        st.session_state["swipe_plan_asked"] = key
        _start_whole_food_benefits(items)
    else:
        st.session_state["swipe_plan_quota"] = key


def _render_plan_item_adds(item: dict[str, Any]) -> None:
    """What this one whole food adds compared with the pill: instant facts from the bundled data, then the AI comparison
    for this food only (one background job and one cache entry per food)."""
    row, items = item["row"], item["items"]
    if _pregnancy_mode() and _not_advised_in_pregnancy(row["food"]):  # never promote a food the app advises against
        st.caption("This food isn't advised in pregnancy, so no comparison is shown.")
        return
    bonus = ", ".join(f"{nutrient} ({_format_need_phrase(pct)})" for nutrient, pct in row["bonus"])
    if bonus:
        st.caption("Also in this portion: " + _md_escape(bonus))
    profile = _selected_dietary_profile()
    for d in items:
        note = _bioavailability_note(
            str(d.get("component_key", "") or ""), str(d.get("form", "") or ""),
            d.get("dose_value"), str(d.get("dose_unit", "") or ""), profile,
        )
        if note:
            st.caption(f"{_md_escape(_nutrient_title(d.get('component')))}: {_md_escape(note)}")
    prompts = _benefits_prompts(items)
    if prompts is None:
        return
    key = prompts[2]
    box = st.container()  # above the button, filled below once it is known whether a job is running
    ready = llm_cache.get(key)  # also with the AI off or the allowance spent: a cached answer costs nothing
    pending = None if ready else llm_cache.inflight(key)  # started before (this window was closed meanwhile) or by another visitor
    if not ready and pending is None and not _ai_is_on():
        box.caption("The AI comparison is switched off right now.")
        return
    # The button stays once this visitor has asked (a tap on it again does nothing), and is never disabled: removing or
    # disabling the focused control would throw a keyboard user's focus out of the window when the text arrives.
    # Its callback starts the job, so the run it triggers already shows the progress.
    if st.session_state.pop("swipe_plan_quota", None) == key:
        box.info(_QUOTA_MESSAGE)
    if (not ready and pending is None) or st.session_state.get("swipe_plan_asked") == key:
        st.button(
            "Show the comparison", type="primary", width="stretch", key="plandlg_benefits",
            on_click=_ask_whole_food_benefits, args=(items, key),
        )
    with box:
        # run_every is chosen per call: polling only while a job runs. It stops with the next run of the window (any
        # tap in it, or closing it); until then the finished text is simply drawn again once a second.
        st.fragment(_benefits_box, run_every=1.0 if pending is not None else None)(key)


def _render_plan_item_athlete(item: dict[str, Any]) -> None:
    """The targets of this item's nutrients, then the table of all of them."""
    food = (item["row"] or {}).get("food")
    for d in item["items"]:
        title = _md_escape(_nutrient_title(d.get("component")) or "This nutrient")
        entry = _rda_for_component(str(d.get("component_key", "") or d.get("component", "") or ""))
        if entry is None:
            st.caption(f"{title}: no athlete target is tracked for this nutrient.")
            continue
        unit = str(entry["unit"])
        facts = [f"athlete target **{_md_escape(_format_rda_target(entry))}**", f"adult RDA {bb.format_float(float(entry['rda']))} {unit}"]
        nrv = _format_eu_nrv(entry)
        if nrv != "–":
            facts.append(f"EU label (100% NRV) {nrv} {unit}")
        lines = [f"**{_md_escape(entry['display'])}** — " + ", ".join(facts)]
        if food:
            reach = _portion_for_target(food, entry["athlete"], unit, str(entry["display"]), note=False)
            if reach:
                lines.append(f"To reach the athlete target: {_md_escape(reach)}")
        else:
            ratio = _dose_vs_athlete_ratio(
                str(d.get("component_key", "") or ""), d.get("dose_value"), str(d.get("dose_unit", "") or ""), str(d.get("form", "") or "")
            )
            if ratio is not None:
                lines.append(f"Your pill: {_format_need_share(int(round(ratio * 100)))} of the athlete target")
        st.markdown("  \n".join(lines))
    _render_athlete_rda_table()


def _render_plan_tab(
    cards: list[dict[str, Any]],
    replace_items: list[dict[str, Any]],
    misfit_items: list[dict[str, Any]],
    keep_items: list[dict[str, Any]],
    diet_name: str,
) -> None:
    rows = _plan_rows(replace_items)
    hinted = False
    if rows:
        st.markdown("<div class='plan-h' role='heading' aria-level='3'>🥗 Eat this</div>", unsafe_allow_html=True)
        st.caption(_TAP_HINT)
        hinted = True
        with st.container(key="planlist_food"):
            for index, row in enumerate(rows):
                food = row["food"]
                name = _food_name(food) or "Whole food"
                icon = _whole_food_icon_from_food(food, "")
                amount = _plan_row_amount(row)
                sub = "for " + ", ".join(dict.fromkeys(n for n in row["nutrients"] if n))
                bonus = ", ".join(f"{nutrient} ({_format_need_phrase(pct)})" for nutrient, pct in row["bonus"])
                bonus_html = f"<div class='plan-bonus'>+ also {html.escape(bonus)}</div>" if bonus else ""
                _plan_item_row(
                    "food", index, _plan_row_key(row),
                    ("<div class='plan-sep'></div>" if index else "")
                    + "<div class='plan-row' aria-hidden='true'>"
                    f"<div class='plan-ico' aria-hidden='true'>{html.escape(icon)}</div>"
                    "<div class='plan-main'>"
                    f"<div class='plan-name'>{html.escape(name)}"
                    + (f"<span class='plan-amt'>{html.escape(amount)}</span>" if amount else "")
                    + "</div>"
                    f"<div class='plan-sub'>{html.escape(sub)}</div>{bonus_html}"
                    "</div><div class='plan-chev' aria-hidden='true'>›</div></div>",
                    f"{name}" + (f", {amount}" if amount else "") + f", {sub}. Opens options.",
                )
    if misfit_items:
        st.markdown(
            f"<div class='plan-h' role='heading' aria-level='3'>⚠️ Needs a new food ({html.escape(diet_name)})</div>", unsafe_allow_html=True
        )
        for d in misfit_items:
            component_key = str(d.get("component_key", "") or "")
            food_name = _food_name(d.get("selected_food")) or "this food"
            st.button(
                f"⚠️ {_nutrient_title(d.get('component')) or 'Unknown'} → {food_name} doesn't fit {diet_name} — tap to choose another",
                width="stretch",
                key=f"final_misfit_{component_key}",
                on_click=_open_card,
                args=(int(d.get("card_index", 0)), True),
            )
    if keep_items:
        st.markdown("<div class='plan-h' role='heading' aria-level='3'>💊 Kept as supplements</div>", unsafe_allow_html=True)
        if not hinted:
            st.caption(_TAP_HINT)
        with st.container(key="planlist_keep"):
            for index, d in enumerate(keep_items):
                title = _nutrient_title(d.get("component")) or "Supplement"
                dose = str(d.get("dose_label", "") or "")
                _plan_item_row(
                    "keep", index, str(d.get("component_key", "") or ""),
                    ("<div class='plan-sep'></div>" if index else "")
                    + "<div class='plan-row' aria-hidden='true'>"
                    "<div class='plan-ico' aria-hidden='true'>💊</div>"
                    "<div class='plan-main'>"
                    f"<div class='plan-name'>{html.escape(title)}"
                    + (f"<span class='plan-dose'>{html.escape(dose)}</span>" if dose else "")
                    + "</div></div><div class='plan-chev' aria-hidden='true'>›</div></div>",
                    f"{title}" + (f", {dose}" if dose else "") + ", kept as a supplement. Opens options.",
                )
    if not rows and not keep_items and not misfit_items:
        st.info("Swipe through your cards to build your plan.")

    # The three options that used to sit down here (change a choice, what the whole food adds, the Athlete RDA
    # guide) open from a tap on a row. The reference table stays one tap away, for plans without a tappable row.
    st.markdown("<div style='height:0.6rem'></div>", unsafe_allow_html=True)
    _render_athlete_rda_popup()


def _render_meals_tab(replace_items: list[dict[str, Any]], diet_label: str, excluded: list[dict[str, Any]]) -> str:
    """Instant serving ideas + the AI meal plan. Returns the plan's cache key."""
    plan_key = ""
    _excluded_swaps_caption(excluded, diet_label)
    if not replace_items:
        st.info("Swipe right on at least one nutrient to get meal ideas.")
        return plan_key
    st.markdown("<div class='plan-h' role='heading' aria-level='3'>⚡ Quick ideas</div>", unsafe_allow_html=True)
    lines = []
    for row in _plan_rows(replace_items):
        if row["practicality"] == "impractical":
            continue
        name = _food_name(row["food"]) or "Whole food"
        amount = _format_plan_grams(row["grams"])
        lines.append(
            "<div class='plan-idea'>"
            f"<b>{html.escape(name)}</b>{' · ' + html.escape(amount) if amount else ''} — "
            f"{html.escape(_serving_idea(row['food']))}</div>"
        )
    if lines:
        st.markdown("".join(lines), unsafe_allow_html=True)

    st.markdown("<div class='plan-h' role='heading' aria-level='3'>✨ Your meal plan</div>", unsafe_allow_html=True)
    # Mirrored into a plain key (_on_meal_count_change): the radio isn't drawn
    # while a card is open, so Streamlit drops its state.
    chosen_meals = int(st.session_state.get("swipe_meal_count_choice", 3) or 3)
    num_meals = st.radio(
        "How many meals?",
        options=[1, 2, 3],
        index=[1, 2, 3].index(chosen_meals) if chosen_meals in (1, 2, 3) else 2,
        horizontal=True,
        key="swipe_meal_count",
        on_change=_on_meal_count_change,
        format_func=lambda m: f"{m} meal" if m == 1 else f"{m} meals",
        label_visibility="collapsed",
    )
    _sys, _usr, plan_key = _meal_plan_prompts(replace_items, diet_label, int(num_meals))
    ready = llm_cache.get(plan_key)
    plan_box = st.empty()
    if ready:
        plan_box.markdown(ready)
        st.session_state["swipe_meal_plan"] = ready
        st.session_state["swipe_meal_plan_key"] = plan_key
        if st.button("🔄 Different meals", width="stretch", key="swipe_regen_meal"):
            llm_cache.drop(plan_key)
            with st.spinner("Cooking up new meals…"):
                st.session_state["swipe_meal_plan"] = _generate_meal_plan(
                    replace_items, diet_label, int(num_meals), placeholder=plan_box
                )
    elif llm_cache.inflight(plan_key) is not None:
        with plan_box.container():
            _live_meal_plan(plan_key)
    elif not _ai_is_on():
        st.caption("The AI meal plan is switched off right now — the ideas above still work.")
    elif st.button("Generate my meals", type="primary", width="stretch", key="swipe_gen_meal"):
        with st.spinner("Cooking up your meals…"):
            plan = _generate_meal_plan(replace_items, diet_label, int(num_meals), placeholder=plan_box)
        st.session_state["swipe_meal_plan"] = plan
        st.session_state["swipe_meal_plan_key"] = plan_key
        if not plan:
            st.warning(_ai_retry_note("Couldn't generate meals right now — please try again."))
    return plan_key


@st.fragment(run_every=1.0)
def _live_meal_plan(plan_key: str) -> None:
    """The meal plan being written in the background, refreshed every second.

    Only this fragment re-runs while it streams; one full re-run when it is done
    shows the finished plan with its buttons (and stops the polling)."""
    if llm_cache.inflight(plan_key) is None:
        if st.session_state.get("swipe_plan_item"):
            return  # a plan item window is open: redrawing the page would make it blink; closing it redraws the page anyway
        st.rerun()
    partial = llm_cache.partial(plan_key)
    if partial:
        st.markdown(partial + " \u258c")
    else:
        st.markdown(
            "<div class='plan-writing'><span class='plan-dots'><i></i><i></i><i></i></span>"
            "Writing your meal plan…</div>",
            unsafe_allow_html=True,
        )


def _render_shopping_tab(
    replace_items: list[dict[str, Any]], keep_items: list[dict[str, Any]], diet_label: str, excluded: list[dict[str, Any]]
) -> None:
    _excluded_swaps_caption(excluded, diet_label)
    rows = [row for row in _plan_rows(replace_items) if row["grams"]]
    practical = [row for row in rows if row["practicality"] != "impractical"]
    if practical:
        st.markdown("<div class='plan-h' role='heading' aria-level='3'>🛒 Groceries for one week</div>", unsafe_allow_html=True)
        parts, total, unpriced = [], 0.0, []
        for row in practical:
            name = _food_name(row["food"]) or "Whole food"
            week_g = row["grams"] * 7
            daily = _estimate_food_price_eur(str(row["food"].get("food_description", "") or ""), row["grams"])
            cost = daily * 7 if daily else None
            if cost is not None:
                total += cost
            else:
                unpriced.append(name)
            parts.append(
                "<div class='shop-row'>"
                f"<span class='shop-name'>{html.escape(name)}</span>"
                f"<span class='shop-qty'>{html.escape(_format_plan_grams(week_g))}</span>"
                f"<span class='shop-cost'>{'~€' + format(cost, '.2f') if cost is not None else '–'}</span>"
                "</div>"
            )
        if total > 0:
            parts.append(
                "<div class='shop-total'><span class='shop-name'>Total per week</span>"
                f"<span class='shop-cost'>~€{total:.2f}</span></div>"
            )
        st.markdown("<div class='shop-list'>" + "".join(parts) + "</div>", unsafe_allow_html=True)
        st.caption(
            "Approximate German discounter prices (ALDI/Lidl/REWE)"
            + (f"; no price for {', '.join(unpriced)}" if unpriced else "")
            + "."
        )
    impractical = [row for row in rows if row["practicality"] == "impractical"]
    if impractical:
        st.caption(
            "Not on the list — more than you'd realistically eat in a day, so keeping the supplement "
            "is the practical choice: "
            + ", ".join(f"{_food_name(row['food'])} (~{_format_grams(row['grams'])}/day)" for row in impractical)
            + "."
        )
    if not practical and not impractical:
        st.info("No whole-food swaps to shop for yet.")
    if keep_items:
        st.markdown("<div class='plan-h' role='heading' aria-level='3'>💊 For the pills you keep</div>", unsafe_allow_html=True)
        covers = ", ".join(dict.fromkeys(_nutrient_title(d.get("component")) for d in keep_items if d.get("component")))
        st.caption(f"One combined product covering {covers} is usually cheapest:")
        _query, links = _supplement_search_links(keep_items)
        cols = st.columns(len(links)) if links else []
        for col, (label, url) in zip(cols, links.items()):
            with col:
                st.link_button(label.replace("Compare prices on ", "").replace("Search on ", ""), url, width="stretch")


_ASK_AI_SUGGESTIONS = [
    "Is my plan balanced?",
    "Which pills should I still keep?",
    "Quick recipes with these foods?",
]


def _queue_ask_ai_suggestion(pills_key: str, pending_key: str) -> None:
    choice = st.session_state.get(pills_key)
    if choice:
        st.session_state[pending_key] = str(choice)
    st.session_state[pills_key] = None


def _render_ask_ai_chat(
    card: dict[str, Any], component_key: str, index: int, suggestions: list[str] | None = None
) -> None:
    """Chat about a card (or the whole plan): history, one-tap suggestions, input."""
    chat_store: dict[str, list[dict[str, str]]] = st.session_state.get("swipe_rag_chats", {})
    history = _chat_without_error_turns(list(chat_store.get(component_key, [])))
    for msg in history[-12:]:
        role = "user" if str(msg.get("role", "")).lower() == "user" else "assistant"
        with st.chat_message(role):
            st.write(str(msg.get("content", "") or ""))

    if not _ai_is_on():
        st.caption("AI answers are switched off right now.")
        return

    pending_key = f"swipe_rag_pending_{component_key}_{index}"
    if suggestions:
        pills_key = f"swipe_rag_suggest_{component_key}_{index}"
        st.pills(
            "Suggestions",
            options=suggestions,
            selection_mode="single",
            key=pills_key,
            on_change=_queue_ask_ai_suggestion,
            args=(pills_key, pending_key),
            label_visibility="collapsed",
        )
    question = st.chat_input(
        "Ask about this nutrient…" if component_key != "summary" else "Ask about your plan…",
        key=f"swipe_rag_chat_input_{component_key}_{index}",
        max_chars=_ASK_AI_MAX_CHARS,
    )
    if history and st.button("Clear chat", type="tertiary", key=f"swipe_rag_clear_{component_key}_{index}"):
        chat_store[component_key] = []
        st.session_state["swipe_rag_chats"] = chat_store
        st.rerun()

    pending = str(st.session_state.pop(pending_key, "") or "")
    asked = (pending or str(question or "").strip())[:_ASK_AI_MAX_CHARS]
    if asked:
        with st.chat_message("user"):
            st.write(asked)
        bubble = st.empty()  # the assistant's bubble is cleared again when there is no answer (no empty avatar)
        with bubble.container():
            with st.chat_message("assistant"):
                stream_box = st.empty()
                with st.spinner("Asking AI research assistant..."):
                    component_name = str(card.get("display", "") or "") or _nutrient_title(card.get("component"))
                    answer, sources_line = _answer_ask_ai_question(
                        component_name,
                        asked,
                        history=_ask_ai_history(component_key),
                        placeholder=stream_box,
                        dose_label=str(card.get("dose_label", "") or ""),
                    )
        if answer is None:
            bubble.empty()
            st.error(_ai_retry_note("Ask AI is unavailable right now — please try again in a moment."))
        else:
            chat_store[component_key] = history + [
                {"role": "user", "content": asked},
                {"role": "assistant", "content": (answer or "No answer available.") + sources_line},
            ]
            st.session_state["swipe_rag_chats"] = chat_store
            st.rerun()


def _card_ask_ai_suggestions(card: dict[str, Any]) -> list[str]:
    name = _nutrient_title(card.get("component")) or "this nutrient"
    dose = str(card.get("dose_label", "") or "").strip()
    if dose.lower().startswith("dose not"):
        dose = ""
    return [
        f"Is {dose} a safe daily dose?" if dose else f"How much {name} do I need?",
        "Which everyday foods have the most?",
        "Who should keep the supplement?",
    ]


def _render_rag_chat_popup(card: dict[str, Any], component_key: str, index: int) -> None:
    if not _ai_is_on():
        return  # nothing to ask: no button that only ends in an error
    with st.popover("💬 Ask AI", width="stretch"):
        st.caption("Science-based answers about this nutrient and your dose.")
        _render_ask_ai_chat(card, component_key, index, suggestions=_card_ask_ai_suggestions(card))


def _render_share_tab(
    keep_items: list[dict[str, Any]], replace_items: list[dict[str, Any]], plan_key: str,
    diet_label: str, excluded: list[dict[str, Any]],
) -> None:
    _excluded_swaps_caption(excluded, diet_label)
    # Only a plan written for the current swaps (not one from before the filter
    # or a choice changed) goes into the share text.
    meal_plan = ""
    if plan_key and st.session_state.get("swipe_meal_plan_key") == plan_key:
        meal_plan = str(st.session_state.get("swipe_meal_plan", "") or "")
        if bb.looks_like_agent_error(meal_plan):  # stored by an older build: not a plan
            meal_plan = ""
    share_text = _build_share_text(keep_items, replace_items, meal_plan)
    st.caption("Tap the copy icon on the box, or download the plan.")
    st.code(share_text, language=None, wrap_lines=True)
    st.download_button(
        "⬇️ Download as text",
        data=share_text,
        file_name="suppswipe_plan.txt",
        mime="text/plain",
        width="stretch",
        key="swipe_share_dl",
    )


def _render_heads_up(warnings: list[str]) -> None:
    """The orange Heads-up box (nothing when there is no warning)."""
    if warnings:
        st.markdown(
            "<div class='plan-warn'><div class='plan-warn-h' role='heading' aria-level='3'>Heads-up</div>"
            + "".join(f"<div class='plan-warn-i'>{html.escape(w)}</div>" for w in warnings)
            + "</div>",
            unsafe_allow_html=True,
        )


def _render_final_card(cards: list[dict[str, Any]], decisions: dict[str, dict[str, Any]]) -> None:
    _render_label_source_notice()  # "sample label" / "looked up online" stays visible on the results too
    profile = _selected_dietary_profile()
    diet_name = _active_diet_label(profile)
    all_replace_items = [d for d in decisions.values() if d.get("decision") == "replace"]
    # Swaps picked before the dietary filter changed and no longer fitting it
    # stay listed (flagged) but are left out of meals, cost and share text.
    replace_items, misfit_items = _split_replacements_by_diet(all_replace_items, profile)
    keep_items = [d for d in decisions.values() if d.get("decision") == "keep"]
    diet_label = str((profile or {}).get("label", "") or "")

    if cards:
        # Callbacks (not st.rerun()) so one tap is one script run.
        st.button(
            "↩ Back to the cards", type="tertiary", key="final_back_last", on_click=_open_card, args=(len(cards) - 1,)
        )
    _render_plan_hero(cards, replace_items, keep_items, misfit_items)
    st.caption(_RESULTS_DISCLAIMER)
    _render_heads_up(_plan_warnings(replace_items, keep_items))

    # Record this completed scan to the on-device history (once per analysis).
    _record_scan_to_history(decisions, diet_label)
    # Start writing the default (3-meal) plan in the background right away.
    _prefetch_meal_plan(replace_items, diet_label, 3)

    tab_plan, tab_meals, tab_shop, tab_ai, tab_share = st.tabs(_RESULT_TABS, key="swipe_result_tabs")
    with tab_plan:
        _render_plan_tab(cards, replace_items, misfit_items, keep_items, diet_name)
    with tab_meals:
        plan_key = _render_meals_tab(replace_items, diet_label, misfit_items)
    with tab_shop:
        _render_shopping_tab(replace_items, keep_items, diet_label, misfit_items)
    with tab_ai:
        summary_context = {"component": "your plan", "display": _plan_context_text(replace_items, keep_items)}
        st.caption("Ask about your whole plan — answers use your nutrients and doses.")
        _render_ask_ai_chat(summary_context, "summary", 0, suggestions=_ASK_AI_SUGGESTIONS)
    with tab_share:
        _render_share_tab(keep_items, replace_items, plan_key, diet_label, misfit_items)


def _format_eu_nrv(entry: dict[str, Any]) -> str:
    """EU NRV of an Athlete-RDA-guide row in that row's unit, or "–" if none."""
    nrv = next((_EU_NRV[k] for k in entry.get("keys", ()) if k in _EU_NRV), None)
    if nrv is None:
        return "–"
    value = _dose_in_unit(str(entry["keys"][0]), nrv[0], nrv[1], str(entry["unit"]))
    return bb.format_float(value) if value is not None else "–"


def _render_athlete_rda_table() -> None:
    """The Athlete RDA guide's body: caption, table of every tracked micronutrient, risk note. The unit sits in the
    nutrient column, so the table has four columns and fits a phone."""
    st.caption(
        "Approximate daily targets for every micronutrient the app tracks. "
        "EU NRV = the reference intake behind the %NRV on EU labels; adult "
        "RDA/AI from NIH ODS; athlete targets raised per ISSN and "
        "ACSM/AND/DC where training increases needs or sweat losses. General "
        "guidance only — consult a sports dietitian for personalised advice."
    )
    st.table(
        [
            {
                "Nutrient (per day)": f"{entry['display']} ({entry['unit']})",
                "EU NRV": _format_eu_nrv(entry),
                "Adult RDA": bb.format_float(float(entry["rda"])),
                "Athlete": bb.format_float(float(entry["athlete"])),
            }
            for entry in _MICRONUTRIENT_RDA
        ]
    )
    st.caption(
        "\U0001F4A1 Athletes training >10 h/week, in low-sunlight regions, or on "
        "plant-based diets are most at risk of Vitamin D, Iron, B12, Zinc and "
        "Omega-3 deficiencies. Iron RDA shown is the general adult value "
        "(menstruating women need ~18 mg; men ~8 mg)."
    )


def _render_athlete_rda_popup() -> None:
    """Static reference: approximate daily micronutrient targets for athletes.

    Values are approximate consensus figures from ISSN (Nutrient Timing, 2017),
    ACSM/AND/DC Nutrition and Athletic Performance (2016/2021), and NIH Office
    of Dietary Supplements RDA fact sheets. General guidance only — kept in a
    popover so the long table doesn't push the results down. The same table
    sits in every plan item's options window (_render_plan_item_athlete).
    """
    with st.popover("\U0001F3C3 Athlete RDA guide", width="stretch"):
        _render_athlete_rda_table()


def _debug_requested() -> bool:
    """?debug=1 opens the diagnostics; with SUPPSWIPE_DEBUG_TOKEN set it takes ?debug=<token> instead (so only the owner can)."""
    asked = str(st.query_params.get("debug", "") or "")
    if not asked:
        return False
    token = str(os.getenv("SUPPSWIPE_DEBUG_TOKEN", "") or "").strip()
    return hmac.compare_digest(asked.encode("utf-8"), token.encode("utf-8")) if token else asked == "1"


def _short_error(text: str) -> str:
    """What the diagnostics may show of an error: the kind ("create conversation: HTTP 404"), never what the server said."""
    first = re.split(r"[{\[\n]", str(text or ""), maxsplit=1)[0].strip()
    return first[:80]


def _render_debug_panel() -> None:
    """Shown only with ?debug=1: which endpoint/model answered the last LLM call
    and how long it took (time-to-first-token and total)."""
    with st.expander("🛠 Diagnostics", expanded=False):
        st.json(
            {
                "build": BUILD_TAG,
                "code_reloads_since_start": int(sys.modules["_suppswipe_imports"].__dict__.get("generation", 0)),
                "model": bb.blockbrain_model_label() or "(none configured)",
                # Names and timings only: never the key, the org id or the bot id.
                "blockbrain_config": bb.blockbrain_config_error() or "complete",
                "ocr_routes": bb._ocr_routes(),
                # Timings are process-wide and carry no text from a server; the error is this session's own.
                "last_call": {k: v for k, v in dict(getattr(bb, "LAST_BLOCKBRAIN_TIMING", {}) or {}).items() if k != "error"},
                "last_error": _short_error(bb.last_call_error()),
                "last_link": {"provider": str(getattr(bb, "LAST_TEXT_PROVIDER", "") or ""),
                              "reason": str(getattr(bb, "LAST_URL_PARSE_REASON", "") or "")},
            }
        )


def _build_mobile_ui() -> None:
    _init_state()
    _render_header()
    if bool(st.session_state.get("swipe_is_analyzing", False)) and isinstance(st.session_state.get("swipe_pending_request"), dict):
        _run_pending_analysis()
    _render_card()
    if _on_results_screen():
        _render_results_settings()
        _render_analyze_bar(results=True)
    else:
        _render_dietary_pills()
        # On the welcome screen the main button sits in the hero (_render_card).
        _render_analyze_bar(button=bool(st.session_state.get("swipe_cards")))
    # A dialog stays requested until it is closed (Cancel, X, or its action).
    if st.session_state.get("swipe_confirm_restart"):
        _confirm_restart_dialog()
    elif st.session_state.get("swipe_open_analyze"):
        _analyze_dialog()
    elif _plan_item_dialog_requested():
        _show_plan_item_dialog()
    if st.session_state.pop("_suppswipe_scroll_top", False):
        _scroll_to_top()
    try:
        show_debug = _debug_requested()
    except Exception:
        show_debug = False
    if show_debug:
        _render_debug_panel()
    st.markdown("<div class='brand-foot'>© mfitness92</div>", unsafe_allow_html=True)
    _sync_scan_history_with_browser()


def _is_streamlit_runtime() -> bool:
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        return get_script_run_ctx() is not None
    except Exception:
        return False


if __name__ == "__main__":
    if _is_streamlit_runtime():
        _build_mobile_ui()
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
        print("Launching SuppSwipe in Streamlit...")
        try:
            subprocess.run(cmd, check=False)
        except Exception as exc:
            print(f"Failed to launch Streamlit automatically: {exc}")
elif _is_streamlit_runtime():
    # Imported by another Streamlit page/runner: render. Imported by tests or
    # tooling (no Streamlit runtime): expose the helpers without drawing the UI.
    _build_mobile_ui()
