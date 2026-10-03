"""Compare literal project and file configuration against pinned cljfmt.

Run with: uv run python scripts/check_config.py --cljfmt /path/to/cljfmt
Requires Clojure and Java. Fixtures are original and created temporarily.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import subprocess
import tempfile
from pathlib import Path

from check_cljfmt import CLJFMT_REVISION

FILE_OPTIONS = "{:indentation? false :align-map-columns? true}"
PROJECT = '(defproject example "0.1.0" %s)'


def cases():
    yield {"name": "same-directory-dot-file-wins", "files": {
        ".cljfmt.edn": FILE_OPTIONS, "cljfmt.edn": "{:indentation? true}"}}
    yield {"name": "child-plain-file-beats-parent-dot-file", "start": "child",
           "files": {".cljfmt.edn": FILE_OPTIONS, "child/cljfmt.edn": "{:indentation? true}"}}
    yield {"name": "parent-file-discovery", "start": "child",
           "files": {".cljfmt.edn": FILE_OPTIONS}}
    yield {"name": "explicit-file-bypasses-project", "project": ":cljfmt {:indentation? true}",
           "explicit": "settings.edn", "files": {"settings.edn": FILE_OPTIONS}}
    yield {"name": "explicit-missing-file-errors", "explicit": "missing.edn", "error": True}
    for flag in ("absent", "false", "nil"):
        entry = "" if flag == "absent" else f":load-config-file? {flag}"
        yield {"name": f"project-file-loading-{flag}",
               "project": f":cljfmt {{:indentation? true {entry}}}",
               "files": {".cljfmt.edn": FILE_OPTIONS}}
    yield {"name": "project-without-cljfmt-ignores-file", "project": "",
           "files": {".cljfmt.edn": FILE_OPTIONS}}
    yield {"name": "nil-cljfmt-ignores-file", "project": ":cljfmt nil",
           "files": {".cljfmt.edn": FILE_OPTIONS}}
    yield {"name": "project-opt-in-file-then-project",
           "project": ":cljfmt {:load-config-file? true :indentation? true}",
           "files": {".cljfmt.edn": FILE_OPTIONS}}
    yield {"name": "project-opt-in-nil-valued-option-wins",
           "project": ":cljfmt {:load-config-file? true :max-column-alignment-gap nil}",
           "keys": ["max-column-alignment-gap"],
           "files": {".cljfmt.edn": "{:max-column-alignment-gap 4}"}}
    yield {"name": "project-maps-replace-file-maps",
           "project": ":cljfmt {:load-config-file? true :extra-indents {project-only [[:block 0]]}}",
           "files": {".cljfmt.edn": "{:extra-indents {file-only [[:inner 0]]}}"}}
    yield {"name": "project-empty-map-replaces-file-map",
           "project": ":cljfmt {:load-config-file? true :extra-indents {}}",
           "files": {".cljfmt.edn": "{:extra-indents {file-only [[:inner 0]]}}"}}
    yield {"name": "project-ignored-malformed-file",
           "project": ":cljfmt {}", "files": {".cljfmt.edn": "{broken"}}
    yield {"name": "project-opt-in-malformed-file-errors",
           "project": ":cljfmt {:load-config-file? true}", "error": True,
           "files": {".cljfmt.edn": "{broken"}}
    yield {"name": "project-root-discovery-from-child", "start": "child",
           "project": ":cljfmt {:load-config-file? true}",
           "files": {".cljfmt.edn": FILE_OPTIONS, "child/.cljfmt.edn": "{:indentation? true}"}}
    yield {"name": "source-and-test-paths",
           "project": ':source-paths ["library" "absent"] :test-paths ["spec"]',
           "directories": ["library", "spec"]}
    yield {"name": "nil-source-paths-retain-default",
           "project": ":source-paths nil :test-paths []"}
    yield {"name": "empty-source-and-test-paths-error", "error": True,
           "project": ":source-paths [] :test-paths []"}
    yield {"name": "missing-source-and-test-directories", "directories": [], "project": ""}
    yield {"name": "configured-project-paths",
           "project": ':cljfmt {:paths ["library"]}', "directories": ["library"]}
    yield {"name": "configured-empty-paths", "project": ":cljfmt {:paths []}"}
    yield {"name": "configured-nil-paths", "project": ":cljfmt {:paths nil}"}
    yield {"name": "file-paths-ignored-by-plugin",
           "project": ":cljfmt {:load-config-file? true}",
           "files": {".cljfmt.edn": '{:paths ["file-only"]}'}}
    yield {"name": "project-regex-rule",
           "project": ':cljfmt {:extra-indents {#"^project" [[:inner 0]]}}'}
    yield {"name": "namespaced-map-aliases",
           "project": ":cljfmt {:alias-map #:local{alias target}}",
           "keys": ["alias-map"]}
    yield {"name": "namespaced-map-edn-aliases",
           "keys": ["alias-map"], "files": {".cljfmt.edn": "{:alias-map #:local{alias target}}"}}
    yield {"name": "file-legacy-key-converted-before-project-merge",
           "project": ":cljfmt {:load-config-file? true :indents {project-only [[:block 0]]}}",
           "files": {".cljfmt.edn": "{:legacy/merge-indents? true :indents {file-only [[:inner 0]]}}"}}
    yield {"name": "file-legacy-key-replaces-extra-indents",
           "files": {".cljfmt.edn": "{:legacy/merge-indents? true :indents {legacy [[:inner 0]]} :extra-indents {extra [[:block 0]]}}"}}
    yield {"name": "invalid-file-not-rescued-by-project-override", "error": True,
           "project": ":cljfmt {:load-config-file? true :indents {project-only [[:block 0]]}}",
           "files": {".cljfmt.edn": "{:indents {42 [[:inner 0]]}}"}}
    yield {"name": "explicit-project-file", "explicit": "project.clj",
           "project": ":cljfmt {:load-config-file? true}",
           "files": {".cljfmt.edn": FILE_OPTIONS}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cljfmt", type=Path, required=True)
    args = parser.parse_args()
    checkout = args.cljfmt.resolve()
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=checkout, text=True
    ).strip()
    if revision != CLJFMT_REVISION:
        parser.error(f"Expected cljfmt {CLJFMT_REVISION}, found {revision}")
    if subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=checkout, text=True
    ).strip():
        parser.error("The cljfmt checkout must have no tracked changes.")

    import basilisp_tools  # noqa: F401
    from basilisp.lang.keyword import keyword as kw
    from basilisp.lang.map import map as lmap
    from basilisp.lang.vector import vector

    formatter = importlib.import_module("basilisp_tools.format")
    edn = importlib.import_module("basilisp.edn")
    failures = []
    with tempfile.TemporaryDirectory(prefix="blt-config-") as temporary:
        root = Path(temporary).resolve()
        specifications = []
        prepared = []
        for index, case in enumerate(cases()):
            directory = root / str(index)
            directory.mkdir()
            start = directory / case.get("start", "")
            start.mkdir(parents=True, exist_ok=True)
            for name in case.get("directories", ["src", "test"]):
                (directory / name).mkdir(parents=True, exist_ok=True)
            for name, text in case.get("files", {}).items():
                file = directory / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(text, encoding="utf-8")
            project = case.get("project")
            source = PROJECT % project if project is not None else ""
            if source:
                (directory / "project.clj").write_text(source, encoding="utf-8")
            explicit = case.get("explicit")
            path = directory / explicit if explicit else start
            mode = "lein" if project is not None and explicit in (None, "project.clj") else (
                "file" if explicit else "discover")
            keys = case.get("keys", ["indentation?", "align-map-columns?", "extra-indents"])
            if mode == "lein":
                keys += ["paths", "project-root"]
            specifications.append(
                "{:name %s :root %s :path %s :mode :%s :source %s :keys [%s]}"
                % (json.dumps(case["name"]), json.dumps(str(directory)), json.dumps(str(path)),
                   mode, json.dumps(source), " ".join(":" + key for key in keys))
            )
            prepared.append((case["name"], mode, directory, path, keys, case.get("error", False)))

        # Prevent a developer's own ancestor config from affecting empty fixtures.
        (root / ".cljfmt.edn").write_text("{}", encoding="utf-8")
        input_file, output_file = root / "input.edn", root / "output.edn"
        input_file.write_text("[" + "\n".join(specifications) + "]", encoding="utf-8")
        subprocess.run([
            "clojure", "-M", str(Path(__file__).with_suffix(".clj").resolve()),
            str(input_file), str(output_file),
        ], cwd=checkout, check=True)
        expected = edn.read_string(
            output_file.read_text(encoding="utf-8"),
            lmap({kw("default"): lambda tag, value: re.compile(value)}),
        )
        if len(expected) != len(prepared):
            raise RuntimeError("The configuration oracle returned an incomplete result.")
        for (name, mode, directory, path, keys, expect_error), oracle in zip(prepared, expected):
            oracle_error = oracle.val_at(kw("error"))
            if bool(oracle_error) != expect_error:
                failures.append((name, f"oracle error={expect_error}", str(oracle_error)))
                continue
            try:
                raw = formatter.load_config(str(path))
                merged = dict(formatter.default_options)
                merged.update(dict(raw))
                actual = {kw(key): merged[kw(key)] for key in keys if kw(key) in merged}
                if mode == "lein" and kw("paths") in actual:
                    actual[kw("paths")] = vector(
                        str((directory / str(item)).resolve()) for item in actual[kw("paths")]
                    )
                expected_options = oracle.val_at(kw("options"))
                if oracle.val_at(kw("error")) is not None:
                    failures.append((name, "expected an error", str(actual)))
                elif lmap(actual) != expected_options:
                    failures.append((name, str(expected_options), str(lmap(actual))))
            except Exception as error:
                if oracle.val_at(kw("error")) is None:
                    failures.append((name, str(oracle.val_at(kw("options"))), repr(error)))
    for name, expected, actual in failures:
        print(f"{name}:\n  cljfmt: {expected}\n  blt:    {actual}")
    print(f"cljfmt configuration: {len(prepared) - len(failures)}/{len(prepared)} matched.")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
