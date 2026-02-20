import customtkinter as ctk
from tkinter import filedialog, messagebox
import tkinter as tk
from PIL import Image
import requests
import json
import re
import threading
import queue
import time
import webbrowser
from urllib.parse import quote_plus
import os
import base64
import mimetypes
import diskcache
import httpx
import asyncio
import hashlib




# ============================================================
# CACHING
# ============================================================

llm_cache = diskcache.Cache('llm_cache', size_limit=100*1024*1024)  # 100MB
usda_cache = diskcache.Cache('usda_cache', size_limit=200*1024*1024)  # 200MB


# ============================================================
# CONFIG (PASTE YOUR KEYS HERE)
# ============================================================

OPENROUTER_API_KEY = "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"  # <-- paste OpenRouter key here (starts with sk-or-...)
USDA_API_KEY = "lCeWwNYurDUsKFHtsPv5ydCmZ1gNatvp3Fs1byFm"        # <-- paste USDA key here

# OpenRouter Chat Completions endpoint
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "openai/gpt-4o-mini"  # same model via OpenRouter

APP_TITLE = "SuppSwap Pro++ – Supplement Replacer -> Replace Supplements with Whole Foods"
WATERMARK = "© mfitness92"

HTTP_TIMEOUT = 30
USDA_TIMEOUT = 15

# Hard caps to avoid token blowups
MAX_INPUT_CHARS = 18000
MAX_DEBUG_CHARS = 12000

# OpenRouter fallback token cap (fits low-credit accounts)
ECONOMY_MAX_TOKENS = 2500

# USDA query seeds for candidate generation (no LLM needed)
USDA_QUERY_SEEDS = [
    "fruit", "vegetable", "leafy", "root", "cruciferous",
    "nut", "seed", "legume", "bean", "grain",
    "fish", "meat", "poultry", "dairy", "mushroom"
]


def _affordable_max_tokens(error_text: str, buffer_tokens: int = 50) -> int | None:
    """Extract affordable max_tokens from OpenRouter 402 error message."""
    if not error_text:
        return None
    m = re.search(r"can only afford (\d+)", error_text)
    if not m:
        return None
    try:
        afford = int(m.group(1))
    except ValueError:
        return None
    return max(1, afford - buffer_tokens)


def get_openrouter_keys() -> list[str]:
    """Return a prioritized list of OpenRouter keys (env first, then fallback)."""
    env_keys = os.getenv("OPENROUTER_API_KEYS", "")
    keys = [k.strip() for k in env_keys.split(",") if k.strip()]
    if OPENROUTER_API_KEY and OPENROUTER_API_KEY.strip():
        primary = OPENROUTER_API_KEY.strip()
        if primary not in keys:
            keys.insert(0, primary)
    return keys


def openrouter_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost",
        "X-Title": "SuppSwap Pro++",
    }


# ============================================================
# UTILITIES (these are your "utility functions")
# ============================================================

def fix_common_ocr_errors(text: str) -> str:
    """
    Fix common OCR confusions on supplement labels.
    Especially: K2 -> KZ, µg -> ug, etc.
    """
    t = text or ""

    # normalize unicode micro sign variants
    t = t.replace("μg", "µg")

    # common OCR: 'K2' read as 'KZ' or 'Kz'
    t = re.sub(r"(?i)\bvitamin\s*kz\b", "vitamin k2", t)
    t = re.sub(r"(?i)\bvit\s*kz\b", "vitamin k2", t)
    t = re.sub(r"(?i)\bkz\b", "k2", t)

    # common nutrient misspellings
    t = re.sub(r"(?i)\bmagneisum\b", "magnesium", t)
    t = re.sub(r"(?i)\bmagensium\b", "magnesium", t)
    t = re.sub(r"(?i)\bmangnesium\b", "magnesium", t)

    # common OCR: microgram written as ug
    t = re.sub(r"(?i)\bug\b", "µg", t)

    # normalize spacing
    t = re.sub(r"[ \t]+", " ", t)
    return t


# ---------- Nutrient color mapping (design + nutrition-informed choices)
# Map common micronutrient keywords to readable hex colors. These are
# friendly, high-contrast choices intended for badges/headers.
NUTRIENT_COLOR_MAP = {
    "vitamin k": "#2f7a3e",   # darker green (leafy greens)
    "vitamin k2": "#2f7a3e",
    "vitamin c": "#d97706",   # orange (citrus, high contrast)
    "vitamin d": "#b78800",   # gold / sunlight (darker)
    "vitamin a": "#c17a00",   # amber
    "vitamin e": "#a16207",   # warm gold
    "iron": "#b91c1c",        # red (blood/iron)
    "calcium": "#1e88e5",     # blue
    "magnesium": "#0f766e",   # teal (darker)
    "zinc": "#374151",        # neutral gray (higher contrast)
    "potassium": "#6d28d9",   # purple
    "omega-3": "#0369a1",     # deep blue (fish/sea)
    "fiber": "#92400e",       # brown/earthy
    "vitamin b12": "#075985", # deep cyan
    "folate": "#15803d",      # green
}

# Short descriptions for micronutrients (used in nutrient header)
MICRONUTRIENT_INFO = {
    "vitamin k": "Supports blood clotting and bone health.",
    "vitamin k2": "Supports blood clotting and bone mineralization.",
    "vitamin c": "Antioxidant; supports immune function and collagen synthesis.",
    "vitamin d": "Regulates calcium metabolism and supports bone health.",
    "vitamin a": "Important for vision, immune function, and cell growth.",
    "vitamin e": "Antioxidant that protects cell membranes.",
    "iron": "Required for oxygen transport in hemoglobin.",
    "calcium": "Key mineral for bone structure and muscle function.",
    "magnesium": "Cofactor in many enzymatic reactions and muscle function.",
    "zinc": "Supports immune function and wound healing.",
    "potassium": "Essential electrolyte for nerve and muscle function.",
    "omega-3": "Anti-inflammatory fatty acids that support heart and brain health.",
    "fiber": "Supports digestive health and moderates blood sugar.",
    "vitamin b12": "Needed for red blood cell formation and nervous system health.",
    "folate": "Important for DNA synthesis and during pregnancy.",
}

def get_nutrient_color(nut_name: str) -> str:
    """Return a hex color for a nutrient name (best-effort match)."""
    if not nut_name:
        return "#9ca3af"  # fallback gray
    n = nut_name.lower()
    # exact keys
    for k in NUTRIENT_COLOR_MAP.keys():
        if k in n:
            return NUTRIENT_COLOR_MAP[k]
    # heuristics: vitamins -> orange/teal depending on letter
    if "vitamin c" in n or "ascorbic" in n:
        return NUTRIENT_COLOR_MAP.get("vitamin c")
    if n.startswith("vitamin"):
        # map B-group to blue-ish, others to amber
        if "b" in n:
            return "#0f78a8"
        return "#f59e0b"
    # fallback: use neutral blue-gray
    return "#9ca3af"


