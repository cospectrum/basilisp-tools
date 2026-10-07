import importlib.util
from pathlib import Path
import sys
import pytest

@pytest.fixture
def worker():
    spec=importlib.util.spec_from_file_location('worker_defaults',Path(__file__).resolve().parents[1] / 'src/basilisp_tools/_inspect.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def inspect_source(worker,tmp_path,source):
    (tmp_path/'fixture.py').write_text(source)
    return worker.StaticInspector([str(tmp_path)],[]).module('fixture')['members']

def test_ordered_defaults_follow_explicit_arguments(worker,tmp_path):
    source=('from typing_extensions import Generic,TypeVar\n'
        'T=TypeVar("T",default=str)\nU=TypeVar("U",default=T)\nV=TypeVar("V",default=list[U])\n'
        'class C(Generic[T,U,V]): pass\na=C()\nb=C[int]()\nc=C[int,float]()\n')
    info=inspect_source(worker,tmp_path,source)
    assert [arg['type-path'] for arg in info['a']['type-arguments']]==[['str'],['str'],['list']]
    assert info['a']['type-arguments'][2]['type-arguments'][0]['type-path']==['str']
    assert [arg['type-path'] for arg in info['b']['type-arguments'][:2]]==[['int'],['int']]
    assert info['c']['type-arguments'][2]['type-arguments'][0]['type-path']==['float']

def test_constructor_arguments_do_not_force_generic_defaults(worker,tmp_path):
    source=('from typing_extensions import Generic,TypeVar\nT=TypeVar("T",default=str)\n'
        'class C(Generic[T]):\n def __init__(self,value:T): pass\nx=C(1)\n')
    info=inspect_source(worker,tmp_path,source)['x']
    assert not info.get('type-arguments')

def test_pack_defaults_and_explicit_empty_pack(worker,tmp_path):
    source=('from typing_extensions import Generic,TypeVar,TypeVarTuple,Unpack\n'
        'T=TypeVar("T",default=str)\nTs=TypeVarTuple("Ts",default=Unpack[tuple[T,T]])\n'
        'class C(Generic[T,Unpack[Ts]]): pass\na=C()\nb=C[int]()\nc=C[int,Unpack[tuple[()]]]()\n')
    info=inspect_source(worker,tmp_path,source)
    assert [arg['type-path'] for arg in info['a']['type-arguments']]==[['str'],['str'],['str']]
    assert [arg['type-path'] for arg in info['b']['type-arguments']]==[['int'],['int'],['int']]
    assert [arg['type-path'] for arg in info['c']['type-arguments']]==[['int']]

@pytest.mark.skipif(sys.version_info<(3,13),reason='PEP696 syntax requires Python3.13')
def test_pep695_dependent_defaults_initialize_in_declaration_order(worker,tmp_path):
    source='class C[T=str,U=T,V=list[U]]: pass\na=C()\nb=C[int]()\n'
    info=inspect_source(worker,tmp_path,source)
    assert [arg['type-path'] for arg in info['b']['type-arguments'][:2]]==[['int'],['int']]
    assert info['b']['type-arguments'][2]['type-arguments'][0]['type-path']==['int']
    namespace={};exec(source,namespace)
    # CPython retains lazy symbolic defaults on its runtime alias; the
    # upstream static contract substitutes them through earlier arguments.
    assert namespace['C'][int].__args__[0] is int
    assert isinstance(namespace['C'][int](), namespace['C'])

@pytest.mark.skipif(sys.version_info<(3,13),reason='PEP696 syntax requires Python3.13')
def test_type_alias_defaults_substitute_earlier_parameters(worker,tmp_path):
    info=inspect_source(worker,tmp_path,'type Alias[T=int,U=list[T]]=tuple[T,U]\ndef f() -> Alias[str]: ...\n')
    args=info['f']['type-arguments']
    assert args[0]['type-path']==['str']
    assert args[1]['type-path']==['list']
    assert args[1]['type-arguments'][0]['type-path']==['str']
