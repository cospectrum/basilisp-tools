"""Run refactoring examples read directly from pinned clojure-lsp tests.

Run: uv run python scripts/check_lsp_upstream.py --clojure-lsp /path/to/clojure-lsp
No Clojure runtime is needed. The upstream test source is read as data; only the
small, explicit fixture DSL below is interpreted. No upstream code is evaluated
or vendored. Exact upstream text is the assertion. Structural similarity ignores
trivia, reader discards, and core qualification, does not prove semantic equivalence
(a core name might be shadowed), and does not turn a failed assertion into a pass.

The selected suites cover threading, collections, bindings, functions, and lookups.
Availability predicates have a separate API upstream and are counted as skipped.
Each case records whether its helper asserts document text or replacement text;
zipper-only nil inputs and edit-range assertions are also listed as skipped.
This does not exercise the LSP wire protocol or the full clojure-lsp test suite.
Use --baseline to guard existing exact passes and extraction coverage while
continuing to report known differences; execution errors always fail.
"""

from __future__ import annotations

import argparse
from collections import Counter
import importlib
import json
from pathlib import Path
import subprocess

CLOJURE_LSP_REVISION = "8ad65c1d681d2fc9022b3854f6dcaf1677d29631"
SELECTED_TESTS = {
    "lib/test/clojure_lsp/refactor/transform_test.clj": {
        "thread-test", "change-coll-test", "move-to-let-test", "introduce-let-test",
        "expand-let-test", "cycle-coll-test", "cycle-privacy-test", "demote-fn-test", "promote-fn-test",
        "unwind-thread-test", "unwind-all-test",
    },
    "lib/test/clojure_lsp/feature/thread_get_test.clj": None,
}


