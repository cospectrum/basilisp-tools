"""A partial must not borrow a function's obsolete declared call shape."""
import importlib
from pathlib import Path

import pytest

import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp


FUNCTION = 'def f(x: int) -> str: return str(x)\n'
CASES = [
    ('defaults', FUNCTION + 'f.__defaults__ = (1,)\nvalue = partial(f)\n', 'value', None),
    ('later', FUNCTION + 'value = partial(f)\nf.__defaults__ = (1,)\n', 'value', None),
    ('alias', FUNCTION + 'alias = f\nalias.__defaults__ = (1,)\nvalue = partial(f)\n', 'value', None),
    ('escape', FUNCTION + 'def mutate(g): g.__defaults__ = (1,)\nmutate(f)\nvalue = partial(f)\n', 'value', None),
    ('wrapper_mutation', FUNCTION + 'value = partial(f)\nvalue.func.__defaults__ = (1,)\n', 'value', None),
    ('class_mutation', '''class Holder:
    def f(x: int) -> str: return str(x)
    f.__defaults__ = (1,)
    value = partial(f)
''', 'Holder.value', None),
    ('class_later', '''class Holder:
    def f(x: int) -> str: return str(x)
    value = partial(f)
Holder.f.__defaults__ = (1,)
''', 'Holder.value', None),
    ('class_escape', '''class Holder:
    def f(x: int) -> str: return str(x)
    def mutate(g): g.__defaults__ = (1,)
    mutate(f)
    value = partial(f)
''', 'Holder.value', None),
    ('ordinary_control', FUNCTION + 'value = partial(f)\n', 'value', 'invalid-arity'),
    ('class_control', '''class Holder:
    def f(x: int) -> str: return str(x)
    value = partial(f)
''', 'Holder.value', 'invalid-arity'),
]


@pytest.fixture(scope='module')
def contracts(tmp_path_factory):
    root = tmp_path_factory.mktemp('partial_signature_mutations')
    for name, body, _, _ in CASES:
        (root / ('partial_' + name + '.py')).write_text('from functools import partial\n' + body)
    analyzer = importlib.import_module('basilisp_tools.analyzer')
    bridge = importlib.import_module('basilisp_tools.python')
    cache = bridge.create_cache()
    options = to_lisp({'python-options': {'python-paths': [str(root)], 'python-inspection?': False,
                                        'python-cache': cache}})
    try:
        yield analyzer, options
    finally:
        bridge.stop_cache__BANG__(cache)


@pytest.mark.parametrize('name,body,target,expected', CASES)
def test_partial_signature_mutation(contracts, name, body, target, expected):
    analyzer, options = contracts
    namespace = {}
    exec('from functools import partial\n' + body, namespace)
    if expected is None:
        assert isinstance(eval(target + '()', namespace), str)
    else:
        with pytest.raises(TypeError):
            eval(target + '()', namespace)
    result = analyzer.analyze('(ns partial-mutation (:import [partial_' + name + ' :as m]))\n(m/' + target + ')', options)
    assert [finding[k('type')] for finding in result[k('findings')]] == ([] if expected is None else [k(expected)])
