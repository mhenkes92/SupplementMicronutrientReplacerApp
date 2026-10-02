"""What counts as a Blockbrain error (not an answer) and as a vision model that never got the image.

These are pure functions of the reply text (blockbrain/app.py: looks_like_agent_error, _image_not_received). They guard
every reply of blockbrain_llm_client.py: a platform error written into a 200 stream as if it were the answer must never
reach the UI or a cache."""
from __future__ import annotations

import pytest

import blockbrain.app as bb


MODEL_ERROR = "[Agent researchAgent] - Failed to resolve model configuration"


def test_looks_like_agent_error():
    assert bb.looks_like_agent_error(MODEL_ERROR)
    assert bb.looks_like_agent_error("[agent customAgent]: timeout")
    assert not bb.looks_like_agent_error("**Breakfast** — oats with berries")
    assert not bb.looks_like_agent_error("[Agent] tips: eat more beans " + "x" * 500)


@pytest.mark.parametrize("text", [
    "[Agent researchAgent] - Failed to resolve model configuration",
    "[Agent researchAgent] Failed to resolve model configuration",
    "[Agent researchAgent] — Failed to resolve model configuration",
    "[Agent researchAgent]: Failed to resolve model configuration",
    "Failed to resolve model configuration",
    "Error: Failed to resolve model configuration.",
])
def test_error_variants_are_recognised(text):
    """Review F3."""
    assert bb.looks_like_agent_error(text)


IMAGE_REPLIES = [
 # blind-model replies
 ("No image has been attached to this message.", True),
 ("No file, image, or document has been attached.", True),
 ("I don't see any image attached. Please upload the label.", True),
 ("I do not see any image in your message.", True),
 ("There is no image in this conversation.", True),
 ("I can't see any images - only text was provided.", True),
 ("Please attach an image of the supplement label and I'll extract the nutrients.", True),
 ("There is nothing for me to extract - no label image was shared.", True),
 ("I don't see an image attached. Could you please upload a clear photo of the label?", True),
 ("Please upload a clear image of the supplement label so I can extract the nutrients.", True),
 ("Please provide a clear photo of the nutrition table and I will read it for you.", True),
 ("I'm unable to view images. Please paste the label text.", True),
 ("No image attached.", True),
 ("No image provided.", True),
 ("No image was provided. Please send a sharp photo of the label.", True),
 ("There's no image attached to your message.", True),
 ("It looks like no image came through. Could you try uploading it again?", True),
 ("The image did not come through. Please upload it again.", True),
 ("I can only see text, not an image. Could you re-upload?", True),
 ("I'm sorry, but I can't see an image in our conversation. Could you upload the label photo?", True),
 ("Sorry, I don't have the ability to see images in this chat.", True),
 ("I cannot process images with this configuration.", True),
 ("As a text-based model I can't analyze images.", True),
 ("Es wurde kein Bild angehängt.", True),
 ("Ich kann kein Bild sehen. Bitte laden Sie ein Foto des Etiketts hoch.", True),
 ("Es wurde kein Bild hochgeladen.", True),
 ("Bitte laden Sie ein Bild des Nahrungsergänzungsmittel-Etiketts hoch.", True),
 ("Ich sehe kein Bild in Ihrer Nachricht.", True),
 ("No label image was attached; please attach a clear picture of the Supplement Facts.", True),
 # real answers (must NOT be treated as image-not-received)
 ("I can't see the image clearly enough to read the nutrition table. Please take a sharper photo.", False),
 ("I cannot see the photo well, it is too dark. Please retake it in better light.", False),
 ("Das Foto ist zu unscharf, bitte ein schärferes Bild aufnehmen.", False),
 ("Vitamin C 80 mg 100%\nVitamin D3 20 µg 400%\nZink 10 mg 100%\n(I can't see the image's lower part, it is cut off.)", False),
 ("Centrum Men\nVitamin D 25 µg\nNote: I could not see the picture's right edge.", False),
 ("Centrum Men\nServing size 1 tablet\nNo image of the back panel was provided, so only the front is read.", False),
 ("Product: Nature Made Fish Oil\nNo nutrient table is visible in the photo.", False),
 ("The photo shows only the front of the box: Brand X Multivitamin. No supplement facts panel is visible.", False),
 ("Das Foto zeigt nur die Vorderseite: Doppelherz Magnesium. Keine Nährwerttabelle sichtbar.", False),
 ("Doppelherz Magnesium 400\nPro Tablette: Magnesium 400 mg", False),
 ("Vitamin D3 20 µg (800 I.E.) 400%\nNote: no photo of the ingredients list was provided.", False),
 ("Magnesium Citrate\nNo document attached to the label; amounts per serving: Magnesium 200 mg", False),
 ("Vitamin B12 (as methylcobalamin) 500 mcg\nVitamin B6 5 mg\nPlease attach the second photo for the ingredients.", False),
]


