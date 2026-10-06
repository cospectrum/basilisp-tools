"""Native extension contracts can come from their package's shipped stubs."""

import abc
import datetime
import importlib.util
import math
import sys
import types
import typing
from pathlib import Path

import pytest


@pytest.fixture
def worker():
    path = Path(__file__).resolve().parents[1] / "src/basilisp_tools/_inspect.py"
    spec = importlib.util.spec_from_file_location("_blt_native_stubs_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("declaration", [
    "from ._native import *",
    "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from ._native import log",
    "if unknown_loader_condition:\n    from ._native import log\nelse:\n    from ._native import log",
])
def test_native_reexports_gain_call_contracts_without_importing_stubs(worker, tmp_path, monkeypatch, declaration):
    package = tmp_path / "native_contracts"
    package.mkdir()
    source = package / "__init__.py"
    source.write_text(declaration)
    stub = package / "_native.pyi"
    stub.write_text("def log(x: float, base: float = ...) -> float: ...\n")
    (package / "_native.py").write_text('raise AssertionError("must not execute")\n')
    module = types.ModuleType("native_contracts")
    module.__file__ = str(source)
    module.log = math.log
    monkeypatch.setitem(sys.modules, module.__name__, module)
    inspector = worker.StaticInspector([], [str(tmp_path)])
    result = worker.inspect_module(module.__name__, [], True, inspector)
    info = result["members"]["log"]
    assert info["type-path"] == ["float"]
    assert [p["required?"] for p in info["parameters"]] == [True, False]
    assert info["parameters"][0]["type-path"] == ["float"]
    assert math.log(1) == 0
    with pytest.raises(TypeError):
        math.log()
    assert str(stub) in inspector.dependencies
    assert "native_contracts._native" not in sys.modules
    assert worker._runtime_inspector.get() is None


def test_ambiguous_conditional_stub_reexports_do_not_supply_contracts(worker, tmp_path, monkeypatch):
    source = tmp_path / "conditional_native.py"
    source.write_text("if unknown_condition:\n    from native_a import log\nelse:\n    from native_b import log\n")
    (tmp_path / "native_a.pyi").write_text("def log(value: int) -> int: ...\n")
    (tmp_path / "native_b.pyi").write_text("def log(value: str) -> str: ...\n")
    module = types.ModuleType("conditional_native")
    module.__file__, module.log = str(source), math.log
    monkeypatch.setitem(sys.modules, module.__name__, module)
    result = worker.inspect_module(module.__name__, [], True, worker.StaticInspector([], [str(tmp_path)]))
    assert "parameters" not in result["members"]["log"]
    assert "type-path" not in result["members"]["log"]


@pytest.mark.parametrize("replacement", [
    "log = fallback",
    "first = log = fallback",
    "first, (log, last) = values",
    "log += fallback",
    "del log",
    "def log(value): pass",
    "def unrelated(value=(log := fallback)): pass",
    "class log: pass",
    "import replacement as log",
    "from unavailable import log",
    "from unavailable import *",
    "if later_condition:\n    log = fallback",
    "if (log := fallback):\n    pass",
    "try:\n    log = fallback\nexcept Exception:\n    pass",
    "for log in values:\n    pass",
])
def test_conditional_stub_reexports_drop_overwritten_names(worker, tmp_path, monkeypatch, replacement):
    source = tmp_path / "conditional_overwrite.py"
    source.write_text("if loader:\n    from native_a import log\nelse:\n    from native_a import log\n"
                      + "".join("    " + line + "\n" for line in replacement.splitlines()))
    (tmp_path / "native_a.pyi").write_text("def log(value: str) -> str: ...\n")
    module = types.ModuleType("conditional_overwrite")
    module.__file__, module.log = str(source), math.log
    monkeypatch.setitem(sys.modules, module.__name__, module)
    result = worker.inspect_module(module.__name__, [], True, worker.StaticInspector([], [str(tmp_path)]))
    assert "parameters" not in result["members"]["log"]
    assert "type-path" not in result["members"]["log"]
    assert math.log(1.5) > 0


@pytest.mark.parametrize("replacement", ["log = fallback", "from unavailable import log", "del log"])
def test_later_ambiguous_branch_invalidates_earlier_stub_export(worker, tmp_path, monkeypatch, replacement):
    source = tmp_path / "later_overwrite.py"
    source.write_text("from native_a import log\nif later_condition:\n    " + replacement + "\n")
    (tmp_path / "native_a.pyi").write_text("def log(value: str) -> str: ...\n")
    module = types.ModuleType("later_overwrite")
    module.__file__, module.log = str(source), math.log
    monkeypatch.setitem(sys.modules, module.__name__, module)
    inspector = worker.StaticInspector([], [str(tmp_path)])
    assert "log" not in worker.runtime_stub_exports(module, inspector)


