"""Callback protocols preserve call signatures, type packs, and extra members."""

import importlib

import pytest

import basilisp_tools  # noqa: F401
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp

SOURCE = '''from typing import Callable, Protocol, TypeVar, TypeVarTuple, Unpack, overload
Ts=TypeVarTuple("Ts")
T=TypeVar("T")
class Callback(Protocol[Unpack[Ts]]):
 def __call__(self, head:int, /, *values:Unpack[Ts])->str:...
class Stateful(Protocol):
 state:int
 def __call__(self, value:int, /)->str:...
class Receiver:
 def __call__(self, head:int, text:str, /)->str:...
class WrongReceiver:
 def __call__(self, head:str, text:str, /)->str:...
class WrongState:
 state:str
 def __call__(self, value:int, /)->str:...
class Overloaded(Protocol):
 @overload
 def __call__(self,value:int,/)->str:...
 @overload
 def __call__(self,value:str,/)->int:...
def good(head:int,text:str,/)->str:...
def bad_head(head:str,text:str,/)->str:...
def bad_return(head:int,text:str,/)->int:...
def int_to_str(head:int,/)->str:...
@overload
def both(value:int,/)->str:...
@overload
def both(value:str,/)->int:...
def protocol(callback:Callback[str])->None:...
def capture(callback:Callback[Unpack[Ts]])->tuple[Unpack[Ts]]:...
def callable(callback:Callable[[int,str],str])->None:...
def callable_capture(callback:Callable[[Unpack[Ts]],str])->tuple[Unpack[Ts]]:...
def state(callback:Stateful)->None:...
def overloaded_callback(callback:Overloaded)->None:...

from typing import Generic
Constrained=TypeVar("Constrained",int,str)
Local=TypeVar("Local",int,str)
class Adder(Generic[Constrained]):
 def add(self,a:Constrained,b:Constrained)->Constrained:...
 def method(self,a:Local,b:Local)->Local:...
unbound:Adder
bound:Adder[int]

class First: ...
class SubFirst(First): ...
class Second: ...
class Unrelated: ...
Restricted=TypeVar("Restricted",First,Second)
Free=TypeVar("Free")
class RestrictedBox(Generic[Restricted]): ...
def constrained(value:Restricted)->Restricted:...
def first_consumer(value:First)->None:...
def sub_consumer(value:SubFirst)->None:...
def common(first:Callable[[Free],None],second:Callable[[Free],None])->Free:...

from typing import Union
class UnionPack(Generic[Unpack[Ts]]):
 def value(self)->Union[Unpack[Ts]]:...

from typing import TypedDict
class Record(TypedDict):
 name:str
 count:int
class PartialRecord(TypedDict,total=False):
 name:str
 count:int
record:Record
partial:PartialRecord
unknown:dict[str,object]
class NestedRecord(TypedDict):
 values:list[list[object]]
'''

@pytest.fixture(scope="module")
def contracts(tmp_path_factory):
    root = tmp_path_factory.mktemp("callback_protocols")
    (root / "contracts.pyi").write_text(SOURCE)
    bridge = importlib.import_module("basilisp_tools.python")
    analyzer = importlib.import_module("basilisp_tools.analyzer")
    cache = bridge.create_cache()
    opts = to_lisp({"python-paths": [str(root)], "python-inspection?": False}).assoc(k("python-cache"), cache)
    try:
        yield bridge, analyzer, opts
    finally:
        bridge.stop_cache__BANG__(cache)

@pytest.mark.parametrize("body,valid", [
    ("(m/protocol m/good)", True),
    ("(m/protocol m/bad-head)", False),
    ("(m/protocol m/bad-return)", False),
    ("(m/capture m/good)", True),
    ("(m/callable (m/Receiver))", True),
    ("(m/callable (m/WrongReceiver))", False),
    ("(m/callable-capture (m/Receiver))", True),
    ("(m/state (m/WrongState))", False),
    ("(m/overloaded-callback m/both)", True),
    ("(m/overloaded-callback m/int-to-str)", False),
])
def test_callback_protocols_and_instances_keep_contracts(contracts, body, valid):
    bridge, analyzer, opts = contracts
    result = analyzer.analyze('(ns callback-protocols (:import [contracts :as m])) ' + body,
                              to_lisp({}).assoc(k("python-options"), opts))
    findings = list(result.val_at(k("findings")))
    if valid:
        assert findings == []
    else:
        assert any(f.val_at(k("type")) in {k("type-mismatch"), k("invalid-arity")} for f in findings)

@pytest.mark.parametrize("function,argument,expected", [
    ("capture", "good", ["str"]),
    ("callable_capture", "Receiver", ["int", "str"]),
])
def test_protocol_and_instance_packs_infer_return_elements(contracts, function, argument, expected):
    bridge, _, opts = contracts
    metadata = bridge.inspect_path("contracts", to_lisp([argument]), opts)
    actual = (bridge.return_type(metadata) if argument == "Receiver" else bridge.callable_type(metadata))
    result = bridge.call_result(bridge.inspect_path("contracts", to_lisp([function]), opts),
                                to_lisp([actual]), to_lisp({}), opts)
    assert result == to_lisp({"module": "builtins", "path": ["tuple"],
                              "arguments": [{"module": "builtins", "path": [item]} for item in expected]})

