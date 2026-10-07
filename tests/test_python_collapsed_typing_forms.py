"""Union subscriptions can return an actual class, unlike most typing forms."""
import importlib
from typing import Optional, Union

import pytest
import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp

@pytest.fixture(scope='module')
def environment(tmp_path_factory):
    root=tmp_path_factory.mktemp('collapsed_forms')
    (root/'collapsed.py').write_text('''from typing import Union, Optional
One = Union[int]
Duplicate = Union[int,int]
JustNone = Optional[None]
NotClass = Union[int,str]
def replace(cls): return int
@replace
class Replaced: pass
def integer_type(value:type[int])->None:pass
def any_type(value:type)->None:pass
def opaque_type()->type[object]:return int
''')
    analyzer=importlib.import_module('basilisp_tools.analyzer')
    bridge=importlib.import_module('basilisp_tools.python')
    cache=bridge.create_cache()
    options=to_lisp({'python-options':{'python-paths':[str(root)],'python-inspection?':False,'python-cache':cache}})
    try:yield analyzer,options
    finally:bridge.stop_cache__BANG__(cache)

@pytest.mark.parametrize('body,error',[
    ('(m/integer-type (aget t/Union python/int))',False),
    ('(m/integer-type (aget t/Union #py (python/int python/int)))',False),
    ('(m/any-type (aget t/Union nil))',False),
    ('(m/any-type (aget t/Optional nil))',False),
    ('(m/any-type (aget t/Union #py (nil nil)))',False),
    ('(m/any-type (aget t/Union #py (python/int python/str)))',True),
    ('(m/any-type (aget t/Union #py (python/object python/int)))',True),
    ('(m/any-type (aget t/Optional python/int))',True),
    ('(m/any-type (aget t/Union (m/opaque-type)))',False),
    ('(m/any-type (aget t/Union #py ((m/opaque-type) python/int)))',False),
    ('(m/any-type (aget t/Optional (m/opaque-type)))',False),
    ('(m/any-type (aget t/Union #py (m/Replaced python/int)))',False),
    ('(m/integer-type m/One)',False),
    ('(m/integer-type m/Duplicate)',False),
    ('(m/any-type m/JustNone)',False),
    ('(m/any-type m/NotClass)',True),
])
def test_collapsed_typing_forms(environment,body,error):
    analyzer,options=environment
    imports=' (:import [collapsed :as m]'+(' [typing :as t]' if 't/' in body else '')+')'
    result=analyzer.analyze('(ns collapsed-forms'+imports+')\n'+body,options)
    findings=list(result[k('findings')])
    assert [f[k('type')] for f in findings]==([k('type-mismatch')] if error else [])


def test_native_union_identity():
    assert Union[int] is int
    assert Union[int,int] is int
    assert Union[None] is type(None)
    assert Optional[None] is type(None)
    assert not isinstance(Union[object,int],type)
    cls: type[object]=int
    assert Union[cls,int] is int
    cls=type(None)
    assert Optional[cls] is type(None)
