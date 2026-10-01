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

    key_map = {
        "BLOCKBRAIN_API_KEY": "BLOCKBRAIN_API_KEY",
        "BLOCKBRAIN_BASE_URL": "BLOCKBRAIN_BASE_URL",
        "BLOCKBRAIN_API_URL": "BLOCKBRAIN_API_URL",
        "BLOCKBRAIN_AGENT_ID": "BLOCKBRAIN_AGENT_ID",
        "BLOCKBRAIN_BOT_ID": "BLOCKBRAIN_BOT_ID",
        "BLOCKBRAIN_BOT_BASE_URL": "BLOCKBRAIN_BOT_BASE_URL",
        "BLOCKBRAIN_RESEARCH_BOT_ID": "BLOCKBRAIN_RESEARCH_BOT_ID",
        "BLOCKBRAIN_MODEL_TEXT": "BLOCKBRAIN_MODEL_TEXT",
        "BLOCKBRAIN_MODEL_VISION": "BLOCKBRAIN_MODEL_VISION",
        "BLOCKBRAIN_MODEL_GENERATION": "BLOCKBRAIN_MODEL_GENERATION",
    }
    for secret_key, env_key in key_map.items():
        if os.getenv(env_key, "").strip():
            continue
        value = str(raw.get(secret_key, "") or "").strip()
        if value:
            os.environ[env_key] = value


_bootstrap_blockbrain_env_from_secrets()

import blockbrain.app as bb  # noqa: E402
import llm_cache  # noqa: E402


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

# A deep candidate pool is fetched per nutrient so dietary filtering still leaves
# real options for restrictive diets (e.g. vegan Vitamin B1, where the top foods
# by concentration are all animal). The visible dropdown is then capped so the
# list stays manageable for unrestricted users.
SWIPE_CARD_FOOD_POOL = 250
SWIPE_CARD_DROPDOWN_MAX = 40

# Bumped on notable releases so we can confirm which build is actually live on
# Streamlit Cloud (shown as a tiny stamp under the title).
BUILD_TAG = "2026-10-01 · speed+safety"

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
def _cached_extract_from_url(url: str) -> str:
    # Raise instead of returning "": st.cache_data doesn't cache exceptions, so a
    # transient fetch/LLM failure is retried next time instead of sticking.
    text = str(bb.extract_supplement_text_from_url(url) or "")
    if not text.strip():
        raise RuntimeError("couldn't read supplement facts from that page")
    return text


@st.cache_data(show_spinner=False, ttl=6 * 3600, max_entries=64)
def _cached_ocr(image_bytes: bytes) -> str:
    text = str(bb.extract_image_text_with_blockbrain(image_bytes) or "")
    if not text.strip():
        raise RuntimeError("vision OCR returned no text")
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
    is empty or weak. The full-resolution original is never uploaded."""
    variants: list[tuple[str, bytes]] = []
    try:
        variants.extend(bb._build_blockbrain_ocr_image_variants(image_bytes))
    except Exception:
        pass
    try:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert("RGB")
        max_side = int(getattr(bb, "BLOCKBRAIN_VISION_MAX_SIDE", 2000) or 2000)
        w, h = image.size
        if max(w, h) > max_side:
            scale = max_side / float(max(w, h))
            image = image.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=88, optimize=True)
        if buf.getvalue():
            variants.append(("detail_jpeg", buf.getvalue()))
    except Exception:
        pass
    if not variants:
        variants = [("original", image_bytes)]

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
    """Return the first OCR read that passes the label-quality gate; otherwise the
    best-scoring read across variants (so a weak small-image read still gets a
    second chance at higher resolution)."""
    best_text, best_route, best_score = "", "", (-1, -1)
    for variant_name, variant_bytes in _build_ocr_image_variants(image_bytes):
        try:
            text = str(_cached_ocr(variant_bytes) or "").strip()
        except Exception:
            text = ""
        if not text:
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


def _generation_model() -> str:
    """Model for long-form answers (meal plans, benefit comparisons, Ask AI).

    Set BLOCKBRAIN_MODEL_GENERATION (Streamlit secrets or env) to use a different
    (e.g. faster) model just for these; otherwise the app's text model is used
    (BLOCKBRAIN_MODEL_TEXT or the pinned default). Benchmark candidates with
    scripts/benchmark_blockbrain_models.py.
    """
    value = ""
    try:
        value = str(st.secrets.get("BLOCKBRAIN_MODEL_GENERATION", "") or "")
    except Exception:
        value = ""
    value = value or os.getenv("BLOCKBRAIN_MODEL_GENERATION", "")
    if not value.strip():
        try:
            value = bb._get_selected_blockbrain_models()[0]
        except Exception:
            value = ""
    return str(value or "").strip()


def _stream_llm_text(
    cache_key: str,
    system_prompt: str,
    user_prompt: str,
    placeholder: Any = None,
    history: list[dict[str, str]] | None = None,
    budget_s: float | None = None,
) -> str:
    """Generate text, streaming partial output into `placeholder` (an st.empty()).

    Reuses a cached answer or a background prefetch for the same prompt when one
    exists; only non-empty answers are cached.
    """
    cached = llm_cache.get(cache_key)
    if cached:
        if placeholder is not None:
            placeholder.markdown(cached)
        return cached
    pending = llm_cache.inflight(cache_key)
    if pending is not None:
        try:
            text = str(pending.result(timeout=float(bb.BLOCKBRAIN_TOTAL_BUDGET_S)) or "").strip()
        except Exception:
            text = ""
        if text:
            if placeholder is not None:
                placeholder.markdown(text)
            return text

    def _show(partial: str) -> None:
        if placeholder is not None and partial:
            placeholder.markdown(partial + " \u258c")

    try:
        text = str(
            bb.call_blockbrain_text(
                system_prompt,
                user_prompt,
                model=_generation_model() or None,
                on_text=_show,
                history=history,
                budget_s=budget_s,
            )
            or ""
        ).strip()
    except Exception:
        text = ""
    if text:
        llm_cache.put(cache_key, text)
        if placeholder is not None:
            placeholder.markdown(text)
    elif placeholder is not None:
        placeholder.empty()
    return text


def _looks_like_extraction_json(text: str) -> bool:
    """True if the text looks like the bot's label-extraction JSON output.

    Guards Ask AI against a bot whose dual-mode system prompt isn't set up yet
    (or mis-fires): an extraction JSON blob is not a usable chat answer, so we
    discard it and fall back to the agent / local RAG.
    """
    low = str(text or "").strip().lower()
    if not low:
        return False
    return (
        '"micronutrients"' in low
        or '"identified_via"' in low
        or ('"product_name"' in low and low.lstrip().startswith(("{", "```")))
    )


_ASK_AI_HISTORY_MESSAGES = 6  # most recent chat messages sent as memory
_ASK_AI_BOT_TIMEOUT = (10, 45)  # (connect, read) seconds for the Knowledge Bot


def _ask_ai_history(component_key: str) -> list[dict[str, str]]:
    """Recent turns of this card's chat, without the appended 'Sources:' line."""
    chat_store = st.session_state.get("swipe_rag_chats", {}) or {}
    turns: list[dict[str, str]] = []
    for msg in list(chat_store.get(component_key, []))[-_ASK_AI_HISTORY_MESSAGES:]:
        role = "user" if str(msg.get("role", "")).lower() == "user" else "assistant"
        content = re.sub(r"\n\nSources: .*$", "", str(msg.get("content", "") or ""), flags=re.S).strip()
        if content:
            turns.append({"role": role, "content": content[:1500]})
    return turns


