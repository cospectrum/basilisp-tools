"""NewType identity constructors and their callable results have distinct contracts."""
import importlib

import pytest

import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp


SOURCE = '''from typing import NewType
import typing
class Functor:
    def __call__(self, value: str) -> bytes: return value.encode()
N = NewType("N", Functor)
Qualified = typing.NewType("Qualified", Functor)
Alias = N
instance = N(Functor())
UserId = NewType("UserId", int)
user_id = UserId(5)
class Holder:
    factory = NewType("Nested", Functor)
def integer(value: int) -> int: return value
def binary(value: bytes) -> bytes: return value
def text(value: str) -> str: return value
'''


@pytest.fixture(scope="module")
def contracts(tmp_path_factory):
    root = tmp_path_factory.mktemp("newtype_callable")
    (root / "newtype_callable.py").write_text(SOURCE)
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
    ('(m/N (m/Functor))', None),
    ('(m/integer m/user-id)', None),
    ('(m/text m/user-id)', 'type-mismatch'),
    ('(m/binary (m/instance "x"))', None),
    ('(m/instance 1)', 'type-mismatch'),
    ('(m/Qualified (m/Functor))', None),
    ('(m/Alias (m/Functor))', None),
    ('(m/Holder.factory (m/Functor))', None),
    ('(.factory (m/Holder) (m/Functor))', None),
    ('(let [constructor m/N] (constructor (m/Functor)))', None),
    ('(m/binary ((m/N (m/Functor)) "x"))', None),
    ('((m/N (m/Functor)) 1)', 'type-mismatch'),
    ('(m/text ((m/N (m/Functor)) "x"))', 'type-mismatch'),
])
def test_newtype_callable_contract(contracts, body, kind):
    analyzer, options = contracts
    result = analyzer.analyze('(ns newtype-callable (:import [newtype_callable :as m]))\n' + body, options)
    assert [f[k("type")] for f in result[k("findings")]] == ([] if kind is None else [k(kind)])


def test_native_identity_constructor():
    namespace = {}
    exec(SOURCE, namespace)
    value = namespace['Functor']()
    for constructor in (namespace['N'], namespace['Qualified'], namespace['Alias'],
                        namespace['Holder'].factory, namespace['Holder']().factory):
        assert constructor(value) is value
        assert constructor(value)('x') == b'x'
