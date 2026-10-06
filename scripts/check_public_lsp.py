#!/usr/bin/env python3
"""Exercise a real blt LSP process against a checked-out Basilisp project."""

import argparse
import collections
import json
import math
import os
import queue
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

try:
    import resource
except ImportError:  # Windows still records wall time.
    resource = None

BLT = str(Path(__file__).resolve().parent.parent / ".venv" / "bin" / "blt")


class Client:
    def __init__(self, root, output, blt=BLT, extra_args=(),
                 request_timeout=None, diagnostic_timeout=None):
        self.output = output
        self.request_timeout = request_timeout
        self.diagnostic_timeout = diagnostic_timeout
        self.started_at = time.monotonic()
        self.usage_before = child_usage()
        self.process_metrics = {}
        self.stderr = open(str(output) + ".stderr", "w")  # noqa: SIM115 - Owned until stop().
        self.process = subprocess.Popen(
            [str(blt), "lsp", *extra_args],
            cwd=root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.stderr,
            start_new_session=os.name == "posix",
        )
        self.messages = queue.Queue()
        self.notifications = []
        self.next_id = 0
        self.timings = []
        self.transcript = []
        self.received_at = {}
        self.document_sends = {}
        self.timed_diagnostics = set()
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        try:
            while True:
                headers = {}
                while True:
                    line = self.process.stdout.readline()
                    if not line:
                        raise EOFError("server closed stdout")
                    if line == b"\r\n":
                        break
                    key, value = line.decode("ascii").strip().split(":", 1)
                    headers[key.lower()] = value.strip()
                message = json.loads(
                    self.process.stdout.read(int(headers["content-length"]))
                )
                self.messages.put((message, time.monotonic()))
        except (EOFError, OSError, ValueError, IndexError) as error:
            self.messages.put(({"reader_error": repr(error)}, time.monotonic()))

    def send(self, method=None, params=None, **fields):
        message = {"jsonrpc": "2.0", **fields}
        if method is not None:
            message.update(method=method, params=params)
        body = json.dumps(message, ensure_ascii=False).encode()
        if method in {"textDocument/didOpen", "textDocument/didChange"}:
            document = params["textDocument"]
            self.document_sends[(document["uri"], document["version"])] = (
                time.monotonic(),
                method,
            )
        self.process.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
        self.process.stdin.flush()

    def receive(self, deadline):
        message, received_at = self.messages.get(
            timeout=max(0.001, deadline - time.monotonic())
        )
        self.transcript.append(message)
        self.received_at[id(message)] = received_at
        if message.get("method") == "textDocument/publishDiagnostics":
            params = message.get("params") or {}
            key = (params.get("uri"), params.get("version"))
            sent = self.document_sends.get(key)
            if sent is not None and key not in self.timed_diagnostics:
                self.timed_diagnostics.add(key)
                self.timings.append(
                    {
                        "method": "diagnostics",
                        "uri": key[0],
                        "version": key[1],
                        "trigger": sent[1],
                        "seconds": round(received_at - sent[0], 4),
                    }
                )
                print(json.dumps(self.timings[-1]), flush=True)
        if "reader_error" in message:
            raise RuntimeError(message)
        if "method" in message and "id" in message:
            result = None
            if message["method"] == "workspace/configuration":
                result = [None for _ in message["params"]["items"]]
            elif message["method"] == "workspace/applyEdit":
                result = {"applied": False, "failureReason": "audit is read-only"}
            self.send(id=message["id"], result=result)
        return message

    def request(self, method, params, timeout=180):
        # Explicit audit budgets override foreground requests, including the
        # longer references probe. Shutdown keeps its separate cleanup bound.
        if self.request_timeout is not None and method != "shutdown":
            timeout = self.request_timeout
        self.next_id += 1
        request_id = self.next_id
        begin = time.monotonic()
        self.send(method, params, id=request_id)
        while True:
            try:
                message = self.receive(begin + timeout)
            except queue.Empty as error:
                raise TimeoutError(
                    f"{method} did not respond within {timeout}s"
                ) from error
            if message.get("id") == request_id and "method" not in message:
                self.timings.append(
                    {
                        "method": method,
                        "seconds": round(self.received_at[id(message)] - begin, 4),
                    }
                )
                print(
                    json.dumps(
                        {"method": method, "seconds": self.timings[-1]["seconds"]}
                    ),
                    flush=True,
                )
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error'].get('message')}")
                return message.get("result")
            self.notifications.append(message)

    def diagnostics(self, uri, version, timeout=300):
        if self.diagnostic_timeout is not None:
            timeout = self.diagnostic_timeout
        begin = time.monotonic()

        def matched(message):
            params = message.get("params", {}) or {}
            return (
                message.get("method") == "textDocument/publishDiagnostics"
                and params.get("uri") == uri
                and params.get("version") == version
            )

        for index, message in enumerate(self.notifications):
            if matched(message):
                return self.notifications.pop(index)["params"]["diagnostics"]
        while True:
            try:
                message = self.receive(begin + timeout)
            except queue.Empty as error:
                raise TimeoutError(
                    f"diagnostics version {version} did not arrive within {timeout}s"
                ) from error
            if matched(message):
                return message["params"]["diagnostics"]
            self.notifications.append(message)

    def stop(self):
        try:
            if self.process.poll() is None:
                self.request("shutdown", None, timeout=30)
                self.send("exit", None)
                # Finish the stdio session once the exit notification is flushed.
                self.process.stdin.close()
                self.process.wait(timeout=30)
        finally:
            kill_process_tree(self.process)
            self.process_metrics = {
                "lifecycle_seconds": round(time.monotonic() - self.started_at, 4),
                **child_usage_delta(self.usage_before),
            }
            for stream in (self.process.stdin, self.process.stdout):
                try:
                    stream.close()
                except OSError:
                    pass
            self.stderr.close()
            Path(str(self.output) + ".protocol.json").write_text(
                json.dumps(self.transcript, indent=2)
            )



