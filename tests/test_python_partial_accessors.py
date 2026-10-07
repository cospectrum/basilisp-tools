"""Transparent accessors preserve partial contracts until a visible mutation/escape."""
import importlib
import importlib.util
from pathlib import Path

import pytest
import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp

PREFIX = '''from functools import partial
from typing import Callable
def f(a:int,b:str)->bool:
 return b.startswith("x")
p=partial(f,1)
'''
ACCESSOR = '''def expose()->partial[bool]:
 return p
'''
CONSUMERS = '''def takes_callable(fn:Callable[[str],bool])->None:pass
def takes_wrong_callable(fn:Callable[[int],bool])->None:pass
'''


@pytest.fixture(scope='module')
def worker():
    path=Path(__file__).resolve().parents[1]/'src/basilisp_tools/_inspect.py'
    spec=importlib.util.spec_from_file_location('_partial_accessor_worker',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def inspect_source(worker,tmp_path,source):
    (tmp_path/'partial_accessor.py').write_text(source)
    return worker.StaticInspector([str(tmp_path)],[]).module('partial_accessor')['members']


def findings(tmp_path,body):
    analyzer=importlib.import_module('basilisp_tools.analyzer')
    bridge=importlib.import_module('basilisp_tools.python');cache=bridge.create_cache()
    try:
        options=to_lisp({'python-options':{'python-paths':[str(tmp_path)],'python-inspection?':False,'python-cache':cache}})
        return list(analyzer.analyze('(ns partial-accessor (:import [partial_accessor :as m]))\n'+body,options)[k('findings')])
    finally:bridge.stop_cache__BANG__(cache)


def test_unused_partial_accessor_retains_callback_mismatch(worker,tmp_path):
    source=PREFIX+CONSUMERS+'''takes_callable(p)
takes_wrong_callable(p)
def returns_partial()->partial[bool]:
 return p
'''
    members=inspect_source(worker,tmp_path,source)
    assert members['p']['parameters'][0]['type-path']==['str']
    assert findings(tmp_path,'(m/takes-callable m/p)')==[]
    assert [row[k('type')] for row in findings(tmp_path,'(m/takes-wrong-callable m/p)')]==[k('type-mismatch')]
    namespace={};exec(source,namespace)
    assert namespace['returns_partial']()('xyz') is True


@pytest.mark.parametrize('getter',[
    'def expose():\n return p\n',
    'def expose():\n "Document the accessor."\n return p\n',
    'def expose():\n return f\n',
])
def test_plain_transparent_getters_keep_metadata(worker,tmp_path,getter):
    members=inspect_source(worker,tmp_path,PREFIX+getter)
    assert members['p']['parameters'][0]['type-path']==['str']


@pytest.mark.parametrize('mutation',[
    'expose().func.__code__=replacement.__code__',
    'q=expose()\nq.func.__code__=replacement.__code__',
    'getters=[expose]\ngetters[0]().func.__code__=replacement.__code__',
    'getter,=[expose]\ngetter().func.__code__=replacement.__code__',
    'setattr(expose().func,"__code__",replacement.__code__)',
    'def mutate(value):value.func.__code__=replacement.__code__\nmutate(expose())',
    'def mutate(getter):getter().func.__code__=replacement.__code__\nmutate(expose)',
])
def test_accessed_function_mutation_declines_stale_contract(worker,tmp_path,mutation):
    source=PREFIX+ACCESSOR+'def replacement(a,b):return b\n'+mutation+'\n'
    members=inspect_source(worker,tmp_path,source)
    assert members['p'].get('signature-unknown?')
    namespace={};exec(source,namespace)
    assert namespace['p'](4)==4
    assert findings(tmp_path,'(inc (m/p 4))')==[]


def test_original_function_accessor_mutation_is_still_detected(worker,tmp_path):
    source=PREFIX+'def expose():return f\ndef replacement(a,b):return b\nexpose().__code__=replacement.__code__\n'
    members=inspect_source(worker,tmp_path,source)
    assert members['p'].get('signature-unknown?')
    namespace={};exec(source,namespace)
    assert namespace['p'](4)==4


def test_decorated_accessor_is_not_treated_as_transparent(worker,tmp_path):
    source=PREFIX+'''def replace(getter):
 getter().func.__annotations__['b']=int
 return getter
@replace
def expose():return p
'''
    members=inspect_source(worker,tmp_path,source)
    assert members['p'].get('signature-unknown?')


def test_accessor_replacement_invalidates_alias_assumption(worker,tmp_path):
    source=PREFIX+ACCESSOR+'''def other():return None
expose.__code__=other.__code__
'''
    assert inspect_source(worker,tmp_path,source)['p'].get('signature-unknown?')


def test_early_accessor_of_later_wrapper_does_not_hide_mutation(worker,tmp_path):
    source='def expose():return p\n'+PREFIX+"""def replacement(a,b):return b
expose().func.__code__=replacement.__code__
"""
    members=inspect_source(worker,tmp_path,source)
    assert members['p'].get('signature-unknown?')
    namespace={};exec(source,namespace)
    assert namespace['p'](4)==4
    assert findings(tmp_path,'(inc (m/p 4))')==[]


@pytest.mark.parametrize('body',[
    'expose().func.__code__=replacement.__code__',
    'q=expose();q.func.__code__=replacement.__code__',
    'def mutate(getter):getter().func.__code__=replacement.__code__\n mutate(expose)',
])
def test_forward_accessor_mutation_is_detected(worker,tmp_path,body):
    source=PREFIX+'def replacement(a,b):return b\ndef touch():\n '+body+'\n'+ACCESSOR+'touch()\n'
    members=inspect_source(worker,tmp_path,source)
    assert members['p'].get('signature-unknown?')
    namespace={};exec(source,namespace)
    assert namespace['p'](4)==4
    assert findings(tmp_path,'(inc (m/p 4))')==[]


@pytest.mark.parametrize('escape', [
    'for getter in [expose]:\n getter().func.__code__=replacement.__code__',
    'for wrapper in [expose()]:\n wrapper.func.__code__=replacement.__code__',
    '[setattr(getter().func,"__code__",replacement.__code__) for getter in [expose]]',
    'match [expose]:\n case [getter]:\n  getter().func.__code__=replacement.__code__',
    'holders=[]\ndef touch():holders[0]().func.__code__=replacement.__code__\nholders += [expose]\ntouch()',
    'def touch(getter=expose):getter().func.__code__=replacement.__code__\ntouch()',
    'def touch(*,getter=expose):getter().func.__code__=replacement.__code__\ntouch()',
    'touch=lambda getter=expose:setattr(getter().func,"__code__",replacement.__code__)\ntouch()',
    'class Holder:\n getter=expose\nHolder.getter().func.__code__=replacement.__code__',
    'alias=expose\nclass Holder:\n getter=alias\nHolder.getter().func.__code__=replacement.__code__',
    'class Active:\n def __class_getitem__(cls,getter):\n  getter().func.__code__=replacement.__code__\n  return int\nseen=Active[expose]',
    'class Active:\n def __add__(self,getter):\n  getter().func.__code__=replacement.__code__\n  return 1\nseen=Active()+expose',
    'class Active:\n def __iadd__(self,getter):\n  getter().func.__code__=replacement.__code__\n  return self\nactive=Active()\nactive+=expose',
    'class Active:\n def __eq__(self,getter):\n  getter().func.__code__=replacement.__code__\n  return True\nseen=Active()==expose',
    'class Active:\n def __contains__(self,getter):\n  getter().func.__code__=replacement.__code__\n  return True\nseen=expose in Active()',
])
def test_implicit_accessor_escapes_decline_stale_contract(worker,tmp_path,escape):
    source=PREFIX+ACCESSOR+'def replacement(a,b):return b\n'+escape+'\n'
    members=inspect_source(worker,tmp_path,source)
    assert members['p'].get('signature-unknown?')
    namespace={};exec(source,namespace)
    assert namespace['p'](4)==4
    assert findings(tmp_path,'(inc (m/p 4))')==[]
