"""Which foods and pills go well together, and which are best kept apart: the deterministic engine behind the "How to combine" parts of
SuppSwipe (the "Pairs well with" row of a swipe card, the "Your day" block of the Meals tab, the "See all combinations" table, the
plan-row dialog and the meal layout of the meal-plan prompt). No AI, no network, no Streamlit, no file access: the rules below are the
module's own source (a curated table; every rule has an authoritative source and a general-guidance wording), so a redeploy replaces
them together with the code and `_load_current` reloads the module like any other.

Public names (all functions are total: they never raise, input they cannot read gives an empty / neutral answer; the output is
plain JSON-able data and the same input always gives the same output, whatever the hash seed):
  RULES / rule(rule_id)                              the curated table (rule() returns a copy)
  card_tags(...)                                     at most two short tags for a swipe card ("some fat", "a meal")
  item_tips(...)                                     every tip of ONE nutrient (the dialog of a plan row)
  kept_pills(keep_items, hooks)                      {nutrient key: dose in mg} of the kept pills that count (the `kept` of item_tips)
  plan_day(keep_items, replace_items, hooks)         the day: which food and which kept pill goes into breakfast / lunch / dinner
  meal_layout(replace_items, n_meals, hooks)         the lines for the meal-plan prompt (foods only, never a pill)
  day_text(day)                                      the day as plain lines for the share text
  table_rows(keep_items, replace_items, hooks)       all rules for the "See all combinations" view, the visitor's own first

`hooks` is an optional dict of the app's own functions for what only it knows; every hook is optional and a failing hook counts as
absent: canon(name) -> nutrient key, title(decision) -> "Vitamin D3", dose_text(decision) -> "20 mcg" ("" when there is no dose),
food_label(food_row) -> "Mackerel, Atlantic", food_icon(food_row) -> glyph, grams(decision) -> grams a day or None,
over_limit(decision) -> bool, skip_food(decision) -> bool, fact(usda_description, "vitamin c") -> mg per 100 g or None.

Safety posture (do not weaken without a clinician's / the owner's review):
  * every sentence the visitor reads is a rule's takeaway / why / caveat (general: "usually", "can", "tends to") or one of the fixed
    texts below (`_HINTS`, `_DIRECTIVE_TEXT`, and the "in one tablet? follow your pack" notes `_ONE_TABLET` / `ONE_TABLET_NOTE`:
    the engine cannot tell separate pills from the nutrients of one tablet, so every rule that keeps pills apart says so);
    no hours, no doses, no numbers except the sourced 500 mg and 25 mg;
  * a pill the Heads-up box warns about (`over_limit`: over its upper intake level, or the smokers' beta-carotene advice) is invisible
    to every rule: no tip, never paired, not placed in a meal (only the pharmacist line still counts it, a medicine binds whatever
    the dose); the day says nothing more about it than "see the Heads-up";
  * the engine only orders what the visitor already chose: it never adds a pill, a dose or a food to the plan, and it has no diet,
    pregnancy or medication logic of its own (the app's own filter drops unsafe foods before they reach it: `skip_food`);
  * the foods are placed first and never look at a pill, so the meal layout of the prompt is the same for every set of kept pills;
    only the foods that must share a meal (or need something added) are placed, every other food can go in any meal.
"""
from __future__ import annotations

import copy
import logging
import re
from typing import Any, Mapping, NamedTuple

logger = logging.getLogger(__name__)

# The nutrients of one label may sit in ONE tablet (a multivitamin), and the engine cannot tell: the rules that keep two pills apart
# carry this note, and the day says it once above the schedule (and the share text as its last line) when it keeps pills apart.
_ONE_TABLET = "In one tablet? Follow the directions on your pack."
ONE_TABLET_NOTE = "Some of these in one tablet? Follow the directions on your pack."