def kill_process_tree(process):
    """Reap the server and stop any descendants left in its owned process group."""
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        elif process.poll() is None:
            process.kill()
    except ProcessLookupError:
        pass
    process.wait()


def rename_contains_declaration(edit, uri, selection_range, new_name):
    if not isinstance(edit, dict):
        return False
    edits = list(edit.get("changes", {}).get(uri, []))
    for change in edit.get("documentChanges", []):
        if change.get("textDocument", {}).get("uri") == uri:
            edits.extend(change.get("edits", []))
    return any(
        item.get("range") == selection_range and item.get("newText") == new_name
        for item in edits
    )


def manifest_scope(root, entry, source_paths=None):
    """Mirror the checker manifest while keeping untracked interop probes visible."""
    root = root.resolve()
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=root, text=True
    ).split("\0")
    sources = sorted(path for path in tracked if Path(path).suffix in {".lpy", ".cljc"})
    selected = sorted(set(entry.get("files") or sources))
    unknown = set(selected) - set(sources)
    if unknown:
        raise ValueError(f"Manifest selects untracked sources: {sorted(unknown)}")
    excluded = sorted(set(sources) - set(selected))
    return {
        "selected_files": selected,
        "selected_file_count": len(selected),
        "selected_bytes": sum((root / path).stat().st_size for path in selected),
        "excluded_files": excluded,
        "source_paths": source_paths or entry.get("source_paths") or ["."],
        "paths_ignore_regex": [re.escape(str((root / path).resolve())) for path in excluded],
    }


def at(uri, source, offset):
    prefix = source[:offset]
    return {
        "textDocument": {"uri": uri},
        "position": {
            "line": prefix.count("\n"),
            "character": len(prefix.rsplit("\n", 1)[-1].encode("utf-16-le")) // 2,
        },
    }


