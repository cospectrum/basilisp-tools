"""A property read through its class returns the descriptor itself."""
import importlib
import pytest
import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp

SOURCE='''class C:
 @property
 def value(self)->int:return 1
def class_value()->type[C]:return C
def property_value(value:property)->None:assert isinstance(value,property)
def integer(value:int)->None:assert isinstance(value,int)
'''

@pytest.fixture(scope='module')
def environment(tmp_path_factory):
    root=tmp_path_factory.mktemp('class_property_reads');(root/'class_properties.py').write_text(SOURCE)
    analyzer=importlib.import_module('basilisp_tools.analyzer');bridge=importlib.import_module('basilisp_tools.python')
    cache=bridge.create_cache()
    options=to_lisp({'python-options':{'python-paths':[str(root)],'python-inspection?':False,'python-cache':cache}})
    try:yield analyzer,options
    finally:bridge.stop_cache__BANG__(cache)

@pytest.mark.parametrize('body,valid',[
    ('(m/property-value (.-value m/C))',True),
    ('(m/property-value m/C.value)',True),
    ('(m/property-value (.-value (m/class-value)))',True),
    ('(m/integer (.-value (m/C)))',True),
    ('(m/integer (.-value m/C))',False),
    ('(m/property-value (.-value (m/C)))',False),
])
def test_class_and_instance_property_read_contracts(environment,body,valid):
    analyzer,options=environment
    result=analyzer.analyze('(ns class-property-reads (:import [class_properties :as m])) '+body,options)
    findings=list(result[k('findings')])
    assert [finding[k('type')] for finding in findings]==([] if valid else [k('type-mismatch')])

def test_class_and_instance_property_values_match_native_python():
    namespace={};exec(SOURCE,namespace)
    namespace['property_value'](namespace['C'].value)
    namespace['property_value'](namespace['class_value']().value)
    namespace['integer'](namespace['C']().value)
