"""Constructor contracts follow Python's metaclass and __new__ dispatch."""

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def worker():
    path = Path(__file__).resolve().parents[1] / "src/basilisp_tools/_inspect.py"
    spec = importlib.util.spec_from_file_location("_blt_constructor_upstream", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inspect_source(worker, tmp_path, source):
    (tmp_path / "constructors.py").write_text(source)
    return worker.StaticInspector([str(tmp_path)], []).module("constructors")["members"]


def test_foreign_new_return_skips_init_but_keeps_class_identity(worker, tmp_path):
    source = "class C:\n def __new__(cls) -> int: return 1\n def __init__(self, required: str): pass\n"
    info = inspect_source(worker, tmp_path, source)["C"]
    assert info["type-path"] == ["C"]
    assert info["parameters"] == []
    assert info["constructor-return"]["type-path"] == ["int"]
    namespace = {}
    exec(source, namespace)
    assert namespace["C"]() == 1
    with pytest.raises(TypeError):
        namespace["C"]("extra")


def test_inherited_foreign_new_ignores_subclass_init(worker, tmp_path):
    source = (
        "class A: pass\nclass B(A):\n def __new__(cls) -> A: return A()\n"
        " def __init__(self, required: int): pass\n"
        "class C(B):\n def __init__(self, other: str): pass\n"
    )
    result = inspect_source(worker, tmp_path, source)
    for name in ("B", "C"):
        assert result[name]["parameters"] == []
        assert result[name]["constructor-return"]["type-path"] == ["A"]
    namespace = {}
    exec(source, namespace)
    assert type(namespace["C"]()) is namespace["A"]


@pytest.mark.parametrize("annotation", ["Any", "C | Any", "int | C"])
def test_any_or_foreign_union_new_return_does_not_require_init(worker, tmp_path, annotation):
    source = (
        "from typing import Any\nclass C:\n"
        f" def __new__(cls) -> '{annotation}': return 1\n"
        " def __init__(self, required: int): pass\n"
    )
    info = inspect_source(worker, tmp_path, source)["C"]
    assert info["parameters"] == []
    assert "constructor-return" in info


def test_metaclass_call_precedes_new_and_inherits(worker, tmp_path):
    source = (
        "class Meta(type):\n def __call__(cls, value: str) -> int: return len(value)\n"
        "class C(metaclass=Meta):\n def __new__(cls, wrong: int): return super().__new__(cls)\n"
        "class D(C): pass\n"
    )
    result = inspect_source(worker, tmp_path, source)
    for name in ("C", "D"):
        assert [p["name"] for p in result[name]["parameters"]] == ["value"]
        assert result[name]["parameters"][0]["type-path"] == ["str"]
        assert result[name]["constructor-return"]["type-path"] == ["int"]
    namespace = {}
    exec(source, namespace)
    assert namespace["D"]("abc") == 3


def test_cls_typevar_metaclass_and_unannotated_new_keep_normal_init(worker, tmp_path):
    source = (
        "from typing import TypeVar\nT = TypeVar('T')\n"
        "class Meta(type):\n def __call__(cls: type[T], *args, **kwargs) -> T: return type.__call__(cls, *args, **kwargs)\n"
        "class C(metaclass=Meta):\n def __new__(cls, *args, **kwargs): return super().__new__(cls)\n"
        " def __init__(self, required: int): pass\n"
    )
    info = inspect_source(worker, tmp_path, source)["C"]
    assert [p["name"] for p in info["parameters"]] == ["required"]
    assert "constructor-return" not in info


def test_subclass_new_result_still_uses_init_contract(worker, tmp_path):
    source = (
        "class C:\n def __new__(cls, *args) -> 'D': return object.__new__(D)\n"
        " def __init__(self, required: int): pass\nclass D(C): pass\n"
    )
    info = inspect_source(worker, tmp_path, source)["C"]
    assert [p["name"] for p in info["parameters"]] == ["required"]
    assert info["constructor-return"]["type-path"] == ["D"]
    namespace = {}
    exec(source, namespace)
    assert type(namespace["C"](1)) is namespace["D"]
    with pytest.raises(TypeError):
        namespace["C"]()


def test_overloaded_constructor_results_remain_per_signature(worker, tmp_path):
    source = (
        "from typing import overload\nclass C:\n"
        " @overload\n def __new__(cls, value: int) -> int: ...\n"
        " @overload\n def __new__(cls, value: str) -> str: ...\n"
        " def __new__(cls, value): return value\n"
        " def __init__(self, impossible: bool): pass\n"
    )
    info = inspect_source(worker, tmp_path, source)["C"]
    assert [s["constructor-return"]["type-path"] for s in info["overloads"]] == [["int"], ["str"]]
    assert all([p["name"] for p in s["parameters"]] == ["value"] for s in info["overloads"])


def test_subclass_new_result_does_not_validate_the_wrong_initializer(worker, tmp_path, monkeypatch):
    import sys
    import types

    source = (
        "class C:\n def __new__(cls, *args) -> 'D': return object.__new__(D)\n"
        " def __init__(self, required: str): raise AssertionError('wrong initializer')\n"
        "class D(C):\n def __init__(self, other: int): self.value = other\n"
    )
    info = inspect_source(worker, tmp_path, source)["C"]
    assert "parameters" not in info
    assert info["constructor-return"]["type-path"] == ["D"]
    module = types.ModuleType("blt_constructor_subclass")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    exec(source, vars(module))
    module.C.__new__.__annotations__["return"] = module.D
    assert module.C(1).value == 1
    runtime = worker.safe_member("C", module.C)
    assert "parameters" not in runtime


def test_runtime_foreign_new_and_metaclass_preserve_identity(worker):
    class C:
        def __new__(cls) -> int:
            return 1

        def __init__(self, required: str):
            pass

    class Meta(type):
        def __call__(cls, value: str) -> int:
            return len(value)

    class D(metaclass=Meta):
        def __new__(cls, wrong: int):
            return super().__new__(cls)

    for value, parameters in [(C, []), (D, ["value"])]:
        info = worker.safe_member(value.__name__, value)
        assert info["kind"] == "class"
        assert info["type-path"] == value.__qualname__.split(".")
        assert [p["name"] for p in info["parameters"]] == parameters
        assert info["constructor-return"]["type-path"] == ["int"]
    assert C() == 1
    assert D("abc") == 3


def test_runtime_metaclass_descriptor_is_not_invoked(worker):
    invoked = []

    class Descriptor:
        def __get__(self, obj, owner):
            invoked.append(True)
            raise AssertionError("descriptor must not execute")

    class Meta(type):
        __call__ = Descriptor()

    class C(metaclass=Meta):
        def __init__(self, required: str):
            pass

    info = worker.safe_member("C", C)
    assert info["kind"] == "class"
    assert "parameters" not in info
    assert info["constructor-return"] == {"type-any?": True}
    assert invoked == []


def test_runtime_unannotated_metaclass_does_not_invent_init_contract(worker):
    class Meta(type):
        def __call__(cls, *args):
            return 1

    class C(metaclass=Meta):
        def __init__(self, required: str):
            pass

    info = worker.safe_member("C", C)
    assert "parameters" not in info
    assert info["constructor-return"] == {"type-any?": True}
    assert C() == 1


def test_unannotated_metaclass_leaves_static_call_and_result_unknown(worker, tmp_path):
    source = (
        "class Meta(type):\n def __call__(cls, *args): return 1\n"
        "class C(metaclass=Meta):\n def __init__(self, required: str): pass\n"
    )
    info = inspect_source(worker, tmp_path, source)["C"]
    assert info["type-path"] == ["C"]
    assert "parameters" not in info
    assert info["constructor-return"] == {"type-any?": True}
    namespace = {}
    exec(source, namespace)
    assert namespace["C"]().bit_length() == 1


def test_metaclass_callable_property_is_unknown_without_descriptor_execution(worker, tmp_path):
    source = (
        "from collections.abc import Callable\nevents = []\n"
        "class Meta(type):\n @property\n def __call__(cls) -> Callable[[], int]:\n"
        "  events.append('called')\n  return lambda: 1\n"
        "class C(metaclass=Meta):\n def __init__(self, required: str): raise AssertionError('wrong init')\n"
    )
    info = inspect_source(worker, tmp_path, source)["C"]
    assert info["type-path"] == ["C"]
    assert "parameters" not in info
    assert info["constructor-return"] == {"type-any?": True}
    namespace = {}
    exec(source, namespace)
    runtime = worker.safe_member("C", namespace["C"])
    assert "parameters" not in runtime
    assert runtime["constructor-return"] == {"type-any?": True}
    assert namespace["events"] == []
    assert namespace["C"]().bit_length() == 1
    assert namespace["events"] == ["called"]


def test_direct_constructor_assignment_preserves_foreign_or_unknown_result(worker, tmp_path):
    source = (
        "class Plain:\n def __new__(cls) -> int: return 1\n"
        "class Meta(type):\n def __call__(cls, *args): return 1\n"
        "class Unknown(metaclass=Meta): pass\n"
        "plain = Plain()\nunknown = Unknown()\n"
    )
    info = inspect_source(worker, tmp_path, source)
    assert info["plain"]["type-path"] == ["int"]
    assert info["unknown"]["type-any?"] is True
    assert "type-path" not in info["unknown"]


def test_basilisp_calls_use_constructor_results_and_validate_selected_contract(tmp_path):
    import importlib
    import basilisp_tools  # noqa: F401
    from basilisp.lang.keyword import keyword
    from basilisp.lang.runtime import to_lisp

    source = (
        "from typing import Generic, TypeVar, overload\nfrom typing_extensions import Self\nT = TypeVar('T')\n"
        "class Plain:\n def __new__(cls) -> int: return 1\n def __init__(self, required: str): pass\n"
        "class Meta(type):\n def __call__(cls, value: str) -> int: return len(value)\n"
        "class ViaMeta(metaclass=Meta):\n def __new__(cls, wrong: int): return super().__new__(cls)\n"
        "class Factory:\n @overload\n def __new__(cls, value: int) -> int: ...\n"
        " @overload\n def __new__(cls, value: str) -> str: ...\n"
        " def __new__(cls, value): return value\n"
        "class UnknownMeta(type):\n def __call__(cls, *args): return 1\n"
        "class Unknown(metaclass=UnknownMeta):\n def __init__(self, required: str): pass\n"
        "class Mixed(Generic[T]):\n @overload\n def __new__(cls, tag: int, value: object) -> int: ...\n"
        " @overload\n def __new__(cls, tag: str, value: object) -> Self: ...\n"
        " def __new__(cls, tag: int | str, value: object) -> object: return object.__new__(cls) if isinstance(tag, str) else 1\n"
        " def __init__(self, tag: str, value: T): pass\n"
    )
    namespace = {}
    exec(source, namespace)
    assert type(namespace["Mixed"]("ok", 1)) is namespace["Mixed"]
    assert namespace["Mixed"](1, 1) == 1
    (tmp_path / "constructors.py").write_text(source)
    analyzer = importlib.import_module("basilisp_tools.analyzer")
    bridge = importlib.import_module("basilisp_tools.python")
    cache = bridge.create_cache()
    try:
        result = analyzer.analyze(
            '(ns fixture (:import constructors))\n'
            '(constructors/Plain)\n(constructors/ViaMeta "hello")\n'
            '(constructors/Factory "hello")\n(constructors/ViaMeta 3)\n(constructors/Plain 1)\n'
            '(.bit_length (constructors/Unknown))\n(constructors/Mixed "ok" 1)\n(constructors/Mixed 1 1)\n',
            to_lisp({"python-options": {"python-paths": [str(tmp_path)], "python-inspection?": False, "python-cache": cache}}),
        )
        inferred = {item[keyword("row")]: item[keyword("python-type")]
                    for item in result[keyword("python-expressions")] if item[keyword("col")] == 1}
        assert [list(inferred[row][keyword("path")]) for row in (2, 3, 4)] == [["int"], ["int"], ["str"]]
        # A selected Self-returning __new__ branch currently retains nominal
        # identity; generic specialization from a separate __init__ is unknown.
        assert list(inferred[8][keyword("path")]) == ["Mixed"]
        assert not inferred[8].get(keyword("arguments"))
        assert list(inferred[9][keyword("path")]) == ["int"]
        errors = [(item[keyword("row")], str(item[keyword("type")])) for item in result[keyword("findings")]
                  if item[keyword("type")] in (keyword("invalid-arity"), keyword("type-mismatch"),
                                                keyword("unresolved-python-member"))]
        assert errors == [(5, ":type-mismatch"), (6, ":invalid-arity")]
    finally:
        bridge.stop_cache__BANG__(cache)
