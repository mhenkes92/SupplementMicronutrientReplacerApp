"""Offline-Test: blockbrain_llm_client - Stream-Parser, Wiederholung bei leerem Lauf, Bildaufbereitung, Cortex-Ablauf (kein Netz).

    .venv\\Scripts\\python.exe intern\\scripts\\shared\\tests\\test_blockbrain_llm_client.py
"""
from __future__ import annotations

import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
sys.path.insert(0, os.path.join(ROOT, "intern", "scripts", "shared"))
import blockbrain_llm_client as bl  # noqa: E402
from PIL import Image  # noqa: E402

fehler = []


def pruefe(bedingung, text):
    print(("ok   " if bedingung else "FAIL ") + text)
    if not bedingung:
        fehler.append(text)


def bild(groesse=(300, 200), fmt="JPEG", **kw):
    buf = io.BytesIO()
    Image.new("RGB", groesse, "white").save(buf, fmt, **kw)
    return buf.getvalue()


# ------------------------------------------------------------------ Konfiguration
for n in ("BLOCKBRAIN_API_KEY", "BLOCKBRAIN_ORG_ID", "BLOCKBRAIN_BOT_ID", "BLOCKBRAIN_MODEL"):
    os.environ.pop(n, None)
try:
    bl.Blockbrain()
    pruefe(False, "ohne Konfiguration muss ein Fehler kommen")
except bl.BlockbrainError as exc:
    pruefe(all(w in str(exc) for w in ("BLOCKBRAIN_API_KEY", "BLOCKBRAIN_ORG_ID", "BLOCKBRAIN_BOT_ID")),
           f"fehlende Konfiguration wird vollstaendig benannt: {exc}")
bb = bl.Blockbrain(api_key="k", org_id="o", model="claude-sonnet-5")
pruefe(bb.bot_id == bl.KNOWN_MODELS["claude-sonnet-5"][1], "Modellschluessel loest den Bot der Sandbox auf")
pruefe(bl.Blockbrain(api_key="k", org_id="o", bot_id="eigen", model="claude-sonnet-5").bot_id == "eigen", "explizite bot_id gewinnt")
os.environ["BLOCKBRAIN_MODEL"] = "gpt-5.5"
pruefe(bl.Blockbrain(api_key="k", org_id="o").bot_id == bl.KNOWN_MODELS["gpt-5.5"][1], "BLOCKBRAIN_MODEL aus der Umgebung")
os.environ.pop("BLOCKBRAIN_MODEL")

# ------------------------------------------------------------------ Dateierkennung und Bildaufbereitung
for daten, erwartet in ((bild(fmt="PNG"), "png"), (bild(), "jpg"), (bild(fmt="WEBP"), "webp"), (b"%PDF-1.7 x", "pdf"),
                        (bild(fmt="TIFF"), "tif")):
    pruefe(bl._sniff(daten)[0] == erwartet, f"_sniff erkennt {erwartet}")
try:
    bl._sniff(b"GIF89a....")
    pruefe(False, "unbekannter Typ muss abgelehnt werden")
except bl.BlockbrainError:
    pruefe(True, "unbekannter Typ wird abgelehnt")

klein = bild()
d, name, mime = bl._load_image(klein, 2000)
pruefe(d == klein and name == "image.jpg" and mime == "image/jpeg", "kleines JPEG bleibt unveraendert, Name hat Endung")
pdf = b"%PDF-1.4 inhalt"
d, name, mime = bl._load_image(pdf, 2000)
pruefe(d == pdf and name == "image.pdf" and mime == "application/pdf", "PDF wird durchgereicht")

exif = Image.Exif()
exif[0x0112] = 6  # Anzeige um 90 Grad im Uhrzeigersinn gedreht (Handyfoto)
gross = bild((4000, 3000), exif=exif)
d, name, mime = bl._load_image(gross, 2000)
img = Image.open(io.BytesIO(d))
pruefe(mime == "image/jpeg" and name.endswith(".jpg") and img.size == (1500, 2000),
       f"grosses Handyfoto: EXIF-Drehung angewendet und auf 2000 px verkleinert: {img.size}")

