"""Explicit Python write contracts apply to setters without confusing class replacement."""
import importlib
import importlib.util
import pytest
import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp

SOURCE = """from typing import Any
number: int = 0
class Descriptor:
    def __get__(self, obj: Any, owner: Any) -> int: return 0
    def __set__(self, obj: Any, value: str) -> None: pass
class Box:
    number: int = 0
    values: list[object] = []
    inferred = 0
    descriptor: Descriptor = Descriptor()
    @property
    def readonly(self) -> int: return 1
    @property
    def converted(self) -> int: return 1
    @converted.setter
    def converted(self, value: str) -> None: pass
    @property
    def dynamic(self) -> int: return 1
    @dynamic.setter
    def dynamic(self, value): pass
class Parent:
    child: Box = Box()
def class_value() -> type[Box]: return Box
"""

@pytest.fixture(scope="module")
def environment(tmp_path_factory):
    root = tmp_path_factory.mktemp("assignment_contracts")
    (root / 'writes.py').write_text(SOURCE)
    analyzer = importlib.import_module('basilisp_tools.analyzer')
    bridge = importlib.import_module('basilisp_tools.python')
    cache = bridge.create_cache()
    options = to_lisp({'python-options': {'python-paths': [str(root)], 'python-inspection?': False,
                                         'python-cache': cache}})
    try:
        yield analyzer, options
    finally:
        bridge.stop_cache__BANG__(cache)

@pytest.mark.parametrize('body,valid', [
    ('(set! (.-number m/Box) 1)', True),
    ('(let [** 1] (set! (.-number (m/Box)) **))', True),
    ('(let [** "bad"] (set! (.-number (m/Box)) **))', False),
    ('(set! (.-number m/Box) "bad")', False),
    ('(set! (.-number (m/Box)) 1)', True),
    ('(set! (.-number (m/Box)) "bad")', False),
    ('(set! (.-number (m/class-value)) 1)', True),
    ('(set! (.-number (m/class-value)) "bad")', False),
    ('(set! (.-converted (m/Box)) "3")', True),
    ('(set! (.-converted (m/Box)) 3)', False),
    ('(set! (.-readonly (m/Box)) 3)', False),
    ('(set! (.-readonly m/Box) 3)', True),
    ('(set! (.-readonly (m/class-value)) 3)', True),
    ('(set! (.-descriptor (m/Box)) "3")', True),
    ('(set! (.-descriptor (m/Box)) 3)', False),
    ('(set! (.-number (.-child (m/Parent))) 1)', True),
    ('(set! (.-number (.-child (m/Parent))) "bad")', False),
    ('(set! (.-values (m/Box)) #py [1])', True),
    ('(let [values #py [1]] (set! (.-values (m/Box)) values))', False),
    ('(set! (.-dynamic (m/Box)) "x")', True),
    ('(set! (.-inferred (m/Box)) "x")', True),
    ('(set! (.-new-attribute (m/Box)) "x")', True),
    ('(defn dynamic [target value] (set! (.-unknown target) value))', True),
])
def test_assignment_contracts(environment, body, valid):
    analyzer, options = environment
    imports = ' (:import [writes :as m])' if 'm/' in body else ''
    result = analyzer.analyze('(ns write-check'+imports+')\n'+body, options)
    findings = list(result[k('findings')])
    assert not any(str(f[k('message')]).startswith('Analysis failed') for f in findings)
    if valid:
        assert findings == []
    else:
        assert [f[k('type')] for f in findings] == [k('type-mismatch')]


def test_property_assignment_boundaries_match_native_python():
    namespace = {}
    exec(SOURCE, namespace)
    Box = namespace['Box']
    obj = Box()
    obj.converted = '3'
    obj.descriptor = '3'
    obj.dynamic = object()
    obj.inferred = 'x'
    obj.new_attribute = 'x'
    with pytest.raises(AttributeError):
        obj.readonly = 3
    Box.readonly = 3
    assert obj.readonly == 3


def test_assignment_check_preserves_rhs_call_signature(environment):
    analyzer,options=environment
    result=analyzer.analyze('(ns write-signature (:import [writes :as m]))\n(set! (.-number (m/Box)) (m/Box))',options)
    constructors=[call for call in result[k('calls')] if call.get(k('name'))=='Box']
    assert len(constructors)==2
    assert all(list(call[k('positional-types')])==[] for call in constructors)
    assert [finding[k('type')] for finding in result[k('findings')]]==[k('type-mismatch')]
