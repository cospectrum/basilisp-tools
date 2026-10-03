"""Standard-library-only inspection worker for a possibly different interpreter.

Invoked by python.lpy with isolated Python. Project files are parsed, never
imported. Installed dependency inspection is enabled by default and can be disabled;
process isolation limits failures and time, and is not a security sandbox.
"""
from __future__ import annotations

import ast
import contextlib
import importlib
import importlib.machinery
import inspect
import json
import os
from pathlib import Path
import sys
import sysconfig
import types

CALLABLE_TYPES = (types.FunctionType, types.BuiltinFunctionType,
                  types.MethodDescriptorType, types.WrapperDescriptorType,
                  types.ClassMethodDescriptorType)

KINDS = {
    inspect.Parameter.POSITIONAL_ONLY: "positional-only",
    inspect.Parameter.POSITIONAL_OR_KEYWORD: "positional-or-keyword",
    inspect.Parameter.VAR_POSITIONAL: "var-positional",
    inspect.Parameter.KEYWORD_ONLY: "keyword-only",
    inspect.Parameter.VAR_KEYWORD: "var-keyword",
}


def annotation(value):
    if value is inspect.Signature.empty:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, type):
        return type.__getattribute__(value, "__module__") + "." + type.__getattribute__(value, "__qualname__")
    return None


def signature_text(params, returns=None):
    parts = []
    saw_star = False
    for index, param in enumerate(params):
        kind = param["kind"]
        if kind == "keyword-only" and not saw_star:
            parts.append("*")
            saw_star = True
        prefix = "*" if kind == "var-positional" else "**" if kind == "var-keyword" else ""
        saw_star = saw_star or bool(prefix)
        item = prefix + param["name"]
        if param.get("annotation"):
            item += ": " + param["annotation"]
        if not param["required?"] and not prefix:
            item += "=..."
        parts.append(item)
        if kind == "positional-only" and (index + 1 == len(params) or params[index + 1]["kind"] != kind):
            parts.append("/")
    return "(" + ", ".join(parts) + ")" + (" -> " + returns if returns else "")


def runtime_signature(value, drop_first=False):
    # Never ask arbitrary callable instances for __signature__ or __wrapped__.
    if not (type(value) in CALLABLE_TYPES
            or (isinstance(value, type) and type(value) is type)):
        return {}
    if isinstance(value, type):
        # inspect.signature itself uses ordinary attribute lookup for class
        # constructors. Reject custom descriptors before reaching that code.
        for key in ("__new__", "__init__"):
            constructor = inspect.getattr_static(value, key, None)
            if type(constructor) is staticmethod:
                constructor = constructor.__func__
            if constructor is not None and type(constructor) not in CALLABLE_TYPES:
                return {}
        text_sig = inspect.getattr_static(value, "__text_signature__", None)
        if text_sig is not None and type(text_sig) not in (str, types.GetSetDescriptorType):
            return {}
    override = inspect.getattr_static(value, "__signature__", None)
    if override is not None and type(override) is not inspect.Signature:
        return {}
    try:
        sig = inspect.signature(value, follow_wrapped=False, eval_str=False)
    except (TypeError, ValueError, AttributeError):
        return {}
    params = [{
        "name": p.name,
        "kind": KINDS[p.kind],
        "required?": p.default is inspect.Parameter.empty
                     and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD),
        "annotation": annotation(p.annotation),
    } for p in sig.parameters.values()]
    if drop_first and params:
        params = params[1:]
    returns = annotation(sig.return_annotation)
    return {"parameters": params, "return-type": returns,
            "signature": signature_text(params, returns)}


def safe_member(name, value, depth=1, owner=None):
    try:
        return runtime_member(name, value, depth, owner)
    except BaseException as error:
        return {"status": "unknown", "name": name, "reason": type(error).__name__}


