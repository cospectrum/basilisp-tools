"""A proven NewType constructor is callable, but is not itself a class."""
import importlib

import pytest

import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp


SOURCE = '''from typing import Any, Callable, Generic, NewType, Type, TypeVar
T = TypeVar("T")
class Box(Generic[T]):
    def __init__(self, value: T): self.value = value
def accepts_box(value: Box[Callable[..., int]]) -> None: pass
def same(value: T) -> T: return value
UserId = NewType("UserId", int)
Alias = UserId
TypeId = NewType("TypeId", type)
class Holder:
    factory = NewType("Nested", int)
def accepts_class(value: type[Any]) -> None: pass
def accepts_legacy_class(value: Type[object]) -> None: pass
def accepts_optional_class(value: type[Any] | None) -> None: pass
def accepts_callable(value: Callable[..., object]) -> None: pass
def accepts_object(value: object) -> None: pass
def unknown() -> Any: return int
def identity(value: Any) -> Any: return value
class CallableClass:
    def __call__(self) -> int: return 1
'''


@pytest.fixture(scope="module")
def contracts(tmp_path_factory):
    root = tmp_path_factory.mktemp("newtype_class_values")
    (root / "newtype_class_values.py").write_text(SOURCE)
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


@pytest.mark.parametrize("body,invalid", [
    ('(m/accepts-class m/UserId)', True),
    ('(m/accepts-class m/Alias)', True),
    ('(m/accepts-legacy-class m/UserId)', True),
    ('(m/accepts-optional-class m/UserId)', True),
    ('(m/accepts-class m/Holder.factory)', True),
    ('(let [factory m/UserId] (m/accepts-class factory))', True),
    ('(m/accepts-class (m/same m/UserId))', True),
    ('(m/accepts-box (m/Box m/UserId))', False),
    ('(m/accepts-callable m/UserId)', False),
    ('(m/accepts-object m/UserId)', False),
    ('(m/accepts-class m/CallableClass)', False),
    ('(m/accepts-class (m/unknown))', False),
    ('(m/accepts-class (m/identity m/UserId))', False),
    ('(m/accepts-class (m/TypeId m/CallableClass))', False),
    ('(m/accepts-optional-class nil)', False),
])
def test_newtype_class_values(contracts, body, invalid):
    analyzer, options = contracts
    result = analyzer.analyze(
        '(ns newtype-class-values (:import [newtype_class_values :as m]))\n' + body,
        options,
    )
    assert [f[k("type")] for f in result[k("findings")]] == ([k("type-mismatch")] if invalid else [])


def test_native_newtype_values():
    namespace = {}
    exec(SOURCE, namespace)
    assert not isinstance(namespace['UserId'], type)
    assert not isinstance(namespace['Alias'], type)
    assert not isinstance(namespace['Holder'].factory, type)
    assert callable(namespace['UserId'])
    assert namespace['TypeId'](int) is int