tif = io.BytesIO()
f1, f2 = Image.new("RGB", (400, 300), "white"), Image.new("RGB", (400, 300), "black")
f1.save(tif, "TIFF", save_all=True, append_images=[f2])
d, name, mime = bl._load_image(tif.getvalue(), 2000)
seiten = len(re.findall(rb"/Type\s*/Page(?![s\w])", d))
pruefe(mime == "application/pdf" and name == "image.pdf" and d[:5] == b"%PDF-" and seiten == 2,
       f"mehrseitiges TIFF wird zu PDF mit allen Seiten: {seiten} Seiten")


# ------------------------------------------------------------------ Netz-Attrappe
class Antwort:
    def __init__(self, status=200, zeilen=(), json_body=None, text=""):
        self.status_code, self._zeilen, self._json, self.text = status, list(zeilen), json_body, text

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_lines(self):
        for z in self._zeilen:
            yield z.encode("utf-8")

    def json(self):
        return self._json


class Netz:
    def __init__(self):
        self.aufrufe = []          # (methode, url, kwargs)
        self.convos = 0
        self.geloescht = []
        self.stream_skripte = []   # je Stream-/Completion-Aufruf eine Zeilenliste
        self.attachment_status = "success"
        self.attachment_folge = []   # optional: Zustandsfolge [(status, calculatedStatus, tokens), ...], je GET einer
        self.patch_status = 200

    def post(self, url, **kw):
        self.aufrufe.append(("POST", url, kw))
        if "/cortex/active-bot/" in url:
            self.convos += 1
            return Antwort(json_body={"body": {"dataRoomId": f"room{self.convos}", "activeBotId": f"act{self.convos}"}})
        if url.endswith("/attachment"):
            return Antwort(json_body={"body": {"_id": "a1", "status": "processing"}})
        zeilen = self.stream_skripte.pop(0) if self.stream_skripte else []
        return Antwort(zeilen=zeilen)

    def get(self, url, **kw):
        self.aufrufe.append(("GET", url, kw))
        if url.endswith("/attachment"):
            if self.attachment_folge:
                status, berechnet, tokens = self.attachment_folge.pop(0) if len(self.attachment_folge) > 1 else self.attachment_folge[0]
                return Antwort(json_body={"body": [{"name": "x", "status": status, "calculatedStatus": berechnet, "tokens": tokens}]})
            return Antwort(json_body={"body": [{"name": "x", "status": self.attachment_status,
                                               "calculatedStatus": self.attachment_status, "tokens": 148}]})
        return Antwort(json_body={"items": [{"id": "m", "supportsVision": True}]})

    def patch(self, url, **kw):
        self.aufrufe.append(("PATCH", url, kw))
        return Antwort(status=self.patch_status, json_body={}, text="patch failed")

    def delete(self, url, **kw):
        self.geloescht.append(url.rsplit("/", 1)[-1])
        return Antwort()


def cortex_sse(*paare):
    """Cortex-Stream: Paare (Ereignisname, Daten) -> event:/data:-Zeilen wie die Plattform sie sendet."""
    out = []
    for name, daten in paare:
        out += [f"event: {name}", "data: " + json.dumps(daten), ""]
    return out + ["data: [DONE]"]


def sse(*events):
    out = []
    for e in events:
        out.append("data: " + json.dumps(e))
        out.append("")
    out.append("data: [DONE]")
    return out


def mit_netz():
    netz = Netz()
    bl.requests.post, bl.requests.get, bl.requests.delete, bl.requests.patch = netz.post, netz.get, netz.delete, netz.patch
    bl.time.sleep = lambda s: None
    return netz, bl.Blockbrain(api_key="k", org_id="o", bot_id="bot1")


