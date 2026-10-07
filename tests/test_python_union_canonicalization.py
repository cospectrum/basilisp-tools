"""Equivalent top-level object and class-object unions have stable inferred types."""
import importlib
import pytest
import basilisp_tools
from basilisp.lang.runtime import to_lisp
from basilisp.lang.keyword import keyword as k

obj = {"module": "builtins", "path": ["object"]}
integer = {"module": "builtins", "path": ["int"]}
string = {"module": "builtins", "path": ["str"]}
none = {"module": "builtins", "path": ["NoneType"]}
def klass(t):
    return {"module": "builtins", "path": ["type"], "arguments": [t]}

@pytest.mark.parametrize("types,expected", [
    ([obj, integer], obj), ([integer, obj], obj),
    ([obj, klass(integer)], obj), ([obj, none], obj),
    ([klass(obj), klass(integer)], klass(obj)),
    ([klass(integer), klass(obj)], klass(obj)),
    ([klass(obj), klass(klass(integer))], klass(obj)),
    ([klass(obj), klass(integer), string], {"union": [klass(obj), string]}),
    ([klass(obj), none], {**klass(obj), "nullable?": True}),
    ([obj, None], None), ([obj, {"any?": True}], None),
    ([integer, string], {"union": [integer, string]}),
    ([{"module": "foreign", "path": ["object"]}, integer],
     {"union": [{"module": "foreign", "path": ["object"]}, integer]}),
])
def test_union_canonicalization(types, expected):
    bridge = importlib.import_module("basilisp_tools.python")
    assert bridge.union_type(to_lisp(types)) == to_lisp(expected)


@pytest.mark.parametrize("actual,compatible", [(1, True), ("x", True), (2, False), ("y", False), (True, False)])
def test_mixed_literals_do_not_widen_to_their_builtin_types(actual, compatible):
    bridge = importlib.import_module("basilisp_tools.python")
    expected = {"union": [integer, string], "literal-values": [1, "x"]}
    actual_type = {"module": "builtins", "path": [type(actual).__name__], "literal-values": [actual]}
    assert bridge.type_compatible__Q__(to_lisp(actual_type), to_lisp(expected)) is compatible


def test_none_is_a_known_literal_without_an_explicit_literal_payload():
    bridge = importlib.import_module("basilisp_tools.python")
    expected = {**none, "literal-values": [None]}
    assert bridge.type_compatible__Q__(to_lisp(none), to_lisp(expected)) is True


def test_inferred_union_return_simplifies_without_losing_literal_constraints(tmp_path):
    (tmp_path / "union_contracts.pyi").write_text('''from typing import TypeVar, Union, Literal
T = TypeVar("T")
S = TypeVar("S")
def union(x: T, y: S) -> Union[T, S]: ...
def literal() -> Literal[1, "x"]: ...
def expects_literal(x: Literal[1, "x"]) -> None: ...
''')
    bridge = importlib.import_module("basilisp_tools.python")
    analyzer = importlib.import_module("basilisp_tools.analyzer")
    cache = bridge.create_cache()
    options = to_lisp({"python-options": {"python-paths": [str(tmp_path)], "python-cache": cache,
                                        "python-inspection?": False}})
    try:
        result = analyzer.analyze('(ns union-test (:import [union_contracts :as m]))\n'
                                  '(m/union python/int (python/object))\n'
                                  '(m/expects-literal (m/literal))\n'
                                  '(m/expects-literal 2)', options)
        returns = [e[k("python-type")] for e in result[k("python-expressions")]
                   if e[k("row")] == 2 and e[k("col")] == 1]
        assert returns == [to_lisp(obj)]
        assert [(f[k("type")], f[k("row")]) for f in result[k("findings")]] == [(k("type-mismatch"), 4)]
    finally:
        bridge.stop_cache__BANG__(cache)
