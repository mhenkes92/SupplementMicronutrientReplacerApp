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

os.environ.setdefault("BLOCKBRAIN_API_KEY", "test-key-offline")
os.environ.setdefault("BLOCKBRAIN_BASE_URL", "https://blockbrain.test")


import importlib.util

import pytest


@pytest.fixture(scope="session")
def sw():
    """The SuppSwipe app module, imported without rendering the UI."""
    spec = importlib.util.spec_from_file_location("suppswipe_app", ROOT / "swipe_mobile_app" / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