def runtime_member(name, value, depth=1, owner=None):
    result = {"status": "known", "name": name}
    static = isinstance(value, staticmethod)
    cls_method = isinstance(value, classmethod) or type(value) is types.ClassMethodDescriptorType
    if static or isinstance(value, classmethod):
        value = value.__func__
    if isinstance(value, property):
        result.update(kind="property")
        return result
    if inspect.ismodule(value):
        result.update(kind="module", target=vars(value).get("__name__", name))
        return result
    if isinstance(value, type):
        result.update(kind="class", **{"return-type": annotation(value)})
        result.update(runtime_signature(value))
        # Constructors produce their class regardless of an __init__ -> None annotation.
        result["return-type"] = annotation(value)
        if depth:
            members = {}
            # Bypass custom metaclass __dir__ and descriptor lookup.
            for base in reversed(type.__getattribute__(value, "__mro__")):
                for key, child in tuple(type.__getattribute__(base, "__dict__").items()):
                    if not key.startswith("_"):
                        members[key] = safe_member(key, child, depth=0, owner=value)
            result["members"] = members
            result["members-complete?"] = not any(
                any(key in type.__getattribute__(base, "__dict__")
                    for key in ("__getattr__", "__dict__"))
                for base in type.__getattribute__(value, "__mro__"))
    elif type(value) in CALLABLE_TYPES:
        result["kind"] = "function"
        result.update(runtime_signature(value, drop_first=cls_method))
        result["instance-method?"] = bool(owner and not static and not cls_method)
        if inspect.isfunction(value):
            result.update(filename=value.__code__.co_filename, row=value.__code__.co_firstlineno)
    else:
        result.update(kind="variable", **{"return-type": annotation(type(value))})
    doc = value.__doc__ if type(value) in CALLABLE_TYPES else inspect.getattr_static(value, "__doc__", None)
    if isinstance(doc, str):
        result["doc"] = doc[:2000]
    return result


def local_file(name, roots):
    parts = name.split(".")
    for root in roots:
        stem = Path(root).joinpath(*parts)
        for candidate in (stem.with_suffix(".pyi"), stem / "__init__.pyi",
                          stem.with_suffix(".py"), stem / "__init__.py"):
            if candidate.is_file():
                return candidate
    return None


def expr_text(node):
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ast.unparse(node)


def static_signature(node, drop_first=False):
    args = node.args
    positional = list(args.posonlyargs) + list(args.args)
    required = len(positional) - len(args.defaults)
    params = [{"name": item.arg,
               "kind": "positional-only" if index < len(args.posonlyargs) else "positional-or-keyword",
               "required?": index < required, "annotation": expr_text(item.annotation)}
              for index, item in enumerate(positional)]
    if drop_first and params:
        params = params[1:]
    if args.vararg:
        params.append({"name": args.vararg.arg, "kind": "var-positional",
                       "required?": False, "annotation": expr_text(args.vararg.annotation)})
    for item, default in zip(args.kwonlyargs, args.kw_defaults):
        params.append({"name": item.arg, "kind": "keyword-only",
                       "required?": default is None, "annotation": expr_text(item.annotation)})
    if args.kwarg:
        params.append({"name": args.kwarg.arg, "kind": "var-keyword",
                       "required?": False, "annotation": expr_text(args.kwarg.annotation)})
    returns = expr_text(node.returns)
    return {"parameters": params, "return-type": returns,
            "signature": signature_text(params, returns)}


