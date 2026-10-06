"""Public formatter audits must explicitly exercise every selected source."""

import importlib.util
import json
import sys
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "public_formatter_audit",
    Path(__file__).resolve().parents[1] / "scripts" / "check_public_format.py",
)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def test_public_formatter_audit_bypasses_project_discovery_filters(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus"
    checkout = corpus / "example"
    (checkout / ".fixtures").mkdir(parents=True)
    files = [".fixtures/hidden.lpy", "main.lpy"]
    for relative in files:
        (checkout / relative).write_text("(ns demo)\n", encoding="utf-8")
    manifest = tmp_path / "projects.json"
    manifest.write_text(json.dumps([{"repo": "owner/example", "sha": "abc"}]))
    report = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", [
        "check_public_format.py", "--corpus", str(corpus),
        "--manifest", str(manifest), "--report", str(report),
        "--blt", sys.executable,
    ])
    monkeypatch.setattr(
        audit.subprocess, "check_output",
        lambda command, **kwargs: "abc\n" if "rev-parse" in command
        else "\0".join(files).encode() + b"\0",
    )
    visited = []

    def run(command, cwd, timeout):
        targets = command[3:] if "--check" in command else command[2:]
        visited.append(targets)
        return {
            "code": 0, "seconds": 0.1, "child_cpu_seconds": 0.1,
            "stdout": "", "stderr": "",
        }

    monkeypatch.setattr(audit, "run_command", run)
    assert audit.main() == 0
    assert visited == [files, files]
    row, = json.loads(report.read_text())
    assert row["selected_files"] == files
    assert row["files"] == 2
    assert row["problems"] == []
