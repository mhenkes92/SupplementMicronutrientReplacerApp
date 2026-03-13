from __future__ import annotations

import argparse
import csv
import io
import re
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

APP_DIR = Path(__file__).resolve().parent
OUT_PATH = APP_DIR / "data" / "official_nutrient_sources.csv"
HTTP_TIMEOUT = 35

NUTRIENT_ALIASES: dict[str, str] = {
    "vitamin c": "Vitamin C",
    "ascorbic acid": "Vitamin C",
    "vitamin d": "Vitamin D",
    "vitamin d3": "Vitamin D",
    "vitamin a": "Vitamin A",
    "vitamin e": "Vitamin E",
    "vitamin k": "Vitamin K",
    "folate": "Folate",
    "folic acid": "Folate",
    "vitamin b6": "Vitamin B6",
    "niacin": "Niacin",
    "thiamin": "Thiamin",
    "riboflavin": "Riboflavin",
    "vitamin b12": "Vitamin B12",
    "biotin": "Biotin",
    "pantothenic acid": "Pantothenic acid",
    "choline": "Choline",
    "calcium": "Calcium",
    "iron": "Iron",
    "magnesium": "Magnesium",
    "zinc": "Zinc",
    "selenium": "Selenium",
    "iodine": "Iodine",
    "copper": "Copper",
    "manganese": "Manganese",
    "molybdenum": "Molybdenum",
    "chromium": "Chromium",
    "potassium": "Potassium",
    "sodium": "Sodium",
}

TARGET_NUTRIENTS = {
    "Vitamin C",
    "Vitamin D",
    "Vitamin A",
    "Vitamin E",
    "Vitamin K",
    "Folate",
    "Vitamin B6",
    "Vitamin B12",
    "Niacin",
    "Thiamin",
    "Riboflavin",
    "Pantothenic acid",
    "Biotin",
    "Choline",
    "Calcium",
    "Iron",
    "Magnesium",
    "Zinc",
    "Selenium",
    "Iodine",
    "Copper",
    "Manganese",
    "Molybdenum",
    "Chromium",
    "Potassium",
    "Sodium",
}

DRI_NUTRIENT_UNIT: dict[str, str] = {
    "Vitamin A": "ug",
    "Vitamin D": "ug",
    "Vitamin E": "mg",
    "Vitamin K": "ug",
    "Vitamin C": "mg",
    "Thiamin": "mg",
    "Riboflavin": "mg",
    "Niacin": "mg",
    "Vitamin B6": "mg",
    "Folate": "ug",
    "Vitamin B12": "ug",
    "Biotin": "ug",
    "Pantothenic acid": "mg",
    "Choline": "mg",
    "Calcium": "mg",
    "Chromium": "ug",
    "Copper": "ug",
    "Iodine": "ug",
    "Iron": "mg",
    "Magnesium": "mg",
    "Manganese": "mg",
    "Molybdenum": "ug",
    "Selenium": "ug",
    "Zinc": "mg",
    "Sodium": "mg",
    "Potassium": "mg",
}

