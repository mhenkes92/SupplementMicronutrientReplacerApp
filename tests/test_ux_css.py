"""Header CSS: no dead selectors (verified in the browser on every screen) and
no non-working "page lock" rules."""
from __future__ import annotations

import re
from pathlib import Path

APP_SRC = (Path(__file__).resolve().parent.parent / "swipe_mobile_app" / "app.py").read_text(encoding="utf-8")


def _header_css(sw, monkeypatch) -> str:
    seen: list[str] = []
    monkeypatch.setattr(sw.st, "markdown", lambda body, **_kw: seen.append(str(body)))
    sw._render_header()
    assert seen
    return seen[0]


def test_removed_selectors_stay_gone(sw, monkeypatch):
    css = _header_css(sw, monkeypatch)
    for dead in (
        ".swipe-progress", ".swipe-dot", ".swipe-title", ".filter-shell", ".filter-chip", ".tinder-stage",
        ".stack-under", ".decision-badge", ".micro-name", ".portion-hint", ".card-hero", ".swipe-final-card",
        ".analyze-loading-spinner", "section.main", "stVerticalBlockBorderWrapper", ".plan-list", ".plan-row + .plan-row",
    ):
        assert dead not in css, dead


def test_no_page_lock_rules(sw, monkeypatch):
    css = _header_css(sw, monkeypatch)
    assert "overflow: hidden !important" not in css
    assert "overscroll-behavior" not in css
    assert not re.search(r"(^|\n)\s*html\s*,\s*body\s*\{", css)


def test_every_custom_class_in_the_css_is_rendered_somewhere(sw, monkeypatch):
    css = re.sub(r"url\([^)]*\)", "url()", _header_css(sw, monkeypatch))  # the icons are data URIs: their text is no selector
    classes = set(re.findall(r"\.([A-Za-z][A-Za-z0-9_-]*)", css)) - {"block-container", "stButton", "stDownloadButton", "stFormSubmitButton"}
    for cls in sorted(classes):
        assert re.search(rf"class=['\"]{re.escape(cls)}['\"]", APP_SRC), f".{cls} styles nothing"
