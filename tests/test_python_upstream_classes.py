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
    parameters = info['parameters']
    if info.get('typed-dict?'):
        assert parameters[0] == {'name': 'mapping', 'kind': 'positional-only', 'required?': False}
        parameters = parameters[1:]
    assert [p['name'] for p in parameters] == expected
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


def test_generic_order_matches_native(worker, tmp_path):
    source = ('from typing import Generic, Iterable, TypeVar\n'
              'T = TypeVar("T")\nS = TypeVar("S")\n'
              'class C(Iterable[S], Generic[T, S]):\n'
              ' def __iter__(self): return iter(())\n'
              ' def call(self, a: T, b: S) -> S: return b\n')
    info = inspect_source(worker, tmp_path, source)['C']
    namespace = {}
    exec(source, namespace)
    assert [p['typevar'] for p in info['type-parameters']] == [p.__name__ for p in namespace['C'].__parameters__] == ['T', 'S']


def test_legacy_positional_only_is_limited_to_double_underscore_prefix(worker, tmp_path):
    source = ('class C:\n def call(self, __a: str, b: str, __dunder__: int = 1): return b\n')
    params = inspect_source(worker, tmp_path, source)['C']['members']['call']['parameters']
    assert [p['kind'] for p in params] == ['positional-or-keyword', 'positional-only', 'positional-or-keyword', 'positional-or-keyword']
    namespace = {}
    exec(source, namespace)
    assert namespace['C']().call('a', b='b', __dunder__=2) == 'b'
    with pytest.raises(TypeError):
        namespace['C']().call(__a='a', b='b')


def test_union_pack_retains_union_operator_during_substitution(worker, tmp_path):
    source = ('from typing import Generic, Union\nfrom typing_extensions import TypeVarTuple, Unpack\n'
              'Ts=TypeVarTuple("Ts")\nclass C(Generic[Unpack[Ts]]):\n'
              ' def result(self) -> Union[Unpack[Ts]]: ...\n')
    info = inspect_source(worker, tmp_path, source)['C']
    result = worker.substitute_types(info, {'Ts': {'type-pack?': True, 'type-arguments': [worker.runtime_type(int), worker.runtime_type(str)]}})
    assert [t['type-path'] for t in result['members']['result']['type-union']] == [['int'], ['str']]
    assert 'type-pack?' not in result['members']['result']
    empty = worker.substitute_types(info, {'Ts': {'type-pack?': True, 'type-arguments': []}})
    assert empty['members']['result']['type-never?']


def test_typeddict_extra_items_are_typed_variadic_keywords(worker, tmp_path):
    source = ('from typing_extensions import TypedDict, Unpack, Never\n'
              'class Extra(TypedDict, extra_items=int):\n name: str\n'
              'class Child(Extra):\n year: int\n'
              'class Closed(TypedDict, extra_items=Never):\n name: str\n'
              'def use(**kwargs: Unpack[Child]) -> None: pass\n')
    info = inspect_source(worker, tmp_path, source)
    assert info['Extra']['typed-dict-extra-items']['type-path'] == ['int']
    assert info['Child']['parameters'][-1]['kind'] == 'var-keyword'
    assert info['Child']['parameters'][-1]['type-path'] == ['int']
    assert info['use']['parameters'][-1]['type-path'] == ['int']
    assert [p['name'] for p in info['use']['parameters'][:-1]] == ['name', 'year']
    assert not any(p['kind'] == 'var-keyword' for p in info['Closed']['parameters'])


def test_runtime_typeddict_preserves_schema_with_native_mapping_constructor(worker):
    import typing

    class Record(typing.TypedDict):
        name: str

    info = worker.safe_member('Record', Record)
    assert info['typed-dict?'] and info['mapping-constructor?']
    assert info['parameters'][0] == {'name': 'mapping', 'kind': 'positional-only', 'required?': False}
    assert info['typed-dict-fields']['name']['required?']
    assert 'constructor-return' not in info
    assert Record({'name': 'before'}, name='after') == {'name': 'after'}


