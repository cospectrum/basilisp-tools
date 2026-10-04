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


    yield {"name": "clj-file-ignored-by-default", "files": {".cljfmt.clj": FILE_OPTIONS}}
    yield {"name": "clj-file-enabled", "read_clj": True,
           "files": {".cljfmt.clj": FILE_OPTIONS}}
    yield {"name": "plain-clj-file-enabled", "read_clj": True,
           "files": {"cljfmt.clj": FILE_OPTIONS}}
    yield {"name": "explicit-clj-file-without-discovery-flag", "explicit": "chosen.clj",
           "files": {"chosen.clj": FILE_OPTIONS}}
    yield {"name": "dot-edn-precedes-dot-clj", "read_clj": True,
           "files": {".cljfmt.edn": FILE_OPTIONS, ".cljfmt.clj": "{:indentation? true}"}}
    yield {"name": "dot-clj-precedes-plain-edn-when-enabled", "read_clj": True,
           "files": {".cljfmt.clj": FILE_OPTIONS, "cljfmt.edn": "{:indentation? true}"}}
    yield {"name": "dot-clj-falls-back-to-plain-edn-when-disabled",
           "files": {".cljfmt.clj": "{:indentation? true}", "cljfmt.edn": FILE_OPTIONS}}
    yield {"name": "plain-edn-precedes-plain-clj", "read_clj": True,
           "files": {"cljfmt.edn": FILE_OPTIONS, "cljfmt.clj": "{:indentation? true}"}}
    yield {"name": "child-clj-beats-parent-edn-when-enabled", "read_clj": True, "start": "child",
           "files": {"child/.cljfmt.clj": FILE_OPTIONS, ".cljfmt.edn": "{:indentation? true}"}}
    yield {"name": "child-clj-ignored-parent-edn-used", "start": "child",
           "files": {"child/.cljfmt.clj": "{:indentation? true}", ".cljfmt.edn": FILE_OPTIONS}}
    yield {"name": "lein-does-not-enable-clj-discovery",
           "project": ":cljfmt {:load-config-file? true :read-clj-config-files? true}",
           "read_clj": True, "files": {".cljfmt.clj": FILE_OPTIONS}}
    yield {"name": "explicit-clj-regex", "explicit": "chosen.clj",
           "files": {"chosen.clj": '{:extra-indents {#"^with-" [[:inner 0]]}}'}}
    yield {"name": "edn-tagged-regex", "files": {
        ".cljfmt.edn": '{:extra-indents {#re "^with-" [[:inner 0]]}}'}}
    yield {"name": "edn-does-not-read-clj-regex", "error": True,
           "files": {".cljfmt.edn": '{:extra-indents {#"^with-" [[:inner 0]]}}'}}
    yield {"name": "clj-does-not-read-edn-re-tag", "error": True,
           "explicit": "chosen.clj",
           "files": {"chosen.clj": '{:extra-indents {#re "^with-" [[:inner 0]]}}'}}
    yield {"name": "clj-map-metadata", "explicit": "chosen.clj",
           "files": {"chosen.clj": '^:replace {:indentation? false}'}}
    yield {"name": "clj-symbol-metadata", "explicit": "chosen.clj",
           "files": {"chosen.clj": '{:extra-indents {^String action [[:inner 0]]}}'}}
    yield {"name": "clj-auto-keyword-namespace", "explicit": "chosen.clj", "keys": ["alias-map"],
           "files": {"chosen.clj": '{:alias-map {::local remote}}'}}
    yield {"name": "clj-auto-map-namespace", "explicit": "chosen.clj",
           "files": {"chosen.clj": '{:extra-indents #::{action [[:inner 0]]}}'}}
    yield {"name": "clj-unresolved-keyword-alias", "explicit": "chosen.clj", "error": True,
           "files": {"chosen.clj": '{:alias-map {::absent/local remote}}'}}
    yield {"name": "edn-rejects-auto-keywords", "error": True,
           "files": {".cljfmt.edn": '{:alias-map {::local remote}}'}}
    for extension in ("edn", "clj"):
        for literal in ("nil", "{} {:indentation? false}", "{} (", "{}]", "#_{} " + FILE_OPTIONS):
            yield {"name": f"{extension}-reader-{literal}", "explicit": f"chosen.{extension}",
                   "files": {f"chosen.{extension}": literal}}
    yield {"name": "empty-edn-is-error", "error": True, "files": {".cljfmt.edn": ""}}
    yield {"name": "empty-clj-is-error", "error": True, "explicit": "chosen.clj",
           "files": {"chosen.clj": ""}}


    for filename in ("chosen.txt", "chosen"):
        yield {"name": f"unsupported-config-extension-{filename}", "explicit": filename, "error": True,
               "files": {filename: "{}"}}

    booleans = ["indent-line-comments?", "indentation?", "normalize-newlines-at-file-end?",
                "insert-missing-whitespace?", "remove-blank-lines-in-forms?",
                "remove-consecutive-blank-lines?", "remove-multiple-non-indenting-spaces?",
                "remove-surrounding-whitespace?", "remove-trailing-whitespace?",
                "sort-ns-references?", "split-keypairs-over-multiple-lines?",
                "align-map-columns?", "align-form-columns?", "align-binding-columns?",
                "align-single-column-lines?", "blank-lines-separate-alignment?",
                "ansi?", "parallel?", "quiet?", "verbose?", "read-clj-config-files?"]
    for value in ("true", "false"):
        yield {"name": f"all-boolean-options-{value}", "keys": booleans,
               "files": {".cljfmt.edn": "{" + " ".join(f":{key} {value}" for key in booleans) + "}"}}
    for style in ("community", "cursive", "zprint"):
        yield {"name": f"function-argument-style-{style}", "keys": ["function-arguments-indentation"],
               "files": {".cljfmt.edn": "{:function-arguments-indentation :" + style + "}"}}
    yield {"name": "file-selection-and-width-options", "keys": [
        "paths", "project-root", "file-pattern", "max-column-alignment-gap", "max-column-alignment-width"],
           "files": {".cljfmt.edn": '{:paths ["lib" "spec"] :project-root "project" :file-pattern #re ".*lpy" :max-column-alignment-gap 3 :max-column-alignment-width 80}'}}
    yield {"name": "custom-form-rules-and-aliases", "keys": [
        "aligned-forms", "extra-aligned-forms", "blank-line-forms", "extra-blank-line-forms", "alias-map", "refer-map"],
           "files": {".cljfmt.edn": '{:aligned-forms {bind #{0}} :extra-aligned-forms {pair #{1}} :blank-line-forms {cond :all} :extra-blank-line-forms {other :all} :alias-map {local external} :refer-map {item external/item}}'}}


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
                "{:name %s :root %s :path %s :mode :%s :source %s :keys [%s] :read-clj-config-files? %s}"
                % (json.dumps(case["name"]), json.dumps(str(directory)), json.dumps(str(path)),
                   mode, json.dumps(source), " ".join(":" + key for key in keys),
                   "true" if case.get("read_clj") else "false")
            )
            prepared.append((case["name"], mode, directory, path, keys, case.get("error", False), case.get("read_clj", False)))

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
        for (name, mode, directory, path, keys, expect_error, read_clj), oracle in zip(prepared, expected):
            oracle_error = oracle.val_at(kw("error"))
            if bool(oracle_error) != expect_error:
                failures.append((name, f"oracle error={expect_error}", str(oracle_error)))
                continue
            try:
                raw = formatter.load_config(str(path), lmap({kw("read-clj-config-files?"): read_clj}))
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