def test_uninspected_function_attributes_remain_unknown(contracts):
    bridge, _, opts = contracts
    actual = bridge.callable_type(bridge.inspect_path("contracts", to_lisp(["int_to_str"]), opts))
    expected = to_lisp({"module": "contracts", "path": ["Stateful"]})
    assert bridge.type_compatible__Q__(actual, expected, opts) is None


@pytest.mark.parametrize("body,valid", [
    ('(.add m/unbound 1 "x")', True),
    ('(.add m/bound 1 2)', True),
    ('(.add m/bound 1 "x")', False),
    ('(.method m/unbound 1 2)', True),
    ('(.method m/unbound 1 "x")', False),
])
def test_class_typevars_are_unknown_until_specialized_but_method_typevars_remain_generic(contracts, body, valid):
    _, analyzer, opts = contracts
    result = analyzer.analyze('(ns receiver-generics (:import [contracts :as m])) ' + body,
                              to_lisp({}).assoc(k("python-options"), opts))
    findings = list(result.val_at(k("findings")))
    assert bool(not findings) is valid


def test_constraint_promotion_uses_inspected_subclass_proof(contracts):
    bridge, _, opts = contracts
    actual = to_lisp({"module": "contracts", "path": ["SubFirst"]})
    expected = to_lisp({"module": "contracts", "path": ["First"]})
    function = bridge.inspect_path("contracts", to_lisp(["constrained"]), opts)
    assert bridge.call_result(function, to_lisp([actual]), to_lisp({}), opts) == expected
    bad = to_lisp({"module": "contracts", "path": ["Unrelated"]})
    assert len(bridge.call_signatures(function, to_lisp([bad]), to_lisp({}), opts)) == 0
    box = bridge.inspect_path("contracts", to_lisp(["RestrictedBox"]), opts)
    receiver = to_lisp({"module": "contracts", "path": ["RestrictedBox"], "arguments": [actual]})
    specialized = bridge.specialize(box, receiver, opts)
    assert bridge.return_type(specialized).val_at(k("arguments"))[0] == expected

@pytest.mark.parametrize("order", [("first_consumer", "sub_consumer"), ("sub_consumer", "first_consumer")])
def test_callback_upper_bounds_use_subclass_proof_in_either_order(contracts, order):
    bridge, _, opts = contracts
    arguments = [bridge.callable_type(bridge.inspect_path("contracts", to_lisp([name]), opts)) for name in order]
    metadata = bridge.inspect_path("contracts", to_lisp(["common"]), opts)
    assert bridge.call_result(metadata, to_lisp(arguments), to_lisp({}), opts) == to_lisp({"module": "contracts", "path": ["SubFirst"]})


@pytest.mark.parametrize("body,valid", [
    ('(m/Record #py {"name" "x" "count" 1})', True),
    ('(m/Record #py {"name" "x"} ** :count 1)', True),
    ('(m/Record #py {"name" 1 "count" 1} ** :name "x")', True),
    ('(m/Record #py {"name" "x" "count" 1} ** :count "bad")', False),
    ('(m/Record #py {"name" "x"})', False),
    ('(m/Record #py {})', False),
    ('(m/Record #py {"name" "x" "count" "bad"})', False),
    ('(m/Record m/record)', True),
    ('(m/Record m/record ** :count "bad")', False),
    ('(m/Record m/partial)', False),
    ('(m/Record m/unknown ** :count "bad")', False),
    ('(m/Record m/unknown)', True),
    ('(m/Record #py {} #py {})', False),
    ('(m/NestedRecord #py {"values" #py [#py [1]]})', True),
    ('(let [existing #py [1]] (m/NestedRecord #py {"values" #py [existing]}))', False),
])
def test_mapping_constructors_keep_required_fields_overrides_and_freshness(contracts, body, valid):
    _, analyzer, opts = contracts
    result = analyzer.analyze('(ns mapping-constructors (:import [contracts :as m])) ' + body,
                              to_lisp({}).assoc(k("python-options"), opts))
    findings = list(result.val_at(k("findings")))
    if valid:
        assert findings == []
    else:
        assert any(f.val_at(k("type")) in {k("type-mismatch"), k("invalid-arity")} for f in findings)


def test_specialized_union_pack_expands_each_alternative(contracts):
    bridge, _, opts = contracts
    receiver = to_lisp({"module": "contracts", "path": ["UnionPack"], "arguments": [
        {"module": "builtins", "path": ["int"]},
        {"module": "builtins", "path": ["str"]}]})
    method = bridge.inspect_member(receiver, "value", opts)
    assert bridge.call_result(method, to_lisp([]), to_lisp({}), opts) == to_lisp({"union": [
        {"module": "builtins", "path": ["int"]},
        {"module": "builtins", "path": ["str"]}]})


@pytest.mark.parametrize("name", ["Literal", "ClassVar", "Required", "Protocol", "Generic", "Any"])
def test_typing_forms_are_values_not_class_objects(contracts, name):
    bridge, _, opts = contracts
    form = bridge.inspect_path("typing_extensions" if name == "Required" else "typing", to_lisp([name]), opts)
    actual = bridge.return_type(form)
    assert actual.val_at(k("typing-form?")) is True
    klass = to_lisp({"module": "builtins", "path": ["type"], "arguments": [{"any?": True}]})
    assert bridge.type_compatible__Q__(actual, klass, opts) is False
    assert bridge.type_compatible__Q__(actual, to_lisp({"any?": True}), opts) is None
    assert bridge.type_compatible__Q__(actual, to_lisp({"module": "builtins", "path": ["object"]}), opts) is True
