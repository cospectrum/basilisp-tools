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


def test_explicit_mypy_optional_policy_preserves_positive_and_negative_calls(harness, tmp_path):
    import basilisp_tools
    from basilisp.lang.keyword import keyword
    from basilisp.lang.runtime import to_lisp
    engine = importlib.import_module('basilisp_tools.analyzer'), importlib.import_module('basilisp_tools.python'), keyword, to_lisp
    item = source_fixture('# mypy: implicit-optional\ndef f(x: int = None) -> int: return 1\nf(None)\nf("bad") # E\n')
    item['provider'] = 'mypy'
    rows = harness.discover(item)
    harness.replay(item, rows, tmp_path, engine)
    assert [row['status'] for row in rows] == ['passed', 'passed']
    adaptation = item['metadata']['configuration_adaptations'][0]
    assert adaptation['option'] == 'implicit-optional'
    assert adaptation['source_sha256'] != adaptation['adapted_sha256']


def test_optional_policy_is_explicit_and_checker_scoped(harness):
    source = '# mypy: implicit-optional\ndef f(x: int = None) -> None: ...\n'
    assert harness.configured_source(source, 'ty') == (source, [])
    for directive in ('no-implicit-optional', 'implicit_optional=False', 'strict-optional'):
        unchanged = source.replace('implicit-optional', directive)
        assert harness.configured_source(unchanged, 'mypy') == (unchanged, [])
    text = 'flag = "# mypy: implicit-optional"\ndef f(x: int = None) -> None: ...\n'
    assert harness.configured_source(text, 'mypy') == (text, [])


def test_optional_policy_preserves_future_imports_forward_refs_and_utf8(harness):
    source = ('"""Δ source"""\nfrom __future__ import annotations\n# mypy: implicit-optional\n'
              'class __blt_config_typing: pass\n'
              'def f(π: "str" = None, /, *, named: int = None, required: int) -> int: return required\n')
    adapted, metadata = harness.configured_source(source, 'mypy')
    namespace = {}
    exec(adapted, namespace)
    assert namespace['f'](None, named=None, required=1) == 1
    assert '__blt_config_typing_.Optional["str"]' in adapted
    assert 'required: int' in adapted
    assert len(metadata[0]['annotations']) == 2


def test_pyright_single_parameter_display_uses_declared_paramspec_slots(harness):
    item = source_fixture('from typing import Generic, ParamSpec, TypeVar\n'
                          'P = ParamSpec("P")\nT = TypeVar("T")\n'
                          'class C(Generic[P, T]): pass\n'
                          'reveal_type(value, expected_text="C[(str), (int)]")\n')
    item['provider'] = 'pyright'
    row = next(row for row in harness.discover(item) if row['kind'] == 'return')
    assert row['display_parameter_slots'] == {'C': [True, False]}
    assert harness.expected_annotation(row) == 'C[[str], int]'
    assert harness.expected_annotation({**row, 'expected_annotation': 'C[str, int]'}) == 'C[str, int]'
    assert harness.expected_annotation({**row, 'expected_display': 'C[(int, str), float]'}) == 'C[[int, str], float]'
    assert harness.expected_annotation({**row, 'expected_display': 'C[..., float]'}) == 'C[..., float]'


def test_display_paramspec_slots_require_actual_typing_provenance(harness):
    source = ('from typing import ParamSpec, Generic\n'
              'def ParamSpec(name): return object()\n'
              'P = ParamSpec("P")\nclass C(Generic[P]): pass\n')
    assert harness.display_parameter_slots(ast.parse(source)) == {}


def test_error_markers_are_comments_not_strings(harness):
    assert harness.markers('f("# E: not an assertion")\n# E: actual\nf(0)\n') == {3: ["E: actual"]}


def test_snapshot_call_diagnostics_are_negative_oracles(harness):
    rows = harness.discover(source_fixture(
        'def pair(x: int, y: str) -> None: ...\n'
        '# snapshot: missing-argument\npair(*[])\n'
        'pair(1, 2)  # snapshot: invalid-argument-type\n'
        'pair(1, "ok")\n'))
    assert [row['expected_error'] for row in rows] == [True, True, False]


