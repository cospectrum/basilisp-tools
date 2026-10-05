"""Runtime annotations share source metadata without evaluating dependency files."""

import importlib.util
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture
def worker():
    path = Path(__file__).resolve().parents[1] / "src" / "basilisp_tools" / "_inspect.py"
    spec = importlib.util.spec_from_file_location("_blt_runtime_inspection_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def runtime_module(monkeypatch, root, name, imports, function):
    path = root.joinpath(*name.split(".")).with_suffix(".py")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(imports + "\n" + function)
    module = types.ModuleType(name)
    module.__file__ = str(path)
    # Only the fixture function is executed; its dependency imports stay in source.
    exec(compile(function, str(path), "exec"), vars(module))
    monkeypatch.setitem(sys.modules, name, module)
    return path


def test_repeated_runtime_annotations_reuse_dependency_graph(worker, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    package = tmp_path / "blt_runtime_graph"
    package.mkdir()
    dependency = package / "models.py"
    dependency.write_text('raise AssertionError("dependency must not execute")\nclass Payload:\n    value: str\n')
    parsed = []
    original_parse = worker.ast.parse

    def parse(source, filename="<unknown>", **kwargs):
        parsed.append(filename)
        return original_parse(source, filename=filename, **kwargs)

    monkeypatch.setattr(worker.ast, "parse", parse)
    inspector = worker.StaticInspector([], list(sys.path))
    for index in range(24):
        name = f"blt_runtime_graph.owner{index}"
        filename = runtime_module(
            monkeypatch, tmp_path, name, "from .models import Payload",
            "def accept(value: 'Payload') -> 'Payload': return value\n",
        )
        result = worker.inspect_module(name, [], True, inspector)
        member = result["members"]["accept"]
        assert result["inspection"] == "runtime"
        assert member["type-module"] == "blt_runtime_graph.models"
        assert member["type-path"] == ["Payload"]
        assert member["parameters"][0]["type-path"] == ["Payload"]
        assert str(filename) in inspector.dependencies
    assert parsed.count(str(dependency)) == 1
    assert str(dependency) in inspector.dependencies
    assert "blt_runtime_graph.models" not in sys.modules
    assert worker._runtime_inspector.get() is None


def test_annotation_dependencies_do_not_leak_between_inspections(worker, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    name = "blt_runtime_fresh.owner"
    runtime_module(
        monkeypatch, tmp_path, name, "from .models import Payload",
        "def accept(value: 'Payload') -> 'Payload': return value\n",
    )
    dependency = tmp_path / "blt_runtime_fresh" / "models.py"
    dependency.write_text("class Payload:\n    value: str\n")
    first = worker.StaticInspector([], list(sys.path))
    worker.inspect_module(name, [], True, first)
    before = first.modules["blt_runtime_fresh.models"]["members"]["Payload"]["members"]["value"]
    assert before["type-path"] == ["str"]
    old_dependency = first.dependencies[str(dependency)]
    dependency.write_text("class Payload:\n    value: int\n# edited dependency\n")
    second = worker.StaticInspector([], list(sys.path))
    worker.inspect_module(name, [], True, second)
    after = second.modules["blt_runtime_fresh.models"]["members"]["Payload"]["members"]["value"]
    assert after["type-path"] == ["int"]
    assert before["type-path"] == ["str"]
    assert second.dependencies[str(dependency)] != old_dependency


def test_shared_generic_aliases_preserve_type_arguments(worker, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    package = tmp_path / "blt_runtime_generic"
    package.mkdir()
    (package / "models.py").write_text(
        "from typing import Generic, TypeVar\nT = TypeVar('T')\n"
        "class Box(Generic[T]):\n    value: T\n"
    )
    inspector = worker.StaticInspector([], list(sys.path))
    for index, expected in enumerate(["int", "str", "int"]):
        name = f"blt_runtime_generic.owner{index}"
        runtime_module(
            monkeypatch, tmp_path, name, "from .models import Box",
            f"def make() -> 'Box[{expected}]': pass\n",
        )
        info = worker.inspect_module(name, [], True, inspector)["members"]["make"]
        assert info["type-module"] == "blt_runtime_generic.models"
        assert info["type-path"] == ["Box"]
        assert info["type-arguments"] == [{"type-module": "builtins", "type-path": [expected]}]


def test_cyclic_runtime_annotation_sources_remain_conservative(worker, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    package = tmp_path / "blt_runtime_cycle"
    package.mkdir()
    (package / "left.py").write_text("from .right import Alias\n")
    (package / "right.py").write_text("from .left import Alias\n")
    runtime_module(
        monkeypatch, tmp_path, "blt_runtime_cycle.owner", "from .left import Alias",
        "def accept(value: 'Alias') -> 'Alias': return value\n",
    )
    inspector = worker.StaticInspector([], list(sys.path))
    info = worker.inspect_module("blt_runtime_cycle.owner", [], True, inspector)
    assert info["status"] == "known"
    assert "parameters" in info["members"]["accept"]
    # Cycles must not poison an independent, subsequently requested module.
    (package / "clean.py").write_text("value: str = 'ok'\n")
    clean = worker.inspect_module("blt_runtime_cycle.clean", [str(tmp_path)], False, inspector)
    assert clean["members"]["value"]["type-path"] == ["str"]


def test_exported_bound_methods_keep_signatures_and_unknown_returns(worker):
    class Service:
        def typed(self, value: int, *, label: str = "") -> str:
            raise AssertionError("method must not execute")

        def unannotated(self, value):
            raise AssertionError("method must not execute")

        @classmethod
        def create(cls, value: int) -> "Self":
            raise AssertionError("method must not execute")

    instance = Service()
    typed = worker.safe_member("exported", instance.typed)
    assert typed["kind"] == "function"
    assert [p["name"] for p in typed["parameters"]] == ["value", "label"]
    assert typed["parameters"][1]["kind"] == "keyword-only"
    assert typed["type-path"] == ["str"]
    assert typed["instance-method?"] is False
    unknown = worker.safe_member("unannotated", instance.unannotated)
    assert unknown["kind"] == "function"
    assert [p["name"] for p in unknown["parameters"]] == ["value"]
    assert not worker.type_fields(unknown)
    created = worker.safe_member("create", Service.create)
    assert [p["name"] for p in created["parameters"]] == ["value"]
    assert created["type-path"][-1] == "Service"


def test_bound_self_uses_each_receiver_without_executing_descriptors(worker):
    class Base:
        def clone(self) -> "Self":
            raise AssertionError("method must not execute")

        def __getattribute__(self, name):
            raise AssertionError("receiver attributes must not execute")

    class Child(Base):
        pass

    method = Base.__dict__["clone"]
    for receiver in [Base(), Child(), Base()]:
        bound = types.MethodType(method, receiver)
        info = worker.safe_member("clone", bound)
        assert info["parameters"] == []
        assert info["type-path"][-1] == type(receiver).__name__
        assert info["instance-method?"] is False
