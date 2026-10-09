"""AppTest helpers for the swipe card's tools: Swap food / Ask AI / More.

The card is a custom component in an iframe; its tools are buttons INSIDE it that report {"kind": "swap" | "ask" | "report", id, card,
index} as the component's value. AppTest has no iframe, so a test plays the component: it writes that value under the component's key
and reruns, exactly what the browser does through Streamlit's setComponentValue. Not a test module."""
from __future__ import annotations

import itertools
import json
from typing import Any

from streamlit.testing.v1 import AppTest

_serial = itertools.count(1)


def component_key(at: AppTest) -> str:
    return "tinder_" + str(at.session_state["swipe_reset_nonce"])


def card_args(at: AppTest) -> dict[str, Any]:
    """The props the swipe card was drawn with (what the iframe shows); {} when no card is on screen."""
    for element in at.get("component_instance"):
        args = json.loads(element.proto.json_args or "{}")
        if "cardId" in args:
            return args
    return {}


def send(at: AppTest, value: dict[str, Any]) -> AppTest:
    at.session_state[component_key(at)] = value
    at.run()
    return at


def tool_value(at: AppTest, kind: str, **override: Any) -> dict[str, Any]:
    """What the component sends for a tap on the tool `kind` of the card on screen (a fresh id each time)."""
    card = at.session_state["swipe_cards"][int(at.session_state["swipe_index"])]
    value = {"kind": kind, "id": f"{kind}-{next(_serial)}", "card": card["component_key"], "index": int(at.session_state["swipe_index"])}
    value.update(override)
    return value


def tap_tool(at: AppTest, kind: str, **override: Any) -> AppTest:
    """A tap on the card's Swap food / Ask AI / More button; the sheet of that name opens."""
    return send(at, tool_value(at, kind, **override))


def sheet_titles(at: AppTest) -> list[str]:
    return [d.proto.dialog.title for d in at.get("dialog")]


def dismiss_sheet(at: AppTest) -> AppTest:
    """X / Esc / a tap outside: the sheet's on_dismiss clears the request and the app reruns."""
    at.session_state["swipe_sheet"] = None
    at.run()
    return at


def swap_options(at: AppTest) -> list[str]:
    """The food list the Swap food sheet offers for the card on screen (opens the sheet, reads it, closes it again)."""
    tap_tool(at, "swap")
    options = list(at.selectbox[0].options)
    dismiss_sheet(at)
    return options


def swipe(at: AppTest, direction: str, swipe_id: str) -> AppTest:
    card = at.session_state["swipe_cards"][int(at.session_state["swipe_index"])]
    return send(at, {"dir": direction, "id": swipe_id, "card": card["component_key"], "index": int(at.session_state["swipe_index"])})
