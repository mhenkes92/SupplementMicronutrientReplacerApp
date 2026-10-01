"""Validate a freshly built USDA rankings DB before it replaces the live one.

Standalone (sqlite3 only) so it can run in CI without importing the heavy app.
Exits non-zero if the DB is missing tables or columns, lost one of its two
source datasets (SR Legacy / Foundation), lost the dietary flags, or if any
nutrient the app depends on lost its food rows — which blocks the weekly
refresh from shipping a broken database.

    python scripts/validate_usda_db.py blockbrain/data/usda_rankings.db
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# Nutrient ids the app depends on (RDA cards, overrides, macro table). Each must
# exist in `nutrients` and keep at least one food row with an amount > 0. Some
# only come from one dataset (folate DFE / folate food: SR Legacy; biotin and
# iodine: Foundation), so these also catch a build that lost a dataset.
REQUIRED_NUTRIENT_IDS: dict[int, str] = {
    1106: "Vitamin A, RAE",
    1114: "Vitamin D (D2 + D3)",
    1109: "Vitamin E (alpha-tocopherol)",
    1162: "Vitamin C, total ascorbic acid",
    1185: "Vitamin K (phylloquinone)",
    1165: "Thiamin",
    1166: "Riboflavin",
    1167: "Niacin",
    1170: "Pantothenic acid",
    1175: "Vitamin B-6",
    1176: "Biotin",
    1190: "Folate, DFE",
    1187: "Folate, food",
    1178: "Vitamin B-12",
    1180: "Choline, total",
    1087: "Calcium, Ca",
    1089: "Iron, Fe",
    1090: "Magnesium, Mg",
    1092: "Potassium, K",
    1095: "Zinc, Zn",
    1098: "Copper, Cu",
    1100: "Iodine, I",
    1101: "Manganese, Mn",
    1103: "Selenium, Se",
    1272: "PUFA 22:6 n-3 (DHA)",
    1278: "PUFA 20:5 n-3 (EPA)",
    1404: "PUFA 18:3 n-3 (ALA)",
    1003: "Protein",
    1004: "Total lipid (fat)",
    1005: "Carbohydrate, by difference",
}

# Columns the app and tooling read (subset; extra columns are fine).
REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "foods": ("fdc_id", "data_type", "food_description", "food_category", "is_single_ingredient_like"),
    "nutrients": ("id", "nutrient_name", "unit_name"),
    "nutrient_rankings": (
        "nutrient_id",
        "nutrient_name",
        "unit_name",
        "fdc_id",
        "food_description",
        "food_category",
        "amount_per_100g",
        "rank_desc",
    ),
    "food_dietary_flags": ("food_key", "food_description", "allowed_vegetarian", "allowed_vegan", "blocked_profiles"),
}

# The shipped DB has 7,793 SR Legacy + 436 Foundation foods, of which 1,090 /
# 216 are ranked. SR Legacy is frozen; Foundation grows with each release.
MIN_FOODS_PER_DATA_TYPE: dict[str, int] = {"sr_legacy_food": 7500, "foundation_food": 350}
MIN_RANKED_FOODS_PER_DATA_TYPE: dict[str, int] = {"sr_legacy_food": 900, "foundation_food": 150}

MIN_NUTRIENTS = 400  # shipped: 477
MIN_RANKING_ROWS = 50000  # rows with amount > 0; shipped: 70,369 (Foundation alone: ~6,400)
MIN_DIETARY_FLAG_ROWS = 1000  # shipped: 1,227


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(r[1]) for r in conn.execute(f'PRAGMA table_info("{table}")')}


def validate(db_path: Path) -> tuple[list[str], dict[str, int]]:
    """Return (errors, stats). An empty error list means the DB is fit to ship."""
    db_path = Path(db_path)
    if not db_path.is_file():
        return [f"DB not found: {db_path}"], {}

    errors: list[str] = []
    stats: dict[str, int] = {}
    try:
        conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return [f"cannot open {db_path}: {exc}"], {}
    try:
        try:
            objects = dict(conn.execute("SELECT name, type FROM sqlite_master"))
        except sqlite3.DatabaseError as exc:
            return [f"not a readable SQLite database: {db_path} ({exc})"], {}

        present: set[str] = set()
        for table, columns in REQUIRED_COLUMNS.items():
            if objects.get(table) != "table":
                errors.append(f"missing table: {table}")
                continue
            missing = [c for c in columns if c not in _columns(conn, table)]
            if missing:
                errors.append(f"table {table} is missing columns: {', '.join(missing)}")
                continue
            present.add(table)

        if "nutrients" in present:
            stats["nutrients"] = conn.execute("SELECT COUNT(*) FROM nutrients").fetchone()[0]
            if stats["nutrients"] < MIN_NUTRIENTS:
                errors.append(f"too few nutrients: {stats['nutrients']} < {MIN_NUTRIENTS}")

        if "nutrient_rankings" in present:
            stats["ranking_rows"] = conn.execute(
                "SELECT COUNT(*) FROM nutrient_rankings WHERE amount_per_100g > 0"
            ).fetchone()[0]
            if stats["ranking_rows"] < MIN_RANKING_ROWS:
                errors.append(f"too few ranking rows: {stats['ranking_rows']} < {MIN_RANKING_ROWS}")

        if "foods" in present:
            foods_by_type = dict(conn.execute("SELECT data_type, COUNT(*) FROM foods GROUP BY data_type"))
            for data_type, minimum in MIN_FOODS_PER_DATA_TYPE.items():
                n = int(foods_by_type.get(data_type, 0))
                stats[f"foods_{data_type}"] = n
                if n < minimum:
                    errors.append(f"too few {data_type} foods: {n} < {minimum}")

        if {"foods", "nutrient_rankings"} <= present:
            ranked_by_type = dict(
                conn.execute(
                    "SELECT f.data_type, COUNT(DISTINCT r.fdc_id) FROM nutrient_rankings AS r "
                    "JOIN foods AS f ON f.fdc_id = r.fdc_id WHERE r.amount_per_100g > 0 GROUP BY f.data_type"
                )
            )
            for data_type, minimum in MIN_RANKED_FOODS_PER_DATA_TYPE.items():
                n = int(ranked_by_type.get(data_type, 0))
                stats[f"ranked_foods_{data_type}"] = n
                if n < minimum:
                    errors.append(f"too few ranked {data_type} foods: {n} < {minimum}")

        if {"nutrients", "nutrient_rankings"} <= present:
            known = {int(r[0]) for r in conn.execute("SELECT id FROM nutrients")}
            rows_by_nutrient = dict(
                conn.execute(
                    "SELECT nutrient_id, COUNT(*) FROM nutrient_rankings WHERE amount_per_100g > 0 GROUP BY nutrient_id"
                )
            )
            for nid, label in REQUIRED_NUTRIENT_IDS.items():
                if nid not in known:
                    errors.append(f"nutrient {nid} ({label}) missing from nutrients")
                elif int(rows_by_nutrient.get(nid, 0)) <= 0:
                    errors.append(f"nutrient {nid} ({label}) has no food rows")

        if "food_dietary_flags" in present:
            stats["dietary_flag_rows"] = conn.execute("SELECT COUNT(*) FROM food_dietary_flags").fetchone()[0]
            if stats["dietary_flag_rows"] < MIN_DIETARY_FLAG_ROWS:
                errors.append(
                    f"too few food_dietary_flags rows: {stats['dietary_flag_rows']} < {MIN_DIETARY_FLAG_ROWS}"
                )
            for column in sorted(_columns(conn, "food_dietary_flags")):
                if not column.startswith("allowed_") or column == "allowed_no_restriction":
                    continue
                suffix = column[len("allowed_"):]
                for view in (f"foods_allowed_{suffix}", f"nutrient_rankings_allowed_{suffix}"):
                    if objects.get(view) != "view":
                        errors.append(f"missing view: {view}")
            if "nutrient_rankings" in present:
                unflagged = conn.execute(
                    "SELECT COUNT(DISTINCT r.food_description) FROM nutrient_rankings AS r "
                    "LEFT JOIN food_dietary_flags AS d ON d.food_description = r.food_description "
                    "WHERE d.food_description IS NULL"
                ).fetchone()[0]
                if unflagged:
                    errors.append(f"{unflagged} ranked foods have no food_dietary_flags row")
    except sqlite3.DatabaseError as exc:
        errors.append(f"query failed: {exc}")
    finally:
        conn.close()
    return errors, stats


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("VALIDATION FAILED: usage: validate_usda_db.py <path-to-db>")
        return 1
    errors, stats = validate(Path(args[0]))
    if errors:
        for err in errors:
            print(f"VALIDATION FAILED: {err}")
        return 1
    print(
        f"VALIDATION OK: {stats['nutrients']} nutrients, {stats['ranking_rows']} ranking rows, "
        f"{stats['foods_sr_legacy_food']} SR Legacy + {stats['foods_foundation_food']} Foundation foods "
        f"({stats['ranked_foods_sr_legacy_food']} + {stats['ranked_foods_foundation_food']} ranked), "
        f"{stats['dietary_flag_rows']} dietary flag rows, "
        f"all {len(REQUIRED_NUTRIENT_IDS)} required nutrients present."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
