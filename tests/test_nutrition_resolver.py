"""Nutrient name -> USDA nutrient resolution and whole-food lists."""
from __future__ import annotations

import sqlite3

import pytest

import blockbrain.app as bb


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(bb, "_ai_canonicalize_nutrient_name", lambda *_a, **_k: "")

    def _no_llm(*_a, **_k):
        raise AssertionError("nutrition code must not call the LLM")

    monkeypatch.setattr(bb, "call_blockbrain_text", _no_llm)


def _ids(name: str) -> list[int]:
    return [n["id"] for n in bb._resolve_local_nutrient_candidates(name, max_ids=3)]


def _foods(name: str, limit: int = 250) -> list[dict]:
    return bb._build_local_food_rows_for_component(name, limit=limit)


@pytest.mark.parametrize(
    "name, nutrient_id",
    [
        ("vitamin b1", 1165), ("thiamine mononitrate", 1165), ("vitamin b2", 1166), ("vitamin b3", 1167),
        ("niacinamide", 1167), ("vitamin b5", 1170), ("pantothenic acid", 1170), ("Pantothensäure", 1170),
        ("calcium pantothenate", 1170), ("vitamin b6", 1175), ("pyridoxine hcl", 1175), ("vitamin b7", 1176),
        ("biotin", 1176), ("vitamin b9", 1190), ("folic acid", 1190), ("Folsäure", 1190), ("vitamin b12", 1178),
        ("Vitamin B-12", 1178), ("B12", 1178), ("Vit. B12", 1178), ("methylcobalamin", 1178),
        ("vitamin d3", 1114), ("cholecalciferol", 1114), ("vitamin d", 1114), ("vitamin a", 1106),
        ("retinyl palmitate", 1106), ("vitamin e", 1109), ("d-alpha tocopheryl acetate", 1109),
        ("vitamin k", 1185), ("vitamin c", 1162), ("Jod", 1100), ("Iodine (as potassium iodide)", 1100),
        ("Eisen", 1089), ("Zink", 1095), ("Selen", 1103), ("Selenium (as sodium selenite)", 1103),
        ("Kupfer", 1098), ("Mangan", 1101), ("Molybdän", 1102), ("Kalium", 1092), ("Kalzium", 1087),
        ("Calcium", 1087), ("Natrium", 1093), ("Fluorid", 1099), ("Cholin", 1180), ("EPA 180 mg", 1278),
        ("dha", 1272), ("alpha-linolenic acid", 1404),
    ],
)
def test_names_resolve_to_one_pinned_usda_nutrient(name, nutrient_id):
    assert _ids(name) == [nutrient_id]


def test_omega3_resolves_to_epa_plus_dha_only():
    assert _ids("omega-3") == [1278, 1272]
    assert _ids("fish oil") == [1278, 1272]


@pytest.mark.parametrize("name", ["vitamin b", "vitamin", "acid", "ashwagandha root extract", "Omega-6", "GLA", "environmental"])
def test_generic_or_unknown_names_do_not_match_a_random_nutrient(name):
    assert bb.canonical_nutrient_key(name) == ""
    assert _ids(name) == []


def test_unknown_names_need_a_relevant_token_match():
    assert _ids("lutein") == [1121]  # exact name beats "Lutein + zeaxanthin"
    assert _ids("malic") == [1039]
    assert _ids("pantothenic acid") != [1039]  # the old resolver matched "acid" -> malic acid


def test_every_pinned_nutrient_has_ranking_rows_in_the_db():
    conn = sqlite3.connect(f"file:{bb.USDA_RANK_DB_PATH}?mode=ro", uri=True)
    try:
        for key, spec in bb._NUTRIENT_LEXICON.items():
            for nutrient_id, _factor in spec["usda"]:
                count = conn.execute(
                    "SELECT COUNT(*) FROM nutrient_rankings WHERE nutrient_id = ? AND amount_per_100g > 0", (nutrient_id,)
                ).fetchone()[0]
                assert count > 0, (key, nutrient_id)
    finally:
        conn.close()