# --- The curated table ------------------------------------------------------------------------------------------------------------
# One entry per rule. kind: with_fat | boost | avoid_with | separate | split_dose | with_meal | note. rel (the glyph): with = "+",
# apart = a double arrow, note = "i". rank: smaller is shown first in a meal. applies_to: pill | food | both. subjects: canonical
# nutrient keys (bb.canonical_nutrient_key) the rule is about. needs_partner: the table calls the rule "in your plan" only when the
# partner nutrient is in the plan too. condition_key: a token _holds() evaluates (None: always; a token it does not know: never).
# short: the partner in a few words for the card row (only the rules with "card" in surfaces). surfaces: card | day | dialog | table.
# verified: how the source was checked (the notes of the owner's sign-off). Edit a rule here, with its source: the lint of
# tests/test_combine_rules.py is strict on purpose (a rule that cannot be sourced is dropped, not softened).
_RULES: tuple[dict[str, Any], ...] = ({'id': 'iron-vitc',
  'kind': 'boost',
  'rel': 'with',
  'rank': 3,
  'applies_to': 'both',
  'subjects': ['iron'],
  'partners': ['vitamin c'],
  'needs_partner': False,
  'condition_key': None,
  'condition': None,
  'title': 'Iron + vitamin C',
  'icon': "\U0001F34A",
  'takeaway': 'Take iron with a vitamin C food or drink',
  'short': None,
  'why': 'Vitamin C helps the gut absorb iron, mostly the plant (non-heme) iron in beans, lentils, greens and iron pills. Citrus, '
         'peppers or a squeeze of lemon all work.',
  'caveats': 'Unsure whether you need iron pills? Ask your doctor. Iron overload (for example hemochromatosis): ask your doctor '
             'first.',
  'source': {'name': 'NIH ODS: Iron fact sheet for health professionals',
             'url': 'https://ods.od.nih.gov/factsheets/Iron-HealthProfessional/'},
  'also_sources': [{'name': 'NIH ODS The Scoop, February 2024 (iron supplement with a vitamin C drink such as orange juice)',
                    'url': 'https://ods.od.nih.gov/pubs/Scoop_February_2024.pdf'},
                   {'name': 'NIH ODS: Vitamin C fact sheet for health professionals',
                    'url': 'https://ods.od.nih.gov/factsheets/VitaminC-HealthProfessional/'},
                   {'name': 'EFSA Scientific Opinion 2009 on vitamin C health claims (non-haem iron absorption)',
                    'url': 'https://efsa.europa.eu/en/efsajournal/pub/1226'}],
  'confidence': 'high',
  'surfaces': ['day', 'dialog', 'table'],
  'verified': 'verified online (WebSearch excerpt of the cited page; the page itself could not be fetched from the sandbox); '
              'merged from the original iron-vitc-food and iron-vitc-pill'},
 {'id': 'iron-tea-coffee',
  'kind': 'avoid_with',
  'rel': 'apart',
  'rank': 6,
  'applies_to': 'both',
  'subjects': ['iron'],
  'partners': ['food:tea_coffee'],
  'needs_partner': False,
  'condition_key': 'non_heme_iron',
  'condition': 'An iron pill is kept, or the swapped food is a plant (non-heme) iron food',
  'title': 'Iron + tea and coffee',
  'icon': "\u2615",
  'takeaway': 'Have tea and coffee between meals, not with your iron',
  'short': None,
  'why': 'Tea and coffee polyphenols can cut plant-iron absorption a lot when drunk with the meal. Having them between meals '
         'lessens the effect.',
  'caveats': 'Low iron or anaemia (also in pregnancy): ask your doctor how to raise your iron. No need to give up tea or coffee.',
  'source': {'name': 'Hurrell RF, Reddy M, Cook JD. Inhibition of non-haem iron absorption in man by polyphenolic-containing '
                     'beverages. Br J Nutr, 1999',
             'url': 'https://pubmed.ncbi.nlm.nih.gov/?term=Inhibition+of+non+haem+iron+absorption+in+man+by+polyphenolic+containing+beverages+Hurrell+Reddy+Cook'},
  'also_sources': [{'name': 'Ahmad Fuzi SF et al. Am J Clin Nutr, 2017: a gap between an iron-containing meal and tea attenuates '
                            'the inhibition',
                    'url': 'https://pubmed.ncbi.nlm.nih.gov/?term=Ahmad+Fuzi+1+h+time+interval+between+a+meal+containing+iron+and+consumption+of+tea+attenuates+the+inhibitory+effect+on+iron+absorption'}],
  'confidence': 'high',
  'surfaces': ['day', 'dialog', 'table'],
  'verified': 'verified online (WebSearch excerpt of the cited page; the page itself could not be fetched from the sandbox); the '
              "'about an hour' sentence of the original why was removed (no hours on screen)"},
 {'id': 'iron-calcium-pill',
  'kind': 'separate',
  'rel': 'apart',
  'rank': 1,
  'applies_to': 'pill',
  'subjects': ['iron'],
  'partners': ['calcium'],
  'needs_partner': True,
  'condition_key': 'iron_and_calcium_pills',
  'condition': 'An iron pill and a calcium pill are both kept (calcium food is not covered: impractical, effect small over weeks)',
  'title': 'Iron pill + calcium pill',
  'icon': "\U0001F48A",
  'takeaway': 'Take iron and calcium pills at different meals',
  'short': None,
  'why': 'Calcium can lower iron absorption when taken at the same time. The effect is clearest at a single meal and smaller over '
         'weeks, but separating them is easy.',
  'caveats': _ONE_TABLET + ' Prescribed iron or calcium? Do not skip or move a dose on your own: ask your doctor or pharmacist about timing.',
  'source': {'name': 'NIH ODS: Iron fact sheet for health professionals',
             'url': 'https://ods.od.nih.gov/factsheets/Iron-HealthProfessional/'},
  'also_sources': [{'name': 'NIH ODS: Iron fact sheet for consumers', 'url': 'https://ods.od.nih.gov/factsheets/Iron-Consumer/'},
                   {'name': 'PubMed 19087437: The effect of calcium on iron absorption (review)',
                    'url': 'https://pubmed.ncbi.nlm.nih.gov/19087437/'}],
  'confidence': 'medium',
  'surfaces': ['day', 'dialog', 'table'],
  'verified': 'verified online (WebSearch excerpt of the cited page; the page itself could not be fetched from the sandbox) (ODS '
              'consumer page: taking calcium and iron at different times of the day might prevent the problem; ODS professional '
              'page: the interaction is not definitively established, hence medium)'},
 {'id': 'zinc-iron-pills',
  'kind': 'separate',
  'rel': 'apart',
  'rank': 1,
  'applies_to': 'pill',
  'subjects': ['zinc'],
  'partners': ['iron'],
  'needs_partner': True,
  'condition_key': 'iron_pill_mg_gte_25',
  'condition': 'A zinc pill and an iron pill of 25 mg or more are both kept (iron in fortified food does not matter much)',
  'title': 'Zinc pill + high-dose iron pill',
  'icon': "\U0001F48A",
  'takeaway': 'Take zinc and high-dose iron pills at different meals',
  'short': None,
  'why': 'Iron pills of 25 mg or more taken together with zinc can lower how much zinc you absorb. Iron added to fortified foods '
         'does not do this much.',
  'caveats': _ONE_TABLET,
  'source': {'name': 'NIH ODS: Zinc fact sheet for health professionals',
             'url': 'https://ods.od.nih.gov/factsheets/Zinc-HealthProfessional/'},
  'also_sources': [{'name': 'NIH ODS: Iron fact sheet for consumers', 'url': 'https://ods.od.nih.gov/factsheets/Iron-Consumer/'}],
  'confidence': 'medium',
  'surfaces': ['day', 'dialog', 'table'],
  'verified': 'verified online (WebSearch excerpt of the cited page; the page itself could not be fetched from the sandbox) '
              '(paraphrased by the search tool)'},
 {'id': 'calcium-split-500',
  'kind': 'split_dose',
  'rel': 'apart',
  'rank': 2,
  'applies_to': 'pill',
  'subjects': ['calcium'],
  'partners': [],
  'needs_partner': False,
  'condition_key': 'dose_mg_gt_500',
  'condition': 'The calcium dose on the label is above 500 mg',
  'title': 'Calcium in portions',
  'icon': "\U0001F48A",
  'takeaway': 'Split more than 500 mg of calcium over two meals',
  'short': None,
  'why': 'The body absorbs calcium best in amounts of 500 mg or less at a time, so a bigger dose works better split across two '
         'meals.',
  'caveats': _ONE_TABLET + ' Calcium prescribed by your doctor? Ask before changing how you take it.',
  'source': {'name': 'NIH ODS: Calcium fact sheet for consumers', 'url': 'https://ods.od.nih.gov/factsheets/Calcium-Consumer/'},
  'also_sources': [{'name': 'NIH ODS: Calcium fact sheet for health professionals',
                    'url': 'https://ods.od.nih.gov/factsheets/Calcium-HealthProfessional/'}],
  'confidence': 'high',
  'surfaces': ['day', 'dialog', 'table'],
  'verified': 'verified online (WebSearch excerpt of the cited page; the page itself could not be fetched from the sandbox); '
              'takeaway reworded to match the two-meal split the day makes'},
 {'id': 'calcium-carbonate-meal',
  'kind': 'with_meal',
  'rel': 'with',
  'rank': 5,
  'applies_to': 'pill',
  'subjects': ['calcium'],
  'partners': ['food:meal'],
  'needs_partner': False,
  'condition_key': 'form_carbonate',
  'condition': 'The label names calcium carbonate (an unknown form shows nothing)',
  'title': 'Calcium carbonate + a meal',
  'icon': "\U0001F37D\uFE0F",
  'takeaway': 'Take calcium carbonate with a meal',
  'short': 'a meal',
  'why': 'Calcium carbonate needs stomach acid, which a meal brings. Calcium citrate absorbs well with or without food, for '
         'example with low stomach acid.',
  'caveats': None,
  'source': {'name': 'NIH ODS: Calcium fact sheet for consumers', 'url': 'https://ods.od.nih.gov/factsheets/Calcium-Consumer/'},
  'also_sources': [],
  'confidence': 'high',
  'surfaces': ['card', 'day', 'dialog', 'table'],
  'verified': 'verified online (WebSearch excerpt of the cited page; the page itself could not be fetched from the sandbox) (the '
              "citrate sentence: 'absorbed well on an empty stomach or a full stomach')"},
 {'id': 'fat-vitamins-de',
  'kind': 'with_fat',
  'rel': 'with',
  'rank': 4,
  'applies_to': 'pill',
  'subjects': ['vitamin d', 'vitamin e'],
  'partners': ['food:fat'],
  'needs_partner': False,
  'condition_key': None,
  'condition': None,
  'title': 'Vitamins D and E + fat',
  'icon': "\U0001F951",
  'takeaway': 'Take vitamins D and E with a meal that has some fat',
  'short': 'some fat',
  'why': 'Vitamins D and E dissolve in fat, so they absorb better with a meal. D is still absorbed without food, so a missed meal '
         'is no problem.',
  'caveats': "Crohn's or celiac disease, or other fat-absorption problems: ask your doctor. On blood thinners: ask your doctor "
             'before vitamin E pills.',
  'source': {'name': 'NIH ODS: Vitamin D fact sheet for health professionals',
             'url': 'https://ods.od.nih.gov/factsheets/VitaminD-HealthProfessional/'},
  'also_sources': [{'name': 'NIH ODS: Vitamin E fact sheet for health professionals',
                    'url': 'https://ods.od.nih.gov/factsheets/VitaminE-HealthProfessional/'},
                   {'name': 'NIH ODS: Vitamin E fact sheet for consumers',
                    'url': 'https://ods.od.nih.gov/factsheets/VitaminE-Consumer/'}],
  'confidence': 'high',
  'surfaces': ['card', 'day', 'dialog', 'table'],
  'verified': 'verified online (WebSearch excerpt of the cited page; the page itself could not be fetched from the sandbox) (ODS '
              'vitamin D: fat in the gut improves absorption, some is absorbed without it; ODS vitamin E: the gut needs some fat '
              "to absorb it). 'With a meal' is the practical reading of both. Merged from the original fat-vitamin-d and "
              'fat-vitamin-e'},
 {'id': 'fat-omega3',
  'kind': 'with_fat',
  'rel': 'with',
  'rank': 4,
  'applies_to': 'pill',
  'subjects': ['omega 3', 'fish oil', 'epa', 'dha'],
  'partners': ['food:fat'],
  'needs_partner': False,
  'condition_key': None,
  'condition': None,
  'title': 'Fish oil + a meal with fat',
  'icon': "\U0001F41F",
  'takeaway': 'Take fish-oil capsules with a meal that has some fat',
  'short': 'some fat',
  'why': 'A meal with some fat helps the body digest and absorb the fats in fish oil. Some capsule types depend less on this than '
         'others.',
  'caveats': None,
  'source': {'name': 'Schuchardt JP, Hahn A. Bioavailability of long-chain omega-3 fatty acids (review). Prostaglandins Leukot '
                     'Essent Fatty Acids, 2013',
             'url': 'https://pubmed.ncbi.nlm.nih.gov/?term=Schuchardt+Hahn+Bioavailability+of+long+chain+omega+3+fatty+acids+Prostaglandins+Leukot+Essent+Fatty+Acids+2013'},
  'also_sources': [{'name': 'Lawson LD, Hughes BG. Absorption of eicosapentaenoic acid and docosahexaenoic acid from fish oil '
                            'triacylglycerols or fish oil ethyl esters coingested with a high-fat meal. Biochem Biophys Res '
                            'Commun, 1988',
                    'url': 'https://pubmed.ncbi.nlm.nih.gov/?term=Absorption+of+eicosapentaenoic+acid+and+docosahexaenoic+acid+from+fish+oil+triacylglycerols+or+fish+oil+ethyl+esters+coingested+with+a+high+fat+meal'}],
  'confidence': 'medium',
  'surfaces': ['card', 'day', 'dialog', 'table'],
  'verified': 'NOT on the NIH ODS omega-3 page (searched). Supported by two peer-reviewed papers whose titles were confirmed; '
              'effect sizes seen only in secondary summaries. Keep only if a human confirms the two papers; otherwise drop the '
              'rule (and its card tag)'},
 {'id': 'fat-carotenoids',
  'kind': 'with_fat',
  'rel': 'with',
  'rank': 4,
  'applies_to': 'food',
  'subjects': ['beta carotene', 'vitamin a'],
  'partners': ['food:fat'],
  'needs_partner': False,
  'condition_key': 'food_is_carotenoid_vegetable',
  'condition': 'The swapped food is an orange or dark-green vegetable (carotenoids)',
  'title': 'Carrots and greens + oil',
  'icon': "\U0001F955",
  'takeaway': 'Add a little oil to carrots, greens and tomatoes',
  'short': None,
  'why': 'Carotenoids dissolve in fat. In a small trial, salads with fat-free dressing gave almost no carotenoid uptake; dressing '
         'with oil gave much more.',
  'caveats': None,
  'source': {'name': 'Brown MJ et al., Am J Clin Nutr 2004: carotenoid bioavailability is higher from salads with full-fat '
                     'dressing',
             'url': 'https://pubmed.ncbi.nlm.nih.gov/?term=Carotenoid+bioavailability+is+higher+from+salads+ingested+with+full+fat+than+with+fat+reduced+salad+dressings'},
  'also_sources': [{'name': 'Unlu NZ et al., J Nutr 2005: avocado added to salad or salsa increased carotenoid absorption',
                    'url': 'https://pubmed.ncbi.nlm.nih.gov/?term=Unlu+carotenoid+absorption+avocado+salsa+salad+J+Nutr+2005'}],
  'confidence': 'high',
  'surfaces': ['day', 'dialog', 'table'],
  'verified': 'verified online (WebSearch excerpt of the cited page; the page itself could not be fetched from the sandbox) (study '
              'design and result confirmed from several summaries of the paper)'},
 {'id': 'calcium-vitamin-d',
  'kind': 'boost',
  'rel': 'with',
  'rank': 7,
  'applies_to': 'both',
  'subjects': ['calcium'],
  'partners': ['vitamin d'],
  'needs_partner': True,
  'condition_key': None,
  'condition': None,
  'title': 'Calcium + vitamin D',
  'icon': "\U0001F95B",
  'takeaway': 'Vitamin D helps your body absorb calcium',
  'short': None,
  'why': 'Vitamin D promotes calcium absorption in the gut, so low vitamin D can limit how much of your calcium the body takes up.',
  'caveats': None,
  'source': {'name': 'NIH ODS: Vitamin D fact sheet for health professionals',
             'url': 'https://ods.od.nih.gov/factsheets/VitaminD-HealthProfessional/'},
  'also_sources': [{'name': 'NIH ODS: Calcium fact sheet for health professionals',
                    'url': 'https://ods.od.nih.gov/factsheets/Calcium-HealthProfessional/'},
                   {'name': "EU Regulation 432/2012: authorised claim 'Vitamin D contributes to the normal absorption/utilisation "
                            "of calcium and phosphorus' (EFSA-assessed)",
                    'url': 'https://eur-lex.europa.eu/eli/reg/2012/432/oj'}],
  'confidence': 'high',
  'surfaces': ['dialog', 'table'],
  'verified': 'verified online (WebSearch excerpt of the cited page; the page itself could not be fetched from the sandbox) (the '
              'ODS vitamin D page introduction contains the sentence; the EU claim wording and link are from knowledge)'},
 {'id': 'medicine-spacing',
  'kind': 'note',
  'rel': 'note',
  'rank': 9,
  'applies_to': 'pill',
  'subjects': ['calcium', 'iron', 'magnesium', 'zinc'],
  'partners': ['drug:thyroid_hormone', 'drug:antibiotics', 'drug:bisphosphonates'],
  'needs_partner': False,
  'condition_key': 'kept_mineral_pill',
  'condition': 'A calcium, iron, magnesium or zinc pill is kept; shown once in the day, never per card; no hours',
  'title': 'Pills + medicines',
  'icon': "\U0001FA7A",
  'takeaway': 'On regular medicine? Ask your pharmacist about timing',
  'short': None,
  'why': 'Calcium, iron, magnesium and zinc pills can bind some medicines (thyroid hormone, certain antibiotics, osteoporosis '
         'tablets) and lower how much is absorbed.',
  'caveats': 'Never skip or move a prescribed medicine on your own; your pharmacist or doctor knows the right gap.',
  'source': {'name': 'NIH ODS: Calcium fact sheet for health professionals',
             'url': 'https://ods.od.nih.gov/factsheets/Calcium-HealthProfessional/'},
  'also_sources': [{'name': 'NIH ODS: Magnesium fact sheet for health professionals',
                    'url': 'https://ods.od.nih.gov/factsheets/Magnesium-HealthProfessional/'},
                   {'name': 'NIH ODS: Zinc fact sheet for health professionals',
                    'url': 'https://ods.od.nih.gov/factsheets/Zinc-HealthProfessional/'},
                   {'name': 'NIH ODS: Iron fact sheet for health professionals',
                    'url': 'https://ods.od.nih.gov/factsheets/Iron-HealthProfessional/'}],
  'confidence': 'high',
  'surfaces': ['day', 'dialog', 'table'],
  'verified': 'verified online (WebSearch excerpt of the cited page; the page itself could not be fetched from the sandbox) '
              '(calcium and levothyroxine; magnesium and bisphosphonates / antibiotics; zinc and antibiotics; iron and '
              'levothyroxine). The hours seen in the sources are deliberately not shown'})