# ------------------------------------------------------------------ Agentic: Text
netz, bb = mit_netz()
netz.stream_skripte = [["event: x", ": keep-alive"] + sse(
    {"type": "start"}, {"type": "data-resolved-model", "data": {"model": "m-1"}}, {"type": "text-delta", "delta": "Hal"},
    {"type": "text-delta", "delta": "lo"}, {"type": "finish", "messageMetadata": {"usage": {"inputTokens": 7}}})]
rep = bb.chat("Hi", system="Sei kurz", history=[{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}])
stream = [a for a in netz.aufrufe if a[1].endswith("/v2/api/agents/customAgent/stream")][0]
pruefe(rep.text == "Hallo" and rep.model == "m-1" and rep.usage == {"inputTokens": 7}, f"Stream wird zusammengesetzt: {rep.text!r}")
pruefe(stream[2]["headers"].get("x-blockbrain-active-bot-id") == "act1" and stream[2]["headers"].get("x-blockbrain-data-room-id") == "room1",
       "beide Blockbrain-Header gesetzt (sonst laeuft der Agent still ohne Kontext)")
pruefe(stream[2]["json"]["trigger"] == "submit-message" and stream[2]["json"]["instructions"] == "Sei kurz"
       and [m["role"] for m in stream[2]["json"]["messages"]] == ["user", "assistant", "user"], "Body: Verlauf + instructions")
pruefe("activeTools" not in stream[2]["json"] and "model" not in stream[2]["json"], "kein activeTools, kein model-Feld")
pruefe(netz.geloescht == ["room1"], "temporaere Conversation wird geloescht")

netz, bb = mit_netz()
netz.stream_skripte = [sse({"type": "start"}, {"type": "finish"}), sse({"type": "text-delta", "delta": "ok"}, {"type": "finish"})]
rep = bb.chat("Hi")
pruefe(rep.text == "ok" and netz.convos == 2 and netz.geloescht == ["room1", "room2"],
       "leerer Lauf: genau eine Wiederholung auf NEUER Conversation, beide werden geloescht")

netz, bb = mit_netz()
netz.stream_skripte = [sse({"type": "finish"}), sse({"type": "finish"})]
try:
    bb.chat("Hi")
    pruefe(False, "zweimal leer muss einen Fehler geben")
except bl.BlockbrainError as exc:
    pruefe("empty answer twice" in str(exc) and netz.geloescht == ["room1", "room2"], "zweimal leer -> Fehler, nichts bleibt liegen")

netz, bb = mit_netz()
netz.stream_skripte = [sse({"type": "text-delta", "delta": "x"}, {"type": "error", "errorText": "Modell ueberlastet"})]
try:
    bb.chat("Hi")
    pruefe(False, "error-Event muss einen Fehler geben")
except bl.BlockbrainError as exc:
    pruefe("Modell ueberlastet" in str(exc) and netz.geloescht == ["room1"], "error-Event wird gemeldet, Conversation geloescht")

# ------------------------------------------------------------------ Agentic: Bild
netz, bb = mit_netz()
netz.stream_skripte = [sse({"type": "text-delta", "delta": "Rechnung 1"}, {"type": "finish"})]
rep = bb.ocr(klein)
nachricht = [a for a in netz.aufrufe if a[1].endswith("/stream")][0][2]["json"]["messages"][0]
teile = {t["type"]: t for t in nachricht["parts"]}
pruefe(rep.text == "Rechnung 1" and set(teile) == {"text", "file"}, "OCR (agentic): Text- und Dateiteil in EINER Nachricht")
pruefe(teile["file"]["mediaType"] == "image/jpeg" and teile["file"]["filename"] == "image.jpg"
       and teile["file"]["url"].startswith("data:image/jpeg;base64,"), "Dateiteil: mediaType, Dateiname mit Endung, Daten-URL")