# ------------------
# Simple translations (extendable)
# ------------------
TRANSLATIONS = {
    "en": {
        "select_language": "Select display language:",
        "continue": "Continue",
        "next": "Next →",
        "back": "← Back",
        "upload_label": "Upload Label Image",
        "try_fetch": "Try to fetch URL locally (optional)",
        "debug_mode": "Debug mode",
        "analyze": "Analyze →",
        "starting_upload": "Starting upload...",
        "complete": "Complete!",
        "input_error": "Input Error",
        "paste_prompt": "Paste supplement facts text OR paste a product URL OR upload label image:",
        "analyzing": "Analyzing…",
        "starting": "Starting…",
        "progress_log": "Progress log:",
        "cancel": "Cancel",
        "back_to_input": "← Back to Input",
        "ocr_error_title": "OCR Error",
        "retry_prompt": "Retry?",
        "ocr_fallback_reduced": "[Reduced Quality] Using economy mode due to credit limits.",
        "ocr_fallback_local": "[Local OCR] Using on-device Tesseract (free).",
        "ocr_all_failed": "OCR FAILED: Install Tesseract or upgrade OpenRouter credits.",
        "no_micronutrients": "No micronutrients detected.",
        "why_exists": "WHY THIS APP EXISTS\n\nYou already have a supplement. Can you replace it with whole foods?\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n🥗 WHY WHOLE FOODS ARE USUALLY SUPERIOR\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n✅ NUTRIENT SYNERGY\nMicronutrients don't work alone. Whole foods deliver a biological matrix:\n• Vitamin C in kiwi comes with polyphenols and flavonoids for enhanced absorption\n• Iron in lentils comes with fiber and compounds that optimize bioavailability\n• Magnesium in pumpkin seeds comes with healthy fats and cofactors\n\nA supplement gives you: One isolated molecule\nWhole food gives you: A coordinated biological system\n\n✅ LOWER TOXICITY RISK\nFood sources naturally cap how much you absorb.\nSupplements can overshoot safe limits, accumulate (especially fat-soluble vitamins), and create mineral imbalances.\n\n✅ BETTER HEALTH OUTCOMES\nPopulation studies consistently show:\n• High fruit/vegetable intake → reduced mortality\n• High supplement intake (isolated nutrients) → neutral or sometimes negative outcome\n\nObservational evidence favors food patterns, not isolated pills.\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⚠️  WHEN SUPPLEMENTS ARE ACTUALLY SUPERIOR\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🔹 Vitamin D: Food sources are too weak. Sunlight or supplement is superior.\n🔹 B12 (vegans): You cannot realistically meet needs from plants alone.\n🔹 Iron deficiency anemia: Food is too slow. Medical supplementation is indicated.\n🔹 Folate in pregnancy: Reduces neural tube defects. Food sources are unreliable.\n🔹 Therapeutic dosing: If you need 1000mg vitamin C or 400mg magnesium, whole food becomes impractical.\n   BUT WAIT—even this might be practical!\n   Example: Your supplement has 500mg vitamin C. You can achieve this by eating:\n   • 3–4 kiwis (~65 mg each) + 1 medium orange (~70 mg) + 1 red bell pepper (~200 mg) = ~500 mg\n   You just got 500mg vitamin C PLUS fiber, antioxidants, polyphenols, and phytonutrients your\n   supplement doesn't provide. And it's only 4–6 items—often more practical than you think!\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n💡 YOUR GOAL\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nThis app finds whole foods that contain the same micronutrient dose as your supplement.\nThen you decide: Is whole food practical for me, or do I stick with the supplement?\n",
        "diet_exclusions_title": "Diet & Exclusions",
        "select_diet_type": "Select diet type (used to filter foods):",
        "exclude_foods_label": "Exclude foods (comma-separated): e.g. intestines, liver",
        "input_title": "Input",
        "cancelling": "Cancelling…",
        "ocr_failed": "OCR failed",
        "supplement_dose": "Supplement dose: {dose} mg",
        "debug_label": "DEBUG",
        "input_used": "Input used (truncated):",
        "raw_parse_output": "Raw parse output (truncated):",
        "app_header": "SUPPLEMENT VS WHOLE FOODS (Top 5)",
        "diet_exclusions": "Diet: {diet} | Exclusions: {exclusions}",
        "summary": "Summary",
        "col_rank": "Rank",
        "col_food": "Whole food (plain)",
        "col_per100g": "mg per 100g",
        "col_required_grams": "grams to match",
        "col_grams_for_rda": "Grams for RDA",
        "col_practicality": "Practicality",
        "col_benefits": "Additional health benefits",
        "col_refs": "Refs",
        "non_food": "NON-FOOD",
        "non_food_descr": "Non-food source (e.g. sunlight)",
        "practical": "✔ Practical",
        "large": "⚠ Large",
        "impractical": "— Impractical",
        "final_recommendation_prefix": "Final recommendation: Replace the supplement with",
        "no_valid_matches": "No valid whole-food matches found for this nutrient.",
        "back_to_diet": "← Back to Diet",
        "rec_practical": "Recommendation: Eat ~{grams} g of {food} to match {dose} mg.",
        "rec_large": "⚠ Larger quantity needed: ~{grams} g of {food}.",
        "rec_impractical": "— This nutrient is impractical to replace with whole foods.",
        "verdict_all_practical": "Whole-food replacement looks practical for all listed nutrients.",
        "verdict_mixed": "Mixed results: Some nutrients are practical to replace, others are not.",
        "no_plan_generated": "No whole-food plan generated.",
        "sunlight_note": "Note: 'Sunlight' is a non-food source of vitamin D — UV exposure enables skin synthesis. Food-based options are limited; consider sunlight exposure, fortified foods, or supplementation.",
        "no_practical_replacement": "No practical whole-food replacement found; consider targeted supplementation.",
        "plan_line": "For {nutrient}: ~{grams} g {food}.",
        "config_error_title": "Config Error",
        "config_error_msg": "Missing USDA_API_KEY. Paste it at the top of the script.",
        "input_error_title": "Input Error",
        "input_error_msg": "Paste supplement facts, a product link, or upload a label image.",
        "step_preparing": "Step 1/6: Preparing input…",
        "step_fetching": "Step 1/6: (Optional) Fetching URL locally…",
        "step_parsing": "Step 2/6: Extracting micronutrients + dosages…",
        "step_normalizing": "Step 3/6: Normalizing nutrient data…",
        "step_matching": "Step 4/6: Matching with USDA foods…",
        "step_filtering": "Step 5/6: Filtering by diet & quality…",
        "step_complete": "Step 6/6: Complete!",
        "rec_large": "Recommendation: {grams} g is a lot—split servings or combine 2–3 foods.",
        "rec_impractical": "Recommendation: {grams} g is impractical—consider targeted supplementation for this nutrient.",
        "verdict_all_practical": "Whole-food replacement looks practical for all listed nutrients.",
        "verdict_mixed": "Some nutrients may require large portions; consider a mixed approach (whole foods + targeted supplement).",
        "step_1_preparing": "Step 1/6: Preparing input…",
        "step_2_extracting": "Step 2/6: Extracting micronutrients + dosages…",
        "step_3_candidates": "Step 3/6: Getting whole-food candidates (plain foods only)…",
        "step_4_ranking": "Step 4/6: Ranking foods using USDA nutrient data…",
        "step_4_ranking_nutrient": "Step 4/6: Ranking {nut_name} ({idx}/{total})…",
        "step_5_benefits": "Step 5/6: Adding 3 additional benefits per whole food…",
        "step_6_rendering": "Step 6/6: Rendering output…",
        "error_input": "Input Error",
        "error_paste": "Paste supplement facts, a URL, or upload label image.",
        "error_parse": "Parse Error",
        "error_no_supplement": "No supplement data found. Try label image or paste Supplement Facts text.",
        "error_no_nutrients": "No valid nutrient values found in supplement data.",
        "ocr_sending": "Sending to OCR service...",
        "ocr_processing": "Processing OCR text...",
        "ocr_done": "Done",
        "ocr_processing_api": "Processing with OCR API...",
        "optional": "Optional",
        "default_diet": "Omnivore",
        "none": "none",
        "default_nutrient_benefit": "Essential micronutrient for health",
        "uncategorized": "Uncategorized",
        "not_available": "N/A",
        "usda": "USDA",
        "dash": "—",
        "showing_range": "Showing {start}-{end} of {total}",
        "legend_practical": "✔ Practical",
        "legend_large_amount": "⚠ Large amount needed",
        "legend_impractical": "✗ Impractical",
        "legend_nutrients": "Nutrients:",
        "verdict": "Verdict",
        "plan": "Plan",
    },
    "es": {
        "select_language": "Seleccionar idioma de visualización:",
        "continue": "Continuar",
        "next": "Siguiente →",
        "back": "← Atrás",
        "upload_label": "Subir imagen de etiqueta",
        "try_fetch": "Intentar obtener URL localmente (opcional)",
        "debug_mode": "Modo depuración",
        "analyze": "Analizar →",
        "starting_upload": "Iniciando carga...",
        "complete": "¡Listo!",
        "input_error": "Error de entrada",
        "paste_prompt": "Pegue texto de información nutricional O pegue una URL del producto O suba la imagen de la etiqueta:",
        "analyzing": "Analizando…",
        "starting": "Iniciando…",
        "progress_log": "Registro de progreso:",
        "cancel": "Cancelar",
        "back_to_input": "← Volver a entrada",
        "ocr_error_title": "Error OCR",
        "retry_prompt": "¿Reintentar?",
        "ocr_fallback_reduced": "[Calidad Reducida] Usando modo económico debido a límites de crédito.",
        "ocr_fallback_local": "[OCR Local] Usando Tesseract en dispositivo (gratuito).",
        "ocr_all_failed": "OCR FALLÓ: Instale Tesseract o actualice créditos de OpenRouter.",
        "no_micronutrients": "No se detectaron micronutrientes.",
        "why_exists": "POR QUÉ EXISTE ESTA APLICACIÓN\n\nYa tienes un suplemento. ¿Puedes reemplazarlo con alimentos integrales?\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n🥗 POR QUÉ LOS ALIMENTOS INTEGRALES SON GENERALMENTE SUPERIORES\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n✅ SINERGIA NUTRICIONAL\nLos micronutrientes no funcionan solos. Los alimentos integrales entregan una matriz biológica:\n• Vitamina C en kiwi viene con polifenoles y flavonoides\n• Hierro en lentejas viene con fibra que optimiza la absorción\n• Magnesio en semillas de calabaza viene con grasas saludables y cofactores\n\nSuplemento: una molécula aislada\nAlimento integral: un sistema biológico coordinado\n\n✅ MENOR RIESGO DE TOXICIDAD\nLas fuentes alimentarias limitan naturalmente la absorción.\nLos suplementos pueden exceder límites seguros, acumularse y crear desequilibrios minerales.\n\n✅ MEJORES RESULTADOS DE SALUD\nEstudios poblacionales muestran consistentemente:\n• Alto consumo de frutas/verduras → mortalidad reducida\n• Alto consumo de suplementos aislados → neutral o a veces negativo\n\nLa evidencia favorece patrones alimentarios, no píldoras aisladas.\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⚠️  CUÁNDO LOS SUPLEMENTOS SON REALMENTE SUPERIORES\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🔹 Vitamina D: Las fuentes alimentarias son débiles. Sunlight o suplemento es superior.\n🔹 B12 (veganos): No puedes cubrir necesidades realísticamente con plantas.\n🔹 Anemia por deficiencia de hierro: La comida es demasiado lenta. Suplementación médica indicada.\n🔹 Ácido fólico en embarazo: Reduce defectos del tubo neural. Fuentes alimentarias no son confiables.\n🔹 Dosis terapéutica: Si necesitas 1000mg vitamina C o 400mg magnesio, la comida es impráctica.\n   PERO ESPERA—¡incluso esto debe ser práctico!\n   Ejemplo: Tu suplemento tiene 500mg vitamina C. Puedes lograrlo comiendo:\n   • 3–4 kiwis (~65 mg cada uno) + 1 naranja mediana (~70 mg) + 1 pimiento rojo (~200 mg) = ~500 mg\n   ¡Acabas de obtener 500mg vitamina C MÁS fibra, antioxidantes, polifenoles y fitonutrientes que tu\n   suplemento no proporciona! Y son solo 4–6 alimentos—¡mucho más práctico de lo que crees!\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n💡 TU OBJETIVO\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nEsta app encuentra alimentos integrales que contienen la misma dosis de micronutriente que tu suplemento.\nLuego decides: ¿Es práctica la comida integral para mí, o mantengo el suplemento?\n",
        "diet_exclusions_title": "Dieta y Exclusiones",
        "select_diet_type": "Seleccione el tipo de dieta (se utiliza para filtrar alimentos):",
        "exclude_foods_label": "Excluir alimentos (separados por comas): por ejemplo, intestinos, hígado",
        "input_title": "Entrada",
        "cancelling": "Cancelando…",
        "ocr_failed": "OCR falló",
        "supplement_dose": "Dosis del suplemento: {dose} mg",
        "debug_label": "DEPURACIÓN",
        "input_used": "Entrada utilizada (truncada):",
        "raw_parse_output": "Salida de análisis sin procesar (truncada):",
        "app_header": "SUPLEMENTO VS ALIMENTOS ENTEROS (Top 5)",
        "diet_exclusions": "Dieta: {diet} | Exclusiones: {exclusions}",
        "summary": "Resumen",
        "col_rank": "Rango",
        "col_food": "Alimento entero (simple)",
        "col_per100g": "mg por 100g",
        "col_required_grams": "gramos para igualar",
        "col_grams_for_rda": "Gramos para RDA",
        "col_practicality": "Practicidad",
        "col_benefits": "Beneficios adicionales para la salud",
        "col_refs": "Refs",
        "non_food": "NO-ALIMENTO",
        "non_food_descr": "Fuente no alimentaria (p. ej. exposición al sol)",
        "practical": "✔ Práctico",
        "large": "⚠ Grande",
        "impractical": "— Impracticable",
        "final_recommendation_prefix": "Recomendación final: Reemplazar el suplemento con",
        "no_valid_matches": "No se encontraron coincidencias de alimentos enteros para este nutriente.",
        "back_to_diet": "← Volver a dieta",
        "rec_practical": "Recomendación: Coma ~{grams} g de {food} para igualar {dose} mg.",
        "rec_large": "Recomendación: {grams} g es mucho—divida las porciones o combine 2–3 alimentos.",
        "rec_impractical": "Recomendación: {grams} g es impracticable—considere suplementación específica para este nutriente.",
        "verdict_all_practical": "El reemplazo de alimento entero parece práctico para todos los nutrientes enumerados.",
        "verdict_mixed": "Algunos nutrientes pueden requerir porciones grandes; considere un enfoque mixto (alimentos enteros + suplemento objetivo).",
        "step_1_preparing": "Paso 1/6: Preparando entrada…",
        "step_2_extracting": "Paso 2/6: Extrayendo micronutrientes + dosis…",
        "step_3_candidates": "Paso 3/6: Obteniendo candidatos de alimentos enteros…",
        "step_4_ranking": "Paso 4/6: Clasificando alimentos usando datos de nutrientes USDA…",
        "step_4_ranking_nutrient": "Paso 4/6: Clasificando {nut_name} ({idx}/{total})…",
        "step_5_benefits": "Paso 5/6: Agregando 3 beneficios adicionales por alimento entero…",
        "step_6_rendering": "Paso 6/6: Renderizando salida…",
        "error_input": "Error de entrada",
        "error_paste": "Pegue información nutricional, una URL o cargue una imagen de etiqueta.",
        "error_parse": "Error de análisis",
        "error_no_supplement": "No se encontraron datos de suplemento. Intente imagen de etiqueta o pegue texto de información nutricional.",
        "error_no_nutrients": "No se encontraron valores nutrientes válidos en datos de suplemento.",
        "ocr_sending": "Enviando al servicio OCR...",
        "ocr_processing": "Procesando texto OCR...",
        "ocr_done": "Hecho",
        "ocr_processing_api": "Procesando con API OCR...",
    },
    "de": {
        "select_language": "Sprache für die Anzeige auswählen:",
        "continue": "Weiter",
        "next": "Weiter →",
        "back": "← Zurück",
        "upload_label": "Etikettenbild hochladen",
        "try_fetch": "Versuchen, die URL lokal abzurufen (optional)",
        "debug_mode": "Debug-Modus",
        "analyze": "Analysieren →",
        "starting_upload": "Ladevorgang startet...",
        "complete": "Fertig!",
        "input_error": "Eingabefehler",
        "paste_prompt": "Fügen Sie den Text der Supplement Facts EIN oder fügen Sie eine Produkt-URL ein oder laden Sie ein Etikettenbild hoch:",
        "analyzing": "Analysiere…",
        "starting": "Start…",
        "progress_log": "Fortschrittsprotokoll:",
        "cancel": "Abbrechen",
        "back_to_input": "← Zurück zur Eingabe",
        "ocr_error_title": "OCR-Fehler",
        "retry_prompt": "Nochmals versuchen?",
        "ocr_fallback_reduced": "[Reduzierte Qualität] Verwendung des Sparmodus aufgrund von Kreditlimits.",
        "ocr_fallback_local": "[Lokales OCR] Tesseract auf dem Gerät verwenden (kostenlos).",
        "ocr_all_failed": "OCR FEHLGESCHLAGEN: Installieren Sie Tesseract oder aktualisieren Sie OpenRouter-Guthaben.",
        "no_micronutrients": "Keine Mikronährstoffe erkannt.",
        "why_exists": "WARUM DIESE APP EXISTIERT\n\nVollwertige Lebensmittel bieten:\n• Bessere Absorption (Nährstoffsynergie)\n• Natürliche Kofaktoren\n• Faser und Phytonährstoffe\n• Geringeres Toxizitätsrisiko\n• Bessere Kosteneffizienz\n\nGroße Nahrungsergänzungsmittelunternehmen profitieren oft von verwirrener Vermarktung.\nDieses Tool vereinfacht den evidenzgestützten Austausch durch echte Lebensmittel.",
        "diet_exclusions_title": "Diät & Ausschlüsse",
        "select_diet_type": "Wählen Sie den Diättyp (wird zum Filtern von Lebensmitteln verwendet):",
        "exclude_foods_label": "Lebensmittel ausschließen (kommagetrennt): z. B. Darm, Leber",
        "input_title": "Eingabe",
        "cancelling": "Wird abgebrochen…",
        "ocr_failed": "OCR fehlgeschlagen",
        "supplement_dose": "Ergänzungsdosis: {dose} mg",
        "debug_label": "DEBUG",
        "input_used": "Verwendete Eingabe (gekürzt):",
        "raw_parse_output": "Rohe Parse-Ausgabe (gekürzt):",
        "app_header": "ERGÄNZUNG VS VOLLWERTIGE NAHRUNG (Top 5)",
        "diet_exclusions": "Diät: {diet} | Ausschlüsse: {exclusions}",
        "summary": "Zusammenfassung",
        "col_rank": "Rang",
        "col_food": "Lebensmittel (einfach)",
        "col_per100g": "mg pro 100g",
        "col_required_grams": "Gramm zum Ausgleich",
        "col_grams_for_rda": "Gramm für RDA",
        "col_practicality": "Praktikabilität",
        "col_benefits": "Zusätzliche gesundheitliche Vorteile",
        "col_refs": "Refs",
        "non_food": "NICHT-LEBENSMITTEL",
        "non_food_descr": "Nicht-essen Quelle (z. B. Sonnenlicht)",
        "practical": "✔ Praktisch",
        "large": "⚠ Viel",
        "impractical": "— Unpraktisch",
        "final_recommendation_prefix": "Abschließende Empfehlung: Ersetzen Sie das Supplement durch",
        "no_valid_matches": "Keine passenden Vollwert-Lebensmittel für diesen Nährstoff gefunden.",
        "back_to_diet": "← Zurück zur Diät",
        "rec_practical": "Empfehlung: Essen Sie ~{grams} g von {food} um {dose} mg auszugleichen.",
        "rec_large": "Empfehlung: {grams} g ist viel—teilen Sie die Portionen auf oder kombinieren Sie 2–3 Lebensmittel.",
        "rec_impractical": "Empfehlung: {grams} g ist unpraktisch—erwägen Sie gezielte Nahrungsergänzung für diesen Nährstoff.",
        "verdict_all_practical": "Der Austausch mit vollwertigen Lebensmitteln scheint für alle aufgelisteten Nährstoffe praktikabel zu sein.",
        "verdict_mixed": "Einige Nährstoffe erfordern möglicherweise große Portionen; erwägen Sie einen gemischten Ansatz (vollwertige Lebensmittel + gezieltes Supplement).",
        "step_1_preparing": "Schritt 1/6: Eingabe wird vorbereitet…",
        "step_2_extracting": "Schritt 2/6: Extrahieren von Mikronährstoffen + Dosierungen…",
        "step_3_candidates": "Schritt 3/6: Ganze Lebensmittelkandidaten werden abgerufen…",
        "step_4_ranking": "Schritt 4/6: Lebensmittel mit USDA-Nährstoffdaten klassifizieren…",
        "step_4_ranking_nutrient": "Schritt 4/6: Klassifizieren von {nut_name} ({idx}/{total})…",
        "step_5_benefits": "Schritt 5/6: Hinzufügen von 3 zusätzlichen Vorteilen pro Lebensmittel…",
        "step_6_rendering": "Schritt 6/6: Ausgabe wird gerendert…",
        "error_input": "Eingabefehler",
        "error_paste": "Fügen Sie Nährstoffinformationen, eine URL oder ein Etikettenbild ein.",
        "error_parse": "Analysefehler",
        "error_no_supplement": "Keine Ergänzungsdaten gefunden. Versuchen Sie ein Etikettenbild oder fügen Sie Text mit Nährstoffinformationen ein.",
        "error_no_nutrients": "Keine gültigen Nährstoffwerte in Ergänzungsdaten gefunden.",
        "ocr_sending": "An OCR-Service senden...",
        "ocr_processing": "OCR-Text wird verarbeitet...",
        "ocr_done": "Fertig",
        "ocr_processing_api": "Verarbeitung mit OCR-API...",
    },
    "fr": {
        "select_language": "Sélectionner la langue d'affichage :",
        "continue": "Continuer",
        "next": "Suivant →",
        "back": "← Retour",
        "upload_label": "Télécharger l'image d'étiquette",
        "try_fetch": "Essayer de récupérer l'URL localement (facultatif)",
        "debug_mode": "Mode débogage",
        "analyze": "Analyser →",
        "starting_upload": "Démarrage du téléchargement...",
        "complete": "Terminé !",
        "input_error": "Erreur d'entrée",
        "paste_prompt": "Collez le texte des faits supplémentaires OU collez une URL de produit OU téléchargez l'image d'étiquette :",
        "analyzing": "Analyse en cours…",
        "starting": "Démarrage…",
        "progress_log": "Journal de progression :",
        "cancel": "Annuler",
        "back_to_input": "← Retour à l'entrée",
        "ocr_error_title": "Erreur OCR",
        "retry_prompt": "Réessayer ?",
        "ocr_fallback_reduced": "[Qualité réduite] Utilisation du mode économique en raison des limites de crédit.",
        "ocr_fallback_local": "[OCR local] Utilisation de Tesseract sur l'appareil (gratuit).",
        "ocr_all_failed": "OCR ÉCHOUÉ : Installez Tesseract ou mettez à jour les crédits OpenRouter.",
        "no_micronutrients": "Aucun micronutriment détecté.",
        "why_exists": "POURQUOI CETTE APPLICATION EXISTE\n\nVous avez déjà un supplément. Pouvez-vous le remplacer par des aliments complets?\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n🥗 POURQUOI LES ALIMENTS COMPLETS SONT GÉNÉRALEMENT SUPÉRIEURS\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n✅ SYNERGIE NUTRITIONNELLE\nLes micronutrients ne fonctionnent pas seuls. Les aliments complets offrent une matrice biologique:\n• Vitamine C dans le kiwi vient avec des polyphénols et des flavonoïdes\n• Fer dans les lentilles vient avec des fibres qui optimisent l'absorption\n• Magnésium dans les graines de courge vient avec des graisses saines et des cofacteurs\n\nSupplement: une molécule isolée\nAliment complet: un système biologique coordonné\n\n✅ RISQUE DE TOXICITÉ INFÉRIEUR\nLes sources alimentaires limitent naturellement l'absorption.\nLes suppléments peuvent dépasser les limites sûres, s'accumuler et créer des déséquilibres minéraux.\n\n✅ MEILLEURS RÉSULTATS SANITAIRES\nLes études de population montrent systématiquement:\n• Haute consommation de fruits/légumes → mortalité réduite\n• Haute consommation de suppléments isolés → neutre ou parfois négatif\n\nLes preuves favorisent les modèles alimentaires, pas les pilules isolées.\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⚠️  QUAND LES SUPPLÉMENTS SONT RÉELLEMENT SUPÉRIEURS\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🔹 Vitamine D: Les sources alimentaires sont trop faibles. Le soleil ou le supplément est supérieur.\n🔹 B12 (vegans): Vous ne pouvez réaliste couvrir les besoins par les plantes seules.\n🔹 Anémie ferriprive: L'alimentation est trop lente. La supplémentation médicale est indiquée.\n🔹 Folate en grossesse: Réduit les malformations du tube neural. Sources alimentaires peu fiables.\n🔹 Dosage thérapeutique: Si vous avez besoin de 1000mg vitamine C ou 400mg magnésium, l'alimentation est impractique.\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n💡 VOTRE OBJECTIF\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nCette app trouve des aliments complets qui contiennent la même dose de micronutriment que votre supplément.\nEnsuite, vous décidez: l'alimentation complète est-elle pratique pour moi, ou je reste avec le supplément?\n",
        "diet_exclusions_title": "Régime & Exclusions",
        "select_diet_type": "Sélectionnez le type de régime (utilisé pour filtrer les aliments) :",
        "exclude_foods_label": "Exclure les aliments (séparés par des virgules) : par exemple, intestins, foie",
        "input_title": "Entrée",
        "cancelling": "Annulation en cours…",
        "ocr_failed": "OCR échoué",
        "supplement_dose": "Dose du supplément : {dose} mg",
        "debug_label": "DÉBOGAGE",
        "input_used": "Entrée utilisée (tronquée) :",
        "raw_parse_output": "Sortie d'analyse brute (tronquée) :",
        "app_header": "SUPPLÉMENT VS ALIMENTS ENTIERS (Top 5)",
        "diet_exclusions": "Régime : {diet} | Exclusions : {exclusions}",
        "summary": "Résumé",
        "col_rank": "Rang",
        "col_food": "Aliment entier (simple)",
        "col_per100g": "mg par 100g",
        "col_required_grams": "grammes pour correspondre",
        "col_grams_for_rda": "Grammes pour RDA",
        "col_practicality": "Praticabilité",
        "col_benefits": "Avantages supplémentaires pour la santé",
        "col_refs": "Refs",
        "non_food": "NON-ALIMENTAIRE",
        "non_food_descr": "Source non alimentaire (par exemple, lumière du soleil)",
        "practical": "✔ Pratique",
        "large": "⚠ Grand",
        "impractical": "— Peu pratique",
        "final_recommendation_prefix": "Recommandation finale : Remplacer le supplément par",
        "no_valid_matches": "Aucun aliment entier correspondant trouvé pour cet élément nutritif.",
        "back_to_diet": "← Retour au régime",
        "rec_practical": "Recommandation : Mangez ~{grams} g de {food} pour correspondre à {dose} mg.",
        "rec_large": "Recommandation : {grams} g c'est beaucoup—divisez les portions ou combinez 2–3 aliments.",
        "rec_impractical": "Recommandation : {grams} g n'est pas pratique—envisagez une supplémentation ciblée pour cet élément nutritif.",
        "verdict_all_practical": "Le remplacement par des aliments entiers semble pratique pour tous les éléments nutritifs énumérés.",
        "verdict_mixed": "Certains éléments nutritifs peuvent nécessiter de grandes portions ; envisagez une approche mixte (aliments entiers + supplément ciblé).",
        "step_1_preparing": "Étape 1/6 : Préparation de l'entrée…",
        "step_2_extracting": "Étape 2/6 : Extraction des micronutriments + dosages…",
        "step_3_candidates": "Étape 3/6 : Obtention des candidats aliments entiers…",
        "step_4_ranking": "Étape 4/6 : Classement des aliments à l'aide des données nutritionnelles USDA…",
        "step_4_ranking_nutrient": "Étape 4/6 : Classement de {nut_name} ({idx}/{total})…",
        "step_5_benefits": "Étape 5/6 : Ajout de 3 avantages supplémentaires par aliment entier…",
        "step_6_rendering": "Étape 6/6 : Rendu de la sortie…",
        "error_input": "Erreur d'entrée",
        "error_paste": "Collez les informations nutritionnelles, une URL ou chargez une image d'étiquette.",
        "error_parse": "Erreur d'analyse",
        "error_no_supplement": "Aucune donnée de supplément trouvée. Essayez l'image d'étiquette ou collez le texte d'information nutritionnelle.",
        "error_no_nutrients": "Aucune valeur nutritionnelle valide trouvée dans les données de supplément.",
        "ocr_sending": "Envoi au service OCR...",
        "ocr_processing": "Traitement du texte OCR...",
        "ocr_done": "Fait",
        "ocr_processing_api": "Traitement avec l'API OCR...",
    },
    "zh": {
        "select_language": "选择显示语言：",
        "continue": "继续",
        "next": "下一个 →",
        "back": "← 返回",
        "upload_label": "上传标签图像",
        "try_fetch": "尝试在本地获取 URL（可选）",
        "debug_mode": "调试模式",
        "analyze": "分析 →",
        "starting_upload": "开始上传...",
        "complete": "完成！",
        "input_error": "输入错误",
        "paste_prompt": "粘贴补充事实文本或粘贴产品 URL 或上传标签图像：",
        "analyzing": "分析中…",
        "starting": "启动中…",
        "progress_log": "进度日志：",
        "cancel": "取消",
        "back_to_input": "← 返回输入",
        "ocr_error_title": "OCR 错误",
        "retry_prompt": "重试？",
        "ocr_fallback_reduced": "[质量降低] 由于信用限制，使用经济模式。",
        "ocr_fallback_local": "[本地 OCR] 使用设备上的 Tesseract（免费）。",
        "ocr_all_failed": "OCR 失败：安装 Tesseract 或升级 OpenRouter 信用。",
        "no_micronutrients": "未检测到微量营养素。",
        "why_exists": "这个应用程序为什么存在\n\n全食物提供：\n• 更好的吸收（营养协同作用）\n• 天然辅酶\n• 纤维和植物营养素\n• 更低的毒性风险\n• 更好的成本效率\n\n大型补充公司通常受益于营销混乱。\n这个工具简化了用真实食物进行的循证替代。",
        "diet_exclusions_title": "饮食和排除",
        "select_diet_type": "选择饮食类型（用于过滤食物）：",
        "exclude_foods_label": "排除食物（逗号分隔）：例如肠、肝",
        "input_title": "输入",
        "cancelling": "取消中…",
        "ocr_failed": "OCR 失败",
        "supplement_dose": "补充剂量：{dose} mg",
        "debug_label": "调试",
        "input_used": "使用的输入（截断）：",
        "raw_parse_output": "原始解析输出（截断）：",
        "app_header": "补充剂 VS 全食物（前 5 名）",
        "diet_exclusions": "饮食：{diet} | 排除：{exclusions}",
        "summary": "摘要",
        "col_rank": "排名",
        "col_food": "全食物（普通）",
        "col_per100g": "每 100g 毫克",
        "col_required_grams": "克数匹配",
        "col_grams_for_rda": "RDA 克数",
        "col_practicality": "可行性",
        "col_benefits": "额外的健康益处",
        "col_refs": "参考文献",
        "non_food": "非食物",
        "non_food_descr": "非食物来源（例如阳光）",
        "practical": "✔ 可行",
        "large": "⚠ 大",
        "impractical": "— 不切实际",
        "final_recommendation_prefix": "最终建议：用以下方式替换补充剂",
        "no_valid_matches": "未找到此营养素的有效全食物匹配。",
        "back_to_diet": "← 返回饮食",
        "rec_practical": "建议：吃约 {grams} 克的 {food} 以匹配 {dose} mg。",
        "rec_large": "建议：{grams} 克太多了——分割份量或组合 2-3 种食物。",
        "rec_impractical": "建议：{grams} 克不切实际——考虑针对此营养素的靶向补充。",
        "verdict_all_practical": "全食物替代对所有列出的营养素似乎都是可行的。",
        "verdict_mixed": "某些营养素可能需要大量；考虑混合方法（全食物+靶向补充剂）。",
    },
    "hi": {
        "select_language": "प्रदर्शन भाषा चुनें:",
        "continue": "जारी रखें",
        "next": "अगला →",
        "back": "← वापस",
        "upload_label": "लेबल छवि अपलोड करें",
        "try_fetch": "स्थानीय रूप से URL लाने का प्रयास करें (वैकल्पिक)",
        "debug_mode": "डीबग मोड",
        "analyze": "विश्लेषण करें →",
        "starting_upload": "अपलोड शुरू हो रहा है...",
        "complete": "पूर्ण!",
        "input_error": "इनपुट त्रुटि",
        "paste_prompt": "पूरक तथ्य पाठ पेस्ट करें या उत्पाद URL पेस्ट करें या लेबल छवि अपलोड करें:",
        "analyzing": "विश्लेषण जारी है…",
        "starting": "शुरुआत की जा रही है…",
        "progress_log": "प्रगति लॉग:",
        "cancel": "रद्द करें",
        "back_to_input": "← इनपुट पर वापस जाएं",
        "ocr_error_title": "OCR त्रुटि",
        "retry_prompt": "फिर से प्रयास करें?",
        "ocr_fallback_reduced": "[कम गुणवत्ता] क्रेडिट सीमा के कारण अर्थव्यवस्था मोड का उपयोग करना।",
        "ocr_fallback_local": "[स्थानीय OCR] डिवाइस पर Tesseract का उपयोग (निःशुल्क)।",
        "ocr_all_failed": "OCR विफल: Tesseract स्थापित करें या OpenRouter क्रेडिट अपग्रेड करें।",
        "no_micronutrients": "कोई माइक्रोन्यूट्रिएंट का पता नहीं चला।",
        "why_exists": "यह ऐप क्यों मौजूद है\n\nपूरे खाद्य पदार्थ प्रदान करते हैं:\n• बेहतर अवशोषण (पोषक तत्वों का सहक्रिया)\n• प्राकृतिक सहकारक\n• फाइबर और फाइटोन्यूट्रिएंट्स\n• कम विषाक्तता का जोखिम\n• बेहतर लागत दक्षता\n\nबड़ी पूरक कंपनियां अक्सर विपणन भ्रम से लाभ उठाती हैं।\nयह उपकरण वास्तविक भोजन के साथ साक्ष्य-आधारित प्रतिस्थापन को सरल बनाता है।",
        "diet_exclusions_title": "आहार और बहिष्कार",
        "select_diet_type": "आहार प्रकार चुनें (खाद्य पदार्थों को फ़िल्टर करने के लिए उपयोग किया जाता है):",
        "exclude_foods_label": "खाद्य पदार्थों को बाहर करें (अल्पविराम से अलग): उदाहरण के लिए आंतें, जिगर",
        "input_title": "इनपुट",
        "cancelling": "रद्द किया जा रहा है…",
        "ocr_failed": "OCR विफल",
        "supplement_dose": "पूरक खुराक: {dose} mg",
        "debug_label": "डीबग",
        "input_used": "उपयोग किया गया इनपुट (छोटा किया गया):",
        "raw_parse_output": "कच्चा पार्स आउटपुट (छोटा किया गया):",
        "app_header": "पूरक बनाम पूरे खाद्य पदार्थ (शीर्ष 5)",
        "diet_exclusions": "आहार: {diet} | बहिष्कार: {exclusions}",
        "summary": "सारांश",
        "col_rank": "रैंक",
        "col_food": "पूरा खाद्य (सादा)",
        "col_per100g": "प्रति 100g मिलीग्राम",
        "col_required_grams": "ग्राम मेल खाएं",
        "col_grams_for_rda": "RDA के लिए ग्राम",
        "col_practicality": "व्यावहारिकता",
        "col_benefits": "अतिरिक्त स्वास्थ्य लाभ",
        "col_refs": "संदर्भ",
        "non_food": "गैर-खाद्य",
        "non_food_descr": "गैर-खाद्य स्रोत (उदाहरण के लिए सूर्य का प्रकाश)",
        "practical": "✔ व्यावहारिक",
        "large": "⚠ बड़ा",
        "impractical": "— अव्यावहारिक",
        "final_recommendation_prefix": "अंतिम सिफारिश: पूरक को बदलें",
        "no_valid_matches": "इस माइक्रोन्यूट्रिएंट के लिए कोई मान्य पूरे खाद्य पदार्थ का मिलान नहीं मिला।",
        "back_to_diet": "← आहार पर वापस जाएं",
        "rec_practical": "सिफारिश: {dose} मिलीग्राम से मेल खाने के लिए {food} के लगभग {grams} ग्राम खाएं।",
        "rec_large": "सिफारिश: {grams} ग्राम बहुत अधिक है—सर्विंग्स को विभाजित करें या 2-3 खाद्य पदार्थों को संयोजित करें।",
        "rec_impractical": "सिफारिश: {grams} ग्राम अव्यावहारिक है—इस माइक्रोन्यूट्रिएंट के लिए लक्षित पूरकता पर विचार करें।",
        "verdict_all_practical": "पूरे खाद्य पदार्थों का प्रतिस्थापन सभी सूचीबद्ध माइक्रोन्यूट्रिएंट्स के लिए व्यावहारिक लगता है।",
        "verdict_mixed": "कुछ माइक्रोन्यूट्रिएंट्स को बड़े हिस्से की आवश्यकता हो सकती है; मिश्रित दृष्टिकोण (पूरे खाद्य पदार्थ + लक्षित पूरक) पर विचार करें।",
    },
    "ar": {
        "select_language": "اختر لغة العرض:",
        "continue": "متابعة",
        "next": "التالي →",
        "back": "← العودة",
        "upload_label": "تحميل صورة الملصق",
        "try_fetch": "محاولة جلب عنوان URL محليًا (اختياري)",
        "debug_mode": "وضع التصحيح",
        "analyze": "تحليل →",
        "starting_upload": "بدء التحميل...",
        "complete": "مكتمل!",
        "input_error": "خطأ في الإدخال",
        "paste_prompt": "الصق نص حقائق المكمل أو الصق عنوان URL منتج أو حمل صورة ملصق:",
        "analyzing": "جارٍ التحليل…",
        "starting": "بدء…",
        "progress_log": "سجل التقدم:",
        "cancel": "إلغاء",
        "back_to_input": "← العودة إلى الإدخال",
        "ocr_error_title": "خطأ OCR",
        "retry_prompt": "إعادة المحاولة؟",
        "ocr_fallback_reduced": "[جودة منخفضة] استخدام الوضع الاقتصادي بسبب حدود الائتمان.",
        "ocr_fallback_local": "[OCR محلي] استخدام Tesseract على الجهاز (مجاني).",
        "ocr_all_failed": "فشل OCR: قم بتثبيت Tesseract أو ترقية أرصدة OpenRouter.",
        "no_micronutrients": "لم يتم كشف المغذيات الدقيقة.",
        "why_exists": "لماذا يوجد هذا التطبيق\n\nالأطعمة الكاملة توفر:\n• امتصاص أفضل (تآزر العناصر الغذائية)\n• عوامل共عامل طبيعية\n• ألياف وفايتونيوترينتس\n• مخاطر السمية المنخفضة\n• كفاءة التكلفة الأفضل\n\nغالبًا ما تستفيد شركات المكملات الكبرى من الالتباس التسويقي.\nتُبسط هذه الأداة الاستبدال القائم على الأدلة بالغذاء الحقيقي.",
        "diet_exclusions_title": "النظام الغذائي والاستبعادات",
        "select_diet_type": "حدد نوع النظام الغذائي (يستخدم لتصفية الأطعمة):",
        "exclude_foods_label": "استبعد الأطعمة (مفصولة بفواصل): على سبيل المثال الأمعاء والكبد",
        "input_title": "الإدخال",
        "cancelling": "جارٍ الإلغاء…",
        "ocr_failed": "فشل OCR",
        "supplement_dose": "جرعة المكمل: {dose} ملغ",
        "debug_label": "تصحيح",
        "input_used": "الإدخال المستخدم (مختصر):",
        "raw_parse_output": "إخراج التحليل الخام (مختصر):",
        "app_header": "المكمل مقابل الأطعمة الكاملة (أفضل 5)",
        "diet_exclusions": "النظام الغذائي: {diet} | الاستبعادات: {exclusions}",
        "summary": "ملخص",
        "col_rank": "الترتيب",
        "col_food": "طعام كامل (عادي)",
        "col_per100g": "ملغ لكل 100 غرام",
        "col_required_grams": "غرام للمطابقة",
        "col_grams_for_rda": "غرام لـ RDA",
        "col_practicality": "الجدوى",
        "col_benefits": "فوائد صحية إضافية",
        "col_refs": "مراجع",
        "non_food": "غير غذائي",
        "non_food_descr": "مصدر غير غذائي (مثل ضوء الشمس)",
        "practical": "✔ عملي",
        "large": "⚠ كبير",
        "impractical": "— غير عملي",
        "final_recommendation_prefix": "التوصية النهائية: استبدل المكمل بـ",
        "no_valid_matches": "لم يتم العثور على مطابقة طعام كامل صحيحة لهذا المغذي.",
        "back_to_diet": "← العودة إلى النظام الغذائي",
        "rec_practical": "التوصية: تناول ما يقرب من {grams} غرام من {food} لمطابقة {dose} ملغ.",
        "rec_large": "التوصية: {grams} غرام كثير جدًا—قسّم الحصص أو اجمع 2-3 أطعمة.",
        "rec_impractical": "التوصية: {grams} غرام غير عملي—فكر في المكملات الموجهة لهذا المغذي.",
        "verdict_all_practical": "يبدو أن استبدال الأطعمة الكاملة عملي لجميع العناصر الغذائية المدرجة.",
        "verdict_mixed": "قد تتطلب بعض العناصر الغذائية حصصًا كبيرة؛ فكر في نهج مختلط (أطعمة كاملة + مكمل موجه).",
    },
    "bn": {
        "select_language": "প্রদর্শন ভাষা নির্বাচন করুন:",
        "continue": "চালিয়ে যান",
        "next": "পরবর্তী →",
        "back": "← ফিরে",
        "upload_label": "লেবেল ইমেজ আপলোড করুন",
        "try_fetch": "স্থানীয়ভাবে URL আনার চেষ্টা করুন (ঐচ্ছিক)",
        "debug_mode": "ডিবাগ মোড",
        "analyze": "বিশ্লেষণ করুন →",
        "starting_upload": "আপলোড শুরু হচ্ছে...",
        "complete": "সম্পন্ন!",
        "input_error": "ইনপুট ত্রুটি",
        "paste_prompt": "সাপ্লিমেন্ট ফ্যাক্ট টেক্সট পেস্ট করুন বা পণ্যের URL পেস্ট করুন বা লেবেল ইমেজ আপলোড করুন:",
        "analyzing": "বিশ্লেষণাধীন…",
        "starting": "শুরু হচ্ছে…",
        "progress_log": "অগ্রগতি লগ:",
        "cancel": "বাতিল করুন",
        "back_to_input": "← ইনপুটে ফিরে যান",
        "ocr_error_title": "OCR ত্রুটি",
        "retry_prompt": "পুনরায় চেষ্টা করুন?",
        "ocr_fallback_reduced": "[হ্রাসকৃত গুণমান] ক্রেডিট সীমার কারণে অর্থনৈতিক মোড ব্যবহার করা হচ্ছে।",
        "ocr_fallback_local": "[স্থানীয় OCR] ডিভাইসে Tesseract ব্যবহার করা হচ্ছে (বিনামূল্যে)।",
        "ocr_all_failed": "OCR ব্যর্থ: Tesseract ইনস্টল করুন বা OpenRouter क্রেडিট আপগ্রেড করুন।",
        "no_micronutrients": "কোন মাইক্রোনিউট্রিয়েন্ট সনাক্ত করা হয়নি।",
        "why_exists": "এই অ্যাপটি কেন উপস্থিত\n\nসম্পূর্ণ খাবার প্রদান করে:\n• ভাল শোষণ (পুষ্টির সিনার্জি)\n• প্রাকৃতিক কোফ্যাক্টর\n• ফাইবার এবং ফাইটোনিউট্রিয়েন্ট\n• কম বিষাক্ততার ঝুঁকি\n• ভাল খরচ দক্ষতা\n\nবড় সাপ্লিমেন্ট কোম্পানিগুলি প্রায়ই মার্কেটিং বিভ্রম থেকে উপকৃত হয়।\nএই সরঞ্জামটি বাস্তব খাবার দিয়ে প্রমাণ-ভিত্তিক প্রতিস্থাপনকে সরল করে।",
        "diet_exclusions_title": "খাদ্য এবং বর্জনসমূহ",
        "select_diet_type": "খাদ্যের ধরন নির্বাচন করুন (খাবার ফিল্টার করতে ব্যবহৃত):",
        "exclude_foods_label": "খাবার বাদ দিন (কমা দ্বারা আলাদা): উদাহরণস্বরূপ অন্ত্র, লিভার",
        "input_title": "ইনপুট",
        "cancelling": "বাতিল করা হচ্ছে…",
        "ocr_failed": "OCR ব্যর্থ",
        "supplement_dose": "সাপ্লিমেন্ট ডোজ: {dose} মিগ্রা",
        "debug_label": "ডিবাগ",
        "input_used": "ব্যবহৃত ইনপুট (সংক্ষিপ্ত):",
        "raw_parse_output": "কাঁচা পার্স আউটপুট (সংক্ষিপ্ত):",
        "app_header": "সাপ্লিমেন্ট বনাম সম্পূর্ণ খাবার (শীর্ষ 5)",
        "diet_exclusions": "খাদ্য: {diet} | বর্জনসমূহ: {exclusions}",
        "summary": "সারমর্ম",
        "col_rank": "র্যাঙ্ক",
        "col_food": "সম্পূর্ণ খাবার (সাধারণ)",
        "col_per100g": "প্রতি 100 গ্রাম মিগ্রা",
        "col_required_grams": "গ্রাম মেলাতে",
        "col_grams_for_rda": "RDA এর জন্য গ্রাম",
        "col_practicality": "ব্যবহারযোগ্যতা",
        "col_benefits": "অতিরিক্ত স্বাস্থ্য সুবিধা",
        "col_refs": "সূত্র",
        "non_food": "অ-খাদ্য",
        "non_food_descr": "অ-খাদ্য উৎস (যেমন সূর্যালোক)",
        "practical": "✔ ব্যবহারযোগ্য",
        "large": "⚠ বড়",
        "impractical": "— অব্যবহারযোগ্য",
        "final_recommendation_prefix": "চূড়ান্ত সুপারিশ: সাপ্লিমেন্ট প্রতিস্থাপন করুন",
        "no_valid_matches": "এই মাইক্রোনিউট্রিয়েন্টের জন্য কোন বৈধ সম্পূর্ণ খাবার মেলা পাওয়া যায়নি।",
        "back_to_diet": "← খাদ্যে ফিরে যান",
        "rec_practical": "সুপারিশ: {dose} মিগ্রা মেলাতে {food} এর প্রায় {grams} গ্রাম খান।",
        "rec_large": "সুপারিশ: {grams} গ্রাম অনেক—পরিবেশন ভাগ করুন বা 2-3 খাবার একত্রিত করুন।",
        "rec_impractical": "সুপারিশ: {grams} গ্রাম অব্যবহারযোগ্য—এই মাইক্রোনিউট্রিয়েন্টের জন্য লক্ষ্যযুক্ত পরিপূরক বিবেচনা করুন।",
        "verdict_all_practical": "সম্পূর্ণ খাদ্য প্রতিস্থাপন সমস্ত তালিকাভুক্ত মাইক্রোনিউট্রিয়েন্টের জন্য ব্যবহারযোগ্য মনে হয়।",
        "verdict_mixed": "কিছু মাইক্রোনিউট্রিয়েন্ট বড় অংশ প্রয়োজন হতে পারে; মিশ্রিত পদ্ধতি বিবেচনা করুন (সম্পূর্ণ খাবার + লক্ষ্যযুক্ত পরিপূরক)।",
    },
    "ru": {
        "select_language": "Выберите язык отображения:",
        "continue": "Продолжить",
        "next": "Далее →",
        "back": "← Назад",
        "upload_label": "Загрузить изображение этикетки",
        "try_fetch": "Попытаться получить URL локально (дополнительно)",
        "debug_mode": "Режим отладки",
        "analyze": "Анализировать →",
        "starting_upload": "Начало загрузки...",
        "complete": "Готово!",
        "input_error": "Ошибка ввода",
        "paste_prompt": "Вставьте текст информации о добавках ИЛИ вставьте URL продукта ИЛИ загрузите изображение этикетки:",
        "analyzing": "Анализ…",
        "starting": "Запуск…",
        "progress_log": "Журнал хода выполнения:",
        "cancel": "Отмена",
        "back_to_input": "← Вернуться к вводу",
        "ocr_error_title": "Ошибка OCR",
        "retry_prompt": "Повторить попытку?",
        "ocr_fallback_reduced": "[Низкое качество] Использование экономного режима из-за ограничения кредитов.",
        "ocr_fallback_local": "[Локальное OCR] Использование Tesseract на устройстве (бесплатно).",
        "ocr_all_failed": "OCR ОШИБКА: Установите Tesseract или обновите кредиты OpenRouter.",
        "no_micronutrients": "Микронутриенты не обнаружены.",
        "why_exists": "ПОЧЕМУ СУЩЕСТВУЕТ ЭТО ПРИЛОЖЕНИЕ\n\nЦельные пищевые продукты обеспечивают:\n• Лучшее усвоение (синергия питательных веществ)\n• Природные кофакторы\n• Клетчатка и фитонутриенты\n• Меньший риск токсичности\n• Лучшую экономическую эффективность\n\nКрупные компании-производители добавок часто извлекают выгоду из маркетингового замешательства.\nЭтот инструмент упрощает замену на основе фактических данных с помощью настоящей пищи.",
        "diet_exclusions_title": "Диета и исключения",
        "select_diet_type": "Выберите тип диеты (используется для фильтрации продуктов):",
        "exclude_foods_label": "Исключите продукты (через запятую): например животные, печень",
        "input_title": "Ввод",
        "cancelling": "Отмена…",
        "ocr_failed": "OCR ошибка",
        "supplement_dose": "Доза добавки: {dose} мг",
        "debug_label": "ОТЛАДКА",
        "input_used": "Использованный ввод (сокращено):",
        "raw_parse_output": "Результат анализа (сокращено):",
        "app_header": "ДОБАВКА VS ЦЕЛЬНЫЕ ПРОДУКТЫ (5 лучших)",
        "diet_exclusions": "Диета: {diet} | Исключения: {exclusions}",
        "summary": "Резюме",
        "col_rank": "Ранг",
        "col_food": "Цельный продукт (простой)",
        "col_per100g": "мг на 100г",
        "col_required_grams": "граммы для соответствия",
        "col_grams_for_rda": "Граммы для RDA",
        "col_practicality": "Практичность",
        "col_benefits": "Дополнительные преимущества для здоровья",
        "col_refs": "Ссылки",
        "non_food": "НЕ ПИЩЕВОЙ",
        "non_food_descr": "Источник, не являющийся пищевым (например, солнечный свет)",
        "practical": "✔ Практично",
        "large": "⚠ Большое",
        "impractical": "— Непрактично",
        "final_recommendation_prefix": "Окончательная рекомендация: замените добавку на",
        "no_valid_matches": "Для этого микронутриента не найдено подходящих цельнозерновых продуктов.",
        "back_to_diet": "← Вернуться к диете",
        "rec_practical": "Рекомендация: Ешьте около {grams} г {food} чтобы соответствовать {dose} мг.",
        "rec_large": "Рекомендация: {grams} г - это много—разделите порции или комбинируйте 2-3 продукта.",
        "rec_impractical": "Рекомендация: {grams} г непрактично—рассмотрите целевую добавку для этого микронутриента.",
        "verdict_all_practical": "Замена цельными продуктами выглядит практичной для всех перечисленных микронутриентов.",
        "verdict_mixed": "Некоторые микронутриенты могут потребовать большие порции; рассмотрите смешанный подход (цельные продукты + целевая добавка).",
    },
    "pt": {
        "select_language": "Selecionar idioma de exibição:",
        "continue": "Continuar",
        "next": "Próximo →",
        "back": "← Voltar",
        "upload_label": "Carregar imagem de etiqueta",
        "try_fetch": "Tentar buscar URL localmente (opcional)",
        "debug_mode": "Modo de depuração",
        "analyze": "Analisar →",
        "starting_upload": "Iniciando upload...",
        "complete": "Concluído!",
        "input_error": "Erro de entrada",
        "paste_prompt": "Cole o texto dos fatos do suplemento OU cole uma URL do produto OU carregue uma imagem de etiqueta:",
        "analyzing": "Analisando…",
        "starting": "Iniciando…",
        "progress_log": "Log de progresso:",
        "cancel": "Cancelar",
        "back_to_input": "← Voltar à entrada",
        "ocr_error_title": "Erro OCR",
        "retry_prompt": "Tentar novamente?",
        "ocr_fallback_reduced": "[Qualidade reduzida] Usando modo econômico devido aos limites de crédito.",
        "ocr_fallback_local": "[OCR local] Usando Tesseract no dispositivo (gratuito).",
        "ocr_all_failed": "OCR FALHOU: Instale o Tesseract ou atualize os créditos do OpenRouter.",
        "no_micronutrients": "Nenhum micronutriente detectado.",
        "why_exists": "POR QUE ESTE APLICATIVO EXISTE\n\nAlimentos integrais fornecem:\n• Melhor absorção (sinergismo nutricional)\n• Cofactores naturais\n• Fibras e fitonutrientes\n• Menor risco de toxicidade\n• Melhor eficiência de custos\n\nGrandes empresas de suplementos frequentemente se beneficiam da confusão de marketing.\nEsta ferramenta simplifica a substituição baseada em evidências com alimento real.",
        "diet_exclusions_title": "Dieta e Exclusões",
        "select_diet_type": "Seleciona tipo de dieta (usado para filtrar alimentos):",
        "exclude_foods_label": "Excluir alimentos (separados por vírgula): por exemplo, intestinos, fígado",
        "input_title": "Entrada",
        "cancelling": "Cancelando…",
        "ocr_failed": "OCR falhou",
        "supplement_dose": "Dose do suplemento: {dose} mg",
        "debug_label": "DEPURAÇÃO",
        "input_used": "Entrada usada (truncada):",
        "raw_parse_output": "Saída de análise bruta (truncada):",
        "app_header": "SUPLEMENTO VS ALIMENTOS INTEGRAIS (Top 5)",
        "diet_exclusions": "Dieta: {diet} | Exclusões: {exclusions}",
        "summary": "Resumo",
        "col_rank": "Ranking",
        "col_food": "Alimento integral (simples)",
        "col_per100g": "mg por 100g",
        "col_required_grams": "gramas para corresponder",
        "col_grams_for_rda": "Gramas para RDA",
        "col_practicality": "Praticabilidade",
        "col_benefits": "Benefícios adicionais à saúde",
        "col_refs": "Refs",
        "non_food": "NÃO-ALIMENTAR",
        "non_food_descr": "Fonte não alimentar (por exemplo, luz solar)",
        "practical": "✔ Prático",
        "large": "⚠ Grande",
        "impractical": "— Impraticável",
        "final_recommendation_prefix": "Recomendação final: Substitua o suplemento por",
        "no_valid_matches": "Nenhuma correspondência de alimento integral válida encontrada para este micronutriente.",
        "back_to_diet": "← Voltar para a dieta",
        "rec_practical": "Recomendação: Coma aproximadamente {grams} g de {food} para corresponder a {dose} mg.",
        "rec_large": "Recomendação: {grams} g é muito—distribua as porções ou combine 2-3 alimentos.",
        "rec_impractical": "Recomendação: {grams} g é impraticável—considere suplementação direcionada para este micronutriente.",
        "verdict_all_practical": "A substituição por alimentos integrais parece prática para todos os micronutrientes listados.",
        "verdict_mixed": "Alguns micronutrientes podem exigir porções grandes; considere uma abordagem mista (alimentos integrais + suplemento direcionado).",
    },
    "ur": {
        "select_language": "ڈسپلے کی زبان منتخب کریں:",
        "continue": "جاری رکھیں",
        "next": "اگلا →",
        "back": "← واپس",
        "upload_label": "لیبل تصویر اپ لوڈ کریں",
        "try_fetch": "مقامی طور پر URL حاصل کرنے کی کوشش کریں (اختیاری)",
        "debug_mode": "ڈیبگ موڈ",
        "analyze": "تجزیہ کریں →",
        "starting_upload": "اپ لوڈ شروع کیا جا رہا ہے...",
        "complete": "مکمل!",
        "input_error": "ان پٹ کی خرابی",
        "paste_prompt": "سپلیمنٹ حقائق متن چسپاں کریں یا پروڈکٹ URL چسپاں کریں یا لیبل تصویر اپ لوڈ کریں:",
        "analyzing": "تجزیہ جاری ہے…",
        "starting": "شروع…",
        "progress_log": "ترقی کا ریکارڈ:",
        "cancel": "منسوخ کریں",
        "back_to_input": "← ان پٹ پر واپس جائیں",
        "ocr_error_title": "OCR خرابی",
        "retry_prompt": "دوبارہ کوشش کریں?",
        "ocr_fallback_reduced": "[کم معیار] کریڈٹ کی حد کی وجہ سے معیشت کی موڈ استعمال کی جا رہی ہے۔",
        "ocr_fallback_local": "[مقامی OCR] ڈیوائس پر Tesseract استعمال کیا جا رہا ہے (مفت)۔",
        "ocr_all_failed": "OCR ناکام: Tesseract انسٹال کریں یا OpenRouter کریڈٹ اپ ڈیٹ کریں۔",
        "no_micronutrients": "کوئی مائکرونیوٹرینٹ نہیں ملا۔",
        "why_exists": "یہ ایپ کیوں موجود ہے\n\nمکمل غذائیں فراہم کرتی ہیں:\n• بہتر جذب (غذائی تعاون)\n• قدرتی کو فیکٹرز\n• فائبر اور فائٹونیوٹرینٹس\n• کم زہریلا پن کا خطرہ\n• بہتر لاگت کی کارکردگی\n\nبڑی سپلیمنٹ کمپنیاں اکثر مارکیٹنگ الجھن سے فائدہ اٹھاتی ہیں۔\nyeh tool asaan karta hai haqeeqi khaan ke saat saboot par mabni badlao.",
        "diet_exclusions_title": "غذا اور استثنیٰ",
        "select_diet_type": "غذا کی قسم منتخب کریں (غذاوں کو فلٹر کرنے کے لیے استعمال):",
        "exclude_foods_label": "غذاوں کو خارج کریں (کوما سے الگ): مثلاً آنتیں، جگر",
        "input_title": "ان پٹ",
        "cancelling": "منسوخ کیا جا رہا ہے…",
        "ocr_failed": "OCR ناکام",
        "supplement_dose": "سپلیمنٹ کی خوراک: {dose} mg",
        "debug_label": "ڈیبگ",
        "input_used": "استعمال شدہ ان پٹ (منقطع):",
        "raw_parse_output": "خام تجزیہ آؤٹ پٹ (منقطع):",
        "app_header": "سپلیمنٹ بمقابلہ مکمل غذائیں (ٹاپ 5)",
        "diet_exclusions": "غذا: {diet} | استثنیٰ: {exclusions}",
        "summary": "خلاصہ",
        "col_rank": "درجہ",
        "col_food": "مکمل غذا (سادہ)",
        "col_per100g": "فی 100g ملی گرام",
        "col_required_grams": "گرام ملنے کے لیے",
        "col_grams_for_rda": "RDA کے لیے گرام",
        "col_practicality": "عملی صلاحیت",
        "col_benefits": "صحت کے اضافی فوائل",
        "col_refs": "حوالہ",
        "non_food": "غیر غذائی",
        "non_food_descr": "غیر غذائی ذریعہ (مثلاً سورج کی روشنی)",
        "practical": "✔ عملی",
        "large": "⚠ بڑا",
        "impractical": "— غیر عملی",
        "final_recommendation_prefix": "حتمی سفارش: سپلیمنٹ کو بدل دیں",
        "no_valid_matches": "اس مائکرونیوٹرینٹ کے لیے کوئی درست مکمل غذا کا میل نہیں ملا۔",
        "back_to_diet": "← غذا پر واپس جائیں",
        "rec_practical": "سفارش: {dose} mg کے مطابق {food} کے تقریباً {grams} g کھائیں۔",
        "rec_large": "سفارش: {grams} g بہت زیادہ ہے—حصے تقسیم کریں یا 2-3 غذائیں ملائیں۔",
        "rec_impractical": "سفارش: {grams} g عملی نہیں ہے—اس مائکرونیوٹرینٹ کے لیے ہدف شدہ سپلیمنٹشن پر غور کریں۔",
        "verdict_all_practical": "مکمل غذا کی متبادل تمام درج بیان شدہ مائکرونیوٹرینٹس کے لیے عملی لگتی ہے۔",
        "verdict_mixed": "کچھ مائکرونیوٹرینٹس کو بڑے حصے کی ضرورت ہو سکتی ہے؛ ملی جلی حکمت عملی پر غور کریں (مکمل غذائیں + ہدف شدہ سپلیمنٹ)۔",
    }
}