def test_lexicon_card_names_round_trip_to_their_nutrient():
    for key, spec in bb._NUTRIENT_LEXICON.items():
        names = [spec["display"]] + [alias[1] for alias in spec["aliases"] if isinstance(alias, tuple)]
        for name in names:
            assert bb.canonical_nutrient_key(name) == key, (key, name)


@pytest.mark.parametrize("key", [k for k, spec in bb._NUTRIENT_LEXICON.items() if spec["usda"]])
def test_food_lists_never_mix_units(key):
    foods = _foods(key)
    assert foods, key
    assert {f["unit"] for f in foods} == {bb._normalize_component_unit_token(bb._NUTRIENT_LEXICON[key]["unit"])}
    amounts = [f["amount_per_100g"] for f in foods]
    assert amounts == sorted(amounts, reverse=True)


def test_vitamin_a_foods_are_rae_not_iu():
    carrots = next(f for f in _foods("vitamin a") if f["food_description"] == "Carrots, raw")
    assert (carrots["amount_per_100g"], carrots["unit"]) == (835.0, "mcg")  # RAE, not 16706 IU


def test_vitamin_d_uses_microgram_rows_and_converts_iu_only_rows():
    foods = {f["food_description"]: f for f in _foods("vitamin d3", limit=400)}
    assert foods["Mushrooms, brown, italian, or crimini, exposed to ultraviolet light, raw"]["amount_per_100g"] == 31.9
    assert {f["unit"] for f in foods.values()} == {"mcg"}


def test_folate_list_is_dfe():
    duck = next(f for f in _foods("folate") if f["food_description"] == "Duck, domesticated, liver, raw")
    assert (duck["amount_per_100g"], duck["unit"]) == (738.0, "mcg")


def test_pantothenic_acid_is_not_malic_acid():
    names = [f["food_description"] for f in _foods("pantothenic acid")[:5]]
    assert "Cherries, sweet, dark red, raw" not in names  # 762 mg malic acid in the old list


def test_epa_dha_lists_exclude_ala_only_foods_but_ala_has_them():
    omega = {f["food_description"] for f in _foods("omega-3")}
    assert "Seeds, hemp seed, hulled" not in omega
    assert not any(name.startswith("Seeds, flaxseed") for name in omega)
    ala = {f["food_description"] for f in _foods("alpha-linolenic acid")}
    assert any(name.startswith("Seeds, flaxseed") for name in ala)


def test_vitamin_k2_uses_curated_literature_list_not_k1_greens():
    foods = _foods("Vitamin K2 (as menaquinone-7)")
    names = [f["food_description"] for f in foods]
    assert names[0].startswith("Natto")
    assert not any(n.startswith(("Parsley", "Amaranth", "Kale", "Spinach")) for n in names)
    assert all("Schurgers" in f["source_db"] for f in foods)
    assert {f["unit"] for f in foods} == {"mcg"}


def test_brazil_nut_selenium_uses_reference_value():
    brazil = next(f for f in _foods("selenium") if f["food_description"] == "Nuts, brazilnuts, raw")
    assert brazil["amount_per_100g"] == 1917.0


def test_food_rows_are_cached_after_first_lookup(monkeypatch):
    first = _foods("riboflavin", limit=123)

    def _no_db():
        raise AssertionError("cached lookup must not reopen SQLite")

    monkeypatch.setattr(bb, "try_open_usda_db", _no_db)
    again = _foods("vitamin b2", limit=123)
    assert again == first
    again[0]["amount_per_100g"] = -1  # callers get copies, the cache stays intact
    assert _foods("riboflavin", limit=123)[0]["amount_per_100g"] == first[0]["amount_per_100g"]


def test_unknown_name_tries_ai_fallback_once_then_resolves(monkeypatch):
    calls = []

    def _fake_ai(name):
        calls.append(name)
        return "Vitamin B6"

    monkeypatch.setattr(bb, "_ai_canonicalize_nutrient_name", _fake_ai)
    foods = _foods("pyridoxolum hydrochloricum")
    assert calls == ["pyridoxolum hydrochloricum"]
    assert foods and {f["unit"] for f in foods} == {"mg"}
