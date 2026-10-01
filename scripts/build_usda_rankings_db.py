"""Build blockbrain/data/usda_rankings.db from USDA FoodData Central CSV releases.

The live app ships one static SQLite snapshot built from TWO FoodData Central
datasets: SR Legacy (frozen 2018 release, ~7,800 foods — most of the vitamin,
folate and omega-3 rows) and Foundation Foods (updated a couple of times a
year). This script rebuilds that file with the same schema and content shape:

  foods                 every SR Legacy + Foundation food (data_type tells which),
                        with its category and an is_single_ingredient_like flag
  nutrients             the FDC nutrient dictionary (id, name, unit, nbr, rank)
  nutrient_rankings     per nutrient, every single-ingredient-like food ranked
                        by amount per 100 g (rank_desc 1 = richest source)
  food_dietary_flags    one row per distinct single-ingredient-like food
                        description: allowed_<profile> 0/1 per dietary profile
                        (blockbrain/dietary_food_classifier.py + the profile and
                        restriction-rule JSON files in blockbrain/data/)
  metadata              build provenance (dataset folders, counts)
  views                 foods_allowed_<profile> / nutrient_rankings_allowed_<profile>

Inputs (FoodData Central "CSV" downloads, unzipped):

    python scripts/build_usda_rankings_db.py \\
        --foundation-csv-dir path/to/FoodData_Central_foundation_food_csv_2025-12-18 \\
        --sr-legacy-csv-dir path/to/FoodData_Central_sr_legacy_food_csv_2018-04

or let the script fetch the pinned releases (what CI does):

    python scripts/build_usda_rankings_db.py --download-latest \\
        --output-db blockbrain/data/usda_rankings.db

To move to a newer USDA release, bump DEFAULT_FOUNDATION_CSV_URL (SR Legacy is
frozen) or pass --foundation-url / --sr-legacy-url. The output is written
atomically and is byte-for-byte reproducible for the same inputs, so the weekly
refresh workflow only opens a PR when the data really changed. Run
scripts/validate_usda_db.py on the result before shipping it.

Standard library only (csv + sqlite3), so CI needs no extra packages.
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Iterator, NamedTuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from blockbrain.dietary_food_classifier import (  # noqa: E402
    food_allowed_for_profile,
    load_dietary_profiles,
    load_dietary_restriction_rules,
    normalize_lookup_key,
    normalize_profile_column_id,
)

# Pinned FoodData Central releases. SR Legacy is final (USDA no longer updates
# it); bump the Foundation URL when USDA publishes a new Foundation release.
DEFAULT_FOUNDATION_CSV_URL = (
    "https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_foundation_food_csv_2025-12-18.zip"
)
DEFAULT_SR_LEGACY_CSV_URL = (
    "https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_sr_legacy_food_csv_2018-04.zip"
)

DEFAULT_OUTPUT_DB = ROOT / "blockbrain" / "data" / "usda_rankings.db"
DEFAULT_PROFILES_PATH = ROOT / "blockbrain" / "data" / "dietary_profiles.json"
DEFAULT_RULES_PATH = ROOT / "blockbrain" / "data" / "dietary_restriction_rules.json"

FOUNDATION_DATA_TYPE = "foundation_food"
SR_LEGACY_DATA_TYPE = "sr_legacy_food"

# Files every FoodData Central CSV release contains that the build reads.
REQUIRED_CSV_FILES = ("food.csv", "nutrient.csv", "food_nutrient.csv", "food_category.csv")

# ---------------------------------------------------------------------------
# "Single-ingredient-like" whole-food rule.
#
# Only foods passing this rule are ranked (and get dietary flags). The three
# lists below reproduce the is_single_ingredient_like column of the shipped DB
# exactly (8,229 of 8,229 foods); tests/test_usda_builder.py guards that.
# ---------------------------------------------------------------------------

# Processing words (whole-word match on the lower-cased description).
PROCESSED_KEYWORDS = [
    "prepared",
    "cooked",
    "canned",
    "frozen",
    "dried",
    "smoked",
    "pickled",
    "fermented",
    "roasted",
    "fried",
    "breaded",
    "powder",
    "extract",
    "juice",
    "sauce",
    "syrup",
    "flavor",
    "flavour",
    "fortified",
    "enriched",
    "blend",
    "mix",
    "recipe",
    "formula",
    "commercial",
]

# FDC food categories that hold composite / processed products, never whole foods.
NON_WHOLE_FOOD_CATEGORIES = {
    "restaurant foods",
    "fast foods",
    "sausages and luncheon meats",
    "baked products",
    "sweets",
    "beverages",
    "breakfast cereals",
    "snacks",
    "baby foods",
    "soups sauces and gravies",
    "meals entrees and side dishes",
    "spices and herbs",
    "fats and oils",
}

# Tokens (whole-word match on normalize_lookup_key(description)) that mark a
# product, a multi-ingredient food ("x and y", "with added ...") or a refined
# fraction rather than a single whole food.
NON_WHOLE_FOOD_TOKENS = {
    # composite / multi-ingredient
    "and",
    "with",
    "sandwich",
    "pizza",
    "burger",
    "burrito",
    "taco",
    "pupusas",
    "tamale",
    # restaurant / ready meals / supplements
    "restaurant",
    "restaruant",
    "fast food",
    "formulated bar",
    "protein bar",
    "granola bar",
    "cereal bar",
    "ready to eat",
    "ready-to-eat",
    "ready to drink",
    "energy drink",
    "nutritional shake",
    "snacks",
    "cereals ready to eat",
    "beverages",
    "spices",
    # processed / preserved
    "cured",
    "salted",
    "dehydrated",
    "kippered",
    "pasteurized",
    "processed",
    "product",
    "water added",
    "pickle",
    "pickles",
    "relish",
    "paste",
    "miso",
    "papad",
    "noodles",
    "dulce de leche",
    # meat products
    "bacon",
    "canadian bacon",
    "ham",
    "ground",
    "luncheon slices",
    "meat extender",
    "meatless",
    "substitute",
    "vegetarian fillets",
    # dairy products and fats
    "butter",
    "margarine",
    "spread",
    "creamer",
    "cream",
    "cream substitute",
    "cheese",
    "cheese food",
    "cheese spread",
    "yogurt",
    "nonfat",
    "low fat",
    "reduced fat",
    "milk dry",
    "oil",
    "fish oil",
    # refined fractions
    "flour",
    "bran",
    "defatted",
    "isolate",
    "concentrate",
    # condiments
    "ketchup",
    "mayonnaise",
    "dressing",
    "sauce",
}

_PROCESSED_PATTERNS = [re.compile(rf"\b{re.escape(k)}\b") for k in PROCESSED_KEYWORDS]
_NON_WHOLE_FOOD_PATTERNS = [
    re.compile(rf"(?<![a-z0-9]){re.escape(normalize_lookup_key(t))}(?![a-z0-9])")
    for t in sorted(NON_WHOLE_FOOD_TOKENS)
]


def is_single_ingredient_like(description: str, food_category: str = "") -> bool:
    """True when a USDA food looks like one whole, minimally processed ingredient."""
    d = (description or "").lower()
    if not d:
        return False
    if any(p.search(d) for p in _PROCESSED_PATTERNS):
        return False
    if normalize_lookup_key(food_category or "") in NON_WHOLE_FOOD_CATEGORIES:
        return False
    key = normalize_lookup_key(description)
    return not any(p.search(key) for p in _NON_WHOLE_FOOD_PATTERNS)


# ---------------------------------------------------------------------------
# CSV reading
# ---------------------------------------------------------------------------

class Dataset(NamedTuple):
    """One unzipped FoodData Central CSV release."""

    path: Path
    data_type: str  # foundation_food / sr_legacy_food

    @property
    def name(self) -> str:
        return self.path.name


def _read_csv(path: Path) -> Iterator[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        yield from csv.DictReader(fh)


def _to_int(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except (ValueError, OverflowError):
        return None


def _to_float(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        out = float(text)
    except ValueError:
        return None
    return out if math.isfinite(out) else None


def _check_dataset_dir(dataset: Dataset) -> None:
    if not dataset.path.is_dir():
        raise FileNotFoundError(f"{dataset.data_type} CSV folder not found: {dataset.path}")
    missing = [f for f in REQUIRED_CSV_FILES if not (dataset.path / f).is_file()]
    if missing:
        raise FileNotFoundError(f"{dataset.path} is missing {', '.join(missing)}")


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

# Schema of the shipped DB (column order and declared types matter: the app and
# older tooling read these tables by name and position).
_SCHEMA = {
    "foods": [
        ("fdc_id", "INTEGER"),
        ("data_type", "TEXT"),
        ("food_description", "TEXT"),
        ("food_category_id", "REAL"),
        ("publication_date", "TEXT"),
        ("id", "INTEGER"),
        ("food_category", "TEXT"),
        ("is_single_ingredient_like", "INTEGER"),
    ],
    "nutrients": [
        ("id", "INTEGER"),
        ("nutrient_name", "TEXT"),
        ("unit_name", "TEXT"),
        ("nutrient_nbr", "REAL"),
        ("nutrient_rank", "REAL"),
    ],
    "nutrient_rankings": [
        ("nutrient_id", "INTEGER"),
        ("nutrient_name", "TEXT"),
        ("unit_name", "TEXT"),
        ("fdc_id", "INTEGER"),
        ("food_description", "TEXT"),
        ("food_category", "TEXT"),
        ("amount_per_100g", "REAL"),
        ("rank_desc", "INTEGER"),
    ],
    "metadata": [("key", "TEXT"), ("value", "TEXT")],
}


def _create_table(conn: sqlite3.Connection, name: str, columns: list[tuple[str, str]]) -> None:
    cols = ",\n  ".join(f'"{col}" {typ}' for col, typ in columns)
    conn.execute(f'CREATE TABLE "{name}" (\n{cols}\n)')


def _insert(conn: sqlite3.Connection, name: str, columns: list[str], rows: list[tuple[Any, ...]]) -> None:
    placeholders = ", ".join("?" for _ in columns)
    col_sql = ", ".join(f'"{c}"' for c in columns)
    conn.executemany(f'INSERT INTO "{name}" ({col_sql}) VALUES ({placeholders})', rows)


def _load_categories(datasets: list[Dataset]) -> dict[int, str]:
    categories: dict[int, str] = {}
    for dataset in datasets:
        for row in _read_csv(dataset.path / "food_category.csv"):
            cid = _to_int(row.get("id"))
            if cid is not None and cid not in categories:
                categories[cid] = row.get("description", "")
    return categories


def _load_nutrients(datasets: list[Dataset]) -> list[tuple[Any, ...]]:
    """Nutrient dictionary in dataset order (SR Legacy first), adding ids only a
    later dataset knows. SR Legacy's names/ranks win where both list an id —
    that is what the shipped DB (and the app's name-based resolver) uses."""
    seen: set[int] = set()
    rows: list[tuple[Any, ...]] = []
    for dataset in datasets:
        for row in _read_csv(dataset.path / "nutrient.csv"):
            nid = _to_int(row.get("id"))
            if nid is None or nid in seen:
                continue
            seen.add(nid)
            rows.append(
                (
                    nid,
                    row.get("name", ""),
                    row.get("unit_name", ""),
                    _to_float(row.get("nutrient_nbr")),
                    _to_float(row.get("rank")),
                )
            )
    return rows


def _load_foods(datasets: list[Dataset], categories: dict[int, str]) -> list[tuple[Any, ...]]:
    foods: list[tuple[Any, ...]] = []
    seen: set[int] = set()
    for dataset in datasets:
        for row in _read_csv(dataset.path / "food.csv"):
            # food.csv of a Foundation release also lists its sample / acquisition
            # sub-records; keep only the dataset's own food records.
            if str(row.get("data_type", "")).strip().lower() != dataset.data_type:
                continue
            fdc_id = _to_int(row.get("fdc_id"))
            if fdc_id is None or fdc_id in seen:
                continue
            seen.add(fdc_id)
            description = row.get("description", "")
            category_id = _to_int(row.get("food_category_id"))
            category = categories.get(category_id) if category_id is not None else None
            foods.append(
                (
                    fdc_id,
                    dataset.data_type,
                    description,
                    float(category_id) if category_id is not None else None,
                    row.get("publication_date") or None,
                    category_id if category is not None else None,
                    category,
                    int(is_single_ingredient_like(description, category or "")),
                )
            )
    return foods


def _build_rankings(
    datasets: list[Dataset],
    foods: list[tuple[Any, ...]],
    nutrients: list[tuple[Any, ...]],
) -> list[tuple[Any, ...]]:
    ranked_foods = {f[0]: (f[2], f[6]) for f in foods if f[7]}
    nutrient_by_id = {n[0]: (n[1], n[2]) for n in nutrients}
    rows: list[tuple[int, str, str, int, str, str | None, float]] = []
    for dataset in datasets:
        for row in _read_csv(dataset.path / "food_nutrient.csv"):
            fdc_id = _to_int(row.get("fdc_id"))
            food = ranked_foods.get(fdc_id) if fdc_id is not None else None
            if food is None:
                continue
            nid = _to_int(row.get("nutrient_id"))
            nutrient = nutrient_by_id.get(nid) if nid is not None else None
            if nutrient is None:
                continue
            amount = _to_float(row.get("amount"))
            rows.append((nid, nutrient[0], nutrient[1], fdc_id, food[0], food[1], 0.0 if amount is None else amount))

    # Richest food first per nutrient; ties by description, then input order
    # (Python's sort is stable, so the result is deterministic).
    rows.sort(key=lambda r: (r[0], -r[6], r[4]))
    ranked: list[tuple[Any, ...]] = []
    current_nid: int | None = None
    rank = 0
    for r in rows:
        if r[0] != current_nid:
            current_nid, rank = r[0], 0
        rank += 1
        ranked.append((*r, rank))
    return ranked


def _profile_column(profile_id: str) -> str:
    if profile_id == "none":
        return "allowed_no_restriction"
    return f"allowed_{normalize_profile_column_id(profile_id)}"


def _build_dietary_flags(
    foods: list[tuple[Any, ...]],
    profiles: list[dict[str, Any]],
    rules: dict[str, dict[str, Any]],
) -> tuple[list[str], list[tuple[Any, ...]]]:
    """One row per distinct single-ingredient-like description (first food wins
    for the category), sorted by food_description like the shipped DB."""
    flag_columns = [_profile_column(str(p["id"])) for p in profiles]
    columns = ["food_key", "food_description", "food_category", *flag_columns, "blocked_profiles"]

    by_key: dict[str, tuple[str, str | None]] = {}
    for food in foods:
        if not food[7]:
            continue
        key = normalize_lookup_key(food[2])
        if key and key not in by_key:
            by_key[key] = (food[2], food[6])

    rows: list[tuple[Any, ...]] = []
    for key, (description, category) in sorted(by_key.items(), key=lambda item: (item[1][0], item[0])):
        allowed: list[int] = []
        blocked: list[str] = []
        for profile in profiles:
            ok = food_allowed_for_profile(description, profile, rule_map=rules)
            allowed.append(int(ok))
            if not ok and profile["id"] != "none":
                blocked.append(str(profile["id"]))
        rows.append((key, description, category, *allowed, "|".join(blocked)))
    return columns, rows


def _view_sql(view: str, source: str, alias: str, column: str) -> str:
    # Same text as the views in the shipped DB.
    return (
        f"CREATE VIEW {view} AS\n"
        f"                SELECT {alias}.*\n"
        f"                FROM {source} AS {alias}\n"
        f"                JOIN food_dietary_flags AS d\n"
        f"                  ON d.food_description = {alias}.food_description\n"
        f"                WHERE d.{column} = 1"
    )


def build_db(
    foundation_dir: Path,
    sr_legacy_dir: Path,
    output_db: Path,
    *,
    profiles_path: Path = DEFAULT_PROFILES_PATH,
    rules_path: Path = DEFAULT_RULES_PATH,
) -> dict[str, int]:
    """Build the rankings DB from unzipped Foundation + SR Legacy CSV folders.

    The DB is written to a temporary file next to ``output_db`` and moved into
    place only after a successful build, so a failed run never leaves a broken
    or half-written database behind. Returns summary counts.
    """
    # SR Legacy first, then Foundation: this is the row order of the shipped DB.
    datasets = [
        Dataset(Path(sr_legacy_dir), SR_LEGACY_DATA_TYPE),
        Dataset(Path(foundation_dir), FOUNDATION_DATA_TYPE),
    ]
    for dataset in datasets:
        _check_dataset_dir(dataset)

    categories = _load_categories(datasets)
    nutrients = _load_nutrients(datasets)
    foods = _load_foods(datasets, categories)
    rankings = _build_rankings(datasets, foods, nutrients)
    profiles = load_dietary_profiles(Path(profiles_path))
    rules = load_dietary_restriction_rules(Path(rules_path))
    flag_columns, flags = _build_dietary_flags(foods, profiles, rules)

    single_count = sum(1 for f in foods if f[7])
    data_types = sorted({f[1] for f in foods})
    metadata = [
        ("source_dataset", "|".join(d.name for d in datasets)),
        ("source_dataset_count", str(len(datasets))),
        ("included_data_types", ",".join(data_types)),
        # Historical key name: the total number of foods (all data types).
        ("foundation_food_count", str(len(foods))),
        ("single_ingredient_like_food_count", str(single_count)),
        ("ranking_row_count", str(len(rankings))),
    ]

    output_db = Path(output_db)
    output_db.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{output_db.name}.", suffix=".tmp", dir=output_db.parent)
    os.close(fd)
    tmp_path = Path(tmp_name)
    tmp_path.unlink()
    try:
        conn = sqlite3.connect(str(tmp_path))
        try:
            for name in ("foods", "nutrients", "nutrient_rankings"):
                _create_table(conn, name, _SCHEMA[name])
            _insert(conn, "foods", [c for c, _ in _SCHEMA["foods"]], foods)
            _insert(conn, "nutrients", [c for c, _ in _SCHEMA["nutrients"]], nutrients)
            _insert(conn, "nutrient_rankings", [c for c, _ in _SCHEMA["nutrient_rankings"]], rankings)

            flag_schema = [
                (col, "INTEGER" if col.startswith("allowed_") else "TEXT") for col in flag_columns
            ]
            _create_table(conn, "food_dietary_flags", flag_schema)
            _insert(conn, "food_dietary_flags", flag_columns, flags)

            _create_table(conn, "metadata", _SCHEMA["metadata"])
            _insert(conn, "metadata", ["key", "value"], metadata)

            conn.execute("CREATE INDEX idx_rankings_nutrient_rank ON nutrient_rankings (nutrient_id, rank_desc)")
            conn.execute("CREATE INDEX idx_rankings_food ON nutrient_rankings (fdc_id)")
            conn.execute("CREATE INDEX idx_nutrients_name ON nutrients (nutrient_name)")
            conn.execute("CREATE INDEX idx_food_dietary_flags_key ON food_dietary_flags (food_key)")

            for column in flag_columns:
                if not column.startswith("allowed_") or column == "allowed_no_restriction":
                    continue
                suffix = column[len("allowed_"):]
                conn.execute(_view_sql(f"foods_allowed_{suffix}", "foods", "f", column))
                conn.execute(_view_sql(f"nutrient_rankings_allowed_{suffix}", "nutrient_rankings", "r", column))
            conn.commit()
        finally:
            conn.close()
        os.replace(tmp_path, output_db)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    counts = {f"foods_{dt}": sum(1 for f in foods if f[1] == dt) for dt in data_types}
    counts.update(
        {
            "foods": len(foods),
            "single_ingredient_like_foods": single_count,
            "nutrients": len(nutrients),
            "ranking_rows": len(rankings),
            "dietary_flag_rows": len(flags),
        }
    )
    return counts


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def find_dataset_dir(root: Path) -> Path:
    """The folder inside an unzipped release that holds food.csv (shallowest)."""
    candidates = sorted(root.rglob("food.csv"), key=lambda p: (len(p.parts), str(p)))
    if not candidates:
        raise FileNotFoundError(f"no food.csv found under {root}")
    return candidates[0].parent


def _safe_extract(archive: zipfile.ZipFile, dest: Path) -> None:
    dest_resolved = dest.resolve()
    for member in archive.infolist():
        target = (dest / member.filename).resolve()
        if target != dest_resolved and dest_resolved not in target.parents:
            raise ValueError(f"unsafe path in zip archive: {member.filename}")
    archive.extractall(dest)


def download_dataset(url: str, download_dir: Path, *, attempts: int = 3, timeout: float = 300.0) -> Path:
    """Download and unzip one FoodData Central CSV release; return its CSV folder."""
    download_dir = Path(download_dir)
    download_dir.mkdir(parents=True, exist_ok=True)
    zip_name = Path(urllib.parse.urlparse(url).path).name or "dataset.zip"
    zip_path = download_dir / zip_name
    part_path = download_dir / f"{zip_name}.part"

    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "SuppSwipe-usda-refresh/1.0"})
            with urllib.request.urlopen(request, timeout=timeout) as response, part_path.open("wb") as fh:
                shutil.copyfileobj(response, fh)
            with zipfile.ZipFile(part_path) as archive:
                bad = archive.testzip()
                if bad is not None:
                    raise zipfile.BadZipFile(f"corrupt member {bad}")
            os.replace(part_path, zip_path)
            break
        except (urllib.error.URLError, OSError, zipfile.BadZipFile) as exc:
            last_error = exc
            part_path.unlink(missing_ok=True)
            print(f"Download attempt {attempt}/{attempts} failed for {url}: {exc}", file=sys.stderr)
            if attempt < attempts:
                time.sleep(2 ** attempt)
    else:
        raise RuntimeError(f"could not download {url}: {last_error}")

    extract_dir = download_dir / Path(zip_name).stem
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    with zipfile.ZipFile(zip_path) as archive:
        _safe_extract(archive, extract_dir)
    return find_dataset_dir(extract_dir)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build the USDA whole-food rankings DB (SR Legacy + Foundation Foods) "
            "used by the app from FoodData Central CSV releases."
        )
    )
    parser.add_argument(
        "--download-latest",
        action="store_true",
        help="Download the pinned Foundation and SR Legacy CSV zips (for any dataset not given as a local folder).",
    )
    parser.add_argument("--foundation-csv-dir", type=Path, help="Unzipped Foundation Foods CSV release folder.")
    parser.add_argument("--sr-legacy-csv-dir", type=Path, help="Unzipped SR Legacy CSV release folder.")
    parser.add_argument("--foundation-url", default=DEFAULT_FOUNDATION_CSV_URL, help="Foundation CSV zip URL.")
    parser.add_argument("--sr-legacy-url", default=DEFAULT_SR_LEGACY_CSV_URL, help="SR Legacy CSV zip URL.")
    parser.add_argument(
        "--download-dir",
        type=Path,
        help="Where to keep downloaded zips (default: a temporary folder removed after the build).",
    )
    parser.add_argument("--output-db", type=Path, default=DEFAULT_OUTPUT_DB)
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES_PATH, help="Dietary profiles JSON.")
    parser.add_argument(
        "--restriction-rules", type=Path, default=DEFAULT_RULES_PATH, help="Dietary restriction rules JSON."
    )
    args = parser.parse_args(argv)
    if not args.download_latest and not (args.foundation_csv_dir and args.sr_legacy_csv_dir):
        parser.error(
            "pass --download-latest, or both --foundation-csv-dir and --sr-legacy-csv-dir "
            "(the app needs SR Legacy and Foundation foods)"
        )
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="usda_downloads_") as tmp:
        download_dir = args.download_dir or Path(tmp)
        foundation_dir = args.foundation_csv_dir
        sr_legacy_dir = args.sr_legacy_csv_dir
        if foundation_dir is None:
            print(f"Downloading Foundation Foods: {args.foundation_url}")
            foundation_dir = download_dataset(args.foundation_url, download_dir / "foundation")
        if sr_legacy_dir is None:
            print(f"Downloading SR Legacy: {args.sr_legacy_url}")
            sr_legacy_dir = download_dataset(args.sr_legacy_url, download_dir / "sr_legacy")

        counts = build_db(
            foundation_dir,
            sr_legacy_dir,
            args.output_db,
            profiles_path=args.profiles,
            rules_path=args.restriction_rules,
        )
    summary = ", ".join(f"{k}={v}" for k, v in counts.items())
    print(f"Built DB: {args.output_db} ({summary})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
