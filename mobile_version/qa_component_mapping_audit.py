from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app import build_usda_matches, normalize_lookup_key


TEST_FAMILIES: dict[str, list[str]] = {
    "Vitamin K forms": [
        "vitamin k",
        "vitamin k1",
        "vitamin k2",
        "vitamin k3",
        "phylloquinone",
        "menaquinone",
        "mk4",
        "mk7",
        "vitmain k",
    ],
    "Core vitamins": [
        "vitamin a",
        "retinol",
        "beta carotene",
        "vitamin c",
        "ascorbic acid",
        "vitamin d",
        "vitamin d3",
        "cholecalciferol",
        "ergocalciferol",
        "vitamin e",
        "alpha tocopherol",
    ],
    "B-complex": [
        "vitamin b1",
        "thiamine",
        "vitamin b2",
        "vitamin b3",
        "niacin",
        "vitamin b5",
        "pantothenic acid",
        "vitamin b6",
        "pyridoxine",
        "p5p",
        "vitamin b7",
        "biotin",
        "vitamin b9",
        "folate",
        "folic acid",
        "methylfolate",
        "5 mthf",
        "vitamin b12",
        "cobalamin",
        "methylcobalamin",
        "cyanocobalamin",
    ],
    "Minerals": [
        "calcium",
        "magnesium",
        "magnesium glycinate",
        "iron",
        "zinc",
        "selenium",
        "potassium",
        "sodium",
        "iodine",
        "chromium",
        "chromium picolinate",
        "molybdenum",
        "manganese",
        "copper",
    ],
    "Fatty acids": [
        "fish oil",
        "omega 3",
        "omega-3",
        "omega 6",
        "omega-6",
        "omega 9",
        "omega-9",
        "dha",
        "epa",
        "linoleic acid",
        "oleic acid",
    ],
    "Other supplements": [
        "choline",
        "alpha gpc",
        "citicoline",
        "coq10",
        "ubiquinone",
        "collagen",
        "probiotic",
        "curcumin",
        "turmeric",
        "ashwagandha",
        "lutein",
        "zeaxanthin",
    ],
}


def _build_components(terms: list[str]) -> list[dict[str, Any]]:
    return [{"component": t, "dose_value": 100.0, "dose_unit": "mg"} for t in terms]


def _run_audit() -> dict[str, Any]:
    all_rows: list[dict[str, Any]] = []
    family_summaries: list[dict[str, Any]] = []

    for family, terms in TEST_FAMILIES.items():
        components = _build_components(terms)
        _, details, status = build_usda_matches(components)
        by_component = {normalize_lookup_key(str(d.get("component", ""))): d for d in details}

        mapped = 0
        mapped_no_foods = 0
        unmapped = 0

        for term in terms:
            key = normalize_lookup_key(term)
            detail = by_component.get(key)
            if not detail:
                unmapped += 1
                all_rows.append(
                    {
                        "family": family,
                        "term": term,
                        "status": "UNMAPPED",
                        "resolved_nutrient": "",
                        "match_method": "",
                        "top_food": "",
                        "top_amount_per_100g": "",
                    }
                )
                continue

            foods = detail.get("foods", []) or []
            top = foods[0] if foods else {}
            top_amount = f"{top.get('amount_per_100g', '')} {top.get('unit', '')}".strip()

            if foods:
                mapped += 1
                status_label = "MAPPED"
            else:
                mapped_no_foods += 1
                status_label = "MAPPED_NO_FOODS"

            all_rows.append(
                {
                    "family": family,
                    "term": term,
                    "status": status_label,
                    "resolved_nutrient": str(detail.get("resolved_nutrient", "") or ""),
                    "match_method": str(detail.get("match_method", "") or ""),
                    "top_food": str(top.get("food_description", "") or ""),
                    "top_amount_per_100g": top_amount,
                }
            )

        family_summaries.append(
            {
                "family": family,
                "status": status,
                "total": len(terms),
                "mapped": mapped,
                "mapped_no_foods": mapped_no_foods,
                "unmapped": unmapped,
            }
        )

    overall = {
        "total": len(all_rows),
        "mapped": sum(1 for r in all_rows if r["status"] == "MAPPED"),
        "mapped_no_foods": sum(1 for r in all_rows if r["status"] == "MAPPED_NO_FOODS"),
        "unmapped": sum(1 for r in all_rows if r["status"] == "UNMAPPED"),
    }

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "families": family_summaries,
        "rows": all_rows,
        "overall": overall,
    }


def _write_report(report: dict[str, Any], out_path: Path) -> None:
    lines: list[str] = []
    lines.append("# QA Component Mapping Audit")
    lines.append("")
    lines.append(f"Generated: {report['generated_at']}")
    lines.append("")

    overall = report["overall"]
    lines.append(
        f"Overall: total={overall['total']} mapped={overall['mapped']} mapped_no_foods={overall['mapped_no_foods']} unmapped={overall['unmapped']}"
    )
    lines.append("")

    lines.append("## Family Summary")
    lines.append("")
    lines.append("| Family | Total | Mapped | Mapped no foods | Unmapped |")
    lines.append("|---|---:|---:|---:|---:|")
    for item in report["families"]:
        lines.append(
            f"| {item['family']} | {item['total']} | {item['mapped']} | {item['mapped_no_foods']} | {item['unmapped']} |"
        )
    lines.append("")

    lines.append("## Detailed Rows")
    lines.append("")
    lines.append("| Family | Term | Status | Resolved nutrient | Method | Top food | Top amount/100g |")
    lines.append("|---|---|---|---|---|---|---|")
    for row in report["rows"]:
        lines.append(
            "| {family} | {term} | {status} | {resolved_nutrient} | {match_method} | {top_food} | {top_amount_per_100g} |".format(
                **row
            )
        )

    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    report = _run_audit()
    out_dir = Path(__file__).resolve().parent / "qa_reports"
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = out_dir / f"qa_component_mapping_audit_{stamp}.md"
    json_path = out_dir / f"qa_component_mapping_audit_{stamp}.json"

    _write_report(report, md_path)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    overall = report["overall"]
    print(
        "QA Component Mapping Audit complete | "
        f"total={overall['total']} mapped={overall['mapped']} "
        f"mapped_no_foods={overall['mapped_no_foods']} unmapped={overall['unmapped']}"
    )
    print(f"Markdown report: {md_path}")
    print(f"JSON report: {json_path}")


if __name__ == "__main__":
    main()