def _t_for(lang: str, key: str, fallback: str = None) -> str:
    return TRANSLATIONS.get(lang, TRANSLATIONS["en"]).get(key, fallback or key)


# Language options (top 10 spoken languages by total speakers)
LANG_OPTIONS = {
    "en": "English",
    "zh": "中文",
    "hi": "हिन्दी",
    "es": "Español",
    "fr": "Français",
    "ar": "العربية",
    "bn": "বাংলা",
    "ru": "Русский",
    "pt": "Português",
    "ur": "اردو",
    "de": "Deutsch",
}

_LANG_NAME_TO_CODE = {v: k for k, v in LANG_OPTIONS.items()}

# Micronutrient health benefits database
MICRONUTRIENT_BENEFITS = {
    "vitamin a": "Essential for vision, immune function, and skin health",
    "vitamin b1": "Supports energy metabolism and nervous system function",
    "vitamin b2": "Aids energy production and antioxidant protection",
    "vitamin b3": "Important for DNA repair and metabolic health",
    "vitamin b5": "Supports hormone and cholesterol production",
    "vitamin b6": "Crucial for brain development and immune function",
    "vitamin b7": "Promotes healthy hair, skin, and nail growth",
    "vitamin b9": "Essential for DNA synthesis and fetal development",
    "vitamin b12": "Critical for nerve function and red blood cell formation",
    "vitamin c": "Boosts immune system and aids collagen synthesis",
    "vitamin d": "Regulates calcium absorption and bone health",
    "vitamin e": "Powerful antioxidant protecting cells from damage",
    "vitamin k": "Essential for blood clotting and bone mineralization",
    "calcium": "Builds and maintains strong bones and teeth",
    "chromium": "Helps regulate blood sugar and insulin sensitivity",
    "copper": "Important for iron metabolism and connective tissue",
    "fluoride": "Strengthens tooth enamel and prevents decay",
    "iodine": "Vital for thyroid hormone production",
    "iron": "Transports oxygen throughout the body",
    "magnesium": "Supports muscle function and energy production",
    "manganese": "Important for bone formation and metabolism",
    "molybdenum": "Aids in break down of amino acids",
    "nickel": "Supports enzyme function and mineral absorption",
    "phosphorus": "Works with calcium to build strong bones",
    "potassium": "Regulates heart rhythm and blood pressure",
    "selenium": "Protects cells and supports thyroid health",
    "sodium": "Maintains fluid balance and nerve function",
    "zinc": "Supports immune function and wound healing",
}

def clean_json(text: str) -> str:
    """Remove common markdown wrappers from model output."""
    if not isinstance(text, str):
        return ""
    text = re.sub(r"```json", "", text, flags=re.IGNORECASE)
    text = re.sub(r"```", "", text)
    return text.strip()


def safe_float(x):
    try:
        return float(x)
    except:
        return None


def normalize_space(s: str) -> str:
    s = s or ""
    s = re.sub(r"\s+", " ", s).strip()
    return s


def is_url(s: str) -> bool:
    s = (s or "").strip().lower()
    return s.startswith("http://") or s.startswith("https://")


def extract_text_from_image(path: str) -> str:
    # legacy wrapper
    return extract_text_from_image_openrouter(path)


def normalize_nutrient_name(s: str) -> str:
    s = (s or "").lower()
    s = s.replace(",", " ").replace("(", " ").replace(")", " ")
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def open_usda_link(fdc_id):
    # Official FDC food page (works for most ids)
    if not fdc_id:
        return
    url = f"https://fdc.nal.usda.gov/fdc-app.html#/food-details/{fdc_id}/nutrients"
    webbrowser.open(url)


def pubmed_search_link(food_name: str):
    q = quote_plus(f"{food_name} health benefits")
    return f"https://pubmed.ncbi.nlm.nih.gov/?term={q}"

