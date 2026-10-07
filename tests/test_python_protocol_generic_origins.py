"""Unrelated generic parameters do not constrain a structural protocol."""
import importlib

import basilisp_tools
import pytest
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp

SOURCE = '''from __future__ import annotations
from typing import Any, ClassVar, Generic, Protocol, TypeVar
T = TypeVar("T")
U = TypeVar("U")
class Box(Protocol[T]):
    content: T
class Linked(Protocol[T]):
    val: T
    def next(self) -> Linked[T]: ...
class Item(Generic[T]):
    content: T
    def __init__(self, content: T): self.content = content
class L(Generic[T]):
    val: Box[T]
    def __init__(self, val: Box[T]): self.val = val
    def next(self) -> L[T]: return self
class Value(Protocol[T]):
    value: T
class Unrelated(Generic[T]):
    value: str
    padding: T
    def __init__(self, padding: T):
        self.padding = padding
        self.value = "ok"
class Reordered(Protocol[T, U]):
    padding: T
    value: U
class Actual(Generic[T, U]):
    value: T
    padding: U
    def __init__(self, value: T, padding: U):
        self.value = value
        self.padding = padding
class TextClass:
    value: ClassVar[str] = "class text"
class Dynamic(Generic[T]):
    value: str
    def __getattribute__(self, name): return 42

def last(seq: Linked[T]) -> T: return seq.val

def get_value(seq: Value[T]) -> T: return seq.value

def get_reordered(seq: Reordered[T, U]) -> U: return seq.value

def make_l() -> L[int]: return L(Item(1))

def make_str_l() -> L[str]: return L(Item("text"))

def actual() -> Actual[int, str]: return Actual(1, "padding")

def take_int(value: int) -> None: pass

def take_str(value: str) -> None: pass
same: Value[str] = Unrelated(1)
unknown: Any = Unrelated(1)
'''

@pytest.fixture(scope="module")
def contracts(tmp_path_factory):
    root = tmp_path_factory.mktemp("protocol_origins")
    (root / "origins.py").write_text(SOURCE)
    bridge = importlib.import_module("basilisp_tools.python")
    analyzer = importlib.import_module("basilisp_tools.analyzer")
    cache = bridge.create_cache()
    opts = to_lisp({"python-paths": [str(root)], "python-inspection?": False}).assoc(
        k("python-cache"), cache
    )
    try:
        yield analyzer, opts
    finally:
        bridge.stop_cache__BANG__(cache)


def analyze(contracts, expression):
    analyzer, opts = contracts
    return analyzer.analyze(
        "(ns protocol-origin (:import [origins :as p])) " + expression,
        to_lisp({}).assoc(k("python-options"), opts),
    )


def scalar(name):
    return {"module": "builtins", "path": [name]}


@pytest.mark.parametrize("expression,expected", [
    ("(p/last (p/make_l))", {"module": "origins", "path": ["Box"], "arguments": [scalar("int")]}),
    ("(.-content (p/last (p/make_l)))", scalar("int")),
    ("(.-content (p/last (p/make_str_l)))", scalar("str")),
    ("(p/get_value (p/Unrelated 1))", scalar("str")),
    ("(p/get_value p/same)", scalar("str")),
    ("(p/get_reordered (p/actual))", scalar("int")),
])
def test_structural_member_types_determine_generic_results(contracts, expression, expected):
    result = analyze(contracts, expression)
    assert list(result.val_at(k("findings"))) == []
    actual = list(result.val_at(k("python-expressions")))[-1].val_at(k("python-type"))
    assert actual == to_lisp(expected)


@pytest.mark.parametrize("expression,valid", [
    ("(p/take_int (.-content (p/last (p/make_l))))", True),
    ("(p/take_str (.-content (p/last (p/make_l))))", False),
    ("(p/take_str (p/get_value (p/Unrelated 1)))", True),
    ("(p/take_int (p/get_value (p/Unrelated 1)))", False),
    ("(p/take_int (p/get_value p/unknown))", True),
    ("(p/take_str (p/get_value p/unknown))", True),
    ("(p/take_str (p/get_value p/TextClass))", True),
    ("(p/take_int (p/get_value (p/Dynamic)))", True),
])
def test_precise_results_and_unknown_inputs_keep_call_contracts(contracts, expression, valid):
    findings = list(analyze(contracts, expression).val_at(k("findings")))
    if valid:
        assert findings == []
    else:
        assert any(f.val_at(k("type")) == k("type-mismatch") for f in findings), findings


def test_native_nested_protocol_values():
    namespace = {}
    exec(SOURCE, namespace)
    value = namespace["last"](namespace["make_l"]())
    assert value.content == 1
    assert namespace["get_value"](namespace["Unrelated"](1)) == "ok"
    assert namespace["get_value"](namespace["Dynamic"]()) == 42
    assert namespace["get_value"](namespace["TextClass"]) == "class text"
