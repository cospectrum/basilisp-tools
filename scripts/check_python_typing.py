"""Audit Python interop using pinned, nonexecuted upstream type-checker fixtures.

This checks translated calls, not Python declaration or control-flow conformance.
Every discovered assertion is retained as tested, unknown, or explicitly excluded.
"""
from __future__ import annotations

import argparse
import ast
import builtins
import collections
import copy
import hashlib
import importlib
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import time
import tokenize

from python_typing_corpus import fetch_source, fixtures


DECLARATIONS = {"TypeVar", "TypeVarTuple", "ParamSpec", "NewType", "TypedDict", "NamedTuple", "type_check_only", "dataclass_transform"}
ASSERTIONS = {"assert_type", "reveal_type"}
CALL_ERRORS = {"invalid-arity", "type-mismatch", "unresolved-python-member"}
PYRIGHT_CALL_ERRORS = {"reportCallIssue", "reportArgumentType", "reportAttributeAccessIssue", "reportIndexIssue", "reportOperatorIssue"}


class Unsupported(ValueError):
    pass


def name_of(node):
    return node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None


def utf16_column(source, line, byte_column):
    prefix = source.splitlines()[line - 1].encode("utf-8")[:byte_column].decode("utf-8")
    return len(prefix.encode("utf-16-le")) // 2


def markers(source):
    """Keep upstream optional/group markers distinct from mandatory call errors."""
    result = collections.defaultdict(list)
    pending = []
    comments = {}
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                comments[token.start[0]] = (token.start[1], token.string)
    except (tokenize.TokenError, IndentationError):
        # Retain comments preceding invalid syntax; the AST inventory records it.
        pass
    for number, line in enumerate(source.splitlines(), 1):
        if pending and line.strip() and not line.lstrip().startswith("#"):
            result[number].extend(pending)
            pending = []
        column, text = comments.get(number, (0, ""))
        comment = re.match(r"#\s*(E(?:\?|\[[^]]+\])?(?::|\s|$).*|error:.*|revealed:.*|N:.*)", text)
        if comment:
            target = pending if not line[:column].strip() else result[number]
            target.append(comment.group(1))
    return dict(result)