def test_shadowed_typeddict_namedtuple_names_are_not_factories(worker, tmp_path):
    source = ('from typing_extensions import TypedDict as TD, NamedTuple as NT\n'
              'class TypedDict:\n def __init__(self): pass\n'
              'class NamedTuple:\n def __init__(self): pass\n'
              'class Plain(TypedDict):\n value: str\n'
              'class PlainTuple(NamedTuple):\n value: str\n'
              'class Real(TD):\n value: str\n'
              'RealTuple = NT("RealTuple", [("value", str)])\n')
    info = inspect_source(worker, tmp_path, source)
    assert not info['Plain'].get('typed-dict?')
    assert not info['PlainTuple'].get('named-tuple?')
    assert info['Plain']['parameters'] == []
    assert info['Real']['typed-dict?']
    assert info['RealTuple']['named-tuple?']


def test_typing_form_values_are_separate_from_annotations(worker, tmp_path):
    source = ('from typing import Any, Generic, Literal, Protocol, TypeAlias\n'
              'Value = Literal[0]\nAlias: TypeAlias = Literal[0]\n'
              'def f(x: Any, y: Literal[0]) -> None: pass\n')
    info = inspect_source(worker, tmp_path, source)
    assert all(info[name]['typing-form?'] for name in ('Any', 'Generic', 'Literal', 'Protocol', 'Value', 'Alias'))
    assert info['Value']['type-path'] == ['Literal']
    assert info['f']['parameters'][0]['type-any?']
    assert info['f']['parameters'][1]['type-path'] == ['int']
    assert not info['f']['parameters'][1].get('typing-form?')
    forms = worker.inspect_module('typing', [str(tmp_path)], False)
    assert forms['members']['Literal']['typing-form?']
    assert not forms['members-complete?']
    (tmp_path/'typing.py').write_text('class Literal: pass\n')
    shadowed = worker.inspect_module('typing', [str(tmp_path)], False)
    assert shadowed['members']['Literal']['kind'] == 'class'
    assert not shadowed['members']['Literal'].get('typing-form?')


def test_runtime_typing_forms_are_descriptor_safe(worker):
    import typing
    for value in (typing.Literal, typing.Literal[0], typing.Any, typing.Protocol, typing.Union[int, str], int | str):
        assert worker.safe_member('alias', value)['typing-form?']
    assert not worker.safe_member('integer', int).get('typing-form?')
    class Spoof:
        @property
        def __origin__(self):
            raise AssertionError('must not execute')
    assert not worker.safe_member('spoof', Spoof()).get('typing-form?')


def test_typing_form_alias_annotation_survives_reexport(worker, tmp_path):
    (tmp_path/'declared.py').write_text('from typing import Any, Literal\nL = Literal[0]\nA = Any\n')
    info = inspect_source(worker, tmp_path, 'from declared import L, A\ndef f(x: L, y: A) -> L: pass\n')
    assert info['L']['typing-form?']
    assert info['f']['parameters'][0]['literal-values'] == [0]
    assert info['f']['parameters'][1]['type-any?']
    assert info['f']['literal-values'] == [0]


def test_enum_calls_use_metaclass_contract(worker, tmp_path):
    source = ('from enum import Enum, EnumMeta\n'
              'class E(Enum):\n A=(1,"one")\n def __init__(self, value, description):\n  self.description=description\n'
              'class Meta(EnumMeta): pass\n'
              'class Empty(Enum, metaclass=Meta): pass\n'
              'class MetaOnly(metaclass=Meta): pass\n'
              'class CustomMeta(EnumMeta):\n def __call__(cls, *, custom: int) -> str: return str(custom)\n'
              'class Custom(Enum, metaclass=CustomMeta):\n A=1\n')
    info = inspect_source(worker, tmp_path, source)
    assert info['E']['parameters'][0]['name'] == 'value'
    assert [p['name'] for p in info['E']['parameters'] if p['required?']] == ['value']
    assert info['Empty']['parameters'][1]['name'] == 'names'
    assert info['MetaOnly']['parameters'][1]['name'] == 'names'
    assert info['Custom']['parameters'][0]['name'] == 'custom'
    assert info['Custom']['constructor-return']['type-path'] == ['str']
    namespace = {}
    exec(source, namespace)
    assert namespace['E']((1, 'one')) is namespace['E'].A
    generated = namespace['Empty']('Generated', 'A B C')
    assert generated._member_map_['A'] is generated.A
    if sys.version_info >= (3, 11):
        meta_only = namespace['MetaOnly']('MetaGenerated', 'A B')
        assert meta_only._member_map_['A'] is meta_only.A
    else:
        with pytest.raises(TypeError):
            namespace['MetaOnly']('MetaGenerated', 'A B')
    assert namespace['Custom'](custom=1) == '1'


