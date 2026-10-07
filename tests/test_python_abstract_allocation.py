"""Native allocation proof is narrower than static abstract-class conventions."""
import importlib
import importlib.util
from pathlib import Path
import pytest
import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp
SOURCE = '''from abc import ABC, ABCMeta, abstractmethod, abstractproperty
import abc
from typing import Protocol
class Abstract(ABC):
 @abstractmethod
 def method(self):return 1
class Inherited(Abstract):pass
class Concrete(Abstract):
 def method(self):return 1
class Plain:
 @abstractmethod
 def method(self):return 1
class Empty(ABC):pass
class EmptyMeta(metaclass=ABCMeta):pass
class AbstractMeta(metaclass=ABCMeta):
 @abstractmethod
 def method(self):return 1
class Meta(ABCMeta):pass
class MetaChild(metaclass=Meta):
 @abstractmethod
 def method(self):return 1
class ForeignNew(Abstract):
 def __new__(cls):return 42
class ForeignChild(ForeignNew):pass
class BypassMeta(ABCMeta):
 def __call__(cls):return 42
class Bypass(Abstract,metaclass=BypassMeta):pass
class CreationMeta(ABCMeta):
 def __new__(meta,name,bases,namespace):
  cls=super().__new__(meta,name,bases,namespace)
  cls.__abstractmethods__=frozenset()
  return cls
class CreationBypass(Abstract,metaclass=CreationMeta):pass
class Proto(Protocol):
 @abstractmethod
 def method(self):return 1
class ProtoChild(Proto):pass
class Attributes(Protocol):value:int
class AttributeChild(Attributes):pass
class Properties(ABC):
 @property
 @abstractmethod
 def value(self):return 1
class LegacyProperty(ABC):
 @abstractproperty
 def value(self):return 1
class ConcreteProperty(Properties):
 @property
 def value(self):return 1
class SlotMixin:__slots__=('value',)
class StillAbstract(Properties,SlotMixin):pass
class SlotConcrete(SlotMixin,Properties):pass
class StillChild(StillAbstract):pass
class MethodMixin:
 def method(self):return 1
class AbstractFirst(Abstract,MethodMixin):pass
class ConcreteFirst(MethodMixin,Abstract):pass
class AbstractInit(ABC):
 @abstractmethod
 def __init__(self):pass
class Wrappers(ABC):
 @classmethod
 @abstractmethod
 def first(cls):return 1
 @staticmethod
 @abstractmethod
 def second():return 2
Alias=Abstract
class Nested:
 class Inner(ABC):
  @abstractmethod
  def method(self):return 1
class LocalWrapper(ABC):
 def property(value):return lambda self:1
 @property
 @abstractmethod
 def method(self):return 1
'''
ABSTRACT = {'Abstract', 'Inherited', 'AbstractMeta', 'MetaChild', 'ProtoChild', 'Properties', 'LegacyProperty', 'StillAbstract', 'StillChild', 'AbstractFirst', 'AbstractInit', 'Wrappers', 'Alias', 'Nested.Inner'}
CONCRETE = {'Concrete', 'Plain', 'Empty', 'EmptyMeta', 'ForeignNew', 'ForeignChild', 'Bypass', 'CreationBypass', 'AttributeChild', 'ConcreteProperty', 'SlotConcrete', 'ConcreteFirst', 'LocalWrapper'}

