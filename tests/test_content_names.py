"""Nutrient display names (UXL-23): cards, results, share text, history and
prompts show "Vitamin A" / "Folic acid" / "Vitamin D3" / "Iodine", never the
lowercase internal keys."""
from __future__ import annotations

import pytest

LIVER = {"food_description": "Lamb, New Zealand, imported, liver, raw", "amount_per_100g": 59.0, "unit": "UG"}


@pytest.mark.parametrize(
    "raw, title",
    [
        ("vitamin a", "Vitamin A"),
        ("folic acid", "Folic acid"),
        ("Folsäure", "Folic acid"),
        ("vitamin d3", "Vitamin D3"),
        ("Cholecalciferol", "Vitamin D3"),
        ("jod", "Iodine"),
        ("iodine", "Iodine"),
        ("vitamin b12", "Vitamin B12"),
        ("vitamin k2", "Vitamin K2"),
        ("Zinc (as zinc citrate)", "Zinc"),
        ("omega-3 (epa+dha)", "Omega-3 (EPA+DHA)"),
        ("epa", "EPA"),
        ("alpha-linolenic acid", "Alpha-linolenic acid"),
        ("pantothenic acid", "Pantothenic acid"),
        ("Coenzyme Q10", "Coenzyme Q10"),  # unknown: kept as written
        ("", ""),
    ],
)
def test_nutrient_title(sw, raw, title):
    assert sw._nutrient_title(raw) == title
    assert sw._nutrient_title(title) == title  # idempotent (history re-renders it)


def _decision(component, decision="replace", food=LIVER, dose=(2.4, "mcg")):
    return {
        "decision": decision,
        "component": component,
        "component_key": component,
        "dose_value": dose[0],
        "dose_unit": dose[1],
        "dose_label": f"{dose[0]} {dose[1]}",
        "form": "",
        "selected_food": food if decision == "replace" else None,
    }


def test_share_text_and_prompts_use_display_names(sw):
    replaced = [_decision("vitamin b12"), _decision("folic acid", dose=(200, "mcg"))]
    kept = [_decision("vitamin d3", decision="keep", dose=(20, "mcg")), _decision("jod", decision="keep", dose=(150, "mcg"))]
    share = sw._build_share_text(kept, replaced, "")
    assert "Vitamin B12: Lamb liver" in share and "Folic acid: Lamb liver" in share
    assert "• Vitamin D3 20 mcg" in share and "• Iodine 150 mcg" in share
    assert "vitamin b12" not in share and "folic acid:" not in share

    _system, user_prompt, _key = sw._meal_plan_prompts(replaced, "No restriction", 1)
    assert "for Vitamin B12" in user_prompt and "for Folic acid" in user_prompt
    _system, benefits_prompt, _key = sw._benefits_prompts(replaced)
    assert "Isolated pill nutrient: Vitamin B12" in benefits_prompt

    query, _links = sw._supplement_search_links(kept)
    assert query == "Vitamin D3 Iodine"


def test_scan_history_stores_display_names(sw, monkeypatch):
    state: dict = {}
    monkeypatch.setattr(sw.st, "session_state", state)
    decisions = {
        "vitamin b12": _decision("vitamin b12"),
        "vitamin d3": _decision("vitamin d3", decision="keep", dose=(20, "mcg")),
    }
    sw._record_scan_to_history(decisions, "Vegan")
    entry = state["suppswipe_scan_history"][-1]
    assert entry["replaced"][0]["component"] == "Vitamin B12"
    assert entry["kept"][0]["component"] == "Vitamin D3"


def test_results_warnings_use_display_names(sw):
    kept = [_decision("vitamin d3", decision="keep", dose=(250, "mcg"))]
    warnings = sw._final_upper_limit_warnings(kept)
    assert warnings and warnings[0].startswith("Vitamin D3: ")
