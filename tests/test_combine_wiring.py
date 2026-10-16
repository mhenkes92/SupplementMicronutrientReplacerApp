"""How the combine engine is wired into the app: loaded by the deploy guard, the app's hooks for it, the meal-plan prompt (unchanged unless
the swapped foods need a layout, never built from a kept pill), the share text and the kill switch. The engine itself is in
tests/test_combine_engine.py and its table in tests/test_combine_rules.py."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

import blockbrain.app as bb
import combine
import llm_cache

ROOT = Path(__file__).resolve().parent.parent
APP_SOURCE = (ROOT / "swipe_mobile_app" / "app.py").read_text(encoding="utf-8")

LENTILS = {"food_description": "Lentils, pink or red, raw", "food_category": "Legumes and Legume Products", "amount_per_100g": 7.39, "unit": "mg"}
GUAVA = {"food_description": "Guavas, common, raw", "food_category": "Fruits and Fruit Juices", "amount_per_100g": 228.3, "unit": "mg"}
CARROT = {"food_description": "Carrots, raw", "food_category": "Vegetables and Vegetable Products", "amount_per_100g": 8285.0, "unit": "mcg"}
LIVER = {"food_description": "Lamb, New Zealand, imported, liver, raw", "food_category": "Lamb, Veal, and Game Products", "amount_per_100g": 90.0, "unit": "mcg"}
KING_MACKEREL = {"food_description": "Fish, mackerel, king, raw", "food_category": "Finfish and Shellfish Products", "amount_per_100g": 4.0, "unit": "mcg"}
PUMPKIN = {"food_description": "Seeds, pumpkin seeds (pepitas), raw", "food_category": "Nut and Seed Products", "amount_per_100g": 8.4, "unit": "mg"}


def _swap(component, food, dose=14, unit="mg"):
    return {"decision": "replace", "component": component, "component_key": component.lower(), "dose_value": dose, "dose_unit": unit, "form": "",
            "dose_label": f"{dose} {unit}", "selected_food": food}


def _pill(component, dose=14, unit="mg", form=""):
    return {"decision": "keep", "component": component, "component_key": component.lower(), "dose_value": dose, "dose_unit": unit, "form": form,
            "dose_label": f"{dose} {unit}", "selected_food": None}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("SUPPSWIPE_COMBINE", raising=False)
    monkeypatch.setenv("BLOCKBRAIN_API_KEY", "k")
    llm_cache.clear()
    yield
    llm_cache.clear()


# ---------------------------------------------------------------------------------------------------------------------- the deploy guard
def test_the_deploy_guard_loads_the_engine_and_it_is_the_one_in_the_app_folder(sw):
    assert sw.combine is combine and Path(combine.__file__).resolve() == ROOT / "swipe_mobile_app" / "combine.py"
    used = set(re.findall(r"\bcombine\.([A-Za-z_]\w*)", APP_SOURCE))
    assert {"plan_day", "meal_layout"} <= used and not [a for a in used if not hasattr(combine, a)]
    assert not re.search(r"\bcombine\.py\b", APP_SOURCE), "the deploy test would read 'py' as an attribute of the module"
    registry = sys.modules["_suppswipe_imports"].__dict__["seen"]
    assert registry["combine"][0] is combine  # loaded through _load_current, like llm_cache: a redeploy reloads it


def test_no_other_module_called_combine_can_shadow_the_engine():
    import importlib.util

    spec = importlib.util.find_spec("combine")
    assert Path(spec.origin).resolve() == ROOT / "swipe_mobile_app" / "combine.py"


def test_the_app_starts_when_the_running_process_holds_the_old_engine():
    """A redeploy adds names to combine; a process that still holds the previous file must be brought up to date, not crash on open."""
    script = textwrap.dedent(
        """
        import os, sys, logging
        logging.disable(logging.CRITICAL)
        os.environ["BLOCKBRAIN_API_KEY"] = ""
        sys.path[:0] = [r"%s", r"%s"]
        import combine
        for name in ("meal_layout", "plan_day", "day_text"):
            delattr(combine, name)
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(r"%s", default_timeout=60)
        at.run()
        print("EXCEPTIONS", len(at.exception), "RELOADED", hasattr(sys.modules["combine"], "meal_layout"))
        """
    ) % (ROOT, ROOT / "swipe_mobile_app", ROOT / "swipe_mobile_app" / "app.py")
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=180)
    assert "EXCEPTIONS 0 RELOADED True" in result.stdout, result.stdout[-800:] + result.stderr[-800:]


# ---------------------------------------------------------------------------------------------------------------------- the app's hooks
def test_the_hooks_are_the_apps_own_checks(sw):
    hooks = sw._combine_hooks(False)
    over, fine = _pill("Iron", 60), _pill("Iron", 14)
    assert hooks["over_limit"](over) is True and hooks["over_limit"](fine) is False
    assert hooks["over_limit"](over) == bool(sw._final_upper_limit_warnings([over]))  # exactly what the Heads-up box shows
    assert hooks["dose_text"](_pill("Iron", 14)) == "14 mg" and hooks["dose_text"]({**fine, "dose_label": "Dose not found"}) == ""
    assert hooks["title"](_pill("Vitamin D3", 20, "mcg")) == "Vitamin D3" and hooks["canon"]("Vitamin D3") == "vitamin d"
    assert hooks["food_label"] is sw._food_name and hooks["grams"] is sw._swap_grams
    assert hooks["fact"]("Guavas, common, raw", "vitamin c") == pytest.approx(228.3) and hooks["fact"]("Guavas, common, raw", "fat") is None
    assert hooks["skip_food"](_swap("Vitamin B12", LIVER)) is False and sw._combine_hooks(True)["skip_food"](_swap("Vitamin B12", LIVER)) is True
    assert sw._combine_hooks(True)["skip_food"](_swap("Iron", LENTILS)) is False
    assert hooks["food_icon"](CARROT) == sw._whole_food_icon_from_food(CARROT, "")


def test_the_hooks_read_the_pregnancy_setting_unless_told(sw):
    state = sw.st.session_state
    before = state.get("swipe_pregnant")
    try:
        state["swipe_pregnant"] = True
        assert sw._combine_hooks()["skip_food"](_swap("Vitamin B12", LIVER)) is True
        assert sw._combine_hooks(False)["skip_food"](_swap("Vitamin B12", LIVER)) is False  # an explicit flag wins
        state["swipe_pregnant"] = False
        assert sw._combine_hooks()["skip_food"](_swap("Vitamin B12", LIVER)) is False
        assert sw._combine_hooks(True)["skip_food"](_swap("Vitamin B12", LIVER)) is True
    finally:
        if before is None:
            state.pop("swipe_pregnant", None)
        else:
            state["swipe_pregnant"] = before


def test_the_day_of_the_app_is_the_engines_day_with_the_apps_hooks(sw):
    kept, swapped = [_pill("Iron"), _pill("Calcium", 1000, "mg", "calcium carbonate"), _pill("Iron", 60)], [_swap("Vitamin C", GUAVA, 80)]
    assert sw._combine_day(kept, swapped, False) == combine.plan_day(kept, swapped, sw._combine_hooks(False))
    day = sw._combine_day(kept[:2], swapped, False)
    assert [i["text"] for i in day["slots"][1]["items"]] == ["Iron 14 mg"] and day["free_foods"][0]["label"] == "Guavas"


# ---------------------------------------------------------------------------------------------------------------------- the meal-plan prompt
def test_a_plan_whose_foods_need_no_layout_gets_the_prompt_it_always_got(sw):
    items = [_swap("Vitamin C", GUAVA, 80), _swap("Zinc", PUMPKIN, 10)]
    system, user, key = sw._meal_plan_prompts(items, "Halal", 2, pregnant=False)
    assert "Meal layout" not in user and user.endswith("\n\nWrite exactly 2 meals now.")
    assert user.count("\n\n") == 1  # one blank line: before the closing sentence, as it always was


def test_the_layout_goes_into_the_prompt_and_changes_the_cache_key(sw, monkeypatch):
    items = [_swap("Iron", LENTILS), _swap("Vitamin C", GUAVA, 80)]
    system, user, key = sw._meal_plan_prompts(items, "No restriction", 3, pregnant=False)
    assert user.endswith(
        "\n\nMeal layout (put each food named here in its meal; any other food can go in any meal; you may add small extras such as oil, lemon or herbs):\n"
        "Meal 2 (lunch): Red lentils (dry) + Guavas\n\nWrite exactly 3 meals now."
    )
    monkeypatch.setenv("SUPPSWIPE_COMBINE", "0")  # the kill switch: the old prompt, so the old cache entries
    system0, user0, key0 = sw._meal_plan_prompts(items, "No restriction", 3, pregnant=False)
    assert "Meal layout" not in user0 and key0 != key and system0 == system  # the system prompt is never touched
    assert user0 == user.split("\n\nMeal layout")[0] + "\n\nWrite exactly 3 meals now."
    monkeypatch.delenv("SUPPSWIPE_COMBINE")
    assert sw._meal_plan_prompts(items, "No restriction", 3, pregnant=False) == (system, user, key)  # the same plan is the same key again
    assert sw._meal_plan_prompts(items, "No restriction", 2, pregnant=False)[2] != key  # another number of meals is another plan
    assert "Meal 1 (lunch): Red lentils (dry) + Guavas" in sw._meal_plan_prompts(items, "No restriction", 2, pregnant=False)[1]


def test_a_food_without_a_partner_asks_for_one_in_the_prompt(sw):
    user = sw._meal_plan_prompts([_swap("Iron", LENTILS)], "No restriction", 3, pregnant=False)[1]
    assert "Meal 2 (lunch): Red lentils (dry)\n  - serve the Red lentils (dry) with a vitamin C food (for example peppers, citrus fruit, kiwi, berries or a squeeze of lemon)\n" in user
    carrot = sw._meal_plan_prompts([_swap("Beta carotene", CARROT, 6)], "Vegan", 1, pregnant=False)[1]
    assert "Meal 1: Carrots\n  - cook or dress the Carrots with a little oil (for example olive or rapeseed oil)\n" in carrot
    assert "Every meal must fit a Vegan diet." in carrot and carrot.index("Vegan diet") < carrot.index("Meal layout")


def _lines_of(day):
    """The prompt lines (3 meals) rebuilt from a day's own food placement and directives."""
    names = ["Meal 1 (breakfast)", "Meal 2 (lunch)", "Meal 3 (dinner)"]
    out = []
    for slot in day["slots"]:
        foods = [i["label"] for i in slot["items"] if i["kind"] == "food"]
        if foods:
            out.append(f"{names[slot['slot'] - 1]}: " + " + ".join(foods))
            out += ["  - " + combine._DIRECTIVE_TEXT[d["kind"]].format(foods=" and ".join(d["foods"])) for d in day["directives"] if d["slot"] == slot["slot"]]
    return out