def child_usage():
    if resource is None:
        return None
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime, usage.ru_stime


def child_usage_delta(before):
    after = child_usage()
    if before is None or after is None:
        return {}
    user, system = (new - old for new, old in zip(after, before))
    return {
        "child_user_seconds": round(user, 4),
        "child_system_seconds": round(system, 4),
        "child_cpu_seconds": round(user + system, 4),
        "cpu_scope": "children reaped during this sequential client lifetime",
    }


def cache_snapshot(path):
    sizes = [file.stat().st_size for file in path.rglob("*.json") if file.is_file()]
    return {"files": len(sizes), "bytes": sum(sizes)}



def diagnostic_policy(root, filename, source, settings, linter):
    """Only waive the probe when an applicable global disable is confirmed."""
    import importlib

    import basilisp_tools  # noqa: F401 - Initialize the Basilisp importer.
    from basilisp.lang.keyword import keyword as kw
    from basilisp.lang.runtime import to_lisp

    lsp = importlib.import_module("basilisp_tools.lsp")
    checker = importlib.import_module("basilisp_tools.check")
    resolved = lsp.load_settings(to_lisp({
        "root": str(root), "client-settings": settings,
    }))
    options = {"filename": str(filename), "cwd": str(root)}
    configured = resolved.get(kw("kondo-config-dir"))
    if configured:
        options["config-dir"] = str(root / configured)
    config = checker.load_config(to_lisp(options))
    level = config.get(kw("linters"), {}).get(
        kw(linter), {}
    ).get(kw("level"))
    # Do not infer a global override through namespace groups or source metadata.
    # Such a project needs a targeted investigation, rather than a waived failure.
    scoped = bool(config.get(kw("config-in-ns"))) or "clj-kondo/config" in source
    return {
        "disabled": level == kw("off") and not scoped,
        "resolved_level": str(level).removeprefix(":") if level is not None else None,
        "config_directory": str(config.get(kw("cfg-dir"))),
        "config_directories": [str(value) for value in config.get(kw("config-directories"), [])],
        "scoped_override_requires_review": scoped,
    }


def unresolved_symbol_policy(root, filename, source, settings):
    return diagnostic_policy(root, filename, source, settings, "unresolved-symbol")


