import importlib
import pytest
import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp

SOURCE = """from typing import Protocol,Final,Any
class Method(Protocol):
 def meth(self)->int:...
class Missing: pass
class SubclassWithMethod(Missing):
 def meth(self)->int:...
def factory()->Missing:...
class Good:
 def meth(self)->int:...
class Dynamic:
 def __getattr__(self,name:str)->Any:...
class Initialized:
 def __init__(self): self.meth=lambda:1
class Mutable(Protocol):
 x:int
class ObjectField(Protocol):
 x:object
class ReadOnly(Protocol):
 @property
 def x(self)->object:...
class FinalField:
 x:Final[int]=1
class IntField:
 x:int
class ObjectValue:
 x:object
class Getter:
 @property
 def x(self)->int:...
class WiderSetter:
 @property
 def x(self)->int:...
 @x.setter
 def x(self,value:object)->None:...
def method(value:Method)->None:...
def mutable(value:Mutable)->None:...
def object_field(value:ObjectField)->None:...
def readonly(value:ReadOnly)->None:...
"""


@pytest.fixture(scope="module")
def contracts(tmp_path_factory):
    root = tmp_path_factory.mktemp("protocol_fields")
    (root / "fields.pyi").write_text(SOURCE)
    p = importlib.import_module("basilisp_tools.python")
    a = importlib.import_module("basilisp_tools.analyzer")
    cache = p.create_cache()
    opts = to_lisp({"python-paths": [str(root)], "python-inspection?": False}).assoc(
        k("python-cache"), cache
    )
    try:
        yield a, opts
    finally:
        p.stop_cache__BANG__(cache)


@pytest.mark.parametrize(
    "body,valid",
    [
        ("(f/method (f/Good))", True),
        ("(f/method (f/Missing))", True),
        ("(f/method (f/factory))", True),
        ("(f/method (f/Dynamic))", True),
        ("(f/method (f/Initialized))", True),
        ("(f/mutable (f/IntField))", True),
        ("(f/mutable (f/FinalField))", False),
        ("(f/mutable (f/Getter))", False),
        ("(f/object-field (f/IntField))", False),
        ("(f/object-field (f/ObjectValue))", True),
        ("(f/readonly (f/FinalField))", True),
        ("(f/readonly (f/IntField))", True),
        ("(f/object-field (f/WiderSetter))", True),
    ],
)
def test_protocols_preserve_missing_members_and_write_contracts(contracts, body, valid):
    a, opts = contracts
    r = a.analyze(
        "(ns protocol-fields (:import [fields :as f])) " + body,
        to_lisp({}).assoc(k("python-options"), opts),
    )
    findings = list(r.val_at(k("findings")))
    assert (len(findings) == 0) is valid, findings


def test_absent_instance_members_remain_unknown_without_exact_value_provenance(
    contracts,
):
    _, opts = contracts
    p = importlib.import_module("basilisp_tools.python")
    assert (
        p.type_compatible__Q__(
            to_lisp({"module": "fields", "path": ["Missing"]}),
            to_lisp({"module": "fields", "path": ["Method"]}),
            opts,
        )
        is None
    )