def test_callable_alias_preserves_prefix_before_symbolic_pack(worker, tmp_path):
    source = ('from typing import Callable, TypeVar\nfrom typing_extensions import TypeVarTuple, Unpack\n'
              'Ts=TypeVarTuple("Ts")\nT=TypeVar("T")\n'
              'Alias=Callable[[Unpack[Ts]],None]\n'
              'def f(cb: Alias[int,Unpack[Ts]]) -> tuple[Unpack[Ts]]: ...\n')
    info = inspect_source(worker, tmp_path, source)['f']
    args = info['parameters'][0]['type-arguments'][0]['type-arguments']
    assert args == [{'type-module': 'builtins', 'type-path': ['int']},
                    {'typevar': 'Ts', 'variadic?': True, 'unpack?': True}]
    assert info['type-arguments'] == [{'typevar': 'Ts', 'variadic?': True, 'unpack?': True}]
    parameters = [{'typevar': 'T'}, {'typevar': 'Ts', 'variadic?': True}]
    assert worker.bind_type_parameters(parameters, [
        {'unpack?': True, 'typevar': 'Us', 'variadic?': True}, {}]) == {}


def test_constrained_constructor_assignment_promotes_proven_subclass(worker, tmp_path):
    source = ('from typing import Generic, TypeVar\n'
              'class A: pass\nclass B: pass\nclass A2(A): pass\n'
              'T=TypeVar("T",A,B)\nclass F(Generic[T]):\n'
              ' def __init__(self, value: T): self.value=value\n'
              'f=F[A2](A2())\n')
    info = inspect_source(worker, tmp_path, source)
    assert info['f']['type-arguments'] == [{'type-module': 'fixture', 'type-path': ['A']}]
    namespace = {}
    exec(source, namespace)
    assert isinstance(namespace['f'].value, namespace['A'])
    uncertain = inspect_source(worker, tmp_path, source.replace('class A2(A): pass',
        'def replace(cls): return B\n@replace\nclass A2(A): pass'))
    assert uncertain['f']['type-arguments'] == [{'type-module': 'fixture', 'type-path': ['A2']}]


def test_explicit_positional_only_separator_disables_legacy_convention(worker,tmp_path):
    source='def f(x:int,/,__y:int) -> int:return x+__y\n'
    params=inspect_source(worker,tmp_path,source)['f']['parameters']
    assert [p['kind'] for p in params]==['positional-only','positional-or-keyword']
    namespace={};exec(source,namespace)
    assert namespace['f'](3,__y=4)==7
    with pytest.raises(TypeError):namespace['f'](x=3,__y=4)


def test_enum_constructor_follows_interpreter_variadic_value_lookup(worker,tmp_path):
    import enum
    import inspect

    source='from enum import Enum\nclass E(Enum):\n A=(1,2,3,4)\n'
    info=inspect_source(worker,tmp_path,source)['E']
    native=inspect.signature(enum.EnumMeta.__call__)
    variadic=any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in native.parameters.values())
    assert any(p['kind']=='var-positional' for p in info['parameters'])==variadic
    assert any(p['kind']=='var-positional' for p in info['overloads'][0]['parameters'])==variadic
    assert all('constructor-return' not in sig for sig in info['overloads'])
    namespace={};exec(source,namespace)
    if variadic:assert namespace['E'](1,2,3,4) is namespace['E'].A
    else:
        with pytest.raises(TypeError):namespace['E'](1,2,3,4)
    assert namespace['E']((1,2,3,4)) is namespace['E'].A