def test_markers_after_suppression_and_multiple_markers_are_preserved(harness):
    source = 'f(0) # type: ignore[unrelated] # E: first # E: second\n'
    assert harness.markers(source) == {1: ['E: first', 'E: second']}
    assert harness.discover(source_fixture(source))[0]['expected_error']


def test_snapshot_style_warning_is_not_a_bad_argument_oracle(harness):
    rows = harness.discover(source_fixture('f(1) # snapshot: redundant-cast\n'))
    assert not rows[0]['expected_error']
    assert rows[0]['markers'] == ['snapshot: redundant-cast']


def test_suppression_codes_preserve_tool_and_diagnostic_scope(harness):
    source = ('f("bad") # type: ignore[arg-type]\n'
              'f() # type: ignore[unrelated]\n'
              'f() # pyright: ignore[reportCallIssue]\n')
    assert harness.suppressions(source, 'mypy') == {
        1: {'comment': '# type: ignore[arg-type]', 'diagnostics': ['type-mismatch']}}
    assert harness.suppressions(source, 'pyright')[3]['diagnostics'] == ['invalid-arity']
    assert harness.suppressions('f() # type: ignore[pyrefly:bad-argument-type]\n', 'pyrefly')[1]['diagnostics'] == ['type-mismatch']
    assert 'invalid-arity' in harness.suppressions('f() # type: ignore[arg-type]\n', 'pyrefly')[1]['diagnostics']
    assert harness.suppressions('f() # type: ignore[pyrefly:bad-return]\n', 'pyrefly') == {}


def test_explicit_ignore_is_replayed_without_hiding_unrelated_errors(harness, tmp_path):
    import basilisp_tools
    from basilisp.lang.keyword import keyword
    from basilisp.lang.runtime import to_lisp
    engine = importlib.import_module('basilisp_tools.analyzer'), importlib.import_module('basilisp_tools.python'), keyword, to_lisp
    item = source_fixture('def f(x: int) -> int: ...\nf("bad") # type: ignore[arg-type]\nf("bad") # type: ignore[call-arg]\nf(1) # type: ignore[arg-type]\n')
    item['provider'] = 'mypy'
    rows = harness.discover(item)
    harness.replay(item, rows, tmp_path, engine)
    assert [row['status'] for row in rows] == ['unknown', 'failed', 'passed']
    assert rows[2]['actual'] == {'module': 'builtins', 'path': ['int']}
    assert rows[0]['findings'] == []


def test_assignment_targets_are_not_read_assertions(harness):
    rows = harness.discover(source_fixture('x["a"] = 1  # E\nx.attr = 2  # E\nx["b"]  # E\n'))
    calls = [row for row in rows if row["kind"] == "call"]
    assert [row["expression"] for row in calls] == ['x["b"]']
    assert len([row for row in rows if row["kind"] == "non-call-assertion"]) == 2


def test_module_assignment_retains_only_explicit_prior_contracts(harness):
    item = source_fixture('class A: pass\nclass B: pass\nx: A\nx = B() # E\ny = B()\n')
    rows = harness.discover(item)
    assert rows[0]['assignment_module_target'] == 'x'
    assert rows[0]['assignment_statement'] == 'x = B()'
    assert rows[0]['expected_error']
    assert 'assignment_target' not in rows[1]


def test_module_assignment_uses_native_module_object_and_retains_rhs_type(harness, tmp_path):
    import basilisp_tools
    from basilisp.lang.keyword import keyword
    from basilisp.lang.runtime import to_lisp
    engine = importlib.import_module('basilisp_tools.analyzer'), importlib.import_module('basilisp_tools.python'), keyword, to_lisp
    item = source_fixture('from typing import assert_type\nclass A: pass\nx: A\nx = assert_type(A(), A)\n')
    rows = harness.discover(item)
    harness.replay(item, rows, tmp_path, engine)
    row = rows[0]
    assert row['status'] == 'passed', row
    assert '(python/__import__ ' in row['basilisp_context']
    assert ' nil nil #py ["*"])' in row['basilisp_context']
    assert row['basilisp'].endswith('/A)')
    assert row['actual']['path'] == ['A']


