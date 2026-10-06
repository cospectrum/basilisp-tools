"""Check real Torch/ONNX calls against native execution and inspected metadata.

The selected interpreter needs torch==2.14.1, onnx==1.23.2, and Basilisp.
No model downloads, accelerators, training data, or network requests are used.
Set PYTHONPATH to a source snapshot to measure an independent baseline.
"""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import time


def cases():
    """Small deterministic programs and argument errors at the same API boundary."""
    specs = [
        ("tensor", "[torch]", '(int (.sum (torch/tensor #py [1 2 3] ** :dtype torch/int64)))', "6", "torch", "tensor", "Tensor"),
        ("factory-device", "[torch]", '(int (.numel (torch/zeros #py [2 3] ** :device "cpu" :dtype torch/float32)))', "6", "torch", "zeros", "Tensor"),
        ("tensor-method", "[torch]", '(int (.numel (.reshape (torch/arange 6) 2 3)))', "6", "torch", "arange", "Tensor"),
        ("tensor-to", "[torch]", '(int (.sum (.to (torch/tensor #py [1 2 3]) ** :dtype torch/float64 :device "cpu")))', "6", "torch", "tensor", "Tensor"),
        ("tensor-add", "[torch]", '(int (.sum (torch/add (torch/ones 2) (torch/ones 2) ** :alpha 2)))', "6", "torch", "add", "Tensor"),
        ("tensor-matmul", "[torch]", '(int (.sum (torch/matmul (torch/ones 2 3) (torch/ones 3 4))))', "24", "torch", "matmul", "Tensor"),
        ("tensor-native-add", "[torch]", '(int (.sum (+ (torch/ones 2) (torch/ones 2))))', "4", "torch", "ones", "Tensor"),
        ("tensor-native-sub", "[torch]", '(int (.sum (- (torch/ones 2) (torch/ones 2))))', "0", "torch", "ones", "Tensor"),
        ("tensor-native-mul", "[torch]", '(int (.sum (* (torch/ones 2) (torch/ones 2))))', "2", "torch", "ones", "Tensor"),
        ("nn-linear", "[torch] [torch.nn :as nn]", '(int (.numel ((nn/Linear 3 2) (torch/ones 1 3))))', "2", "torch.nn", "Linear", "Linear"),
        ("nn-functional", "[torch] [torch.nn.functional :as functional]", '(int (.sum (functional/relu (torch/tensor #py [-1 2 3]))))', "5", "torch.nn.functional", "relu", "Tensor"),
        ("optimizer", "[torch.nn :as nn] [torch.optim :as optim]", '(let [layer (nn/Linear 3 2) optimizer (optim/SGD (.parameters layer) ** :lr 0.1)] (.zero_grad optimizer) (count (.-param_groups optimizer)))', "1", "torch.optim", "SGD", "SGD"),
        ("training-step", "[torch] [torch.nn :as nn] [torch.optim :as optim]", '(let [layer (nn/Linear 3 2) optimizer (optim/SGD (.parameters layer) ** :lr 0.1) result (layer (torch/ones 2 3)) loss (.sum result)] (.backward loss) (.step optimizer) (int (.numel result)))', "4", "torch.optim", "SGD", "SGD"),
        ("data-loader", "[torch] [torch.utils.data :as data]", '(let [dataset (data/TensorDataset (torch/ones 3 2)) loader (data/DataLoader dataset ** :batch_size 2)] (int (.numel (first (first (seq loader))))))', "4", "torch.utils.data", "DataLoader", "DataLoader"),
        ("no-grad", "[torch]", '(with [_context (torch/no_grad)] (int (.sum (torch/ones 2))))', "2", "torch", "no_grad", "no_grad"),
        ("onnx-array", "[numpy :as np] [onnx.numpy_helper :as arrays]", '(int (.sum (arrays/to_array (arrays/from_array (np/array #py [1 2 3]) ** :name "values"))))', "6", "onnx.numpy_helper", "from_array", "TensorProto"),
        ("onnx-node", "[onnx.helper :as helper]", '(.-op_type (helper/make_node "Add" #py ["x" "y"] #py ["z"]))', '"Add"', "onnx.helper", "make_node", "NodeProto"),
        ("onnx-graph", "[onnx] [onnx.helper :as helper] [onnx.checker :as checker] [onnx.shape_inference :as shapes]", '(let [input (helper/make_tensor_value_info "x" (.-FLOAT onnx/TensorProto) #py [2]) output (helper/make_tensor_value_info "y" (.-FLOAT onnx/TensorProto) #py [2]) node (helper/make_node "Identity" #py ["x"] #py ["y"]) graph (helper/make_graph #py [node] "identity" #py [input] #py [output]) model (helper/make_model graph)] (checker/check_model model) (.-name (.-graph (shapes/infer_shapes model))))', '"identity"', "onnx.shape_inference", "infer_shapes", "ModelProto"),
        ("onnx-reference", "[numpy :as np] [onnx.helper :as helper] [onnx.reference :as reference]", '(let [node (helper/make_node "Add" #py ["x" "y"] #py ["z"]) evaluator (reference/ReferenceEvaluator node) results (.run evaluator nil #py {"x" (np/array #py [1 2]) "y" (np/array #py [3 4])})] (int (.sum (first results))))', "10", "onnx.reference", "ReferenceEvaluator", "ReferenceEvaluator"),
        ("tensor-size", "[torch]", '(int (.numel (torch/Size #py [1 2 3])))', "6", "torch", "Size", "Size"),
        ("distribution-normal", "[torch.distributions :as distributions]", '(int (.item (.-mean (distributions/Normal 0 1))))', "0", "torch.distributions", "Normal", "Normal"),
        ("distribution-multivariate", "[torch] [torch.distributions :as distributions]", '(int (.numel (.-covariance_matrix (distributions/MultivariateNormal (torch/zeros 2) (torch/eye 2)))))', "4", "torch.distributions", "MultivariateNormal", "MultivariateNormal"),
    ]
    required_members = {
        "tensor": ["sum"], "factory-device": ["numel"],
        "tensor-method": ["reshape", "numel"], "tensor-to": ["to", "sum"],
        "tensor-add": ["sum"], "tensor-matmul": ["sum"], "tensor-native-add": ["sum"],
        "tensor-native-sub": ["sum"], "tensor-native-mul": ["sum"],
        "nn-functional": ["sum"], "optimizer": ["parameters", "zero_grad"],
        "training-step": ["parameters", "step"], "no-grad": ["sum"],
        "onnx-array": ["sum"], "onnx-node": ["op_type"], "onnx-graph": ["graph", "name"],
        "onnx-reference": ["run"], "tensor-size": ["numel"],
        "distribution-normal": ["mean", "item"], "distribution-multivariate": ["covariance_matrix", "numel"],
    }
    for name, imports, body, expected, module, member, result in specs:
        yield {"name": name, "source": f"(ns ml-audit.{name} (:import {imports}))\n{body}\n", "expected": expected,
               "required_members": required_members.get(name, []),
               "metadata": {"module": module, "path": [member], "result_name": result}}
    bad = [
        ("tensor-missing-argument", "[torch]", "(torch/tensor)", "invalid-arity"),
        ("tensor-bad-size", "[torch]", '(torch/ones "bad")', "type-mismatch"),
        ("tensor-bad-operand", "[torch]", '(torch/add (torch/ones 2) "bad")', "type-mismatch"),
        ("linear-bad-size", "[torch.nn :as nn]", '(nn/Linear "bad" 2)', "type-mismatch"),
        ("functional-missing-input", "[torch.nn.functional :as functional]", '(functional/relu)', "invalid-arity"),
        ("onnx-node-bad-name", "[onnx.helper :as helper]", '(helper/make_node 42 #py [] #py [])', "type-mismatch"),
        ("onnx-model-bad-graph", "[onnx.helper :as helper]", '(helper/make_model "bad")', "type-mismatch"),
        ("onnx-array-bad-value", "[onnx.numpy_helper :as arrays]", '(arrays/from_array #py [1 2])', "type-mismatch"),
        ("tensor-bad-dtype", "[torch]", '(torch/tensor #py [3] ** :dtype "int32")', "type-mismatch"),
        ("ones-bad-dtype", "[torch]", '(torch/ones 3 ** :dtype "int32")', "type-mismatch"),
        ("zeros-bad-dtype", "[torch]", '(torch/zeros 3 ** :dtype "int32")', "type-mismatch"),
        ("rng-bad-state", "[torch]", '(torch/set_rng_state #py [1 2 3])', "type-mismatch"),
        ("size-bad-add", "[torch]", '(+ (torch/Size #py [1 2 3]) #py ("foo"))', "type-mismatch"),
    ]
    for name, imports, body, diagnostic in bad:
        yield {"name": name, "source": f"(ns ml-audit.{name} (:import {imports}))\n{body}\n", "diagnostic": diagnostic}