def _strip_html_to_text(html: str) -> str:
    """Convert HTML into normalized plain text."""
    if not html:
        return ""
    txt = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
    txt = re.sub(r"<style[\s\S]*?</style>", " ", txt, flags=re.IGNORECASE)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"&nbsp;", " ", txt)
    txt = re.sub(r"&amp;", "&", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def fetch_url_text(url: str, timeout: int = 15) -> str:
    """Best-effort text fetch for product URLs; falls back to jina.ai proxy."""
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    best = ""

    try:
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        if r.status_code == 200 and r.text:
            best = _strip_html_to_text(r.text)
    except Exception:
        pass

    blocked_markers = ["enable javascript", "access denied", "are you a human", "captcha", "cloudflare"]
    looks_blocked = (len(best) < 500) or any(marker in best.lower() for marker in blocked_markers)

    if looks_blocked:
        try:
            proxy_url = "https://r.jina.ai/" + url
            r2 = requests.get(proxy_url, headers=headers, timeout=timeout, allow_redirects=True)
            if r2.status_code == 200 and r2.text:
                alt = r2.text.strip()
                if len(alt) > len(best):
                    best = alt
        except Exception:
            pass

    return best


def extract_product_name_from_url(url: str) -> str:
    """Extract product name from URL. E.g., 'Magnesium-Complex' from magnesium-complex-417-mg URL."""
    try:
        # Get the path part of URL
        from urllib.parse import urlparse
        path = urlparse(url).path
        # Get last path segment before params/query
        product_slug = path.split('/')[-1] if path else ""
        # Remove query params and anchors
        product_slug = product_slug.split('?')[0].split('#')[0]
        # Replace hyphens with spaces for readability
        product_name = product_slug.replace('-', ' ').replace('_', ' ')
        # Keep only first few meaningful words (avoid product IDs, codes, etc.)
        words = [w for w in product_name.split() if not w.isdigit() and len(w) > 2]
        return ' '.join(words[:4])  # Max 4 words
    except:
        return ""


def llm_extract_supplement_facts_from_page(page_text: str, product_name: str = "") -> str:
    """Use the LLM to pull out only the supplement facts / nutrient table text from a messy webpage.
    
    Args:
        page_text: Full page text from fetched webpage
        product_name: Expected product name (e.g., "Magnesium Complex"). Helps identify correct facts table if page has multiple products.
    """
    page_text = (page_text or "").strip()
    if not page_text:
        return ""

    # Keep it tight to avoid token blowups
    page_text = page_text[:MAX_INPUT_CHARS]

    system = (
        "Extract ONLY the official supplement facts / nutrition label from product pages.\n"
        "REMOVE all marketing language, comparisons, and disclaimers.\n"
        "Return ONLY nutrient names + amounts + units clearly listed in the nutrition table.\n"
        "Do not include nutrients mentioned in marketing text or comparisons. Compact output."
    )

    product_hint = f"\nFOCUS ON PRODUCT: {product_name}\nIf multiple supplement tables exist on this page, extract ONLY the facts table for '{product_name}'." if product_name else ""
    
    user = (
        "Extract ONLY the official supplement facts table from this page.\n"
        "CRITICAL: Ignore any nutrient mentioned outside a nutrition table or label.\n"
        "Look for standard nutrition label format (nutrient name, amount, unit).\n"
        "Remove all marketing language like 'without X' or 'compared to X'.\n"
        "If you find a nutrition table, extract only what's in it.\n"
        "If multiple tables exist, choose the one that matches the main/featured product.\n"
        f"{product_hint}"
        "If you cannot find any nutrient table, return the original text unchanged.\n\n"
        f"PAGE_TEXT:\n{page_text}"
    )

    out = call_llm(system, user, temperature=0.0, timeout=60)
    
    # Strip economy mode prefix if present
    if out.startswith("[Economy Mode]\n"):
        out = out.replace("[Economy Mode]\n", "", 1)
    
    out = (out or "").strip()
    if not out:
        return page_text

    # If the model refuses / errors, keep original
    if "API ERROR" in out or "CONNECTION ERROR" in out or "INVALID JSON" in out:
        return page_text

    # If output is suspiciously short, keep original
    if len(out) < 80:
        return page_text

    return out




# ============================================================
# OPENROUTER VISION OCR WITH INTELLIGENT FALLBACK
# ============================================================

def _try_tesseract_ocr(path: str) -> str:
    """Fallback: Try local Tesseract OCR (free, on-device)."""
    try:
        from PIL import Image
        import pytesseract
        
        img = Image.open(path)
        text = pytesseract.image_to_string(img)
        if text.strip():
            return text
    except ImportError:
        pass
    except Exception:
        pass
    return None


def _try_reduced_token_ocr(path: str, api_key: str, system_text: str, max_tokens: int = 8000) -> str:
    """Fallback Tier 1: Retry OpenRouter with reduced max_tokens."""
    try:
        with open(path, "rb") as f:
            img_bytes = f.read()
    except:
        return None

    mime, _ = mimetypes.guess_type(path)
    if not mime:
        mime = "image/jpeg"

    b64 = base64.b64encode(img_bytes).decode("utf-8")
    data_url = f"data:{mime};base64,{b64}"

    user_content = [
        {"type": "text", "text": "Extract all readable text from supplement label image."},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]

    payload = {
        "model": OPENROUTER_MODEL,
        "temperature": 0,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_content},
        ],
    }

    try:
        headers = openrouter_headers(api_key)
        r = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=60)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
    except:
        pass
    
    return None


def extract_text_from_image_openrouter(path: str) -> str:
    """OCR a label image with intelligent fallback chain.
    Tier 1: Full OpenRouter with full tokens
    Tier 2: OpenRouter with reduced tokens (handles credit limits)
    Tier 3: Local Tesseract (free, no credits needed)
    """
    keys = get_openrouter_keys()
    if not keys:
        return "OCR ERROR: Missing OPENROUTER_API_KEY."

    try:
        with open(path, "rb") as f:
            img_bytes = f.read()
    except Exception as e:
        return f"OCR ERROR: Cannot read image: {e}"

    mime, _ = mimetypes.guess_type(path)
    if not mime:
        mime = "image/jpeg"

    b64 = base64.b64encode(img_bytes).decode("utf-8")
    data_url = f"data:{mime};base64,{b64}"

    system_text = (
        "You are a precise OCR engine for supplement labels. "
        "Return ONLY the extracted text. Preserve line breaks. "
        "Do not add explanations."
    )

    user_content = [
        {"type": "text", "text": "Extract all readable text from this supplement label image."},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]

    # TIER 1: Try full quality API call
    payload = {
        "model": OPENROUTER_MODEL,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_content},
        ],
    }

    attempts = 3
    last_error = None

    for api_key in keys:
        headers = openrouter_headers(api_key)
        backoff = 1.0
        tier1_error = None

        for _ in range(attempts):
            try:
                r = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=90)
            except Exception as e:
                tier1_error = e
                time.sleep(backoff)
                backoff *= 2
                continue

            # Success
            if r.status_code == 200:
                try:
                    return r.json()["choices"][0]["message"]["content"]
                except Exception:
                    return f"OCR INVALID JSON: {r.text[:100]}"

            # 402: Insufficient credits—try fallback
            if r.status_code == 402:
                tier1_error = f"402: Insufficient credits. Trying fallback OCR..."
                break

            # Rotate on auth/limit
            if r.status_code in [401, 403, 429]:
                tier1_error = f"OCR API ERROR {r.status_code}"
                break

            # Other errors
            tier1_error = f"OCR API ERROR {r.status_code}"
            break

        # TIER 2: Retry with reduced max_tokens (cheaper, often works)
        if tier1_error:
            reduced_result = _try_reduced_token_ocr(path, api_key, system_text, max_tokens=ECONOMY_MAX_TOKENS)
            if reduced_result and not reduced_result.startswith("OCR"):
                return reduced_result
            last_error = tier1_error
            continue

    # TIER 3: Try local Tesseract (free, instant)
    tesseract_result = _try_tesseract_ocr(path)
    if tesseract_result:
        return f"[Local OCR]\n{tesseract_result}"
    
    # All tiers failed - provide helpful message
    final_msg = (
        "OCR FAILED: OpenRouter credits exhausted and Tesseract unavailable.\n\n"
        "To fix:\n"
        "1. Install Tesseract: https://github.com/UB-Mannheim/tesseract/wiki\n"
        "2. Or upgrade OpenRouter: https://openrouter.ai/settings/credits\n"
        "3. Or paste the text manually (easier!)"
    )
    return last_error or final_msg


async def async_extract_text_from_image_openrouter(path: str) -> str:
    """Async OCR with intelligent fallback chain (same as sync version)."""
    keys = get_openrouter_keys()
    if not keys:
        return "OCR ERROR: Missing OPENROUTER_API_KEY."

    try:
        with open(path, "rb") as f:
            img_bytes = f.read()
    except Exception as e:
        return f"OCR ERROR: Cannot read image: {e}"

    mime, _ = mimetypes.guess_type(path)
    if not mime:
        mime = "image/jpeg"

    b64 = base64.b64encode(img_bytes).decode("utf-8")
    data_url = f"data:{mime};base64,{b64}"

    system_text = (
        "You are a precise OCR engine for supplement labels. "
        "Return ONLY the extracted text. Preserve line breaks. "
        "Do not add explanations."
    )

    user_content = [
        {"type": "text", "text": "Extract all readable text from supplement label image."},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]

    # TIER 1: Try full quality API call
    payload = {
        "model": OPENROUTER_MODEL,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_content},
        ],
    }

    attempts = 3
    last_error = None

    async with httpx.AsyncClient() as client:
        for api_key in keys:
            headers = openrouter_headers(api_key)
            backoff = 1.0
            tier1_error = None

            for _ in range(attempts):
                try:
                    r = await client.post(OPENROUTER_URL, headers=headers, json=payload, timeout=90.0)
                except Exception as e:
                    last_error = f"OCR CONNECTION ERROR: {e}"
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue

                # Success
                if r.status_code == 200:
                    try:
                        return r.json()["choices"][0]["message"]["content"]
                    except Exception:
                        return f"OCR INVALID JSON: {r.text[:100]}"

                # 402: Insufficient credits—try fallback
                if r.status_code == 402:
                    tier1_error = f"402: Insufficient credits. Trying fallback OCR..."
                    break

                # Rotate on auth/limit
                if r.status_code in [401, 403, 429]:
                    tier1_error = f"OCR API ERROR {r.status_code}"
                    break

                # Other errors
                tier1_error = f"OCR API ERROR {r.status_code}"
                break

            # TIER 2: Sync fallback with reduced tokens
            if tier1_error:
                reduced_result = _try_reduced_token_ocr(path, api_key, system_text, max_tokens=ECONOMY_MAX_TOKENS)
                if reduced_result and not reduced_result.startswith("OCR"):
                    return reduced_result
                last_error = tier1_error
                continue

    # TIER 3: Try local Tesseract (free, instant)
    tesseract_result = _try_tesseract_ocr(path)
    if tesseract_result:
        return f"[Local OCR]\n{tesseract_result}"
    
    # All tiers failed - provide helpful message
    final_msg = (
        "OCR FAILED: OpenRouter credits exhausted and Tesseract unavailable.\n\n"
        "To fix:\n"
        "1. Install Tesseract: https://github.com/UB-Mannheim/tesseract/wiki\n"
        "2. Or upgrade OpenRouter: https://openrouter.ai/settings/credits\n"
        "3. Or paste the text manually (easier!)"
    )
    return last_error or final_msg

# ============================================================
# DIET / FILTERING
# ============================================================

DIET_RESTRICTIONS = {
    "Omnivore": [],
    "Vegetarian": ["beef", "pork", "chicken", "fish", "salmon", "tuna", "shrimp", "lamb", "turkey", "bacon"],
    "Vegan": ["beef", "pork", "chicken", "fish", "salmon", "tuna", "shrimp", "lamb", "turkey", "bacon",
              "egg", "milk", "yogurt", "cheese", "honey", "whey", "casein"],
    "Halal": ["pork", "bacon"],
    "Kosher": ["pork", "bacon", "shellfish", "shrimp", "lobster", "crab"],
    "Pescatarian": ["beef", "pork", "chicken", "lamb", "turkey", "bacon"],
    "Dairy-Free": ["milk", "yogurt", "cheese", "whey", "casein", "butter"],
}

# USDA RDAs (Recommended Dietary Allowances) for adults, from NIH
# Units: mcg for some, mg for others
RDAS = {
    # Vitamins
    "vitamin a": 900,      # mcg RAE
    "vitamin c": 90,       # mg
    "vitamin d": 15,       # mcg
    "vitamin e": 15,       # mg
    "vitamin k": 120,      # mcg
    "vitamin b1": 1.2,     # mg
    "vitamin b2": 1.3,     # mg
    "vitamin b6": 1.7,     # mg
    "folate": 400,         # mcg
    "vitamin b12": 2.4,    # mcg
    "biotin": 30,          # mcg
    "pantothenic acid": 5, # mg
    
    # Minerals (mg except where noted)
    "calcium": 1000,       # mg
    "chromium": 35,        # mcg
    "copper": 0.9,         # mg
    "fluoride": 4,         # mg
    "iodine": 150,         # mcg
    "iron": 8,             # mg (adult men), 18 for women - using conservative estimate
    "magnesium": 400,      # mg
    "manganese": 2.3,      # mg
    "molybdenum": 45,      # mcg
    "phosphorus": 700,     # mg
    "potassium": 3400,     # mg
    "selenium": 55,        # mcg
    "sodium": 2300,        # mg (upper limit)
    "zinc": 11,            # mg
}

# Load benefits database from file if exists, else use embedded
FOOD_BENEFITS_DB = {}
try:
    with open('food_benefits.json', 'r') as f:
        FOOD_BENEFITS_DB = json.load(f)
except FileNotFoundError:
    # Fallback to minimal embedded database
    FOOD_BENEFITS_DB = {
        "kiwi": [
            "supports digestive health with fiber",
            "provides antioxidant protection from polyphenols",
            "aids collagen synthesis for skin health"
        ]
    }

ORGAN_KEYWORDS = [
    "liver", "kidney", "heart", "intestine", "tripe",
    "sweetbread", "brain", "tongue"
]

# "Plain, whole, unprocessed" means we filter these out
PROCESSED_KEYWORDS = [
    # multi ingredient / culinary items
    "salad", "dressing", "sauce", "soup", "stew", "curry",
    "pizza", "sandwich", "burger", "pasta", "lasagna",
    "casserole", "omelet", "batter",

    # processed / manufactured
    "juice", "cocktail", "canned", "fortified", "enriched",
    "drink", "beverage", "powder", "extract", "supplement",
    "bar", "shake", "ready-to-drink", "cereal", "instant",
    "mix", "flavored", "candied", "syrup", "concentrate",
    "puree", "spread", "snack", "snacks",

    # spice / seasoning
    "spice", "seasoning", "blend", "chili powder",
    "paprika powder", "garlic powder", "onion powder"
]


# Food group aliases for common exclusion categories in multiple languages
FOOD_GROUP_KEYWORDS = {
    # Legumes/pulses + seeds (German: Hülsenfrüchte, Spanish: Legumbres, French: Légumineuses)
    # Seeds nutritionally classified as legumes/pulses (plant-based proteins)
    "legume": ["bean", "lentil", "pea", "chickpea", "pulse", "legume", "seed", "sunflower", "pumpkin", "sesame", "flax", "chia"],
    "pulse": ["bean", "lentil", "pea", "chickpea", "pulse", "legume", "seed", "sunflower", "pumpkin", "sesame", "flax", "chia"],
    "bean": ["bean", "lentil", "pea", "chickpea", "pulse", "legume", "seed", "sunflower", "pumpkin", "sesame", "flax", "chia"],
    "lentil": ["bean", "lentil", "pea", "chickpea", "pulse", "legume", "seed", "sunflower", "pumpkin", "sesame", "flax", "chia"],
    "pea": ["bean", "lentil", "pea", "chickpea", "pulse", "legume", "seed", "sunflower", "pumpkin", "sesame", "flax", "chia"],
    "chickpea": ["bean", "lentil", "pea", "chickpea", "pulse", "legume", "seed", "sunflower", "pumpkin", "sesame", "flax", "chia"],
    "huelsenfruechte": ["bean", "lentil", "pea", "chickpea", "pulse", "legume", "seed", "sunflower", "pumpkin", "sesame", "flax", "chia"],
    "hülsenfrüchte": ["bean", "lentil", "pea", "chickpea", "pulse", "legume", "seed", "sunflower", "pumpkin", "sesame", "flax", "chia"],
    "legumbres": ["bean", "lentil", "pea", "chickpea", "pulse", "legume", "seed", "sunflower", "pumpkin", "sesame", "flax", "chia"],
    "légumineuses": ["bean", "lentil", "pea", "chickpea", "pulse", "legume", "seed", "sunflower", "pumpkin", "sesame", "flax", "chia"],
    "dal": ["bean", "lentil", "pea", "chickpea", "pulse", "legume", "seed", "sunflower", "pumpkin", "sesame", "flax", "chia"],
    
    # Special: standalone seed exclusion maps to same expanded list
    "seed": ["bean", "lentil", "pea", "chickpea", "pulse", "legume", "seed", "sunflower", "pumpkin", "sesame", "flax", "chia"],
    "seeds": ["bean", "lentil", "pea", "chickpea", "pulse", "legume", "seed", "sunflower", "pumpkin", "sesame", "flax", "chia"],
    "samen": ["bean", "lentil", "pea", "chickpea", "pulse", "legume", "seed", "sunflower", "pumpkin", "sesame", "flax", "chia"],
    "kerne": ["bean", "lentil", "pea", "chickpea", "pulse", "legume", "seed", "sunflower", "pumpkin", "sesame", "flax", "chia"],
    
    # Grains (German: Getreide, Spanish: Cereales, French: Céréales)
    "grain": ["grain", "wheat", "rice", "oat", "barley", "corn", "rye", "millet", "spelt"],
    "grains": ["grain", "wheat", "rice", "oat", "barley", "corn", "rye", "millet", "spelt"],
    "wheat": ["grain", "wheat", "rice", "oat", "barley", "corn", "rye", "millet", "spelt"],
    "rice": ["grain", "wheat", "rice", "oat", "barley", "corn", "rye", "millet", "spelt"],
    "oat": ["grain", "wheat", "rice", "oat", "barley", "corn", "rye", "millet", "spelt"],
    "barley": ["grain", "wheat", "rice", "oat", "barley", "corn", "rye", "millet", "spelt"],
    "corn": ["grain", "wheat", "rice", "oat", "barley", "corn", "rye", "millet", "spelt"],
    "getreide": ["grain", "wheat", "rice", "oat", "barley", "corn", "rye", "millet", "spelt"],
    "cereales": ["grain", "wheat", "rice", "oat", "barley", "corn", "rye", "millet", "spelt"],
    "céréale": ["grain", "wheat", "rice", "oat", "barley", "corn", "rye", "millet", "spelt"],
    
    # Fruits (German: Obst, Spanish: Frutas, French: Fruits)
    "fruit": ["apple", "banana", "orange", "lemon", "lime", "grapefruit", "berry", "blueberry", "strawberry", "raspberry", "blackberry", "watermelon", "melon", "cantaloupe", "kiwi", "mango", "pineapple", "peach", "plum", "apricot", "grape", "papaya", "coconut", "avocado", "fig", "date", "persimmon", "cherry", "tangerine", "mandarin", "clementine", "pomegranate"],
    "fruits": ["apple", "banana", "orange", "lemon", "lime", "grapefruit", "berry", "blueberry", "strawberry", "raspberry", "blackberry", "watermelon", "melon", "cantaloupe", "kiwi", "mango", "pineapple", "peach", "plum", "apricot", "grape", "papaya", "coconut", "avocado", "fig", "date", "persimmon", "cherry", "tangerine", "mandarin", "clementine", "pomegranate"],
    "obst": ["apple", "banana", "orange", "lemon", "lime", "grapefruit", "berry", "blueberry", "strawberry", "raspberry", "blackberry", "watermelon", "melon", "cantaloupe", "kiwi", "mango", "pineapple", "peach", "plum", "apricot", "grape", "papaya", "coconut", "avocado", "fig", "date", "persimmon", "cherry", "tangerine", "mandarin", "clementine", "pomegranate"],
    "frutas": ["apple", "banana", "orange", "lemon", "lime", "grapefruit", "berry", "blueberry", "strawberry", "raspberry", "blackberry", "watermelon", "melon", "cantaloupe", "kiwi", "mango", "pineapple", "peach", "plum", "apricot", "grape", "papaya", "coconut", "avocado", "fig", "date", "persimmon", "cherry", "tangerine", "mandarin", "clementine", "pomegranate"],
    
    # Vegetables (German: Gemüse, Spanish: Verduras, French: Légumes)
    "vegetable": ["carrot", "broccoli", "cauliflower", "spinach", "kale", "lettuce", "cabbage", "cucumber", "tomato", "pepper", "eggplant", "zucchini", "squash", "pumpkin", "sweet potato", "beet", "radish", "turnip", "onion", "garlic", "leek", "celery", "asparagus", "green bean", "mushroom", "artichoke", "parsnip", "parsley", "chard", "arugula"],
    "vegetables": ["carrot", "broccoli", "cauliflower", "spinach", "kale", "lettuce", "cabbage", "cucumber", "tomato", "pepper", "eggplant", "zucchini", "squash", "pumpkin", "sweet potato", "beet", "radish", "turnip", "onion", "garlic", "leek", "celery", "asparagus", "green bean", "mushroom", "artichoke", "parsnip", "parsley", "chard", "arugula"],
    "veggie": ["carrot", "broccoli", "cauliflower", "spinach", "kale", "lettuce", "cabbage", "cucumber", "tomato", "pepper", "eggplant", "zucchini", "squash", "pumpkin", "sweet potato", "beet", "radish", "turnip", "onion", "garlic", "leek", "celery", "asparagus", "green bean", "mushroom", "artichoke", "parsnip", "parsley", "chard", "arugula"],
    "veggies": ["carrot", "broccoli", "cauliflower", "spinach", "kale", "lettuce", "cabbage", "cucumber", "tomato", "pepper", "eggplant", "zucchini", "squash", "pumpkin", "sweet potato", "beet", "radish", "turnip", "onion", "garlic", "leek", "celery", "asparagus", "green bean", "mushroom", "artichoke", "parsnip", "parsley", "chard", "arugula"],
    "veg": ["carrot", "broccoli", "cauliflower", "spinach", "kale", "lettuce", "cabbage", "cucumber", "tomato", "pepper", "eggplant", "zucchini", "squash", "pumpkin", "sweet potato", "beet", "radish", "turnip", "onion", "garlic", "leek", "celery", "asparagus", "green bean", "mushroom", "artichoke", "parsnip", "parsley", "chard", "arugula"],
    "gemüse": ["carrot", "broccoli", "cauliflower", "spinach", "kale", "lettuce", "cabbage", "cucumber", "tomato", "pepper", "eggplant", "zucchini", "squash", "pumpkin", "sweet potato", "beet", "radish", "turnip", "onion", "garlic", "leek", "celery", "asparagus", "green bean", "mushroom", "artichoke", "parsnip", "parsley", "chard", "arugula"],
    "verduras": ["carrot", "broccoli", "cauliflower", "spinach", "kale", "lettuce", "cabbage", "cucumber", "tomato", "pepper", "eggplant", "zucchini", "squash", "pumpkin", "sweet potato", "beet", "radish", "turnip", "onion", "garlic", "leek", "celery", "asparagus", "green bean", "mushroom", "artichoke", "parsnip", "parsley", "chard", "arugula"],
    
    # Nuts
    "nut": ["almond", "walnut", "cashew", "pecan", "pistachio", "hazelnut", "macadamia", "brazil nut", "pine nut", "chestnut"],
    "nuts": ["almond", "walnut", "cashew", "pecan", "pistachio", "hazelnut", "macadamia", "brazil nut", "pine nut", "chestnut"],
    
    # Red Meat/Beef
    "beef": ["beef", "cow", "steak", "ground beef", "veal"],
    "meat": ["beef", "pork", "lamb", "mutton", "venison", "duck", "chicken", "turkey", "rabbit"],
    "red meat": ["beef", "cow", "steak", "ground beef", "veal", "pork", "lamb", "mutton"],
    "poultry": ["chicken", "turkey", "duck", "pheasant"],
    "chicken": ["chicken"],
    "turkey": ["turkey"],
    "pork": ["pork", "ham", "bacon", "sausage"],
    "lamb": ["lamb", "mutton"],
    "fleisch": ["beef", "pork", "lamb", "mutton", "venison", "duck", "chicken", "turkey", "rabbit"],
    "carne": ["beef", "pork", "lamb", "mutton", "venison", "duck", "chicken", "turkey", "rabbit"],
    
    # Fish/Seafood
    "fish": ["salmon", "tuna", "cod", "tilapia", "anchovy", "sardine", "herring", "trout", "halibut", "mackerel", "sea bass"],
    "seafood": ["salmon", "tuna", "cod", "tilapia", "anchovy", "sardine", "herring", "trout", "halibut", "shrimp", "crab", "lobster", "oyster", "mussel", "clam", "squid", "scallop"],
    "shellfish": ["shrimp", "crab", "lobster", "oyster", "mussel", "clam", "scallop"],
    "pescado": ["salmon", "tuna", "cod", "tilapia", "anchovy", "sardine", "herring", "trout", "halibut", "mackerel", "sea bass"],
    
    # Dairy
    "dairy": ["milk", "cheese", "yogurt", "cream", "butter", "whey", "casein", "cottage cheese", "mozzarella", "cheddar"],
    "milk": ["milk", "cheese", "yogurt", "cream", "butter", "whey", "casein", "cottage cheese"],
    "cheese": ["cheese", "cheddar", "mozzarella", "parmesan", "feta", "gouda"],
    "lactose": ["milk", "cheese", "yogurt", "cream", "butter", "whey", "casein"],
    
    # Nightshades (German: Nachtschattengewächse)
    "nightshade": ["tomato", "potato", "pepper", "eggplant", "nightshade"],
    "nachtschatten": ["tomato", "potato", "pepper", "eggplant", "nightshade"],
    "solanum": ["tomato", "potato", "pepper", "eggplant", "nightshade"],
}

