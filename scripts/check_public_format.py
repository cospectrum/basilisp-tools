"""Exercise the real formatter CLI on pinned public Basilisp projects.

Run after cloning the revisions in scripts/public_projects.json:
    uv run python scripts/check_public_format.py --corpus /path/to/checkouts

Each checkout is copied to a temporary directory before formatting. The audit
checks a second CLI pass, parser round trips, token/literal preservation, and
records timings and diffs. No project source is imported or executed.
"""

from __future__ import annotations

import argparse
import difflib
import importlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def tracked_sources(checkout: Path) -> list[str]:
    paths = (
        subprocess.check_output(
            ["git", "ls-files", "-z", "--", "*.lpy", "*.cljc"], cwd=checkout
        )
        .decode("utf-8")
        .split("\0")
    )
    return sorted(path for path in paths if path and (checkout / path).is_file())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).with_name("public_projects.json"),
    )
    parser.add_argument("--blt", type=Path, default=Path(sys.executable).parent / "blt")
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--diff-dir", type=Path, help="Retain complete diffs for review."
    )
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()
    executable = args.blt.absolute()
    if not executable.is_file():
        parser.error(f"CLI not found: {executable}; pass --blt /path/to/blt")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.diff_dir:
        args.diff_dir.mkdir(parents=True, exist_ok=True)
    projects = json.loads(args.manifest.read_text(encoding="utf-8"))

    from basilisp.lang.keyword import keyword as kw
    from basilisp.lang.map import map as lmap

    import basilisp_tools  # noqa: F401 - initialize the Basilisp importer

    syntax = importlib.import_module("basilisp_tools.syntax")
    dialect = lmap({kw("dialect"): kw("clojure")})

    def payload(source: str):
        parsed = syntax.parse(source, dialect)
        if syntax.text(parsed[kw("root")]) != source:
            raise AssertionError("Lossless parser round trip changed source")
        if len(parsed[kw("diagnostics")]):
            raise AssertionError(str(parsed[kw("diagnostics")]))
        values = []
        for token in syntax.tokens(parsed[kw("root")]):
            kind = str(token[kw("kind")])
            if kind not in (":whitespace", ":newline"):
                text = token[kw("text")]
                values.append(
                    (kind, text.rstrip(" \t") if kind == ":comment" else text)
                )
        return values

    reports = []
    for project in projects:
        name = project["repo"].split("/")[-1]
        checkout = args.corpus / name
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=checkout, text=True
        ).strip()
        if revision != project["sha"]:
            parser.error(
                f"{project['repo']}: expected {project['sha']}, got {revision}"
            )
        files = tracked_sources(checkout)
        report = {
            "repo": project["repo"],
            "sha": revision,
            "files": len(files),
            "bytes": 0,
            "runs": [],
            "changes": [],
            "problems": [],
        }
        if not files:
            report["problems"].append(
                {"stage": "discovery", "detail": "No tracked sources"}
            )
        with tempfile.TemporaryDirectory(prefix=f"blt-format-{name}-") as temporary:
            target = Path(temporary) / name
            shutil.copytree(
                checkout,
                target,
                ignore=shutil.ignore_patterns(
                    ".git",
                    ".venv",
                    "__pycache__",
                    ".cache",
                    ".mypy_cache",
                    ".pytest_cache",
                ),
            )
            originals = {
                relative: (target / relative).read_bytes() for relative in files
            }
            report["bytes"] = sum(map(len, originals.values()))
            for label, flags in [("format", []), ("check-formatted", ["--check"])]:
                command = [str(executable), "format", *flags, "."]
                started = time.perf_counter()
                try:
                    process = subprocess.run(
                        command,
                        cwd=target,
                        text=True,
                        capture_output=True,
                        timeout=args.timeout,
                        check=False,
                    )
                    run = {
                        "stage": label,
                        "command": command,
                        "code": process.returncode,
                        "stdout": process.stdout,
                        "stderr": process.stderr,
                    }
                except subprocess.TimeoutExpired:
                    run = {"stage": label, "command": command, "code": "timeout"}
                run["seconds"] = round(time.perf_counter() - started, 3)
                report["runs"].append(run)
                if run["code"] != 0:
                    report["problems"].append({"stage": label, "detail": run})
                print(
                    f"{name}: {label}: exit {run['code']}, {run['seconds']:.3f}s",
                    flush=True,
                )
            diffs = []
            for relative, raw in originals.items():
                actual_raw = (target / relative).read_bytes()
                try:
                    source, actual = raw.decode("utf-8"), actual_raw.decode("utf-8")
                    before = payload(source)
                    after = before if actual_raw == raw else payload(actual)
                    if before != after:
                        raise AssertionError(
                            "Non-whitespace tokens changed; review the diff"
                        )
                except Exception as error:  # noqa: BLE001 -- Preserve parser crashes in the audit report.
                    report["problems"].append(
                        {
                            "stage": "preservation",
                            "path": relative,
                            "detail": str(error),
                        }
                    )
                    if isinstance(error, UnicodeError):
                        continue
                if actual_raw != raw:
                    diff = list(
                        difflib.unified_diff(
                            source.splitlines(keepends=True),
                            actual.splitlines(keepends=True),
                            fromfile=relative,
                            tofile=relative,
                        )
                    )
                    report["changes"].append(
                        {
                            "path": relative,
                            "old_bytes": len(raw),
                            "new_bytes": len(actual_raw),
                            "diff_lines": len(diff),
                        }
                    )
                    diffs.extend(diff)
            if args.diff_dir:
                (args.diff_dir / f"{name}.diff").write_text(
                    "".join(diffs), encoding="utf-8"
                )
        reports.append(report)
        if args.report:
            args.report.write_text(
                json.dumps(reports, indent=2) + "\n", encoding="utf-8"
            )
        print(
            f"{name}: {len(files)} files, {len(report['changes'])} changed, "
            f"{len(report['problems'])} problems",
            flush=True,
        )
    failures = sum(len(report["problems"]) for report in reports)
    print(
        f"Public formatter audit: {len(reports)} projects, "
        f"{sum(report['files'] for report in reports)} files, {failures} problems"
    )
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
