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
import os
import signal
import statistics
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path



try:
    import resource
except ImportError:  # Windows does not provide per-child CPU accounting here.
    resource = None


def run_command(command, cwd, timeout):
    """Measure this waited child process tree; kill its group on timeout."""
    before = resource.getrusage(resource.RUSAGE_CHILDREN) if resource else None
    started = time.perf_counter()
    with subprocess.Popen(
        command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=os.name == "posix",
    ) as process:
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
    after = resource.getrusage(resource.RUSAGE_CHILDREN) if resource else None
    user = after.ru_utime - before.ru_utime if resource else None
    system = after.ru_stime - before.ru_stime if resource else None
    return {
        "code": "timeout" if timed_out else process.returncode,
        "seconds": round(time.perf_counter() - started, 6),
        "child_user_seconds": round(user, 6) if resource else None,
        "child_system_seconds": round(system, 6) if resource else None,
        "child_cpu_seconds": round(user + system, 6) if resource else None,
        "stdout": stdout,
        "stderr": stderr,
    }


def tracked_sources(checkout: Path) -> list[str]:
    paths = (
        subprocess.check_output(
            ["git", "ls-files", "-z", "--", "*.lpy", "*.cljc"], cwd=checkout
        )
        .decode("utf-8")
        .split("\0")
    )
    return sorted(path for path in paths if path)



def source_symlinks(checkout: Path, files: list[str]) -> list[str]:
    """Reject links in selected source paths, including symlinked parents."""
    linked = []
    for relative in files:
        path = checkout
        for part in Path(relative).parts:
            path = path / part
            if path.is_symlink():
                linked.append(relative)
                break
    return linked


def copy_checkout(checkout: Path, target: Path) -> list[str]:
    """Copy regular project files without traversing checkout symlinks."""
    ignore_patterns = shutil.ignore_patterns(
        ".git", ".venv", "__pycache__", ".cache", ".mypy_cache", ".pytest_cache",
    )
    links = []

    def ignore(directory, names):
        ignored = set(ignore_patterns(directory, names))
        for name in names:
            path = Path(directory) / name
            if path.is_symlink():
                ignored.add(name)
                links.append(str(path.relative_to(checkout)))
        return ignored

    shutil.copytree(checkout, target, ignore=ignore)
    return sorted(links)


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
    parser.add_argument(
        "--project", action="append",
        help="Repository basename or owner/name; repeat to select projects",
    )
    parser.add_argument(
        "--repeat", type=int, default=1,
        help="Number of steady-state --check processes after formatting (default: 1)",
    )
    args = parser.parse_args()
    executable = args.blt.absolute()
    if not executable.is_file():
        parser.error(f"CLI not found: {executable}; pass --blt /path/to/blt")
    if args.timeout <= 0 or args.repeat < 1:
        parser.error("--timeout and --repeat must be positive")
    if args.diff_dir:
        args.diff_dir.mkdir(parents=True, exist_ok=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    projects = manifest.get("projects", []) if isinstance(manifest, dict) else manifest

    from basilisp.lang.keyword import keyword as kw
    from basilisp.lang.map import map as lmap

    import basilisp_tools  # noqa: F401 - initialize the Basilisp importer

    syntax = importlib.import_module("basilisp_tools.syntax")

    def source_dialect(relative: str) -> str:
        return "basilisp" if Path(relative).suffix in {".lpy", ".cljc"} else "clojure"

    def payload(source: str, relative: str):
        dialect = lmap({kw("dialect"): kw(source_dialect(relative))})
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
        if args.project and not {name, project["repo"]}.intersection(args.project):
            continue
        checkout = args.corpus / name
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=checkout, text=True
        ).strip()
        expected = project.get("sha") or project.get("revision")
        if expected and revision != expected:
            parser.error(
                f"{project['repo']}: expected {expected}, got {revision}"
            )
        files = project.get("files") or tracked_sources(checkout)
        linked = source_symlinks(checkout, files)
        if linked:
            parser.error(f"{project['repo']}: source symlinks are unsupported: {linked}")
        missing = [path for path in files if not (checkout / path).is_file()]
        if missing:
            parser.error(f"{project['repo']}: missing selected sources: {missing}")
        report = {
            "repo": project["repo"],
            "sha": revision,
            "files": len(files),
            "selected_files": files,
            "source_dialects": sorted({source_dialect(path) for path in files}),
            "bytes": 0,
            "runs": [],
            "changes": [],
            "problems": [],
            "benchmark_notes": [
                "Processes run sequentially; unrelated host workloads are not controlled.",
                "Compiler and OS caches are not cleared; parser imports can warm compiler caches.",
                "Child CPU includes waited subprocess descendants, excluding preservation review.",
            ],
            "compiler_cache_disabled_env": os.environ.get(
                "BASILISP_DO_NOT_CACHE_NAMESPACES"
            ),
        }
        if not files:
            report["problems"].append(
                {"stage": "discovery", "detail": "No tracked sources"}
            )
        with tempfile.TemporaryDirectory(prefix=f"blt-format-{name}-") as temporary:
            target = Path(temporary) / name
            report["ignored_symlinks"] = copy_checkout(checkout, target)
            originals = {
                relative: (target / relative).read_bytes() for relative in files
            }
            report["bytes"] = sum(map(len, originals.values()))
            stages = [("format", [])] + [
                ("check-formatted" if index == 0 else f"check-formatted-{index + 1}", ["--check"])
                for index in range(args.repeat)
            ]
            for label, flags in stages:
                # Explicit paths bypass project discovery filters, so every
                # selected source participates even when it is unchanged.
                command = [str(executable), "format", *flags, *files]
                run = {"stage": label, "command": command}
                run.update(run_command(command, target, args.timeout))
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
                    before = payload(source, relative)
                    after = before if actual_raw == raw else payload(actual, relative)
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
        steady = report["runs"][1:]
        report["benchmark"] = {
            "steady_state_runs": len(steady),
            **{
                key + "_median": (
                    statistics.median(run[key] for run in steady)
                    if all(run[key] is not None for run in steady) else None
                )
                for key in ("seconds", "child_cpu_seconds")
            },
        }
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
    if not reports:
        parser.error("No projects matched")
    failures = sum(len(report["problems"]) for report in reports)
    print(
        f"Public formatter audit: {len(reports)} projects, "
        f"{sum(report['files'] for report in reports)} files, {failures} problems"
    )
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
