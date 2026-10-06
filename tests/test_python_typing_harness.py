"""Keep upstream assertion extraction independent of Basilisp's implementation."""

import ast
import importlib
import io
from pathlib import Path
import tarfile
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("check_python_typing")


def source_fixture(source):
    return {"provider": "typing", "path": "example.py", "name": "example",
            "files": {"example.py": source}, "metadata": {"source_file": "example.py"}}


def test_error_markers_are_comments_not_strings(harness):
    assert harness.markers('f("# E: not an assertion")\n# E: actual\nf(0)\n') == {3: ["E: actual"]}


def test_assignment_targets_are_not_read_assertions(harness):
    rows = harness.discover(source_fixture('x["a"] = 1  # E\nx.attr = 2  # E\nx["b"]  # E\n'))
    calls = [row for row in rows if row["kind"] == "call"]
    assert [row["expression"] for row in calls] == ['x["b"]']
    assert len([row for row in rows if row["kind"] == "non-call-assertion"]) == 2


def test_future_rebinding_is_not_imported_as_an_earlier_value(harness):
    rows = harness.discover(source_fixture('Value = factory(int)\nValue(1)\nValue = factory(str)\nValue("x")\n'))
    calls = [row for row in rows if row["expression"].startswith("Value(")]
    assert "Temporal Python binding" in calls[0]["excluded"]
    assert "excluded" not in calls[1]


@pytest.mark.parametrize("later", ["from other import f", "import other as f", "f += other", "del f"])
def test_all_later_binding_writes_prevent_stale_global_replay(harness, later):
    rows = harness.discover(source_fixture('from first import f\nf()\n' + later + '\n'))
    assert "Temporal Python binding" in rows[0]["excluded"]


def test_optional_and_grouped_errors_remain_distinct(harness):
    rows = harness.discover(source_fixture('f()  # E?\ng()  # E[group+]\nh()  # E\n'))
    assert all("cross-line oracle" in row["excluded"] for row in rows[:2])
    assert rows[2]["expected_error"] and "excluded" not in rows[2]


def test_line_error_marker_does_not_guess_between_multiple_consumers(harness):
    rows = harness.discover(source_fixture('good(); bad()  # E\n'))
    assert len(rows) == 2
    assert all("target is ambiguous" in row["excluded"] for row in rows)


def test_keyword_and_literal_unpack_translation(harness):
    expression = ast.parse('f(*(1, "x"), **{"flag": True})', mode="eval").body
    assert harness.translate(expression, "fixture") == '(fixture/f 1 "x" ** :flag true)'


@pytest.mark.parametrize(('source', 'reason'), [
    ('f(x=1, x=2)', 'Repeated keyword arguments'),
    ('f(x=1, **{"x": 2})', 'Repeated keyword arguments'),
    ('f(**{"x": 1}, **{"x": 2})', 'Repeated keyword arguments'),
    ('f(**{"x": 1, "x": 2})', 'Repeated keys in a literal keyword dictionary'),
    ('f(outer=g(x=1, **{"x": 2}))', 'Repeated keyword arguments'),
])
def test_repeated_keyword_sources_require_an_exact_adapter(harness, source, reason):
    expression = ast.parse(source, mode='eval').body
    with pytest.raises(harness.Unsupported, match=reason):
        harness.translate(expression, 'fixture')


def test_resolved_symbol_without_signature_remains_unknown(harness):
    assert not harness.signature_known({"status": ":known", "type-path": ["Result"]})
    assert not harness.signature_known({"status": ":unknown", "parameters": []})
    assert harness.signature_known({"status": ":known", "parameters": []})
    assert not harness.signature_known({"status": ":known", "overloads": [{"parameters": []}, {}]})
    assert not harness.type_resolved({"any?": True})
    assert not harness.type_resolved({"module": "builtins", "path": ["list"], "arguments": [{"any?": True}]})
    assert harness.type_resolved({"module": "builtins", "path": ["int"]})


def test_oracle_non_call_error_does_not_become_bad_argument(harness):
    item = source_fixture('f()\n')
    item["metadata"]["import_root"] = "/tmp"
    rows = harness.discover(item)
    error = {"severity": "error", "rule": "reportAssignmentType", "range": {"start": {"line": 0}}}
    harness.apply_pyright_oracle(item, rows, {"/tmp/example.py": [error]})
    assert "non-call aspect" in rows[0]["excluded"]


def test_oracle_diagnostic_on_multiline_argument_is_preserved(harness):
    item = source_fixture('f(\n "bad"\n)\n')
    item["metadata"]["import_root"] = "/tmp"
    rows = harness.discover(item)
    error = {"severity": "error", "rule": "reportArgumentType", "range": {"start": {"line": 1}}}
    harness.apply_pyright_oracle(item, rows, {"/tmp/example.py": [error]})
    assert rows[0]["expected_error"]


