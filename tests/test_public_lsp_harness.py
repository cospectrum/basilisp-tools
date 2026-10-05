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
    client.request_timeout = None
    client.diagnostic_timeout = None
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

    def audit(root, filename, output, source_paths, python_executable, blt, cache_path,
              python_timeout=None, workspace_scope=None,
              request_timeout=None, diagnostic_timeout=None):
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


def test_manifest_scope_excludes_only_unselected_tracked_sources(tmp_path, monkeypatch):
    paths = ["src/main.lpy", "ui/example[1].cljc", "templates/test.cljc", "README.md"]
    for path in paths:
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("(ns example)")
    monkeypatch.setattr(harness.subprocess, "check_output", lambda *a, **kw: "\0".join(paths))
    scope = harness.manifest_scope(tmp_path, {"files": ["src/main.lpy"]})
    assert scope["selected_file_count"] == 1
    assert scope["selected_files"] == ["src/main.lpy"]
    assert scope["source_paths"] == ["."]
    patterns = scope["paths_ignore_regex"]
    ignored = lambda path: any(harness.re.fullmatch(pattern, str((tmp_path / path).resolve())) for pattern in patterns)
    assert ignored("ui/example[1].cljc")
    assert ignored("templates/test.cljc")
    assert not ignored("src/main.lpy")
    assert not ignored("ui/example1.cljc")  # Regex metacharacters are literal.
    assert not ignored("src/__blt_audit_interop.lpy")
    assert harness.manifest_scope(tmp_path, {"source_paths": ["src"]})["source_paths"] == ["src"]


def test_rename_requires_exact_declaration_edit():
    uri = "file:///project/main.lpy"
    selection = {"start": {"line": 1, "character": 6}, "end": {"line": 1, "character": 9}}
    edit = {"range": selection, "newText": "renamed"}
    assert harness.rename_contains_declaration({"changes": {uri: [edit]}}, uri, selection, "renamed")
    assert harness.rename_contains_declaration({"documentChanges": [{"textDocument": {"uri": uri}, "edits": [edit]}]}, uri, selection, "renamed")
    assert not harness.rename_contains_declaration({"changes": {"file:///other.lpy": [edit]}}, uri, selection, "renamed")
    assert not harness.rename_contains_declaration({"changes": {uri: [edit]}}, uri, selection, "wrong")
    assert not harness.rename_contains_declaration({"changes": {uri: [edit]}}, uri, {"start": {}, "end": {}}, "renamed")


def test_cleanup_kills_owned_group_even_if_server_has_exited(monkeypatch):
    if harness.os.name != "posix":
        return
    calls = []
    process = SimpleNamespace(pid=42, poll=lambda: 0, wait=lambda: calls.append("wait"))
    monkeypatch.setattr(harness.os, "killpg", lambda pid, sig: calls.append((pid, sig)))
    harness.kill_process_tree(process)
    assert calls == [(42, harness.signal.SIGKILL), "wait"]


def test_cleanup_reaps_an_already_gone_group(monkeypatch):
    if harness.os.name != "posix":
        return
    calls = []
    def missing(pid, sig):
        raise ProcessLookupError
    monkeypatch.setattr(harness.os, "killpg", missing)
    harness.kill_process_tree(SimpleNamespace(pid=42, wait=lambda: calls.append("wait")))
    assert calls == ["wait"]


def diagnostic_probe_client(unresolved=True, syntax_character=9):
    messages = []
    missing = [{
        "code": "unresolved-symbol", "message": "Unresolved symbol: blt-audit-missing",
    }] if unresolved else []
    syntax = [{
        "code": "syntax",
        "message": "Unexpected closing delimiter",
        "range": {
            "start": {"line": 5, "character": syntax_character},
            "end": {"line": 5, "character": syntax_character + 1},
        },
    }]
    diagnostics = {2: missing, 3: [], 4: syntax, 5: []}
    return SimpleNamespace(
        send=lambda method, params: messages.append((method, params)),
        diagnostics=lambda uri, version: diagnostics[version],
        sent=messages,
    )


def run_diagnostic_probes(client, tmp_path):
    report = {"observations": {}}
    assertions = {}

    def verify(name, condition):
        assertions[name] = bool(condition)

    harness.diagnostic_edit_probes(
        client, tmp_path, tmp_path / "demo.lpy", "(ns demo)\n", {}, report, verify,
    )
    return report, assertions


def test_diagnostic_probes_require_semantic_and_utf16_syntax_errors(tmp_path):
    client = diagnostic_probe_client()
    report, assertions = run_diagnostic_probes(client, tmp_path)
    assert all(assertions.values())
    assert "skipped_probes" not in report["observations"]
    versions = [params["textDocument"]["version"] for _, params in client.sent]
    assert versions == [2, 3, 4, 5]
    repair = client.sent[-1][1]["contentChanges"][0]
    assert repair == {
        "range": {
            "start": {"line": 5, "character": 9},
            "end": {"line": 5, "character": 10},
        },
        "text": "nil",
    }
    _, wrong_range = run_diagnostic_probes(
        diagnostic_probe_client(syntax_character=8), tmp_path,
    )
    assert not wrong_range["syntax diagnostic range uses UTF-16"]


def test_disabled_semantic_probe_still_requires_syntax_roundtrip(tmp_path, monkeypatch):
    evidence = {
        "disabled": True, "resolved_level": "off",
        "config_directory": str(tmp_path / ".clj-kondo"),
    }
    monkeypatch.setattr(harness, "unresolved_symbol_policy", lambda *args: evidence)
    report, assertions = run_diagnostic_probes(
        diagnostic_probe_client(unresolved=False), tmp_path,
    )
    assert all(assertions.values())
    assert assertions["edit introduces syntax error"]
    assert assertions["edit repairs introduced syntax error"]
    assert report["observations"]["skipped_probes"][0]["configuration"] == evidence
    assert "edit introduces unresolved symbol" not in assertions
    assert assertions["disabled unresolved-symbol setting honored"]


def test_missing_diagnostic_is_not_waived_without_confirmed_disable(tmp_path, monkeypatch):
    monkeypatch.setattr(
        harness, "unresolved_symbol_policy",
        lambda *args: {"disabled": False, "scoped_override_requires_review": True},
    )
    report, assertions = run_diagnostic_probes(
        diagnostic_probe_client(unresolved=False), tmp_path,
    )
    assert not assertions["edit introduces unresolved symbol"]
    assert "skipped_probes" not in report["observations"]

    def configuration_error(*args):
        raise ValueError("bad config")

    monkeypatch.setattr(harness, "unresolved_symbol_policy", configuration_error)
    report, assertions = run_diagnostic_probes(
        diagnostic_probe_client(unresolved=False), tmp_path,
    )
    assert not assertions["edit introduces unresolved symbol"]
    assert "bad config" in report["observations"]["unresolved_symbol_policy"]["error"]
