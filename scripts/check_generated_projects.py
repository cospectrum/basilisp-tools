"""Generate runnable projects and test formatting, checking, and the LSP.

Every valid project is executed before and after formatting and an LSP rename.
Mutations have explicit expected diagnostics; repairs must remove them.
Run: uv run python scripts/check_generated_projects.py --output /tmp/blt-generated
"""

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from check_public_check import run_command
from check_public_lsp import Client, at


@dataclass
class Case:
    name: str
    files: dict[str, str]
    expression: str
    bad_call: str
    diagnostic: str
    dependencies: tuple[str, ...] = ()
    python_probes: tuple[tuple[str, str], ...] = ()
    mutation_file: str = "demo/main.lpy"
    native_source_optional: tuple[str, ...] = ()


def cases(seed, workspace_files=24):
    rng = random.Random(seed)
    value = rng.randint(10, 90)
    delta = 100 - value
    main = "(ns demo.main%s)\n(defn run []\n%s)\n"
    yield Case(
        "namespaces",
        {
            "demo/math.lpy": "(ns demo.math)\n(defn add [x y] (+ x y))\n",
            "demo/main.lpy": main
            % (" (:require [demo.math :as m])", f"(m/add {value} {delta})"),
        },
        "100",
        "(m/add 1)",
        "invalid-arity",
    )
    yield Case(
        "python",
        {
            "support.py": "from dataclasses import dataclass\n@dataclass\nclass Point:\n    x: int\n    y: int\ndef offset(value: int, *, by: int) -> int:\n    return value + by\n",
            "demo/main.lpy": main
            % (
                " (:import [support :as py])",
                f"(py/offset (.-x (py/Point {value} 3)) ** :by {delta})",
            ),
        },
        "100",
        '(py/offset "bad" ** :by 1)',
        "type-mismatch",
    )
    yield Case(
        "reader-conditional",
        {
            "demo/shared.cljc": f"(ns demo.shared)\n#?(:clj (defn value [] (JavaOnly/missing))\n:lpy (defn value [] {value}))\n",
            "demo/main.lpy": main
            % (" (:require [demo.shared :as shared])", f"(+ (shared/value) {delta})"),
        },
        "100",
        "(shared/value 1)",
        "invalid-arity",
    )
    yield Case(
        "unicode",
        {
            "demo/main.lpy": main
            % (
                "",
                f'(let [значение {value} label "😀λ"]\n(+ значение {delta} (- (count label) 2)))',
            ),
        },
        "100",
        '(do "😀" generated-missing)',
        "unresolved-symbol",
    )
    yield Case(
        "macro",
        {
            "demo/macros.lpy": "(ns demo.macros)\n(defmacro unless [condition & body] `(if ~condition nil (do ~@body)))\n",
            "demo/main.lpy": main
            % (
                " (:require [demo.macros :refer [unless]])",
                f"(unless false (+ {value} {delta}))",
            ),
        },
        "100",
        "(run 1)",
        "invalid-arity",
    )
    yield Case(
        "stubs",
        {
            "support.py": "def normalize(value):\n    return value.strip().lower()\n",
            "support.pyi": "def normalize(value: str) -> str: ...\n",
            "demo/main.lpy": main
            % (
                " (:import [support :as py])",
                '(+ 92 (count (py/normalize "  FortyTwo  ")))',
            ),
        },
        "100",
        "(py/normalize 123)",
        "type-mismatch",
    )
    yield Case(
        "context-manager",
        {
            "demo/main.lpy": main
            % (
                " (:import [contextlib])",
                "(with [value (contextlib/nullcontext 100)] value)",
            ),
        },
        "100",
        "(run 1)",
        "invalid-arity",
    )
    yield Case(
        "lint-as",
        {
            "demo/macros.lpy": "(ns demo.macros)\n(defmacro defhandler [name args & body] `(defn ~name ~args ~@body))\n",
            "demo/main.lpy": "(ns demo.main (:require [demo.macros :refer [defhandler]]))\n(defhandler run [] 100)\n",
        },
        "100",
        "(run 1)",
        "invalid-arity",
    )

    modules = {
        f"demo/part{i}.lpy": f"(ns demo.part{i})\n(defn value [] {i})\n"
        for i in range(workspace_files)
    }
    requires = " ".join(f"[demo.part{i} :as p{i}]" for i in range(workspace_files))
    calls = " ".join(f"(p{i}/value)" for i in range(workspace_files))
    modules["demo/main.lpy"] = main % (f" (:require {requires})", f"(+ {calls})")
    yield Case(
        "workspace",
        modules,
        str(sum(range(workspace_files))),
        "(p0/value 1)",
        "invalid-arity",
    )


