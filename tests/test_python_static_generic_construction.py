"""Declared constructor parameters and nested defaults specialize exported values."""
import importlib.util
from pathlib import Path
import sys

import pytest


@pytest.fixture
def worker():
    path = Path(__file__).resolve().parents[1] / 'src/basilisp_tools/_inspect.py'
    spec = importlib.util.spec_from_file_location('_fifth_constructor_worker', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inspect_source(worker, tmp_path, source):
    (tmp_path / 'fixture.py').write_text(source)
    return worker.StaticInspector([str(tmp_path)], []).module('fixture')['members']


SOURCE = '''from typing import Generic, NamedTuple, TypeVar, overload, Any
T=TypeVar('T')
class Property(NamedTuple, Generic[T]):
    name: str
    value: T
class Box(Generic[T]):
    def __init__(self, value: T): self.value=value
class Twice(Generic[T]):
    def __init__(self, first: T, second: T): pass
class Overloaded(Generic[T]):
    @overload
    def __init__(self, value: int): ...
    @overload
    def __init__(self, value: str): ...
    def __init__(self, value): pass
class Receiver(Generic[T]):
    def __init__(self: 'Receiver[str]', value: T): pass
class Variadic(Generic[T]):
    def __init__(self, *values: T): pass
constant=3.4
unknown: Any
literal=Property('', 3.4)
keyword=Property(name='', value=constant)
box=Box(42)
explicit=Box[str](42)
conflict=Twice(1, 'x')
overloaded=Overloaded(1)
receiver=Receiver(1)
variadic=Variadic(1)
unpacked=Box(*unknown)
unknown_value=Box(unknown)
missing=Property('')
duplicate=Box(1,value=1)
'''


@pytest.mark.parametrize('name,expected', [('literal','float'),('keyword','float'),('box','int'),('explicit','str')])
def test_direct_generic_parameter_inference(worker,tmp_path,name,expected):
    value=inspect_source(worker,tmp_path,SOURCE)[name]
    assert value['type-arguments']==[{'type-module':'builtins','type-path':[expected]}]


@pytest.mark.parametrize('name', ['conflict','overloaded','receiver','variadic','unpacked','unknown_value','missing','duplicate'])
def test_uncertain_construction_does_not_invent_arguments(worker,tmp_path,name):
    value=inspect_source(worker,tmp_path,SOURCE)[name]
    assert not value.get('type-arguments') or all(argument.get('type-any?') for argument in value['type-arguments'])


@pytest.mark.skipif(sys.version_info<(3,11),reason="Generic NamedTuple requires Python3.11")
def test_native_literal_namedtuple_value():
    namespace={}
    exec(SOURCE.split('unknown: Any')[0]+"\nliteral=Property('', 3.4)\nkeyword=Property(name='', value=constant)\n",namespace)
    assert type(namespace['literal'].value) is float
    assert type(namespace['keyword'].value) is float


def test_inference_precedes_dependent_defaults(worker,tmp_path):
    source='''from typing_extensions import Generic,TypeVar
T=TypeVar('T')
U=TypeVar('U',default=list[T])
class C(Generic[T,U]):
 def __init__(self,value:T):pass
value=C('x')
'''
    value=inspect_source(worker,tmp_path,source)['value']
    assert value['type-arguments']==[{'type-module':'builtins','type-path':['str']},{'type-module':'builtins','type-path':['list'],'type-arguments':[{'type-module':'builtins','type-path':['str']}]}]


@pytest.mark.skipif(sys.version_info<(3,13),reason='PEP695 defaults require Python3.13')
def test_forward_nested_default_is_completed(worker,tmp_path):
    values=inspect_source(worker,tmp_path,'class Child[S=Parent]:pass\nclass Parent[T=int]:pass\nvalue=Child()\n')
    expected={'type-module':'fixture','type-path':['Parent'],'type-arguments':[{'type-module':'builtins','type-path':['int']}]}
    assert values['Child']['type-parameters'][0]['type-default']==expected
    assert values['value']['type-arguments']==[expected]


@pytest.mark.skipif(sys.version_info<(3,13),reason='PEP695 defaults require Python3.13')
def test_nested_default_keeps_explicit_arguments(worker,tmp_path):
    values=inspect_source(worker,tmp_path,'class Parent[T=int]:pass\nclass Child[S=Parent[str]]:pass\nvalue=Child()\n')
    assert values['value']['type-arguments'][0]['type-arguments']==[{'type-module':'builtins','type-path':['str']}]


@pytest.mark.skipif(sys.version_info<(3,13),reason='PEP695 defaults require Python3.13')
def test_recursive_default_is_bounded(worker,tmp_path):
    values=inspect_source(worker,tmp_path,'class Node[T=Node]:pass\nvalue=Node()\n')
    assert values['value']['type-path']==['Node']


def test_uncertain_supplied_argument_does_not_select_default(worker,tmp_path):
    source='''from typing import Any
from typing_extensions import Generic,TypeVar
U=TypeVar('U')
T=TypeVar('T',default=int)
class Pair(Generic[U,T]):
 def __init__(self,left:U,right:T):pass
unknown:Any='x'
value=Pair(1,unknown)
'''
    value=inspect_source(worker,tmp_path,source)['value']
    assert value['type-arguments']==[{'type-module':'builtins','type-path':['int']},{'type-any?':True}]


def test_function_value_is_not_its_return_annotation(worker,tmp_path):
    source='''from typing import Generic,TypeVar
T=TypeVar('T')
class Box(Generic[T]):
 def __init__(self,value:T):pass
def function()->int:return 1
value=Box(function)
'''
    value=inspect_source(worker,tmp_path,source)['value']
    assert value['type-arguments']==[{'type-any?':True}]


def test_conflicting_argument_does_not_select_default(worker,tmp_path):
    source='''from typing_extensions import Generic,TypeVar
U=TypeVar('U')
T=TypeVar('T',default=int)
class C(Generic[U,T]):
 def __init__(self,first:U,second:T,third:T):pass
value=C(True,1,'x')
'''
    value=inspect_source(worker,tmp_path,source)['value']
    assert value['type-arguments']==[{'type-module':'builtins','type-path':['bool']},{'type-any?':True}]
