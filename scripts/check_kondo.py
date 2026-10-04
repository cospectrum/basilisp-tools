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

CONFIG_CASES = [('exclude-symbol', 'missing kept', '{:linters {:unresolved-symbol {:exclude [missing]}}}'),
 ('exclude-symbol-regex',
  '?query kept',
  '{:linters {:unresolved-symbol {:exclude-patterns ["^\\\\?"]}}}'),
 ('duplicate-default', 'missing missing', '{}'),
 ('duplicate-report',
  'missing missing',
  '{:linters {:unresolved-symbol {:report-duplicates true}}}'),
 ('duplicate-namespace', '(absent/a) (absent/b)', '{}'),
 ('unused-binding-regex',
  '(let [this-that 1 kept 2] :ok)',
  '{:linters {:unused-binding {:exclude-patterns ["^this"]}}}'),
 ('unused-private-exclude',
  '(defn- hidden [] :ok) (defn- kept [] :ok)',
  '{:linters {:unused-private-var {:exclude [sample/hidden]}}}'),
 ('unused-ns-exclude',
  '(ns sample (:require [unused.lib :as u]))',
  '{:linters {:unused-namespace {:exclude [unused.lib]}}}'),
 ('unused-ns-regex',
  '(ns sample (:require [unused.lib :as u]))',
  '{:linters {:unused-namespace {:exclude [".*lib$"]}}}'),
 ('unused-refer',
  '(ns sample (:require [unused.lib :refer [a b]]))',
  '{:linters {:unused-referred-var {:exclude {unused.lib [a]}} :unused-namespace {:level :off}}}'),
 ('ns-groups',
  'missing',
  '{:ns-groups [{:pattern "^sam" :name samples}] :config-in-ns {samples {:ignore '
  '[:unresolved-symbol]}}}'),
 ('ns-groups-filename',
  'missing',
  '{:ns-groups [{:filename-pattern "sample\\\\.clj$" :name samples}] :config-in-ns {samples '
  '{:ignore [:unresolved-symbol]}}}'),
 ('ns-groups-specific',
  'missing',
  '{:ns-groups [{:pattern "^sam" :name samples}] :config-in-ns {samples {:ignore '
  '[:unresolved-symbol]} sample {:linters {:unresolved-symbol {:level :warning}}}}}'),
 ('ns-deep-merge',
  'missing kept',
  '{:linters {:unresolved-symbol {:exclude [missing]}} :config-in-ns {sample {:linters '
  '{:unresolved-symbol {:exclude [kept]}}}}}'),
 ('ns-replace',
  'missing kept',
  '{:linters {:unresolved-symbol {:exclude [missing]}} :config-in-ns {sample {:linters '
  '{:unresolved-symbol {:exclude ^:replace [kept]}}}}}'),
 ('ns-metadata',
  "(ns sample {:clj-kondo/config '{:linters {:unresolved-symbol {:level :off}}}}) missing",
  '{}'),
 ('ns-metadata-ignore', '(ns sample {:clj-kondo/ignore [:unresolved-symbol]}) missing', '{}'),
 ('ns-name-metadata-ignore', '(ns ^{:clj-kondo/ignore true} sample) missing', '{}'),
 ('call-config',
  '(defn wrap [x] x) (wrap missing) kept',
  '{:config-in-call {sample/wrap {:ignore [:unresolved-symbol]}}}'),
 ('call-config-scope-unused',
  '(identity (let [x 1] :ok)) (let [y 1] :ok)',
  '{:config-in-call {clojure.core/identity {:ignore [:unused-binding]}}}'),
 ('call-config-alias',
  '(ns sample (:require [clojure.core :as c]))\n(c/identity missing) kept',
  '{:config-in-call {clojure.core/identity {:ignore [:unresolved-symbol]}}}'),
 ('call-exclude',
  '(identity missing) missing',
  '{:linters {:unresolved-symbol {:exclude [(clojure.core/identity [missing])]}}}'),
 ('call-exclude-all',
  '(identity [missing other]) kept',
  '{:linters {:unresolved-symbol {:exclude [(clojure.core/identity)]}}}'),
 ('skip-args-linter',
  '(identity (inc)) (inc)',
  '{:linters {:invalid-arity {:skip-args [clojure.core/identity]}}}'),
 ('skip-args', '(identity (inc missing other)) (inc)', '{:skip-args [clojure.core/identity]}'),
 ('skip-comments', '(comment missing (inc)) kept', '{:skip-comments true}'),
 ('tag-default', '#data [missing (inc)]', '{}'),
 ('tag-config',
  '#data [missing (inc)]',
  '{:config-in-tag {data {:linters {:unresolved-symbol {:level :error}}}}}'),
 ('discard-ignore', '#_:clj-kondo/ignore (inc) (inc)', '{}'),
 ('discard-ignore-specific', '#_{:clj-kondo/ignore [:invalid-arity]} (inc missing 2)', '{}'),
 ('fn-destructure-keys',
  '(fn [{:keys [x]}] :ok) (let [{:keys [y]} {}] :ok)',
  '{:linters {:unused-binding {:exclude-destructured-keys-in-fn-args true}}}'),
 ('destructure-as',
  '(let [{:keys [x] :as m} {} [y :as all] []] [x y])',
  '{:linters {:unused-binding {:exclude-destructured-as true}}}'),
 ('lint-as',
  '(defmacro my-let [& args] args) (my-let [x 1] x)',
  '{:lint-as {sample/my-let clojure.core/let}}'),
 ('lint-as-local-shadow',
  '(defmacro my-let [& args] args) (let [my-let (fn [x] x)] (my-let 1))',
  '{:lint-as {sample/my-let clojure.core/let}}'),
 ('inline-macro',
  "(defmacro with-x {:clj-kondo/config '{:ignore [:unresolved-symbol]}} [& args] args) (with-x "
  'missing)',
  '{}'),
 ('nested-destructure',
  '(fn [{:keys [x]}] (let [{:keys [nested]} {}] :ok))',
  '{:linters {:unused-binding {:exclude-destructured-keys-in-fn-args true}}}'),
 ('discard-binding', '(let [#_:clj-kondo/ignore unused 1] :ok)', '{}'),
 ('call-macro-report',
  '(defmacro wrap [& args] args) (wrap missing)',
  '{:config-in-call {sample/wrap {:linters {:unresolved-symbol {:level :warning}}}}}'),
 ('unresolved-ns-group',
  '(absent/value)',
  '{:ns-groups [{:pattern "^abs" :name optional}] :linters {:unresolved-namespace {:exclude '
  '[optional]}}}'),
 ('unresolved-var-exclusion',
  '(sample/missing)',
  '{:linters {:unresolved-var {:exclude [sample]}}}'),
 ('macro-inline-lint-as',
  "(defmacro my-let {:clj-kondo/lint-as 'clojure.core/let} [& args] args) (my-let [x 1] x)",
  '{}'),
 ('fn-explicit-map-destructure',
  '(fn [{x :x}] :ok)',
  '{:linters {:unused-binding {:exclude-destructured-keys-in-fn-args true}}}'),
 ('defmulti-args',
  '(defmulti f (fn [x y] x))',
  '{:linters {:unused-binding {:exclude-defmulti-args true}}}'),
 ('defmulti-nested-fn',
  '(defmulti f (fn [x y] (fn [z] :ok)))',
  '{:linters {:unused-binding {:exclude-defmulti-args true}}}'),
 ('thread-call-config',
  '(-> 1 (+ missing))',
  '{:config-in-call {clojure.core/+ {:ignore [:unresolved-symbol]}}}'),
 ('cond-thread-call-config',
  '(cond-> 1 true (inc 2))',
  '{:config-in-call {clojure.core/inc {:ignore [:invalid-arity]}}}'),
 ('thread-call-arity',
  '(-> 1 (identity missing))',
  '{:config-in-call {clojure.core/identity {:ignore true}}}')]



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
        for case in [*CASES, *CONFIG_CASES]:
            name, body, *configs = case
            config = configs[0] if configs else "{}"
            source = body if body.startswith("(ns ") else "(ns sample)\n" + body
            filename = "sample.clj"
            oracle = subprocess.run([
                args.clj_kondo, "--lint", "-", "--lang", "clj", "--filename", filename,
                "--cache", "false", "--repro", "--config-dir", str(config_directory),
                "--config", config, "--config", "{:output {:format :json}}",
            ], input=source, text=True, capture_output=True, env=environment, cwd=temporary)
            if oracle.returncode not in (0, 2, 3):
                raise RuntimeError(f"clj-kondo failed for {name}: {oracle.stderr}")
            expected = normalize(json.loads(oracle.stdout)["findings"])
            try:
                with patch.dict(os.environ, environment, clear=True):
                    result = checker.run(lmap({
                        kw("stdin"): source.replace("clojure.core", "basilisp.core"),
                        kw("filename"): filename, kw("config"): checker.read_config(config),
                        kw("config-dir"): str(config_directory), kw("repro"): True,
                        kw("python-inspection?"): False,
                    }))
                actual = normalize(plain_findings(result))
                status = checker.exit_status(result)
                comparison = {
                    "name": name, "source": source, "config": config, "expected": expected, "actual": actual,
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