RULES: tuple[dict[str, Any], ...] = _RULES
_BY_ID: dict[str, dict[str, Any]] = {r["id"]: r for r in RULES}
_ORDER: dict[str, int] = {r["id"]: i for i, r in enumerate(RULES)}
_REL_RANK = {"with": 0, "apart": 1, "note": 2}


def rule(rule_id: Any) -> dict[str, Any] | None:
    """A copy of one rule of the table (None for an unknown id), so a caller can never change the table."""
    found = _BY_ID.get(str(rule_id or ""))
    return None if found is None else copy.deepcopy(found)


# --- Constants --------------------------------------------------------------------------------------------------------------------
# The first two are numbers the visitor can read (each is in a rule's sourced wording); the others only decide WHICH meal or food a
# tip is attached to and are never shown (unsourced on purpose: they cannot become a claim).
CALCIUM_PORTION_MG = 500  # NIH ODS: calcium is absorbed best in portions of 500 mg or less
IRON_PILL_WITH_ZINC_MG = 25  # NIH ODS: iron pills of 25 mg or more taken with zinc can lower zinc absorption
_VITC_MIN_MG = 20.0  # a portion counts as a "vitamin C food" from this much (about a quarter of the EU NRV; internal)
_DEFAULT_PORTION_G = 100.0  # a food without an amount weighs like this when the meals are balanced

# The day is always breakfast, lunch and dinner (the 1-3 meals choice only sets how many recipes the plan writer is asked for).
_SLOTS = ((1, "breakfast", "Breakfast"), (2, "lunch", "Lunch"), (3, "dinner", "Dinner"))
_SLOT_NUMBERS = (1, 2, 3)
_PREF = (2, 3, 1)  # the meal a tie goes to first: lunch is the main meal, then dinner, then breakfast
# The three slots of a day as the n meals of the meal plan: (label of meal m, slots that make meal m). With 2 meals the breakfast
# foods join lunch (the plan has no breakfast); with 1 meal everything is one meal.
_MEALS = {
    3: (("Meal 1 (breakfast)", (1,)), ("Meal 2 (lunch)", (2,)), ("Meal 3 (dinner)", (3,))),
    2: (("Meal 1 (lunch)", (1, 2)), ("Meal 2 (dinner)", (3,))),
    1: (("Meal 1", (1, 2, 3)),),
}

# Copy that is not a rule: the plain advice for the one place where a rule cannot be placed by itself. No digits, and nothing a
# dietary profile excludes (tests/test_combine_rules.py reads blockbrain/data/dietary_profiles.json), so no nuts: the visitor may
# have filtered them out.
_HINTS = {
    "add_fat": "No fat in this meal yet? A little oil or avocado will do.",
}
# What the meal-plan writer is asked to add where a swapped food has no partner in the plan. {foods}: the food names, joined with
# " and ". Fixed text: the only words besides food names and meal numbers that reach the prompt.
_DIRECTIVE_TEXT = {
    "add_vitc": "serve the {foods} with a vitamin C food (for example peppers, citrus fruit, kiwi, berries or a squeeze of lemon)",
    "add_fat": "cook or dress the {foods} with a little oil (for example olive or rapeseed oil)",
}
_DIRECTIVE_RULE = {"add_vitc": "iron-vitc", "add_fat": "fat-carotenoids"}  # the rule behind each directive