def capture_cases(checkout, selected_tests=None):
    import basilisp_tools  # noqa: F401
    from basilisp.lang import reader
    from basilisp.lang.keyword import keyword
    from basilisp.lang.list import PersistentList, list as llist
    from basilisp.lang.symbol import Symbol
    from basilisp.lang.vector import PersistentVector

    commands = {"thread-first", "thread-last", "thread-first-all", "thread-last-all", "change-coll",
                "introduce-let", "move-to-let", "expand-let", "cycle-coll", "cycle-privacy", "demote-fn", "promote-fn"}
    files = SELECTED_TESTS if selected_tests is None else selected_tests
    cases, skipped, suites = [], [], []
    inventory = []
    settings = {}

    def head(form):
        return str(form.first) if isinstance(form, PersistentList) and form else None

    def literal(form, environment):
        if isinstance(form, Symbol):
            if str(form) not in environment:
                raise ValueError(f"Unknown fixture binding: {form}")
            return environment[str(form)]
        if isinstance(form, PersistentList):
            if head(form) == "z/of-string":
                return {"marked": literal(form[1], environment), "repeat": 0}
            if head(form) in {"transform/unwind-thread", "transform/unwind-all"}:
                previous = literal(form[1], environment)
                return {**previous, "command": head(form).removeprefix("transform/"),
                        "repeat": previous["repeat"] + 1}
            if head(form) == "->" and head(form[2]) == "z/find-next-value":
                state = literal(form[1], environment)
                target = literal(form[2][2], environment)
                marked = state["marked"]
                if str(form[2][1]) != "z/next" or len(form) != 3 or marked.count(target) != 1:
                    raise ValueError(f"Unsupported upstream zipper position: {form}")
                offset = marked.index(target)
                return {**state, "marked": marked[:offset] + "|" + marked[offset:]}
            if head(form) == "h/code":
                return "\n".join(literal(value, environment) for value in list(form)[1:])
            if head(form) == "str":
                return "".join(str(literal(value, environment)) for value in list(form)[1:])
            if head(form) == "quote":
                return str(form[1])
            raise ValueError(f"Unsupported fixture expression: {form}")
        if isinstance(form, PersistentVector):
            return [literal(value, environment) for value in form]
        return form

    def operation(form, environment):
        command = head(form)
        if command == "->":
            steps = list(form)[2:]
            if [str(step) for step in steps] == ["h/zloc-from-code", "transform/cycle-coll", "as-string"]:
                return "cycle-coll", literal(form[1], environment), []
            if len(steps) != 1:
                raise ValueError(f"Unsupported fixture threading: {form}")
            step = steps[0]
            if isinstance(step, Symbol):
                return operation(llist([step, form[1]]), environment)
            return operation(llist([step[0], form[1], *list(step)[1:]]), environment)
        if command in commands:
            arguments = [literal(value, environment) for value in list(form)[1:]]
            return command, arguments[0], arguments[1:]
        if command in {"transform/introduce-let", "transform/move-to-let"} and form[1] is None:
            return command.removeprefix("transform/"), None, []
        if command == "transform/change-coll" and head(form[1]) == "z/of-string":
            return "change-coll", literal(form[1][1], environment), [literal(form[2], environment)]
        if command == "transform/cycle-coll" and head(form[1]) == "h/zloc-from-code":
            return "cycle-coll", literal(form[1][1], environment), []
        raise ValueError(f"Unsupported upstream operation: {form}")

    def add_case(command, marked, expected, arguments, metadata):
        if marked is None:
            skipped.append({**metadata, "reason": "Upstream nil zipper input has no document/cursor equivalent",
                            "command": command, "expected": expected})
            return
        marker = marked.find("|")
        if marked.count("|") > 1:
            raise ValueError(f"Ambiguous upstream cursor marker: {marked}")
        source = marked.replace("|", "")
        cases.append({**metadata, "id": f"{metadata['test']}:{len(cases) + 1}",
                      "command": command, "source": source, "offset": max(marker, 0),
                      "args": arguments, "expected": expected, "settings": dict(settings),
                      "output": "replacement" if command in {"change-coll", "cycle-coll", "demote-fn", "cycle-privacy"}
                      else "replacements" if command == "promote-fn" else "document"})

    def walk(form, environment, metadata):
        nonlocal settings
        kind = head(form)
        if kind == "testing":
            for child in list(form)[2:]:
                walk(child, environment, {**metadata, "context": literal(form[1], environment)})
        elif kind == "let":
            environment = dict(environment)
            bindings = list(form[1])
            for key, value in zip(bindings[::2], bindings[1::2]):
                resolved = literal(value, environment)
                if isinstance(key, Symbol):
                    environment[str(key)] = resolved
                elif isinstance(key, PersistentVector) and len(key) == 1 and isinstance(resolved, dict):
                    for local, field in key[0].items():
                        if local == keyword("keys"):
                            for name in field:
                                environment[str(name)] = resolved if str(name) == "loc" else None
                        elif field == keyword("loc"):
                            environment[str(local)] = resolved
                        else:
                            raise ValueError(f"Unsupported upstream edit binding: {key}")
                else:
                    raise ValueError(f"Unsupported upstream binding: {key}")
            for child in list(form)[2:]:
                walk(child, environment, metadata)
        elif kind == "are":
            names, assertion, values = list(form[1]), form[2], list(form)[3:]
            if not names or len(values) % len(names):
                raise ValueError(f"Invalid upstream assertion table: {form}")
            for start in range(0, len(values), len(names)):
                row = {str(name): literal(value, environment)
                       for name, value in zip(names, values[start:start + len(names)])}
                walk(llist([Symbol("is"), assertion]), {**environment, **row}, metadata)
        elif kind == "h/reset-components!":
            settings = {}
        elif kind == "swap!":
            if str(form[1]) != "(h/db*)" or str(form[2]) != "shared/deep-merge":
                raise ValueError(f"Unsupported upstream settings mutation: {form}")
            settings.update({str(key)[1:]: value for key, value in form[3][keyword("settings")].items()})
        elif kind and kind.startswith("assert-get-in-"):
            add_case(kind.removeprefix("assert-"), literal(form[2], environment),
                     literal(form[1], environment), [], metadata)
            skipped.append({**metadata, "reason": "Upstream availability predicate has a separate API",
                            "assertion": f"Availability precondition in {kind}",
                            "source": literal(form[2], environment)})
        elif kind == "is":
            assertion = form[1]
            assertion_kind = head(assertion)
            if assertion_kind == "=":
                expected = literal(assertion[1], environment)
                expression = assertion[2]
                if head(expression) == "z/string":
                    state = literal(expression[1], environment)
                    add_case(state["command"], state["marked"], expected, [], metadata)
                    cases[-1].update(output="replacement", repeat=state["repeat"])
                    return
                if head(expression) == "->>" and head(expression[2]) == "iterate":
                    iterator = expression[2][1]
                    parameter = iterator[1][0] if head(iterator) == "fn*" and len(iterator[1]) == 1 else None
                    expected_iterator = f"(fn* [{parameter}] (as-string (transform/cycle-coll (z/of-string {parameter}))))"
                    if (metadata["test"] != "cycle-coll-test" or head(expression[3]) != "take"
                            or str(iterator) != expected_iterator):
                        raise ValueError(f"Unsupported upstream iteration: {expression}")
                    add_case("cycle-coll", literal(expression[1], environment), expected, [], metadata)
                    cases[-1].update(output="sequence", repeat=literal(expression[3][1], environment) - 1)
                    return
                command, marked, arguments = operation(assertion[2], environment)
            elif assertion_kind == "nil?":
                expected = None
                command, marked, arguments = operation(assertion[1], environment)
            elif assertion_kind == "not" and head(assertion[1]) in {
                "can-get-in-more-code?", "can-get-in-less-code?"
            }:
                skipped.append({**metadata, "reason": "Upstream availability predicate has a separate API",
                                "assertion": str(assertion)})
                return
            elif assertion_kind == "some?" and str(assertion[1]) == "range":
                skipped.append({**metadata, "reason": "Upstream zipper edit-range assertion; this audit compares output text",
                                "assertion": str(assertion)})
                return
            else:
                raise ValueError(f"Unsupported upstream assertion: {assertion}")
            add_case(command, marked, expected, arguments, metadata)
        else:
            raise ValueError(f"Unsupported upstream fixture form: {form}")

    def assertion_forms(form):
        if not isinstance(form, (PersistentList, PersistentVector)):
            return 0
        kind = head(form)
        own = int(kind in {"is", "are"} or bool(kind and kind.startswith("assert-get-in-")))
        return own + sum(assertion_forms(child) for child in form)

    for filename, selected in files.items():
        tests = [form for form in reader.read_str((checkout / filename).read_text(encoding="utf-8"))
                 if head(form) == "deftest"]
        found = set()
        for test in tests:
            name = str(test[1])
            included = selected is None or name in selected
            inventory.append({"file": filename, "test": name, "selected": included,
                              "assertion_forms": assertion_forms(test)})
            if not included:
                continue
            found.add(name)
            settings = {}
            metadata = {"file": filename, "test": name,
                        "line": test.meta.val_at(keyword("line", "basilisp.lang.reader")), "context": ""}
            suites.append(metadata)
            for form in list(test)[2:]:
                walk(form, {}, metadata)
        if selected is not None and found != selected:
            raise ValueError(f"Missing selected upstream tests in {filename}: {selected - found}")
    if not cases:
        raise ValueError("No upstream refactor assertions were captured")
    return cases, skipped, suites, inventory