def test_future_rebinding_is_not_imported_as_an_earlier_value(harness):
    rows = harness.discover(source_fixture('Value = factory(int)\nValue(1)\nValue = factory(str)\nValue("x")\n'))
    calls = [row for row in rows if row["expression"].startswith("Value(")]
    assert "Temporal Python binding" in calls[0]["excluded"]
    assert "excluded" not in calls[1]


def test_immutable_bindings_are_taken_from_the_consumer_source_position(harness):
    tree = ast.parse('value = "first"\nconsume(value)\nvalue = b"last"\nconsume(value)\n')
    assert ast.literal_eval(harness.literal_bindings_before(tree, 2)['value']) == 'first'
    assert ast.literal_eval(harness.literal_bindings_before(tree, 4)['value']) == b'last'
    rows = harness.discover(source_fixture(ast.unparse(tree)))
    assert all('excluded' not in row for row in rows)


def test_uncertain_writes_invalidate_literal_facts(harness):
    tree = ast.parse('value = "first"\nif flag:\n    value = "second"\nconsume(value)\n')
    assert harness.literal_bindings_before(tree, 5) == {}


def test_comprehension_scope_is_retained_without_module_qualifying_locals(harness):
    source = 'x = "outside"\n[assert_type((x, y), tuple[int, int]) for x in (1, 2) for y in (x,)]\n'
    row = harness.discover(source_fixture(source))[0]
    scope, prefix, locals_ = harness.lexical_comprehension(row, 'sample', {})
    expression = harness.translate(ast.parse(row['expression'], mode='eval').body, 'sample', scope)
    assert expression == '#py (blt-comprehension-0 blt-comprehension-1)'
    assert prefix == '(for [blt-comprehension-0 #py (1 2) blt-comprehension-1 #py (blt-comprehension-0)] '
    assert set(locals_) == {'x', 'y'}
    assert 'excluded' not in row


def test_comprehension_iterable_uses_only_preceding_generator_bindings(harness):
    source = '[x for x in reveal_type(values)]\n'
    row = harness.discover(source_fixture(source))[0]
    assert 'comprehension_bindings' not in row
    nested = harness.discover(source_fixture(
        '[[reveal_type((x, y)) for x in (1, 2)] for y in ("a", "b")]\n'))[0]
    assert [item['target'] for item in nested['comprehension_bindings']] == ['y', 'x']


def test_comprehension_repeated_binding_names_have_distinct_local_symbols(harness):
    row = harness.discover(source_fixture('[reveal_type((x, y)) for x in (1,) for x in (2,) for y in (3,)]\n'))[0]
    scope, prefix, _ = harness.lexical_comprehension(row, 'sample', {})
    assert 'blt-comprehension-0' in prefix and 'blt-comprehension-1' in prefix
    assert harness.translate(ast.parse(row['expression'], mode='eval').body, 'sample', scope) == '#py (blt-comprehension-1 blt-comprehension-2)'


def test_comprehension_replay_compares_inner_expression_type(harness, tmp_path):
    import basilisp_tools
    from basilisp.lang.keyword import keyword
    from basilisp.lang.runtime import to_lisp
    engine = importlib.import_module('basilisp_tools.analyzer'), importlib.import_module('basilisp_tools.python'), keyword, to_lisp
    item = source_fixture('from typing import assert_type\na: int = 1\nb: str = "a"\n'
                          '[assert_type((x, y), tuple[int, str]) for x, y in ((a, b),)]\n')
    rows = harness.discover(item)
    harness.replay(item, rows, tmp_path, engine)
    assert rows[0]['status'] == 'passed', rows[0]
    assert rows[0]['coverage']['return_resolved']
    assert rows[0]['basilisp_context'].startswith('(for [[')
    assert harness.literal_bindings_before(ast.parse('value: object = "first"\nconsume(value)\n'), 2) == {}
    assert harness.literal_bindings_before(ast.parse('value = [1]\nconsume(value)\n'), 2) == {}


def test_bytes_translation_preserves_every_byte(harness):
    from basilisp.lang import reader
    from io import StringIO
    value = bytes(range(256))
    translated = harness.translate(ast.Constant(value), 'fixture')
    assert next(reader.read(StringIO(translated))) == value


def test_deprecation_expectations_do_not_accept_argument_errors(harness):
    rows = harness.discover(source_fixture('f(1) # E: Use of deprecated function\n'))
    assert rows[0]['expected_diagnostic_types'] == ['deprecated-var']


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


