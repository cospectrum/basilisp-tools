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
import typing

CALLABLE_TYPES = (types.FunctionType, types.BuiltinFunctionType,
                  types.MethodDescriptorType, types.WrapperDescriptorType,
                  types.ClassMethodDescriptorType)

TYPING_TYPES = {type(typing.List[int]), type(typing.Union[int, str]),
                type(typing.Annotated[int, "metadata"]), type(typing.ClassVar[int]),
                type(typing.Literal[1]), type(typing.TypeVar("T"))}


def class_attribute(value, name):
    # Call only type's own descriptors, bypassing metaclass overrides/properties.
    return type.__dict__[name].__get__(value, type(value))


def is_class(value):
    # isinstance(value, ...) may execute an arbitrary object's __class__ property.
    return issubclass(type(value), type)


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
    if type(value) is str:
        return value
    if is_class(value):
        module = class_attribute(value, "__module__")
        name = class_attribute(value, "__qualname__")
        return module + "." + name if type(module) is str and type(name) is str else None
    if (type(value) in (types.GenericAlias, types.UnionType)
            or type(value) in TYPING_TYPES):
        # Do not evaluate forward references or user annotation expressions.
        origin = typing.get_origin(value)
        args = typing.get_args(value)
        base = annotation(origin) if origin is not None else None
        values = [annotation(arg) for arg in args]
        if base and all(item is not None for item in values):
            return base + "[" + ", ".join(values) + "]"
    return None


def runtime_type(value, module=None, owner=None):
    """Resolve annotations using dictionaries, never eval/get_type_hints."""
    if value is inspect.Signature.empty or value is None:
        return {}
    if is_class(value):
        module = class_attribute(value, "__module__")
        name = class_attribute(value, "__qualname__")
        return {"type-module": module, "type-path": name.split(".")} if type(module) is str and type(name) is str else {}
    if value is getattr(typing, "Self", None):
        return runtime_type(owner) if owner is not None else {}
    trusted = type(value) in TYPING_TYPES or type(value) in (types.GenericAlias, types.UnionType)
    origin = typing.get_origin(value) if trusted else None
    args = typing.get_args(value) if trusted else ()
    if origin is typing.Union or origin is types.UnionType:
        candidates = [runtime_type(arg, module, owner) for arg in args if arg is not type(None)]
        return candidates[0] if candidates and all(c == candidates[0] for c in candidates) else {}
    if any(origin is wrapper for wrapper in (typing.Annotated, typing.ClassVar, typing.Final)):
        return runtime_type(args[0], module, owner) if args else {}
    if origin is not None:
        result = runtime_type(origin, module, owner)
        if args:
            result["type-arguments"] = [runtime_type(arg, module, owner) for arg in args]
        return result
    if type(value) is str:
        # String annotations can contain executable code; parse names only.
        try:
            node = ast.parse(value, mode="eval").body
        except (SyntaxError, ValueError):
            return {}
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return runtime_type(node.value, module, owner)
        if isinstance(node, ast.Subscript):
            node = node.value
        parts = expr_text(node).split(".")
        if not all(part.isidentifier() for part in parts):
            return {}
        if parts == ["Self"] and owner is not None:
            return runtime_type(owner)
        namespace = vars(sys.modules[module]) if type(module) is str and module in sys.modules else {}
        value = namespace.get(parts[0], vars(sys.modules["builtins"]).get(parts[0]))
        for part in parts[1:]:
            if type(value) is types.ModuleType:
                value = vars(value).get(part)
            elif is_class(value):
                value = inspect.getattr_static(value, part, None)
            else:
                return {}
        return runtime_type(value, module, owner) if is_class(value) else {}
    return {}


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


def runtime_signature(value, drop_first=False, owner=None):
    # Never ask arbitrary callable instances for __signature__ or __wrapped__.
    if not (type(value) in CALLABLE_TYPES
            or (is_class(value) and type(value) is type)):
        return {}
    if is_class(value):
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
    module = getattr(value, "__module__", None) if type(value) in CALLABLE_TYPES else None
    return {"parameters": params, "return-type": returns,
            "signature": signature_text(params, returns),
            **runtime_type(sig.return_annotation, module, owner)}


