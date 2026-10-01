"""Final review (LLM security) F1: live-looking API keys were committed to the
public repo in the past. Rotating them and purging the history is an owner
action; this test keeps any new key out of the tracked tree."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SECRET_RE = re.compile(
    r"sk-kb-[A-Za-z0-9]{8,}"            # Blockbrain
    r"|gh[pousr]_[A-Za-z0-9]{30,}"      # GitHub tokens
    r"|github_pat_[A-Za-z0-9_]{30,}"
    r"|sk-or-v1-[A-Za-z0-9]{20,}"       # OpenRouter
    r"|sk-(?:proj-)?[A-Za-z0-9_-]{32,}"  # OpenAI
    r"|AKIA[0-9A-Z]{16}"                # AWS access key id
)


def _tracked_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True, timeout=30).stdout
    except Exception:
        pytest.skip("git is not available")
    return [ROOT / name for name in out.decode("utf-8", "replace").split("\0") if name]


def test_no_api_keys_in_tracked_files():
    hits = []
    for path in _tracked_files():
        if not path.is_file() or path.stat().st_size > 5_000_000 or path.suffix in {".db", ".sqlite", ".png", ".jpg", ".gz"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for m in _SECRET_RE.finditer(text):
            hits.append(f"{path.relative_to(ROOT)}: {m.group(0)[:10]}…")
    assert hits == [], "API key-like strings in tracked files (rotate them and remove): " + ", ".join(hits)
