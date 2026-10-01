""""🚩 Report a problem with this card": one structured warning line via the
blockbrain logger with nutrient, dose, label line, chosen food and diet — no
free text, nothing personal."""
from __future__ import annotations

import json
import logging

import blockbrain.app as bb

LABEL = "Folsäure 200 µg 100%\nZink 10 mg 100%"


def _cards(sw):
    components = sw._filter_to_micronutrients(bb.parse_components(LABEL))
    return components, sw._build_swipe_cards(components, [])


def test_report_logs_one_structured_line(sw, monkeypatch, caplog):
    components, cards = _cards(sw)
    state = {"swipe_components": components, "swipe_pregnant": True}
    monkeypatch.setattr(sw.st, "session_state", state)
    folate = cards[0]
    food = folate["foods"][0]
    _ids, profiles = sw._dietary_profile_lookup()
    with caplog.at_level(logging.WARNING, logger=bb.logger.name):
        payload = sw._report_card_problem(folate, food, profiles["vegan"])
    records = [r for r in caplog.records if r.name == bb.logger.name and "card report" in r.getMessage()]
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    message = records[0].getMessage()
    assert "\n" not in message
    logged = json.loads(message.split(": ", 1)[1])
    assert logged == payload
    assert logged == {
        "nutrient": "Folic acid",
        "nutrient_key": "folate",
        "dose": "200 mcg",
        "label_line": "Folsäure 200 µg 100%",
        "food": food["food_description"],
        "diet": "Vegan",
    }
    # Nothing personal: the pregnancy toggle is never part of the report.
    assert "pregnan" not in message.lower()


def test_report_without_label_line_or_food(sw, monkeypatch, caplog):
    _components, cards = _cards(sw)
    monkeypatch.setattr(sw.st, "session_state", {})
    with caplog.at_level(logging.WARNING, logger=bb.logger.name):
        payload = sw._report_card_problem(cards[1], None, None)
    assert payload["nutrient"] == "Zinc" and payload["dose"] == "10 mg"
    assert payload["label_line"] == "" and payload["food"] == "" and payload["diet"] == "No restriction"
    assert sorted(payload) == ["diet", "dose", "food", "label_line", "nutrient", "nutrient_key"]
