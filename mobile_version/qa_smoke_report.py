from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import app


@dataclass
class QAResult:
    group: str
    name: str
    status: str
    detail: str


def _ok(group: str, name: str, detail: str) -> QAResult:
    return QAResult(group=group, name=name, status="PASS", detail=detail)


def _fail(group: str, name: str, detail: str) -> QAResult:
    return QAResult(group=group, name=name, status="FAIL", detail=detail)


def _run_test(group: str, name: str, fn: Callable[[], str]) -> QAResult:
    try:
        detail = fn()
        return _ok(group, name, detail)
    except AssertionError as exc:
        return _fail(group, name, f"assertion failed: {exc}")
    except Exception as exc:
        return _fail(group, name, f"exception: {type(exc).__name__}: {exc}")


def _restore(module: Any, key: str, original: Any) -> None:
    setattr(module, key, original)


def test_local_path_selection() -> str:
    est = app.get_food_price_estimate(
        food_name="kale",
        country="Germany",
        currency="EUR",
        market="Auto",
        enable_live=False,
        grams_needed=180.0,
        ean_hint="",
        use_serpapi=False,
        use_dataforseo=False,
    )
    assert est is not None, "expected local estimate"
    assert est.get("price_per_kg") is not None, "missing price_per_kg"
    assert est.get("audit_top_candidates") is not None, "missing audit trail"
    return f"source={est.get('source_type')} price_per_kg={est.get('price_per_kg')} confidence={est.get('confidence')}"


def test_short_circuit_live_calls() -> str:
    original_lookup = app.lookup_local_price_offers
    original_market = app.fetch_market_price_offers
    original_serp = app.fetch_serpapi_shopping_offers
    original_dfs = app.fetch_dataforseo_shopping_offers
    original_llm = app.estimate_price_with_llm

    counters = {"market": 0, "serp": 0, "dfs": 0, "llm": 0}

    def fake_lookup(*_: Any, **__: Any) -> list[dict[str, Any]]:
        return [
            {
                "canonical_food": "kale",
                "title": "kale local",
                "ean": "",
                "pack_kg": 1.0,
                "price_per_kg": 4.0,
                "currency": "EUR",
                "country": "Germany",
                "source": "local",
                "source_type": "official_stat_mapped",
                "source_url": "",
                "last_updated": "2026-02-20",
                "note": "",
            }
        ]

    def fake_market(*_: Any, **__: Any) -> list[dict[str, Any]]:
        counters["market"] += 1
        return []

    def fake_serp(*_: Any, **__: Any) -> list[dict[str, Any]]:
        counters["serp"] += 1
        return []

    def fake_dfs(*_: Any, **__: Any) -> list[dict[str, Any]]:
        counters["dfs"] += 1
        return []

    def fake_llm(*_: Any, **__: Any) -> dict[str, Any] | None:
        counters["llm"] += 1
        return None

    try:
        app.lookup_local_price_offers = fake_lookup
        app.fetch_market_price_offers = fake_market
        app.fetch_serpapi_shopping_offers = fake_serp
        app.fetch_dataforseo_shopping_offers = fake_dfs
        app.estimate_price_with_llm = fake_llm

        est = app.get_food_price_estimate(
            food_name="kale",
            country="Germany",
            currency="EUR",
            market="Auto",
            enable_live=True,
            grams_needed=100.0,
            ean_hint="",
            use_serpapi=True,
            use_dataforseo=True,
        )
        assert est is not None, "expected estimate"
        assert counters["market"] == 0 and counters["serp"] == 0 and counters["dfs"] == 0 and counters["llm"] == 0, "expected live sources to be skipped"
        return f"selected={est.get('source_type')} live_calls={counters}"
    finally:
        _restore(app, "lookup_local_price_offers", original_lookup)
        _restore(app, "fetch_market_price_offers", original_market)
        _restore(app, "fetch_serpapi_shopping_offers", original_serp)
        _restore(app, "fetch_dataforseo_shopping_offers", original_dfs)
        _restore(app, "estimate_price_with_llm", original_llm)


