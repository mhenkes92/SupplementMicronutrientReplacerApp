"""Phone photos (MPO JPEGs) are read, and product links whose facts table is only a picture (Amazon) are read from the
product's own gallery images."""
import io

import blockbrain.app as bb
from PIL import Image


def _label_image(size=(4032, 3024)):
    return Image.new("RGB", size, "white")


def test_phone_mpo_photo_is_decoded_not_refused():
    big = _label_image()
    buf = io.BytesIO()
    big.save(buf, "MPO", save_all=True, append_images=[big.resize((1008, 756))])
    variants = bb.build_vision_image_variants(buf.getvalue())
    assert [name for name, _ in variants] == ["fast_jpeg", "detail_jpeg"]


def test_big_png_is_still_refused():
    buf = io.BytesIO()
    _label_image().save(buf, "PNG")
    assert bb.build_vision_image_variants(buf.getvalue()) == []


def test_amazon_ad_click_link_is_unwrapped_to_the_product():
    url = ("https://www.amazon.de/-/en/sspa/click?ie=UTF8&spc=abc&url=%2Fnatural-elements-Premium%2Fdp%2FB07RFLY8DZ%2Fref%3Dsr_1_1"
           "%3Fpsc%3D1&aref=x")
    assert bb._unwrap_shop_redirect(url) == "https://www.amazon.de/natural-elements-Premium/dp/B07RFLY8DZ/ref=sr_1_1?psc=1"
    assert bb._unwrap_shop_redirect("https://shop.test/p?url=https://evil.test/") == "https://shop.test/p?url=https://evil.test/"
    assert bb._unwrap_shop_redirect("https://www.amazon.de/sspa/click?url=//evil.test/x").startswith("https://www.amazon.de/sspa/")


_GALLERY = """<html><body><h1>natural elements Premium Multivitamin - 180 capsules</h1>
<p>About this item: all-round wellness with 21 vital nutrients such as vitamin C and magnesium.</p>
<img data-old-hires="https://m.media-amazon.com/images/I/front.jpg">
<script>'colorImages': { 'initial': [{"hiRes":"https://m.media-amazon.com/images/I/front.jpg"},
{"hiRes":"https://m.media-amazon.com/images/I/facts.jpg"},{"hiRes":"https://m.media-amazon.com/images/I/claims.jpg"}]}</script>
<div id="similar"><img src="https://m.media-amazon.com/images/I/other-product.jpg"></div></body></html>"""

_FACTS = ("Premium Multi\npro Tagesdosis (2 Kapseln)\nVitamin C 200mg 250%\nVitamin D3 20µg 400%\nZink 6,5mg 65%\nSelen 50µg 91%\n"
          "Magnesium 150mg 40%\nVitamin B12 20µg 800%\nVitamin E 12mg 100%\nVitamin K 75µg 100%\nBiotin 145µg 290%")
_CLAIMS = "Vitamin C 200 mg for your immune system! Magnesium 150 mg against tiredness! Zinc 10 mg for skin."


def test_gallery_urls_are_the_product_images_only():
    urls = bb.product_image_urls(_GALLERY, "https://www.amazon.de/dp/X")
    assert urls == ["https://m.media-amazon.com/images/I/front.jpg", "https://m.media-amazon.com/images/I/facts.jpg",
                    "https://m.media-amazon.com/images/I/claims.jpg"]


def test_link_with_facts_only_in_a_picture_reads_the_facts_image(monkeypatch):
    reads = {"front.jpg": "Premium Multi natural elements 180 Kapseln", "facts.jpg": _FACTS, "claims.jpg": _CLAIMS}
    monkeypatch.setattr(bb, "_read_product_image", lambda url: reads[url.rsplit("/", 1)[-1]])
    monkeypatch.setattr(bb, "_text_llm_available", lambda: True)

    def fake_get(url, headers=None, timeout=None):
        return 200, {"content-type": "text/html"}, _GALLERY

    monkeypatch.setattr(bb, "_safe_public_get", fake_get)
    text_calls = []
    monkeypatch.setattr(bb, "call_text_llm", lambda *a, **k: text_calls.append(a) or "NONE")
    out = bb.extract_supplement_text_from_url("https://www.amazon.de/dp/X", llm_allowed=lambda: True)
    assert out == _FACTS
    assert bb.LAST_TEXT_PROVIDER == "Blockbrain vision (product images)"
    assert not text_calls


def test_link_image_step_respects_the_quota(monkeypatch):
    monkeypatch.setattr(bb, "_read_product_image", lambda url: _FACTS)
    monkeypatch.setattr(bb, "_text_llm_available", lambda: True)
    monkeypatch.setattr(bb, "_safe_public_get", lambda url, headers=None, timeout=None: (200, {"content-type": "text/html"}, _GALLERY))
    monkeypatch.setattr(bb, "call_text_llm", lambda *a, **k: "NONE")
    assert bb.extract_supplement_text_from_url("https://www.amazon.de/dp/X", llm_allowed=lambda: False) == ""


def test_text_prompt_asks_for_the_product_label_only(monkeypatch):
    monkeypatch.setattr(bb, "_text_llm_available", lambda: True)
    monkeypatch.setattr(bb, "_safe_public_get", lambda url, headers=None, timeout=None: (200, {"content-type": "text/html"}, "<p>x</p>" * 30))
    seen = {}
    monkeypatch.setattr(bb, "call_text_llm", lambda system, user, *a, **k: seen.setdefault("system", system) and "NONE")
    bb.extract_supplement_text_from_url("https://shop.test/p", llm_allowed=lambda: True)
    assert "ONE product" in seen["system"] and "Ignore marketing claims" in seen["system"]
