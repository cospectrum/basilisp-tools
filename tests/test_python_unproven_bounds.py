"""Unknown bound intersections must not invent a definite bound instance."""

import importlib

import pytest
import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp

SOURCE = """from dataclasses import dataclass
from typing import Any, Callable, Generic, Protocol, TypeVar, TypedDict
class TD1(TypedDict):
    a: str
class TD2(TD1):
    b: str
T = TypeVar("T", bound=TD1)
I = TypeVar("I", bound=int)
@dataclass
class DC1(Generic[T]):
    x: T
@dataclass
class DC2(Generic[T]):
    y: list[DC1[T]]
@dataclass
class DC3(Generic[T]):
    embedded: DC2[T]
def identity(value: T) -> T:
    return value
def integer(value: I) -> I:
    return value
F = TypeVar("F", bound=Callable[..., Any])
def decorate(value: F) -> F:
    return value
def known_function(value: str) -> int:
    return len(value)
class Readable(Protocol):
    def read(self) -> Any: ...
class Number:
    def read(self) -> int:
        return 1
B = TypeVar("B", bound=Readable)
def protocol_identity(value: B) -> B:
    return value
"""


@pytest.fixture(scope="module")
def contracts(tmp_path_factory):
    root = tmp_path_factory.mktemp("unproven_bounds")
    (root / "bounds.py").write_text(SOURCE)
    bridge = importlib.import_module("basilisp_tools.python")
    analyzer = importlib.import_module("basilisp_tools.analyzer")
    cache = bridge.create_cache()
    opts = to_lisp({"python-paths": [str(root)], "python-inspection?": False}).assoc(
        k("python-cache"), cache
    )
    try:
        yield bridge, analyzer, opts
    finally:
        bridge.stop_cache__BANG__(cache)


def analyze(contracts, expression):
    _, analyzer, opts = contracts
    return analyzer.analyze(
        "(ns bounds-audit (:import [bounds :as b])) " + expression,
        to_lisp({}).assoc(k("python-options"), opts),
    )


def test_nested_fresh_typed_dict_context_does_not_narrow_to_the_bound(contracts):
    result = analyze(
        contracts,
        '((aget b/DC3 b/TD2) (b/DC2 ** :y #py [(b/DC1 ** :x #py {"a" "" "b" ""})]))',
    )
    assert list(result.val_at(k("findings"))) == []
    inferred = list(result.val_at(k("python-expressions")))[-1].val_at(k("python-type"))
    assert inferred == to_lisp(
        {
            "module": "bounds",
            "path": ["DC3"],
            "arguments": [{"module": "bounds", "path": ["TD2"]}],
        }
    )


def test_unproven_bound_result_preserves_the_observed_input(contracts):
    bridge, _, opts = contracts
    member = bridge.inspect_path("bounds", to_lisp(["identity"]), opts)
    actual = to_lisp(
        {
            "module": "builtins",
            "path": ["dict"],
            "arguments": [
                {"module": "builtins", "path": ["str"]},
                {"module": "builtins", "path": ["str"]},
            ],
        }
    )
    assert bridge.call_result(member, to_lisp([actual]), to_lisp({}), opts) == actual


@pytest.mark.parametrize(
    "expression",
    [
        '(b/integer "bad")',
        '((aget b/DC3 b/TD2) (b/DC2 ** :y #py [(b/DC1 ** :x (b/TD1 ** :a "a"))]))',
    ],
)
def test_proven_bound_and_typed_dict_mismatches_remain_errors(contracts, expression):
    findings = list(analyze(contracts, expression).val_at(k("findings")))
    assert any(
        finding.val_at(k("type")) == k("type-mismatch") for finding in findings
    ), findings


def test_proven_subtype_keeps_its_inferred_type(contracts):
    result = analyze(contracts, '(b/identity (b/TD2 ** :a "a" :b "b"))')
    assert list(result.val_at(k("findings"))) == []
    inferred = list(result.val_at(k("python-expressions")))[-1].val_at(k("python-type"))
    assert inferred == to_lisp({"module": "bounds", "path": ["TD2"]})


def test_native_nested_constructor_accepts_the_extra_typed_dict_field():
    namespace = {}
    exec(SOURCE, namespace)
    value = namespace["DC3"][namespace["TD2"]](
        namespace["DC2"]([namespace["DC1"]({"a": "", "b": ""})])
    )
    assert value.embedded.y[0].x == {"a": "", "b": ""}


def test_callable_bound_does_not_erase_known_function_signature(contracts):
    result = analyze(contracts, "(b/decorate b/known_function)")
    assert list(result.val_at(k("findings"))) == []
    inferred = list(result.val_at(k("python-expressions")))[-1].val_at(k("python-type"))
    arguments = list(inferred.val_at(k("arguments")))
    assert arguments[1] == to_lisp({"module": "builtins", "path": ["int"]})
    parameters = list(arguments[0].val_at(k("parameters")))
    assert parameters[0].val_at(k("type-path")) == to_lisp(["str"])


def test_protocol_bound_preserves_concrete_implementing_receiver(contracts):
    result = analyze(contracts, "(b/protocol_identity (b/Number))")
    assert list(result.val_at(k("findings"))) == []
    inferred = list(result.val_at(k("python-expressions")))[-1].val_at(k("python-type"))
    assert inferred == to_lisp({"module": "bounds", "path": ["Number"]})
