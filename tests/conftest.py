"""Shared pytest setup: make the repo root and the swipe app folder importable,
and keep every test offline (no Blockbrain key, no network)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT, ROOT / "swipe_mobile_app"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

# A complete but FAKE Blockbrain configuration, forced (not setdefault): a developer who has the real key in the
# environment must never have a test reach the real platform with it. Every test that reaches the client monkeypatches
# it or talks to the local fake server (tests/fake_blockbrain.py); the network guard below enforces "offline".
for _name in ("BLOCKBRAIN_BOT_ID", "BLOCKBRAIN_OCR_ROUTE", "BLOCKBRAIN_MODEL_TEXT", "BLOCKBRAIN_MODEL_VISION"):
    os.environ.pop(_name, None)
os.environ["BLOCKBRAIN_API_KEY"] = "test-key-offline"
os.environ["BLOCKBRAIN_ORG_ID"] = "test-org"
os.environ["BLOCKBRAIN_MODEL"] = "claude-sonnet-5"
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"


import importlib.util

import pytest


@pytest.fixture(scope="session")
def sw():
    """The SuppSwipe app module, imported without rendering the UI."""
    spec = importlib.util.spec_from_file_location("suppswipe_app", ROOT / "swipe_mobile_app" / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- guard against the real Blockbrain + the fake Blockbrain server --------------------------------------------------------------------
import requests
import requests.adapters

_REAL_SEND = requests.adapters.HTTPAdapter.send


def _offline_send(self, request, *args, **kwargs):
    from urllib.parse import urlparse

    host = urlparse(request.url).hostname or ""
    if host.endswith("theblockbrain.ai"):  # the one thing no test may ever reach: the real platform
        raise RuntimeError(f"tests must never contact Blockbrain: {request.method} {host}")
    return _REAL_SEND(self, request, *args, **kwargs)


@pytest.fixture(autouse=True, scope="session")
def _tests_stay_offline():
    requests.adapters.HTTPAdapter.send = _offline_send
    yield
    requests.adapters.HTTPAdapter.send = _REAL_SEND


@pytest.fixture
def fake_bb(monkeypatch):
    """A running FakeBlockbrain; the client and the app are configured to talk to it (and only to it)."""
    import blockbrain_llm_client as client
    import fake_blockbrain as fb

    server = fb.FakeBlockbrain().start()
    monkeypatch.setattr(client, "BLOCKY", server.url)
    monkeypatch.setattr(client, "AGENTIC", server.url)
    monkeypatch.setenv("BLOCKBRAIN_API_KEY", fb.API_KEY)
    monkeypatch.setenv("BLOCKBRAIN_ORG_ID", fb.ORG_ID)
    monkeypatch.setenv("BLOCKBRAIN_BOT_ID", fb.BOT_ID)
    monkeypatch.delenv("BLOCKBRAIN_MODEL", raising=False)
    monkeypatch.delenv("BLOCKBRAIN_OCR_ROUTE", raising=False)
    # The attachment poll waits 2 s per round: give the client a clock whose sleep is instant (only inside the client).
    import time as _time
    import types

    monkeypatch.setattr(client, "time", types.SimpleNamespace(time=_time.time, sleep=lambda _s: None))
    yield server
    server.stop()


@pytest.fixture(autouse=True)
def _fresh_route_memory():
    """The photo route that worked last is remembered for 15 minutes (process-wide): never across tests."""
    import blockbrain.app as app

    app._ROUTE_PREFERENCE.update(route="", until=0.0)
    yield
    app._ROUTE_PREFERENCE.update(route="", until=0.0)
