"""Class values and polymorphic callbacks keep independent invocation scopes."""

import importlib

import pytest

import basilisp_tools  # noqa: F401
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp


SOURCE = '''from typing import Callable, Generic, ParamSpec, TypeVar, overload
class Meta(type): ...
class OtherMeta(type): ...
class E(metaclass=Meta): ...
class Child(E): ...
class Other(metaclass=OtherMeta): ...
@overload
def metaclass_result(value: Meta) -> int: ...
@overload
def metaclass_result(value: OtherMeta) -> str: ...
def needs_meta(value: Meta) -> None: ...
A = TypeVar("A")
B = TypeVar("B")
P = ParamSpec("P")
class Gen(Generic[A]): ...
def identity(value: A) -> A: ...
def nested(first: Gen[A], second: A) -> Gen[Gen[A]]: ...
def bad_return(first: Gen[int], second: int) -> str: ...
def bad_shape() -> Gen[Gen[int]]: ...
def combine(value: Gen[A], identity: Callable[[B], B], nested: Callable[[A, B], Gen[A]]) -> A: ...
x: Gen[Gen[int]]
class Forward(Generic[P, A]):
 def __init__(self, target: Callable[P, A], *args: P.args, **kwargs: P.kwargs) -> None: ...
class Target(Generic[A]):
 def __init__(self, value: type[A]) -> None: ...
def integer(value: int) -> int: ...
'''


@pytest.fixture(scope="module")
def contracts(tmp_path_factory):
    root = tmp_path_factory.mktemp("polymorphic_values")
    (root / "poly.pyi").write_text(SOURCE)
    bridge = importlib.import_module("basilisp_tools.python")
    analyzer = importlib.import_module("basilisp_tools.analyzer")
    cache = bridge.create_cache()
    opts = to_lisp({"python-paths": [str(root)], "python-inspection?": False}).assoc(k("python-cache"), cache)
    try:
        yield bridge, analyzer, opts
    finally:
        bridge.stop_cache__BANG__(cache)


@pytest.mark.parametrize("body,valid", [
    ("(m/needs-meta m/E)", True),
    ("(m/needs-meta m/Child)", True),
    ("(m/needs-meta m/Other)", False),
    ('(m/needs-meta "bad")', False),
    ("(m/Forward m/Target m/E)", True),
    ("(m/Forward)", False),
    ('(m/Forward m/integer "bad")', False),
    ("(m/combine m/x m/identity m/nested)", True),
    ("(m/combine m/x m/identity m/bad-return)", False),
    ("(m/combine m/x m/identity m/bad-shape)", False),
])
def test_polymorphic_values_preserve_known_negative_checks(contracts, body, valid):
    _, analyzer, opts = contracts
    result = analyzer.analyze('(ns polymorphic-values (:import [poly :as m])) ' + body,
                              to_lisp({}).assoc(k("python-options"), opts))
    findings = list(result.val_at(k("findings")))
    if valid:
        assert findings == []
    else:
        assert any(finding.val_at(k("type")) in {k("type-mismatch"), k("invalid-arity")}
                   for finding in findings)


@pytest.mark.parametrize("argument,expected", [("E", "int"), ("Child", "int"), ("Other", "str")])
def test_class_value_uses_actual_metaclass_to_select_overload(contracts, argument, expected):
    bridge, _, opts = contracts
    metadata = bridge.inspect_path("poly", to_lisp(["metaclass_result"]), opts)
    actual = bridge.callable_type(bridge.inspect_path("poly", to_lisp([argument]), opts))
    result = bridge.call_result(metadata, to_lisp([actual]), to_lisp({}), opts)
    assert result == to_lisp({"module": "builtins", "path": [expected]})


def test_polymorphic_callback_cannot_replace_concrete_outer_binding(contracts):
    bridge, _, opts = contracts
    value = bridge.return_type(bridge.inspect_path("poly", to_lisp(["x"]), opts))
    callbacks = [bridge.callable_type(bridge.inspect_path("poly", to_lisp([name]), opts))
                 for name in ("identity", "nested")]
    metadata = bridge.inspect_path("poly", to_lisp(["combine"]), opts)
    result = bridge.call_result(metadata, to_lisp([value, *callbacks]), to_lisp({}), opts)
    assert result == to_lisp({"module": "poly", "path": ["Gen"],
                              "arguments": [{"module": "builtins", "path": ["int"]}]})


@pytest.mark.parametrize("actual,expected,valid", [
    ("ValueError", "Exception", True),
    ("KeyError", "LookupError", True),
    ("ValueError", "TypeError", False),
    ("str", "Exception", False),
    ("str", "int", False),
])
def test_builtin_exception_ancestry_preserves_unrelated_negative_types(contracts, actual, expected, valid):
    bridge, _, opts = contracts
    actual_type = to_lisp({"module": "builtins", "path": [actual]})
    expected_type = to_lisp({"module": "builtins", "path": [expected]})
    assert bridge.type_compatible__Q__(actual_type, expected_type, opts) is valid
