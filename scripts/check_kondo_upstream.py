"""Replay literal lint! inputs/config from a pinned clj-kondo upstream test suite.

The native clj-kondo release supplies complete diagnostic/exit-code expectations.
This is a static extraction, not execution of the upstream Clojure assertions.
Dynamic fixtures and non-Clojure dialects are counted explicitly in the report.
"""

from __future__ import annotations

import argparse
from collections import Counter
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from unittest.mock import patch

from check_kondo import FIELDS, KONDO_VERSION

KONDO_REVISION = "6267607412e55ed3b91710ef542f3fee2ad7aa05"
NAMESPACES = ("core", "test", "template", "string", "set")
NAMESPACE_PATTERN = re.compile(
    r"(?<![\w./-])clojure\.(" + "|".join(NAMESPACES) + r")(?![\w.-])"
)


def adapt(source):
    return NAMESPACE_PATTERN.sub(lambda match: f"basilisp.{match[1]}", source)


def normalize(findings, source=None):
    result = []
    lines = source.splitlines() if source is not None else []
    for finding in findings:
        item = {field: finding.get(field) for field in FIELDS}
        item["message"] = adapt(item["message"])
        for row_key, column_key in (("row", "col"), ("end-row", "end-col")):
            row, column = item[row_key], item[column_key]
            if row and column and row <= len(lines):
                line = lines[row - 1]
                item[column_key] += sum(
                    len(line[:match.end()].encode("utf-16-le")) // 2 < column
                    for match in NAMESPACE_PATTERN.finditer(line)
                )
        result.append(item)
    return sorted(result, key=lambda item: (
        item["row"] or 0, item["col"] or 0, item["type"], item["message"]
    ))


