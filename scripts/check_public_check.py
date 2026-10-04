"""Run blt check against pinned public projects without treating their findings as failures.

Clone scripts/public_projects.json revisions below --corpus first. Results include
raw findings, stderr, exact commands, revisions and timings; classification needs
human review because public suites intentionally contain invalid example code.
"""

import argparse
import collections
import json
import subprocess
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).with_name("public_projects.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--blt", default="blt")
    parser.add_argument("--python", help="Dependency interpreter passed to blt check")
    parser.add_argument(
        "--project",
        action="append",
        help="Repository basename; repeat to select projects",
    )
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--no-python-inspection", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(args.manifest.read_text())
    entries = manifest.get("projects", []) if isinstance(manifest, dict) else manifest
    rows = []
    failed = False
    for item in entries:
        name = item["repo"].split("/")[-1]
        if args.project and name not in args.project:
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
        command = [args.blt, "check", "--repro", "--cache", "false", "--format", "json"]
        if args.python:
            command.extend(["--python", args.python])
        if args.no_python_inspection:
            command.append("--no-python-inspection")
        command.extend(files)
        row = {
            "repo": item["repo"],
            "sha": sha,
            "files": len(files),
            "command": command,
        }
        print(
            json.dumps({"event": "start", "repo": item["repo"], "files": len(files)}),
            flush=True,
        )
        start = time.perf_counter()
        try:
            result = subprocess.run(
                command,
                cwd=root,
                capture_output=True,
                text=True,
                timeout=args.timeout,
                check=False,
            )
            (args.output / f"{name}.json").write_text(result.stdout)
            (args.output / f"{name}.stderr").write_text(result.stderr)
            row["exit"] = result.returncode
            payload = json.loads(result.stdout)
            row["summary"] = payload["summary"]
            row["counts"] = dict(
                collections.Counter(f["type"] for f in payload["findings"])
            )
            row["valid"] = result.returncode in {0, 2, 3} and payload["summary"][
                "files"
            ] == len(files)
            failed |= not row["valid"]
        except subprocess.TimeoutExpired as exc:
            row["timeout"] = True
            (args.output / f"{name}.partial").write_bytes(exc.stdout or b"")
            failed = True
        except (ValueError, KeyError, TypeError) as exc:
            row["error"] = str(exc)
            failed = True
        row["seconds"] = round(time.perf_counter() - start, 3)
        rows.append(row)
        (args.output / "summary.json").write_text(json.dumps(rows, indent=2) + "\n")
        print(json.dumps(row), flush=True)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
