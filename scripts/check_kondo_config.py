"""Compare original configuration fixtures against the pinned clj-kondo binary.

Checks diagnostics, exit status, file counts, and configured text rendering.
Run inside the compatibility Nix shell: uv run python scripts/check_kondo_config.py.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stderr
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

from check_kondo import KONDO_VERSION, normalize

SOURCE = "(ns sample)\n(let [unused 1] :ok)\nmissing\n"


def level(value):
    return "{:linters {:unresolved-symbol {:level :" + value + "}}}"


CASES = [
    {"name": "defaults"},
    {"name": "nil-project-config", "files": {"project/.clj-kondo/config.edn": "nil"}},
    {"name": "empty-project-config", "files": {"project/.clj-kondo/config.edn": "; empty"}},
    {"name": "first-form-only", "files": {"project/.clj-kondo/config.edn": "{} " + level("off")}},
    {"name": "trailing-invalid-form", "files": {"project/.clj-kondo/config.edn": "{} ("}},
    {"name": "invalid-project-config", "files": {"project/.clj-kondo/config.edn": "{:broken"}},
    {"name": "top-level-missing-include", "files": {"project/.clj-kondo/config.edn": '#include "missing.edn"'}},
    {"name": "home", "files": {"home/clj-kondo/config.edn": level("off")}, "repro": False},
    {"name": "repro-skips-home", "files": {"home/clj-kondo/config.edn": level("off")}},
    {"name": "project-over-home", "repro": False, "files": {
        "home/clj-kondo/config.edn": level("off"),
        "project/.clj-kondo/config.edn": level("warning")}},
    {"name": "env-over-project", "files": {
        "project/.clj-kondo/config.edn": level("off"), "extra/config.edn": level("warning")}},
    {"name": "cli-over-env", "files": {"extra/config.edn": level("off")}, "configs": [level("warning")]},
    {"name": "repeated-cli", "configs": [level("off"), level("warning")]},
    {"name": "cli-file", "files": {"project/override.edn": level("warning")}, "configs": ["override.edn"]},
    {"name": "replace-skips-home", "repro": False, "files": {
        "home/clj-kondo/config.edn": level("off"),
        "project/.clj-kondo/config.edn": "{:config-paths ^:replace []}"}},
    {"name": "imported-before-project", "files": {
        "project/.clj-kondo/vendor/config.edn": level("off"),
        "project/.clj-kondo/config.edn": '{:config-paths ["vendor"] :linters {:unresolved-symbol {:level :warning}}}'}},
    {"name": "import-order", "files": {
        "project/.clj-kondo/one/config.edn": level("off"),
        "project/.clj-kondo/two/config.edn": level("warning"),
        "project/.clj-kondo/config.edn": '{:auto-load-configs false :config-paths ["one" "two"]}'}},
    {"name": "nested-import", "files": {
        "project/.clj-kondo/vendor/config.edn": '{:config-paths ["inner"]}',
        "project/.clj-kondo/vendor/inner/config.edn": level("warning"),
        "project/.clj-kondo/config.edn": '{:auto-load-configs false :config-paths ["vendor"]}'}},
    {"name": "missing-import", "files": {
        "project/.clj-kondo/config.edn": '{:config-paths ["absent"]}'}},
    {"name": "autoload-one-level", "files": {"project/.clj-kondo/vendor/config.edn": level("off")}},
    {"name": "autoload-two-level", "files": {"project/.clj-kondo/vendor/library/config.edn": level("off")}},
    {"name": "autoload-three-level-ignored", "files": {"project/.clj-kondo/a/b/c/config.edn": level("off")}},
    {"name": "autoload-import-directory", "files": {"project/.clj-kondo/imports/vendor/library/config.edn": level("off")}},
    {"name": "autoload-disabled", "files": {
        "project/.clj-kondo/vendor/library/config.edn": level("off"),
        "project/.clj-kondo/config.edn": "{:auto-load-configs false}"}},
    {"name": "autoload-sorted", "files": {
        "project/.clj-kondo/a/lib/config.edn": level("off"),
        "project/.clj-kondo/z/lib/config.edn": level("warning")}},
    {"name": "nearest-config", "discover": True, "cwd": "project/nested", "files": {
        ".clj-kondo/config.edn": level("off"),
        "project/.clj-kondo/config.edn": level("warning")}},
    {"name": "include-map", "files": {
        "project/.clj-kondo/config.edn": '{:linters #include "linters.edn"}',
        "project/.clj-kondo/linters.edn": "{:unresolved-symbol {:level :off}}"}},
    {"name": "include-nested-relative", "files": {
        "project/.clj-kondo/config.edn": '#include "nested/full.edn"',
        "project/.clj-kondo/nested/full.edn": '{:linters #include "linters.edn"}',
        "project/.clj-kondo/nested/linters.edn": "{:unresolved-symbol {:level :warning}}"}},
    {"name": "include-missing", "files": {
        "project/.clj-kondo/config.edn": '{:linters #include "missing.edn"}'}},
    {"name": "replace-output", "configs": [
        '{:output {:exclude-files ["sample"]}}', '{:output ^:replace {:summary false}}']},
    {"name": "replace-exclusions", "configs": [
        '{:output {:exclude-files ["sample"]}}', '{:output {:exclude-files ^:replace []}}']},
    {"name": "exclude-input", "configs": ['{:exclude-files "sample"}']},
    {"name": "exclude-output", "configs": ['{:output {:exclude-files ["sample"]}}']},
    {"name": "include-output", "configs": ['{:output {:include-files ["other"]}}']},
    {"name": "include-output-regex", "configs": ['{:output {:include-files ["sample[.]clj"]}}']},
    {"name": "no-summary", "configs": ["{:output {:summary false}}"]},
    {"name": "canonical-stdin", "configs": ["{:output {:canonical-paths true}}"]},
    {"name": "linter-name", "text": True, "configs": ["{:output {:linter-name true}}"]},
    {"name": "linter-name-alias", "text": True, "configs": ["{:output {:show-rule-name-in-message true}}"]},
    {"name": "output-pattern", "text": True, "configs": [
        '{:output {:pattern "{{filename}}:{{row}}:{{col}}-{{end-row}}:{{end-col}} {{LEVEL}} {{level}} {{type}} {{message}}" :linter-name true}}']},
    {"name": "text-report-level", "text": True, "report_level": "error"},
    {"name": "json-report-level-retains-findings", "report_level": "error"},
]


@contextmanager
def working_directory(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clj-kondo", default="clj-kondo")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    version = subprocess.check_output([args.clj_kondo, "--version"], text=True).strip()
    if version != f"clj-kondo v{KONDO_VERSION}":
        parser.error(f"Expected clj-kondo v{KONDO_VERSION}, found {version}")

    import basilisp_tools  # noqa: F401
    from basilisp.lang.keyword import keyword as kw
    from basilisp.lang.map import map as lmap
    from basilisp.lang.vector import vector

    checker = importlib.import_module("basilisp_tools.check")
    comparisons = []
    with tempfile.TemporaryDirectory(prefix="blt-kondo-config-") as temporary:
        for case in CASES:
            root = Path(temporary) / case["name"]
            cwd = root / case.get("cwd", "project")
            config_dir = root / "project/.clj-kondo"
            cwd.mkdir(parents=True)
            config_dir.mkdir(parents=True, exist_ok=True)
            for name, content in case.get("files", {}).items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            environment = dict(os.environ, XDG_CONFIG_HOME=str(root / "home"),
                               CLJ_KONDO_EXTRA_CONFIG_DIR=str(root / "extra"))
            repro = case.get("repro", True)
            report_level = case.get("report_level", "info")
            configs = case.get("configs", []) + [
                "{:output {:format :text :summary false}}" if case.get("text") else "{:output {:format :json}}"
            ]
            command = [args.clj_kondo, "--lint", "-", "--lang", "clj", "--filename", "sample.clj",
                       "--cache", "false", "--report-level", report_level]
            options = {kw("stdin"): SOURCE, kw("filename"): "sample.clj",
                       kw("cwd"): str(cwd), kw("repro"): repro,
                       kw("config"): vector(configs), kw("python-inspection?"): False,
                       kw("report-level"): kw(report_level)}
            if repro:
                command.append("--repro")
            if not case.get("discover"):
                command.extend(["--config-dir", str(config_dir)])
                options[kw("config-dir")] = str(config_dir)
            for config in configs:
                command.extend(["--config", config])
            oracle = subprocess.run(command, input=SOURCE, text=True, capture_output=True,
                                    env=environment, cwd=cwd)
            if oracle.returncode not in (0, 2, 3):
                raise RuntimeError(f"{case['name']}: clj-kondo failed: {oracle.stderr}")
            diagnostics = io.StringIO()
            with patch.dict(os.environ, environment, clear=True), working_directory(cwd), redirect_stderr(diagnostics):
                result = checker.run(lmap(options))
                actual_text = checker.output(result)
            if case.get("text"):
                expected = oracle.stdout.replace("clojure.core/", "basilisp.core/")
                actual = actual_text
            else:
                def normalized_output(text):
                    data = json.loads(text)
                    data["findings"] = normalize(data["findings"])
                    if "summary" in data:
                        data["summary"].pop("duration", None)
                    return data
                expected = normalized_output(oracle.stdout)
                actual = normalized_output(actual_text)
            status = checker.exit_status(result)
            expected_warning = "WARNING:" in oracle.stderr
            actual_warning = "WARNING:" in diagnostics.getvalue()
            equal = (actual == expected and status == oracle.returncode
                     and expected_warning == actual_warning)
            comparisons.append({"name": case["name"], "match": equal,
                                "expected": expected, "actual": actual,
                                "expected_status": oracle.returncode, "actual_status": status,
                                "expected_warning": expected_warning, "actual_warning": actual_warning})
            print(("PASS" if equal else "FAIL") + " " + case["name"])
    if args.report:
        args.report.write_text(json.dumps(comparisons, indent=2, ensure_ascii=False) + "\n",
                               encoding="utf-8")
    matched = sum(item["match"] for item in comparisons)
    print(f"{matched}/{len(comparisons)} clj-kondo configuration cases match")
    return 0 if matched == len(comparisons) else 1


if __name__ == "__main__":
    raise SystemExit(main())