@pytest.mark.parametrize("statement", [
    "unrelated = fallback",
    "def unrelated():\n    log = fallback",
    "if later_condition:\n    unrelated = fallback",
])
def test_unrelated_writes_preserve_proven_stub_export(worker, tmp_path, monkeypatch, statement):
    source = tmp_path / "unrelated_write.py"
    source.write_text("from native_a import log\n" + statement + "\n")
    (tmp_path / "native_a.pyi").write_text("def log(value: float) -> float: ...\n")
    module = types.ModuleType("unrelated_write")
    module.__file__, module.log = str(source), math.log
    monkeypatch.setitem(sys.modules, module.__name__, module)
    inspector = worker.StaticInspector([], [str(tmp_path)])
    assert worker.runtime_stub_exports(module, inspector)["log"]["parameters"][0]["type-path"] == ["float"]


@pytest.mark.parametrize("exports,preserved", [(["other"], True), (("other",), True), (["log"], False)])
def test_loaded_star_export_invalidates_only_explicit_names(worker, tmp_path, monkeypatch, exports, preserved):
    source = tmp_path / "star_reexports.py"
    source.write_text("from native_a import log\nfrom loaded_star import *\n")
    (tmp_path / "native_a.pyi").write_text("def log(value: float) -> float: ...\n")
    imported = types.ModuleType("loaded_star")
    imported.__all__ = exports
    module = types.ModuleType("star_reexports")
    module.__file__ = str(source)
    monkeypatch.setitem(sys.modules, imported.__name__, imported)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    inspector = worker.StaticInspector([], [str(tmp_path)])
    assert ("log" in worker.runtime_stub_exports(module, inspector)) is preserved


@pytest.mark.parametrize("dynamic", [False, True])
def test_loaded_star_export_without_all_uses_only_static_namespace(worker, tmp_path, monkeypatch, dynamic):
    source = tmp_path / "star_namespace.py"
    source.write_text("from native_a import log\nfrom loaded_namespace import *\n")
    (tmp_path / "native_a.pyi").write_text("def log(value: float) -> float: ...\n")
    imported = types.ModuleType("loaded_namespace")
    imported.other = 1
    accessed = []
    if dynamic:
        def getter(name):
            accessed.append(name)
            raise AssertionError("module getter must not execute")
        imported.__getattr__ = getter
    module = types.ModuleType("star_namespace")
    module.__file__ = str(source)
    monkeypatch.setitem(sys.modules, imported.__name__, imported)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    inspector = worker.StaticInspector([], [str(tmp_path)])
    assert ("log" in worker.runtime_stub_exports(module, inspector)) is (not dynamic)
    assert accessed == []


def test_custom_star_all_is_not_iterated(worker, tmp_path, monkeypatch):
    source = tmp_path / "star_custom.py"
    source.write_text("from native_a import log\nfrom loaded_custom import *\n")
    (tmp_path / "native_a.pyi").write_text("def log(value: float) -> float: ...\n")
    accessed = []
    class Exports(list):
        def __iter__(self):
            accessed.append(True)
            raise AssertionError("custom __all__ must not execute")
    imported = types.ModuleType("loaded_custom")
    imported.__all__ = Exports(["other"])
    module = types.ModuleType("star_custom")
    module.__file__ = str(source)
    monkeypatch.setitem(sys.modules, imported.__name__, imported)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    inspector = worker.StaticInspector([], [str(tmp_path)])
    assert "log" not in worker.runtime_stub_exports(module, inspector)
    assert accessed == []


def test_stub_aliases_do_not_replace_known_runtime_binding(worker, tmp_path, monkeypatch):
    source = tmp_path / "stub_aliases.py"
    source.write_text("from declarations import logarithm as log, factorial\n")
    (tmp_path / "declarations.pyi").write_text(
        "def logarithm(value: float) -> float: ...\n"
        "def factorial(value: int, unsupported: str) -> int: ...\n")
    module = types.ModuleType("stub_aliases")
    module.__file__ = str(source)
    module.log, module.factorial = math.log, math.factorial
    monkeypatch.setitem(sys.modules, module.__name__, module)
    info = worker.inspect_module(module.__name__, [], True,
                                 worker.StaticInspector([], [str(tmp_path)]))["members"]
    assert info["log"]["type-path"] == ["float"]
    assert len(info["factorial"]["parameters"]) == 1
    assert info["factorial"]["parameters"][0]["kind"] == "positional-only"


