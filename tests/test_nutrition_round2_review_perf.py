"""Round-2 confirmation review, timing guard: a long '+' / '/'-joined title
group whose doses all sit at the end of the line parses in linear time (the
salt-word check reads only each member's own segment, the "je" check a short
window, and the %-column check is computed once per line)."""
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


def _best_time(text: str) -> float:
    bb.parse_components(text)  # warm caches
    best = float("inf")
    for _ in range(2):
        start = time.perf_counter()
        bb.parse_components(text)
        best = min(best, time.perf_counter() - start)
    return best


@pytest.mark.parametrize(
    "text",
    [
        " + ".join(["Zink", "Selen"] * 800) + " " + " + ".join(["10 mg", "55 µg"] * 800),
        " / ".join(["Vitamin D3", "K2", "Calcium", "Magnesium"] * 800) + " 1000 IE",
        "Vitamin D3 + K2 + " * 1600 + "1000 IE",
    ],
    ids=["1600-names-with-doses", "slash-joined", "plus-joined"],
)
def test_long_joined_title_groups_parse_quickly(text):
    # ~25-30 KB on one line: 1.6-2.9 s with the old per-member re-scans.
    assert len(text) > 24_000
    assert _best_time(text) < 1.0


def test_joined_group_with_doses_still_reads_each_dose():
    text = " + ".join(["Zink", "Selen"] * 400) + " " + " + ".join(["10 mg", "55 µg"] * 400)
    rows = [(r["component"], r["dose_value"], r["dose_unit"]) for r in bb.parse_components(text)]
    assert rows == [("zinc", 10.0, "mg"), ("selenium", 55.0, "mcg")]
