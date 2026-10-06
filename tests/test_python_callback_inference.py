"""Callback input bounds must retain variance, literals, and argument order."""

import importlib
import itertools
import json

import pytest

import basilisp_tools  # noqa: F401
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp


CONTRACTS = '''from typing import Callable, Literal, TypeVar
T = TypeVar("T")
U = TypeVar("U")
class A: ...
class B: ...
First = Literal[5] | A | list["First"]
Second = Literal[5] | B | list["Second"]
def first(value: First) -> None: ...
def second(value: Second) -> None: ...
def literal(value: Literal[5]) -> None: ...
def integer(value: int) -> str: ...
def string(value: str) -> None: ...
def integer_identity(value: int) -> int: ...
def integer_factory() -> int: ...
def string_factory() -> str: ...
def two_arguments(first: int, second: int) -> None: ...
def accepts_consumer(callback: Callable[[int], None]) -> None: ...
def accepts_producer(callback: Callable[[], int]) -> None: ...
def infer(first: Callable[[T], None], second: Callable[[T], None]) -> T: ...
def three(first: Callable[[T], None], second: Callable[[T], None], third: Callable[[T], None]) -> T: ...
def apply(callback: Callable[[T], U], value: T) -> U: ...
def reverse(value: T, callback: Callable[[T], U]) -> U: ...
def identity(callback: Callable[[T], T], value: T) -> T: ...
def produce(first: Callable[[], T], second: Callable[[], T]) -> T: ...
def nested(callback: Callable[[Callable[[T], None]], None], value: T) -> T: ...
def nested_return(callback: Callable[[Callable[[], T]], None], value: T) -> T: ...
'''


@pytest.fixture(scope="module")
def contracts(tmp_path_factory):
    root = tmp_path_factory.mktemp("callback_contracts")
    (root / "callback_contracts.pyi").write_text(CONTRACTS)
    bridge = importlib.import_module("basilisp_tools.python")
    analyzer = importlib.import_module("basilisp_tools.analyzer")
    cache = bridge.create_cache()
    options = to_lisp({"python-paths": [str(root)], "python-inspection?": False}).assoc(k("cache"), cache)
    try:
        yield bridge, analyzer, options
    finally:
        bridge.stop_cache__BANG__(cache)


def prepare(contracts, function, arguments):
    bridge, _, options = contracts
    metadata = bridge.inspect_path("callback_contracts", to_lisp([function]), options)
    types, forms = [], []
    for argument in arguments:
        if isinstance(argument, tuple):
            name, value = argument
            types.append(to_lisp({"module": "builtins", "path": [name], "literal-values": [value]}))
            forms.append(json.dumps(value))
        else:
            value = bridge.inspect_path("callback_contracts", to_lisp([argument]), options)
            types.append(bridge.callable_type(value))
            forms.append("m/" + argument.replace("_", "-"))
    source = "(ns callback-inference (:import [callback_contracts :as m])) "
    source += "(m/" + function.replace("_", "-") + " " + " ".join(forms) + ")"
    return metadata, to_lisp(types), source


@pytest.mark.parametrize("function,arguments", [
    ("infer", ("first", "literal")),
    ("infer", ("literal", "first")),
    *[("three", order) for order in itertools.permutations(("first", "second", "literal"))],
])
def test_callback_upper_bounds_preserve_literal_intersection(contracts, function, arguments):
    bridge, analyzer, options = contracts
    metadata, types, source = prepare(contracts, function, arguments)
    result = bridge.call_result(metadata, types, to_lisp({}), options)
    assert result == to_lisp({"module": "builtins", "path": ["int"], "literal-values": [5]})
    analysis = analyzer.analyze(source, to_lisp({}).assoc(k("python-options"), options))
    assert len(analysis.val_at(k("findings"))) == 0


@pytest.mark.parametrize("function,arguments,valid", [
    ("apply", ("literal", ("int", 5)), True),
    ("apply", ("literal", ("int", 6)), False),
    ("reverse", (("int", 5), "literal"), True),
    ("reverse", (("int", 6), "literal"), False),
    ("apply", ("integer", ("int", 6)), True),
    ("apply", ("integer", ("str", "bad")), False),
    ("identity", ("integer_identity", ("int", 6)), True),
    ("identity", ("integer_identity", ("str", "bad")), False),
    ("nested_return", ("accepts_producer", ("int", 6)), True),
    ("nested_return", ("accepts_producer", ("str", "bad")), False),
    ("infer", ("two_arguments", "literal"), False),
])
def test_callback_bounds_keep_independent_value_and_arity_errors(contracts, function, arguments, valid):
    bridge, analyzer, options = contracts
    metadata, types, source = prepare(contracts, function, arguments)
    assert (len(bridge.call_signatures(metadata, types, to_lisp({}), options)) > 0) is valid
    analysis = analyzer.analyze(source, to_lisp({}).assoc(k("python-options"), options))
    errors = [finding for finding in analysis.val_at(k("findings"))
              if finding.val_at(k("type")) == k("type-mismatch")]
    assert (len(errors) == 0) is valid


@pytest.mark.parametrize("function,arguments", [
    ("produce", ("integer_factory", "string_factory")),
    ("nested", ("accepts_consumer", ("str", "value"))),
])
def test_callback_returns_and_double_parameter_flip_remain_covariant(contracts, function, arguments):
    bridge, analyzer, options = contracts
    metadata, types, source = prepare(contracts, function, arguments)
    result = bridge.call_result(metadata, types, to_lisp({}), options)
    expected = to_lisp({"union": [{"module": "builtins", "path": ["int"]},
                                  {"module": "builtins", "path": ["str"]}]})
    assert bridge.type_compatible__Q__(result, expected) is True
    assert bridge.type_compatible__Q__(expected, result) is True
    analysis = analyzer.analyze(source, to_lisp({}).assoc(k("python-options"), options))
    assert len(analysis.val_at(k("findings"))) == 0


def test_unproven_callback_intersection_stays_unknown(contracts):
    bridge, analyzer, options = contracts
    metadata, types, source = prepare(contracts, "infer", ("first", "second"))
    result = bridge.call_result(metadata, types, to_lisp({}), options)
    assert result == to_lisp({"any?": True})
    analysis = analyzer.analyze(source, to_lisp({}).assoc(k("python-options"), options))
    assert len(analysis.val_at(k("findings"))) == 0
