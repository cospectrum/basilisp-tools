"""Callable annotations retain defaults and concrete implementation binding."""
import importlib

import pytest

import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp


SOURCE = '''from typing import Callable, Generic, Protocol, TypeVar, NewType, no_type_check, overload
class Callback(Protocol):
    def __call__(self, y: int, a: int = 0) -> bool: ...
UserId = NewType('UserId', int)
first: Callable[[int, int], bool] = lambda y, a=0: a == y
second: Callback = lambda y, a=0: a == y
plain = lambda value, *, suffix='x': str(value) + suffix
positional = lambda value, /: value
private_keyword = lambda __value: __value
class _PrivateLambda:
    call = lambda self, __value: __value
    keyword = lambda self, *, __value: __value
class ___:
    call = lambda self, __value: __value
class C:
    @overload
    @staticmethod
    def method(self, x: int) -> int: ...
    @overload
    @staticmethod
    def method(self, x: str) -> str: ...
    def method(self, x: int | str) -> int | str: return x
    @overload
    @classmethod
    def create(cls, x: int) -> int: ...
    @overload
    @classmethod
    def create(cls, x: str) -> str: ...
    def create(cls, x: int | str) -> int | str: return x
    @overload
    def static(self, x: int) -> int: ...
    @overload
    def static(self, x: str) -> str: ...
    @staticmethod
    def static(self, x: int | str) -> int | str: return x
    @overload
    def bound(cls, x: int) -> int: ...
    @overload
    @classmethod
    def bound(cls, x: str) -> str: ...
    @classmethod
    def bound(cls, x: int | str) -> int | str: return x
T = TypeVar('T')
class Functor(Generic[T]):
    def __call__(self, value: T) -> T: return value
functor: Functor[int] = Functor()
def factory() -> Functor[int]: return Functor()
class Holder:
    @property
    def callback(self) -> Functor[int]: return Functor()
class Hook:
    def __getattribute__(self, name): raise RuntimeError(name)
    def __call__(self, value: int) -> str: return str(value)
class LambdaSlots:
    __new__ = lambda cls, value: object.__new__(cls)
    __init__ = lambda self, value: None
    __class_getitem__ = lambda cls, value: value
@no_type_check
class Unchecked:
    run: Callable[[object, int], bool] = lambda self, value: value
def integer(value: int) -> int: return value
def text(value: str) -> str: return value
def boolean(value: bool) -> bool: return value
'''


@pytest.fixture(scope="module")
def environment(tmp_path_factory):
    root = tmp_path_factory.mktemp("callable_binding")
    (root / "call_bindings.py").write_text(SOURCE)
    analyzer = importlib.import_module("basilisp_tools.analyzer")
    bridge = importlib.import_module("basilisp_tools.python")
    cache = bridge.create_cache()
    options = to_lisp({"python-options": {
        "python-paths": [str(root)], "python-inspection?": False, "python-cache": cache,
    }})
    try:
        yield analyzer, options
    finally:
        bridge.stop_cache__BANG__(cache)