EXCLUSION_PREFIXES = [
    "no ",
    "without ",
    "avoid ",
    "avoidance of ",
    "dont like ",
    "don't like ",
    "do not like ",
    "do not eat ",
    "not eating ",
    "exclude ",
    "excluding ",
    "allergic to ",
    "allergy to ",
    "intolerant to ",
    "intolerance to ",
    "free of ",
]

def normalize_exclusion_term(term: str) -> str:
    """Normalize a user exclusion term for consistent matching."""
    if not term:
        return ""
    cleaned = term.strip().lower()
    for prefix in EXCLUSION_PREFIXES:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
            break
    cleaned = cleaned.strip(" .;:!?\t")
    return " ".join(cleaned.split())

def parse_exclusion_input(raw: str) -> list[str]:
    """Parse raw exclusions into normalized tokens (handles commas, 'and', 'or')."""
    if not raw:
        return []
    normalized = raw.replace(";", ",")
    parts = []
    for piece in normalized.split(","):
        p = piece.strip().lower()
        if not p:
            continue
        # split on simple conjunctions
        if " or " in p:
            parts.extend([x.strip() for x in p.split(" or ") if x.strip()])
        elif " and " in p:
            parts.extend([x.strip() for x in p.split(" and ") if x.strip()])
        else:
            parts.append(p)
    cleaned = [normalize_exclusion_term(p) for p in parts]
    return [c for c in cleaned if c]

def expand_exclusion_keywords(exclusions: list[str]) -> list[str]:
    """Expand food group keywords to include all equivalent terms.
    
    Works for ANY user-provided term:
    - If term is a known food group (e.g. 'hülsenfrüchte', 'legume', 'grain'), expand to all foods in that group
    - If term is a custom/specific food (e.g. 'carrots', 'tofu'), keep it as-is for substring matching
    
    Examples:
    - User types 'hülsenfrüchte' -> expands to ['bean', 'lentil', 'pea', 'chickpea', 'pulse', 'legume', 'hülsenfrüchte']
    - User types 'carrots' -> stays as ['carrots']
    - User types 'hülsenfrüchte, carrots' -> expands to all legumes + ['carrots']
    """
    expanded = set()
    
    for excl in exclusions:
        excl_lower = excl.strip().lower()
        
        # Always include the original term (user may have typed something specific)
        expanded.add(excl_lower)
        
        # If this term is a known food group, add all foods in that group
        if excl_lower in FOOD_GROUP_KEYWORDS:
            expanded.update(FOOD_GROUP_KEYWORDS[excl_lower])
    
    return list(expanded)

def is_forbidden_food(desc_lower: str, diet: str, exclusions: list[str]) -> bool:
    """Check if a food should be excluded based on diet restrictions and user exclusions."""
    desc_lower = desc_lower or ""
    diet_forbidden = DIET_RESTRICTIONS.get(diet, [])
    expanded_exclusions = expand_exclusion_keywords(exclusions) if exclusions else []
    forbidden = diet_forbidden + expanded_exclusions

    # Check forbidden terms with word-boundary matching to avoid accidental substring hits
    for term in forbidden:
        if not term:
            continue
        if " " in term or "-" in term:
            if term in desc_lower:
                return True
        else:
            if re.search(r"\b" + re.escape(term) + r"\b", desc_lower):
                return True
    if any(x in desc_lower for x in ORGAN_KEYWORDS):
        return True
    if any(x in desc_lower for x in PROCESSED_KEYWORDS):
        return True
    return False

def is_single_ingredient_food(desc: str) -> bool:
    return False

def is_single_ingredient_food(desc: str) -> bool:
    desc = (desc or "").lower()

    # reject if obviously multi-ingredient
    if "," in desc and any(word in desc for word in [
        "with", "and", "in", "type", "style"
    ]):
        return False

    # reject if contains processed keywords
    if any(p in desc for p in PROCESSED_KEYWORDS):
        return False

    return True

def pick_plain_food(foods: list[dict]) -> dict | None:
    """Prefer shortest non-processed description."""
    if not foods:
        return None
    candidates = []
    for f in foods:
        desc = (f.get("description") or "").lower()
        if any(p in desc for p in PROCESSED_KEYWORDS):
            continue
        candidates.append(f)
    if not candidates:
        candidates = foods
    candidates.sort(key=lambda x: len((x.get("description") or "")))
    return candidates[0]


# ============================================================
# OPENROUTER LLM CLIENT (STRICT + RETRIES)
# ============================================================

def call_llm(system_text: str, user_text: str, temperature: float = 0.2, timeout: int = 60, max_tokens: int = None) -> str:
    """Call OpenRouter LLM with automatic fallback to reduced tokens on 402 errors."""
    import hashlib
    cache_key = hashlib.md5(f"{system_text}{user_text}{temperature}".encode()).hexdigest()
    if cache_key in llm_cache:
        return llm_cache[cache_key]

    keys = get_openrouter_keys()
    if not keys:
        return "API ERROR: Missing OPENROUTER_API_KEY. Paste it at the top of the script."

    # Attempt 1: Full request
    payload = {
        "model": OPENROUTER_MODEL,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_text},
        ]
    }
    
    if max_tokens:
        payload["max_tokens"] = max_tokens

    last_error = None

    for api_key in keys:
        headers = openrouter_headers(api_key)
        try:
            r = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=timeout)
        except Exception as e:
            last_error = f"API CONNECTION ERROR:\n{str(e)}"
            continue

        # 402: Insufficient credits → retry with reduced tokens
        if r.status_code == 402:
            if not max_tokens or max_tokens > ECONOMY_MAX_TOKENS:
                affordable = _affordable_max_tokens(r.text)
                payload["max_tokens"] = affordable if affordable else ECONOMY_MAX_TOKENS
                try:
                    r = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=timeout)
                    if r.status_code == 200:
                        try:
                            result = r.json()["choices"][0]["message"]["content"]
                            llm_cache[cache_key] = result
                            return f"[Economy Mode]\n{result}"
                        except Exception:
                            last_error = f"INVALID JSON RESPONSE:\n{r.text}"
                            continue
                except Exception as e:
                    last_error = f"API CONNECTION ERROR (fallback):\n{str(e)}"
                    continue
            last_error = f"API ERROR {r.status_code}:\n{r.text[:500]}"
            continue

        if r.status_code in [401, 403, 429]:
            last_error = f"API ERROR {r.status_code}:\n{r.text[:500]}"
            continue

        if r.status_code != 200:
            return f"API ERROR {r.status_code}:\n{r.text[:500]}"

        try:
            result = r.json()["choices"][0]["message"]["content"]
            llm_cache[cache_key] = result
            return result
        except Exception:
            last_error = f"INVALID JSON RESPONSE:\n{r.text[:500]}"
            continue

    return last_error or "API ERROR: No OpenRouter key succeeded."


async def async_call_llm(system_text: str, user_text: str, temperature: float = 0.2, timeout: int = 60, max_tokens: int = None) -> str:
    """Async LLM call with automatic fallback to reduced tokens on 402 errors."""
    cache_key = hashlib.md5(f"{system_text}{user_text}{temperature}".encode()).hexdigest()
    if cache_key in llm_cache:
        return llm_cache[cache_key]

    keys = get_openrouter_keys()
    if not keys:
        return "API ERROR: Missing OPENROUTER_API_KEY. Paste it at the top of the script."

    # Attempt 1: Full request
    payload = {
        "model": OPENROUTER_MODEL,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_text},
        ]
    }
    
    if max_tokens:
        payload["max_tokens"] = max_tokens

    last_error = None

    async with httpx.AsyncClient() as client:
        for api_key in keys:
            headers = openrouter_headers(api_key)
            try:
                r = await client.post(OPENROUTER_URL, headers=headers, json=payload, timeout=timeout)
            except Exception as e:
                last_error = f"API CONNECTION ERROR:\n{str(e)}"
                continue

            # 402: Insufficient credits → retry with reduced tokens
            if r.status_code == 402:
                if not max_tokens or max_tokens > ECONOMY_MAX_TOKENS:
                    affordable = _affordable_max_tokens(r.text)
                    payload["max_tokens"] = affordable if affordable else ECONOMY_MAX_TOKENS
                    try:
                        r = await client.post(OPENROUTER_URL, headers=headers, json=payload, timeout=timeout)
                        if r.status_code == 200:
                            try:
                                result = r.json()["choices"][0]["message"]["content"]
                                llm_cache[cache_key] = result
                                return f"[Economy Mode]\n{result}"
                            except Exception:
                                last_error = f"INVALID JSON RESPONSE:\n{r.text}"
                                continue
                    except Exception as e:
                        last_error = f"API CONNECTION ERROR (fallback):\n{str(e)}"
                        continue
                last_error = f"API ERROR {r.status_code}:\n{r.text[:500]}"
                continue

            if r.status_code in [401, 403, 429]:
                last_error = f"API ERROR {r.status_code}:\n{r.text[:500]}"
                continue

            if r.status_code != 200:
                return f"API ERROR {r.status_code}:\n{r.text[:500]}"

            try:
                result = r.json()["choices"][0]["message"]["content"]
                llm_cache[cache_key] = result
                return result
            except Exception:
                last_error = f"INVALID JSON RESPONSE:\n{r.text[:500]}"
                continue

    return last_error or "API ERROR: No OpenRouter key succeeded."


def llm_json(system_text: str, user_text: str, retries: int = 2) -> dict | list | None:
    """
    Ask LLM for JSON. If parse fails, try repair once/twice.
    Strips economy mode prefix if present.
    """
    out = call_llm(system_text, user_text, temperature=0.0, timeout=60)
    if "API ERROR" in out or "CONNECTION ERROR" in out or "INVALID JSON" in out:
        return {"__error__": out}

    # Strip [Economy Mode] prefix if present
    if out.startswith("[Economy Mode]\n"):
        out = out.replace("[Economy Mode]\n", "", 1)

    raw = clean_json(out)
    try:
        return json.loads(raw)
    except:
        # repair loop
        for _ in range(retries):
            repair_prompt = (
                "Fix this into STRICT valid JSON only. No markdown. No commentary.\n\n"
                f"BAD_OUTPUT:\n{out}"
            )
            out2 = call_llm(system_text, repair_prompt, temperature=0.0, timeout=60)
            if out2.startswith("[Economy Mode]\n"):
                out2 = out2.replace("[Economy Mode]\n", "", 1)
            raw2 = clean_json(out2)
            try:
                return json.loads(raw2)
            except:
                out = out2
        return {"__error__": f"PARSE ERROR:\n{out}"}


# ============================================================


def generate_benefits_for_food(food_name: str) -> list | dict:
    """
    Generate up to 3 short, evidence-informed health benefit strings for a
    whole food using the OpenRouter LLM. Returns a list of strings or a
    dict with '__error__' on failure.
    """
    if not food_name or not isinstance(food_name, str):
        return {"__error__": "Invalid food name"}

    fn = food_name.strip().lower()

    system = (
        "You are a nutrition researcher. Return STRICT valid JSON (a JSON array)\n"
        "Provide up to 3 concise (4-10 word) health benefit statements for the"
        " whole food named by the user. Each statement should be evidence-informed"
        " and describe an effect or mechanism (e.g., 'supports gut microbiome diversity')."
        " Do NOT include citations, markdown, or any extra text — only a JSON list of strings."
    )

    user = f"Food: {fn}\nRespond with a JSON array of strings (up to 3)."

    out = llm_json(system, user, retries=2)
    return out

# USDA CLIENT + LIGHT CACHING
# ============================================================

LOW_FOOD_FEASIBILITY = {
    "vitamin d": {
        "reason": "Primarily synthesized via sun exposure.",
        "recommendation": "15–30 minutes midday sun exposure (arms/face) OR consider supplementation."
    }
}

_usda_cache = {}

def usda_search_food(query: str, page_size: int = 25) -> list[dict] | None:
    q = (query or "").strip().lower()
    if not q:
        return None

    cache_key = f"{q}_{page_size}"
    if cache_key in usda_cache:
        return usda_cache[cache_key]

    url = "https://api.nal.usda.gov/fdc/v1/foods/search"
    params = {
        "query": query,
        "api_key": USDA_API_KEY,
        "pageSize": page_size,
        "dataType": ["Foundation", "SR Legacy"]
    }

    try:
        r = requests.get(url, params=params, timeout=USDA_TIMEOUT)
    except:
        return None

    if r.status_code != 200:
        return None

    foods = r.json().get("foods", [])
    usda_cache[cache_key] = foods
    return foods


def get_food_category(food_json: dict) -> str:
    """Extract USDA foodCategory with sensible fallback."""
    cat = food_json.get("foodCategory")
    if isinstance(cat, dict):
        cat = cat.get("description") or cat.get("label") or cat.get("name")
    if not cat:
        return "Uncategorized"
    return str(cat)


def extract_nutrients(food_json: dict) -> dict:
    nutrients = {}
    for nutrient in food_json.get("foodNutrients", []):
        name = (nutrient.get("nutrientName", "") or "").lower()
        val = nutrient.get("value", 0) or 0
        unit = (nutrient.get("unitName", "") or "").upper()
        nutrients[f"{name} ({unit})"] = val
    return nutrients


SPECIAL_TERMS = {
    "vitamin a": ["vitamin a", "rae", "retinol", "beta carotene", "carotene"],
    "vitamin d": ["vitamin d", "d2", "d3", "cholecalciferol", "ergocalciferol"],
    "vitamin k": ["vitamin k", "phylloquinone", "menaquinone"],
    "folate": ["folate", "folic acid", "dfe", "dietary folate equivalents"],
    "vitamin b12": ["vitamin b12", "cobalamin"],
    "b12": ["vitamin b12", "cobalamin"],
}

# Deterministic fallback candidates when LLM is unavailable or returns nothing
NUTRIENT_FALLBACK_CANDIDATES = {
    "zinc": [
        "oyster", "beef", "pumpkin seeds", "lentils", "chickpeas",
        "cashews", "cheddar", "eggs", "spinach", "mushrooms"
    ],
    "vitamin c": [
        "red bell pepper", "kiwi", "orange", "strawberries", "broccoli",
        "brussels sprouts", "papaya", "guava", "kale", "pineapple"
    ],
    "magnesium": [
        "pumpkin seeds", "almonds", "spinach", "black beans", "edamame",
        "cashews", "peanut", "oats", "avocado", "dark chocolate"
    ],
    "iron": [
        "lentils", "spinach", "chickpeas", "beef", "pumpkin seeds",
        "quinoa", "tofu", "oysters", "turkey", "beans"
    ],
    "calcium": [
        "yogurt", "milk", "cheddar", "kale", "broccoli",
        "sardines", "tofu", "bok choy", "almonds", "chia seeds"
    ],
    "potassium": [
        "potato", "sweet potato", "white beans", "spinach", "banana",
        "avocado", "tomato", "salmon", "yogurt", "beets"
    ],
    "folate": [
        "lentils", "spinach", "asparagus", "broccoli", "avocado",
        "brussels sprouts", "black beans", "beets", "romaine", "orange"
    ],
    "vitamin a": [
        "sweet potato", "carrot", "spinach", "kale", "butternut squash",
        "red pepper", "cantaloupe", "egg", "liver", "apricot"
    ],
    "vitamin d": [
        "salmon", "sardines", "egg", "mushrooms", "trout",
        "tuna", "cod", "fortified milk", "fortified yogurt", "fortified cereal"
    ],
    "vitamin b12": [
        "clams", "liver", "salmon", "tuna", "beef",
        "eggs", "milk", "yogurt", "cheddar", "trout"
    ],
}

def normalize_special_nutrient_name(name: str) -> str:
    name = name.lower()

    # B vitamins
    if name in ["b1", "vitamin b1"]:
        return "thiamin"
    if name in ["b2", "vitamin b2"]:
        return "riboflavin"
    if name in ["b6", "vitamin b6"]:
        return "vitamin b6"
    if name in ["b12", "vitamin b12"]:
        return "vitamin b12"

    # Vitamin K2 → Vitamin K
    if "vitamin k2" in name or "k2" in name:
        return "vitamin k"

    # Vitamin D3 → Vitamin D
    if "vitamin d3" in name:
        return "vitamin d"

    return name

def find_best_nutrient_key(nutrients_dict: dict, nut_name: str) -> str | None:
    """
    Robust nutrient matching for vitamins including K1/K2 variants.
    """

    if not nutrients_dict:
        return None

    user = normalize_nutrient_name(nut_name)
    if not user:
        return None

    # Vitamin K special handling
    if "vitamin k" in user or user in ["k", "k1", "k2"]:
        for k in nutrients_dict.keys():
            if "vitamin k" in k.lower():
                return k

    # Vitamin D handling
    if "vitamin d" in user:
        for k in nutrients_dict.keys():
            if "vitamin d" in k.lower():
                return k

    # B vitamin automatic matching
    b_map = {
        "b1": "thiamin",
        "b2": "riboflavin",
        "b3": "niacin",
        "b5": "pantothenic",
        "b6": "vitamin b6",
        "b7": "biotin",
        "b9": "folate",
        "b12": "vitamin b12"
    }

    if user in b_map:
        target = b_map[user]
        for k in nutrients_dict.keys():
            if target in k.lower():
                return k

    # Generic fallback
    user_tokens = set(user.split())
    best_key = None
    best_score = 0

    for k in nutrients_dict.keys():
        k_norm = normalize_nutrient_name(k)
        k_tokens = set(k_norm.split())

        score = 0

        if user in k_norm:
            score += 5

        overlap = user_tokens & k_tokens
        score += len(overlap)

        if score > best_score:
            best_score = score
            best_key = k

    if best_score == 0:
        return None

    return best_key




# ============================================================
# SYSTEM PROMPT (nutritionist constraints + output rules)
# ============================================================

SYSTEM_NUTRITIONIST = (
    "You are an expert clinical nutritionist and supplement-label analyst.\n"
    "Goal: replace supplements with WHOLE foods found as close to nature as possible.\n\n"

    "STRICT FOOD RULES:\n"
    "- Only plain, unprocessed whole foods.\n"
    "- Prefer foods exactly as found in nature.\n"
    "- Avoid dried/dehydrated versions unless absolutely necessary.\n"
    "- Avoid powders, extracts, juices, sauces, concentrates.\n"
    "- Avoid peeled/trimmed variants if whole version exists (prefer cucumber with peel over peeled).\n"
    "- Avoid multiple variants of the same food (do NOT list raw vs dried vs cooked versions of same item).\n"
    "- Top foods must represent DIFFERENT food types (e.g., kiwi + bell pepper + broccoli, not 3 bell pepper variants).\n\n"

    "Output structured data only when requested."
)

SYSTEM_PARSER = (
    "You are a strict data extraction engine.\n"
    "Your ONLY task is to extract micronutrients and their dosages from supplement facts text.\n"
    "Do NOT apply dietary reasoning.\n"
    "Do NOT suggest foods.\n"
    "Return ONLY valid JSON.\n"
)


# ============================================================
# PRO++ WORKER PIPELINE (runs in background thread)
# ============================================================

def simplify_food_name_for_display(desc: str) -> str:
    """
    Simplify USDA food description for UI display.
    Keep: specific types (almond vs hazelnut), colors, and NUTRITIONALLY IMPORTANT processing (raw/cooked/peeled)
    Remove: location/origin, variety codes, unnecessary modifiers
    Examples:
        'almonds, blanched, roasted' -> 'almonds, roasted'
        'seeds, pumpkin, dried' -> 'pumpkin seeds, dried'
        'carrot, peeled, raw' -> 'carrot, peeled, raw'
        'carrot, with peel, cooked' -> 'carrot, with peel, cooked'
        'pepper, sweet, red, raw' -> 'red pepper, raw'
        'seaweed canadian cultivated emi-tsuinomata, rehydrated' -> 'seaweed, rehydrated'
    """
    if not desc:
        return ""
    
    desc = desc.lower().strip()
    
    # Color keywords to KEEP (nutritionally significant)
    colors = {"red", "green", "orange", "yellow", "white", "black", "purple", "pink", "dark", "light"}
    
    # Processing methods to KEEP (affect micronutrient content)
    keep_processing = {"raw", "dried", "dehydrated", "freeze-dried", "cooked", "boiled", "baked", "steamed", 
                       "peeled", "without peel", "with peel", "frozen", "canned", "pickled", "roasted", 
                       "blanched", "rehydrated"}
    
    # Location/origin/irrelevant keywords to REMOVE
    remove_words = {"canadian", "chinese", "japanese", "imported", "organic", "cultivated", 
                    "baby", "medium", "large", "small", "sweet", "hot", "mild", "unsalted", "salted"}
    
    # Split by comma to get components
    parts = [p.strip() for p in desc.split(",") if p.strip()]
    
    # Collect meaningful words (keep processing + colors, remove locations)
    meaningful_words = []
    for part in parts:
        for word in part.split():
            if word not in remove_words:
                meaningful_words.append(word)
    
    if not meaningful_words:
        return "food"
    
    # Special handling for "seeds X" or "nuts X" patterns
    # e.g., ["seeds", "pumpkin", "dried"] should become "pumpkin seeds, dried"
    if len(meaningful_words) >= 2:
        if meaningful_words[0] in ("seeds", "nuts", "grains"):
            specific = meaningful_words[1]
            generic = meaningful_words[0]
            # Put specific variety first: "pumpkin seeds"
            result = f"{specific} {generic}"
            
            # Append processing methods and colors in a natural order
            remaining = meaningful_words[2:]
            if remaining:
                # Keep colors separate from processing
                color_words = [w for w in remaining if w in colors]
                processing_words = [w for w in remaining if w in keep_processing]
                
                if color_words:
                    result = f"{color_words[0]} {result}"
                if processing_words:
                    result = f"{result}, {', '.join(processing_words)}"
            
            return result.strip()
    
    # For regular foods, organize: color + ingredient + processing
    color_words = [w for w in meaningful_words if w in colors]
    processing_words = [w for w in meaningful_words if w in keep_processing]
    ingredient_words = [w for w in meaningful_words if w not in colors and w not in keep_processing]
    
    # Build result
    result_parts = []
    
    if color_words:
        result_parts.append(color_words[0])  # First color
    
    if ingredient_words:
        result_parts.append(ingredient_words[0])  # Main ingredient
    
    result = " ".join(result_parts) if result_parts else "food"
    
    if processing_words:
        result = f"{result}, {', '.join(processing_words)}"
    
    return result.strip() or "food"

def canonical_food_family(desc: str) -> str:
    """
    Reduce USDA description to main food identity.
    Example:
        'Kiwifruit, green, raw' -> 'kiwifruit'
        'Pepper, sweet, red, raw' -> 'pepper'
        'Pepper, sweet, red, dried' -> 'pepper'
    """
    desc = (desc or "").lower()

    # remove processing modifiers
    remove_words = [
        "raw", "dried", "dehydrated", "freeze-dried", "cooked",
        "boiled", "baked", "steamed", "peeled", "without peel",
        "with peel", "fresh", "frozen"
    ]

    for w in remove_words:
        desc = desc.replace(w, "")

    # keep only first meaningful token
    parts = [p.strip() for p in desc.split(",") if p.strip()]
    if parts:
        return parts[0]

    return desc.strip()

def normalize_unit_to_mg(value, unit, nutrient_name=None):
    unit = (unit or "").lower()

    if unit in ["mg"]:
        return value

    if unit in ["g", "gram", "grams"]:
        return value * 1000.0

    if unit in ["mcg", "µg", "μg"]:
        return value / 1000.0

    if unit == "iu":
        name = (nutrient_name or "").lower()

        # Vitamin D: 1 mcg = 40 IU
        if "vitamin d" in name:
            return (value / 40.0) / 1000.0

        # Vitamin A: 1 IU ≈ 0.3 mcg retinol
        if "vitamin a" in name:
            return (value * 0.3) / 1000.0

        # Vitamin E: 1 IU ≈ 0.67 mg (natural form)
        if "vitamin e" in name:
            return value * 0.67

    return value