def diagnostic_edit_probes(client, root, filename, source, settings, report, verify):
    """Check diagnostic edits and exact UTF-16 ranges under project settings."""
    uri = filename.as_uri()
    suffix = '\n(def blt-audit-emoji "😀")\nblt-audit-missing\n'
    client.send(
        "textDocument/didChange",
        {
            "textDocument": {"uri": uri, "version": 2},
            "contentChanges": [{
                "range": {
                    "start": at(uri, source, len(source))["position"],
                    "end": at(uri, source, len(source))["position"],
                },
                "text": suffix,
            }],
        },
    )
    changed = source + suffix
    changed_diagnostics = client.diagnostics(uri, 2)
    has_missing = any(
        diagnostic.get("code") == "unresolved-symbol"
        and "blt-audit-missing" in diagnostic.get("message", "")
        for diagnostic in changed_diagnostics
    )
    if not has_missing:
        try:
            policy = unresolved_symbol_policy(root, filename, source, settings)
        except Exception as error:  # noqa: BLE001 - Failure to resolve is not a waiver.
            policy = {"disabled": False, "error": repr(error)}
        report["observations"]["unresolved_symbol_policy"] = policy
    else:
        policy = {"disabled": False}
    if policy["disabled"]:
        report["observations"].setdefault("skipped_probes", []).append({
            "name": "edit introduces unresolved symbol",
            "reason": "Resolved project configuration disables :unresolved-symbol globally.",
            "configuration": policy,
        })
        verify("disabled unresolved-symbol setting honored", not has_missing)
    else:
        verify("edit introduces unresolved symbol", has_missing)
    start = changed.index("blt-audit-missing")
    client.send(
        "textDocument/didChange",
        {
            "textDocument": {"uri": uri, "version": 3},
            "contentChanges": [{
                "range": {
                    "start": at(uri, changed, start)["position"],
                    "end": at(uri, changed, start + len("blt-audit-missing"))["position"],
                },
                "text": "blt-audit-emoji",
            }],
        },
    )
    repaired = changed[:start] + "blt-audit-emoji" + changed[start + len("blt-audit-missing"):]
    fixed = client.diagnostics(uri, 3)
    verify(
        "edit removes unresolved symbol",
        not any("blt-audit-missing" in diagnostic.get("message", "") for diagnostic in fixed),
    )

    # The unexpected delimiter follows an astral character on the same line.
    # Checking its exact range exercises UTF-16 conversion, even when a project
    # intentionally disables unresolved-symbol diagnostics.
    invalid_suffix = '\n(do "😀" ])\n'
    client.send(
        "textDocument/didChange",
        {
            "textDocument": {"uri": uri, "version": 4},
            "contentChanges": [{
                "range": {
                    "start": at(uri, repaired, len(repaired))["position"],
                    "end": at(uri, repaired, len(repaired))["position"],
                },
                "text": invalid_suffix,
            }],
        },
    )
    invalid = repaired + invalid_suffix
    error_offset = len(repaired) + invalid_suffix.index("]")
    error_start = at(uri, invalid, error_offset)["position"]
    error_end = at(uri, invalid, error_offset + 1)["position"]
    broken = client.diagnostics(uri, 4)
    syntax_errors = [
        diagnostic for diagnostic in broken
        if diagnostic.get("code") == "syntax"
        and diagnostic.get("range", {}).get("start", {}).get("line") == error_start["line"]
    ]
    syntax_policy = {"disabled": False}
    if not syntax_errors:
        try:
            syntax_policy = diagnostic_policy(root, filename, source, settings, "syntax")
        except Exception as error:  # noqa: BLE001 - Failure to resolve is not a waiver.
            syntax_policy = {"disabled": False, "error": repr(error)}
        report["observations"]["syntax_policy"] = syntax_policy
    if syntax_policy["disabled"]:
        report["observations"].setdefault("skipped_probes", []).append({
            "name": "syntax diagnostic introduction and repair",
            "reason": "Resolved project configuration disables :syntax globally.",
            "configuration": syntax_policy,
        })
        verify("disabled syntax setting honored", not syntax_errors)
        # An enabled unused-value warning spans the quoted astral character.
        # Its end column also distinguishes UTF-16 from code-point offsets.
        emoji_start = len(repaired) + invalid_suffix.index('"😀"')
        emoji_range = {
            "start": at(uri, invalid, emoji_start)["position"],
            "end": at(uri, invalid, emoji_start + len('"😀"'))["position"],
        }
        emoji_warnings = [
            diagnostic for diagnostic in broken
            if diagnostic.get("code") == "unused-value"
            and '"😀"' in diagnostic.get("message", "")
            and diagnostic.get("range", {}).get("start", {}).get("line") == error_start["line"]
        ]
        if emoji_warnings:
            verify(
                "enabled diagnostic range uses UTF-16",
                any(diagnostic.get("range") == emoji_range for diagnostic in emoji_warnings),
            )
        else:
            report["observations"]["skipped_probes"].append({
                "name": "diagnostic range uses UTF-16",
                "reason": "Syntax diagnostics are disabled and no enabled diagnostic spans the emoji probe.",
                "configuration": syntax_policy,
            })
    else:
        verify("edit introduces syntax error", bool(syntax_errors))
        verify(
            "syntax diagnostic range uses UTF-16",
            any(diagnostic.get("range") == {"start": error_start, "end": error_end}
                for diagnostic in syntax_errors),
        )
    client.send(
        "textDocument/didChange",
        {
            "textDocument": {"uri": uri, "version": 5},
            "contentChanges": [{
                "range": {"start": error_start, "end": error_end},
                "text": "nil",
            }],
        },
    )
    syntax_repaired = client.diagnostics(uri, 5)
    verify(
        "disabled syntax setting remains honored after repair" if syntax_policy["disabled"]
        else "edit repairs introduced syntax error",
        not any(
            diagnostic.get("code") == "syntax"
            and diagnostic.get("range", {}).get("start", {}).get("line", -1) >= error_start["line"]
            for diagnostic in syntax_repaired
        ),
    )
    report["observations"]["probe_diagnostics"] = {
        "unresolved": changed_diagnostics,
        "unresolved_repaired": fixed,
        "syntax": broken,
        "syntax_repaired": syntax_repaired,
    }