# ------------------------------------------------------------------ Cortex
netz, bb = mit_netz()
netz.stream_skripte = [["event: new_token", 'data: {"token": "Zeile"}', "", "event: new_token", 'data: {"token": " 1"}', "",
                        "event: message_end", "data: {}", "", "data: [DONE]"]]
rep = bb.ocr(klein, via="cortex")
anlegen = [a for a in netz.aufrufe if "/cortex/active-bot/" in a[1]][0][2]["json"]
upload = [a for a in netz.aufrufe if a[1].endswith("/attachment") and a[0] == "POST"][0][2]
frage = [a for a in netz.aufrufe if a[1].endswith("/completions/v2/user-input")][0][2]["json"]
pruefe(rep.text == "Zeile 1" and rep.usage == {"attachmentTokens": 148}, f"Cortex: Antwort aus new_token, Anhangstokens: {rep.text!r}")
pruefe(anlegen.get("agent") == "" and upload["files"]["attachment"][0] == "image.jpg" and upload["data"] == {"session_id": "room1"}
       and frage["convoId"] == "room1" and frage["sessionId"] == "room1", "Cortex: Normal-Chat (agent ''), Upload mit Endung, Frage an dieselbe Conversation")
pruefe("Content-Type" not in upload["headers"], "Upload ohne JSON-Content-Type (multipart setzt ihn selbst)")
pruefe(netz.geloescht == ["room1"], "Cortex: Conversation wird geloescht")

netz, bb = mit_netz()
netz.stream_skripte = [["event: new_token", 'data: {"token": "### ERROR: Maximum Output Tokens Exceeded"}', "", "data: [DONE]"]]
try:
    bb.ocr(klein, via="cortex")
    pruefe(False, "Plattform-Fehlertext muss ein Fehler sein")
except bl.BlockbrainError as exc:
    pruefe("### ERROR" in str(exc), "Cortex: '### ERROR ...' als Antwort ist ein Fehler, kein Inhalt")

netz, bb = mit_netz()
netz.attachment_status = "failed"
try:
    bb.ocr(klein, via="cortex")
    pruefe(False, "fehlgeschlagener Anhang muss ein Fehler sein")
except bl.BlockbrainError as exc:
    pruefe("attachment failed" in str(exc) and netz.geloescht == ["room1"], "Cortex: fehlgeschlagener Anhang -> Fehler, aufgeraeumt")

try:
    mit_netz()[1].ocr(klein, via="x")
    pruefe(False, "unbekannte Route muss abgelehnt werden")
except bl.BlockbrainError:
    pruefe(True, "unbekannte Route wird abgelehnt")

# ------------------------------------------------------------------ Cortex: Text (v2)
def user_input(netz):
    return [a for a in netz.aufrufe if a[1].endswith("/completions/v2/user-input")][0][2]["json"]


for n in ("BLOCKBRAIN_TEXT_ROUTE", "BLOCKBRAIN_TEXT_MODEL", "BLOCKBRAIN_OCR_MODEL"):
    os.environ.pop(n, None)

netz, bb = mit_netz()
netz.stream_skripte = [cortex_sse(
    ("user_message", {"model": "m-sonnet"}),
    ("prompt_context", {"context": [{"doc_name": "sleep.pdf", "score": 0.84}, {"doc_name": "full.pdf", "score": 0.0},
                                    {"doc_name": "sleep.pdf", "score": 0.5}]}),
    ("new_token", {"token": "Hal"}), ("new_token", {"token": "lo"}), ("message_end", {}))]
rep = bb.chat("Hi", via="cortex")
anlegen = [a for a in netz.aufrufe if "/cortex/active-bot/" in a[1]][0][2]["json"]
pruefe(rep.text == "Hallo" and rep.via == "cortex" and rep.model == "m-sonnet", f"Cortex-Text: Antwort + Modell aus user_message: {rep.text!r} {rep.model}")
pruefe(rep.sources == ["sleep.pdf"], f"Quellen: nur Dokumente mit Score > 0, ohne Doppelte: {rep.sources}")
pruefe(anlegen.get("agent") == "" and user_input(netz) == {"convoId": "room1", "sessionId": "room1", "content": "Hi"},
       "Cortex-Text: Normal-Chat (agent ''), kein model-Feld, Inhalt = Frage")
