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
import keyword as pykeyword
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
SOURCE_NAME_ERROR = "unresolved-python-source-name"
PYRIGHT_CALL_ERRORS = {"reportCallIssue", "reportArgumentType", "reportAttributeAccessIssue", "reportIndexIssue", "reportOperatorIssue"}
SNAPSHOT_CALL_ERRORS = {
    "invalid-argument-type", "missing-argument", "too-many-positional-arguments",
    "unknown-argument", "parameter-already-assigned", "no-matching-overload",
    "call-non-callable", "unresolved-attribute", "invalid-attribute-access",
    "possibly-missing-attribute", "not-subscriptable", "index-out-of-bounds",
    "unsupported-operator", "deprecated",
}
SUPPRESSION_CODES = {
    "name-defined": {SOURCE_NAME_ERROR},
    "arg-type": {"type-mismatch"}, "call-arg": {"invalid-arity"},
    "call-overload": {"type-mismatch", "invalid-arity"},
    "attr-defined": {"unresolved-python-member"}, "union-attr": {"unresolved-python-member"},
    "index": {"type-mismatch", "invalid-arity"}, "operator": {"type-mismatch"},
    "invalid-argument-type": {"type-mismatch"}, "bad-argument-type": {"type-mismatch"},
    "missing-argument": {"invalid-arity"}, "too-many-positional-arguments": {"invalid-arity"},
    "unexpected-keyword": {"invalid-arity"}, "unknown-argument": {"invalid-arity"},
    "reportArgumentType": {"type-mismatch"}, "reportCallIssue": {"invalid-arity"},
    "reportAttributeAccessIssue": {"unresolved-python-member"},
    "reportIndexIssue": {"type-mismatch", "invalid-arity"}, "reportOperatorIssue": {"type-mismatch"},
    "deprecated": {"deprecated-var"}, "reportDeprecated": {"deprecated-var"},
}


class Unsupported(ValueError):
    pass


class LocalName(ast.Name):
    """A translated lexical binding, distinct from a Python module export."""


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
        pattern = r"#\s*(E(?:\?|\[[^]]+\])?(?::|\s|$)|error:|revealed:|N:|snapshot:)"
        starts = list(re.finditer(pattern, text))
        for index, comment in enumerate(starts):
            end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
            target = pending if not line[:column].strip() else result[number]
            target.append(text[comment.start(1):end].strip())
    return dict(result)


def expects_diagnostic(notes):
    return any((re.match(r"E(?:\s|:|$)|error:", note) and "revealed type:" not in note.lower())
               or (note.startswith("snapshot:") and note.split(":", 1)[1].strip() in SNAPSHOT_CALL_ERRORS)
               for note in notes)


def suppressions(source, provider):
    """Retain explicit upstream ignores as scoped Basilisp linter configuration."""
    result = {}
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for token in tokens:
            if token.type != tokenize.COMMENT:
                continue
            match = re.search(r"#\s*(type|pyright|pyrefly|ty):\s*ignore(?:\[([^]]*)\])?", token.string)
            if not match or match[1] not in {"type", provider}:
                continue
            codes = [code.strip() for code in match[2].split(",")] if match[2] is not None else []
            all_codes = CALL_ERRORS | {"deprecated-var", SOURCE_NAME_ERROR}
            if not codes:
                ignored = all_codes
            elif provider == "pyrefly" and match[1] == "type":
                own_codes = [code.removeprefix("pyrefly:") for code in codes if code.startswith("pyrefly:")]
                ignored = (set().union(*(SUPPRESSION_CODES.get(code, set()) for code in own_codes))
                           if own_codes else all_codes)
            else:
                ignored = set().union(*(SUPPRESSION_CODES.get(code, set()) for code in codes))
            conditional = provider == "mypy" and "misc" in codes
            if ignored or conditional:
                result[token.start[0]] = {"comment": match[0], "diagnostics": sorted(ignored)}
                if conditional:
                    result[token.start[0]]["conditional"] = "mypy-too-many-positional"
    except (tokenize.TokenError, IndentationError):
        pass
    return result