def safe_member(name, value, depth=1, owner=None):
    try:
        return runtime_member(name, value, depth, owner)
    except BaseException as error:
        return {"status": "unknown", "name": name, "reason": type(error).__name__}


def runtime_member(name, value, depth=1, owner=None):
    result = {"status": "known", "name": name}
    static = type(value) is staticmethod
    cls_method = type(value) is classmethod or type(value) is types.ClassMethodDescriptorType
    if static or type(value) is classmethod:
        value = value.__func__
    if type(value) is property:
        result.update(kind="property")
        if type(value.fget) is types.FunctionType:
            info = runtime_signature(value.fget, owner=owner)
            result.update({key: item for key, item in info.items()
                           if key in ("return-type", "type-module", "type-path", "type-arguments")})
            result.update(filename=value.fget.__code__.co_filename, row=value.fget.__code__.co_firstlineno)
            result["doc"] = (value.fget.__doc__ or "")[:2000]
        return result
    if type(value) is types.ModuleType:
        result.update(kind="module", target=vars(value).get("__name__", name))
        return result
    if is_class(value):
        result.update(kind="class", **{"return-type": annotation(value)})
        result.update(runtime_signature(value))
        # Constructors produce their class regardless of an __init__ -> None annotation.
        result["return-type"] = annotation(value)
        result.update(runtime_type(value))
        if depth:
            members = {}
            # Bypass custom metaclass __dir__ and descriptor lookup.
            for base in reversed(class_attribute(value, "__mro__")):
                for key, child in tuple(class_attribute(base, "__dict__").items()):
                    if not key.startswith("_"):
                        members[key] = safe_member(key, child, depth=0, owner=value)
                annotations = class_attribute(base, "__dict__").get("__annotations__", {})
                if type(annotations) is dict:
                    for key, hint in annotations.items():
                        if not key.startswith("_") and key not in members:
                            members[key] = {"status": "known", "name": key, "kind": "variable",
                                            "return-type": annotation(hint),
                                            **runtime_type(hint, class_attribute(base, "__module__"), base)}
            result["members"] = members
            result["members-complete?"] = not any(
                any(key in class_attribute(base, "__dict__")
                    for key in ("__getattr__", "__dict__"))
                for base in class_attribute(value, "__mro__"))
    elif type(value) in CALLABLE_TYPES:
        result["kind"] = "function"
        result.update(runtime_signature(value, drop_first=cls_method, owner=owner))
        if type(value) is types.FunctionType and hasattr(typing, "get_overloads"):
            overloads = [{"status": "known", "instance-method?": bool(owner is not None and not static and not cls_method),
                          **runtime_signature(fn, drop_first=cls_method, owner=owner)}
                         for fn in typing.get_overloads(value)]
            if overloads:
                result["overloads"] = overloads
        result["instance-method?"] = bool(owner is not None and not static and not cls_method)
        if type(value) is types.FunctionType:
            result.update(filename=value.__code__.co_filename, row=value.__code__.co_firstlineno)
    else:
        result.update(kind="variable", **{"return-type": annotation(type(value)), **runtime_type(type(value))})
    doc = value.__doc__ if type(value) in CALLABLE_TYPES else inspect.getattr_static(value, "__doc__", None)
    if type(doc) is str:
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


def stub_file(name, roots):
    """Find inline/stub-only packages without importing their parent packages."""
    parts = name.split(".")
    for root in roots:
        for package in (parts[0] + "-stubs", parts[0]):
            stem = Path(root).joinpath(package, *parts[1:])
            for candidate in (stem.with_suffix(".pyi"), stem / "__init__.pyi"):
                if candidate.is_file():
                    return candidate
    return None