def plain(value):
    from basilisp.lang.keyword import Keyword
    from collections.abc import Mapping
    if isinstance(value, Keyword):
        return str(value).removeprefix(":")
    if isinstance(value, Mapping):
        return {str(plain(key)): plain(item) for key, item in value.items()}
    if isinstance(value, (str, bool, int, float)) or value is None:
        return value
    try:
        return [plain(item) for item in value]
    except TypeError:
        return str(value)


def native(selected):
    from basilisp.main import init
    init()
    core = importlib.import_module("basilisp.core")
    import torch
    torch.set_num_threads(1)
    result = []
    for case in selected:
        started = time.monotonic()
        try:
            value = core.load_string(case["source"])
            row = {"name": case["name"], "value": core.pr_str(value), "exception": None}
        except Exception as error:  # Native rejection is required for negative fixtures.
            row = {"name": case["name"], "exception": type(error).__name__, "message": str(error)}
        row["seconds"] = time.monotonic() - started
        result.append(row)
    from importlib.metadata import version
    print(json.dumps({"versions": {name: version(name) for name in ("torch", "onnx", "numpy", "basilisp")},
                      "python": sys.version, "platform": sys.platform, "cases": result}))


def metadata_summary(metadata):
    from basilisp.lang.keyword import keyword as kw
    if metadata is None:
        return None
    fields = ("name", "status", "kind", "filename", "row", "signature", "parameters",
              "type-module", "type-path", "type-arguments", "type-union",
              "type-any?", "nullable?", "return-type")
    result = {key: plain(metadata.val_at(kw(key))) for key in fields if kw(key) in metadata}
    overloads = metadata.val_at(kw("overloads"))
    if overloads is not None:
        result["overloads"] = [metadata_summary(item) for item in overloads]
    return result