def test_native_class_reexports_fill_matching_parameter_annotations(worker, tmp_path, monkeypatch):
    source = tmp_path / "datetime.py"
    source.write_text("from native_clock import datetime\n")
    stub = tmp_path / "native_clock.pyi"
    stub.write_text("from datetime import timedelta\nclass datetime:\n"
                    "    def __add__(self, other: timedelta, /) -> datetime: ...\n")
    module = types.ModuleType("datetime")
    module.__file__ = str(source)
    module.datetime = datetime.datetime
    monkeypatch.setitem(sys.modules, "datetime", module)
    inspector = worker.StaticInspector([], [str(tmp_path)])
    token = worker._runtime_inspector.set(inspector)
    try:
        info = worker.runtime_member("datetime", datetime.datetime)
        member = info["members"]["__add__"]
        assert member["parameters"][1]["name"] == "value"
        assert member["parameters"][1]["kind"] == "positional-only"
        assert member["parameters"][1]["type-path"] == ["timedelta"]
        assert member["type-path"] == ["datetime"]
        assert worker.runtime_stub_exports(module, inspector) is inspector.stub_exports["datetime"]
    finally:
        worker._runtime_inspector.reset(token)
    assert datetime.datetime(2026, 1, 1) + datetime.timedelta(days=1) == datetime.datetime(2026, 1, 2)
    with pytest.raises(TypeError):
        datetime.datetime(2026, 1, 1) + "bad"
    assert str(stub) in inspector.dependencies
    assert "native_clock" not in sys.modules


def test_native_parameter_annotations_require_matching_binding_layout(worker):
    native = {"status": "known", "kind": "function", "name": "f",
              "parameters": [{"name": "value", "kind": "positional-only", "required?": False}]}
    parameter = {"name": "value", "kind": "positional-only", "required?": True,
                 "type-module": "builtins", "type-path": ["int"], "annotation": "int"}
    declared = {"status": "known", "kind": "function", "name": "f", "parameters": [parameter]}
    result = worker.supplement_stub(native, declared)
    assert result["parameters"][0]["type-path"] == ["int"]
    assert result["parameters"][0]["required?"] is False
    assert "type-path" not in native["parameters"][0]
    for parameters in [[{**parameter, "kind": "keyword-only"}], [parameter, parameter]]:
        assert worker.supplement_stub(native, {**declared, "parameters": parameters}) == native
    typed = {**native, "parameters": [{**native["parameters"][0], "type-module": "builtins", "type-path": ["str"]}]}
    assert worker.supplement_stub(typed, declared) == typed


def test_native_base_methods_and_fields_use_stubs_but_python_overrides_win(worker, tmp_path):
    (tmp_path / "datetime.pyi").write_text(
        "class date:\n    year: int\n    month: int\n"
        "    def isocalendar(self) -> tuple[int, int, int]: ...\n")

    class Date(datetime.date):
        @property
        def month(self) -> str:
            return "overridden"

    inspector = worker.StaticInspector([], [str(tmp_path)])
    token = worker._runtime_inspector.set(inspector)
    try:
        info = worker.runtime_member("Date", Date)["members"]
    finally:
        worker._runtime_inspector.reset(token)
    assert info["year"]["type-path"] == ["int"]
    assert info["month"]["type-path"] == ["str"]
    assert info["isocalendar"]["type-path"] == ["tuple"]
    assert info["isocalendar"]["instance-method?"]
    assert len(info["isocalendar"]["parameters"]) == 1


def test_private_source_aliases_are_cached_without_loading_dependencies(worker, tmp_path, monkeypatch):
    aliases = tmp_path / "private_aliases.py"
    aliases.write_text('raise AssertionError("annotation dependency must not execute")\n'
                       'from builtins import int as _index\n'
                       'from typing import TypeAlias\n'
                       '_number: TypeAlias = float | _index\n')
    (tmp_path / "native_declarations.pyi").write_text(
        'from private_aliases import _index, _number\n' +
        ''.join(f'def log{index}(value: _number, base: _index = ...) -> float: ...\n'
                for index in range(12)))
    source = tmp_path / "alias_exports.py"
    source.write_text('from native_declarations import *\n')
    module = types.ModuleType("alias_exports")
    module.__file__ = str(source)
    for index in range(12):
        setattr(module, f"log{index}", math.log)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    parsed, original = [], worker.ast.parse

    def parse(source, filename="<unknown>", **kwargs):
        parsed.append(filename)
        return original(source, filename=filename, **kwargs)

    monkeypatch.setattr(worker.ast, "parse", parse)
    inspector = worker.StaticInspector([], [str(tmp_path)])
    result = worker.inspect_module(module.__name__, [], True, inspector)
    parameters = result["members"]["log0"]["parameters"]
    assert parameters[0]["type-union"] == [
        {"type-module": "builtins", "type-path": ["float"]},
        {"type-module": "builtins", "type-path": ["int"]},
    ]
    assert parameters[1]["type-path"] == ["int"]
    assert parsed.count(str(aliases)) == 1
    assert str(aliases) in inspector.dependencies
    assert "private_aliases" not in sys.modules