class StaticInspector:
    """A bounded source graph; every file is parsed, never executed."""

    def __init__(self, roots, installed):
        self.roots = roots
        self.installed = installed
        self.modules = {}
        self.active = set()
        self.dependencies = {}
        self.source_packages = set()

    def path(self, name):
        return (local_file(name, self.roots) or stub_file(name, self.installed)
                or (local_file(name, self.installed)
                    if name.split(".")[0] in self.source_packages else None))

    def dependency(self, path):
        stat = path.stat()
        self.dependencies[str(path)] = [str(path), stat.st_mtime_ns, stat.st_size]

    def module(self, name, path=None):
        if path is not None:
            self.source_packages.add(name.split(".")[0])
        if name in self.modules:
            return self.modules[name]
        if name in self.active or len(self.modules) + len(self.active) >= 64:
            return {"status": "unknown", "name": name, "reason": "cyclic-or-large-import"}
        path = path or self.path(name)
        if path is None:
            return {"status": "unknown", "name": name, "reason": "external-import"}
        self.active.add(name)
        try:
            import tokenize
            self.dependency(path)
            with tokenize.open(path) as source:
                tree = ast.parse(source.read(), filename=str(path), type_comments=True)
            context = StaticModule(self, name, path, tree)
            result = context.build()
            self.modules[name] = result
            return result
        except (OSError, SyntaxError, UnicodeError, RecursionError) as error:
            return {"status": "unknown", "name": name, "reason": type(error).__name__}
        finally:
            self.active.remove(name)


