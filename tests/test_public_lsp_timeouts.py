"""The audit's foreground deadline overrides must never extend shutdown."""

import importlib.util
import queue
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "public_lsp_audit",
    Path(__file__).resolve().parents[1] / "scripts" / "check_public_lsp.py",
)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def client(monkeypatch, request_timeout=None, diagnostic_timeout=None):
    instance = audit.Client.__new__(audit.Client)
    instance.request_timeout = request_timeout
    instance.diagnostic_timeout = diagnostic_timeout
    instance.next_id = 0
    instance.notifications = []
    instance.send = lambda *args, **kwargs: None
    deadlines = []

    def receive(deadline):
        deadlines.append(deadline)
        raise queue.Empty

    instance.receive = receive
    monkeypatch.setattr(audit.time, "monotonic", lambda: 100)
    return instance, deadlines


@pytest.mark.parametrize(
    "override,method,explicit,expected",
    [
        (None, "textDocument/hover", None, 180),
        (None, "textDocument/references", 900, 900),
        (1800, "textDocument/hover", None, 1800),
        (1800, "textDocument/references", 900, 1800),
        (1800, "shutdown", 30, 30),
    ],
)
def test_request_deadlines_preserve_defaults_and_shutdown(
    monkeypatch, override, method, explicit, expected
):
    instance, deadlines = client(monkeypatch, request_timeout=override)
    arguments = {} if explicit is None else {"timeout": explicit}
    with pytest.raises(TimeoutError, match=f"within {expected}s"):
        instance.request(method, {}, **arguments)
    assert deadlines == [100 + expected]


@pytest.mark.parametrize("override,expected", [(None, 300), (1800, 1800)])
def test_diagnostics_deadline(monkeypatch, override, expected):
    instance, deadlines = client(monkeypatch, diagnostic_timeout=override)
    with pytest.raises(TimeoutError, match=f"within {expected}s"):
        instance.diagnostics("file:///sample.lpy", 2)
    assert deadlines == [100 + expected]


def test_repeat_propagates_selected_deadlines(monkeypatch, tmp_path):
    calls = []

    def fake_audit(*args, **kwargs):
        calls.append(kwargs)
        return {"assertions": []}

    monkeypatch.setattr(audit, "audit", fake_audit)
    audit.repeated_audit(
        tmp_path, tmp_path / "sample.lpy", tmp_path / "report.json",
        None, None, "blt", repeat=2,
        request_timeout=1800, diagnostic_timeout=1200,
    )
    assert len(calls) == 2
    assert all(call["request_timeout"] == 1800 for call in calls)
    assert all(call["diagnostic_timeout"] == 1200 for call in calls)


@pytest.mark.parametrize(
    "option,value",
    [
        ("--request-timeout", "0"),
        ("--request-timeout", "nan"),
        ("--diagnostic-timeout", "-1"),
        ("--diagnostic-timeout", "inf"),
    ],
)
def test_invalid_deadline_fails_before_starting_a_server(monkeypatch, option, value):
    monkeypatch.setattr(audit.sys, "argv", ["check_public_lsp.py", option, value])
    with pytest.raises(SystemExit) as error:
        audit.main()
    assert error.value.code == 2