def server_failure(diagnostic):
    # Checker exceptions can be surfaced as syntax findings. They are tool
    # failures, unlike genuine syntax errors in the checked-out project.
    return diagnostic.get("code") == "configuration" or str(
        diagnostic.get("message", "")
    ).startswith("Analysis failed:")


def check_published_failures(published, report, verify):
    failures = [
        {"uri": item["uri"], "version": item.get("version"), "diagnostic": diagnostic}
        for item in published
        for diagnostic in item["diagnostics"]
        if server_failure(diagnostic)
    ]
    report["observations"]["server_failures"] = failures
    verify("no background server/configuration failures", not failures)


def audit(
    root, filename, output, source_paths=None, python_executable=None, blt=BLT,
    cache_path=None, python_timeout=None, workspace_scope=None,
    request_timeout=None, diagnostic_timeout=None,
):
    print(
        json.dumps({"project": root.name, "file": str(filename), "status": "starting"}),
        flush=True,
    )
    source = filename.read_text()
    uri = filename.as_uri()
    cache_path = Path(cache_path or (str(output) + ".cache")).resolve()
    cache_before = cache_snapshot(cache_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    extra_args = [] if python_timeout is None else ["--python-timeout", str(python_timeout)]
    client = Client(root, output, blt=blt, extra_args=extra_args,
                    request_timeout=request_timeout, diagnostic_timeout=diagnostic_timeout)
    report = {
        "root": str(root),
        "file": str(filename),
        "bytes": len(source.encode()),
        "assertions": [],
        "observations": {},
        "workspace_scope": workspace_scope,
        "timeouts": {
            "request_seconds": request_timeout if request_timeout is not None else 180,
            "references_seconds": request_timeout if request_timeout is not None else 900,
            "diagnostic_seconds": diagnostic_timeout if diagnostic_timeout is not None else 300,
            "shutdown_seconds": 30,
            "python_inspection_seconds": python_timeout,  # None retains the selected tool's default.
        },
        "analysis_cache": {
            "path": str(cache_path),
            "before": cache_before,
            "start_state": "populated" if cache_before["files"] else "empty",
            "compiler_bytecode": "unchanged by harness",
        },
    }

    def verify(label, condition):
        report["assertions"].append({"name": label, "passed": bool(condition)})

    try:
        settings = {
            "text-document-sync-kind": "incremental",
            "cache-path": str(cache_path),
        }
        if source_paths:
            settings["source-paths"] = source_paths
        if python_executable:
            settings["python"] = {"executable": python_executable}
        if workspace_scope and workspace_scope["paths_ignore_regex"]:
            settings["paths-ignore-regex"] = workspace_scope["paths_ignore_regex"]
        report["initialization_settings"] = settings
        initialized = client.request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": root.as_uri(),
                "capabilities": {
                    "general": {"positionEncodings": ["utf-16"]},
                    "workspace": {"workspaceEdit": {"documentChanges": True}},
                },
                "initializationOptions": settings,
            },
        )
        verify(
            "incremental sync advertised",
            initialized["capabilities"]["textDocumentSync"]["change"] == 2,
        )
        client.send("initialized", {})
        client.send(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "basilisp",
                    "version": 1,
                    "text": source,
                }
            },
        )
        original_diagnostics = client.diagnostics(uri, 1)
        report["observations"]["diagnostics"] = original_diagnostics
        report["observations"]["diagnostic_counts"] = dict(
            collections.Counter(d.get("code") for d in original_diagnostics)
        )
        verify(
            "no server/configuration failure",
            not any(server_failure(d) for d in original_diagnostics),
        )
        symbols = client.request(
            "textDocument/documentSymbol", {"textDocument": {"uri": uri}}
        )
        report["observations"]["symbols"] = symbols
        verify("document symbols found", bool(symbols))
        candidate = None
        # Protocol method symbols may be generated/non-renamable; select an
        # editable declaration through the same capability an editor uses.
        for symbol in sorted(symbols, key=lambda item: item.get("kind") != 12):
            probe = {
                "textDocument": {"uri": uri},
                "position": symbol["selectionRange"]["start"],
            }
            try:
                prepared = client.request("textDocument/prepareRename", probe)
            except RuntimeError:
                continue
            if prepared:
                candidate = symbol
                report["observations"]["rename_candidate"] = symbol["name"]
                break
        verify("editable declaration found", candidate is not None)
        if candidate:
            point = {
                "textDocument": {"uri": uri},
                "position": candidate["selectionRange"]["start"],
            }
            hover = client.request("textDocument/hover", point)
            report["observations"]["hover"] = hover
            verify(
                "declared symbol hover", bool(hover) and candidate["name"] in str(hover)
            )
            references = client.request(
                "textDocument/references",
                {**point, "context": {"includeDeclaration": True}},
                timeout=900,
            )
            report["observations"]["references"] = references
            verify(
                "references include declaration",
                any(
                    x["uri"] == uri and x["range"]["start"] == point["position"]
                    for x in references
                ),
            )
            definition = client.request("textDocument/definition", point)
            report["observations"]["definition"] = definition
            verify(
                "definition points to same document",
                isinstance(definition, dict) and definition.get("uri") == uri,
            )
            rename = client.request(
                "textDocument/rename", {**point, "newName": "blt-audit-renamed"}
            )
            report["observations"]["rename"] = rename
            verify(
                "rename contains declaration edit",
                rename_contains_declaration(
                    rename, uri, candidate["selectionRange"], "blt-audit-renamed"
                ),
            )
        formatted = client.request(
            "textDocument/formatting",
            {
                "textDocument": {"uri": uri},
                "options": {"tabSize": 2, "insertSpaces": True},
            },
        )
        report["observations"]["format_edits"] = formatted
        diagnostic_edit_probes(
            client, root, filename, source, settings, report, verify,
        )
        # Supplement real source with an in-memory stdlib interop buffer.
        python_uri = (filename.parent / "__blt_audit_interop.lpy").as_uri()
        python_source = '(ns blt-audit-interop (:import [pathlib]))\n(def p (pathlib/Path "."))\n(.read_text p)\n(.read_ p)\n'
        client.send(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": python_uri,
                    "languageId": "basilisp",
                    "version": 1,
                    "text": python_source,
                }
            },
        )
        report["observations"]["python_diagnostics"] = client.diagnostics(python_uri, 1)
        completion = client.request(
            "textDocument/completion",
            at(
                python_uri,
                python_source,
                python_source.rindex(".read_") + len(".read_"),
            ),
        )
        labels = [x["label"] for x in completion["items"]]
        report["observations"]["python_completions"] = labels
        verify(
            "Python instance completion",
            ".read_text" in labels and ".read_bytes" in labels,
        )
        python_hover = client.request(
            "textDocument/hover",
            at(python_uri, python_source, python_source.index(".read_text") + 3),
        )
        report["observations"]["python_hover"] = python_hover
        verify(
            "Python method hover",
            bool(python_hover) and "read_text" in str(python_hover),
        )
    except KeyboardInterrupt:
        report["error"] = "Interrupted before the audit completed"
        raise
    except Exception as error:  # noqa: BLE001 - Preserve failures and audit the next project.
        report["error"] = repr(error)
    finally:
        try:
            client.stop()
            verify("clean shutdown", client.process.returncode == 0)
        except Exception as error:  # noqa: BLE001 - Persist shutdown failure evidence.
            report["shutdown_error"] = repr(error)
        report["timings"] = client.timings
        report["process_metrics"] = client.process_metrics
        report["analysis_cache"]["after"] = cache_snapshot(cache_path)
        published = [
            message["params"]
            for message in client.transcript
            if message.get("method") == "textDocument/publishDiagnostics"
        ]
        report["observations"]["published_versions"] = [
            {
                "uri": item["uri"],
                "version": item.get("version"),
                "counts": dict(
                    collections.Counter(d.get("code") for d in item["diagnostics"])
                ),
            }
            for item in published
        ]
        check_published_failures(published, report, verify)
        output.write_text(json.dumps(report, indent=2))
        print(
            json.dumps(
                {
                    "output": str(output),
                    "file": str(filename),
                    "assertions": report["assertions"],
                    "timings": client.timings,
                    "diagnostic_counts": report["observations"].get(
                        "diagnostic_counts"
                    ),
                    "error": report.get("error"),
                    "shutdown_error": report.get("shutdown_error"),
                }
            ),
            flush=True,
        )
    return report


