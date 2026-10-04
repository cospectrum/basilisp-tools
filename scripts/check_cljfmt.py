"""Compare with an external, pinned cljfmt checkout.

Run with: uv run python scripts/check_cljfmt.py --cljfmt /path/to/cljfmt
Requires Clojure and Java. No upstream source or generated fixtures are vendored.
Add --corpus /path/to/basilisp to compare real .lpy and .cljc files as well.
"""

from __future__ import annotations

import argparse
import collections
import importlib
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path

CLJFMT_REVISION = "baab5008032945434cbca23ef5eda516e3ea97b0"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cljfmt", type=Path, required=True)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--report", type=Path, help="Write a JSON report of all failures and skips.")
    parser.add_argument("--report-only", action="store_true", help="Report mismatches without failing.")
    args = parser.parse_args()
    checkout = args.cljfmt.resolve()
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=checkout, text=True
    ).strip()
    if revision != CLJFMT_REVISION:
        parser.error(f"Expected cljfmt {CLJFMT_REVISION}, found {revision}")
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=checkout, text=True
    ).strip()
    if dirty:
        parser.error("The cljfmt checkout must have no tracked changes.")

    # Import the package first to initialize Basilisp's importer.
    import basilisp_tools  # noqa: F401
    from basilisp.lang.keyword import keyword as kw
    from basilisp.lang.map import map as lmap
    from basilisp.lang.vector import vector

    edn = importlib.import_module("basilisp.edn")
    formatter = importlib.import_module("basilisp_tools.format")
    syntax = importlib.import_module("basilisp_tools.syntax")
    regex_adapter = importlib.import_module("basilisp_tools.regex")

    def tagged_reader(tag, value):
        if str(tag) != "re":
            raise ValueError(f"Unexpected oracle EDN tag: {tag}")
        return regex_adapter.compile_(value)

    with tempfile.TemporaryDirectory(prefix="blt-cljfmt-") as temporary:
        output = Path(temporary) / "oracle.edn"
        command = [
            "clojure", "-Sdeps",
            '{:paths ["cljfmt/src" "cljfmt/resources" "cljfmt/test"]}',
            "-M", str(Path(__file__).with_name("capture_cljfmt.clj").resolve()),
            str(output),
        ]
        if args.corpus:
            command.append(str(args.corpus.resolve()))
        subprocess.run(command, cwd=checkout, check=True)
        captured = edn.read_string(
            output.read_text(encoding="utf-8"),
            lmap({kw("default"): tagged_reader}),
        )
        # Corpus sources can be hundreds of KB each. Transport them as UTF-8
        # files rather than making the EDN reader decode huge escaped strings.
        corpus_cases = []
        for case in captured.val_at(kw("corpus")):
            item = dict(case)
            for field_name in ("source", "expected"):
                path = item.pop(kw(f"{field_name}-file"), None)
                if path is not None:
                    item[kw(field_name)] = Path(path).read_bytes().decode("utf-8")
            corpus_cases.append(lmap(item))
        captured = captured.assoc(kw("corpus"), vector(corpus_cases))

    counts = collections.Counter()
    findings = []
    timings = []

    def field(value, name, default=None):
        return value.val_at(kw(name), default)

    def compare(case, name=None, options=None):
        source = field(case, "source")
        name = name or field(case, "name")
        options = options if options is not None else field(case, "options", lmap({}))
        expected = field(case, "expected")
        details = {"name": name, "source": source, "options": str(options)}
        oracle_error = field(case, "oracle-error")
        if oracle_error:
            counts["oracle-unsupported"] += 1
            findings.append({**details, "kind": "oracle-unsupported", "reason": oracle_error})
        diagnostics = field(syntax.parse(source, lmap({kw("dialect"): kw("clojure")})), "diagnostics")
        if len(diagnostics):
            counts["unexpected-parser-error"] += 1
            findings.append({
                **details, "kind": "unexpected-parser-error", "reason": str(diagnostics)
            })
            return
        started = time.perf_counter()
        try:
            actual = formatter.format_string(source, options)
            repeated = formatter.format_string(actual, options)
        except Exception as exception:
            counts["exception"] += 1
            findings.append({**details, "kind": "exception", "reason": str(exception)})
            return
        timings.append({"name": name, "characters": len(source),
                        "seconds": round(time.perf_counter() - started, 4)})
        if oracle_error:
            counts["basilisp-preserved"] += 1
        elif actual != expected:
            counts["mismatch"] += 1
            findings.append({
                **details, "kind": "mismatch", "expected": expected, "actual": actual
            })
        else:
            counts["match"] += 1
        if repeated != actual:
            counts["not-idempotent"] += 1
            findings.append({
                **details, "kind": "not-idempotent", "actual": actual, "repeated": repeated
            })

    cases = field(captured, "cases")
    if not len(cases):
        raise RuntimeError("The upstream suite did not produce any formatter cases.")
    for index, case in enumerate(cases, 1):
        compare(case)
        if index % 100 == 0:
            print(f"Compared {index} formatting cases.", flush=True)
    for index, case in enumerate(field(captured, "corpus"), 1):
        compare(case)
        if index % 10 == 0:
            print(f"Compared {index} corpus files.", flush=True)

    report = {
        "cljfmt_revision": revision,
        "upstream_cases": field(captured, "upstream-count", len(cases)),
        "regression_cases": field(captured, "regression-count", 0),
        "corpus_files": len(field(captured, "corpus")),
        "counts": dict(counts), "findings": findings,
        "format_seconds": round(sum(item["seconds"] for item in timings), 4),
        "slowest": sorted(timings, key=lambda item: item["seconds"], reverse=True)[:10],
    }
    if args.report:
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("cljfmt differential results:", json.dumps(dict(counts), sort_keys=True))
    failed = sum(counts[key] for key in (
        "mismatch", "not-idempotent", "exception", "unexpected-parser-error"
    ))
    for finding in [f for f in findings if f["kind"] not in {
        "unsupported-syntax", "oracle-unsupported"
    }][:10]:
        print(finding["kind"], finding["name"], repr(finding["source"][:120]))
    return 0 if args.report_only or not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
