"""The curated combination table (swipe_mobile_app/combine.py): every rule is sourced, short, general and safe, and every
machine-readable part of it is one the engine understands. The table is health guidance: a rule that cannot be sourced is dropped, not
softened, so these tests are strict on purpose (a new rule that fails one of them is not ready)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import combine

ROOT = Path(__file__).resolve().parent.parent

KINDS = {"with_fat", "boost", "avoid_with", "separate", "split_dose", "with_meal", "note"}
RELS = {"with", "apart", "note"}
APPLIES = {"pill", "food", "both"}
SURFACES = {"card", "day", "dialog", "table"}
KIND_REL = {"with_fat": "with", "boost": "with", "with_meal": "with", "avoid_with": "apart", "separate": "apart", "split_dose": "apart", "note": "note"}
# Words the visitor must never read (they sound like a diagnosis, a cure, a prescription or a promise), and any number of hours or minutes.
FORBIDDEN = re.compile(
    r"\b(cure[sd]?|treat(?:s|ed|ment)?|prevent(?:s|ed|ion)?|diagnos\w*|must|never take|always|dangerous|deficien\w*|guarantee\w*|ideal|best way)\b",
    re.I,
)
HOURS = re.compile(r"\b\d+\s*(?:h|hr|hrs|hours?|minutes?|min)\b|\b(?:an|one|two|three)\s+hours?\b", re.I)
# The only numbers a visitor may read in a rule: the two sourced ones.
SOURCED_NUMBERS = re.compile(r"\b(?:500|25) mg\b")

# The table as the build spec fixes it (id, takeaway verbatim, rel, rank, surfaces). A change here is a decision, not an accident.
PINNED = [
    ("iron-vitc", "Take iron with a vitamin C food or drink", "with", 3, ["day", "dialog", "table"]),
    ("iron-tea-coffee", "Have tea and coffee between meals, not with your iron", "apart", 6, ["day", "dialog", "table"]),
    ("iron-calcium-pill", "Take iron and calcium pills at different meals", "apart", 1, ["day", "dialog", "table"]),
    ("zinc-iron-pills", "Take zinc and high-dose iron pills at different meals", "apart", 1, ["day", "dialog", "table"]),
    ("calcium-split-500", "Split more than 500 mg of calcium over two meals", "apart", 2, ["day", "dialog", "table"]),
    ("calcium-carbonate-meal", "Take calcium carbonate with a meal", "with", 5, ["card", "day", "dialog", "table"]),
    ("fat-vitamins-de", "Take vitamins D and E with a meal that has some fat", "with", 4, ["card", "day", "dialog", "table"]),
    ("fat-omega3", "Take fish-oil capsules with a meal that has some fat", "with", 4, ["card", "day", "dialog", "table"]),
    ("fat-carotenoids", "Add a little oil to carrots, greens and tomatoes", "with", 4, ["day", "dialog", "table"]),
    ("calcium-vitamin-d", "Vitamin D helps your body absorb calcium", "with", 7, ["dialog", "table"]),
    ("medicine-spacing", "On regular medicine? Ask your pharmacist about timing", "note", 9, ["day", "dialog", "table"]),
]


def _visitor_texts(r):
    for field in ("title", "takeaway", "why", "caveats", "short"):
        if r[field]:
            yield field, r[field]


def test_the_table_is_the_eleven_rules_of_the_spec():
    assert [(r["id"], r["takeaway"], r["rel"], r["rank"], r["surfaces"]) for r in combine.RULES] == PINNED
    ids = [r["id"] for r in combine.RULES]
    assert len(ids) == len(set(ids)) == 11 and all(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", i) for i in ids)
    assert sum(r["confidence"] == "high" for r in combine.RULES) == 8


def test_every_rule_is_sourced_by_a_named_https_page():
    for r in combine.RULES:
        for src in [r["source"], *r["also_sources"]]:
            assert src["name"].strip() and src["url"].startswith("https://"), r["id"]
            assert "visitor" not in src["name"], (r["id"], "an editorial note in a source name would be shown to the visitor")
        assert r["confidence"] in ("high", "medium"), r["id"]  # a rule below "medium" is dropped, not softened
        assert r["verified"].strip(), r["id"]


def test_the_fields_are_the_ones_the_engine_reads():
    for r in combine.RULES:
        assert r["kind"] in KINDS and r["rel"] in RELS and r["applies_to"] in APPLIES, r["id"]
        assert KIND_REL[r["kind"]] == r["rel"], r["id"]
        assert set(r["surfaces"]) <= SURFACES and {"dialog", "table"} <= set(r["surfaces"]), r["id"]
        assert r["subjects"] and all(isinstance(s, str) and s for s in r["subjects"]), r["id"]
        assert not r["icon"].isascii(), (r["id"], "the icon is an emoji, not a word")
        assert r["condition_key"] is None or r["condition_key"] in combine._COND_TOKENS, (r["id"], r["condition_key"])
        assert (r["condition_key"] is None) == (r["condition"] is None), r["id"]
        assert isinstance(r["rank"], int) and 1 <= r["rank"] <= 9 and isinstance(r["needs_partner"], bool)
        for p in r["partners"]:
            assert p.startswith(("food:", "drug:")) or p in combine._KNOWN_KEYS, (r["id"], p)
    assert {r["condition_key"] for r in combine.RULES if r["condition_key"]} == combine._COND_TOKENS  # no token the table does not use


def test_a_pair_rule_names_its_partner_and_the_card_rules_have_a_short_word():
    for r in combine.RULES:
        if r["needs_partner"]:
            assert [p for p in r["partners"] if not p.startswith(("food:", "drug:"))], r["id"]
        if r["kind"] == "separate":
            assert r["needs_partner"] and r["applies_to"] == "pill", r["id"]
    cards = [r for r in combine.RULES if "card" in r["surfaces"]]
    assert [r["id"] for r in cards] == ["calcium-carbonate-meal", "fat-vitamins-de", "fat-omega3"]
    assert all(r["rel"] == "with" and r["short"] and len(r["short"]) <= 11 for r in cards)  # one line on a 320 px card
    assert all(r["short"] is None for r in combine.RULES if "card" not in r["surfaces"])


def test_subjects_are_real_nutrient_keys():
    import blockbrain.app as bb

    for r in combine.RULES:
        for s in r["subjects"]:
            assert bb.canonical_nutrient_key(s) == s, (r["id"], s)
        for p in r["partners"]:
            if not p.startswith(("food:", "drug:")):
                assert bb.canonical_nutrient_key(p) == p, (r["id"], p)


def test_texts_are_short_general_and_free_of_unsourced_numbers():
    for r in combine.RULES:
        assert len(r["takeaway"]) <= 60 and len(r["why"]) <= 160 and len(r["caveats"] or "") <= 170, r["id"]
        assert len(r["title"]) <= 32 and len(r["short"] or "") <= 18, r["id"]
        for field, text in _visitor_texts(r):
            assert not FORBIDDEN.search(text), (r["id"], field, FORBIDDEN.search(text).group(0))
            assert not HOURS.search(text), (r["id"], field, "no hours or minutes in user-facing text")
            assert not re.search(r"\d", SOURCED_NUMBERS.sub("", text)), (r["id"], field, text)
        assert r["takeaway"][0].isupper() and not r["takeaway"].endswith("."), r["id"]
    numbers = {m for r in combine.RULES for _, t in _visitor_texts(r) for m in re.findall(r"\d+", t)}
    assert numbers == {"500", "25"}  # the two sourced numbers (NIH ODS), nothing else
    assert combine.CALCIUM_PORTION_MG == 500 and combine.IRON_PILL_WITH_ZINC_MG == 25


def test_a_rule_that_keeps_pills_apart_says_what_to_do_when_the_nutrients_are_in_one_tablet():
    """The engine cannot tell two pills from the nutrients of ONE tablet (a multivitamin), so a schedule that separates them must not be
    the last word: the three rules carry the note and the day repeats it once."""
    apart = {r["id"] for r in combine.RULES if r["kind"] in ("separate", "split_dose")}
    assert apart == {"iron-calcium-pill", "zinc-iron-pills", "calcium-split-500"} == set(combine._APART_PILL_RULES)
    for rid in apart:
        assert combine._ONE_TABLET in combine.rule(rid)["caveats"], rid
    assert not [r["id"] for r in combine.RULES if r["id"] not in apart and combine._ONE_TABLET in (r["caveats"] or "")]
    for text in (combine._ONE_TABLET, combine.ONE_TABLET_NOTE):
        assert "pack" in text and not re.search(r"\d", text) and not FORBIDDEN.search(text) and not HOURS.search(text)


def test_the_pharmacist_line_names_no_dose_time_or_drug_brand_and_defers_to_the_pharmacist():
    r = combine.rule("medicine-spacing")
    assert "pharmacist" in r["takeaway"].lower() and "Never skip" in r["caveats"]
    assert not re.search(r"\d", r["takeaway"] + r["why"] + r["caveats"])
    assert set(r["subjects"]) == {"calcium", "iron", "magnesium", "zinc"} == set(combine._MINERAL_KEYS)


def test_sensitive_rules_carry_their_caveat_and_defer_to_a_doctor():
    for rid in ("iron-vitc", "iron-tea-coffee", "iron-calcium-pill", "fat-vitamins-de", "calcium-split-500", "medicine-spacing"):
        assert re.search(r"ask your (?:doctor|pharmacist)|ask before|pharmacist or doctor", combine.rule(rid)["caveats"], re.I), rid
    assert "blood thinners" in combine.rule("fat-vitamins-de")["caveats"]  # vitamin E and anticoagulants
    assert "hemochromatosis" in combine.rule("iron-vitc")["caveats"]  # iron overload


def test_no_rule_touches_what_the_existing_notes_handle_with_care(sw):
    """The card's medication notes (vitamin K on warfarin, potassium, iodine) and the pregnancy caution on vitamin A pills stay the
    only word on those: no rule nudges anyone to absorb more of them."""
    careful = {sw.bb.canonical_nutrient_key(k) for k in sw._MEDICATION_NOTES}
    assert {"vitamin k", "vitamin k2", "potassium", "iodine"} <= careful
    for r in combine.RULES:
        assert not careful & set(r["subjects"]), r["id"]
        assert not careful & {p for p in r["partners"]}, r["id"]
        if "vitamin a" in r["subjects"]:
            assert r["applies_to"] == "food", r["id"]  # a vitamin A PILL has an upper level and a pregnancy caution: no tip
    assert combine.item_tips("vitamin k", dose_value=90, dose_unit="mcg") == [] and combine.card_tags("vitamin k2") == []
    assert combine.item_tips("vitamin a", dose_value=800, dose_unit="mcg") == [] and combine.card_tags("vitamin a") == []


def test_rule_returns_a_copy_and_none_for_an_unknown_id():
    copy = combine.rule("iron-vitc")
    copy["takeaway"] = "changed"
    copy["also_sources"].clear()
    copy["source"]["name"] = "changed"
    fresh = combine.rule("iron-vitc")
    assert fresh["takeaway"] == "Take iron with a vitamin C food or drink" and fresh["also_sources"] and fresh["source"]["name"] != "changed"
    assert combine.rule("nope") is None and combine.rule(None) is None and combine.rule(["x"]) is None


def test_the_fixed_texts_have_no_digits_and_nothing_a_dietary_profile_excludes():
    profiles = json.loads((ROOT / "blockbrain" / "data" / "dietary_profiles.json").read_text(encoding="utf-8"))
    assert profiles
    texts = [*combine._HINTS.values(), *(t.format(foods="") for t in combine._DIRECTIVE_TEXT.values())]
    assert set(combine._HINTS) == {"add_fat"} and set(combine._DIRECTIVE_TEXT) == {"add_vitc", "add_fat"}
    for text in texts:
        assert not re.search(r"\d", text) and not FORBIDDEN.search(text) and not HOURS.search(text), text
        for profile in profiles:
            for word in profile["avoid_keywords"]:
                assert not re.search(rf"\b{re.escape(word)}\b", text, re.I), (profile["id"], word, text)
    assert "nuts" not in " ".join(texts).lower()  # the visitor may filter nuts out


def test_the_table_view_lists_every_rule_and_hides_nothing():
    rows = combine.table_rows()
    # No plan: nothing is "in your plan", so the order is with < apart < note, then rank, then the table's own order.
    assert [r["id"] for r in rows] == [
        "iron-vitc", "fat-vitamins-de", "fat-omega3", "fat-carotenoids", "calcium-carbonate-meal", "calcium-vitamin-d",
        "iron-calcium-pill", "zinc-iron-pills", "calcium-split-500", "iron-tea-coffee", "medicine-spacing",
    ]
    assert len(rows) == 11 and not any(r["in_plan"] for r in rows)
    for r in rows:
        assert set(r) == {"id", "rel", "icon", "title", "text", "why", "caveats", "sources", "in_plan", "confidence"}
        assert isinstance(r["caveats"], list) and r["sources"] and all(s["name"] and s["url"].startswith("https://") for s in r["sources"])
        rule = combine.rule(r["id"])
        assert r["text"] == rule["takeaway"] and r["title"] == rule["title"] and r["sources"][0] == rule["source"]
        assert [s["url"] for s in r["sources"]] == [rule["source"]["url"], *(s["url"] for s in rule["also_sources"])]