class StaticModule:
    def __init__(self, inspector, name, path, tree):
        self.inspector = inspector
        self.name = name
        self.path = path
        self.tree = tree
        self.aliases = {}
        self.classes = {}
        self.class_results = {}
        self.active_classes = set()
        self.alias_nodes = {}
        self.bounds = {}
        self.is_stub = path.suffix == ".pyi"
        self.exports = None
        self.collect(tree.body)

    def import_source(self, node):
        if node.level:
            package = self.name if self.path.name.startswith("__init__.") else self.name.rpartition(".")[0]
            parts = package.split(".") if package else []
            if node.level > len(parts):
                return None
            prefix = ".".join(parts[:len(parts) - node.level + 1])
            return ".".join(filter(None, (prefix, node.module)))
        return node.module

    def collect(self, body, owner=()):
        for node in body:
            if isinstance(node, ast.ClassDef):
                self.classes[owner + (node.name,)] = node
                self.collect(node.body, owner + (node.name,))
            elif not owner and isinstance(node, ast.Import):
                for alias in node.names:
                    self.aliases[alias.asname or alias.name.split(".")[0]] = (
                        alias.name if alias.asname else alias.name.split(".")[0], [])
            elif not owner and isinstance(node, ast.ImportFrom):
                source = self.import_source(node)
                if source:
                    for alias in node.names:
                        if alias.name != "*":
                            self.aliases[alias.asname or alias.name] = (source, [alias.name])
            elif not owner and hasattr(ast, "TypeAlias") and isinstance(node, ast.TypeAlias):
                self.alias_nodes[node.name.id] = node.value
            elif not owner and isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        self.alias_nodes[target.id] = node.value
                        if target.id == "__all__":
                            try:
                                value = ast.literal_eval(node.value)
                                if isinstance(value, (list, tuple)) and all(isinstance(x, str) for x in value):
                                    self.exports = set(value)
                            except (ValueError, TypeError):
                                pass
            elif isinstance(node, ast.If) and self.type_checking(node.test):
                self.collect(node.body, owner)

    def type_checking(self, node):
        return expr_text(node) in ("TYPE_CHECKING", "typing.TYPE_CHECKING")

    def location(self, node, name):
        return {"status": "known", "name": name, "filename": str(self.path),
                "row": node.lineno, "end-row": getattr(node, "end_lineno", node.lineno),
                "col": node.col_offset + 1}

    def reference(self, node, owner=(), seen=()):
        if node is None or len(seen) > 16:
            return {}
        if isinstance(node, ast.Call) and expr_text(node.func).split(".")[-1] == "TypeVar":
            bound = next((kw.value for kw in node.keywords if kw.arg == "bound"), None)
            return self.reference(bound, owner, seen)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                return self.reference(ast.parse(node.value, mode="eval").body, owner, seen)
            except (SyntaxError, ValueError):
                return {}
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            values = [self.reference(n, owner, seen) for n in (node.left, node.right)
                      if not (isinstance(n, ast.Constant) and n.value is None)]
            return values[0] if values and all(v == values[0] for v in values) else {}
        if isinstance(node, ast.Subscript):
            base = expr_text(node.value).split(".")[-1]
            args = list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]
            if base in ("Optional", "Annotated", "ClassVar", "Final", "Required", "NotRequired"):
                return self.reference(args[0], owner, seen)
            if base == "Union":
                refs = [self.reference(n, owner, seen) for n in args
                        if not (isinstance(n, ast.Constant) and n.value is None)]
                return refs[0] if refs and all(v == refs[0] for v in refs) else {}
            result = self.reference(node.value, owner, seen)
            if result:
                result["type-arguments"] = [self.reference(n, owner, seen) for n in args]
            return result
        text = expr_text(node)
        if not text or not all(part.isidentifier() for part in text.split(".")):
            return {}
        parts = text.split(".")
        name = parts[0]
        if name in ("Self",) or text in ("typing.Self", "typing_extensions.Self"):
            return {"type-module": self.name, "type-path": list(owner)} if owner else {}
        if name in self.bounds and name not in seen:
            return self.reference(self.bounds[name], owner, seen + (name,))
        if name in self.aliases:
            module, path = self.aliases[name]
            full = path + parts[1:]
            if module in ("typing", "typing_extensions") and full:
                aliases = {"List": "list", "Dict": "dict", "Set": "set", "FrozenSet": "frozenset",
                           "Tuple": "tuple", "Type": "type"}
                if full[0] in aliases:
                    return {"type-module": "builtins", "type-path": [aliases[full[0]]]}
                if full[0] in ("Any", "Never", "NoReturn", "Literal", "Union", "Optional"):
                    return {}
            if full and (module, tuple(full)) not in seen:
                value = self.resolve(module, full)
                if value and value.get("type-module"):
                    return {key: value[key] for key in ("type-module", "type-path", "type-arguments") if key in value}
            return {"type-module": module, "type-path": full} if full else {}
        if tuple(parts) in self.classes:
            return {"type-module": self.name, "type-path": parts}
        for count in range(len(owner), 0, -1):
            candidate = owner[:count] + tuple(parts)
            if candidate in self.classes:
                return {"type-module": self.name, "type-path": list(candidate)}
        if name in self.alias_nodes and name not in seen:
            return self.reference(self.alias_nodes[name], owner, seen + (name,))
        builtin = vars(sys.modules["builtins"]).get(name)
        if isinstance(builtin, type):
            return {"type-module": "builtins", "type-path": parts}
        return {}

    def resolve(self, module, path):
        if module == self.name and tuple(path) in self.classes:
            return self.class_info(tuple(path))
        child = ".".join([module, *path])
        if not self.inspector.path(module):
            if self.inspector.path(child):
                return {"status": "known", "kind": "module", "name": path[-1], "target": child}
            return None
        info = self.inspector.module(module)
        for part in path:
            info = info.get("members", {}).get(part, {})
        if not info and self.inspector.path(child):
            return {"status": "known", "kind": "module", "name": path[-1], "target": child}
        return info or None

    def decorators(self, node):
        return {expr_text(d.func if isinstance(d, ast.Call) else d).split(".")[-1]
                for d in node.decorator_list}

    def function(self, node, owner=()):
        decorators = self.decorators(node)
        cls_method = "classmethod" in decorators
        prop = bool(decorators & {"property", "cached_property"})
        info = {**self.location(node, node.name), "kind": "property" if prop else "function",
                "doc": (ast.get_docstring(node) or "")[:2000],
                "instance-method?": bool(owner and not cls_method and "staticmethod" not in decorators)}
        info.update(static_signature(node, drop_first=cls_method))
        previous_bounds = self.bounds
        self.bounds = {**self.bounds, **{p.name: p.bound for p in getattr(node, "type_params", [])
                                      if hasattr(p, "bound") and p.bound is not None}}
        info.update(self.reference(node.returns, owner))
        self.bounds = previous_bounds
        if isinstance(node, ast.AsyncFunctionDef):
            info["async?"] = True
        known = {"classmethod", "staticmethod", "property", "cached_property", "overload",
                 "abstractmethod", "final", "override"}
        if decorators - known or prop:
            info.pop("parameters", None)
            info.pop("signature", None)
        if prop:
            info["instance-method?"] = False
        return info

    def class_info(self, owner):
        if owner in self.class_results:
            return self.class_results[owner]
        if owner in self.active_classes or len(owner) > 8:
            return {}
        self.active_classes.add(owner)
        try:
            node = self.classes[owner]
            children = {}
            bases = []
            inherited_parameters = None
            for base in node.bases:
                ref = self.reference(base, owner[:-1])
                if ref:
                    bases.append(ref)
                    base_info = self.resolve(ref["type-module"], ref["type-path"])
                    if base_info:
                        children = {**base_info.get("members", {}), **children}
                        if inherited_parameters is None and "parameters" in base_info:
                            inherited_parameters = base_info["parameters"]
            own, complete = self.members(node.body, owner)
            children.update(own)
            info = {**self.location(node, owner[-1]), "kind": "class",
                    "doc": (ast.get_docstring(node) or "")[:2000],
                    "return-type": self.name + "." + ".".join(owner),
                    "type-module": self.name, "type-path": list(owner),
                    "members": children, "members-complete?": False, "bases": bases}
            constructor = own.get("__init__") or own.get("__new__")
            if constructor:
                if "parameters" in constructor:
                    params = constructor["parameters"][1:]
                    info.update(parameters=params, signature=signature_text(params, info["return-type"]))
                if constructor.get("overloads"):
                    info["overloads"] = [
                        {**sig, "parameters": sig["parameters"][1:],
                         "signature": signature_text(sig["parameters"][1:], info["return-type"]),
                         "return-type": info["return-type"], "type-module": self.name,
                         "type-path": list(owner), "instance-method?": False}
                        for sig in constructor["overloads"] if "parameters" in sig]
            elif "dataclass" in self.decorators(node):
                params = self.dataclass_parameters(node, inherited_parameters or [])
                info.update(parameters=params, signature=signature_text(params, info["return-type"]))
            elif inherited_parameters is not None:
                info.update(parameters=inherited_parameters,
                            signature=signature_text(inherited_parameters, info["return-type"]))
            elif not node.bases:
                info.update(parameters=[], signature=signature_text([], info["return-type"]))
            if self.decorators(node) - {"dataclass", "final", "runtime_checkable"}:
                for key in ("parameters", "signature", "overloads"):
                    info.pop(key, None)
            self.class_results[owner] = info
            return info
        finally:
            self.active_classes.remove(owner)

    def dataclass_parameters(self, node, inherited):
        options = {}
        for deco in node.decorator_list:
            if isinstance(deco, ast.Call) and expr_text(deco.func).split(".")[-1] == "dataclass":
                options = {kw.arg: kw.value.value for kw in deco.keywords if isinstance(kw.value, ast.Constant)}
        if options.get("init") is False:
            return inherited
        params = {p["name"]: p for p in inherited}
        kw_only = options.get("kw_only", False)
        for field in node.body:
            if not isinstance(field, ast.AnnAssign) or not isinstance(field.target, ast.Name):
                continue
            hint = expr_text(field.annotation) or ""
            if hint.split(".")[-1] == "KW_ONLY":
                kw_only = True
                continue
            if hint.split("[")[0].split(".")[-1] == "ClassVar":
                continue
            field_options = {}
            default = field.value is not None
            if isinstance(field.value, ast.Call) and expr_text(field.value.func).split(".")[-1] == "field":
                field_options = {kw.arg: kw.value for kw in field.value.keywords}
                if isinstance(field_options.get("init"), ast.Constant) and field_options["init"].value is False:
                    continue
                default = "default" in field_options or "default_factory" in field_options
            keyword = field_options.get("kw_only")
            keyword = keyword.value if isinstance(keyword, ast.Constant) else kw_only
            params[field.target.id] = {
                "name": field.target.id, "kind": "keyword-only" if keyword else "positional-or-keyword",
                "required?": not default, "annotation": hint}
        return sorted(params.values(), key=lambda item: item["kind"] == "keyword-only")

    def members(self, body, owner=()):
        members = {}
        overloads = {}
        complete = True
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                info = self.function(node, owner)
                if "overload" in self.decorators(node):
                    overloads.setdefault(node.name, []).append(info)
                members[node.name] = info
                if node.name == "__getattr__":
                    complete = False
            elif isinstance(node, ast.ClassDef):
                members[node.name] = self.class_info(owner + (node.name,))
            elif hasattr(ast, "TypeAlias") and isinstance(node, ast.TypeAlias):
                members[node.name.id] = {**self.location(node, node.name.id), "kind": "variable",
                                         "return-type": expr_text(node.value),
                                         **self.reference(node.value, owner)}
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if not isinstance(target, ast.Name):
                        continue
                    returns = expr_text(node.annotation) if isinstance(node, ast.AnnAssign) else None
                    ref = self.reference(node.annotation, owner) if isinstance(node, ast.AnnAssign) else {}
                    if isinstance(node.value, ast.Constant) and returns is None:
                        returns = annotation(type(node.value.value))
                        ref = runtime_type(type(node.value.value))
                    if isinstance(node.value, ast.Call) and not ref:
                        ref = self.reference(node.value.func, owner)
                        returns = expr_text(node.value.func)
                    if isinstance(node.value, ast.Subscript) and not ref:
                        ref = self.reference(node.value, owner)
                        returns = expr_text(node.value)
                    if isinstance(node.value, (ast.Name, ast.Attribute)):
                        alias_ref = self.reference(node.value, owner)
                        value = self.resolve(alias_ref["type-module"], alias_ref["type-path"]) if alias_ref else None
                        if value:
                            members[target.id] = {**value, "name": target.id}
                            continue
                    members[target.id] = {**self.location(node, target.id), "kind": "variable",
                                          "return-type": returns, **ref}
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name.split(".")[0]
                    members[name] = {"status": "known", "name": name, "kind": "module",
                                     "target": alias.name if alias.asname else name}
            elif isinstance(node, ast.ImportFrom):
                source = self.import_source(node)
                if not source:
                    complete = False
                    continue
                for alias in node.names:
                    if alias.name == "*":
                        imported = self.inspector.module(source)
                        exports = imported.get("exports")
                        members.update({k: v for k, v in imported.get("members", {}).items()
                                        if k in exports} if exports is not None else
                                       {k: v for k, v in imported.get("members", {}).items() if not k.startswith("_")})
                        complete = complete and imported.get("members-complete?", False)
                    else:
                        name = alias.asname or alias.name
                        value = self.resolve(source, [alias.name])
                        if value:
                            members[name] = {**value, "name": name}
                        else:
                            members[name] = {"status": "unknown", "name": name, "kind": "variable",
                                             "target-module": source, "target-path": [alias.name]}
            elif isinstance(node, ast.If) and self.type_checking(node.test):
                extra, certain = self.members(node.body, owner)
                members.update(extra)
                complete = complete and certain
            elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                complete = False
            elif isinstance(node, ast.Expr) and not isinstance(node.value, ast.Constant):
                complete = False
        for name, signatures in overloads.items():
            info = dict(members[name])
            members[name] = info
            info["overloads"] = signatures
            info.pop("parameters", None)
            info["signature"] = "\n".join(sig["signature"] for sig in signatures if "signature" in sig)
            refs = [{k: sig[k] for k in ("type-module", "type-path") if k in sig} for sig in signatures]
            if refs and all(ref == refs[0] for ref in refs):
                info.update(refs[0])
            else:
                info.pop("type-module", None)
                info.pop("type-path", None)
        return members, complete

    def build(self):
        members, complete = self.members(self.tree.body)
        partial = False
        if self.is_stub:
            for parent in list(self.path.parents)[:8]:
                marker = parent / "py.typed"
                if marker.is_file():
                    partial = "partial" in marker.read_text(encoding="utf-8").splitlines()
                    break
        return {"status": "known", "name": self.name, "kind": "module",
                "filename": str(self.path), "doc": (ast.get_docstring(self.tree) or "")[:2000],
                "members": members, "members-complete?": complete and not partial, "partial-stub?": partial,
                "exports": sorted(self.exports) if self.exports is not None else None,
                "inspection": "static"}


