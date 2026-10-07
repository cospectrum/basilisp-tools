"""Class objects retain their declared generic constraints when used as values."""
import importlib

import basilisp_tools  # noqa: F401
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp


def test_class_subscription_promotes_a_subclass_to_its_typevar_constraint(tmp_path):
    (tmp_path / "class_values.pyi").write_text('''from typing import Generic, TypeVar
class First: ...
class Child(First): ...
class Second: ...
T = TypeVar("T", First, Second)
class Family(Generic[T]): ...
''')
    analyzer = importlib.import_module("basilisp_tools.analyzer")
    bridge = importlib.import_module("basilisp_tools.python")
    cache = bridge.create_cache()
    options = to_lisp({"python-options": {"python-paths": [str(tmp_path)], "python-cache": cache,
                                        "python-inspection?": False}})
    try:
        result = analyzer.analyze('(ns class-values (:import [class_values :as m]))\n'
                                  '(aget m/Family m/Child)', options)
        assert list(result[k("findings")]) == []
        types = [e[k("python-type")] for e in result[k("python-expressions")]
                 if e[k("row")] == 2 and e[k("col")] == 1]
        assert types == [to_lisp({"module": "builtins", "path": ["type"], "arguments": [
            {"module": "class_values", "path": ["Family"], "arguments": [
                {"module": "class_values", "path": ["First"]}]}]})]
    finally:
        bridge.stop_cache__BANG__(cache)
