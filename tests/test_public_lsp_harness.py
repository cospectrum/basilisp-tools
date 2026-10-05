"""Keep audit latency measurements independent of when the harness consumes them."""

import importlib.util
import io
import queue
from pathlib import Path
from types import SimpleNamespace

harness_path = Path(__file__).resolve().parents[1] / "scripts" / "check_public_lsp.py"
spec = importlib.util.spec_from_file_location("_blt_lsp_audit", harness_path)
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


def client_without_process():
    client = harness.Client.__new__(harness.Client)
    client.process = SimpleNamespace(stdin=io.BytesIO())
    client.messages = queue.Queue()
    client.notifications = []
    client.transcript = []
    client.timings = []
    client.received_at = {}
    client.document_sends = {}
    client.timed_diagnostics = set()
    client.next_id = 0
    return client


def test_queued_diagnostics_measure_send_to_arrival_once(monkeypatch):
    client = client_without_process()
    monkeypatch.setattr(harness.time, "monotonic", lambda: 10.0)
    uri = "file:///sample.lpy"
    client.send("textDocument/didChange", {
        "textDocument": {"uri": uri, "version": 2}, "contentChanges": [],
    })
    message = {
        "method": "textDocument/publishDiagnostics",
        "params": {"uri": uri, "version": 2, "diagnostics": []},
    }
    client.messages.put((message, 10.25))
    client.notifications.append(client.receive(11))
    # The caller may ask for diagnostics much later, after another request.
    monkeypatch.setattr(harness.time, "monotonic", lambda: 20.0)
    assert client.diagnostics(uri, 2) == []
    assert client.timings == [{
        "method": "diagnostics", "uri": uri, "version": 2,
        "trigger": "textDocument/didChange", "seconds": 0.25,
    }]
    # Local and whole-project findings can publish the same version twice.
    client.messages.put((dict(message), 20.5))
    client.receive(21)
    assert len(client.timings) == 1


def test_request_uses_message_arrival_not_queue_consumption(monkeypatch):
    client = client_without_process()
    clock = iter([10.0, 12.0])
    monkeypatch.setattr(harness.time, "monotonic", lambda: next(clock))
    client.messages.put(({"id": 1, "result": {"value": 7}}, 10.125))
    assert client.request("example", {}) == {"value": 7}
    assert client.timings == [{"method": "example", "seconds": 0.125}]


def test_repeated_processes_share_only_their_new_cache(tmp_path, monkeypatch):
    observed = []

    def audit(root, filename, output, source_paths, python_executable, blt, cache_path):
        before = harness.cache_snapshot(cache_path)
        observed.append((cache_path, before))
        (cache_path / "entry.json").write_text("{}")
        return {"assertions": [{"passed": True}]}

    monkeypatch.setattr(harness, "audit", audit)
    args = (tmp_path, tmp_path / "main.lpy", tmp_path / "report.json", None, None, "blt")
    reports = harness.repeated_audit(*args, repeat=2)
    assert [report["process_run"] for report in reports] == [1, 2]
    assert observed[0][0] == observed[1][0]
    assert observed[0][1] == {"files": 0, "bytes": 0}
    assert observed[1][1] == {"files": 1, "bytes": 2}
    assert (tmp_path / "report.run-1.json").is_file()
    assert (tmp_path / "report.run-2.json").is_file()
    harness.repeated_audit(*args, repeat=1, isolated_cache=True)
    assert observed[2][0] != observed[0][0]
    assert observed[2][1] == {"files": 0, "bytes": 0}
