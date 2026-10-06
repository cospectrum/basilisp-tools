"""Regression tests for upstream fixture adaptation and coverage checks."""

import importlib.util
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("check_kondo_upstream", SCRIPTS / "check_kondo_upstream.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def test_namespace_adaptation_keeps_other_libraries():
    assert audit.adapt("[clojure.core clojure.core.async clojure.test.check clojure.core/inc]") == (
        "[basilisp.core clojure.core.async clojure.test.check basilisp.core/inc]"
    )


def test_diagnostic_column_adaptation_tracks_only_replaced_namespaces():
    source = "clojure.core.async clojure.core/inc 😀 missing"
    column = len(source[:source.index("missing")].encode("utf-16-le")) // 2 + 1
    finding = {"type": "unresolved-symbol", "level": "error", "message": "clojure.core/inc",
               "row": 1, "col": column, "end-row": 1, "end-col": column + 7}
    actual = audit.normalize([finding], source)[0]
    assert actual["col"] == column + 1
    assert actual["end-col"] == column + 8
    assert actual["message"] == "basilisp.core/inc"


def test_message_adaptation_accepts_sentence_punctuation():
    result = audit.normalize([{"message": "namespace clojure.string. clojure.core.async"}])
    assert result[0]["message"] == "namespace basilisp.string. clojure.core.async"
    assert audit.adapt("clojure.string.") == "clojure.string."


def test_empty_extraction_is_an_error():
    with pytest.raises(ValueError, match="no cases"):
        audit.validate_capture({"cases": [], "skipped": []})


def test_duplicate_ids_across_cases_and_skips_are_errors():
    with pytest.raises(ValueError, match="duplicate"):
        audit.validate_capture({"cases": [{"id": "same"}], "skipped": [{"id": "same"}]})
