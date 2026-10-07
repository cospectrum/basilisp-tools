"""Native NamedTuple constructor binding and default-mutation controls."""
import importlib.util
import os
from pathlib import Path

import pytest


@pytest.fixture
def worker():
    spec = importlib.util.spec_from_file_location('_fourth_namedtuple_worker', Path(os.environ.get('BLT_INSPECT_WORKER', Path(__file__).resolve().parents[1] / 'src/basilisp_tools/_inspect.py')))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inspect_source(worker, tmp_path, source):
    (tmp_path / 'fixture.py').write_text(source)
    return worker.StaticInspector([str(tmp_path)], []).module('fixture')['members']


def test_inline_namedtuple_factory_keeps_constructor_ahead_of_mixin(worker, tmp_path):
    source = '''from collections import namedtuple
class Mixin:
    def greet(self) -> str:
        return 'hi'
class B(namedtuple('B', ['x']), Mixin):
    pass
'''
    result = inspect_source(worker, tmp_path, source)
    assert [(p['name'], p['required?']) for p in result['B']['parameters']] == [('x', True)]
    native = {}
    exec(source, native)
    assert native['B'](1).greet() == 'hi'
    assert native['B'](1).x == 1
    with pytest.raises(TypeError):
        native['B']()
    with pytest.raises(TypeError):
        native['B'](1, 2)


def test_unknown_earlier_factory_base_does_not_borrow_mixin_zero_arity(worker, tmp_path):
    source = '''class Mixin: pass
def factory():
    class Actual:
        def __init__(self, value): self.value = value
    return Actual
class B(factory(), Mixin): pass
'''
    result = inspect_source(worker, tmp_path, source)
    assert 'parameters' not in result['B']
    native = {}
    exec(source, native)
    assert native['B'](1).value == 1


@pytest.mark.parametrize('factory', ["NamedTuple('Point', [('x', int), ('y', int), ('z', int)])", "namedtuple('Point', 'x,y,z')"])
def test_defaults_mutation_changes_only_trailing_required_parameters(worker, tmp_path, factory):
    source = f'''from typing import NamedTuple
from collections import namedtuple
Point = {factory}
Point.__new__.__defaults__ = (0,)
'''
    result = inspect_source(worker, tmp_path, source)
    assert [p['required?'] for p in result['Point']['parameters']] == [True, True, False]
    native = {}
    exec(source, native)
    assert native['Point'](1, 2).z == 0
    with pytest.raises(TypeError):
        native['Point'](1)


def test_defaults_tuple_values_are_not_executed(worker, tmp_path):
    source = '''from typing import NamedTuple
A = NamedTuple('A', [('x', int)])
B = NamedTuple('B', [('a', A)])
B.__new__.__defaults__ = (A(1),)
'''
    result = inspect_source(worker, tmp_path, source)
    assert result['B']['parameters'][0]['required?'] is False
    native = {}
    exec(source, native)
    assert native['B']().a.x == 1
    no_execution = source.replace('A(1)', "__import__('pathlib').Path('MUST_NOT_EXIST').write_text('bad')")
    assert inspect_source(worker, tmp_path, no_execution)['B']['parameters'][0]['required?'] is False
    assert not Path('MUST_NOT_EXIST').exists()


@pytest.mark.parametrize('defaults,required', [('None', [True, True]), ('(1, 2, 3)', [False, False]), ('()', [True, True])])
def test_defaults_replacement_and_clearing(worker, tmp_path, defaults, required):
    source = f'''from collections import namedtuple
P = namedtuple('P', ['x', 'y'], defaults=[0])
P.__new__.__defaults__ = {defaults}
'''
    result = inspect_source(worker, tmp_path, source)
    assert [p['required?'] for p in result['P']['parameters']] == required
    native = {}
    exec(source, native)
    import inspect
    assert [p.default is inspect.Parameter.empty for p in inspect.signature(native['P']).parameters.values()] == required


def test_unknown_defaults_do_not_keep_stale_arity(worker, tmp_path):
    result = inspect_source(worker, tmp_path, '''from typing import NamedTuple
P = NamedTuple('P', [('x', int)])
P.__new__.__defaults__ = unknown_defaults()
''')
    assert 'parameters' not in result['P']


def test_collections_namedtuple_renamed_fields_and_literal_defaults(worker, tmp_path):
    source = "from collections import namedtuple\nP = namedtuple('P', ['good', 'class', 'good'], rename=True, defaults=[0, 1])\n"
    result = inspect_source(worker, tmp_path, source)
    assert [p['name'] for p in result['P']['parameters']] == ['good', '_1', '_2']
    assert [p['required?'] for p in result['P']['parameters']] == [True, False, False]
    native = {}
    exec(source, native)
    assert native['P'](2) == (2, 0, 1)


def test_namedtuple_subclass_binds_original_base_before_redefinition(worker, tmp_path):
    source = '''from typing import NamedTuple
class User(NamedTuple):
    id: int
    name: str
OldUser = User
class SuperUser(User):
    level: int
class User(NamedTuple):
    id: int
    name: str
    age: int
'''
    result = inspect_source(worker, tmp_path, source)
    assert [p['name'] for p in result['SuperUser']['parameters']] == ['id', 'name']
    assert [p['name'] for p in result['User']['parameters']] == ['id', 'name', 'age']
    assert result['OldUser'].get('type-path') != ['User']
    assert all(base.get('type-path') != ['User'] for base in result['SuperUser']['bases'])
    native = {}
    exec(source, native)
    assert native['SuperUser'](1, 'Alice').name == 'Alice'
    assert issubclass(native['SuperUser'], native['OldUser'])
    assert not issubclass(native['SuperUser'], native['User'])
    with pytest.raises(TypeError):
        native['SuperUser'](1, 'Alice', 3)


