"""Mutable constructor slots cannot supply new generic specialization proof."""
import importlib
import importlib.util
from pathlib import Path

import basilisp_tools
import pytest
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp

HEADER = '''from typing import Generic, TypeVar
from types import SimpleNamespace
T=TypeVar('T')
class C(Generic[T]):
    value:T
    def __init__(self,value:T): self.value=value
def strings(value:str)->None: pass
def integers(value:int)->None: pass
'''
MUTATOR = "lambda self,value:setattr(self,'value','ok')"
CASES = [
    ('direct', 'C.__init__='+MUTATOR+'\nx=C(1)\n'),
    ('alias', 'Alias=C\nAlias.__init__='+MUTATOR+'\nx=C(1)\n'),
    ('walrus', '(Alias:=C)\nAlias.__init__='+MUTATOR+'\nx=C(1)\n'),
    ('container', 'aliases=[C]\naliases[0].__init__='+MUTATOR+'\nx=C(1)\n'),
    ('setattr', 'setattr(C,"__init__",'+MUTATOR+')\nx=C(1)\n'),
    ('escape', 'def mutate(cls): cls.__init__='+MUTATOR+'\nmutate(C)\nx=C(1)\n'),
    ('reflection', 'globals()["C"].__init__='+MUTATOR+'\nx=C(1)\n'),
    ('inherited', 'class D(C[T]): pass\nC.__init__='+MUTATOR+'\nx=D(1)\n'),
    ('inherited_alias', 'Alias=C\nclass D(Alias[T]): pass\nAlias.__init__='+MUTATOR+'\nx=D(1)\n'),
    ('new', 'C.__new__=staticmethod(lambda cls,value:SimpleNamespace(value="ok"))\nx=C(1)\n'),
    ('inherited_new', 'class D(C[T]): pass\nC.__new__=staticmethod(lambda cls,value:SimpleNamespace(value="ok"))\nx=D(1)\n'),
    ('meta', 'class Meta(type): pass\nclass D(C[T],metaclass=Meta): pass\nMeta.__call__=lambda cls,value:SimpleNamespace(value="ok")\nx=D(1)\n'),
    ('inherited_meta', 'class Meta(type): pass\nclass D(C[T],metaclass=Meta): pass\nclass E(D[T]): pass\nMeta.__call__=lambda cls,value:SimpleNamespace(value="ok")\nx=E(1)\n'),
    ('class_subscription', 'class D(C[T]):\n def __class_getitem__(cls, item):\n  cls.__init__='+MUTATOR+'\n  return cls\nseen=D[int](0)\nx=D(1)\n'),
    ('metaclass_subscription', 'class Meta(type):\n def __getitem__(cls, item):\n  cls.__init__='+MUTATOR+'\n  return cls\nclass D(C[T],metaclass=Meta): pass\nseen=D[int](0)\nx=D(1)\n'),
    ('annotation_hook', 'import sys\ndef install():\n sys._getframe(1).f_locals["__init__"]='+MUTATOR+'\n return int\nclass D(C[T]):\n def method(self, value:install()): pass\nx=D(1)\n'),
    ('descriptor', 'class Rewrite:\n def __set_name__(self, owner, name): owner.__init__='+MUTATOR+'\nclass D(C[T]):\n trigger=Rewrite()\nx=D(1)\n'),
]

CASES += [('instance_escape', 'def mutate(obj): type(obj).__init__ = lambda self, value: setattr(self, "value", "ok")\nmutate(C(0))\nx = C(1)\n'), ('instance_alias_escape', 'first = C(0)\ndef mutate(obj): type(obj).__init__ = lambda self, value: setattr(self, "value", "ok")\nmutate(first)\nx = C(1)\n'), ('instance_class_slot', 'C(0).__class__.__init__ = lambda self, value: setattr(self, "value", "ok")\nx = C(1)\n'), ('instance_alias_class_slot', 'first = C(0)\nfirst.__class__.__init__ = lambda self, value: setattr(self, "value", "ok")\nx = C(1)\n'), ('namespace_alias', 'namespace = globals\nnamespace()["C"].__init__ = lambda self, value: setattr(self, "value", "ok")\nx = C(1)\n'), ('builtin_namespace_getattr', 'import builtins\ngetattr(builtins, "globals")()["C"].__init__ = lambda self, value: setattr(self, "value", "ok")\nx = C(1)\n'), ('builtin_namespace_dict', 'import builtins\nbuiltins.__dict__["globals"]()["C"].__init__ = lambda self, value: setattr(self, "value", "ok")\nx = C(1)\n')]


