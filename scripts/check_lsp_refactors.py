"""Compare refactor forms with actual pinned clojure-lsp implementations.

Run: uv run python scripts/check_lsp_refactors.py --clojure-lsp /path/to/clojure-lsp
Requires Clojure and Java. Fixtures are original; upstream code is not vendored.
This checks selected refactor form parity, ignoring whitespace and equivalent core
namespace qualification. It does not claim exact text or complete LSP parity.
Separate executable counterexamples check value and evaluation-count preservation;
unsafe upstream rewrites are not treated as a compatibility requirement.
"""

from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import tempfile
from pathlib import Path

CLOJURE_LSP_REVISION = "8ad65c1d681d2fc9022b3854f6dcaf1677d29631"
ORACLE_DEPS = '{:deps {org.clojure/clojure {:mvn/version "1.12.5"} rewrite-clj/rewrite-clj {:mvn/version "1.2.55"}}}'


def cases():
    for command, sources in [
        ("thread-first", ["(+ 1 2)", "(inc 1)", "(+ (* 2 3) 4)", "(assoc {:a 1} :b 2)"]),
        ("thread-last", ["(+ 1 2)", "(inc 1)", "(+ 4 (* 2 3))", "(map inc [1 2])"]),
        ("thread-first-all", ["(+ (* (- 8 2) 3) 4)", "(inc (inc 1))", "(assoc (dissoc {:a 1} :a) :b 2)"]),
        ("thread-last-all", ["(+ 4 (* 3 (- 8 2)))", "(inc (inc 1))", "(vec (map inc (range 3)))"]),
        ("unwind-thread", ["(-> 2 inc)", "(-> 2 (* 3) (+ 4))", "(->> 2 (* 3) (+ 4))", "(-> {:a 1} :a inc)"]),
        ("unwind-all", ["(-> 2 inc inc)", "(-> 2 (* 3) (+ 4))", "(->> 2 (* 3) (+ 4))", "(-> {:a 1} :a inc)"]),
        ("cycle-coll", ["[1 2]", "{:a 1}", "#{1 2}", "(1 2)"]),
        ("get-in-more", ["(:a {:a 1})", "(:a {:a 1} 0)", "(get (:a {:a {:b 2}}) :b)", "(get-in (:a {:a {:b {:c 3}}}) [:b :c])"]),
        ("get-in-all", ["(:b (:a {:a {:b 2}}))", "(get (:b (:a {:a {:b {:c 3}}})) :c)", "(get-in (:a {:a {:b {:c 3}}}) [:b :c])"]),
        ("get-in-less", ["(get {:a 1} :a)", "(get {:a 1} :a 0)", "(get-in {:a {:b 2}} [:a :b])", "(get-in {:a {:b {:c 3}}} [:a :b :c])", "(get-in {:a {:b 2}} [:a :b] 0)"]),
        ("get-in-none", ["(get {:a 1} :a)", "(get-in {:a {:b 2}} [:a :b])", "(get-in {:a {:b {:c 3}}} [:a :b :c])", "(get-in {:a {:b 2}} [:a :b] 0)"]),
    ]:
        for index, source in enumerate(sources):
            yield {"name": f"{command}-{index}", "source": source, "command": command, "offset": 0}
    for keep in (False, True):
        for command in ("thread-first", "thread-last", "thread-first-all", "thread-last-all"):
            yield {"name": f"{command}-keep-parens-{keep}", "source": "(inc (inc 1))",
                   "command": command, "offset": 0,
                   "settings": {"keep-parens-when-threading?": keep}}
    for kind in ("list", "vector", "set", "map"):
        yield {"name": f"change-coll-{kind}", "source": "[1 2]", "command": "change-coll", "offset": 0, "args": [kind]}
    # This also exercises the actual upstream edit range application in context.
    for command, source, selected in [
        ("thread-first-all", "(let [x 1]\n  (+ (* x 2) 3))", "(+"),
        ("thread-last-all", "(let [x 1]\n  (+ 3 (* 2 x)))", "(+"),
        ("unwind-all", "(let [x 1]\n  (-> x inc inc))", "(->"),
        ("get-in-less", "(let [m {:a {:b 2}}]\n  (get-in m [:a :b]))", "(get-in"),
    ]:
        yield {"name": f"{command}-nested-range", "source": source, "command": command, "offset": source.index(selected)}