# The conditions _holds() evaluates; a rule with any other token is never shown (a typo cannot become a claim).
_COND_TOKENS = frozenset({
    "dose_mg_gt_500", "form_carbonate", "iron_pill_mg_gte_25", "iron_and_calcium_pills", "non_heme_iron",
    "food_is_carotenoid_vegetable", "kept_mineral_pill",
})
# The trait a FOOD needs for the rules that are about plant (non-heme) iron / carotenoid vegetables only.
_FOOD_TRAIT = {"iron-vitc": "plant_iron", "iron-tea-coffee": "plant_iron", "fat-carotenoids": "carotenoid"}
# Pills that go into the meal with the most fat (nutrient key: rule id), and the rules whose tips are ONE tip when they share a meal.
_FAT_PILL_RULE = {
    s: r["id"] for r in RULES if r["kind"] == "with_fat" and r["applies_to"] == "pill" for s in r["subjects"]
}
_FAT_RULE_IDS = frozenset(_FAT_PILL_RULE.values())
_APART_PILL_RULES = frozenset(r["id"] for r in RULES if r["kind"] in ("separate", "split_dose"))  # a schedule only if the nutrients are separate pills
_MINERAL_KEYS = frozenset(_BY_ID["medicine-spacing"]["subjects"])  # the pills the pharmacist line is about
_KNOWN_KEYS = frozenset(k for r in RULES for k in (*r["subjects"], *r["partners"]) if not k.startswith(("food:", "drug:")))


