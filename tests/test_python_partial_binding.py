"""Concrete partial bindings preserve defaults beyond a callback annotation."""
import importlib
import functools
import sys
import warnings

import pytest

import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp


SOURCE = '''from functools import partial, partial as bind
import functools as ft
from typing import Protocol
def original(a: int, b: int, x: str) -> str: return x
class First(Protocol):
    def __call__(self, x: str) -> str: ...
class Second(Protocol):
    def __call__(self, b: int) -> str: ...
first: First = partial(original, 3, 4, x="a")
second: Second = partial(original, 3, b=3, x="a")
third = partial(original, 3, b=3)
aliased: First = bind(original, 3, 4, x="a")
qualified: First = ft.partial(original, 3, 4, x="a")
from functools import partial as inline_bind; inline = inline_bind(original, 3, 4, x="a")
def variadic(*args: int, **kwargs: str) -> str: return ""
uncertain: First = partial(variadic, 1)
def text(value: str) -> str: return value
def integer(value: int) -> int: return value
class Holder:
    run: First = partial(original, 3, 4, x="a")
'''


@pytest.fixture(scope="module")
def contracts(tmp_path_factory):
    root = tmp_path_factory.mktemp("partial_binding")
    (root / "partial_binding.py").write_text(SOURCE)
    analyzer = importlib.import_module("basilisp_tools.analyzer")
    bridge = importlib.import_module("basilisp_tools.python")
    cache = bridge.create_cache()
    options = to_lisp({"python-options": {
        "python-paths": [str(root)], "python-inspection?": False,
        "python-cache": cache,
    }})
    try:
        yield analyzer, options
    finally:
        bridge.stop_cache__BANG__(cache)


@pytest.mark.parametrize("body,kind", [
    ('(m/text (m/first))', None),
    ('(m/text (m/first ** :x "b"))', None),
    ('(m/first "b")', 'invalid-arity'),
    ('(m/first ** :x 1)', 'type-mismatch'),
    ('(m/first ** :unknown 1)', 'invalid-arity'),
    ('(m/integer (m/first))', 'type-mismatch'),
    ('(m/text (m/second))', None),
    ('(m/text (m/second ** :x "b"))', None),
    ('(m/text (m/second ** :b 3))', None),
    ('(m/text (m/second ** :x "b" :b 3))', None),
    ('(m/text (m/second ** :b 3 :x "b"))', None),
    ('(m/second ** :b "bad")', 'type-mismatch'),
    ('(m/third)', 'invalid-arity'),
    ('(m/text (m/third ** :x "b"))', None),
    ('(m/third 4 "b")', 'invalid-arity'),
    ('(m/text (m/aliased))', None),
    ('(m/text (m/qualified))', None),
    ('(m/text (m/inline))', None),
    ('(m/uncertain)', None),
    ('(m/uncertain 1 2 ** :anything "b")', None),
    pytest.param('(m/text (.run (m/Holder)))', None, marks=pytest.mark.skipif(
        sys.version_info >= (3, 14), reason='New partial descriptor binding stays unknown')),
    pytest.param('(.run (m/Holder) ** :x 1)', 'type-mismatch', marks=pytest.mark.skipif(
        sys.version_info >= (3, 14), reason='New partial descriptor binding stays unknown')),
])
def test_partial_contracts(contracts, body, kind):
    analyzer, options = contracts
    result = analyzer.analyze('(ns partial-audit (:import [partial_binding :as m]))\n' + body, options)
    assert [f[k("type")] for f in result[k("findings")]] == ([] if kind is None else [k(kind)])


def test_native_binding():
    namespace = {}
    exec(SOURCE, namespace)
    assert namespace['first']() == 'a'
    assert namespace['first'](x='b') == 'b'
    assert namespace['second']() == 'a'
    assert namespace['second'](b=3, x='b') == 'b'
    assert namespace['third'](x='b') == 'b'
    if sys.version_info < (3, 14):
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', category=FutureWarning, message='functools.partial will be a method descriptor.*')
            assert namespace['Holder']().run() == 'a'
    with pytest.raises(TypeError):
        namespace['first']('b')
    with pytest.raises(TypeError):
        namespace['third'](4, 'b')


@pytest.mark.parametrize('rebound', [
    "class Holder:\n    partial = lambda *args: lambda: 'native'\n    run = partial(original, 1)\n",
    "from builtins import str as original\nclass Holder:\n    run = partial(original, 1)\n",
    "class original(str): pass\nclass Holder:\n    run = partial(original, 'native')\n",
])
def test_rebinding_cannot_borrow_an_obsolete_function_contract(tmp_path, rebound):
    source = ('from functools import partial\n'
              'def original(value: int) -> int: return value\n' + rebound +
              'def text(value: str) -> str: return value\n')
    values = {}
    exec(source, values)
    assert isinstance(values['Holder'].run(), str)
    (tmp_path / 'rebound_partial.py').write_text(source)
    analyzer = importlib.import_module('basilisp_tools.analyzer')
    bridge = importlib.import_module('basilisp_tools.python')
    cache = bridge.create_cache()
    try:
        result = analyzer.analyze(
            '(ns partial-rebinding (:import [rebound_partial :as m])) (m/text (m/Holder.run))',
            to_lisp({'python-options': {'python-paths': [str(tmp_path)],
                                       'python-inspection?': False, 'python-cache': cache}}))
        assert list(result[k('findings')]) == []
    finally:
        bridge.stop_cache__BANG__(cache)


@pytest.mark.parametrize('body,expression', [
    ('from builtins import str as partial\nvalue = partial(original)\nfrom functools import partial\n', 'm/value'),
    ("import functools as ft\nft.partial = lambda *args: lambda: 'native'\nvalue = ft.partial(original, 1)\n", '(m/value)'),
    ("import functools as ft\nft.partial = lambda *args: lambda: 'native'; value = ft.partial(original, 1)\n", '(m/value)'),
    ("import functools as ft\nsetattr(ft, 'partial', lambda *args: lambda: 'native')\nvalue = ft.partial(original, 1)\n", '(m/value)'),
])
def test_factory_import_order_and_direct_writes(tmp_path, body, expression):
    source = ('def original(value: int) -> int: return value\n' + body +
              'def text(value: str) -> str: return value\n')
    original = functools.partial
    try:
        values = {}
        exec(source, values)
        value = values['value']
        assert isinstance(value() if callable(value) else value, str)
    finally:
        functools.partial = original
    (tmp_path / 'partial_writes.py').write_text(source)
    analyzer = importlib.import_module('basilisp_tools.analyzer')
    bridge = importlib.import_module('basilisp_tools.python')
    cache = bridge.create_cache()
    try:
        result = analyzer.analyze(
            '(ns partial-writes (:import [partial_writes :as m])) (m/text ' + expression + ')',
            to_lisp({'python-options': {'python-paths': [str(tmp_path)],
                                       'python-inspection?': False, 'python-cache': cache}}))
        assert list(result[k('findings')]) == []
    finally:
        bridge.stop_cache__BANG__(cache)
