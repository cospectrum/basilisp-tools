"""Dynamic Python attribute dispatch can bypass declared descriptor contracts."""

import importlib
import pytest
import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp

SOURCE = """from typing import Any, Protocol
class Descriptor:
    def __get__(self, obj: Any, owner: Any) -> int:
        return 1
class Plain:
    descriptor: Descriptor = Descriptor()
    field: int = 1
    @property
    def readonly(self) -> int:
        return 1
    def method(self, value: int) -> int:
        return value
class Dynamic(Plain):
    def __getattribute__(self, name: str) -> Any:
        if name in {"descriptor", "field"}:
            return "ok"
        if name == "method":
            return lambda value: "ok"
        return object.__getattribute__(self, name)
    def __setattr__(self, name: str, value: object) -> None:
        object.__getattribute__(self, "__dict__")[name] = value
class InheritedDynamic(Dynamic):
    pass
class Meta(type):
    def __getattribute__(cls, name: str) -> Any:
        if name in {"descriptor", "field"}:
            return "ok"
        if name == "method":
            return lambda value: "ok"
        return type.__getattribute__(cls, name)
    def __setattr__(cls, name: str, value: object) -> None:
        type.__setattr__(cls, name, value)
class SubMeta(Meta):
    pass
class DynamicClass(Plain, metaclass=Meta):
    pass
class InheritedMetaClass(Plain, metaclass=SubMeta):
    pass
class DynamicReadOnly(Plain):
    def __getattribute__(self, name: str) -> Any:
        if name in {"descriptor", "field"}:
            return "ok"
        if name == "method":
            return lambda value: "ok"
        return object.__getattribute__(self, name)
class DynamicWriteOnly(Plain):
    def __setattr__(self, name: str, value: object) -> None:
        object.__getattribute__(self, "__dict__")[name] = value
class TextField(Protocol):
    field: str
class TextMethod(Protocol):
    def method(self, value: str) -> str: ...
class WritableProperty(Protocol):
    readonly: int
def text_field(value: TextField) -> None:
    assert isinstance(value.field, str)
def text_method(value: TextMethod) -> None:
    assert isinstance(value.method("argument"), str)
def writable_property(value: WritableProperty) -> None:
    value.readonly = 1
def text(value: str) -> None:
    assert isinstance(value, str)
"""


@pytest.fixture(scope="module")
def environment(tmp_path_factory):
    root = tmp_path_factory.mktemp("dynamic_attribute_contracts")
    (root / "dynamic_attributes.py").write_text(SOURCE)
    bridge = importlib.import_module("basilisp_tools.python")
    analyzer = importlib.import_module("basilisp_tools.analyzer")
    cache = bridge.create_cache()
    options = to_lisp(
        {
            "python-options": {
                "python-paths": [str(root)],
                "python-inspection?": False,
                "python-cache": cache,
            }
        }
    )
    try:
        yield analyzer, options
    finally:
        bridge.stop_cache__BANG__(cache)


INSTANCE_CALLS = [
    "(m/text (.-descriptor (m/{name})))",
    "(m/text (.-field (m/{name})))",
    '(m/text (.method (m/{name}) "argument"))',
    '(set! (.-readonly (m/{name})) "ok")',
    '(set! (.-field (m/{name})) "ok")',
]
CLASS_CALLS = [
    "(m/text (.-descriptor m/{name}))",
    "(m/text (.-field m/{name}))",
    '(m/text (.method m/{name} "argument"))',
    '(set! (.-field m/{name}) "ok")',
]


@pytest.mark.parametrize(
    "body",
    [
        body.format(name=name)
        for name in ("Dynamic", "InheritedDynamic")
        for body in INSTANCE_CALLS
    ]
    + [
        body.format(name=name)
        for name in ("DynamicClass", "InheritedMetaClass")
        for body in CLASS_CALLS
    ],
)
def test_dynamic_dispatch_does_not_invent_attribute_errors(environment, body):
    analyzer, options = environment
    result = analyzer.analyze(
        "(ns dynamic-attributes (:import [dynamic_attributes :as m])) " + body, options
    )
    assert list(result[k("findings")]) == []


@pytest.mark.parametrize(
    "body",
    [
        "(m/text (.-descriptor (m/Plain)))",
        "(m/text (.-field (m/Plain)))",
        '(.method (m/Plain) "argument")',
        '(set! (.-readonly (m/Plain)) "ok")',
        '(set! (.-field (m/Plain)) "ok")',
        "(m/text (.-descriptor m/Plain))",
        '(set! (.-field m/Plain) "ok")',
    ],
)
def test_ordinary_attribute_contracts_remain_enforced(environment, body):
    analyzer, options = environment
    result = analyzer.analyze(
        "(ns ordinary-attributes (:import [dynamic_attributes :as m])) " + body, options
    )
    assert [finding[k("type")] for finding in result[k("findings")]] == [
        k("type-mismatch")
    ]


def test_dynamic_dispatch_matches_native_python():
    scope = {}
    exec(SOURCE, scope)
    for name in ("Dynamic", "InheritedDynamic"):
        value = scope[name]()
        scope["text"](value.descriptor)
        scope["text"](value.field)
        scope["text"](value.method("argument"))
        value.readonly = "ok"
        value.field = "ok"
    for name in ("DynamicClass", "InheritedMetaClass"):
        value = scope[name]
        scope["text"](value.descriptor)
        scope["text"](value.field)
        scope["text"](value.method("argument"))
        value.field = "ok"
    with pytest.raises(AttributeError):
        scope["Plain"]().readonly = "ok"


@pytest.mark.parametrize(
    "body,valid",
    [
        ("(m/text-field (m/Dynamic))", True),
        ("(m/text-field (m/InheritedDynamic))", True),
        ("(m/text-method (m/Dynamic))", True),
        ("(m/text-method (m/InheritedDynamic))", True),
        ("(m/writable-property (m/Dynamic))", True),
        ("(m/text-field (m/Plain))", False),
        ("(m/text-method (m/Plain))", False),
        ("(m/writable-property (m/DynamicReadOnly))", False),
        ("(m/text-field (m/DynamicWriteOnly))", False),
    ],
)
def test_structural_protocols_use_effective_instance_contracts(
    environment, body, valid
):
    analyzer, options = environment
    result = analyzer.analyze(
        "(ns protocol-dispatch (:import [dynamic_attributes :as m])) " + body, options
    )
    findings = list(result[k("findings")])
    if valid:
        assert findings == []
    else:
        assert [finding[k("type")] for finding in findings] == [k("type-mismatch")]


def test_dynamic_protocol_controls_match_native_python():
    scope = {}
    exec(SOURCE, scope)
    for name in ("Dynamic", "InheritedDynamic"):
        value = scope[name]()
        scope["text_field"](value)
        scope["text_method"](value)
        scope["writable_property"](value)
    with pytest.raises(AttributeError):
        scope["writable_property"](scope["DynamicReadOnly"]())
    with pytest.raises(AssertionError):
        scope["text_field"](scope["DynamicWriteOnly"]())
