"""Exercise Python interop against real installed packages.

Run with numpy, requests, types-requests, and pydantic in the selected interpreter.
These are development fixtures, not blt runtime dependencies.
"""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
import subprocess
import tempfile
import sys
import time

import basilisp_tools  # noqa: F401: install the Basilisp importer
from basilisp.lang.keyword import keyword
from basilisp.lang.map import map as lmap
from basilisp.lang.vector import vector


def get(value, key, default=None):
    return value.val_at(keyword(key), default) if value is not None else default


def require(condition, message):
    if not condition:
        raise AssertionError(message)



def check_runtime_models(executable, worker, timeout):
    # These fixtures are deliberately executed in the development interpreter;
    # user project sources continue to be inspected as ASTs only.
    source = """import importlib.util, json, sys
from typing import Generic, TypeVar
from pydantic import BaseModel, ConfigDict, Field, create_model
spec = importlib.util.spec_from_file_location("worker", sys.argv[1])
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
T = TypeVar("T")
class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    count: int = 0
    external: str = Field(alias="externalName")
    values: list[str] = Field(default_factory=list)
class Box(BaseModel, Generic[T]):
    value: T
record = worker.runtime_member("Record", Record)
box = worker.runtime_member("Box", Box[int])
dynamic = worker.runtime_member("Dynamic", create_model("Dynamic", value=(str, ...)))
print(json.dumps({"record": record["parameters"], "box": box["parameters"],
                  "dynamic": dynamic["parameters"]}))
"""
    process = subprocess.run([executable, "-I", "-c", source, str(worker)],
                             text=True, capture_output=True, timeout=timeout, check=True)
    result = json.loads(process.stdout)
    fields = {field["name"]: field for field in result["record"]}
    require(set(fields) == {"name", "count", "externalName", "values"},
            "Pydantic constructor fields and aliases were lost")
    require(fields["name"]["required?"] and fields["externalName"]["required?"]
            and not fields["count"]["required?"] and not fields["values"]["required?"],
            "Pydantic required/default/default_factory metadata is incorrect")
    require(fields["values"]["type-arguments"][0]["type-path"] == ["str"],
            "Pydantic generic field annotation is unavailable")
    require(result["box"][0]["type-path"] == ["int"],
            "Specialized Pydantic generic model constructor lost its field type")
    require(result["dynamic"][0]["type-path"] == ["str"],
            "Pydantic create_model metadata is unavailable")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable, help="Interpreter containing the test packages.")
    parser.add_argument("--timeout", type=float, default=5, help="Inspection timeout in seconds (default: 5).")
    args = parser.parse_args()
    bridge = importlib.import_module("basilisp_tools.python")
    analyzer = importlib.import_module("basilisp_tools.analyzer")
    options = lmap({keyword("python-executable"): args.python,
                    keyword("python-timeout"): args.timeout,
                    keyword("python-cache"): bridge.create_cache()})
    started = time.monotonic()

    array = bridge.inspect_path("numpy", vector(["array"]), options)
    require(get(array, "status") == keyword("known"), "numpy.array metadata is unavailable")
    require(get(array, "parameters") is not None or len(get(array, "overloads", [])) > 0,
            "numpy.array needs a usable signature")
    require(get(array, "type-module") and get(array, "type-path"),
            "numpy.array needs a resolved return type")

    array_result = bridge.call_result(array, vector([None]), lmap({}), options)
    require(get(array_result, "module") == "numpy" and list(get(array_result, "path", [])) == ["ndarray"],
            "Ambiguous ndarray overloads should retain their common ndarray result")

    request = bridge.inspect_path("requests", vector(["get"]), options)
    require(get(request, "parameters") is not None or len(get(request, "overloads", [])) > 0,
            "requests.get stub signature is unavailable")
    require(get(request, "type-path") and "Response" in get(request, "type-path"),
            "requests.get should resolve to Response")
    response = bridge.inspect_path(get(request, "type-module"), get(request, "type-path"), options)
    require(bridge.resolve_member(response, "raise_for_status") is not None,
            "Response methods should be discoverable")
    bound = bridge.bind_member(bridge.resolve_member(response, "raise_for_status"))
    require(bridge.check_call(bound, 0, vector([])) == vector([]),
            "Response.raise_for_status should bind its receiver")

    base_model = bridge.inspect_path("pydantic", vector(["BaseModel"]), options)
    require(bridge.resolve_member(base_model, "model_dump") is not None,
            "Pydantic BaseModel methods should be discoverable")

    check_runtime_models(args.python, Path(bridge.__file__).with_name("_inspect.py"), args.timeout)
    with tempfile.TemporaryDirectory() as folder:
        Path(folder, "package_models.py").write_text(
            "from pydantic import BaseModel\n"
            "class User(BaseModel):\n"
            "    name: str\n", encoding="utf-8")
        local_options = options.assoc(keyword("python-paths"), vector([folder]))
        inherited = bridge.inspect_member(lmap({keyword("module"): "package_models",
                                               keyword("path"): vector(["User"])}),
                                          "model_dump", local_options)
        require(get(inherited, "status") == keyword("known"),
                "Static project models should inherit installed Pydantic methods")

    source = """(ns package-smoke (:import [numpy :as np] [requests :as requests]))
(def values (np/array #py [1 2 3]))
(.mean values)
(def response (requests/get "https://example.invalid"))
(.raise_for_status response)
"""
    result = analyzer.analyze(source, lmap({keyword("filename"): "package_smoke.lpy",
                                           keyword("python-options"): options}))
    require(len(get(result, "findings")) == 0, "Valid package calls produced findings: " + str(get(result, "findings")))
    methods = {get(item, "name"): get(item, "definition") for item in get(result, "python-usages", [])}
    require(get(methods.get("mean"), "status") == keyword("known"),
            "Analyzer lost ndarray type after numpy.array")
    require(get(methods.get("raise_for_status"), "status") == keyword("known"),
            "Analyzer lost Response type after requests.get")
    print(f"Real-package interop passed (NumPy, Requests stubs, Pydantic; {time.monotonic() - started:.2f}s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
