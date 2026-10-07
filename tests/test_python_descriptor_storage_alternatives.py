"""Descriptor reads preserve declared unions and proven dataclass storage."""
import importlib
import pytest
import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp

SOURCE='''from typing import Generic,TypeVar
from dataclasses import dataclass
T=TypeVar('T')
class Field(Generic[T]):
 def __get__(self,obj:object|None,owner:type|None=None)->T:return 'value'
class Settings:
 with_default:Field[str]|str=Field()
 optional:Field[str]|None=Field()
 distinct:Field[str]|int=Field()
class Dev:
 def __get__(self,obj,owner)->'Dev':return self
@dataclass
class Base:
 x:Dev=Dev()
@dataclass
class Sub(Base):pass
class Other:pass
class Uncertain:
 def __get__(self,obj,owner)->Other:return Other()
@dataclass
class Bad:
 x:Uncertain=Uncertain()
def text(value:str)->None:pass
def optional_text(value:str|None)->None:pass
def distinct(value:str|int)->None:pass
def integer(value:int)->None:pass
def descriptor(value:Dev)->None:pass
'''

@pytest.fixture(scope='module')
def environment(tmp_path_factory):
 root=tmp_path_factory.mktemp('descriptor_storage');(root/'storage.py').write_text(SOURCE)
 analyzer=importlib.import_module('basilisp_tools.analyzer');bridge=importlib.import_module('basilisp_tools.python')
 cache=bridge.create_cache();opts=to_lisp({'python-paths':[str(root)],'python-inspection?':False,'python-cache':cache})
 try:yield analyzer,bridge,opts
 finally:bridge.stop_cache__BANG__(cache)

@pytest.mark.parametrize('body,valid',[
 ('(m/text (.-with-default (m/Settings)))',True),
 ('(m/integer (.-with-default (m/Settings)))',False),
 ('(m/optional-text (.-optional (m/Settings)))',True),
 ('(m/text (.-optional (m/Settings)))',False),
 ('(m/distinct (.-distinct (m/Settings)))',True),
 ('(m/text (.-distinct (m/Settings)))',False),
 ('(m/text m/Settings.with-default)',True),
 ('(m/descriptor (.-x (m/Base)))',True),
 ('(m/descriptor (.-x (m/Sub)))',True),
 ('(m/integer (.-x (m/Base)))',False),
])
def test_declared_storage_read_alternatives(environment,body,valid):
 analyzer,_,opts=environment
 result=analyzer.analyze('(ns descriptor-storage (:import [storage :as m])) '+body,to_lisp({'python-options':opts}))
 assert [finding[k('type')] for finding in result[k('findings')]]==([] if valid else [k('type-mismatch')])


def test_unproven_default_storage_remains_unknown(environment):
 _,bridge,opts=environment
 member=bridge.inspect_path('storage',to_lisp(['Bad','x']),opts)
 assert member[k('type-any?')] is True
 assert k('instance-read-type') not in member


def test_native_storage_contracts():
 namespace={};exec(SOURCE,namespace)
 settings=namespace['Settings']()
 assert settings.with_default=='value'
 settings.optional=None;assert settings.optional is None
 settings.distinct=1;assert settings.distinct==1
 assert isinstance(namespace['Base']().x,namespace['Dev'])
 assert isinstance(namespace['Sub']().x,namespace['Dev'])
 assert isinstance(namespace['Bad']().x,namespace['Other'])