def discover(item):
    """Inventory assertions and direct consumer calls, with their lexical context."""
    rows = []
    for filename, source in item["files"].items():
        if not filename.endswith((".py", ".pyi")):
            continue
        comments = markers(source)
        for line in item["metadata"].get("expected_output", "").splitlines():
            diagnostic = re.match(r"([^:]+):(\d+): (error|note): (.*)", line)
            if diagnostic and diagnostic.group(1) in {filename, "main" if filename == "main.py" else filename}:
                comments.setdefault(int(diagnostic.group(2)), []).append(
                    ("E: " if diagnostic.group(3) == "error" else "N: ") + diagnostic.group(4))
        try:
            tree = ast.parse(source)
        except (SyntaxError, RecursionError) as error:
            rows.append({"line": getattr(error, "lineno", 1), "file": filename, "kind": "unparsed-source",
                         "excluded": f"Host Python AST cannot parse fixture: {error}",
                         "marker_count": sum(map(len, comments.values()))})
            continue
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        writes = collections.defaultdict(list)
        for statement in tree.body:
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                for target in targets:
                    for part in ast.walk(target):
                        if isinstance(part, ast.Name) and isinstance(part.ctx, ast.Store):
                            writes[part.id].append(statement.lineno)
            elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                writes[statement.name].append(statement.lineno)
            elif isinstance(statement, (ast.Import, ast.ImportFrom)):
                for alias in statement.names:
                    writes[alias.asname or alias.name.split(".")[0]].append(statement.lineno)
            elif isinstance(statement, (ast.AugAssign, ast.Delete)):
                targets = statement.targets if isinstance(statement, ast.Delete) else [statement.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        writes[target.id].append(statement.lineno)
        used_markers = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Call, ast.Subscript, ast.Attribute)):
                continue
            assertion = isinstance(node, ast.Call) and name_of(node.func) in ASSERTIONS
            parent = parents.get(node)
            direct = isinstance(parent, (ast.Expr, ast.Assign, ast.AnnAssign)) and parent.value is node
            if not assertion and not direct:
                continue
            expression = node.args[0] if assertion and node.args else node
            expectation = ast.get_source_segment(source, node.args[1]) if assertion and name_of(node.func) == "assert_type" and len(node.args) > 1 else None
            expected_text = next((value.value.value for value in getattr(node, "keywords", []) if value.arg == "expected_text" and isinstance(value.value, ast.Constant) and isinstance(value.value.value, str)), None)
            notes = [note for line in range(node.lineno, node.end_lineno + 1) for note in comments.get(line, [])]
            for line in range(node.lineno, node.end_lineno + 1):
                if line in comments:
                    used_markers.add(line)
            if not expectation:
                for note in notes:
                    revealed = re.search(r'(?:revealed:|revealed type:|Revealed type is)\s*["\']?(.*?)["\']?$', note, re.I)
                    if revealed:
                        expected_text = revealed.group(1)
            context = []
            ancestor = parents.get(node)
            while ancestor and not isinstance(ancestor, ast.Module):
                if isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.If, ast.For, ast.While, ast.Try, ast.With, ast.Match, ast.Lambda)):
                    context.append(type(ancestor).__name__)
                ancestor = parents.get(ancestor)
            row = {"file": filename, "line": node.lineno, "end_line": node.end_lineno, "column": node.col_offset,
                   "oracle_start": [node.lineno - 1, utf16_column(source, node.lineno, node.col_offset)],
                   "oracle_end": [node.end_lineno - 1, utf16_column(source, node.end_lineno, node.end_col_offset)],
                   "kind": "return" if assertion else "call", "expression": ast.get_source_segment(source, expression),
                   "expected_annotation": expectation, "expected_display": expected_text,
                   "markers": notes, "context": context,
                   "expected_error": any(re.match(r"E(?:\s|:|$)|error:", note) and "revealed type:" not in note.lower() for note in notes)}
            row["assertion_source"] = "type-directive" if assertion else "expected-diagnostic" if notes else "consumer-call"
            rebound = sorted({part.id for part in ast.walk(expression) if isinstance(part, ast.Name)
                              and any(line >= node.lineno for line in writes.get(part.id, []))})
            if context:
                row["excluded"] = "Lexical/control-flow context requires adaptation: " + ",".join(context)
            elif rebound:
                row["excluded"] = "Temporal Python binding requires source-order replay: " + ", ".join(rebound)
            elif isinstance(node, ast.Call) and name_of(node.func) in DECLARATIONS:
                row["excluded"] = "Python type-system declaration, not an interop call"
            elif isinstance(parent, ast.AnnAssign) and name_of(parent.annotation) == "TypeAlias":
                row["excluded"] = "Python type alias declaration, not an interop call"
            elif any(note.startswith(("E?", "E[")) for note in notes):
                row["excluded"] = "Optional/grouped diagnostic requires preserving its cross-line oracle"
            elif isinstance(parent, ast.AnnAssign) and row["expected_error"]:
                row["excluded"] = "Annotated Python assignment may reject the return, independently of call acceptance"
            elif assertion and name_of(node.func) == "assert_type" and row["expected_error"]:
                row["excluded"] = "The assert_type directive itself is expected to fail"
            elif assertion and not expectation and not expected_text:
                row["excluded"] = "Reveal has no machine-readable expected type"
            rows.append(row)
        for line, notes in comments.items():
            marked = [row for row in rows if row["file"] == filename
                      and row["line"] <= line <= row.get("end_line", row["line"])
                      and row.get("expected_error")]
            if item["provider"] != "pyright" and len(marked) > 1:
                for row in marked:
                    row.setdefault("excluded", "One line-based error marker covers multiple consumers; its target is ambiguous")
            if line not in used_markers:
                rows.append({"file": filename, "line": line, "kind": "non-call-assertion", "markers": notes,
                             "excluded": "Assertion concerns Python declarations, assignments, or other non-call semantics"})
    for index, row in enumerate(rows):
        row["id"] = f"{item['provider']}:{item['path']}::{item['name']}::{row['file']}:{row['line']}:{row.get('column', 0)}:{row['kind']}:{index}"
    return rows