def _answer_ask_ai_question(
    component_name: str,
    question: str,
    history: list[dict[str, str]] | None = None,
    placeholder: Any = None,
) -> tuple[str | None, str]:
    """Answer an "Ask AI" question.

    Order of preference:
      1) the Blockbrain Knowledge Bot (a cortex bot with the Examine knowledge
         base attached — only bots, not agents, can hold a knowledge base). We
         send an "[ASK]" mode marker so a single dual-mode bot can tell research
         questions apart from label-extraction requests;
      2) the Blockbrain agent (general nutrition reasoning), streamed into
         `placeholder` as it is written;
      3) the local RAG index.

    `history` carries the earlier turns of this chat so follow-up questions
    ("and for vegans?") are understood. First questions (no history) are cached.

    Returns (answer, sources_line). answer is None only when nothing at all is
    available (no bot, no agent, and no local index produced a response).
    """
    history = list(history or [])
    scoped_question = f"{component_name}: {question}".strip(": ").strip()
    cache_key = ""
    if not history:
        cache_key = llm_cache.make_key("ask_ai", component_name.strip().lower(), question.strip().lower())
        cached = llm_cache.get(cache_key)
        if cached:
            return cached, ""

    history_block = ""
    if history:
        history_block = "Earlier in this conversation:\n" + "\n".join(
            f"{'User' if t['role'] == 'user' else 'Assistant'}: {t['content']}" for t in history
        ) + "\n\n"

    # 1) Preferred: the Knowledge Bot (answers from the attached Examine KB). Uses
    #    BLOCKBRAIN_RESEARCH_BOT_ID if set, otherwise the default bot. The JSON
    #    guard discards any accidental extraction-schema output so we still fall
    #    back cleanly.
    ask_message = (
        "[ASK]\n"
        f"Micronutrient / supplement component: {component_name or 'unspecified'}\n"
        f"{history_block}"
        f"Question: {question}\n\n"
        "Answer concisely and evidence-based using the connected knowledge "
        "base. General guidance only; no individual medical advice."
        + _MARKDOWN_STYLE
    )
    research_bot_id = os.getenv("BLOCKBRAIN_RESEARCH_BOT_ID", "").strip()
    try:
        bot_answer = bb.call_blockbrain_bot(
            ask_message, bot_id=(research_bot_id or None), timeout=_ASK_AI_BOT_TIMEOUT
        )
        if (
            isinstance(bot_answer, str)
            and bot_answer.strip()
            and not _looks_like_extraction_json(bot_answer)
        ):
            if cache_key:
                llm_cache.put(cache_key, bot_answer.strip())
            return bot_answer.strip(), ""
    except Exception:
        pass

    # 2) Fallback: the general agent (streamed).
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
        f"Question: {question}"
    )
    agent_answer = _stream_llm_text(
        cache_key or llm_cache.make_key("ask_ai_followup", component_name, question, history),
        system_prompt,
        user_prompt,
        placeholder=placeholder,
        history=history,
        budget_s=90,
    )
    if agent_answer:
        return agent_answer, ""

    # 3) Fallback: local research RAG index.
    try:
        chunks = _cached_rag_chunks()
    except Exception:
        chunks = []
    if not chunks:
        return None, ""
    answer, sources, _meta = bb.answer_rag_question(scoped_question, chunks)
    sources_line = ""
    if sources:
        sources_line = "\n\nSources: " + ", ".join(sources[:4])
    return (answer or "No answer available."), sources_line


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
                with st.spinner("Asking AI research assistant..."):
                    component_name = str(card.get("component", "") or "").strip()
                    stream_box = st.empty()
                    answer, sources_line = _answer_ask_ai_question(
                        component_name,
                        question.strip(),
                        history=_ask_ai_history(component_key),
                        placeholder=stream_box,
                    )
                    if answer is None:
                        st.error("AI research is not available in this environment.")
                    else:
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
        # 3 decimals so small label doses stay exact ("0.025 mg", not "0.03 mg").
        return f"{bb.format_float(float(dose_value), 3)} {dose_unit}".strip()
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
_PORTION_LARGE_G = 400.0
_PORTION_IMPRACTICAL_G = 1000.0


