"""Trusted EnumMeta inherits ordinary class lookup, unless explicitly replaced."""
import importlib
import importlib.util
from pathlib import Path
import pytest
import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp

SOURCE='''from enum import Enum,EnumMeta
class Meta(EnumMeta):pass
class E(Enum,metaclass=Meta):A=1
class Base(Enum,metaclass=Meta):
 @property
 def value(self)->str:return 'value'
class Child(Base):A=1
class DynamicMeta(Meta):
 def __getattribute__(cls,name):return 'dynamic' if name=='A' else super().__getattribute__(name)
class Dynamic(Enum,metaclass=DynamicMeta):A=1
class ReplacingMeta(Meta):
 def __new__(mcls,name,bases,namespace):return type('Replacement',(),{'A':'replacement'})
class Replaced(Enum,metaclass=ReplacingMeta):A=1
def enum_value(value:E)->None:assert isinstance(value,E)
def integer(value:int)->None:assert isinstance(value,int)
def text(value:str)->None:assert isinstance(value,str)
'''

@pytest.fixture(scope='module')
def environment(tmp_path_factory):
 root=tmp_path_factory.mktemp('enum_meta_reads');(root/'enum_reads.py').write_text(SOURCE)
 analyzer=importlib.import_module('basilisp_tools.analyzer');bridge=importlib.import_module('basilisp_tools.python')
 cache=bridge.create_cache();opts=to_lisp({'python-paths':[str(root)],'python-inspection?':False,'python-cache':cache})
 try:yield analyzer,bridge,opts
 finally:bridge.stop_cache__BANG__(cache)

@pytest.mark.parametrize('body,valid',[
 ('(m/enum-value m/E.A)',True),
 ('(m/text (.-value m/Child.A))',True),
 ('(m/integer (.-value m/Child.A))',False),
 ('(m/text m/Dynamic.A)',True),
 ('(m/text m/Replaced.A)',True),
])
def test_trusted_and_overridden_enum_metaclass_reads(environment,body,valid):
 analyzer,_,opts=environment
 result=analyzer.analyze('(ns enum-meta-reads (:import [enum_reads :as m])) '+body,to_lisp({'python-options':opts}))
 assert [finding[k('type')] for finding in result[k('findings')]]==([] if valid else [k('type-mismatch')])


def test_native_enum_metaclass_reads():
 namespace={};exec(SOURCE,namespace)
 assert isinstance(namespace['E'].A,namespace['E'])
 assert namespace['Child'].A.value=='value'
 assert namespace['Dynamic'].A=='dynamic'
 assert namespace['Replaced'].A=='replacement'


def test_project_enum_shadow_does_not_grant_known_lookup(tmp_path):
 spec=importlib.util.spec_from_file_location('enum_meta_worker',Path(__file__).resolve().parents[1]/'src/basilisp_tools/_inspect.py')
 worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
 (tmp_path/'enum.py').write_text('from unknown import Base\nclass EnumMeta(Base):pass\n')
 (tmp_path/'fixture.py').write_text('from enum import EnumMeta\nclass C(metaclass=EnumMeta):value:int=1\n')
 member=worker.StaticInspector([str(tmp_path)],[]).module('fixture')['members']['C']['members']['value']
 assert member.get('class-read-type')=={'type-any?':True}


def test_enum_member_identity_is_concrete(environment):
 _,bridge,opts=environment
 member=bridge.inspect_path('enum_reads',to_lisp(['E','A']),opts)
 assert member[k('type-path')]==to_lisp(['E'])
 assert k('class-read-type') not in member


def test_custom_enum_metaclass_new_can_replace_the_class(environment):
 _,bridge,opts=environment
 member=bridge.inspect_path('enum_reads',to_lisp(['Replaced','A']),opts)
 assert member[k('class-read-type')]==to_lisp({'type-any?':True})
