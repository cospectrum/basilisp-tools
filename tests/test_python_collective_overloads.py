"""Each union input must be covered by the callback overload set."""

import importlib
import pytest
import basilisp_tools
from basilisp.lang.runtime import to_lisp


def scalar(name):
    return {"module": "builtins", "path": [name]}


def union(*types):
    return {"union": list(types)}


def callback(arguments, result):
    return {
        "module": "collections.abc",
        "path": ["Callable"],
        "arguments": [{"parameter-list?": True, "arguments": arguments}, result],
    }


def overloaded(*branches):
    return {**branches[0], "callable-overloads": list(branches)}


INT, STR, BYTES = map(scalar, ("int", "str", "bytes"))
MIXED = union(INT, STR)
IDENTITY = overloaded(callback([INT], INT), callback([STR], STR))


@pytest.mark.parametrize(
    "actual,expected,compatible",
    [
        (IDENTITY, callback([MIXED], MIXED), True),
        (IDENTITY, callback([union(INT, STR, BYTES)], MIXED), False),
        (IDENTITY, callback([MIXED], INT), False),
        (IDENTITY, callback([MIXED, INT], MIXED), False),
        (
            overloaded(callback([INT, INT], INT), callback([STR, STR], STR)),
            callback([MIXED, MIXED], MIXED),
            False,
        ),
        (
            overloaded(
                callback([INT, INT], INT),
                callback([INT, STR], STR),
                callback([STR, INT], STR),
                callback([STR, STR], STR),
            ),
            callback([MIXED, MIXED], MIXED),
            True,
        ),
        (
            overloaded(callback([INT], INT), callback([STR], INT)),
            callback([MIXED], INT),
            True,
        ),
    ],
)
def test_overloads_collectively_cover_inputs_without_losing_returns(
    actual, expected, compatible
):
    bridge = importlib.import_module("basilisp_tools.python")
    assert (
        bridge.type_compatible__Q__(to_lisp(actual), to_lisp(expected), to_lisp({}))
        is compatible
    )


def test_expansion_budget_is_unknown():
    bridge = importlib.import_module("basilisp_tools.python")
    actual = overloaded(callback([INT] * 6, INT), callback([STR] * 6, STR))
    expected = callback([MIXED] * 6, MIXED)
    assert (
        bridge.type_compatible__Q__(to_lisp(actual), to_lisp(expected), to_lisp({}))
        is None
    )


def test_independent_arity_error_is_not_hidden_by_expansion_budget():
    bridge = importlib.import_module("basilisp_tools.python")
    actual = overloaded(callback([INT] * 5, INT), callback([STR] * 5, STR))
    expected = callback([MIXED] * 6, MIXED)
    assert (
        bridge.type_compatible__Q__(to_lisp(actual), to_lisp(expected), to_lisp({}))
        is False
    )


SOURCE = """from typing import Callable,TypeVar,ParamSpec,overload
T=TypeVar("T")
def accepts(fn:Callable[[int|str],int|str])->None:pass
def bytes_input(fn:Callable[[int|str|bytes],int|str])->None:pass
def integer_result(fn:Callable[[int|str],int])->None:pass
@overload
def identity(value:int)->int:...
@overload
def identity(value:str)->str:...
def identity(value:int|str)->int|str:return value
def twice(callback:Callable[[T],T],left:T,right:T)->tuple[T,T]:
    return callback(left),callback(right)
x:int|str=1
y:int|str="value"
P=ParamSpec("P")
R=TypeVar("R")
def forward(fn:Callable[P,R], *args:P.args, **kwargs:P.kwargs)->R:
    return fn(*args,**kwargs)
@overload
def optional()->None:...
@overload
def optional(value:int)->None:...
def optional(value:int=0)->None:pass
"""


@pytest.fixture(scope="module")
def contracts(tmp_path_factory):
    from basilisp.lang.keyword import keyword as k

    root = tmp_path_factory.mktemp("collective_overloads")
    (root / "collective.py").write_text(SOURCE)
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


@pytest.mark.parametrize(
    "body,valid",
    [
        ("(c/accepts c/identity)", True),
        ("(c/bytes_input c/identity)", False),
        ("(c/integer_result c/identity)", False),
        ("(c/twice c/identity c/x c/y)", True),
        ('(c/twice c/identity 1 #b"value")', False),
    ],
)
def test_integrated_overloaded_callbacks(contracts, body, valid):
    from basilisp.lang.keyword import keyword as k

    analyzer, opts = contracts
    result = analyzer.analyze(
        "(ns collective-audit (:import [collective :as c])) " + body,
        to_lisp({}).assoc(k("python-options"), opts),
    )
    findings = list(result.val_at(k("findings")))
    if valid:
        assert findings == []
    else:
        assert any(
            f.val_at(k("type")) == k("type-mismatch") for f in findings
        ), findings


def test_native_union_callback_inputs():
    values = {}
    exec(SOURCE, values)
    assert values["twice"](values["identity"], 1, "value") == (1, "value")


@pytest.mark.parametrize(
    "body,valid",
    [
        ("(c/forward c/optional)", True),
        ("(c/forward c/optional 1)", True),
        ("(c/forward c/optional ** :value 1)", True),
        ("(c/forward c/optional 1 2)", False),
        ("(c/forward c/optional ** :other 1)", False),
        ("(c/forward c/identity 1)", True),
        ('(c/forward c/identity "x")', True),
        ("(c/forward c/identity 1.0)", False),
        ("(c/forward c/identity ** :other 1)", False),
    ],
)
def test_collective_overloads_preserve_forwarded_argument_checks(
    contracts, body, valid
):
    from basilisp.lang.keyword import keyword as k

    analyzer, opts = contracts
    result = analyzer.analyze(
        "(ns collective-forwarding (:import [collective :as c])) " + body,
        to_lisp({}).assoc(k("python-options"), opts),
    )
    findings = list(result.val_at(k("findings")))
    if valid:
        assert findings == []
    else:
        assert any(
            f.val_at(k("type")) == k("type-mismatch") for f in findings
        ), findings
