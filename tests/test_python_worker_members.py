import importlib.util
from pathlib import Path
import pytest
import sys

@pytest.fixture
def worker():
    spec=importlib.util.spec_from_file_location('worker_members',Path(__file__).resolve().parents[1] / 'src/basilisp_tools/_inspect.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def inspect_source(worker,tmp_path,source):
    (tmp_path/'fixture.py').write_text(source)
    return worker.StaticInspector([str(tmp_path)],[]).module('fixture')['members']

def test_class_completeness_is_separate_from_instance_attributes(worker,tmp_path):
    source=('from typing import Generic, TypeVar\nT=TypeVar("T")\n'
        'class Plain: pass\nclass Child(Plain): pass\nclass G(Generic[T]): pass\n'
        'class Unknown(missing.Base): pass\nclass Meta(type): pass\n'
        'class Custom(metaclass=Meta): pass\nclass Conditional:\n if condition():\n  x=1\n')
    info=inspect_source(worker,tmp_path,source)
    assert all(info[n]['class-members-complete?'] for n in ['Plain','Child'])
    assert not info['Plain']['members-complete?']
    assert all(not info[n]['class-members-complete?'] for n in ['Unknown','Custom','Conditional','G'])

def test_overloaded_descriptor_methods_preserve_class_instance_branches(worker,tmp_path):
    source=('from typing import overload, Generic, TypeVar\nT=TypeVar("T")\n'
        'class D(Generic[T]):\n'
        ' @overload\n def __get__(self,obj: None,owner: type) -> "D[T]": ...\n'
        ' @overload\n def __get__(self,obj: object,owner: type) -> T: ...\n'
        ' def __get__(self,obj,owner): return self if obj is None else 1\n'
        ' def __set__(self,obj,value: T): pass\n'
        'class C:\n x: D[int] = D()\n annotation_only: D[int]\n')
    info=inspect_source(worker,tmp_path,source)['C']['members']
    assert len(info['x']['descriptor-get']['overloads'])==2
    assert info['x']['descriptor-get']['overloads'][1]['type-path']==['int']
    assert info['x']['descriptor-instance?']
    assert info['x']['descriptor-set']['parameters'][2]['type-path']==['int']
    assert 'descriptor-get' not in info['annotation_only']

def test_nondatadescriptor_instance_shadowing_retains_class_getter(worker,tmp_path):
    source=('class D:\n def __get__(self,obj,owner) -> int: return 1\n'
        'class C:\n x=D()\n def __init__(self): self.x="stored"\n')
    info=inspect_source(worker,tmp_path,source)['C']['members']['x']
    assert info['descriptor-get']['type-path']==['int']
    assert not info['descriptor-instance?']
    namespace={};exec(source,namespace)
    assert namespace['C'].x==1 and namespace['C']().x=='stored'

def test_unknown_replacement_descriptor_cannot_prove_getter(worker,tmp_path):
    source=('def replace(cls): return int\n@replace\nclass D:\n'
        ' def __get__(self,obj,owner) -> str: return "wrong"\nclass C:\n x=D()\n')
    info=inspect_source(worker,tmp_path,source)['C']['members']['x']
    assert 'descriptor-get' not in info

def test_runtime_class_completeness_retains_special_methods(worker):
    class C:
        def __class_getitem__(cls,value: int) -> str:return str(value)
    info=worker.safe_member('C',C)
    assert info['class-members-complete?']
    assert info['members']['__class_getitem__']['parameters'][0]['type-path']==['int']
    class Meta(type):pass
    class Custom(metaclass=Meta):pass
    assert not worker.safe_member('Custom',Custom)['class-members-complete?']


def test_runtime_descriptor_contracts_do_not_execute_getter(worker):
    class D:
        def __get__(self,obj,owner) -> int:
            raise AssertionError('must not execute')
        def __set__(self,obj,value:str):pass
    class C:
        x: D = D()
    info=worker.safe_member('C',C)['members']['x']
    assert info['descriptor-get']['type-path']==['int']
    assert info['descriptor-set']['parameters'][2]['type-path']==['str']
    assert info['descriptor-instance?']


def test_descriptor_class_results_remain_type_values(worker,tmp_path):
    source=('from typing import overload\nclass C: pass\n'
        'class D:\n @overload\n def __get__(self,obj:None,owner:type) -> type[C]: ...\n'
        ' @overload\n def __get__(self,obj:object,owner:type) -> C: ...\n'
        ' def __get__(self,obj,owner): return C if obj is None else C()\n'
        'class Owner:\n value=D()\n'
        'def factory() -> type[C]: return C\ndirect=C\n')
    info=inspect_source(worker,tmp_path,source)
    class_result=info['Owner']['members']['value']['descriptor-get']['overloads'][0]
    assert class_result['type-path']==['type']
    assert class_result['type-arguments']==[{'type-module':'fixture','type-path':['C']}]
    assert info['factory']['type-path']==['type']
    assert info['direct']['kind']=='class' and info['direct']['type-path']==['C']
    namespace={};exec(source,namespace)
    assert namespace['Owner'].value is namespace['C']
    assert isinstance(namespace['Owner']().value,namespace['C'])


def test_runtime_descriptor_inspection_never_tests_user_truthiness(worker):
    class Trap:
        def __bool__(self):raise AssertionError('must not execute')
    class D:
        def __get__(self,obj,owner) -> int:return 1
        __set__=Trap()
    class C:
        value=D()
    info=worker.safe_member('C',C)
    assert info['members']['value']['descriptor-get']['type-path']==['int']


def test_shadowed_descriptor_storage_is_unknown_without_losing_class_getter(worker,tmp_path):
    source=('from typing import Callable\nclass D:\n'
        ' def __get__(self,obj,owner) -> Callable[[str],int]: return len\n'
        'class C:\n value=D()\n def __init__(self): self.value=1\n')
    info=inspect_source(worker,tmp_path,source)['C']['members']['value']
    assert info['type-any?'] and 'type-path' not in info
    assert info['descriptor-get']['type-path']==['Callable']
    namespace={};exec(source,namespace)
    assert namespace['C']().value==1 and namespace['C'].value('word')==4
    native=worker.safe_member('C',namespace['C'])['members']['value']
    assert native['type-any?'] and 'type-path' not in native
    assert native['descriptor-get']['type-path']==['Callable']


def test_index_slot_inspection_ignores_instance_attribute_hooks(worker):
    class Index:
        def __getattribute__(self,name):raise AssertionError('must not execute')
        def __getattr__(self,name):raise AssertionError('must not execute')
        def __index__(self) -> int:return 0
    info=worker.safe_member('Index',Index)
    assert info['class-members-complete?']
    assert info['members']['__index__']['type-path']==['int']
    assert [17][Index()]==17
    class Pretend:
        def __getattr__(self,name):return lambda:0
    absent=worker.safe_member('Pretend',Pretend)
    assert absent['class-members-complete?'] and '__index__' not in absent['members']
    with pytest.raises(TypeError):[17][Pretend()]


def test_instance_completeness_requires_no_dynamic_attribute_sources(worker,tmp_path):
    source=('class Plain: pass\nclass Initialized:\n'
        ' def __init__(self): self.method=lambda: 1\n'
        'class Dynamic:\n def __getattr__(self,name): return lambda: 1\n'
        'class Getattribute:\n def __getattribute__(self,name): return lambda: 1\n')
    info=inspect_source(worker,tmp_path,source)
    assert info['Plain']['instance-members-complete?']
    assert all(not info[name]['instance-members-complete?'] for name in ['Initialized','Dynamic','Getattribute'])
    namespace={};exec(source,namespace)
    for name in ['Plain','Initialized','Dynamic','Getattribute']:
        assert worker.safe_member(name,namespace[name])['instance-members-complete?']==(name=='Plain')


def test_runtime_final_annotation_is_read_only_contract(worker):
    from typing import Final
    class C:
        fixed:Final[int]=1
        mutable:int=1
    info=worker.safe_member('C',C)['members']
    assert info['fixed']['read-only?'] and info['fixed']['assignment-type']['type-path']==['int']
    assert 'read-only?' not in info['mutable']


def test_frozen_dataclass_only_blocks_instance_field_assignment(worker,tmp_path,monkeypatch):
    import types
    source=('from dataclasses import dataclass\n@dataclass(frozen=True)\nclass C:\n value:int=1\n'
        'class Child(C): pass\n')
    info=inspect_source(worker,tmp_path,source)
    assert info['C']['members']['value']['instance-read-only?']
    assert info['Child']['members']['value']['instance-read-only?']
    assert 'read-only?' not in info['C']['members']['value']
    module=types.ModuleType('fixture');monkeypatch.setitem(sys.modules,'fixture',module)
    exec(source,vars(module))
    with pytest.raises(AttributeError):module.C().value=2
    module.C.value=3
    assert module.C.value==3
    unknown=inspect_source(worker,tmp_path,'class Meta(type): pass\n'+source.replace('class C:', 'class C(metaclass=Meta):'))
    assert not unknown['C']['members']['value'].get('instance-read-only?')


def test_enum_readonly_storage_is_class_only_and_not_exported_alias(worker,tmp_path):
    source='from enum import Enum\nclass E(Enum):\n A=1\n B=2\nalias=E.A\n'
    info=inspect_source(worker,tmp_path,source)
    assert info['E']['members']['A']['class-read-only?']
    assert not info['E']['members']['A'].get('read-only?')
    assert 'class-read-only?' not in info['alias']
    namespace={};exec(source,namespace)
    with pytest.raises(AttributeError):namespace['E'].A=namespace['E'].B
    namespace['E'].A.A=namespace['E'].B
    assert namespace['E'].A.A is namespace['E'].B
    namespace['alias']=namespace['E'].B


def test_custom_enum_metaclass_does_not_claim_readonly(worker,tmp_path):
    source=('from enum import Enum,EnumMeta\nclass Meta(EnumMeta):\n'
        ' def __setattr__(cls,name,value): type.__setattr__(cls,name,value)\n'
        'class E(Enum,metaclass=Meta):\n A=1\n B=2\n')
    info=inspect_source(worker,tmp_path,source)
    assert not info['E']['members']['A'].get('class-read-only?')
    namespace={};exec(source,namespace)
    old=namespace['E'].B;namespace['E'].A=old
    assert namespace['E'].A is old


@pytest.mark.parametrize('mutation',[
    'B.method=lambda self:1',
    'setattr(B,"method",lambda self:1)',
    'def register(cls): cls.method=lambda self:1\nregister(B)',
    'Alias=B\nAlias.method=lambda self:1',
    'classes=[B]\nclasses[0].method=lambda self:1',
])
def test_visible_class_mutation_or_escape_prevents_absence_proof(worker,tmp_path,mutation):
    source='class B: pass\n'+mutation+'\n'
    info=inspect_source(worker,tmp_path,source)['B']
    assert not info['class-members-complete?']
    assert not info['instance-members-complete?']
    namespace={};exec(source,namespace)
    assert namespace['B']().method()==1


def test_unmodeled_class_statement_prevents_absence_proof(worker,tmp_path):
    info=inspect_source(worker,tmp_path,'class C:\n match 1:\n  case 1:\n   method=lambda self:1\n')['C']
    assert not info['class-members-complete?']


def test_instance_consumer_can_reach_and_mutate_its_class(worker,tmp_path):
    source=('class B: pass\ndef consume(value):\n'
        ' type(value).__class_getitem__=classmethod(lambda cls,key:key)\nconsume(B())\n')
    info=inspect_source(worker,tmp_path,source)['B']
    assert not info['class-members-complete?']
    namespace={};exec(source,namespace);assert namespace['B'][3]==3


def test_returned_class_escape_prevents_absence_proof(worker,tmp_path):
    source=('class B: pass\ndef factory() -> type[B]:return B\n'
        'def register(cls):cls.method=lambda self:1\nregister(factory())\n')
    info=inspect_source(worker,tmp_path,source)['B']
    assert not info['instance-members-complete?']
    namespace={};exec(source,namespace);assert namespace['B']().method()==1


@pytest.mark.parametrize('mutation',[
    'B.__class_getitem__=classmethod(lambda cls,key:key)',
    'setattr(B,"__class_getitem__",classmethod(lambda cls,key:key))',
    'def register(cls):cls.__class_getitem__=classmethod(lambda cls,key:key)\nregister(B)',
    'def factory() -> type[B]:return B\n'
    'def register(cls):cls.__class_getitem__=classmethod(lambda cls,key:key)\nregister(factory())',
])
def test_class_subscription_mutation_disables_closed_class_proof(worker,tmp_path,mutation):
    source='class B: pass\n'+mutation+'\n'
    info=inspect_source(worker,tmp_path,source)['B']
    assert not info['class-members-complete?']
    namespace={};exec(source,namespace)
    assert namespace['B'][3]==3


def test_annotated_factory_class_may_return_subclass_with_new_slots(worker,tmp_path):
    source=('class Base: pass\nclass Child(Base):\n'
        ' def __class_getitem__(cls,key:int)->str:return str(key)\n'
        ' def __index__(self)->int:return 0\n'
        'def factory()->type[Base]:return Child\n'
        'def instance()->Base:return Child()\n')
    info=inspect_source(worker,tmp_path,source)
    assert info['Base']['class-members-complete?']
    assert info['factory']['kind']=='function'
    assert info['factory']['type-path']==['type']
    assert info['factory']['type-arguments'][0]['type-path']==['Base']
    assert 'class-members-complete?' not in info['factory']
    assert 'instance-members-complete?' not in info['instance']
    namespace={};exec(source,namespace)
    assert namespace['factory']()[3]=='3'
    assert [42][namespace['instance']()]==42


@pytest.mark.parametrize('inherited',[False,True])
def test_custom_instance_dispatch_overrides_field_descriptor_and_method_contracts(worker,tmp_path,inherited):
    source=('class Descriptor:\n def __get__(self,obj,owner)->int:return 1\n'
        'class Base:\n number:int=1\n value=Descriptor()\n'
        ' @property\n def readonly(self)->int:return 1\n'
        ' def method(self,value:int)->int:return value\n'
        ' def __getattribute__(self,name):\n'
        '  if name in ("number","value"):return "text"\n'
        '  if name=="method":return lambda value:"text"\n'
        '  return object.__getattribute__(self,name)\n'
        ' def __setattr__(self,name,value):self.__dict__[name]=value\n'
        + ('class C(Base):pass\n' if inherited else 'C=Base\n'))
    info=inspect_source(worker,tmp_path,source)['C']
    for name in ['number','value','readonly','method']:
        field=info['members'][name]
        assert field['instance-read-type']=={'type-any?':True}
        assert field['instance-assignment-type']=={'type-any?':True}
        assert not field.get('instance-read-only?')
        assert field.get('class-read-type') == (worker.runtime_type(property) if name=='readonly' else None)
    assert info['members']['value']['descriptor-get']['type-path']==['int']
    namespace={};exec(source,namespace);obj=namespace['C']()
    assert obj.value==obj.number==obj.method('text')=='text'
    obj.readonly='accepted';assert obj.__dict__['readonly']=='accepted'
    native=worker.safe_member('C',namespace['C'])
    assert native['members']['value']['instance-read-type']=={'type-any?':True}
    assert native['members']['readonly']['instance-assignment-type']=={'type-any?':True}


@pytest.mark.parametrize('inherited',[False,True])
def test_custom_metaclass_dispatch_retains_instance_contracts(worker,tmp_path,inherited):
    source=('class Descriptor:\n def __get__(self,obj,owner)->int:return 1\n'
        'class Meta(type):\n'
        ' def __getattribute__(cls,name):\n'
        '  if name in ("number","value"):return "text"\n'
        '  if name=="method":return lambda value:"text"\n'
        '  return type.__getattribute__(cls,name)\n'
        ' def __setattr__(cls,name,value):type.__setattr__(cls,name,value)\n'
        + ('class ChildMeta(Meta):pass\n' if inherited else '')+
        'class C(metaclass='+('ChildMeta' if inherited else 'Meta')+'):\n'
        ' number:int=1\n value=Descriptor()\n'
        ' def method(self,value:int)->int:return value\n')
    info=inspect_source(worker,tmp_path,source)['C']
    for name in ['number','value','method']:
        field=info['members'][name]
        assert field['class-read-type']=={'type-any?':True}
        assert field['class-assignment-type']=={'type-any?':True}
        assert 'instance-read-type' not in field
    namespace={};exec(source,namespace);C=namespace['C']
    assert C.value==C.number==C.method('text')=='text'
    assert C().value==1
    C.number='text';assert type.__getattribute__(C,'number')=='text'
    native=worker.safe_member('C',C)
    assert native['members']['value']['class-read-type']=={'type-any?':True}
    assert 'instance-read-type' not in native['members']['value']


def test_builtin_native_attribute_slots_do_not_erase_known_field_contracts(worker):
    info=worker.safe_member('int',int)
    assert 'instance-read-type' not in info['members']['real']


def test_class_property_reads_expose_descriptor_while_instances_keep_getter_type(worker,tmp_path):
    source=('class C:\n @property\n def value(self)->int:return 1\n'
        ' @value.setter\n def value(self,value:str)->None:pass\n'
        ' @value.getter\n def value(self)->float:return 1.0\n')
    info=inspect_source(worker,tmp_path,source)['C']['members']['value']
    assert info['class-read-type']==worker.runtime_type(property)
    assert info['type-path']==['float']
    assert info['instance-assignment-type']['type-path']==['str']
    namespace={};exec(source,namespace);C=namespace['C']
    assert type(C.value) is property and type(C().value) is float
    native=worker.safe_member('C',C)['members']['value']
    assert native['class-read-type']==worker.runtime_type(property)
    assert native['type-path']==['float']