# --- Reading the input (every reader is total) ------------------------------------------------------------------------------------
def _txt(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if out == out and abs(out) != float("inf") else None  # not NaN, not infinite


def _call(hooks: Any, name: str, default: Any, *args: Any) -> Any:
    """The app's own function `name` (title, grams, ...) when it gave one; a failing hook is the default, never an error."""
    fn = hooks.get(name) if isinstance(hooks, Mapping) else None
    if fn is None:
        return default
    try:
        value = fn(*args)
    except Exception:
        return default
    return default if value is None else value


def _canon(name: Any, hooks: Any = None) -> str:
    """The canonical nutrient key of a name ("Vitamin D3" -> "vitamin d"), "" when the lexicon does not know it."""
    text = _txt(name).lower()
    if not text:
        return ""
    if text in _KNOWN_KEYS:
        return text
    fn = hooks.get("canon") if isinstance(hooks, Mapping) else None
    if fn is not None:
        try:
            return _txt(fn(text)).lower()
        except Exception:
            pass  # a failing hook counts as absent
    try:  # the app's lexicon, imported late (this module loads first, and the tests can run it alone)
        from blockbrain.app import canonical_nutrient_key

        return _txt(canonical_nutrient_key(text)).lower()
    except Exception:
        return ""


def _to_mg(value: Any, unit: Any) -> float | None:
    amount = _num(value)
    if amount is None:
        return None
    u = _txt(unit).lower().replace("µ", "mc").replace("μ", "mc")  # micro sign / Greek mu: "µg" is "mcg"
    factor = {"mg": 1.0, "g": 1000.0, "mcg": 0.001, "ug": 0.001, "kg": 1e6}.get(u)
    return None if factor is None else amount * factor


def _dose_mg(d: Mapping[str, Any]) -> float | None:
    """The pill's daily dose in mg (the upper end of a label range); None for IU / an unknown unit / no dose."""
    low = _to_mg(d.get("dose_value"), d.get("dose_unit"))
    high = _to_mg(d.get("dose_max"), d.get("dose_unit"))
    if low is None:
        return high
    return max(low, high) if high is not None else low


def _plain_dose(d: Mapping[str, Any]) -> str:
    label = _txt(d.get("dose_label"))
    return "" if label.lower().startswith("dose not") else label  # the app's "Dose not found" is no dose


class _Pill(NamedTuple):
    uid: str
    order: int
    key: str  # canonical nutrient key, "" when the lexicon does not know the name
    title: str
    dose_text: str
    dose_mg: float | None
    salt: str  # lower-case "component form": "calcium calcium carbonate"
    over_limit: bool


def _pills(keep_items: Any, hooks: Any) -> list[_Pill]:
    """One entry per kept nutrient, in card order ("Vitamin D" and "Vitamin D3" are one pill: a pill is never placed twice); entries
    without a name are dropped."""
    out: list[_Pill] = []
    seen: set[str] = set()
    for order, d in enumerate(keep_items if isinstance(keep_items, (list, tuple)) else []):
        if not isinstance(d, Mapping):
            continue
        key = _canon(d.get("component_key"), hooks) or _canon(d.get("component"), hooks)
        title = _txt(_call(hooks, "title", "", d)) or _txt(d.get("component")) or key.title()
        ident = key or title.lower()
        if not ident or ident in seen:
            continue
        seen.add(ident)
        out.append(_Pill(
            uid=f"pill:{ident}", order=order, key=key, title=title, dose_text=_txt(_call(hooks, "dose_text", _plain_dose(d), d)),
            dose_mg=_dose_mg(d), salt=f"{_txt(d.get('component') or d.get('component_key'))} {_txt(d.get('form'))}".lower(),
            over_limit=bool(_call(hooks, "over_limit", False, d)),
        ))
    return out


# --- What a swapped food is (internal, never shown) -------------------------------------------------------------------------------
# USDA's category first, then the name; every pattern is lower-case. These only decide WHICH meal a tip sits in.
_ANIMAL_CATEGORY = (
    "beef", "lamb, veal", "poultry", "pork", "finfish", "shellfish", "sausage", "dairy", "egg", "game", "fish", "meat",
)
_PLANT_CATEGORY = ("vegetable", "fruit", "legume", "cereal", "grain", "nut and seed", "spice", "beverage", "fortified")
_ANIMAL_NAME_RE = re.compile(
    r"\b(?:beef|pork|lamb|veal|chicken|turkey|duck|goose|venison|bison|liver|fish|salmon|sardines?|mackerel|herring|trout|tuna|cod|"
    r"haddock|pollock|anchov\w*|oysters?|clams?|mussels?|shrimps?|prawns?|crab|lobster|scallops?|eggs?|milk|cheese|yogurt)\b"
)
_NOT_ANIMAL_RE = re.compile(r"\b(?:beans?|oil|sauce|lentils?|peas?|nuts?)\b")  # "kidney beans", "fish oil" are no meat
_CAROTENOID_RE = re.compile(  # orange and dark-green vegetables, leafy greens, tomatoes and peppers (not "green peas", not pumpkin SEEDS)
    r"\b(?:carrots?|sweet potato(?:es)?|pumpkin|winter squash|squash, winter|butternut|hubbard|spinach|kale|collards?|chard|tomato(?:es)?|"
    r"peppers?|broccoli|romaine|lettuce|parsley|cress|dandelion|arugula|rocket|chicory|endive|cilantro|coriander|pak-choi|greens)\b"
)
_BREAKFAST_CATEGORY = ("fruit", "nut and seed", "dairy and egg", "cereal", "grain")  # a savoury food (legume, vegetable, meat, fish) is no breakfast
_FAT_CATEGORY = ("fats and oils", "nut and seed")
_FAT_NAME_RE = re.compile(
    r"\b(?:oils?|nuts?|almonds?|cashews?|walnuts?|hazelnuts?|filberts?|pecans?|pistachios?|peanuts?|macadamias?|seeds?|flax\w*|"
    r"linseeds?|hemp|chia|tahini|avocados?|olives?|salmon|mackerel|sardines?|herring|trout|anchov\w*|eggs?|cheese|butter|coconuts?)\b"
)
_SEEDS_OF_BEANS_RE = re.compile(r"\b(?:im)?mature seeds?\b")  # "Beans, white, mature seeds, raw" is no seed food
_NOT_FAT_RE = re.compile(r"\b(?:chestnuts?|egg,? whites?|coconut water|sprouted)\b")  # sprouted seeds are a vegetable
_VITC_FRUIT_RE = re.compile(
    r"\b(?:oranges?|lemons?|limes?|grapefruits?|clementines?|tangerines?|mandarins?|kiwi\w*|guavas?|acerola|strawberr\w*|papayas?|"
    r"currants?|rose hips?|citrus|sea buckthorn)\b"
)
_VITC_VEG_RE = re.compile(r"\b(?:peppers?|broccoli|brussels sprouts|cauliflower|kale)\b")


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


def _is_fat_food(desc: str, cat: str) -> bool:
    if _NOT_FAT_RE.search(desc):
        return False
    return _has(cat, _FAT_CATEGORY) or bool(_FAT_NAME_RE.search(_SEEDS_OF_BEANS_RE.sub(" ", desc)))


def _food_traits(desc: str, category: str, nutrients: set[str], grams: float | None, vitc_per_100: float | None) -> frozenset[str]:
    """What a chosen food is, for the layout: plant_iron, carotenoid, fat, vitc. plant_iron and carotenoid also need the food to have
    been chosen FOR iron / beta carotene or vitamin A (a spinach chosen for vitamin K is not asked to meet vitamin C)."""
    name, cat = desc.lower(), category.lower()
    animal = _has(cat, _ANIMAL_CATEGORY) or (
        not _has(cat, _PLANT_CATEGORY) and bool(_ANIMAL_NAME_RE.search(name)) and not _NOT_ANIMAL_RE.search(name)
    )
    plant = (_has(cat, _PLANT_CATEGORY) or not cat) and not animal
    portion = (grams if grams and grams > 0 else _DEFAULT_PORTION_G) / 100.0
    traits: set[str] = set()
    if "iron" in nutrients and plant:
        traits.add("plant_iron")
    if nutrients & {"beta carotene", "vitamin a"} and plant and (not cat or "vegetable" in cat) and _CAROTENOID_RE.search(name):
        traits.add("carotenoid")
    if _is_fat_food(name, cat):
        traits.add("fat")
    if vitc_per_100 is not None:
        if vitc_per_100 * portion >= _VITC_MIN_MG:
            traits.add("vitc")
    elif not animal and ((_VITC_FRUIT_RE.search(name) and (not cat or "fruit" in cat)) or (_VITC_VEG_RE.search(name) and (not cat or "vegetable" in cat))):
        traits.add("vitc")  # no USDA vitamin C figure for this food: its name says it is a citrus fruit, a pepper, a cabbage ...
    return frozenset(traits)


class _Food(NamedTuple):
    uid: str
    order: int
    label: str
    grams: float  # for balancing the meals (a food without an amount counts _DEFAULT_PORTION_G)
    nutrients: tuple  # canonical keys of the nutrients it was chosen for, sorted
    icon: str
    traits: frozenset
    vitc_mg: float  # vitamin C of the portion; 0.0 when the data has none
    breakfast: bool = False  # a fruit, nut / seed, dairy / egg or cereal: a food a breakfast bundle may be made of


def _foods(replace_items: Any, hooks: Any) -> list[_Food]:
    """One entry per distinct food, in card order (a food chosen for several nutrients is one entry, at its largest amount, as the
    plan rows do). A food the app's `skip_food` rejects (not advised in pregnancy) is not in the day at all."""
    groups: dict[str, dict[str, Any]] = {}
    for order, d in enumerate(replace_items if isinstance(replace_items, (list, tuple)) else []):
        food = d.get("selected_food") if isinstance(d, Mapping) else None
        if not isinstance(food, Mapping):
            continue
        desc = _txt(food.get("food_description"))
        if not desc or _call(hooks, "skip_food", False, d):
            continue
        label = _txt(_call(hooks, "food_label", "", food)) or desc.split(",")[0].strip() or desc
        gkey = re.sub(r"[^a-z0-9]+", " ", label.lower()).strip() or desc.lower()
        key = _canon(d.get("component_key"), hooks) or _canon(d.get("component"), hooks)
        grams = _num(_call(hooks, "grams", None, d))
        grams = grams if grams is not None and grams > 0 else None
        g = groups.get(gkey)
        if g is None:
            g = groups[gkey] = {"order": order, "label": label, "food": food, "grams": grams, "nutrients": set()}
        elif grams is not None and (g["grams"] is None or grams > g["grams"]):
            g.update(food=food, grams=grams)
        if key:
            g["nutrients"].add(key)
    out: list[_Food] = []
    for gkey, g in groups.items():
        out.append(_make_food(f"food:{gkey}", g["order"], g["label"], g["food"], g["grams"], g["nutrients"], hooks))
    out.sort(key=lambda f: (f.order, f.uid))
    return out


def _make_food(uid: str, order: int, label: str, food: Mapping[str, Any], grams: float | None, nutrients: set[str], hooks: Any) -> _Food:
    desc = _txt(food.get("food_description"))
    vitc100 = _num(_call(hooks, "fact", None, desc, "vitamin c"))
    portion = (grams or _DEFAULT_PORTION_G) / 100.0
    return _Food(
        uid=uid, order=order, label=label, grams=grams or _DEFAULT_PORTION_G, nutrients=tuple(sorted(nutrients)),
        icon=_txt(_call(hooks, "food_icon", "", food)) or "\U0001F957",
        traits=_food_traits(desc, _txt(food.get("food_category")), nutrients, grams, vitc100),
        vitc_mg=(vitc100 * portion) if vitc100 is not None else 0.0,
        breakfast=_has(_txt(food.get("food_category")).lower(), _BREAKFAST_CATEGORY),
    )


def _n(n_meals: Any) -> int:
    try:
        n = int(n_meals)
    except (TypeError, ValueError, OverflowError):
        n = 3
    return max(1, min(3, n))


# --- Chips and tips (every sentence is a rule's own) ------------------------------------------------------------------------------
def _pill_item(p: _Pill, half: bool = False) -> dict[str, Any]:
    if half:
        text = f"{p.title}, half of {p.dose_text or 'your dose'}"
    else:
        text = f"{p.title} {p.dose_text}".strip()
    return {"uid": p.uid, "kind": "pill", "key": p.key, "label": p.title, "text": text, "icon": "\U0001F48A"}


def _food_item(f: _Food) -> dict[str, Any]:
    return {"uid": f.uid, "kind": "food", "key": f.nutrients[0] if f.nutrients else "", "label": f.label, "text": f.label, "icon": f.icon}


def _unique(items: list[Any]) -> list[Any]:
    out: list[Any] = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out


def _tip(rule_ids: list[str], uids: list[str], extra: str = "") -> dict[str, Any]:
    """One tip of the day. Several rules in one tip (the fat tips of several pills in one meal): the first rule by table order is the
    tip, the others are named in `also`, and each rule's why / caveat / primary source is kept."""
    rules = [_BY_ID[i] for i in rule_ids]
    first = rules[0]
    return {
        "rule_id": first["id"], "also": [r["id"] for r in rules[1:]], "rel": first["rel"], "rank": first["rank"], "text": first["takeaway"],
        "why": " ".join(_unique([r["why"] for r in rules])), "caveats": _unique([r["caveats"] for r in rules]),
        "sources": _unique([dict(r["source"]) for r in rules]), "extra": extra, "items": list(uids),
    }


def _merge_notes(notes: list[dict[str, Any]], order: list[str]) -> list[dict[str, Any]]:
    """The tips of one meal from its notes ({rule, uids, extra}): one tip per rule, and ONE for all the fat rules together; most
    useful first (rank, then table order). The items of a tip are listed in the order of the meal's own items (`order`), then the
    ones that are in another meal (the iron next to the calcium tip)."""
    merged: dict[str, dict[str, Any]] = {}
    for n in sorted(notes, key=lambda n: _ORDER[n["rule"]]):
        family = "fat" if n["rule"] in _FAT_RULE_IDS else n["rule"]
        cur = merged.get(family)
        if cur is None:
            merged[family] = {"rules": [n["rule"]], "uids": list(n["uids"]), "extra": n["extra"]}
            continue
        if n["rule"] not in cur["rules"]:
            cur["rules"].append(n["rule"])
        cur["uids"] += [u for u in n["uids"] if u not in cur["uids"]]
        cur["extra"] = cur["extra"] or n["extra"]
    rank = {u: i for i, u in enumerate(order)}
    tips = [_tip(m["rules"], sorted(m["uids"], key=lambda u: rank.get(u, len(rank))), m["extra"]) for m in merged.values()]
    return sorted(tips, key=lambda t: (t["rank"], _ORDER[t["rule_id"]]))


# --- Step 1: foods into meals -----------------------------------------------------------------------------------------------------
def _place_foods(foods: list[_Food]) -> tuple[dict[str, int], list[dict[str, Any]], bool]:
    """(meal of each placed food uid, directives, needs_layout). Only the foods that must be somewhere in particular are placed: foods
    that want each other (a plant-iron food and a vitamin C food, a carotenoid vegetable and a fat food) are one bundle, and a food whose
    partner nobody chose becomes a directive for the meal-plan writer (add a vitamin C food / some oil) and is placed to anchor it.
    Every other food is left out: it can go in any meal, the plan writer is not told where (a layout that placed every food by grams
    alone gave lopsided, odd meals). Bundles go heaviest first into the meal with the fewest foods (then the least grams, then the
    main meal first); a bundle with a savoury food (a legume, a vegetable, meat, fish) is never a breakfast. This step never looks at
    a pill, so the foods' meals are the same for every set of kept pills. When no food needs a partner and none got one, nothing
    needs a layout: needs_layout is False, no food is placed and the meal-plan prompt stays what it always was."""
    parent = {f.uid: f.uid for f in foods}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)  # the smaller uid is the root: the same bundles whatever the order of the unions

    def best(trait: str, amount: Any, other: _Food) -> _Food | None:
        cands = [f for f in foods if trait in f.traits and f.uid != other.uid]
        return max(cands, key=lambda f: (amount(f), -f.order)) if cands else None

    wants: list[tuple[str, _Food]] = []  # (directive kind, the food that wants a partner nobody chose)
    for f in foods:
        if "plant_iron" in f.traits and "vitc" not in f.traits:
            partner = best("vitc", lambda x: x.vitc_mg, f)  # the vitamin C food with the most vitamin C
            if partner is not None:
                union(f.uid, partner.uid)
            else:
                wants.append(("add_vitc", f))
        if "carotenoid" in f.traits and "fat" not in f.traits:
            partner = best("fat", lambda x: x.grams, f)  # no fat data: the fat food with the biggest portion
            if partner is not None:
                union(f.uid, partner.uid)
            else:
                wants.append(("add_fat", f))
    bundles: dict[str, list[_Food]] = {}
    for f in foods:
        bundles.setdefault(find(f.uid), []).append(f)
    anchored = {f.uid for _, f in wants}  # a lone food with a directive is placed too: the directive needs a meal to stand under
    must = {root: m for root, m in bundles.items() if len(m) > 1 or m[0].uid in anchored}
    count = {s: 0 for s in _SLOT_NUMBERS}
    grams = {s: 0.0 for s in _SLOT_NUMBERS}
    slot_of: dict[str, int] = {}
    for root, members in sorted(must.items(), key=lambda kv: (-sum(m.grams for m in kv[1]), min(m.order for m in kv[1]), kv[0])):
        open_slots = _SLOT_NUMBERS if all(m.breakfast for m in members) else (2, 3)
        slot = min(open_slots, key=lambda s: (count[s], grams[s], _PREF.index(s)))
        count[slot] += len(members)
        grams[slot] += sum(m.grams for m in members)
        for m in members:
            slot_of[m.uid] = slot
    directives: list[dict[str, Any]] = []
    for kind in ("add_vitc", "add_fat"):
        by_slot: dict[int, list[str]] = {}
        for k, f in wants:
            if k == kind:
                by_slot.setdefault(slot_of[f.uid], []).append(f.label)
        for slot in sorted(by_slot):
            directives.append({"kind": kind, "slot": slot, "foods": sorted(by_slot[slot], key=str.lower), "rule_id": _DIRECTIVE_RULE[kind]})
    return slot_of, directives, bool(slot_of)


