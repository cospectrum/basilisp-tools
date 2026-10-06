"""Python subscription contracts adapted from checker subscript/generic suites."""

import importlib

import pytest

import basilisp_tools  # noqa: F401
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp


@pytest.fixture(scope="module")
def environment(tmp_path_factory):
    root = tmp_path_factory.mktemp("subscription_contracts")
    (root / "subscriptions.py").write_text('''from typing import Generic, TypeVar, NamedTuple
T = TypeVar("T")
class Box(Generic[T]):
    value: T
    def __init__(self, value: T): self.value = value
    def __getitem__(self, key: str) -> T: return self.value
class Pair(NamedTuple):
    number: int
    text: str
class Index:
    def __index__(self) -> int: return 0
class Custom:
    def __class_getitem__(cls, index: int) -> str: return str(index)
class Meta(type):
    def __getitem__(cls, index: str) -> int: return len(index)
class ViaMeta(metaclass=Meta):
    def __class_getitem__(cls, index: int) -> str: return str(index)
def pair() -> tuple[int, str]: return (1, "a")
def named() -> Pair: return Pair(1, "a")
def box() -> Box[int]: return Box(1)
''')
    analyzer = importlib.import_module("basilisp_tools.analyzer")
    bridge = importlib.import_module("basilisp_tools.python")
    cache = bridge.create_cache()
    options = to_lisp({"python-options": {"python-paths": [str(root)], "python-inspection?": False,
                                        "python-cache": cache}})
    try:
        yield analyzer, options
    finally:
        bridge.stop_cache__BANG__(cache)


def analyze(environment, expression):
    analyzer, options = environment
    imports = ' (:import [subscriptions :as s])' if 's/' in expression else ''
    result = analyzer.analyze('(ns subscription-check' + imports + ')\n' + expression, options)
    findings = list(result[k("findings")])
    assert not any(str(f[k("message")]).startswith("Analysis failed") for f in findings)
    types = [item[k("python-type")] for item in result[k("python-expressions")]
             if item[k("row")] == 2 and item[k("col")] == 1]
    return types[-1] if types else None, findings


@pytest.mark.parametrize("expression,expected", [
    ('(aget "abc" -1)', {"module": "builtins", "path": ["str"], "literal-values": ["c"]}),
    ('(aget "👍🏼" 1)', {"module": "builtins", "path": ["str"], "literal-values": ["🏼"]}),
    ('(aget "abc" true)', {"module": "builtins", "path": ["str"], "literal-values": ["b"]}),
    ('(aget #b "abc" -1)', {"module": "builtins", "path": ["int"], "literal-values": [99]}),
    ('(aget (s/pair) -1)', {"module": "builtins", "path": ["str"]}),
    ('(aget (s/pair) false)', {"module": "builtins", "path": ["int"]}),
    ('(aget (s/named) -1)', {"module": "builtins", "path": ["str"]}),
    ('(aget (s/box) "key")', {"module": "builtins", "path": ["int"]}),
    ('(aget #py [#py [1]] 0 0)', {"module": "builtins", "path": ["int"]}),
    ('(.-value (aget s/Box python/int))', {"module": "builtins", "path": ["int"]}),
    ('(let [klass (aget s/Box python/int)] (.-value klass))', {"module": "builtins", "path": ["int"]}),
    ('(do (def klass (aget s/Box python/int)) (.-value klass))', {"module": "builtins", "path": ["int"]}),
    ('(do (def data #b "abc") (aget data 0))', {"module": "builtins", "path": ["int"], "literal-values": [97]}),
    ('(aget python/list python/int)', {"module": "builtins", "path": ["type"], "arguments": [
        {"module": "builtins", "path": ["list"], "arguments": [{"module": "builtins", "path": ["int"]}]}]}),
    ('(aget s/Custom 3)', {"module": "builtins", "path": ["str"]}),
    ('(aget s/ViaMeta "abc")', {"module": "builtins", "path": ["int"]}),
])
def test_subscription_result(environment, expression, expected):
    actual, findings = analyze(environment, expression)
    assert findings == []
    assert actual == to_lisp(expected)


@pytest.mark.parametrize("expression", [
    '(aget "abc" 1.5)', '(aget #py [1] "key")', '(aget (s/box) 1)',
    '(aget s/Custom "bad")', '(aget s/ViaMeta 1)',
    '(aget "abc" 3)', '(aget "abc" -4)', '(aget (s/pair) 2)', '(aget #py () 0)', '(aget #b "abc" 3)',
])
def test_subscription_argument_errors(environment, expression):
    _, findings = analyze(environment, expression)
    assert [f[k("type")] for f in findings] == [k("type-mismatch")]


@pytest.mark.parametrize("expression", [
    '(aget "abc" (s/Index))', '(defn index [x] (aget "abc" x))',
    '(defn index [x] (aget (s/box) x))',
])
def test_unknown_and_index_protocol_are_not_rejected(environment, expression):
    _, findings = analyze(environment, expression)
    assert findings == []
