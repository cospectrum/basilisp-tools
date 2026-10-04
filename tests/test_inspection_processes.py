"""Inspection workers must finish with their owning language server."""
import concurrent.futures
import os
import importlib.util
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

worker_path = Path(__file__).resolve().parents[1] / "src" / "basilisp_tools" / "_inspect.py"
worker_spec = importlib.util.spec_from_file_location("_blt_inspection_test", worker_path)
worker_module = importlib.util.module_from_spec(worker_spec)
worker_spec.loader.exec_module(worker_module)
InspectionProcesses = worker_module.InspectionProcesses


def test_run_preserves_output_and_process_errors():
    workers = InspectionProcesses()
    command = [sys.executable, "-c", "import sys; print(sys.stdin.read()); print('error', file=sys.stderr)"]
    result = workers.run(command, input="value", text=True, capture_output=True, check=True)
    assert result.stdout == "value\n"
    assert result.stderr == "error\n"
    with pytest.raises(subprocess.CalledProcessError) as failure:
        workers.run([sys.executable, "-c", "print('failed'); raise SystemExit(3)"],
                    text=True, capture_output=True, check=True)
    assert failure.value.returncode == 3
    assert failure.value.stdout == "failed\n"
    workers.stop()
    workers.stop()
    with pytest.raises(OSError, match="stopped"):
        workers.run(command)


def test_timeout_preserves_partial_output_and_reaps_child(tmp_path):
    workers = InspectionProcesses()
    marker = tmp_path / "pid"
    command = [sys.executable, "-c",
               "import os, pathlib, sys, time; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
               "print('partial', flush=True); time.sleep(30)", str(marker)]
    try:
        with pytest.raises(subprocess.TimeoutExpired) as timeout:
            workers.run(command, text=True, capture_output=True, timeout=1)
        assert timeout.value.stdout == b"partial\n"
        if os.name == "posix":
            with pytest.raises(ProcessLookupError):
                os.kill(int(marker.read_text()), 0)
    finally:
        workers.stop()


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal lifecycle")
def test_stop_kills_and_reaps_running_inspection(tmp_path):
    workers = InspectionProcesses()
    marker = tmp_path / "pid"
    command = [sys.executable, "-c",
               "import os, pathlib, signal, sys, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
               "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)", str(marker)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(workers.run, command, capture_output=True, timeout=30)
        try:
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert marker.exists(), "inspection child failed to start"
            started = time.monotonic()
            workers.stop()
            assert time.monotonic() - started < 2
            assert future.result(timeout=2).returncode == -signal.SIGKILL
            with pytest.raises(ProcessLookupError):
                os.kill(int(marker.read_text()), 0)
            with pytest.raises(OSError, match="stopped"):
                workers.run(command)
        finally:
            workers.stop()
