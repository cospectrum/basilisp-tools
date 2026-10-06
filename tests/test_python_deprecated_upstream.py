"""Deprecated Python APIs retain their warnings in Basilisp consumer forms."""

import importlib

import basilisp_tools  # noqa: F401
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp


def test_deprecated_python_references_calls_properties_and_overloads(tmp_path):
    (tmp_path / "deprecated_api.py").write_text('''from typing import overload
from typing_extensions import deprecated
@deprecated("use fresh")
def old(value: int) -> int: return value
def fresh(value: int) -> int: return value
@overload
@deprecated("use a string")
def choose(value: int) -> int: ...
@overload
def choose(value: str) -> str: ...
def choose(value): return value
class Item:
    @property
    @deprecated("use current")
    def previous(self) -> int: return 1
    @property
    def current(self) -> int: return 1
@deprecated("use Item")
class OldItem: pass
''')
    analyzer = importlib.import_module("basilisp_tools.analyzer")
    bridge = importlib.import_module("basilisp_tools.python")
    cache = bridge.create_cache()
    options = to_lisp({"python-options": {"python-paths": [str(tmp_path)], "python-inspection?": False,
                                        "python-cache": cache}})
    try:
        result = analyzer.analyze('''(ns deprecation-check (:import [deprecated_api :as api]))
(api/old 1)
(api/fresh 1)
(api/choose 1)
(api/choose "ok")
(.-previous (api/Item))
(.-current (api/Item))
(api/OldItem)
(map api/old [1])
''', options)
        findings = list(result[k("findings")])
        assert [(f[k("row")], f[k("type")], f[k("level")]) for f in findings] == [
            (row, k("deprecated-var"), k("warning")) for row in (2, 4, 6, 8, 9)]
        assert all("use " in f[k("message")] for f in findings)
    finally:
        bridge.stop_cache__BANG__(cache)
