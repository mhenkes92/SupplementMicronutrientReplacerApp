from __future__ import annotations

import json
import importlib
import re
from pathlib import Path
from typing import Any

try:
    _llama_module = importlib.import_module("llama_cpp")
    Llama = getattr(_llama_module, "Llama", None)
except Exception:
    Llama = None


DOSE_PATTERN = re.compile(r"(?P<name>[A-Za-z][A-Za-z0-9\-\s\(\)\+/]{1,80}?)\s*(?P<dose>\d+(?:[\.,]\d+)?)\s*(?P<unit>mg|mcg|ug|µg|μg|g|iu)\b", re.I)
TOKEN_PATTERN = re.compile(r"[a-z0-9]+")

DEFAULT_RAG_CHUNKS = [
    {
        "source": "local_guidance",
        "text": "Vitamin D supports bone and immune health. Practical food sources include salmon, sardines, egg yolk, and fortified dairy.",
    },
    {
        "source": "local_guidance",
        "text": "Magnesium-rich foods include pumpkin seeds, almonds, spinach, black beans, and whole grains.",
    },
    {
        "source": "local_guidance",
        "text": "Vitamin C can be obtained from bell peppers, kiwi, citrus fruits, broccoli, and strawberries.",
    },
    {
        "source": "local_guidance",
        "text": "Iron-rich foods include beef, lentils, spinach, tofu, and pumpkin seeds. Pair plant iron with vitamin C foods.",
    },
]

FOOD_MAP: dict[str, list[str]] = {
    "vitamin a": ["carrot", "sweet potato", "spinach", "egg yolk"],
    "vitamin b1": ["sunflower seeds", "pork", "beans", "whole grains"],
    "vitamin b2": ["eggs", "almonds", "yogurt", "mushrooms"],
    "vitamin b3": ["chicken", "tuna", "turkey", "peanuts"],
    "vitamin b5": ["mushrooms", "avocado", "chicken", "lentils"],
    "vitamin b6": ["chickpeas", "banana", "salmon", "potato"],
    "vitamin b7": ["egg yolk", "nuts", "seeds", "sweet potato"],
    "vitamin b9": ["spinach", "lentils", "beans", "asparagus"],
    "vitamin b12": ["salmon", "beef", "eggs", "dairy"],
    "vitamin c": ["bell pepper", "kiwi", "orange", "broccoli"],
    "vitamin d": ["salmon", "sardines", "egg yolk", "fortified dairy"],
    "vitamin e": ["almonds", "sunflower seeds", "avocado", "spinach"],
    "vitamin k": ["kale", "spinach", "broccoli", "brussels sprouts"],
    "calcium": ["yogurt", "sardines", "tofu", "kale"],
    "magnesium": ["pumpkin seeds", "almonds", "spinach", "black beans"],
    "iron": ["beef", "lentils", "spinach", "tofu"],
    "zinc": ["beef", "pumpkin seeds", "chickpeas", "cashews"],
    "selenium": ["brazil nuts", "tuna", "eggs", "sunflower seeds"],
    "iodine": ["seaweed", "dairy", "cod", "eggs"],
}


