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
import time
from dataclasses import dataclass
from pathlib import Path

from check_public_lsp import Client, at


@dataclass
class Case:
    name: str
    files: dict[str, str]
    expression: str
    bad_call: str
    diagnostic: str


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


def run(command, root, timeout=120):
    start = time.monotonic()
    result = subprocess.run(
        command, cwd=root, text=True, capture_output=True, timeout=timeout, check=False
    )
    return result, round(time.monotonic() - start, 4)


def expected(command, root, codes=(0,)):
    result, elapsed = run(command, root)
    if result.returncode not in codes:
        raise AssertionError(
            f"{command}: exit {result.returncode}\n{result.stdout}\n{result.stderr}"
        )
    return result, elapsed


def offset(text, point):
    lines = text.splitlines(keepends=True)
    line, units = point["line"], point["character"]
    prefix = "".join(lines[:line])
    current = lines[line] if line < len(lines) else ""
    return len(prefix) + len(
        current.encode("utf-16-le")[: units * 2].decode("utf-16-le")
    )


def apply_rename(edit, root):
    from urllib.parse import unquote, urlparse

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


def audit(case, seed, output, blt, basilisp, protocol=True):
    root = output / f"{case.name}-{seed}"
    root.mkdir(parents=True, exist_ok=False)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "blt-generated-{case.name}-{seed}"\n'
        'version = "0.0.0"\nrequires-python = ">=3.10"\n'
        'dependencies = ["basilisp>=0.5.1"]\n',
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
    report = {"project": root.name, "timings": {}}
    valid = src / "demo/main.lpy"
    try:
        result, elapsed = expected(runtime, root)
        assert result.stdout.strip() == case.expression, result.stdout
        report["timings"]["runtime_before"] = elapsed
        result, elapsed = expected([blt, "format", "."], root)
        report["timings"]["format"] = elapsed
        expected([blt, "format", "--check", "."], root)
        formatted = {
            p: p.read_bytes() for p in src.rglob("*") if p.suffix in {".lpy", ".cljc"}
        }
        expected([blt, "format", "."], root)
        assert all(p.read_bytes() == data for p, data in formatted.items()), (
            "Formatting is not idempotent"
        )
        result, _ = expected(runtime, root)
        assert result.stdout.strip() == case.expression, result.stdout
        result, elapsed = expected(
            [blt, "check", "--repro", "--format", "json", "src"], root
        )
        clean = json.loads(result.stdout)
        assert not clean["findings"], clean
        report["timings"]["check"] = elapsed
        original = valid.read_text(encoding="utf-8")
        valid.write_text(original + "\n" + case.bad_call + "\n", encoding="utf-8")
        result, _ = expected(
            [blt, "check", "--repro", "--format", "json", "src"], root, (2, 3)
        )
        findings = json.loads(result.stdout)["findings"]
        assert any(f["type"] == case.diagnostic for f in findings), findings
        report["mutation_findings"] = findings
        valid.write_text(original, encoding="utf-8")
        expected([blt, "check", "--repro", "--format", "json", "src"], root)
        if protocol:
            client = Client(root, root / "lsp", blt=blt)
            uri = valid.as_uri()
            try:
                result = client.request(
                    "initialize",
                    {
                        "processId": os.getpid(),
                        "rootUri": root.as_uri(),
                        "capabilities": {
                            "general": {"positionEncodings": ["utf-16"]},
                            "workspace": {"workspaceEdit": {"documentChanges": True}},
                        },
                        "initializationOptions": {
                            "source-paths": ["src"],
                            "text-document-sync-kind": "incremental",
                            "cache-path": False,
                        },
                    },
                )
                assert result["capabilities"]["textDocumentSync"]["change"] == 2
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
                diagnostics = client.diagnostics(uri, 1)
                assert not [d for d in diagnostics if d.get("severity", 1) <= 2], (
                    diagnostics
                )
                symbols = client.request(
                    "textDocument/documentSymbol", {"textDocument": {"uri": uri}}
                )
                run_symbol = next(s for s in symbols if s["name"] == "run")
                point = {
                    "textDocument": {"uri": uri},
                    "position": run_symbol["selectionRange"]["start"],
                }
                assert "run" in str(client.request("textDocument/hover", point))
                references = client.request(
                    "textDocument/references",
                    {**point, "context": {"includeDeclaration": True}},
                )
                assert any(r["uri"].endswith("/runner.lpy") for r in references), (
                    references
                )
                if case.name in {"python", "stubs"}:
                    symbol = "py/offset" if case.name == "python" else "py/normalize"
                    position = original.index(symbol)
                    completion = client.request(
                        "textDocument/completion", at(uri, original, position + 3)
                    )
                    assert symbol in [item["label"] for item in completion["items"]], (
                        completion
                    )
                    py_point = at(uri, original, position + 4)
                    hover = client.request("textDocument/hover", py_point)
                    assert symbol.split("/")[1] in str(hover), hover
                    definition = client.request("textDocument/definition", py_point)
                    assert definition and "/support.py" in str(definition), definition
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
                        diagnostics = client.diagnostics(uri, version)
                        assert (
                            any(d.get("code") == "type-mismatch" for d in diagnostics)
                            == should_error
                        ), diagnostics
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
                diagnostics = client.diagnostics(uri, version + 1)
                missing = next(
                    d
                    for d in diagnostics
                    if d.get("code") == "unresolved-symbol"
                    and "generated-missing" in d["message"]
                )
                bad = original + suffix
                start = bad.index("generated-missing")
                assert missing["range"]["start"] == at(uri, bad, start)["position"], (
                    missing
                )
                client.send(
                    "textDocument/didChange",
                    {
                        "textDocument": {"uri": uri, "version": version + 2},
                        "contentChanges": [{"text": original}],
                    },
                )
                repaired = client.diagnostics(uri, version + 2)
                assert not [d for d in repaired if d.get("severity", 1) <= 2], repaired
                rename = client.request(
                    "textDocument/rename", {**point, "newName": "run-renamed"}
                )
                apply_rename(rename, root)
                report["timings"]["lsp"] = client.timings
            finally:
                client.stop()
            assert client.process.returncode == 0
            result, _ = expected(runtime, root)
            assert result.stdout.strip() == case.expression, result.stdout
            expected([blt, "check", "--repro", "--format", "json", "src"], root)
        report["passed"] = True
    except Exception as error:  # noqa: BLE001 -- Keep auditing and report every failed scenario.
        report.update(passed=False, error=str(error))
    (root / "audit.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--blt", default=shutil.which("blt"))
    parser.add_argument("--basilisp", default=shutil.which("basilisp"))
    parser.add_argument("--seeds", type=int, default=2)
    parser.add_argument("--workspace-files", type=int, default=24)
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
    args.output.mkdir(parents=True, exist_ok=True)
    reports = []
    for seed in range(args.seeds):
        for case in cases(seed, args.workspace_files):
            if not args.project or case.name in args.project:
                reports.append(
                    audit(
                        case,
                        seed,
                        args.output.resolve(),
                        str(Path(args.blt).resolve()),
                        str(Path(args.basilisp).resolve()),
                        not args.no_lsp,
                    )
                )
    assert reports, "No scenarios selected"
    (args.output / "summary.json").write_text(
        json.dumps(reports, indent=2) + "\n", encoding="utf-8"
    )
    raise SystemExit(0 if all(r["passed"] for r in reports) else 1)


if __name__ == "__main__":
    main()
