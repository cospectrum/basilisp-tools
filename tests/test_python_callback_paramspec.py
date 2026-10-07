import importlib
import pytest
import basilisp_tools
from basilisp.lang.runtime import to_lisp
from basilisp.lang.keyword import keyword as k

SOURCE="""from typing import Callable, Concatenate, ParamSpec, Protocol
P=ParamSpec('P')
class Prefix(Protocol[P]):
    def __call__(self,head:int,/,*args:P.args,**kwargs:P.kwargs)->int:...
class Identity(Protocol[P]):
    def __call__(self,*args:P.args,**kwargs:P.kwargs)->int:...
def capture(cb:Prefix[P])->Callable[P,bool]:...
def full(cb:Identity[P])->Callable[P,bool]:...
def good(a:int,b:str,c:str)->int:...
def wrong_head(a:str,b:str,c:str)->int:...
def wrong_result(a:int,b:str,c:str)->str:...
def optional(a:int,b:str='x',*,flag:bool=False)->int:...
"""
@pytest.fixture(scope='module')
def contracts(tmp_path_factory):
    root=tmp_path_factory.mktemp('callback_paramspec');(root/'contracts.pyi').write_text(SOURCE)
    bridge=importlib.import_module('basilisp_tools.python');analyzer=importlib.import_module('basilisp_tools.analyzer')
    cache=bridge.create_cache();opts=to_lisp({'python-paths':[str(root)],'python-inspection?':False}).assoc(k('python-cache'),cache)
    try:yield analyzer,opts
    finally:bridge.stop_cache__BANG__(cache)

@pytest.mark.parametrize('body,valid',[
    ('(m/capture m/good)',True),('(m/capture m/wrong-head)',False),('(m/capture m/wrong-result)',False),
    ('((m/capture m/good) "b" "c")',True),('((m/capture m/good) ** :b "b" :c "c")',True),
    ('((m/capture m/good) "b")',False),('((m/capture m/good) "b" 1)',False),
    ('((m/capture m/good) ** :a 1 :b "b" :c "c")',False),
    ('((m/capture m/optional))',True),('((m/capture m/optional) ** :flag true)',True),
    ('((m/capture m/optional) ** :flag "bad")',False),
    ('((m/full m/good) 1 "b" "c")',True),('((m/full m/good) "b" "c")',False),
])
def test_callback_protocol_paramspec_preserves_the_captured_tail(contracts,body,valid):
    analyzer,opts=contracts
    result=analyzer.analyze('(ns callback-paramspec (:import [contracts :as m])) '+body,to_lisp({}).assoc(k('python-options'),opts))
    findings=list(result.val_at(k('findings')))
    assert (len(findings)==0) is valid,findings