def metadata_coverage(metadata, expected_name):
    """A known symbol alone does not prove that its call contract is usable."""
    metadata = metadata or {}
    overloads = metadata.get("overloads", [])
    signatures = overloads or [metadata]
    return {
        "symbol_known": metadata.get("status") == "known",
        "signature_resolved": all(item.get("parameters") is not None for item in signatures),
        "return_resolved": expected_name in metadata.get("type-path", []) or bool(
            overloads and all(expected_name in item.get("type-path", []) for item in overloads)
        ),
    }


def compare_baseline(report, baseline):
    """Match reviewed limitations without changing the audit's failure counts."""
    permitted = {"API signature or expected return type was not resolved",
                 "Result member metadata was not resolved",
                 "Known invalid argument was not diagnosed"}
    cases = {case["name"] for case in report["cases"]}
    entries = baseline["allowed_failures"]
    allowed = {(item["case"], item["reason"]) for item in entries}
    if len(allowed) != len(entries):
        raise ValueError("Duplicate ML baseline allowances")
    if any(case not in cases or reason not in permitted for case, reason in allowed):
        raise ValueError("ML baseline must name selected cases and permitted missing-type limitations")
    actual = {(item["case"], item["reason"]) for item in report["failures"]}

    def records(values):
        return [{"case": case, "reason": reason} for case, reason in sorted(values)]

    return {"matched": records(actual & allowed), "unexpected": records(actual - allowed),
            "stale": records(allowed - actual), "passed": actual == allowed}


def audit_failure_findings(findings):
    """Execution, inspection and parser failures never satisfy a negative case."""
    return [finding for finding in findings
            if finding.get("type", "").removeprefix(":") in {"file", "syntax", "python-inspection"}
            or finding.get("message", "").startswith("Analysis failed:")]


