"""Round-2 timing guard: pathological label text stays fast (no quadratic
re-scan of a long line in the label-line parser)."""
from __future__ import annotations

import time

import pytest

import blockbrain.app as bb


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(bb, "_ai_canonicalize_nutrient_name", lambda *_a, **_k: "")

    def _no_llm(*_a, **_k):
        raise AssertionError("nutrition code must not call the LLM")

    monkeypatch.setattr(bb, "call_blockbrain_text", _no_llm)


@pytest.mark.parametrize(
    "text",
    [
        " ".join(["Vitamin A", "Vitamin C", "Zink", "Selen", "Jod", "Eisen", "Magnesium", "Calcium"] * 300),
        "Vitamin C 80 mg 100%, " * 800,
        "Vitamin D3 + K2 + " * 600 + "1000 IE",
        "mit 500 µg Vitamin B12 " * 150,
    ],
    ids=["names-without-doses", "repeated-rows", "joined-titles", "doses-before-names"],
)
def test_long_single_line_labels_parse_quickly(text):
    bb.parse_components(text)  # warm caches
    start = time.perf_counter()
    bb.parse_components(text)
    assert time.perf_counter() - start < 1.5