@pytest.mark.parametrize("body,kind", [
    ('(m/integer (m/UserId 42))', None),
    ('(let [id m/UserId] (m/integer (id 42)))', None),
    ('(m/boolean (m/first 20))', None),
    ('(m/boolean (m/first 20 0))', None),
    ('(m/first 20 ** :a 0)', None),
    ('(m/first)', 'invalid-arity'),
    ('(m/first 1 2 3)', 'invalid-arity'),
    ('(m/first "x")', 'type-mismatch'),
    ('(m/text (m/first 20))', 'type-mismatch'),
    ('(m/boolean (m/second 20))', None),
    ('(m/text (m/second 20))', 'type-mismatch'),
    ('(m/second "x")', 'type-mismatch'),
    ('(let [callback m/second] (m/boolean (callback 20)))', None),
    ('(let [callback m/second] (callback "x"))', 'type-mismatch'),
    ('(m/boolean (apply-kw m/second 20 {:a 0}))', None),
    ('(apply-kw m/second 20 {:a "x"})', 'type-mismatch'),
    ('(m/plain 1 ** :suffix "!")', None),
    ('(m/plain 1 "!")', 'invalid-arity'),
    ('(m/positional ** :value 1)', 'invalid-arity'),
    ('(m/private_keyword ** :__value 1)', None),
    ('(m/private_keyword)', 'invalid-arity'),
    ('(.call (m/_PrivateLambda) ** :_PrivateLambda__value 1)', None),
    ('(.call (m/_PrivateLambda) ** :__value 1)', 'invalid-arity'),
    ('(.keyword (m/_PrivateLambda) ** :_PrivateLambda__value 1)', None),
    ('(.keyword (m/_PrivateLambda) 1)', 'invalid-arity'),
    ('(.call (m/___) ** :__value 1)', None),
    ('(m/integer (.method (m/C) 1))', None),
    ('(m/text (.method (m/C) "x"))', None),
    ('(.method (m/C) #py [])', 'type-mismatch'),
    ('(m/integer (m/C.create m/C 1))', None),
    ('(m/text (m/C.create m/C "x"))', None),
    ('(m/C.create m/C #py [])', 'type-mismatch'),
    ('(m/integer (.static (m/C) nil 1))', None),
    ('(.static (m/C) 1)', 'invalid-arity'),
    ('(m/integer (m/C.bound 1))', None),
    ('(m/text (.bound (m/C) "x"))', None),
    ('(m/C.bound m/C 1)', 'invalid-arity'),
    ('(m/integer (m/functor 1))', None),
    ('(m/functor "x")', 'type-mismatch'),
    ('(m/integer ((m/factory) 1))', None),
    ('((m/factory) "x")', 'type-mismatch'),
    ('(m/integer (.callback (m/Holder) 1))', None),
    ('(.callback (m/Holder) "x")', 'type-mismatch'),
    ('(m/text ((m/Hook) 1))', None),
    ('((m/Hook) "x")', 'type-mismatch'),
    ('(m/LambdaSlots 1)', None),
    ('(m/LambdaSlots)', 'invalid-arity'),
    ('(aget m/LambdaSlots 1)', None),
    ('(.run (m/Unchecked) "x")', None),
    ('(.run (m/Unchecked))', 'invalid-arity'),
])
def test_concrete_binding(environment, body, kind):
    analyzer, options = environment
    result = analyzer.analyze('(ns callable-binding (:import [call_bindings :as m]))\n' + body, options)
    assert [finding[k("type")] for finding in result[k("findings")]] == ([] if kind is None else [k(kind)])


def test_native_binding():
    namespace = {}
    exec(SOURCE, namespace)
    assert namespace['first'](20) is False
    assert namespace['second'](20) is False
    owner = namespace['C']
    assert owner().method(1) == 1
    assert owner.create(owner, 1) == 1
    assert owner().static(None, 1) == 1
    assert owner.bound(1) == 1
    assert namespace['functor'](1) == 1
    assert namespace['factory']()(1) == 1
    assert namespace['Holder']().callback(1) == 1
    assert namespace['Hook']()(1) == '1'
    assert isinstance(namespace['LambdaSlots'](1), namespace['LambdaSlots'])
    assert namespace['LambdaSlots'][1] == 1
    assert namespace['Unchecked']().run('x') == 'x'
    assert namespace['private_keyword'](__value=1) == 1
    assert namespace['_PrivateLambda']().call(_PrivateLambda__value=1) == 1
    assert namespace['_PrivateLambda']().keyword(_PrivateLambda__value=1) == 1
    assert namespace['___']().call(__value=1) == 1
    with pytest.raises(TypeError):
        namespace['_PrivateLambda']().call(__value=1)
    with pytest.raises(TypeError):
        owner().static(1)
