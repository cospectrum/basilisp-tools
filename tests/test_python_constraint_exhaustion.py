"""Callback constraints retain failure and distinguish upper from lower evidence."""

import importlib

import basilisp_tools
import pytest
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp

SOURCE = """from typing import Any, Callable, NoReturn, TypeVar, overload
T = TypeVar("T", int, str)
def constrained(consumer: Callable[[T], None]) -> T: ...
def accepts_int(value: int) -> None: pass
def accepts_str(value: str) -> None: pass
def accepts_bool(value: bool) -> None: pass
def accepts_any(value: Any) -> None: pass
def accepts_object(value: object) -> None: pass
def bottom() -> NoReturn: raise RuntimeError()
def identity(value: T) -> T: return value

Narrow = TypeVar("Narrow", int, object)
Broad = TypeVar("Broad", object, int)
def narrow(callback: Callable[[Narrow], None]) -> Narrow: ...
def broad(callback: Callable[[Broad], None]) -> Broad: ...
def witnessed(callback: Callable[[Narrow], None], value: Narrow) -> Narrow: return value
def reversed_args(value: Narrow, callback: Callable[[Narrow], None]) -> Narrow: return value

class Base: pass
class Middle(Base): pass
class Child(Middle): pass
Nominal = TypeVar("Nominal", Middle, Base)
def accepts_base(value: Base) -> None: pass
def accepts_middle(value: Middle) -> None: pass
def nominal(callback: Callable[[Nominal], None]) -> Nominal: ...
def nominal_witness(callback: Callable[[Nominal], None], value: Nominal) -> Nominal: return value
def nominal_reversed(value: Nominal, callback: Callable[[Nominal], None]) -> Nominal: return value

U = TypeVar("U", int, str)
V = TypeVar("V", int, str)
@overload
def source_pair(value: int) -> tuple[int, str]: ...
@overload
def source_pair(value: str) -> tuple[str, int]: ...
def source_pair(value: int | str) -> tuple[int, str] | tuple[str, int]:
    return (value, "") if isinstance(value, int) else (value, 1)
def good_pair(value: int) -> tuple[int, int]: return (value, value)
def unknown_pair(value: Any) -> Any: return value
def g(callback: Callable[..., tuple[U, V]], x: U, y: V) -> None: pass
"""


@pytest.fixture(scope="module")
def contracts(tmp_path_factory):
    root = tmp_path_factory.mktemp("constraint_exhaustion")
    (root / "constraint_fixture.py").write_text(SOURCE)
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
        "(ns constraints-audit (:import [constraint_fixture :as c])) " + expression,
        to_lisp({}).assoc(k("python-options"), opts),
    )


def inferred(result):
    return list(result.val_at(k("python-expressions")))[-1].val_at(k("python-type"))


@pytest.mark.parametrize("callback", ["accepts_int", "accepts_str", "accepts_any"])
def test_one_viable_or_unknown_alternative_remains_accepted(contracts, callback):
    assert (
        list(analyze(contracts, f"(c/constrained c/{callback})").val_at(k("findings")))
        == []
    )


def test_exhausted_alternatives_are_not_a_vacuously_valid_never_callback(contracts):
    result = analyze(contracts, "(c/constrained c/accepts_bool)")
    assert any(
        f.val_at(k("type")) == k("type-mismatch") for f in result.val_at(k("findings"))
    )


def test_actual_bottom_input_does_not_mean_constraints_failed(contracts):
    assert (
        list(analyze(contracts, "(c/identity (c/bottom))").val_at(k("findings"))) == []
    )


@pytest.mark.parametrize("function", ["narrow", "broad"])
def test_upper_only_constraints_choose_general_declared_type(contracts, function):
    result = analyze(contracts, f"(c/{function} c/accepts_object)")
    assert list(result.val_at(k("findings"))) == []
    assert inferred(result) == to_lisp({"module": "builtins", "path": ["object"]})


@pytest.mark.parametrize(
    "expression",
    ["(c/witnessed c/accepts_object 1)", "(c/reversed_args 1 c/accepts_object)"],
)
def test_lower_witness_selects_specific_constraint_independent_of_order(
    contracts, expression
):
    result = analyze(contracts, expression)
    assert list(result.val_at(k("findings"))) == []
    assert inferred(result) == to_lisp({"module": "builtins", "path": ["int"]})


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("(c/nominal c/accepts_base)", "Base"),
        ("(c/nominal c/accepts_middle)", "Middle"),
        ("(c/nominal_witness c/accepts_base (c/Child))", "Middle"),
        ("(c/nominal_reversed (c/Child) c/accepts_base)", "Middle"),
    ],
)
def test_nominal_upper_selection_and_lower_promotion(contracts, expression, expected):
    result = analyze(contracts, expression)
    assert list(result.val_at(k("findings"))) == []
    assert inferred(result) == to_lisp(
        {"module": "constraint_fixture", "path": [expected]}
    )


def test_ellipsis_shape_does_not_hide_incompatible_overload_returns(contracts):
    result = analyze(contracts, "(c/g c/source_pair 1 1)")
    assert any(
        f.val_at(k("type")) == k("type-mismatch") for f in result.val_at(k("findings"))
    )


@pytest.mark.parametrize("callback", ["good_pair", "unknown_pair"])
def test_ellipsis_accepts_compatible_or_unknown_returns(contracts, callback):
    assert (
        list(analyze(contracts, f"(c/g c/{callback} 1 1)").val_at(k("findings"))) == []
    )


def test_callback_uncertainty_keeps_independent_arity_failure(contracts):
    result = analyze(contracts, "(c/g c/unknown_pair 1)")
    assert any(
        f.val_at(k("type")) == k("invalid-arity") for f in result.val_at(k("findings"))
    )


def test_native_nominal_witness_preserves_value():
    namespace = {}
    exec(SOURCE, namespace)
    value = namespace["Child"]()
    assert namespace["nominal_witness"](namespace["accepts_base"], value) is value
    assert namespace["nominal_reversed"](value, namespace["accepts_base"]) is value
