"""Adapt PyTorch's pinned typing fixtures to executable Basilisp interop calls.

The input directory contains the upstream test/typing/{pass,fail,reveal} files.
Every statement is inventoried, including unsupported Python-only constructs.
Adapted programs run in the selected Torch interpreter before static analysis;
runtime-invalid upstream examples remain visible and are not positive oracles.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request

from check_ml_packages import audit_failure_findings, plain


class NotAdapted(ValueError):
    pass


OPERATORS = {
    ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/",
    ast.Mod: "operator.mod", ast.BitAnd: "bit-and", ast.BitOr: "bit-or",
    ast.BitXor: "bit-xor", ast.LShift: "bit-shift-left", ast.RShift: "bit-shift-right",
    ast.FloorDiv: "operator.floordiv", ast.Pow: "operator.pow", ast.MatMult: "operator.matmul",
    ast.Eq: "operator.eq", ast.NotEq: "operator.ne", ast.Lt: "operator.lt",
    ast.LtE: "operator.le", ast.Gt: "operator.gt", ast.GtE: "operator.ge",
}


class Adapter:
    def __init__(self, filename, source, package_root, companion):
        self.filename = filename
        self.source = source
        self.package_root = package_root
        self.companion = companion
        self.names = {}
        self.modules = {}
        self.aliases = {}
        self.rows = []
        self.forms = []
        self.adaptations = []
        self.directory_names = {}
        self.used_fixtures = set()

    def imported(self, module, member=None):
        alias = self.aliases.setdefault(module, "py_" + str(len(self.aliases)))
        return alias + "/" + member if member else alias

    def dotted(self, name):
        parts = name.split(".")
        if parts[0] == self.companion and len(parts) > 1:
            self.used_fixtures.add(parts[1])
        module_count = 1
        for index in range(2, len(parts) + 1):
            path = self.package_root.joinpath(*parts[:index])
            if path.parent not in self.directory_names:
                self.directory_names[path.parent] = {item.name for item in path.parent.iterdir()} if path.parent.is_dir() else set()
            names = self.directory_names[path.parent]
            if path.name in names and path.is_dir() or path.name + ".py" in names or path.name + ".pyi" in names:
                module_count = index
        if module_count == len(parts):
            return self.imported(name)
        value = self.imported(".".join(parts[:module_count]), parts[module_count])
        for part in parts[module_count + 1:]:
            value = f"(.-{part} {value})"
        return value

    def qualified(self, node):
        if isinstance(node, ast.Name):
            return self.modules.get(node.id)
        if isinstance(node, ast.Attribute) and (parent := self.qualified(node.value)):
            return parent + "." + node.attr
        return None

    def expr(self, node):
        if isinstance(node, ast.Constant):
            value = node.value
            if value is None:
                return "nil"
            if isinstance(value, bool):
                return str(value).lower()
            if isinstance(value, str):
                if value in ("cuda", "cuda:0"):
                    self.adaptations.append({"row": node.lineno, "from": value, "to": "cpu", "reason": "CPU device exercises the same call contract"})
                    value = "cpu"
                return json.dumps(value)
            if isinstance(value, complex):
                return f"({self.imported('builtins', 'complex')} {value.real} {value.imag})"
            if isinstance(value, (int, float)):
                return repr(value)
            raise NotAdapted("Python literal " + repr(value))
        if isinstance(node, ast.Name):
            if node.id in self.modules:
                return self.dotted(self.modules[node.id])
            if node.id in self.names:
                return self.names[node.id]
            if node.id in ("int", "float", "bool", "str", "tuple", "list", "complex", "slice"):
                return self.imported("builtins", node.id)
            raise NotAdapted("No adapted definition for " + node.id)
        if isinstance(node, (ast.List, ast.Tuple)):
            left, right = ("[", "]") if isinstance(node, ast.List) else ("(", ")")
            return "#py " + left + " ".join(self.expr(item) for item in node.elts) + right
        if isinstance(node, ast.Dict):
            return "#py {" + " ".join(self.expr(key) + " " + self.expr(value) for key, value in zip(node.keys, node.values)) + "}"
        if isinstance(node, ast.Attribute):
            if name := self.qualified(node):
                return self.dotted(name)
            return f"(.-{node.attr} {self.expr(node.value)})"
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in ("assert_type", "assert_never", "reveal_type"):
                return self.expr(node.args[0])
            if isinstance(node.func, ast.Attribute) and not self.qualified(node.func):
                head = "." + node.func.attr + " " + self.expr(node.func.value)
            else:
                head = self.expr(node.func)
            args = [self.expr(item) for item in node.args]
            if node.keywords:
                if any(item.arg is None for item in node.keywords):
                    raise NotAdapted("Dynamic Python keyword expansion")
                args += ["**"] + [":" + item.arg + " " + self.expr(item.value) for item in node.keywords]
            return "(" + " ".join([head, *args]) + ")"
        if isinstance(node, ast.BinOp):
            operator = OPERATORS[type(node.op)]
            if self.filename == "runtime_operators.py" and isinstance(node.op, ast.Div):
                operator = "operator.truediv"
            if "." in operator:
                operator = self.dotted(operator)
            return f"({operator} {self.expr(node.left)} {self.expr(node.right)})"
        if isinstance(node, ast.UnaryOp):
            operator = {ast.UAdd: "+", ast.USub: "-", ast.Invert: "bit-not", ast.Not: "not"}[type(node.op)]
            return f"({operator} {self.expr(node.operand)})"
        if isinstance(node, ast.Compare):
            if len(node.ops) != 1:
                raise NotAdapted("Chained Python comparison")
            return f"({self.dotted(OPERATORS[type(node.ops[0])])} {self.expr(node.left)} {self.expr(node.comparators[0])})"
        if isinstance(node, ast.Subscript):
            return f"({self.imported('operator', 'getitem')} {self.expr(node.value)} {self.expr(node.slice)})"
        if isinstance(node, ast.Slice):
            values = [self.expr(value) if value else "nil" for value in (node.lower, node.upper, node.step)]
            return "(" + " ".join([self.imported("builtins", "slice"), *values]) + ")"
        raise NotAdapted("Python expression " + type(node).__name__)

    def statements(self, nodes, negative=False):
        for node in nodes:
            row = {"line": node.lineno, "end_line": node.end_lineno,
                   "python": ast.get_source_segment(self.source, node), "kind": type(node).__name__,
                   "negative": negative or self.filename.startswith("fail/") and isinstance(node, ast.Expr)}
            self.rows.append(row)
            try:
                if isinstance(node, ast.Import):
                    for item in node.names:
                        if item.name != "pytest":
                            self.modules[item.asname or item.name.split(".")[0]] = item.name
                    row["status"] = "setup"
                    continue
                if isinstance(node, ast.ImportFrom):
                    if node.module in ("typing", "typing_extensions", "torch.testing._internal.common_utils"):
                        row.update(status="python-only", reason="Type assertion helpers or TEST_NUMPY gate; calls are unwrapped and NumPy is installed")
                    else:
                        for item in node.names:
                            self.modules[item.asname or item.name] = node.module + "." + item.name
                        row["status"] = "setup"
                    continue
                if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "TEST_NUMPY":
                    row.update(status="setup", reason="Installed NumPy makes this upstream branch applicable")
                    self.statements(node.body)
                    continue
                if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
                    self.modules[node.name] = self.companion + "." + node.name
                    row.update(status="python-fixture", name=node.name,
                               reason="Original Python definition retained in companion; executable call sites are inventoried separately")
                    if self.filename == "pass/dynamo_utils.py" and isinstance(node, ast.FunctionDef):
                        row["reason"] = "Each TypeIs branch uses a typed input factory retaining the original parameter annotation"
                        self.used_fixtures.add(node.name)
                        for index, branch in enumerate(node.body):
                            assertion = branch.body[0]
                            argument = node.args.args[0].arg
                            factory = f"_input_{node.name}_{index}"
                            saved = dict(self.names)
                            local = "guard_value_" + str(assertion.lineno)
                            self.names[argument] = local
                            condition = self.expr(branch.test)
                            self.names = saved
                            expression = f"(let [{local} ({self.imported(self.companion, factory)})] (if {condition} {local} nil))"
                            form = f"(def case_{assertion.lineno} {expression})"
                            adapted = {"line": assertion.lineno, "end_line": assertion.end_lineno,
                                       "python": ast.get_source_segment(self.source, assertion), "kind": "Expr",
                                       "status": "adapted", "negative": False, "form": form, "expression": expression,
                                       "expected_type": ast.unparse(assertion.value.args[1]),
                                       "input_factory": factory, "input_name": argument,
                                       "python_condition": ast.unparse(branch.test),
                                       "assertion_offset": form.rindex(local + " nil")}
                            self.rows.append(adapted)
                            self.forms.append(adapted)
                    continue
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id in ("TypeVar", "ParamSpec"):
                    row.update(status="python-only", reason="Python generic parameter declaration retained in companion annotations")
                    continue
                if isinstance(node, ast.With) and len(node.items) == 1 and isinstance(node.items[0].context_expr, ast.Call) and ast.unparse(node.items[0].context_expr.func) == "pytest.raises":
                    row.update(status="setup", reason="Expected runtime rejection is recorded on the adapted call")
                    self.statements(node.body, negative=True)
                    continue
                if isinstance(node, ast.AnnAssign) and ast.unparse(node.annotation) == "TypeAlias":
                    row.update(status="python-only", reason="Python type alias retained in companion annotations")
                    continue
                expression = node.value if isinstance(node, (ast.Expr, ast.Assign, ast.AnnAssign)) else None
                if expression is None:
                    raise NotAdapted("Python statement " + type(node).__name__)
                value = self.expr(expression)
                name = f"case_{node.lineno}"
                row["expected_type"] = None
                if isinstance(expression, ast.Call) and isinstance(expression.func, ast.Name):
                    if expression.func.id == "assert_type":
                        row["expected_type"] = ast.unparse(expression.args[1])
                    elif expression.func.id == "reveal_type":
                        text = "\n".join(self.source.splitlines()[node.lineno - 1:node.end_lineno])
                        if match := re.search(r"# E:\s*(.+)", text):
                            row["expected_type"] = match.group(1).strip().strip("{}")
                form = f"(def {name} {value})"
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Name):
                            self.names[target.id] = name
                        elif isinstance(target, (ast.Tuple, ast.List)):
                            direct = isinstance(expression, (ast.Tuple, ast.List)) and len(expression.elts) == len(target.elts)
                            elements = [self.expr(item) for item in expression.elts] if direct else []
                            bindings = []
                            for index, item in enumerate(target.elts):
                                if not isinstance(item, ast.Name):
                                    raise NotAdapted("Nested assignment destructuring")
                                local = name + "_" + str(index)
                                binding = elements[index] if direct else f"({self.imported('operator', 'getitem')} {name} {index})"
                                bindings.append(f"(def {local} {binding})")
                                self.names[item.id] = local
                            if direct:
                                left, right = ("[", "]") if isinstance(expression, ast.List) else ("(", ")")
                                value = "#py " + left + " ".join(name + "_" + str(index) for index in range(len(target.elts))) + right
                                form = "\n".join([*bindings, f"(def {name} {value})"])
                            else:
                                form += "\n" + "\n".join(bindings)
                        else:
                            raise NotAdapted("Assignment to " + type(target).__name__)
                row.update(status="adapted", form=form, expression=value)
                self.forms.append(row)
            except (NotAdapted, KeyError) as error:
                row.update(status="not-adapted", reason=str(error))

    def build(self):
        self.statements(ast.parse(self.source).body)
        for row in self.rows:
            if row["status"] == "python-fixture":
                row["exercised"] = row["name"] in self.used_fixtures
                if not row["exercised"]:
                    row["reason"] = ("CUDA stream body needs accelerator hardware" if self.filename == "pass/cuda_steam.py"
                                     else "No top-level calls; function-body type narrowing assertions remain outside this expression adapter")
        namespace = "torch-upstream." + self.filename.replace("_", "-").replace("/", ".").removesuffix(".py")
        body = "\n".join(row["form"] for row in self.forms)
        imports = " ".join(f"[{module} :as {alias}]" for module, alias in self.aliases.items() if re.search(r"\b" + alias + r"(?:/|\b)", body))
        header = f"(ns {namespace} (:import {imports}))\n"
        source = header
        for row in self.forms:
            row["start"] = len(source)
            marker = f"(def case_{row['line']} "
            row["expression_start"] = len(source) + row.get("assertion_offset", row["form"].index(marker) + len(marker))
            source += row["form"] + "\n"
            row["end"] = len(source) - 1
        return {"filename": self.filename, "namespace": namespace, "header": header,
                "source": source, "statements": self.rows, "adaptations": self.adaptations,
                "companion": self.companion,
                "upstream_sha256": hashlib.sha256(self.source.encode()).hexdigest()}


def prepare(root, output, package_root):
    output.mkdir(parents=True, exist_ok=True)
    files = []
    for group in ("pass", "fail", "reveal"):
        for path in sorted((root / group).glob("*.py")):
            source = path.read_text()
            name = "torch_fixture_" + group + "_" + path.stem
            companion = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)]
            for node in ast.parse(source).body:
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.Import, ast.ImportFrom)):
                    if isinstance(node, ast.ImportFrom) and node.module == "torch.testing._internal.common_utils":
                        continue
                    if isinstance(node, ast.Import) and any(item.name == "pytest" for item in node.names):
                        continue
                    companion.append(node)
                elif isinstance(node, ast.AnnAssign) and ast.unparse(node.annotation) == "TypeAlias":
                    companion.append(node)
                elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id in ("TypeVar", "ParamSpec"):
                    companion.append(node)
            if group == "pass" and path.name == "dynamo_utils.py":
                for node in ast.parse(source).body:
                    if isinstance(node, ast.FunctionDef):
                        annotation = ast.unparse(node.args.args[0].annotation)
                        for index, branch in enumerate(node.body):
                            sample = repr("probe") if ast.unparse(branch.test.args[1]) == "(str, int)" else "[1]"
                            companion.extend(ast.parse(f"def _input_{node.name}_{index}() -> {annotation}:\n    return {sample}\n").body)
            (output / (name + ".py")).write_text(ast.unparse(ast.Module(body=companion, type_ignores=[])) + "\n")
            adapter = Adapter(group + "/" + path.name, source, package_root, name)
            # Companion modules are generated fixtures, not installed modules.
            adapter.aliases[name] = "fixture"
            artifact = adapter.build()
            artifact["filename_lpy"] = group + "/" + path.stem + ".lpy"
            artifact["python_paths"] = [str(output)]
            (output / group).mkdir(exist_ok=True)
            (output / artifact["filename_lpy"]).write_text(artifact["source"])
            files.append(artifact)
    driver = root / "test_python_operators.py"
    if driver.exists():
        constants = {node.targets[0].id: node.value for node in ast.parse(driver.read_text()).body
                     if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)}

        def expand(node):
            if isinstance(node, ast.Name):
                return expand(constants[node.id])
            if isinstance(node, ast.Tuple):
                return [value for item in node.elts for value in
                        (expand(item.value) if isinstance(item, ast.Starred) else expand(item) if isinstance(item, ast.Name) else [item])]
            return [node]

        values = [ast.unparse(value) for value in expand(constants["ALL"])]
        unary = [ast.literal_eval(value) for value in expand(constants["UNARY_OPS"])]
        binary = [ast.literal_eval(value) for value in expand(constants["BINARY_OPS"])]
        expressions = [f"{operator}({value})" for operator in unary for value in values]
        expressions += [f"({left}) {operator} ({right})" for operator in binary for left in values for right in values]
        source = "import torch\n" + "\n".join(expressions) + "\n"
        artifact = Adapter("runtime_operators.py", source, package_root, "unused_runtime_fixture").build()
        artifact.update(filename_lpy="runtime_operators.lpy", python_paths=[str(output)],
                        derived_from="test_python_operators.py", driver_sha256=hashlib.sha256(driver.read_bytes()).hexdigest())
        (output / artifact["filename_lpy"]).write_text(artifact["source"])
        files.append(artifact)
    if not files:
        raise ValueError("No upstream fixtures found below " + str(root))
    return files


def verify_sources(root, fetch=False):
    manifest = json.loads(Path(__file__).with_name("torch_typing_sources.json").read_text())
    for item in [*manifest["files"], manifest["runtime_driver"], manifest["typing_driver"], manifest["license"]]:
        path = root / item["path"]
        if fetch and not path.exists():
            with urllib.request.urlopen(item["url"], timeout=30) as response:
                data = response.read()
            if hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise ValueError("Downloaded upstream content changed: " + item["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Missing or changed pinned upstream fixture: " + item["path"])
    actual = {str(path.relative_to(root)) for group in ("pass", "fail", "reveal") for path in (root / group).glob("*.py")}
    expected = {item["path"] for item in manifest["files"]}
    if actual != expected:
        raise ValueError("Upstream inventory differs from the pinned manifest")


def runtime_result_type(value):
    result = {"module": type(value).__module__, "name": type(value).__qualname__}
    if isinstance(value, type):
        result.update(is_class=True, class_name=value.__qualname__)
    return result


def native(files):
    from basilisp.main import init
    init()
    core = importlib.import_module("basilisp.core")
    import torch
    torch.set_num_threads(1)
    torch.manual_seed(0)
    results = []
    for artifact in files:
        sys.path[:0] = artifact["python_paths"]
        original = python_runtime(artifact)
        for row in artifact["statements"]:
            if row["status"] != "adapted":
                continue
            item = {"filename": artifact["filename"], "line": row["line"]}
            try:
                value = core.load_string(artifact["header"] + row["form"] + "\n" + "case_" + str(row["line"]))
                item.update(status="valid", result_type=runtime_result_type(value))
            except Exception as error:
                item.update(status="exception", exception=type(error).__name__, message=str(error))
            item["python"] = original[row["line"]]
            item["runtime_parity"] = item["status"] == item["python"]["status"] and (
                item.get("result_type") == item["python"].get("result_type")
                if item["status"] == "valid" else item["exception"] == item["python"]["exception"]
            )
            results.append(item)
    print(json.dumps(results))


def python_runtime(artifact):
    """Check the adapter against the original expressions, including failures."""
    class CPUDevice(ast.NodeTransformer):
        def visit_Constant(self, node):
            if node.value in ("cuda", "cuda:0"):
                return ast.copy_location(ast.Constant("cpu"), node)
            return node

    context = {"__name__": artifact["companion"], "TEST_NUMPY": True}
    results = {}
    for row in artifact["statements"]:
        node = CPUDevice().visit(ast.parse(row["python"]).body[0])
        if isinstance(node, (ast.If, ast.With)):
            continue  # Their selected children have their own inventory rows.
        if isinstance(node, ast.ImportFrom) and node.module == "torch.testing._internal.common_utils":
            continue
        if isinstance(node, ast.Import) and any(item.name == "pytest" for item in node.names):
            continue
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            context[node.name] = getattr(importlib.import_module(artifact["companion"]), node.name)
            continue
        if row["status"] != "adapted":
            if row["status"] != "not-adapted":
                exec(compile(ast.Module(body=[node], type_ignores=[]), "<upstream fixture>", "exec"), context)
            continue
        result = {}
        try:
            if row.get("input_factory"):
                context[row["input_name"]] = getattr(importlib.import_module(artifact["companion"]), row["input_factory"])()
                if not eval(row["python_condition"], context):
                    raise ValueError("Generated input did not enter the original TypeIs branch")
            expression = node.value
            if isinstance(expression, ast.Call) and isinstance(expression.func, ast.Name) and expression.func.id in ("assert_type", "assert_never", "reveal_type"):
                expression = expression.args[0]
            value = eval(compile(ast.Expression(expression), "<upstream fixture>", "eval"), context)
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                context["__case_value"] = value
                node.value = ast.copy_location(ast.Name(id="__case_value", ctx=ast.Load()), node.value)
                exec(compile(ast.Module(body=[node], type_ignores=[]), "<upstream fixture>", "exec"), context)
            result.update(status="valid", result_type=runtime_result_type(value))
        except Exception as error:
            result.update(status="exception", exception=type(error).__name__, message=str(error))
        results[row["line"]] = result
    return results


def analyze(artifact, args):
    import basilisp_tools  # noqa: F401
    from basilisp.lang.runtime import to_lisp
    from basilisp.lang.keyword import keyword as kw
    bridge = importlib.import_module("basilisp_tools.python")
    analyzer = importlib.import_module("basilisp_tools.analyzer")
    cache = bridge.create_cache()
    options = to_lisp({"python-executable": args.python, "python-timeout": args.python_timeout,
                       "python-paths": artifact["python_paths"]}).assoc(kw("python-cache"), cache)
    try:
        start, cpu = time.monotonic(), time.process_time()
        result = analyzer.analyze(artifact["source"], to_lisp({"filename": artifact["filename_lpy"]}).assoc(kw("python-options"), options))
        report = {"seconds": time.monotonic() - start, "parent_cpu_seconds": time.process_time() - cpu,
                  "findings": plain(result.val_at(kw("findings"))),
                  "expressions": plain(result.val_at(kw("python-expressions"))),
                  "engine": analyzer.__file__,
                  "source_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in
                                    (Path(analyzer.__file__), Path(bridge.__file__), Path(bridge.__file__).with_name("_inspect.py"))}}
    finally:
        bridge.stop_cache__BANG__(cache)
    print(json.dumps(report))


def expected_type_shape(text):
    """Normalize fixture annotations without importing or evaluating them."""
    def shape(node):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            members = []
            for value in (shape(node.left), shape(node.right)):
                members.extend(value[1] if value[0] == "union" else [value])
            return ("union", tuple(sorted(set(members))))
        if isinstance(node, (ast.Name, ast.Attribute)):
            return ("name", node.id if isinstance(node, ast.Name) else node.attr, ())
        if isinstance(node, ast.Subscript):
            arguments = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            return ("name", shape(node.value)[1], tuple(shape(value) for value in arguments))
        if isinstance(node, ast.Constant) and node.value is Ellipsis:
            return ("ellipsis",)
        raise ValueError("Unsupported type assertion: " + text)
    return shape(ast.parse(text, mode="eval").body)


def inferred_type_shape(value):
    if value.get("any?"):
        return ("name", "Any", ())
    if value.get("ellipsis?"):
        return ("ellipsis",)
    if value.get("union"):
        members = [inferred_type_shape(item) for item in value["union"]]
        result = ("union", tuple(sorted(set(members))))
    elif value.get("module") and value.get("path"):
        result = ("name", value["path"][-1], tuple(inferred_type_shape(item) for item in value.get("arguments", [])))
    else:
        return ("unknown",)
    if value.get("nullable?"):
        members = list(result[1]) if result[0] == "union" else [result]
        result = ("union", tuple(sorted(set([*members, ("name", "NoneType", ())]))))
    return result


def concrete_type_shape(shape):
    if shape[0] == "unknown" or shape == ("name", "Any", ()):
        return False
    if shape[0] == "union":
        return all(concrete_type_shape(member) for member in shape[1])
    # A known generic with Any parameters remains a concrete outer type; exact
    # argument comparison below still catches lost parameter information.
    return True


def runtime_type_agrees(expected, native):
    if expected[0] == "union":
        return any(runtime_type_agrees(member, native) for member in expected[1])
    if expected[0] != "name":
        return False
    if expected[1] == "type" and expected[2]:
        return native.get("is_class", False) and expected[2][0][1] == native.get("class_name", "").split(".")[-1]
    return expected[1] == native["name"].split(".")[-1] or expected[1] == "Any"


def summarize(report):
    rows = [row for artifact in report["files"] for row in artifact["statements"]]
    adapted = [row for row in rows if row["status"] == "adapted"]
    positives = [row for row in adapted if not row["negative"] and row["native"]["status"] == "valid"]
    negatives = [row for row in adapted if row["negative"] and row["native"]["status"] == "exception"]
    type_assertions = [row for row in positives if row.get("expected_type") not in (None, "Any")]
    rejected_assertions = [row for row in adapted if not row["negative"] and row["native"]["status"] == "exception"
                           and row.get("expected_type") not in (None, "Any")]
    for row in [*type_assertions, *rejected_assertions]:
        expected = expected_type_shape(row["expected_type"])
        inferred = [inferred_type_shape(item) for item in row.get("inferred", [])]
        concrete = bool(inferred) and all(concrete_type_shape(item) for item in inferred)
        row["type_assertion"] = {
            "concrete": concrete,
            "matches_expected": concrete and all(item == expected for item in inferred),
            "upstream_runtime_agrees": (runtime_type_agrees(expected, row["native"]["result_type"])
                                         if row["native"]["status"] == "valid" else None),
            "matches_runtime_result": (concrete and all(runtime_type_agrees(item, row["native"]["result_type"])
                                                       for item in inferred)
                                       if row["native"]["status"] == "valid" else None),
        }
    report["summary"] = {
        "files": len(report["files"]), "statements": len(rows), "adapted": len(adapted),
        "unadapted": sum(row["status"] == "not-adapted" for row in rows),
        "native_valid_positives": len(positives), "native_rejected_negatives": len(negatives),
        "native_rejected_upstream_positives": sum(not row["negative"] and row["native"]["status"] == "exception" for row in adapted),
        "native_accepted_upstream_negatives": sum(row["negative"] and row["native"]["status"] == "valid" for row in adapted),
        "positive_diagnostic_cases": sum(bool(row.get("findings")) for row in positives),
        "negative_misses": sum(not any(finding["type"] in ("type-mismatch", "invalid-arity") for finding in row.get("findings", [])) for row in negatives),
        "inferred_type_assertions": len(type_assertions),
        "unresolved_type_assertions": sum(not row["type_assertion"]["concrete"] for row in type_assertions),
        "incorrect_type_assertions": sum(row["type_assertion"]["concrete"] and row["type_assertion"]["upstream_runtime_agrees"] and not row["type_assertion"]["matches_expected"] for row in type_assertions),
        "upstream_type_assertions_disagree_runtime": sum(not row["type_assertion"]["upstream_runtime_agrees"] for row in type_assertions),
        "runtime_type_mismatches": sum(row["type_assertion"]["concrete"]
                                       and not row["type_assertion"]["upstream_runtime_agrees"]
                                       and not row["type_assertion"]["matches_runtime_result"] for row in type_assertions),
        "native_rejected_type_assertions": len(rejected_assertions),
        "native_rejected_unresolved_type_assertions": sum(not row["type_assertion"]["concrete"] for row in rejected_assertions),
        "native_rejected_type_mismatches": sum(row["type_assertion"]["concrete"] and not row["type_assertion"]["matches_expected"] for row in rejected_assertions),
        "native_rejected_assertion_diagnostics": sum(bool(row.get("findings")) for row in rejected_assertions),
        "analysis_failures": sum(bool(artifact["analysis"].get("failure")) for artifact in report["files"]),
        "analysis_internal_failures": sum(len(audit_failure_findings(artifact["analysis"].get("findings", [])))
                                          for artifact in report["files"]),
        "unattributed_diagnostics": sum(
            not any(row["status"] == "adapted" and row["start"] <= finding.get("start", -1) < row["end"]
                    for row in artifact["statements"])
            for artifact in report["files"] for finding in artifact["analysis"].get("findings", [])),
        "runtime_adapter_mismatches": sum(not row["native"].get("runtime_parity", False) for row in adapted),
        "unexercised_python_functions": sum(row["status"] == "python-fixture" and not row.get("exercised", False) for row in rows),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--python-timeout", type=float, default=30)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--native", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--analyze", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--file", action="append", default=[], help="Replay selected manifest paths after verifying the complete inventory.")
    parser.add_argument("--fetch", action="store_true", help="Fetch missing files from the pinned manifest, verifying their SHA256 hashes.")
    args = parser.parse_args()
    if args.native:
        native(json.loads(args.native.read_text()))
        return 0
    if args.analyze:
        analyze(json.loads(args.analyze.read_text()), args)
        return 0
    if args.root is None:
        parser.error("--root is required")
    verify_sources(args.root, args.fetch)
    environment = json.loads(subprocess.check_output([
        args.python, "-c",
        "import importlib.metadata,json,pathlib,sys,torch;"
        "print(json.dumps({'package_root':str(pathlib.Path(torch.__file__).parent.parent),"
        "'python':sys.version,'versions':{name:importlib.metadata.version(name) "
        "for name in ('torch','numpy','basilisp')}}))",
    ], text=True))
    package_root = Path(environment.pop("package_root"))
    generated = args.output.with_suffix("")
    files = prepare(args.root, generated, package_root)
    available_files = [artifact["filename"] for artifact in files]
    if args.file:
        unknown = set(args.file) - {artifact["filename"] for artifact in files}
        if unknown:
            parser.error("Unknown fixture paths: " + ", ".join(sorted(unknown)))
        files = [artifact for artifact in files if artifact["filename"] in args.file]
    inventory = generated / "inventory.json"
    inventory.write_text(json.dumps(files, indent=2) + "\n")
    if args.prepare_only:
        print(str(inventory))
        return 0
    native_process = subprocess.run([args.python, __file__, "--native", str(inventory), "--output", str(args.output)], text=True, capture_output=True, timeout=args.timeout, check=True)
    native_rows = {(row["filename"], row["line"]): row for row in json.loads(native_process.stdout)}
    manifest_path = Path(__file__).with_name("torch_typing_sources.json")
    manifest = json.loads(manifest_path.read_text())
    report = {"upstream": "https://github.com/pytorch/pytorch/tree/v2.14.1/test/typing",
              "source_release": manifest["release"], "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
              "license": manifest["license"], "typing_driver": manifest["typing_driver"], "runtime": environment,
              "scope": {"selected_files": [artifact["filename"] for artifact in files], "available_files": available_files,
                        "complete_inventory_selected": len(files) == len(available_files),
                        "boundary": "Translated consumer expressions and explicit narrowing branches; original Python fixtures retained."},
              "files": [], "completed": False, "native_stderr": native_process.stderr}
    for artifact in files:
        print(artifact["filename"], flush=True)
        artifact_path = generated / "current.json"
        artifact_path.write_text(json.dumps(artifact))
        try:
            process = subprocess.run([sys.executable, __file__, "--analyze", str(artifact_path), "--python", args.python,
                                      "--python-timeout", str(args.python_timeout), "--output", str(args.output)],
                                     text=True, capture_output=True, timeout=args.timeout, check=True)
            analysis = json.loads(process.stdout)
        except Exception as error:
            analysis = {"failure": repr(error), "stderr": getattr(error, "stderr", None)}
        artifact["analysis"] = analysis
        for row in artifact["statements"]:
            if row["status"] != "adapted":
                continue
            row["native"] = native_rows[(artifact["filename"], row["line"])]
            if artifact.get("derived_from") and row["native"]["status"] == "valid":
                row["expected_type"] = row["native"]["result_type"]["name"]
            row["findings"] = [finding for finding in analysis.get("findings", []) if row["start"] <= finding.get("start", -1) < row["end"]]
            row["inferred"] = [expression["python-type"] for expression in analysis.get("expressions", []) if expression.get("start") == row["expression_start"]]
        report["files"].append(artifact)
        report["completed"] = len(report["files"]) == len(files)
        summarize(report)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["summary"]))
    summary = report["summary"]
    failures = ("unadapted", "analysis_failures", "analysis_internal_failures", "unattributed_diagnostics", "runtime_adapter_mismatches",
                "positive_diagnostic_cases", "negative_misses", "unresolved_type_assertions", "incorrect_type_assertions", "runtime_type_mismatches",
                "native_rejected_unresolved_type_assertions", "native_rejected_type_mismatches", "native_rejected_assertion_diagnostics")
    return 1 if any(summary[key] for key in failures) else 0


if __name__ == "__main__":
    raise SystemExit(main())
