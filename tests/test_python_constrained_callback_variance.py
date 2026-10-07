"""Constrained callback bounds honor declared variance and argument order."""

import importlib
import pytest
import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp

SOURCE = """from typing import Callable, Concatenate, Generic, ParamSpec, TypeVar
P=ParamSpec("P",contravariant=True)
class Callback(Generic[P]):
    def __init__(self, callback:Callable[P,None])->None:
        self.callback=callback
class Base: pass
class Middle(Base): pass
class Second(Base): pass
class Other: pass
C=TypeVar("C",Middle,Second,Other)
Q=ParamSpec("Q")
def accepts_base(first:Base,/,value:str)->None: pass
def accepts_middle(first:Middle,/,value:str)->None: pass
def accepts_other(first:Other,/,value:str)->None: pass
def constrained(callback:Callback[Concatenate[C,Q]],witness:C)->tuple[C,Callable[Q,None]]:
    return witness,lambda *args,**kwargs:callback.callback(witness,*args,**kwargs)
def reversed_args(witness:C,callback:Callback[Concatenate[C,Q]])->tuple[C,Callable[Q,None]]:
    return constrained(callback,witness)
V=TypeVar("V",object,bool)
def impossible(callback:Callable[[V],V])->V: ...
def integer_identity(value:int)->int:return value
"""


@pytest.fixture(scope="module")
def contracts(tmp_path_factory):
    root = tmp_path_factory.mktemp("constraint_variance")
    (root / "variance_fixture.py").write_text(SOURCE)
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
        "(ns variance-audit (:import [variance_fixture :as v])) " + expression,
        to_lisp({}).assoc(k("python-options"), opts),
    )


@pytest.mark.parametrize("name", ["Middle", "Second"])
@pytest.mark.parametrize("reverse", [False, True])
def test_common_upper_bound_can_choose_either_declared_constraint(
    contracts, name, reverse
):
    callback = "(v/Callback v/accepts_base)"
    body = (
        f"(v/reversed_args (v/{name}) {callback})"
        if reverse
        else f"(v/constrained {callback} (v/{name}))"
    )
    result = analyze(contracts, body)
    assert list(result.val_at(k("findings"))) == []
    inferred = list(result.val_at(k("python-expressions")))[-1].val_at(k("python-type"))
    assert list(inferred.val_at(k("arguments")))[0] == to_lisp(
        {"module": "variance_fixture", "path": [name]}
    )


@pytest.mark.parametrize(
    "callback,witness",
    [
        ("accepts_middle", "Second"),
        ("accepts_other", "Middle"),
        ("accepts_base", "Other"),
    ],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_incompatible_constraints_are_not_erased(contracts, callback, witness, reverse):
    cb = f"(v/Callback v/{callback})"
    body = (
        f"(v/reversed_args (v/{witness}) {cb})"
        if reverse
        else f"(v/constrained {cb} (v/{witness}))"
    )
    findings = list(analyze(contracts, body).val_at(k("findings")))
    assert any(f.val_at(k("type")) == k("type-mismatch") for f in findings), findings


def test_callable_input_and_output_need_the_same_allowed_type(contracts):
    findings = list(
        analyze(contracts, "(v/impossible v/integer_identity)").val_at(k("findings"))
    )
    assert any(f.val_at(k("type")) == k("type-mismatch") for f in findings), findings


def test_native_valid_callback_binding():
    values = {}
    exec(SOURCE, values)
    for name in ("Middle", "Second"):
        witness = values[name]()
        cb = values["Callback"](values["accepts_base"])
        result = values["constrained"](cb, witness)
        assert result[0] is witness
        assert result[1]("value") is None
