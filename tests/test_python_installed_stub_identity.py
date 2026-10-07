"""Installed stubs retain signatures but runtime classes supply nominal proof."""

import abc
import importlib.util
import sys
import types
from pathlib import Path
import pytest


@pytest.fixture
def worker():
    path = Path(__file__).resolve().parents[1] / "src/basilisp_tools/_inspect.py"
    spec = importlib.util.spec_from_file_location("_installed_identity_test", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def make_module(
    worker, tmp_path, monkeypatch, name, body, stub, *, local=False, enabled=True
):
    (tmp_path / f"{name}.py").write_text(body)
    (tmp_path / f"{name}.pyi").write_text(stub)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, name, raising=False)
    roots = [str(tmp_path)] if local else []
    inspector = worker.StaticInspector(roots, [] if local else [str(tmp_path)])
    result = worker.inspect_module(name, roots, enabled, inspector)
    return result, inspector


def test_first_lookup_imports_installed_class_facts_without_losing_stub_contracts(
    worker, tmp_path, monkeypatch
):
    name = "installed_generic_identity"
    result, inspector = make_module(
        worker,
        tmp_path,
        monkeypatch,
        name,
        "class Runtime: pass\nAlias=Runtime\ndef takes(value): return value\n",
        'from typing import TypeVar,Generic\nT=TypeVar("T")\nclass Alias(Generic[T]):\n def take(self,value:T)->T:...\ndef takes(value:int)->str:...\n',
    )
    cls = result["members"]["Alias"]
    assert name in sys.modules
    assert cls["nominal?"] is True
    assert cls["type-module"] == name and cls["type-path"] == ["Runtime"]
    assert cls["type-parameters"][0]["typevar"] == "T"
    assert cls["members"]["take"]["parameters"][1]["typevar"] == "T"
    assert result["members"]["takes"]["parameters"][0]["type-path"] == ["int"]
    assert result["members"]["takes"]["type-path"] == ["str"]
    assert str(tmp_path / f"{name}.pyi") in inspector.dependencies
    assert str(tmp_path / f"{name}.py") in inspector.dependencies


@pytest.mark.parametrize("local,enabled", [(True, True), (True, False), (False, False)])
def test_static_or_project_sources_never_execute_for_nominal_proof(
    worker, tmp_path, monkeypatch, local, enabled
):
    name = f"no_identity_import_{int(local)}_{int(enabled)}"
    result, _ = make_module(
        worker,
        tmp_path,
        monkeypatch,
        name,
        'raise AssertionError("source must not execute")\n',
        "class C: ...\n",
        local=local,
        enabled=enabled,
    )
    assert result["status"] == "known"
    assert name not in sys.modules


@pytest.mark.parametrize(
    "body,nominal",
    [
        ("class C: pass", True),
        ("class Meta(type): pass\nclass C(metaclass=Meta): pass", True),
        ("from abc import ABC\nclass C(ABC): pass\nC.register(str)", False),
        (
            "class Meta(type):\n def __instancecheck__(cls,value): return True\nclass C(metaclass=Meta): pass",
            False,
        ),
        (
            "class Meta(type):\n def __subclasscheck__(cls,value): return True\nclass C(metaclass=Meta): pass",
            False,
        ),
        ("class C:\n @property\n def __class__(self): return str", False),
        ("from typing import Protocol\nclass C(Protocol): pass", False),
        ("from typing import TypedDict\nclass C(TypedDict):\n value:int", False),
        ("def C(): return 42", False),
    ],
)
def test_runtime_dispatch_proof_preserves_non_nominal_boundaries(
    worker, tmp_path, monkeypatch, body, nominal
):
    result, _ = make_module(
        worker,
        tmp_path,
        monkeypatch,
        "identity_boundary",
        body + "\n",
        "class C: ...\n",
    )
    assert result["members"]["C"]["nominal?"] is nominal


def test_metaclass_descriptors_and_module_getters_are_not_bound(
    worker, tmp_path, monkeypatch
):
    body = """calls=[]
class Meta(type):
 def __getattribute__(cls,name):
  calls.append(name)
  raise AssertionError("metaclass lookup must not execute")
class C(metaclass=Meta): pass
def __getattr__(name):
 calls.append(name)
 raise AssertionError("module getter must not execute")
"""
    result, _ = make_module(
        worker,
        tmp_path,
        monkeypatch,
        "identity_raw_lookup",
        body,
        "class C: ...\nclass Missing: ...\n",
    )
    assert result["members"]["C"]["nominal?"] is True
    assert vars(sys.modules["identity_raw_lookup"])["calls"] == []


def test_runtime_identity_import_failure_keeps_static_contract(
    worker, tmp_path, monkeypatch
):
    result, _ = make_module(
        worker,
        tmp_path,
        monkeypatch,
        "identity_missing_dependency",
        "import definitely_missing_identity_dependency\n",
        "class C:\n def method(self,value:int)->str:...\n",
    )
    assert result["status"] == "known"
    assert result["members"]["C"]["members"]["method"]["type-path"] == ["str"]