def test_oracle_columns_disambiguate_calls_on_one_line(harness):
    item = source_fixture('good(); bad()\n')
    item["metadata"]["import_root"] = "/tmp"
    rows = harness.discover(item)
    error = {"severity": "error", "rule": "reportCallIssue", "range": {
        "start": {"line": 0, "character": 8}, "end": {"line": 0, "character": 13},
    }}
    harness.apply_pyright_oracle(item, rows, {"/tmp/example.py": [error]})
    assert [row["expected_error"] for row in rows] == [False, True]


def test_python_byte_offsets_are_converted_to_oracle_utf16(harness):
    source = '"😀"; bad()\n'
    node = ast.parse(source).body[1].value
    assert harness.utf16_column(source, 1, node.col_offset) == 6


def test_ty_named_files_and_inherited_config(harness, tmp_path):
    corpus = importlib.import_module("python_typing_corpus")
    directory = tmp_path / "crates/ty_python_semantic/resources/mdtest"
    directory.mkdir(parents=True)
    (directory / "sample.md").write_text(
        '# Parent\n```toml\n[environment]\npython-version = "3.13"\n```\n'
        '## Child\n`helper.pyi`:\n```pyi\ndef f() -> int: ...\n```\n'
        '```py\nfrom helper import f\n```\n```py\nf()\n```\n',
    )
    cases = list(corpus.ty_fixtures(tmp_path))
    assert len(cases) == 1
    assert cases[0]["files"]["helper.pyi"] == 'def f() -> int: ...\n\n'
    assert 'from helper import f' in cases[0]["files"]["mdtest_snippet.py"]
    assert cases[0]["metadata"]["configuration"] == ['[environment]\npython-version = "3.13"\n']


def test_rust_parentheses_inside_raw_source_keep_macro_boundary(harness):
    corpus = importlib.import_module("python_typing_corpus")
    source = 'testcase!(first, r#"f(\")\")"#); testcase!(second, r"g()");'
    cases = list(corpus.rust_testcases(source))
    assert [(name, complete) for name, _, _, complete, _ in cases] == [("first", True), ("second", True)]
    assert cases[1][1][0][0] == "g()"


def test_pinned_archive_extracts_only_selected_regular_files(harness, tmp_path):
    corpus = importlib.import_module("python_typing_corpus")
    entry = {"directory": "sample", "revision": "abc123", "paths": ["tests/", "LICENSE"]}
    with tarfile.open(tmp_path / "sample.tar.gz", "w:gz") as archive:
        for path in ["project-abc123/tests/test.py", "project-abc123/setup.py", "project-abc123/LICENSE"]:
            content = b"fixture"
            item = tarfile.TarInfo(path)
            item.size = len(content)
            archive.addfile(item, io.BytesIO(content))
    corpus.fetch_source(entry, tmp_path)
    assert (tmp_path / "sample/tests/test.py").read_text() == "fixture"
    assert not (tmp_path / "sample/setup.py").exists()
    assert (tmp_path / "sample/.blt-source-revision").read_text() == "abc123\n"


def test_pinned_archive_rejects_mismatched_revision(harness, tmp_path):
    corpus = importlib.import_module("python_typing_corpus")
    with tarfile.open(tmp_path / "sample.tar.gz", "w:gz") as archive:
        item = tarfile.TarInfo("project-other/tests/test.py")
        item.size = 0
        archive.addfile(item, io.BytesIO())
    with pytest.raises(ValueError, match="pinned revision"):
        corpus.fetch_source({"directory": "sample", "revision": "expected", "paths": ["tests/"]}, tmp_path)


def test_display_type_names_are_annotation_safe(harness):
    assert harness.expected_annotation({'expected_display': 'builtins.int | list[__main__.Outer.Inner]'}) == '__blt_expected_builtins.int | list[Outer.Inner]'
    assert harness.expected_annotation({'expected_annotation': 'builtins.int'}) == 'builtins.int'
    assert not harness.explicit_any_expectation({'expected_display': 'Missing | int'})
    assert harness.explicit_any_expectation({'expected_display': 'Any'})


@pytest.mark.parametrize(('source', 'status'), [
    ('def f() -> int | str: ...\nreveal_type(f()) # N: Revealed type is "builtins.int | builtins.str"\n', 'passed'),
    ('class C: pass\nreveal_type(C()) # N: Revealed type is "__main__.C"\n', 'passed'),
    ('def f() -> int: ...\nreveal_type(f()) # N: Revealed type is "Missing | int"\n', 'unknown'),
    ('from typing import Any\ndef f() -> Any: ...\nreveal_type(f()) # N: Revealed type is "Any"\n', 'passed'),
])
def test_return_oracle_resolution(harness, tmp_path, source, status):
    import importlib
    import basilisp_tools
    from basilisp.lang.keyword import keyword
    from basilisp.lang.runtime import to_lisp
    engine = importlib.import_module('basilisp_tools.analyzer'), importlib.import_module('basilisp_tools.python'), keyword, to_lisp
    item = {'provider': 'mypy', 'path': 'example.test', 'name': 'case', 'files': {'main.py': source}, 'metadata': {'source_file': 'main.py'}}
    rows = harness.discover(item)
    harness.replay(item, rows, tmp_path, engine)
    assert rows[0]['status'] == status


