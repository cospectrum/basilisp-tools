import importlib
import pytest
import basilisp_tools
from basilisp.lang.runtime import to_lisp
from basilisp.lang.keyword import keyword as k


def builtin(name):
    return {"module": "builtins", "path": [name]}


def raw(type):
    result = {}
    for key, value in type.items():
        if key == "module":
            result["type-module"] = value
        elif key == "path":
            result["type-path"] = value
        elif key == "arguments":
            result["type-arguments"] = [raw(x) for x in value]
        elif key == "default":
            result["type-default"] = raw(value)
        else:
            result[key] = value
    return result


@pytest.fixture
def bridge():
    return importlib.import_module("basilisp_tools.python")


def model(parameters):
    return to_lisp(
        {
            "status": k("known"),
            "kind": k("class"),
            "name": "Box",
            "type-module": "models",
            "type-path": ["Box"],
            "parameters": [],
            "type-parameters": [raw(x) for x in parameters],
            "type-arguments": [
                raw({**x, **({"unpack?": True} if x.get("variadic?") else {})})
                for x in parameters
            ],
        }
    )


def fixed_parameters():
    return [
        {"typevar": "T", "default": builtin("str")},
        {"typevar": "U", "default": {"typevar": "T"}},
        {
            "typevar": "V",
            "default": {**builtin("list"), "arguments": [{"typevar": "U"}]},
        },
    ]


@pytest.mark.parametrize(
    "supplied,expected",
    [
        ([], ["str", "str", "str"]),
        (["int"], ["int", "int", "int"]),
        (["int", "float"], ["int", "float", "float"]),
    ],
)
def test_dependent_defaults_follow_earlier_arguments(bridge, supplied, expected):
    metadata = model(fixed_parameters())
    receiver = to_lisp(
        {
            "module": "models",
            "path": ["Box"],
            "arguments": [builtin(x) for x in supplied],
        }
    )
    result = bridge.return_type(bridge.specialize(metadata, receiver))
    assert result == to_lisp(
        {
            "module": "models",
            "path": ["Box"],
            "arguments": [
                builtin(expected[0]),
                builtin(expected[1]),
                {**builtin("list"), "arguments": [builtin(expected[2])]},
            ],
        }
    )


def test_constructor_default_completion_happens_after_inference(bridge):
    metadata = model(fixed_parameters())
    result = bridge.call_result(metadata, to_lisp([]), to_lisp({}))
    assert result == to_lisp(
        {
            "module": "models",
            "path": ["Box"],
            "arguments": [
                builtin("str"),
                builtin("str"),
                {**builtin("list"), "arguments": [builtin("str")]},
            ],
        }
    )
    metadata = metadata.assoc(
        k("parameters"),
        to_lisp(
            [
                {
                    "name": "value",
                    "kind": k("positional-only"),
                    "required?": True,
                    "typevar": "T",
                }
            ]
        ),
    )
    result = bridge.call_result(metadata, to_lisp([builtin("int")]), to_lisp({}))
    assert result == to_lisp(
        {
            "module": "models",
            "path": ["Box"],
            "arguments": [
                builtin("int"),
                builtin("int"),
                {**builtin("list"), "arguments": [builtin("int")]},
            ],
        }
    )


@pytest.mark.parametrize(
    "supplied,expected",
    [
        ([], ["str", "str", "str"]),
        (["int"], ["int", "int", "int"]),
        (["int", "float"], ["int", "float"]),
    ],
)
def test_variadic_defaults_expand_after_fixed_defaults(bridge, supplied, expected):
    parameters = [
        {"typevar": "T", "default": builtin("str")},
        {
            "typevar": "Ts",
            "variadic?": True,
            "default": {
                **builtin("tuple"),
                "unpack?": True,
                "arguments": [{"typevar": "T"}, {"typevar": "T"}],
            },
        },
    ]
    result = bridge.return_type(
        bridge.specialize(
            model(parameters),
            to_lisp(
                {
                    "module": "models",
                    "path": ["Box"],
                    "arguments": [builtin(x) for x in supplied],
                }
            ),
        )
    )
    assert result == to_lisp(
        {
            "module": "models",
            "path": ["Box"],
            "arguments": [builtin(x) for x in expected],
        }
    )


def test_explicit_empty_unpack_overrides_pack_default(bridge):
    parameters = [
        {"typevar": "T", "default": builtin("str")},
        {
            "typevar": "Ts",
            "variadic?": True,
            "default": {
                **builtin("tuple"),
                "unpack?": True,
                "arguments": [{"typevar": "T"}],
            },
        },
    ]
    receiver = to_lisp(
        {
            "module": "models",
            "path": ["Box"],
            "arguments": [
                builtin("int"),
                {**builtin("tuple"), "unpack?": True, "arguments": []},
            ],
        }
    )
    result = bridge.return_type(bridge.specialize(model(parameters), receiver))
    assert result == to_lisp(
        {"module": "models", "path": ["Box"], "arguments": [builtin("int")]}
    )


def test_class_self_receives_completed_defaults(bridge):
    metadata = model(fixed_parameters()).assoc(
        k("members"),
        to_lisp(
            {"copy": {"status": k("known"), "kind": k("function"), "type-self?": True}}
        ),
    )
    receiver = to_lisp(
        {"module": "models", "path": ["Box"], "arguments": [builtin("int")]}
    )
    result = bridge.specialize(metadata, receiver)
    assert bridge.return_type(
        result.val_at(k("members")).val_at(k("copy"))
    ) == bridge.return_type(result)