def _portion_practicality(grams: float | None) -> str:
    """"ok" | "large" (400-1000 g/day) | "impractical" (> 1 kg/day) for a daily food amount."""
    try:
        value = float(grams) if grams is not None else 0.0
    except Exception:
        value = 0.0
    if value > _PORTION_IMPRACTICAL_G:
        return "impractical"
    if value >= _PORTION_LARGE_G:
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
) -> str:
    """How much of `food` supplies `target_value target_unit` of the nutrient.

    Returns a short label like "~85 g (~2 eggs)", "<1 g", "a lot of food (~450
    g/day)" or "not practical from food alone (~23.5 kg/day)", or "" when it
    can't be computed. Units of the target and the food need not match — both
    are normalised by bb.grams_needed_to_match_dose; `form` (the label's "(as
    ...)" text) selects the IU / folic-acid conversions for a PILL dose.
    """
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

    practicality = _portion_practicality(grams)
    if practicality == "impractical":
        return f"not practical from food alone (~{bb.format_float(grams / 1000.0, 1)} kg/day)"
    if practicality == "large":
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
# are bb canonical nutrient keys; "match" words are a whole-word fallback for
# names the lexicon does not know. General guidance only — not individualised
# medical advice.
_MICRONUTRIENT_RDA: list[dict[str, Any]] = [
    {"display": "Vitamin B12", "unit": "mcg", "rda": 2.4, "athlete": 4.0, "keys": ["vitamin b12"], "match": ["vitamin b12", "cobalamin"]},
    {"display": "Vitamin B9 (Folate)", "unit": "mcg", "rda": 400, "athlete": 600, "keys": ["folate"], "match": ["vitamin b9", "folate", "folic", "folacin", "methylfolate"]},
    {"display": "Vitamin B7 (Biotin)", "unit": "mcg", "rda": 30, "athlete": 30, "keys": ["biotin"], "match": ["vitamin b7", "biotin"]},
    {"display": "Vitamin B6", "unit": "mg", "rda": 1.3, "athlete": 2.0, "keys": ["vitamin b6"], "match": ["vitamin b6", "pyridoxine"]},
    {"display": "Vitamin B5 (Pantothenic)", "unit": "mg", "rda": 5, "athlete": 7, "keys": ["pantothenic acid"], "match": ["vitamin b5", "pantothenic", "panthenol"]},
    {"display": "Vitamin B3 (Niacin)", "unit": "mg", "rda": 16, "athlete": 20, "keys": ["niacin"], "match": ["vitamin b3", "niacin", "nicotinamide", "nicotinic"]},
    {"display": "Vitamin B2 (Riboflavin)", "unit": "mg", "rda": 1.3, "athlete": 2.0, "keys": ["riboflavin"], "match": ["vitamin b2", "riboflavin"]},
    {"display": "Vitamin B1 (Thiamin)", "unit": "mg", "rda": 1.2, "athlete": 2.0, "keys": ["thiamin"], "match": ["vitamin b1", "thiamin", "thiamine"]},
    {"display": "Vitamin A", "unit": "mcg", "rda": 900, "athlete": 1000, "keys": ["vitamin a"], "match": ["vitamin a", "retinol", "retinyl"]},
    {"display": "Vitamin C", "unit": "mg", "rda": 90, "athlete": 200, "keys": ["vitamin c"], "match": ["vitamin c", "ascorbic"]},
    {"display": "Vitamin D", "unit": "mcg", "rda": 15, "athlete": 25, "keys": ["vitamin d"], "match": ["vitamin d", "cholecalciferol", "ergocalciferol"]},
    {"display": "Vitamin E", "unit": "mg", "rda": 15, "athlete": 20, "keys": ["vitamin e"], "match": ["vitamin e", "tocopherol", "tocopheryl", "tocotrienol"]},
    {"display": "Vitamin K", "unit": "mcg", "rda": 120, "athlete": 120, "keys": ["vitamin k", "vitamin k2"], "match": ["vitamin k", "phylloquinone", "menaquinone", "phytonadione"]},
    {"display": "Calcium", "unit": "mg", "rda": 1000, "athlete": 1300, "keys": ["calcium"], "match": ["calcium"]},
    {"display": "Phosphorus", "unit": "mg", "rda": 700, "athlete": 1000, "keys": ["phosphorus"], "match": ["phosphorus", "phosphate"]},
    {"display": "Magnesium", "unit": "mg", "rda": 400, "athlete": 500, "keys": ["magnesium"], "match": ["magnesium"]},
    {"display": "Potassium", "unit": "mg", "rda": 3400, "athlete": 3500, "keys": ["potassium"], "match": ["potassium"]},
    {"display": "Sodium", "unit": "mg", "rda": 1500, "athlete": 2300, "keys": ["sodium"], "match": ["sodium"]},
    {"display": "Chloride", "unit": "mg", "rda": 2300, "athlete": 2300, "keys": ["chloride"], "match": ["chloride"]},
    {"display": "Iron", "unit": "mg", "rda": 8, "athlete": 18, "keys": ["iron"], "match": ["iron", "ferrous", "ferric"]},
    {"display": "Zinc", "unit": "mg", "rda": 11, "athlete": 15, "keys": ["zinc"], "match": ["zinc"]},
    {"display": "Copper", "unit": "mg", "rda": 0.9, "athlete": 1.2, "keys": ["copper"], "match": ["copper", "cupric"]},
    {"display": "Manganese", "unit": "mg", "rda": 2.3, "athlete": 2.3, "keys": ["manganese"], "match": ["manganese"]},
    {"display": "Iodine", "unit": "mcg", "rda": 150, "athlete": 150, "keys": ["iodine"], "match": ["iodine", "iodide"]},
    {"display": "Selenium", "unit": "mcg", "rda": 55, "athlete": 70, "keys": ["selenium"], "match": ["selenium", "selenite", "selenomethionine"]},
    {"display": "Molybdenum", "unit": "mcg", "rda": 45, "athlete": 45, "keys": ["molybdenum"], "match": ["molybdenum"]},
    {"display": "Chromium", "unit": "mcg", "rda": 35, "athlete": 35, "keys": ["chromium"], "match": ["chromium"]},
    {"display": "Fluoride", "unit": "mg", "rda": 4, "athlete": 4, "keys": ["fluoride"], "match": ["fluoride", "fluorine"]},
    {"display": "Choline", "unit": "mg", "rda": 550, "athlete": 550, "keys": ["choline"], "match": ["choline"]},
    # EPA+DHA target; single EPA or DHA cards have no matching target of their own.
    {"display": "Omega-3 (EPA+DHA)", "unit": "g", "rda": 0.25, "athlete": 2.0, "keys": ["omega 3", "fish oil"], "match": ["omega 3", "fish oil"]},
    {"display": "Omega-3 ALA", "unit": "g", "rda": 1.6, "athlete": 1.6, "keys": ["ala"], "match": ["alpha linolenic"]},
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
        if any(_whole_word_in(m, head) for m in entry["match"]):
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
        if "carot" in form_l and "retin" not in form_l:
            return None  # beta-carotene has no UL
    if key == "folate":
        share = re.search(r"(\d+(?:\.\d+)?)\s*(mcg|mg)\s+folic acid", form_l)
        if share:
            amount = _dose_in_unit(component, share.group(1), share.group(2), entry["unit"])
            return (amount, f"{bb.format_float(float(share.group(1)))} {share.group(2)} folic acid", entry) if amount is not None else None
        if not bb._is_folic_acid_dose(component, form):
            return None  # food folate / methylfolate / DFE without a folic-acid share
    amount = _dose_in_unit(component, value, unit, entry["unit"], form)
    if amount is None:
        return None
    text = _dose_text(value, unit)
    if bb.normalize_lookup_key(str(unit or "")) in bb._IU_UNIT_KEYS:
        text += f" ({bb.format_float(amount)} {entry['unit']})"
    return amount, text, entry


def _upper_limit_warning(component_key: str, dose_value: Any, dose_unit: str, form: str = "") -> str:
    """Warning text when the PILL dose is above the adult safe upper limit, else "".

    e.g. "⚠️ 50 mg is above the safe upper limit for vitamin B6 (12 mg/day,
    EFSA) — check with a doctor before taking this long-term."
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
        f"⚠️ {dose_txt} is above the safe upper limit for {entry['name']} "
        f"({bb.format_float(float(entry['limit']))} {entry['unit']}/day, {entry['source']}) — "
        "check with a doctor before taking this long-term."
    )


def _final_upper_limit_warnings(items: list[dict[str, Any]]) -> list[str]:
    """Upper-limit warnings for kept pills (decision dicts), for the results screen."""
    out = []
    for d in items:
        warning = _upper_limit_warning(
            str(d.get("component", "") or d.get("component_key", "") or ""),
            d.get("dose_value"),
            str(d.get("dose_unit", "") or ""),
            str(d.get("form", "") or ""),
        )
        if warning:
            out.append(f"{d.get('component', '')}: {warning}")
    return out


# --- Deficiency risk, bioavailability, pricing, meal plan, share, history -----
# Lightweight feature helpers layered on top of the swipe flow. Anything that
# calls the LLM is wrapped in try/except and degrades to a curated fallback so a
# flaky network never breaks the results screen.

# Nutrients most commonly under-consumed by active people (see the Athlete RDA
# guide caption): flagged even when the kept pill dose looks adequate.
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


def _deficiency_flag(component_key: str, dose_value: Any, dose_unit: str, form: str = "") -> str:
    """Short warning when the kept pill is well below the athlete target and/or the
    nutrient is one athletes commonly fall short on. "" when nothing to flag."""
    high_risk = bb.canonical_nutrient_key(component_key) in _HIGH_RISK_NUTRIENT_KEYS
    ratio = _dose_vs_athlete_ratio(component_key, dose_value, dose_unit, form)
    if ratio is not None and ratio < 0.5:
        pct = max(1, int(round(ratio * 100)))
        tail = " — commonly under-consumed, prioritise it" if high_risk else ""
        return f"⚠️ This pill covers only ~{pct}% of the athlete daily target{tail}."
    if high_risk:
        return "⚠️ Athletes commonly fall short on this one — worth prioritising."
    return ""


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
    """Diet-specific advice that overrides "replace with food" (vegan/vegetarian B12)."""
    diet = _plant_based_diet(profile)
    if diet and bb.canonical_nutrient_key(component_key) == "vitamin b12":
        return (
            f"⚠️ On a {diet} diet whole foods aren't a reliable vitamin B12 source — "
            "keeping the supplement is recommended."
        )
    return ""


def _card_warning_text(
    component_key: str,
    dose_value: Any,
    dose_unit: str,
    form: str = "",
    profile: dict[str, Any] | None = None,
) -> str:
    """The card's warn text: an over-upper-limit warning replaces the
    deficiency / "prioritise it" flag (never both); diet advice is appended."""
    parts = []
    upper = _upper_limit_warning(component_key, dose_value, dose_unit, form)
    diet = _diet_specific_warning(component_key, profile)
    if upper:
        parts.append(upper)
    elif not diet:
        flag = _deficiency_flag(component_key, dose_value, dose_unit, form)
        if flag:
            parts.append(flag)
    if diet:
        parts.append(diet)
    if bb.canonical_nutrient_key(component_key) == "fish oil" and dose_value is not None:
        parts.append("ℹ️ The label gives the fish-oil weight; portions assume ~30% of it is EPA+DHA.")
    return " ".join(parts)


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
    "selenium": "One Brazil nut (~5 g) holds roughly 50–100 µg selenium (it varies with soil) — one nut a day is plenty; several a day can exceed the safe upper limit.",
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
_FOLIC_ACID_NOTE = (
    "Your pill is folic acid, which is absorbed ~1.7× better than food folate — so the food "
    "portion is sized to 1.7× the label amount (µg DFE)."
)


def _bioavailability_note(component_key: str, form: str = "") -> str:
    key = bb.canonical_nutrient_key(component_key)
    if key == "folate" and bb._is_folic_acid_dose(component_key, form):
        return _FOLIC_ACID_NOTE
    note = _BIOAVAILABILITY_NOTES.get(key)
    if note:
        return note
    return (
        "Whole foods deliver this nutrient with natural cofactors and a food matrix "
        "that generally improve absorption versus an isolated pill."
    )


# Approximate German discounter prices (REWE/ALDI/Lidl average, €/kg, 2024). Used
# only for a rough basket estimate — clearly labelled as approximate in the UI.
_GERMAN_FOOD_PRICE_PER_KG: list[tuple[list[str], float]] = [
    (["salmon", "lachs"], 22.0),
    (["sardine", "mackerel", "makrele", "anchovy", "hering", "herring"], 12.0),
    (["tuna", "thunfisch"], 15.0),
    (["fish", "seafood", "fisch"], 18.0),
    (["liver", "leber"], 9.0),
    (["beef", "rind"], 14.0),
    (["pork", "schwein"], 9.0),
    (["chicken", "poultry", "huhn", "hähnchen"], 8.0),
    (["egg", "eier"], 4.0),
    (["cheese", "käse"], 10.0),
    (["yogurt", "joghurt", "milk", "milch", "quark"], 1.6),
    (["almond", "mandel"], 14.0),
    (["walnut", "walnuss"], 13.0),
    (["hazelnut", "hasel"], 14.0),
    (["cashew"], 15.0),
    (["peanut", "erdnuss"], 6.0),
    (["seed", "samen", "kerne", "sunflower", "pumpkin", "chia", "flax", "lein"], 8.0),
    (["nut", "nuss"], 12.0),
    (["spinach", "spinat"], 5.0),
    (["kale", "grünkohl"], 4.0),
    (["broccoli", "brokkoli"], 3.5),
    (["cabbage", "kohl", "lettuce", "salat", "chard", "mangold", "greens"], 3.0),
    (["carrot", "möhre", "karotte"], 1.5),
    (["sweet potato", "süßkartoffel"], 3.0),
    (["potato", "kartoffel"], 1.2),
    (["bean", "bohne", "lentil", "linse", "chickpea", "kichererbse", "legume"], 3.0),
    (["tofu", "soy", "soja"], 6.0),
    (["berry", "beere", "strawberry", "erdbeere", "blueberry", "heidelbeere"], 8.0),
    (["orange", "apple", "apfel", "banana", "banane", "fruit", "obst"], 2.5),
    (["mushroom", "pilz", "champignon"], 8.0),
    (["oat", "hafer", "rice", "reis", "grain", "getreide", "bread", "brot"], 2.0),
]


def _estimate_food_price_eur(food_name: str, grams: float | None) -> float | None:
    if grams is None or grams <= 0:
        return None
    key = bb.normalize_lookup_key(food_name)
    for needles, price_per_kg in _GERMAN_FOOD_PRICE_PER_KG:
        if any(n in key for n in needles):
            return (grams / 1000.0) * price_per_kg
    return None


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


def _basket_cost_summary(replace_items: list[dict[str, Any]]) -> tuple[float, list[tuple[str, float]], list[str]]:
    rows: list[tuple[str, float]] = []
    unknown: list[str] = []
    total = 0.0
    for d in replace_items:
        name = str((d.get("selected_food") or {}).get("food_description", "") or "")
        grams = _grams_to_match_dose(d)
        cost = _estimate_food_price_eur(name, grams)
        if cost is not None and cost > 0:
            rows.append((name, cost))
            total += cost
        elif name:
            unknown.append(name)
    return total, rows, unknown


def _meal_plan_prompts(
    replace_items: list[dict[str, Any]], diet_label: str, num_meals: int = 3
) -> tuple[str, str, str]:
    """(system_prompt, user_prompt, cache_key) for the meal-plan generation."""
    n = max(1, min(3, int(num_meals or 3)))
    lines = []
    for d in replace_items:
        food = str((d.get("selected_food") or {}).get("food_description", "") or "")
        amount = _amount_to_match_dose(d)
        nutrient = str(d.get("component", "") or "")
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
        "realistic, and give each meal a short **bold** title followed by 3-5 short bullet points "
        "(ingredients with gram amounts, then one line on preparation). Keep each meal under 80 words. "
        "Respect safe intakes: if an amount is unrealistic (more than about 500 g of one food per day) "
        "or would exceed a safe upper limit (for example liver at most one small portion per week "
        "because of vitamin A, at most 2 Brazil nuts per day because of selenium), use a sensible "
        "amount instead and add one short note that a supplement may be the practical choice for that "
        "nutrient. General guidance only; no medical advice."
        + _MARKDOWN_STYLE
    )
    user_prompt = (
        "Whole foods to include, with the daily amount to aim for:\n"
        + "\n".join(lines)
        + diet_clause
        + f"\n\nWrite exactly {n} {meal_word} now."
    )
    return system_prompt, user_prompt, llm_cache.make_key("meal_plan", system_prompt, user_prompt, _generation_model())


def _generate_meal_plan(
    replace_items: list[dict[str, Any]],
    diet_label: str,
    num_meals: int = 3,
    placeholder: Any = None,
) -> str:
    if not replace_items:
        return ""
    system_prompt, user_prompt, key = _meal_plan_prompts(replace_items, diet_label, num_meals)
    return _stream_llm_text(key, system_prompt, user_prompt, placeholder=placeholder)


def _prefetch_meal_plan(replace_items: list[dict[str, Any]], diet_label: str, num_meals: int = 3) -> None:
    """Start writing the default meal plan in the background as soon as the
    results screen opens, so "Generate meals" is instant (or nearly) when tapped.
    Disable with SUPPSWIPE_PREFETCH_MEALS=0."""
    if not replace_items:
        return
    if str(os.getenv("SUPPSWIPE_PREFETCH_MEALS", "1") or "1").strip().lower() in {"0", "false", "off", "no"}:
        return
    if not os.getenv("BLOCKBRAIN_API_KEY", "").strip():
        try:
            if not str(st.secrets.get("BLOCKBRAIN_API_KEY", "") or "").strip():
                return
        except Exception:
            return
    system_prompt, user_prompt, key = _meal_plan_prompts(replace_items, diet_label, num_meals)
    model = _generation_model() or None
    llm_cache.submit(key, lambda: bb.call_blockbrain_text(system_prompt, user_prompt, model=model))


def _benefits_prompts(replace_items: list[dict[str, Any]]) -> tuple[str, str, str] | None:
    lines = []
    for d in replace_items:
        nutrient = str(d.get("component", "") or "")
        food = str((d.get("selected_food") or {}).get("food_description", "") or "")
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
        "selenium). For each item use this compact structure: a bold heading '<Nutrient> \→ <Food>', "
        "then '💊 Pill alone:' with one short line, then '🥗 Whole food also gives:' "
        "with 3-4 short bullets. Be concise and evidence-based. General guidance only; no individual "
        "medical advice."
        + _MARKDOWN_STYLE
    )
    user_prompt = "Pairings:\n" + "\n".join(lines) + "\n\nWrite the comparison now."
    return system_prompt, user_prompt, llm_cache.make_key("benefits", system_prompt, user_prompt, _generation_model())


def _generate_whole_food_benefits(replace_items: list[dict[str, Any]], placeholder: Any = None) -> str:
    """Contrast the isolated pill nutrient vs. the fuller benefits of the chosen whole food."""
    if not replace_items:
        return ""
    prompts = _benefits_prompts(replace_items)
    if prompts is None:
        return ""
    system_prompt, user_prompt, key = prompts
    return _stream_llm_text(key, system_prompt, user_prompt, placeholder=placeholder)


def _supplement_search_links(keep_items: list[dict[str, Any]]) -> tuple[str, dict[str, str]]:
    import urllib.parse

    names = [str(d.get("component", "") or "").strip() for d in keep_items if d.get("component")]
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
            food = str((d.get("selected_food") or {}).get("food_description", "") or "")
            out.append(f"  • {d.get('component', '')}: {food} ({_amount_to_match_dose(d)})")
    else:
        out.append("  • (none)")
    out.append("")
    out.append(f"💊 Kept as a supplement ({len(keep_items)}):")
    if keep_items:
        for d in keep_items:
            out.append(f"  • {d.get('component', '')} {d.get('dose_label', '')}".rstrip())
    else:
        out.append("  • (none)")
    if meal_plan.strip():
        out += ["", "🍽️ Meal plan:", meal_plan.strip()]
    out += ["", "Made with SuppSwipe — ditch the pill, eat the real thing."]
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


def _sync_scan_history_with_browser() -> None:
    """Render the invisible storage component (once per run, at the end of the
    page): persist any pending change and pull the device's stored history in
    on first load, merged with anything recorded before it arrived."""
    if _history_store is None:
        return
    pending = st.session_state.pop("_suppswipe_history_save", None)
    clear = bool(st.session_state.pop("_suppswipe_history_clear", False))
    try:
        stored = _history_store(save=pending, clear=clear, key="suppswipe_history_store", default=None)
    except Exception:
        return
    if isinstance(stored, list) and not st.session_state.get("_suppswipe_history_loaded"):
        st.session_state["_suppswipe_history_loaded"] = True
        current = _load_scan_history()
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for entry in [e for e in stored if isinstance(e, dict)] + current:
            sig = repr(sorted((k, repr(v)) for k, v in entry.items()))
            if sig in seen:
                continue
            seen.add(sig)
            merged.append(entry)
        st.session_state["suppswipe_scan_history"] = merged[-_HISTORY_MAX:]
        if len(merged) != len(stored):
            st.session_state["_suppswipe_history_save"] = merged[-_HISTORY_MAX:]
        st.rerun()


def _record_scan_to_history(decisions: dict[str, dict[str, Any]], diet_label: str) -> None:
    if not decisions:
        return
    sig = str(st.session_state.get("swipe_last_auto_signature", "") or "")
    if sig and sig == str(st.session_state.get("swipe_history_recorded_sig", "") or ""):
        return
    import time

    entry = {
        "ts": time.strftime("%Y-%m-%d %H:%M"),
        "diet": diet_label,
        "kept": [
            {"component": str(d.get("component", "") or ""), "dose": str(d.get("dose_label", "") or "")}
            for d in decisions.values()
            if d.get("decision") == "keep"
        ],
        "replaced": [
            {
                "component": str(d.get("component", "") or ""),
                "food": str((d.get("selected_food") or {}).get("food_description", "") or ""),
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
    with st.popover(f"🕘 Recent scans ({len(history)})", use_container_width=True):
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
                st.markdown(f"- 🥗 {r.get('component', '')} → {r.get('food', '')} ({r.get('amount', '')})")
            for k in kept:
                st.markdown(f"- 💊 {k.get('component', '')} {k.get('dose', '')}".rstrip())
            st.divider()
        if st.button("Clear history", use_container_width=True, key="swipe_clear_history"):
            st.session_state["suppswipe_scan_history"] = []
            st.session_state["_suppswipe_history_clear"] = True
            st.rerun()


def _render_final_actions(
    keep_items: list[dict[str, Any]],
    replace_items: list[dict[str, Any]],
    diet_label: str,
) -> None:
    row1 = st.columns(2)
    with row1[0]:
        with st.popover("🍽️ Meal plan", use_container_width=True):
            st.caption("Turn your whole-food swaps into meals that use all of them.")
            if not replace_items:
                st.info("Swipe right on at least one nutrient to build a meal plan.")
            else:
                num_meals = st.radio(
                    "How many meals?",
                    options=[1, 2, 3],
                    index=2,
                    horizontal=True,
                    key="swipe_meal_count",
                    format_func=lambda m: f"{m} meal" if m == 1 else f"{m} meals",
                )
                _sys, _usr, plan_key = _meal_plan_prompts(replace_items, diet_label, int(num_meals))
                ready = llm_cache.get(plan_key)
                plan_box = st.empty()
                if ready:
                    plan_box.markdown(ready)
                    st.session_state["swipe_meal_plan"] = ready
                    if st.button("🔄 Different meals", use_container_width=True, key="swipe_regen_meal"):
                        llm_cache.drop(plan_key)
                        with st.spinner("Cooking up new meals…"):
                            st.session_state["swipe_meal_plan"] = _generate_meal_plan(
                                replace_items, diet_label, int(num_meals), placeholder=plan_box
                            )
                elif st.button("Generate meals", type="primary", use_container_width=True, key="swipe_gen_meal"):
                    with st.spinner("Cooking up your meals…"):
                        plan = _generate_meal_plan(replace_items, diet_label, int(num_meals), placeholder=plan_box)
                    st.session_state["swipe_meal_plan"] = plan
                    if not plan:
                        st.warning("Couldn't generate meals right now — please try again.")
                elif llm_cache.inflight(plan_key) is not None:
                    st.caption("⚡ Already preparing your meals in the background — tap Generate to see them.")
    with row1[1]:
        with st.popover("🛒 Grocery cost", use_container_width=True):
            st.caption("Rough daily cost of your swaps at German discounters (REWE/ALDI/Lidl average).")
            total, rows, unknown = _basket_cost_summary(replace_items)
            if not rows and not unknown:
                st.info("No whole-food swaps to price yet.")
            else:
                for name, cost in rows:
                    st.markdown(f"- {name}: ~€{cost:.2f}/day")
                if total > 0:
                    st.markdown(f"**≈ €{total:.2f}/day · €{total * 7:.2f}/week**")
                if unknown:
                    st.caption("No estimate for: " + ", ".join(unknown))
                st.caption("Approximate 2024 discounter prices — actual prices vary by shop and season.")
    row2 = st.columns(2)
    with row2[0]:
        with st.popover("💊 Cheapest combo", use_container_width=True):
            st.caption("Find one all-in-one product covering the pills you kept.")
            if not keep_items:
                st.info("You didn't keep any supplements — nothing to buy!")
            else:
                _query, links = _supplement_search_links(keep_items)
                covers = ", ".join(dict.fromkeys(str(d.get("component", "") or "") for d in keep_items if d.get("component")))
                st.markdown(f"**Covers:** {covers}")
                for label, url in links.items():
                    st.markdown(f"- [{label}]({url})")
                st.caption("Links open a live search so you can compare real products and prices. Not medical or purchase advice.")
    with row2[1]:
        with st.popover("📤 Share", use_container_width=True):
            st.caption("Copy or download your results.")
            share_text = _build_share_text(
                keep_items, replace_items, str(st.session_state.get("swipe_meal_plan", "") or "")
            )
            st.code(share_text)
            st.download_button(
                "Download as text",
                data=share_text,
                file_name="suppswipe_results.txt",
                mime="text/plain",
                use_container_width=True,
                key="swipe_share_dl",
            )

    with st.popover("🌱 Pill vs whole-food benefits", use_container_width=True):
        st.caption(
            "See how much MORE you get by eating the whole food instead of just the isolated pill."
        )
        if not replace_items:
            st.info("Swipe right on at least one nutrient to compare benefits.")
        else:
            prompts = _benefits_prompts(replace_items)
            ready = llm_cache.get(prompts[2]) if prompts else None
            benefits_box = st.empty()
            if ready:
                benefits_box.markdown(ready)
            elif st.button(
                "Show benefit comparison",
                type="primary",
                use_container_width=True,
                key="swipe_gen_benefits",
            ):
                with st.spinner("Gathering whole-food benefits…"):
                    benefits = _generate_whole_food_benefits(replace_items, placeholder=benefits_box)
                if not benefits:
                    st.warning("Couldn't fetch the comparison right now — please try again.")


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
    return bool(bb.canonical_nutrient_key(str(name or "")))


def _filter_to_micronutrients(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop anything that is not a micronutrient so users only swipe real nutrients."""
    return [c for c in components if _is_micronutrient(str(c.get("component", "") or ""))]


# --- One card per nutrient ----------------------------------------------------
# Cards that resolve to the same nutrient are merged (they would otherwise
# overwrite each other's swipe decision). The dosed row is kept; doses are
# summed ONLY when the label genuinely lists the nutrient on separate lines in
# different forms (e.g. vitamin A as retinyl palmitate + as beta-carotene).
# Same-form repeats — OCR echoes, a dose-less ingredient-list mention — are
# duplicates, not extra dose. Omega-3 rows ("Fish oil 1000 mg / EPA 180 mg /
# DHA 120 mg") become ONE EPA+DHA card; the fish-oil weight is the carrier,
# not an omega-3 amount.
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
    return merged


def _merge_same_nutrient_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    dosed = [r for r in rows if r.get("dose_value") is not None]
    if not dosed:
        return dict(rows[0])
    if len(dosed) > 1:
        forms = [bb.normalize_lookup_key(str(r.get("form", "") or "")) for r in dosed]
        lines = [str(r.get("label_line", "") or "") for r in dosed]
        if all(forms) and all(lines) and len(set(forms)) == len(forms) and len(set(lines)) == len(lines):
            summed = _sum_distinct_form_doses(dosed)
            if summed is not None:
                return summed
    return dict(dosed[0])


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
        foods: list[dict[str, Any]] = []
        try:
            foods = list(bb._build_local_food_rows_for_component(comp_name, limit=SWIPE_CARD_FOOD_POOL) or [])
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
                "nutrient_key": _component_nutrient_key(item),
                "dose_label": _dose_label(item),
                "dose_value": item.get("dose_value"),
                "dose_unit": str(item.get("dose_unit", "") or ""),
                "form": str(item.get("form", "") or ""),
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
                padding-top: 0.8rem;
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
            .portion-hint {
                margin: 0.15rem 0 0.35rem 0;
                padding: 0.5rem 0.7rem;
                border-radius: 12px;
                background: #f1f7f2;
                border: 1px solid #cfe6d5;
                color: #234a32;
                font-size: 0.85rem;
                line-height: 1.5;
            }
            .deficiency-flag {
                margin: 0 0 0.45rem 0;
                padding: 0.4rem 0.6rem;
                border-radius: 10px;
                background: #fff5f5;
                border: 1px solid #f3c0c0;
                color: #9b1c1c;
                font-size: 0.78rem;
                font-weight: 700;
                line-height: 1.35;
            }
            .bioavail-note {
                margin: 0.1rem 0 0.35rem 0;
                padding: 0.45rem 0.6rem;
                border-radius: 10px;
                background: #f3f8ff;
                border: 1px solid #cfe0f2;
                color: #24425f;
                font-size: 0.8rem;
                line-height: 1.45;
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
    """Retrieve a supplement label for an EAN/UPC barcode via trusted product
    databases (OpenFoodFacts + secondary lookups only).

    We deliberately do NOT ask the LLM to guess a product from a bare barcode
    number: language models have no reliable barcode→product mapping and will
    confidently hallucinate an unrelated label (e.g. returning a generic
    multivitamin — whose vitamin K then maps to parsley — for a turmeric
    product). When the databases don't know the code we return "" so the caller
    can ask the user to photograph the label instead, which we can research
    reliably from the product name.
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
    return ""


def _research_product_from_label_text(label_text: str) -> tuple[str, str]:
    """Identify a supplement from text read off a product photo and return
    (supplement facts text, source URL).

    Used when a photo shows the product (brand / product name / marketing copy)
    but not a complete, readable Supplement Facts panel. The agent may use its
    web tools for this one call so values come from a real product page rather
    than the model's memory; the result is still flagged to the user as
    AI-researched (see swipe_label_source) because it was not read off the photo.
    """
    snippet = str(label_text or "").strip()
    if len(snippet) < 3:
        return "", ""
    snippet = snippet[:1200]
    system_prompt = (
        "You are a supplement-label research assistant. You are given raw text read "
        "from a photo of a supplement product (often the front of the pack: brand, "
        "product name, and marketing text). Identify the exact product, look it up "
        "online (manufacturer page or a major retailer listing), and return its full "
        "Supplement Facts / nutrition label as plain text. First line: 'Source: <URL of "
        "the page you used>'. Then one nutrient or active ingredient per line with "
        "amount and unit (for example 'Vitamin D 25 mcg', 'Magnesium 300 mg', "
        "'Curcumin 500 mg'). Never invent or estimate values. If you cannot confidently "
        "identify the product and find its label, reply with exactly NONE."
    )
    user_prompt = (
        "Text read from the product photo:\n"
        f"{snippet}\n\n"
        "Identify the product and return only the source line and its supplement facts label text."
    )
    try:
        reply = str(
            bb.call_blockbrain_text(system_prompt, user_prompt, allow_tools=True, budget_s=90) or ""
        ).strip()
    except Exception:
        reply = ""
    if not reply or reply.upper().strip(" .") == "NONE":
        return "", ""
    source_url = ""
    m = re.search(r"^\s*\**source\**\s*:\s*(\S+)", reply, flags=re.I | re.M)
    if m:
        source_url = m.group(1).strip("<>()[]")
        reply = (reply[: m.start()] + reply[m.end():]).strip()
    if not re.match(r"https?://", source_url, flags=re.I):
        source_url = ""
    return reply, source_url


def _on_diet_profile_change() -> None:
    """Persist the dietary filter into a plain (non-widget) session key.

    Streamlit clears a widget's keyed state whenever that widget isn't rendered
    on a run. A swipe calls st.rerun() before the dietary pills render, so the
    radio's own key was being garbage-collected and the filter reset to "No
    restriction". Mirroring the choice into `swipe_diet_profile_id` (never used
    as a widget key) keeps it across swipes.
    """
    st.session_state["swipe_diet_profile_id"] = str(
        st.session_state.get("swipe_diet_radio", "none") or "none"
    )


def _render_dietary_pills() -> None:
    ordered_ids, profile_by_id = _dietary_profile_lookup()
    if not ordered_ids:
        return
    selected_id = bb.normalize_lookup_key(str(st.session_state.get("swipe_diet_profile_id", "none") or "none"))
    if selected_id not in profile_by_id:
        selected_id = "none" if "none" in profile_by_id else ordered_ids[0]
        st.session_state["swipe_diet_profile_id"] = selected_id

    st.markdown("<div class='diet-strip-label'>Dietary filter</div>", unsafe_allow_html=True)
    st.radio(
        "Dietary filter",
        options=ordered_ids,
        index=ordered_ids.index(selected_id),
        key="swipe_diet_radio",
        on_change=_on_diet_profile_change,
        horizontal=True,
        label_visibility="collapsed",
        format_func=lambda pid: str(profile_by_id.get(pid, {}).get("label", pid)).strip() or pid,
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
            st.session_state["swipe_analysis_kicked"] = False
            st.session_state["swipe_progress_pct"] = 0
            st.error(message)

        _set_progress(6, "Preparing AI analysis…")
        text_parts: list[str] = []
        # Where the doses came from; "ai_research" is surfaced on every card so the
        # user knows the values were looked up, not read from their own photo.
        label_source: dict[str, str] = {"kind": "input", "url": ""}

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
                        # Product-name fallback: if we still don't have a readable
                        # facts panel, treat the photo as a product shot and research
                        # the label from its visible brand / product name.
                        if ocr_text.strip() and not bb.extraction_gate_report("\n".join(text_parts)).get("passed"):
                            _set_progress(min(96, pct + 6), "Researching the product from the label…")
                            researched_name, source_url = _research_product_from_label_text(ocr_text)
                            if researched_name:
                                text_parts.append(researched_name)
                                label_source = {"kind": "ai_research", "url": source_url}
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
                        st.warning(
                            "I couldn't find that barcode in the product databases. "
                            "Snap a photo of the label (the front of the pack or the "
                            "Supplement Facts panel) instead — I'll research the product "
                            "from the photo."
                        )
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

            # Keep only scientifically recognised micronutrients (vitamins +
            # minerals, plus choline / omega-3 unless _STRICT_MICRONUTRIENTS_ONLY).
            # This drops macronutrients (protein/fat/carbs/sugar/calories),
            # fillers and label metadata so the user only swipes real nutrients.
            components = _filter_to_micronutrients(components)
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
    st.session_state["swipe_analysis_kicked"] = False
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
    _render_scan_history_popover()


def _render_label_source_notice() -> None:
    """Warn when the doses were researched online by AI instead of read from the
    user's own photo (front-of-pack photos without a readable facts panel)."""
    source = st.session_state.get("swipe_label_source") or {}
    if str(source.get("kind", "") or "") != "ai_research":
        return
    url = str(source.get("url", "") or "")
    where = f" ([source]({url}))" if url else ""
    st.caption(
        f"⚠️ These doses were looked up online by AI from the product name{where}, "
        "not read from your photo — check them against your pack."
    )


def _render_card() -> None:
    cards: list[dict[str, Any]] = st.session_state.get("swipe_cards", [])
    index = int(st.session_state.get("swipe_index", 0))
    decisions: dict[str, dict[str, Any]] = st.session_state.get("swipe_decisions", {})

    if not cards:
        with st.container(border=True):
            st.markdown(
                "<div class='tap-card-wrap'>"
                "<div style='font-size:2.4rem;line-height:1.2;letter-spacing:0.1em;'>💊 &#8594; 🥦</div>"
                "<div class='tap-card-title' style='font-size:1.1rem;margin-top:0.5rem;'>Ditch the pill. Eat the real thing.</div>"
                "<div class='tap-card-sub' style='max-width:300px;'>"
                "Whole foods are <em>generally superior</em> to synthetic supplements — "
                "more bioavailable, naturally balanced, and packed with synergistic co-nutrients "
                "no pill can replicate."
                "</div>"
                "<div class='tap-card-sub' style='max-width:300px;margin-top:0.5rem;'>"
                "📸 Scan your supplement label, then <strong>swipe right</strong> to replace each nutrient "
                "with its whole-food equivalent — or <strong>swipe left</strong> to keep it."
                "</div>"
                "<div class='tap-card-sub' style='max-width:300px;margin-top:0.5rem;'>"
                "🥗 <strong>Vegan? Keto? Nut-free?</strong> Set your dietary filter below and only "
                "whole foods that fit <em>your</em> lifestyle will be suggested."
                "</div>"
                "<div class='tap-card-sub' style='max-width:300px;margin-top:0.5rem;'>"
                "🤖 Not sure about a swap? Tap <strong>Ask AI</strong> on any card for science-backed answers."
                "</div>"
                "<div style='margin-top:1rem;font-size:0.95rem;font-weight:800;color:#047857;' aria-label='To get started, tap the Analyze my Supplement button below'>"
                "Ready? &#8594; tap <em>Analyze my Supplement</em> below &#8595;"
                "</div>"
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
    # Filter the (possibly deep) pool by the dietary profile, then cap the
    # visible dropdown (highest concentration first) so the list stays manageable.
    foods = bb.apply_food_filters(foods_raw, selected_profile, use_llm_adjudication=False)[:SWIPE_CARD_DROPDOWN_MAX]
    # Self-heal: if there is nothing to show (the stored pool was empty, OR a
    # stale/shallow pool built by an older version got filtered away by the
    # dietary profile), re-fetch the deep pool live and retry. This applies the
    # resolver + deeper-pool fixes (e.g. Vitamin E) to already-built cards
    # without re-analysing the supplement.
    if not foods and component_key:
        try:
            deep_pool = list(bb._build_local_food_rows_for_component(component_key, limit=SWIPE_CARD_FOOD_POOL) or [])
        except Exception:
            deep_pool = []
        if deep_pool and deep_pool != foods_raw:
            card["foods"] = deep_pool
            foods_raw = deep_pool
            foods = bb.apply_food_filters(deep_pool, selected_profile, use_llm_adjudication=False)[:SWIPE_CARD_DROPDOWN_MAX]

    dots = []
    for i in range(len(cards)):
        css_class = "swipe-dot active" if i == index else "swipe-dot"
        dots.append(f"<span class='{css_class}'></span>")
    st.markdown(f"<div class='swipe-progress'>{''.join(dots)}</div>", unsafe_allow_html=True)
    _render_label_source_notice()

    # The swipe card and its controls (whole-food dropdown + Ask AI) share one
    # bordered container so they read as a single card.
    theme = _component_card_theme(str(card.get("component", "") or ""))
    nonce = int(st.session_state.get("swipe_reset_nonce", 0))
    swipe_result = None
    selected_food = None
    match_dose_txt = ""
    rda_amount_txt = ""
    rda_label_txt = ""
    with st.container(border=True):
        # Computed here but shown INSIDE the swipe card (passed as `warn` below),
        # so only the dropdown / Ask AI / dietary filter sit below the card.
        card_form = str(card.get("form", "") or "")
        warn_text = _card_warning_text(
            component_key, card.get("dose_value"), str(card.get("dose_unit", "") or ""), card_form, selected_profile
        )
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

            # For the selected whole food, compute how much to eat to (a) match
            # the supplement dose and (b) reach the athlete daily target. These
            # are rendered INSIDE the swipe card (passed as props below).
            comp_name = str(card.get("component", "") or "")
            match_dose_txt = _portion_for_target(
                selected_food, card.get("dose_value"), str(card.get("dose_unit", "") or ""), comp_name, card_form
            )
            rda_entry = _rda_for_component(component_key)
            if rda_entry is not None:
                # The target is a food amount (e.g. folate in DFE), so it is
                # named by the RDA entry, not by the pill's form.
                rda_amount_txt = _portion_for_target(
                    selected_food, rda_entry["athlete"], str(rda_entry["unit"]), str(rda_entry["display"])
                )
                if rda_amount_txt:
                    rda_label_txt = _format_rda_target(rda_entry)
        else:
            if foods_raw:
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
        bio_note = _bioavailability_note(component_key, card_form) if selected_food is not None else ""

        _render_rag_chat_popup(card, component_key, index)

        food_label = str((selected_food or {}).get("food_description", "") or "").strip()
        with stage:
            swipe_result = tinder_swipe(
                name=str(card.get("component", "Unknown micronutrient")),
                dose=str(card.get("dose_label", "Not available")),
                food=food_label,
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
                canReplace=selected_food is not None,
                height=400,
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
            "dose_value": card.get("dose_value"),
            "dose_unit": card.get("dose_unit", ""),
            "form": card_form,
            "decision": decision,
            "selected_food": selected_food,
            "card_index": index,
        }
        st.session_state["swipe_decisions"] = decisions
        st.session_state["swipe_index"] = index + 1
        st.rerun()


def _render_final_card(cards: list[dict[str, Any]], decisions: dict[str, dict[str, Any]]) -> None:
    replace_items = [d for d in decisions.values() if d.get("decision") == "replace"]
    keep_items = [d for d in decisions.values() if d.get("decision") == "keep"]

    with st.container(border=True):
        st.subheader("Your results")
        st.caption("Tap any nutrient to go back to its card and change your choice.")

        # Two columns of tappable nutrients: kept supplements (left) vs
        # whole-food swaps (right). Tapping one reopens that micronutrient's card.
        col_keep, col_replace = st.columns(2)
        with col_keep:
            st.markdown(f"**{LEFT_SWIPE_ICON} Kept ({len(keep_items)})**")
            if keep_items:
                for d in keep_items:
                    component_key = str(d.get("component_key", "") or "")
                    dose = str(d.get("dose_label", "") or "")
                    label = f"{LEFT_SWIPE_ICON} {d.get('component', 'Unknown')}"
                    if dose:
                        label += f" · {dose}"
                    if st.button(
                        label,
                        use_container_width=True,
                        key=f"final_keep_{component_key}",
                    ):
                        st.session_state["swipe_index"] = int(d.get("card_index", 0))
                        st.rerun()
                # Kept pills above the safe upper limit stay flagged on the results.
                for warning in _final_upper_limit_warnings(keep_items):
                    st.caption(warning)
            else:
                st.caption("Nothing swiped left.")
        with col_replace:
            st.markdown(f"**{TITLE_WHOLE_FOOD_ICON} Replaced ({len(replace_items)})**")
            if replace_items:
                for d in replace_items:
                    component_key = str(d.get("component_key", "") or "")
                    food = d.get("selected_food") or {}
                    food_name = str(food.get("food_description", "") or "")
                    icon = _whole_food_icon_from_food(food, component_key)
                    amount_txt = _amount_to_match_dose(d)
                    detail = food_name + (f" ({amount_txt})" if (food_name and amount_txt) else "")
                    label = f"{icon} {d.get('component', 'Unknown')}"
                    if detail:
                        label += f" → {detail}"
                    if st.button(
                        label,
                        use_container_width=True,
                        key=f"final_repl_{component_key}",
                    ):
                        st.session_state["swipe_index"] = int(d.get("card_index", 0))
                        st.rerun()
            else:
                st.caption("Nothing swiped right.")

    # Record this completed scan to the on-device history (once per analysis).
    diet_label = str((_selected_dietary_profile() or {}).get("label", "") or "")
    _record_scan_to_history(decisions, diet_label)

    # Start writing the default (3-meal) plan in the background right away.
    _prefetch_meal_plan(replace_items, diet_label, 3)

    # Action row: meal plan, German grocery cost, all-in-one supplement finder, share.
    _render_final_actions(keep_items, replace_items, diet_label)

    # A single Ask AI chat for the whole summary, shown once below the card.
    all_components = [str(d.get("component", "") or "") for d in decisions.values() if d.get("component")]
    summary_context = {"component": ", ".join(all_components)} if all_components else {"component": ""}
    _render_rag_chat_popup(summary_context, "summary", 0)

    # Athlete RDA reference guide, shown once directly below Ask AI on the results screen.
    _render_athlete_rda_popup()


def _render_athlete_rda_popup() -> None:
    """Static reference: approximate daily micronutrient targets for athletes.

    Values are approximate consensus figures from ISSN (Nutrient Timing, 2017),
    ACSM/AND/DC Nutrition and Athletic Performance (2016/2021), and NIH Office
    of Dietary Supplements RDA fact sheets. General guidance only — shown as a
    popover so it works with the app's no-scroll layout.
    """
    with st.popover("\U0001F3C3 Athlete RDA guide", use_container_width=True):
        st.caption(
            "Approximate daily targets for every micronutrient the app tracks. "
            "Adult RDA/AI from NIH ODS; athlete targets raised per ISSN and "
            "ACSM/AND/DC where training increases needs or sweat losses. General "
            "guidance only — consult a sports dietitian for personalised advice."
        )
        st.table(
            [
                {
                    "Nutrient": str(entry["display"]),
                    "Unit": str(entry["unit"]),
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


def _render_debug_panel() -> None:
    """Shown only with ?debug=1: which endpoint/model answered the last LLM call
    and how long it took (time-to-first-token and total)."""
    with st.expander("🛠 Diagnostics", expanded=False):
        st.json(
            {
                "build": BUILD_TAG,
                "generation_model": _generation_model(),
                "last_call": dict(getattr(bb, "LAST_BLOCKBRAIN_TIMING", {}) or {}),
                "last_error": str(getattr(bb, "LAST_BLOCKBRAIN_ERROR", "") or ""),
            }
        )


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
    try:
        show_debug = str(st.query_params.get("debug", "") or "") == "1"
    except Exception:
        show_debug = False
    if show_debug:
        _render_debug_panel()
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
