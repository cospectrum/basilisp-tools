"""Compare analysis and SARIF records with the pinned clj-kondo executable.

The analyzer retains Basilisp namespaces and signatures. The comparison maps
basilisp.core to clojure.core and sorts arity sets, without dropping record fields.
SARIF compares results; each tool keeps its own identity and rule catalog.
"""
from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from check_kondo import KONDO_VERSION, normalize

CASES = [
    ("quoted-qualified-symbols", "'x '(a example/x)", "{:symbols true}"),
    ("quoted-symbol-aliases", "(ns sample (:require [example :as e])) '(e/f example/g)", "{:symbols true}"),
    ("discarded-quoted-symbols", "#_'example/ignored 'example/used", "{:symbols true}"),
    ("quote-call-symbols", "(quote [example/a example/b])", "{:symbols true}"),
    ("definition-callstack", "(comment (def x 1) (let [a 1] (def y a))) (def z 3)", "{:var-definitions {:callstack true}}"),
    ("referred-declarations", "(ns sample (:require [example :refer [f g] :rename {g h}])) (f) (h)", "true"),
    ("context-selector", "(ns sample) (def x :foo) x", "{:context [:app] :keywords true}"),
    ("context-true", "(ns sample) (def x :foo) x", "{:context true :keywords true}"),
    ("instance-methods", '(let [x "abc"] (.upper x) (. x lower) (. x (replace "a" "b")))', "{:instance-invocations true :var-usages false}"),
    ("instance-quoted-ignore", "'(.upper x) '( . x lower)", "{:instance-invocations true}"),
    ("destructuring-keywords", "(defn f [{:keys [a abc/b] :abc/keys [c] :as m :or {a :x}}] [a b c m])", "{:keywords true :var-usages false}"),
    ("empty-optional-analysis", "", "{:symbols true :instance-invocations true :protocol-impls true}"),
    ("protocol-record-implementation", "(ns sample) (defprotocol P (f [x])) (defrecord R [] P (f [x] x))", "{:protocol-impls true :var-usages false}"),
    ("protocol-reify-implementation", "(ns sample) (defprotocol P (f [x])) (reify P (f [x] x))", "{:protocol-impls true :var-usages false}"),
    ("protocol-type-implementation", "(ns sample) (defprotocol P (f [x])) (deftype T [] P (f [x] x))", "{:protocol-impls true :var-usages false}"),
    ("protocol-extend-type", "(ns sample) (defprotocol P (f [x])) (deftype T []) (extend-type T P (f [x] x))", "{:protocol-impls true :var-usages false}"),
    ("protocol-extend-protocol", "(ns sample) (defprotocol P (f [x])) (deftype T []) (extend-protocol P T (f [x] x))", "{:protocol-impls true :var-usages false}"),
    ("shallow-definitions", "(def x (missing)) (def y x)", "{:var-definitions {:shallow true}}"),
    ("shallow-body-metadata", "(defn f [x] [:body 'example/x (.upper x)])", "{:var-definitions {:shallow true} :var-usages false :keywords true :symbols true :instance-invocations true :locals true}"),
    ("empty-analysis", "", "true"),
    ("unresolved-variable", "missing", "true"),
    ("unresolved-call", "(missing 1)", "true"),
    ("unresolved-namespace", "(missing/f 1)", "true"),
    ("alias-value", "(ns sample (:require [example.util :as u]))\nu/f", "true"),
    ("alias-call", "(ns sample (:require [example.util :as u]))\n(u/f 1)", "true"),
    ("value-is-not-metadata", '(ns sample)\n(def x {:deprecated true :added "1"})',
     "{:var-definitions {:meta true}}"),
    ("namespaced-map-keywords", "(ns sample)\n#:other{:a 1 :_/b 2 :foo/c 3}\n#::{:k 1}",
     "{:keywords true}"),
    ("namespace", "(ns sample)", "true"),
    ("namespace-doc", '(ns sample "Documentation")', "true"),
    ("namespace-alias", "(ns sample (:require [example.util :as u]))", "true"),
    ("definition-and-use", "(ns sample)\n(def x 1)\nx", "true"),
    ("private-definition", "(ns sample)\n(def ^:private x 1)", "true"),
    ("local-analysis", "(ns sample)\n(let [x 1] x)", "{:locals true}"),
    ("nested-locals", "(ns sample)\n(let [x 1] (let [y x] (+ x y)))", "{:locals true}"),
    ("disable-var-usages", "(ns sample)\n(defn f [a] a)\n(f 1)", "{:var-usages false}"),
    ("function-arities", "(ns sample)\n(defn f ([a] a) ([a b] (+ a b)))",
     "{:var-usages false :arglists true :locals true}"),
    ("var-metadata", '(ns sample)\n(def ^{:private true :added "1" :custom 42} x 1)',
     "{:var-definitions {:meta true}}"),
    ("namespaced-var-metadata", '(def ^{:custom/flag true} x 1)', "{:var-definitions {:meta true}}"),
    ("tagged-uuid-metadata", '(def ^{:custom #uuid "12345678-1234-1234-1234-123456789abc"} x 1)', "{:var-definitions {:meta true}}"),
    ("tagged-instant-metadata", '(def ^{:custom #inst "2020-01-01"} x 1)', "{:var-definitions {:meta true}}"),
    ("ratio-metadata", '(def ^{:custom 1/2} x 1)', "{:var-definitions {:meta true}}"),
    ("decimal-metadata", '(def ^{:custom 1.5M} x 1)', "{:var-definitions {:meta true}}"),
    ("selected-var-metadata", '(ns sample)\n(def ^{:private true :added "1" :custom 42} x 1)',
     "{:var-definitions {:meta [:custom]}}"),
    ("namespace-metadata", '(ns ^{:deprecated "1" :custom 42} sample)',
     "{:namespace-definitions {:meta true}}"),
    ("selected-namespace-metadata", '(ns ^{:deprecated "1" :custom 42} sample)',
     "{:namespace-definitions {:meta [:custom]}}"),
    ("literal-keywords", "(ns sample)\n[:plain :abc/a ::local]", "{:keywords true}"),
    ("namespace-keywords", "(ns sample (:require [example.util :as u]))\n::u/key",
     "{:keywords true}"),
    ("keyword-in-definition", "(ns sample)\n(def x {:key :value})",
     "{:keywords true}"),
    ("discarded-keywords", "(ns sample)\n#_[:ignored] [:used]", "{:keywords true}"),
    ("unicode-locations", '(ns sample)\n(let [x "😀"] x)', "{:locals true}"),
    ("top-level-analysis", "(ns sample)\n(def x 1)", "true", "analysis"),
    ("analysis-precedence", "(ns sample)\n(def x 1)", "false", "analysis"),
]


