# SuppSwap Architecture Flow (ReAct-style Execution Map)

This diagram shows the end-to-end execution path and the concrete tools/functions used at each stage.

```mermaid
flowchart TD
    A[User Input in UI\nbuild_mobile_ui] --> B{Input Type}

    B -->|Image| C[OCR Pipeline\ncall_openrouter_vision_ocr]
    C --> C1[OpenRouter Vision]
    C1 -->|fallback| C2[OpenAI Vision]
    C2 -->|fallback| C3[GitHub Models Vision]
    C3 -->|fallback| C4[try_tesseract_ocr]

    B -->|URL| D[Web Parsing Pipeline\nfetch_clean_page_text -> extract_supplement_text_from_url]
    B -->|Manual Text| E[Raw Text]

    C --> F[Combined Text]
    D --> F
    E --> F

    F --> G[Component Extraction\nparse_components]
    G --> G1[LLM JSON extraction]
    G --> G2[Rule-based fallback\nparse_components_rule_based]

    G --> H[Normalization\nnormalize_component_name + normalize_lookup_key]

    H --> I[USDA Mapping Orchestrator\nbuild_usda_matches]

    I --> J[Load Local Maps]
    J --> J1[load_component_aliases\ncomponent_aliases.csv]
    J --> J2[load_component_similarity_map\ncomponent_similarity_map.csv]
    J --> J3[load_component_proxy_rules\ncomponent_proxy_rules.csv]

    I --> K[Resolve Nutrient Candidates\nresolve_component_to_nutrients]
    K --> K1[Alias Match]
    K --> K2[Similarity/Family Match]
    K --> K3[Direct DB Name Match]
    K --> K4[Curated Proxy Match]
    K --> K5[Fuzzy Match]
    K --> K6[LLM Match Last Resort]

    I --> L[USDA DB Access\ntry_open_usda_db -> _lookup_nutrient_row]
    L --> M[Retrieve + Sort Foods\nget_top_ranked_foods]
    M --> M1[SQL ORDER BY\namount_per_100g DESC, rank_desc ASC]

    I --> N[Merge Multi-Nutrient Foods\nDeduplicate food list]
    I --> O{Mapped?}
    O -->|No| P[log_unmapped_component\nunmapped_components_log.csv]
    O -->|Yes| Q[Component Summary + Details]

    Q --> R[Dose Matching\nunit_to_mg + grams_needed_to_match_dose]

    R --> S[Pricing Estimation\nget_food_price_estimate]
    S --> S1[Local Price DB First\nload_whole_food_prices + lookup_local_price_offers]
    S --> S2[Optional Live Market/API\nfetch_market_price_offers\nfetch_serpapi_shopping_offers\nfetch_dataforseo_shopping_offers]
    S --> S3[Optional LLM Estimate\nestimate_price_with_llm]
    S --> S4[Offer Ranking\n_rank_price_offers]

    Q --> T[Meal Requirement Build\nresolve_cheapest_meal_requirements]
    T --> U[Local Meal Suggestions\nfind_local_meal_suggestions]
    U --> U1[Recipe coverage scoring\n_evaluate_recipe_coverage]
    U --> U2[Optional AI meals\ngenerate_llm_meal_suggestions]

    Q --> V[UI Render\nOverview table + per-component expander]
    S4 --> V
    U --> V

    W[Local Feedback Loop] --> J2
    P --> W
```

## Data files in this flow
- data/component_aliases.csv
- data/component_similarity_map.csv
- data/component_proxy_rules.csv
- data/unmapped_components_log.csv
- data/usda_rankings.db
- data/whole_food_prices.csv
- data/meal_recipes_local.json
- data/meal_recipes_fitness_pack.json
