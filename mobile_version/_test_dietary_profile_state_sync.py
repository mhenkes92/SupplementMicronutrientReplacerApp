import app


PROFILES = [
    {"id": "none", "label": "No restriction", "avoid_keywords": []},
    {"id": "vegan", "label": "Vegan", "avoid_keywords": ["egg", "milk"]},
    {"id": "vegetarian", "label": "Vegetarian", "avoid_keywords": ["beef"]},
]


def run_test() -> bool:
    ok = True

    state = {}
    selected_id, selected_profile = app._resolve_results_dietary_profile_state(PROFILES, state)
    if selected_id != "none" or state.get("results_dietary_profile_selector") != "none" or state.get("global_diet_profile") != "none":
        print("FAIL A:", selected_id, selected_profile, state)
        ok = False
    else:
        print("PASS A")

    state = {
        "global_diet_profile": "none",
        "results_dietary_profile_selector": "vegan",
    }
    selected_id, selected_profile = app._resolve_results_dietary_profile_state(PROFILES, state)
    if selected_id != "vegan" or state.get("global_diet_profile") != "vegan":
        print("FAIL B:", selected_id, selected_profile, state)
        ok = False
    else:
        print("PASS B")

    state = {
        "global_diet_profile": "vegetarian",
        "results_dietary_profile_selector": "not-a-real-profile",
    }
    selected_id, selected_profile = app._resolve_results_dietary_profile_state(PROFILES, state)
    if selected_id != "none" or state.get("results_dietary_profile_selector") != "none" or state.get("global_diet_profile") != "none":
        print("FAIL C:", selected_id, selected_profile, state)
        ok = False
    else:
        print("PASS C")

    return ok


if __name__ == "__main__":
    raise SystemExit(0 if run_test() else 1)
