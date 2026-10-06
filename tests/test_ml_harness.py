"""The ML audit must distinguish missing contracts from clean diagnostics."""

import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


spec = importlib.util.spec_from_file_location(
    "ml_harness", Path(__file__).resolve().parents[1] / "scripts" / "check_ml_packages.py",
)
ml_harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ml_harness)


def test_known_symbol_without_contract_does_not_pass():
    case = {"expected": "6", "metadata": {"result_name": "Tensor"}}
    analysis = {"runs": [{"findings": []}], "metadata": {"status": "known"}}
    failures, coverage = ml_harness.evaluate_analysis(case, analysis)
    assert failures == ["API signature or expected return type was not resolved"]
    assert coverage == {"symbol_known": True, "signature_resolved": False, "return_resolved": False}


def test_diagnostics_do_not_hide_missing_contracts():
    case = {"expected": "6", "metadata": {"result_name": "Tensor"}}
    analysis = {"runs": [{"findings": [{"type": "type-mismatch"}]}], "metadata": None}
    failures, _ = ml_harness.evaluate_analysis(case, analysis)
    assert len(failures) == 2


def test_warm_success_does_not_hide_cold_inspection_failure():
    analysis = {"runs": [
        {"findings": [{"type": "python-inspection"}]},
        {"findings": []},
    ]}
    failures, _ = ml_harness.evaluate_analysis({"expected": "6"}, analysis)
    assert failures == ["Analyzer or inspection failure", "Valid runtime program produced diagnostics"]


def test_overloads_need_known_signatures_and_a_common_return():
    signature = {"parameters": [], "type-path": ["Tensor"]}
    metadata = {"status": "known", "overloads": [signature, signature]}
    assert all(ml_harness.metadata_coverage(metadata, "Tensor").values())
    metadata["overloads"] = [signature, {"parameters": [], "type-path": ["int"]}]
    assert not ml_harness.metadata_coverage(metadata, "Tensor")["return_resolved"]


def test_unrelated_error_does_not_satisfy_negative_fixture():
    analysis = {"runs": [{"findings": [{"type": "namespace-name-mismatch"}]}]}
    failures, _ = ml_harness.evaluate_analysis({"diagnostic": "invalid-arity"}, analysis)
    assert failures == ["Known invalid argument was not diagnosed"]


def test_summary_keeps_unknown_metadata_visible():
    report = {"cases": [
        {"expected": "6", "failures": ["API signature or expected return type was not resolved"],
         "coverage": {"signature_resolved": False, "return_resolved": False}},
        {"diagnostic": "invalid-arity", "failures": ["Known invalid argument was not diagnosed"]},
    ]}
    ml_harness.update_summary(report)
    assert report["summary"]["unresolved_signatures"] == 1
    assert report["summary"]["unresolved_returns"] == 1
    assert report["summary"]["positive_diagnostic_cases"] == 0
    assert report["summary"]["missed_negative_cases"] == 1


def test_empty_manifest_cannot_report_success(tmp_path):
    manifest = tmp_path / "empty.json"
    manifest.write_text("[]")
    result = subprocess.run([sys.executable, ml_harness.__file__, "--manifest", str(manifest)], capture_output=True, text=True)
    assert result.returncode != 0
    assert "must not be empty" in result.stderr


def test_resolved_api_does_not_hide_lost_receiver():
    case = {"expected": "6", "metadata": {"result_name": "Tensor"}, "required_members": ["sum"]}
    analysis = {"runs": [{"findings": [], "python_usages": []}],
                "metadata": {"status": "known", "parameters": [], "type-path": ["Tensor"]}}
    failures, coverage = ml_harness.evaluate_analysis(case, analysis)
    assert failures == ["Result member metadata was not resolved"]
    assert not coverage["members_resolved"]


