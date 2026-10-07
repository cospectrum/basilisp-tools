"""An explicit Any class specialization must not reopen method TypeVars."""

import importlib
import pytest
import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp

SOURCE = """from typing import Any,Generic
from typing_extensions import TypeVar
T=TypeVar("T",int,str)
D=TypeVar("D",default=int)
class Adder(Generic[T]):
    def add(self,a:T,b:T)->T:...
class Default(Generic[D]):
    def pair(self,a:D,b:D)->D:...
any_adder:Adder[Any]
int_adder:Adder[int]
str_adder:Adder[str]
any_default:Default[Any]
bare_default:Default
generic_adder=Adder()
def same(a:T,b:T)->T:...
"""


@pytest.fixture(scope="module")
def contracts(tmp_path_factory):
    root = tmp_path_factory.mktemp("any_receiver")
    (root / "receiver.pyi").write_text(SOURCE)
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


@pytest.mark.parametrize(
    "body,valid",
    [
        ('(.add r/any-adder 1 "b")', True),
        ('(.add r/generic-adder 1 "b")', True),
        ('(.pair r/any-default 1 "b")', True),
        ('(.pair r/bare-default 1 "b")', False),
        ('(.add r/int-adder 1 "b")', False),
        ('(.add r/str-adder 1 "b")', False),
        ("(.add r/int-adder 1 2)", True),
        ('(.add r/str-adder "a" "b")', True),
        ('(r/same 1 "b")', False),
        ("(r/same 1 2)", True),
    ],
)
def test_explicit_receiver_any_does_not_weaken_call_inference(contracts, body, valid):
    _, analyzer, opts = contracts
    result = analyzer.analyze(
        "(ns receiver-any (:import [receiver :as r])) " + body,
        to_lisp({}).assoc(k("python-options"), opts),
    )
    findings = list(result.val_at(k("findings")))
    assert (not findings) is valid, findings


def test_receiver_any_is_retained_in_method_metadata(contracts):
    bridge, _, opts = contracts
    owner = to_lisp(
        {"module": "receiver", "path": ["Adder"], "arguments": [{"any?": True}]}
    )
    method = bridge.inspect_member(owner, "add", opts)
    assert bridge.return_type(method) == to_lisp({"any?": True})
    assert all(
        bridge.return_type(p) == to_lisp({"any?": True})
        for p in method.val_at(k("parameters"))
    )


def test_unknown_receiver_pack_elements_are_preserved():
    bridge = importlib.import_module("basilisp_tools.python")
    metadata = to_lisp(
        {
            "type-module": "fixture",
            "type-path": ["Packed"],
            "type-parameters": [
                {"typevar": "T"},
                {"typevar": "Ts", "variadic?": True},
                {"typevar": "U"},
            ],
            "type-arguments": [
                {"typevar": "T"},
                {"typevar": "Ts", "variadic?": True, "unpack?": True},
                {"typevar": "U"},
            ],
        }
    )
    receiver = to_lisp(
        {
            "module": "fixture",
            "path": ["Packed"],
            "arguments": [
                {"any?": True},
                {"module": "builtins", "path": ["int"]},
                {"any?": True},
            ],
        }
    )
    result = bridge.return_type(bridge.specialize(metadata, receiver, to_lisp({})))
    assert result == receiver
