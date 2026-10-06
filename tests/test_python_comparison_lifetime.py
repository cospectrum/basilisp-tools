"""A shared inspection worker must not retain comparison proofs across analyses."""

import importlib

import basilisp_tools  # noqa: F401
from basilisp.lang.keyword import keyword as k
from basilisp.lang.runtime import to_lisp


def test_python_dependency_edits_invalidate_comparison_proofs(tmp_path):
    path = tmp_path / "changing_protocol.pyi"
    source = "from typing import Protocol\nclass Contract(Protocol):\n{body}\ndef accept(value: Contract) -> None: ...\n"
    bridge = importlib.import_module("basilisp_tools.python")
    analyzer = importlib.import_module("basilisp_tools.analyzer")
    cache = bridge.create_cache()
    python_options = to_lisp({"python-paths": [str(tmp_path)]}).assoc(k("python-cache"), cache)
    options = to_lisp({}).assoc(k("python-options"), python_options)
    program = '(ns comparison-lifetime (:import [changing_protocol :as m])) (m/accept "text")'
    try:
        # The same nominal pair str -> Contract changes its structural meaning.
        for body, expected_errors in [
            ("    def unavailable_member(self) -> None: ...", 1),
            ("    pass", 0),
            ("    def unavailable_member(self) -> None: ...", 1),
        ]:
            path.write_text(source.format(body=body))
            result = analyzer.analyze(program, options)
            errors = [finding for finding in result.val_at(k("findings"))
                      if finding.val_at(k("type")) == k("type-mismatch")]
            assert len(errors) == expected_errors
        # analyze copied the caller's options instead of attaching its memo there.
        assert k("type-comparisons", ns="basilisp-tools.python") not in python_options
    finally:
        bridge.stop_cache__BANG__(cache)