# --- The day ----------------------------------------------------------------------------------------------------------------------
def _empty_day() -> dict[str, Any]:
    return {
        "version": 1,
        "slots": [{"slot": s, "key": key, "label": label, "items": [], "tips": []} for s, key, label in _SLOTS],
        "anytime": [], "held": [], "free_foods": [], "directives": [], "needs_layout": False, "medicine": None, "empty": True,
        "has_items": False, "split_pills": False,
    }


def plan_day(keep_items: Any, replace_items: Any, hooks: Any = None) -> dict[str, Any]:
    """The day: which swapped food and which kept pill goes into breakfast, lunch and dinner, and the tips that go with it.

    keep_items / replace_items: the app's decision dicts (component, component_key, dose_label, dose_value, dose_max, dose_unit, form,
    decision, selected_food = a USDA row, card_index). Always three slots. Returns
      {"version": 1,
       "slots": [{"slot": 1|2|3, "key": "breakfast|lunch|dinner", "label": "Breakfast|Lunch|Dinner", "items": [item], "tips": [tip]} x3],
       "anytime": [item]    kept pills no rule asks to put in a particular meal,
       "held": [item]       kept pills over their upper intake level: not scheduled, no tip,
       "free_foods": [item] swapped foods that can go in any meal (not placed: only a food that must share a meal with a partner, or that
                            needs one added, is placed),
       "directives": [{"kind": "add_vitc|add_fat", "slot": n, "foods": [labels], "rule_id": id}],
       "needs_layout": bool, "medicine": tip | None, "empty": bool (no meal has a tip), "has_items": bool (any pill or food),
       "split_pills": bool (the day keeps pills apart: they may come in one tablet, see ONE_TABLET_NOTE)}
    item = {"uid", "kind": "pill|food", "key", "label", "text" (the chip: "Iron 14 mg", "Calcium, half of 1000 mg", a food's name),
    "icon"}. tip = {"rule_id", "also": [rule ids], "rel": "with|apart|note", "rank", "text" (the rule's takeaway), "why", "caveats":
    [str], "sources": [{"name", "url"}], "extra": "" | a fixed hint, "items": [uid]}. Total: an internal error gives the empty day."""
    try:
        return _plan_day(keep_items, replace_items, hooks)
    except Exception:  # pragma: no cover - the property tests call _plan_day itself, so a bug cannot hide behind this
        logger.debug("combine.plan_day failed", exc_info=True)
        return _empty_day()


def _plan_day(keep_items: Any, replace_items: Any, hooks: Any) -> dict[str, Any]:
    """Constraints, strongest first: a pill is placed once (a split calcium dose is two portions of ONE pill); iron is kept apart from
    a calcium pill and from a zinc pill (when the iron is 25 mg or more); calcium above 500 mg is split over two meals; iron goes to
    the meal with a vitamin C food, else next to a vitamin C pill; fat-soluble pills share the meal with the most fat foods. The steps
    run in that order and a step never moves a pill an earlier step placed, so a constraint that can be met stays met."""
    pills = _pills(keep_items, hooks)
    foods = _foods(replace_items, hooks)
    slot_of, directives, needs_layout = _place_foods(foods)
    slot_foods: dict[int, list[_Food]] = {s: [] for s in _SLOT_NUMBERS}
    for f in foods:
        if f.uid in slot_of:  # only the foods that must share a meal are placed; the rest can go in any meal
            slot_foods[slot_of[f.uid]].append(f)
    held = [p for p in pills if p.over_limit]  # over the upper level: the card's own warning is the message, no tip
    live = [p for p in pills if not p.over_limit]
    by_key = {p.key: p for p in live if p.key}
    chips: dict[int, list[tuple[int, dict[str, Any]]]] = {s: [] for s in _SLOT_NUMBERS}  # (card order, chip) of the pills in a meal
    notes: dict[int, list[dict[str, Any]]] = {s: [] for s in _SLOT_NUMBERS}
    placed: set[str] = set()

    def load(s: int) -> float:
        return sum(f.grams for f in slot_foods[s])

    def vitc(s: int) -> float:  # the vitamin C of the vitamin C foods of a meal (0.0: the meal has none)
        here = [f for f in slot_foods[s] if "vitc" in f.traits]
        return max(sum(f.vitc_mg for f in here), _VITC_MIN_MG) if here else 0.0

    def put(p: _Pill, s: int, half: bool = False) -> None:
        chips[s].append((p.order, _pill_item(p, half)))
        placed.add(p.uid)

    def note(s: int, rule_id: str, uids: list[str], extra: str = "") -> None:
        notes[s].append({"rule": rule_id, "uids": uids, "extra": extra})

    iron, calcium, zinc, vit_c = by_key.get("iron"), by_key.get("calcium"), by_key.get("zinc"), by_key.get("vitamin c")
    iron_slot: int | None = None

    # Iron first: its meal is the one with the most vitamin C (a vitamin C food, else a vitamin C pill moves in with it).
    if iron is not None:
        iron_slot = max(_SLOT_NUMBERS, key=lambda s: (vitc(s), load(s), -_PREF.index(s)))
        put(iron, iron_slot)
        partner: list[str] = []
        here = [f for f in slot_foods[iron_slot] if "vitc" in f.traits]
        if here:
            partner = [max(here, key=lambda f: (f.vitc_mg, -f.order)).uid]
        elif vit_c is not None:
            put(vit_c, iron_slot)
            partner = [vit_c.uid]
        note(iron_slot, "iron-vitc", [iron.uid, *partner])
        note(iron_slot, "iron-tea-coffee", [iron.uid])

    # A vitamin C pill that no iron pill needed goes to the meal of a plant-iron food that has no vitamin C food.
    if vit_c is not None and vit_c.uid not in placed:
        needy = [
            s for s in _SLOT_NUMBERS if vitc(s) == 0 and any("plant_iron" in f.traits and "vitc" not in f.traits for f in slot_foods[s])
        ]
        if needy:
            s = max(needy, key=lambda s: (sum(f.grams for f in slot_foods[s] if "plant_iron" in f.traits), -_PREF.index(s)))
            put(vit_c, s)
            note(s, "iron-vitc", [f.uid for f in slot_foods[s] if "plant_iron" in f.traits and "vitc" not in f.traits] + [vit_c.uid])

    # Calcium: above 500 mg in two meals, a carbonate with a meal, and away from an iron pill. With no rule it has no meal.
    if calcium is not None:
        free = [s for s in _SLOT_NUMBERS if s != iron_slot]
        ranked = sorted(free, key=lambda s: (-load(s), _PREF.index(s)))
        cal_slots: list[int] = []
        if calcium.dose_mg is not None and calcium.dose_mg > CALCIUM_PORTION_MG and len(free) >= 2:
            cal_slots = sorted(ranked[:2])
            for s in cal_slots:
                put(calcium, s, half=True)
            note(cal_slots[0], "calcium-split-500", [calcium.uid])
        elif "carbonat" in calcium.salt:
            cal_slots = ranked[:1]
            put(calcium, cal_slots[0])
            note(cal_slots[0], "calcium-carbonate-meal", [calcium.uid])
        elif iron is not None and ranked:
            cal_slots = ranked[:1]
            put(calcium, cal_slots[0])
        if iron is not None and cal_slots:
            note(cal_slots[0], "iron-calcium-pill", [calcium.uid, iron.uid])

    # Zinc is kept apart from an iron pill of 25 mg or more; below that no rule asks anything of it.
    if zinc is not None and iron is not None and iron.dose_mg is not None and iron.dose_mg >= IRON_PILL_WITH_ZINC_MG:
        free = [s for s in _SLOT_NUMBERS if s != iron_slot]
        if free:
            s = min(free, key=lambda s: (load(s), _PREF.index(s)))
            put(zinc, s)
            note(s, "zinc-iron-pills", [zinc.uid, iron.uid])

    # Fat-soluble pills all go to ONE meal, the one with the most fat foods (the main meal when none has any).
    fat_pills = [p for p in live if p.key in _FAT_PILL_RULE]
    if fat_pills:
        fat_slot = max(_SLOT_NUMBERS, key=lambda s: (sum("fat" in f.traits for f in slot_foods[s]), load(s), -_PREF.index(s)))
        any_fat_food = any("fat" in f.traits for f in foods)  # placed or not: then the hint "add some fat" is not needed
        for p in fat_pills:
            put(p, fat_slot)
            note(fat_slot, _FAT_PILL_RULE[p.key], [p.uid], extra="" if any_fat_food else _HINTS["add_fat"])

    # The tips that come from the foods of a meal (plant iron next to vitamin C, a carotenoid vegetable next to fat).
    for s in _SLOT_NUMBERS:
        here = slot_foods[s]
        here_iron = [f for f in here if "plant_iron" in f.traits]
        needs_c = [f for f in here_iron if "vitc" not in f.traits]
        vitc_foods = [f for f in here if "vitc" in f.traits]
        if needs_c and (vitc_foods or any(d["kind"] == "add_vitc" and d["slot"] == s for d in directives)):
            partner_c = [max(vitc_foods, key=lambda f: (f.vitc_mg, -f.order)).uid] if vitc_foods else []
            note(s, "iron-vitc", [f.uid for f in needs_c] + partner_c)
        if here_iron:
            note(s, "iron-tea-coffee", [f.uid for f in here_iron])
        needs_fat = [f for f in here if "carotenoid" in f.traits and "fat" not in f.traits]
        fat_foods = [f for f in here if "fat" in f.traits]
        if needs_fat and (fat_foods or any(d["kind"] == "add_fat" and d["slot"] == s for d in directives)):
            partner_f = [max(fat_foods, key=lambda f: (f.grams, -f.order)).uid] if fat_foods else []
            note(s, "fat-carotenoids", [f.uid for f in needs_fat] + partner_f)

    mineral = [p for p in pills if p.key in _MINERAL_KEYS]  # a held one counts: a medicine binds whatever the dose
    medicine = _tip(["medicine-spacing"], [p.uid for p in mineral]) if mineral else None
    day = _empty_day()
    for s in _SLOT_NUMBERS:
        slot = day["slots"][s - 1]
        slot["items"] = [_food_item(f) for f in slot_foods[s]] + [chip for _, chip in sorted(chips[s], key=lambda t: t[0])]
        slot["tips"] = _merge_notes(notes[s], [i["uid"] for i in slot["items"]])
    day["anytime"] = [_pill_item(p) for p in live if p.uid not in placed]
    day["held"] = [_pill_item(p) for p in held]
    day["free_foods"] = [_food_item(f) for f in foods if f.uid not in slot_of]
    day["directives"] = directives if needs_layout else []
    day["needs_layout"] = needs_layout
    day["medicine"] = medicine
    day["empty"] = not any(slot["tips"] for slot in day["slots"])
    day["has_items"] = bool(pills or foods)
    day["split_pills"] = any(t["rule_id"] in _APART_PILL_RULES for slot in day["slots"] for t in slot["tips"])
    return day


