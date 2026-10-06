"""Keep public runtime checks from accepting skipped or empty upstream suites."""

import importlib.util
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def runtime_audit(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "public_runtime_audit", scripts / "check_public_runtime.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("cases", [
    "",
    '<testcase classname="calc" name="x"><skipped/></testcase>',
    '<testcase classname="calc" name="x"><failure/></testcase>',
    '<testcase classname="calc" name="x"><error/></testcase>',
    '<testcase classname="calc" name="x"/>' * 2,
])
def test_incomplete_upstream_suite_is_rejected(runtime_audit, tmp_path, cases):
    report = tmp_path / "junit.xml"
    report.write_text(f"<testsuites><testsuite>{cases}</testsuite></testsuites>")
    with pytest.raises(ValueError):
        runtime_audit.passing_tests(report)


def test_upstream_test_identity_includes_namespace(runtime_audit, tmp_path):
    report = tmp_path / "junit.xml"
    report.write_text(
        '<testsuites><testsuite><testcase classname="calc.parser" name="x"/>'
        '<testcase classname="calc.units" name="x"/></testsuite></testsuites>'
    )
    assert runtime_audit.passing_tests(report) == {("calc.parser", "x"), ("calc.units", "x")}


def test_runtime_copy_excludes_untracked_pytest_hooks(runtime_audit, tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    subprocess.run(["git", "init", "--quiet", str(checkout)], check=True)
    (checkout / "src").mkdir()
    (checkout / "src" / "core.cljc").write_text("(ns calc.core)")
    subprocess.run(["git", "add", "src/core.cljc"], cwd=checkout, check=True)
    (checkout / "pytest.ini").write_text("[pytest]\naddopts = -k only_one\n")
    (checkout / "conftest.py").write_text("raise AssertionError('unreviewed hook')\n")
    target = tmp_path / "runtime"
    runtime_audit.copy_runtime_checkout(checkout, target)
    assert (target / "src" / "core.cljc").read_text() == "(ns calc.core)"
    assert not (target / "pytest.ini").exists()
    assert not (target / "conftest.py").exists()
