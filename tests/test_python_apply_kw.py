"""Raw Python keyword names and call contracts survive apply-kw."""
import importlib
import pytest
import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp


@pytest.fixture(scope="module")
def contracts(tmp_path_factory):
    root = tmp_path_factory.mktemp("apply_kw_contracts")
    (root / 'keywords.pyi').write_text('''from typing import TypedDict, Unpack
Raw = TypedDict("Raw", {"in": int, "x-y": str})
def raw(**kwargs: Unpack[Raw]) -> int: ...
def ordinary(x: int, *, value: str) -> str: ...
def empty() -> int: ...
''')
    analyzer = importlib.import_module('basilisp_tools.analyzer')
    bridge = importlib.import_module('basilisp_tools.python')
    cache = bridge.create_cache()
    options = to_lisp({'python-options': {'python-paths': [str(root)], 'python-inspection?': False,
                                         'python-cache': cache}})
    try:
        yield analyzer, options
    finally:
        bridge.stop_cache__BANG__(cache)


@pytest.mark.parametrize('body,valid', [
    ('(apply-kw m/raw {"in" 1 "x-y" "x"})', True),
    ('(apply-kw m/raw {"in" "x" "x-y" "x"})', False),
    ('(apply-kw m/raw {"in" 1 "x_y" "x"})', False),
    ('(apply-kw m/ordinary 1 {:value "x"})', True),
    ('(apply-kw m/ordinary 1 {"value" "x"})', True),
    ('(apply-kw m/ordinary 1 {:ignored/value "x"})', True),
    ('(apply-kw m/ordinary 1 {:value 2})', False),
    ('(apply-kw m/ordinary 1 {})', False),
    ('(apply-kw m/empty nil)', True),
    ('(apply-kw m/empty {})', True),
    ('(apply-kw m/empty)', False),
    ('(let [kwargs {"in" 1 "x-y" "x"}] (apply-kw m/raw kwargs))', True),
    ('(defn dynamic [kwargs] (apply-kw m/raw kwargs))', True),
    ('(apply-kw m/ordinary 1 {:value "x" "value" 2})', True),
])
def test_apply_kw_contracts(contracts, body, valid):
    analyzer, options = contracts
    result = analyzer.analyze('(ns kw-test (:import [keywords :as m]))\n'+body, options)
    findings = list(result[k('findings')])
    assert not any(str(f[k('message')]).startswith('Analysis failed') for f in findings)
    if valid:
        assert findings == []
    else:
        assert any(f[k('type')] in (k('invalid-arity'), k('type-mismatch')) for f in findings)


def test_raw_keyword_conversion_matches_basilisp_runtime():
    from basilisp.lang.runtime import apply_kw

    def capture(**kwargs):
        return kwargs

    mapping = to_lisp({"in": 1, "x-y": "x"}, keywordize_keys=False).assoc(k("value", "ignored"), 2)
    assert apply_kw(capture, [mapping]) == {"in": 1, "x-y": "x", "value": 2}


def test_apply_kw_records_return_type(contracts):
    analyzer, options = contracts
    result = analyzer.analyze('(ns kw-test (:import [keywords :as m]))\n'
                              '(apply-kw m/ordinary 1 {:value "x"})', options)
    expressions = [entry for entry in result[k('python-expressions')]
                   if entry[k('row')] == 2 and entry[k('col')] == 1]
    assert expressions[-1][k('python-type')] == to_lisp({"module": "builtins", "path": ["str"]})
