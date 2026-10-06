"""Keep uncertain or explicitly ignored annotations out of interop contracts."""

import importlib.util
import types
from pathlib import Path

import pytest


@pytest.fixture
def worker():
    path = Path(__file__).resolve().parents[1] / "src/basilisp_tools/_inspect.py"
    spec = importlib.util.spec_from_file_location("_blt_typing_contracts_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SOURCE = '''from typing import no_type_check, overload, TypeGuard
def replace(function):
    return lambda *args: 42
@replace
def changed(value: int) -> str: return "old"
@replace
async def changed_async(value: int) -> str: return "old"
@replace
def changed_guard(value: object) -> TypeGuard[str]: return True
@no_type_check
def unchecked(value: int) -> str: return value
@overload
def overloaded(value: int) -> str: ...
@overload
def overloaded(value: str) -> int: ...
@replace
def overloaded(value): return value
@overload
def unchecked_overload(value: int) -> str: ...
@overload
def unchecked_overload(value: str) -> int: ...
@no_type_check
def unchecked_overload(value): return value
def checked(value: int) -> int: return value + 1
'''


def test_unknown_decorators_do_not_preserve_original_return_or_guard(worker, tmp_path):
    path = tmp_path / "contracts.py"
    path.write_text(SOURCE)
    members = worker.static_module("contracts", path)["members"]
    for name in ("changed", "changed_async", "changed_guard", "overloaded"):
        info = members[name]
        assert not worker.type_fields(info), (name, info)
        assert "parameters" not in info and "overloads" not in info
        assert not info.get("async?") and not info.get("type-guard")
    assert members["checked"]["type-path"] == ["int"]
    assert members["checked"]["parameters"][0]["type-path"] == ["int"]


@pytest.mark.parametrize("inspection", ["static", "runtime"])
def test_no_type_check_retains_binding_but_ignores_annotations(worker, tmp_path, inspection):
    path = tmp_path / "contracts.py"
    path.write_text(SOURCE)
    if inspection == "static":
        members = worker.static_module("contracts", path)["members"]
    else:
        module = types.ModuleType("contracts")
        exec(compile(SOURCE, str(path), "exec"), vars(module))
        members = {name: worker.runtime_member(name, getattr(module, name))
                   for name in ("unchecked", "unchecked_overload", "checked")}
    for name in ("unchecked", "unchecked_overload"):
        info = members[name]
        assert not worker.type_fields(info), (name, info)
        assert "overloads" not in info
        assert len(info["parameters"]) == 1
        assert info["parameters"][0]["required?"]
        assert not worker.type_fields(info["parameters"][0])
        assert info["parameters"][0].get("annotation") is None
    assert members["checked"]["type-path"] == ["int"]


def test_no_type_check_alias_preserves_only_real_call_binding(worker, tmp_path):
    path = tmp_path / "contracts.py"
    path.write_text('from typing import no_type_check as untyped\n'
                    '@untyped\ndef f(value: int, *, flag: bool) -> str: return value\n')
    info = worker.static_module("contracts", path)["members"]["f"]
    assert not worker.type_fields(info)
    assert [p["name"] for p in info["parameters"]] == ["value", "flag"]
    assert info["parameters"][1]["kind"] == "keyword-only"
    assert all(not worker.type_fields(param) for param in info["parameters"])


@pytest.mark.parametrize("inspection", ["static", "runtime"])
def test_ignored_keyword_annotations_do_not_expand_a_typed_dictionary(worker, tmp_path, inspection):
    path = tmp_path / "contracts.py"
    source = ('from typing import no_type_check, TypedDict\n'
              'class Options(TypedDict):\n    required: int\n'
              '@no_type_check\n'
              'def f(**values: "Unpack[Options]") -> str: return values\n')
    path.write_text(source)
    if inspection == "static":
        info = worker.static_module("contracts", path)["members"]["f"]
    else:
        namespace = {}
        exec(source, namespace)
        assert namespace["f"](arbitrary=42) == {"arbitrary": 42}
        info = worker.runtime_member("f", namespace["f"])
    assert info["parameters"] == [{"name": "values", "kind": "var-keyword", "required?": False}]
    assert not worker.type_fields(info)


@pytest.mark.parametrize("decorators", ["@no_type_check\n@cache", "@cache\n@no_type_check"])
def test_ignored_annotations_survive_cache_wrapper_order(worker, decorators):
    namespace = {}
    exec('from functools import cache\nfrom typing import no_type_check\n'
         + decorators + '\ndef f(value: int) -> str: return value\n', namespace)
    function = namespace["f"]
    assert function(42) == 42
    info = worker.runtime_member("f", function)
    assert not worker.type_fields(info)
    assert not worker.type_fields(info["parameters"][0])
    assert info["parameters"][0]["required?"]


def test_ignored_class_method_annotations_do_not_affect_inherited_contracts(worker, tmp_path):
    path = tmp_path / "contracts.py"
    path.write_text('from typing import no_type_check\n'
                    'class Base:\n    def inherited(self, value: int) -> int: return value + 1\n'
                    '@no_type_check\nclass Child(Base):\n'
                    '    def own(self, value: int) -> str: return value\n'
                    '    class Nested:\n        def own(self, value: int) -> str: return value\n')
    child = worker.static_module("contracts", path)["members"]["Child"]
    own = child["members"]["own"]
    assert not worker.type_fields(own)
    assert not worker.type_fields(own["parameters"][1])
    assert not worker.type_fields(child["members"]["Nested"]["members"]["own"])
    inherited = child["members"]["inherited"]
    assert inherited["type-path"] == ["int"]
    assert inherited["parameters"][1]["type-path"] == ["int"]


@pytest.mark.parametrize("inspection", ["static", "runtime"])
def test_ignored_constructor_keeps_binding_without_erasing_inherited_types(worker, tmp_path, inspection):
    path = tmp_path / "constructors.py"
    source = ('from typing import no_type_check\n'
              'class Base:\n    def __init__(self, value: int): self.value = value + 1\n'
              '@no_type_check\nclass Inherited(Base): pass\n'
              '@no_type_check\nclass Own:\n'
              '    def __init__(self, value: int, *, flag: bool): self.value = value\n')
    path.write_text(source)
    namespace = {"__name__": "constructors"}
    exec(compile(source, str(path), "exec"), namespace)
    assert namespace["Own"]("ok", flag="ok").value == "ok"
    with pytest.raises(TypeError):
        namespace["Inherited"]("bad")
    if inspection == "static":
        members = worker.static_module("constructors", path)["members"]
    else:
        members = {name: worker.runtime_member(name, namespace[name]) for name in ("Own", "Inherited")}
    params = members["Own"]["parameters"]
    assert [p["name"] for p in params] == ["value", "flag"]
    assert params[1]["kind"] == "keyword-only" and params[1]["required?"]
    assert all(not worker.type_fields(param) for param in params)
    assert members["Inherited"]["parameters"][0]["type-path"] == ["int"]
