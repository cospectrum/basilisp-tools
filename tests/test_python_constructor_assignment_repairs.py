"""Unspecialized constructed values retain gradual missing parameters."""
import importlib
import importlib.util
from pathlib import Path
import pytest
import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp

SOURCE='''from typing_extensions import Generic,TypeVar
T=TypeVar('T',int,str)
class Adder(Generic[T]):
 def add(self,a:T,b:T)->T:return a
bare=Adder()
explicit=Adder[int]()
S=TypeVar('S')
U=TypeVar('U',default=S)
class Dependent(Generic[S,U]):pass
dependent=Dependent()
class A:pass
class A2(A):pass
class B:pass
V=TypeVar('V',A,B)
class F(Generic[V]):
 def __init__(self,value:V):pass
promoted=F[A2](A2())
'''

@pytest.fixture(scope='module')
def environment(tmp_path_factory):
 root=tmp_path_factory.mktemp('constructor_assignment_repairs');(root/'constructors.py').write_text(SOURCE)
 analyzer=importlib.import_module('basilisp_tools.analyzer');bridge=importlib.import_module('basilisp_tools.python')
 cache=bridge.create_cache();opts=to_lisp({'python-paths':[str(root)],'python-inspection?':False,'python-cache':cache})
 try:yield root,analyzer,bridge,opts
 finally:bridge.stop_cache__BANG__(cache)


def test_static_constructed_argument_metadata(environment):
 _,_,bridge,opts=environment
 bare=bridge.inspect_path('constructors',to_lisp(['bare']),opts)
 assert list(bare[k('type-arguments')])==[to_lisp({'type-any?':True})]
 explicit=bridge.inspect_path('constructors',to_lisp(['explicit']),opts)
 assert explicit[k('type-arguments')][0][k('type-path')]==to_lisp(['int'])
 dependent=bridge.inspect_path('constructors',to_lisp(['dependent']),opts)
 assert list(dependent[k('type-arguments')])==[to_lisp({'type-any?':True})]*2
 promoted=bridge.inspect_path('constructors',to_lisp(['promoted']),opts)
 assert promoted[k('type-arguments')][0][k('type-path')]==to_lisp(['A'])

@pytest.mark.parametrize('body,valid',[
 ('(.add m/bare 1 "b")',True),
 ('(.add m/bare 1 2)',True),
 ('(.add m/bare "a" "b")',True),
 ('(.add m/explicit 1 2)',True),
 ('(.add m/explicit 1 "b")',False),
])
def test_gradual_parameters_preserve_explicit_specialization(environment,body,valid):
 _,analyzer,_,opts=environment
 result=analyzer.analyze('(ns constructor-assignment-repairs (:import [constructors :as m])) '+body,to_lisp({'python-options':opts}))
 assert [finding[k('type')] for finding in result[k('findings')]]==([] if valid else [k('type-mismatch')])
