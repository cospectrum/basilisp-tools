"""Smoke-test an installed LSP command, e.g. smoke_lsp.py uv tool run ... blt lsp."""

import json
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


def read_frames(stream, messages):
    try:
        while True:
            line = stream.readline(8192)
            if not line:
                break
            headers = {}
            while line != b"\r\n":
                if not line.endswith(b"\r\n"):
                    raise RuntimeError("LSP stdout contains an invalid header")
                key, value = line.decode("ascii").strip().split(":", 1)
                key = key.lower()
                if key in headers:
                    raise RuntimeError("LSP stdout contains a duplicate header")
                headers[key] = value.strip()
                line = stream.readline(8192)
            length = int(headers["content-length"])
            if not 0 < length <= 8 * 1024 * 1024:
                raise RuntimeError("Invalid LSP Content-Length")
            body = stream.read(length)
            if len(body) != length:
                raise RuntimeError("Truncated LSP body")
            message = json.loads(body)
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                raise RuntimeError("Invalid JSON-RPC message")
            messages.put(message)
    except Exception as error:
        messages.put(error)
    finally:
        messages.put(None)


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Usage: smoke_lsp.py COMMAND [ARGS...]")
    with tempfile.TemporaryDirectory(prefix="blt-lsp-smoke-") as root, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            sys.argv[1:], cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=errors, start_new_session=os.name == "posix",
        )
        messages = queue.Queue()
        reader = threading.Thread(target=read_frames, args=(process.stdout, messages), daemon=True)
        reader.start()

        def send(method, params, request_id=None):
            message = {"jsonrpc": "2.0", "method": method, "params": params}
            if request_id is not None:
                message["id"] = request_id
            body = json.dumps(message).encode("utf-8")
            process.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
            process.stdin.flush()

        def response(request_id):
            deadline = time.monotonic() + 60
            while True:
                message = messages.get(timeout=max(0, deadline - time.monotonic()))
                if isinstance(message, Exception):
                    raise message
                if message is None:
                    raise RuntimeError("LSP exited before responding")
                if message.get("id") == request_id:
                    if "error" in message:
                        raise RuntimeError(str(message["error"]))
                    return message["result"]

        try:
            send("initialize", {"processId": None, "rootUri": Path(root).as_uri(),
                               "capabilities": {"general": {"positionEncodings": ["utf-16"]}}}, 1)
            capabilities = response(1)["capabilities"]
            assert capabilities["positionEncoding"] == "utf-16", capabilities
            assert capabilities["textDocumentSync"]["change"] == 1, capabilities
            for name in ("hoverProvider", "definitionProvider", "referencesProvider",
                         "renameProvider", "completionProvider", "documentSymbolProvider",
                         "workspaceSymbolProvider", "documentFormattingProvider"):
                assert capabilities.get(name) is not None and capabilities[name] is not False, name
            send("initialized", {})
            send("shutdown", None, 2)
            assert response(2) is None, "shutdown must return null"
            send("exit", None)
            assert process.wait(timeout=10) == 0, "LSP exited unsuccessfully"
            reader.join(timeout=5)
            assert not reader.is_alive(), "LSP stdout remained open after exit"
            while not messages.empty():
                message = messages.get_nowait()
                if isinstance(message, Exception):
                    raise message
        except BaseException:
            errors.seek(0)
            sys.stderr.write(errors.read(16384).decode("utf-8", errors="replace"))
            raise
        finally:
            if process.poll() is None:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()
    print("Installed LSP initializes, advertises capabilities, and exits cleanly.")


if __name__ == "__main__":
    main()