def convert_to_mg(value, unit, nutrient_name):
    if not unit:
        return value

    unit = unit.lower()

    if unit in ["mg"]:
        return value

    if unit in ["g", "gram", "grams"]:
        return value * 1000.0

    if unit in ["mcg", "µg"]:
        return value / 1000.0

    if unit == "iu":
        name = nutrient_name.lower()

        # Vitamin D: 1 µg = 40 IU
        if "vitamin d" in name:
            return (value / 40.0) / 1000.0

        # Vitamin A: 1 IU ≈ 0.3 µg retinol
        if "vitamin a" in name:
            return (value * 0.3) / 1000.0

        # Vitamin E: 1 IU ≈ 0.67 mg (natural form approx)
        if "vitamin e" in name:
            return value * 0.67

    return value
    
def fallback_parse_supplement_label(text: str) -> dict:
    """
    Regex-based fallback extractor for common supplement-label lines like:
    Vitamin K2 20 µg
    Vitamin A 125 µg
    Vitamin D3 125 µg
    Vitamin E 3 mg
    Vitamin C 150 mg
    Folate 375 mcg
    Returns dict in the same schema as the LLM step.
    """
    t = fix_common_ocr_errors((text or "").lower())

    # normalize decimal commas: "3,5 mg" -> "3.5 mg"
    t = re.sub(r"(\d),(\d)", r"\1.\2", t)

    out: dict = {}

    # helper to store (supports ranges like 700-900)
    def _put(key: str, val_str: str, unit_str: str, val_str2: str = None):
        val = safe_float(val_str)
        if val_str2:
            val2 = safe_float(val_str2)
            if val2 is not None and (val is None or val2 > val):
                val = val2
        unit = (unit_str or "").lower()
        if unit in ["µg", "μg"]:
            unit = "mcg"
        if unit == "ug":
            unit = "mcg"
        if val is not None and val > 0 and unit in ["mg", "mcg", "iu", "g"]:
            out[key] = {"value": val, "unit": unit}

    # patterns: nutrient name + number/range + unit (handles optional punctuation)
    val_rgx = r"(\d+(?:\.\d+)?)(?:\s*[-–]\s*(\d+(?:\.\d+)?))?"
    patterns = [
        (re.compile(rf"\bvitamin\s*k2\b\s*[:\-]?\s*{val_rgx}\s*(µg|μg|mcg|ug|mg|g|iu)\b", re.IGNORECASE), "vitamin k2"),
        (re.compile(rf"\bvitamin\s*k1\b\s*[:\-]?\s*{val_rgx}\s*(µg|μg|mcg|ug|mg|g|iu)\b", re.IGNORECASE), "vitamin k1"),
        (re.compile(rf"\bvitamin\s*k\b\s*[:\-]?\s*{val_rgx}\s*(µg|μg|mcg|ug|mg|g|iu)\b", re.IGNORECASE), "vitamin k"),
        (re.compile(rf"\bvitamin\s*a\b\s*[:\-]?\s*{val_rgx}\s*(µg|μg|mcg|ug|mg|g|iu)\b", re.IGNORECASE), "vitamin a"),
        (re.compile(rf"\bvitamin\s*c\b\s*[:\-]?\s*{val_rgx}\s*(µg|μg|mcg|ug|mg|g|iu)\b", re.IGNORECASE), "vitamin c"),
        (re.compile(rf"\bvitamin\s*d3\b\s*[:\-]?\s*{val_rgx}\s*(µg|μg|mcg|ug|mg|g|iu)\b", re.IGNORECASE), "vitamin d3"),
        (re.compile(rf"\bvitamin\s*d2\b\s*[:\-]?\s*{val_rgx}\s*(µg|μg|mcg|ug|mg|g|iu)\b", re.IGNORECASE), "vitamin d2"),
        (re.compile(rf"\bvitamin\s*d\b\s*[:\-]?\s*{val_rgx}\s*(µg|μg|mcg|ug|mg|g|iu)\b", re.IGNORECASE), "vitamin d"),
        (re.compile(rf"\bvitamin\s*e\b\s*[:\-]?\s*{val_rgx}\s*(µg|μg|mcg|ug|mg|g|iu)\b", re.IGNORECASE), "vitamin e"),
        (re.compile(rf"\bvitamin\s*b1\b\s*[:\-]?\s*{val_rgx}\s*(µg|μg|mcg|ug|mg|g|iu)\b", re.IGNORECASE), "vitamin b1"),
        (re.compile(rf"\bthiamine\b\s*[:\-]?\s*{val_rgx}\s*(µg|μg|mcg|ug|mg|g|iu)\b", re.IGNORECASE), "vitamin b1"),
        (re.compile(rf"\bvitamin\s*b2\b\s*[:\-]?\s*{val_rgx}\s*(µg|μg|mcg|ug|mg|g|iu)\b", re.IGNORECASE), "vitamin b2"),
        (re.compile(rf"\bvitamin\s*b6\b\s*[:\-]?\s*{val_rgx}\s*(µg|μg|mcg|ug|mg|g|iu)\b", re.IGNORECASE), "vitamin b6"),
        (re.compile(rf"\bvitamin\s*b9\b\s*[:\-]?\s*{val_rgx}\s*(µg|μg|mcg|ug|mg|g|iu)\b", re.IGNORECASE), "folate"),
        (re.compile(rf"\bfolate\b\s*[:\-]?\s*{val_rgx}\s*(µg|μg|mcg|ug|mg|g|iu)\b", re.IGNORECASE), "folate"),
        (re.compile(rf"\bvitamin\s*b12\b\s*[:\-]?\s*{val_rgx}\s*(µg|μg|mcg|ug|mg|g|iu)\b", re.IGNORECASE), "vitamin b12"),
        (re.compile(rf"\bbiotin\b\s*[:\-]?\s*{val_rgx}\s*(µg|μg|mcg|ug|mg|g|iu)\b", re.IGNORECASE), "biotin"),
        (re.compile(rf"\bpantothenic\s*acid\b\s*[:\-]?\s*{val_rgx}\s*(µg|μg|mcg|ug|mg|g|iu)\b", re.IGNORECASE), "pantothenic acid"),
        (re.compile(rf"\bselenium\b\s*[:\-]?\s*{val_rgx}\s*(µg|μg|mcg|ug|mg|g|iu)\b", re.IGNORECASE), "selenium"),
        (re.compile(rf"\bmagnesium\b\s*[:\-]?\s*{val_rgx}\s*(µg|μg|mcg|ug|mg|g|iu)\b", re.IGNORECASE), "magnesium"),
        (re.compile(rf"\bmanganese\b\s*[:\-]?\s*{val_rgx}\s*(µg|μg|mcg|ug|mg|g|iu)\b", re.IGNORECASE), "manganese"),
        (re.compile(rf"\bpotassium\b\s*[:\-]?\s*{val_rgx}\s*(µg|μg|mcg|ug|mg|g|iu)\b", re.IGNORECASE), "potassium"),
        (re.compile(rf"\bsodium\b\s*[:\-]?\s*{val_rgx}\s*(µg|μg|mcg|ug|mg|g|iu)\b", re.IGNORECASE), "sodium"),
        (re.compile(rf"\bcaffeine\b\s*[:\-]?\s*{val_rgx}\s*(µg|μg|mcg|ug|mg|g|iu)\b", re.IGNORECASE), "caffeine"),
    ]

    for pat, key in patterns:
        m = pat.search(t)
        if not m:
            continue
        _put(key, m.group(1), m.group(3), m.group(2))

    return out
    
def validate_parsed_nutrients(parsed: dict, source_text: str) -> dict:
    """
    Validate extracted nutrients by checking if the nutrient name appears in the source text.
    Filters out hallucinations (nutrients mentioned in marketing but not in facts table).
    Works with any nutrient - no hardcoded list needed.
    """
    if not isinstance(parsed, dict) or not source_text:
        return parsed
    
    source_lower = source_text.lower()
    validated = {}
    
    for nutrient_name in parsed:
        # Convert nutrient name to lowercase and remove extra whitespace
        nutrient_lower = nutrient_name.lower().strip()
        
        # Check if nutrient name appears in source (even as substring)
        # E.g., "magnesium" appears in "magnesium complex"
        if nutrient_lower in source_lower:
            validated[nutrient_name] = parsed[nutrient_name]
    
    return validated


def pipeline_run(
    input_text: str,
    diet: str,
    exclusions: list[str],
    try_fetch_url: bool,
    lang: str,
    progress_q: queue.Queue,
    cancel_event: threading.Event
):
    """
    Returns dict:
      {
        "supplement_dict": {...},
        "results": { nutrient: [top5 foods...] },
        "debug": {"input_used": "...", "parsed_json_raw": "..."}
      }
    """

    def progress(step: str, key: str = None, value: float = None, fmt: dict | None = None):
        """Send progress message with optional translation key and formatting values."""
        progress_q.put({"type": "progress", "text": step, "key": key, "value": value, "fmt": fmt or {}})

    def fail(
        err_title: str,
        err_key: str = None,
        err_msg: str = None,
        msg_key: str = None,
    ):
        """Send error with optional translation keys for title and message."""
        progress_q.put({
            "type": "error",
            "title": err_title,
            "title_key": err_key,
            "msg": err_msg or err_title,
            "msg_key": msg_key,
        })

    # --------------------
    # STEP 0: basic checks
    # --------------------
    if not USDA_API_KEY.strip():
        fail(
            "Config Error",
            err_key="config_error_title",
            err_msg="Missing USDA_API_KEY. Paste it at the top of the script.",
            msg_key="config_error_msg",
        )
        return

    input_text = (input_text or "").strip()
    if not input_text:
        fail(
            "Input Error",
            err_key="input_error_title",
            err_msg="Paste supplement facts, a product link, or upload a label image.",
            msg_key="input_error_msg",
        )
        return

    # Keep SuppSwap2 URL logic unchanged by default:
    # - If URL, we DO NOT fetch it unless user toggled try_fetch_url = True.
    input_used = input_text

    if cancel_event.is_set():
        return

    progress("Step 1/6: Preparing input…", "step_preparing", 0.1)

    if is_url(input_text) and try_fetch_url:
        progress("Step 1/6: (Optional) Fetching URL locally…", "step_fetching", 0.2)
        page_text = fetch_url_text(input_text, timeout=HTTP_TIMEOUT)
        if page_text:
            # Extract product name from URL to help identify correct facts table
            product_name = extract_product_name_from_url(input_text)
            # LLM compresses the page into the label / supplement-facts text
            input_used = llm_extract_supplement_facts_from_page(page_text, product_name)
        else:
            input_used = input_text
        progress("Step 1/6: Preparing input…", "step_preparing", 0.3)

    input_used = input_used[:MAX_INPUT_CHARS]
    input_used = fix_common_ocr_errors(input_used)

    if cancel_event.is_set():
        return

    # --------------------
    # STEP 2: Parse supplement facts JSON
    # --------------------
    progress("Step 2/6: Extracting micronutrients + dosages…", "step_parsing", 0.4)

    parse_user = f"""
Extract micronutrients and dosages from the text below.

Rules:
- Return ONLY valid JSON.
- Keys: nutrient names (lowercase).
- Values must be objects with:
    {{
      "value": number,
      "unit": "mg" OR "mcg" OR "iu"
    }}

IMPORTANT:
- Treat "µg" as "mcg".
- OCR may confuse "k2" as "kz". If you see "vitamin kz", output "vitamin k2".
- DO NOT convert units.
- Extract ALL nutrients that are clearly listed in the official supplement facts table.
- IGNORE any nutrient mentioned in marketing language or outside the nutrition table.
- If you cannot find supplement facts, return exactly: NO_DATA

Example (extract everything in the table - not just these):
{{
  "magnesium": {{"value": 417, "unit": "mg"}},
  "calcium": {{"value": 50, "unit": "mg"}},
  "vitamin d3": {{"value": 20, "unit": "mcg"}},
  "vitamin k2": {{"value": 30, "unit": "mcg"}},
  "zinc": {{"value": 5, "unit": "mg"}}
}}

TEXT:
{input_used}
"""

    # 2a) Call LLM once (for debug + early error detection)
    parse_out = call_llm(SYSTEM_PARSER, parse_user, temperature=0.0, timeout=60)

    parsed_raw = None
    parsed = None

    if "API ERROR" in parse_out or "CONNECTION ERROR" in parse_out or "INVALID JSON" in parse_out:
        fallback_only = fallback_parse_supplement_label(input_used)
        if not fallback_only:
            fail("API Error", err_msg=parse_out)
            return
        parsed_raw = parse_out
        parsed = fallback_only
    elif parse_out.strip() == "NO_DATA":
        # try regex fallback before failing
        fallback_only = fallback_parse_supplement_label(input_used)
        if not fallback_only:
            fail(
                "Parse Error",
                err_key="error_parse",
                err_msg="No supplement data found. Try label image or paste Supplement Facts text.",
                msg_key="error_no_supplement",
            )
            return
        parsed_raw = parse_out
        parsed = fallback_only
    else:
        parsed_raw = parse_out

        # 2b) Robust JSON parse (with repair attempts)
        parsed = llm_json(SYSTEM_PARSER, parse_user, retries=2)
        if isinstance(parsed, dict) and "__error__" in parsed:
            # if LLM JSON parsing fails, try regex fallback before failing
            fallback_only = fallback_parse_supplement_label(input_used)
            if not fallback_only:
                fail("Parse Error", err_key="error_parse", err_msg=parsed["__error__"])
                return
            parsed = fallback_only

        if not isinstance(parsed, dict) or not parsed:
            # try regex fallback before failing
            fallback_only = fallback_parse_supplement_label(input_used)
            if not fallback_only:
                fail(
                    "Parse Error",
                    err_key="error_parse",
                    err_msg="No supplement data found. Try label image or paste Supplement Facts text.",
                    msg_key="error_no_supplement",
                )
                return
            parsed = fallback_only

    # 2c) Merge fallback nutrients (fills missing ones like vitamin k2)
    fallback = fallback_parse_supplement_label(input_used)
    if isinstance(parsed, dict) and isinstance(fallback, dict):
        for k, v in fallback.items():
            if k not in parsed:
                parsed[k] = v

    # 2c-validation) Filter out hallucinated nutrients not in source text
    parsed = validate_parsed_nutrients(parsed, input_used)

    # 2d) Normalize parsed nutrient dict (convert everything to mg)
    supplement_dict = {}
    for k, v in parsed.items():
        name = normalize_nutrient_name(str(k))
        if not isinstance(v, dict):
            continue

        raw_value = safe_float(v.get("value"))
        unit = v.get("unit")

        if name and raw_value is not None and raw_value > 0:
            mg_value = normalize_unit_to_mg(raw_value, unit, name)
            supplement_dict[name] = mg_value

    if not supplement_dict:
        fail(
            "Parse Error",
            err_key="error_parse",
            err_msg="No valid nutrient values found in supplement data.",
            msg_key="error_no_nutrients",
        )
        return

    if cancel_event.is_set():
        return

    # --------------------
    # STEP 3: Ask LLM for Top 10 whole-food candidates per nutrient (single call)
    # --------------------
    progress("Step 3/6: Getting whole-food candidates (plain foods only)…", "step_3_candidates", 0.5)

    nutrients_str = ", ".join(sorted(supplement_dict.keys()))
    exclusions_str = ", ".join([e for e in exclusions if e]) or "none"

    candidates_prompt = f"""
For EACH nutrient in this list:
[{nutrients_str}]

Return 10 candidate foods each, that are:
- Plain, single-ingredient whole foods (fresh/raw/minimally processed)
- Common in supermarkets
- NOT processed (no sauces, juices, powders, bars, shakes, cereals, fortified/enriched foods)
- Must respect diet type: {diet}
- Must avoid these exclusions: {exclusions_str}

Output ONLY valid JSON in this exact format (no markdown):
{{
  "vitamin c": ["kiwi","orange","red bell pepper", "..."],
  "iron": ["lentils","spinach","...", "..."]
}}
"""
    candidates_map = llm_json(SYSTEM_NUTRITIONIST, candidates_prompt, retries=2)
    if isinstance(candidates_map, dict) and "__error__" in candidates_map:
        # if this fails we still proceed with fallback candidates = nutrient itself
        candidates_map = {}

    if cancel_event.is_set():
        return

    # --------------------
    # STEP 4: USDA ranking per nutrient (Top 5 by practicality/grams)
    # --------------------
    progress("Step 4/6: Ranking foods using USDA nutrient data…", "step_4_ranking", 0.6)

    results = {}  # nutrient -> list of top picks

    nutrients_list = list(supplement_dict.items())
    total_n = len(nutrients_list)

    for idx, (nut_name, target_mg) in enumerate(nutrients_list, start=1):
        if cancel_event.is_set():
            return
        progress(
            f"Step 4/6: Ranking {nut_name} ({idx}/{total_n})…",
            "step_4_ranking_nutrient",
            fmt={"nut_name": nut_name, "idx": idx, "total": total_n},
        )

        candidates = []
        arr = candidates_map.get(nut_name, [])
        if isinstance(arr, list):
            for x in arr:
                if isinstance(x, str):
                    s = x.strip().lower()
                    if s:
                        candidates.append(s)

        # USDA-only candidate queries: nutrient name + broad food groups
        if not candidates:
            candidates = [nut_name] + USDA_QUERY_SEEDS

        picks = []

        if "vitamin d" in nut_name or nut_name in ["d", "d2", "d3"]:

            results[nut_name] = [{
                "food": "Sunlight (UVB exposure)",
                "fdcId": None,
                "nutrient_key": None,
                "per_100g": None,
                "required_grams": None,
                "practicality": "☀ Prefer sunlight",
                "benefits": [
                    "natural hormone synthesis",
                    "supports immune regulation",
                    "improves calcium metabolism"
                ],
                "pubmed": pubmed_search_link("vitamin d sunlight synthesis")
            }]

            continue


        # gather many USDA foods from each candidate
        for food_query in candidates[:10]:
            if cancel_event.is_set():
                return

            foods = usda_search_food(food_query, page_size=25)
            if not foods:
                continue

            # Evaluate multiple items, not just first
            for f in foods[:25]:
                desc = (f.get("description") or "")
                desc_lower = desc.lower()

                if is_forbidden_food(desc_lower, diet, exclusions):
                    continue

                if not is_single_ingredient_food(desc):
                    continue
                    
                if "powder" in desc_lower:
                    continue

                if "oil" in desc_lower and "," in desc_lower:
                    continue  # e.g., oil blends



                nutrients = extract_nutrients(f)
                search_name = normalize_special_nutrient_name(nut_name)
                nutrient_key = find_best_nutrient_key(nutrients, search_name)

                if not nutrient_key:
                    continue

                per_100g = safe_float(nutrients.get(nutrient_key))
                if per_100g is None or per_100g <= 0:
                    continue

                # calculate grams
                required_grams = (target_mg / per_100g) * 100.0

                # calculate grams for RDA
                grams_for_rda = None
                if nut_name in RDAS:
                    rda_raw = RDAS[nut_name]
                    # Nutrients stored in mcg that need conversion to mg (for comparison with per_100g which is in mg)
                    if nut_name in ["vitamin a", "vitamin d", "vitamin k", "folate", "vitamin b12", "biotin", "selenium", "chromium", "iodine", "molybdenum"]:
                        rda_mg = rda_raw / 1000.0  # convert mcg to mg
                    else:
                        rda_mg = rda_raw  # already in mg
                    if per_100g > 0:
                        grams_for_rda_raw = (rda_mg / per_100g) * 100.0
                        # Handle rounding without losing very small values
                        if grams_for_rda_raw < 0.001:
                            grams_for_rda = f"< 0.001"
                        elif grams_for_rda_raw < 0.01:
                            grams_for_rda = round(grams_for_rda_raw, 4)
                            if grams_for_rda == 0:
                                grams_for_rda = f"< 0.001"
                        elif grams_for_rda_raw < 0.1:
                            grams_for_rda = round(grams_for_rda_raw, 3)
                            if grams_for_rda == 0:
                                grams_for_rda = f"< 0.1"
                        elif grams_for_rda_raw < 1:
                            grams_for_rda = round(grams_for_rda_raw, 2)
                        elif grams_for_rda_raw < 10:
                            grams_for_rda = round(grams_for_rda_raw, 1)
                        else:
                            grams_for_rda = round(grams_for_rda_raw, 0)

                # sensitive rounding for very small amounts (handle 0.0 case)
                if required_grams < 0.001:
                    display_grams = "< 0.001"
                elif required_grams < 0.01:
                    display_grams = round(required_grams, 4)
                    if display_grams == 0:
                        display_grams = "< 0.001"
                elif required_grams < 0.1:
                    display_grams = round(required_grams, 3)
                    if display_grams == 0:
                        display_grams = "< 0.1"
                elif required_grams < 1:
                    display_grams = round(required_grams, 2)
                elif required_grams < 10:
                    display_grams = round(required_grams, 1)
                else:
                    display_grams = round(required_grams, 0)

                if required_grams < 300:
                    practicality = "✔ Realistic"
                elif required_grams < 600:
                    practicality = "⚠ Moderate"
                else:
                    practicality = "❌ Unrealistic"

                picks.append({
                    "food": simplify_food_name_for_display(desc),
                    "fdcId": f.get("fdcId"),
                    "nutrient_key": nutrient_key,
                    "category": get_food_category(f),
                    "per_100g": round(per_100g, 3),
                    "required_grams": display_grams,
                    "grams_for_rda": grams_for_rda,
                    "practicality": practicality,
                })


        # Deduplicate by FOOD FAMILY (not variants)
        dedup = {}

        for p in picks:

            desc = (p["food"] or "").strip().lower()
            if not desc:
                continue

            family = canonical_food_family(desc)

            # prefer more natural variant
            score = 0
            if "dried" in desc:
                score += 5
            if "peeled" in desc:
                score += 3
            if "powder" in desc:
                score += 10

            p["_nature_penalty"] = score

            if family not in dedup:
                dedup[family] = p
            else:
                existing = dedup[family]

                # choose more natural OR lower grams
                if (
                    p["_nature_penalty"] < existing["_nature_penalty"]
                    or (
                        p["_nature_penalty"] == existing["_nature_penalty"]
                        and p["required_grams"] < existing["required_grams"]
                    )
                ):
                    dedup[family] = p


        picks = list(dedup.values())

        # Sort: prioritize realistic > moderate > unrealistic, then by required grams
        def band_score(pr):
            if "✔" in pr:
                return 0
            if "⚠" in pr:
                return 1
            return 2

        picks.sort(key=lambda x: (band_score(x["practicality"]), x["required_grams"]))

        # Keep more results for paging in the UI
        max_results = 50
        picks = picks[:max_results]

        # If we found nothing, keep empty
        if not picks:
            results[nut_name] = []
            continue

        results[nut_name] = picks

    if cancel_event.is_set():
        return

    # --------------------
    # STEP 5: Add 3 "additional benefits" per food (compact call)
    # --------------------
    progress("Step 5/6: Adding 3 additional benefits per whole food…", "step_5_benefits", 0.8)

    # Build a compact list of foods to annotate (unique)
    unique_foods = []
    seen = set()
    for nut_name, top5 in results.items():
        for p in top5:
            f = normalize_space(p.get("food", "")).lower()
            if f and f not in seen:
                seen.add(f)
                unique_foods.append(f)

    # Cap to avoid token blowups
    unique_foods = unique_foods[:25]

    benefits_map = {}
    if unique_foods:
        # First, try to get benefits from our evidence-based database
        for food in unique_foods:
            food_lower = food.lower()
            # Check exact match first
            if food_lower in FOOD_BENEFITS_DB:
                benefits_map[food_lower] = FOOD_BENEFITS_DB[food_lower][:3]
            else:
                # Check for partial matches (e.g., "kiwi fruit" matches "kiwi")
                for db_food, db_benefits in FOOD_BENEFITS_DB.items():
                    if db_food in food_lower or food_lower in db_food:
                        benefits_map[food_lower] = db_benefits[:3]
                        break

        # Special-case: sunlight is a non-food source for vitamin D — mark clearly
        # and do NOT generate additional food-based benefits for it.
        if any(f.lower() == 'sunlight' for f in unique_foods):
            benefits_map['sunlight'] = [
                "Non-food source: sunlight (vitamin D synthesis)",
                "Additional food-based benefits not applicable",
                "—"
            ]
        
        # For foods not in DB, use LLM
        missing_foods = [f for f in unique_foods if normalize_space(f).lower() not in benefits_map]
        if missing_foods:
            foods_str = ", ".join(missing_foods)
            benefits_prompt = f"""
You are a clinical nutrition expert with access to scientific literature.

For each whole food in this list:
[{foods_str}]

Provide EXACTLY 3 evidence-based health benefits beyond the primary micronutrient.

Rules:
- Base benefits on peer-reviewed research (cite key studies/compounds).
- Focus on whole-food synergies: fiber, phytonutrients, antioxidants, healthy fats, etc.
- Do NOT mention the matched micronutrient.
- No disease-treatment claims.
- Each benefit under 10 words, include compound name.
- Output STRICT JSON only.

Example:
{{
  "kiwi": ["fiber supports digestive health", "polyphenols provide antioxidant protection", "aids collagen synthesis"],
  "broccoli": ["sulforaphane enables cellular detoxification", "supports microbiome diversity", "glucosinolates reduce inflammation"]
}}
"""

            benefits = llm_json(SYSTEM_NUTRITIONIST, benefits_prompt, retries=2)
            if isinstance(benefits, dict) and "__error__" not in benefits:
                # normalize keys
                for k, v in benefits.items():
                    kk = normalize_space(str(k)).lower()
                    if isinstance(v, list):
                        benefits_map[kk] = [normalize_space(str(x)) for x in v[:3]]

    # attach benefits + reference links
    for nut_name, top5 in results.items():
        for p in top5:
            key = normalize_space(p.get("food", "")).lower()

            benefits = benefits_map.get(key)

            if not benefits:
                benefits = [
                    "contains natural cofactors",
                    "provides dietary fiber",
                    "offers phytonutrient diversity"
                ]

            p["benefits"] = benefits[:3]
            p["pubmed"] = pubmed_search_link(p.get("food", ""))


    # --------------------
    # STEP 6: Done
    # --------------------
    progress("Step 6/6: Rendering output…", "step_6_rendering", 0.9)

    progress_q.put({
        "type": "done",
        "payload": {
            "supplement_dict": supplement_dict,
            "results": results,
            "debug": {
                "input_used": input_used[:MAX_DEBUG_CHARS],
                "parsed_json_raw": parsed_raw[:MAX_DEBUG_CHARS],
                "diet": diet,
                "exclusions": exclusions,
                "try_fetch_url": try_fetch_url,
            }
        }
    })


