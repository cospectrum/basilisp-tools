"""Constructor/member regressions minimized from pinned typing and Pyright fixtures."""

import importlib.util
import os
from pathlib import Path

import pytest


@pytest.fixture
def worker():
    path = Path(os.environ.get("BLT_INSPECT_WORKER", Path(__file__).resolve().parents[1] / "src/basilisp_tools/_inspect.py"))
    spec = importlib.util.spec_from_file_location("_blt_dataclass_upstream", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def members(worker, tmp_path, source):
    (tmp_path / "fixture.py").write_text(source)
    return worker.StaticInspector([str(tmp_path)], []).module("fixture")["members"]


def test_classvar_aliases_are_unwrapped_and_bare_classvar_is_any(worker, tmp_path):
    result = members(worker, tmp_path, "from typing import ClassVar as CV\nclass C:\n x: CV[int]\n y: CV\n")
    assert result["C"]["members"]["x"]["type-path"] == ["int"]
    assert result["C"]["members"]["y"]["type-any?"]


def test_dataclass_inherits_fields_from_all_dataclass_bases(worker, tmp_path):
    result = members(worker, tmp_path,
        "from dataclasses import dataclass\n"
        "class Ordinary:\n unrelated: int\n"
        "@dataclass\nclass Base:\n a: int\n b: str\n"
        "@dataclass\nclass Child(Ordinary, Base):\n a: float\n c: bool\n")
    params = result["Child"]["parameters"]
    assert [parameter["name"] for parameter in params] == ["a", "b", "c"]
    assert params[0]["type-path"] == ["float"]


def test_classvar_override_removes_inherited_constructor_field(worker, tmp_path):
    result = members(worker, tmp_path,
        "from dataclasses import dataclass\nfrom typing import ClassVar as CV\n"
        "@dataclass\nclass Base:\n x: int\n y: int\n"
        "@dataclass\nclass Child(Base):\n x: CV[int] = 1\n z: str\n")
    assert [parameter["name"] for parameter in result["Child"]["parameters"]] == ["y", "z"]


def test_transform_base_constructor_arguments_are_not_dataclass_fields(worker, tmp_path):
    result = members(worker, tmp_path,
        "from typing import dataclass_transform\n"
        "@dataclass_transform(kw_only_default=True)\nclass Base:\n"
        " not_a_field: str\n def __init__(self, not_a_field: str): ...\n"
        "class Child(Base):\n id: int\n name: str = 'name'\n")
    params = result["Child"]["parameters"]
    assert [parameter["name"] for parameter in params] == ["id", "name"]
    assert [parameter["required?"] for parameter in params] == [True, False]
    assert all(parameter["kind"] == "keyword-only" for parameter in params)


def test_field_specifier_implicit_init_and_keyword_defaults(worker, tmp_path):
    result = members(worker, tmp_path,
        "from typing import Any, Callable, Literal, dataclass_transform, overload\n"
        "@overload\ndef field(*, resolver: Callable[[],Any], init: Literal[False] = False) -> Any: ...\n"
        "@overload\ndef field(*, resolver: None = None, init: Literal[True] = True) -> Any: ...\n"
        "def field(*, resolver=None, init=True): ...\n"
        "def keyword_field(*, init=False, kw_only=True) -> Any: ...\n"
        "@dataclass_transform(field_specifiers=(field,keyword_field))\nclass Base: ...\n"
        "class Child(Base):\n hidden: int = field(resolver=lambda: 0)\n"
        " name: str = field()\n hidden2: int = keyword_field()\n visible: int = keyword_field(init=True)\n")
    params = result["Child"]["parameters"]
    assert [parameter["name"] for parameter in params] == ["name", "visible"]
    assert params[1]["kind"] == "keyword-only"


def test_inherited_defaults_and_hash_annotation_are_optional(worker, tmp_path):
    result = members(worker, tmp_path,
        "from dataclasses import dataclass\n"
        "@dataclass\nclass Base:\n value: int = 1\n"
        "@dataclass\nclass Child(Base):\n value: int\n __hash__: None\n")
    assert [parameter["required?"] for parameter in result["Child"]["parameters"]] == [False, False]


def test_init_false_override_removes_inherited_field(worker, tmp_path):
    result = members(worker, tmp_path,
        "from dataclasses import dataclass, field\n"
        "@dataclass\nclass Base:\n x: int\n y: int\n"
        "@dataclass\nclass Child(Base):\n x: int = field(init=False)\n")
    assert [parameter["name"] for parameter in result["Child"]["parameters"]] == ["y"]


def test_init_false_dataclass_still_contributes_fields_to_child(worker, tmp_path):
    result = members(worker, tmp_path,
        "from dataclasses import dataclass\n"
        "@dataclass(init=False)\nclass Base:\n x: int\n"
        "@dataclass\nclass Child(Base):\n y: str\n")
    assert result["Base"]["parameters"] == []
    assert [parameter["name"] for parameter in result["Child"]["parameters"]] == ["x", "y"]


def test_plain_call_assignment_propagates_return_annotation(worker, tmp_path):
    result = members(worker, tmp_path,
        "def count(value: str) -> int: return len(value)\n"
        "value = count('abc')\ninvalid_call = count(1)\n")
    assert result["value"]["type-path"] == ["int"]
    # A declared result says nothing about acceptance of the initializer call.
    assert result["invalid_call"]["type-path"] == ["int"]
    assert len("abc") == 3
    with pytest.raises(TypeError):
        len(1)


def test_unresolved_generic_overloaded_and_async_assignments_stay_unknown(worker, tmp_path):
    result = members(worker, tmp_path,
        "from typing import TypeVar, overload\nT = TypeVar('T')\n"
        "def generic(value: T) -> T: return value\n"
        "@overload\ndef overloaded(value: int) -> str: ...\n"
        "@overload\ndef overloaded(value: str) -> int: ...\n"
        "def overloaded(value): ...\n"
        "async def async_call() -> int: return 1\n"
        "def missing(): ...\n"
        "a = generic(1)\nb = overloaded(1)\nc = missing()\nd = unknown()\ne = async_call()\n")
    for name in "abcde":
        assert not worker.type_fields(result[name]), (name, result[name])


def test_generic_constructor_assignment_keeps_its_class_identity(worker, tmp_path):
    result = members(worker, tmp_path,
        "from typing import Generic, TypeVar\nT = TypeVar('T')\n"
        "class Box(Generic[T]): ...\nvalue = Box[int]()\n")
    assert result["value"]["type-path"] == ["Box"]
    assert result["value"]["type-arguments"][0]["type-path"] == ["int"]


def test_overridden_aliased_field_does_not_leave_stale_constructor_alias(worker, tmp_path):
    result = members(worker, tmp_path,
        "from typing import Any, ClassVar, dataclass_transform\n"
        "def field(*, alias: str) -> Any: ...\n"
        "@dataclass_transform(field_specifiers=(field,))\nclass Model: ...\n"
        "class Base(Model):\n x: int = field(alias='old_name')\n"
        "class Changed(Base):\n x: str\n"
        "class Removed(Base):\n x: ClassVar[int] = 1\n")
    assert [p["name"] for p in result["Base"]["parameters"]] == ["old_name"]
    assert [p["name"] for p in result["Changed"]["parameters"]] == ["x"]
    assert result["Removed"]["parameters"] == []