def test_the_prompt_never_depends_on_a_kept_pill_and_never_names_one(sw):
    items = [_swap("Iron", LENTILS), _swap("Beta carotene", CARROT, 6)]
    kept = [_pill("Vitamin D3", 20, "mcg"), _pill("Calcium", 1000, "mg", "calcium carbonate"), _pill("Zinc", 15)]
    layout = combine.meal_layout(items, 3, sw._combine_hooks(False))
    assert layout and _lines_of(sw._combine_day(kept, items, False)) == layout == _lines_of(sw._combine_day([], items, False))
    user = sw._meal_plan_prompts(items, "No restriction", 3, pregnant=False)[1].lower()
    assert "calcium" not in user and "vitamin d3" not in user and "zinc" not in user and "supplement" not in user and "pill" not in user
    import inspect

    assert "keep_items" not in inspect.signature(sw._meal_plan_prompts).parameters and "keep_items" not in inspect.signature(sw._meal_layout_text).parameters


def test_the_pregnancy_prompt_leaves_an_unsafe_food_out_of_the_layout(sw):
    items = [_swap("Beta carotene", CARROT, 6), _swap("Vitamin D", KING_MACKEREL, 15, "mcg"), _swap("Vitamin B12", LIVER, 2.5, "mcg")]
    user = sw._meal_plan_prompts(items, "No restriction", 3, pregnant=True)[1]
    layout = user.split("Meal layout", 1)[1]
    assert "mackerel" not in layout.lower() and "liver" not in layout.lower() and "Carrots" in layout
    assert "cook or dress the Carrots with a little oil" in layout  # the unsafe fat partner is gone: the writer is asked for oil
    assert "a pregnancy-safe food rich in Vitamin B12 instead of Lamb liver (not advised in pregnancy)" in user  # the existing line is unchanged
    normal = sw._meal_plan_prompts(items, "No restriction", 3, pregnant=False)[1].split("Meal layout", 1)[1]
    assert "Carrots + Mackerel, king" in normal and "liver" not in normal.lower()  # without the flag the king mackerel is the carrots' fat