def expanded_cases(seed, small_files=1, large_files=100):
    """Vary source size and Python surface independently, with no network I/O."""
    families = {
        "pure": (
            (),
            [
                ("", "(reduce + (map inc [1 2 3]))", 9, None),
                ("", "(let [{:keys [a b]} {:a 7 :b 8}] (+ a b))", 15, None),
                (
                    "",
                    "(loop [items [2 4 6] total 0] (if (seq items) (recur (next items) (+ total (first items))) total))",
                    12,
                    None,
                ),
                ("", "(count (filter odd? (range 10)))", 5, None),
            ],
        ),
        "stdlib": (
            (),
            [
                (
                    "[statistics :as stats]",
                    "(int (stats/mean #py [2 4 6]))",
                    4,
                    "stats/mean",
                ),
                (
                    "[json]",
                    r'(get (json/loads "{\"value\": 13}") "value")',
                    13,
                    "json/loads",
                ),
                (
                    "[pathlib :as paths]",
                    '(count (.-suffix (paths/PurePosixPath "data/table.csv")))',
                    4,
                    "paths/PurePosixPath",
                ),
                (
                    "[urllib.parse :as urls]",
                    '(count (.-path (urls/urlsplit "https://example.invalid/items?q=1")))',
                    6,
                    "urls/urlsplit",
                ),
                (
                    "[datetime :as dates]",
                    "(.-day (dates/date 2024 1 9))",
                    9,
                    "dates/date",
                ),
                (
                    "[contextlib]",
                    "(with [value (contextlib/nullcontext 17)] value)",
                    17,
                    "contextlib/nullcontext",
                ),
            ],
        ),
        "data": (
            ("numpy", "pandas", "scipy", "pydantic"),
            [
                (
                    "[numpy :as np]",
                    "(int (.sum (np/array #py [2 3 5])))",
                    10,
                    "np/array",
                ),
                (
                    "[pandas :as pd]",
                    "(int (.sum (pd/Series #py [4 5 6])))",
                    15,
                    "pd/Series",
                ),
                (
                    "[scipy.special :as special]",
                    "(int (special/comb 5 2 ** :exact true))",
                    10,
                    "special/comb",
                ),
                (
                    "[pydantic.type_adapter :as pyd]",
                    '(.validate_python (pyd/TypeAdapter python/int) "7")',
                    7,
                    "pyd/TypeAdapter",
                ),
            ],
        ),
        "services": (
            ("requests", "httpx", "sqlalchemy", "python-dateutil", "rich", "pillow"),
            [
                (
                    "[requests]",
                    '(count (.-method (.prepare (requests/Request "GET" "https://example.invalid/items"))))',
                    3,
                    "requests/Request",
                ),
                (
                    "[httpx]",
                    '(count (.-path (httpx/URL "https://example.invalid/items?q=1")))',
                    6,
                    "httpx/URL",
                ),
                (
                    "[sqlalchemy :as sa]",
                    '(let [engine (sa/create_engine "sqlite+pysqlite:///:memory:")] (try (with [connection (.connect engine)] (.scalar (.execute connection (sa/text "SELECT 11")))) (finally (.dispose engine))))',
                    11,
                    "sa/create_engine",
                ),
                (
                    "[dateutil.parser :as dates]",
                    '(.-day (dates/isoparse "2024-01-09"))',
                    9,
                    "dates/isoparse",
                ),
                (
                    "[rich.text :as text]",
                    '(count (.-plain (text/Text "hello")))',
                    5,
                    "text/Text",
                ),
                (
                    "[PIL.Image :as image]",
                    '(with [picture (image/new "RGB" #py (2 3))] (.-width picture))',
                    2,
                    "image/new",
                ),
            ],
        ),
    }
    for family, (dependencies, variants) in families.items():
        for size, count in (("small", small_files), ("large", large_files)):
            files, probes, values = {}, [], []
            for index in range(count):
                selected = variants if count == 1 else [variants[index % len(variants)]]
                imports = " ".join(item[0] for item in selected if item[0])
                namespace = (
                    f"(ns demo.part{index}"
                    + (f" (:import {imports})" if imports else "")
                    + ")\n"
                )
                definitions, calls = [], []
                for number, (_, body, value, symbol) in enumerate(selected):
                    definitions.append(f"(defn calculate{number} [n] (+ n {body}))\n")
                    calls.append(f"(calculate{number} {index + seed})")
                    values.append(index + seed + value)
                    if symbol and not any(probe[1] == symbol for probe in probes):
                        probes.append((f"demo/part{index}.lpy", symbol))
                files[f"demo/part{index}.lpy"] = (
                    namespace
                    + "".join(definitions)
                    + f"(defn value [] (+ {' '.join(calls)}))\n"
                )
            requires = " ".join(f"[demo.part{i} :as p{i}]" for i in range(count))
            calls = " ".join(f"(p{i}/value)" for i in range(count))
            files["demo/main.lpy"] = (
                f"(ns demo.main (:require {requires}))\n(defn run [] (+ {calls}))\n"
            )
            yield Case(
                f"{family}-{size}",
                files,
                str(sum(values)),
                "(np/array)"
                if family == "data"
                else "(requests/get 123)"
                if family == "services"
                else "(stats/mean)"
                if family == "stdlib"
                else "(p0/value 1)",
                "type-mismatch" if family == "services" else "invalid-arity",
                dependencies,
                tuple(probes),
                "demo/part0.lpy" if family != "pure" else "demo/main.lpy",
                ("dates/date",) if family == "stdlib" else (),
            )
    from generated_edge_cases import cases as edge_cases

    yield from edge_cases(Case)


