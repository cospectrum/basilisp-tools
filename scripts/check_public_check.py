"""Run blt check against pinned public projects and retain findings for review.

Clone scripts/public_projects.json revisions below --corpus first. --repeat N
measures N sequential processes and checks finding stability. --isolated-cache
uses a new cache below --output: the first run starts empty and later runs reuse
it. Compiler and operating-system caches are not cleared. Public suites can
intentionally contain invalid code, so findings need human classification.
"""

import argparse
import collections
import json
import os
import signal
import statistics
import subprocess
import tempfile
import time
from pathlib import Path


try:
    import resource
except ImportError:  # Windows does not provide per-child CPU accounting here.
    resource = None


def run_command(command, cwd, timeout):
    """Measure only this waited child process tree; kill its group on timeout."""
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
        "exit": process.returncode,
        "timeout": timed_out,
        "seconds": round(time.perf_counter() - started, 6),
        "child_user_seconds": round(user, 6) if resource else None,
        "child_system_seconds": round(system, 6) if resource else None,
        "child_cpu_seconds": round(user + system, 6) if resource else None,
        "stdout": stdout,
        "stderr": stderr,
    }


def cache_snapshot(directory):
    files = [path for path in directory.rglob("*") if path.is_file()]
    return {
        "state": "populated" if files else "empty",
        "files": len(files),
        "bytes": sum(path.stat().st_size for path in files),
    }