def apply_pyright_oracle(item, rows, diagnostics):
    """Attach an independently executed oracle; never infer errors from prose."""
    filename = str(Path(item["metadata"]["import_root"]) / item["metadata"]["source_file"])
    by_line = collections.defaultdict(list)
    for diagnostic in diagnostics.get(filename, []):
        if diagnostic.get("severity") == "error":
            by_line[diagnostic["range"]["start"]["line"] + 1].append(diagnostic)
    for row in rows:
        if row.get("excluded"):
            continue
        errors = [diagnostic for line in range(row["line"], row.get("end_line", row["line"]) + 1)
                  for diagnostic in by_line.get(line, [])]
        start, end = row.get("oracle_start"), row.get("oracle_end")
        if start is not None and end is not None:
            errors = [diagnostic for diagnostic in errors
                      if [diagnostic["range"]["start"]["line"], diagnostic["range"]["start"].get("character", 0)] < end
                      and [diagnostic["range"].get("end", diagnostic["range"]["start"])["line"],
                           diagnostic["range"].get("end", diagnostic["range"]["start"]).get("character", 0)] >= start]
        row["oracle_diagnostics"] = errors
        if errors and not all(error.get("rule") in PYRIGHT_CALL_ERRORS for error in errors):
            row["excluded"] = "Native oracle rejects a non-call aspect of this Python statement"
        elif row["kind"] == "return" and errors:
            row["excluded"] = "Upstream reveal expression is rejected by the independent oracle"
        else:
            row["expected_error"] = bool(errors)


def translate(node, module, bindings=None):
    if bindings is not None:
        class Substitute(ast.NodeTransformer):
            def visit_Name(self, name):
                if name.id in bindings:
                    return ast.copy_location(ast.Name(id=bindings[name.id], ctx=ast.Load()), name)
                return name
        # Symbol replacements contain their complete Basilisp qualification.
        node = Substitute().visit(copy.deepcopy(node))
    if isinstance(node, ast.Constant):
        if node.value is None:
            return "nil"
        if node.value is True:
            return "true"
        if node.value is False:
            return "false"
        if isinstance(node.value, (str, int, float)):
            return json.dumps(node.value, ensure_ascii=False, allow_nan=False)
        raise Unsupported("Unsupported Python literal: " + type(node.value).__name__)
    if isinstance(node, ast.Name):
        return node.id if "/" in node.id else module + "/" + node.id
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        opening, closing = ("#py [", "]") if isinstance(node, ast.List) else ("#py (", ")") if isinstance(node, ast.Tuple) else ("#py #{", "}")
        return opening + " ".join(translate(value, module) for value in node.elts) + closing
    if isinstance(node, ast.Dict):
        if any(key is None for key in node.keys):
            raise Unsupported("Dictionary unpack requires an exact argument environment")
        return "#py {" + " ".join(translate(value, module) for pair in zip(node.keys, node.values) for value in pair) + "}"
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)) and isinstance(node.operand, ast.Constant) and type(node.operand.value) in (int, float):
        return repr(-node.operand.value if isinstance(node.op, ast.USub) else node.operand.value)
    if isinstance(node, ast.Attribute):
        return "(.-" + node.attr + " " + translate(node.value, module) + ")"
    if isinstance(node, ast.Subscript) and not isinstance(node.slice, ast.Slice):
        return "(aget " + translate(node.value, module) + " " + translate(node.slice, module) + ")"
    if isinstance(node, ast.Call):
        values = []
        for arg in node.args:
            if isinstance(arg, ast.Starred):
                if not isinstance(arg.value, (ast.Tuple, ast.List)):
                    raise Unsupported("Dynamic starred arguments need a typed lexical environment")
                values.extend(translate(value, module) for value in arg.value.elts)
            else:
                values.append(translate(arg, module))
        keywords = []
        for arg in node.keywords:
            if arg.arg is None:
                if not isinstance(arg.value, ast.Dict) or not all(isinstance(key, ast.Constant) and isinstance(key.value, str) for key in arg.value.keys):
                    raise Unsupported("Dynamic keyword unpack requires a typed lexical environment")
                names = [key.value for key in arg.value.keys]
                if len(set(names)) != len(names):
                    raise Unsupported("Repeated keys in a literal keyword dictionary require Python overwrite and evaluation semantics")
                keywords.extend((key.value, value) for key, value in zip(arg.value.keys, arg.value.values))
            else:
                keywords.append((arg.arg, arg.value))
        if len({key for key, _ in keywords}) != len(keywords):
            raise Unsupported("Repeated keyword arguments require preserving Python compilation or unpacking errors before Basilisp call generation")
        if keywords:
            values += ["**"] + [part for key, value in keywords for part in (":" + key, translate(value, module))]
        if isinstance(node.func, ast.Attribute):
            head = "." + node.func.attr + " " + translate(node.func.value, module)
        else:
            head = translate(node.func, module)
        return "(" + " ".join([head, *values]) + ")"
    raise Unsupported("Unsupported expression adaptation: " + type(node).__name__)