@pytest.fixture(scope='module')
def worker():
    spec=importlib.util.spec_from_file_location('_constructor_mutation',Path(__file__).resolve().parents[1]/'src/basilisp_tools/_inspect.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def contracts(tmp_path_factory):
    root=tmp_path_factory.mktemp('constructor_mutation')
    for name,source in CASES:
        (root/(name+'.py')).write_text(HEADER+source)
    analyzer=importlib.import_module('basilisp_tools.analyzer')
    bridge=importlib.import_module('basilisp_tools.python')
    cache=bridge.create_cache()
    options=to_lisp({'python-options':{'python-paths':[str(root)],'python-inspection?':False,'python-cache':cache}})
    try:yield root,analyzer,options
    finally:bridge.stop_cache__BANG__(cache)


@pytest.mark.parametrize('name,source',CASES)
def test_visible_constructor_mutation_cannot_invent_invariant_argument(worker,contracts,name,source):
    root,analyzer,options=contracts
    info=worker.StaticInspector([str(root)],[]).module(name)['members']['x']
    assert info.get('type-arguments') != [{'type-module':'builtins','type-path':['int']}], info
    result=analyzer.analyze('(ns constructor-mutation (:import ['+name+' :as m])) (m/strings (.-value m/x))',options)
    assert list(result[k('findings')])==[]
    native={};exec(HEADER+source,native)
    assert native['x'].value=='ok'
    native['strings'](native['x'].value)


@pytest.mark.parametrize('source', ['x=C(1)\n','class D(C[T]): pass\nx=D(1)\n','Alias=C\nx=Alias(1)\n'])
def test_unmodified_local_constructor_retains_inference(worker,tmp_path,source):
    (tmp_path/'fixture.py').write_text(HEADER+source)
    info=worker.StaticInspector([str(tmp_path)],[]).module('fixture')['members']['x']
    assert info['type-arguments']==[{'type-module':'builtins','type-path':['int']}]


def test_imported_constructor_remains_unknown_until_identity_is_proved(worker,tmp_path):
    (tmp_path/'external.py').write_text(HEADER)
    (tmp_path/'fixture.py').write_text('from external import C\nx=C(1)\n')
    info=worker.StaticInspector([str(tmp_path)],[]).module('fixture')['members']['x']
    assert all(argument.get('typevar') or argument.get('type-any?') for argument in info.get('type-arguments', []))


def test_unmodified_constructor_retains_negative_consumers(contracts):
    root,analyzer,options=contracts
    (root/'unchanged.py').write_text(HEADER+'x=C(1)\n')
    result=analyzer.analyze('(ns constructor-mutation-negative (:import [unchanged :as m])) (m/strings (.-value m/x))',options)
    assert any(f[k('type')]==k('type-mismatch') for f in result[k('findings')])


def test_no_argument_defaults_retain_existing_specialization(worker,tmp_path):
    source = "from typing_extensions import Generic,TypeVar\nT=TypeVar('T',default=str)\nclass C(Generic[T]):pass\nC.extra=1\nx=C()\n"
    (tmp_path/'fixture.py').write_text(source)
    info=worker.StaticInspector([str(tmp_path)],[]).module('fixture')['members']['x']
    assert info['type-arguments']==[{'type-module':'builtins','type-path':['str']}]


def test_nongeneric_constructor_skips_proof(worker,tmp_path,monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError('nongeneric calls do not need type-parameter inference proof')
    monkeypatch.setattr(worker.StaticModule,'constructor_inference_unchanged',unexpected)
    (tmp_path/'fixture.py').write_text('class C:\n def __init__(self,value:int):self.value=value\nx=C(1)\n')
    info=worker.StaticInspector([str(tmp_path)],[]).module('fixture')['members']['x']
    assert info['type-path']==['C']


def test_repeated_construction_reuses_local_proof(worker,tmp_path,monkeypatch):
    calls=[]
    original=worker.StaticModule.prove_constructor_inference
    def counted(self,declaration,seen=()):
        calls.append(tuple(declaration['type-path']))
        return original(self,declaration,seen)
    monkeypatch.setattr(worker.StaticModule,'prove_constructor_inference',counted)
    (tmp_path/'fixture.py').write_text(HEADER+'x=C(1)\ny=C(2)\nz=C(3)\n')
    info=worker.StaticInspector([str(tmp_path)],[]).module('fixture')['members']
    assert info['x']['type-arguments']==info['y']['type-arguments']==info['z']['type-arguments']
    assert calls.count(('C',))==1


def test_mutation_index_rechecks_redefined_class(worker,tmp_path):
    import ast
    source=HEADER+'''first=C(0)
first.change()
class C(Generic[T]):
 value:T
 def __init__(self,value:T):self.value=value
 def change(self):type(self).__init__=lambda self,value:setattr(self,"value","ok")
second=C(0)
second.change()
x=C(1)
'''
    source=source.replace('def __init__(self,value:T): self.value=value','def __init__(self,value:T): self.value=value\n    def change(self):pass')
    (tmp_path/'fixture.py').write_text(source)
    native={};exec(source,native)
    assert native['x'].value=='ok'
    inspector=worker.StaticInspector([str(tmp_path)],[])
    tree=ast.parse(source)
    module=worker.StaticModule(inspector,'fixture',tmp_path/'fixture.py',tree)
    definitions=[node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='C']
    module.classes[('C',)]=definitions[0]
    assert ('C',) not in module.constructor_mutable_classes()
    module.classes[('C',)]=definitions[1]
    assert ('C',) in module.constructor_mutable_classes()
    metadata=inspector.module('fixture')['members']['x']
    assert metadata.get('type-arguments') != [{'type-module':'builtins','type-path':['int']}]