def run(command, root, timeout=120):
    # Share process-tree cleanup and optional POSIX CPU accounting with the public audit.
    outcome = run_command(command, root, timeout)
    stderr = outcome["stderr"]
    if outcome["timeout"]:
        stderr += f"\nHarness timeout after {timeout}s\n"
    result = subprocess.CompletedProcess(
        command, outcome["exit"], outcome["stdout"], stderr
    )
    metrics = {
        key: value
        for key, value in outcome.items()
        if key not in {"exit", "stdout", "stderr"}
    }
    return result, metrics


def cache_size(path):
    files = (
        [item for item in path.rglob("*") if item.is_file()] if path.exists() else []
    )
    return {"files": len(files), "bytes": sum(item.stat().st_size for item in files)}


def offset(text, point):
    lines = text.splitlines(keepends=True)
    line, units = point["line"], point["character"]
    prefix = "".join(lines[:line])
    current = lines[line] if line < len(lines) else ""
    return len(prefix) + len(
        current.encode("utf-16-le")[: units * 2].decode("utf-16-le")
    )


def apply_rename(edit, root):
    changes = dict(edit.get("changes", {}))
    for item in edit.get("documentChanges", []):
        if "textDocument" not in item:
            raise AssertionError(f"Unexpected file operation in symbol rename: {item}")
        changes.setdefault(item["textDocument"]["uri"], []).extend(item["edits"])
    assert len(changes) >= 2, f"Rename must update declaration and consumer: {edit}"
    for uri, edits in changes.items():
        path = Path(unquote(urlparse(uri).path)).resolve()
        assert root.resolve() in path.parents, (
            f"Rename escaped generated project: {path}"
        )
        text = path.read_text(encoding="utf-8")
        replacements = [
            (
                offset(text, item["range"]["start"]),
                offset(text, item["range"]["end"]),
                item["newText"],
            )
            for item in edits
        ]
        for start, end, replacement in sorted(replacements, reverse=True):
            text = text[:start] + replacement + text[end:]
        path.write_text(text, encoding="utf-8")