def test_mypy_misc_ignore_does_not_hide_other_argument_failures(harness, tmp_path):
    import basilisp_tools
    from basilisp.lang.keyword import keyword
    from basilisp.lang.runtime import to_lisp
    engine = importlib.import_module('basilisp_tools.analyzer'), importlib.import_module('basilisp_tools.python'), keyword, to_lisp
    source = ('def f(a: int, *, b: int) -> None: ...\n'
              'def g() -> int: ...\n'
              'f(1, 2) # type: ignore[misc]\n'
              'f(1, 2, 3) # type: ignore[misc] # E: Excess arguments\n'
              'f() # type: ignore[misc] # E: Missing arguments\n'
              'f(1, b="bad") # type: ignore[misc] # E: Invalid type\n'
              'f(g(1), 2) # type: ignore[misc] # E: Nested invalid call\n'
              'f(1, b=2) # type: ignore[misc]\n'
              'g(1) # type: ignore[misc] # E: Excess arguments\n')
    item = source_fixture(source)
    item['provider'] = 'mypy'
    rows = harness.discover(item)
    document = harness.replay(item, rows, tmp_path, engine)
    assert [row['status'] for row in rows] == ['unknown', 'passed', 'passed', 'passed', 'passed', 'passed', 'passed']
    assert rows[0]['suppressed_findings'][0] in document
    assert rows[0]['findings'] == []
    assert len(rows[4]['findings']) == 1
    assert 'received 1' in rows[4]['findings'][0]['message']


def test_raw_keyword_names_are_preserved_through_native_apply_kw(harness, tmp_path):
    import basilisp_tools
    from basilisp.lang.keyword import keyword
    from basilisp.lang.runtime import to_lisp
    engine = importlib.import_module('basilisp_tools.analyzer'), importlib.import_module('basilisp_tools.python'), keyword, to_lisp
    item = source_fixture('def raw(**kwargs: int) -> int: ...\n'
                          'raw(**{"in": 1, "x-y": 2})\n'
                          'raw(**{"x-y": "bad"}) # E: Invalid value\n')
    rows = harness.discover(item)
    harness.replay(item, rows, tmp_path, engine)
    assert [row['status'] for row in rows] == ['passed', 'passed']
    assert '(apply-kw ' in rows[0]['basilisp']
    assert '{"in" 1 "x-y" 2}' in rows[0]['basilisp']
    assert harness.translate(ast.parse('obj.raw(**{"x-y": 1})', mode='eval').body, 'fixture') == '(apply-kw (.-raw fixture/obj) {"x-y" 1})'


def test_exact_selection_rejects_missing_duplicate_unknown_and_changed_source(harness, tmp_path):
    import json
    fixture = {'provider': 'mypy', 'path': 'case.test', 'name': 'testCase', 'source_sha256': 'a' * 64,
               'cases': [{'kind': 'call', 'expected_error': False, 'status': 'passed'},
                         {'kind': 'call', 'expected_error': True, 'status': 'passed'}]}
    selected = {key: fixture[key] for key in ('provider', 'path', 'name', 'source_sha256')}
    selected['expected'] = harness.selection_result(fixture)
    path = tmp_path / 'selection.json'
    path.write_text(json.dumps([selected]))
    selection = harness.load_selection(path)
    assert harness.validate_selection({'fixtures': [fixture]}, selection) == []
    assert harness.validate_selection({'fixtures': []}, selection)
    assert harness.validate_selection({'fixtures': [fixture, fixture]}, selection)
    assert harness.validate_selection({'fixtures': [{**fixture, 'source_sha256': 'b' * 64}]}, selection)
    changed = {**fixture, 'cases': [{**fixture['cases'][0], 'status': 'unknown'}, fixture['cases'][1]]}
    assert harness.validate_selection({'fixtures': [changed]}, selection)


