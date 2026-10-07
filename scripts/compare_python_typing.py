"""Compare complete upstream interop reports produced by the same adapter."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from check_python_typing import CALL_ERRORS, finding_type


def index(report):
    result = {}
    for fixture in report["fixtures"]:
        for case in fixture["cases"]:
            if case["id"] in result:
                raise ValueError("Duplicate assertion: " + case["id"])
            result[case["id"]] = (fixture, case)
    return result


def coverage(cases):
    return {
        "matched_positive_calls": sum(case["status"] == "passed" and case["kind"] == "call"
                                      and not case.get("expected_error") for case in cases),
        "matched_negative_calls": sum(case["status"] == "passed" and case.get("expected_error", False) for case in cases),
        "matched_return_assertions": sum(case["status"] == "passed" and case["kind"] == "return"
                                         and not case.get("expected_error") for case in cases),
        "expected_valid_diagnostic_cases": sum(not case.get("expected_error", False) and case["status"] == "failed"
                                               and any(finding_type(finding) in CALL_ERRORS
                                                       for finding in case.get("findings", [])) for case in cases),
    }


def compare(before, after):
    for report in (before, after):
        if "source_hashes_end" not in report or report.get("source_changed_during_run"):
            raise ValueError("A complete report from unchanged sources is required")
    if before["sources"] != after["sources"] or before.get("oracle") != after.get("oracle"):
        raise ValueError("Source pins and oracle must match")
    adapters = lambda report: {name: value for name, value in report["source_hashes_start"].items()
                               if name.startswith("scripts/")}
    if adapters(before) != adapters(after):
        raise ValueError("Both reports must use the same adapter")
    left, right = index(before), index(after)
    if left.keys() != right.keys():
        raise ValueError("Discovered assertion inventories differ")
    if [(f["provider"], f["path"], f["name"], f["source_sha256"]) for f in before["fixtures"]] != [
            (f["provider"], f["path"], f["name"], f["source_sha256"]) for f in after["fixtures"]]:
        raise ValueError("Fixture content or selection differs")
    providers = {}
    changed = []
    newly_failing_valid = []
    for provider in sorted({fixture["provider"] for fixture in after["fixtures"]}):
        old_cases = [case for fixture, case in left.values() if fixture["provider"] == provider]
        new_cases = [case for fixture, case in right.values() if fixture["provider"] == provider]
        fixtures = [fixture for fixture in after["fixtures"] if fixture["provider"] == provider]
        providers[provider] = {
            "fixtures": len(fixtures), "empty_fixtures": sum(not fixture["cases"] for fixture in fixtures),
            "units": len(new_cases), "before": dict(Counter(case["status"] for case in old_cases)),
            "after": dict(Counter(case["status"] for case in new_cases)),
            "coverage_before": coverage(old_cases), "coverage_after": coverage(new_cases),
            "exclusion_reasons": dict(Counter(case["excluded"] for case in new_cases if case.get("excluded"))),
        }
    for identifier, (fixture, current) in right.items():
        previous = left[identifier][1]
        for key in ("expression", "expected_error", "expected_annotation", "expected_display", "excluded"):
            if previous.get(key) != current.get(key):
                raise ValueError("Assertion expectation changed: " + identifier)
        if previous["status"] != current["status"]:
            row = {"id": identifier, "before": previous["status"], "after": current["status"],
                   "expected_error": current.get("expected_error"), "expression": current.get("expression")}
            changed.append(row)
            if (current["status"] == "failed" and previous["status"] in ("passed", "unknown")
                    and not current.get("expected_error")):
                newly_failing_valid.append({**row, "findings": current.get("findings"),
                                           "expected": current.get("expected"), "actual": current.get("actual")})
    return {
        "boundary": "Translated interop assertions; excluded and unknown units are not passes.",
        "providers": providers,
        "before": dict(Counter(case["status"] for _, case in left.values())),
        "after": dict(Counter(case["status"] for _, case in right.values())),
        "transitions": dict(Counter(row["before"] + " -> " + row["after"] for row in changed)),
        "newly_failing_expected_valid": newly_failing_valid,
        "changed": changed,
        "remaining_differences": [identifier for identifier, (_, case) in right.items() if case["status"] == "failed"],
        "replay_errors": [identifier for identifier, (_, case) in right.items() if case["status"] == "error"],
        "source_hashes_before": before["source_hashes_start"],
        "source_hashes_after": after["source_hashes_start"],
        "scope_before": before["scope"], "scope_after": after["scope"],
        "oracle": after.get("oracle"), "sources": after["sources"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = compare(json.loads(args.baseline.read_text()), json.loads(args.candidate.read_text()))
    report["raw_reports"] = {label: {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                             for label, path in (("baseline", args.baseline), ("candidate", args.candidate))}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({name: report[name] for name in ("before", "after", "transitions")}))
    return int(bool(report["newly_failing_expected_valid"] or report["replay_errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