def static_module(name, path, inspector=None):
    inspector = inspector or StaticInspector([str(Path(path).parent)], [])
    return inspector.module(name, Path(path))


def inspect_module(name, roots, enabled, inspector=None, skip_stubs=False):
    if name == "python":
        name = "builtins"
    inspector = inspector or StaticInspector(roots, [p for p in sys.path if p])
    local = local_file(name, roots)
    path = local or (None if skip_stubs else stub_file(name, inspector.installed))
    if path is not None:
        result = static_module(name, path, inspector)
        if local is None and enabled and result.get("partial-stub?"):
            runtime = inspect_module(name, roots, enabled, inspector, skip_stubs=True)
            if runtime.get("status") == "known":
                result = {**result, "members": {**runtime.get("members", {}), **result.get("members", {})}}
        return result
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
                        return static_module(name, origin, inspector) if origin.suffix in (".py", ".pyi") else {
                            "status": "unknown", "name": name, "reason": "local-extension"}
                search = list(spec.submodule_search_locations or [])
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            module = importlib.import_module(name)
        members = {key: safe_member(key, value) for key, value in tuple(vars(module).items())
                   if not key.startswith("_")}
        source = vars(module).get("__file__")
        if "__getattr__" in vars(module) and source and source.endswith(".py"):
            # Lazy modules often declare their public imports under TYPE_CHECKING.
            # Parse these declarations instead of calling module.__getattr__.
            declared = static_module(name, Path(source), inspector).get("members", {})
            members = {**declared, **members}
        return {"status": "known", "name": name, "kind": "module", "members": members,
                "members-complete?": "__getattr__" not in vars(module),
                "filename": vars(module).get("__file__"),
                "doc": (vars(module).get("__doc__") or "")[:2000], "inspection": "runtime"}
    except BaseException as error:
        return {"status": "unknown", "name": name, "reason": type(error).__name__}