pruefe(not any(a[1].endswith("/stream") for a in netz.aufrufe) and netz.geloescht == ["room1"], "Cortex-Text: kein Agent-Stream, Conversation geloescht")

netz, bb = mit_netz()
netz.stream_skripte = [cortex_sse(("new_token", {"token": "OK"}))]
rep = bb.chat("Hi", via="cortex", model="azure-gpt-41-nano")
pruefe(user_input(netz).get("model") == "azure-gpt-41-nano" and rep.text == "OK" and rep.model == "azure-gpt-41-nano",
       "model= wird pro Nachricht gesendet (kurze Antwort 'OK' kommt trotz Fehlerpruefung an)")

netz, bb = mit_netz()
netz.stream_skripte = [cortex_sse(("new_token", {"token": "x"}))]
os.environ["BLOCKBRAIN_TEXT_ROUTE"], os.environ["BLOCKBRAIN_TEXT_MODEL"] = "cortex", "bedrock-anthropic-claude-haiku-4.5-fast"
bb2 = bl.Blockbrain(api_key="k", org_id="o", bot_id="bot1")
rep = bb2.chat("Hi")
pruefe(rep.via == "cortex" and user_input(netz).get("model") == "bedrock-anthropic-claude-haiku-4.5-fast"
       and not any(a[1].endswith("/stream") for a in netz.aufrufe), "BLOCKBRAIN_TEXT_ROUTE/-MODEL aus der Umgebung wirken ohne Codeaenderung")
os.environ.pop("BLOCKBRAIN_TEXT_ROUTE"), os.environ.pop("BLOCKBRAIN_TEXT_MODEL")

netz, bb = mit_netz()
netz.stream_skripte = [cortex_sse(("new_token", {"token": "Antwort mit Quelle"}))]
bb.chat("Frage", via="cortex", web=True)
patch = [a for a in netz.aufrufe if a[0] == "PATCH"][0]
pruefe(patch[1].endswith("/cortex/conversation/room1") and patch[2]["json"] == {"enableWebSearch": True},
       "web=True: PATCH /cortex/conversation/{id} {'enableWebSearch': true}")
pruefe([a[0] for a in netz.aufrufe].index("PATCH") < [a[1].endswith("user-input") for a in netz.aufrufe].index(True),
       "Websuche wird VOR der Frage aktiviert")

netz, bb = mit_netz()
netz.patch_status = 500
try:
    bb.chat("Frage", via="cortex", web=True)
    pruefe(False, "fehlgeschlagene Websuche-Aktivierung muss ein Fehler sein")
except bl.BlockbrainError as exc:
    pruefe("enable web search" in str(exc) and netz.geloescht == ["room1"], "Websuche nicht aktivierbar -> Fehler, aufgeraeumt")

netz, bb = mit_netz()
netz.stream_skripte = [cortex_sse(("new_token", {"token": "ok"}))]
bb.chat("Frage 3", system="Antworte kurz", history=[{"role": "user", "content": "Frage 1"}, {"role": "assistant", "content": "Antwort 1"}], via="cortex")
inhalt = user_input(netz)["content"]
pruefe(inhalt.index("Antworte kurz") < inhalt.index("User: Frage 1") < inhalt.index("Assistant: Antwort 1") < inhalt.index("Frage 3")
       and bl._fold_message("nur Frage", None, None) == "nur Frage", "system + Verlauf werden in EINE Nachricht gefaltet (Reihenfolge stimmt)")