def test_keyword_only_separator_disables_legacy_positional_convention(worker, tmp_path):
    source = 'def f(__x:str,*,__y__:str,__z:str) -> str:return __x+__y__+__z\n'
    params = inspect_source(worker, tmp_path, source)['f']['parameters']
    assert [p['kind'] for p in params] == ['positional-or-keyword', 'keyword-only', 'keyword-only']
    namespace = {}
    exec(source, namespace)
    assert namespace['f'](__x='a', __y__='b', __z='c') == 'abc'
    with pytest.raises(TypeError):
        namespace['f']('a', 'b', 'c')


def test_project_enum_module_does_not_acquire_stdlib_constructor(worker, tmp_path):
    source = 'class Enum:\n def __init__(self,*,custom:int): self.custom=custom\n'
    (tmp_path / 'enum.py').write_text(source)
    (tmp_path / 'fixture.py').write_text('from enum import Enum\nclass E(Enum): pass\n')
    info = worker.StaticInspector([str(tmp_path)], []).module('fixture')['members']['E']
    assert not info.get('enum?')
    assert [p['name'] for p in info['parameters']] == ['custom']
    namespace = {}
    exec(source + '\nclass E(Enum): pass\n', namespace)
    assert namespace['E'](custom=1).custom == 1
    with pytest.raises(TypeError):
        namespace['E'](1)


def test_free_function_legacy_positional_group_stops_at_regular_parameter(worker, tmp_path):
    source = ('def free(x: int, __y: int) -> int: return x + __y\n'
              'def mixed(__x: int, y: int, __z: int) -> int: return __x + y + __z\n')
    info = inspect_source(worker, tmp_path, source)
    assert [p['kind'] for p in info['free']['parameters']] == ['positional-or-keyword'] * 2
    assert [p['kind'] for p in info['mixed']['parameters']] == [
        'positional-only', 'positional-or-keyword', 'positional-or-keyword']
    namespace = {}
    exec(source, namespace)
    assert namespace['free'](1, __y=2) == 3
    assert namespace['mixed'](1, y=2, __z=3) == 6


def test_class_private_parameter_keywords_require_native_mangling(worker, tmp_path):
    source = ('class C:\n'
              ' @staticmethod\n def static(self, __x: int) -> int: return __x\n'
              ' @classmethod\n def method(cls, __x: int) -> int: return __x\n'
              ' def __new__(cls, __x: int): return object.__new__(cls)\n')
    info = inspect_source(worker, tmp_path, source)['C']['members']
    assert info['static']['parameters'][1]['kind'] == 'positional-only'
    assert info['method']['parameters'][0]['kind'] == 'positional-only'
    assert info['__new__']['parameters'][1]['kind'] == 'positional-only'
    namespace = {}
    exec(source, namespace)
    C = namespace['C']
    with pytest.raises(TypeError):
        C.static('x', __x=42)
    assert C.static('x', _C__x=42) == C.method(_C__x=42) == 42
    assert isinstance(C(_C__x=42), C)


def test_collapsed_union_values_are_actual_classes(worker, tmp_path):
    source = ('from typing import Union, Optional\n'
              'class C: pass\n'
              'One=Union[int]\nDuplicate=Union[int,int]\n'
              'NoneUnion=Union[None]\nNoneOptional=Optional[None]\n'
              'Local=Union[C,C]\nGeneral=Union[object,int]\n'
              'Parameterized=Union[list[int]]\nForward=Union["int"]\n')
    info = inspect_source(worker, tmp_path, source)
    namespace = {}
    exec(source, namespace)
    for name, target in [('One', int), ('Duplicate', int), ('NoneUnion', type(None)),
                         ('NoneOptional', type(None)), ('Local', namespace['C'])]:
        assert namespace[name] is target
        assert info[name]['kind'] == 'class'
        assert not info[name].get('typing-form?')
        assert info[name]['type-path'] == [target.__name__]
    for name in ['General', 'Parameterized', 'Forward']:
        assert not issubclass(type(namespace[name]), type)
        assert info[name]['typing-form?']


def test_forward_union_alias_does_not_invent_a_runtime_class(worker, tmp_path):
    source = 'from typing import Union\nForward="int"\nAlias=Union[Forward]\n'
    info = inspect_source(worker, tmp_path, source)['Alias']
    namespace = {}
    exec(source, namespace)
    assert not isinstance(namespace['Alias'], type)
    assert info['typing-form?']