def compare_cases(cases):
    import basilisp_tools  # noqa: F401
    from basilisp.lang.keyword import keyword as kw
    from basilisp.lang.map import map as lmap
    from basilisp.lang.vector import vector

    analyzer = importlib.import_module("basilisp_tools.analyzer")
    lsp = importlib.import_module("basilisp_tools.lsp")
    syntax = importlib.import_module("basilisp_tools.syntax")

    def form(source):
        parsed = syntax.parse(source)
        if len(parsed.val_at(kw("diagnostics"))):
            return None

        def visit(node):
            kind = str(node.val_at(kw("kind")))
            children = tuple(visit(child) for child in syntax.forms(node))
            if children:
                return kind, children
            text = node.val_at(kw("text"), "")
            if kind == ":symbol":
                for prefix in ("basilisp.core/", "clojure.core/"):
                    if text.startswith(prefix):
                        text = text[len(prefix):]
            return kind, text

        return tuple(visit(node) for node in syntax.forms(parsed.val_at(kw("root"))))

    results = []
    for case in cases:
        source = case["source"]
        try:
            actual_document = source
            offset, edit_count = case["offset"], 0
            sequence = [source]
            for _ in range(case.get("repeat", 1)):
                analysis = analyzer.analyze(actual_document, lmap({kw("filename"): "/tmp/blt-upstream-refactor.lpy"}))
                analysis = analysis.assoc(kw("settings"), lmap({kw(k): v for k, v in case["settings"].items()}))
                edits = list(lsp.refactor_edits(actual_document, analysis, case["command"], offset,
                                               vector(case["args"])) or [])
                for start, end, replacement in sorted(edits, key=lambda item: (item[0], item[1]), reverse=True):
                    actual_document = actual_document[:start] + replacement + actual_document[end:]
                if edits:
                    offset = min(edit[0] for edit in edits)
                edit_count += len(edits)
                sequence.append(actual_document)
            output = case.get("output", "document")
            if output == "sequence":
                actual = sequence
            elif output == "replacement":
                actual = edits[0][2] if edits else None
            elif output == "replacements":
                actual = [edit[2] for edit in edits]
            else:
                actual = actual_document
            expected = case["expected"]
            if expected is None:
                status = "exact" if not edit_count else "mismatch"
                structural_match = not edit_count
            else:
                expected_parts = expected if isinstance(expected, list) else [expected]
                actual_parts = actual if isinstance(actual, list) else [actual]
                expected_forms = [form(part) for part in expected_parts]
                actual_forms = [form(part) if isinstance(part, str) else None for part in actual_parts]
                structural_match = None not in expected_forms and expected_forms == actual_forms
                status = "exact" if actual == expected else "structural-only" if structural_match else "mismatch"
            results.append({**case, "status": status, "structural_match": structural_match, "actual": actual,
                            "actual_document": actual_document, "edit_count": edit_count})
        except Exception as error:
            results.append({**case, "status": "error", "structural_match": False, "error": repr(error)})
    return results


