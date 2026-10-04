"""Compare LSP settings with actual functions from pinned clojure-lsp sources.

Run: uv run python scripts/check_lsp_config.py --clojure-lsp /path/to/clojure-lsp
Requires Clojure and Java. Fixtures are original; upstream code is not vendored.
This checks settings semantics, not full clojure-lsp feature compatibility.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import subprocess
import tempfile
from pathlib import Path

CLOJURE_LSP_REVISION = "8ad65c1d681d2fc9022b3854f6dcaf1677d29631"
# Match the dependency used by the pinned upstream checkout, without pulling
# Java indexing, LSP transport, analyzers, or the rest of that application's deps.
ORACLE_DEPS = '{:deps {org.clojure/clojure {:mvn/version "1.12.5"} medley/medley {:mvn/version "1.4.0"}}}'


def cases():
    for name, a, b in [
        ("nested-map-merge", "{:a {:x 1 :keep 2}}", "{:a {:x 3 :added 4}}"),
        ("nil-right-keeps-map", "{:a 1}", "nil"),
        ("nil-values-replace", "{:a true :b 1}", "{:a nil :b false}"),
        ("empty-values-replace", '{:a "yes" :b 1}', '{:a "" :b 0}'),
        ("vector-concatenation", "{:a [1 2]}", "{:a [2 3]}"),
        ("list-concatenation", "{:a (1 2)}", "{:a (2 3)}"),
        ("vector-list-concatenation", "{:a [1 2]}", "{:a (3 4)}"),
        ("set-union", "{:a #{1 2}}", "{:a #{2 3}}"),
        ("nested-collections", "{:a {:b [1]}}", "{:a {:b [2]}}"),
        ("collection-scalar-replacement", "{:a [1] :b 1}", "{:a false :b [2]}"),
    ]:
        yield {"name": name, "mode": "deep-merge", "a": a, "b": b}
    for name, a, b in [
        ("indent-rule-replaced", "{:cljfmt {:indents {foo [[:block 0]]}}}",
         "{:cljfmt {:indents {foo [[:inner 1]] bar [[:block 2]]}}}"),
        ("indent-unrelated-rules-retained", "{:cljfmt {:indents {foo [[:block 0]]}}}",
         "{:cljfmt {:indents {bar [[:inner 1]]}}}"),
        ("ignore-paths-distinct", '{:paths-ignore-regex ["a" "b"]}',
         '{:paths-ignore-regex ["a" "c"]}'),
        ("indent-branch-preserves-duplicate-paths", '{:cljfmt {:indents {}} :paths-ignore-regex ["a"]}',
         '{:paths-ignore-regex ["a"]}'),
        ("project-specs-first-path-wins", '{:project-specs [{:project-path "a" :id 1}]}',
         '{:project-specs [{:project-path "a" :id 2} {:project-path "b" :id 3}]}'),
        ("ignore-branch-preserves-duplicate-project-specs", '{:paths-ignore-regex ["a"] :project-specs [{:project-path "x" :id 1}]}',
         '{:project-specs [{:project-path "x" :id 2}]}'),
        ("nil-indent-replacement", "{:cljfmt {:indents {foo [[:block 0]]}}}",
         "{:cljfmt {:indents nil}}"),
    ]:
        yield {"name": name, "mode": "merge", "a": a, "b": b}
    for name, client in [
        ("client-defaults", "{}"),
        ("client-preserves-false", '{:document-formatting? false :document-range-formatting? false :dependency-scheme ""}'),
        ("client-nil-defaults", "{:document-formatting? nil :document-range-formatting? nil :dependency-scheme nil :cljfmt-config-path nil}"),
        ("client-source-paths", '{:source-paths ["src" ":test" "src" :ignored 5 nil]}'),
        ("client-source-aliases", '{:source-aliases ["dev" ":test" :dev 9 nil]}'),
        ("client-sync-kind", '{:text-document-sync-kind ":incremental"}'),
        ("client-indent-json-values", '{:cljfmt {:indents {"foo" [["block" 0]] "#^with-" [["inner" 0]]}}}'),
        ("client-linter-json-values", '{:linters {:example {:level "warning" :exclude ["demo"]}}}'),
    ]:
        yield {"name": name, "mode": "clean", "client": client}
    # The pinned upstream has a traversal bug that merges the root file once per
    # ancestor instead of reading ancestor files. These exact parity fixtures use
    # idempotent maps; collection merges above exercise the real helper directly.
    # blt intentionally follows documented ancestor discovery, tested separately.
    yield {"name": "load-empty", "mode": "load"}
    yield {"name": "home-global-config", "mode": "load", "files": {
        "home/.lsp/config.edn": "{:hover {:hide-arity-on-same-line? true}}"}}
    yield {"name": "xdg-global-preferred", "mode": "load", "files": {
        "home/.lsp/config.edn": "{:document-formatting? false}",
        "xdg/clojure-lsp/config.edn": "{:document-formatting? true}"}}
    yield {"name": "default-xdg-home-location", "mode": "load", "xdg_unset": True,
           "files": {"home/.config/clojure-lsp/config.edn": "{:document-formatting? false}"}}
    yield {"name": "empty-xdg-directory-hides-home", "mode": "load",
           "directories": ["xdg/clojure-lsp"],
           "files": {"home/.lsp/config.edn": "{:document-formatting? false}"}}
    yield {"name": "client-global-project-forced-precedence", "mode": "load",
           "client": "{:a 1 :b 1 :c 1 :nested {:keep 1 :override 1}}",
           "force": "{:c 4 :nested {:override 4}}", "files": {
               "home/.lsp/config.edn": "{:a 2 :b 2 :c 2}",
               "project/.lsp/config.edn": "{:b 3 :c 3 :nested {:added 3}}"}}
    yield {"name": "startup-global-collections-replace-client", "mode": "load",
           "client": '{:source-paths ["client"] :custom {:items [1 2]}}', "files": {
               "home/.lsp/config.edn": '{:source-paths ["global"] :custom {:items [3]}}'}}
    yield {"name": "startup-forced-collections-replace-files", "mode": "load",
           "force": '{:source-paths ["forced"] :custom {:items []}}', "files": {
               "home/.lsp/config.edn": '{:source-paths ["global"] :custom {:items [3]}}'}}
    yield {"name": "startup-global-indent-rules-replace-client", "mode": "load",
           "client": '{:cljfmt {:indents {"foo" [["block" 0]]}}}', "files": {
               "home/.lsp/config.edn": '{:cljfmt {:indents {foo [[:inner 1]]}}}'}}
    yield {"name": "project-nil-and-false-replace", "mode": "load",
           "client": "{:a true :document-formatting? true}", "files": {
               "project/.lsp/config.edn": "{:a nil :document-formatting? false}"}}
    yield {"name": "file-top-level-string-keys", "mode": "load", "files": {
        "project/.lsp/config.edn": '{"document-formatting?" false "nested" {"key" "value"}}'}}
    yield {"name": "file-regex-reader", "mode": "load", "files": {
        "project/.lsp/config.edn": '{:custom-regex #re "^generated/"}'}}
    yield {"name": "malformed-project-config-ignored", "mode": "load", "files": {
        "project/.lsp/config.edn": "{:broken"}}
    yield {"name": "malformed-global-keeps-project", "mode": "load", "files": {
        "home/.lsp/config.edn": "{:broken",
        "project/.lsp/config.edn": "{:document-formatting? false}"}}


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

    settings = importlib.import_module("basilisp_tools.lsp")
    edn = importlib.import_module("basilisp.edn")
    reader_options = lmap({kw("default"): lambda tag, value: re.compile(value)})

    def read(text):
        return edn.read_string(text, reader_options)

    results = []
    with tempfile.TemporaryDirectory(prefix="blt-lsp-config-") as temporary:
        base = Path(temporary).resolve()
        prepared, specifications = [], []
        for index, case in enumerate(cases()):
            directory = base / str(index)
            for path in ["project", "home", "xdg", *case.get("directories", [])]:
                (directory / path).mkdir(parents=True, exist_ok=True)
            for name, text in case.get("files", {}).items():
                path = directory / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            values = {"name": json.dumps(case["name"]), "mode": ":" + case["mode"],
                      "a": case.get("a", "nil"), "b": case.get("b", "nil"),
                      "client": case.get("client", "{}"), "force": case.get("force", "{}"),
                      "root": json.dumps(str(directory / "project")),
                      "home": json.dumps(str(directory / "home")),
                      "xdg": "nil" if case.get("xdg_unset") else json.dumps(str(directory / "xdg"))}
            specifications.append("{" + " ".join(f":{key} {value}" for key, value in values.items()) + "}")
            prepared.append((case, directory))
        input_file, output_file = base / "input.edn", base / "output.edn"
        input_file.write_text("[" + "\n".join(specifications) + "]", encoding="utf-8")
        subprocess.run(["clojure", "-Srepro", "-Sdeps", ORACLE_DEPS, "-M",
                        str(Path(__file__).with_suffix(".clj").resolve()), str(checkout),
                        str(input_file), str(output_file)], cwd=base, check=True)
        expected = read(output_file.read_text(encoding="utf-8"))
        if len(expected) != len(prepared):
            raise RuntimeError("The LSP configuration oracle returned incomplete results.")
        for (case, directory), oracle in zip(prepared, expected):
            expected_settings = oracle.val_at(kw("settings"))
            expected_error = oracle.val_at(kw("error"))
            try:
                if case["mode"] == "deep-merge":
                    actual = settings.deep_merge_settings(read(case["a"]), read(case["b"]))
                elif case["mode"] == "merge":
                    actual = settings.merge_settings(read(case["a"]), read(case["b"]))
                elif case["mode"] == "clean":
                    actual = settings.clean_client_settings(read(case["client"]))
                else:
                    actual = settings.load_settings(lmap({
                        kw("root"): str(directory / "project"),
                        kw("home"): str(directory / "home"),
                        kw("xdg-config-home"): None if case.get("xdg_unset") else str(directory / "xdg"),
                        kw("client-settings"): read(case.get("client", "{}")),
                        kw("force-settings"): read(case.get("force", "{}")),
                    }))
                matched = expected_error is None and actual == expected_settings
                actual_text = str(actual)
            except Exception as error:
                # No fixture expects an error; matching unrelated exceptions
                # must never count as configuration compatibility.
                matched = False
                actual_text = repr(error)
            results.append({"name": case["name"], "matched": matched,
                            "expected": str(expected_error if expected_error is not None else expected_settings),
                            "actual": actual_text})
    failures = [result for result in results if not result["matched"]]
    for result in failures:
        print(f"{result['name']}:\n  clojure-lsp: {result['expected']}\n  blt:         {result['actual']}")
    print(f"clojure-lsp configuration: {len(results) - len(failures)}/{len(results)} matched.")
    print("Intentional deviation: blt reads ancestor .lsp/config.edn files; pinned upstream repeats the root file.")
    if args.report:
        args.report.write_text(json.dumps({"upstream_revision": revision, "cases": results}, indent=2) + "\n",
                               encoding="utf-8")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
