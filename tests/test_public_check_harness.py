"""A successful checker process must still analyze every selected public file."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "public_checker_audit",
    Path(__file__).resolve().parents[1] / "scripts" / "check_public_check.py",
)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


@pytest.mark.parametrize("finding_type,message,valid", [
    ("file", "Analysis failed: simulated analyzer exception", False),
    ("syntax", "Analysis failed: simulated analyzer exception", False),
    ("file", "Unable to read selected file", False),
    ("syntax", "Invalid syntax in an intentional test fixture", True),
])
def test_public_checker_audit_distinguishes_failures_from_findings(
    tmp_path, monkeypatch, finding_type, message, valid,
):
    corpus = tmp_path / "corpus"
    checkout = corpus / "example"
    checkout.mkdir(parents=True)
    (checkout / "main.lpy").write_text("(ns main)\n", encoding="utf-8")
    manifest = tmp_path / "projects.json"
    manifest.write_text(json.dumps([{"repo": "owner/example", "sha": "abc"}]))
    output = tmp_path / "output"
    monkeypatch.setattr(sys, "argv", [
        "check_public_check.py", "--corpus", str(corpus),
        "--manifest", str(manifest), "--output", str(output),
    ])
    monkeypatch.setattr(
        audit.subprocess, "check_output",
        lambda command, **kwargs: "abc\n" if "rev-parse" in command else "main.lpy\0",
    )
    payload = {
        "summary": {"files": 1, "error": 1},
        "findings": [{"filename": "main.lpy", "type": finding_type,
                      "level": "error", "message": message}],
    }
    monkeypatch.setattr(audit, "run_command", lambda *args: {
        "exit": 3, "timeout": False, "seconds": 0.1,
        "child_user_seconds": 0.1, "child_system_seconds": 0.0,
        "child_cpu_seconds": 0.1, "stdout": json.dumps(payload), "stderr": "",
    })

    assert audit.main() == int(not valid)
    row, = json.loads((output / "summary.json").read_text())
    assert row["valid"] is valid
    assert row["runs"][0]["valid"] is valid
    assert bool(row["runs"][0]["file_failures"]) is not valid
    assert ("error" in row) is not valid
