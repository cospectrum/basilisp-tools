"""Compare shared-language diagnostics with the pinned clj-kondo executable.

Run with: uv run python scripts/check_kondo.py
The compatibility Nix shell supplies clj-kondo. These fixtures are original;
Python interoperability has separate tests because clj-kondo does not analyze it.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

KONDO_VERSION = "2026.08.04"
FIELDS = ("type", "level", "message", "row", "col", "end-row", "end-col")
CASES = [
    ("unresolved-value", "missing\n"),
    ("unicode-column", '(do "😀" missing)\n'),
    ("unicode-multiline-column", '(do "😀\n😀" missing)\n'),
    ("unresolved-call", "(missing 1)\n"),
    ("unresolved-namespace", "(absent/value 1)\n"),
    ("core-zero-arity", "(inc)\n"),
    ("core-extra-argument", "(inc 1 2)\n"),
    ("user-function-arity", "(defn take-one [value] value)\n(take-one)\n"),
    ("unused-let-binding", "(let [unused 1] :ok)\n"),
    ("unused-function-argument", "(defn run [unused] :ok)\n"),
    ("unused-vector-binding", "(let [[used spare] [1 2]] used)\n"),
    ("unused-map-binding", "(let [{:keys [used spare]} {:used 1 :spare 2}] used)\n"),
    ("redefined-var", "(def value 1)\n(def value 2)\n"),
    ("unresolved-binding-initializer", "(let [value value] value)\n"),
    ("quoted-symbols", "'(absent/value missing)\n"),
    ("discarded-code", "#_(missing 1)\n42\n"),
    ("ignored-binding", "(let [_ignored 1] :ok)\n"),
    ("sequential-bindings", "(let [value 1 result (inc value)] result)\n"),
    ("captured-binding", "(let [value 1] ((fn [] value)))\n"),
    ("variadic-function", "(defn prepend [head & tail] (cons head tail))\n(prepend 1 2)\n"),
    ("local-function", "(let [f (fn [value] value)] (f 1))\n"),
    ("multi-arity-function", "(defn value ([] 0) ([x] x))\n(value)\n(value 1)\n"),
]


def normalize(findings):
    # The core namespace is the only intentional language-specific difference.
    result = []
    for finding in findings:
        item = {field: finding.get(field) for field in FIELDS}
        item["message"] = item["message"].replace("clojure.core/", "basilisp.core/")
        result.append(item)
    return sorted(result, key=lambda item: (
        item["row"] or 0, item["col"] or 0, item["type"], item["message"]
    ))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clj-kondo", default="clj-kondo", help="Path to the pinned executable.")
    parser.add_argument("--report", type=Path, help="Write all comparisons as JSON.")
    args = parser.parse_args()
    version = subprocess.check_output([args.clj_kondo, "--version"], text=True).strip()
    if version != f"clj-kondo v{KONDO_VERSION}":
        parser.error(f"Expected clj-kondo v{KONDO_VERSION}, found {version}")

    import basilisp_tools  # noqa: F401
    from basilisp.lang.keyword import keyword as kw
    from basilisp.lang.map import map as lmap

    checker = importlib.import_module("basilisp_tools.check")

    def plain_findings(result):
        return [
            {str(key).removeprefix(":"): (
                str(value).removeprefix(":") if str(key) in (":type", ":level") else value
            ) for key, value in finding.items()}
            for finding in result.val_at(kw("findings"))
        ]

    comparisons = []
    with tempfile.TemporaryDirectory(prefix="blt-kondo-") as temporary:
        config_directory = Path(temporary) / ".clj-kondo"
        config_directory.mkdir()
        (config_directory / "config.edn").write_text(
            "{:config-paths ^:replace []}", encoding="utf-8"
        )
        environment = dict(os.environ)
        environment.pop("CLJ_KONDO_EXTRA_CONFIG_DIR", None)
        for name, body in CASES:
            source = "(ns sample)\n" + body
            filename = "sample.clj"
            oracle = subprocess.run([
                args.clj_kondo, "--lint", "-", "--lang", "clj", "--filename", filename,
                "--cache", "false", "--repro", "--config-dir", str(config_directory),
                "--config", "{:output {:format :json}}",
            ], input=source, text=True, capture_output=True, env=environment, cwd=temporary)
            if oracle.returncode not in (0, 2, 3):
                raise RuntimeError(f"clj-kondo failed for {name}: {oracle.stderr}")
            expected = normalize(json.loads(oracle.stdout)["findings"])
            try:
                with patch.dict(os.environ, environment, clear=True):
                    result = checker.run(lmap({
                        kw("stdin"): source, kw("filename"): filename,
                        kw("config-dir"): str(config_directory), kw("repro"): True,
                        kw("python-inspection?"): False,
                    }))
                actual = normalize(plain_findings(result))
                status = checker.exit_status(result)
                comparison = {
                    "name": name, "source": source, "expected": expected, "actual": actual,
                    "expected_exit": oracle.returncode, "actual_exit": status,
                    "match": expected == actual and oracle.returncode == status,
                }
            except Exception as error:
                comparison = {
                    "name": name, "source": source, "expected": expected,
                    "exception": repr(error), "match": False,
                }
            comparisons.append(comparison)
    failed = [case for case in comparisons if not case["match"]]
    if args.report:
        args.report.write_text(json.dumps({
            "clj_kondo_version": KONDO_VERSION, "cases": comparisons,
        }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for case in failed:
        print(json.dumps(case, ensure_ascii=False, sort_keys=True))
    print(f"clj-kondo diagnostics: {len(comparisons) - len(failed)}/{len(comparisons)} matched.")
    return int(bool(failed))


if __name__ == "__main__":
    raise SystemExit(main())