netz, bb = mit_netz()
netz.stream_skripte = [cortex_sse(("new_token", {"token": "### ER"}), ("new_token", {"token": "ROR: Input too large"}))]
try:
    bb.chat("Hi", via="cortex")
    pruefe(False, "Plattform-Fehlertext im Cortex-Text muss ein Fehler sein")
except bl.BlockbrainError as exc:
    pruefe("### ERROR" in str(exc) and netz.geloescht == ["room1"], "Cortex-Text: '### ERROR' ueber zwei Tokens verteilt wird erkannt")

netz, bb = mit_netz()
netz.stream_skripte = [cortex_sse(("message_end", {})), cortex_sse(("new_token", {"token": "da"}))]
rep = bb.chat("Hi", via="cortex")
pruefe(rep.text == "da" and netz.convos == 2 and netz.geloescht == ["room1", "room2"], "Cortex-Text: leerer Lauf -> eine Wiederholung auf NEUER Conversation")

netz, bb = mit_netz()
netz.stream_skripte = [cortex_sse(("message_end", {})), cortex_sse(("message_end", {}))]
try:
    bb.chat("Hi", via="cortex")
    pruefe(False, "zweimal leer muss einen Fehler geben")
except bl.BlockbrainError as exc:
    pruefe("empty answer twice" in str(exc), "Cortex-Text: zweimal leer -> Fehler")

# ------------------------------------------------------------------ Argumente
for kw, was in (({"model": "x"}, "model= ohne Cortex"), ({"web": True}, "web=True ohne Cortex"), ({"via": "x"}, "unbekannte Route")):
    netz, bb = mit_netz()
    try:
        bb.chat("Hi", **kw)
        pruefe(False, f"{was} muss abgelehnt werden")
    except bl.BlockbrainError:
        pruefe(netz.aufrufe == [], f"{was} wird abgelehnt, bevor irgendein Netzaufruf passiert")
netz, bb = mit_netz()
try:
    bb.chat_stream("Hi", model="x")
    pruefe(False, "chat_stream mit falschen Argumenten muss SOFORT fehlschlagen")
except bl.BlockbrainError:
    pruefe(True, "chat_stream prueft Argumente beim Aufruf, nicht erst beim ersten Stueck")
try:
    mit_netz()[1].ocr(klein, via="agentic", model="x")
    pruefe(False, "ocr: model= ohne Cortex muss abgelehnt werden")
except bl.BlockbrainError:
    pruefe(True, "ocr: model= ohne Cortex wird abgelehnt")

# ------------------------------------------------------------------ chat_stream
netz, bb = mit_netz()
netz.stream_skripte = [cortex_sse(("user_message", {"model": "m"}), ("new_token", {"token": "Zeile"}), ("new_token", {"token": " 1"}),
                                  ("new_token", {"token": " und"}), ("new_token", {"token": " mehr"}), ("new_token", {"token": " Text"}),
                                  ("message_end", {}))]
stuecke = list(bb.chat_stream("Hi", via="cortex"))
pruefe("".join(stuecke) == "Zeile 1 und mehr Text" and len(stuecke) >= 3,
       f"chat_stream (cortex) liefert Stuecke nacheinander (nur die ersten Zeichen werden fuer die Fehlerpruefung gesammelt): {stuecke}")
pruefe(netz.geloescht == ["room1"], "chat_stream (cortex): Conversation nach dem letzten Stueck geloescht")

netz, bb = mit_netz()
netz.stream_skripte = [cortex_sse(("new_token", {"token": "lang"}), ("new_token", {"token": " genug fuer die Pruefung"}), ("new_token", {"token": "x"}))]
erzeuger = bb.chat_stream("Hi", via="cortex")
erstes = next(erzeuger)
erzeuger.close()
pruefe(erstes.startswith("lang") and netz.geloescht == ["room1"], "chat_stream: Abbruch durch den Aufrufer raeumt die Conversation auf")