def immutable_literal_expression(node, bindings):
    """Copy a provable immutable value without evaluating upstream fixture code."""
    if isinstance(node, ast.Constant) and (node.value is None or type(node.value) in (bool, int, float, str, bytes)):
        return copy.deepcopy(node)
    if isinstance(node, ast.Name) and node.id in bindings:
        return copy.deepcopy(bindings[node.id])
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        operand = immutable_literal_expression(node.operand, bindings)
        if isinstance(operand, ast.Constant) and type(operand.value) in (int, float):
            return ast.copy_location(ast.Constant(-operand.value if isinstance(node.op, ast.USub) else operand.value), node)
    if isinstance(node, ast.Tuple):
        values = [immutable_literal_expression(value, bindings) for value in node.elts]
        if all(value is not None for value in values):
            return ast.copy_location(ast.Tuple(elts=values, ctx=ast.Load()), node)
    return None


def literal_bindings_before(tree, line):
    """Replay immutable module assignments up to the consumer's source position."""
    bindings = {}
    for statement in tree.body:
        if statement.lineno >= line:
            break
        value = None
        targets = []
        if isinstance(statement, (ast.Assign, ast.AnnAssign)):
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            value = immutable_literal_expression(statement.value, bindings) if statement.value is not None else None
            # Preserve explicit abstract annotations used by upstream nonliteral tests.
            # Literal and Final annotations alone promise the retained constant fact.
            if isinstance(statement, ast.AnnAssign):
                annotation = statement.annotation
                origin = annotation.value if isinstance(annotation, ast.Subscript) else annotation
                if name_of(origin) not in {"Final", "Literal"}:
                    value = None
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            writes = {statement.name}
        else:
            writes = {node.id for node in ast.walk(statement)
                      if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))}
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            writes.update(alias.asname or alias.name.split('.')[0] for alias in statement.names)
        for name in writes:
            bindings.pop(name, None)
        if value is not None:
            for target in targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = copy.deepcopy(value)
    return bindings


def comprehension_bindings(node, parents, source):
    """Retain only generators whose bindings are in scope at this assertion."""
    groups = []
    ancestor = parents.get(node)
    while ancestor and not isinstance(ancestor, ast.Module):
        if isinstance(ancestor, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            generators = []
            for generator in ancestor.generators:
                if node in ast.walk(generator.iter):
                    break
                generators.append({"target": ast.get_source_segment(source, generator.target),
                                   "iterable": ast.get_source_segment(source, generator.iter),
                                   "filters": [ast.get_source_segment(source, value) for value in generator.ifs],
                                   "async": bool(generator.is_async)})
                if any(node in ast.walk(value) for value in generator.ifs):
                    raise Unsupported("Assertion inside a comprehension filter requires preserving filter order")
            groups.append(generators)
        ancestor = parents.get(ancestor)
    return [generator for group in reversed(groups) for generator in group]


def lexical_comprehension(row, module, bindings):
    """Build equivalent for bindings without evaluating fixture iterables."""
    bindings = dict(bindings)
    clauses = []
    locals_ = {}
    local_count = 0

    def target(node):
        nonlocal local_count
        if isinstance(node, ast.Name):
            local = LocalName(id=f"blt-comprehension-{local_count}", ctx=ast.Load())
            local_count += 1
            locals_[node.id] = local
            bindings[node.id] = local
            return local.id
        if isinstance(node, (ast.Tuple, ast.List)):
            return "[" + " ".join(target(item) for item in node.elts) + "]"
        raise Unsupported("Comprehension target requires unsupported destructuring: " + type(node).__name__)

    for generator in row.get("comprehension_bindings", []):
        if generator["async"]:
            raise Unsupported("Async comprehension requires its async iterator contract")
        iterable = translate(ast.parse(generator["iterable"], mode="eval").body, module, bindings)
        pattern = target(ast.parse(generator["target"], mode="eval").body)
        clauses.extend([pattern, iterable])
        for value in generator["filters"]:
            clauses.extend([":when", translate(ast.parse(value, mode="eval").body, module, bindings)])
    return bindings, ("(for [" + " ".join(clauses) + "] " if clauses else ""), locals_


def display_parameter_slots(tree, line=None):
    """Identify local ParamSpec positions independently of inferred result types."""
    imports, parameters, classes = {}, set(), {}

    def origin(node):
        if isinstance(node, ast.Name):
            return imports.get(node.id)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if imports.get(node.value.id) in ("typing", "typing_extensions"):
                return node.attr
        return None

    for statement in tree.body:
        if line is not None and statement.lineno >= line:
            break
        if isinstance(statement, ast.ImportFrom) and statement.module in ("typing", "typing_extensions"):
            imports.update({alias.asname or alias.name: alias.name for alias in statement.names})
        elif isinstance(statement, ast.Import):
            imports.update({alias.asname or alias.name: alias.name for alias in statement.names
                            if alias.name in ("typing", "typing_extensions")})
        elif isinstance(statement, ast.Assign):
            declared = isinstance(statement.value, ast.Call) and origin(statement.value.func) == "ParamSpec"
            for target in statement.targets:
                if isinstance(target, ast.Name):
                    imports.pop(target.id, None)
                    parameters.discard(target.id)
                    classes.pop(target.id, None)
                    if declared:
                        parameters.add(target.id)
        elif isinstance(statement, ast.ClassDef):
            classes.pop(statement.name, None)
            for base in statement.bases:
                if isinstance(base, ast.Subscript) and origin(base.value) in ("Generic", "Protocol"):
                    arguments = base.slice.elts if isinstance(base.slice, ast.Tuple) else [base.slice]
                    slots = [isinstance(argument, ast.Name) and argument.id in parameters for argument in arguments]
                    if any(slots):
                        classes[statement.name] = slots
            if getattr(statement, "type_params", []):
                slots = [type(parameter).__name__ == "ParamSpec" for parameter in statement.type_params]
                if any(slots):
                    classes[statement.name] = slots
            imports.pop(statement.name, None)
            parameters.discard(statement.name)
        elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            imports.pop(statement.name, None)
            parameters.discard(statement.name)
            classes.pop(statement.name, None)
        else:
            writes = {node.id for node in ast.walk(statement)
                      if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))}
            writes.update(node.name for node in ast.walk(statement)
                          if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)))
            for name in writes:
                imports.pop(name, None)
                parameters.discard(name)
                classes.pop(name, None)
    return classes


