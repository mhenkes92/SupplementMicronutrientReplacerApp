import sqlite3, os
db = "usda_rankings.db"
c = sqlite3.connect(db)
rows = c.execute(
    "SELECT id, nutrient_name, unit_name FROM nutrients "
    "WHERE lower(nutrient_name) LIKE '%protein%' "
    "   OR lower(nutrient_name) LIKE '%fat%' "
    "   OR lower(nutrient_name) LIKE '%carbohydrate%' "
    "   OR lower(nutrient_name) LIKE '%energy%'"
).fetchall()
for r in rows:
    print(r)
c.close()