netz, bb = mit_netz()
netz.stream_skripte = [sse({"type": "start"}, {"type": "finish"}), sse({"type": "text-delta", "delta": "Ha"}, {"type": "text-delta", "delta": "llo"},
                                                                         {"type": "finish"})]
stuecke = list(bb.chat_stream("Hi", system="kurz"))
pruefe(stuecke == ["Ha", "llo"] and netz.convos == 2 and netz.geloescht == ["room1", "room2"],
       f"chat_stream (agentic): Stuecke aus text-delta, leerer erster Lauf wird auf NEUER Conversation wiederholt: {stuecke}")

netz, bb = mit_netz()
netz.stream_skripte = [sse({"type": "finish"}), sse({"type": "finish"})]
try:
    list(bb.chat_stream("Hi"))
    pruefe(False, "chat_stream: zweimal leer muss einen Fehler geben")
except bl.BlockbrainError:
    pruefe(True, "chat_stream: zweimal leer -> Fehler")

netz, bb = mit_netz()
netz.stream_skripte = [sse({"type": "text-delta", "delta": "Teil"}, {"type": "error", "errorText": "abgebrochen"})]
gelesen = []
try:
    for stueck in bb.chat_stream("Hi"):
        gelesen.append(stueck)
    pruefe(False, "Fehler mitten im Stream muss ausgeloest werden")
except bl.BlockbrainError as exc:
    pruefe(gelesen == ["Teil"] and "abgebrochen" in str(exc), "chat_stream: Fehler mitten im Stream - bereits gelieferte Stuecke bleiben gueltig")

# ------------------------------------------------------------------ Cortex-OCR mit Modell
netz, bb = mit_netz()
netz.attachment_folge = [("IN_PROGRESS", "SUCCESS", 0), ("IN_PROGRESS", "SUCCESS", 0), ("SUCCESS", "SUCCESS", 615)]
netz.stream_skripte = [cortex_sse(("new_token", {"token": "Zeile 1"}))]
rep = bb.ocr(klein, via="cortex")
abfragen = [a for a in netz.aufrufe if a[0] == "GET" and a[1].endswith("/attachment")]
pruefe(len(abfragen) == 3 and rep.usage == {"attachmentTokens": 615},
       f"Anhang: 'calculatedStatus SUCCESS' bei status IN_PROGRESS und 0 Tokens ist noch NICHT fertig - es wird weiter gewartet ({len(abfragen)} Abfragen, {rep.usage})")
netz, bb = mit_netz()
netz.attachment_folge = [("IN_PROGRESS", "SUCCESS", 40)]
netz.stream_skripte = [cortex_sse(("new_token", {"token": "Zeile 1"}))]
pruefe(bb.ocr(klein, via="cortex").usage == {"attachmentTokens": 40}, "Anhang: Tokens > 0 gelten auch ohne status SUCCESS als fertig")
netz, bb = mit_netz()
netz.stream_skripte = [cortex_sse(("new_token", {"token": "Zeile 1"}))]
rep = bb.ocr(klein, via="cortex", model="azure-gpt-41-nano")
pruefe(user_input(netz).get("model") == "azure-gpt-41-nano" and rep.text == "Zeile 1", "ocr(via='cortex', model=...) sendet das Modell pro Nachricht")
netz, bb = mit_netz()
netz.stream_skripte = [cortex_sse(("new_token", {"token": "Zeile 1"}))]
os.environ["BLOCKBRAIN_OCR_MODEL"] = "google-gemini-2.5-flash-lite"
rep = bl.Blockbrain(api_key="k", org_id="o", bot_id="bot1").ocr(klein, via="cortex")
pruefe(user_input(netz).get("model") == "google-gemini-2.5-flash-lite", "BLOCKBRAIN_OCR_MODEL wirkt auf die Cortex-OCR")
os.environ.pop("BLOCKBRAIN_OCR_MODEL")

print(f"\n{len(fehler)} FEHLER" if fehler else "\nALLE OK")
sys.exit(1 if fehler else 0)