PUBLIC_FILES = {
    "basilisp-lang/basilisp": "src/basilisp/contrib/bencode.lpy",
    "ikappaki/basilisp-pprint": "src/basilisp_pprint/pprint.lpy",
    "ikappaki/basilisp-nrepl-async": "src/basilisp_nrepl_async/utils.lpy",
    "ikappaki/basilisp-kernel": "basilisp_kernel/nrepl_server.lpy",
    "ikappaki/basilisp-blender": "src/basilisp_blender/utils.lpy",
    "vefjun/basilisp-flask": "src/basilisp_flask/demo.lpy",
    "dpom/aerob": "src/aero/core.lpy",
    "EnigmaCurry/calc": "src/calc/dice.cljc",
    "vandyand/balli": "src/balli/describe.lpy",
    "dpom/steno": "src/steno/utils.lpy",
}


def successful(report):
    return (
        not report.get("error")
        and not report.get("shutdown_error")
        and all(item["passed"] for item in report["assertions"])
    )


def repeated_audit(
    root, filename, output, source_paths, python_executable, blt,
    repeat=1, isolated_cache=False, python_timeout=None, workspace_scope=None,
    request_timeout=None, diagnostic_timeout=None,
):
    output.parent.mkdir(parents=True, exist_ok=True)
    cache_path = None
    if isolated_cache or repeat > 1:
        # A fresh directory prevents a previous audit from warming run one.
        # Retain it alongside reports for inspection, never delete user caches.
        cache_path = Path(
            tempfile.mkdtemp(prefix=output.stem + ".cache-", dir=output.parent)
        )
    reports = []
    for index in range(repeat):
        destination = (
            output if repeat == 1 else
            output.with_name(f"{output.stem}.run-{index + 1}{output.suffix}")
        )
        report = audit(
            root, filename, destination, source_paths, python_executable,
            blt, cache_path=cache_path, python_timeout=python_timeout,
            workspace_scope=workspace_scope,
            request_timeout=request_timeout, diagnostic_timeout=diagnostic_timeout,
        )
        report["process_run"] = index + 1
        report["process_runs"] = repeat
        destination.write_text(json.dumps(report, indent=2))
        reports.append(report)
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, nargs="?")
    parser.add_argument("file", type=Path, nargs="?")
    parser.add_argument("report", type=Path, nargs="?")
    parser.add_argument(
        "--manifest",
        type=Path,
        help="JSON repository manifest (defaults to public_projects.json with --corpus)",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        help="Directory containing checkout folders named after each repository",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Directory for per-project JSON, wire transcript and stderr",
    )
    parser.add_argument(
        "--project",
        action="append",
        help="Limit to repository names or their final path components",
    )
    parser.add_argument("--blt", default=BLT)
    parser.add_argument("--source-path", action="append")
    parser.add_argument("--python-executable")
    parser.add_argument("--python-timeout", type=float, help="Forward inspection timeout to blt lsp")
    parser.add_argument(
        "--request-timeout", type=float,
        help="Override foreground request deadlines (defaults: 180s, references 900s; shutdown stays 30s)",
    )
    parser.add_argument(
        "--diagnostic-timeout", type=float,
        help="Override each diagnostics deadline (default: 300s)",
    )
    parser.add_argument(
        "--repeat", type=int, default=1,
        help="Total fresh LSP processes; repeats share a new isolated analysis cache",
    )
    parser.add_argument(
        "--isolated-cache", action="store_true",
        help="Use a new analysis cache even for a single process; compiler bytecode is unchanged",
    )
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    if args.python_timeout is not None and args.python_timeout <= 0:
        parser.error("--python-timeout must be positive")
    for name in ("request_timeout", "diagnostic_timeout"):
        value = getattr(args, name)
        if value is not None and (not math.isfinite(value) or value <= 0):
            parser.error("--" + name.replace("_", "-") + " must be positive and finite")
    if args.corpus and not args.manifest:
        args.manifest = Path(__file__).with_name("public_projects.json")
    if not args.manifest:
        if not (args.root and args.file and args.report):
            parser.error("provide root/file/report or --manifest and --output")
        reports = repeated_audit(
            args.root.resolve(),
            args.file.resolve(),
            args.report,
            args.source_path,
            args.python_executable,
            args.blt,
            args.repeat,
            args.isolated_cache,
            args.python_timeout,
            request_timeout=args.request_timeout, diagnostic_timeout=args.diagnostic_timeout,
        )
        if args.repeat > 1:
            args.report.write_text(json.dumps(reports, indent=2))
        return 0 if all(successful(report) for report in reports) else 1
    if not args.output:
        parser.error("--manifest requires --output")
    if not args.corpus and any(
        not entry.get("path") for entry in json.loads(args.manifest.read_text())
    ):
        parser.error("manifest entries without path require --corpus")
    args.output.mkdir(parents=True, exist_ok=True)
    reports = []
    for entry in json.loads(args.manifest.read_text()):
        name = entry["repo"]
        if (
            args.project
            and name not in args.project
            and name.split("/")[-1] not in args.project
        ):
            continue
        root = (
            Path(entry["path"])
            if entry.get("path")
            else args.corpus / name.split("/")[-1]
        ).resolve()
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip()
        if entry.get("sha") and revision != entry["sha"]:
            parser.error(f"{name}: checkout differs from the pinned manifest")
        selected = entry.get("lsp_file") or PUBLIC_FILES.get(name)
        if not selected:
            selected = next(iter(entry.get("files", [])), None)
        if not selected:
            parser.error(f"{name}: manifest requires lsp_file")
        filename = root / selected
        scope = manifest_scope(root, entry, args.source_path)
        if selected not in scope["selected_files"]:
            parser.error(f"{name}: lsp_file is outside the selected manifest scope")
        project_reports = repeated_audit(
            root,
            filename,
            args.output / (name.replace("/", "__") + ".json"),
            scope["source_paths"],
            args.python_executable,
            args.blt,
            args.repeat,
            args.isolated_cache,
            args.python_timeout,
            scope,
            request_timeout=args.request_timeout, diagnostic_timeout=args.diagnostic_timeout,
        )
        for report in project_reports:
            report["repo"] = name
            report["sha"] = revision
        reports.extend(project_reports)
    if not reports:
        parser.error("no projects matched")
    (args.output / "summary.json").write_text(json.dumps(reports, indent=2))
    return 0 if all(successful(report) for report in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