DGE_SOURCES: list[tuple[str, str, str]] = [
    ("Vitamin C", "mg", "https://www.dge.de/wissenschaft/referenzwerte/vitamin-c/"),
    ("Vitamin D", "ug", "https://www.dge.de/wissenschaft/referenzwerte/vitamin-d/"),
    ("Vitamin A", "ug", "https://www.dge.de/wissenschaft/referenzwerte/vitamin-a/"),
    ("Vitamin E", "mg", "https://www.dge.de/wissenschaft/referenzwerte/vitamin-e/"),
    ("Vitamin K", "ug", "https://www.dge.de/wissenschaft/referenzwerte/vitamin-k/"),
    ("Folate", "ug", "https://www.dge.de/wissenschaft/referenzwerte/folat/"),
    ("Vitamin B6", "mg", "https://www.dge.de/wissenschaft/referenzwerte/vitamin-b6/"),
    ("Vitamin B12", "ug", "https://www.dge.de/wissenschaft/referenzwerte/vitamin-b12/"),
    ("Niacin", "mg", "https://www.dge.de/wissenschaft/referenzwerte/niacin/"),
    ("Thiamin", "mg", "https://www.dge.de/wissenschaft/referenzwerte/thiamin/"),
    ("Riboflavin", "mg", "https://www.dge.de/wissenschaft/referenzwerte/riboflavin/"),
    ("Pantothenic acid", "mg", "https://www.dge.de/wissenschaft/referenzwerte/pantothensaeure/"),
    ("Biotin", "ug", "https://www.dge.de/wissenschaft/referenzwerte/biotin/"),
    ("Calcium", "mg", "https://www.dge.de/wissenschaft/referenzwerte/calcium/"),
    ("Iron", "mg", "https://www.dge.de/wissenschaft/referenzwerte/eisen/"),
    ("Magnesium", "mg", "https://www.dge.de/wissenschaft/referenzwerte/magnesium/"),
    ("Zinc", "mg", "https://www.dge.de/wissenschaft/referenzwerte/zink/"),
    ("Selenium", "ug", "https://www.dge.de/wissenschaft/referenzwerte/selen/"),
    ("Iodine", "ug", "https://www.dge.de/wissenschaft/referenzwerte/jod/"),
    ("Copper", "mg", "https://www.dge.de/wissenschaft/referenzwerte/kupfer-mangan-chrom-molybdaen/"),
    ("Manganese", "mg", "https://www.dge.de/wissenschaft/referenzwerte/kupfer-mangan-chrom-molybdaen/"),
    ("Molybdenum", "ug", "https://www.dge.de/wissenschaft/referenzwerte/kupfer-mangan-chrom-molybdaen/"),
    ("Potassium", "mg", "https://www.dge.de/wissenschaft/referenzwerte/kalium/"),
    ("Sodium", "mg", "https://www.dge.de/wissenschaft/referenzwerte/natrium/"),
]

FAO_CHAPTERS: list[tuple[str, str, str]] = [
    ("Vitamin C", "mg", "https://www.fao.org/4/y2809e/y2809e0c.htm"),
    ("Vitamin D", "ug", "https://www.fao.org/4/y2809e/y2809e0e.htm"),
    ("Vitamin A", "ug", "https://www.fao.org/4/y2809e/y2809e0d.htm"),
    ("Calcium", "mg", "https://www.fao.org/4/y2809e/y2809e0h.htm"),
    ("Iron", "mg", "https://www.fao.org/4/y2809e/y2809e0j.htm"),
    ("Magnesium", "mg", "https://www.fao.org/4/y2809e/y2809e0k.htm"),
    ("Zinc", "mg", "https://www.fao.org/4/y2809e/y2809e0m.htm"),
    ("Folate", "ug", "https://www.fao.org/4/y2809e/y2809e0a.htm"),
]


class ImportWarning(Exception):
    pass


def normalize_text(value: str) -> str:
    text = (value or "").strip().lower()
    text = text.replace("\u2013", "-")
    text = text.replace("\u2014", "-")
    text = text.replace("\u2212", "-")
    return re.sub(r"\s+", " ", text)


def parse_number(value: str) -> float | None:
    text = (value or "").strip()
    if not text:
        return None
    text = text.replace("*", "")
    text = text.replace(",", "")
    text = text.replace("−", "-")
    text = re.sub(r"[^0-9.\-]", "", text)
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        return None


def canonical_nutrient(label: str) -> str | None:
    key = normalize_text(label)
    for alias, canonical in NUTRIENT_ALIASES.items():
        if alias in key:
            return canonical
    return None