@pytest.fixture(scope='module')
def worker():
    p = Path(__file__).resolve().parents[1] / 'src/basilisp_tools/_inspect.py'
    spec = importlib.util.spec_from_file_location('_abstract_worker', p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

@pytest.fixture(scope='module')
def contracts(worker, tmp_path_factory):
    root = tmp_path_factory.mktemp('abstract_allocation')
    (root / 'abstract_contracts.py').write_text(SOURCE)
    info = worker.StaticInspector([str(root)], []).module('abstract_contracts')
    analyzer = importlib.import_module('basilisp_tools.analyzer')
    bridge = importlib.import_module('basilisp_tools.python')
    cache = bridge.create_cache()
    opts = to_lisp({'python-options': {'python-paths': [str(root)], 'python-inspection?': False, 'python-cache': cache}})
    try:
        yield (info, analyzer, opts)
    finally:
        bridge.stop_cache__BANG__(cache)

@pytest.mark.parametrize('name', sorted(ABSTRACT | CONCRETE))
def test_metadata_and_diagnostic(contracts, name):
    info, analyzer, opts = contracts
    member = info
    for part in name.split('.'):
        member = member['members'][part]
    assert bool(member.get('abstract-allocation-members')) == (name in ABSTRACT), (name, member)
    result = analyzer.analyze('(ns abstract-contracts (:import [abstract_contracts :as m]))\n(m/' + name + ')', opts)
    assert [f[k('type')] for f in result[k('findings')]] == ([k('type-mismatch')] if name in ABSTRACT else [])

@pytest.mark.parametrize('name', sorted(ABSTRACT | CONCRETE))
def test_native_allocation(name):
    values = {}
    exec(SOURCE, values)
    cls = values[name.split('.')[0]]
    for part in name.split('.')[1:]:
        cls = getattr(cls, part)
    if name in ABSTRACT:
        with pytest.raises(TypeError, match='abstract'):
            cls()
    else:
        cls()

@pytest.mark.parametrize('source', ['from abc import ABC,abstractmethod\ndef abstractmethod(f):return f\nclass A(ABC):\n @abstractmethod\n def method(self):pass\n', 'from abc import ABC\nclass Fake:\n @staticmethod\n def abstractmethod(f):return f\nclass A(ABC):\n @Fake.abstractmethod\n def method(self):pass\n', 'from abc import ABC,abstractmethod\nclass A(ABC):\n @abstractmethod\n def method(self):pass\nA.__abstractmethods__=frozenset()\n', 'from abc import ABC,abstractmethod\ndef replace(cls):return int\n@replace\nclass A(ABC):\n @abstractmethod\n def method(self):pass\n'])
def test_unknown_or_replaced_identity_has_no_proof(worker, tmp_path, source):
    (tmp_path / 'guard.py').write_text(source)
    info = worker.StaticInspector([str(tmp_path)], []).module('guard')['members']['A']
    assert not info.get('abstract-allocation-members')
    values = {}
    exec(source, values)
    values['A']()

@pytest.mark.parametrize('name,expected', [('Base', True), ('PrivateChild', True), ('ExactOverride', False)])
def test_private_names_follow_native_mangling(worker, tmp_path, name, expected):
    source = '''from abc import ABC,abstractmethod
class Base(ABC):
 @abstractmethod
 def __method(self):return 1
class PrivateChild(Base):
 def __method(self):return 2
class ExactOverride(Base):
 def _Base__method(self):return 3
'''
    (tmp_path / 'mangled.py').write_text(source)
    info = worker.StaticInspector([str(tmp_path)], []).module('mangled')['members'][name]
    assert bool(info.get('abstract-allocation-members')) == expected
    values = {}
    exec(source, values)
    if expected:
        with pytest.raises(TypeError, match='abstract'):
            values[name]()
    else:
        values[name]()

@pytest.mark.parametrize('mutation', ['abc.ABC.__init_subclass__=replacement', 'abc.ABCMeta.__new__=replacement', 'abc.ABCMeta.__call__=replacement', "setattr(abc.ABC,'__init_subclass__',replacement)", 'alias=abc.ABC\nalias.__init_subclass__=replacement'])
def test_standard_object_mutation_erases_proof(worker, tmp_path, mutation):
    source = 'import abc\ndef replacement(*args,**kwargs):pass\n' + mutation + '\nclass A(abc.ABC):\n @abc.abstractmethod\n def method(self):pass\n'
    (tmp_path / 'mutated.py').write_text(source)
    info = worker.StaticInspector([str(tmp_path)], []).module('mutated')['members']['A']
    assert not info.get('abstract-allocation-members')

def test_custom_metaclass_mro_cannot_establish_allocation(worker, tmp_path):
    source = '''from abc import ABC,ABCMeta,abstractmethod
class Meta(ABCMeta):
 def mro(cls):return [cls,object]
class Base(ABC):
 @abstractmethod
 def method(self):pass
class A(Base,metaclass=Meta):pass
'''
    (tmp_path / 'custom_mro.py').write_text(source)
    info = worker.StaticInspector([str(tmp_path)], []).module('custom_mro')['members']['A']
    assert not info.get('abstract-allocation-members')
    values = {}
    exec(source, values)
    values['A']()

@pytest.mark.parametrize('name', sorted(ABSTRACT | CONCRETE))
def test_runtime_metadata_uses_actual_flags_and_dispatch(worker, name):
    values = {}
    exec(SOURCE, values)
    cls = values[name.split('.')[0]]
    for part in name.split('.')[1:]:
        cls = getattr(cls, part)
    assert bool(worker.runtime_abstract_allocation_members(cls)) == (name in ABSTRACT)
    assert bool(worker.runtime_member(name, cls, depth=0).get('abstract-allocation-members')) == (name in ABSTRACT)

@pytest.mark.parametrize('mutation', ['abc._abc_init=replacement', 'abc.abstractmethod=replacement', 'alias=abc\nalias._abc_init=replacement'])
def test_abc_namespace_mutation_erases_proof(worker, tmp_path, mutation):
    source = 'import abc\ndef replacement(*args,**kwargs):pass\n' + mutation + '\nclass A(abc.ABC):\n @abc.abstractmethod\n def method(self):pass\n'
    (tmp_path / 'namespace_mutated.py').write_text(source)
    info = worker.StaticInspector([str(tmp_path)], []).module('namespace_mutated')['members']['A']
    assert not info.get('abstract-allocation-members')

@pytest.mark.parametrize('header', ['class A(base()):', 'class A(Base,metaclass=meta()):'])
def test_opaque_class_header_cannot_prove_identity(worker, tmp_path, header):
    source = '''from abc import ABC,ABCMeta,abstractmethod
class Base(ABC):
 @abstractmethod
 def method(self):pass
def base()->type[Base]:return object
def meta()->type[ABCMeta]:return type
''' + header + '\n pass\n'
    (tmp_path / 'opaque_header.py').write_text(source)
    info = worker.StaticInspector([str(tmp_path)], []).module('opaque_header')['members']['A']
    assert not info.get('abstract-allocation-members')

@pytest.mark.parametrize('mutation', ['abc.ABCMeta.__call__=replacement', "setattr(abc.ABCMeta,'__call__',replacement)", 'meta_alias=abc.ABCMeta\nmeta_alias.__call__=replacement'])
def test_post_declaration_metaclass_mutation_erases_proof(worker, tmp_path, mutation):
    source = 'import abc\nclass A(abc.ABC):\n @abc.abstractmethod\n def method(self):pass\ndef replacement(*args,**kwargs):return 42\n' + mutation + '\n'
    (tmp_path / 'late_mutated.py').write_text(source)
    info = worker.StaticInspector([str(tmp_path)], []).module('late_mutated')['members']['A']
    assert not info.get('abstract-allocation-members')

@pytest.mark.parametrize('escape', ['mutate(value=abc.ABCMeta)', 'mutate([abc.ABCMeta])', 'holder={"meta":abc.ABCMeta}'])
def test_nested_standard_factory_escape_erases_proof(worker, tmp_path, escape):
    source = 'import abc\nclass A(abc.ABC):\n @abc.abstractmethod\n def method(self):pass\n' + escape + '\n'
    (tmp_path / 'escaped.py').write_text(source)
    info = worker.StaticInspector([str(tmp_path)], []).module('escaped')['members']['A']
    assert not info.get('abstract-allocation-members')

@pytest.mark.parametrize('mutation', ["globals()['C'].__abstractmethods__=frozenset()", "locals()['C'].__abstractmethods__=frozenset()", "vars()['C'].__abstractmethods__=frozenset()", 'exec("C.__abstractmethods__=frozenset()")', 'eval("setattr(C,\'__abstractmethods__\',frozenset())")', "from builtins import globals as namespace\nnamespace()['C'].__abstractmethods__=frozenset()"])
def test_reflective_namespace_mutation_erases_proof(worker, tmp_path, mutation):
    source = 'from abc import ABC,abstractmethod\nclass C(ABC):\n @abstractmethod\n def method(self):pass\n' + mutation + '\n'
    (tmp_path / 'reflective.py').write_text(source)
    info = worker.StaticInspector([str(tmp_path)], []).module('reflective')['members']['C']
    assert not info.get('abstract-allocation-members')
    values = {}
    exec(source, values)
    values['C']()

def test_rebound_alias_cannot_hide_class_mutation(worker, tmp_path):
    source = '''from abc import ABC,abstractmethod
class C(ABC):
 @abstractmethod
 def method(self):pass
alias=C
alias.__abstractmethods__=frozenset()
alias=None
'''
    (tmp_path / 'alias_lifetime.py').write_text(source)
    info = worker.StaticInspector([str(tmp_path)], []).module('alias_lifetime')['members']['C']
    assert not info.get('abstract-allocation-members')
    values = {}
    exec(source, values)
    values['C']()


@pytest.mark.parametrize('mutation', [
    'def expose():return C\nexpose().__abstractmethods__=frozenset()',
    'for exposed in [C]:\n exposed.__abstractmethods__=frozenset()',
])
def test_indirect_class_escape_erases_proof(worker, tmp_path, mutation):
    source = 'from abc import ABC,abstractmethod\nclass C(ABC):\n @abstractmethod\n def method(self):pass\n' + mutation + '\n'
    (tmp_path / 'indirect.py').write_text(source)
    info = worker.StaticInspector([str(tmp_path)], []).module('indirect')['members']['C']
    assert not info.get('abstract-allocation-members')
    values = {}
    exec(source, values)
    values['C']()


@pytest.mark.parametrize('aliasing', [
    '(alias := C)\nalias.__abstractmethods__ = frozenset()',
    '(first, second) = (C, None)\nfirst.__abstractmethods__ = frozenset()',
    'first, *rest = [C, None]\nfirst.__abstractmethods__ = frozenset()',
    'match C:\n case alias:\n  alias.__abstractmethods__ = frozenset()',
])
def test_class_binding_forms_preserve_mutation_safety(worker, tmp_path, aliasing):
    source = 'from abc import ABC, abstractmethod\nclass C(ABC):\n @abstractmethod\n def method(self):pass\n' + aliasing + '\n'
    assert_native_constructible_without_static_rejection(worker, tmp_path, source)


def assert_native_constructible_without_static_rejection(worker, tmp_path, source):
    (tmp_path / 'peer_guard.py').write_text(source)
    info = worker.StaticInspector([str(tmp_path)], []).module('peer_guard')['members']['C']
    assert not info.get('abstract-allocation-members')
    values = {}
    exec(source, values)
    values['C']()
    analyzer = importlib.import_module('basilisp_tools.analyzer')
    bridge = importlib.import_module('basilisp_tools.python')
    cache = bridge.create_cache()
    try:
        options = to_lisp({'python-options': {'python-paths': [str(tmp_path)],
                          'python-inspection?': False, 'python-cache': cache}})
        result = analyzer.analyze('(ns peer-guard (:import [peer_guard :as m]))\n(m/C)', options)
        assert list(result[k('findings')]) == []
    finally:
        bridge.stop_cache__BANG__(cache)


@pytest.mark.parametrize('wrapper', ['classmethod', 'staticmethod', 'property'])
@pytest.mark.parametrize('namespace', ['builtins', '__builtins__', '__import__', 'importlib'])
def test_standard_builtin_wrapper_mutation_has_no_proof(worker, tmp_path, wrapper, namespace):
    access = {
        'builtins': 'builtins.' + wrapper,
        '__builtins__': '__builtins__[' + repr(wrapper) + ']',
        '__import__': '__import__("builtins").' + wrapper,
        'importlib': 'importlib.import_module("builtins").' + wrapper,
    }[namespace]
    source = f'''import builtins
import importlib
from abc import ABC, abstractmethod
original = {access}
try:
 {access} = lambda f: (setattr(f, '__isabstractmethod__', False) or f)
 class C(ABC):
  @{wrapper}
  @abstractmethod
  def method(self):pass
finally:
 {access} = original
'''
    # The class is nested in a try solely to guarantee native restoration.
    # Static fixture construction can use the same declaration without control
    # flow; native execution retains the restoration boundary below.
    plain = source.replace('try:\n', '').replace('finally:\n', '')
    plain = '\n'.join(line[1:] if line.startswith(' ') else line for line in plain.splitlines()) + '\n'
    (tmp_path / 'peer_guard.py').write_text(plain)
    info = worker.StaticInspector([str(tmp_path)], []).module('peer_guard')['members']['C']
    assert not info.get('abstract-allocation-members')
    values = {}
    exec(source, values)
    values['C']()


def test_builtin_object_binding_cannot_prove_default_allocation(worker, tmp_path):
    source = '''import builtins
from abc import ABCMeta, abstractmethod
class Replacement:
 def __new__(cls):return 42
original = builtins.object
builtins.object = Replacement
class C(object, metaclass=ABCMeta):
 @abstractmethod
 def method(self):pass
builtins.object = original
'''
    assert_native_constructible_without_static_rejection(worker, tmp_path, source)


def test_metaclass_method_decorator_can_install_creation_hook(worker, tmp_path):
    source = '''from abc import ABCMeta, abstractmethod
class Mutator:
 def __init__(self, fn):pass
 def __set_name__(self, owner, name):owner.__call__ = lambda cls:42
class Meta(ABCMeta):
 @Mutator
 def helper(self):pass
class C(metaclass=Meta):
 @abstractmethod
 def method(self):pass
'''
    assert_native_constructible_without_static_rejection(worker, tmp_path, source)