def test_the_prompt_layout_uses_the_pregnancy_setting_like_the_rest_of_the_prompt(sw):
    state = sw.st.session_state
    before = state.get("swipe_pregnant")
    items = [_swap("Beta carotene", CARROT, 6), _swap("Vitamin D", KING_MACKEREL, 15, "mcg")]
    try:
        state["swipe_pregnant"] = True
        assert "ackerel" not in sw._meal_plan_prompts(items, "No restriction", 3)[1].split("Meal layout", 1)[1]
        state["swipe_pregnant"] = False
        assert "ackerel" in sw._meal_plan_prompts(items, "No restriction", 3)[1].split("Meal layout", 1)[1]
    finally:
        if before is None:
            state.pop("swipe_pregnant", None)
        else:
            state["swipe_pregnant"] = before


def test_the_prefetch_and_the_meals_tab_use_the_same_plan_and_the_layout_reaches_the_model(sw, monkeypatch):
    seen = []

    def slow_text(system, user, model=None, on_text=None, history=None, budget_s=None, allow_tools=False):
        seen.append(user)
        time.sleep(0.2)
        return "Prefetched plan"

    monkeypatch.setattr(bb, "call_blockbrain_text", slow_text)
    sw.st.session_state.pop("_suppswipe_prefetched", None)
    items = [_swap("Iron", LENTILS, 13), _swap("Vitamin C", GUAVA, 81)]  # a plan no other test prefetched
    sw._prefetch_meal_plan(items, "", 3)
    key = sw._meal_plan_prompts(items, "", 3)[2]  # the key the Meals tab computes
    assert sw.llm_cache.inflight(key) is not None or sw.llm_cache.get(key) is not None
    assert sw._generate_meal_plan(items, "", 3) == "Prefetched plan"
    assert len(seen) == 1 and "Meal 2 (lunch): Red lentils (dry) + Guavas" in seen[0]  # the prefetch IS the plan the visitor gets