def test_ean_exact_priority() -> str:
    offers = [
        {
            "canonical_food": "kale",
            "title": "kale generic",
            "ean": "",
            "pack_kg": 1.0,
            "price_per_kg": 3.5,
            "currency": "EUR",
            "country": "Germany",
            "source": "serp",
            "source_type": "serpapi_google_shopping",
            "source_url": "",
            "last_updated": "2026-02-21",
            "note": "",
        },
        {
            "canonical_food": "kale",
            "title": "kale ean",
            "ean": "4001234567890",
            "pack_kg": 1.0,
            "price_per_kg": 4.1,
            "currency": "EUR",
            "country": "Germany",
            "source": "retailer",
            "source_type": "retailer_api",
            "source_url": "",
            "last_updated": "2026-02-21",
            "note": "",
        },
    ]
    ranked = app._rank_price_offers(
        offers,
        food_name="kale",
        country="Germany",
        currency="EUR",
        grams_needed=120.0,
        ean_hint="4001234567890",
    )
    assert ranked, "expected ranked offers"
    assert ranked[0].get("ean") == "4001234567890", "expected EAN exact match to rank first"
    return f"top_source={ranked[0].get('source_type')} top_score={ranked[0].get('final_score')}"


def test_parse_and_map_sample_1() -> str:
    text = "Vitamin C 100 mg\nMagnesium 200 mg\nZinc 15 mg"
    components = app.parse_components(text)
    assert components, "parse_components returned empty"
    _, details, status = app.build_usda_matches(components)
    assert status == "ok", f"unexpected USDA status: {status}"
    assert any(d.get("foods") for d in details), "no mapped foods"
    return f"components={len(components)} mapped={sum(1 for d in details if d.get('foods'))}"


def test_parse_and_map_sample_2() -> str:
    text = "Vitamin D3 25 mcg\nIron 18 mg\nVitamin K2 120 mcg"
    components = app.parse_components(text)
    assert components, "parse_components returned empty"
    _, details, status = app.build_usda_matches(components)
    assert status == "ok", f"unexpected USDA status: {status}"
    assert len(details) >= 1, "expected mapped detail rows"
    return f"components={len(components)} detail_rows={len(details)}"


def test_price_estimate_known_foods() -> str:
    foods = ["kale", "spinach", "broccoli"]
    resolved = 0
    for food in foods:
        est = app.get_food_price_estimate(
            food_name=food,
            country="Germany",
            currency="EUR",
            market="Auto",
            enable_live=False,
            grams_needed=140.0,
            ean_hint="",
            use_serpapi=False,
            use_dataforseo=False,
        )
        if est and est.get("price_per_kg") is not None:
            resolved += 1
    assert resolved == len(foods), f"resolved={resolved}, expected={len(foods)}"
    return f"resolved={resolved}/{len(foods)} known-food local estimates"


def build_report(results: list[QAResult]) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# QA Smoke Report",
        "",
        f"Generated: {ts}",
        "",
        "| Group | Test | Status | Detail |",
        "|---|---|---|---|",
    ]
    for result in results:
        lines.append(
            f"| {result.group} | {result.name} | {result.status} | {result.detail.replace('|', '/')} |"
        )

    total = len(results)
    passed = sum(1 for r in results if r.status == "PASS")
    failed = total - passed
    lines.extend([
        "",
        f"Summary: {passed}/{total} passed, {failed} failed.",
    ])
    return "\n".join(lines)


def main() -> int:
    tests: list[tuple[str, str, Callable[[], str]]] = [
        ("Pricing", "Local path selection", test_local_path_selection),
        ("Pricing", "Live short-circuit optimization", test_short_circuit_live_calls),
        ("Pricing", "EAN exact priority", test_ean_exact_priority),
        ("Integration", "Parse+USDA mapping sample A", test_parse_and_map_sample_1),
        ("Integration", "Parse+USDA mapping sample B", test_parse_and_map_sample_2),
        ("Integration", "Known-food local pricing", test_price_estimate_known_foods),
    ]

    results = [_run_test(group, name, fn) for group, name, fn in tests]
    report = build_report(results)

    reports_dir = Path(__file__).resolve().parent / "qa_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_file = reports_dir / f"qa_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    out_file.write_text(report, encoding="utf-8")

    print(report)
    print(f"\nReport file: {out_file}")

    failed = sum(1 for r in results if r.status == "FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
