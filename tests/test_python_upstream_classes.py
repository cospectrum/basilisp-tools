"""Native-backed class metadata cases shared by the upstream typing corpora."""

import importlib.util
import ast
from pathlib import Path
import sys
import types

import pytest


@pytest.fixture
def worker():
    path = Path(__file__).resolve().parents[1] / "src/basilisp_tools/_inspect.py"
    spec = importlib.util.spec_from_file_location("_blt_upstream_classes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inspect_source(worker, tmp_path, source):
    (tmp_path / "fixture.py").write_text(source)
    return worker.StaticInspector([str(tmp_path)], []).module("fixture")["members"]


@pytest.mark.parametrize(('imports', 'header'), [
    ('from dataclasses import dataclass', '@dataclass\nclass C:'),
    ('from typing import NamedTuple', 'class C(NamedTuple):'),
    ('from typing import TypedDict', 'class C(TypedDict):'),
])
def test_conditional_fields_follow_selected_interpreter(worker, tmp_path, monkeypatch, imports, header):
    source = (f'import sys as selected\n{imports}\n{header}\n x: int\n'
              ' if selected.version_info >= (3, 12):\n  y: str\n'
              ' if selected.version_info >= (4, 0):\n  future: bytes\n')
    info = inspect_source(worker, tmp_path, source)['C']
    expected = ['x', 'y'] if sys.version_info >= (3, 12) else ['x']
    assert [p['name'] for p in info['parameters']] == expected
    module = types.ModuleType('fixture')
    monkeypatch.setitem(sys.modules, 'fixture', module)
    exec(source, vars(module))
    assert list(module.C.__annotations__) == expected
    module.C(**dict(zip(expected, [1, 'value'])))


def test_unknown_field_conditions_do_not_invent_an_exhaustive_constructor(worker, tmp_path):
    result = inspect_source(worker, tmp_path,
        'from typing import NamedTuple\ndef condition(): return True\n'
        'class C(NamedTuple):\n x: int\n if condition():\n  y: int\n')
    assert 'parameters' not in result['C']
    assert result['C']['signature-unknown?']


def test_final_literal_namedtuple_fields_and_aliases(worker, tmp_path):
    source = ('from typing import Final, NamedTuple as NT\n'
              'X: Final = "x"\nY: Final[str] = "y"\nN = NT("N", [(X, int), (Y, str)])\n')
    info = inspect_source(worker, tmp_path, source)['N']
    assert [p['name'] for p in info['parameters']] == ['x', 'y']
    assert [p['type-path'] for p in info['parameters']] == [['int'], ['str']]
    namespace = {}
    exec(source, namespace)
    assert namespace['N'](x=1, y='a') == (1, 'a')


def test_implicit_class_methods_and_static_new_binding(worker, tmp_path):
    source = ('class C:\n'
              ' def __class_getitem__(cls, item: int) -> str: return str(item)\n'
              ' def __init_subclass__(cls, *, flag=False): pass\n'
              ' def __new__(cls, value: int): return object.__new__(cls)\n')
    info = inspect_source(worker, tmp_path, source)['C']
    for name, expected in [('__class_getitem__', ['item']), ('__init_subclass__', ['flag']), ('__new__', ['cls', 'value'])]:
        assert [p['name'] for p in info['members'][name]['parameters']] == expected
        assert not info['members'][name]['instance-method?']
    namespace = {}
    exec(source, namespace)
    assert namespace['C'][3] == '3'
    assert namespace['C'].__class_getitem__(4) == '4'
    assert isinstance(namespace['C'](1).__new__(namespace['C'], 2), namespace['C'])


def test_nominal_proof_excludes_custom_and_structural_classes(worker, tmp_path):
    source = ('from typing import Generic, TypeVar, Protocol, TypedDict\nfrom abc import ABC\n'
              'T = TypeVar("T")\nclass A: pass\nclass B: pass\nclass C(A): pass\n'
              'class Box(Generic[T]): pass\nclass Meta(type): pass\nclass Custom(metaclass=Meta): pass\n'
              'class Abstract(ABC): pass\nclass P(Protocol): pass\nclass TD(TypedDict):\n x: int\n')
    result = inspect_source(worker, tmp_path, source)
    assert all(result[name]['nominal?'] for name in ('A', 'B', 'C', 'Box'))
    assert not any(result[name]['nominal?'] for name in ('Custom', 'Abstract', 'P', 'TD'))
    namespace = {}
    exec(source, namespace)
    assert not isinstance(namespace['A'](), namespace['B'])
    assert isinstance(namespace['C'](), namespace['A'])


def test_builtin_class_values_remain_known_without_dependency_execution(worker, tmp_path):
    # A same-named local file cannot replace the interpreter's builtin module.
    (tmp_path / 'builtins.py').write_text('raise AssertionError("must not execute")\n')
    info = worker.inspect_module('builtins', [str(tmp_path)], False)
    integer = info['members']['int']
    assert integer['kind'] == 'class'
    assert integer['type-module'] == 'builtins' and integer['type-path'] == ['int']
    assert integer['nominal?']
    assert 'parameters' not in integer
    assert not info['members-complete?']


def test_class_attribute_override_prevents_nominal_rejection(worker, tmp_path):
    source = ('class Expected: pass\nclass Proxy:\n'
              ' @property\n def __class__(self): return Expected\n')
    info = inspect_source(worker, tmp_path, source)
    assert not info['Proxy']['nominal?']
    namespace = {}
    exec(source, namespace)
    assert isinstance(namespace['Proxy'](), namespace['Expected'])
    assert not worker.safe_member('Proxy', namespace['Proxy'])['nominal?']


def test_static_deprecation_preserves_alias_contracts_and_property_metadata(worker, tmp_path):
    result = inspect_source(worker, tmp_path,
        'from typing_extensions import deprecated as old\n'
        '@old("use modern")\ndef f(x: int) -> str: ...\n'
        '@old("new class")\nclass C:\n'
        ' @property\n @old("new property")\n def value(self) -> int: ...\n')
    assert result['f']['deprecated'] == 'use modern'
    assert result['f']['parameters'][0]['type-path'] == ['int']
    assert result['C']['deprecated'] == 'new class'
    assert result['C']['members']['value']['deprecated'] == 'new property'


def test_runtime_deprecation_reads_stored_markers_without_descriptors(worker):
    def f(x: int) -> str:
        return str(x)
    f.__deprecated__ = 'replace f'
    assert worker.safe_member('f', f)['deprecated'] == 'replace f'
    assert worker.safe_member('p', property(f))['deprecated'] == 'replace f'
    class C:
        __deprecated__ = 'replace C'
    assert worker.safe_member('C', C)['deprecated'] == 'replace C'
    class Meta(type):
        @property
        def __deprecated__(cls):
            raise AssertionError('must not execute')
    class Safe(metaclass=Meta):
        pass
    assert 'deprecated' not in worker.safe_member('Safe', Safe)


def test_static_identity_conditions_only_prove_singletons(worker, tmp_path):
    inspector = worker.StaticInspector([str(tmp_path)], [])
    context = worker.StaticModule(inspector, 'fixture', tmp_path / 'fixture.py', ast.parse(''))
    for expression in ('1000 is 1000', '"interned" is "interned"', '1 is not 2'):
        assert context.static_value(ast.parse(expression, mode='eval').body) is worker.UNKNOWN_VALUE
    assert context.static_value(ast.parse('None is not False', mode='eval').body) is True
    assert context.static_value(ast.parse('True is True', mode='eval').body) is True


def test_unknown_outer_decorator_can_discard_deprecation(worker, tmp_path):
    result = inspect_source(worker, tmp_path,
        'from typing_extensions import deprecated\n'
        'def replace(f): return lambda: 1\n'
        '@replace\n@deprecated("gone")\ndef inner(): pass\n'
        '@deprecated("retained")\n@replace\ndef outer(): pass\n')
    assert 'deprecated' not in result['inner']
    assert result['outer']['deprecated'] == 'retained'
    namespace = {}
    exec((tmp_path / 'fixture.py').read_text(), namespace)
    assert not hasattr(namespace['inner'], '__deprecated__')
    assert namespace['outer'].__deprecated__ == 'retained'


DESCRIPTOR_DATACLASS = '''from dataclasses import dataclass
from typing import Any, overload
class Descriptor:
    @overload
    def __get__(self, obj: None, owner: Any) -> "Descriptor": ...
    @overload
    def __get__(self, obj: object, owner: Any) -> int: ...
    def __get__(self, obj: object | None, owner: Any) -> "int | Descriptor":
        return self if obj is None else obj._value
    def __set__(self, obj: object, value: int) -> None:
        if not isinstance(value, int):
            raise TypeError("expected integer")
        obj._value = value
@dataclass
class Item:
    value: Descriptor = Descriptor()
'''


def test_dataclass_descriptor_constructor_uses_setter_input(worker, tmp_path, monkeypatch):
    info = inspect_source(worker, tmp_path, DESCRIPTOR_DATACLASS)['Item']
    assert info['parameters'][0]['type-path'] == ['int']
    module = types.ModuleType('fixture')
    monkeypatch.setitem(sys.modules, 'fixture', module)
    exec(DESCRIPTOR_DATACLASS, vars(module))
    assert module.Item(3).value == 3
    with pytest.raises(TypeError, match='expected integer'):
        module.Item('bad')


def test_dataclass_descriptor_input_is_specialized_and_unknown_setter_stays_unknown(worker, tmp_path):
    source = ('from dataclasses import dataclass\nfrom typing import Generic, TypeVar\n'
              'T=TypeVar("T")\nclass Descriptor(Generic[T]):\n'
              ' def __get__(self, obj, owner): return self\n'
              ' def __set__(self, obj, value: T): pass\n'
              'class Unknown:\n def __get__(self, obj, owner): return self\n'
              ' def __set__(self, obj, value): pass\n'
              '@dataclass\nclass Item:\n value: Descriptor[int] = Descriptor()\n'
              ' unknown: Unknown = Unknown()\n plain: Descriptor[str] = None\n')
    params = inspect_source(worker, tmp_path, source)['Item']['parameters']
    assert params[0]['type-path'] == ['int']
    assert params[1]['type-any?']
    assert params[2]['type-path'] == ['Descriptor']


def test_dataclass_descriptor_constructor_consumer_diagnostics(tmp_path):
    import importlib
    import basilisp_tools  # noqa: F401
    from basilisp.lang.keyword import keyword as k
    from basilisp.lang.runtime import to_lisp

    (tmp_path / 'descriptor_api.py').write_text(DESCRIPTOR_DATACLASS)
    analyzer = importlib.import_module('basilisp_tools.analyzer')
    bridge = importlib.import_module('basilisp_tools.python')
    cache = bridge.create_cache()
    options = to_lisp({'python-options': {'python-paths': [str(tmp_path)], 'python-inspection?': False,
                                        'python-cache': cache}})
    try:
        result = analyzer.analyze('(ns descriptor-check (:import [descriptor_api :as api]))\n'
                                  '(api/Item 3)\n(api/Item "bad")\n', options)
        assert [(f[k('row')], f[k('type')]) for f in result[k('findings')]] == [(3, k('type-mismatch'))]
    finally:
        bridge.stop_cache__BANG__(cache)


def test_converter_inputs_do_not_replace_member_output_types(worker, tmp_path):
    source = ('from typing_extensions import dataclass_transform, overload\n'
              'def field(*, converter): return converter\n'
              '@dataclass_transform(field_specifiers=(field,))\nclass Base: pass\n'
              'def convert(value: str) -> int: return int(value)\n'
              'def spread(*values: str) -> int: return int(values[0])\n'
              '@overload\ndef choose(value: str) -> int: ...\n'
              '@overload\ndef choose(value: bytes) -> int: ...\n'
              'def choose(value): return int(value)\n'
              'class Converter:\n def __init__(self, value: str | bytes): pass\n'
              'class C(Base):\n x: int = field(converter=convert)\n y: int = field(converter=spread)\n'
              ' z: int = field(converter=choose)\n w: Converter = field(converter=Converter)\n')
    info = inspect_source(worker, tmp_path, source)['C']
    assert info['parameters'][0]['type-path'] == ['str']
    assert info['parameters'][1]['type-path'] == ['str']
    assert [p['type-path'] for p in info['parameters'][2]['type-union']] == [['str'], ['bytes']]
    assert [p['type-path'] for p in info['parameters'][3]['type-union']] == [['str'], ['bytes']]
    assert info['members']['x']['type-path'] == ['int']
    assert info['members']['w']['type-path'] == ['Converter']


def test_builtin_converters_preserve_generic_inputs(worker, tmp_path):
    source = ('from typing_extensions import dataclass_transform\nfrom typing import Generic, TypeVar\n'
              'def field(*, converter): return converter\n'
              '@dataclass_transform(field_specifiers=(field,))\nclass Base: pass\n'
              'T=TypeVar("T")\nclass C(Base, Generic[T]):\n x: set[T] = field(converter=set)\n'
              ' y: dict[str, T] = field(converter=dict)\n z: tuple[T,...] = field(converter=tuple)\n')
    info = inspect_source(worker, tmp_path, source)['C']
    for idx in (0, 2):
        assert info['parameters'][idx]['type-path'] == ['Iterable']
        assert info['parameters'][idx]['type-arguments'] == [{'typevar': 'T'}]
    alternatives = info['parameters'][1]['type-union']
    assert [t['type-path'] for t in alternatives] == [['Mapping'], ['Iterable']]
    assert alternatives[0]['type-arguments'][1] == {'typevar': 'T'}
    assert info['members']['x']['type-path'] == ['set']


def test_unknown_converter_input_is_unknown(worker, tmp_path):
    source = ('from typing_extensions import dataclass_transform\n'
              'def field(*, converter): return converter\n'
              '@dataclass_transform(field_specifiers=(field,))\nclass Base: pass\n'
              'def unknown(value): return int(value)\nclass C(Base):\n x: int = field(converter=unknown)\n')
    info = inspect_source(worker, tmp_path, source)['C']
    assert info['parameters'][0].get('type-any?')
    assert info['members']['x']['type-path'] == ['int']


def test_descriptor_defaults_distribute_reads_over_union(worker, tmp_path):
    source = ('from typing import Generic, TypeVar\nT=TypeVar("T")\n'
              'class Field(Generic[T]):\n def __init__(self, value: T): self.value=value\n'
              ' def __get__(self, obj: object | None, owner: type | None = None) -> T: return self.value\n'
              'class Settings:\n with_default: Field[str] | str = Field("ok")\n'
              ' optional: Field[str] | None = Field("ok")\n')
    info = inspect_source(worker, tmp_path, source)['Settings']['members']
    assert info['with_default']['type-path'] == ['str']
    assert info['optional']['type-path'] == ['str'] and info['optional']['nullable?']
    namespace = {}
    exec(source, namespace)
    assert namespace['Settings']().with_default == 'ok'
    assert namespace['Settings'].with_default == 'ok'


def test_canonical_native_generic_base_keeps_nominal_proof(worker, tmp_path):
    if sys.version_info < (3, 12):
        pytest.skip('the native _typing.Generic identity was introduced in Python 3.12')
    info = inspect_source(worker, tmp_path,
        'from _typing import Generic\nfrom typing import TypeVar\nT=TypeVar("T")\nclass Box(Generic[T]): pass\n')
    assert info['Box']['nominal?']


def test_unresolved_syntactic_bases_prevent_nominal_rejection(worker, tmp_path):
    source = ('class Base: pass\ndef choose_base(): return Base\n'
              'class Dynamic(choose_base()): pass\n')
    info = inspect_source(worker, tmp_path, source)
    assert info['Base']['nominal?']
    assert not info['Dynamic']['nominal?']
    namespace = {}
    exec(source, namespace)
    assert isinstance(namespace['Dynamic'](), namespace['Base'])
    missing = inspect_source(worker, tmp_path, 'class Unresolved(unknown.Base): pass\n')
    assert not missing['Unresolved']['nominal?']


def test_unresolved_metaclass_expressions_prevent_nominal_rejection(worker, tmp_path):
    source = ('class Meta(type):\n def __instancecheck__(cls, value): return True\n'
              'def choose_meta(): return Meta\n'
              'class Dynamic(metaclass=choose_meta()): pass\n'
              'options = {"metaclass": Meta}\nclass Expanded(**options): pass\n')
    info = inspect_source(worker, tmp_path, source)
    assert not info['Dynamic']['nominal?']
    assert not info['Expanded']['nominal?']
    namespace = {}
    exec(source, namespace)
    assert isinstance(1, namespace['Dynamic'])
    assert isinstance(1, namespace['Expanded'])


def test_non_data_descriptor_shadowing_keeps_read_type_unknown(worker, tmp_path):
    source = ('class Descriptor:\n def __get__(self, obj, owner) -> int: return 1\n'
              'class Owner:\n value: Descriptor = Descriptor()\n'
              ' def __init__(self): self.value = Descriptor()\n'
              'class Child(Owner):\n value: Descriptor = Descriptor()\n')
    info = inspect_source(worker, tmp_path, source)
    assert info['Owner']['members']['value']['type-any?']
    assert info['Child']['members']['value']['type-any?']
    namespace = {}
    exec(source, namespace)
    assert isinstance(namespace['Owner']().value, namespace['Descriptor'])
    assert isinstance(namespace['Child']().value, namespace['Descriptor'])
    assert namespace['Owner'].value == 1
