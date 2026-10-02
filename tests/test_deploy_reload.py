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
import threading
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
        registry.__dict__.get("seen", {}).pop(f"{name}.mod", None)


def test_a_changed_file_is_reloaded_and_an_unchanged_one_is_not(sw, package):
    name, folder = package
    _write(folder / "mod.py", "value = 1\nstate = []\n")
    first = sw._load_current(f"{name}.mod")
    assert first.value == 1
    first.state.append("kept")
    assert sw._load_current(f"{name}.mod").state == ["kept"]  # nothing changed: nothing is reset
    _write(folder / "mod.py", "value = 2\nstate = []\n")  # the redeploy
    reloaded = sw._load_current(f"{name}.mod")
    assert reloaded.value == 2 and reloaded.state == []
    assert sw._load_current(f"{name}.mod") is reloaded


def test_a_module_loaded_before_the_guard_existed_is_reloaded(sw, package):
    """The live case: the process imported the old module itself, the files were replaced afterwards."""
    name, folder = package
    _write(folder / "mod.py", "def old_function():\n    return 1\n")
    import importlib

    old = importlib.import_module(f"{name}.mod")  # not through the guard: no timestamp is known
    assert not hasattr(old, "new_function")
    _write(folder / "mod.py", "def old_function():\n    return 1\n\ndef new_function():\n    return 2\n")
    current = sw._load_current(f"{name}.mod")
    assert current.new_function() == 2


def test_a_preloaded_module_is_reloaded_even_when_it_has_every_name_the_script_uses(sw, package):
    """Same names, new behaviour (a bug fix): the old attribute check could not see that."""
    name, folder = package
    _write(folder / "mod.py", "def compute():\n    return 'old'\n")
    import importlib

    assert importlib.import_module(f"{name}.mod").compute() == "old"
    _write(folder / "mod.py", "def compute():\n    return 'new'\n")
    assert sw._load_current(f"{name}.mod").compute() == "new"


def test_a_broken_deploy_is_retried_on_the_next_run(sw, package):
    name, folder = package
    _write(folder / "mod.py", "value = 1\n")
    assert sw._load_current(f"{name}.mod").value == 1
    _write(folder / "mod.py", "value = (\n")  # half-copied file
    with pytest.raises(SyntaxError):
        sw._load_current(f"{name}.mod")
    _write(folder / "mod.py", "value = 3\n")  # the copy finished
    assert sw._load_current(f"{name}.mod").value == 3


def test_every_attribute_the_app_uses_exists_in_its_own_modules(sw):
    """A deploy whose app.py uses a name its modules lack would die on open: keep the two in step."""
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


def test_a_changed_file_is_reloaded_although_its_mtime_was_restored(sw, package):
    """`git archive | tar -x`, `cp -p` and `rsync -t` give the new file an old, even identical, mtime."""
    name, folder = package
    path = folder / "mod.py"
    path.write_text("value = 1\n")
    os.utime(path, ns=(10**18, 10**18))
    assert sw._load_current(f"{name}.mod").value == 1
    time.sleep(0.05)  # the ctime clock ticks in milliseconds
    path.write_text("value = 2\n")  # same size too
    os.utime(path, ns=(10**18, 10**18))
    assert sw._load_current(f"{name}.mod").value == 2


def test_a_deploy_that_lands_while_the_module_is_being_imported_is_picked_up_on_the_next_run(sw, package):
    name, folder = package
    path = folder / "mod.py"
    _write(path, "import time\nversion = 1\ntime.sleep(0.6)\n")

    def deploy():
        time.sleep(0.2)  # the old source is already read, the body is still running
        _write(path, "version = 2\n")

    thread = threading.Thread(target=deploy)
    thread.start()
    assert sw._load_current(f"{name}.mod").version == 1
    thread.join()
    assert sw._load_current(f"{name}.mod").version == 2


def test_same_second_same_size_edits_do_not_come_back_as_the_cached_bytecode(sw, package, monkeypatch):
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    name, folder = package
    path = folder / "mod.py"
    path.write_text("value = 1\n")
    os.utime(path, ns=(2 * 10**18 + 100_000_000,) * 2)
    assert sw._load_current(f"{name}.mod").value == 1
    time.sleep(0.05)
    path.write_text("value = 2\n")
    os.utime(path, ns=(2 * 10**18 + 900_000_000,) * 2)  # another instant of the same second
    assert sw._load_current(f"{name}.mod").value == 2


def test_a_reload_that_fails_midway_is_redone_in_full_once_the_file_is_fixed(sw, package):
    name, folder = package
    _write(folder / "mod.py", "CONFIG = {'v': 1}\ndef compute():\n    return CONFIG['v']\n")
    assert sw._load_current(f"{name}.mod").compute() == 1
    _write(folder / "mod.py", "CONFIG = {'version': 2}\nraise RuntimeError('dependency hiccup')\n")
    with pytest.raises(RuntimeError):
        sw._load_current(f"{name}.mod")
    with pytest.raises(RuntimeError):  # still broken: tried again, not served half new
        sw._load_current(f"{name}.mod")
    _write(folder / "mod.py", "CONFIG = {'version': 3}\ndef compute():\n    return CONFIG['version']\n")
    assert sw._load_current(f"{name}.mod").compute() == 3