def upstream_adapter(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("torch_upstream", scripts / "check_torch_upstream.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_module_lookup_preserves_case_on_macos(monkeypatch, tmp_path):
    adapter = upstream_adapter(monkeypatch)
    package = tmp_path / "torch" / "nn"
    package.mkdir(parents=True)
    (package / "parameter.py").write_text("class Parameter: pass\n")
    translator = adapter.Adapter("pass/example.py", "", tmp_path, "fixture")
    value = translator.dotted("torch.nn.Parameter")
    assert value.endswith("/Parameter")
    assert "torch.nn" in translator.aliases
    assert "torch.nn.Parameter" not in translator.aliases


def test_parallel_assignments_keep_each_value_and_evaluate_once(monkeypatch, tmp_path):
    adapter = upstream_adapter(monkeypatch)
    translated = adapter.Adapter(
        "pass/example.py", "import torch\nx, y = torch.randn(3), 2\nx + y\n", tmp_path, "fixture",
    ).build()
    assert translated["source"].count("/randn") == 1
    assert "(def case_2_0 (py_0/randn 3))" in translated["source"]
    assert "(+ case_2_0 case_2_1)" in translated["source"]
    for row in translated["statements"]:
        if row["status"] == "adapted":
            start = row["expression_start"]
            assert translated["source"][start:].startswith(row["expression"])


def test_python_modulo_uses_python_operator_semantics(monkeypatch, tmp_path):
    adapter = upstream_adapter(monkeypatch)
    translated = adapter.Adapter("pass/example.py", "5 % 2", tmp_path, "fixture").build()
    assert "/mod 5 2" in translated["source"]
    assert "[operator :as" in translated["header"]


def test_union_assertions_preserve_container_arguments(monkeypatch):
    adapter = upstream_adapter(monkeypatch)
    integer = {"module": "builtins", "path": ["int"]}
    inferred = {"union": [
        {"module": "builtins", "path": ["tuple"], "arguments": [integer, {"ellipsis?": True}]},
        {"module": "builtins", "path": ["list"], "arguments": [integer]},
    ]}
    expected = adapter.expected_type_shape("list[int] | tuple[int, ...]")
    assert adapter.inferred_type_shape(inferred) == expected
    assert adapter.runtime_type_agrees(expected, {"name": "list"})
    inferred["union"].append({"module": "builtins", "path": ["str"]})
    assert adapter.inferred_type_shape(inferred) != expected
    assert adapter.concrete_type_shape(adapter.inferred_type_shape(inferred))


def test_dynamo_assertions_use_original_typed_input_branches(monkeypatch, tmp_path):
    adapter = upstream_adapter(monkeypatch)
    source = """from torch._dynamo.utils import istype
def check(x: object):
    if istype(x, (str, int)):
        assert_type(x, str | int)
    if istype(x, (list, tuple)):
        assert_type(x, list[Any] | tuple[Any, ...])
"""
    artifact = adapter.Adapter("pass/dynamo_utils.py", source, tmp_path, "fixture").build()
    rows = [row for row in artifact["statements"] if row["status"] == "adapted"]
    assert len(rows) == 2
    assert not any(row["status"] == "not-adapted" for row in artifact["statements"])
    for row in rows:
        assert artifact["source"][row["expression_start"]:].startswith("guard_value_")
        assert row["input_factory"].startswith("_input_check_")


def test_class_assertions_accept_proven_custom_metaclasses(monkeypatch):
    from enum import Enum
    adapter = upstream_adapter(monkeypatch)

    class Color(Enum):
        RED = 1

    expected = adapter.expected_type_shape("type[Color]")
    assert adapter.runtime_type_agrees(expected, adapter.runtime_result_type(Color))
    assert not adapter.runtime_type_agrees(expected, adapter.runtime_result_type(Color.RED))


def test_file_diagnostics_cannot_escape_expression_accounting(monkeypatch):
    adapter = upstream_adapter(monkeypatch)
    report = {"files": [{"statements": [], "analysis": {"findings": [{"type": "syntax", "start": 0}]}}]}
    adapter.summarize(report)
    assert report["summary"]["unattributed_diagnostics"] == 1


def test_reviewed_limitations_keep_failure_counts_and_reject_stale_entries():
    item = {"case": "tensor", "reason": "Result member metadata was not resolved"}
    report = {"cases": [{"name": "tensor"}], "failures": [item]}
    baseline = {"allowed_failures": [item]}
    assert ml_harness.compare_baseline(report, baseline)["passed"]
    assert report["failures"] == [item]
    report["failures"] = []
    result = ml_harness.compare_baseline(report, baseline)
    assert not result["passed"]
    assert result["stale"] == [item]


def test_positive_diagnostics_cannot_be_baselined():
    item = {"case": "tensor", "reason": "Valid runtime program produced diagnostics"}
    report = {"cases": [{"name": "tensor"}], "failures": [item]}
    with pytest.raises(ValueError, match="permitted missing-type limitations"):
        ml_harness.compare_baseline(report, {"allowed_failures": [item]})
    result = ml_harness.compare_baseline(report, {"allowed_failures": []})
    assert not result["passed"]
    assert result["unexpected"] == [item]


def test_wrong_upstream_expectation_does_not_accept_arbitrary_concrete_type(monkeypatch):
    adapter = upstream_adapter(monkeypatch)
    row = {"status": "adapted", "negative": False, "expected_type": "bool",
           "native": {"status": "valid", "result_type": {"module": "torch", "name": "Tensor"}, "runtime_parity": True},
           "inferred": [{"module": "builtins", "path": ["str"]}], "findings": []}
    report = {"files": [{"statements": [row], "analysis": {"findings": []}}]}
    adapter.summarize(report)
    assert report["summary"]["upstream_type_assertions_disagree_runtime"] == 1
    assert report["summary"]["runtime_type_mismatches"] == 1
    row["inferred"] = [{"module": "torch._tensor", "path": ["Tensor"]}]
    adapter.summarize(report)
    assert report["summary"]["runtime_type_mismatches"] == 0


@pytest.mark.parametrize("internal", [
    {"type": "syntax", "message": "Cannot parse source"},
    {"type": ":file", "message": "Cannot read source"},
    {"type": "python-inspection", "message": "Inspection timed out"},
    {"type": "type-mismatch", "message": "Analysis failed: KeyError"},
])
def test_internal_failure_never_satisfies_negative_case(internal):
    expected = {"type": "type-mismatch", "message": "Wrong operand"}
    analysis = {"runs": [{"findings": [expected, internal]}, {"findings": [expected]}]}
    failures, _ = ml_harness.evaluate_analysis({"diagnostic": "type-mismatch"}, analysis)
    assert failures == ["Analyzer or inspection failure"]
    item = {"case": "negative", "reason": failures[0]}
    report = {"cases": [{"name": "negative"}], "failures": [item]}
    with pytest.raises(ValueError, match="permitted missing-type limitations"):
        ml_harness.compare_baseline(report, {"allowed_failures": [item]})


def test_internal_failure_inside_negative_expression_fails_upstream_audit(monkeypatch):
    adapter = upstream_adapter(monkeypatch)
    findings = [{"type": "type-mismatch", "start": 5},
                {"type": "syntax", "start": 6, "message": "Analysis failed: KeyError"}]
    row = {"status": "adapted", "negative": True, "start": 0, "end": 20,
           "native": {"status": "exception", "runtime_parity": True}, "findings": findings}
    report = {"files": [{"statements": [row], "analysis": {"findings": findings}}]}
    adapter.summarize(report)
    assert report["summary"]["negative_misses"] == 0
    assert report["summary"]["unattributed_diagnostics"] == 0
    assert report["summary"]["analysis_internal_failures"] == 1
