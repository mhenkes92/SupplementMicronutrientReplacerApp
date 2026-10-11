"""A stand-in for Streamlit around the swipe card's page, so a browser test can draw the card with ANY props (a verdict, no athlete target,
a long label) and measure it, without a Streamlit server and without a scan.

The card is `swipe_component/index.html` in an iframe. This module serves it from the repo (read at request time) inside a host page of
the same origin that plays the few parts of Streamlit the card talks to: `componentReady` is answered with a `streamlit:render` message
carrying the props, `setFrameHeight` sizes the iframe, and the props are sent AGAIN after every height change (Streamlit does that, and
the card must ignore the repeat), `setComponentValue` is collected in `window.__events`. The card reads its room from the host page
(`[data-testid="stMain"]`, the fixed bottom bar `[class~="st-key-appbar"]`, the frame's top): the host draws a bar and a top offset of the
real app's size (the sample label at 390x844: frame top 86 px, bar 58 px high, 8 px above the screen's foot; 320x568: top 55, bar 54 high
and 6 px above the foot), so the card tightens and scrolls here as it does in the app. `args` below are the props the app passes
(swipe_mobile_app/app.py, `tinder_swipe(...)`); `card_args()` builds a complete set. Not a test module."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "swipe_mobile_app" / "swipe_component" / "index.html"

HOST = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>host</title>
<style>
  html, body { margin: 0; padding: 0; background: #f8fbff; font-family: sans-serif; }
  [data-testid="stMain"] { position: absolute; inset: 0; overflow: auto; }
  #slot { margin: 0 16px; padding-top: __TOP__px; }
  iframe { display: block; width: 100%; border: 0; }
  .st-key-appbar { position: fixed; left: 16px; right: 16px; bottom: __BOTTOM__px; height: __BAR__px; background: #fff; border-radius: 16px;
                   box-shadow: 0 2px 10px rgba(15,23,42,.18); }
</style></head><body>
<div data-testid="stMain"><div id="slot"><iframe id="f" src="/component/index.html" height="440" title="card"></iframe></div></div>
<div class="st-key-appbar"></div>
<script>
  var f = document.getElementById("f");
  window.__events = [];
  window.__args = window.__args || {};
  window.__heights = [];
  window.draw = function (args) { window.__args = args; f.contentWindow.postMessage({ type: "streamlit:render", args: args }, "*"); };
  window.addEventListener("message", function (e) {
    var d = e.data;
    if (!d || !d.isStreamlitMessage) { return; }
    if (d.type === "streamlit:componentReady") { window.draw(window.__args); }
    else if (d.type === "streamlit:setFrameHeight") {
      window.__heights.push(d.height);
      if (parseInt(f.style.height || f.getAttribute("height"), 10) !== d.height) {
        f.style.height = d.height + "px";
        setTimeout(function () { window.draw(window.__args); }, 30);   // Streamlit re-sends the props after a height change
      }
    }
    else if (d.type === "streamlit:setComponentValue") { window.__events.push(d.value); }
  });
</script></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    component: Path = COMPONENT

    def do_GET(self):  # noqa: N802 (http.server's name)
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path == "/component/index.html":
            body, kind = self.component.read_bytes(), "text/html; charset=utf-8"
        elif url.path == "/host.html":
            top, bar, bottom = (query.get(k, [d])[0] for k, d in (("top", "86"), ("bar", "58"), ("bottom", "8")))
            body = HOST.replace("__TOP__", str(int(top))).replace("__BAR__", str(int(bar))).replace("__BOTTOM__", str(int(bottom))).encode()
            kind = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence
        pass


class Harness:
    """`with Harness() as h: ctx, page, frame = h.open(browser, (390, 844), args)`; `Harness(component=path)` serves another copy of the card."""

    def __init__(self, component: Path | None = None):
        handler = type("Handler", (_Handler,), {"component": Path(component) if component else COMPONENT})
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "Harness":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()

    def open(self, browser, size: tuple[int, int], args: dict[str, Any], *, touch: bool = True, zoom: int = 100, top: int | None = None,
             bar: int | None = None, bottom: int | None = None, **context_options):
        """A phone-sized page with the card drawn from `args`. `zoom` = the phone's text size in percent (the card's root font size, as a
        phone's text-size setting scales every frame). Returns (context, page, the card's frame); JS errors are collected in `page.errors`."""
        ctx = browser.new_context(viewport={"width": size[0], "height": size[1]}, is_mobile=touch, has_touch=touch, device_scale_factor=2,
                                  **context_options)
        if zoom != 100:
            style = f"<style>html {{ font-size: {zoom}% !important; }}</style>"

            def zoomed(route):
                response = route.fetch()
                headers = {k: v for k, v in response.headers.items() if k.lower() not in ("content-length", "content-encoding")}
                route.fulfill(status=response.status, headers=headers, body=response.text().replace("</head>", style + "</head>", 1))

            ctx.route("**/component/index.html", zoomed)
        page = ctx.new_page()
        page.errors = []
        page.on("pageerror", lambda e: page.errors.append(str(e)))
        page.on("console", lambda m: page.errors.append(m.text) if m.type == "error" else None)
        small = size[0] <= 320
        top = top if top is not None else (55 if small else 86)
        bar = bar if bar is not None else (54 if small else 58)
        bottom = bottom if bottom is not None else (6 if small else 8)
        page.add_init_script("window.__args = %s;" % json.dumps(args))
        page.goto(f"{self.url}/host.html?top={top}&bar={bar}&bottom={bottom}", wait_until="load")
        frame = page.frame(url=lambda u: "component/index.html" in u)
        frame.wait_for_selector("#cbody .name", timeout=10000)
        page.wait_for_timeout(450)  # the first fit, the repeat of the props after the height change and the cue's timer
        return ctx, page, frame

    @staticmethod
    def redraw(page, args: dict[str, Any], wait: int = 450) -> None:
        """Draw another card in the same frame (what Streamlit does for the next card of a scan); `wait` ms for the card to settle."""
        page.evaluate("a => window.draw(a)", args)
        page.wait_for_timeout(wait)


def card_args(**override: Any) -> dict[str, Any]:
    """The props of a card as the app builds them, for Vitamin C on a 80 mg pill (the sample label's first card); override any of them."""
    args: dict[str, Any] = dict(
        name="Vitamin C", dose="80 mg", food="Guavas", foodIcon="🍈", foodNote="", source="USDA: Guavas, common, raw",
        matchDose="~35 g", rdaAmount="~88 g", rdaLabel="200 mg", warn="",
        bioNote="Whole foods pair vitamin C with bioflavonoids that support its absorption and antioxidant action.",
        index=0, total=7, accent="#f97316", ink="#7a3a10", bg="linear-gradient(160deg, #ffffff 0%, #fef1e8 55%, #fee6d5 100%)",
        canReplace=True, canSwap=True, canAsk=True, previous="", editing=False, cardId="vitamin c", scan=1, ack="", height=440,
    )
    args.update(override)
    return args


# The food amounts of real cards, as the app's own card builder wrote them (collected for every nutrient of the RDA table and for the sample
# label; tests/test_ux_dose_blocks.py checks that the app still builds only these KINDS of text). `matchDose` is the amount of the shown food
# that equals the pill's dose, `rdaAmount` the amount that reaches the athlete target, `rdaLabel` that target. Override or add keys freely.
SHAPES: dict[str, dict[str, Any]] = {
    "plain": dict(name="Vitamin C", dose="80 mg", food="Guavas", matchDose="~35 g", rdaAmount="~88 g", rdaLabel="200 mg"),
    "three digits": dict(name="Vitamin D3", dose="20 mcg", food="Mackerel, Atlantic", matchDose="~124 g", rdaAmount="~155 g", rdaLabel="25 mcg"),
    "IU dose": dict(name="Vitamin D3", dose="1000 IU", food="Mackerel, Atlantic", matchDose="~155 g", rdaAmount="~155 g", rdaLabel="25 mcg"),
    "athlete amount smaller": dict(name="Biotin", dose="50 mcg", food="Maitake mushroom", matchDose="~154 g", rdaAmount="~92 g", rdaLabel="30 mcg"),
    "units in brackets": dict(name="Selenium", dose="55 mcg", food="Brazil nuts", matchDose="~2.9 g (~0.6 Brazil nuts)",
                              rdaAmount="~3.7 g (~0.7 Brazil nuts)", rdaLabel="70 mcg"),
    "athlete verdict": dict(name="Magnesium", dose="56 mg", food="Hemp seeds (hulled)", matchDose="~8 g",
                            rdaAmount="not practical from food alone (~71 g/day; realistic max ~70 g/day)", rdaLabel="500 mg"),
    "a lot of food": dict(name="Potassium", dose="200 mg", food="Soybeans (dry)", matchDose="~11 g",
                          rdaAmount="a lot of food (~195 g/day, ~870 kcal)", rdaLabel="3500 mg"),
    "two verdicts": dict(name="Fluoride", dose="3 mg", food="Seedless dark raisins", matchDose="not practical from food alone (~1.3 kg/day)",
                         rdaAmount="not practical from food alone (~1.7 kg/day)", rdaLabel="4 mg"),
    "mushroom note": dict(name="Vitamin D3", dose="20 mcg", food="Maitake mushroom", matchDose="~20 g (only UV-treated mushrooms \u2014 regular mushrooms "
                          "contain almost no vitamin D)", rdaAmount="~25 g", rdaLabel="25 mcg"),
    "long label": dict(name="Omega-3 (EPA+DHA)", dose="500 mg", food="Fish roe", matchDose="~21 g",
                       rdaAmount="not practical from food alone (~85 g/day; realistic max ~50 g/day)", rdaLabel="2 g"),
    "weekly dose": dict(name="Vitamin D3", dose="5000 IU once a week (~714 IU/day)", food="Mackerel, Atlantic", matchDose="~44 g", rdaAmount="~155 g", rdaLabel="25 mcg"),
    "dose range": dict(name="Vitamin C", dose="100\u2013200 mg", food="Guavas", matchDose="~44 g", rdaAmount="~88 g", rdaLabel="200 mg"),
    "no athlete target label": dict(name="Vitamin C", dose="80 mg", food="Guavas", matchDose="~35 g", rdaAmount="~88 g", rdaLabel=""),
    "only the pill match": dict(name="Vitamin C", dose="80 mg", food="Guavas", matchDose="~35 g", rdaAmount="", rdaLabel=""),
    "only the athlete target": dict(name="Vitamin C", dose="80 mg", food="Guavas", matchDose="", rdaAmount="~88 g", rdaLabel="200 mg"),
    "no amounts": dict(name="Chloride", dose="200 mg", food="", foodNote="No whole-food alternatives found for this card.", matchDose="", rdaAmount="",
                       rdaLabel="", canReplace=False),
}
