from __future__ import annotations

import io
import os
import re
import subprocess
import sys
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

    key_map = {
        "BLOCKBRAIN_API_KEY": "BLOCKBRAIN_API_KEY",
        "BLOCKBRAIN_BASE_URL": "BLOCKBRAIN_BASE_URL",
        "BLOCKBRAIN_API_URL": "BLOCKBRAIN_API_URL",
        "BLOCKBRAIN_AGENT_ID": "BLOCKBRAIN_AGENT_ID",
        "BLOCKBRAIN_MODEL_TEXT": "BLOCKBRAIN_MODEL_TEXT",
        "BLOCKBRAIN_MODEL_VISION": "BLOCKBRAIN_MODEL_VISION",
    }
    for secret_key, env_key in key_map.items():
        if os.getenv(env_key, "").strip():
            continue
        value = str(raw.get(secret_key, "") or "").strip()
        if value:
            os.environ[env_key] = value


_bootstrap_blockbrain_env_from_secrets()

import blockbrain.app as bb  # noqa: E402


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


def _decode_camera_image(value: Any) -> bytes:
    """Decode the {'image': dataURL} value from the camera component into JPEG bytes."""
    if not isinstance(value, dict):
        return b""
    data_url = str(value.get("image", "") or "")
    if "," not in data_url:
        return b""
    try:
        import base64 as _b64
        return _b64.b64decode(data_url.split(",", 1)[1])
    except Exception:
        return b""


LEFT_SWIPE_ICON = "💊"
TITLE_WHOLE_FOOD_ICON = "🥗"
WHOLE_FOOD_ICONS = ["🥦", "🥕", "🥚", "🍓", "🐟", "🥜", "🍠", "🥬"]


def _whole_food_icon(component_key: str) -> str:
    _ = component_key
    return TITLE_WHOLE_FOOD_ICON


def _whole_food_icon_from_food(food: dict[str, Any] | None, component_key: str = "") -> str:
    if not food:
        return _whole_food_icon(component_key)

    desc = str(food.get("food_description", "") or "").lower()
    category = str(food.get("food_category", "") or "").lower()
    blob = f"{desc} {category}".strip()

    if any(token in blob for token in ["salmon", "sardine", "tuna", "mackerel", "anchovy", "fish", "seafood"]):
        return "🐟"
    if any(token in blob for token in ["egg", "eggs"]):
        return "🥚"
    if any(token in blob for token in ["almond", "cashew", "walnut", "pistachio", "hazelnut", "pecan", "peanut", "nut", "seed"]):
        return "🥜"
    if any(token in blob for token in ["spinach", "kale", "broccoli", "cabbage", "lettuce", "chard", "leafy", "greens"]):
        return "🥬"
    if any(token in blob for token in ["carrot", "beet", "turnip", "radish", "root"]):
        return "🥕"
    if any(token in blob for token in ["sweet potato", "potato", "yam"]):
        return "🍠"
    if any(token in blob for token in ["berry", "berries", "strawberry", "blueberry", "raspberry", "fruit", "orange", "apple"]):
        return "🍓"
    if any(token in blob for token in ["bean", "lentil", "chickpea", "legume", "tofu", "soy"]):
        return "🫘"
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
        "swipe_reset_nonce": 0,
        "swipe_is_analyzing": False,
        "swipe_pending_request": None,
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


@st.cache_data(show_spinner=False)
def _cached_extract_from_url(url: str) -> str:
    return bb.extract_supplement_text_from_url(url)


@st.cache_data(show_spinner=False)
def _cached_ocr(image_bytes: bytes) -> str:
    return bb.extract_image_text_with_blockbrain(image_bytes)


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
    variants: list[tuple[str, bytes]] = [("original", image_bytes)]
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        image = ImageOps.exif_transpose(image)

        max_side = 1800
        w, h = image.size
        if max(w, h) > max_side:
            scale = max_side / float(max(w, h))
            image = image.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)

        buf_std = io.BytesIO()
        image.save(buf_std, format="JPEG", quality=88, optimize=True)
        resized_payload = buf_std.getvalue()
        if resized_payload and resized_payload != image_bytes:
            variants.append(("resized_jpeg", resized_payload))
    except Exception:
        pass

    # Deduplicate identical byte payloads.
    unique: list[tuple[str, bytes]] = []
    seen: set[bytes] = set()
    for name, payload in variants:
        if payload in seen:
            continue
        seen.add(payload)
        unique.append((name, payload))
    return unique