def test_exact_selection_requires_resolved_coverage_and_unique_identity(harness, tmp_path):
    import json
    item = {'provider': 'mypy', 'path': 'case.test', 'name': 'testCase', 'source_sha256': 'a' * 64,
            'expected': {'passed': 1, 'excluded': 0, 'positive_calls': 1, 'negative_calls': 0, 'return_assertions': 0}}
    path = tmp_path / 'selection.json'
    for payload in ([], [item, item], [{**item, 'expected': {**item['expected'], 'passed': 0}}]):
        path.write_text(json.dumps(payload))
        with pytest.raises(ValueError):
            harness.load_selection(path)


def test_explicit_abstract_annotations_are_not_replaced_with_literal_values(harness, tmp_path):
    import basilisp_tools
    from basilisp.lang.keyword import keyword
    from basilisp.lang.runtime import to_lisp
    engine = importlib.import_module('basilisp_tools.analyzer'), importlib.import_module('basilisp_tools.python'), keyword, to_lisp
    tree = ast.parse('a: int = 42\nb: Final[int] = 42\nc: Literal[42] = 42\nd = 42\nconsume(a, b, c, d)\n')
    assert set(harness.literal_bindings_before(tree, 5)) == {'b', 'c', 'd'}
    item = source_fixture('from typing import assert_type\nx = b"abc"\ni: int = 42\n'
                          'assert_type(x[i], int)\nx[42] # E: Proven invalid literal index\n')
    rows = harness.discover(item)
    harness.replay(item, rows, tmp_path, engine)
    assert [row['status'] for row in rows] == ['passed', 'passed']
    assert 'i' not in rows[0]['literal_bindings']
    assert rows[1]['findings'][0]['type'] == ':type-mismatch'


def test_attribute_assignment_preserves_the_mutation_target_and_oracle_span(harness):
    item = source_fixture('receiver.field = make_value() # E: Incompatible assignment\n')
    row = harness.discover(item)[0]
    assert row['expression'] == 'make_value()'
    assert row['assignment_target'] == 'receiver.field'
    assert row['assignment_statement'] == 'receiver.field = make_value()'
    assert row['oracle_start'] == [0, 0]
    item['metadata']['import_root'] = '/fixtures'
    diagnostic = {'severity': 'error', 'rule': 'reportAssignmentType',
                  'range': {'start': {'line': 0, 'character': 0}, 'end': {'line': 0, 'character': 14}}}
    harness.apply_pyright_oracle(item, [row], {'/fixtures/example.py': [diagnostic]})
    assert row['expected_error']
    assert 'excluded' not in row


def test_python_member_findings_are_scored_without_accepting_lexical_errors(harness):
    missing = {'type': ':unresolved-symbol', 'message': 'Unresolved Python member: absent'}
    lexical = {'type': ':unresolved-symbol', 'message': 'Unresolved symbol: local-name'}
    assert harness.finding_type(missing) == 'unresolved-python-member'
    assert harness.finding_type(lexical) == 'unresolved-symbol'
    assert harness.diagnostic_status({'expected_error': True, 'findings': [missing]}) == 'passed'
    assert harness.diagnostic_status({'expected_error': False, 'findings': [missing]}) == 'failed'
    assert harness.diagnostic_status({'expected_error': True, 'findings': [lexical, missing]}) == 'error'
    assert harness.diagnostic_status({'expected_error': False, 'findings': [lexical]}) == 'error'
    assert harness.diagnostic_status({'expected_error': True, 'expected_diagnostic_types': ['deprecated-var'],
                                     'findings': [missing]}) is None


def test_missing_python_module_member_is_a_real_negative_oracle(harness, tmp_path):
    import basilisp_tools
    from basilisp.lang.keyword import keyword
    from basilisp.lang.runtime import to_lisp
    engine = importlib.import_module('basilisp_tools.analyzer'), importlib.import_module('basilisp_tools.python'), keyword, to_lisp
    item = source_fixture('import supplied\nsupplied.absent # E\nsupplied.other\n'
                          'supplied.ignored # type: ignore[attr-defined]\n')
    item['files']['supplied.py'] = 'present = 1\n'
    rows = harness.discover(item)
    harness.replay(item, rows, tmp_path, engine)
    assert [row['status'] for row in rows] == ['passed', 'failed', 'unknown']
    assert rows[0]['findings'][0]['message'] == 'Unresolved Python member: absent'
    assert rows[1]['findings'][0]['message'] == 'Unresolved Python member: other'
    assert rows[2]['findings'] == []
