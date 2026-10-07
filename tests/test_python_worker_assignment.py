import importlib.util
from pathlib import Path
import sys
import types
import pytest

@pytest.fixture
def worker():
    spec=importlib.util.spec_from_file_location('worker_assignment',Path(__file__).resolve().parents[1] / 'src/basilisp_tools/_inspect.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def inspect_source(worker,tmp_path,source):
    (tmp_path/'fixture.py').write_text(source)
    return worker.StaticInspector([str(tmp_path)],[]).module('fixture')['members']

def test_explicit_annotations_not_inferred_values(worker,tmp_path):
    info=inspect_source(worker,tmp_path,'x: int = 1\ny = 1\nclass C:\n x: str = "s"\n y = 1\n')
    assert info['x']['assignment-type']['type-path']==['int']
    assert 'assignment-type' not in info['y']
    assert info['C']['members']['x']['assignment-type']['type-path']==['str']
    assert 'assignment-type' not in info['C']['members']['y']

def test_property_setter_and_getter_contracts_are_separate(worker,tmp_path):
    source=('class C:\n'
        ' @property\n def x(self) -> int: return self._x\n'
        ' @x.setter\n def x(self, value: str): self._x=int(value)\n'
        ' @property\n def readonly(self) -> str: return "fixed"\n')
    info=inspect_source(worker,tmp_path,source)['C']['members']
    assert info['x']['type-path']==['int']
    assert info['x']['instance-assignment-type']['type-path']==['str']
    assert not info['x'].get('instance-read-only?')
    assert info['readonly']['instance-read-only?']
    assert not info['readonly'].get('read-only?')
    namespace={};exec(source,namespace)
    obj=namespace['C']();obj.x='3';assert obj.x==3
    with pytest.raises(AttributeError):obj.readonly='new'
    namespace['C'].readonly='replacement';assert obj.readonly=='replacement'
    native=worker.safe_member('C',namespace['C'])['members']['x']
    assert native['instance-assignment-type']['type-path']==['str']

def test_untyped_setter_and_unknown_decorator_remain_unknown(worker,tmp_path):
    info=inspect_source(worker,tmp_path,('def wrap(fn): return fn\nclass C:\n'
        ' @property\n def x(self) -> int: return 1\n'
        ' @x.setter\n def x(self,value): pass\n'
        ' @wrap\n @property\n def y(self) -> int: return 1\n'))['C']['members']
    assert info['x']['instance-assignment-type']=={'type-any?':True}
    assert 'instance-read-only?' not in info['y']

def test_data_descriptor_setter_is_instance_only(worker,tmp_path):
    source=('class D:\n def __get__(self,obj,owner) -> int: return obj._value\n'
        ' def __set__(self,obj,value: str): obj._value=int(value)\n'
        'class C:\n x: D = D()\n')
    info=inspect_source(worker,tmp_path,source)['C']['members']['x']
    assert info['instance-assignment-type']['type-path']==['str']
    assert info['assignment-type']['type-path']==['D']
    namespace={};exec(source,namespace);obj=namespace['C']();obj.x='4';assert obj.x==4
    namespace['C'].x=namespace['D']();obj.x='5';assert obj.x==5

def test_runtime_declared_class_annotations(worker):
    class C:
        declared: int = 1
        inferred = 1
    info=worker.safe_member('C',C)['members']
    assert info['declared']['assignment-type']['type-path']==['int']
    assert 'assignment-type' not in info['inferred']


def test_shadowed_property_name_cannot_prove_read_only(worker,tmp_path):
    info=inspect_source(worker,tmp_path,'def property(fn): return fn\nclass C:\n @property\n def value(self) -> int: return 1\n')
    assert 'instance-read-only?' not in info['C']['members']['value']


def test_later_rebinding_preserves_declared_contracts_and_final(worker,tmp_path):
    source=('from typing import Final\nclass A: pass\nclass B: pass\n'
        'value: A\nvalue=B()\nlimit:Final[int]=1\nlimit="changed"\n'
        'class C:\n value:A\n value=B()\n fixed:Final[int]=1\n fixed="changed"\n')
    info=inspect_source(worker,tmp_path,source)
    for members in [info,info['C']['members']]:
        assert members['value']['assignment-type']['type-path']==['A']
        assert members['value']['type-path']==['B']
    assert info['limit']['assignment-type']['type-path']==['int'] and info['limit']['read-only?']
    assert info['C']['members']['fixed']['read-only?']
