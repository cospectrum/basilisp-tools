"""A differential summary never silently accepts changed input or oracle scope."""

import copy
import importlib
from pathlib import Path

import pytest


@pytest.fixture
def comparison(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("compare_python_typing")


def report(status="passed"):
    return {"source_hashes_start": {"scripts/adapter.py": "same"}, "source_hashes_end": {},
            "source_changed_during_run": False, "sources": [], "scope": {}, "fixtures": [
                {"provider": "typing", "path": "example.py", "name": "example", "source_sha256": "source",
                 "cases": [{"id": "case", "status": status, "kind": "call", "expected_error": False, "expression": "f()"}]}
            ]}


def test_new_false_positive_is_retained_for_review(comparison):
    after = report("failed")
    after["fixtures"][0]["cases"][0]["findings"] = [{"type": "type-mismatch"}]
    result = comparison.compare(report("unknown"), after)
    assert result["transitions"] == {"unknown -> failed": 1}
    assert result["newly_failing_expected_valid"][0]["id"] == "case"
    assert result["remaining_differences"] == ["case"]


def test_negative_reveal_is_not_counted_as_a_matched_return(comparison):
    cases = [
        {"status": "passed", "kind": "call", "expected_error": False},
        {"status": "passed", "kind": "call", "expected_error": True},
        {"status": "passed", "kind": "return", "expected_error": False},
        {"status": "passed", "kind": "return", "expected_error": True},
    ]
    result = comparison.coverage(cases)
    assert result == {"matched_positive_calls": 1, "matched_negative_calls": 2,
                      "matched_return_assertions": 1, "expected_valid_diagnostic_cases": 0}


@pytest.mark.parametrize("change", ["source", "oracle", "adapter", "missing", "duplicate", "concurrent", "unfinished"])
def test_changed_or_incomplete_evidence_cannot_be_compared(comparison, change):
    before = report()
    after = copy.deepcopy(before)
    if change == "source":
        after["fixtures"][0]["source_sha256"] = "different"
    elif change == "oracle":
        after["oracle"] = {"sha256": "new"}
    elif change == "adapter":
        after["source_hashes_start"]["scripts/adapter.py"] = "different"
    elif change == "missing":
        after["fixtures"][0]["cases"].clear()
    elif change == "duplicate":
        after["fixtures"][0]["cases"] *= 2
    elif change == "concurrent":
        after["source_changed_during_run"] = True
    elif change == "unfinished":
        del after["source_hashes_end"]
    with pytest.raises(ValueError):
        comparison.compare(before, after)
