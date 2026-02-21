from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path

import pandas as pd


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


def is_single_ingredient_like(description: str) -> bool:
    d = (description or "").lower()
    if not d:
        return False
    for keyword in PROCESSED_KEYWORDS:
        if re.search(rf"\b{re.escape(keyword)}\b", d):
            return False
    return True


def build_db(dataset_dir: Path, db_path: Path) -> None:
    food = pd.read_csv(dataset_dir / "food.csv", low_memory=False)
    foundation = pd.read_csv(dataset_dir / "foundation_food.csv")
    food_nutrient = pd.read_csv(dataset_dir / "food_nutrient.csv", low_memory=False)
    nutrient = pd.read_csv(dataset_dir / "nutrient.csv", low_memory=False)
    food_category = pd.read_csv(dataset_dir / "food_category.csv", low_memory=False)

    foundation_foods = food.merge(foundation[["fdc_id"]], on="fdc_id", how="inner")
    foundation_foods = foundation_foods[
        foundation_foods["data_type"].astype(str).str.lower() == "foundation_food"
    ].copy()

    foundation_foods["is_single_ingredient_like"] = foundation_foods["description"].apply(is_single_ingredient_like)
    foundation_foods = foundation_foods.merge(
        food_category[["id", "description"]],
        left_on="food_category_id",
        right_on="id",
        how="left",
        suffixes=("", "_category"),
    )

    nutrient = nutrient.rename(columns={"name": "nutrient_name", "rank": "nutrient_rank"})

    merged = food_nutrient.merge(
        nutrient[["id", "nutrient_name", "unit_name", "nutrient_nbr", "nutrient_rank"]],
        left_on="nutrient_id",
        right_on="id",
        how="inner",
    )

    merged = merged.merge(
        foundation_foods[[
            "fdc_id",
            "description",
            "description_category",
            "publication_date",
            "is_single_ingredient_like",
        ]],
        on="fdc_id",
        how="inner",
    )

    merged["amount"] = pd.to_numeric(merged["amount"], errors="coerce").fillna(0.0)

    ranked = merged[merged["is_single_ingredient_like"]].copy()
    ranked = ranked.sort_values(["nutrient_id", "amount", "description"], ascending=[True, False, True])
    ranked["rank_desc"] = ranked.groupby("nutrient_id").cumcount() + 1

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        foundation_foods.rename(columns={"description": "food_description", "description_category": "food_category"}).to_sql(
            "foods", conn, if_exists="replace", index=False
        )

        nutrient[["id", "nutrient_name", "unit_name", "nutrient_nbr", "nutrient_rank"]].to_sql(
            "nutrients", conn, if_exists="replace", index=False
        )

        ranked[[
            "nutrient_id",
            "nutrient_name",
            "unit_name",
            "fdc_id",
            "description",
            "description_category",
            "amount",
            "rank_desc",
        ]].rename(
            columns={
                "description": "food_description",
                "description_category": "food_category",
                "amount": "amount_per_100g",
            }
        ).to_sql("nutrient_rankings", conn, if_exists="replace", index=False)

        metadata_rows = pd.DataFrame(
            [
                {"key": "source_dataset", "value": str(dataset_dir)},
                {"key": "foundation_food_count", "value": str(len(foundation_foods))},
                {"key": "single_ingredient_like_food_count", "value": str(int(foundation_foods["is_single_ingredient_like"].sum()))},
                {"key": "ranking_row_count", "value": str(len(ranked))},
            ]
        )
        metadata_rows.to_sql("metadata", conn, if_exists="replace", index=False)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_rankings_nutrient_rank ON nutrient_rankings (nutrient_id, rank_desc)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rankings_food ON nutrient_rankings (fdc_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_nutrients_name ON nutrients (nutrient_name)")
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build precomputed USDA nutrient ranking DB for whole-food lookup")
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "FoodData_Central_foundation_food_csv_2025-12-18",
    )
    parser.add_argument(
        "--output-db",
        type=Path,
        default=Path(__file__).resolve().parent / "data" / "usda_rankings.db",
    )
    args = parser.parse_args()

    build_db(args.dataset_dir, args.output_db)
    print(f"Built DB: {args.output_db}")


if __name__ == "__main__":
    main()