def normalized(value):
    if isinstance(value, dict):
        return {key: sorted(item) if key == "fixed-arities" else normalized(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [normalized(item) for item in value]
    if isinstance(value, str):
        return value.replace("basilisp.core", "clojure.core")
    return value


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

    checker = importlib.import_module("basilisp_tools.check")
    comparisons = []
    with tempfile.TemporaryDirectory(prefix="blt-kondo-output-") as temporary:
        def compare(name, source, config, extra=(), options=None, sarif=False, filename="sample.clj"):
            command = [args.clj_kondo, "--lint", "-", "--filename", filename,
                       "--repro", "--cache", "false", "--config-dir", temporary,
                       "--config", config, *extra]
            oracle = subprocess.run(command, input=source, text=True, capture_output=True)
            if oracle.returncode not in (0, 2, 3):
                raise RuntimeError(f"{name}: clj-kondo failed: {oracle.stderr}")
            result = checker.run(lmap({
                kw("stdin"): source, kw("filename"): filename,
                kw("config-dir"): temporary, kw("repro"): True,
                kw("config"): config, kw("python-inspection?"): False,
                **(options or {}),
            }))
            expected = json.loads(oracle.stdout) if oracle.stdout.strip() else {}
            actual = json.loads(checker.output(result)) if checker.output(result).strip() else {}
            if sarif:
                assert actual["version"] == expected["version"] == "2.1.0"
                expected = expected["runs"][0]["results"]
                actual = actual["runs"][0]["results"]
            else:
                expected.pop("summary", None)
                actual.pop("summary", None)
                for data in (expected, actual):
                    if "findings" in data:
                        data["findings"] = normalize(data["findings"])
            expected, actual = normalized(expected), normalized(actual)
            status = checker.exit_status(result)
            equal = actual == expected and status == oracle.returncode
            comparisons.append({"name": name, "match": equal,
                                "expected": expected, "actual": actual,
                                "expected_status": oracle.returncode, "actual_status": status})
            print(("PASS" if equal else "FAIL") + " " + name)

        for case in CASES:
            name, source, analysis, *location = case
            config = ("{:analysis " + analysis + " :output {:analysis true :format :json}}"
                      if location else "{:output {:format :json :analysis " + analysis + "}}")
            compare(name, source, config)
        hook = "(fn [{:keys [node]}] {:node (with-meta (clj-kondo.hooks-api/list-node (cons (clj-kondo.hooks-api/token-node 'def) (rest (:children node)))) (meta node))})"
        compare("hook-definition-origin", "(declare define-value) (define-value answer 42)",
                "{:hooks {:__dangerously-allow-string-hooks__ true :analyze-call {user/define-value "
                + json.dumps(hook) + "}} :analysis {:var-usages false} :output {:format :json}}")
        for name, source, config in [
            ("sarif-clean", "(ns sample)", "{}"),
            ("sarif-error", "(ns sample)\nmissing", "{}"),
            ("sarif-warning", "(ns sample)\n(let [unused 1] nil)", "{}"),
            ("sarif-info", "(ns sample)\nmissing", "{:linters {:unresolved-symbol {:level :info}}}"),
        ]:
            compare(name, source, config[:-1] + " :output {:format :sarif}}", sarif=True)
        compare("sarif-report-level", "(ns sample)\n(let [unused 1] nil)",
                "{:output {:format :sarif}}",
                ("--report-level", "error"), {kw("report-level"): kw("error")}, sarif=True)
        for name, source in [
            ("edn-symbol-data", "{plain external/value :keyword [foo bar]}"),
            ("edn-duplicate-map", "{:a 1 :a 2}"),
            ("edn-duplicate-set", "#{:a :a}"),
            ("edn-structural-error", "{:a"),
        ]:
            compare(name, source, "{:output {:format :json}}", filename="data.edn")
        compare("edn-symbol-analysis", "{plain external/value}", "{:analysis {:symbols true} :output {:format :json}}", filename="data.edn")
        compare("skip-lint-output", "missing", "{:output {:format :json}}",
                ("--skip-lint",), {kw("skip-lint"): True})
        compare("skip-lint-analysis", "(ns sample) (def x missing)",
                "{:analysis true :output {:format :json}}",
                ("--skip-lint",), {kw("skip-lint"): True})
        source = "(ns sample)\n(def x missing)"
        oracle = subprocess.run([args.clj_kondo, "--lint", "-", "--filename", "sample.clj",
                                 "--config-dir", temporary, "--repro", "--dependencies"],
                                input=source, text=True, capture_output=True)
        actual = subprocess.run([
            sys.executable, "-c",
            "import basilisp_tools, importlib; raise SystemExit(importlib.import_module(\"basilisp_tools.cli\").main())",
            "check", "--config-dir", temporary, "--repro", "--no-python-inspection",
            "--format", "json", "--dependencies", "--filename", "sample.clj", "-"],
            input=source, text=True, capture_output=True)
        equal = actual.stdout == oracle.stdout == "" and actual.returncode == oracle.returncode == 0
        comparisons.append({"name": "dependencies-suppress-output", "match": equal,
                            "expected": oracle.stdout, "actual": actual.stdout,
                            "expected_status": oracle.returncode, "actual_status": actual.returncode})
        print(("PASS" if equal else "FAIL") + " dependencies-suppress-output")
    if args.report:
        args.report.write_text(json.dumps(comparisons, indent=2, ensure_ascii=False) + "\n",
                               encoding="utf-8")
    count = sum(item["match"] for item in comparisons)
    print(f"{count}/{len(comparisons)} clj-kondo output cases match")
    return 0 if count == len(comparisons) else 1


if __name__ == "__main__":
    raise SystemExit(main())