def _extract_image_text_best_effort(image_bytes: bytes) -> tuple[str, str]:
    for variant_name, variant_bytes in _build_ocr_image_variants(image_bytes):
        try:
            primary = _cached_ocr(variant_bytes)
        except Exception:
            primary = ""
        text = str(primary or "").strip()
        if text:
            return text, f"Blockbrain vision OCR ({variant_name})"

    return "", ""


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


def _blockbrain_ready_error() -> str:
    api_key = str(os.getenv("BLOCKBRAIN_API_KEY", "") or "").strip()
    if not api_key:
        return (
            "Blockbrain API key is missing. Please set BLOCKBRAIN_API_KEY in environment "
            "or blockbrain/.streamlit/secrets.toml."
        )
    return ""


def _blockbrain_text_probe() -> tuple[bool, str]:
    try:
        reply = bb.call_blockbrain_text(
            "You are a connectivity checker.",
            "Reply with the exact word OK.",
            model=os.getenv("BLOCKBRAIN_MODEL_TEXT", "") or None,
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


def _render_rag_chat_popup(card: dict[str, Any], component_key: str, index: int) -> None:
    with st.popover("💬 Ask AI", use_container_width=True):
        st.caption("Ask AI research questions about this micronutrient in chat form.")
        chat_store: dict[str, list[dict[str, str]]] = st.session_state.get("swipe_rag_chats", {})
        history = list(chat_store.get(component_key, []))

        for msg in history[-12:]:
            role = "user" if str(msg.get("role", "")).lower() == "user" else "assistant"
            content = str(msg.get("content", "") or "")
            if hasattr(st, "chat_message"):
                with st.chat_message(role):
                    st.write(content)
            else:
                st.markdown(f"**{role.title()}:** {content}")

        question = st.text_input(
            "Question",
            placeholder="Example: Is this dose usually safe long-term?",
            key=f"swipe_rag_chat_input_{component_key}_{index}",
        )
        send_col, clear_col = st.columns(2)
        with send_col:
            send_clicked = st.button(
                "Send",
                type="primary",
                use_container_width=True,
                key=f"swipe_rag_send_{component_key}_{index}",
            )
        with clear_col:
            clear_clicked = st.button(
                "Clear chat",
                use_container_width=True,
                key=f"swipe_rag_clear_{component_key}_{index}",
            )

        if clear_clicked:
            chat_store[component_key] = []
            st.session_state["swipe_rag_chats"] = chat_store
            st.rerun()

        if send_clicked:
            if not question.strip():
                st.warning("Enter a question first.")
            else:
                with st.spinner("Searching local research..."):
                    chunks = _cached_rag_chunks()
                    if not chunks:
                        st.error("RAG index is not available in this environment.")
                    else:
                        scoped_query = f"{card.get('component', '')}: {question.strip()}"
                        answer, sources, _meta = bb.answer_rag_question(scoped_query, chunks)
                        sources_line = ""
                        if sources:
                            sources_line = "\n\nSources: " + ", ".join(sources[:4])
                        updated_history = history + [
                            {"role": "user", "content": question.strip()},
                            {"role": "assistant", "content": (answer or "No answer available.") + sources_line},
                        ]
                        chat_store[component_key] = updated_history
                        st.session_state["swipe_rag_chats"] = chat_store
                        st.rerun()


def _dose_label(component: dict[str, Any]) -> str:
    dose_value = component.get("dose_value")
    dose_unit = str(component.get("dose_unit", "") or "").strip()
    if dose_value is None:
        return "Dose not found"
    try:
        return f"{bb.format_float(float(dose_value))} {dose_unit}".strip()
    except Exception:
        return str(dose_value)


def _food_label(food: dict[str, Any]) -> str:
    try:
        amount_per_100g = float(food.get("amount_per_100g", 0.0) or 0.0)
    except Exception:
        amount_per_100g = 0.0
    unit_raw = str(food.get("unit", "") or "")
    amount_txt, unit_txt = bb.format_amount_unit_for_dropdown(amount_per_100g, unit_raw)
    food_name = str(food.get("food_description", "") or "").strip() or "Unknown food"
    if amount_txt and unit_txt:
        return f"{food_name} ({amount_txt} {unit_txt}/100g)"
    return food_name


def _build_swipe_cards(components: list[dict[str, Any]], details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    detail_by_component = {
        bb.normalize_lookup_key(str(d.get("component", ""))): d for d in details
    }
    cards: list[dict[str, Any]] = []
    for item in components:
        comp_name = str(item.get("component", "") or "").strip()
        comp_key = bb.normalize_lookup_key(comp_name)
        # Primary source: USDA single-ingredient whole foods, ranked by the
        # amount of THIS nutrient per 100 g (highest dose on top).
        foods: list[dict[str, Any]] = []
        try:
            foods = list(bb._build_local_food_rows_for_component(comp_key) or [])
        except Exception:
            foods = []
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
                "dose_label": _dose_label(item),
                "foods": foods,
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
    st.markdown(
        """
        <style>
            /* Keep the Streamlit top bar visible but transparent, and push content below it. */
            [data-testid="stHeader"] {
                background: transparent;
            }
            /* Tinder-style page lock: the page itself never scrolls (no left/right/up/down);
               only the swipe card moves. */
            html, body {
                overflow: hidden !important;
                overscroll-behavior: none !important;
            }
            [data-testid="stAppViewContainer"],
            [data-testid="stMain"],
            section.main {
                overflow: hidden !important;
                overscroll-behavior: none !important;
            }
            .block-container {
                overflow-x: hidden !important;
                max-width: 100vw;
            }
            [data-testid="stAppViewContainer"] {
                background:
                    radial-gradient(circle at 0% 0%, #fff4de 0%, rgba(255, 244, 222, 0.22) 45%, transparent 70%),
                    radial-gradient(circle at 100% 0%, #dff7ef 0%, rgba(223, 247, 239, 0.20) 42%, transparent 70%),
                    linear-gradient(180deg, #fefcf8 0%, #f8fbff 100%);
            }
            .block-container {
                max-width: 440px;
                padding-top: 3rem;
                padding-bottom: 0.6rem;
            }
            /* Dietary filter: horizontally scrollable pills (not a dropdown). */
            div[role="radiogroup"] {
                flex-wrap: nowrap !important;
                overflow-x: auto !important;
                gap: 6px;
                padding: 2px 0 8px 0;
                scrollbar-width: thin;
            }
            div[role="radiogroup"] > label {
                flex: 0 0 auto !important;
                border: 1px solid #d6dde7;
                background: #ffffff;
                border-radius: 999px;
                padding: 3px 12px;
                margin: 0 !important;
                white-space: nowrap;
            }
            .diet-strip-label {
                font-size: 0.72rem;
                font-weight: 800;
                letter-spacing: 0.05em;
                text-transform: uppercase;
                color: #64748b;
                margin: 0.25rem 0 0.3rem 0;
            }
            .swipe-title {
                font-size: 2.05rem;
                font-weight: 900;
                letter-spacing: 0.015em;
                line-height: 1.05;
                margin-bottom: 0.15rem;
                color: #111827;
            }
            .swipe-subtitle {
                color: #425466;
                margin-bottom: 1rem;
                font-size: 0.95rem;
            }
            .filter-shell {
                margin: 0.3rem 0 0.9rem 0;
                padding: 0.85rem 0.9rem 0.8rem 0.9rem;
                border: 1px solid #d9e2ef;
                border-radius: 22px;
                background: linear-gradient(160deg, rgba(255,255,255,0.92) 0%, rgba(247,250,255,0.92) 100%);
                box-shadow: 0 12px 26px rgba(15, 23, 42, 0.06);
            }
            .filter-topline {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 0.5rem;
                margin-bottom: 0.35rem;
            }
            .filter-title {
                font-size: 0.82rem;
                font-weight: 800;
                letter-spacing: 0.04em;
                text-transform: uppercase;
                color: #475569;
            }
            .filter-chip {
                display: inline-flex;
                align-items: center;
                gap: 0.4rem;
                padding: 0.35rem 0.7rem;
                border-radius: 999px;
                border: 1px solid #d6dde7;
                background: #ffffff;
                color: #0f172a;
                font-size: 0.8rem;
                font-weight: 700;
            }
            .filter-chip-dot {
                width: 9px;
                height: 9px;
                border-radius: 999px;
                background: #22c55e;
                box-shadow: 0 0 0 3px rgba(34, 197, 94, 0.15);
            }
            .swipe-progress {
                margin: 0 0 0.7rem 0;
                display: flex;
                justify-content: center;
                gap: 0.35rem;
            }
            .swipe-dot {
                width: 7px;
                height: 7px;
                border-radius: 999px;
                background: #cfd9e5;
            }
            .swipe-dot.active {
                width: 18px;
                background: #22c55e;
            }
            .tinder-stage {
                position: relative;
                margin: 0.05rem 0 0.35rem 0;
                min-height: 8px;
            }
            .stack-under-1,
            .stack-under-2 {
                position: absolute;
                left: 12px;
                right: 12px;
                border-radius: 28px;
                background: #e8eef6;
                border: 1px solid #d4deea;
            }
            .stack-under-1 {
                top: 14px;
                bottom: 2px;
                opacity: 0.86;
                transform: scale(0.985);
            }
            .stack-under-2 {
                top: 7px;
                bottom: 10px;
                opacity: 0.56;
                transform: scale(0.97);
            }
            .card {
                position: relative;
                border-radius: 28px;
                padding: 18px 18px 14px 18px;
                background: linear-gradient(165deg, #ffffff 0%, #f9fcff 45%, #f7fbf5 100%);
                border: 1px solid #d7e2ee;
                box-shadow:
                    0 18px 38px rgba(15, 36, 64, 0.18),
                    0 3px 8px rgba(15, 36, 64, 0.08);
                min-height: 488px;
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
            .decision-rail {
                display: flex;
                justify-content: space-between;
                margin: 0.15rem 0 0.55rem 0;
                gap: 0.6rem;
            }
            .decision-badge {
                display: inline-flex;
                align-items: center;
                justify-content: center;
                font-size: 0.73rem;
                font-weight: 800;
                border-radius: 10px;
                padding: 4px 8px;
                letter-spacing: 0.03em;
                min-width: 86px;
            }
            .decision-badge.left {
                color: #b91c1c;
                border: 1px solid #efb9b9;
                background: #fff1f1;
            }
            .decision-badge.right {
                margin-left: auto;
                color: #047857;
                border: 1px solid #9addc6;
                background: #e8fff5;
            }
            .micro-name {
                font-size: 1.8rem;
                font-weight: 900;
                color: #101a25;
                margin-bottom: 0.45rem;
                line-height: 1.08;
            }
            .dose {
                color: #1d3650;
                font-size: 1.02rem;
                margin-bottom: 0.7rem;
                font-weight: 600;
            }
            .swipe-hint {
                font-size: 0.86rem;
                color: #4c6076;
                margin-top: 0.6rem;
                margin-bottom: 0.35rem;
            }
            .action-legend {
                text-align: center;
                color: #5a6778;
                font-size: 0.78rem;
                margin: 0.3rem 0 0.55rem 0;
            }
            .card-hero {
                position: relative;
                overflow: hidden;
                border-radius: 22px;
                margin: 0.05rem 0 0.8rem 0;
                padding: 14px 14px 12px 14px;
                border: 1px solid rgba(148, 163, 184, 0.22);
                box-shadow: 0 10px 20px rgba(15, 23, 42, 0.08);
                background: var(--card-bg, linear-gradient(160deg, #ffffff 0%, #f8fbff 55%, #f4f7fb 100%));
            }
            .card-hero::before {
                content: "";
                position: absolute;
                inset: 0;
                background: linear-gradient(135deg, var(--card-accent, #64748b) 0%, transparent 42%);
                opacity: 0.18;
                pointer-events: none;
            }
            .card-hero-top {
                position: relative;
                z-index: 1;
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 0.75rem;
            }
            .card-hero-name {
                font-size: 1.42rem;
                font-weight: 900;
                line-height: 1.05;
                color: var(--card-ink, #0f172a);
                letter-spacing: -0.02em;
            }
            .card-hero-dose {
                position: relative;
                z-index: 1;
                margin-top: 0.5rem;
                color: #334155;
                font-size: 0.96rem;
                font-weight: 600;
            }
            .card-hero-pill {
                position: relative;
                z-index: 1;
                display: inline-flex;
                margin-top: 0.55rem;
                padding: 0.28rem 0.6rem;
                border-radius: 999px;
                background: var(--card-chip-bg, rgba(100, 116, 139, 0.12));
                color: var(--card-chip-text, #334155);
                font-size: 0.75rem;
                font-weight: 800;
                letter-spacing: 0.02em;
            }
            .swipe-final-card {
                border-radius: 24px;
                padding: 18px;
                background: linear-gradient(145deg, #ffffff 0%, #fff7ec 60%, #f8fbff 100%);
                border: 1px solid #e2d5c0;
                box-shadow: 0 14px 30px rgba(37, 48, 64, 0.12);
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
            .analyze-loading-spinner {
                width: 54px;
                height: 54px;
                border-radius: 999px;
                border: 4px solid #d5deeb;
                border-top-color: #16a34a;
                animation: suppswipe-spin 1s linear infinite;
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
            .tap-card-wrap {
                min-height: 380px;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                text-align: center;
                gap: 0.8rem;
            }
            .tap-card-title {
                font-size: 1.05rem;
                font-weight: 900;
                color: #132536;
            }
            .tap-card-sub {
                font-size: 0.9rem;
                color: #516476;
                max-width: 280px;
            }
            @keyframes suppswipe-spin {
                from { transform: rotate(0deg); }
                to { transform: rotate(360deg); }
            }
            div[data-testid="stVerticalBlockBorderWrapper"] {
                border-radius: 16px;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("<div class='swipe-title'>SuppSwipe</div>", unsafe_allow_html=True)


def _reset_swipe_state() -> None:
    """Clear swipe session state, but keep the chosen dietary filter."""
    saved_diet = st.session_state.get("swipe_diet_profile_id", "none")
    next_nonce = int(st.session_state.get("swipe_reset_nonce", 0)) + 1
    for key in [k for k in list(st.session_state.keys()) if k.startswith("swipe_")]:
        st.session_state.pop(key, None)
    st.session_state["swipe_reset_nonce"] = next_nonce
    _init_state()
    st.session_state["swipe_diet_profile_id"] = saved_diet


def _selected_session_in_progress() -> bool:
    if not (st.session_state.get("swipe_cards") or []):
        return False
    return bool(int(st.session_state.get("swipe_index", 0)) > 0 or (st.session_state.get("swipe_decisions") or {}))


def _extract_ean_from_text(text: str) -> str:
    for chunk in re.findall(r"\d[\d\s\-]{6,18}\d", str(text or "")):
        digits = re.sub(r"\D", "", chunk)
        if 8 <= len(digits) <= 14:
            return digits
    return ""


def _research_barcode_label(barcode: str) -> str:
    """Retrieve a supplement label for an EAN/UPC barcode.

    Tries Blockbrain's deterministic web lookup first, then falls back to an
    agentic Blockbrain research prompt so the bot can look the product up.
    """
    barcode = re.sub(r"\D", "", str(barcode or ""))
    if not (8 <= len(barcode) <= 14):
        return ""
    try:
        text, _name, _provider, _reason = bb.extract_supplement_text_from_barcode(barcode)
        if text and text.strip():
            return text.strip()
    except Exception:
        pass
    system_prompt = (
        "You are a supplement-label research assistant. Given a product barcode "
        "(EAN-13/EAN-8/UPC), identify the exact product and return its Supplement "
        "Facts / nutrition label as plain text: one nutrient per line with amount "
        "and unit (for example 'Vitamin D 25 mcg', 'Magnesium 300 mg'). Include the "
        "ingredient list if available. Never invent values. If you cannot confidently "
        "identify the product, reply with exactly NONE."
    )
    user_prompt = (
        f"Barcode: {barcode}\n"
        "Research this product and return only its supplement facts label text."
    )
    try:
        reply = str(bb.call_blockbrain_text(system_prompt, user_prompt) or "").strip()
    except Exception:
        reply = ""
    if reply and reply.upper() != "NONE":
        return reply
    return ""


def _render_dietary_pills() -> None:
    ordered_ids, profile_by_id = _dietary_profile_lookup()
    if not ordered_ids:
        return
    selected_id = bb.normalize_lookup_key(str(st.session_state.get("swipe_diet_profile_id", "none") or "none"))
    if selected_id not in profile_by_id:
        st.session_state["swipe_diet_profile_id"] = "none" if "none" in profile_by_id else ordered_ids[0]

    st.markdown("<div class='diet-strip-label'>Dietary filter</div>", unsafe_allow_html=True)
    st.radio(
        "Dietary filter",
        options=ordered_ids,
        key="swipe_diet_profile_id",
        horizontal=True,
        label_visibility="collapsed",
        format_func=lambda pid: str(profile_by_id.get(pid, {}).get("label", pid)).strip() or pid,
    )


def _run_pending_analysis() -> None:
    req = dict(st.session_state.get("swipe_pending_request") or {})

    with st.container(border=True):
        st.markdown("<div class='chip'>Analyzing…</div>", unsafe_allow_html=True)
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
            st.session_state["swipe_progress_pct"] = 0
            st.error(message)

        _set_progress(6, "Preparing AI analysis…")
        text_parts: list[str] = []

        with st.spinner("Extracting and parsing supplement info…"):
            for label, key, pct in (("uploaded image", "upload_bytes", 26), ("camera image", "camera_bytes", 42)):
                img = req.get(key)
                if isinstance(img, (bytes, bytearray)) and img:
                    _set_progress(pct, f"Reading {label}…")
                    try:
                        ocr_text, _route = _extract_image_text_best_effort(bytes(img))
                        if ocr_text.strip():
                            text_parts.append(ocr_text)
                        # Barcode fallback: if the label text is not strong, try to
                        # read an EAN from the OCR text and research the product.
                        if not bb.extraction_gate_report("\n".join(text_parts)).get("passed"):
                            ean = _extract_ean_from_text(ocr_text)
                            if ean:
                                _set_progress(min(96, pct + 8), f"Researching barcode {ean}…")
                                researched = _research_barcode_label(ean)
                                if researched:
                                    text_parts.append(researched)
                    except Exception as exc:
                        st.warning(f"Image OCR failed: {exc}")

            manual = str(req.get("manual", "") or "").strip()
            if manual:
                digits = re.sub(r"\D", "", manual)
                if re.fullmatch(r"[\d\s\-]{8,18}", manual) and 8 <= len(digits) <= 14:
                    _set_progress(56, "Researching barcode…")
                    researched = _research_barcode_label(manual)
                    if researched:
                        text_parts.append(researched)
                    else:
                        st.warning("Could not research that barcode. Try a photo or paste the label text.")
                elif re.match(r"https?://", manual, re.I):
                    _set_progress(56, "Fetching product page…")
                    try:
                        url_text = _cached_extract_from_url(manual)
                        if url_text.strip():
                            text_parts.append(url_text)
                    except Exception as exc:
                        st.warning(f"URL fetch failed: {exc}")
                else:
                    _set_progress(58, "Processing text input…")
                    text_parts.append(manual)

            combined = "\n\n".join([x for x in text_parts if str(x).strip()]).strip()
            if not combined:
                _abort("No analyzable input found. Add a photo, barcode, URL, or supplement-facts text.")
                return

            _set_progress(72, "Parsing micronutrients…")
            components = bb.parse_components(combined)
            if not components:
                _abort("No micronutrients could be parsed from the provided input.")
                return

            _set_progress(86, "Ranking whole-food alternatives from the USDA database…")
            details: list[dict[str, Any]] = []

        _set_progress(100, "Opening your first card…")

        st.session_state["swipe_cards"] = _build_swipe_cards(components, details)
        st.session_state["swipe_analysis_text"] = combined
        st.session_state["swipe_components"] = components
        st.session_state["swipe_decisions"] = {}
        st.session_state["swipe_rag_chats"] = {}
        st.session_state["swipe_index"] = 0
        st.session_state["swipe_is_analyzing"] = False
        st.session_state["swipe_pending_request"] = None
        st.session_state["swipe_progress_pct"] = 0
        st.rerun()


def _stage_analysis_from_inputs(upload_bytes: bytes, camera_bytes: bytes, manual_text: str) -> bool:
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
    }
    st.session_state["swipe_last_auto_signature"] = sig
    st.session_state["swipe_progress_pct"] = 1
    st.session_state["swipe_is_analyzing"] = True
    return True


@st.dialog("Analyze my supplement")
def _analyze_dialog() -> None:
    nonce = int(st.session_state.get("swipe_reset_nonce", 0))
    precheck_error = _blockbrain_ready_error()
    if precheck_error:
        st.error(precheck_error)
    st.caption("Analysis starts automatically once you add a photo, barcode, file, URL, or text.")

    method = st.radio(
        "How would you like to add your supplement?",
        options=["📷 Photo / Barcode", "🖼️ File / Gallery", "🔗 URL / Text"],
        key=f"dlg_method_{nonce}",
        label_visibility="collapsed",
    )

    upload_bytes = b""
    camera_bytes = b""
    manual_text = ""

    if "Photo" in method:
        # Custom back-camera component (getUserMedia facingMode 'environment').
        # Falls back to Streamlit's default camera if the component is unavailable.
        if _back_camera is not None:
            cam_value = _back_camera(key=f"dlg_backcam_{nonce}", default=None)
            camera_bytes = _decode_camera_image(cam_value)
        else:
            camera = st.camera_input(
                "Take a photo of the label or barcode",
                key=f"dlg_camera_{nonce}",
                label_visibility="collapsed",
            )
            camera_bytes = camera.getvalue() if camera is not None else b""
    elif "File" in method:
        upload = st.file_uploader(
            "Choose an image from your files or gallery",
            type=["png", "jpg", "jpeg", "webp"],
            key=f"dlg_upload_{nonce}",
            label_visibility="collapsed",
        )
        upload_bytes = upload.getvalue() if upload is not None else b""
    else:
        manual = st.text_area(
            "Paste a product URL, a barcode number, or the supplement facts text",
            height=120,
            key=f"dlg_manual_{nonce}",
            label_visibility="collapsed",
        )
        manual_text = str(manual or "").strip()

    if not precheck_error and _stage_analysis_from_inputs(upload_bytes, camera_bytes, manual_text):
        # Close the dialog and let the main app run the analysis immediately.
        st.rerun(scope="app")

    if st.button("Cancel", use_container_width=True, key=f"dlg_cancel_{nonce}"):
        st.rerun()


@st.dialog("Start over?")
def _confirm_restart_dialog() -> None:
    st.write(
        "You've already started swiping. Analyzing a new supplement will clear your "
        "current cards and decisions."
    )
    col_cancel, col_ok = st.columns(2)
    with col_cancel:
        if st.button("Cancel", use_container_width=True, key="swipe_restart_cancel"):
            st.rerun()
    with col_ok:
        if st.button("Start over", type="primary", use_container_width=True, key="swipe_restart_confirm"):
            _reset_swipe_state()
            st.session_state["swipe_open_analyze"] = True
            st.rerun()


def _render_analyze_bar() -> None:
    label = f"Analyze my Supplement {LEFT_SWIPE_ICON} → {TITLE_WHOLE_FOOD_ICON}"
    if st.button(label, type="primary", use_container_width=True, key="swipe_analyze_btn"):
        if _selected_session_in_progress():
            st.session_state["swipe_confirm_restart"] = True
        else:
            st.session_state["swipe_open_analyze"] = True
        st.rerun()


def _render_card() -> None:
    cards: list[dict[str, Any]] = st.session_state.get("swipe_cards", [])
    index = int(st.session_state.get("swipe_index", 0))
    decisions: dict[str, dict[str, Any]] = st.session_state.get("swipe_decisions", {})

    if not cards:
        with st.container(border=True):
            st.markdown(
                "<div class='tap-card-wrap'>"
                "<div class='analyze-loading-arrow' style='animation:none;'>\U0001F957</div>"
                "<div class='tap-card-title'>No supplement analyzed yet</div>"
                "<div class='tap-card-sub'>Tap \u201cAnalyze my Supplement\u201d below to scan a label or barcode, "
                "upload a photo, or paste a URL / text \u2014 your first swipe card appears here.</div>"
                "</div>",
                unsafe_allow_html=True,
            )
        return

    if index >= len(cards):
        _render_final_card(cards, decisions)
        return

    card = cards[index]
    component_key = str(card.get("component_key", "") or "")
    foods_raw: list[dict[str, Any]] = card.get("foods", []) if isinstance(card.get("foods", []), list) else []
    selected_profile = _selected_dietary_profile()
    foods = bb.apply_food_filters(foods_raw, selected_profile, use_llm_adjudication=False)

    dots = []
    for i in range(len(cards)):
        css_class = "swipe-dot active" if i == index else "swipe-dot"
        dots.append(f"<span class='{css_class}'></span>")
    st.markdown(f"<div class='swipe-progress'>{''.join(dots)}</div>", unsafe_allow_html=True)

    # The swipe card and its controls (whole-food dropdown + Ask AI) share one
    # bordered container so they read as a single card.
    theme = _component_card_theme(str(card.get("component", "") or ""))
    nonce = int(st.session_state.get("swipe_reset_nonce", 0))
    swipe_result = None
    selected_food = None
    with st.container(border=True):
        stage = st.container()  # draggable swipe card sits at the top of this card

        # --- On-card controls ---
        if foods:
            option_labels = [_food_label(food) for food in foods]
            selected_label = st.selectbox(
                "Whole-food replacement",
                options=option_labels,
                index=0,
                key=f"swipe_food_select_{component_key}_{index}",
                label_visibility="collapsed",
            )
            selected_food = foods[option_labels.index(selected_label)]
        else:
            st.caption(
                "No whole-food alternatives remain after dietary filtering."
                if foods_raw
                else "No whole-food alternatives found for this card."
            )

        _render_rag_chat_popup(card, component_key, index)

        food_label = str((selected_food or {}).get("food_description", "") or "").strip()
        with stage:
            swipe_result = tinder_swipe(
                name=str(card.get("component", "Unknown micronutrient")),
                dose=str(card.get("dose_label", "Not available")),
                food=food_label,
                index=index,
                total=len(cards),
                accent=theme["accent"],
                ink=theme["accent2"],
                bg=theme["bg"],
                canReplace=selected_food is not None,
                height=250,
                key=f"tinder_{component_key}_{index}_{nonce}",
                default=None,
            )

    # Advance only via swiping the card (Keep = left, Replace = right).
    decision = None
    if isinstance(swipe_result, dict) and swipe_result.get("dir") in ("left", "right"):
        decision = "keep" if swipe_result["dir"] == "left" else "replace"
    # Can't replace with a whole food that doesn't exist.
    if decision == "replace" and selected_food is None:
        decision = None

    if decision:
        decisions[component_key] = {
            "component_key": component_key,
            "component": card.get("component", ""),
            "dose_label": card.get("dose_label", ""),
            "decision": decision,
            "selected_food": selected_food,
            "card_index": index,
        }
        st.session_state["swipe_decisions"] = decisions
        st.session_state["swipe_index"] = index + 1
        st.rerun()


def _render_final_card(cards: list[dict[str, Any]], decisions: dict[str, dict[str, Any]]) -> None:
    with st.container(border=True):
        st.subheader("Your results")

        replace_items = [d for d in decisions.values() if d.get("decision") == "replace"]
        keep_items = [d for d in decisions.values() if d.get("decision") == "keep"]
        st.caption("Tap any item to reopen its card and change your choice.")

        st.markdown(f"**{TITLE_WHOLE_FOOD_ICON} Whole-food replacements ({len(replace_items)})**")
        if not replace_items:
            st.caption("Nothing swiped right yet.")
        for decision in replace_items:
            component_key = str(decision.get("component_key", "") or "")
            if not component_key:
                continue
            selected_food = decision.get("selected_food") or {}
            food_name = str(selected_food.get("food_description", "") or "")
            right_icon = _whole_food_icon_from_food(selected_food, component_key)
            label = f"{right_icon} {decision.get('component', 'Unknown')} ({decision.get('dose_label', '')})"
            if food_name:
                label += f" → {food_name}"
            if st.button(label, use_container_width=True, key=f"final_replace_{component_key}"):
                st.session_state["swipe_index"] = int(decision.get("card_index", 0))
                st.rerun()

        st.markdown(f"**{LEFT_SWIPE_ICON} Kept as supplements ({len(keep_items)})**")
        if not keep_items:
            st.caption("Nothing swiped left yet.")
        for decision in keep_items:
            component_key = str(decision.get("component_key", "") or "")
            if not component_key:
                continue
            label = f"{LEFT_SWIPE_ICON} {decision.get('component', 'Unknown')} ({decision.get('dose_label', '')})"
            if st.button(label, use_container_width=True, key=f"final_keep_{component_key}"):
                st.session_state["swipe_index"] = int(decision.get("card_index", 0))
                st.rerun()

    # A single Ask AI chat for the whole summary, shown once below the card.
    all_components = [str(d.get("component", "") or "") for d in decisions.values() if d.get("component")]
    summary_context = {"component": ", ".join(all_components)} if all_components else {"component": ""}
    _render_rag_chat_popup(summary_context, "summary", 0)


def _build_mobile_ui() -> None:
    _init_state()
    _render_header()
    if bool(st.session_state.get("swipe_is_analyzing", False)) and isinstance(st.session_state.get("swipe_pending_request"), dict):
        _run_pending_analysis()
    _render_card()
    _render_dietary_pills()
    _render_analyze_bar()
    if st.session_state.pop("swipe_confirm_restart", False):
        _confirm_restart_dialog()
    if st.session_state.pop("swipe_open_analyze", False):
        _analyze_dialog()


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
else:
    _build_mobile_ui()


