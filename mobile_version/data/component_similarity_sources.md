# Component Similarity Mapping Sources

This file documents how to curate and expand `component_similarity_map.csv`.

## Core principle
Use **local deterministic mappings first** (alias/similarity/proxy), and use LLM mapping only as last fallback.

## Recommended source hierarchy
1. **USDA FoodData Central nutrient schema (primary)**
   - Use nutrient names exactly as they appear in your local USDA rankings DB (`nutrients.nutrient_name`).
   - Ensures mappings resolve directly and avoid runtime misses.

2. **NIH Office of Dietary Supplements Fact Sheets (naming + forms)**
   - Useful for vitamin form equivalences (e.g., pyridoxine/P5P → Vitamin B-6, folic acid/5-MTHF → Folate family).

3. **EFSA / NHS / national nutrition authorities (terminology variants)**
   - Helpful for EU/UK naming conventions and spelling variants.

4. **Peer-reviewed nutrition references for fatty-acid naming**
   - Map supplement terms (fish oil, omega-3, omega-6) to measurable analytes (EPA, DHA, linoleic acid) present in USDA-style datasets.

## Curation workflow
1. Collect unresolved component terms from app logs/QA sessions.
2. Normalize term to lower-case canonical key (`normalize_lookup_key`).
3. Add 1..N rows to `component_similarity_map.csv`:
   - `component`
   - `target_nutrient` (must match USDA nutrient name in DB)
   - `confidence` (`high|medium|low`)
   - `relationship` (`synonym|form|family|form_proxy|...`)
   - `priority` (1 highest)
   - `rationale`
4. Run smoke test and verify mapped/unmapped counts.
5. Promote repeated LLM-resolved terms into the CSV to reduce future network dependency.

## Important limitations
- There is no truly complete static list of every supplement naming variant globally.
- New products introduce new brand terms and proprietary blends continuously.
- Treat this map as a living local knowledge base that grows from real user inputs.