def test_the_golden_prompt_of_the_speed_round_is_untouched(sw):
    """tests/test_speed_round.py pins the exact meal-plan request; foods that need no partner must not change it."""
    items = [
        {"component": "Vitamin C", "component_key": "vitamin c", "dose_value": 90.0, "dose_unit": "mg", "decision": "replace",
         "selected_food": {"food_description": "Kiwi", "amount_per_100g": 90, "unit": "mg"}},
        {"component": "Zinc", "component_key": "zinc", "dose_value": 10.0, "dose_unit": "mg", "decision": "replace",
         "selected_food": {"food_description": "Pumpkin seeds", "amount_per_100g": 7.8, "unit": "mg"}},
    ]
    user = sw._meal_plan_prompts(items, "Halal", 2, pregnant=False)[1]
    assert user == (
        "Whole foods to include, with the daily amount to aim for:\n- Kiwi (eat ~100 g (~1 kiwi)) for Vitamin C\n"
        "- Pumpkin seeds (a normal portion (about 70 g); the full dose isn't practical from food) for Zinc Every meal must fit a Halal diet."
        "\n\nWrite exactly 2 meals now."
    )


# ---------------------------------------------------------------------------------------------------------------------- the share text
def test_the_share_text_carries_the_day_between_the_kept_pills_and_the_meal_plan(sw):
    kept, swapped = [_pill("Iron"), _pill("Calcium", 1000, "mg", "calcium carbonate")], [_swap("Vitamin C", GUAVA, 80)]
    lines = combine.day_text(sw._combine_day(kept, swapped, False))
    assert lines[0].startswith("Breakfast: Calcium, half of 1000 mg")
    text = sw._build_share_text(kept, swapped, "**Meal 1** soup", lines)
    out = text.splitlines()
    head = out.index("How to combine (general guidance, not medical advice):")
    assert out[head - 1] == "" and out[head + 1 : head + 1 + len(lines)] == lines
    assert out.index("  • Calcium 1000 mg") < head < out.index("🍽️ Meal plan:")
    assert "Take iron and calcium pills at different meals" not in text  # lines only, no tips and no sources
    plain = sw._build_share_text(kept, swapped, "**Meal 1** soup")
    assert plain == sw._build_share_text(kept, swapped, "**Meal 1** soup", None) == sw._build_share_text(kept, swapped, "**Meal 1** soup", [])
    assert "How to combine" not in plain