# ============================================================
# UI
# ============================================================

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1100x720")

        self.diet = "Omnivore"
        self.exclusions = []
        self.last_input_text = ""

        self.progress_q = queue.Queue()
        self.upload_queue = queue.Queue()
        self.upload_progress_q = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker_thread = None

        # UI state widgets
        self.diet_menu = None
        self.exclude_entry = None
        self.text_box = None
        self.try_fetch_var = ctk.BooleanVar(value=True)  # ON by default - automatically fetch URLs
        self.debug_var = ctk.BooleanVar(value=False)
        self.upload_in_progress = False
        self.analysis_in_progress = False

        # Screen state tracking for dynamic language refresh
        self.current_screen = None
        self.current_screen_kwargs = {}

        # default language
        self.lang = "en"
        # watermark placement: 'bottom-right' | 'bottom-left' | 'bottom-center'
        self.watermark_pos = 'bottom-right'
        self.show_language_selection()

    def t(self, key: str, fallback: str = None) -> str:
        return _t_for(self.lang, key, fallback)
    
    def show_screen(self, screen_name: str, **kwargs):
        """Show a screen and track it for potential language refresh."""
        self.current_screen = screen_name
        self.current_screen_kwargs = kwargs
        method = getattr(self, f"show_{screen_name}")
        method(**kwargs)
    
    def refresh_screen(self):
        """Rebuild current screen with new language."""
        if self.current_screen:
            self.show_screen(self.current_screen, **self.current_screen_kwargs)
    
    def _make_lang_selector(self, parent_frame: ctk.CTkFrame):
        """Create language selector dropdown for any screen."""
        lang_menu = ctk.CTkOptionMenu(
            parent_frame,
            values=list(LANG_OPTIONS.values()),
            command=self._on_lang_change_dynamic
        )
        lang_menu.set(LANG_OPTIONS.get(self.lang, "English"))
        lang_menu.pack(side="right", padx=10)
        return lang_menu
    
    def _on_lang_change_dynamic(self, lang_name: str):
        """Handle language change and refresh current screen."""
        code = _LANG_NAME_TO_CODE.get(lang_name, self.lang)
        if code != self.lang:
            self._capture_state_before_refresh()
            self.lang = code
            self.refresh_screen()

    def _capture_state_before_refresh(self):
        """Capture mutable screen state before rebuilding UI for language refresh."""
        try:
            if self.current_screen in ("diet", "input"):
                if self.diet_menu:
                    self.diet = self.diet_menu.get() or self.diet
                if self.exclude_entry:
                    self.exclusions = parse_exclusion_input(self.exclude_entry.get() or "")

            if self.current_screen == "input" and self.text_box:
                self.last_input_text = self.text_box.get("1.0", "end").strip()
        except Exception:
            pass

    def clear(self):
        for w in self.winfo_children():
            w.destroy()
        # Re-place persistent watermark after clearing the main UI
        try:
            self.place_watermark()
        except Exception:
            pass

    # -----------------------------
    # NAV (Simplified - screens now track state)
    # -----------------------------
    def go_intro(self):
        self.show_screen("intro")

    def go_diet(self):
        self.show_screen("diet")

    def go_input(self):
        self.show_screen("input", from_back=True)

    def place_watermark(self, position: str = None):
        """Place a persistent watermark on the main window.
        position: 'bottom-right', 'bottom-left', or 'bottom-center'.
        """
        pos = position or getattr(self, 'watermark_pos', 'bottom-right')
        # remove existing watermark if present
        try:
            if hasattr(self, 'watermark_label') and self.watermark_label:
                self.watermark_label.place_forget()
        except Exception:
            pass

        self.watermark_label = ctk.CTkLabel(self, text=WATERMARK, font=("Arial", 9))
        # map to relx/rely and anchor - place at bottom with margin (rely=0.92 leaves room for footer)
        if pos == 'bottom-left':
            self.watermark_label.place(relx=0.01, rely=0.92, anchor='sw')
        elif pos == 'bottom-center':
            self.watermark_label.place(relx=0.5, rely=0.92, anchor='s')
        else:
            # default bottom-right
            self.watermark_label.place(relx=0.99, rely=0.92, anchor='se')
        
        # Ensure watermark is on top
        self.watermark_label.lift()

    # ------------------------------------
    # SCREEN 1: Language Selection + Intro
    # ------------------------------------
    def show_language_selection(self):
        """Initial language selection screen."""
        self.current_screen = "language_selection"
        self.current_screen_kwargs = {}
        self.clear()

        frame = ctk.CTkFrame(self)
        frame.pack(expand=True)
        
        lang_names = list(LANG_OPTIONS.values())
        lang_var = ctk.StringVar(value=LANG_OPTIONS.get(self.lang, "English"))

        label_txt = ctk.CTkLabel(frame, text=self.t("select_language"), font=("Arial", 16))
        label_txt.pack(pady=(20, 10))

        # Dropdown calls refresh directly on language change
        opt = ctk.CTkOptionMenu(frame, values=lang_names, variable=lang_var, command=self._on_lang_change_dynamic)
        opt.pack(pady=10)

        def _cont():
            sel_name = lang_var.get() or LANG_OPTIONS.get(self.lang, "English")
            self.lang = _LANG_NAME_TO_CODE.get(sel_name, "en")
            self.show_screen("intro")

        btn_continue = ctk.CTkButton(frame, text=self.t("continue"), command=_cont, width=140)
        btn_continue.pack(pady=12)

    def show_intro(self):
        """Intro screen with dynamic language switching."""
        self.current_screen = "intro"
        self.current_screen_kwargs = {}
        self.clear()

        # Outer container with consistent background
        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True)

        # Header with language selector (fixed at top)
        header = ctk.CTkFrame(outer, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(12, 0))
        self._make_lang_selector(header)

        # Middle section: scrollable content
        middle = ctk.CTkFrame(outer, fg_color="transparent")
        middle.pack(fill="both", expand=True, padx=12, pady=12)

        # Create scrollable canvas (vertical only)
        canvas = tk.Canvas(middle, bg=self._get_bg_color(), highlightthickness=0)
        scrollbar = tk.Scrollbar(middle, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)

        # Content frame
        content = ctk.CTkFrame(canvas, fg_color="transparent")
        window_id = canvas.create_window((0, 0), window=content, anchor='nw')

        def _on_frame_config(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        content.bind("<Configure>", _on_frame_config)

        def _on_canvas_config(event):
            canvas.itemconfig(window_id, width=event.width - 2)
        canvas.bind("<Configure>", _on_canvas_config)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind("<MouseWheel>", _on_mousewheel)

        # Content text
        why_box = ctk.CTkTextbox(content, width=900, height=400, font=("Arial", 11), state="disabled")
        why_box.pack(pady=20, padx=20, fill="both", expand=True)
        why_box.configure(state="normal")
        why_box.insert("end", self.t("why_exists"))
        why_box.configure(state="disabled")

        # Navigation at bottom (fixed)
        nav = ctk.CTkFrame(outer, fg_color="transparent")
        nav.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkButton(nav, text=self.t("next"), command=lambda: self.show_screen("diet"), width=160).pack()

        self.after(100, self.place_watermark)
    
    def _get_bg_color(self):
        """Get appropriate background color based on theme."""
        mode = ctk.get_appearance_mode()
        return "#f0f0f0" if mode == "Light" else "#212121"

    # -----------------------------
    # SCREEN 2: Diet + exclusions
    # -----------------------------
    def show_diet(self):
        """Diet & exclusions screen with dynamic language switching."""
        self.current_screen = "diet"
        self.current_screen_kwargs = {}
        self.clear()

        # Outer container with consistent background
        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True)

        # Header with language selector (fixed at top)
        header = ctk.CTkFrame(outer, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(12, 0))
        title_lbl = ctk.CTkLabel(header, text=self.t("diet_exclusions_title"), font=("Arial", 16, "bold"))
        title_lbl.pack(side="left", padx=10)
        self._make_lang_selector(header)

        # Middle section: scrollable content
        middle = ctk.CTkFrame(outer, fg_color="transparent")
        middle.pack(fill="both", expand=True, padx=12, pady=12)

        # Create scrollable canvas (vertical only)
        canvas = tk.Canvas(middle, bg=self._get_bg_color(), highlightthickness=0)
        scrollbar = tk.Scrollbar(middle, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)

        # Content frame
        content = ctk.CTkFrame(canvas, fg_color="transparent")
        window_id = canvas.create_window((0, 0), window=content, anchor='nw')

        def _on_frame_config(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        content.bind("<Configure>", _on_frame_config)

        def _on_canvas_config(event):
            canvas.itemconfig(window_id, width=event.width - 2)
        canvas.bind("<Configure>", _on_canvas_config)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind("<MouseWheel>", _on_mousewheel)

        # Content
        ctk.CTkLabel(content, text=self.t("select_diet_type"), font=("Arial", 13, "bold")).pack(pady=(15, 8), padx=20)
        self.diet_menu = ctk.CTkOptionMenu(content, values=list(DIET_RESTRICTIONS.keys()), width=500)
        self.diet_menu.set(self.diet or "Omnivore")
        self.diet_menu.pack(pady=8, padx=20)

        ctk.CTkLabel(content, text=self.t("exclude_foods_label"), font=("Arial", 13, "bold")).pack(pady=(20, 8), padx=20)
        self.exclude_entry = ctk.CTkEntry(content, width=500, placeholder_text=self.t("optional", "Optional"))
        self.exclude_entry.insert(0, ", ".join(self.exclusions) if self.exclusions else "")
        self.exclude_entry.pack(pady=8, padx=20)

        # Navigation at bottom (fixed)
        nav = ctk.CTkFrame(outer, fg_color="transparent")
        nav.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkButton(nav, text=self.t("back"), command=self.go_intro, width=160).pack(side="left", padx=5)
        ctk.CTkButton(nav, text=self.t("next"), command=self.show_input, width=160).pack(side="left", padx=5)

        self.after(100, self.place_watermark)

    # -----------------------------
    # SCREEN 3: Input
    # -----------------------------
    def show_input(self, from_back=False):
        """Input screen with dynamic language switching."""
        if not from_back:
            try:
                self.diet = self.diet_menu.get() if self.diet_menu else (self.diet or "Omnivore")
            except:
                self.diet = self.diet or "Omnivore"
            try:
                raw = self.exclude_entry.get() if self.exclude_entry else ""
                self.exclusions = parse_exclusion_input(raw)
            except:
                self.exclusions = self.exclusions or []
        else:
            self.diet = self.diet or "Omnivore"
            self.exclusions = self.exclusions or []

        self.current_screen = "input"
        self.current_screen_kwargs = {"from_back": from_back}
        self.clear()

        # Outer container with consistent background
        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True)

        # Header with language selector (fixed at top)
        header = ctk.CTkFrame(outer, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(12, 0))
        title_lbl = ctk.CTkLabel(header, text=self.t("input_title"), font=("Arial", 16, "bold"))
        title_lbl.pack(side="left", padx=10)
        self._make_lang_selector(header)

        # Middle section: scrollable content
        middle = ctk.CTkFrame(outer, fg_color="transparent")
        middle.pack(fill="both", expand=True, padx=12, pady=12)

        # Create scrollable canvas (vertical only)
        canvas = tk.Canvas(middle, bg=self._get_bg_color(), highlightthickness=0)
        scrollbar = tk.Scrollbar(middle, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)

        # Content frame
        content = ctk.CTkFrame(canvas, fg_color="transparent")
        window_id = canvas.create_window((0, 0), window=content, anchor='nw')

        def _on_frame_config(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        content.bind("<Configure>", _on_frame_config)

        def _on_canvas_config(event):
            canvas.itemconfig(window_id, width=event.width - 2)
        canvas.bind("<Configure>", _on_canvas_config)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind("<MouseWheel>", _on_mousewheel)

        # Content
        ctk.CTkLabel(content, text=self.t("paste_prompt"), font=("Arial", 12, "bold")).pack(pady=(15, 8), padx=20)
        
        self.text_box = ctk.CTkTextbox(content, width=850, height=140)
        self.text_box.pack(pady=10, padx=20, fill="both", expand=False)
        if self.last_input_text:
            self.text_box.insert("1.0", self.last_input_text)

        # Controls row
        controls_frame = ctk.CTkFrame(content, fg_color="transparent")
        controls_frame.pack(pady=15, padx=20, fill="x")
        
        ctk.CTkButton(controls_frame, text=self.t("upload_label"), command=self.upload_image, width=140).pack(side="left", padx=5)
        ctk.CTkCheckBox(controls_frame, text=self.t("try_fetch"), variable=self.try_fetch_var).pack(side="left", padx=10)
        ctk.CTkCheckBox(controls_frame, text=self.t("debug_mode"), variable=self.debug_var).pack(side="left", padx=10)

        # Navigation at bottom (fixed)
        nav = ctk.CTkFrame(outer, fg_color="transparent")
        nav.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkButton(nav, text=self.t("back"), command=self.go_diet, width=160).pack(side="left", padx=5)
        ctk.CTkButton(nav, text=self.t("analyze"), command=self.analyze_clicked, width=160).pack(side="left", padx=5)

        self.after(100, self.place_watermark)

    def upload_image(self):
        self.upload_in_progress = True  # Flag to prevent language refresh during upload
        try:
            if not hasattr(self, 'upload_progress_label'):
                self.upload_progress_label = ctk.CTkLabel(self, text=self.t("starting_upload"), font=("Arial", 12))
            self.upload_progress_label.pack(pady=5)
            
            if not hasattr(self, 'upload_progress'):
                self.upload_progress = ctk.CTkProgressBar(self, width=400, mode="determinate")
            self.upload_progress.pack(pady=5)
            self.upload_progress.set(0.0)

            path = filedialog.askopenfilename()
            if path:
                # start the upload/ocr worker for the chosen path
                self.start_upload_worker(path)
                self.poll_upload_queue()
            else:
                try:
                    self.upload_progress.pack_forget()
                except:
                    pass
                try:
                    if hasattr(self, 'upload_progress_label'):
                        self.upload_progress_label.pack_forget()
                except:
                    pass
                self.upload_in_progress = False
        except:
            self.upload_in_progress = False

    def start_upload_worker(self, path: str):
        """Begin async OCR upload for given path (used for Retry as well)."""
        async def async_worker():
            self.upload_progress_q.put({"type": "progress", "value": 0.1, "text": "Starting upload...", "key": "starting_upload"})
            await asyncio.sleep(0.2)
            self.upload_progress_q.put({"type": "progress", "value": 0.2, "text": "Sending to OCR service...", "key": "ocr_sending"})

            # Start progress updater
            progress_task = asyncio.create_task(update_progress())

            text = await async_extract_text_from_image_openrouter(path)

            progress_task.cancel()

            # If OCR returned an error string (convention: starts with 'OCR') then surface as upload error
            if isinstance(text, str) and text.startswith("OCR"):
                self.upload_progress_q.put({"type": "progress", "value": 0.0, "text": "OCR Error", "key": "ocr_error_title"})
                # Put an error object on the upload_queue for the UI to handle (contains path for retry)
                self.upload_queue.put({"__error__": True, "text": text, "path": path})
                return

            self.upload_progress_q.put({"type": "progress", "value": 0.9, "text": "Processing OCR text...", "key": "ocr_processing"})
            text = fix_common_ocr_errors(text)
            self.upload_progress_q.put({"type": "progress", "value": 1.0, "text": "Done", "key": "ocr_done"})
            self.upload_queue.put(text)

        async def update_progress():
            val = 0.2
            while True:
                val = min(val + 0.005, 0.8)  # Increment by 0.5% every 0.1s
                self.upload_progress_q.put({"type": "progress", "value": val, "text": "Processing with OCR API...", "key": "ocr_processing_api"})
                await asyncio.sleep(0.1)

        def upload_worker():
            try:
                asyncio.run(async_worker())
            except Exception as e:
                # unexpected exception; surface to upload_queue as error
                self.upload_queue.put({"__error__": True, "text": f"OCR CONNECTION ERROR: {e}", "path": path})

        threading.Thread(target=upload_worker, daemon=True).start()

    # -----------------------------
    # SCREEN: Progress
    # -----------------------------
    def show_progress(self):
        """Analysis progress screen with dynamic language switching."""
        self.current_screen = "progress"
        self.current_screen_kwargs = {}
        self.clear()

        # Header with language selector
        header = ctk.CTkFrame(self)
        header.pack(fill="x", padx=10, pady=10)
        self._make_lang_selector(header)

        ctk.CTkLabel(self, text=self.t("analyzing"), font=("Arial", 22, "bold")).pack(pady=(40, 8))
        self.step_label = ctk.CTkLabel(self, text=self.t("starting"), font=("Arial", 14))
        self.step_label.pack(pady=8)

        self.progress_bar = ctk.CTkProgressBar(self, width=980)
        self.progress_bar.pack(pady=10)
        self.progress_bar.set(0.0)

        self.log_box = ctk.CTkTextbox(self, width=980, height=260)
        self.log_box.pack(pady=15)
        self.log_box.insert("end", self.t("progress_log") + "\n")

        controls = ctk.CTkFrame(self)
        controls.pack(pady=10)

        ctk.CTkButton(controls, text=self.t("cancel"), command=self.cancel_worker, width=160).pack(side="left", padx=10)
        ctk.CTkButton(controls, text=self.t("back_to_input"), command=self.go_input, width=160).pack(side="left", padx=10)

        self.after(120, self.poll_progress_queue)

    def cancel_worker(self):
        self.cancel_event.set()
        try:
            self.step_label.configure(text=self.t("cancelling"))
        except:
            pass

    def log(self, s: str):
        try:
            self.log_box.insert("end", s + "\n")
            self.log_box.see("end")
        except:
            pass

    def poll_progress_queue(self):
        try:
            while True:
                msg = self.progress_q.get_nowait()
                t = msg.get("type")

                if t == "progress":
                    text = msg.get("text", "")
                    key = msg.get("key")
                    fmt = msg.get("fmt") or {}
                    if key:
                        text = self.t(key, text)
                        if fmt:
                            try:
                                text = text.format(**fmt)
                            except Exception:
                                pass
                    self.step_label.configure(text=text)
                    self.log(text)
                    val = msg.get("value")
                    if val is not None:
                        self.progress_bar.set(val)

                elif t == "error":
                    title = msg.get("title", "Error")
                    err = msg.get("msg", "Unknown error.")
                    title_key = msg.get("title_key")
                    msg_key = msg.get("msg_key")
                    if title_key:
                        title = self.t(title_key, title)
                    if msg_key:
                        err = self.t(msg_key, err)
                    self.analysis_in_progress = False
                    messagebox.showerror(title, err)
                    self.show_input(from_back=True)
                    return

                elif t == "done":
                    payload = msg.get("payload")
                    self.analysis_in_progress = False
                    self.show_screen("output", payload=payload)
                    return
        except queue.Empty:
            pass

        # keep polling until done
        self.after(120, self.poll_progress_queue)

    def poll_upload_queue(self):
        try:
            while True:
                msg = self.upload_progress_q.get_nowait()
                t = msg.get("type")
                if t == "progress":
                    val = msg.get("value", 0.0)
                    text = msg.get("text", "")
                    key = msg.get("key")
                    if key:
                        text = self.t(key, text)
                    if hasattr(self, "upload_progress") and self.upload_progress and self.upload_progress.winfo_exists():
                        self.upload_progress.set(val)
                    if hasattr(self, "upload_progress_label") and self.upload_progress_label and self.upload_progress_label.winfo_exists():
                        self.upload_progress_label.configure(text=text)
        except queue.Empty:
            pass

        try:
            text = self.upload_queue.get_nowait()
            # If upload_queue contains a structured error, offer Retry
            if isinstance(text, dict) and text.get("__error__"):
                err = text.get("text", "OCR error")
                p = text.get("path")
                retry = messagebox.askretrycancel(self.t("ocr_error_title"), f"{err}\n\n{self.t('retry_prompt')}")
                if retry and p:
                    # restart upload for same path
                    self.start_upload_worker(p)
                    # continue polling
                    self.after(100, self.poll_upload_queue)
                    return
                else:
                    # Give user a visible message and hide progress
                    if hasattr(self, "upload_progress") and self.upload_progress and self.upload_progress.winfo_exists():
                        self.upload_progress.set(0.0)
                    if hasattr(self, "upload_progress_label") and self.upload_progress_label and self.upload_progress_label.winfo_exists():
                        self.upload_progress_label.configure(text=self.t("ocr_failed"))
                    self.after(
                        1000,
                        lambda: (
                            self.upload_progress.pack_forget() if hasattr(self, "upload_progress") and self.upload_progress and self.upload_progress.winfo_exists() else None,
                            self.upload_progress_label.pack_forget() if hasattr(self, "upload_progress_label") and self.upload_progress_label and self.upload_progress_label.winfo_exists() else None,
                        ),
                    )
                    self.upload_in_progress = False
                    return

            # Normal successful OCR text
            self.text_box.insert("end", "\n\n[OCR TEXT]\n" + text)
            if hasattr(self, "upload_progress") and self.upload_progress and self.upload_progress.winfo_exists():
                self.upload_progress.set(1.0)
            if hasattr(self, "upload_progress_label") and self.upload_progress_label and self.upload_progress_label.winfo_exists():
                self.upload_progress_label.configure(text=self.t("complete"))
            self.after(
                1000,
                lambda: (
                    self.upload_progress.pack_forget() if hasattr(self, "upload_progress") and self.upload_progress and self.upload_progress.winfo_exists() else None,
                    self.upload_progress_label.pack_forget() if hasattr(self, "upload_progress_label") and self.upload_progress_label and self.upload_progress_label.winfo_exists() else None,
                ),
            )
            self.upload_in_progress = False
        except queue.Empty:
            self.after(100, self.poll_upload_queue)

    # -----------------------------
    # ANALYZE
    # -----------------------------
    def analyze_clicked(self):
        self.last_input_text = self.text_box.get("1.0", "end").strip() if self.text_box else ""
        if not self.last_input_text:
            messagebox.showerror(self.t("input_error_title", "Input Error"), self.t("input_error_msg", "Paste supplement facts, a product link, or upload a label image."))
            return

        # refresh diet/exclusions from stored state (already set in show_input)
        # start worker
        self.analysis_in_progress = True  # Flag to prevent language refresh during analysis
        self.cancel_event = threading.Event()
        self.progress_q = queue.Queue()

        self.show_progress()

        self.worker_thread = threading.Thread(
            target=pipeline_run,
            args=(
                self.last_input_text,
                self.diet,
                self.exclusions,
                bool(self.try_fetch_var.get()),
                self.lang,
                self.progress_q,
                self.cancel_event
            ),
            daemon=True
        )
        self.worker_thread.start()

    # -----------------------------
    # OUTPUT
    # -----------------------------
    def show_output(self, payload: dict = None):
        """Results screen with dynamic language switching."""
        # If called through show_screen, get payload from kwargs
        if payload is None:
            payload = self.current_screen_kwargs.get("payload", {})
        
        # Track this screen for language refresh
        self.current_screen = "output"
        self.current_screen_kwargs = {"payload": payload}
        self.clear()

        supplement_dict = payload.get("supplement_dict", {})
        results = payload.get("results", {})
        debug = payload.get("debug", {})

        # Track paging offsets per nutrient for the "Show more" button
        self.output_offsets = {}
        self.output_categories = {}
        self.output_diverse_vars = {}

        # Create a container with both horizontal and vertical scrolling
        container = ctk.CTkFrame(self)
        container.pack(fill="both", expand=True, padx=12, pady=(12, 0))

        # Create fixed footer FIRST (packed first=positioned at bottom)
        footer = ctk.CTkFrame(container)
        footer.pack(side='bottom', fill='x', padx=0, pady=(6, 6))

        canvas = tk.Canvas(container, width=1030, height=640)
        hbar = tk.Scrollbar(container, orient='horizontal', command=canvas.xview)
        vbar = tk.Scrollbar(container, orient='vertical', command=canvas.yview)
        canvas.configure(xscrollcommand=hbar.set, yscrollcommand=vbar.set)

        hbar.pack(side='bottom', fill='x')
        vbar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)

        # Inner frame where we'll place CTk widgets
        main = ctk.CTkFrame(canvas)
        window_id = canvas.create_window((0, 0), window=main, anchor='nw')

        # Keep canvas scrollregion updated when inner frame size changes
        def _on_frame_config(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        main.bind("<Configure>", _on_frame_config)

        # Make inner frame width follow canvas width (responsive)
        def _on_canvas_config(event):
            canvas.itemconfig(window_id, width=event.width)
        canvas.bind("<Configure>", _on_canvas_config)

        # Mouse wheel scrolling (vertical). Shift+wheel -> horizontal.
        def _on_mousewheel(event):
            # Windows: event.delta is multiple of 120
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _on_shift_mousewheel(event):
            canvas.xview_scroll(int(-1 * (event.delta / 120)), "units")

        # Bind to the canvas widget and to all so mouse wheel works when pointer is over child widgets
        canvas.bind("<MouseWheel>", _on_mousewheel)
        canvas.bind("<Shift-MouseWheel>", _on_shift_mousewheel)

        header = ctk.CTkFrame(main)
        header.pack(fill="x", pady=(5, 12))

        # Create title frame for header content
        title_frame = ctk.CTkFrame(header)
        title_frame.pack(fill="x", padx=12, pady=(10, 0))
        
        ctk.CTkLabel(title_frame, text=self.t("app_header"), font=("Arial", 22, "bold")).pack(anchor="w", side="left")
        
        # Add language selector to the right
        self._make_lang_selector(title_frame)
        
        diet_txt = self.t("diet_exclusions").format(
            diet=debug.get("diet") or self.t("default_diet", "Omnivore"),
            exclusions=", ".join(debug.get("exclusions") or []) or self.t("none", "none"),
        )
        diet_box = ctk.CTkTextbox(header, width=900, height=50, font=("Arial", 13), state="disabled")
        diet_box.pack(anchor="w", padx=12, pady=(0, 10), fill="x")
        diet_box.configure(state="normal")
        diet_box.insert("end", diet_txt)
        diet_box.configure(state="disabled")

        # (Legend moved to the summary area so it remains visible while browsing nutrients)

        # Debug panel
        if self.debug_var.get():
            dbg = ctk.CTkFrame(main)
            dbg.pack(fill="x", pady=(0, 12))
            ctk.CTkLabel(dbg, text=self.t("debug_label"), font=("Arial", 16, "bold")).pack(anchor="w", padx=12, pady=(10, 5))

            ctk.CTkLabel(dbg, text=self.t("input_used"), font=("Arial", 13, "bold")).pack(anchor="w", padx=12)
            box1 = ctk.CTkTextbox(dbg, width=980, height=120, state="disabled")
            box1.pack(padx=12, pady=6)
            box1.configure(state="normal")
            box1.insert("end", debug.get("input_used", ""))
            box1.configure(state="disabled")

            ctk.CTkLabel(dbg, text=self.t("raw_parse_output"), font=("Arial", 13, "bold")).pack(anchor="w", padx=12)
            box2 = ctk.CTkTextbox(dbg, width=980, height=80, state="disabled")
            box2.pack(padx=12, pady=(0, 12))
            box2.configure(state="normal")
            box2.insert("end", debug.get("parsed_json_raw", ""))
            box2.configure(state="disabled")

        # If multiple micronutrients were parsed, show a dropdown to select one at a time
        nutrient_names = list(supplement_dict.keys())

        if not nutrient_names:
            ctk.CTkLabel(main, text=self.t("no_micronutrients"), font=("Arial", 14)).pack(padx=12, pady=12)
        else:
            # Option menu: display title-cased nutrient names but keep mapping to original keys
            display_names = [n.title() for n in nutrient_names]
            display_to_key = {d: k for d, k in zip(display_names, nutrient_names)}
            sel_var = ctk.StringVar(value=display_names[0])
            opt = ctk.CTkOptionMenu(header, values=display_names, variable=sel_var)
            opt.pack(anchor="e", padx=12, pady=(0, 6))

            # Frame to host the single nutrient card
            nut_container = ctk.CTkFrame(main)
            nut_container.pack(fill="both", expand=False, padx=6, pady=6)

            def render_nutrient(nut_name):
                # clear existing
                for w in nut_container.winfo_children():
                    w.destroy()

                target_mg = supplement_dict.get(nut_name)
                card = ctk.CTkFrame(nut_container)
                card.pack(fill="x", pady=10, padx=6)

                # Header with color swatch for the nutrient
                hdr = ctk.CTkFrame(card)
                hdr.pack(fill="x", pady=(8, 0), padx=12)
                sw_color = get_nutrient_color(nut_name)
                # small colored swatch
                sw = ctk.CTkLabel(hdr, text=" ", fg_color=(sw_color, sw_color), width=24, corner_radius=6)
                sw.pack(side="left", padx=(0,8))
                ctk.CTkLabel(hdr, text=nut_name.title(), font=("Arial", 18, "bold")).pack(side="left", anchor="w")
                
                # Health benefit description
                benefit = MICRONUTRIENT_BENEFITS.get(nut_name.lower(), self.t("default_nutrient_benefit", "Essential micronutrient for health"))
                benefit_box = ctk.CTkTextbox(card, width=900, height=50, font=("Arial", 11), state="disabled")
                benefit_box.pack(anchor="w", padx=12, pady=(4, 0), fill="x")
                benefit_box.configure(state="normal")
                benefit_box.insert("end", benefit)
                benefit_box.configure(state="disabled")
                
                # Supplement dose
                dose_box = ctk.CTkTextbox(card, width=900, height=40, font=("Arial", 13), state="disabled")
                dose_box.pack(anchor="w", padx=12, pady=(3, 8), fill="x")
                dose_box.configure(state="normal")
                dose_box.insert("end", self.t("supplement_dose").format(dose=target_mg))
                dose_box.configure(state="disabled")

                all_picks = results.get(nut_name, [])
                if nut_name not in self.output_offsets:
                    self.output_offsets[nut_name] = 0
                if nut_name not in self.output_categories:
                    self.output_categories[nut_name] = self.t("all_categories", "All categories")
                if nut_name not in self.output_diverse_vars:
                    self.output_diverse_vars[nut_name] = ctk.BooleanVar(value=False)

                if not all_picks:
                    ctk.CTkLabel(
                        card,
                        text=self.t("no_valid_matches"),
                        font=("Arial", 13)
                    ).pack(anchor="w", padx=12, pady=(0, 10))
                    return

                # Category filter + diversity toggle
                categories = []
                seen_cats = set()
                for p in all_picks:
                    cat = p.get("category") or self.t("uncategorized", "Uncategorized")
                    if cat in seen_cats:
                        continue
                    seen_cats.add(cat)
                    categories.append(cat)
                cat_label = self.t("filter_category", "Category")
                all_label = self.t("all_categories", "All categories")

                filter_bar = ctk.CTkFrame(card, fg_color="transparent")
                filter_bar.pack(fill="x", padx=12, pady=(0, 6))
                ctk.CTkLabel(filter_bar, text=f"{cat_label}:", font=("Arial", 11, "bold")).pack(side="left")

                cat_var = ctk.StringVar(value=self.output_categories.get(nut_name, all_label))
                cat_values = [all_label] + categories

                def _on_cat_change(val):
                    self.output_categories[nut_name] = val
                    self.output_offsets[nut_name] = 0
                    render_nutrient(nut_name)

                cat_menu = ctk.CTkOptionMenu(filter_bar, values=cat_values, variable=cat_var, command=_on_cat_change, width=260)
                cat_menu.pack(side="left", padx=8)

                def _on_diverse_toggle():
                    self.output_offsets[nut_name] = 0
                    render_nutrient(nut_name)

                ctk.CTkCheckBox(
                    filter_bar,
                    text=self.t("diverse_categories", "Only show different categories"),
                    variable=self.output_diverse_vars[nut_name],
                    command=_on_diverse_toggle
                ).pack(side="left", padx=12)

                selected_category = self.output_categories.get(nut_name, all_label)
                filtered = all_picks
                if selected_category != all_label:
                    filtered = [p for p in filtered if (p.get("category") or self.t("uncategorized", "Uncategorized")) == selected_category]

                if self.output_diverse_vars[nut_name].get():
                    seen = set()
                    diverse = []
                    for p in filtered:
                        cat = p.get("category") or self.t("uncategorized", "Uncategorized")
                        if cat in seen:
                            continue
                        seen.add(cat)
                        diverse.append(p)
                    filtered = diverse

                if not filtered:
                    ctk.CTkLabel(
                        card,
                        text=self.t("no_valid_matches", "No valid whole-food matches found for this nutrient."),
                        font=("Arial", 13)
                    ).pack(anchor="w", padx=12, pady=(0, 10))
                    return

                offset = self.output_offsets.get(nut_name, 0)
                if offset >= len(filtered):
                    offset = 0
                    self.output_offsets[nut_name] = 0
                top5 = filtered[offset:offset + 5]

                # Clean table layout with proper column structure
                table_frame = ctk.CTkFrame(card)
                table_frame.pack(fill="x", padx=12, pady=(8, 12))

                # Header row with background emphasis
                header_row = ctk.CTkFrame(table_frame, fg_color=("#f0f0f0", "#2b2b2b"), corner_radius=6)
                header_row.pack(fill="x", pady=(0, 1))

                header_data = [
                    ("#", 45), 
                    (self.t("col_food"), 220), 
                    (self.t("col_per100g"), 90),
                    (self.t("col_required_grams"), 110), 
                    (self.t("col_grams_for_rda"), 100),
                    (self.t("col_practicality"), 80), 
                    (self.t("col_refs"), 60)
                ]

                for col_text, col_width in header_data:
                    col = ctk.CTkFrame(header_row, fg_color="transparent")
                    col.pack(side="left", padx=6, pady=8)
                    ctk.CTkLabel(col, text=col_text, font=("Arial", 10, "bold"), text_color=("#000000", "#ffffff")).pack(anchor="w", expand=False)

                # Data rows with alternating background
                for i, item in enumerate(top5, start=1):
                    # Alternate row colors for readability
                    row_bg = ("#ffffff", "#1f1f1f") if i % 2 == 0 else ("#f9f9f9", "#252525")
                    
                    row_frame = ctk.CTkFrame(table_frame, fg_color=row_bg, corner_radius=4)
                    row_frame.pack(fill="x", pady=1)

                    # Rank
                    rank_col = ctk.CTkFrame(row_frame, fg_color="transparent", width=45)
                    rank_col.pack(side="left", padx=6, pady=8)
                    ctk.CTkLabel(rank_col, text=str(i), font=("Arial", 11, "bold")).pack(anchor="w")

                    # Food name
                    food_col = ctk.CTkFrame(row_frame, fg_color="transparent", width=220)
                    food_col.pack(side="left", padx=6, pady=8)
                    food_name = item.get("food", "")
                    ctk.CTkLabel(food_col, text=food_name, font=("Arial", 11)).pack(anchor="w")

                    # Per 100g
                    p100_col = ctk.CTkFrame(row_frame, fg_color="transparent", width=90)
                    p100_col.pack(side="left", padx=6, pady=8)
                    ctk.CTkLabel(p100_col, text=str(item.get("per_100g", "")), font=("Arial", 10)).pack(anchor="w")

                    # Required grams
                    req_col = ctk.CTkFrame(row_frame, fg_color="transparent", width=110)
                    req_col.pack(side="left", padx=6, pady=8)
                    ctk.CTkLabel(req_col, text=str(item.get("required_grams", "")), font=("Arial", 10)).pack(anchor="w")

                    # Grams for RDA
                    rda_col = ctk.CTkFrame(row_frame, fg_color="transparent", width=100)
                    rda_col.pack(side="left", padx=6, pady=8)
                    rda_display = item.get("grams_for_rda", "")
                    if rda_display is None:
                        rda_display = self.t("not_available", "N/A")
                    ctk.CTkLabel(rda_col, text=str(rda_display), font=("Arial", 10)).pack(anchor="w")

                    # Practicality (with visual indicator)
                    prac_col = ctk.CTkFrame(row_frame, fg_color="transparent", width=80)
                    prac_col.pack(side="left", padx=6, pady=8)
                    prac_raw = item.get("practicality", "")
                    if "✔" in prac_raw:
                        prac_text = self.t("practical", "✔ Practical")
                    elif "⚠" in prac_raw:
                        prac_text = self.t("large", "⚠ Large")
                    else:
                        prac_text = self.t("impractical", "— Impractical")
                    prac_color = "#4CAF50" if "✔" in prac_raw else "#FF9800" if "⚠" in prac_raw else "#f44336"
                    ctk.CTkLabel(prac_col, text=prac_text, font=("Arial", 10, "bold"), text_color=prac_color).pack(anchor="w")

                    # Reference button
                    ref_col = ctk.CTkFrame(row_frame, fg_color="transparent", width=60)
                    ref_col.pack(side="left", padx=6, pady=8)
                    fdc_id = item.get("fdcId")
                    if fdc_id:
                        ctk.CTkButton(ref_col, text=self.t("usda", "USDA"), width=50, height=24, font=("Arial", 9), command=lambda fid=fdc_id: open_usda_link(fid)).pack(side="left")
                    else:
                        ctk.CTkLabel(ref_col, text=self.t("dash", "—"), font=("Arial", 10)).pack(anchor="w")

                # Paging controls for additional results
                if len(filtered) > 5:
                    controls = ctk.CTkFrame(card, fg_color="transparent")
                    controls.pack(fill="x", padx=12, pady=(0, 8))
                    start_idx = offset + 1
                    end_idx = min(offset + 5, len(filtered))
                    ctk.CTkLabel(
                        controls,
                        text=self.t("showing_range", "Showing {start}-{end} of {total}").format(start=start_idx, end=end_idx, total=len(filtered)),
                        font=("Arial", 10)
                    ).pack(side="left")

                    def _show_more():
                        current = self.output_offsets.get(nut_name, 0)
                        new_offset = current + 5
                        if new_offset >= len(filtered):
                            new_offset = 0
                        self.output_offsets[nut_name] = new_offset
                        render_nutrient(nut_name)

                    def _show_prev():
                        current = self.output_offsets.get(nut_name, 0)
                        new_offset = current - 5
                        if new_offset < 0:
                            remainder = len(filtered) % 5
                            if remainder == 0:
                                new_offset = len(filtered) - 5
                            else:
                                new_offset = len(filtered) - remainder
                        self.output_offsets[nut_name] = new_offset
                        render_nutrient(nut_name)

                    at_start = offset == 0
                    at_end = (offset + 5) >= len(all_picks)

                    if at_end:
                        ctk.CTkButton(
                            controls,
                            text=self.t("show_prev", "Show previous"),
                            width=120,
                            command=_show_prev
                        ).pack(side="right")
                    elif at_start:
                        ctk.CTkButton(
                            controls,
                            text=self.t("show_more", "Show more"),
                            width=120,
                            command=_show_more
                        ).pack(side="right")
                    else:
                        ctk.CTkButton(
                            controls,
                            text=self.t("show_prev", "Show previous"),
                            width=120,
                            command=_show_prev
                        ).pack(side="right", padx=(6, 0))
                        ctk.CTkButton(
                            controls,
                            text=self.t("show_more", "Show more"),
                            width=120,
                            command=_show_more
                        ).pack(side="right")

                # Benefits section below table (grouped, not per-row)
                if any(item.get("benefits") for item in top5):
                    benefits_title = ctk.CTkLabel(card, text=self.t("col_benefits"), font=("Arial", 11, "bold"))
                    benefits_title.pack(anchor="w", padx=12, pady=(8, 4))
                    
                    for i, item in enumerate(top5, start=1):
                        benefits = item.get("benefits") or []
                        if benefits:
                            benefits_txt = " • ".join(benefits)
                            food_name = item.get("food", "")
                            benefit_label = ctk.CTkLabel(
                                card, 
                                text=f"{i}. {food_name}: {benefits_txt}", 
                                font=("Arial", 10), 
                                wraplength=880
                            )
                            benefit_label.pack(anchor="w", padx=12, pady=2)

                # Update scrollregion so both scrollbars reflect full content
                main.update_idletasks()
                canvas.configure(scrollregion=canvas.bbox("all"))

            # render initial selection (map display name -> key)
            first_key = display_to_key.get(display_names[0], nutrient_names[0])
            render_nutrient(first_key)

            # bind selection change (map the selected display name back to the original key)
            def _on_select(display_val):
                key = display_to_key.get(display_val)
                if key:
                    render_nutrient(key)
            opt.configure(command=_on_select)

        # Final summary - CLEAN AND CONSOLIDATED
        summary = ctk.CTkFrame(main)
        summary.pack(fill="x", pady=(16, 12), padx=12)

        # Summary section title with better hierarchy
        ctk.CTkLabel(summary, text=self.t("summary"), font=("Arial", 16, "bold")).pack(anchor="w", pady=(0, 10))

        # Legend in a cleaner format
        legend_frame = ctk.CTkFrame(summary, fg_color=("#f5f5f5", "#2b2b2b"), corner_radius=4)
        legend_frame.pack(fill="x", pady=(0, 12), padx=0)

        legend_content = ctk.CTkFrame(legend_frame, fg_color="transparent")
        legend_content.pack(fill="x", padx=10, pady=8)

        # Legend items in rows
        row1 = ctk.CTkFrame(legend_content, fg_color="transparent")
        row1.pack(fill="x", pady=4)
        ctk.CTkLabel(row1, text=self.t("legend_practical", "✔ Practical"), font=("Arial", 10), text_color="#4CAF50").pack(side="left", padx=(0, 20))
        ctk.CTkLabel(row1, text=self.t("legend_large_amount", "⚠ Large amount needed"), font=("Arial", 10), text_color="#FF9800").pack(side="left", padx=(0, 20))
        ctk.CTkLabel(row1, text=self.t("legend_impractical", "✗ Impractical"), font=("Arial", 10), text_color="#f44336").pack(side="left", padx=(0, 20))

        # Nutrient color swatches
        if nutrient_names:
            row2 = ctk.CTkFrame(legend_content, fg_color="transparent")
            row2.pack(fill="x", pady=4)
            ctk.CTkLabel(row2, text=self.t("legend_nutrients", "Nutrients:"), font=("Arial", 10, "bold")).pack(side="left", padx=(0, 8))
            
            swatch_frame = ctk.CTkFrame(row2, fg_color="transparent")
            swatch_frame.pack(side="left", fill="x", expand=True)
            
            for nn in nutrient_names:
                col = get_nutrient_color(nn)
                color_item = ctk.CTkFrame(swatch_frame, fg_color="transparent")
                color_item.pack(side="left", padx=(0, 12))
                swatch = ctk.CTkLabel(color_item, text=" ", fg_color=(col, col), width=16, corner_radius=3)
                swatch.pack(side="left", padx=(0, 4))
                ctk.CTkLabel(color_item, text=nn.title(), font=("Arial", 9)).pack(side="left")

        # Overall verdict (concise label, not a textbox)
        verdict_section = ctk.CTkFrame(summary, fg_color="transparent")
        verdict_section.pack(fill="x", pady=(8, 0))
        
        all_items = []
        for items in results.values():
            all_items.extend((items or [])[:5])

        if all_items and all("✔" in it.get("practicality", "") for it in all_items):
            verdict = self.t("verdict_all_practical")
            verdict_color = "#4CAF50"
        else:
            verdict = self.t("verdict_mixed")
            verdict_color = "#FF9800"

        ctk.CTkLabel(verdict_section, text=self.t("verdict") + ":", font=("Arial", 11, "bold")).pack(anchor="w")
        verdict_label = ctk.CTkLabel(
            verdict_section, 
            text=verdict, 
            font=("Arial", 11),
            text_color=verdict_color,
            wraplength=880
        )
        verdict_label.pack(anchor="w", pady=(2, 8))

        # Personalized plan
        plan_lines = []
        sunlight_present = False
        for nut, items in results.items():
            if items:
                best = items[0]
                food = best.get('food', 'Unknown')
                grams = best.get('required_grams', 'N/A')
                plan_lines.append(f"• {nut.title()}: ~{grams}g {food}")
                if isinstance(food, str) and food.strip().lower() in ('sunlight', 'uv exposure'):
                    sunlight_present = True

        ctk.CTkLabel(summary, text=self.t("plan") + ":", font=("Arial", 11, "bold")).pack(anchor="w", pady=(8, 2))
        
        if plan_lines:
            plan_text = "\n".join(plan_lines)
            plan_label = ctk.CTkLabel(
                summary, 
                text=plan_text, 
                font=("Arial", 10),
                wraplength=880,
                justify="left"
            )
            plan_label.pack(anchor="w", pady=(2, 8))

        # Sunlight note if present (inline, not a textbox)
        if sunlight_present:
            sun_label = ctk.CTkLabel(
                summary,
                text=self.t("sunlight_note"),
                font=("Arial", 10),
                text_color=("#666666", "#999999"),
                wraplength=880,
                justify="left"
            )
            sun_label.pack(anchor="w", pady=(4, 8))

        # Final recommendation (prominent but clean)
        final_bits = []
        for nut, items in results.items():
            if items:
                best = items[0]
                food = best.get('food', '')
                grams = best.get('required_grams')
                if grams is None:
                    final_bits.append(f"{food}")
                else:
                    final_bits.append(f"{grams}g {food}")

        if final_bits:
            final_sentence = self.t("final_recommendation_prefix") + " " + " + ".join(final_bits) + "."
        else:
            final_sentence = self.t("final_recommendation_prefix") + " " + self.t("no_practical_replacement")

        final_frame = ctk.CTkFrame(summary, fg_color=("#e8f5e9", "#1b5e20"), corner_radius=6)
        final_frame.pack(fill="x", pady=(12, 0), padx=0)
        
        final_label = ctk.CTkLabel(
            final_frame,
            text=final_sentence,
            font=("Arial", 11, "bold"),
            wraplength=880,
            justify="left",
            text_color=("#1b5e20", "#c8e6c9")
        )
        final_label.pack(anchor="w", padx=12, pady=10)

        # Navigation buttons moved to fixed footer (outside scrollable area)
        nav = ctk.CTkFrame(footer)
        nav.pack(pady=10)
        ctk.CTkButton(nav, text=self.t("back_to_input"), command=self.go_input, width=180).pack(side="left", padx=10)
        ctk.CTkButton(nav, text=self.t("back_to_diet"), command=self.go_diet, width=180).pack(side="left", padx=10)

        # Re-place watermark after output screen is fully rendered (delayed to ensure it appears on top)
        self.after(100, self.place_watermark)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")
    app = App()
    app.mainloop()