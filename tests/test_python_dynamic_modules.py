"""Dynamic module hooks may be declared, assigned, or reexported."""

import importlib
import importlib.util
from pathlib import Path
import sys
import types

import pytest


@pytest.fixture
def worker():
    path = Path(__file__).resolve().parents[1] / "src/basilisp_tools/_inspect.py"
    spec = importlib.util.spec_from_file_location("_blt_dynamic_modules", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("source,valid", [
    ('from typing import Callable\n'
     'def make() -> Callable[[str], int]: return lambda name: 42\n'
     '__getattr__ = make()\n', True),
    ('from hook_source import __getattr__\n', True),
    ('from typing import Callable\n'
     'def make() -> Callable[[], int]: return lambda: 42\n'
     '__getattr__ = make()\n', False),
])
def test_dynamic_hook_exports_do_not_prove_missing_members(worker, tmp_path, monkeypatch, source, valid):
    hook = types.ModuleType("hook_source")
    hook_source = 'def __getattr__(name: str) -> int: return 42\n'
    exec(hook_source, vars(hook))
    monkeypatch.setitem(sys.modules, "hook_source", hook)
    (tmp_path / "hook_source.py").write_text(hook_source)
    (tmp_path / "dynamic_fixture.py").write_text(source)
    metadata = worker.StaticInspector([str(tmp_path)], []).module("dynamic_fixture")
    assert not metadata["members-complete?"]
    assert "__getattr__" in metadata["members"]
    native = types.ModuleType("dynamic_fixture")
    exec(source, vars(native))
    if valid:
        assert native.missing == 42
    else:
        # The invalid hook signature is distinct from claiming the member is absent.
        with pytest.raises(TypeError):
            native.missing

    import basilisp_tools  # noqa: F401
    from basilisp.lang.keyword import keyword as k
    from basilisp.lang.runtime import to_lisp

    analyzer = importlib.import_module("basilisp_tools.analyzer")
    bridge = importlib.import_module("basilisp_tools.python")
    cache = bridge.create_cache()
    try:
        options = to_lisp({"python-options": {"python-paths": [str(tmp_path)],
                                            "python-inspection?": False, "python-cache": cache}})
        result = analyzer.analyze('(ns dynamic-hook-check (:import [dynamic_fixture :as d]))\nd/missing', options)
        assert not list(result[k("findings")])
    finally:
        bridge.stop_cache__BANG__(cache)


def test_ordinary_module_still_proves_missing_member(worker, tmp_path):
    (tmp_path / "ordinary.py").write_text('present = 42\n')
    assert worker.StaticInspector([str(tmp_path)], []).module("ordinary")["members-complete?"]