def preservation_cases():
    # Each complete source returns its value plus any evaluation observations.
    # No external state, imports, or I/O is executed by these authored fixtures.
    for name, command, source, selected in [
        ("thread-first-evaluation-order", "thread-first-all",
         "(let [events (atom []) a (fn [] (swap! events conj :a) 2) b (fn [] (swap! events conj :b) 3)] [(+ (a) (b)) @events])", "(+"),
        ("thread-last-evaluation-order", "thread-last-all",
         "(let [events (atom []) a (fn [] (swap! events conj :a) 2) b (fn [] (swap! events conj :b) 3)] [(+ (a) (b)) @events])", "(+"),
        ("thread-special-form", "thread-first",
         "(if false (throw (ex-info \"never\" {})) 3)", "(if"),
        ("thread-nested-special-form", "thread-first-all",
         "(inc (if false (throw (ex-info \"never\" {})) 3))", "(inc"),
        ("unwind-nil-short-circuit", "unwind-all",
         "(some-> nil inc inc)", "(some->"),
        ("unwind-side-effect-count", "unwind-all",
         "(let [events (atom [])] [(-> (do (swap! events conj :once) 2) inc inc) @events])", "(->"),
        ("get-keyword-default", "get-in-none", "(get-in {} [:a :b] 7)", "(get-in"),
        ("get-numeric-key", "get-in-less", "(get-in [{:a 2}] [0 :a])", "(get-in"),
        ("get-default-eager-evaluation", "get-in-none",
         "(let [events (atom [])] [(get-in {:a {:b 2}} [:a :b] (do (swap! events conj :default) 7)) @events])", "(get-in"),
        ("get-nested-default", "get-in-all",
         "(get (get {} :a {:b 7}) :b)", "(get"),
        ("ordinary-call-is-not-get", "get-in-more", "(inc 2)", "(inc"),
        ("inline-binding-evaluation-count", "inline-symbol",
         "(let [events (atom []) x (do (swap! events conj :once) 2)] [[x x] @events])", "x (do"),
        ("introduce-let-capture", "introduce-let",
         "(let [x 2] (+ x 3))", "(+"),
    ]:
        case = {"name": name, "command": command, "source": source, "offset": source.index(selected)}
        if name == "introduce-let-capture":
            case["args"] = ["x"]
        yield case


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clojure-lsp", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    checkout = args.clojure_lsp.resolve()
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
    if revision != CLOJURE_LSP_REVISION:
        parser.error(f"Expected clojure-lsp {CLOJURE_LSP_REVISION}, found {revision}")
    if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"],
                               cwd=checkout, text=True).strip():
        parser.error("The clojure-lsp checkout must have no tracked changes.")

    import basilisp_tools  # noqa: F401
    from basilisp.lang.keyword import keyword as kw
    from basilisp.lang.map import map as lmap
    from basilisp.lang.vector import vector

    lsp = importlib.import_module("basilisp_tools.lsp")
    analyzer = importlib.import_module("basilisp_tools.analyzer")
    syntax = importlib.import_module("basilisp_tools.syntax")
    edn = importlib.import_module("basilisp.edn")
    core = importlib.import_module("basilisp.core")

    def value(mapping, name, default=None):
        return mapping.val_at(kw(name), default)

    def canonical(source):
        tree = syntax.parse(source)
        if len(value(tree, "diagnostics")):
            raise ValueError(f"Refactor produced invalid syntax: {source}")

        def node_form(node):
            kind = str(value(node, "kind"))
            children = tuple(node_form(child) for child in syntax.forms(node))
            if children:
                return kind, children
            text = value(node, "text", "")
            if kind == ":symbol":
                for prefix in ("basilisp.core/", "clojure.core/"):
                    if text.startswith(prefix):
                        text = text[len(prefix):]
            return kind, text

        return tuple(node_form(node) for node in syntax.forms(value(tree, "root")))

    def transform(case):
        source = case["source"]
        analysis = analyzer.analyze(source, lmap({kw("filename"): "/tmp/blt-refactor-oracle.lpy"}))
        if "settings" in case:
            analysis = analysis.assoc(kw("settings"), lmap({kw(k): v for k, v in case["settings"].items()}))
        edits = list(lsp.refactor_edits(source, analysis, case["command"], case["offset"],
                                        vector(case.get("args", []))) or [])
        for start, end, replacement in sorted(edits, key=lambda edit: (edit[0], edit[1]), reverse=True):
            source = source[:start] + replacement + source[end:]
        return source

    prepared, results = list(cases()), []
    with tempfile.TemporaryDirectory(prefix="blt-lsp-refactors-") as temporary:
        base = Path(temporary).resolve()
        input_file, output_file = base / "input.edn", base / "output.edn"
        specifications = []
        for case in prepared:
            fields = {**case, "args": case.get("args", [])}
            def encode(value):
                if isinstance(value, dict):
                    return "{" + " ".join(f":{k} {encode(v)}" for k, v in value.items()) + "}"
                return json.dumps(value)
            specifications.append("{" + " ".join(f":{key} {encode(value)}" for key, value in fields.items()) + "}")
        input_file.write_text("[" + "\n".join(specifications) + "]", encoding="utf-8")
        subprocess.run(["clojure", "-Srepro", "-Sdeps", ORACLE_DEPS, "-M",
                        str(Path(__file__).with_suffix(".clj").resolve()), str(checkout),
                        str(input_file), str(output_file)], cwd=base, check=True)
        expected = edn.read_string(output_file.read_text(encoding="utf-8"))
        if len(expected) != len(prepared):
            raise RuntimeError("The refactor oracle returned incomplete results.")
        for case, oracle in zip(prepared, expected):
            expected_source = value(oracle, "source")
            expected_error = value(oracle, "error")
            try:
                actual = transform(case)
                matched = expected_error is None and canonical(actual) == canonical(expected_source)
            except Exception as error:
                actual, matched = repr(error), False
            results.append({"name": case["name"], "matched": matched,
                            "expected": expected_error or expected_source, "actual": actual})

    preservation = []
    for case in preservation_cases():
        try:
            actual = transform(case)
            canonical(actual)
            before, after = core.load_string(case["source"]), core.load_string(actual)
            matched = before == after
            detail = f"{before!s} -> {after!s}"
        except Exception as error:
            actual, matched, detail = locals().get("actual"), False, repr(error)
        preservation.append({"name": case["name"], "matched": matched, "actual": actual, "evaluation": detail})
    failures = [result for result in results if not result["matched"]]
    for result in failures:
        print(f"{result['name']}:\n  clojure-lsp: {result['expected']}\n  blt:         {result['actual']}")
    unsafe = [result for result in preservation if not result["matched"]]
    for result in unsafe:
        print(f"Preservation {result['name']}: {result['evaluation']}\n  blt: {result['actual']}")
    print(f"clojure-lsp refactor forms: {len(results) - len(failures)}/{len(results)} matched.")
    print(f"Executable value/evaluation guards: {len(preservation) - len(unsafe)}/{len(preservation)} passed.")
    if args.report:
        args.report.write_text(json.dumps({"upstream_revision": revision, "cases": results,
                                          "preservation": preservation}, indent=2) + "\n", encoding="utf-8")
    return int(bool(failures or unsafe))


if __name__ == "__main__":
    raise SystemExit(main())
