"""Unbound instance methods preserve their actual generic Self receiver."""

import importlib

import basilisp_tools
import pytest
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp

SOURCE = """from typing import Generic, TypeVar
from typing_extensions import Self
T = TypeVar("T")
class Box(Generic[T]):
    def __init__(self, value: T) -> None: self.value = value
    def copy(self, /) -> Self: return self
    def named(receiver) -> Self: return receiver
    def pair(self) -> tuple[Self, Self]: return (self, self)
    def optional(self) -> Self | None: return self
    def choose(self, other: Self) -> Self: return other
    @staticmethod
    def text(value: int) -> str: return str(value)
class Sub(Box[T]): pass
class Plain:
    def copy(self) -> Self: return self
    @classmethod
    def make(cls) -> Self: return cls()
class Child(Plain): pass
unknown: object
def accept_string_box(value: Box[str]) -> None: pass
"""


@pytest.fixture(scope="module")
def contracts(tmp_path_factory):
    root = tmp_path_factory.mktemp("unbound_self")
    (root / "self_fixture.py").write_text(SOURCE)
    bridge = importlib.import_module("basilisp_tools.python")
    analyzer = importlib.import_module("basilisp_tools.analyzer")
    cache = bridge.create_cache()
    opts = to_lisp({"python-paths": [str(root)], "python-inspection?": False}).assoc(
        k("python-cache"), cache
    )
    try:
        yield analyzer, bridge, opts
    finally:
        bridge.stop_cache__BANG__(cache)


def analyze(contracts, expression):
    analyzer, _, opts = contracts
    return analyzer.analyze(
        "(ns self-audit (:import [self_fixture :as s])) " + expression,
        to_lisp({}).assoc(k("python-options"), opts),
    )


def scalar(name):
    return {"module": "builtins", "path": [name]}


def instance(name, *arguments):
    result = {"module": "self_fixture", "path": [name]}
    if arguments:
        result["arguments"] = list(arguments)
    return result


def result_type(result):
    return list(result.val_at(k("python-expressions")))[-1].val_at(k("python-type"))


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("(s/Box.copy (s/Box 1))", instance("Box", scalar("int"))),
        ('(s/Box.copy (s/Box "a"))', instance("Box", scalar("str"))),
        ("(s/Box.copy (s/Sub 1))", instance("Sub", scalar("int"))),
        ("(s/Sub.copy (s/Sub 1))", instance("Sub", scalar("int"))),
        ("(s/Box.named ** :receiver (s/Box 1))", instance("Box", scalar("int"))),
        ("(s/Plain.copy (s/Child))", instance("Child")),
    ],
)
def test_actual_unbound_receiver_preserves_type_arguments(
    contracts, expression, expected
):
    result = analyze(contracts, expression)
    assert list(result.val_at(k("findings"))) == []
    assert result_type(result) == to_lisp(expected)


def test_nested_self_result(contracts):
    result = analyze(contracts, "(s/Box.pair (s/Box 1))")
    assert list(result.val_at(k("findings"))) == []
    expected = {**scalar("tuple"), "arguments": [instance("Box", scalar("int"))] * 2}
    assert result_type(result) == to_lisp(expected)


def test_nullable_self_result(contracts):
    result = analyze(contracts, "(s/Box.optional (s/Box 1))")
    assert list(result.val_at(k("findings"))) == []
    assert result_type(result) == to_lisp(
        {**instance("Box", scalar("int")), "nullable?": True}
    )


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("(.copy (s/Box 1))", instance("Box", scalar("int"))),
        ("(s/Box.text 1)", scalar("str")),
        ("(s/Plain.make)", instance("Plain")),
    ],
)
def test_bound_static_and_class_methods_keep_existing_contracts(
    contracts, expression, expected
):
    result = analyze(contracts, expression)
    assert list(result.val_at(k("findings"))) == []
    assert result_type(result) == to_lisp(expected)


def test_receiver_binding_does_not_hide_arity_failure(contracts):
    result = analyze(contracts, "(s/Box.copy)")
    assert any(
        f.val_at(k("type")) == k("invalid-arity") for f in result.val_at(k("findings"))
    )


def test_unknown_or_unrelated_receiver_is_not_invented_as_self(contracts):
    _, bridge, opts = contracts
    member = bridge.inspect_path("self_fixture", to_lisp(["Box", "copy"]), opts)
    for receiver in [
        scalar("str"),
        scalar("object"),
        scalar("NoneType"),
        {"any?": True},
    ]:
        inferred = bridge.call_result(member, to_lisp([receiver]), to_lisp({}), opts)
        assert inferred != to_lisp(receiver)


def test_native_unbound_and_bound_receivers_are_identical():
    namespace = {}
    exec(SOURCE, namespace)
    box, sub = namespace["Box"](1), namespace["Sub"](1)
    assert namespace["Box"].copy(box) is box
    assert namespace["Box"].copy(sub) is sub
    assert namespace["Box"].named(receiver=box) is box
    assert box.copy() is box
    assert namespace["Box"].pair(box) == (box, box)


def test_additional_self_input_is_not_specialized_from_receiver_alone(contracts):
    result = analyze(
        contracts,
        '(s/accept_string_box (s/Box.choose (s/Box 1) (s/Box "a")))',
    )
    assert list(result.val_at(k("findings"))) == []
    namespace = {}
    exec(SOURCE, namespace)
    first, other = namespace["Box"](1), namespace["Box"]("a")
    chosen = namespace["Box"].choose(first, other)
    assert chosen is other
    assert chosen.value == "a"
    assert namespace["accept_string_box"](chosen) is None


def test_nullable_return_does_not_make_none_a_valid_self_receiver(contracts):
    _, bridge, opts = contracts
    member = bridge.inspect_path("self_fixture", to_lisp(["Box", "optional"]), opts)
    inferred = bridge.call_result(
        member, to_lisp([scalar("NoneType")]), to_lisp({}), opts
    )
    assert inferred.val_at(k("path")) == to_lisp(["Box"])


def test_unresolved_additional_self_owner_still_blocks_receiver_only_inference(
    contracts,
):
    _, bridge, opts = contracts
    member = bridge.inspect_path("self_fixture", to_lisp(["Box", "copy"]), opts)
    parameters = list(member.val_at(k("parameters"))) + [
        to_lisp(
            {
                "name": "other",
                "kind": k("positional-or-keyword"),
                "required?": True,
                "type-self?": True,
            }
        )
    ]
    incomplete = member.assoc(k("parameters"), to_lisp(parameters))
    inferred = bridge.call_result(
        incomplete,
        to_lisp([instance("Box", scalar("int")), {"any?": True}]),
        to_lisp({}),
        opts,
    )
    assert inferred.val_at(k("arguments")) is None