@pytest.mark.parametrize("reply, blind", IMAGE_REPLIES)
def test_image_not_received_wordings(reply, blind):
    """Review FRV-5: what a model that never got the photo says, in English and German, versus
    what a model that did see it says about it (quality, label text with a note)."""
    assert bb._image_not_received(reply) is blind


@pytest.mark.parametrize("text", [
    "AI_APICallError: Rate limit exceeded",
    "AI_APICallError: Insufficient credits",
    "[Agent 6a4bc43653952e29ba6ef1d6] - Failed to resolve model configuration for model gpt-4.1-nano. Details: " + "x" * 600,
    "Failed to resolve model configuration. " + "The pinned model is not available. " * 30,
    "[Agent Orange label reader] - something failed " + "y" * 800,
    "Error: Rate limit exceeded",
    "Error: Insufficient credits",
    "Error: Request failed with status code 429",
    '{"error": "Rate limit exceeded"}',
    "Internal Server Error",
    "Service Unavailable",
    "Unauthorized",
])
def test_longer_and_other_error_shapes_are_recognised(text):
    """Review NEW-ERROR-SHAPES / NEW-LONG-ERROR-TEXT: no length cap for the exact phrase or the [Agent …] prefix."""
    assert bb.looks_like_agent_error(text)


@pytest.mark.parametrize("answer", [
    "Vitamin D: 25 µg per day is plenty. In case of an error in dosing, ask your doctor.",
    "[Agent-based models] are not used for nutrient advice. " + "z" * 600,
    "Der Fehler liegt oft an der Dosierung; Magnesium 300 mg am Abend ist üblich.",
    "Eisen: take it with vitamin C. [Agent Orange](https://example.org) is unrelated.",
    "Error bars in the study show the effect of magnesium is not significant.",
    "Error: dose not found on the label for Selen, so I assumed 55 µg.",
    "The service unavailable message appears when the product page is down; the label itself lists Zinc 10 mg.",
])
def test_real_answers_are_never_mistaken_for_errors(answer):
    assert not bb.looks_like_agent_error(answer)


@pytest.mark.parametrize("text", [
    "AI_RetryError: Failed after 3 attempts. Last error: Rate limit exceeded",
    "AI_LoadAPIKeyError: API key is missing",
    "Error code: 404 - {'error': {'message': 'The model `gpt-4.1-nano` does not exist', 'code': 'model_not_found'}}",
    "openai.NotFoundError: The model `gpt-4.1-nano` does not exist",
    "Rate limit exceeded. Please try again later.",
    "HTTP 502 Bad Gateway",
    "Error 503: Service Unavailable",
    "502 Bad Gateway",
    "Something went wrong. Please try again.",
    "An error occurred.",
    "Failed to fetch",
    "⚠️ Error: Rate limit exceeded",
])
def test_more_error_shapes_are_recognised(text):
    """Review ERRTXT-1."""
    assert bb.looks_like_agent_error(text)


@pytest.mark.parametrize("reply", [
    "I'm not seeing an image in your message.", "The image appears to be missing.", "The image wasn't attached.",
    "There doesn't appear to be an image attached.", "It looks like your message didn't include an image.",
    "I have not received an image.", "I'm a text-based assistant, so I can't look at photos.",
    "Ich sehe leider kein Bild.", "Ich habe kein Bild erhalten.", "Es wurde kein Bild gesendet.",
    "Je ne vois aucune image.", "No veo ninguna imagen.",
])
def test_more_blind_model_wordings(reply):
    """Review IMG-1."""
    assert bb._image_not_received(reply)


@pytest.mark.parametrize("reply", [
    "Nature Made Vitamin D3\nPlease upload a photo of the Supplement Facts panel.",
    "Centrum Silver Women 50+\nNo nutrient table visible. Please send a photo of the back of the package.",
    "Doppelherz Magnesium\nBitte laden Sie ein Foto der Rückseite hoch.",
    "Garden of Life Vitamin Code\nNo photo of the supplement facts was provided, only the front of the bottle.",
    "Doppelherz Magnesium 400",
    "Orthomol Immun",
])
def test_a_read_of_the_front_of_the_pack_is_an_answer(reply):
    """Review IMG-2: a product name followed by a note is label text for the product research."""
    assert not bb._image_not_received(reply)


@pytest.mark.parametrize("answer", [
    "Short answer: the 'more is better' model is not supported by the evidence — 25 µg/day is plenty.",
    "Not found — this product model is not found in the databases I checked.",
    "Unknown model number; please photograph the Supplement Facts panel instead.",
    "[Agent Orange](https://example.org) was a herbicide; it is unrelated to vitamin D.",
    "[Agent-based models] are not used for nutrient advice.",
])
def test_short_real_answers_are_kept(answer):
    """An answer that merely mentions "model … not supported" is an answer."""
    assert not bb.looks_like_agent_error(answer)
