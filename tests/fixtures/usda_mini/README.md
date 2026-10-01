Tiny FoodData Central-shaped CSV releases for `tests/test_usda_builder.py`.

`foundation/` mimics a Foundation Foods CSV download (its `food.csv` also lists
sample / acquisition sub-records, which the builder must skip) and `sr_legacy/`
an SR Legacy download. Same file names, column order and quoting as the real
USDA files; the values are synthetic.