def test_only_plain_runtime_classes_prove_nominal_exclusion(worker):
    class Ordinary:
        pass

    class PlainMeta(type):
        pass

    class PlainSubclass(metaclass=PlainMeta):
        pass

    class Registered(metaclass=abc.ABCMeta):
        pass

    Registered.register(str)

    class AcceptStrings(type):
        def __instancecheck__(cls, value):
            return isinstance(value, str)

    class Structural(metaclass=AcceptStrings):
        pass

    class AcceptSubclasses(type):
        def __subclasscheck__(cls, value):
            return True

    class Virtual(metaclass=AcceptSubclasses):
        pass

    assert isinstance("valid", Registered)
    assert isinstance("valid", Structural)
    assert worker.runtime_member("Ordinary", Ordinary)["nominal?"]
    assert worker.runtime_member("PlainSubclass", PlainSubclass)["nominal?"]
    assert worker.runtime_member("int", int)["nominal?"]
    assert not worker.runtime_member("Registered", Registered)["nominal?"]
    assert not worker.runtime_member("Structural", Structural)["nominal?"]
    assert not worker.runtime_member("Virtual", Virtual)["nominal?"]


def test_lazy_reexports_never_call_getattr_or_import_dependencies(worker, tmp_path, monkeypatch):
    source = tmp_path / "lazy_exports.py"
    source.write_text('from typing import TYPE_CHECKING\n'
                      'if TYPE_CHECKING:\n    from lazy_dependency import Public\n')
    dependency = tmp_path / "lazy_dependency.py"
    dependency.write_text('raise AssertionError("must not import")\nclass Public: pass\n')
    module = types.ModuleType("lazy_exports")
    module.__file__ = str(source)

    def lookup(name):
        raise AssertionError("must not execute lazy imports")

    module.__getattr__ = lookup
    monkeypatch.setitem(sys.modules, module.__name__, module)
    inspector = worker.StaticInspector([], [str(tmp_path)])
    info = worker.inspect_module(module.__name__, [], True, inspector)["members"]["Public"]
    assert info["target-module"] == "lazy_dependency"
    assert info["target-path"] == ["Public"]
    assert str(dependency) not in inspector.dependencies
    assert "lazy_dependency" not in sys.modules


def test_descriptors_expose_declared_values_without_running_getters(worker):
    result_type = typing.TypeVar("Result")

    class Text:
        def __get__(self, instance, owner=None) -> str:
            raise AssertionError("inspection must not run descriptors")

    class Generic(typing.Generic[result_type]):
        def __get__(self, instance, owner=None) -> result_type:
            raise AssertionError("inspection must not run descriptors")

    class Unknown:
        def __get__(self, instance, owner=None):
            raise AssertionError("inspection must not run descriptors")

    class Model:
        __slots__ = ("slot",)
        slot: int
        text = Text()
        generic = Generic()
        unknown = Unknown()

    members = worker.runtime_member("Model", Model)["members"]
    assert members["slot"]["type-path"] == ["int"]
    assert members["text"]["type-path"] == ["str"]
    for name in ("generic", "unknown"):
        assert members[name]["kind"] == "property"
        assert not worker.type_fields(members[name])
    # The same descriptor stored as an ordinary module value is still an object.
    assert worker.runtime_member("text", Text())["type-path"][-1] == "Text"


def test_builtin_descriptors_keep_their_value_types(worker):
    for cls, name, expected in ((complex, "real", float), (complex, "imag", float),
                                (float, "real", float), (int, "numerator", int),
                                (range, "start", int)):
        info = worker.runtime_member(cls.__name__, cls)["members"][name]
        assert info["type-module"] == "builtins"
        assert info["type-path"] == [expected.__name__]
