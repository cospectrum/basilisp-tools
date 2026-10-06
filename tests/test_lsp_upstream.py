"""Check that the upstream audit preserves assertions and fails closed."""

import importlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


script = Path(__file__).resolve().parents[1] / "scripts" / "check_lsp_upstream.py"
spec = importlib.util.spec_from_file_location("_blt_lsp_upstream", script)
audit = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(script.parent))
try:
    spec.loader.exec_module(audit)
finally:
    sys.path.pop(0)
selection = {
    "lib/test/clojure_lsp/refactor/transform_test.clj": {"thread-test", "change-coll-test"},
    "lib/test/clojure_lsp/feature/thread_get_test.clj": None,
}


def fixture_checkout(tmp_path, threading):
    # These are original harness fixtures, not vendored upstream tests.
    base = tmp_path / "lib/test/clojure_lsp"
    (base / "refactor").mkdir(parents=True)
    (base / "feature").mkdir(parents=True)
    (base / "refactor/transform_test.clj").write_text(
        threading + '\n(deftest change-coll-test (is (= "(1 2)" (change-coll "[1 2]" "list"))))',
        encoding="utf-8",
    )
    (base / "feature/thread_get_test.clj").write_text(
        '(deftest lookup-test (assert-get-in-more "(get m :a)" "|(:a m)"))',
        encoding="utf-8",
    )
    return tmp_path


def test_capture_retains_cursor_expected_text_settings_and_exclusions(tmp_path):
    checkout = fixture_checkout(tmp_path, '''
(deftest ignored-test (arbitrary-code))
(deftest thread-test
  (let [code "(inc |2)"]
    (is (= (h/code "(-> 2" "    inc)") (thread-first code)))))
''')
    cases, skipped, suites, inventory = audit.capture_cases(checkout, selection)
    assert len(cases) == 3
    assert cases[0]["source"] == "(inc 2)"
    assert cases[0]["offset"] == 5
    assert cases[0]["expected"] == "(-> 2\n    inc)"
    assert cases[0]["line"] == 3
    assert cases[1]["args"] == ["list"]
    assert len(skipped) == 1  # The lookup macro also asserts availability.
    assert {item["test"] for item in suites} == {"thread-test", "change-coll-test", "lookup-test"}
    assert len(inventory) == 4
    assert [item["test"] for item in inventory if not item["selected"]] == ["ignored-test"]


def test_capture_rejects_unhandled_assertions_in_selected_suite(tmp_path):
    checkout = fixture_checkout(tmp_path, '(deftest thread-test (is (unknown-assertion "data")))')
    with pytest.raises(ValueError, match="Unsupported upstream assertion"):
        audit.capture_cases(checkout, selection)


def test_capture_applies_and_resets_threading_settings(tmp_path):
    checkout = fixture_checkout(tmp_path, '''
(deftest thread-test
  (swap! (h/db*) shared/deep-merge {:settings {:keep-parens-when-threading? true}})
  (is (= "(-> 1 (inc))" (thread-first "|(inc 1)")))
  (h/reset-components!)
  (is (= "(-> 1 inc)" (thread-first "|(inc 1)"))))
''')
    cases, _, _, _ = audit.capture_cases(checkout, selection)
    assert cases[0]["settings"] == {"keep-parens-when-threading?": True}
    assert cases[1]["settings"] == {}


def test_structural_similarity_does_not_count_as_exact_text_passes():
    case = {"command": "thread-first", "source": "(inc 1)", "offset": 0, "args": [],
            "settings": {}, "expected": "(-> 1\n       inc)"}
    result, = audit.compare_cases([case])
    assert result["status"] == "structural-only"
    assert result["structural_match"] is True


def test_no_edit_expectation_requires_no_edit():
    case = {"command": "thread-first", "source": "[1 2]", "offset": 0, "args": [],
            "settings": {}, "expected": None}
    result, = audit.compare_cases([case])
    assert result["status"] == "exact"
    assert result["edit_count"] == 0


def test_capture_expands_assertion_tables_and_threaded_helpers(tmp_path):
    checkout = fixture_checkout(tmp_path, '''
(deftest thread-test
  (are [expected source] (= expected (-> source thread-first))
    "(-> 2 inc)" "|(inc 2)"
    "(-> 3 inc)" "|(inc 3)"))
''')
    cases, _, _, _ = audit.capture_cases(checkout, selection)
    assert [case["source"] for case in cases[:2]] == ["(inc 2)", "(inc 3)"]
    assert [case["expected"] for case in cases[:2]] == ["(-> 2 inc)", "(-> 3 inc)"]


