"""Executable annotations and initializer receiver escapes invalidate specialization."""
import importlib
import importlib.util
from pathlib import Path
import pytest
import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp

CASES = [
    ('vararg_annotation',
     'from typing import Generic,TypeVar\n'
     "T=TypeVar('T')\n"
     "def replacement(self,value):self.value='ok'\n"
     'def mutate(function):\n'
     ' function.__code__=replacement.__code__\n'
     ' return int\n'
     'class C(Generic[T]):\n'
     ' value:T\n'
     ' def __init__(self,value:T):self.value=value\n'
     ' def method(self,*args:mutate(__init__)):pass\n'
     'x=C(1)\n'
    ),
    ('kwarg_annotation',
     'from typing import Generic,TypeVar\n'
     "T=TypeVar('T')\n"
     "def replacement(self,value):self.value='ok'\n"
     'def mutate(function):\n'
     ' function.__code__=replacement.__code__\n'
     ' return int\n'
     'class C(Generic[T]):\n'
     ' value:T\n'
     ' def __init__(self,value:T):self.value=value\n'
     ' def method(self,**kwargs:mutate(__init__)):pass\n'
     'x=C(1)\n'
    ),
    ('field_annotation',
     'from typing import Generic,TypeVar\n'
     "T=TypeVar('T')\n"
     "def replacement(self,value):self.value='ok'\n"
     'def mutate(function):\n'
     ' function.__code__=replacement.__code__\n'
     ' return int\n'
     'class C(Generic[T]):\n'
     ' value:T\n'
     ' def __init__(self,value:T):self.value=value\n'
     ' other:mutate(__init__)\n'
     'x=C(1)\n'
    ),
    ('subscription_annotation',
     'from typing import Generic,TypeVar\n'
     "T=TypeVar('T')\n"
     "def replacement(self,value):self.value='ok'\n"
     'def mutate(function):\n'
     ' function.__code__=replacement.__code__\n'
     ' return int\n'
     'class Active:\n'
     ' def __class_getitem__(cls,function):return mutate(function)\n'
     'class C(Generic[T]):\n'
     ' value:T\n'
     ' def __init__(self,value:T):self.value=value\n'
     ' def method(self,value:Active[__init__]):pass\n'
     'x=C(1)\n'
    ),
    ('constructor_rebinds_inert_method',
     'from typing import Generic, TypeVar\n'
     'T=TypeVar("T")\n'
     'class C(Generic[T]):\n'
     '    value:T\n'
     '    def __init__(self,value:T):\n'
     '        self.value=value\n'
     '        self.change=lambda:setattr(type(self),"__init__",lambda self,value:setattr(self,"value","ok"))\n'
     '    def change(self):pass\n'
     'first=C(0)\n'
     'first.change()\n'
     'x=C(1)\n'
    ),
    ('constructor_changes_inert_class_method',
     'from typing import Generic, TypeVar\n'
     'T=TypeVar("T")\n'
     'class C(Generic[T]):\n'
     '    value:T\n'
     '    def __init__(self,value:T):\n'
     '        self.value=value\n'
     '        type(self).change=lambda self:setattr(type(self),"__init__",lambda self,value:setattr(self,"value","ok"))\n'
     '    def change(self):pass\n'
     'first=C(0)\n'
     'first.change()\n'
     'x=C(1)\n'
    ),
    ('constructor_changes_own_class',
     'from typing import Generic, TypeVar\n'
     'T=TypeVar("T")\n'
     'class C(Generic[T]):\n'
     '    value:T\n'
     '    def __init__(self,value:T):\n'
     '        self.value=value\n'
     '        type(self).__init__=lambda self,value:setattr(self,"value","ok")\n'
     'first=C(0)\n'
     'x=C(1)\n'
    ),
    ('initializer_receiver_escape',
     'from typing import Generic,TypeVar\n'
     "T=TypeVar('T')\n"
     "def replacement(self,value):self.value='ok'\n"
     'def mutate(obj):\n'
     ' cls=type(obj[0] if isinstance(obj,list) else obj)\n'
     ' cls.__init__=replacement\n'
     'class C(Generic[T]):\n'
     ' value:T\n'
     ' def __init__(self,value:T):\n'
     '  self.value=value\n'
     '  mutate(self)\n'
     'first=C(0)\n'
     'x=C(1)\n'
    ),
    ('initializer_container_escape',
     'from typing import Generic,TypeVar\n'
     "T=TypeVar('T')\n"
     "def replacement(self,value):self.value='ok'\n"
     'def mutate(obj):\n'
     ' cls=type(obj[0] if isinstance(obj,list) else obj)\n'
     ' cls.__init__=replacement\n'
     'class C(Generic[T]):\n'
     ' value:T\n'
     ' def __init__(self,value:T):\n'
     '  self.value=value\n'
     '  mutate([self])\n'
     'first=C(0)\n'
     'x=C(1)\n'
    ),
]

@pytest.fixture(scope="module")
def contracts(tmp_path_factory):
    root=tmp_path_factory.mktemp("constructor_executable")
    spec=importlib.util.spec_from_file_location("_constructor_executable",Path(__file__).resolve().parents[1]/"src/basilisp_tools/_inspect.py")
    worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
    for name,source in CASES:
        (root/(name+".py")).write_text(source+"def strings(value:str)->None:pass\n")
    analyzer=importlib.import_module("basilisp_tools.analyzer")
    bridge=importlib.import_module("basilisp_tools.python")
    cache=bridge.create_cache()
    options=to_lisp({"python-options":{"python-paths":[str(root)],"python-inspection?":False,"python-cache":cache}})
    try: yield root,worker,analyzer,options
    finally:bridge.stop_cache__BANG__(cache)

@pytest.mark.parametrize("name,source",CASES)
def test_executable_class_definition_does_not_invent_generic_arguments(contracts,name,source):
    root,worker,analyzer,options=contracts
    native={};exec(source,native)
    assert native["x"].value=="ok"
    metadata=worker.StaticInspector([str(root)],[]).module(name)["members"]["x"]
    assert metadata.get("type-arguments") != [{"type-module":"builtins","type-path":["int"]}]
    result=analyzer.analyze("(ns constructor-executable (:import ["+name+" :as m])) (m/strings (.-value m/x))",options)
    assert list(result[k("findings")])==[]


def test_unchanged_inert_instance_method_preserves_negative(contracts):
    root,worker,analyzer,options=contracts
    source='from typing import Generic, TypeVar\nT=TypeVar("T")\nclass C(Generic[T]):\n    value:T\n    def __init__(self,value:T):self.value=value\n    def change(self):pass\nfirst=C(0)\nfirst.change()\nx=C(1)\ndef strings(value:str)->None:pass\n'
    (root/"inert_control.py").write_text(source)
    native={};exec(source,native)
    assert native["x"].value==1
    metadata=worker.StaticInspector([str(root)],[]).module("inert_control")["members"]["x"]
    assert metadata["type-arguments"]==[{"type-module":"builtins","type-path":["int"]}]
    result=analyzer.analyze("(ns constructor-inert (:import [inert_control :as m])) (m/strings (.-value m/x))",options)
    assert [f[k("type")] for f in result[k("findings")]]==[k("type-mismatch")]
