from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def normalize_lookup_key(value: str) -> str:
    text = str(value or "").strip().lower().replace("_", " ")
    text = re.sub(r"[^a-z0-9\s\-\+\(\)]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_profile_column_id(profile_id: str) -> str:
    return normalize_lookup_key(profile_id).replace(" ", "_")


def load_dietary_profiles(profiles_path: Path) -> list[dict[str, Any]]:
    if not profiles_path.exists():
        return [{"id": "none", "label": "No restriction", "description": "No dietary filtering", "avoid_keywords": []}]

    try:
        raw = json.loads(profiles_path.read_text(encoding="utf-8"))
    except Exception:
        return [{"id": "none", "label": "No restriction", "description": "No dietary filtering", "avoid_keywords": []}]

    if not isinstance(raw, list):
        return [{"id": "none", "label": "No restriction", "description": "No dietary filtering", "avoid_keywords": []}]

    profiles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        profile_id = normalize_lookup_key(str(item.get("id", "") or ""))
        label = str(item.get("label", "") or "").strip()
        if not profile_id or not label or profile_id in seen:
            continue
        seen.add(profile_id)
        avoid = item.get("avoid_keywords", [])
        if not isinstance(avoid, list):
            avoid = []
        profiles.append(
            {
                "id": profile_id,
                "label": label,
                "description": str(item.get("description", "") or "").strip(),
                "avoid_keywords": [normalize_lookup_key(str(x)) for x in avoid if str(x).strip()],
            }
        )

    if not profiles:
        profiles.append({"id": "none", "label": "No restriction", "description": "No dietary filtering", "avoid_keywords": []})
    return profiles


def load_dietary_restriction_rules(rules_path: Path) -> dict[str, dict[str, Any]]:
    if not rules_path.exists():
        return {}

    try:
        raw = json.loads(rules_path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    if not isinstance(raw, list):
        return {}

    rules: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        rule_id = normalize_lookup_key(str(item.get("id", "") or ""))
        if not rule_id:
            continue
        avoid = item.get("avoid_keywords", [])
        if not isinstance(avoid, list):
            avoid = []
        rules[rule_id] = {
            "id": rule_id,
            "avoid_keywords": [normalize_lookup_key(str(x)) for x in avoid if str(x).strip()],
            "notes": str(item.get("notes", "") or "").strip(),
        }

    return rules


def keyword_matches_food_blob(keyword: str, blob: str, blob_compact: str) -> bool:
    normalized_keyword = normalize_lookup_key(keyword)
    if not normalized_keyword:
        return False

    if " " in normalized_keyword and normalized_keyword in blob:
        return True

    pattern = rf"(?<![a-z0-9]){re.escape(normalized_keyword)}(?![a-z0-9])"
    if re.search(pattern, blob):
        return True

    compact_keyword = normalized_keyword.replace(" ", "")
    if compact_keyword and compact_keyword in blob_compact:
        return True

    return False


def expanded_profile_avoid_keywords(
    profile: dict[str, Any] | None,
    rule_map: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    if not profile:
        return []

    profile_id = normalize_lookup_key(str(profile.get("id", "") or ""))
    profile_label = normalize_lookup_key(str(profile.get("label", "") or ""))
    profile_keywords = [normalize_lookup_key(str(x)) for x in (profile.get("avoid_keywords", []) or []) if str(x).strip()]

    rules = rule_map or {}
    rule_keywords: list[str] = []
    if profile_id and profile_id in rules:
        rule_keywords = [
            normalize_lookup_key(str(x))
            for x in (rules[profile_id].get("avoid_keywords", []) or [])
            if str(x).strip()
        ]

    avoid_keywords = {x for x in (profile_keywords + rule_keywords) if x}

    marine_animal_tokens = {
        "fish", "salmon", "sardine", "anchovy", "tuna", "trout", "mackerel", "cod", "herring",
        "shellfish", "shrimp", "prawn", "crab", "lobster", "clam", "mussel", "mussels", "oyster",
        "oysters", "scallop", "scallops", "squid", "octopus", "roe", "caviar", "whelk", "mollusk", "mollusks",
    }
    land_animal_tokens = {
        "beef", "veal", "pork", "ham", "bacon", "chicken", "turkey", "lamb", "mutton", "goat", "duck", "goose",
        "moose", "deer", "venison", "bison", "buffalo", "elk", "rabbit", "caribou", "emu", "ostrich", "boar",
        "pheasant", "quail", "seal", "whale", "walrus", "sea lion", "game meat",
    }
    organ_and_derivative_tokens = {
        "liver", "kidney", "heart", "tripe", "gizzard", "tongue", "sweetbread", "organ meat", "offal", "gelatin", "collagen",
    }

    if profile_id == "vegetarian" or profile_label == "vegetarian":
        avoid_keywords.update(land_animal_tokens)
        avoid_keywords.update(marine_animal_tokens)
        avoid_keywords.update(organ_and_derivative_tokens)

    if profile_id == "vegan" or profile_label == "vegan":
        avoid_keywords.update(land_animal_tokens)
        avoid_keywords.update(marine_animal_tokens)
        avoid_keywords.update(organ_and_derivative_tokens)
        avoid_keywords.update({"egg", "eggs", "milk", "cream", "cheese", "yogurt", "butter", "honey", "whey", "casein"})

    if profile_id == "pescatarian" or profile_label == "pescatarian":
        avoid_keywords.update(land_animal_tokens)
        avoid_keywords.update(organ_and_derivative_tokens)

    if profile_id == "nut free" or profile_label == "nut free":
        avoid_keywords.update(
            {
                "peanut", "almond", "almond butter", "walnut", "cashew", "hazelnut", "filbert", "pistachio", "pecan",
                "macadamia", "brazil nut", "brazilnut", "pine nut", "pinenut", "mixed nuts", "nut butter",
            }
        )

    if profile_id == "kosher style" or profile_label == "kosher style":
        avoid_keywords.update({"whelk", "mollusk", "mollusks"})

    return sorted(avoid_keywords)


def food_allowed_for_profile(
    food_description: str,
    profile: dict[str, Any] | None,
    rule_map: dict[str, dict[str, Any]] | None = None,
) -> bool:
    if not profile:
        return True

    avoid_keywords = expanded_profile_avoid_keywords(profile, rule_map=rule_map)
    if not avoid_keywords:
        return True

    blob = normalize_lookup_key(food_description)
    blob_compact = blob.replace(" ", "")
    if not blob:
        return False

    for keyword in avoid_keywords:
        if keyword_matches_food_blob(keyword, blob, blob_compact):
            return False

    return True