"""A recursive Python type alias must not crash Basilisp type comparisons."""

import importlib
import importlib.util
import json
from pathlib import Path

import pytest

import basilisp_tools  # noqa: F401
from basilisp.lang.keyword import keyword
from basilisp.lang.map import map as persistent_map
from basilisp.lang.runtime import to_lisp
from basilisp.lang.vector import vector


@pytest.mark.parametrize("graph", [False, True])
def test_unknown_type_positions_are_hashable_without_changing_member_names(graph):
    path = Path(__file__).resolve().parents[1] / "src/basilisp_tools/_inspect.py"
    spec = importlib.util.spec_from_file_location("_blt_recursive_decoder", path)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    decoder = worker.metadata_decoder(keyword, persistent_map, vector)
    metadata = {"status": "known", "kind": "class", "type-arguments": [{}],
                "type-union": [{}], "type-constraints": [{}], "bases": [{}],
                "type-parameters": [{"typevar": "T", "type-bound": {}, "type-default": {}}],
                "members": {name: {"status": "unknown", "name": name}
                            for name in ("typevar", "status", "type-arguments")}}
    decoded = json.loads(json.dumps(worker.encode_graph(metadata) if graph else metadata), object_hook=decoder)
    hash(decoded)
    assert set(decoded.val_at(keyword("members"))) == set(metadata["members"])
    assert decoded.val_at(keyword("type-arguments"))[0] == persistent_map({})


def test_recursive_callable_type_metadata_is_hashable(tmp_path):
    source = (
        'from typing import Callable, Literal, TypeVar\n'
        'T = TypeVar("T")\n'
        'Recursive = Literal[5] | list["Recursive"]\n'
        'def recursive(value: Recursive) -> None: ...\n'
        'def literal(value: Literal[5]) -> None: ...\n'
        'def infer(first: Callable[[T], None], second: Callable[[T], None]) -> T: ...\n'
    )
    (tmp_path / "recursive_consumers.py").write_text(source)
    analyzer = importlib.import_module("basilisp_tools.analyzer")
    bridge = importlib.import_module("basilisp_tools.python")
    cache = bridge.create_cache()
    options = to_lisp({"python-paths": [str(tmp_path)], "python-inspection?": False, "python-cache": cache})
    try:
        metadata = bridge.inspect_path("recursive_consumers", to_lisp(["recursive"]), options)
        hash(metadata)
        # A successful analysis is the regression: the old memoization path
        # raises TypeError before returning any result.
        result = analyzer.analyze(
            "(ns fixture (:import recursive_consumers))\n"
            "(recursive_consumers/infer recursive_consumers/recursive recursive_consumers/literal)",
            to_lisp({"python-options": options}),
        )
        assert len(result.val_at(keyword("findings"))) == 0
    finally:
        bridge.stop_cache__BANG__(cache)
