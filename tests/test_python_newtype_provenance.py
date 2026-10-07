"""Only genuine, unchanged typing factories produce NewType constructors."""
import importlib
import importlib.util
from pathlib import Path
import typing

import pytest
import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp

COMMON='''from typing import NewType
import typing
class Base:
    def __call__(self, value:str)->bytes:return value.encode()
class API:
    @staticmethod
    def NewType(name:str,base:type[Base])->int:return 1
def integer(value:int)->int:return value
'''

CASES=[
 ('foreign_method', "value=API.NewType('V',Base)"),
 ('qualified_write', "typing.NewType=API.NewType\nvalue=typing.NewType('V',Base)"),
 ('same_line_write', "typing.NewType=API.NewType; value=typing.NewType('V',Base)"),
 ('tuple_write', "typing.NewType,other=API.NewType,1\nvalue=typing.NewType('V',Base)"),
 ('setattr_write', "setattr(typing,'NewType',API.NewType)\nvalue=typing.NewType('V',Base)"),
 ('delete_write', "del typing.NewType\ntyping.NewType=API.NewType\nvalue=typing.NewType('V',Base)"),
 ('module_rebinding', "typing=API\nvalue=typing.NewType('V',Base)"),
 ('function_rebinding', "def NewType(name,base)->int:return 1\nvalue=NewType('V',Base)"),
 ('class_rebinding', "class NewType:\n def __new__(cls,name,base)->int:return 1\nvalue=NewType('V',Base)"),
 ('lambda_rebinding', "NewType=lambda name,base:1\nvalue=NewType('V',Base)"),
]


@pytest.fixture(scope='module')
def worker():
    path=Path(__file__).resolve().parents[1]/'src/basilisp_tools/_inspect.py'
    spec=importlib.util.spec_from_file_location('_newtype_provenance',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def contracts(tmp_path_factory):
    root=tmp_path_factory.mktemp('newtype_provenance')
    for name,body in CASES:(root/(name+'.py')).write_text(COMMON+body+'\n')
    analyzer=importlib.import_module('basilisp_tools.analyzer');bridge=importlib.import_module('basilisp_tools.python')
    cache=bridge.create_cache();options=to_lisp({'python-options':{'python-paths':[str(root)],'python-inspection?':False,'python-cache':cache}})
    try:yield root,analyzer,options
    finally:bridge.stop_cache__BANG__(cache)


@pytest.mark.parametrize('name,body',CASES)
def test_fake_factory_does_not_invent_constructor(worker,contracts,name,body):
    root,analyzer,options=contracts
    metadata=worker.StaticInspector([str(root)],[]).module(name)['members']['value']
    assert not metadata.get('new-type-constructor?')
    result=analyzer.analyze('(ns provenance (:import ['+name+' :as m]))\n(m/integer m/value)',options)
    assert list(result[k('findings')]) == []
    original=typing.NewType
    try:
        namespace={};exec(COMMON+body,namespace)
        assert namespace['integer'](namespace['value'])==1
    finally:typing.NewType=original


@pytest.mark.parametrize('prefix,call',[
    ('from typing import NewType','NewType'),
    ('from typing import NewType as NT','NT'),
    ('import typing as t','t.NewType'),
    ('from typing_extensions import NewType as NT','NT'),
])
def test_trusted_factory_aliases(worker,tmp_path,prefix,call):
    (tmp_path/'fixture.py').write_text(prefix+"\nV="+call+"('V',int)\nAlias=V\nvalue=Alias(1)\n")
    info=worker.StaticInspector([str(tmp_path)],[]).module('fixture')['members']
    for name in ['V','Alias']:
        assert info[name]['new-type-constructor?']
        assert info[name]['kind']=='function'
    assert info['value']['type-path']==['int']


def test_constructor_import_retains_marker(worker,tmp_path):
    (tmp_path/'definitions.py').write_text("from typing import NewType\nV=NewType('V',int)\n")
    (tmp_path/'fixture.py').write_text('from definitions import V\nAlias=V\nvalue=Alias(1)\n')
    info=worker.StaticInspector([str(tmp_path)],[]).module('fixture')['members']
    assert info['V']['new-type-constructor?'] and info['Alias']['new-type-constructor?']
    assert info['value']['type-path']==['int']


@pytest.mark.parametrize('local',[
    'NewType=API.NewType',
    'typing=API',
    'ignored=(NewType:=API.NewType)',
])
def test_class_local_factory_shadow_is_unknown(worker,tmp_path,local):
    call='typing.NewType' if local=='typing=API' else 'NewType'
    source=COMMON+'class Holder:\n '+local+"\n value="+call+"('V',Base)\n"
    (tmp_path/'fixture.py').write_text(source)
    value=worker.StaticInspector([str(tmp_path)],[]).module('fixture')['members']['Holder']['members']['value']
    assert not value.get('new-type-constructor?')
    namespace={};exec(source,namespace);assert namespace['Holder'].value==1


def test_later_import_does_not_retroactively_prove_factory(worker,tmp_path):
    (tmp_path/'foreign.py').write_text('def NewType(name,base):return 1\n')
    (tmp_path/'fixture.py').write_text("import foreign as typing\nvalue=typing.NewType('V',int)\nimport typing\n")
    value=worker.StaticInspector([str(tmp_path)],[]).module('fixture')['members']['value']
    assert not value.get('new-type-constructor?') and value.get('type-any?')


def test_same_line_trusted_import_precedes_factory(worker,tmp_path):
    (tmp_path/'fixture.py').write_text("from typing import NewType; V=NewType('V',int)\n")
    assert worker.StaticInspector([str(tmp_path)],[]).module('fixture')['members']['V']['new-type-constructor?']


def test_local_typing_module_is_not_executed_or_trusted(worker,tmp_path):
    (tmp_path/'typing.py').write_text("raise RuntimeError('must not execute')\ndef NewType(name,base)->int:return 1\n")
    (tmp_path/'fixture.py').write_text("import typing\nvalue=typing.NewType('V',int)\n")
    value=worker.StaticInspector([str(tmp_path)],[]).module('fixture')['members']['value']
    assert not value.get('new-type-constructor?')
