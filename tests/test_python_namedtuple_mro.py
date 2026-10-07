"""Synthesized NamedTuple slots participate in native constructor MRO."""
import importlib
import importlib.util
from pathlib import Path

import pytest

import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp


@pytest.fixture(scope='module')
def worker():
    path = Path(__file__).resolve().parents[1] / 'src/basilisp_tools/_inspect.py'
    spec = importlib.util.spec_from_file_location('_namedtuple_mro_worker', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SOURCE = '''from collections import namedtuple
from typing import NamedTuple
class ForeignNew:
    def __new__(cls) -> str: return 'foreign'
P = namedtuple('P', ['x'])
class Inline(namedtuple('Inline', ['x']), ForeignNew): pass
class Assigned(P, ForeignNew): pass
class Typed(NamedTuple):
    x: int
class TypedChild(Typed, ForeignNew): pass
class Reverse(ForeignNew, P): pass
class Own(P, ForeignNew):
    def __new__(cls) -> str: return 'own'
class Grandchild(Assigned): pass
Alias = P
Alias.__new__.__defaults__ = (0,)
class AfterDefaults(P, ForeignNew): pass
Q = namedtuple('Q', ['x'])
Q.__new__.__defaults__ = unknown_defaults() if False else (0,)
def integer(value: int) -> int: return value
def text(value: str) -> str: return value
'''


@pytest.fixture(scope='module')
def environment(tmp_path_factory):
    root = tmp_path_factory.mktemp('namedtuple_mro')
    (root / 'tuple_mro.py').write_text(SOURCE)
    analyzer = importlib.import_module('basilisp_tools.analyzer')
    bridge = importlib.import_module('basilisp_tools.python')
    cache = bridge.create_cache()
    options = to_lisp({'python-options': {
        'python-paths': [str(root)], 'python-inspection?': False, 'python-cache': cache}})
    try:
        yield analyzer, options
    finally:
        bridge.stop_cache__BANG__(cache)


@pytest.mark.parametrize('body,kind', [
    ('(m/Inline 1)', None),
    ('(m/Inline)', 'invalid-arity'),
    ('(m/Inline 1 2)', 'invalid-arity'),
    ('(m/Assigned)', None),
    ('(m/Assigned 1)', None),
    ('(m/Assigned 1 2)', 'invalid-arity'),
    ('(m/integer (.-x (m/TypedChild 1)))', None),
    ('(m/TypedChild "wrong")', 'type-mismatch'),
    ('(m/TypedChild)', 'invalid-arity'),
    ('(m/text (m/Reverse))', None),
    ('(m/Reverse 1)', 'invalid-arity'),
    ('(m/text (m/Own))', None),
    ('(m/Own 1)', 'invalid-arity'),
    ('(m/Grandchild)', None),
    ('(m/AfterDefaults)', None),
    ('(m/Q)', None),
])
def test_constructor_mro(environment, body, kind):
    analyzer, options = environment
    result = analyzer.analyze('(ns tuple-mro (:import [tuple_mro :as m]))\n' + body, options)
    assert [finding[k('type')] for finding in result[k('findings')]] == ([] if kind is None else [k(kind)])


def test_native_constructor_mro():
    values = {}
    exec(SOURCE, values)
    for name in ('Inline', 'Assigned', 'TypedChild', 'Grandchild', 'AfterDefaults'):
        assert values[name](1).x == 1
    for name in ('Assigned', 'Grandchild', 'AfterDefaults', 'Q'):
        assert values[name]().x == 0
    assert values['Reverse']() == 'foreign'
    assert values['Own']() == 'own'
    for name, args in [('Inline', ()), ('Inline', (1, 2)), ('TypedChild', ()), ('Reverse', (1,)), ('Own', (1,))]:
        with pytest.raises(TypeError):
            values[name](*args)


@pytest.mark.parametrize('mutation,unknown_result', [
    ('P.__new__.__defaults__ = unknown()', False),
    ('P.__new__ = replacement', True),
])
def test_mutated_slot_does_not_keep_known_signature(worker, tmp_path, mutation, unknown_result):
    source = '''from collections import namedtuple
P = namedtuple('P', ['x'])
class Mixin:
    def __new__(cls) -> str: return 'foreign'
def replacement(cls, *args): return 42
''' + mutation + '\nclass Child(P, Mixin): pass\n'
    (tmp_path / 'fixture.py').write_text(source)
    members = worker.StaticInspector([str(tmp_path)], []).module('fixture')['members']
    for name in ('P', 'Child'):
        assert 'parameters' not in members[name]
        assert members[name]['members']['__new__'].get('signature-unknown?')
        if unknown_result:
            assert members[name]['constructor-return'] == {'type-any?': True}
        else:
            assert 'constructor-return' not in members[name]


def test_earlier_tuple_slot_preserves_instance_return(worker, tmp_path):
    (tmp_path / 'fixture.py').write_text(SOURCE)
    members = worker.StaticInspector([str(tmp_path)], []).module('fixture')['members']
    for name in ('Inline', 'Assigned', 'TypedChild', 'Grandchild', 'AfterDefaults'):
        assert 'constructor-return' not in members[name]
        assert members[name]['type-path'] == [name]
        assert members[name]['members']['__new__']['type-self?']
    for name in ('Reverse', 'Own'):
        assert members[name]['constructor-return'] == {'type-module': 'builtins', 'type-path': ['str']}