def audit(
    case,
    seed,
    output,
    blt,
    basilisp,
    protocol=True,
    python_executable=None,
    timeout=120,
    python_timeout=5,
    lsp_repeats=1,
):
    root = output / f"{case.name}-{seed}"
    root.mkdir(parents=True, exist_ok=False)
    dependencies = ["basilisp>=0.5.1", *case.dependencies]
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "blt-generated-{case.name}-{seed}"\n'
        'version = "0.0.0"\nrequires-python = ">=3.10"\n'
        f"dependencies = {json.dumps(dependencies)}\n",
        encoding="utf-8",
    )
    src = root / "src"
    for name, text in case.files.items():
        path = src / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (src / "demo/runner.lpy").write_text(
        "(ns demo.runner (:require [demo.main :as main]))\n(defn start [] (main/run))\n",
        encoding="utf-8",
    )
    config = root / ".clj-kondo"
    config.mkdir()
    (config / "config.edn").write_text(
        "{:lint-as {demo.macros/defhandler basilisp.core/defn}}\n", encoding="utf-8"
    )
    runtime = [
        basilisp,
        "run",
        "-p",
        str(src),
        "--include-unsafe-path=false",
        "-c",
        "(require '[demo.runner :as runner]) (println (runner/start))",
    ]
    checker = [
        blt,
        "check",
        "--repro",
        "--format",
        "json",
        "--python-timeout",
        str(python_timeout),
    ]
    if python_executable:
        checker.extend(["--python", python_executable])
    checker.append("src")
    source_files = [p for p in src.rglob("*") if p.suffix in {".lpy", ".cljc"}]
    report = {
        "project": root.name,
        "source_files": len(source_files),
        "source_bytes": sum(p.stat().st_size for p in source_files),
        "dependencies": list(case.dependencies),
        "timings": {},
        "commands": [],
        "failures": [],
        "lsp_runs": [],
    }
    commands = root / "commands"
    commands.mkdir()
    valid = src / "demo/main.lpy"

    def verify(condition, message, evidence=None):
        if not condition:
            report["failures"].append({"assertion": message, "evidence": evidence})

    def expected(label, command, codes=(0,)):
        print(
            json.dumps({"project": root.name, "stage": label, "status": "starting"}),
            flush=True,
        )
        result, metrics = run(command, root, timeout)
        report["timings"][label] = metrics["seconds"]
        report["commands"].append(
            {
                "stage": label,
                "command": command,
                "returncode": result.returncode,
                **metrics,
            }
        )
        (commands / f"{label}.stdout").write_text(result.stdout, encoding="utf-8")
        (commands / f"{label}.stderr").write_text(result.stderr, encoding="utf-8")
        if result.returncode not in codes:
            raise AssertionError(
                f"{label}: exit {result.returncode}\n{result.stdout}\n{result.stderr}"
            )
        return result

    def check(label, expected_diagnostic=None):
        result = expected(label, checker, (0, 2, 3))
        payload = json.loads(result.stdout)
        verify(
            payload.get("summary", {}).get("files") == report["source_files"],
            f"{label}: every generated source was checked",
            payload.get("summary"),
        )
        findings = payload["findings"]
        verify(
            any(f["type"] == expected_diagnostic for f in findings)
            if expected_diagnostic
            else not findings,
            f"{label}: expected {expected_diagnostic or 'no findings'}",
            findings,
        )
        return findings

    try:
        result = expected("runtime_before", runtime)
        assert result.stdout.strip() == case.expression, result.stdout
        expected("format", [blt, "format", "."])
        expected("format_check", [blt, "format", "--check", "."])
        formatted = {p: p.read_bytes() for p in source_files}
        expected("format_warm", [blt, "format", "."])
        verify(
            all(p.read_bytes() == data for p, data in formatted.items()),
            "Formatting is idempotent",
        )
        result = expected("runtime_after_format", runtime)
        verify(
            result.stdout.strip() == case.expression,
            "Runtime preserved by formatting",
            result.stdout,
        )
        report["check_cache_before"] = cache_size(config / ".cache")
        check("check")
        report["check_cache_after"] = cache_size(config / ".cache")
        check("check_warm")
        mutation_file = src / case.mutation_file
        original_mutation = mutation_file.read_text(encoding="utf-8")
        mutation_file.write_text(
            original_mutation + "\n" + case.bad_call + "\n", encoding="utf-8"
        )
        report["mutation_findings"] = check("check_mutation", case.diagnostic)
        mutation_file.write_text(original_mutation, encoding="utf-8")
        check("check_repair")
        if protocol:
            uri = valid.as_uri()
            settings = {
                "source-paths": ["src"],
                "text-document-sync-kind": "incremental",
                "cache-path": str(root / "lsp-cache") if lsp_repeats > 1 else False,
                "python": {},
            }
            if python_executable:
                settings["python"]["executable"] = python_executable
            rename = None
            for repetition in range(lsp_repeats):
                client = Client(
                    root,
                    root / f"lsp-{repetition + 1}",
                    blt=blt,
                    extra_args=["--python-timeout", str(python_timeout)],
                )
                lsp_report = {
                    "run": repetition + 1,
                    "cache_before": cache_size(root / "lsp-cache"),
                }
                report["lsp_runs"].append(lsp_report)
                original = valid.read_text(encoding="utf-8")
                run_name = "run"
                try:
                    initialized = client.request(
                        "initialize",
                        {
                            "processId": os.getpid(),
                            "rootUri": root.as_uri(),
                            "capabilities": {
                                "general": {"positionEncodings": ["utf-16"]},
                                "workspace": {
                                    "workspaceEdit": {"documentChanges": True}
                                },
                            },
                            "initializationOptions": settings,
                        },
                        timeout=timeout,
                    )
                    verify(
                        initialized["capabilities"]["textDocumentSync"]["change"] == 2,
                        "Incremental sync advertised",
                    )
                    client.send("initialized", {})
                    client.send(
                        "textDocument/didOpen",
                        {
                            "textDocument": {
                                "uri": uri,
                                "languageId": "basilisp",
                                "version": 1,
                                "text": original,
                            }
                        },
                    )
                    diagnostics = client.diagnostics(uri, 1, timeout=timeout)
                    lsp_report["initial_diagnostics"] = diagnostics
                    verify(
                        not [d for d in diagnostics if d.get("severity", 1) <= 2],
                        "LSP initial diagnostics are clean",
                        diagnostics,
                    )
                    symbols = client.request(
                        "textDocument/documentSymbol",
                        {"textDocument": {"uri": uri}},
                        timeout=timeout,
                    )
                    format_edits = client.request(
                        "textDocument/formatting",
                        {
                            "textDocument": {"uri": uri},
                            "options": {"tabSize": 2, "insertSpaces": True},
                        },
                        timeout=timeout,
                    )
                    formatted_source = original
                    replacements = [
                        (
                            offset(original, e["range"]["start"]),
                            offset(original, e["range"]["end"]),
                            e["newText"],
                        )
                        for e in (format_edits or [])
                    ]
                    for begin, end, text in sorted(replacements, reverse=True):
                        formatted_source = (
                            formatted_source[:begin] + text + formatted_source[end:]
                        )
                    verify(
                        formatted_source == original,
                        "LSP and CLI formatting agree",
                        format_edits,
                    )
                    run_symbol = next(s for s in symbols if s["name"] == run_name)
                    point = {
                        "textDocument": {"uri": uri},
                        "position": run_symbol["selectionRange"]["start"],
                    }
                    hover = client.request("textDocument/hover", point, timeout=timeout)
                    verify(run_name in str(hover), "Hover describes function", hover)
                    references = client.request(
                        "textDocument/references",
                        {**point, "context": {"includeDeclaration": True}},
                        timeout=timeout,
                    )
                    verify(
                        any(r["uri"].endswith("/runner.lpy") for r in references),
                        "References include consumer",
                        references,
                    )
                    if repetition > 0:
                        continue
                    python_probes = list(case.python_probes)
                    if case.name in {"python", "stubs"}:
                        python_probes.append(
                            (
                                "demo/main.lpy",
                                "py/offset"
                                if case.name == "python"
                                else "py/normalize",
                            )
                        )
                    opened = {uri}
                    lsp_report["python_probes"] = []
                    for filename, symbol in python_probes:
                        probe_path = src / filename
                        probe_uri, probe_source = (
                            probe_path.as_uri(),
                            probe_path.read_text(encoding="utf-8"),
                        )
                        if probe_uri not in opened:
                            client.send(
                                "textDocument/didOpen",
                                {
                                    "textDocument": {
                                        "uri": probe_uri,
                                        "languageId": "basilisp",
                                        "version": 1,
                                        "text": probe_source,
                                    }
                                },
                            )
                            probe_diagnostics = client.diagnostics(
                                probe_uri, 1, timeout=timeout
                            )
                            verify(
                                not [
                                    d
                                    for d in probe_diagnostics
                                    if d.get("severity", 1) <= 2
                                ],
                                f"Clean Python document {filename}",
                                probe_diagnostics,
                            )
                            opened.add(probe_uri)
                        position = probe_source.index(symbol)
                        prefix = symbol.index("/") + 1
                        completion = client.request(
                            "textDocument/completion",
                            at(probe_uri, probe_source, position + prefix),
                            timeout=timeout,
                        )
                        verify(
                            symbol in [item["label"] for item in completion["items"]],
                            f"Python completion for {symbol}",
                            [item["label"] for item in completion["items"]],
                        )
                        py_point = at(probe_uri, probe_source, position + prefix + 1)
                        hover = client.request(
                            "textDocument/hover", py_point, timeout=timeout
                        )
                        verify(
                            symbol.split("/")[1] in str(hover),
                            f"Python hover for {symbol}",
                            hover,
                        )
                        definition = client.request(
                            "textDocument/definition", py_point, timeout=timeout
                        )
                        locations = (
                            definition
                            if isinstance(definition, list)
                            else [definition]
                            if definition
                            else []
                        )
                        source_locations = []
                        for location in locations:
                            target_uri = (
                                location.get("uri") or location.get("targetUri") or ""
                            )
                            target = Path(unquote(urlparse(target_uri).path))
                            if (
                                urlparse(target_uri).scheme == "file"
                                and target.suffix in {".py", ".pyi"}
                                and target.is_file()
                            ):
                                source_locations.append(location)
                        optional_native = symbol in case.native_source_optional
                        verify(
                            len(source_locations) == len(locations)
                            and (bool(source_locations) or optional_native),
                            f"Python definition targets source/stub for {symbol}",
                            definition,
                        )
                        definition_status = (
                            "source"
                            if source_locations
                            else "native-object-without-source"
                            if optional_native and not locations
                            else "missing-or-invalid-source"
                        )
                        if case.name in {"python", "stubs"}:
                            verify(
                                "/support.py" in str(definition),
                                "Python navigation targets local source/stub",
                                definition,
                            )
                        lsp_report["python_probes"].append(
                            {
                                "symbol": symbol,
                                "hover": hover,
                                "definition": definition,
                                "definition_status": definition_status,
                            }
                        )
                    version = 1
                    if case.name == "stubs":
                        stub = src / "support.pyi"
                        stub_text = stub.read_text(encoding="utf-8")
                        for text, should_error in [
                            (stub_text.replace("value: str", "value: int"), True),
                            (stub_text, False),
                        ]:
                            stub.write_text(text, encoding="utf-8")
                            client.send(
                                "workspace/didChangeWatchedFiles",
                                {"changes": [{"uri": stub.as_uri(), "type": 2}]},
                            )
                            version += 1
                            client.send(
                                "textDocument/didChange",
                                {
                                    "textDocument": {"uri": uri, "version": version},
                                    "contentChanges": [{"text": original}],
                                },
                            )
                            diagnostics = client.diagnostics(
                                uri, version, timeout=timeout
                            )
                            verify(
                                any(
                                    d.get("code") == "type-mismatch"
                                    for d in diagnostics
                                )
                                == should_error,
                                "Stub edit invalidates inspection metadata",
                                diagnostics,
                            )
                    suffix = '\n(do "😀" generated-missing)\n'
                    end = at(uri, original, len(original))["position"]
                    client.send(
                        "textDocument/didChange",
                        {
                            "textDocument": {"uri": uri, "version": version + 1},
                            "contentChanges": [
                                {"range": {"start": end, "end": end}, "text": suffix}
                            ],
                        },
                    )
                    diagnostics = client.diagnostics(uri, version + 1, timeout=timeout)
                    missing = next(
                        d
                        for d in diagnostics
                        if d.get("code") == "unresolved-symbol"
                        and "generated-missing" in d["message"]
                    )
                    bad = original + suffix
                    verify(
                        missing["range"]["start"]
                        == at(uri, bad, bad.index("generated-missing"))["position"],
                        "Diagnostic UTF-16 position after emoji",
                        missing,
                    )
                    client.send(
                        "textDocument/didChange",
                        {
                            "textDocument": {"uri": uri, "version": version + 2},
                            "contentChanges": [{"text": original}],
                        },
                    )
                    repaired = client.diagnostics(uri, version + 2, timeout=timeout)
                    verify(
                        not [d for d in repaired if d.get("severity", 1) <= 2],
                        "Repair removes diagnostics",
                        repaired,
                    )
                    rename = client.request(
                        "textDocument/rename",
                        {**point, "newName": "run-renamed"},
                        timeout=timeout,
                    )
                except Exception as error:  # noqa: BLE001 -- Retain failures and still exercise a warm server.
                    report["failures"].append(
                        {"assertion": "LSP session", "evidence": repr(error)}
                    )
                finally:
                    client.stop()
                    lsp_report["timings"] = client.timings
                    lsp_report["process_metrics"] = getattr(
                        client, "process_metrics", {}
                    )
                    lsp_report["cache_after"] = cache_size(root / "lsp-cache")
                    verify(
                        client.process.returncode == 0,
                        "LSP clean exit",
                        client.process.returncode,
                    )
            report["timings"]["lsp"] = report["lsp_runs"][0]["timings"]
            if rename is not None:
                apply_rename(rename, root)
            else:
                verify(False, "LSP returned a rename edit")
            result = expected("runtime_after_rename", runtime)
            verify(
                result.stdout.strip() == case.expression,
                "Runtime preserved by rename",
                result.stdout,
            )
            check("check_after_rename")
    except Exception as error:  # noqa: BLE001 -- Keep auditing and report every failed scenario.
        report["failures"].append({"assertion": "Audit stage", "evidence": repr(error)})
    report["passed"] = not report["failures"]
    (root / "audit.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "project": root.name,
                "passed": report["passed"],
                "failures": report["failures"],
                "timings": report["timings"],
            }
        ),
        flush=True,
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--blt", default=shutil.which("blt"))
    parser.add_argument("--basilisp", default=shutil.which("basilisp"))
    parser.add_argument("--seeds", type=int, default=2)
    parser.add_argument("--workspace-files", type=int, default=24)
    parser.add_argument(
        "--profile", choices=("standard", "expanded"), default="standard"
    )
    parser.add_argument(
        "--small-files",
        type=int,
        default=1,
        help="Leaf modules in each expanded small project",
    )
    parser.add_argument(
        "--large-files",
        type=int,
        default=100,
        help="Leaf modules in each expanded large project",
    )
    parser.add_argument(
        "--python",
        dest="python_executable",
        help="Dependency interpreter inspected by check and LSP",
    )
    parser.add_argument("--python-timeout", type=float, default=5)
    parser.add_argument(
        "--timeout",
        type=float,
        default=120,
        help="Per-command/LSP request deadline in seconds",
    )
    parser.add_argument(
        "--lsp-repeats",
        type=int,
        default=1,
        help="Fresh server runs sharing the project analysis cache",
    )
    parser.add_argument(
        "--project", action="append", help="Generate only the named scenario"
    )
    parser.add_argument(
        "--no-lsp",
        action="store_true",
        help="Run runtime, formatting and checker stages only",
    )
    args = parser.parse_args()
    if not args.blt or not args.basilisp or args.seeds < 1 or args.workspace_files < 2:
        parser.error(
            "blt and basilisp executables and a positive seed count are required"
        )
    if (
        min(args.small_files, args.large_files, args.lsp_repeats) < 1
        or args.timeout <= 0
        or args.python_timeout <= 0
    ):
        parser.error("sizes, repeats and timeouts must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    python_executable = (
        str(Path(args.python_executable).absolute()) if args.python_executable else None
    )
    environment = {
        "harness_python": sys.version,
        "python_executable": python_executable,
        "profile": args.profile,
        "args": vars(args) | {"output": str(args.output)},
    }
    if python_executable:
        metadata = subprocess.run(
            [
                python_executable,
                "-c",
                "import importlib.metadata as m,json,platform; print(json.dumps({'python':platform.python_version(),'packages':{d.metadata['Name']:d.version for d in m.distributions()}}))",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        environment["dependency_environment"] = json.loads(metadata.stdout)
    (args.output / "environment.json").write_text(
        json.dumps(environment, indent=2) + "\n", encoding="utf-8"
    )
    reports = []
    for seed in range(args.seeds):
        scenarios = list(cases(seed, args.workspace_files))
        if args.profile == "expanded":
            scenarios.extend(expanded_cases(seed, args.small_files, args.large_files))
        for case in scenarios:
            if not args.project or case.name in args.project:
                reports.append(
                    audit(
                        case,
                        seed,
                        args.output.resolve(),
                        str(Path(args.blt).resolve()),
                        str(Path(args.basilisp).resolve()),
                        not args.no_lsp,
                        python_executable,
                        args.timeout,
                        args.python_timeout,
                        args.lsp_repeats,
                    )
                )
    assert reports, "No scenarios selected"
    (args.output / "summary.json").write_text(
        json.dumps(reports, indent=2) + "\n", encoding="utf-8"
    )
    raise SystemExit(0 if all(r["passed"] for r in reports) else 1)


if __name__ == "__main__":
    main()