def plain(value):
    if hasattr(value, "items"):
        return {str(key).removeprefix(":"): plain(item) for key, item in value.items()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        return [plain(item) for item in value]
    except TypeError:
        return str(value)


def canonical(value, expected=None):
    if not isinstance(value, dict):
        return value
    keep = {"module", "path", "arguments", "union", "nullable?", "literal-values", "any?", "never?", "typevar", "bound", "constraints", "parameter-list?", "ellipsis?"}
    result = {}
    for key, item in value.items():
        if key not in keep or (key == "literal-values" and expected is not None and key not in expected):
            continue
        if isinstance(item, list):
            values = [canonical(x) for x in item]
            result[key] = sorted(values, key=lambda x: json.dumps(x, sort_keys=True)) if key in {"union", "literal-values"} else values
        else:
            result[key] = canonical(item)
    return result


def signature_known(metadata):
    if not isinstance(metadata, dict) or metadata.get("status", "").removeprefix(":") != "known":
        return False
    signatures = metadata.get("overloads") or [metadata]
    return bool(signatures) and all(isinstance(signature.get("parameters"), list) for signature in signatures)


def type_resolved(value):
    if not isinstance(value, dict) or not value or value.get("any?") or value.get("typevar"):
        return False
    return all(type_resolved(child) for key in ("arguments", "union") for child in value.get(key, []))


def expected_annotation(row):
    text = row.get("expected_annotation") or row.get("expected_display")
    if not text:
        return None
    tree = ast.parse(text, mode="eval")
    if row.get("expected_display") and not row.get("expected_annotation"):
        class DisplayNames(ast.NodeTransformer):
            def visit_Attribute(self, node):
                node = self.generic_visit(node)
                if isinstance(node.value, ast.Name):
                    if node.value.id == "builtins":
                        node.value.id = "__blt_expected_builtins"
                    elif node.value.id == "__main__":
                        return ast.copy_location(ast.Name(id=node.attr, ctx=ast.Load()), node)
                return node
        tree = DisplayNames().visit(tree)
    return ast.unparse(tree)


def explicit_any_expectation(row):
    return (row.get("expected_annotation") or row.get("expected_display")) in {
        "Any", "typing.Any", "typing_extensions.Any"}


def source_hashes(engine=None):
    root = Path(__file__).resolve().parents[1]
    package = Path(engine[0].__file__).parent if engine else root / "src/basilisp_tools"
    paths = {"src/basilisp_tools/" + path.name: path
             for path in package.iterdir() if path.suffix in {".py", ".lpy"}}
    paths.update({"scripts/" + name: root / "scripts" / name for name in
                  ("check_python_typing.py", "python_typing_corpus.py", "python_typing_sources.json")})
    return {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in sorted(paths.items())}


def consumer_metadata(node, module, row, result, bridge, options, to_lisp, bindings):
    """Resolve the translated consumer independently of its inferred return type."""
    if not isinstance(node, ast.Call):
        return None
    if isinstance(node.func, ast.Name):
        owner, name = bindings.get(node.func.id, module + "/" + node.func.id).split("/", 1)
        return plain(bridge.inspect_path(owner, to_lisp([name]), options))
    if isinstance(node.func, ast.Attribute):
        receiver = node.func.value
        if isinstance(receiver, ast.Name):
            return plain(bridge.inspect_path(module, to_lisp([receiver.id, node.func.attr]), options))
        translated = translate(receiver, module)
        offset = row["basilisp"].find(translated)
        column = row["basilisp_column"] + offset
        types = [value.get("python-type") for value in result.get("python-expressions", [])
                 if value.get("row") == row["basilisp_row"] and value.get("col") == column]
        if offset >= 0 and types and types[-1]:
            return plain(bridge.inspect_member(to_lisp(types[-1]), node.func.attr, options))
    return None


def internal_analysis_findings(findings):
    """Internal failures cannot satisfy an expected argument diagnostic."""
    return [finding for finding in findings
            if finding.get("type", "").removeprefix(":") in {"file", "syntax", "python-inspection"}
            or finding.get("message", "").startswith("Analysis failed:")]


def replay(item, rows, working, engine):
    analyzer, bridge, kw, to_lisp = engine
    module = "blt_fixture_" + hashlib.sha256((item["provider"] + item["path"] + item["name"]).encode()).hexdigest()[:16]
    folder = working / module
    folder.mkdir()
    filename = item["metadata"].get("source_file")
    source = item["files"].get(filename, "")
    tree = ast.parse(source)
    bound = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)}
    bound.update(node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
    bound.update(alias.asname or alias.name.split(".")[0] for node in ast.walk(tree)
                 if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names)
    bindings = {name: "builtins/" + name for name in dir(builtins) if name not in bound}
    generated = ["import builtins as __blt_expected_builtins\n"]
    imports = [str(folder)]
    if item["metadata"].get("import_root"):
        imports.append(item["metadata"]["import_root"])
    for path, content in item["files"].items():
        target = folder / path
        if Path(path).is_absolute() or not target.resolve().is_relative_to(folder.resolve()):
            for row in rows:
                row.setdefault("excluded", "Fixture requires an external/absolute Python module layout")
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    translated = []
    for index, row in enumerate(rows):
        if row.get("excluded"):
            continue
        if row["file"] != filename:
            row["excluded"] = "Auxiliary-file assertion requires its own lexical replay"
            continue
        try:
            expression = translate(ast.parse(row["expression"], mode="eval").body, module, bindings)
            expected = expected_annotation(row)
            if expected:
                ast.parse(expected, mode="eval")
                helper = f"__blt_expected_{index}"
                generated.append(f"def {helper}() -> {expected}: ...\n")
                row["expected_helper"] = helper
            row["basilisp"] = expression
            translated.append(row)
        except (Unsupported, SyntaxError, ValueError, RecursionError) as error:
            row["excluded"] = str(error)
    if not translated:
        return
    suffix = ".pyi" if filename.endswith(".pyi") else ".py"
    (folder / (module + suffix)).write_text(source + "\n" + "".join(generated), encoding="utf-8")
    cache = bridge.create_cache()
    options = to_lisp({"python-paths": imports, "python-inspection?": False, "python-cache": cache})
    try:
        body = f"(ns blt-typing-audit (:import {module} builtins))\n"
        for index, row in enumerate(translated):
            prefix = f"(def blt-case-{index} "
            row["basilisp_start"] = len(body) + len(prefix)
            body += prefix + row["basilisp"] + ")\n"
            row["basilisp_end"] = len(body) - 2
            row["basilisp_row"] = index + 2
            row["basilisp_column"] = len(prefix) + 1
        result = plain(analyzer.analyze(body, to_lisp({"filename": str(folder / "audit.lpy"), "python-options": options})))
        document_findings = result["findings"]
        internal = internal_analysis_findings(document_findings)
        if internal:
            for row in translated:
                row.update(status="error", error="Analyzer reported a document or inspection failure",
                           findings=document_findings)
            return document_findings
        for row in translated:
            findings = [finding for finding in result["findings"] if finding.get("row") == row["basilisp_row"]]
            expressions = [value for value in result.get("python-expressions", []) if value.get("row") == row["basilisp_row"] and value.get("col") == row["basilisp_start"] - body.rfind("\n", 0, row["basilisp_start"])]
            actual = expressions[-1].get("python-type") if expressions else None
            expected = None
            if row.get("expected_helper"):
                metadata = bridge.inspect_path(module, to_lisp([row["expected_helper"]]), options)
                expected = plain(bridge.return_type(metadata))
            errors = [finding for finding in findings if finding.get("type", "").removeprefix(":") in CALL_ERRORS]
            node = ast.parse(row["expression"], mode="eval").body
            metadata = consumer_metadata(node, module, row, result, bridge, options, to_lisp, bindings)
            contract_known = not isinstance(node, ast.Call) or signature_known(metadata)
            return_known = type_resolved(canonical(actual))
            explicit_any = (row["kind"] == "return" and explicit_any_expectation(row)
                            and expected is not None and canonical(expected).get("any?"))
            row.update(actual=canonical(actual, expected), expected=canonical(expected), findings=findings,
                       coverage={"return_resolved": return_known, "signature_resolved": contract_known})
            if row["expected_error"]:
                row["status"] = "passed" if errors else "unknown" if actual is None or not contract_known else "failed"
            elif errors:
                row["status"] = "failed"
            elif not contract_known:
                row["status"] = "unknown"
            elif not return_known and not explicit_any:
                row["status"] = "unknown"
            elif row["kind"] == "return":
                row["status"] = ("unknown" if actual is None or not (type_resolved(canonical(expected)) or explicit_any)
                                 else "passed" if canonical(actual, expected) == canonical(expected) else "failed")
            else:
                row["status"] = "passed" if actual is not None else "unknown"
        return document_findings
    finally:
        bridge.stop_cache__BANG__(cache)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("python_typing_sources.json"))
    parser.add_argument("--provider", action="append")
    parser.add_argument("--fixture", help="Substring filter, always recorded in report scope")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument("--require-resolved", action="store_true", help="Fail if any selected translated assertion has unknown metadata")
    parser.add_argument("--fetch", action="store_true", help="Download pinned archives and extract only fixture/license paths")
    parser.add_argument("--pyright-oracle", type=Path, help="Pyright --outputjson report over the original Pyright sample corpus")
    args = parser.parse_args()
    entries = json.loads(args.manifest.read_text())
    unknown = set(args.provider or []) - {entry["provider"] for entry in entries}
    if unknown:
        parser.error("Unknown providers: " + ", ".join(sorted(unknown)))
    oracle = None
    if args.pyright_oracle:
        oracle = collections.defaultdict(list)
        payload = json.loads(args.pyright_oracle.read_text())
        for diagnostic in payload["generalDiagnostics"]:
            oracle[diagnostic["file"]].append(diagnostic)
    engine = None
    if not args.inventory_only:
        import basilisp_tools  # noqa: F401 - Install the local Basilisp importer.
        from basilisp.lang.keyword import keyword
        from basilisp.lang.runtime import to_lisp
        engine = importlib.import_module("basilisp_tools.analyzer"), importlib.import_module("basilisp_tools.python"), keyword, to_lisp
    report = {"scope": {"providers": args.provider, "fixture_filter": args.fixture, "python": sys.version,
                         "require_resolved": args.require_resolved,
                         "boundary": "Translated Python interop calls; not whole-Python language conformance"},
              "sources": entries, "fixtures": [], "started": time.time(),
              "source_hashes_start": source_hashes(engine)}
    if engine:
        report["engine"] = {"analyzer": engine[0].__file__, "python_bridge": engine[1].__file__}
    if args.pyright_oracle:
        report["oracle"] = {"version": payload["version"], "summary": payload["summary"],
                            "sha256": hashlib.sha256(args.pyright_oracle.read_bytes()).hexdigest(),
                            "profile": "Pyright CLI defaults plus fixture directives; original TypeScript test-runner settings are not reproduced"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="blt-typing-fixtures-") as directory:
        for entry in report["sources"]:
            if args.provider and entry["provider"] not in args.provider:
                continue
            if args.fetch:
                fetch_source(entry, args.corpus)
            for item in fixtures(entry["provider"], args.corpus / entry["directory"]):
                if args.fixture and args.fixture not in item["path"] + "::" + item["name"]:
                    continue
                rows = discover(item)
                if item["provider"] == "pyright" and engine:
                    if oracle is None:
                        for row in rows:
                            row.setdefault("excluded", "Pyright consumer calls require an independently executed native oracle")
                    else:
                        apply_pyright_oracle(item, rows, oracle)
                if item["metadata"].get("unsupported_environment"):
                    for row in rows:
                        row.setdefault("excluded", item["metadata"]["unsupported_environment"])
                document_findings = None
                if engine:
                    try:
                        document_findings = replay(item, rows, Path(directory), engine)
                    except Exception as error:
                        for row in rows:
                            if not row.get("excluded"):
                                row.update(status="error", error=repr(error))
                for row in rows:
                    row.setdefault("status", "excluded" if row.get("excluded") else "not-run")
                result = {key: item[key] for key in ["provider", "path", "name", "source_sha256", "metadata"]}
                result["cases"] = rows
                if document_findings is not None:
                    result["document_findings"] = document_findings
                report["fixtures"].append(result)
                if len(report["fixtures"]) % 100 == 0:
                    print(entry["provider"], len(report["fixtures"]), "fixtures processed", flush=True)
            counts = collections.Counter(row["status"] for item in report["fixtures"] for row in item["cases"])
            print(entry["provider"], len(report["fixtures"]), dict(counts), flush=True)
            report["counts"] = dict(counts)
            args.output.write_text(json.dumps(report, indent=2) + "\n")
    if not report["fixtures"] or not any(fixture["cases"] for fixture in report["fixtures"]):
        parser.error("Selection contains no discovered assertions")
    if engine and not any(report["counts"].get(status, 0) for status in ("passed", "failed", "unknown", "error")):
        parser.error("Selection contains no translated assertions")
    report["seconds"] = time.time() - report["started"]
    report["source_hashes_end"] = source_hashes(engine)
    report["source_changed_during_run"] = report["source_hashes_start"] != report["source_hashes_end"]
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    failures = ["failed", "error"] + (["unknown"] if args.require_resolved else [])
    return int(any(report["counts"].get(status, 0) for status in failures))


if __name__ == "__main__":
    raise SystemExit(main())