def validate_capture(captured):
    if not captured["cases"]:
        raise ValueError("Upstream extraction produced no cases.")
    ids = [case["id"] for case in captured["cases"] + captured["skipped"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Upstream extraction produced duplicate case IDs.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument("--clj-kondo", default="clj-kondo")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--baseline", type=Path, help="Require previously matching cases to stay exact.")
    parser.add_argument("--write-baseline", type=Path, help="Record case coverage and exact matches.")
    args = parser.parse_args()
    checkout = args.checkout.resolve()
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
    if revision != KONDO_REVISION:
        parser.error(f"Expected clj-kondo source {KONDO_REVISION}, found {revision}")
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=checkout, text=True
    ).strip()
    if dirty:
        parser.error("The clj-kondo checkout must have no tracked changes.")
    version = subprocess.check_output([args.clj_kondo, "--version"], text=True).strip()
    if version != f"clj-kondo v{KONDO_VERSION}":
        parser.error(f"Expected clj-kondo v{KONDO_VERSION}, found {version}")

    import basilisp_tools  # noqa: F401
    from basilisp.lang.keyword import keyword as kw
    from basilisp.lang.map import map as lmap

    checker = importlib.import_module("basilisp_tools.check")

    def field(value, name, default=None):
        return value.val_at(kw(name), default)

    comparisons = []
    environment = dict(os.environ)
    environment.pop("CLJ_KONDO_EXTRA_CONFIG_DIR", None)
    with tempfile.TemporaryDirectory(prefix="blt-kondo-upstream-") as temporary:
        output = Path(temporary) / "captured.json"
        subprocess.run([
            "clojure", "-Srepro", "-Sdeps", '{:paths []}', "-M",
            str(Path(__file__).with_name("capture_kondo_tests.clj").resolve()),
            str(checkout), str(output),
        ], cwd=temporary, check=True)
        captured = json.loads(output.read_text(encoding="utf-8"))
        validate_capture(captured)
        base_config = captured["base-config"]
        config_directory = Path(temporary) / ".clj-kondo"
        config_directory.mkdir()
        (config_directory / "config.edn").write_text("{:config-paths ^:replace []}\n", encoding="utf-8")
        print(f"Extracted {len(captured['cases'])} literal Clojure inputs; "
              f"skipped {len(captured['skipped'])} calls.", flush=True)
        for index, case in enumerate(captured["cases"], 1):
            source, config, filename = (case[key] for key in ("source", "config", "filename"))
            oracle = subprocess.run([
                args.clj_kondo, "--lint", "-", "--lang", "clj", "--filename", filename,
                "--cache", "false", "--repro", "--config-dir", str(config_directory),
                "--config", base_config, "--config", config, "--config", "{:output {:format :json}}",
            ], input=source, text=True, capture_output=True, env=environment, cwd=temporary, timeout=30)
            if oracle.returncode not in (0, 2, 3):
                raise RuntimeError(f"clj-kondo failed for {case['id']}: {oracle.stderr}")
            try:
                expected = normalize(json.loads(oracle.stdout)["findings"], source)
            except (ValueError, KeyError) as error:
                raise RuntimeError(f"Invalid clj-kondo JSON for {case['id']}: "
                                   f"{oracle.stdout[:1000]!r}; {oracle.stderr[:1000]!r}") from error
            comparison = {
                key: case[key] for key in ("id", "file", "test", "line", "source", "config", "filename")
            }
            comparison.update(expected=expected, expected_exit=oracle.returncode)
            try:
                with patch.dict(os.environ, environment, clear=True):
                    result = checker.run(lmap({
                        kw("stdin"): adapt(source), kw("filename"): filename,
                        kw("config"): checker.merge_config(
                            checker.read_config(base_config), checker.read_config(adapt(config))
                        ),
                        kw("config-dir"): str(config_directory), kw("repro"): True,
                        kw("python-inspection?"): False,
                    }))
                actual = normalize([
                    {str(key).removeprefix(":"): (
                        str(value).removeprefix(":") if str(key) in (":type", ":level") else value
                    ) for key, value in finding.items()}
                    for finding in field(result, "findings")
                ])
                status = checker.exit_status(result)
                comparison.update(actual=actual, actual_exit=status,
                                  match=actual == expected and status == oracle.returncode)
            except Exception as error:
                comparison.update(exception=repr(error), match=False)
            comparisons.append(comparison)
            if index % 250 == 0:
                print(f"Compared {index} upstream inputs.", flush=True)
        skipped = captured["skipped"]
    matched = [case["id"] for case in comparisons if case["match"]]
    mismatches = [case for case in comparisons if not case["match"]]
    exceptions = [case for case in comparisons if "exception" in case]
    regressions = []
    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        if baseline["revision"] != revision:
            parser.error("Baseline revision differs from upstream source revision.")
        regressions = sorted(set(baseline["matched"]) - set(matched))
        if len(comparisons) != baseline["cases"] or len(skipped) != baseline["skipped"]:
            regressions.append("extraction coverage changed")
    counts = {
        "extracted": len(comparisons), "matched": len(matched), "mismatched": len(mismatches),
        "exceptions": len(exceptions), "skipped": len(skipped), "regressions": len(regressions),
    }
    report = {"revision": revision, "version": KONDO_VERSION, "counts": counts,
              "skip_reasons": dict(Counter(item["reason"] for item in skipped)),
              "regressions": regressions, "cases": comparisons, "skipped": skipped}
    if args.report:
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.write_baseline and not exceptions:
        args.write_baseline.write_text(json.dumps({
            "revision": revision, "cases": len(comparisons), "skipped": len(skipped), "matched": matched,
        }, indent=2) + "\n", encoding="utf-8")
    print("clj-kondo upstream: " + json.dumps(counts, sort_keys=True))
    if args.write_baseline and exceptions:
        print("Baseline was not written because checker exceptions occurred.")
    for case in exceptions:
        print(f"  exception {case['id']}: {case['exception']}")
    for regression in regressions:
        print(f"  regression {regression}")
    return int(bool(exceptions or regressions or (mismatches and not (args.report_only or args.baseline))))


if __name__ == "__main__":
    raise SystemExit(main())
