"""Explicit legacy variance metadata stays separate from inferred PEP695 variance."""

import importlib.util
from pathlib import Path
import typing
import pytest


@pytest.fixture
def worker():
    path = Path(__file__).resolve().parents[1] / "src/basilisp_tools/_inspect.py"
    spec = importlib.util.spec_from_file_location("_variance_worker", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("factory", [typing.TypeVar, typing.ParamSpec])
@pytest.mark.parametrize(
    "options,expected",
    [
        ({}, "invariant"),
        ({"covariant": True}, "covariant"),
        ({"contravariant": True}, "contravariant"),
    ],
)
def test_runtime_explicit_variance(worker, factory, options, expected):
    assert worker.runtime_type(factory("T", **options))["type-variance"] == expected


@pytest.mark.parametrize("factory", ["TypeVar", "ParamSpec"])
@pytest.mark.parametrize(
    "options,expected",
    [
        ("", "invariant"),
        (", covariant=True", "covariant"),
        (", contravariant=True", "contravariant"),
        (", covariant=unknown", None),
        (", infer_variance=True", None),
        (", covariant=True,contravariant=True", None),
    ],
)
def test_static_explicit_variance_never_evaluates_arguments(
    worker, tmp_path, factory, options, expected
):
    path = tmp_path / "variance.py"
    path.write_text(
        f'from typing import Generic,{factory}\nT={factory}("T"{options})\nclass C(Generic[T]):pass\n'
    )
    inspector = worker.StaticInspector([str(tmp_path)], [])
    result = worker.static_module("variance", path, inspector)
    assert result["members"]["C"]["type-parameters"][0].get("type-variance") == expected