def test_rebinding_class_erases_old_nominal_identity_but_keeps_contract(worker, tmp_path):
    source = '''class User:
    def __init__(self, value: int): self.value = value
OldUser = User
class Child(User): pass
class User:
    def __init__(self, name: str, age: int): pass
'''
    result = inspect_source(worker, tmp_path, source)
    assert [p['name'] for p in result['Child']['parameters']] == ['value']
    assert result['Child']['nominal?'] is False
    assert result['OldUser'].get('type-path') != ['User']
    assert result['User']['nominal?'] is True
    native = {}
    exec(source, native)
    assert isinstance(native['Child'](1), native['OldUser'])
    assert not isinstance(native['Child'](1), native['User'])


def test_defaults_mutation_updates_aliases_and_inherited_constructor_only(worker, tmp_path):
    source = '''from collections import namedtuple
P = namedtuple('P', ['x'])
Alias = P
class Child(P): pass
class Custom(P):
    def __new__(cls, value, extra):
        return super().__new__(cls, value + extra)
Alias.__new__.__defaults__ = (0,)
'''
    result = inspect_source(worker, tmp_path, source)
    for name in ['P', 'Alias', 'Child']:
        assert [p['required?'] for p in result[name]['parameters']] == [False]
    assert [p['required?'] for p in result['Custom']['parameters']] == [True, True]
    native = {}
    exec(source, native)
    assert native['P']() == native['Alias']() == native['Child']() == (0,)
    with pytest.raises(TypeError):
        native['Custom']()


def test_mutating_inherited_new_changes_base_and_sibling(worker, tmp_path):
    source = '''from typing import NamedTuple
P = NamedTuple('P', [('x', int)])
class Child(P): pass
class Sibling(P): pass
Child.__new__.__defaults__ = (0,)
'''
    result = inspect_source(worker, tmp_path, source)
    for name in ['P', 'Child', 'Sibling']:
        assert [p['required?'] for p in result[name]['parameters']] == [False]
    native = {}
    exec(source, native)
    assert native['P']() == native['Child']() == native['Sibling']() == (0,)


def test_unknown_defaults_can_be_replaced_by_literal_defaults(worker, tmp_path):
    result = inspect_source(worker, tmp_path, '''from typing import NamedTuple
P = NamedTuple('P', [('x', int)])
P.__new__.__defaults__ = unknown()
P.__new__.__defaults__ = (0,)
''')
    assert result['P']['parameters'][0]['required?'] is False


def test_unknown_factory_base_keeps_explicit_own_constructor(worker, tmp_path):
    result = inspect_source(worker, tmp_path, '''class Mixin: pass
class B(factory(), Mixin):
    def __init__(self, value: str): pass
''')
    assert [p['name'] for p in result['B']['parameters']] == ['value']


def test_old_class_alias_annotation_does_not_become_new_class_annotation(worker, tmp_path):
    source = '''class User: pass
OldUser = User
class User: pass
def accepts_old(value: OldUser): pass
class Another: pass
OldUser = Another
def accepts_rebound_alias(value: OldUser): pass
'''
    result = inspect_source(worker, tmp_path, source)
    assert not worker.type_fields(result['accepts_old']['parameters'][0])
    assert result['accepts_rebound_alias']['parameters'][0]['type-path'] == ['Another']
    native = {}
    exec(source, native)
    assert native['accepts_old'].__annotations__['value'] is not native['User']
    assert native['accepts_rebound_alias'].__annotations__['value'] is native['Another']


def test_replaced_namedtuple_new_drops_old_parameter_and_return_contract(worker, tmp_path):
    source = '''from collections import namedtuple
P = namedtuple('P', ['x'])
Alias = P
class Child(P): pass
def replacement(cls): return 42
P.__new__ = replacement
P.__new__.__defaults__ = ()
'''
    result = inspect_source(worker, tmp_path, source)
    for name in ['P', 'Alias', 'Child']:
        assert 'parameters' not in result[name]
        assert result[name]['constructor-return'] == {'type-any?': True}
    native = {}
    exec(source, native)
    assert native['P']() == native['Alias']() == native['Child']() == 42


def test_replaced_subclass_new_does_not_change_parent_constructor(worker, tmp_path):
    source = '''from collections import namedtuple
P = namedtuple('P', ['x'])
class Child(P): pass
class Grandchild(Child): pass
def replacement(cls): return 42
Child.__new__ = replacement
'''
    result = inspect_source(worker, tmp_path, source)
    assert result['P']['parameters'][0]['required?'] is True
    for name in ['Child', 'Grandchild']:
        assert 'parameters' not in result[name]
        assert result[name]['constructor-return'] == {'type-any?': True}
    native = {}
    exec(source, native)
    assert native['P'](1) == (1,)
    assert native['Child']() == native['Grandchild']() == 42


def test_shadowed_namedtuple_factory_does_not_invent_fields(worker, tmp_path):
    source = '''from collections import namedtuple
class Actual:
    def __init__(self, x, y): self.values = (x, y)
def namedtuple(*args): return Actual
B = namedtuple('B', ['x'])
'''
    result = inspect_source(worker, tmp_path, source)
    assert not result['B'].get('named-tuple?')
    assert 'parameters' not in result['B']
    native = {}
    exec(source, native)
    assert native['B'](1, 2).values == (1, 2)


def test_project_collections_module_is_not_treated_as_trusted_factory(worker, tmp_path):
    (tmp_path / 'collections.py').write_text('def namedtuple(*args): ...\n')
    result = inspect_source(worker, tmp_path, "from collections import namedtuple\nB = namedtuple('B', ['x'])\n")
    assert not result['B'].get('named-tuple?')
    assert 'parameters' not in result['B']