def baseline_snapshot(revision, results, skipped, inventory):
    """Record exact passes and coverage, without accepting structural-only results."""
    return {
        "revision": revision,
        "cases": len(results),
        "skipped": len(skipped),
        "inventory": {
            "deftests": len(inventory),
            "selected_deftests": sum(item["selected"] for item in inventory),
            "assertion_forms": sum(item["assertion_forms"] for item in inventory),
            "excluded_assertion_forms": sum(item["assertion_forms"] for item in inventory if not item["selected"]),
        },
        "matched": [item["id"] for item in results if item["status"] == "exact"],
    }


def baseline_regressions(baseline, current):
    if baseline["revision"] != current["revision"]:
        raise ValueError("Baseline revision differs from upstream source revision")
    regressions = sorted(set(baseline["matched"]) - set(current["matched"]))
    for field in ("cases", "skipped", "inventory"):
        if baseline[field] != current[field]:
            regressions.append(f"extraction coverage changed: {field}")
    return regressions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clojure-lsp", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--report-only", action="store_true", help="Report mismatches without failing; execution errors still fail")
    parser.add_argument("--baseline", type=Path, help="Require recorded exact passes and extraction coverage to remain")
    parser.add_argument("--write-baseline", type=Path, help="Record extraction coverage and exact passes")
    args = parser.parse_args()
    checkout = args.clojure_lsp.resolve()
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
    if revision != CLOJURE_LSP_REVISION:
        parser.error(f"Expected clojure-lsp {CLOJURE_LSP_REVISION}, found {revision}")
    if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"],
                               cwd=checkout, text=True).strip():
        parser.error("The clojure-lsp checkout must have no tracked changes")
    cases, skipped, suites, inventory = capture_cases(checkout)
    results = compare_cases(cases)
    counts = Counter(item["status"] for item in results)
    snapshot = baseline_snapshot(revision, results, skipped, inventory)
    regressions = []
    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        try:
            regressions = baseline_regressions(baseline, snapshot)
        except ValueError as error:
            parser.error(str(error))
    for result in results:
        if result["status"] in {"mismatch", "error"}:
            print(f"{result['id']} ({result['command']}): {result['status']}\n"
                  f"  upstream: {result['expected']!r}\n  blt:      {result.get('actual', result.get('error'))!r}")
    print(f"Upstream clojure-lsp: {len(results)} refactor expectations from {len(suites)} tests; "
          f"{counts['exact']} exact, {counts['structural-only']} structural-only, "
          f"{counts['mismatch']} mismatches, {counts['error']} errors; {len(skipped)} assertions skipped.")
    print(f"Selection: {len(suites)}/{len(inventory)} deftests in the two source files; "
          f"{sum(item['assertion_forms'] for item in inventory if not item['selected'])} "
          "assertion forms in unselected tests. Other upstream test files are outside this audit.")
    for regression in regressions:
        print(f"Baseline regression: {regression}")
    if args.report:
        args.report.write_text(json.dumps({"upstream_revision": revision, "suites": suites,
                                          "counts": dict(counts), "cases": results, "skipped": skipped,
                                          "inventory": inventory, "regressions": regressions,
                                          "structural_comparison": "Omits trivia/reader discards and normalizes core namespaces; not semantic equivalence"},
                                         indent=2) + "\n", encoding="utf-8")
    if args.write_baseline:
        if counts["error"]:
            parser.error("Cannot record a baseline containing execution errors")
        args.write_baseline.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    return int(bool(counts["error"] or regressions)
               or (not (args.report_only or args.baseline) and any(item["status"] != "exact" for item in results)))


if __name__ == "__main__":
    raise SystemExit(main())
