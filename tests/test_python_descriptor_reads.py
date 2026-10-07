"""Descriptor reads bind the instance and owner before selecting overloads."""
import importlib
import pytest
import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp

SOURCE = '''from typing import Callable, Generic, TypeVar, overload
T=TypeVar('T')
class Item:
    value: int = 1
class Descriptor(Generic[T]):
    @overload
    def __get__(self, obj: None, owner: type) -> str: ...
    @overload
    def __get__(self, obj: object, owner: type) -> T: ...
    def __get__(self, obj, owner): return 'class' if obj is None else 1
    def __set__(self, obj, value: T): pass
class C:
    value: Descriptor[int] = Descriptor()
class ClassDescriptor:
    @overload
    def __get__(self, obj: None, owner: type) -> type[Item]: ...
    @overload
    def __get__(self, obj: object, owner: type) -> Item: ...
    def __get__(self,obj,owner):return Item if obj is None else Item()
class Owner:
    value=ClassDescriptor()
class CallableDescriptor:
    def __get__(self, obj, owner) -> Callable[[str],int]: return lambda value:len(value)
class CallableOwner:
    run=CallableDescriptor()
class Shadowed:
    value=CallableDescriptor()
    def __init__(self): self.value=1
class Plain: pass
class Indexable(Plain):
    def __class_getitem__(cls, value: int) -> str: return str(value)
class MetaIndex(type):
    def __class_getitem__(cls,value:int)->str:return str(value)
class ViaMeta(metaclass=MetaIndex):pass
class WithIndex:
    def __index__(self)->int:return 0
class DerivedIndex(Plain):
    def __index__(self)->int:return 0
def class_index_factory()->type[Plain]:return Indexable
def instance_index_factory()->Plain:return DerivedIndex()
class FakeIndex:
    def __init__(self):self.__index__=lambda:0
class GetattrIndex:
    def __getattr__(self,name):return lambda:0
class Meta(type): pass
class Dynamic(metaclass=Meta):pass
def factory()->type[C]:return C
def string(value:str)->str:return value
def integer(value:int)->int:return value
'''

@pytest.fixture(scope='module')
def environment(tmp_path_factory):
    root=tmp_path_factory.mktemp('descriptor_reads')
    (root/'descriptors.py').write_text(SOURCE)
    analyzer=importlib.import_module('basilisp_tools.analyzer')
    bridge=importlib.import_module('basilisp_tools.python')
    cache=bridge.create_cache()
    options=to_lisp({'python-options':{'python-paths':[str(root)],'python-inspection?':False,'python-cache':cache}})
    try:yield analyzer,options
    finally:bridge.stop_cache__BANG__(cache)

@pytest.mark.parametrize('body,valid',[
    ('(m/string (.-value m/C))',True),
    ('(m/integer (.-value m/C))',False),
    ('(m/string m/C.value)',True),
    ('(m/integer m/C.value)',False),
    ('(m/integer (.-value (m/C)))',True),
    ('(m/string (.-value (m/C)))',False),
    ('(m/string (.-value (m/factory)))',True),
    ('(m/integer (.-value (m/factory)))',False),
    ('(m/integer (.-value (.-value m/Owner)))',True),
    ('(m/string (.-value (.-value m/Owner)))',False),
    ('(m/integer (.-value (.-value (m/Owner))))',True),
    ('(m/string (.-value (.-value (m/Owner))))',False),
    ('(m/integer (.run (m/CallableOwner) "a"))',True),
    ('(.run (m/CallableOwner) 1)',False),
    ('(m/integer ((.-run (m/CallableOwner)) "a"))',True),
    ('((.-run (m/CallableOwner)) 1)',False),
    ('(m/integer (.-value (m/Shadowed)))',True),
    ('(aget m/Plain python/int)',False),
    ('(m/string (aget m/Indexable 1))',True),
    ('(aget m/Indexable "a")',False),
    ('(aget m/Dynamic 1)',True),
    ('(m/string (aget m/ViaMeta 1))',True),
    ('(aget m/ViaMeta "a")',False),
    ('(aget #py [1] (m/WithIndex))',True),
    ('(aget (m/class-index-factory) 1)',True),
    ('(aget #py [1] (m/instance-index-factory))',True),
])
def test_descriptor_context_and_class_subscription(environment,body,valid):
    analyzer,options=environment
    result=analyzer.analyze('(ns descriptor-reads (:import [descriptors :as m]))\n'+body,options)
    findings=list(result[k('findings')])
    assert not any(str(f[k('message')]).startswith('Analysis failed') for f in findings)
    assert ([] if valid else [k('type-mismatch')]) == [f[k('type')] for f in findings]

def test_native_descriptor_bindings():
    ns={};exec(SOURCE,ns)
    assert ns['C'].value=='class'
    assert ns['C']().value==1
    assert ns['Owner'].value is ns['Item']
    assert isinstance(ns['Owner']().value,ns['Item'])
    assert ns['CallableOwner']().run('a')==1
    assert ns['Shadowed']().value==1
    assert ns['Indexable'][1]=='1'
    with pytest.raises(TypeError):ns['Plain'][int]

def test_native_index_slots():
    ns={};exec(SOURCE,ns)
    assert ns['ViaMeta'][1]=='1'
    assert [1][ns['WithIndex']()]==1
    assert ns['class_index_factory']()[1]=='1'
    assert [1][ns['instance_index_factory']()]==1
    with pytest.raises(TypeError):[1][ns['Plain']()]
    with pytest.raises(TypeError):[1][ns['FakeIndex']()]