def test_project_parent_package_blocks_installed_child_runtime_import(
    worker, tmp_path, monkeypatch
):
    project = tmp_path / "project"
    installed = tmp_path / "installed"
    for root in (project, installed):
        (root / "shadow_package").mkdir(parents=True)
    (project / "shadow_package/__init__.py").write_text(
        'raise AssertionError("project parent executed")\n'
    )
    (installed / "shadow_package/__init__.py").write_text(
        'raise AssertionError("runtime enrichment should be blocked")\n'
    )
    (installed / "shadow_package/child.pyi").write_text("class C: ...\n")
    monkeypatch.syspath_prepend(str(installed))
    monkeypatch.syspath_prepend(str(project))
    monkeypatch.delitem(sys.modules, "shadow_package", raising=False)
    inspector = worker.StaticInspector([str(project)], [str(installed)])
    result = worker.inspect_module(
        "shadow_package.child", [str(project)], True, inspector
    )
    assert result["status"] == "known"
    assert "shadow_package" not in sys.modules
    assert result["members"]["C"]["type-path"] == ["C"]


@pytest.mark.parametrize("package_path", [False, True])
def test_loaded_local_parent_blocks_identity_overlay_even_without_source_file(
    worker, tmp_path, monkeypatch, package_path
):
    project = tmp_path / "project"
    installed = tmp_path / "installed"
    (installed / "loaded_shadow").mkdir(parents=True)
    (installed / "loaded_shadow/child.pyi").write_text("class C: ...\n")
    parent = types.ModuleType("loaded_shadow")
    if package_path:
        parent.__path__ = [str(project / "loaded_shadow")]
    else:
        parent.__file__ = str(project / "loaded_shadow/__init__.py")
    child = types.ModuleType("loaded_shadow.child")

    class Runtime:
        pass

    child.C = Runtime
    monkeypatch.setitem(sys.modules, "loaded_shadow", parent)
    monkeypatch.setitem(sys.modules, "loaded_shadow.child", child)
    inspector = worker.StaticInspector([str(project)], [str(installed)])
    result = worker.inspect_module(
        "loaded_shadow.child", [str(project)], True, inspector
    )
    assert result["members"]["C"]["type-module"] == "loaded_shadow.child"
    assert result["members"]["C"]["type-path"] == ["C"]


def test_runtime_source_change_invalidates_enriched_stub_cache(
    worker, tmp_path, monkeypatch
):
    name = "changed_identity"
    _, inspector = make_module(
        worker, tmp_path, monkeypatch, name, "class C: pass\n", "class C: ...\n"
    )
    dependencies = list(inspector.dependencies.values())
    assert worker.dependencies_fresh(dependencies)
    (tmp_path / f"{name}.py").write_text("class C:\n def __class__(self): return str\n")
    assert not worker.dependencies_fresh(dependencies)


def test_multiple_class_proofs_do_not_share_nominality(worker, tmp_path, monkeypatch):
    body = """from abc import ABC
class Ordinary: pass
class Dynamic(ABC): pass
Dynamic.register(str)
Alias=Ordinary
"""
    stub = "class Ordinary: ...\nclass Dynamic: ...\nclass Alias: ...\n"
    result, _ = make_module(
        worker, tmp_path, monkeypatch, "separate_identities", body, stub
    )
    members = result["members"]
    assert members["Ordinary"]["nominal?"] is True
    assert members["Dynamic"]["nominal?"] is False
    assert members["Alias"]["nominal?"] is True
    assert members["Alias"]["type-path"] == ["Ordinary"]


@pytest.mark.parametrize("opaque_module", [False, True])
def test_opaque_loaded_parent_never_executes_lookup_or_path_iteration(
    worker, tmp_path, monkeypatch, opaque_module
):
    installed = tmp_path / "installed"
    (installed / "opaque_parent").mkdir(parents=True)
    (installed / "opaque_parent/child.pyi").write_text("class C: ...\n")
    calls = []

    class DynamicModule(types.ModuleType):
        def __getattribute__(self, name):
            calls.append(name)
            raise AssertionError("opaque module lookup executed")

    class DynamicPath:
        def __iter__(self):
            calls.append("iterate")
            raise AssertionError("opaque package path executed")

    parent = DynamicModule("opaque_parent") if opaque_module else types.ModuleType("opaque_parent")
    if not opaque_module:
        parent.__path__ = DynamicPath()
    monkeypatch.setitem(sys.modules, "opaque_parent", parent)
    inspector = worker.StaticInspector([], [str(installed)])
    result = worker.inspect_module("opaque_parent.child", [], True, inspector)
    assert result["status"] == "known"
    assert result["members"]["C"]["type-module"] == "opaque_parent.child"
    assert calls == []
