"""A redeploy under a running Streamlit process must not break the app.

Live incident: the process still held the OLD `blockbrain.app` and `llm_cache` modules after a redeploy, so the new
entry script died on open with `module 'llm_cache' has no attribute 'set_reject'` until the app was rebooted.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _write(path: Path, text: str) -> None:
    path.write_text(textwrap.dedent(text))
    future = time.time() + _write.counter
    _write.counter += 2  # every write gets a distinct, later mtime (a real pull does too)
    os.utime(path, (future, future))


_write.counter = 1


@pytest.fixture
def package(tmp_path, monkeypatch):
    name = f"_redeploy_{abs(hash(str(tmp_path))) % 10**8}"
    folder = tmp_path / name
    folder.mkdir()
    (folder / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    yield name, folder
    for key in [k for k in sys.modules if k == name or k.startswith(name + ".")]:
        del sys.modules[key]
    registry = sys.modules.get("_suppswipe_imports")
    if registry is not None:
        registry.__dict__.get("stamps", {}).pop(f"{name}.mod", None)


def test_a_changed_file_is_reloaded_and_an_unchanged_one_is_not(sw, package):
    name, folder = package
    script = folder / "script.py"
    script.write_text("zz.value\n")
    _write(folder / "mod.py", "value = 1\nstate = []\n")
    first = sw._load_current(f"{name}.mod", "zz", script)
    assert first.value == 1
    first.state.append("kept")
    assert sw._load_current(f"{name}.mod", "zz", script).state == ["kept"]  # nothing changed: nothing is reset
    _write(folder / "mod.py", "value = 2\nstate = []\n")  # the redeploy
    reloaded = sw._load_current(f"{name}.mod", "zz", script)
    assert reloaded.value == 2 and reloaded.state == []
    assert sw._load_current(f"{name}.mod", "zz", script) is reloaded


def test_a_module_loaded_before_the_guard_existed_is_reloaded_when_it_lacks_what_the_script_uses(sw, package):
    """The live case: the process imported the old module itself, the files were replaced afterwards."""
    name, folder = package
    script = folder / "script.py"
    script.write_text("zz.old_function()\nzz.new_function()\n")
    _write(folder / "mod.py", "def old_function():\n    return 1\n")
    import importlib

    old = importlib.import_module(f"{name}.mod")  # not through the guard: no timestamp is known
    assert not hasattr(old, "new_function")
    _write(folder / "mod.py", "def old_function():\n    return 1\n\ndef new_function():\n    return 2\n")
    current = sw._load_current(f"{name}.mod", "zz", script)
    assert current.new_function() == 2


def test_a_broken_deploy_is_retried_on_the_next_run(sw, package):
    name, folder = package
    script = folder / "script.py"
    script.write_text("zz.value\n")
    _write(folder / "mod.py", "value = 1\n")
    assert sw._load_current(f"{name}.mod", "zz", script).value == 1
    _write(folder / "mod.py", "value = (\n")  # half-copied file
    with pytest.raises(SyntaxError):
        sw._load_current(f"{name}.mod", "zz", script)
    _write(folder / "mod.py", "value = 3\n")  # the copy finished
    assert sw._load_current(f"{name}.mod", "zz", script).value == 3


def test_every_attribute_the_app_uses_exists_in_its_own_modules(sw):
    """The guard decides "stale" by the names the script uses; this keeps that list honest."""
    source = (ROOT / "swipe_mobile_app" / "app.py").read_text(encoding="utf-8")
    import blockbrain.app as bb
    import llm_cache

    for alias, module in (("bb", bb), ("llm_cache", llm_cache)):
        used = set(re.findall(r"\b" + alias + r"\.([A-Za-z_]\w*)", source))
        assert used, alias
        missing = sorted(a for a in used if not hasattr(module, a))
        assert not missing, f"{alias}: {missing}"


def test_the_app_starts_when_the_running_process_holds_the_old_modules():
    """End to end, in a clean process: the loaded modules lack what the new script calls (the live failure)."""
    script = textwrap.dedent(
        """
        import os, sys, logging
        logging.disable(logging.CRITICAL)
        os.environ["BLOCKBRAIN_API_KEY"] = ""
        sys.path[:0] = [r"%s", r"%s"]
        import blockbrain.app as bb, llm_cache
        # What a process that loaded the previous deploy looks like:
        for name in ("looks_like_agent_error", "reset_call_error", "last_call_error", "unresolved_models"):
            delattr(bb, name)
        delattr(llm_cache, "set_reject")
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(r"%s", default_timeout=60)
        at.run()
        print("EXCEPTIONS", len(at.exception), "ELEMENTS", len(at.markdown) + len(at.button))
        """
    ) % (ROOT, ROOT / "swipe_mobile_app", ROOT / "swipe_mobile_app" / "app.py")
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=180)
    assert "EXCEPTIONS 0" in result.stdout, result.stdout[-800:] + result.stderr[-800:]
    assert not result.stdout.strip().endswith("ELEMENTS 0")