def fetch_text(url: str) -> str:
    response = requests.get(url, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    return response.text


def parse_html_table_rows(html: str) -> list[list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        return []

    rows: list[list[str]] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        row = [normalize_text(c.get_text(" ", strip=True)) for c in cells]
        row = [c for c in row if c]
        if row:
            rows.append(row)
    return rows


def find_header_row(rows: list[list[str]]) -> tuple[int, list[str]]:
    for i, row in enumerate(rows):
        joined = " ".join(row)
        if len(row) >= 6 and any(k in joined for k in ["vitamin", "folate", "thiamin", "calcium", "iron", "magnesium", "zinc", "selenium"]):
            return i, row
    raise ImportWarning("Could not locate a nutrient header row")


def parse_dri_table(url: str, source_agency: str, row_label: str = "19–30 y") -> list[dict[str, Any]]:
    html = fetch_text(url)
    rows = parse_html_table_rows(html)
    if not rows:
        raise ImportWarning(f"No table rows found at {url}")

    header_idx, header = find_header_row(rows)
    data_rows = rows[header_idx + 1 :]

    out: list[dict[str, Any]] = []
    section = ""
    for row in data_rows:
        first = row[0]
        if first in {"males", "females", "pregnancy", "lactation", "children", "infants"}:
            section = first
            continue

        if first != normalize_text(row_label):
            continue

        sex = "Male" if section == "males" else ("Female" if section == "females" else "All")

        values = row[1:]
        for idx, cell_value in enumerate(values):
            if idx >= len(header) - 1:
                break
            nutrient_label = header[idx + 1]
            nutrient = canonical_nutrient(nutrient_label)
            if nutrient is None or nutrient not in TARGET_NUTRIENTS:
                continue

            number = parse_number(cell_value)
            if number is None:
                continue

            unit = DRI_NUTRIENT_UNIT.get(nutrient, "mg")

            out.append(
                {
                    "nutrient": nutrient,
                    "unit": unit,
                    "life_stage": "Adults",
                    "sex": sex,
                    "source_agency": source_agency,
                    "recommended_value": number,
                    "upper_limit_value": None,
                    "source_url": url,
                    "notes": "Auto-imported from DRI table",
                }
            )
    if not out:
        raise ImportWarning(f"No adult rows parsed from {url}")
    return out


def parse_dri_ul_table(url: str, source_agency: str, row_label: str = "19–30 y") -> list[dict[str, Any]]:
    rows = parse_dri_table(url, source_agency, row_label=row_label)
    for row in rows:
        row["upper_limit_value"] = row["recommended_value"]
        row["recommended_value"] = None
        row["notes"] = "Auto-imported from DRI UL table"
    return rows


def parse_dge_pages() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for nutrient, unit, url in DGE_SOURCES:
        try:
            html = fetch_text(url)
        except Exception:
            continue

        soup = BeautifulSoup(html, "html.parser")
        rows: list[list[str]] = []
        for tr in soup.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            cells = [c for c in cells if c]
            if cells:
                rows.append(cells)

        male_val: float | None = None
        female_val: float | None = None

        for row in rows:
            key = normalize_text(row[0])
            if "19 bis unter 25" not in key:
                continue
            if len(row) >= 3:
                male_val = parse_number(row[1])
                female_val = parse_number(row[2])
            elif len(row) >= 2:
                male_val = parse_number(row[1])
                female_val = parse_number(row[1])
            break

        if male_val is not None:
            out.append(
                {
                    "nutrient": nutrient,
                    "unit": unit,
                    "life_stage": "Adults",
                    "sex": "Male",
                    "source_agency": "DGE",
                    "recommended_value": male_val,
                    "upper_limit_value": None,
                    "source_url": url,
                    "notes": "Auto-imported from DGE nutrient page",
                }
            )
        if female_val is not None:
            out.append(
                {
                    "nutrient": nutrient,
                    "unit": unit,
                    "life_stage": "Adults",
                    "sex": "Female",
                    "source_agency": "DGE",
                    "recommended_value": female_val,
                    "upper_limit_value": None,
                    "source_url": url,
                    "notes": "Auto-imported from DGE nutrient page",
                }
            )
    return out


def pdf_to_text(url: str) -> str:
    response = requests.get(url, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    reader = PdfReader(io.BytesIO(response.content))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def parse_fda_dv_pdf() -> list[dict[str, Any]]:
    # FDA official DV table (vitamins/minerals)
    url = "https://www.fda.gov/media/99069/download"
    try:
        text = pdf_to_text(url)
    except Exception:
        return []

    patterns = {
        "Vitamin C": (r"Vitamin\s*C\s*(\d+(?:\.\d+)?)\s*(mg|mcg|µg|ug|g)", "mg"),
        "Vitamin D": (r"Vitamin\s*D\s*(\d+(?:\.\d+)?)\s*(mg|mcg|µg|ug|g)", "ug"),
        "Calcium": (r"Calcium\s*(\d+(?:\.\d+)?)\s*(mg|mcg|µg|ug|g)", "mg"),
        "Iron": (r"Iron\s*(\d+(?:\.\d+)?)\s*(mg|mcg|µg|ug|g)", "mg"),
        "Magnesium": (r"Magnesium\s*(\d+(?:\.\d+)?)\s*(mg|mcg|µg|ug|g)", "mg"),
        "Zinc": (r"Zinc\s*(\d+(?:\.\d+)?)\s*(mg|mcg|µg|ug|g)", "mg"),
        "Folate": (r"Folate\s*(\d+(?:\.\d+)?)\s*(mg|mcg|µg|ug|g)", "ug"),
    }

    out: list[dict[str, Any]] = []
    for nutrient, (pattern, default_unit) in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        value = parse_number(match.group(1))
        unit_raw = normalize_text(match.group(2))
        unit = "ug" if unit_raw in {"mcg", "µg", "ug"} else ("g" if unit_raw == "g" else "mg")
        if value is None:
            continue
        out.append(
            {
                "nutrient": nutrient,
                "unit": unit or default_unit,
                "life_stage": "Adults",
                "sex": "All",
                "source_agency": "FDA",
                "recommended_value": value,
                "upper_limit_value": None,
                "source_url": url,
                "notes": "Auto-imported from FDA Daily Value table (DV, not RDA)",
            }
        )
    return out


def parse_efsa_ul_summary() -> list[dict[str, Any]]:
    url = "https://www.efsa.europa.eu/sites/default/files/2024-05/ul-summary-report.pdf"
    try:
        text = pdf_to_text(url)
    except Exception:
        return []

    patterns = {
        "Vitamin D": r"Vitamin\s*D[^\n]{0,120}?adult[^\n]{0,120}?(\d+(?:\.\d+)?)\s*(mg|µg|ug|mcg)",
        "Vitamin E": r"Vitamin\s*E[^\n]{0,120}?adult[^\n]{0,120}?(\d+(?:\.\d+)?)\s*(mg|µg|ug|mcg)",
        "Iron": r"Iron[^\n]{0,120}?adult[^\n]{0,120}?(\d+(?:\.\d+)?)\s*(mg|µg|ug|mcg)",
        "Folate": r"Folate[^\n]{0,120}?adult[^\n]{0,120}?(\d+(?:\.\d+)?)\s*(mg|µg|ug|mcg)",
        "Vitamin B6": r"Vitamin\s*B6[^\n]{0,120}?adult[^\n]{0,120}?(\d+(?:\.\d+)?)\s*(mg|µg|ug|mcg)",
        "Selenium": r"Selenium[^\n]{0,120}?adult[^\n]{0,120}?(\d+(?:\.\d+)?)\s*(mg|µg|ug|mcg)",
    }

    out: list[dict[str, Any]] = []
    for nutrient, pattern in patterns.items():
        m = re.search(pattern, text, re.IGNORECASE)
        if not m:
            continue
        number = parse_number(m.group(1))
        unit_raw = normalize_text(m.group(2))
        if number is None:
            continue
        unit = "ug" if unit_raw in {"µg", "ug", "mcg"} else "mg"
        out.append(
            {
                "nutrient": nutrient,
                "unit": unit,
                "life_stage": "Adults",
                "sex": "All",
                "source_agency": "EFSA",
                "recommended_value": None,
                "upper_limit_value": number,
                "source_url": url,
                "notes": "Auto-imported from EFSA UL summary (UL only)",
            }
        )
    return out


def parse_fao_chapters() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for nutrient, unit, url in FAO_CHAPTERS:
        try:
            html = fetch_text(url)
        except Exception:
            continue

        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        # Heuristic: look for an adult intake expression nearest the nutrient chapter text.
        match = re.search(r"adult[s]?[^.]{0,200}?(\d+(?:\.\d+)?)\s*(mg|µg|ug|mcg|g)\s*(?:/|per)?\s*day", text, re.IGNORECASE)
        if not match:
            continue

        value = parse_number(match.group(1))
        unit_raw = normalize_text(match.group(2))
        parsed_unit = "ug" if unit_raw in {"µg", "ug", "mcg"} else ("g" if unit_raw == "g" else "mg")
        if value is None:
            continue

        out.append(
            {
                "nutrient": nutrient,
                "unit": parsed_unit or unit,
                "life_stage": "Adults",
                "sex": "All",
                "source_agency": "FAO",
                "recommended_value": value,
                "upper_limit_value": None,
                "source_url": url,
                "notes": "Heuristic parse from FAO chapter text; verify before publication",
            }
        )
    return out


def load_existing_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    return rows


def as_row_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        normalize_text(str(row.get("nutrient", ""))),
        normalize_text(str(row.get("life_stage", ""))),
        normalize_text(str(row.get("sex", ""))),
        normalize_text(str(row.get("source_agency", ""))),
    )


def merge_rows(existing: list[dict[str, Any]], fresh: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in existing:
        by_key[as_row_key(row)] = row
    for row in fresh:
        key = as_row_key(row)
        if key not in by_key:
            by_key[key] = row
            continue

        # Preserve both recommended and UL values when separate passes populate one side each.
        prev = by_key[key]
        merged = dict(prev)
        for field in [
            "unit",
            "life_stage",
            "sex",
            "source_agency",
            "source_url",
            "notes",
        ]:
            if str(row.get(field, "") or "").strip():
                merged[field] = row.get(field)

        new_rec = row.get("recommended_value")
        new_ul = row.get("upper_limit_value")
        old_rec = prev.get("recommended_value")
        old_ul = prev.get("upper_limit_value")
        merged["recommended_value"] = new_rec if new_rec not in {None, ""} else old_rec
        merged["upper_limit_value"] = new_ul if new_ul not in {None, ""} else old_ul
        by_key[key] = merged
    merged = list(by_key.values())
    merged.sort(key=lambda r: (str(r.get("nutrient", "")), str(r.get("source_agency", "")), str(r.get("sex", ""))))
    return merged


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "nutrient",
        "unit",
        "life_stage",
        "sex",
        "source_agency",
        "recommended_value",
        "upper_limit_value",
        "source_url",
        "notes",
    ]

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "nutrient": row.get("nutrient", ""),
                    "unit": row.get("unit", ""),
                    "life_stage": row.get("life_stage", "Adults"),
                    "sex": row.get("sex", "All"),
                    "source_agency": row.get("source_agency", ""),
                    "recommended_value": row.get("recommended_value", ""),
                    "upper_limit_value": row.get("upper_limit_value", ""),
                    "source_url": row.get("source_url", ""),
                    "notes": row.get("notes", ""),
                }
            )


