"""Metaclass data properties override class storage in class context only."""
import importlib
import importlib.util
from pathlib import Path
import pytest
import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp

SOURCE='''from typing import Final
from typing_extensions import Self
class Meta(type):
 @property
 def value(cls)->int:return 3
 @value.setter
 def value(cls,value:int):type.__setattr__(cls,'stored',value)
 @property
 def locked(cls)->int:return 4
 @property
 def dependent(cls)->Self:return cls
class ChildMeta(Meta):pass
class C(metaclass=ChildMeta):
 @property
 def value(self)->str:return 'instance'
 @property
 def locked(self)->str:return 'instance locked'
 replacement=0
class D(metaclass=Meta):
 value:Final[str]='declared instance'
class DynamicMeta(Meta):
 def __getattribute__(cls,name):return 'dynamic' if name=='value' else type.__getattribute__(cls,name)
 def __setattr__(cls,name,value):type.__setattr__(cls,'dynamic_value',value)
class Dynamic(metaclass=DynamicMeta):pass
def integer(value:int)->None:assert isinstance(value,int)
def text(value:str)->None:assert isinstance(value,str)
'''

@pytest.fixture(scope='module')
def environment(tmp_path_factory):
 root=tmp_path_factory.mktemp('meta_property_precedence');(root/'meta_properties.py').write_text(SOURCE)
 analyzer=importlib.import_module('basilisp_tools.analyzer');bridge=importlib.import_module('basilisp_tools.python')
 cache=bridge.create_cache();opts=to_lisp({'python-paths':[str(root)],'python-inspection?':False,'python-cache':cache})
 try:yield root,analyzer,bridge,opts
 finally:bridge.stop_cache__BANG__(cache)

@pytest.mark.parametrize('body,valid',[
 ('(m/integer (.-value m/C))',True),
 ('(m/integer m/C.value)',True),
 ('(m/text (.-value m/C))',False),
 ('(m/text (.-value (m/C)))',True),
 ('(m/integer (.-value (m/C)))',False),
 ('(set! (.-value m/C) 7)',True),
 ('(set! (.-value m/C) "bad")',False),
 ('(set! (.-value (m/C)) "bad")',False),
 ('(set! (.-locked m/C) 7)',False),
 ('(m/integer (.-locked m/C))',True),
 ('(m/text (.-locked (m/C)))',True),
 ('(set! (.-value m/D) 7)',True),
 ('(set! (.-value (m/D)) "bad")',False),
 ('(m/text (.-value m/Dynamic))',True),
 ('(set! (.-locked m/Dynamic) "allowed")',True),
 ('(set! (.-replacement m/C) "ordinary class replacement")',True),
])
def test_class_and_instance_precedence(environment,body,valid):
 _,analyzer,_,opts=environment
 result=analyzer.analyze('(ns meta-property-precedence (:import [meta_properties :as m])) '+body,to_lisp({'python-options':opts}))
 assert [finding[k('type')] for finding in result[k('findings')]]==([] if valid else [k('type-mismatch')])


def test_dependent_receiver_stays_unknown(environment):
 _,_,bridge,opts=environment
 member=bridge.inspect_path('meta_properties',to_lisp(['C','dependent']),opts)
 assert member[k('class-read-type')]==to_lisp({'type-any?':True})


def test_native_metaclass_property_precedence():
 namespace={};exec(SOURCE,namespace)
 C=namespace['C'];D=namespace['D'];Dynamic=namespace['Dynamic']
 assert C.value==3 and C().value=='instance'
 C.value=7;assert C.stored==7
 assert C.locked==4 and C().locked=='instance locked'
 with pytest.raises(AttributeError):C.locked=7
 with pytest.raises(AttributeError):C().value='bad'
 D.value=7;assert D.stored==7
 assert Dynamic.value=='dynamic'
 Dynamic.locked='allowed';assert Dynamic.dynamic_value=='allowed'
 C.replacement='ordinary class replacement'


def test_runtime_property_metadata_respects_metaclass_precedence():
 spec=importlib.util.spec_from_file_location('meta_property_worker',Path(__file__).resolve().parents[1]/'src/basilisp_tools/_inspect.py')
 worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
 namespace={};exec(SOURCE,namespace)
 cls=worker.runtime_member('C',namespace['C'])
 value=cls['members']['value']
 assert value['type-path']==['str']
 assert value['class-read-type']['type-path']==['int']
 assert value['class-assignment-type']['type-path']==['int']
 assert value['instance-read-only?'] is True
 assert 'class-read-only?' not in value
 assert cls['members']['locked']['class-read-only?'] is True
 dynamic=worker.runtime_member('Dynamic',namespace['Dynamic'])
 assert dynamic['members']['value']['class-read-type']=={'type-any?':True}
 assert dynamic['members']['locked']['class-assignment-type']=={'type-any?':True}
 assert not dynamic['members']['locked'].get('class-read-only?')