def metadata_decoder(keyword, persistent_map, vector):
    """Build the host's JSON hook without paying Lisp call overhead per field.

    Constructors are supplied by the host; the isolated worker remains usable
    with an interpreter that does not have Basilisp installed.
    """
    keys = {}
    def intern(value):
        if value not in keys:
            keys[value] = keyword(value)
        return keys[value]
    vector_keys = {"parameters", "overloads", "type-path", "target-path", "type-arguments",
                   "dependencies", "bases"}
    enum_keys = {"status", "kind", "inspection"}
    def decode(raw):
        if not ("status" in raw or "required?" in raw or "type-module" in raw):
            return raw
        result = {}
        for key, value in raw.items():
            if key == "members":
                value = persistent_map(value)
            elif key in vector_keys:
                value = vector(value)
            elif key in enum_keys and value is not None:
                value = intern(value)
            result[intern(key)] = value
        return persistent_map(result)
    return decode


def main():
    request = json.load(sys.stdin)
    if request.get("operation") == "environment":
        json.dump([p for p in sys.path if p], sys.stdout)
        return
    roots = request.get("paths", [])
    inspector = StaticInspector(roots, [p for p in sys.path if p])
    results = {name: inspect_module(name, roots, request.get("enabled", True), inspector)
               for name in request["modules"]}
    for result in results.values():
        result["dependencies"] = list(inspector.dependencies.values())
    json.dump(results, sys.stdout, ensure_ascii=True)


if __name__ == "__main__":
    main()
