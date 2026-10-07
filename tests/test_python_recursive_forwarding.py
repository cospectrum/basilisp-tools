import importlib
import pytest
import basilisp_tools
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp

SOURCE = """from typing import Callable, ParamSpec, TypeVar
P=ParamSpec('P')
T=TypeVar('T')
def forward(f:Callable[P,T],*args:P.args,**kwargs:P.kwargs)->T:...
def text(f:Callable[P,T],*args:P.args,**kwargs:P.kwargs)->str:...
def integer(value:int,/)->int:...
def keyword(*,value:int)->int:...
def collisions(value:int,/,**kwargs:str)->None:...
def objects(values:list[object])->None:...
from typing import Iterable, Literal
def flatten(*values:Iterable[T])->list[T]:...
def flatten_covariant(*values:Iterable[T])->tuple[T,...]:...
def element(values:list[T])->T:...
def nested(values:list[list[T]])->T:...
literals:list[Literal["x"]]
nested_literals:list[list[Literal["x"]]]
def constrained_iterables(*values:Iterable[int])->None:...
def choose(x:T|int,y:T)->T:...
def optional(x:T|None)->T:...
Bound=TypeVar("Bound",bound=int)
def bound_optional(x:Bound|None)->None:...
"""


@pytest.fixture(scope="module")
def contracts(tmp_path_factory):
    root = tmp_path_factory.mktemp("forwarding")
    (root / "fwd.pyi").write_text(SOURCE)
    bridge = importlib.import_module("basilisp_tools.python")
    analyzer = importlib.import_module("basilisp_tools.analyzer")
    cache = bridge.create_cache()
    opts = to_lisp({"python-paths": [str(root)], "python-inspection?": False}).assoc(
        k("python-cache"), cache
    )
    try:
        yield analyzer, opts
    finally:
        bridge.stop_cache__BANG__(cache)


@pytest.mark.parametrize(
    "body,valid",
    [
        ("(f/text f/integer 1)", True),
        ('(f/text f/integer "x")', False),
        ("(f/text f/text f/integer 1)", True),
        ('(f/text f/text f/integer "x")', False),
        ("(f/text f/text)", False),
        ("(f/text f/text f/text f/integer 1)", True),
        ("(f/text f/text f/keyword ** :value 1)", True),
        ('(f/text f/text f/keyword ** :value "x")', False),
        ("(f/text f/text f/keyword)", False),
        ("(f/text ** :f f/text :args 1)", False),
        ('(f/text f/text f/collisions 1 ** :value "x")', True),
        ("(f/text f/text f/collisions 1 ** :value 2)", False),
        ("(f/text f/text f/objects #py [1])", True),
        ("(let [alias #py [1]] (f/text f/text f/objects alias))", False),
    ],
)
def test_nested_forwarding(contracts, body, valid):
    analyzer, opts = contracts
    result = analyzer.analyze(
        "(ns recursive-fwd (:import [fwd :as f])) " + body,
        to_lisp({}).assoc(k("python-options"), opts),
    )
    findings = list(result.val_at(k("findings")))
    assert (len(findings) == 0) is valid, findings


@pytest.mark.parametrize(
    "body,valid",
    [
        ('(f/flatten "abc" #py (1 2 3))', True),
        ('(f/flatten #b "abc" #py ("x"))', True),
        ('(f/flatten #py {"x" 1} #py (2 3))', True),
        ('(f/constrained-iterables "abc")', False),
        ('(f/constrained-iterables #py {"x" 1})', False),
        ('(f/constrained-iterables #py (1 "x"))', False),
        ("(f/constrained-iterables #py (1 2))", True),
    ],
)
def test_iterable_elements_are_inferred_from_all_builtin_elements(
    contracts, body, valid
):
    analyzer, opts = contracts
    result = analyzer.analyze(
        "(ns iterable-infer (:import [fwd :as f])) " + body,
        to_lisp({}).assoc(k("python-options"), opts),
    )
    findings = list(result.val_at(k("findings")))
    assert (len(findings) == 0) is valid, findings


def test_fixed_union_alternative_does_not_widen_other_parameter(contracts):
    analyzer, opts = contracts
    result = analyzer.analyze(
        '(ns union-infer (:import [fwd :as f])) (f/choose 1 "x")',
        to_lisp({}).assoc(k("python-options"), opts),
    )
    inferred = list(result.val_at(k("python-expressions")))[-1].val_at(k("python-type"))
    assert inferred == to_lisp({"module": "builtins", "path": ["str"]})
    assert list(result.val_at(k("findings"))) == []


@pytest.mark.parametrize(
    "body,valid",
    [
        ("(f/bound-optional nil)", True),
        ("(f/bound-optional 1)", True),
        ('(f/bound-optional "bad")', False),
    ],
)
def test_optional_bound_retains_its_none_branch(contracts, body, valid):
    analyzer, opts = contracts
    result = analyzer.analyze(
        "(ns optional-bound (:import [fwd :as f])) " + body,
        to_lisp({}).assoc(k("python-options"), opts),
    )
    assert (len(result.val_at(k("findings"))) == 0) is valid


def test_nullable_typevar_cannot_hide_a_none_value():
    bridge = importlib.import_module("basilisp_tools.python")
    integer = {"module": "builtins", "path": ["int"]}
    bounded = {"typevar": "T", "bound": integer, "nullable?": True}
    assert bridge.type_compatible__Q__(to_lisp(bounded), to_lisp(integer)) is False
    assert (
        bridge.type_compatible__Q__(
            to_lisp(bounded), to_lisp({**integer, "nullable?": True})
        )
        is True
    )


def test_bytearray_elements_are_integer_sequences():
    bridge = importlib.import_module("basilisp_tools.python")
    actual = to_lisp({"module": "builtins", "path": ["bytearray"]})
    for target in (
        "Iterable",
        "Sequence",
        "Collection",
        "Container",
        "MutableSequence",
    ):
        integer = to_lisp(
            {
                "module": "collections.abc",
                "path": [target],
                "arguments": [{"module": "builtins", "path": ["int"]}],
            }
        )
        text = to_lisp(
            {
                "module": "collections.abc",
                "path": [target],
                "arguments": [{"module": "builtins", "path": ["str"]}],
            }
        )
        assert bridge.type_compatible__Q__(actual, integer) is True
        assert bridge.type_compatible__Q__(actual, text) is False


@pytest.mark.parametrize(
    "body,expected",
    [
        (
            "(f/element f/literals)",
            {"module": "builtins", "path": ["str"], "literal-values": ["x"]},
        ),
        (
            "(f/nested f/nested-literals)",
            {"module": "builtins", "path": ["str"], "literal-values": ["x"]},
        ),
        (
            '(f/flatten-covariant #b "abc" #py ("x"))',
            {
                "module": "builtins",
                "path": ["tuple"],
                "arguments": [
                    {
                        "union": [
                            {"module": "builtins", "path": ["int"]},
                            {
                                "module": "builtins",
                                "path": ["str"],
                                "literal-values": ["x"],
                            },
                        ]
                    },
                    {"ellipsis?": True},
                ],
            },
        ),
    ],
)
def test_generic_element_annotations_preserve_literals(contracts, body, expected):
    analyzer, opts = contracts
    result = analyzer.analyze(
        "(ns contained-literals (:import [fwd :as f])) " + body,
        to_lisp({}).assoc(k("python-options"), opts),
    )
    assert list(result.val_at(k("findings"))) == []
    inferred = list(result.val_at(k("python-expressions")))[-1].val_at(k("python-type"))
    assert inferred == to_lisp(expected)
