"""A reviewed platform difference must never mask a new failure."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from upstream_differences import input_digest, observation, review_regressions


def sample():
    case = {"id": "fixture", "source": "(f)", "expected": [], "actual": ["native"],
            "expected_exit": 0, "actual_exit": 2, "match": False}
    review = {"reason": "A different runtime contract", "evidence": "An executable counterexample",
              "input_sha256": input_digest(case), "observed": observation(case)}
    return case, {case["id"]: review}


def test_review_accepts_only_the_documented_observation():
    case, reviews = sample()
    assert review_regressions([case], reviews, lambda item: item["match"]) == []
    case["actual_exit"] = 3
    assert any("behavior changed" in error
               for error in review_regressions([case], reviews, lambda item: item["match"]))


def test_review_is_bound_to_input_and_expectation():
    for field, changed in (("source", "(g)"), ("expected", ["new upstream finding"])):
        case, reviews = sample()
        case[field] = changed
        assert any("input changed" in error
                   for error in review_regressions([case], reviews, lambda item: item["match"]))


def test_unclassified_missing_obsolete_and_undocumented_reviews_fail():
    case, reviews = sample()
    assert review_regressions([case], {}, lambda item: False)
    assert review_regressions([], reviews, lambda item: False)
    assert review_regressions([case], reviews, lambda item: True)
    del reviews["fixture"]["evidence"]
    assert review_regressions([case], reviews, lambda item: False)


def test_an_execution_error_cannot_be_reviewed_away():
    case, reviews = sample()
    case["exception"] = "RuntimeError"
    assert review_regressions([case], reviews, lambda item: False)
