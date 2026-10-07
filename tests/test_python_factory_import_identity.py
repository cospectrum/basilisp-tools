"""A visible import-cache replacement erases trusted factory identities."""
import importlib
import importlib.util
from pathlib import Path

import pytest

import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp


@pytest.fixture(scope='module')
def worker():
    path = Path(__file__).resolve().parents[1] / 'src/basilisp_tools/_inspect.py'
    spec = importlib.util.spec_from_file_location('_factory_identity_worker', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inspect_and_check(worker, tmp_path, source, expression):
    (tmp_path / 'factory_identity.py').write_text(source)
    members = worker.StaticInspector([str(tmp_path)], []).module('factory_identity')['members']
    analyzer = importlib.import_module('basilisp_tools.analyzer')
    bridge = importlib.import_module('basilisp_tools.python')
    cache = bridge.create_cache()
    try:
        options = to_lisp({'python-options': {'python-paths': [str(tmp_path)],
                         'python-inspection?': False, 'python-cache': cache}})
        result = analyzer.analyze('(ns factory-identity (:import [factory_identity :as m]))\n' + expression, options)
        assert list(result[k('findings')]) == []
    finally:
        bridge.stop_cache__BANG__(cache)
    return members


@pytest.mark.parametrize('setup,cache', [
    ('import sys', 'sys.modules'),
    ('from sys import __dict__ as namespace', 'namespace["modules"]'),
    ('import sys', 'sys.__getattribute__("modules")'),
    ('import sys as system', 'system.modules'),
    ('from sys import modules as cache', 'cache'),
    ('import sys\ncache=sys.modules', 'cache'),
    ('import sys\n(alias:=sys)', 'alias.modules'),
    ('import sys\n(cache:=sys.modules)', 'cache'),
    ('import sys\ncache,other=sys.modules,None', 'cache'),
    ('', '__import__("sys").modules'),
    ('import importlib', 'importlib.import_module("sys").modules'),
])
def test_replaced_abc_module_is_not_a_native_abstract_proof(worker, tmp_path, setup, cache):
    source = setup + f'''
from types import SimpleNamespace
saved={cache}["abc"]
{cache}["abc"]=SimpleNamespace(ABC=object,abstractmethod=lambda f:f)
from abc import ABC,abstractmethod
class C(ABC):
 @abstractmethod
 def method(self):pass
{cache}["abc"]=saved
'''
    members = inspect_and_check(worker, tmp_path, source, '(m/C)')
    assert not members['C'].get('abstract-allocation-members')
    import sys
    saved = sys.modules['abc']
    namespace = {}
    try:
        exec(source, namespace)
        namespace['C']()
    finally:
        sys.modules['abc'] = saved


@pytest.mark.parametrize('module_name', ['typing', 'typing_extensions'])
def test_replaced_newtype_factory_can_return_an_actual_class(worker, tmp_path, module_name):
    importlib.import_module(module_name)
    source = f'''import sys
from types import SimpleNamespace
saved=sys.modules[{module_name!r}]
sys.modules[{module_name!r}]=SimpleNamespace(NewType=lambda name,base:int)
from {module_name} import NewType
UserId=NewType("UserId",int)
sys.modules[{module_name!r}]=saved
def accepts_class(value:type[object])->None:pass
'''
    members = inspect_and_check(worker, tmp_path, source, '(m/accepts-class m/UserId)')
    assert not members['UserId'].get('new-type-constructor?')
    import sys
    saved = sys.modules[module_name]
    namespace = {}
    try:
        exec(source, namespace)
        assert namespace['UserId'] is int
        namespace['accepts_class'](namespace['UserId'])
    finally:
        sys.modules[module_name] = saved


def test_replaced_partial_factory_does_not_supply_a_false_function_contract(worker, tmp_path):
    source = '''import sys
from types import SimpleNamespace
saved=sys.modules["functools"]
sys.modules["functools"]=SimpleNamespace(partial=lambda *args,**kwargs:int)
from functools import partial
def target(value:int)->str:pass
result=partial(target)
sys.modules["functools"]=saved
'''
    members = inspect_and_check(worker, tmp_path, source, '(m/result)')
    assert not members['result'].get('parameters')
    import sys
    saved = sys.modules['functools']
    namespace = {}
    try:
        exec(source, namespace)
        assert namespace['result']() == 0
    finally:
        sys.modules['functools'] = saved


@pytest.mark.parametrize('version_code', [
    'import sys',
    'import sys\nversion=sys.version_info',
    'import sys\nversion=tuple(sys.version_info)',
    'import sys as system\nplatform=system.platform',
    'from sys import version_info as version',
])
def test_ordinary_system_version_access_retains_factory_proofs(worker, tmp_path, version_code):
    source = version_code + '''
from abc import ABC,abstractmethod
from typing import NewType
class C(ABC):
 @abstractmethod
 def method(self):pass
UserId=NewType("UserId",int)
'''
    (tmp_path / 'version_guard.py').write_text(source)
    members = worker.StaticInspector([str(tmp_path)], []).module('version_guard')['members']
    assert members['C']['abstract-allocation-members'] == ['method']
    assert members['UserId']['new-type-constructor?']
