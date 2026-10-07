"""Static callback capture requires an unchanged ordinary local function."""
import importlib
import importlib.util
from pathlib import Path

import pytest

import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp


HEADER = '''from typing import Callable, Generic, ParamSpec, TypeVar, overload
P = ParamSpec("P")
R = TypeVar("R")
class Remote(Generic[P, R]):
    def __init__(self, function: Callable[P, R]): self.function = function
    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        return self.function(*args, **kwargs)
def text(value: str) -> str: return value
'''
FUNCTION = 'def f(x: int) -> str: return str(x)\n'

# Each expression succeeds natively. The wrapper's captured shape must remain
# unknown because the function object no longer has its declared signature.
MUTATIONS = [
    ('defaults', FUNCTION + 'f.__defaults__ = (1,)\nremote = Remote(f)\n', 'remote()'),
    ('same_line', FUNCTION + 'f.__defaults__ = (1,); remote = Remote(f)\n', 'remote()'),
    ('alias', FUNCTION + 'alias = f\nalias.__defaults__ = (1,)\nremote = Remote(f)\n', 'remote()'),
    ('alias_chain', FUNCTION + 'first = f\nsecond = first\nsecond.__defaults__ = (1,)\nremote = Remote(first)\n', 'remote()'),
    ('tuple_alias', FUNCTION + 'alias, other = f, 2\nalias.__defaults__ = (1,)\nremote = Remote(f)\n', 'remote()'),
    ('container_alias', FUNCTION + 'values = [f]\nvalues[0].__defaults__ = (1,)\nremote = Remote(f)\n', 'remote()'),
    ('setattr', FUNCTION + 'setattr(f, "__defaults__", (1,))\nremote = Remote(f)\n', 'remote()'),
    ('setter_alias', 'write = setattr\n' + FUNCTION + 'write(f, "__defaults__", (1,))\nremote = Remote(f)\n', 'remote()'),
    ('imported_setter', 'from builtins import setattr as write\n' + FUNCTION + 'write(f, "__defaults__", (1,))\nremote = Remote(f)\n', 'remote()'),
    ('qualified_setter', 'import builtins\n' + FUNCTION + 'builtins.setattr(f, "__defaults__", (1,))\nremote = Remote(f)\n', 'remote()'),
    ('kwdefaults', 'def f(*, flag: bool) -> str: return str(flag)\nf.__kwdefaults__ = {"flag": True}\nremote = Remote(f)\n', 'remote()'),
    ('code', FUNCTION + 'def replacement() -> str: return "ok"\nf.__code__ = replacement.__code__\nremote = Remote(f)\n', 'remote()'),
    ('annotations', FUNCTION + 'f.__annotations__ = {}\nremote = Remote(f)\n', 'remote("ok")'),
    ('annotation_item', FUNCTION + 'f.__annotations__["x"] = str\nremote = Remote(f)\n', 'remote("ok")'),
    ('annotation_update', FUNCTION + 'f.__annotations__.update(x=str)\nremote = Remote(f)\n', 'remote("ok")'),
    ('annotation_delete', FUNCTION + 'del f.__annotations__["x"]\nremote = Remote(f)\n', 'remote("ok")'),
    ('signature', 'import inspect\n' + FUNCTION + 'f.__signature__ = inspect.Signature()\nremote = Remote(f)\n', 'remote(1)'),
    ('unproven_attribute_call', FUNCTION + 'setattr(f, "label", "unrelated")\nremote = Remote(f)\n', 'remote(1)'),
    ('delattr', FUNCTION + 'delattr(f, "__defaults__")\nremote = Remote(f)\n', 'remote(1)'),
    ('later_write', FUNCTION + 'remote = Remote(f)\nf.__defaults__ = (1,)\n', 'remote()'),
    ('later_alias_write', FUNCTION + 'remote = Remote(f); alias = f; alias.__defaults__ = (1,)\n', 'remote()'),
    ('namespace_lookup', FUNCTION + 'globals()["f"].__defaults__ = (1,)\nremote = Remote(f)\n', 'remote()'),
    ('namespace_alias', FUNCTION + 'namespace = globals()\nnamespace["f"].__defaults__ = (1,)\nremote = Remote(f)\n', 'remote()'),
    ('escape', FUNCTION + 'def mutate(g): g.__defaults__ = (1,)\nmutate(f)\nremote = Remote(f)\n', 'remote()'),
    ('earlier_mutator', 'def mutate(): f.__defaults__ = (1,)\n' + FUNCTION + 'mutate()\nremote = Remote(f)\n', 'remote()'),
    ('earlier_field_escape', 'class Sink:\n    def __setattr__(self, name, g): g.__defaults__ = (1,)\ndef mutate(): sink.callback = f\n' + FUNCTION + 'sink = Sink()\nmutate()\nremote = Remote(f)\n', 'remote()'),
    ('inert_destructor', 'class Touch:\n    def __init__(self, g): pass\n    def __del__(self): f.__defaults__ = (1,)\n' + FUNCTION + 'Touch(f)\nremote = Remote(f)\n', 'remote()'),
    ('keyword_escape', FUNCTION + 'def mutate(*, g): g.__defaults__ = (1,)\nmutate(g=f)\nremote = Remote(f)\n', 'remote()'),
    ('container_escape', FUNCTION + 'def mutate(values): values[0].__defaults__ = (1,)\nmutate([f])\nremote = Remote(f)\n', 'remote()'),
    ('imported_escape', FUNCTION + 'from callback_external import mutate\nmutate(f)\nremote = Remote(f)\n', 'remote()'),
    ('closure_escape', FUNCTION + 'def expose(): return f\ndef mutate(g): g.__defaults__ = (1,)\nmutate(expose())\nremote = Remote(f)\n', 'remote()'),
    ('inert_code_replaced', FUNCTION + 'def harmless(g): pass\ndef mutate(g): g.__defaults__ = (1,)\nharmless.__code__ = mutate.__code__\nharmless(f)\nremote = Remote(f)\n', 'remote()'),
    ('inert_code_alias_replaced', FUNCTION + 'def harmless(g): pass\ndef mutate(g): g.__defaults__ = (1,)\nalias = harmless\nalias.__code__ = mutate.__code__\nharmless(f)\nremote = Remote(f)\n', 'remote()'),
    ('inert_initializer_replaced', FUNCTION + 'class Pair:\n    def __init__(self, g): pass\ndef mutate(self, g): g.__defaults__ = (1,)\nPair.__init__ = mutate\nPair(f)\nremote = Remote(f)\n', 'remote()'),
    ('inert_descriptor_replaces_initializer', FUNCTION + 'class Rewrite:\n    def __set_name__(self, owner, name):\n        owner.__init__ = lambda self, g: setattr(g, "__defaults__", (1,))\nclass Pair:\n    field = Rewrite()\n    def __init__(self, g): pass\nPair(f)\nremote = Remote(f)\n', 'remote()'),
    ('inert_method_decorator_replaces_initializer', FUNCTION + 'class Rewrite:\n    def __init__(self, method): pass\n    def __set_name__(self, owner, name):\n        owner.__init__ = lambda self, g: setattr(g, "__defaults__", (1,))\nclass Pair:\n    def __init__(self, g): pass\n    @Rewrite\n    def ordinary(self): pass\nPair(f)\nremote = Remote(f)\n', 'remote()'),
    ('inert_rebound', FUNCTION + 'def harmless(g): pass\ndef mutate(g): g.__defaults__ = (1,)\nharmless = mutate\nharmless(f)\nremote = Remote(f)\n', 'remote()'),
    ('directive_mutation', 'import typing_extensions as t\n' + FUNCTION + 'def mutate(g): g.__defaults__ = (1,)\noriginal = t.reveal_type\nt.reveal_type = mutate\nt.reveal_type(f)\nt.reveal_type = original\nremote = Remote(f)\n', 'remote()'),
    ('field_escape', FUNCTION + 'class Sink:\n    def __setattr__(self, name, g): g.__defaults__ = (1,)\nsink = Sink()\nsink.callback = f\nremote = Remote(f)\n', 'remote()'),
    ('constructor_rebound', FUNCTION + 'remote = Remote(f)\ndef mutate(g): g.__defaults__ = (1,)\nRemote = mutate\nRemote(f)\n', 'remote()'),
    ('constructor_mutation', FUNCTION + '''class MutatingRemote(Generic[P, R]):
    def __init__(self, function: Callable[P, R]):
        function.__defaults__ = (1,)
        self.function = function
    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        return self.function(*args, **kwargs)
remote = MutatingRemote(f)
''', 'remote()'),
]


