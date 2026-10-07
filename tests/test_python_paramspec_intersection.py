"""A shared ParamSpec needs at least one call shape accepted by every callback."""
import importlib
import itertools

import pytest

import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp

SOURCE = '''from typing import Callable, Concatenate, ParamSpec
P = ParamSpec("P")
def combine(first: Callable[P, int], second: Callable[P, int]) -> Callable[P, bool]: ...
def three(first: Callable[P, int], second: Callable[P, int], third: Callable[P, int]) -> Callable[P, bool]: ...
def tails(first: Callable[Concatenate[str, P], int], second: Callable[Concatenate[str, P], int]) -> Callable[P, bool]: ...
def x(*, x: int) -> int: ...
def y(*, y: int) -> int: ...
def optional_x(*, x: int = 0) -> int: ...
def optional_y(*, y: int = 0) -> int: ...
def x_and_y(*, x: int, y: int = 0) -> int: ...
def keywords(**kwargs: int) -> int: ...
def open_x(*, x: int, **kwargs: int) -> int: ...
def positional_x(x: int) -> int: ...
def positional_y(y: int) -> int: ...
def prefixed_x(prefix: str, /, *, x: int) -> int: ...
def prefixed_y(prefix: str, /, *, y: int) -> int: ...
def empty() -> int: ...
def one(value: int, /) -> int: ...
def two(first: int, second: int, /) -> int: ...
def optional_two(first: int, second: int = 0, /) -> int: ...
def variadic(*values: int) -> int: ...
def mixed(first: int, *, x: int) -> int: ...
def accepts_one_and_keywords(value: int, /, **kwargs: int) -> int: ...
unknown: Callable[..., int]
'''

@pytest.fixture(scope="module")
def contracts(tmp_path_factory):
    root = tmp_path_factory.mktemp("paramspec_intersection")
    (root / "paramspec_intersection.pyi").write_text(SOURCE)
    bridge = importlib.import_module("basilisp_tools.python")
    analyzer = importlib.import_module("basilisp_tools.analyzer")
    cache = bridge.create_cache()
    options = to_lisp({"python-options": {"python-paths": [str(root)], "python-inspection?": False,
                                         "python-cache": cache}})
    try:
        yield analyzer, options
    finally:
        bridge.stop_cache__BANG__(cache)

@pytest.mark.parametrize("body,valid", [
    ('(m/combine m/x m/y)', False),
    ('(m/combine m/empty m/one)', False),
    ('(m/combine m/one m/two)', False),
    ('(m/combine m/two m/one)', False),
    ('(m/combine m/one m/optional-two)', True),
    ('(m/combine m/optional-two m/one)', True),
    ('(m/combine m/two m/variadic)', True),
    ('(m/combine m/variadic m/two)', True),
    ('(m/combine m/mixed m/one)', False),
    ('(m/combine m/mixed m/accepts-one-and-keywords)', True),
    ('(m/combine m/keywords m/one)', False),
    ('(m/combine m/positional-x m/x)', True),
    ('(m/combine m/y m/x)', False),
    ('(m/combine m/x m/x)', True),
    ('(m/combine m/optional-x m/optional-y)', True),
    ('(m/combine m/optional-y m/optional-x)', True),
    ('(m/combine m/x m/optional-y)', False),
    ('(m/combine m/x m/x-and-y)', True),
    ('(m/combine m/x-and-y m/x)', True),
    ('(m/combine m/x m/keywords)', True),
    ('(m/combine m/keywords m/x)', True),
    ('(m/combine m/open-x m/x-and-y)', True),
    ('(m/combine m/x m/unknown)', True),
    ('(m/combine m/unknown m/x)', True),
    ('(m/combine m/positional-x m/positional-y)', True),
    ('(m/tails m/prefixed-x m/prefixed-y)', False),
    *[('(m/three ' + ' '.join('m/' + name for name in names) + ')', False)
      for names in itertools.permutations(('x', 'y', 'keywords'))],
])
def test_shared_paramspec_domains(contracts, body, valid):
    analyzer, options = contracts
    result = analyzer.analyze('(ns paramspec-intersection (:import [paramspec_intersection :as m]))\n' + body, options)
    findings = [f[k('type')] for f in result[k('findings')]]
    assert findings == ([] if valid else [k('type-mismatch')])
