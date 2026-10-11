"""AppTest helpers for the swipe card's tools (Swap food, Ask AI) and for the More sheet (About & privacy, the report).

The card is a custom component in an iframe; its two tools are buttons INSIDE it that report {"kind": "swap" | "ask", id, card, index}
as the component's value (a card page loaded before the More tab existed may still send "report": the server accepts it). AppTest has
no iframe, so a test plays the component: it writes that value under the component's key and reruns, exactly what the browser does
through Streamlit's setComponentValue. The report and About live in the More sheet, behind the bar's right-most tab: `open_more` and
`tap_more_entry` play that path. Not a test module."""
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
    value = {"kind": kind, "id": f"{kind}-{next(_serial)}", "card": card["component_key"], "index": int(at.session_state["swipe_index"]),
             "scan": int(at.session_state.get("swipe_scan_serial", 0) or 0)}  # the scan the card was drawn with, echoed like the component does
    value.update(override)
    return value


def tap_tool(at: AppTest, kind: str, **override: Any) -> AppTest:
    """A tap on the card's Swap food / Ask AI button (or, from a stale page, on its old More button: kind "report"); the sheet of that
    name opens."""
    return send(at, tool_value(at, kind, **override))


def open_more(at: AppTest) -> AppTest:
    """A tap on the bar's More tab: the More sheet opens."""
    at.button(key="appbar_more").click().run()
    return at


def tap_more_entry(at: AppTest, entry: str) -> AppTest:
    """A tap on an entry of the open More sheet ("about" or "report"): it replaces the More sheet by that sheet."""
    at.button(key=f"more_{entry}").click().run()
    return at


def open_about(at: AppTest) -> AppTest:
    """The way to About & privacy: the More tab, then its first entry."""
    return tap_more_entry(open_more(at), "about")


def open_report(at: AppTest) -> AppTest:
    """The way to the report of the card on screen: the More tab, then its second entry."""
    return tap_more_entry(open_more(at), "report")


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
    return send(at, {"dir": direction, "id": swipe_id, "card": card["component_key"], "index": int(at.session_state["swipe_index"]),
                     "scan": int(at.session_state.get("swipe_scan_serial", 0) or 0)})