@pytest.fixture(scope='module')
def worker():
    path = Path(__file__).resolve().parents[1] / 'src/basilisp_tools/_inspect.py'
    spec = importlib.util.spec_from_file_location('_callback_provenance', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def contracts(tmp_path_factory):
    root = tmp_path_factory.mktemp('callback_provenance')
    (root / 'callback_external.py').write_text('def mutate(g): g.__defaults__ = (1,)\n')
    for name, source, _ in MUTATIONS:
        (root / (name + '.py')).write_text(HEADER + source)
    analyzer = importlib.import_module('basilisp_tools.analyzer')
    bridge = importlib.import_module('basilisp_tools.python')
    cache = bridge.create_cache()
    options = to_lisp({'python-options': {'python-paths': [str(root)], 'python-inspection?': False,
                                        'python-cache': cache}})
    try:
        yield root, analyzer, options
    finally:
        bridge.stop_cache__BANG__(cache)


@pytest.mark.parametrize('name,source,expression', MUTATIONS)
def test_mutated_callback_remains_unknown(worker, contracts, monkeypatch, name, source, expression):
    root, analyzer, options = contracts
    info = worker.StaticInspector([str(root)], []).module(name)['members']['remote']
    # A declined constructor proof preserves its unspecialized nominal class;
    # a proved constructor with an uncertain callback explicitly carries Any.
    assert info.get('type-arguments') in (None, [{'type-any?': True}, {'type-any?': True}])
    native = {}
    monkeypatch.syspath_prepend(str(root))
    exec(HEADER + source, native)
    assert isinstance(eval(expression, native), str)
    # This valid zero-argument call used to inherit the stale required x flag.
    if expression == 'remote()':
        result = analyzer.analyze('(ns callback-provenance (:import [' + name + ' :as m]))\n(m/text (m/remote))', options)
        assert list(result[k('findings')]) == []


def inspected_remote(worker, root, source):
    (root / 'fixture.py').write_text(HEADER + source)
    return worker.StaticInspector([str(root)], []).module('fixture')['members']['remote']['type-arguments']


@pytest.mark.parametrize('source', [
    '''@overload
def f(x: int) -> int: ...
@overload
def f(x: str) -> str: ...
def f(x): return x
remote = Remote(f)
''',
    FUNCTION + 'old = f\ndef f(x: str) -> str: return x\nremote = Remote(old)\n',
    'def identity(function): return function\n@identity\n' + FUNCTION + 'remote = Remote(f)\n',
    'from other import f\nremote = Remote(f)\n',
    'from functools import partial\n' + FUNCTION + 'callback = partial(f)\nremote = Remote(callback)\n',
    FUNCTION + 'def factory() -> Callable[[int], str]: return f\ncallback = factory()\nremote = Remote(callback)\n',
])
def test_nonordinary_callback_does_not_capture_pack(worker, tmp_path, source):
    (tmp_path / 'other.py').write_text(FUNCTION)
    assert inspected_remote(worker, tmp_path, source) == [{'type-any?': True}, {'type-any?': True}]


@pytest.mark.parametrize('source', [
    FUNCTION + 'remote = Remote(f)\n',
    FUNCTION + 'alias = f\nremote = Remote(alias)\n',
    FUNCTION + 'first = f\nsecond = first\nremote = Remote(second)\n',
    FUNCTION + 'def harmless(g): pass\nharmless(f)\nremote = Remote(f)\n',
    FUNCTION + 'f.label = "unrelated"\nremote = Remote(f)\n',
])
def test_unchanged_local_callback_still_captures(worker, tmp_path, source):
    parameters, result = inspected_remote(worker, tmp_path, source)
    assert parameters['parameter-list?']
    assert parameters['parameters'][0]['name'] == 'x'
    assert parameters['parameters'][0]['required?']
    assert result == {'type-module': 'builtins', 'type-path': ['str']}
