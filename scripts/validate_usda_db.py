"""Validate a freshly built USDA rankings DB before it replaces the live one.

Standalone (sqlite3 only) so it can run in CI without importing the heavy app.
Exits non-zero if the DB is missing tables, is suspiciously small, or if any
nutrient the app depends on lost its food rows — which blocks the weekly refresh
from shipping a broken database.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# Nutrient ids the app explicitly depends on (overrides / known-good mappings).
# If a future USDA release drops these, the refresh must fail loudly.
REQUIRED_NUTRIENT_IDS: dict[int, str] = {
    1109: "Vitamin E (alpha-tocopherol)",
    1272: "PUFA 22:6 n-3 (DHA)",
    1278: "PUFA 20:5 n-3 (EPA)",
    1404: "PUFA 18:3 n-3 (ALA)",
}

MIN_NUTRIENTS = 100
MIN_RANKING_ROWS = 5000


def _fail(msg: str) -> None:
    print(f"VALIDATION FAILED: {msg}")
    sys.exit(1)


def main() -> None:
    if len(sys.argv) < 2:
        _fail("usage: validate_usda_db.py <path-to-db>")
    db_path = Path(sys.argv[1])
    if not db_path.exists():
        _fail(f"DB not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for required in ("nutrients", "nutrient_rankings"):
            if required not in tables:
                _fail(f"missing table: {required}")

        n_nutrients = conn.execute("SELECT COUNT(*) FROM nutrients").fetchone()[0]
        n_rankings = conn.execute(
            "SELECT COUNT(*) FROM nutrient_rankings WHERE amount_per_100g > 0"
        ).fetchone()[0]
        if n_nutrients < MIN_NUTRIENTS:
            _fail(f"too few nutrients: {n_nutrients} < {MIN_NUTRIENTS}")
        if n_rankings < MIN_RANKING_ROWS:
            _fail(f"too few ranking rows: {n_rankings} < {MIN_RANKING_ROWS}")

        for nid, label in REQUIRED_NUTRIENT_IDS.items():
            rows = conn.execute(
                "SELECT COUNT(*) FROM nutrient_rankings WHERE nutrient_id = ? AND amount_per_100g > 0",
                (nid,),
            ).fetchone()[0]
            if rows <= 0:
                _fail(f"nutrient {nid} ({label}) has no food rows")
    finally:
        conn.close()

    print(
        f"VALIDATION OK: {n_nutrients} nutrients, {n_rankings} ranking rows, "
        f"all {len(REQUIRED_NUTRIENT_IDS)} required nutrients present."
    )


if __name__ == "__main__":
    main()
