"""Run the reviewed calc test suite before and after formatting a temporary copy.

Fetch the pinned EnigmaCurry/calc entry in public_projects.json first. This
executes upstream code; the broader static corpus is intentionally not executed.
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from check_public_format import run_command, source_symlinks, tracked_sources


def copy_runtime_checkout(checkout, target):
    """Execute only tracked checkout files, excluding leftover pytest hooks/config."""
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=checkout, text=True,
    ).split("\0")
    tracked = [name for name in tracked if name]
    if source_symlinks(checkout, tracked):
        raise ValueError("Tracked runtime symlinks are unsupported")
    target.mkdir()
    for name in tracked:
        source = checkout / name
        # Sparse source-only checkouts deliberately omit non-source assets.
        if source.is_file():
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)


def passing_tests(report):
    root = ET.parse(report).getroot()
    tests = root.findall(".//testcase")
    if not tests or any(test.find(kind) is not None for test in tests
                        for kind in ("error", "failure", "skipped")):
        raise ValueError("Expected a nonempty, fully passing upstream suite")
    identities = [(test.get("classname"), test.get("name")) for test in tests]
    if len(set(identities)) != len(identities):
        raise ValueError("Duplicate upstream test identities")
    return set(identities)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--blt", type=Path, default=Path(sys.executable).parent / "blt")
    parser.add_argument("--basilisp", type=Path, default=Path(sys.executable).parent / "basilisp")
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    entries = json.loads(Path(__file__).with_name("public_projects.json").read_text())
    entry = next(item for item in entries if item["repo"] == "EnigmaCurry/calc")
    checkout = args.corpus.resolve() / "calc"
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=checkout, text=True,
    ).strip()
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=checkout, text=True,
    )
    if revision != entry["sha"] or dirty:
        parser.error("calc checkout differs from the reviewed pinned revision")
    files = tracked_sources(checkout)
    if source_symlinks(checkout, files):
        parser.error("Source symlinks are unsupported")
    report = {"repo": entry["repo"], "sha": revision, "runs": [], "passed": False}
    try:
        with tempfile.TemporaryDirectory(prefix="blt-public-runtime-") as directory:
            target = Path(directory) / "calc"
            copy_runtime_checkout(checkout, target)
            test = [str(args.basilisp.resolve()), "test", "-p", "src", "-p", "test",
                    "--include-unsafe-path=false", "test/calc", "-q"]
            commands = [
                ("before", [*test, f"--junitxml={args.output / 'before.xml'}"]),
                ("format", [str(args.blt.resolve()), "format", *files]),
                ("check-format", [str(args.blt.resolve()), "format", "--check", *files]),
                ("after", [*test, f"--junitxml={args.output / 'after.xml'}"]),
            ]
            for stage, command in commands:
                result = {"stage": stage, **run_command(command, target, args.timeout)}
                report["runs"].append(result)
                (args.output / f"{stage}.log").write_text(result["stdout"] + result["stderr"])
                print(f"calc {stage}: exit {result['code']}, {result['seconds']:.2f}s", flush=True)
                if result["code"] != 0:
                    raise ValueError(f"Upstream runtime audit failed during {stage}")
            before = passing_tests(args.output / "before.xml")
            after = passing_tests(args.output / "after.xml")
            if before != after:
                raise ValueError("Formatting changed the collected upstream tests")
            report.update(passed=True, tests=len(before))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        report["error"] = str(error)
    finally:
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    if not report["passed"]:
        parser.exit(1, report["error"] + "\n")
    print(f"Same {report['tests']} upstream tests passed before and after formatting.")


if __name__ == "__main__":
    main()
