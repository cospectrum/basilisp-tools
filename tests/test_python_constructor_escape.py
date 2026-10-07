"""Executable annotations and initializer receiver escapes invalidate specialization."""
import importlib
import importlib.util
from pathlib import Path
import pytest
import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp

CASES = [
    ('default_accessor',
     'from typing import Generic, TypeVar\n'
     'T=TypeVar("T")\n'
     'class C(Generic[T]):\n'
     '    value:T\n'
     '    def __init__(self,value:T):self.value=value\n'
     'def expose(cls=C):return cls\n'
     'expose().__init__=lambda self,value:setattr(self,"value","ok")\n'
     'x=C(1)\n'
    ),
    ('class_held_alias',
     'from typing import Generic, TypeVar\n'
     'T=TypeVar("T")\n'
     'class C(Generic[T]):\n'
     '    value:T\n'
     '    def __init__(self,value:T):self.value=value\n'
     'class Holder:\n'
     '    owner=C\n'
     'Holder.owner.__init__=lambda self,value:setattr(self,"value","ok")\n'
     'x=C(1)\n'
    ),
    ('class_held_default_accessor',
     'from typing import Generic, TypeVar\n'
     'T=TypeVar("T")\n'
     'class C(Generic[T]):\n'
     '    value:T\n'
     '    def __init__(self,value:T):self.value=value\n'
     'class Holder:\n'
     '    @staticmethod\n'
     '    def expose(cls=C):return cls\n'
     'Holder.expose().__init__=lambda self,value:setattr(self,"value","ok")\n'
     'x=C(1)\n'
    ),
    ('class_held_accessor',
     'from typing import Generic, TypeVar\n'
     'T=TypeVar("T")\n'
     'class C(Generic[T]):\n'
     '    value:T\n'
     '    def __init__(self,value:T):self.value=value\n'
     'class Holder:\n'
     '    def expose():return C\n'
     'Holder.expose().__init__=lambda self,value:setattr(self,"value","ok")\n'
     'x=C(1)\n'
    ),
    ('loop_alias',
     'from typing import Generic, TypeVar\n'
     'T=TypeVar("T")\n'
     'class C(Generic[T]):\n'
     '    value:T\n'
     '    def __init__(self,value:T):self.value=value\n'
     'for cls in [C]:\n'
     '    cls.__init__=lambda self,value:setattr(self,"value","ok")\n'
     'x=C(1)\n'
    ),
    ('comprehension_alias',
     'from typing import Generic, TypeVar\n'
     'T=TypeVar("T")\n'
     'class C(Generic[T]):\n'
     '    value:T\n'
     '    def __init__(self,value:T):self.value=value\n'
     '[setattr(cls,"__init__",lambda self,value:setattr(self,"value","ok")) for cls in [C]]\n'
     'x=C(1)\n'
    ),
    ('match_alias',
     'from typing import Generic, TypeVar\n'
     'T=TypeVar("T")\n'
     'class C(Generic[T]):\n'
     '    value:T\n'
     '    def __init__(self,value:T):self.value=value\n'
     'match C:\n'
     '    case cls:\n'
     '        cls.__init__=lambda self,value:setattr(self,"value","ok")\n'
     'x=C(1)\n'
    ),
    ('walrus_alias',
     'from typing import Generic, TypeVar\n'
     'T=TypeVar("T")\n'
     'class C(Generic[T]):\n'
     '    value:T\n'
     '    def __init__(self,value:T):self.value=value\n'
     '(cls:=C)\n'
     'cls.__init__=lambda self,value:setattr(self,"value","ok")\n'
     'x=C(1)\n'
    ),
    ('forward_global_accessor',
     'from typing import Generic, TypeVar\n'
     'T=TypeVar("T")\n'
     'class C(Generic[T]):\n'
     '    value:T\n'
     '    def __init__(self,value:T):self.value=value\n'
     'def expose():return Later\n'
     'Later=C\n'
     'expose().__init__=lambda self,value:setattr(self,"value","ok")\n'
     'x=C(1)\n'
    ),
    ('subscription_expr',
     'from typing import Generic, TypeVar\n'
     'T=TypeVar("T")\n'
     'class C(Generic[T]):\n'
     '    value:T\n'
     '    def __init__(self,value:T):self.value=value\n'
     'class Active:\n'
     '    def __class_getitem__(cls, target):\n'
     '        target.__init__=lambda self,value:setattr(self,"value","ok")\n'
     '        return target\n'
     'Active[C]\n'
     'x=C(1)\n'
    ),
    ('subscription_alias',
     'from typing import Generic, TypeVar\n'
     'T=TypeVar("T")\n'
     'class C(Generic[T]):\n'
     '    value:T\n'
     '    def __init__(self,value:T):self.value=value\n'
     'class Active:\n'
     '    def __class_getitem__(cls, target):\n'
     '        target.__init__=lambda self,value:setattr(self,"value","ok")\n'
     '        return target\n'
     'seen=Active[C]\n'
     'x=C(1)\n'
    ),
    ('operator_expr',
     'from typing import Generic, TypeVar\n'
     'T=TypeVar("T")\n'
     'class C(Generic[T]):\n'
     '    value:T\n'
     '    def __init__(self,value:T):self.value=value\n'
     'class Active:\n'
     '    def __add__(self,target):\n'
     '        target.__init__=lambda self,value:setattr(self,"value","ok")\n'
     'Active()+C\n'
     'x=C(1)\n'
    ),
    ('augassign_operator',
     'from typing import Generic, TypeVar\n'
     'T=TypeVar("T")\n'
     'class C(Generic[T]):\n'
     '    value:T\n'
     '    def __init__(self,value:T):self.value=value\n'
     'class Active:\n'
     '    def __iadd__(self,target):\n'
     '        target.__init__=lambda self,value:setattr(self,"value","ok")\n'
     '        return self\n'
     'active=Active()\n'
     'active+=C\n'
     'x=C(1)\n'
    ),
    ('comparison_expr',
     'from typing import Generic, TypeVar\n'
     'T=TypeVar("T")\n'
     'class C(Generic[T]):\n'
     '    value:T\n'
     '    def __init__(self,value:T):self.value=value\n'
     'class Active:\n'
     '    def __eq__(self,target):\n'
     '        target.__init__=lambda self,value:setattr(self,"value","ok")\n'
     '        return True\n'
     'Active()==C\n'
     'x=C(1)\n'
    ),
    ('membership_expr',
     'from typing import Generic, TypeVar\n'
     'T=TypeVar("T")\n'
     'class C(Generic[T]):\n'
     '    value:T\n'
     '    def __init__(self,value:T):self.value=value\n'
     'class Active:\n'
     '    def __contains__(self,target):\n'
     '        target.__init__=lambda self,value:setattr(self,"value","ok")\n'
     '        return True\n'
     'C in Active()\n'
     'x=C(1)\n'
    ),
    ('subscript_store',
     'from typing import Generic, TypeVar\n'
     'T=TypeVar("T")\n'
     'class C(Generic[T]):\n'
     '    value:T\n'
     '    def __init__(self,value:T):self.value=value\n'
     'class Active:\n'
     '    def __setitem__(self,target,value): target.__init__=lambda self,value:setattr(self,"value","ok")\n'
     '    def __delitem__(self,target): target.__init__=lambda self,value:setattr(self,"value","ok")\n'
     'Active()[C]=None\n'
     'x=C(1)\n'
    ),
    ('subscript_delete',
     'from typing import Generic, TypeVar\n'
     'T=TypeVar("T")\n'
     'class C(Generic[T]):\n'
     '    value:T\n'
     '    def __init__(self,value:T):self.value=value\n'
     'class Active:\n'
     '    def __setitem__(self,target,value): target.__init__=lambda self,value:setattr(self,"value","ok")\n'
     '    def __delitem__(self,target): target.__init__=lambda self,value:setattr(self,"value","ok")\n'
     'del Active()[C]\n'
     'x=C(1)\n'
    ),
    ('rebound_class_storage',
     'from typing import Generic,TypeVar\n'
     "T=TypeVar('T')\n"
     'class C(Generic[T]):\n'
     ' value:T\n'
     ' def __init__(self,value:T):self.value=value\n'
     'class Holder:\n'
     ' owner=C\n'
     'Old=Holder\n'
     'class Holder:pass\n'
     "Old.owner.__init__=lambda self,value:setattr(self,'value','ok')\n"
     'x=C(1)\n'
    ),
]


@pytest.fixture(scope="module")
def contracts(tmp_path_factory):
    root=tmp_path_factory.mktemp("constructor_escape")
    spec=importlib.util.spec_from_file_location("_constructor_escape",Path(__file__).resolve().parents[1]/"src/basilisp_tools/_inspect.py")
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