# --- Lines for the meal-plan prompt and for plain text ----------------------------------------------------------------------------
def meal_layout(replace_items: Any, n_meals: Any = 3, hooks: Any = None) -> list[str]:
    """The meal layout for the meal-plan prompt: only the foods that must share a meal (a plant-iron food and its vitamin C food, a
    carotenoid vegetable and its fat) or that need something added; every other food is left out, it can go in any meal. One line per
    meal that has such a food and a "  - ..." line under it where the plan writer has to add something:
      Meal 2 (lunch): Red lentils (dry) + Guavas
        - serve the ... with a vitamin C food (for example peppers, citrus fruit, kiwi, berries or a squeeze of lemon)
    n_meals (1-3, anything else is 3) decides how the day's three meals are written: 3 = breakfast / lunch / dinner, 2 = lunch (the
    breakfast foods join it) and dinner, 1 = one meal. [] when the foods need no layout (the prompt is then exactly what it was).
    Built from the swapped foods alone: only food names, meal numbers and the fixed texts of _DIRECTIVE_TEXT can reach the prompt, never
    a pill, a dose or a rule's text, and the lines are the same for every set of kept pills. Total: [] when anything is unreadable."""
    try:
        foods = _foods(replace_items, hooks)
        slot_of, directives, needs_layout = _place_foods(foods)
        if not needs_layout:
            return []
        out: list[str] = []
        for label, slots in _MEALS[_n(n_meals)]:
            names = [f.label for s in slots for f in foods if slot_of.get(f.uid) == s]
            if not names:
                continue
            out.append(f"{label}: " + " + ".join(names))  # " + ": a food's own name can hold a comma ("Salmon, sockeye")
            for kind in ("add_vitc", "add_fat"):
                asked = sorted({name for d in directives if d["kind"] == kind and d["slot"] in slots for name in d["foods"]}, key=str.lower)
                if asked:
                    out.append("  - " + _DIRECTIVE_TEXT[kind].format(foods=" and ".join(asked)))
        return out
    except Exception:  # pragma: no cover
        return []


def day_text(day: Any) -> list[str]:
    """The day as plain lines for the share text: "Breakfast: Calcium, half of 1000 mg" for each meal with something in it, then
    "Any meal: Magnesium 56 mg", then ONE_TABLET_NOTE when the day keeps pills apart (the nutrients may be in one tablet, and a copied
    schedule has no tips). No tips and no sources (the Meals tab has those). [] when no meal has anything in it."""
    try:
        lines = [
            f"{slot['label']}: " + ", ".join(i["text"] for i in slot["items"])
            for slot in day["slots"] if slot["items"] or slot["tips"]
        ]
        if lines and day.get("anytime"):
            lines.append("Any meal: " + ", ".join(i["text"] for i in day["anytime"]))
        if lines and day.get("split_pills"):
            lines.append(ONE_TABLET_NOTE)
        return lines
    except Exception:
        return []


# --- Tips of one nutrient (the swipe card, the dialog of a plan row) --------------------------------------------------------------
class _Ctx(NamedTuple):
    """What a condition is evaluated against: the pills that count, the swapped foods, and whether a mineral pill is kept."""
    mg: Mapping[str, float | None]  # canonical key -> dose in mg of the kept pills that count (not over their upper level)
    salt: Mapping[str, str]  # canonical key -> lower-case "component form"
    foods: tuple
    mineral: bool  # a calcium / iron / magnesium / zinc pill is kept (a held one counts)


_NO_PLAN = _Ctx({}, {}, (), False)


def _holds(token: Any, ctx: _Ctx) -> bool:
    """Does a rule's machine condition hold? A token this function does not know never holds (the rule is not shown)."""
    if token is None:
        return True
    if token not in _COND_TOKENS:
        return False
    if token == "dose_mg_gt_500":
        dose = ctx.mg.get("calcium")
        return dose is not None and dose > CALCIUM_PORTION_MG
    if token == "form_carbonate":
        return "calcium" in ctx.mg and "carbonat" in ctx.salt.get("calcium", "")
    if token == "iron_pill_mg_gte_25":
        dose = ctx.mg.get("iron")
        return "zinc" in ctx.mg and dose is not None and dose >= IRON_PILL_WITH_ZINC_MG
    if token == "iron_and_calcium_pills":
        return "iron" in ctx.mg and "calcium" in ctx.mg
    if token == "non_heme_iron":
        return "iron" in ctx.mg or any("plant_iron" in f.traits for f in ctx.foods)
    if token == "food_is_carotenoid_vegetable":
        return any("carotenoid" in f.traits for f in ctx.foods)
    return ctx.mineral  # kept_mineral_pill


def _partner_keys(r: Mapping[str, Any]) -> list[str]:
    return [k for k in r["partners"] if not k.startswith(("food:", "drug:"))]


def _pair_kept(r: Mapping[str, Any], ctx: _Ctx) -> bool:
    """A rule about a pair of pills (iron + calcium, zinc + a high-dose iron pill) needs both of them kept."""
    return all(k in ctx.mg for k in (*r["subjects"], *_partner_keys(r)))


