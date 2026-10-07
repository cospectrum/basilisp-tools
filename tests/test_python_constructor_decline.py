"""Declined argument inference preserves a bare class, without default specialization."""
import importlib
import importlib.util
from pathlib import Path

import pytest
import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp


@pytest.fixture(scope='module')
def worker():
    path=Path(__file__).resolve().parents[1]/'src/basilisp_tools/_inspect.py'
    spec=importlib.util.spec_from_file_location('_constructor_decline',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def analyze(tmp_path,body):
    analyzer=importlib.import_module('basilisp_tools.analyzer')
    bridge=importlib.import_module('basilisp_tools.python');cache=bridge.create_cache()
    try:
        opts=to_lisp({'python-options':{'python-paths':[str(tmp_path)],'python-inspection?':False,'python-cache':cache}})
        return analyzer.analyze('(ns constructor-decline (:import [declined :as m])) '+body,opts)
    finally:bridge.stop_cache__BANG__(cache)


@pytest.mark.parametrize('actual,body,expected',[(1,'(m/integers (.-value m/x))',int),(1.5,'(m/floats (.-value m/x))',float)])
def test_declined_call_with_arguments_never_reuses_declared_default(worker,tmp_path,actual,body,expected):
    source='''from typing_extensions import Generic,TypeVar
T=TypeVar('T',default=str)
class C(Generic[T]):
 value:T
 def __init__(self,value:T):self.value=value
C.__init__=lambda self,value:setattr(self,'value',value)
x=C('''+repr(actual)+''')
def integers(value:int)->None:pass
def floats(value:float)->None:pass
'''
    (tmp_path/'declined.py').write_text(source)
    info=worker.StaticInspector([str(tmp_path)],[]).module('declined')['members']['x']
    assert worker.type_fields(info)=={'type-module':'declined','type-path':['C'],
                                      'type-arguments':[{'type-any?':True}]}
    native={};exec(source,native)
    assert type(native['x'].value) is expected
    assert list(analyze(tmp_path,body)[k('findings')])==[]


def test_declined_paramspec_keeps_preexisting_unspecialized_class_contract(worker,tmp_path):
    source='''from typing import Any,Callable,Concatenate,Generic,ParamSpec,TypeVar
T=TypeVar('T')
P=ParamSpec('P')
class X(Generic[T,P]):pass
class Y(Generic[P]):
 def __init__(self,cb:Callable[P,Any]):self.cb=cb
 def m1(self)->X[int,Concatenate[float,P]]:return X()
def escaped_annotation(value:Y[P])->None:pass
def callback(value:int)->str:return str(value)
y=Y(callback)
'''
    (tmp_path/'declined.py').write_text(source)
    info=worker.StaticInspector([str(tmp_path)],[]).module('declined')['members']['y']
    assert worker.type_fields(info)=={'type-module':'declined','type-path':['Y']}
    native={};exec(source,native)
    assert isinstance(native['y'].m1(),native['X'])
    assert native['y'].cb(1)=='1'
    assert list(analyze(tmp_path,'(.m1 m/y)')[k('findings')])==[]