def test_nested_collapsed_union_alias_remains_a_class(worker, tmp_path):
    source = 'from typing import Union\nInner=Union[int]\nAlias=Union[Inner,Union[int,int]]\n'
    info = inspect_source(worker, tmp_path, source)['Alias']
    namespace = {}
    exec(source, namespace)
    assert namespace['Alias'] is int
    assert info['kind'] == 'class' and not info.get('typing-form?')


def test_unknown_class_union_operands_can_collapse(worker, tmp_path):
    source = ('from typing import Union, Optional\n'
              'def factory() -> type[object]: return int\n'
              'def none_factory() -> type[object]: return type(None)\n'
              'cls=factory()\nnone_cls=none_factory()\n'
              'Maybe=Union[cls,int]\nMaybeNone=Optional[none_cls]\nOne=Union[cls]\n')
    info = inspect_source(worker, tmp_path, source)
    namespace = {}
    exec(source, namespace)
    assert namespace['Maybe'] is namespace['One'] is int
    assert namespace['MaybeNone'] is type(None)
    for name in ['Maybe', 'MaybeNone']:
        assert not info[name].get('typing-form?')
        assert info[name].get('type-any?')
        assert 'type-path' not in info[name]
    assert not info['One'].get('typing-form?')
    assert info['One']['type-path'] == ['type']
    assert info['One']['type-arguments'][0]['type-path'] == ['object']


def test_union_value_proof_uses_declaration_order_not_later_aliases(worker, tmp_path):
    source = ('from typing import Union\n'
              'def factory() -> type[object]: return int\n'
              'cls=factory()\nAlias=Union[cls,int]\ncls=str\n'
              'class C: pass\nclass D: pass\n'
              'Current=C\nExact=Union[Current,C]\nCurrent=D\n')
    info = inspect_source(worker, tmp_path, source)
    namespace = {}
    exec(source, namespace)
    assert namespace['Alias'] is int
    assert info['Alias'].get('type-any?') and not info['Alias'].get('typing-form?')
    assert namespace['Exact'] is namespace['C']
    assert info['Exact']['kind'] == 'class' and info['Exact']['type-path'] == ['C']


def test_shadowed_builtin_union_operands_are_not_assumed_builtin_values(worker, tmp_path):
    source = ('from typing import Union\n'
              'def factory()->type[object]:return str\n'
              'int:type[object]=factory()\nAlias=Union[int,str]\n'
              'class list:\n def __class_getitem__(cls,key):return str\n'
              'Other=Union[list[int],str]\n')
    info = inspect_source(worker, tmp_path, source)
    namespace = {}
    exec(source, namespace)
    assert namespace['Alias'] is namespace['Other'] is str
    assert all(info[name].get('type-any?') and not info[name].get('typing-form?')
               for name in ['Alias', 'Other'])


def test_shadowed_typing_wrapper_is_not_a_nonclass_union_proof(worker, tmp_path):
    source = ('from typing import Union,Literal\n'
              'class Custom:\n def __class_getitem__(cls,key):return int\n'
              'Literal=Custom\nAlias=Union[Literal[0],int]\n')
    info = inspect_source(worker, tmp_path, source)['Alias']
    namespace = {}
    exec(source, namespace)
    assert namespace['Alias'] is int
    assert info.get('type-any?') and not info.get('typing-form?')


@pytest.mark.parametrize('source', [
    'from typing import Union\ndef replace(cls): return int\n'
    '@replace\nclass C: pass\nAlias=Union[C,int]\n',
    'from typing import Union\nclass Meta(type):\n'
    ' def __hash__(cls): return hash(int)\n'
    ' def __eq__(cls,other): return other is int\n'
    'class C(metaclass=Meta): pass\nAlias=Union[int,C]\n',
])
def test_replaced_class_or_custom_equality_cannot_prove_union_identity(worker, tmp_path, source):
    info = inspect_source(worker, tmp_path, source)['Alias']
    namespace = {}
    exec(source, namespace)
    assert namespace['Alias'] is int
    assert info.get('type-any?') and not info.get('typing-form?')