def normalize_key(text: str) -> str:
    value = (text or "").strip().lower()
    value = re.sub(r"[^a-z0-9\s\-\+]", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def tokenize(text: str) -> set[str]:
    return set(TOKEN_PATTERN.findall((text or "").lower()))


class RAGEngine:
    def __init__(self, app_root: Path | None = None, model_path: str | None = None):
        self.app_root = Path(app_root or Path(__file__).resolve().parents[1])
        self.model_path = Path(model_path) if model_path else self._default_model_path()
        self._llm = None
        self._rag_chunks = self._load_rag_chunks()

    def _default_model_path(self) -> Path:
        return self.app_root / "assets" / "models" / "tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf"

    def _ensure_llm(self) -> Any:
        if self._llm is not None:
            return self._llm
        if Llama is None:
            return None
        if not self.model_path.exists():
            return None
        self._llm = Llama(
            model_path=str(self.model_path),
            n_ctx=2048,
            n_threads=4,
            n_gpu_layers=0,
            verbose=False,
        )
        return self._llm

    def _load_rag_chunks(self) -> list[dict[str, str]]:
        index_path = self.app_root / "assets" / "models" / "rag_chunks.jsonl"
        if not index_path.exists():
            return list(DEFAULT_RAG_CHUNKS)

        chunks: list[dict[str, str]] = []
        try:
            with index_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    text = str(obj.get("text", "") or "").strip()
                    if not text:
                        continue
                    chunks.append(
                        {
                            "source": str(obj.get("source", "local") or "local"),
                            "text": text,
                        }
                    )
        except Exception:
            return list(DEFAULT_RAG_CHUNKS)

        return chunks if chunks else list(DEFAULT_RAG_CHUNKS)

    def parse_components(self, source_text: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[tuple[str, float, str]] = set()

        for match in DOSE_PATTERN.finditer(source_text or ""):
            name = normalize_key(match.group("name"))
            if not name:
                continue
            try:
                dose_value = float(match.group("dose").replace(",", "."))
            except Exception:
                continue
            unit = normalize_key(match.group("unit"))
            if unit in {"ug", "µg", "μg"}:
                unit = "mcg"
            item_key = (name, dose_value, unit)
            if item_key in seen:
                continue
            seen.add(item_key)
            rows.append({"component": name, "dose_value": dose_value, "dose_unit": unit})

        return rows

    def map_component_foods(self, component: str) -> list[str]:
        key = normalize_key(component)
        if key in FOOD_MAP:
            return FOOD_MAP[key]

        # Fuzzy fallback using shared tokens.
        comp_tokens = tokenize(key)
        best_key = ""
        best_overlap = 0
        for candidate in FOOD_MAP.keys():
            overlap = len(comp_tokens.intersection(tokenize(candidate)))
            if overlap > best_overlap:
                best_overlap = overlap
                best_key = candidate
        if best_key and best_overlap > 0:
            return FOOD_MAP[best_key]
        return ["No reliable whole-food mapping found"]

    def build_label_analysis_report(self, source_text: str) -> str:
        rows = self.parse_components(source_text)
        if not rows:
            return "No nutrient dose rows found. Add clearer label text (example: Vitamin C 500 mg)."

        lines = ["Supplement-to-food offline mapping:"]
        for item in rows:
            component = str(item.get("component", "") or "")
            dose_value = item.get("dose_value")
            dose_unit = str(item.get("dose_unit", "") or "")
            foods = self.map_component_foods(component)
            dose_label = f"{dose_value:g} {dose_unit}" if dose_value is not None else "n/a"
            lines.append(f"- {component.title()} ({dose_label}) -> {', '.join(foods[:4])}")

        return "\n".join(lines)

    def _retrieve(self, question: str, top_k: int = 3) -> list[dict[str, str]]:
        q_tokens = tokenize(question)
        scored: list[tuple[int, dict[str, str]]] = []
        for chunk in self._rag_chunks:
            c_tokens = tokenize(chunk.get("text", ""))
            score = len(q_tokens.intersection(c_tokens))
            if score > 0:
                scored.append((score, chunk))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [s[1] for s in scored[:top_k]]

    def _llm_answer(self, question: str, contexts: list[dict[str, str]]) -> str:
        llm = self._ensure_llm()
        if llm is None:
            return ""

        context_text = "\n\n".join([f"SOURCE: {c['source']}\n{c['text']}" for c in contexts])
        prompt = (
            "You are an offline nutrition assistant. Use only the provided context. "
            "If uncertain, say what is missing.\n\n"
            f"Question: {question}\n\n"
            f"Context:\n{context_text}\n\n"
            "Answer:"
        )

        try:
            resp = llm(prompt=prompt, max_tokens=240, temperature=0.2)
            choices = resp.get("choices", []) if isinstance(resp, dict) else []
            if choices:
                return str(choices[0].get("text", "") or "").strip()
        except Exception:
            return ""
        return ""

    def _deterministic_answer(self, question: str, contexts: list[dict[str, str]]) -> str:
        if not contexts:
            return "No local RAG context matched your question."
        joined = "\n\n".join([f"- {c['text']}" for c in contexts])
        return f"Offline evidence snippets for: {question}\n\n{joined}"

    def answer_question(self, question: str) -> str:
        contexts = self._retrieve(question)
        llm_response = self._llm_answer(question, contexts)
        if llm_response:
            sources = ", ".join(sorted({c.get("source", "local") for c in contexts}))
            return f"{llm_response}\n\nSources: {sources}"
        return self._deterministic_answer(question, contexts)