def timing_summary(runs):
    return {
        "runs": len(runs),
        **{
            key + "_median": (
                statistics.median(run[key] for run in runs)
                if all(run[key] is not None for run in runs) else None
            )
            for key in ("seconds", "child_cpu_seconds")
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument(
        "--manifest", type=Path,
        default=Path(__file__).with_name("public_projects.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--blt", default="blt")
    parser.add_argument("--python", help="Dependency interpreter passed to blt check")
    parser.add_argument(
        "--python-timeout", type=float,
        help="Inspection timeout forwarded to blt check (CLI default: 5 seconds)",
    )
    parser.add_argument(
        "--project", action="append",
        help="Repository basename or owner/name; repeat to select projects",
    )
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--no-python-inspection", action="store_true")
    parser.add_argument(
        "--repeat", type=int, default=1,
        help="Number of sequential checker processes per project (default: 1)",
    )
    parser.add_argument(
        "--isolated-cache", action="store_true",
        help="Use a new declaration cache below --output; requires --repeat >= 2",
    )
    args = parser.parse_args()
    if args.timeout <= 0 or args.repeat < 1:
        parser.error("--timeout and --repeat must be positive")
    if args.python_timeout is not None and args.python_timeout <= 0:
        parser.error("--python-timeout must be positive")
    if args.isolated_cache and args.repeat < 2:
        parser.error("--isolated-cache requires --repeat >= 2 for empty/reused passes")
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(args.manifest.read_text())
    entries = manifest.get("projects", []) if isinstance(manifest, dict) else manifest
    rows = []
    failed = False
    for item in entries:
        name = item["repo"].split("/")[-1]
        if args.project and not {name, item["repo"]}.intersection(args.project):
            continue
        root = args.corpus / name
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip()
        expected = item.get("sha") or item.get("revision")
        if expected and sha != expected:
            raise SystemExit(f"{name}: expected {expected}, got {sha}")
        tracked = subprocess.check_output(
            ["git", "ls-files", "-z"], cwd=root, text=True
        ).split("\0")
        files = item.get("files") or sorted(
            p for p in tracked if Path(p).suffix in {".lpy", ".cljc"}
        )
        if not files:
            raise SystemExit(f"{name}: no tracked Basilisp sources")
        command = [args.blt, "check", "--repro", "--format", "json"]
        cache = None
        if args.isolated_cache:
            cache = Path(tempfile.mkdtemp(prefix=f"{name}-cache-", dir=args.output))
            command.extend(["--cache", "true", "--cache-dir", str(cache)])
        else:
            command.extend(["--cache", "false"])
        if args.python:
            command.extend(["--python", args.python])
        if args.python_timeout is not None:
            command.extend(["--python-timeout", str(args.python_timeout)])
        if args.no_python_inspection:
            command.append("--no-python-inspection")
        command.extend(files)
        row = {
            "repo": item["repo"], "sha": sha, "files": len(files),
            "bytes": sum((root / path).stat().st_size for path in files),
            "command": command, "runs": [], "findings_stable": True,
            "python_timeout_seconds": (
                args.python_timeout if args.python_timeout is not None else 5.0
            ),
            "benchmark_notes": [
                "Processes run sequentially; unrelated host workloads are not controlled.",
                "Compiler and OS caches are not cleared; an empty declaration cache is not a cold compiler.",
                "Child CPU includes waited subprocess descendants, excluding audit JSON processing.",
            ],
            "compiler_cache_disabled_env": os.environ.get(
                "BASILISP_DO_NOT_CACHE_NAMESPACES"
            ),
        }
        if cache:
            row["cache_dir"] = str(cache)
        baseline = None
        for index in range(args.repeat):
            run = {
                "run": index + 1,
                "cache_before": cache_snapshot(cache) if cache else {"state": "disabled"},
            }
            print(json.dumps({
                "event": "start", "repo": item["repo"], "files": len(files),
                "run": index + 1, "cache": run["cache_before"]["state"],
            }), flush=True)
            result = run_command(command, root, args.timeout)
            suffix = "" if index == 0 else f".run-{index + 1}"
            json_path = args.output / f"{name}{suffix}.json"
            stderr_path = args.output / f"{name}{suffix}.stderr"
            json_path.write_text(result.pop("stdout"), encoding="utf-8")
            stderr_path.write_text(result.pop("stderr"), encoding="utf-8")
            run.update(result)
            run["output"] = str(json_path)
            run["stderr"] = str(stderr_path)
            run["cache_after"] = cache_snapshot(cache) if cache else {"state": "disabled"}
            run["valid"] = False
            try:
                payload = json.loads(json_path.read_text(encoding="utf-8"))
                run["summary"] = payload["summary"]
                run["counts"] = dict(
                    collections.Counter(f["type"] for f in payload["findings"])
                )
                run["valid"] = (
                    not run["timeout"] and run["exit"] in {0, 2, 3}
                    and payload["summary"]["files"] == len(files)
                )
                findings = json.dumps(payload["findings"], sort_keys=True)
                if baseline is None:
                    baseline = findings
                run["findings_match_first"] = findings == baseline
                row["findings_stable"] &= run["findings_match_first"]
            except (ValueError, KeyError, TypeError) as exc:
                run["error"] = str(exc)
            failed |= not run["valid"] or not row["findings_stable"]
            row["runs"].append(run)
            # Keep the original one-run summary fields for existing report readers.
            if index == 0:
                row.update({
                    key: run[key] for key in (
                        "exit", "seconds", "summary", "counts", "valid", "error",
                        "child_user_seconds", "child_system_seconds", "child_cpu_seconds",
                    ) if key in run
                })
                if run["timeout"]:
                    row["timeout"] = True
            (args.output / "summary.json").write_text(
                json.dumps([*rows, row], indent=2) + "\n", encoding="utf-8"
            )
            print(json.dumps({"repo": item["repo"], **run}), flush=True)
        row["valid"] = all(run["valid"] for run in row["runs"]) and row["findings_stable"]
        row["benchmark"] = {
            state: timing_summary([
                run for run in row["runs"] if run["cache_before"]["state"] == state
            ])
            for state in dict.fromkeys(run["cache_before"]["state"] for run in row["runs"])
        }
        rows.append(row)
        (args.output / "summary.json").write_text(
            json.dumps(rows, indent=2) + "\n", encoding="utf-8"
        )
    if not rows:
        parser.error("No projects matched")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
