"""Generic protocol member contracts contribute argument constraints."""

import importlib
import pytest
import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp

SOURCE = """from typing import Protocol,TypeVar
T=TypeVar("T",covariant=True)
S=TypeVar("S",covariant=True)
U=TypeVar("U")
V=TypeVar("V",bound=int)
class P(Protocol[T,S]):
    def x(self)->T:...
    def y(self)->S:...
class C:
    def x(self)->int:return 1
    def y(self)->int:return 2
class Strings:
    def x(self)->str:return "x"
    def y(self)->str:return "y"
class Dynamic:
    def __getattribute__(self,name):return lambda:42
    def x(self)->str:return "x"
    def y(self)->str:return "y"
def merge(value:U,provider:P[U,U])->U:return provider.x()
def reverse(provider:P[U,U],value:U)->U:return provider.x()
def bounded(value:V,provider:P[V,V])->V:return provider.x()
def integers(provider:P[int,int])->int:return provider.x()
"""


@pytest.fixture(scope="module")
def contracts(tmp_path_factory):
    root = tmp_path_factory.mktemp("protocol_constraints")
    (root / "protocols.py").write_text(SOURCE)
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
        ('(p/merge "a" (p/C))', True),
        ('(p/reverse (p/C) "a")', True),
        ("(p/merge 1 (p/Strings))", True),
        ("(p/bounded 1 (p/C))", True),
        ("(p/bounded 1 (p/Strings))", False),
        ("(p/integers (p/Strings))", False),
        ("(p/integers (p/Dynamic))", True),
    ],
)
def test_protocol_inference_preserves_known_negative_and_dynamic_contracts(
    contracts, body, valid
):
    analyzer, opts = contracts
    result = analyzer.analyze(
        "(ns protocol-inference (:import [protocols :as p])) " + body,
        to_lisp({}).assoc(k("python-options"), opts),
    )
    findings = list(result.val_at(k("findings")))
    if valid:
        assert findings == []
    else:
        assert any(
            f.val_at(k("type")) == k("type-mismatch") for f in findings
        ), findings


def test_native_protocol_member_calls():
    ns = {}
    exec(SOURCE, ns)
    assert ns["merge"]("a", ns["C"]()) == 1
    assert ns["integers"](ns["Dynamic"]()) == 42