def _fits(r: Mapping[str, Any], key: str, side: str, ctx: _Ctx, food: _Food | None) -> bool:
    """Does rule `r` apply to ONE item (nutrient `key`, as a pill or as a swapped food)? A pair rule applies to both of its pills."""
    pair = r["kind"] == "separate"
    if r["applies_to"] not in (side, "both") or not (key in r["subjects"] or (pair and key in _partner_keys(r))):
        return False
    if side == "food":
        trait = _FOOD_TRAIT.get(r["id"])
        if trait and (food is None or trait not in food.traits):
            return False
    return _holds(r["condition_key"], ctx) and (not pair or _pair_kept(r, ctx))


def _kept_map(kept: Any, hooks: Any) -> dict[str, float | None]:
    """The `kept` of item_tips as {canonical key: dose in mg or None}: a mapping as kept_pills() gives it, or any collection of keys."""
    if isinstance(kept, Mapping):
        pairs = list(kept.items())
    elif isinstance(kept, str):
        pairs = [(kept, None)]
    else:
        try:
            pairs = [(key, None) for key in kept]
        except TypeError:
            return {}
    out: dict[str, float | None] = {}
    for key, dose in pairs:
        canon = _canon(key, hooks)
        if canon:
            out[canon] = _num(dose)
    return out


def _item_rules(
    component_key: Any, side: Any, form: Any, dose_value: Any, dose_unit: Any, dose_max: Any, over_limit: Any, component: Any, food: Any,
    kept: Any, hooks: Any,
) -> list[dict[str, Any]]:
    """The rules that apply to ONE nutrient of the visitor's scan, in the order of the table."""
    if over_limit or side not in ("pill", "food"):
        return []  # a dose over the upper intake level gets no tip: the card's own warning is the message
    key = _canon(component_key, hooks) or _canon(component, hooks)
    if not key:
        return []
    mg = _kept_map(kept, hooks)
    salt = {k: "" for k in mg}
    item_food: _Food | None = None
    if side == "pill":
        mg[key] = _dose_mg({"dose_value": dose_value, "dose_max": dose_max, "dose_unit": dose_unit})
        salt[key] = f"{_txt(component or component_key)} {_txt(form)}".lower()
    elif isinstance(food, Mapping) and _txt(food.get("food_description")):
        item_food = _make_food("food:item", 0, "", food, None, {key}, hooks)
    ctx = _Ctx(mg, salt, (item_food,) if item_food else (), any(k in _MINERAL_KEYS for k in mg))
    return [r for r in RULES if _fits(r, key, side, ctx, item_food)]


def kept_pills(keep_items: Any, hooks: Any = None) -> dict[str, float | None]:
    """{canonical nutrient key: dose in mg or None} of the kept pills that count for the rules: not over their upper intake level and
    known to the lexicon. This is the `kept` that item_tips() wants (the dose lets it judge "an iron pill of 25 mg or more")."""
    try:
        return {p.key: p.dose_mg for p in _pills(keep_items, hooks) if p.key and not p.over_limit}
    except Exception:
        return {}


def item_tips(
    component_key: Any, side: Any = "pill", *, form: Any = "", dose_value: Any = None, dose_unit: Any = "", dose_max: Any = None,
    over_limit: Any = False, component: Any = "", food: Any = None, kept: Any = frozenset(), hooks: Any = None,
) -> list[dict[str, Any]]:
    """Every tip that applies to ONE nutrient of the visitor's scan, most useful first (rank, then table order), for the dialog of a
    plan row: [{"rule_id", "rel": "with|apart|note", "icon", "text" (the rule's takeaway), "short" (the card word or None), "why",
    "caveats": [str], "sources": [{"name", "url"}] (the primary source first, then the others), "rank"}].
    side: "pill" (a kept pill, with its form and dose) or "food" (the swapped food, a USDA row, for the tips that depend on it).
    kept: the kept pills as kept_pills() gives them ({key: mg}; a plain collection of keys also works), for the rules about a pair of
    pills (iron + calcium, zinc + a high-dose iron pill): they need the partner in `kept` and then show on both pills of the pair (a
    plain collection of keys cannot tell "an iron pill of 25 mg or more", so that one is not shown on the zinc pill). over_limit: the
    pill is over its upper intake level: no tip. Total: [] when anything is unreadable."""
    try:
        rules = _item_rules(component_key, side, form, dose_value, dose_unit, dose_max, over_limit, component, food, kept, hooks)
        return [
            {
                "rule_id": r["id"], "rel": r["rel"], "icon": r["icon"], "text": r["takeaway"], "short": r["short"], "why": r["why"],
                "caveats": [r["caveats"]] if r["caveats"] else [], "sources": [dict(r["source"]), *(dict(s) for s in r["also_sources"])],
                "rank": r["rank"],
            }
            for r in sorted((r for r in rules if "dialog" in r["surfaces"]), key=lambda r: (r["rank"], _ORDER[r["id"]]))
        ]
    except Exception:  # pragma: no cover
        return []


def card_tags(
    component_key: Any, form: Any = "", dose_value: Any = None, dose_unit: Any = "", *, dose_max: Any = None, over_limit: Any = False,
    component: Any = "", hooks: Any = None,
) -> list[dict[str, Any]]:
    """The "Pairs well with" tags of a swipe card: at most two {"rule_id", "icon", "text"} (the rule's `short`: "some fat", "a meal"),
    only from the rules marked for the card, only positive pairings ("with"), none for a pill over its upper intake level. [] for
    most nutrients (no alarm fatigue). Total: [] when anything is unreadable."""
    try:
        rules = _item_rules(component_key, "pill", form, dose_value, dose_unit, dose_max, over_limit, component, None, frozenset(), hooks)
        out: list[dict[str, Any]] = []
        for r in rules:
            if "card" in r["surfaces"] and r["rel"] == "with" and r["short"] and all(t["text"] != r["short"] for t in out):
                out.append({"rule_id": r["id"], "icon": r["icon"], "text": r["short"]})
        return out[:2]
    except Exception:  # pragma: no cover
        return []


# --- The table of all combinations ------------------------------------------------------------------------------------------------
def _plan_ctx(keep_items: Any, replace_items: Any, hooks: Any) -> _Ctx:
    pills = _pills(keep_items, hooks)
    live = [p for p in pills if p.key and not p.over_limit]
    return _Ctx(
        {p.key: p.dose_mg for p in live}, {p.key: p.salt for p in live}, tuple(_foods(replace_items, hooks)),
        any(p.key in _MINERAL_KEYS for p in pills),
    )


def _in_plan(r: Mapping[str, Any], ctx: _Ctx) -> bool:
    """Does the rule apply to something in the visitor's plan (a kept pill, or a swapped food)? The partner counts when the rule needs
    one (Vitamin D helps calcium only in a plan that has both)."""
    subjects = r["subjects"]
    pill_side = r["applies_to"] in ("pill", "both") and (any(k in ctx.mg for k in subjects) or (
        r["condition_key"] == "kept_mineral_pill" and ctx.mineral))
    trait = _FOOD_TRAIT.get(r["id"])
    food_side = r["applies_to"] in ("food", "both") and any(
        any(k in f.nutrients for k in subjects) and (trait is None or trait in f.traits) for f in ctx.foods
    )
    if not (pill_side or food_side) or not _holds(r["condition_key"], ctx):
        return False
    if r["kind"] == "separate":
        return _pair_kept(r, ctx)
    if r["needs_partner"]:
        return all(k in ctx.mg or any(k in f.nutrients for f in ctx.foods) for k in _partner_keys(r))
    return True


def table_rows(keep_items: Any = None, replace_items: Any = None, hooks: Any = None) -> list[dict[str, Any]]:
    """ALL rules for the "See all combinations" view, the ones that apply to the visitor's own plan first ("in_plan"), then with <
    apart < note, then rank, then table order: [{"id", "rel", "icon", "title", "text" (the takeaway), "why", "caveats": [str],
    "sources": [{"name", "url"}] (the primary source, then every other), "in_plan": bool, "confidence": "high|medium"}]. It is the
    same data the tips come from, so the two can never disagree. Nothing is hidden: with an unreadable plan every row is just not
    in_plan. Total: [] only when the table itself cannot be read."""
    try:
        try:
            ctx = _plan_ctx(keep_items, replace_items, hooks)
        except Exception:
            ctx = _NO_PLAN
        rows = []
        for r in RULES:
            in_plan = _in_plan(r, ctx)
            rows.append(((0 if in_plan else 1, _REL_RANK.get(r["rel"], 3), r["rank"], _ORDER[r["id"]]), {
                "id": r["id"], "rel": r["rel"], "icon": r["icon"], "title": r["title"], "text": r["takeaway"], "why": r["why"],
                "caveats": [r["caveats"]] if r["caveats"] else [], "sources": [dict(r["source"]), *(dict(s) for s in r["also_sources"])],
                "in_plan": in_plan, "confidence": r["confidence"],
            }))
        return [row for _, row in sorted(rows, key=lambda t: t[0])]
    except Exception:  # pragma: no cover
        return []
