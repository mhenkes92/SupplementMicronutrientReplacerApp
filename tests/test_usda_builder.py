"""Offline tests for scripts/build_usda_rankings_db.py.

The builder runs on tiny FoodData Central-shaped CSV releases in
tests/fixtures/usda_mini/ (no USDA download). Schema and the whole-food rule are
also checked against the shipped blockbrain/data/usda_rankings.db so a rebuild
can't silently change the content shape the app reads.
"""
from __future__ import annotations

import hashlib
import importlib.util
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BUILDER_PATH = ROOT / "scripts" / "build_usda_rankings_db.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "usda_mini"
FOUNDATION_DIR = FIXTURES / "foundation"
SR_LEGACY_DIR = FIXTURES / "sr_legacy"
LIVE_DB = ROOT / "blockbrain" / "data" / "usda_rankings.db"

PROFILE_VIEW_SUFFIXES = [
    "vegetarian",
    "vegan",
    "pescatarian",
    "halal_friendly",
    "kosher_style",
    "gluten_free",
    "lactose_free",
    "nut_free",
    "low_sodium_aware",
]


@pytest.fixture(scope="module")
def builder():
    spec = importlib.util.spec_from_file_location("usda_builder", BUILDER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def built(builder, tmp_path_factory):
    out = tmp_path_factory.mktemp("usda_build") / "usda_rankings.db"
    counts = builder.build_db(FOUNDATION_DIR, SR_LEGACY_DIR, out)
    return out, counts


@pytest.fixture()
def db(built):
    conn = sqlite3.connect(f"file:{built[0]}?mode=ro", uri=True)
    yield conn
    conn.close()


def _live_db():
    if not LIVE_DB.exists() or LIVE_DB.stat().st_size == 0:
        pytest.skip("shipped usda_rankings.db not present")
    return sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _zip_release(src: Path, zip_path: Path) -> Path:
    """Zip a fixture release the way USDA ships it (one top-level folder)."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for f in sorted(src.iterdir()):
            archive.write(f, f"{src.name}/{f.name}")
    return zip_path


# --- content -----------------------------------------------------------------


def test_build_keeps_sr_legacy_and_foundation_foods(built, db):
    _, counts = built
    by_type = dict(db.execute("SELECT data_type, COUNT(*) FROM foods GROUP BY data_type"))
    assert by_type == {"foundation_food": 10, "sr_legacy_food": 9}
    assert counts["foods_sr_legacy_food"] == 9 and counts["foods_foundation_food"] == 10
    # Sample / acquisition sub-records in the Foundation food.csv are skipped ...
    ids = {r[0] for r in db.execute("SELECT fdc_id FROM foods")}
    assert not ids & {319874, 319875, 330008}
    # ... but a foundation_food row missing from foundation_food.csv is kept.
    assert 330007 in ids
    # SR Legacy first, then Foundation (row order of the shipped DB).
    order = [r[0] for r in db.execute("SELECT data_type FROM foods ORDER BY rowid")]
    assert order == ["sr_legacy_food"] * 9 + ["foundation_food"] * 10


def test_foods_rows_carry_category_and_types(db):
    row = db.execute(
        "SELECT fdc_id, data_type, food_description, food_category_id, publication_date, id, "
        "food_category, is_single_ingredient_like FROM foods WHERE fdc_id = 168462"
    ).fetchone()
    assert row == (168462, "sr_legacy_food", "Spinach, raw", 11.0, "2019-04-01", 11,
                   "Vegetables and Vegetable Products", 1)
    types = db.execute(
        "SELECT typeof(food_category_id), typeof(id), typeof(is_single_ingredient_like) FROM foods LIMIT 1"
    ).fetchone()
    assert types == ("real", "integer", "integer")


@pytest.mark.parametrize(
    "description, expected",
    [
        ("Spinach, raw", 1),
        ("Kale, raw", 1),
        ("Lentils, raw", 1),
        ("Eggs, Grade A, Large, egg whole", 1),
        # whole-word matching: "broilers" is not "oil", "fryers" is not "fried"
        ("Chicken, broilers or fryers, meat only, raw", 1),
        # "butternut" is not "butter"
        ("Squash, winter, butternut, raw", 1),
        ("Bread, white wheat", 0),  # Baked Products category
        ("Salt, table, iodized", 0),  # Spices and Herbs category
        ("Cheese, cheddar", 0),
        ("Apples, fuji, with skin, raw", 0),  # "with" = composite
        ("Beef, variety meats and by-products, liver, raw", 0),  # "and"
        ("Hummus, commercial", 0),
        ("Fish, mackerel, salted", 0),
    ],
)
def test_single_ingredient_flag(db, description, expected):
    flags = {r[0] for r in db.execute(
        "SELECT is_single_ingredient_like FROM foods WHERE food_description = ?", (description,)
    )}
    assert flags == {expected}


def test_nutrients_sr_legacy_first_then_foundation_only_ids(db):
    rows = db.execute("SELECT id, nutrient_name, unit_name, nutrient_nbr, nutrient_rank FROM nutrients ORDER BY rowid").fetchall()
    assert [r[0] for r in rows] == [1003, 1089, 1106, 1114, 1162, 1190, 2000, 1100, 1176, 1278, 2047]
    by_id = {r[0]: r for r in rows}
    assert by_id[2000][1] == "Sugars, Total"  # SR Legacy name wins over Foundation's "Total Sugars"
    assert by_id[1190] == (1190, "Folate, DFE", "UG", 435.0, 7600.0)  # SR Legacy-only
    assert by_id[2047][4] is None  # blank rank -> NULL


def test_rankings_order_and_filters(db):
    iron = db.execute(
        "SELECT rank_desc, fdc_id, food_description, food_category, amount_per_100g, nutrient_name, unit_name "
        "FROM nutrient_rankings WHERE nutrient_id = 1089 ORDER BY rowid"
    ).fetchall()
    assert [r[0] for r in iron] == list(range(1, len(iron) + 1))
    amounts = [r[4] for r in iron]
    assert amounts == sorted(amounts, reverse=True)
    assert iron[0][1:5] == (330004, "Seeds, pumpkin seeds (pepitas), raw", "Nut and Seed Products", 8.07)
    assert iron[1][1] == 172420  # SR Legacy lentils rank among Foundation foods
    assert {r[5:] for r in iron} == {("Iron, Fe", "MG")}
    ranked_ids = {r[1] for r in iron}
    # non-whole foods, sample records and unknown nutrient ids never reach the rankings
    assert not ranked_ids & {167512, 169451, 175139, 330001, 319874, 319875}
    assert db.execute("SELECT COUNT(*) FROM nutrient_rankings WHERE nutrient_id = 9999").fetchone()[0] == 0
    singles = {r[0] for r in db.execute("SELECT fdc_id FROM foods WHERE is_single_ingredient_like = 1")}
    all_ranked = {r[0] for r in db.execute("SELECT DISTINCT fdc_id FROM nutrient_rankings")}
    assert all_ranked <= singles


def test_blank_or_non_numeric_amount_becomes_zero(db):
    biotin = db.execute(
        "SELECT food_description, amount_per_100g, rank_desc FROM nutrient_rankings "
        "WHERE nutrient_id = 1176 ORDER BY rank_desc"
    ).fetchall()
    assert biotin == [
        ("Eggs, Grade A, Large, egg whole", 16.0, 1),
        ("Broccoli, raw", 0.0, 2),  # blank amount
        ("Seeds, pumpkin seeds (pepitas), raw", 0.0, 3),  # "n/a"
    ]


def test_dietary_flags_one_row_per_description(builder, db):
    cur = db.execute("SELECT * FROM food_dietary_flags ORDER BY rowid")
    columns = [d[0] for d in cur.description]
    rows = [dict(zip(columns, r)) for r in cur.fetchall()]
    descriptions = [r["food_description"] for r in rows]
    assert descriptions == sorted(descriptions)
    assert descriptions.count("Kale, raw") == 1  # in both datasets, flagged once
    singles = {r[0] for r in db.execute("SELECT food_description FROM foods WHERE is_single_ingredient_like = 1")}
    assert set(descriptions) == singles

    by_desc = {r["food_description"]: r for r in rows}
    assert by_desc["Kale, raw"]["food_key"] == "kale raw"
    assert by_desc["Kale, raw"]["allowed_vegan"] == 1
    assert by_desc["Kale, raw"]["blocked_profiles"] == ""
    salmon = by_desc["Fish, salmon, Atlantic, farm raised, raw"]
    assert (salmon["allowed_vegan"], salmon["allowed_vegetarian"], salmon["allowed_pescatarian"]) == (0, 0, 1)
    assert salmon["blocked_profiles"] == "vegetarian|vegan"
    assert by_desc["Eggs, Grade A, Large, egg whole"]["blocked_profiles"] == "vegan"
    assert by_desc["Nuts, walnuts, english"]["allowed_nut_free"] == 0
    assert all(r["allowed_no_restriction"] == 1 for r in rows)


def test_dietary_flags_follow_the_classifier(builder, db):
    from blockbrain.dietary_food_classifier import food_allowed_for_profile

    profiles = builder.load_dietary_profiles(builder.DEFAULT_PROFILES_PATH)
    rules = builder.load_dietary_restriction_rules(builder.DEFAULT_RULES_PATH)
    cur = db.execute("SELECT * FROM food_dietary_flags")
    columns = [d[0] for d in cur.description]
    for row in cur.fetchall():
        record = dict(zip(columns, row))
        for profile in profiles:
            expected = int(food_allowed_for_profile(record["food_description"], profile, rule_map=rules))
            assert record[builder._profile_column(profile["id"])] == expected


def test_diet_views_filter_foods_and_rankings(db):
    vegan_foods = {r[0] for r in db.execute("SELECT food_description FROM foods_allowed_vegan")}
    assert "Kale, raw" in vegan_foods and "Spinach, raw" in vegan_foods
    assert "Fish, salmon, Atlantic, farm raised, raw" not in vegan_foods
    assert "Eggs, Grade A, Large, egg whole" not in vegan_foods
    vegan_iron = {r[0] for r in db.execute("SELECT fdc_id FROM nutrient_rankings_allowed_vegan WHERE nutrient_id = 1089")}
    assert 172420 in vegan_iron and 330000 not in vegan_iron
    pesc = {r[0] for r in db.execute("SELECT food_description FROM foods_allowed_pescatarian")}
    assert "Fish, salmon, Atlantic, farm raised, raw" in pesc


def test_metadata(built, db):
    _, counts = built
    meta = dict(db.execute("SELECT key, value FROM metadata"))
    assert meta == {
        "source_dataset": "sr_legacy|foundation",
        "source_dataset_count": "2",
        "included_data_types": "foundation_food,sr_legacy_food",
        "foundation_food_count": "19",
        "single_ingredient_like_food_count": str(counts["single_ingredient_like_foods"]),
        "ranking_row_count": str(counts["ranking_rows"]),
    }


# --- parity with the shipped DB ----------------------------------------------


def test_schema_matches_shipped_db(db):
    live = _live_db()
    try:
        for table in ("foods", "nutrients", "nutrient_rankings", "food_dietary_flags", "metadata"):
            live_cols = [(r[1], r[2]) for r in live.execute(f"PRAGMA table_info({table})")]
            new_cols = [(r[1], r[2]) for r in db.execute(f"PRAGMA table_info({table})")]
            assert new_cols == live_cols, table
        live_objects = dict(live.execute("SELECT name, sql FROM sqlite_master WHERE type IN ('index', 'view')"))
        new_objects = dict(db.execute("SELECT name, sql FROM sqlite_master WHERE type IN ('index', 'view')"))
        assert new_objects == live_objects
        live_meta_keys = [r[0] for r in live.execute("SELECT key FROM metadata ORDER BY rowid")]
        new_meta_keys = [r[0] for r in db.execute("SELECT key FROM metadata ORDER BY rowid")]
        assert new_meta_keys == live_meta_keys
    finally:
        live.close()
    views = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'view'")}
    assert views == {f"{kind}_allowed_{s}" for kind in ("foods", "nutrient_rankings") for s in PROFILE_VIEW_SUFFIXES}


def test_whole_food_rule_reproduces_shipped_db(builder):
    live = _live_db()
    try:
        rows = live.execute(
            "SELECT food_description, food_category, is_single_ingredient_like FROM foods"
        ).fetchall()
    finally:
        live.close()
    assert len(rows) > 8000
    mismatches = [
        (desc, cat, flag) for desc, cat, flag in rows
        if int(builder.is_single_ingredient_like(desc, cat or "")) != flag
    ]
    assert mismatches == []


# --- build mechanics ---------------------------------------------------------


def test_build_is_byte_for_byte_reproducible(builder, built, tmp_path):
    again = tmp_path / "again.db"
    builder.build_db(FOUNDATION_DIR, SR_LEGACY_DIR, again)
    assert _sha256(again) == _sha256(built[0])


def test_failed_build_leaves_existing_db_untouched(builder, tmp_path):
    broken = tmp_path / "sr_legacy"
    shutil.copytree(SR_LEGACY_DIR, broken)
    (broken / "food_nutrient.csv").unlink()
    out = tmp_path / "usda_rankings.db"
    out.write_bytes(b"previous good db")
    with pytest.raises(FileNotFoundError, match="food_nutrient.csv"):
        builder.build_db(FOUNDATION_DIR, broken, out)
    assert out.read_bytes() == b"previous good db"
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_build_overwrites_existing_output(builder, built, tmp_path):
    out = tmp_path / "usda_rankings.db"
    out.write_bytes(b"stale")
    builder.build_db(FOUNDATION_DIR, SR_LEGACY_DIR, out)
    assert _sha256(out) == _sha256(built[0])


# --- CLI / download ----------------------------------------------------------


def test_cli_download_latest_from_pinned_zip_urls(builder, built, tmp_path, capsys):
    foundation_zip = _zip_release(FOUNDATION_DIR, tmp_path / "foundation.zip")
    sr_zip = _zip_release(SR_LEGACY_DIR, tmp_path / "sr_legacy.zip")
    out = tmp_path / "out" / "usda_rankings.db"
    rc = builder.main([
        "--download-latest",
        "--foundation-url", foundation_zip.as_uri(),
        "--sr-legacy-url", sr_zip.as_uri(),
        "--download-dir", str(tmp_path / "downloads"),
        "--output-db", str(out),
    ])
    assert rc == 0
    assert "Built DB" in capsys.readouterr().out
    # same release content -> identical DB to the local-folder build
    assert _sha256(out) == _sha256(built[0])
    assert (tmp_path / "downloads" / "foundation" / "foundation.zip").exists()


def test_cli_mixes_local_folder_and_download(builder, built, tmp_path):
    sr_zip = _zip_release(SR_LEGACY_DIR, tmp_path / "sr_legacy.zip")
    out = tmp_path / "usda_rankings.db"
    rc = builder.main([
        "--download-latest",
        "--foundation-csv-dir", str(FOUNDATION_DIR),
        "--sr-legacy-url", sr_zip.as_uri(),
        "--output-db", str(out),
    ])
    assert rc == 0
    assert _sha256(out) == _sha256(built[0])


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--foundation-csv-dir", str(FOUNDATION_DIR)],  # SR Legacy is mandatory
        ["--sr-legacy-csv-dir", str(SR_LEGACY_DIR)],
    ],
)
def test_cli_requires_both_datasets(builder, argv, capsys):
    with pytest.raises(SystemExit) as exc:
        builder.main(argv)
    assert exc.value.code == 2
    assert "--download-latest" in capsys.readouterr().err


def test_script_runs_standalone_with_ci_arguments(tmp_path):
    """What .github/workflows/refresh-usda-db.yml runs (minus the download)."""
    help_out = subprocess.run(
        [sys.executable, str(BUILDER_PATH), "--help"], capture_output=True, text=True, cwd=ROOT, timeout=60
    )
    assert help_out.returncode == 0, help_out.stderr
    for flag in ("--download-latest", "--foundation-csv-dir", "--sr-legacy-csv-dir", "--output-db"):
        assert flag in help_out.stdout

    out = tmp_path / "usda_rankings.db"
    run = subprocess.run(
        [
            sys.executable, str(BUILDER_PATH),
            "--foundation-csv-dir", str(FOUNDATION_DIR),
            "--sr-legacy-csv-dir", str(SR_LEGACY_DIR),
            "--output-db", str(out),
        ],
        capture_output=True, text=True, cwd=tmp_path, timeout=120,
    )
    assert run.returncode == 0, run.stderr
    assert "foods_sr_legacy_food=9" in run.stdout
    assert out.exists()


def test_download_retries_then_gives_up(builder, tmp_path, monkeypatch):
    monkeypatch.setattr(builder.time, "sleep", lambda s: None)
    missing = (tmp_path / "nope.zip").as_uri()
    with pytest.raises(RuntimeError, match="could not download"):
        builder.download_dataset(missing, tmp_path / "dl", attempts=2)
    assert not list((tmp_path / "dl").glob("*.part"))


def test_download_rejects_corrupt_zip(builder, tmp_path, monkeypatch):
    monkeypatch.setattr(builder.time, "sleep", lambda s: None)
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"<html>USDA maintenance page</html>")
    with pytest.raises(RuntimeError, match="could not download"):
        builder.download_dataset(bad.as_uri(), tmp_path / "dl", attempts=1)


def test_download_rejects_zip_path_traversal(builder, tmp_path):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as archive:
        archive.writestr("../escaped.csv", "x")
        archive.writestr("release/food.csv", "fdc_id\n")
    with pytest.raises(ValueError, match="unsafe path"):
        builder.download_dataset(evil.as_uri(), tmp_path / "dl", attempts=1)
    assert not (tmp_path / "dl" / "escaped.csv").exists()
