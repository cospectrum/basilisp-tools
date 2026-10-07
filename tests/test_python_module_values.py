"""Literal absolute imports retain the exact module returned by Python."""
import importlib
import sys
import subprocess
from pathlib import Path
import pytest
import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp

@pytest.fixture(scope='module')
def environment(tmp_path_factory):
    root=tmp_path_factory.mktemp('module_values')
    package=root/'writes';package.mkdir()
    (package/'__init__.py').write_text('from typing import Final\nnumber: str = "root"\nclass A: pass\nclass B: pass\ndeclared: A = A()\ndeclared = B()\nfixed: Final[int] = 1\nfixed = 2\n')
    (package/'sub.py').write_text('number: int = 0\n')
    analyzer=importlib.import_module('basilisp_tools.analyzer')
    bridge=importlib.import_module('basilisp_tools.python')
    cache=bridge.create_cache()
    options=to_lisp({'python-options':{'python-paths':[str(root)],'python-inspection?':False,'python-cache':cache}})
    try:yield analyzer,options,root
    finally:bridge.stop_cache__BANG__(cache)

@pytest.mark.parametrize('body,valid',[
    ('(set! (.-number (python/__import__ "writes")) "changed")',True),
    ('(set! (.-number (python/__import__ "writes")) 1)',False),
    ('(set! (.-number (python/__import__ "writes.sub")) "changed")',True),
    ('(set! (.-number (python/__import__ "writes.sub")) 1)',False),
    ('(set! (.-number (python/__import__ "writes.sub" nil nil #py ["*"])) 1)',True),
    ('(set! (.-number (python/__import__ "writes.sub" nil nil #py ["*"])) "bad")',False),
    ('(set! (.-number (python/__import__ "writes.sub" nil nil #py ["*"] 0)) 1)',True),
    ('(set! (.-number (python/__import__ "writes.sub" nil nil #py [])) "changed")',True),
    ('(set! (.-number (python/__import__ "writes.sub" nil nil #py [])) 1)',False),
    ('(set! (.-new-attribute (python/__import__ "writes")) 1)',True),
    ('(let [m (python/__import__ "writes")] (set! (.-declared m) ((.-A m))))',True),
    ('(let [m (python/__import__ "writes")] (set! (.-declared m) ((.-B m))))',False),
    ('(set! (.-fixed (python/__import__ "writes")) 3)',False),
    ('(defn dynamic [name] (set! (.-number (python/__import__ name)) 1))',True),
    ('(defn dynamic [from] (set! (.-number (python/__import__ "writes.sub" nil nil from)) "x"))',True),
    ('(set! (.-number (python/__import__ "writes.sub" nil nil #py ["*"] 1)) "x")',True),
    ('(set! (.-number (python/__import__ "missing_writes")) "x")',True),
])
def test_module_read_write_identity(environment,body,valid):
    analyzer,options,_=environment
    result=analyzer.analyze('(ns module-writes)\n'+body,options)
    findings=list(result[k('findings')])
    assert ([] if valid else [k('type-mismatch')])==[f[k('type')] for f in findings]

def test_native_module_assignment_syntax(environment):
    _,_,root=environment
    source='''(ns module-write-native)
(set! (.-number (python/__import__ "writes")) "changed")
(python/print (.-number (python/__import__ "writes.sub")))
(set! (.-number (python/__import__ "writes.sub" nil nil #py ["*"])) 2)
(python/print (.-number (python/__import__ "writes.sub" nil nil #py ["*"])))
'''
    command=[str(Path(sys.executable).parent/'basilisp'),'run','-p',str(root),'--include-unsafe-path=false','-c',source]
    p=subprocess.run(command,text=True,capture_output=True,timeout=45)
    assert p.returncode==0,p.stderr
    assert p.stdout.strip().splitlines()==["changed","2"]