def test_the_share_tab_hands_the_lines_to_the_share_text(sw, monkeypatch):
    got = {}
    monkeypatch.setattr(sw, "_excluded_swaps_caption", lambda *a, **k: None)
    monkeypatch.setattr(sw.st, "caption", lambda *a, **k: None)
    monkeypatch.setattr(sw.st, "code", lambda *a, **k: None)
    monkeypatch.setattr(sw.st, "download_button", lambda *a, **k: None)
    monkeypatch.setattr(sw, "_build_share_text", lambda keep, repl, plan, lines=None: got.update(lines=lines) or "text")
    sw._render_share_tab([], [], "", "", [], ["Lunch: Iron 14 mg"])
    assert got["lines"] == ["Lunch: Iron 14 mg"]
    sw._render_share_tab([], [], "", "", [])
    assert got["lines"] is None


# ---------------------------------------------------------------------------------------------------------------------- the kill switch
@pytest.mark.parametrize("value", ["0", "false", "off", "no", " OFF ", "False", "NO"])
def test_the_kill_switch_removes_the_layout_and_the_share_lines(sw, monkeypatch, value):
    items = [_swap("Iron", LENTILS)]
    kept = [_pill("Iron")]
    lines = combine.day_text(sw._combine_day(kept, items, False))
    assert sw._combine_enabled() is True and "Meal layout" in sw._meal_plan_prompts(items, "", 3)[1]
    assert "How to combine" in sw._build_share_text(kept, items, "", lines)
    monkeypatch.setenv("SUPPSWIPE_COMBINE", value)
    assert sw._combine_enabled() is False
    assert "Meal layout" not in sw._meal_plan_prompts(items, "", 3)[1]
    assert "How to combine" not in sw._build_share_text(kept, items, "", lines)


@pytest.mark.parametrize("value", ["1", "true", "on", "yes", "", "anything"])
def test_everything_else_leaves_the_feature_on(sw, monkeypatch, value):
    monkeypatch.setenv("SUPPSWIPE_COMBINE", value)
    assert sw._combine_enabled() is True
