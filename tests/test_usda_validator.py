"""Tests for scripts/validate_usda_db.py: the shipped DB passes; DBs that lost
SR Legacy, a critical nutrient or the dietary flags fail (exit code 1)."""
from __future__ import annotations

import importlib.util
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VALIDATOR_PATH = ROOT / "scripts" / "validate_usda_db.py"
BUILDER_PATH = ROOT / "scripts" / "build_usda_rankings_db.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "usda_mini"
LIVE_DB = ROOT / "blockbrain" / "data" / "usda_rankings.db"

# The app-critical ids the task list names (verified present in the shipped DB).
APP_CRITICAL_IDS = {
    1106, 1114, 1109, 1190, 1187, 1178, 1165, 1166, 1167, 1170, 1175, 1176, 1185,
    1087, 1089, 1090, 1095, 1103, 1100, 1098, 1101, 1092, 1180, 1278, 1272,
}


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def validator():
    return _load("usda_validator", VALIDATOR_PATH)


@pytest.fixture(scope="module")
def live_db() -> Path:
    if not LIVE_DB.exists() or LIVE_DB.stat().st_size == 0:
        pytest.skip("shipped usda_rankings.db not present")
    return LIVE_DB


def _broken_copy(src: Path, dest: Path, *statements: str) -> Path:
    shutil.copyfile(src, dest)
    conn = sqlite3.connect(dest)
    try:
        for sql in statements:
            conn.execute(sql)
        conn.commit()
    finally:
        conn.close()
    return dest


def _run_cli(db_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(VALIDATOR_PATH), str(db_path)], capture_output=True, text=True, timeout=120
    )


def test_required_ids_cover_app_critical_nutrients(validator):
    assert APP_CRITICAL_IDS <= set(validator.REQUIRED_NUTRIENT_IDS)
    # vitamin C, ALA and the macro-table ids the app reads are guarded too
    assert {1162, 1404, 1003, 1004, 1005} <= set(validator.REQUIRED_NUTRIENT_IDS)


def test_shipped_db_passes(validator, live_db):
    errors, stats = validator.validate(live_db)
    assert errors == []
    assert stats["foods_sr_legacy_food"] >= validator.MIN_FOODS_PER_DATA_TYPE["sr_legacy_food"]
    assert stats["foods_foundation_food"] >= validator.MIN_FOODS_PER_DATA_TYPE["foundation_food"]


def test_shipped_db_cli_exit_zero(live_db):
    run = _run_cli(live_db)
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.startswith("VALIDATION OK")
    assert "SR Legacy" in run.stdout


def test_foundation_only_db_fails(validator, live_db, tmp_path):
    """What the old CI builder produced: Foundation foods only."""
    bad = _broken_copy(
        live_db,
        tmp_path / "foundation_only.db",
        "DELETE FROM nutrient_rankings WHERE fdc_id IN (SELECT fdc_id FROM foods WHERE data_type = 'sr_legacy_food')",
        "DELETE FROM foods WHERE data_type = 'sr_legacy_food'",
    )
    errors, _ = validator.validate(bad)
    text = "\n".join(errors)
    assert "too few sr_legacy_food foods: 0" in text
    assert "too few ranked sr_legacy_food foods: 0" in text
    assert "1190 (Folate, DFE) has no food rows" in text  # folate DFE only comes from SR Legacy
    run = _run_cli(bad)
    assert run.returncode == 1
    assert "VALIDATION FAILED: too few sr_legacy_food foods" in run.stdout


@pytest.mark.parametrize(
    "statements, expected",
    [
        (["DELETE FROM nutrient_rankings WHERE nutrient_id = 1106"], "nutrient 1106 (Vitamin A, RAE) has no food rows"),
        (["UPDATE nutrient_rankings SET amount_per_100g = 0 WHERE nutrient_id = 1178"], "nutrient 1178 (Vitamin B-12) has no food rows"),
        (["DELETE FROM nutrients WHERE id = 1114"], "nutrient 1114 (Vitamin D (D2 + D3)) missing from nutrients"),
        (["DELETE FROM nutrient_rankings WHERE nutrient_id = 1100"], "nutrient 1100 (Iodine, I) has no food rows"),
    ],
)
def test_missing_critical_nutrient_fails(validator, live_db, tmp_path, statements, expected):
    bad = _broken_copy(live_db, tmp_path / "bad.db", *statements)
    errors, _ = validator.validate(bad)
    assert errors == [expected]
    run = _run_cli(bad)
    assert run.returncode == 1
    assert f"VALIDATION FAILED: {expected}" in run.stdout


def test_missing_dietary_flags_fails(validator, live_db, tmp_path):
    bad = _broken_copy(live_db, tmp_path / "no_flags.db", "DROP TABLE food_dietary_flags")
    errors, _ = validator.validate(bad)
    assert errors == ["missing table: food_dietary_flags"]
    assert _run_cli(bad).returncode == 1


def test_missing_diet_view_and_unflagged_ranked_food_fail(validator, live_db, tmp_path):
    bad = _broken_copy(
        live_db,
        tmp_path / "partial_flags.db",
        "DROP VIEW foods_allowed_vegan",
        "DELETE FROM food_dietary_flags WHERE food_description = 'Kale, raw'",
    )
    errors, _ = validator.validate(bad)
    assert "missing view: foods_allowed_vegan" in errors
    assert "1 ranked foods have no food_dietary_flags row" in errors


def test_missing_column_fails(validator, live_db, tmp_path):
    bad = _broken_copy(
        live_db,
        tmp_path / "no_col.db",
        "DROP INDEX idx_rankings_nutrient_rank",
        "ALTER TABLE nutrient_rankings DROP COLUMN rank_desc",
    )
    errors, _ = validator.validate(bad)
    assert "table nutrient_rankings is missing columns: rank_desc" in errors


def test_tiny_fixture_build_fails_minimum_counts(validator, tmp_path):
    """A structurally complete but tiny DB is still rejected (row minimums)."""
    builder = _load("usda_builder_for_validator", BUILDER_PATH)
    out = tmp_path / "mini.db"
    builder.build_db(FIXTURES / "foundation", FIXTURES / "sr_legacy", out)
    errors, _ = validator.validate(out)
    text = "\n".join(errors)
    assert "missing table" not in text and "missing view" not in text
    assert "too few sr_legacy_food foods: 9" in text
    assert "too few nutrients" in text


@pytest.mark.parametrize("content", [None, b"", b"<html>not a database</html>" * 50])
def test_unreadable_input_fails_cleanly(validator, tmp_path, content):
    path = tmp_path / "broken.db"
    if content is not None:
        path.write_bytes(content)
    errors, _ = validator.validate(path)
    assert errors
    run = _run_cli(path)
    assert run.returncode == 1
    assert "VALIDATION FAILED" in run.stdout
    assert "Traceback" not in run.stderr


def test_cli_usage_error(validator, capsys):
    assert validator.main([]) == 1
    assert "usage" in capsys.readouterr().out
