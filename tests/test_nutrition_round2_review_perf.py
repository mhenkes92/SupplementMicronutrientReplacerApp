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
    for _ in range(3):
        start = time.perf_counter()
        bb.parse_components(text)
        best = min(best, time.perf_counter() - start)
    return best


_BUILDERS = {
    "1600-names-with-doses": lambda n: " + ".join(["Zink", "Selen"] * n) + " " + " + ".join(["10 mg", "55 µg"] * n),
    "slash-joined": lambda n: " / ".join(["Vitamin D3", "K2", "Calcium", "Magnesium"] * n) + " 1000 IE",
    "plus-joined": lambda n: "Vitamin D3 + K2 + " * (2 * n) + "1000 IE",
}


@pytest.mark.parametrize("kind", sorted(_BUILDERS))
def test_long_joined_title_groups_parse_quickly(kind):
    # ~25-30 KB on one line took 1.6-2.9 s with the old per-member re-scans (quadratic). The check compares the run with
    # itself (double the input must cost about double the time, quadratic would cost four times) instead of a wall-clock
    # limit, so a loaded machine cannot fail it.
    small, big = _BUILDERS[kind](800), _BUILDERS[kind](1600)
    assert len(small) > 24_000
    t_small, t_big = _best_time(small), _best_time(big)
    assert t_big < 3.2 * t_small + 0.05, f"not linear: {t_small:.3f}s for {len(small)} chars, {t_big:.3f}s for {len(big)} chars"
    assert t_big < 20.0  # a sanity ceiling, far above any healthy machine


def test_joined_group_with_doses_still_reads_each_dose():
    text = " + ".join(["Zink", "Selen"] * 400) + " " + " + ".join(["10 mg", "55 µg"] * 400)
    rows = [(r["component"], r["dose_value"], r["dose_unit"]) for r in bb.parse_components(text)]
    assert rows == [("zinc", 10.0, "mg"), ("selenium", 55.0, "mcg")]
