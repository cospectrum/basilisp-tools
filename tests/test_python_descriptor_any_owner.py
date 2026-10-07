"""Explicit Any parameters do not obscure known overload discriminators."""
import importlib
import pytest
import sys
import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp

SOURCE = '''from typing import Any, Generic, TypeVar, overload
from dataclasses import dataclass
T=TypeVar('T')
class Desc:
 @overload
 def __get__(self, obj:None, owner:Any)->"Desc":...
 @overload
 def __get__(self, obj:object, owner:Any)->int:...
 def __get__(self,obj,owner):return self if obj is None else 1
 def __set__(self,obj,value:int):pass
@dataclass
class Owner:
 value:Desc=Desc()
class HashableList(list):
 __hash__=object.__hash__
class GenericDesc(Generic[T]):
 @overload
 def __get__(self,obj:None,owner:Any)->list[T]:...
 @overload
 def __get__(self,obj:object,owner:Any)->T:...
 def __get__(self,obj,owner):return HashableList(['class']) if obj is None else 'instance'
@dataclass
class GenericOwner:
 value:GenericDesc[str]=GenericDesc()
@overload
def choose(value:None,owner:Any,count:int)->str:...
@overload
def choose(value:object,owner:Any,count:int)->int:...
def choose(value,owner,count):return 'none' if value is None else 1
@overload
def fallback(value:list[str])->str:...
@overload
def fallback(value:Any)->int:...
def fallback(value):return 1
def descriptor(value:Desc)->None:assert isinstance(value,Desc)
def integer(value:int)->None:assert isinstance(value,int)
def text(value:str)->None:assert isinstance(value,str)
def strings(value:list[str])->None:assert isinstance(value,list)
'''

@pytest.fixture(scope='module')
def environment(tmp_path_factory):
 root=tmp_path_factory.mktemp('descriptor_any_owner');(root/'any_owner.py').write_text(SOURCE)
 analyzer=importlib.import_module('basilisp_tools.analyzer');bridge=importlib.import_module('basilisp_tools.python')
 cache=bridge.create_cache();opts=to_lisp({'python-paths':[str(root)],'python-inspection?':False,'python-cache':cache})
 try:yield analyzer,bridge,opts
 finally:bridge.stop_cache__BANG__(cache)

@pytest.mark.parametrize('body,valid',[
 ('(m/descriptor (.-value m/Owner))',True),
 ('(m/descriptor m/Owner.value)',True),
 ('(m/integer (.-value m/Owner))',False),
 ('(m/integer (.-value (m/Owner 3)))',True),
 ('(m/text (.-value (m/Owner 3)))',False),
 ('(m/strings (.-value m/GenericOwner))',True),
 ('(m/integer (.-value m/GenericOwner))',False),
 ('(m/text (m/choose nil m/Owner 1))',True),
 ('(m/integer (m/choose nil m/Owner 1))',False),
 ('(m/integer (m/choose 1 m/Owner 1))',True),
 ('(m/text (m/choose 1 m/Owner 1))',False),
 ('(m/choose nil m/Owner "bad")',False),
])
def test_known_discriminator_and_independent_errors(environment,body,valid):
 analyzer,bridge,opts=environment
 result=analyzer.analyze('(ns descriptor-any-owner (:import [any_owner :as m])) '+body,to_lisp({'python-options':opts}))
 findings=list(result[k('findings')])
 assert [finding[k('type')] for finding in findings]==([] if valid else [k('type-mismatch')])

@pytest.mark.parametrize('actual',[None,{'any?':True}])
def test_unknown_discriminator_does_not_prove_a_branch(environment,actual):
 _,bridge,opts=environment
 member=bridge.inspect_path('any_owner',to_lisp(['choose']),opts)
 owner={'module':'builtins','path':['type'],'arguments':[{'module':'any_owner','path':['Owner']}]}
 arguments=to_lisp([actual,owner,{'module':'builtins','path':['int']}])
 signatures=bridge.call_signatures(member,arguments,to_lisp({}),opts)
 assert len(signatures)==2
 assert not any(signature[k('type-match?')] for signature in signatures)
 assert bridge.call_result(member,arguments,to_lisp({}),opts) is None


def test_global_any_compatibility_remains_unknown(environment):
 _,bridge,_=environment
 integer=to_lisp({'module':'builtins','path':['int']});any_type=to_lisp({'any?':True})
 assert bridge.type_compatible__Q__(integer,any_type) is None
 assert bridge.type_compatible__Q__(any_type,integer) is None


def test_native_descriptor_context_and_nondata_storage():
 namespace={}
 if sys.version_info < (3,11):
  # Python3.10 rejects every list-valued dataclass default, including
  # this hashable subclass. The descriptor itself is still valid.
  with pytest.raises(ValueError,match='mutable default'):
   exec(SOURCE,namespace)
  owner=namespace['Owner']
  assert isinstance(owner.value,namespace['Desc']) and owner(3).value==1
  return
 exec(SOURCE,namespace)
 owner=namespace['Owner'];desc=namespace['Desc'];generic_owner=namespace['GenericOwner'];generic_desc=namespace['GenericDesc']
 assert isinstance(owner.value,desc)
 assert owner(3).value==1
 assert generic_owner.value==['class']
 stored=generic_desc()
 assert generic_owner(stored).value is stored


def test_fallback_only_any_does_not_select_over_uncertain_container(environment):
 _,bridge,opts=environment
 member=bridge.inspect_path('any_owner',to_lisp(['fallback']),opts)
 arguments=to_lisp([{'module':'builtins','path':['list'],'arguments':[{'any?':True}]}])
 signatures=bridge.call_signatures(member,arguments,to_lisp({}),opts)
 assert len(signatures)==2
 assert not any(signature[k('type-match?')] for signature in signatures)
 assert bridge.call_result(member,arguments,to_lisp({}),opts) is None


def test_shared_any_owner_is_irrelevant_even_when_unknown(environment):
 _,bridge,opts=environment
 member=bridge.inspect_path('any_owner',to_lisp(['choose']),opts)
 arguments=to_lisp([{'module':'builtins','path':['NoneType']},{'any?':True},{'module':'builtins','path':['int']}])
 assert bridge.call_result(member,arguments,to_lisp({}),opts)==to_lisp({'module':'builtins','path':['str']})
