"""Method syntax invokes the value produced by a descriptor read."""
import importlib

import pytest

import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp


SOURCE = '''from typing import Callable
class Meta(type):
    @property
    def run(cls) -> Callable[[str], int]:
        return lambda value: len(value)
class Owner(metaclass=Meta):
    @property
    def run(self) -> Callable[[int], str]:
        return lambda value: str(value + 1)
def integer(value: int) -> int: return value
def string(value: str) -> str: return value
'''


@pytest.fixture(scope="module")
def environment(tmp_path_factory):
    root = tmp_path_factory.mktemp("descriptor_call_values")
    (root / "call_values.py").write_text(SOURCE)
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


@pytest.mark.parametrize("body,valid", [
    ('(m/integer (.run m/Owner "abc"))', True),
    ('(m/string (.run m/Owner "abc"))', False),
    ('(.run m/Owner 1)', False),
    ('(m/integer ((.-run m/Owner) "abc"))', True),
    ('((.-run m/Owner) 1)', False),
    ('(m/integer (m/Owner.run "abc"))', True),
    ('(m/Owner.run 1)', False),
    ('(m/string (.run (m/Owner) 1))', True),
    ('(m/integer (.run (m/Owner) 1))', False),
    ('(.run (m/Owner) "abc")', False),
])
def test_callable_descriptor_read_is_invoked(environment, body, valid):
    analyzer, options = environment
    result = analyzer.analyze('(ns descriptor-calls (:import [call_values :as m]))\n' + body, options)
    assert [finding[k("type")] for finding in result[k("findings")]] == (
        [] if valid else [k("type-mismatch")]
    )


def test_native_callable_descriptor_contexts():
    namespace = {}
    exec(SOURCE, namespace)
    owner = namespace["Owner"]
    assert owner.run("abc") == 3
    assert owner().run(1) == "2"
    with pytest.raises(TypeError):
        owner.run(1)
    with pytest.raises(TypeError):
        owner().run("abc")