def evaluate_analysis(case, analysis):
    """Keep diagnostic failures separate from missing type information."""
    failures = []
    runs = analysis["runs"]
    coverage = {}
    if any(audit_failure_findings(run["findings"]) for run in runs):
        failures.append("Analyzer or inspection failure")
    if "expected" in case and any(run["findings"] for run in runs):
        failures.append("Valid runtime program produced diagnostics")
    if "diagnostic" in case and not all(any(item["type"] == case["diagnostic"] for item in run["findings"]) for run in runs):
        failures.append("Known invalid argument was not diagnosed")
    if target := case.get("metadata"):
        coverage = metadata_coverage(analysis.get("metadata"), target["result_name"])
        if not all(coverage.values()):
            failures.append("API signature or expected return type was not resolved")
    if members := case.get("required_members"):
        member_coverage = {name: [[((item.get("definition") or {}).get("status") == "known")
                                   for item in run.get("python_usages", []) if item["name"] == name]
                                  for run in runs] for name in members}
        analysis["member_coverage"] = {name: all(statuses and all(statuses) for statuses in repeated)
                                       for name, repeated in member_coverage.items()}
        coverage["members_resolved"] = all(analysis["member_coverage"].values())
        if not coverage["members_resolved"]:
            failures.append("Result member metadata was not resolved")
    return failures, coverage


def update_summary(report):
    rows = report["cases"]
    positives = [row for row in rows if "expected" in row]
    negatives = [row for row in rows if "diagnostic" in row]
    report["summary"] = {
        "cases": len(rows),
        "positive_cases": len(positives),
        "negative_cases": len(negatives),
        "failed_cases": sum(bool(row["failures"]) for row in rows),
        "unresolved_signatures": sum(not row.get("coverage", {}).get("signature_resolved", False) for row in positives),
        "unresolved_returns": sum(not row.get("coverage", {}).get("return_resolved", False) for row in positives),
        "positive_diagnostic_cases": sum("Valid runtime program produced diagnostics" in row["failures"] for row in positives),
        "missed_negative_cases": sum("Known invalid argument was not diagnosed" in row["failures"] for row in negatives),
        "unresolved_member_cases": sum("Result member metadata was not resolved" in row["failures"] for row in positives),
    }


