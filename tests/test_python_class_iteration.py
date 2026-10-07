"""Class iteration uses metaclass slots and preserves represented-class generics."""

import importlib
import pytest
import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp

SOURCE = """from typing import Iterable, Iterator, TypeVar
T=TypeVar("T")
M=TypeVar("M")
class Meta(type):
    def __iter__(self:type[M])->Iterator[M]:
        yield self()
class Foo(metaclass=Meta): pass
class Sub(Foo): pass
class Numbers(type):
    def __iter__(self)->Iterator[int]:
        yield 42
class NumberClass(metaclass=Numbers):
    def __iter__(self)->Iterator[str]:
        yield "instance"
class StaticNumbers(type):
    @staticmethod
    def __iter__()->Iterator[int]:
        yield 42
class StaticClass(metaclass=StaticNumbers): pass
class ClassNumbers(type):
    @classmethod
    def __iter__(cls)->Iterator[int]:
        yield 42
class ClassMethodClass(metaclass=ClassNumbers): pass
class Intercepts(type):
    def __getattribute__(self,name):
        if name=="__iter__": return lambda: iter(["dynamic attribute"])
        return super().__getattribute__(name)
    def __iter__(self)->Iterator[int]:
        yield 42
class Intercepted(metaclass=Intercepts): pass
class InstanceOnly:
    def __iter__(self)->Iterator[str]:
        yield "instance"
def first(values:Iterable[T])->T:
    return next(iter(values))
def numbers(values:Iterable[int])->int:
    return next(iter(values))
def strings(values:Iterable[str])->str:
    return next(iter(values))
"""


@pytest.fixture(scope="module")
def contracts(tmp_path_factory):
    root = tmp_path_factory.mktemp("class_iteration")
    (root / "iteration.py").write_text(SOURCE)
    bridge = importlib.import_module("basilisp_tools.python")
    analyzer = importlib.import_module("basilisp_tools.analyzer")
    cache = bridge.create_cache()
    opts = to_lisp({"python-paths": [str(root)], "python-inspection?": False}).assoc(
        k("python-cache"), cache
    )
    try:
        yield bridge, analyzer, opts
    finally:
        bridge.stop_cache__BANG__(cache)


def analyze(contracts, body):
    _, analyzer, opts = contracts
    return analyzer.analyze(
        "(ns class-iteration (:import [iteration :as i])) " + body,
        to_lisp({}).assoc(k("python-options"), opts),
    )


@pytest.mark.parametrize(
    "body,name",
    [
        ("(i/first i/Foo)", "Foo"),
        ("(i/first i/Sub)", "Sub"),
        ("(i/first i/NumberClass)", "int"),
        ("(i/first i/StaticClass)", "int"),
        ("(i/first i/ClassMethodClass)", "int"),
        ("(i/first i/Intercepted)", "int"),
    ],
)
def test_class_slot_call_infers_the_metaclass_receiver(contracts, body, name):
    result = analyze(contracts, body)
    assert list(result.val_at(k("findings"))) == []
    inferred = list(result.val_at(k("python-expressions")))[-1].val_at(k("python-type"))
    assert inferred == to_lisp(
        {"module": "builtins" if name == "int" else "iteration", "path": [name]}
    )


@pytest.mark.parametrize(
    "body",
    [
        "(i/strings i/NumberClass)",
        "(i/strings i/StaticClass)",
        "(i/strings i/ClassMethodClass)",
        "(i/strings i/Intercepted)",
        "(i/numbers 42)",
        '(i/numbers "abc")',
    ],
)
def test_proven_element_and_scalar_mismatches_still_fail(contracts, body):
    findings = list(analyze(contracts, body).val_at(k("findings")))
    assert any(f.val_at(k("type")) == k("type-mismatch") for f in findings), findings


def test_instance_iteration_does_not_prove_class_iteration(contracts):
    bridge, _, opts = contracts
    class_type = to_lisp(
        {
            "module": "builtins",
            "path": ["type"],
            "arguments": [{"module": "iteration", "path": ["InstanceOnly"]}],
        }
    )
    assert bridge.iterated_type(class_type, opts) is None
    expected = to_lisp(
        {
            "module": "collections.abc",
            "path": ["Iterable"],
            "arguments": [{"module": "builtins", "path": ["str"]}],
        }
    )
    assert bridge.type_compatible__Q__(class_type, expected, opts) is None


def test_unavailable_metaclass_evidence_stays_unknown(contracts):
    bridge, _, opts = contracts
    class_type = to_lisp(
        {
            "module": "builtins",
            "path": ["type"],
            "arguments": [{"module": "missing_package", "path": ["Unknown"]}],
        }
    )
    assert bridge.iterated_type(class_type, opts) is None


def test_native_metaclass_slots_and_dynamic_lookup_boundary():
    namespace = {}
    exec(SOURCE, namespace)
    assert isinstance(namespace["first"](namespace["Foo"]), namespace["Foo"])
    assert isinstance(namespace["first"](namespace["Sub"]), namespace["Sub"])
    for name in ("NumberClass", "StaticClass", "ClassMethodClass", "Intercepted"):
        assert namespace["numbers"](namespace[name]) == 42
    assert list(namespace["NumberClass"]()) == ["instance"]
    assert list(namespace["Intercepted"].__iter__()) == ["dynamic attribute"]
    with pytest.raises(TypeError):
        iter(namespace["InstanceOnly"])