def test_replacement_assertions_compare_edits_with_context_retained():
    case = {"command": "change-coll", "source": "[1 2] :untouched", "offset": 0,
            "args": ["list"], "settings": {}, "expected": "(1 2)", "output": "replacement"}
    result, = audit.compare_cases([case])
    assert result["status"] == "exact"
    assert result["actual_document"] == "(1 2) :untouched"


def test_single_replacement_helper_reads_first_edit_without_discarding_other_edits(monkeypatch):
    import basilisp_tools  # noqa: F401

    lsp = importlib.import_module("basilisp_tools.lsp")
    monkeypatch.setattr(lsp, "refactor_edits", lambda *args: [[0, 1, "3"], [2, 3, "4"]])
    case = {"command": "change-coll", "source": "1 2", "offset": 0, "args": [],
            "settings": {}, "expected": "3", "output": "replacement"}
    result, = audit.compare_cases([case])
    assert result["status"] == "exact"
    assert result["actual_document"] == "3 4"
    assert result["edit_count"] == 2


def test_repeated_unwinding_reselects_the_updated_expression():
    case = {"command": "unwind-thread", "source": "(identity (-> 1 inc inc))", "offset": 18,
            "args": [], "settings": {}, "expected": "(inc (inc 1))", "output": "replacement", "repeat": 2}
    result, = audit.compare_cases([case])
    assert result["status"] == "exact"
    assert result["actual_document"] == "(identity (inc (inc 1)))"


def test_baseline_protects_exact_matches_but_allows_known_differences():
    inventory = [{"selected": True, "assertion_forms": 3}]
    results = [{"id": "exact", "status": "exact"},
               {"id": "different", "status": "mismatch"},
               {"id": "structural", "status": "structural-only"}]
    baseline = audit.baseline_snapshot("revision", results, [], inventory)
    assert baseline["matched"] == ["exact"]
    assert audit.baseline_regressions(baseline, baseline) == []
    degraded = audit.baseline_snapshot("revision", [{**case, "status": "structural-only"} for case in results], [], inventory)
    assert audit.baseline_regressions(baseline, degraded) == ["exact"]
    improved = audit.baseline_snapshot("revision", [{**case, "status": "exact"} for case in results], [], inventory)
    assert audit.baseline_regressions(baseline, improved) == []


def test_baseline_rejects_missing_cases_changed_coverage_and_revision():
    baseline = audit.baseline_snapshot("revision", [{"id": "exact", "status": "exact"}], [],
                                       [{"selected": True, "assertion_forms": 1}])
    missing = audit.baseline_snapshot("revision", [], [], [{"selected": True, "assertion_forms": 1}])
    assert audit.baseline_regressions(baseline, missing) == ["exact", "extraction coverage changed: cases"]
    changed = {**baseline, "skipped": 1, "inventory": {**baseline["inventory"], "selected_deftests": 0}}
    assert audit.baseline_regressions(baseline, changed) == ["extraction coverage changed: skipped",
                                                          "extraction coverage changed: inventory"]
    with pytest.raises(ValueError, match="Baseline revision"):
        audit.baseline_regressions(baseline, {**baseline, "revision": "other"})


def test_baseline_and_report_only_never_hide_execution_errors(tmp_path, monkeypatch):
    inventory = [{"selected": True, "assertion_forms": 1}]
    previous = [{"id": "known-difference", "status": "mismatch"}]
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(audit.baseline_snapshot(audit.CLOJURE_LSP_REVISION, previous, [], inventory)),
                        encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [str(script), "--clojure-lsp", str(tmp_path),
                                     "--baseline", str(baseline), "--report-only"])
    monkeypatch.setattr(audit.subprocess, "check_output", lambda command, **kwargs:
                        audit.CLOJURE_LSP_REVISION if command[1] == "rev-parse" else "")
    monkeypatch.setattr(audit, "capture_cases", lambda checkout: ([{}], [], [{}], inventory))
    monkeypatch.setattr(audit, "compare_cases", lambda cases: [
        {"id": "known-difference", "status": "error", "command": "example",
         "expected": "text", "error": "unexpected failure"},
    ])
    assert audit.main() == 1