def analyze_case(case, args):
    import basilisp_tools  # noqa: F401 - Register the Basilisp importer.
    from basilisp.lang.runtime import to_lisp
    from basilisp.lang.keyword import keyword as kw
    bridge = importlib.import_module("basilisp_tools.python")
    analyzer = importlib.import_module("basilisp_tools.analyzer")
    cache = bridge.create_cache()
    options = to_lisp({"python-executable": args.python, "python-timeout": args.python_timeout}).assoc(kw("python-cache"), cache)
    report = {"name": case["name"], "source": case["source"], "runs": [], "engine": analyzer.__file__,
              "source_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in (Path(analyzer.__file__), Path(bridge.__file__), Path(bridge.__file__).with_name("_inspect.py"))}}
    try:
        for iteration in range(args.repeats):
            profiler = cProfile.Profile() if args.profile_dir and iteration else None
            if profiler:
                profiler.enable()
            wall, cpu = time.monotonic(), time.process_time()
            try:
                result = analyzer.analyze(case["source"], to_lisp({"filename": case["name"].replace("-", "_") + ".lpy"}).assoc(kw("python-options"), options))
            finally:
                if profiler:
                    profiler.disable()
                    args.profile_dir.mkdir(parents=True, exist_ok=True)
                    profiler.dump_stats(args.profile_dir / f"{case['name']}-{iteration}.pstats")
            report["runs"].append({"seconds": time.monotonic() - wall, "parent_cpu_seconds": time.process_time() - cpu,
                                   "profiled": profiler is not None,
                                   "findings": plain(result.val_at(kw("findings"))),
                                   "python_usages": [{"name": item.val_at(kw("name")),
                                                       "definition": metadata_summary(item.val_at(kw("definition")))}
                                                      for item in result.val_at(kw("python-usages"), [])]})
        if target := case.get("metadata"):
            metadata = bridge.inspect_path(target["module"], to_lisp(target["path"]), options)
            report["metadata"] = metadata_summary(metadata)
    finally:
        bridge.stop_cache__BANG__(cache)
    print(json.dumps(report))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--output", type=Path, default=Path("ml-package-results.json"))
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--manifest", type=Path, help="JSON array of generated cases using the same native/metadata checks.")
    parser.add_argument("--python-timeout", type=float, default=30)
    parser.add_argument("--case-timeout", type=float, default=180)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--profile-dir", type=Path, help="Save cProfile data for warm runs; instrumented timings are not benchmarks.")
    parser.add_argument("--baseline", type=Path, help="Require an exact match to reviewed missing-type limitations; positive diagnostics cannot be allowed.")
    parser.add_argument("--native", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--analyze-case", help=argparse.SUPPRESS)
    args = parser.parse_args()
    available = json.loads(args.manifest.read_text()) if args.manifest else list(cases())
    if not available:
        parser.error("The case manifest must not be empty")
    if len({case["name"] for case in available}) != len(available):
        parser.error("Case names must be unique")
    unknown = set(args.case) - {case["name"] for case in available}
    if unknown:
        parser.error("Unknown cases: " + ", ".join(sorted(unknown)))
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    if args.profile_dir and args.repeats < 2:
        parser.error("--profile-dir requires at least two repeats")
    selected = [case for case in available if not args.case or case["name"] in args.case]
    if args.native:
        native(selected)
        return 0
    if args.analyze_case:
        analyze_case(next(case for case in available if case["name"] == args.analyze_case), args)
        return 0
    command = [args.python, str(Path(__file__).resolve()), "--native"]
    if args.manifest:
        command.extend(["--manifest", str(args.manifest.resolve())])
    for name in args.case:
        command.extend(["--case", name])
    native_process = subprocess.run(command, text=True, capture_output=True, timeout=args.case_timeout)
    native_process.check_returncode()
    runtime = json.loads(native_process.stdout)
    native_rows = {row["name"]: row for row in runtime["cases"]}
    report = {"versions": runtime["versions"], "python": runtime["python"], "platform": runtime["platform"],
              "scope": {"selected_cases": [case["name"] for case in selected],
                        "available_cases": [case["name"] for case in available],
                        "complete_inventory_selected": len(selected) == len(available)},
              "cases": [], "failures": [], "completed": False,
              "timing_notes": "Per-case process, repeated analyzer calls share one inspection cache. Parent CPU excludes inspection children. Exploratory timings; control contention for comparisons."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for case in selected:
        print(json.dumps({"case": case["name"], "stage": "analyze"}), flush=True)
        row = {**case, "native": native_rows[case["name"]], "failures": []}
        native_ok = row["native"].get("value") == case["expected"] if "expected" in case else row["native"].get("exception") in {"TypeError", "ValueError", "RuntimeError", "AttributeError"}
        if not native_ok:
            row["failures"].append("Fixture did not meet its native runtime expectation")
        else:
            try:
                command = [sys.executable, str(Path(__file__).resolve()), "--analyze-case", case["name"], "--python", args.python,
                           "--python-timeout", str(args.python_timeout), "--repeats", str(args.repeats)]
                if args.manifest:
                    command.extend(["--manifest", str(args.manifest.resolve())])
                if args.profile_dir:
                    command.extend(["--profile-dir", str(args.profile_dir.resolve())])
                process = subprocess.run(command, text=True, capture_output=True, timeout=args.case_timeout)
                process.check_returncode()
                row["analysis"] = json.loads(process.stdout)
                row["failures"], row["coverage"] = evaluate_analysis(case, row["analysis"])
            except Exception as error:
                row["failures"].append(repr(error))
                row["stderr"] = getattr(error, "stderr", None)
        for failure in row["failures"]:
            report["failures"].append({"case": case["name"], "reason": failure})
        report["cases"].append(row)
        report["completed"] = len(report["cases"]) == len(selected)
        update_summary(report)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({"case": case["name"], "failures": row["failures"]}), flush=True)
    if args.baseline:
        report["baseline"] = {"path": str(args.baseline),
                              "sha256": hashlib.sha256(args.baseline.read_bytes()).hexdigest(),
                              **compare_baseline(report, json.loads(args.baseline.read_text()))}
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"cases": len(report["cases"]), "failures": len(report["failures"]),
                      "baseline_passed": report.get("baseline", {}).get("passed"), "output": str(args.output)}))
    if args.baseline:
        return 0 if report["baseline"]["passed"] else 1
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
