"""Gate reviewed language differences without hiding new audit failures."""

import hashlib
import json


def input_digest(case):
    """Bind an exception to its original input, configuration, and expectation."""
    omitted = {"actual", "actual_exit", "actual_document", "edit_count", "match",
               "status", "structural_match", "error", "exception", "review"}
    source = {key: value for key, value in case.items() if key not in omitted}
    return hashlib.sha256(json.dumps(source, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def observation(case):
    return {key: case[key] for key in ("actual", "actual_exit", "actual_document", "edit_count")
            if key in case}


def review_regressions(cases, reviews, exact):
    """Require every non-exact result to equal an explicitly reviewed outcome."""
    regressions = []
    ids = {case["id"] for case in cases}
    for missing in sorted(set(reviews) - ids):
        regressions.append(f"reviewed case disappeared: {missing}")
    for case in cases:
        case_id = case["id"]
        review = reviews.get(case_id)
        if exact(case):
            if review:
                regressions.append(f"review is obsolete; now matches upstream: {case_id}")
            continue
        if review is None:
            regressions.append(f"unclassified difference: {case_id}")
            continue
        if not review.get("reason") or not review.get("evidence"):
            regressions.append(f"review lacks reason or evidence: {case_id}")
        if review.get("input_sha256") != input_digest(case):
            regressions.append(f"reviewed input changed: {case_id}")
        if "error" in case or "exception" in case or review.get("observed") != observation(case):
            regressions.append(f"reviewed Basilisp behavior changed: {case_id}")
        case["review"] = {key: review[key] for key in ("reason", "evidence") if key in review}
    return regressions