def test_a_module_that_streamlit_evicted_is_not_executed_twice(sw, package):
    """Streamlit's file watcher drops a changed module of the script folder from sys.modules: that import is already current."""
    import builtins

    name, folder = package
    counter = f"_runs_{name}"
    setattr(builtins, counter, [])
    try:
        body = f"import builtins\nbuiltins.{counter}.append(1)\n"
        _write(folder / "mod.py", body + "value = 1\n")
        assert sw._load_current(f"{name}.mod").value == 1
        _write(folder / "mod.py", body + "value = 2\n")
        del sys.modules[f"{name}.mod"]  # what LocalSourcesWatcher.flush_pending_evictions does
        assert sw._load_current(f"{name}.mod").value == 2
        assert len(getattr(builtins, counter)) == 2  # not 3
    finally:
        delattr(builtins, counter)


def test_sessions_that_arrive_together_after_a_deploy_reload_once(sw, package):
    import builtins

    name, folder = package
    counter = f"_runs_{name}"
    setattr(builtins, counter, [])
    try:
        body = f"import builtins, time\nbuiltins.{counter}.append(1)\ntime.sleep(0.3)\n"
        _write(folder / "mod.py", body + "value = 1\n")
        sw._load_current(f"{name}.mod")
        _write(folder / "mod.py", body + "value = 2\n")
        results, errors = [], []

        def session():
            try:
                results.append(sw._load_current(f"{name}.mod").value)
            except BaseException as exc:  # pragma: no cover - the assertion below reports it
                errors.append(exc)

        threads = [threading.Thread(target=session) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        assert not errors and results == [2] * 8
        assert len(getattr(builtins, counter)) == 2  # the first import and one reload
    finally:
        delattr(builtins, counter)


def test_a_reload_of_llm_cache_keeps_its_cache_and_the_jobs_that_are_running():
    """A redeploy reloads llm_cache in place while a prefetch may still be writing a meal plan."""
    import importlib

    import llm_cache

    gate = threading.Event()
    key = llm_cache.make_key("deploy-reload-test", time.time())
    llm_cache.put(key + ":kept", "an answer")
    future = llm_cache.submit(key, lambda: (gate.wait(10), "meal plan")[1])
    try:
        reloaded = importlib.reload(llm_cache)
        assert reloaded.get(key + ":kept") == "an answer"
        assert reloaded.inflight(key) is future  # still known: nobody starts the same generation again
        assert reloaded.submit(key, lambda: "a second generation") is future
    finally:
        gate.set()
    assert future.result(10) == "meal plan"
    assert reloaded.get(key) == "meal plan" and reloaded.inflight(key) is None
    reloaded.drop(key)
    reloaded.drop(key + ":kept")


def test_a_data_only_deploy_reloads_the_module_but_a_log_the_app_writes_does_not(sw, package, tmp_path):
    """The weekly USDA refresh changes data files only; the module's cached loaders must start over."""
    name, folder = package
    data = tmp_path / "data"
    data.mkdir()
    (data / "rules.json").write_text('{"v": 1}')
    (data / "unmapped_components_log.csv").write_text("a\n")
    _write(folder / "mod.py", "import json, functools\n@functools.lru_cache\ndef rules():\n    return json.load(open(%r))['v']\n" % str(data / "rules.json"))
    first = sw._load_current(f"{name}.mod", watch=data)
    assert first.rules() == 1
    time.sleep(0.05)
    (data / "unmapped_components_log.csv").write_text("a\nb\n")  # the app logging at run time: not a deploy
    assert sw._load_current(f"{name}.mod", watch=data) is first and first.rules() == 1
    time.sleep(0.05)
    (data / "rules.json").write_text('{"v": 2}')  # a data deploy
    assert sw._load_current(f"{name}.mod", watch=data).rules() == 2


def test_the_app_never_runs_a_cached_result_of_a_replaced_module():
    """After a reload the cached OCR / page-text results of the old code are dropped (their key is only the function's own source)."""
    source = (ROOT / "swipe_mobile_app" / "app.py").read_text(encoding="utf-8")
    assert "st.cache_data.clear()" in source and "_cached_rag_chunks.clear()" in source


def test_damaged_browser_data_is_cleaned_instead_of_failing_every_run(sw):
    assert sw._clean_history_entry("x") is None and sw._clean_history_entry(5) is None
    clean = sw._clean_history_entry({"ts": "2026-10-02 10:00", "kept": ["x", {"component": "Zinc", "dose": 5}], "replaced": "text"})
    assert clean == {"ts": "2026-10-02 10:00", "kept": [{"component": "Zinc", "dose": 5}], "replaced": []}
    ok = {"ts": "t", "diet": "d", "kept": [{"component": "Zinc", "dose": "10 mg"}], "replaced": [{"component": "C", "food": "Kiwi", "amount": "97 g"}]}
    assert sw._clean_history_entry(ok) == ok  # a well-formed entry is unchanged, so it still de-duplicates


def test_a_damaged_or_huge_saved_scan_is_not_offered_and_never_raises(sw):
    base = {"v": sw._SAVED_SCAN_VERSION, "text": "Vitamin C 80 mg", "total": 3, "ts": 1000.0}
    good = {**base, "decisions": {}, "label_source": {}}
    assert sw._resumable_scan(good, now=1010.0) is good
    fixed = sw._resumable_scan({**base, "decisions": 5, "label_source": 5}, now=1010.0)
    assert fixed["decisions"] == {} and fixed["label_source"] == {}
    assert sw._resume_label(fixed)  # does not raise
    assert sw._resumable_scan({**base, "text": "Vitamin C 80 mg\n" * 5000}, now=1010.0) is None  # beyond the label size cap


def test_a_chat_and_a_plan_from_before_the_fix_do_not_show_or_resend_the_old_error(sw):
    error = "Failed to resolve model configuration\n\n_🤖 General AI answer — the knowledge base couldn't answer_"
    chat = [
        {"role": "user", "content": "first"}, {"role": "assistant", "content": "a real answer\n\n_🤖 General AI answer (not from your knowledge base)_"},
        {"role": "user", "content": "second"}, {"role": "assistant", "content": error},
        {"role": "user", "content": "third"},
    ]
    kept = sw._chat_without_error_turns(chat)
    assert [m["content"] for m in kept] == ["first", "a real answer\n\n_🤖 General AI answer (not from your knowledge base)_", "third"]
    assert "Failed to resolve" not in " ".join(m["content"] for m in kept)
    assert sw._SOURCE_LABEL_RE.sub("", chat[1]["content"]).strip() == "a real answer"


def test_the_build_tag_names_the_commit_of_this_checkout():
    import re as _re

    import importlib.util as _u

    spec = _u.spec_from_file_location("suppswipe_app_tag", ROOT / "swipe_mobile_app" / "app.py")
    module = _u.module_from_spec(spec)
    spec.loader.exec_module(module)
    commit = module._build_commit()
    assert commit == "" or _re.fullmatch(r"[0-9a-f]{7}", commit)
    assert module.BUILD_TAG.startswith("2026-10-02 · deploy-safe")


def test_importing_blockbrain_works_on_a_read_only_checkout():
    """The log file is optional: a checkout the app can't write to still starts."""
    script = (
        "import logging, sys\n"
        "def _deny(*a, **k):\n    raise OSError(30, 'Read-only file system')\n"
        "logging.FileHandler = _deny\n"
        "for h in list(logging.getLogger().handlers): logging.getLogger().removeHandler(h)\n"
        f"sys.path.insert(0, r'{ROOT}')\n"
        "import blockbrain.app as bb\n"
        "print('IMPORTED', hasattr(bb, 'looks_like_agent_error'))\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert "IMPORTED True" in result.stdout, result.stdout[-400:] + result.stderr[-600:]


def test_a_rag_index_deploy_reloads_but_the_feedback_reports_the_app_writes_do_not(sw, package, tmp_path):
    """Review (Copilot): the local RAG index is data.jsonl; feedback_reports.jsonl is written at run time."""
    name, folder = package
    data = tmp_path / "data"
    data.mkdir()
    (data / "fitness_rag_chunks.jsonl").write_text('{"v": 1}\n')
    (data / "feedback_reports.jsonl").write_text("")
    _write(folder / "mod.py", "state = []\n")  # a reload starts the module over: its state is empty again
    first = sw._load_current(f"{name}.mod", watch=data)
    first.state.append("loaded once")
    time.sleep(0.05)
    (data / "feedback_reports.jsonl").write_text('{"report": 1}\n')
    assert sw._load_current(f"{name}.mod", watch=data).state == ["loaded once"]  # not a deploy
    time.sleep(0.05)
    (data / "fitness_rag_chunks.jsonl").write_text('{"v": 2}\n')
    assert sw._load_current(f"{name}.mod", watch=data).state == []  # a deploy: reloaded


def test_a_repaired_history_is_written_back_so_it_is_not_repaired_in_every_session(sw, monkeypatch):
    """Review (Copilot): one damaged record keeps the list the same length, but the cleaned list must be saved."""
    state: dict = {}
    monkeypatch.setattr(sw.st, "session_state", state)
    monkeypatch.setattr(sw.st, "rerun", lambda *a, **k: None)
    damaged = {"ts": "2026-10-02 10:00", "diet": "d", "kept": [], "replaced": "text"}
    monkeypatch.setattr(sw, "_history_store", lambda **kw: {"history": [damaged], "scan": None})
    sw._sync_scan_history_with_browser()
    repaired = {"ts": "2026-10-02 10:00", "diet": "d", "kept": [], "replaced": []}
    assert state["suppswipe_scan_history"] == [repaired]
    assert state["_suppswipe_history_save"] == [repaired]  # written back to the browser
    # An already clean history is not written again.
    state.clear()
    monkeypatch.setattr(sw, "_history_store", lambda **kw: {"history": [repaired], "scan": None})
    sw._sync_scan_history_with_browser()
    assert "_suppswipe_history_save" not in state