def build_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    # DRI via NIH/NCBI object-only tables (most reliable machine-readable source in this pipeline).
    try:
        rows.extend(parse_dri_table("https://www.ncbi.nlm.nih.gov/books/NBK56068/table/summarytables.t2/?report=objectonly", "DRI"))
    except Exception:
        pass
    try:
        rows.extend(parse_dri_table("https://www.ncbi.nlm.nih.gov/books/NBK545442/table/appJ_tab3/?report=objectonly", "DRI"))
    except Exception:
        pass
    try:
        rows.extend(parse_dri_ul_table("https://www.ncbi.nlm.nih.gov/books/NBK56068/table/summarytables.t7/?report=objectonly", "DRI"))
    except Exception:
        pass
    try:
        rows.extend(parse_dri_ul_table("https://www.ncbi.nlm.nih.gov/books/NBK545442/table/appJ_tab9/?report=objectonly", "DRI"))
    except Exception:
        pass

    # DGE nutrient pages (adult male/female row extraction).
    rows.extend(parse_dge_pages())

    # FDA Daily Values PDF.
    rows.extend(parse_fda_dv_pdf())

    # EFSA UL summary PDF.
    rows.extend(parse_efsa_ul_summary())

    # FAO/WHO chapter pages (heuristic parse).
    rows.extend(parse_fao_chapters())

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Build/update official nutrient source table for SuppSwap")
    parser.add_argument("--output", default=str(OUT_PATH), help="Output CSV path")
    parser.add_argument("--replace", action="store_true", help="Replace output instead of merging with existing rows")
    args = parser.parse_args()

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fresh_rows = build_rows()
    if args.replace:
        rows = fresh_rows
    else:
        existing = load_existing_rows(output_path)
        rows = merge_rows(existing, fresh_rows)

    write_rows(output_path, rows)

    print(f"Wrote {len(rows)} rows to {output_path}")
    sources = sorted({str(r.get('source_agency', '') or '') for r in rows if str(r.get('source_agency', '') or '').strip()})
    print("Sources in output:", ", ".join(sources))


if __name__ == "__main__":
    main()
