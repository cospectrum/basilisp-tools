"""Exported generic callable instances retain constructor callback parameter packs."""
import importlib

import pytest

import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp

SOURCE = '''from typing import Any, Callable, Generic, ParamSpec, TypeVar, overload
P = ParamSpec("P")
R = TypeVar("R")
T = TypeVar("T")
class Remote(Generic[P, R]):
    def __init__(self, function: Callable[P, R]): self.function = function
    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        return self.function(*args, **kwargs)
    def remote(self, *args: P.args, **kwargs: P.kwargs) -> R:
        return self.function(*args, **kwargs)
def function(a: str, b: list[int]) -> str: return a
remote = Remote(function)
alias = function
aliased = Remote(alias)
keyword = Remote(function=function)
def optional(a: int, /, b: str = 'x', *, flag: bool = False) -> str: return b
with_defaults = Remote(optional)
def generic(value: T) -> T: return value
uncertain = Remote(generic)
async def asynchronous(a: int) -> str: return str(a)
async_remote = Remote(asynchronous)
@overload
def overloaded(a: int) -> int: ...
@overload
def overloaded(a: str) -> str: ...
def overloaded(a): return a
overload_remote = Remote(overloaded)
class Pair(Generic[P, R]):
    def __init__(self, first: Callable[P, R], second: Callable[P, R]): pass
    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R: ...
conflict = Pair(function, optional)
def text(value: str) -> str: return value
def integer(value: int) -> int: return value
'''

@pytest.fixture(scope='module')
def contracts(tmp_path_factory):
    root = tmp_path_factory.mktemp('static_callbacks')
    (root / 'static_callbacks.py').write_text(SOURCE)
    analyzer = importlib.import_module('basilisp_tools.analyzer')
    bridge = importlib.import_module('basilisp_tools.python')
    cache = bridge.create_cache()
    options = to_lisp({'python-options': {'python-paths': [str(root)], 'python-inspection?': False,
                                        'python-cache': cache}})
    try:
        yield analyzer, options
    finally:
        bridge.stop_cache__BANG__(cache)

@pytest.mark.parametrize('body,kind', [
    ('(m/text (m/remote "hi" #py []))', None),
    ('(m/text (m/aliased "hi" #py []))', None),
    ('(m/text (m/keyword "hi" #py []))', None),
    ('(m/remote 1 #py [])', 'type-mismatch'),
    ('(m/remote "hi")', 'invalid-arity'),
    ('(m/remote "hi" #py ["bad"])', 'type-mismatch'),
    ('(m/remote ** :a "hi" :b #py [1])', None),
    ('(.remote m/remote 1 #py [])', 'type-mismatch'),
    ('(m/integer (m/remote "hi" #py []))', 'type-mismatch'),
    ('(m/text (m/with-defaults 1))', None),
    ('(m/text (m/with-defaults 1 ** :flag true))', None),
    ('(m/with-defaults 1 ** :flag "bad")', 'type-mismatch'),
    ('(m/with-defaults ** :a 1)', 'invalid-arity'),
    ('(m/uncertain "hi")', None),
    ('(m/async-remote "hi")', None),
    ('(m/overload-remote "hi")', None),
    ('(m/conflict 1 2 3 ** :anything "x")', None),
])
def test_constructor_callback_contracts(contracts, body, kind):
    analyzer, options = contracts
    result = analyzer.analyze('(ns static-callbacks (:import [static_callbacks :as m]))\n' + body, options)
    assert [f[k('type')] for f in result[k('findings')]] == ([] if kind is None else [k(kind)])


def test_native_remote_callback():
    namespace = {}
    exec(SOURCE, namespace)
    for name in ('remote', 'aliased', 'keyword'):
        assert namespace[name]('hi', []) == 'hi'
        assert namespace[name](a='hi', b=[1]) == 'hi'
        with pytest.raises(TypeError):
            namespace[name]('hi')
    assert namespace['with_defaults'](1) == 'x'
    assert namespace['with_defaults'](1, flag=True) == 'x'
    with pytest.raises(TypeError):
        namespace['with_defaults'](a=1)