def static_members(body, filename, module, owner=None):
    members = {}
    complete = True
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decorators = {expr_text(d) for d in node.decorator_list}
            cls_method = "classmethod" in decorators
            static = "staticmethod" in decorators
            result = {"status": "known", "name": node.name,
                      "kind": "property" if "property" in decorators else "function",
                      "filename": str(filename), "row": node.lineno,
                      "doc": (ast.get_docstring(node) or "")[:2000],
                      "instance-method?": bool(owner and not cls_method and not static)}
            result.update(static_signature(node, drop_first=cls_method))
            # Arbitrary decorators may change a callable's signature.
            if decorators - {"classmethod", "staticmethod", "property", "typing.overload", "overload"}:
                result.pop("parameters", None)
                result.pop("signature", None)
            if node.name in members:  # Stub overloads: don't invent one accepted signature.
                result.pop("parameters", None)
                result.pop("signature", None)
            members[node.name] = result
            if node.name == "__getattr__":
                complete = False
        elif isinstance(node, ast.ClassDef):
            children, child_complete = static_members(node.body, filename, module, node.name)
            result = {"status": "known", "name": node.name, "kind": "class",
                      "filename": str(filename), "row": node.lineno,
                      "doc": (ast.get_docstring(node) or "")[:2000],
                      "return-type": module + "." + node.name, "members": children,
                      "members-complete?": False}
            init = next((n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"), None)
            if init and not init.decorator_list:
                result.update(static_signature(init, drop_first=True))
                result["return-type"] = module + "." + node.name
            members[node.name] = result
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    returns = expr_text(node.annotation) if isinstance(node, ast.AnnAssign) else None
                    if returns is None and isinstance(node.value, ast.Constant):
                        returns = annotation(type(node.value.value))
                    members[target.id] = {"status": "known", "name": target.id, "kind": "variable",
                                          "return-type": returns, "filename": str(filename), "row": node.lineno}
        elif isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                members[name] = {"status": "known", "name": name, "kind": "module",
                                 "target": alias.name if alias.asname else name}
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    complete = False
                else:
                    name = alias.asname or alias.name
                    members[name] = {"status": "unknown", "name": name, "kind": "variable"}
        elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
            complete = False
        elif isinstance(node, ast.Expr) and not isinstance(node.value, ast.Constant):
            complete = False
    return members, complete


def static_module(name, path):
    try:
        # tokenize.open respects the file's Python source-encoding declaration.
        import tokenize
        with tokenize.open(path) as source:
            tree = ast.parse(source.read(), filename=str(path), type_comments=True)
        members, complete = static_members(tree.body, path, name)
        return {"status": "known", "name": name, "kind": "module",
                "filename": str(path), "doc": (ast.get_docstring(tree) or "")[:2000],
                "members": members, "members-complete?": complete, "inspection": "static"}
    except (OSError, SyntaxError, UnicodeError) as error:
        return {"status": "unknown", "name": name, "reason": type(error).__name__}


def inspect_module(name, roots, enabled):
    if name == "python":
        name = "builtins"
    path = local_file(name, roots)
    if path is not None:
        return static_module(name, path)
    if not enabled:
        return {"status": "unknown", "name": name, "reason": "inspection-disabled"}
    try:
        # PathFinder bypasses editable-install meta finders that could import the
        # project being analyzed. Verify each package component before importing.
        search = [p for p in sys.path if p and Path(p).is_dir()
                  and not any(Path(p).resolve() == Path(r).resolve() for r in roots)]
        if name not in sys.builtin_module_names and name not in sys.modules:
            for index in range(1, len(name.split(".")) + 1):
                prefix = ".".join(name.split(".")[:index])
                spec = importlib.machinery.PathFinder.find_spec(prefix, search)
                if spec is None:
                    return {"status": "missing", "name": name}
                if spec.origin and spec.origin not in ("built-in", "frozen"):
                    origin = Path(spec.origin).resolve()
                    installed = [Path(p).resolve() for p in sysconfig.get_paths().values() if p]
                    if (any(origin.is_relative_to(Path(root).resolve()) for root in roots)
                            and not any(origin.is_relative_to(p) for p in installed)):
                        return static_module(name, origin) if origin.suffix in (".py", ".pyi") else {
                            "status": "unknown", "name": name, "reason": "local-extension"}
                search = list(spec.submodule_search_locations or [])
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            module = importlib.import_module(name)
        members = {key: safe_member(key, value) for key, value in tuple(vars(module).items())
                   if not key.startswith("_")}
        return {"status": "known", "name": name, "kind": "module", "members": members,
                "members-complete?": "__getattr__" not in vars(module),
                "filename": vars(module).get("__file__"),
                "doc": (vars(module).get("__doc__") or "")[:2000], "inspection": "runtime"}
    except BaseException as error:
        return {"status": "unknown", "name": name, "reason": type(error).__name__}


def main():
    request = json.load(sys.stdin)
    roots = request.get("paths", [])
    results = {name: inspect_module(name, roots, request.get("enabled", True))
               for name in request["modules"]}
    json.dump(results, sys.stdout, ensure_ascii=True)


if __name__ == "__main__":
    main()
