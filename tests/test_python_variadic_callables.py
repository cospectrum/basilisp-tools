"""Callable type packs retain concrete positions, repeated tails and constraints."""

import importlib

import pytest

import basilisp_tools  # noqa: F401
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp


SOURCE = '''from typing import Callable, Literal, TypeVar, Unpack
from typing_extensions import TypeVarTuple
Ts = TypeVarTuple("Ts")
T = TypeVar("T")
R = TypeVar("R")
class Process:
    def __init__(self, target: Callable[[Unpack[Ts]], None], args: tuple[Unpack[Ts]]): ...
def two(a: int, b: str) -> None: ...
def fixed(*args: Unpack[tuple[int, str]]) -> str: ...
def middle(*args: Unpack[tuple[int, Unpack[tuple[str, ...]], int]]) -> int: ...
def variadic(head: int, *tail: str) -> None: ...
def literal(value: Literal[1]) -> None: ...
def unknown(value) -> None: ...
def positional_named(x: int, /, **kwargs: str) -> None: ...
def callback(a: int, b: str, c: int, d: complex) -> tuple[complex, str, int]: ...
def short(a: int, d: str) -> tuple[str]: ...
def collect(*values: Unpack[Ts]) -> tuple[Unpack[Ts]]: ...
def forward(callback: Callable[[Unpack[Ts]], R], *values: Unpack[Ts]) -> R: ...
def capture(callback: Callable[[Unpack[Ts]], None]) -> tuple[Unpack[Ts]]: ...
def drop_head(callback: Callable[[int, Unpack[Ts]], None]) -> Callable[[Unpack[Ts]], int]: ...
def rotate(callback: Callable[[int, Unpack[Ts], T], tuple[T, Unpack[Ts]]]) -> tuple[Unpack[Ts], T]: ...
'''


@pytest.fixture(scope="module")
def contracts(tmp_path_factory):
    root = tmp_path_factory.mktemp("variadic_callables")
    (root / "packs.pyi").write_text(SOURCE)
    bridge = importlib.import_module("basilisp_tools.python")
    analyzer = importlib.import_module("basilisp_tools.analyzer")
    cache = bridge.create_cache()
    options = to_lisp({"python-paths": [str(root)], "python-inspection?": False}).assoc(k("python-cache"), cache)
    try:
        yield bridge, analyzer, options
    finally:
        bridge.stop_cache__BANG__(cache)


def analyze(contracts, body):
    _, analyzer, options = contracts
    return analyzer.analyze('(ns variadic-contracts (:import [packs :as m])) ' + body,
                            to_lisp({}).assoc(k("python-options"), options))


@pytest.mark.parametrize("body,valid", [
    ('(m/capture m/unknown)', True),
    ('(m/positional-named 0 ** :x "value")', True),
    ('(m/positional-named 0 ** :x 1)', False),
    ('(m/Process ** :target m/two :args #py (0 ""))', True),
    ('(m/Process ** :target m/two :args #py ("" 0))', False),
    ('(m/forward m/fixed 1 "value")', True),
    ('(m/forward m/fixed)', False),
    ('(m/forward m/fixed 1 2)', False),
    ('(m/forward m/middle 1 2)', True),
    ('(m/forward m/middle 1 "x" "y" 2)', True),
    ('(m/forward m/middle)', False),
    ('(m/forward m/middle 1 2 3)', False),
    ('((m/drop-head m/variadic))', True),
    ('((m/drop-head m/variadic) "x" "y")', True),
    ('((m/drop-head m/variadic) 1)', False),
])
def test_pack_call_shapes_accept_only_supported_arguments(contracts, body, valid):
    findings = list(analyze(contracts, body).val_at(k("findings")))
    if valid:
        assert findings == []
    else:
        assert any(finding.val_at(k("type")) in {k("invalid-arity"), k("type-mismatch")}
                   for finding in findings)


@pytest.mark.parametrize("function,argument,expected", [
    ("rotate", "callback", ["str", "int", "complex"]),
    ("rotate", "short", ["str"]),
])
def test_fixed_prefix_and_suffix_are_removed_from_captured_pack(contracts, function, argument, expected):
    bridge, _, options = contracts
    metadata = bridge.inspect_path("packs", to_lisp([function]), options)
    callback = bridge.callable_type(bridge.inspect_path("packs", to_lisp([argument]), options))
    result = bridge.call_result(metadata, to_lisp([callback]), to_lisp({}), options)
    assert result == to_lisp({"module": "builtins", "path": ["tuple"],
                              "arguments": [{"module": "builtins", "path": [name]} for name in expected]})
    assert len(analyze(contracts, f"(m/{function} m/{argument})").val_at(k("findings"))) == 0


def test_pack_value_inference_widens_literals_but_callback_bounds_retain_them(contracts):
    bridge, _, options = contracts
    integer = {"module": "builtins", "path": ["int"]}
    string = {"module": "builtins", "path": ["str"]}
    collect = bridge.inspect_path("packs", to_lisp(["collect"]), options)
    inferred = bridge.call_result(collect, to_lisp([{**integer, "literal-values": [1]},
                                                   {**string, "literal-values": ["x"]}]), to_lisp({}), options)
    assert inferred == to_lisp({"module": "builtins", "path": ["tuple"], "arguments": [integer, string]})
    capture = bridge.inspect_path("packs", to_lisp(["capture"]), options)
    callback = bridge.callable_type(bridge.inspect_path("packs", to_lisp(["literal"]), options))
    inferred = bridge.call_result(capture, to_lisp([callback]), to_lisp({}), options)
    assert inferred == to_lisp({"module": "builtins", "path": ["tuple"],
                                "arguments": [{**integer, "literal-values": [1]}]})