def discover(item):
    """Inventory assertions and direct consumer calls, with their lexical context."""
    rows = []
    for filename, source in item["files"].items():
        if not filename.endswith((".py", ".pyi")):
            continue
        comments = markers(source)
        ignored = suppressions(source, item["provider"])
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
                if isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.If, ast.For, ast.While, ast.Try, ast.With, ast.Match, ast.Lambda)) or type(ancestor).__name__ == "TryStar":
                    context.append(type(ancestor).__name__)
                ancestor = parents.get(ancestor)
            row = {"file": filename, "line": node.lineno, "end_line": node.end_lineno, "column": node.col_offset,
                   "oracle_start": [node.lineno - 1, utf16_column(source, node.lineno, node.col_offset)],
                   "oracle_end": [node.end_lineno - 1, utf16_column(source, node.end_lineno, node.end_col_offset)],
                   "kind": "return" if assertion else "call", "expression": ast.get_source_segment(source, expression),
                   "expected_annotation": expectation, "expected_display": expected_text,
                   "markers": notes, "context": context,
                   "expected_error": expects_diagnostic(notes)}
            target = parent.targets[0] if isinstance(parent, ast.Assign) and len(parent.targets) == 1 else None
            annotated_module_target = isinstance(target, ast.Name) and any(
                isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
                and statement.target.id == target.id and statement.lineno < node.lineno for statement in tree.body)
            if isinstance(target, ast.Attribute) or annotated_module_target:
                row["assignment_target"] = ast.get_source_segment(source, parent.targets[0])
                if annotated_module_target:
                    row["assignment_module_target"] = target.id
                row["assignment_statement"] = ast.get_source_segment(source, parent)
                row["oracle_start"] = [parent.lineno - 1, utf16_column(source, parent.lineno, parent.col_offset)]
                row["oracle_end"] = [parent.end_lineno - 1, utf16_column(source, parent.end_lineno, parent.end_col_offset)]
            if row["expected_error"]:
                row["expected_diagnostic_types"] = (["deprecated-var"] if any("deprecated" in note.lower() for note in notes)
                                                     else sorted(CALL_ERRORS))
            if expected_text and item["provider"] == "pyright":
                display_slots = display_parameter_slots(tree, node.lineno)
                if display_slots:
                    row["display_parameter_slots"] = display_slots
            row_suppressions = [value for line, value in ignored.items() if node.lineno <= line <= node.end_lineno]
            if row_suppressions:
                row["suppression_directives"] = row_suppressions
            row["assertion_source"] = "type-directive" if assertion else "expected-diagnostic" if notes else "consumer-call"
            try:
                generators = comprehension_bindings(node, parents, source)
                if generators:
                    row["comprehension_bindings"] = generators
            except Unsupported as error:
                row["excluded"] = str(error)
            local_names = {part.id for generator in row.get("comprehension_bindings", [])
                           for part in ast.walk(ast.parse(generator["target"], mode="eval")) if isinstance(part, ast.Name)}
            literal_bindings = literal_bindings_before(tree, node.lineno)
            rebound = sorted({part.id for part in ast.walk(expression) if isinstance(part, ast.Name)
                              and part.id not in local_names
                              and part.id not in literal_bindings
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
        allowed = PYRIGHT_CALL_ERRORS | ({"reportAssignmentType"} if row.get("assignment_target") else set())
        if errors and not all(error.get("rule") in allowed for error in errors):
            row["excluded"] = "Native oracle rejects a non-call aspect of this Python statement"
        elif row["kind"] == "return" and errors:
            row["excluded"] = "Upstream reveal expression is rejected by the independent oracle"
        else:
            row["expected_error"] = bool(errors)
            if errors:
                row["expected_diagnostic_types"] = sorted(CALL_ERRORS)


def translate(node, module, bindings=None):
    if bindings is not None:
        class Substitute(ast.NodeTransformer):
            def visit_Name(self, name):
                if name.id in bindings:
                    value = bindings[name.id]
                    return ast.copy_location(copy.deepcopy(value) if isinstance(value, ast.AST)
                                             else ast.Name(id=value, ctx=ast.Load()), name)
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
        if isinstance(node.value, bytes):
            return '#b"' + ''.join(f'\\x{value:02x}' for value in node.value) + '"'
        raise Unsupported("Unsupported Python literal: " + type(node.value).__name__)
    if isinstance(node, ast.Name):
        return node.id if isinstance(node, LocalName) or "/" in node.id else module + "/" + node.id
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
        if any(not key.isidentifier() or pykeyword.iskeyword(key) for key, _ in keywords):
            mapping = "{" + " ".join(part for key, value in keywords
                                      for part in (json.dumps(key, ensure_ascii=False), translate(value, module))) + "}"
            target = translate(node.func, module)
            return "(" + " ".join(["apply-kw", target, *values, mapping]) + ")"
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

            def visit_Subscript(self, node):
                node = self.generic_visit(node)
                slots = row.get("display_parameter_slots", {}).get(node.value.id) if isinstance(node.value, ast.Name) else None
                if not slots:
                    return node
                arguments = list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]
                if len(slots) == 1 and slots[0] and len(arguments) > 1:
                    arguments = [ast.List(elts=arguments, ctx=ast.Load())]
                elif len(arguments) == len(slots):
                    arguments = [ast.List(elts=list(argument.elts) if isinstance(argument, ast.Tuple) else [argument], ctx=ast.Load())
                                 if parameter and not isinstance(argument, ast.List)
                                 and not (isinstance(argument, ast.Constant) and argument.value is Ellipsis) else argument
                                 for parameter, argument in zip(slots, arguments)]
                else:
                    return node
                node.slice = arguments[0] if len(arguments) == 1 else ast.Tuple(elts=arguments, ctx=ast.Load())
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
    calls = [call for call in result.get("calls", [])
             if call.get("python") is True
             and row.get("basilisp_start") is not None and row.get("basilisp_end") is not None
             and call.get("start") == row["basilisp_start"] and call.get("end") == row["basilisp_end"]
             and signature_known(call.get("definition"))
             and call["definition"].get("kind", "").removeprefix(":") in {"function", "class"}]
    if len(calls) == 1:
        return calls[0]["definition"]
    if isinstance(node.func, ast.Name):
        target = bindings.get(node.func.id, module + "/" + node.func.id)
        if not isinstance(target, str):
            return None
        owner, name = target.split("/", 1)
        return plain(bridge.inspect_path(owner, to_lisp([name]), options))
    if isinstance(node.func, ast.Attribute):
        receiver = node.func.value
        if isinstance(receiver, ast.Name) and not isinstance(bindings.get(receiver.id), ast.AST):
            return plain(bridge.inspect_path(module, to_lisp([receiver.id, node.func.attr]), options))
        translated = translate(receiver, module, bindings)
        offset = row["basilisp"].find(translated)
        column = row["basilisp_column"] + offset
        types = [value.get("python-type") for value in result.get("python-expressions", [])
                 if value.get("row") == row["basilisp_row"] and value.get("col") == column]
        if offset >= 0 and types and types[-1]:
            return plain(bridge.inspect_member(to_lisp(types[-1]), node.func.attr, options))
    return None


def conditionally_suppressed_findings(node, metadata, row, findings):
    """Mypy's misc alias covers keyword-only positional misuse, not all arity errors."""
    if not any(directive.get("conditional") == "mypy-too-many-positional"
               for directive in row.get("suppression_directives", [])):
        return []
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
        return []
    if not signature_known(metadata) or metadata.get("overloads"):
        return []
    kinds = [parameter.get("kind", "").removeprefix(":") for parameter in metadata["parameters"]]
    if "var-positional" in kinds:
        return []
    positional = sum(kind in {"positional-only", "positional-or-keyword"} for kind in kinds)
    keyword_only = kinds.count("keyword-only")
    supplied = 0
    for argument in node.args:
        if isinstance(argument, ast.Starred):
            if not isinstance(argument.value, (ast.List, ast.Tuple)):
                return []
            supplied += len(argument.value.elts)
        else:
            supplied += 1
    if not positional < supplied <= positional + keyword_only:
        return []
    message = f"Expected at most {positional} positional arguments, received {supplied}"
    return [finding for finding in findings
            if finding.get("type", "").removeprefix(":") == "invalid-arity"
            and finding.get("message") == message
            and finding.get("col") == row["basilisp_column"]]


def internal_analysis_findings(findings):
    """Internal failures cannot satisfy an expected argument diagnostic."""
    return [finding for finding in findings
            if finding.get("type", "").removeprefix(":") in {"file", "syntax", "python-inspection"}
            or finding.get("message", "").startswith("Analysis failed:")]


def configured_source(source, provider):
    """Express mypy's explicit implicit-optional policy in ordinary annotations."""
    if provider != "mypy":
        return source, []
    enabled = False
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type != tokenize.COMMENT:
                continue
            directive = re.fullmatch(r"#\s*mypy:\s*(.*)", token.string)
            if directive:
                for option in directive[1].split(","):
                    match = re.fullmatch(r"\s*(no[-_])?implicit[-_]optional(?:\s*=\s*(true|false))?\s*", option, re.I)
                    if match:
                        enabled = not bool(match[1]) and (match[2] or "true").lower() == "true"
    except (tokenize.TokenError, IndentationError):
        return source, []
    if not enabled:
        return source, []
    tree = ast.parse(source)
    alias = "__blt_config_typing"
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names.update(node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
    names.update(alias.asname or alias.name.split('.')[0] for node in ast.walk(tree)
                 if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names)
    while alias in names:
        alias += "_"
    encoded = source.encode("utf-8")
    lines = encoded.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    edits, annotations = [], []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        positional = [*node.args.posonlyargs, *node.args.args]
        defaults = [None] * (len(positional) - len(node.args.defaults)) + node.args.defaults
        for argument, default in [*zip(positional, defaults), *zip(node.args.kwonlyargs, node.args.kw_defaults)]:
            if argument.annotation is None or not isinstance(default, ast.Constant) or default.value is not None:
                continue
            hint = argument.annotation
            text = ast.get_source_segment(source, hint)
            replacement = f"{alias}.Optional[{text}]"
            edits.append((offsets[hint.lineno - 1] + hint.col_offset,
                          offsets[hint.end_lineno - 1] + hint.end_col_offset, replacement.encode("utf-8")))
            annotations.append({"line": hint.lineno, "column": hint.col_offset,
                                "before": text, "after": replacement})
    if not edits:
        return source, []
    import_line = 0
    for index, node in enumerate(tree.body):
        if (index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)) or isinstance(node, ast.ImportFrom) and node.module == "__future__":
            import_line = node.end_lineno
        else:
            break
    edits.append((offsets[import_line], offsets[import_line], f"import typing as {alias}\n".encode()))
    for start, end, replacement in sorted(edits, reverse=True):
        encoded = encoded[:start] + replacement + encoded[end:]
    adapted = encoded.decode("utf-8")
    return adapted, [{"provider": "mypy", "option": "implicit-optional", "annotations": annotations,
                      "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                      "adapted_sha256": hashlib.sha256(encoded).hexdigest()}]


def finding_type(finding):
    """Distinguish the analyzer's Python-member diagnostic from lexical errors."""
    kind = finding.get("type", "").removeprefix(":")
    if kind == "unresolved-symbol" and finding.get("message", "").startswith("Unresolved Python member: "):
        return "unresolved-python-member"
    return kind


def source_name_symbols(node, tree, module, bound, lexical_names):
    """Find undeclared source names without trusting arbitrary adapted lexical errors."""
    if any(isinstance(part, ast.ImportFrom) and any(alias.name == "*" for alias in part.names)
           or isinstance(part, ast.Call) and isinstance(part.func, ast.Name)
           and part.func.id in {"exec", "eval", "globals", "locals"}
           for part in ast.walk(tree)):
        return []
    implicit = {"__name__", "__file__", "__spec__", "__loader__", "__package__", "__doc__",
                "__cached__", "__builtins__", "__annotations__"}
    declared = bound | set(dir(builtins)) | set(lexical_names) | implicit
    missing = {part.id for part in ast.walk(node)
               if isinstance(part, ast.Name) and isinstance(part.ctx, ast.Load)
               and part.id not in declared}
    return [module + "/" + name for name in sorted(missing)]


def row_finding_type(row, finding):
    """Name-resolution checks require the source proof and exact generated symbol."""
    kind = finding_type(finding)
    if kind == "unresolved-symbol" and finding.get("message") in {
            "Unresolved symbol: " + name for name in row.get("source_name_symbols", [])}:
        return SOURCE_NAME_ERROR
    return kind


def diagnostic_status(row):
    """A concrete diagnostic overrides incomplete return/signature metadata."""
    findings = row.get("findings", [])
    if any(row_finding_type(row, finding) == "unresolved-symbol" for finding in findings):
        return "error"
    if row.get("expected_error"):
        expected = row.get("expected_diagnostic_types", CALL_ERRORS)
        if any(row_finding_type(row, finding) in expected for finding in findings):
            return "passed"
    elif any(row_finding_type(row, finding) in CALL_ERRORS | {SOURCE_NAME_ERROR} for finding in findings):
        return "failed"
    return None


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
        if path.endswith((".py", ".pyi")):
            content, adaptations = configured_source(content, item["provider"])
            if adaptations:
                item["metadata"].setdefault("configuration_adaptations", []).extend(
                    {"file": path, **adaptation} for adaptation in adaptations)
            if path == filename:
                source = content
        target.write_text(content, encoding="utf-8")
    translated = []
    for index, row in enumerate(rows):
        if row.get("excluded"):
            continue
        if row["file"] != filename:
            row["excluded"] = "Auxiliary-file assertion requires its own lexical replay"
            continue
        try:
            node = ast.parse(row["expression"], mode="eval").body
            literals = literal_bindings_before(tree, row["line"])
            used = {part.id for part in ast.walk(node) if isinstance(part, ast.Name)}
            row["literal_bindings"] = {name: ast.unparse(value) for name, value in literals.items() if name in used}
            scope, context, locals_ = lexical_comprehension(row, module, {**bindings, **literals})
            expression = translate(node, module, scope)
            row["source_name_symbols"] = source_name_symbols(node, tree, module, bound, locals_)
            if row["source_name_symbols"] and row.get("expected_error"):
                notes = row.get("markers", [])
                name_expected = any("[name-defined]" in note or "is not defined" in note for note in notes)
                unspecified = bool(notes) and all(re.fullmatch(r"E\s*:\s*", note) for note in notes)
                if name_expected or unspecified:
                    row["expected_diagnostic_types"] = [SOURCE_NAME_ERROR]
                    row["diagnostic_oracle_category"] = "source-name-resolution"
            closing = ")" if context else ""
            if context:
                row["lexical_names"] = {name: local.id for name, local in locals_.items()}
            if row.get("assignment_target"):
                target = ast.parse(row["assignment_target"], mode="eval").body
                if row.get("assignment_module_target"):
                    rendered_target = f'(.-{target.id} (python/__import__ {json.dumps(module)} nil nil #py ["*"]))'
                else:
                    rendered_target = translate(target, module, {**bindings, **literals})
                context += "(set! " + rendered_target + " "
                closing += ")"
            if context:
                row["basilisp_context"] = context + expression + closing
                row["basilisp_context_offset"] = len(context)
            expected = expected_annotation(row)
            if expected:
                row["expected_rendered_annotation"] = expected
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
            if row.get("suppression_directives"):
                ignored = sorted({kind for directive in row["suppression_directives"]
                                  for kind in directive["diagnostics"]
                                  if kind not in {"unresolved-python-member", SOURCE_NAME_ERROR}})
                if ignored:
                    prefix = "#_{:clj-kondo/ignore [" + " ".join(":" + kind for kind in ignored) + "]} " + prefix
            offset = row.get("basilisp_context_offset", 0)
            row["basilisp_start"] = len(body) + len(prefix) + offset
            body += prefix + row.get("basilisp_context", row["basilisp"]) + ")\n"
            row["basilisp_end"] = row["basilisp_start"] + len(row["basilisp"])
            row["basilisp_row"] = index + 2
            row["basilisp_column"] = len(prefix) + offset + 1
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
            node = ast.parse(row["expression"], mode="eval").body
            literal_bindings = {name: ast.parse(value, mode="eval").body for name, value in row.get("literal_bindings", {}).items()}
            lexical_names = {name: LocalName(id=local, ctx=ast.Load()) for name, local in row.get("lexical_names", {}).items()}
            metadata = consumer_metadata(node, module, row, result, bridge, options, to_lisp,
                                         {**bindings, **literal_bindings, **lexical_names})
            suppressed = conditionally_suppressed_findings(node, metadata, row, findings)
            if any("unresolved-python-member" in directive["diagnostics"]
                   for directive in row.get("suppression_directives", [])):
                suppressed.extend(finding for finding in findings
                                  if finding_type(finding) == "unresolved-python-member")
            if any(SOURCE_NAME_ERROR in directive["diagnostics"]
                   for directive in row.get("suppression_directives", [])):
                suppressed.extend(finding for finding in findings
                                  if row_finding_type(row, finding) == SOURCE_NAME_ERROR)
            if suppressed:
                row["suppressed_findings"] = suppressed
                findings = [finding for finding in findings if finding not in suppressed]
            contract_known = not isinstance(node, ast.Call) or signature_known(metadata)
            return_known = type_resolved(canonical(actual))
            explicit_any = (row["kind"] == "return" and explicit_any_expectation(row)
                            and expected is not None and canonical(expected).get("any?"))
            row.update(actual=canonical(actual, expected), expected=canonical(expected), findings=findings,
                       coverage={"return_resolved": return_known, "signature_resolved": contract_known})
            diagnosed = diagnostic_status(row)
            if diagnosed:
                row["status"] = diagnosed
                if diagnosed == "error":
                    row["error"] = "Unresolved lexical symbol in the adapted expression"
            elif row["expected_error"]:
                row["status"] = "unknown" if actual is None or not contract_known else "failed"
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


def load_selection(path):
    """Require exact pinned fixtures and reviewed result counts for strict CI."""
    if path is None:
        return None
    result = {}
    for item in json.loads(path.read_text()):
        if set(item) != {"provider", "path", "name", "source_sha256", "expected"}:
            raise ValueError("Selection entries need provider/path/name/source_sha256/expected")
        key = tuple(item[name] for name in ("provider", "path", "name"))
        if not all(isinstance(value, str) and value for value in key) or key in result:
            raise ValueError("Selection fixtures must have unique nonempty identities")
        if not re.fullmatch(r"[0-9a-f]{64}", item["source_sha256"]):
            raise ValueError("Selection fixtures need an exact SHA256")
        expected = item["expected"]
        keys = {"passed", "excluded", "positive_calls", "negative_calls", "return_assertions"}
        if set(expected) != keys or any(type(value) is not int or value < 0 for value in expected.values()):
            raise ValueError("Selection entries need nonnegative exact result and coverage counts")
        if expected["passed"] == 0:
            raise ValueError("A strict selected fixture must exercise a resolved assertion")
        result[key] = item
    if not result:
        raise ValueError("Selection must contain at least one fixture")
    return result


def selection_result(fixture):
    rows = fixture["cases"]
    statuses = collections.Counter(row["status"] for row in rows)
    return {**{status: statuses[status] for status in ("passed", "excluded")},
            **{status: count for status, count in statuses.items() if status not in {"passed", "excluded"}},
            "positive_calls": sum(row["status"] == "passed" and row["kind"] == "call"
                                  and not row.get("expected_error") for row in rows),
            "negative_calls": sum(row["status"] == "passed" and row.get("expected_error", False)
                                  for row in rows),
            "return_assertions": sum(row["status"] == "passed" and row["kind"] == "return"
                                     and not row.get("expected_error") for row in rows)}


def validate_selection(report, selection):
    if selection is None:
        return []
    seen = set()
    failures = []
    for fixture in report["fixtures"]:
        key = tuple(fixture[name] for name in ("provider", "path", "name"))
        if key in seen or key not in selection:
            failures.append({"fixture": key, "reason": "Unexpected or duplicate selected fixture"})
            continue
        seen.add(key)
        expected = selection[key]
        actual = selection_result(fixture)
        if fixture["source_sha256"] != expected["source_sha256"] or actual != expected["expected"]:
            failures.append({"fixture": key, "expected": expected,
                             "actual": {"source_sha256": fixture["source_sha256"], "counts": actual}})
    failures.extend({"fixture": key, "reason": "Selected fixture was not discovered"}
                    for key in selection.keys() - seen)
    return failures


def oracle_target(payload, host_version=None):
    """Retain legacy oracle provenance and validate an explicitly pinned target."""
    invocation = payload.get("blt_invocation", {})
    command = invocation.get("command", [])
    version = invocation.get("python_version")
    targets = [argument.split("=", 1)[1] for argument in command if argument.startswith("--pythonversion=")]
    for index, argument in enumerate(command):
        if argument == "--pythonversion":
            if index + 1 >= len(command):
                raise ValueError("Pyright invocation lacks its --pythonversion value")
            targets.append(command[index + 1])
    for target in targets:
        if version is not None and version != target:
            raise ValueError("Pyright invocation target metadata disagrees with its command")
        version = target
    host = host_version or f"{sys.version_info.major}.{sys.version_info.minor}"
    if version is not None and version != host:
        raise ValueError(f"Pyright oracle targets Python {version}, but replay uses Python {host}")
    return {"python_version": version, "matches_replay_python": True if version is not None else None,
            "profile": ("Explicit matching Python target plus fixture directives; original TypeScript test-runner settings are not reproduced"
                        if version is not None else "Pyright CLI defaults plus fixture directives; Python target is unverified and original TypeScript test-runner settings are not reproduced")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("python_typing_sources.json"))
    parser.add_argument("--provider", action="append")
    parser.add_argument("--selection", type=Path, help="Exact pinned fixture/result manifest for strict CI")
    parser.add_argument("--fixture", help="Substring filter, always recorded in report scope")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument("--require-resolved", action="store_true", help="Fail if any selected translated assertion has unknown metadata")
    parser.add_argument("--fetch", action="store_true", help="Download pinned archives and extract only fixture/license paths")
    parser.add_argument("--pyright-oracle", type=Path, help="Pyright --outputjson report over the original Pyright sample corpus")
    args = parser.parse_args()
    if args.selection and (args.provider or args.fixture or args.inventory_only):
        parser.error("--selection cannot be combined with provider/fixture filters or inventory-only")
    selection = load_selection(args.selection)
    entries = json.loads(args.manifest.read_text())
    unknown = set(args.provider or []) - {entry["provider"] for entry in entries}
    if unknown:
        parser.error("Unknown providers: " + ", ".join(sorted(unknown)))
    oracle = None
    if args.pyright_oracle:
        oracle = collections.defaultdict(list)
        payload = json.loads(args.pyright_oracle.read_text())
        try:
            target = oracle_target(payload)
        except ValueError as error:
            parser.error(str(error))
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
    if selection is not None:
        report["scope"]["selection"] = {"sha256": hashlib.sha256(args.selection.read_bytes()).hexdigest(),
                                        "fixtures": list(selection.values())}
    if engine:
        report["engine"] = {"analyzer": engine[0].__file__, "python_bridge": engine[1].__file__}
    if args.pyright_oracle:
        report["oracle"] = {"version": payload["version"], "summary": payload["summary"],
                            "sha256": hashlib.sha256(args.pyright_oracle.read_bytes()).hexdigest(),
                            **target}
        if "blt_invocation" in payload:
            report["oracle"]["invocation"] = payload["blt_invocation"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="blt-typing-fixtures-") as directory:
        for entry in report["sources"]:
            if args.provider and entry["provider"] not in args.provider:
                continue
            if selection is not None and entry["provider"] not in {key[0] for key in selection}:
                continue
            if args.fetch:
                fetch_source(entry, args.corpus)
            for item in fixtures(entry["provider"], args.corpus / entry["directory"]):
                if args.fixture and args.fixture not in item["path"] + "::" + item["name"]:
                    continue
                if selection is not None and tuple(item[name] for name in ("provider", "path", "name")) not in selection:
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
    report["selection_failures"] = validate_selection(report, selection)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    failures = ["failed", "error"] + (["unknown"] if args.require_resolved else [])
    return int(bool(report["selection_failures"]) or any(report["counts"].get(status, 0) for status in failures))


if __name__ == "__main__":
    raise SystemExit(main())