@pytest.mark.parametrize('provider', ['invalid', 'typing'])
def test_empty_and_unknown_selections_fail(harness, monkeypatch, tmp_path, provider):
    monkeypatch.setattr(harness, 'source_hashes', lambda engine: {})
    monkeypatch.setattr(harness, 'fixtures', lambda *args: [])
    monkeypatch.setattr(sys, 'argv', ['audit', '--corpus', str(tmp_path), '--provider', provider, '--inventory-only', '--output', str(tmp_path / 'report.json')])
    with pytest.raises(SystemExit) as error:
        harness.main()
    assert error.value.code == 2


def test_ci_can_require_resolved_contracts(harness, monkeypatch, tmp_path):
    source = 'f()\n'
    item = {'provider': 'typing', 'path': 'example.py', 'name': 'example', 'source_sha256': 'fixture',
            'files': {'main.py': source}, 'metadata': {'source_file': 'main.py'}}
    monkeypatch.setattr(harness, 'source_hashes', lambda engine: {})
    monkeypatch.setattr(harness, 'fixtures', lambda *args: [item])
    monkeypatch.setattr(harness, 'replay', lambda item, rows, *args: [row.update(status='unknown') for row in rows])
    monkeypatch.setattr(sys, 'argv', ['audit', '--corpus', str(tmp_path), '--provider', 'typing', '--require-resolved', '--output', str(tmp_path / 'report.json')])
    assert harness.main() == 1


def test_rust_bug_headers_comments_and_environment_helpers(harness, tmp_path):
    import python_typing_corpus as corpus
    folder = tmp_path / 'pyrefly/lib/test'
    folder.mkdir(parents=True)
    (folder / 'example.rs').write_text(
        'testcase!(bug = "known", broken, r"f()");\n'
        'testcase!( // header comment\n normal, r"f()");\n'
        'testcase!(custom, custom_env(), r"f()");\n'
        'testcase!(plain, TestEnv::new(), r"f()");\n'
        'testcase!(mapped, TestEnv::one("aux", r"def f(): ...",), r"from aux import f\nf()");\n'
        'testcase!(unmapped, TestEnv::one("aux", "def f(): ..."), r"from aux import f\nf()");\n')
    cases = {item['name']: item for item in corpus.pyrefly_fixtures(tmp_path)}
    assert len(cases) == 6
    assert cases['broken']['metadata']['upstream_bug'] == '"known"'
    assert 'unsupported_environment' in cases['custom']['metadata']
    assert 'unsupported_environment' in cases['unmapped']['metadata']
    assert all('unsupported_environment' not in cases[name]['metadata'] for name in ('normal', 'plain', 'mapped'))
    assert cases['mapped']['files']['aux.py'] == 'def f(): ...'


def test_mypy_flags_and_configuration_are_not_silently_ignored(harness, tmp_path):
    import python_typing_corpus as corpus
    folder = tmp_path / 'test-data/unit'
    folder.mkdir(parents=True)
    (folder / 'check-example.test').write_text(
        '[case configured]\n# flags: --strict\nf()\n'
        '[case plugin]\nf()\n[file mypy.ini]\n[mypy]\nplugins=plugin.py\n'
        '[case normal]\nf()\n')
    cases = {item['name']: item for item in corpus.mypy_fixtures(tmp_path)}
    assert cases['configured']['metadata']['flags'] == ['--strict']
    assert cases['plugin']['metadata']['configuration_files'] == ['mypy.ini']
    assert all('unsupported_environment' in cases[name]['metadata'] for name in ('configured', 'plugin'))
    assert 'unsupported_environment' not in cases['normal']['metadata']


@pytest.mark.parametrize('internal', [
    {'type': 'syntax', 'row': 1, 'message': 'Analysis failed: KeyError'},
    {'type': ':file', 'message': 'Cannot analyze this document'},
    {'type': ':syntax', 'row': 2, 'message': 'Missing closing delimiter'},
    {'type': 'python-inspection', 'row': 1, 'message': 'Inspector did not complete'},
])
def test_internal_failure_cannot_satisfy_an_expected_call_error(harness, tmp_path, internal):
    item = source_fixture('def f(x: int) -> int: ...\nf("bad") # E\nf(1)\n')
    rows = harness.discover(item)
    findings = [{'type': 'type-mismatch', 'row': 2, 'message': 'Wrong argument'}, internal]
    stopped = []
    analyzer = SimpleNamespace(analyze=lambda *args: {'findings': findings})
    bridge = SimpleNamespace(create_cache=lambda: object(),
                             stop_cache__BANG__=lambda cache: stopped.append(cache))
    document_findings = harness.replay(item, rows, tmp_path, (analyzer, bridge, None, lambda x: x))
    assert document_findings == findings
    assert all(row['status'] == 'error' and row['findings'] == findings for row in rows)
    assert len(stopped) == 1
