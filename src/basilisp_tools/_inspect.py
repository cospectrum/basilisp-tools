"""Standard-library-only inspection worker for a possibly different interpreter.

Invoked by python.lpy with isolated Python. Project files are parsed, never
imported. Installed dependency inspection is enabled by default and can be disabled;
process isolation limits failures and time, and is not a security sandbox.
"""
from __future__ import annotations

import ast
import contextlib
import contextvars
import functools
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

TYPEVAR_TYPE = type(typing.TypeVar("_BltT"))
PARAMSPEC_TYPE = type(typing.ParamSpec("_BltP"))
PARAMSPEC_ARGS_TYPE = type(typing.ParamSpec("_BltP").args)
PARAMSPEC_KWARGS_TYPE = type(typing.ParamSpec("_BltP").kwargs)
TYPEVARTUPLE_TYPE = type(typing.TypeVarTuple("_BltTs")) if hasattr(typing, "TypeVarTuple") else None
TYPE_PARAMETER_TYPES = tuple(cls for cls in (TYPEVAR_TYPE, PARAMSPEC_TYPE, TYPEVARTUPLE_TYPE) if cls is not None)

TYPING_TYPES = {type(typing.Callable), type(typing.Callable[..., int]), type(typing.Iterable),
                type(typing.List[int]), type(typing.Union[int, str]),
                type(typing.Annotated[int, "metadata"]), type(typing.ClassVar[int]),
                type(typing.Literal[1]), type(typing.TypeVar("T")),
                type(typing.Concatenate[int, typing.ParamSpec("_BltP")])}
if hasattr(typing, "Unpack"):
    TYPING_TYPES.add(type(typing.Unpack[typing.TypeVarTuple("_BltTs")]))


def exact_type(value, classes):
    """Type identity checks never invoke an untrusted metaclass hash/equality."""
    actual = type(value)
    return any(actual is cls for cls in classes)


def class_attribute(value, name):
    # Call only type's own descriptors, bypassing metaclass overrides/properties.
    return type.__dict__[name].__get__(value, type(value))


def static_class_member(value, name, default=None):
    for base in class_attribute(value, "__mro__"):
        namespace = class_attribute(base, "__dict__")
        if name in namespace:
            return namespace[name]
    return default


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
    if exact_type(value, TYPE_PARAMETER_TYPES):
        return value.__name__
    if value is None:
        return "None"
    if type(value) is typing.ForwardRef:
        return value.__forward_arg__
    if is_class(value):
        module = class_attribute(value, "__module__")
        name = class_attribute(value, "__qualname__")
        return module + "." + name if type(module) is str and type(name) is str else None
    if (exact_type(value, (types.GenericAlias, types.UnionType))
            or exact_type(value, TYPING_TYPES)):
        # Do not evaluate forward references or user annotation expressions.
        origin = typing.get_origin(value)
        args = typing.get_args(value)
        if origin is typing.Union or origin is types.UnionType:
            values = [annotation(arg) for arg in args]
            return " | ".join(values) if all(item is not None for item in values) else None
        if origin is typing.Literal and all(exact_type(arg, (str, int, float, bool, type(None))) for arg in args):
            return "Literal[" + ", ".join(repr(arg) for arg in args) + "]"
        base = annotation(origin) if origin is not None else None
        values = [annotation(arg) for arg in args]
        if base and all(item is not None for item in values):
            return base + "[" + ", ".join(values) + "]"
    return None


TYPE_KEYS = ("type-module", "type-path", "type-arguments", "type-union",
             "typevar", "type-bound", "type-constraints", "type-default",
             "literal-values", "nullable?", "type-any?", "type-never?", "type-self?",
             "type-ellipsis?", "parameter-spec?", "variadic?", "unpack?", "parameter-part",
             "parameter-list?", "type-pack?")


def type_fields(info):
    return {key: info[key] for key in TYPE_KEYS if key in info and key not in ("parameters", "callable-overloads")}


def union_type(values):
    # Unknown/Any absorbs a union; never invent a concrete result.
    if any(not value or value.get("type-any?") for value in values):
        return {"type-any?": True}
    unique = []
    nullable = False
    for value in values:
        for item in value.get("type-union", [value]):
            item = dict(item)
            nullable = nullable or item.pop("nullable?", False)
            if item.get("type-module") == "builtins" and item.get("type-path") == ["NoneType"]:
                nullable = True
            elif item not in unique and not item.get("type-never?"):
                unique.append(item)
    result = (dict(unique[0]) if len(unique) == 1 else {"type-union": unique}
              if unique else ({"type-module": "builtins", "type-path": ["NoneType"]}
                               if nullable else {"type-never?": True}))
    if len(unique) > 1:
        origins = []
        for item in unique:
            while item.get("typevar") and item.get("type-bound"):
                item = item["type-bound"]
            origins.append((item.get("type-module"), item.get("type-path")))
        if origins and origins[0][0] and all(origin == origins[0] for origin in origins):
            result["type-module"], result["type-path"] = origins[0]
    if nullable and unique:
        result["nullable?"] = True
    return result


def bind_type_parameters(parameters, arguments):
    """Capture one variadic pack without truncating following generic arguments."""
    packs = [index for index, param in enumerate(parameters) if param.get("variadic?")]
    if not packs:
        return {param["typevar"]: arg for param, arg in zip(parameters, arguments)}
    if (len(packs) != 1 or len(arguments) < len(parameters) - 1
            or any(arg.get("unpack?") or arg.get("type-ellipsis?") for arg in arguments)):
        return {}
    index = packs[0]
    suffix = len(parameters) - index - 1
    end = len(arguments) - suffix
    result = {param["typevar"]: arg for param, arg in zip(parameters[:index], arguments[:index])}
    result[parameters[index]["typevar"]] = {"type-pack?": True, "type-arguments": arguments[index:end]}
    result.update({param["typevar"]: arg for param, arg in zip(parameters[index + 1:], arguments[end:])})
    return result


def type_parameter_infos(value, names):
    found = {}
    def visit(item):
        if isinstance(item, dict):
            if item.get("typevar"):
                found.setdefault(item["typevar"], type_fields(item))
            for nested in item.values():
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)
    visit(value)
    return [found.get(name, {"typevar": name}) for name in names]


def substitute_types(value, bindings, variables=None):
    """Apply generic arguments, retaining unchanged immutable declarations."""
    if not bindings or (variables is not None and not variables(value).intersection(bindings)):
        return value
    if isinstance(value, list):
        result = [substitute_types(item, bindings, variables) for item in value]
        return value if all(a is b for a, b in zip(value, result)) else result
    if not isinstance(value, dict):
        return value
    variable = "__Self__" if value.get("type-self?") else value.get("typevar")
    result = {key: substitute_types(item, bindings, variables) for key, item in value.items()}
    if "type-arguments" in value:
        arguments = []
        for original, replaced in zip(value["type-arguments"], result["type-arguments"]):
            if original.get("unpack?") and replaced.get("type-pack?"):
                arguments.extend(replaced.get("type-arguments", []))
            else:
                arguments.append(replaced)
        if any(a is not b for a, b in zip(arguments, result["type-arguments"])) or len(arguments) != len(result["type-arguments"]):
            result["type-arguments"] = arguments
    if variable in bindings:
        result = {key: item for key, item in result.items() if key not in TYPE_KEYS}
        result.update(bindings[variable])
    return value if len(result) == len(value) and all(key in result and result[key] is item for key, item in value.items()) else result


def find_typevars(value):
    result = []
    if isinstance(value, dict):
        if value.get("typevar"):
            result.append(value["typevar"])
        for key, item in value.items():
            if key != "type-parameters":
                for name in find_typevars(item):
                    if name not in result:
                        result.append(name)
    elif isinstance(value, list):
        for item in value:
            for name in find_typevars(item):
                if name not in result:
                    result.append(name)
    return result


def runtime_type(value, module=None, owner=None, depth=0, seen=frozenset()):
    """Resolve annotations using dictionaries and ASTs, never eval/get_type_hints."""
    marker = ("annotation", value) if type(value) is str else id(value)
    if depth > 24 or value is inspect.Signature.empty or marker in seen:
        return {}
    seen = seen | {marker}
    if value is None or value is type(None):
        return {"type-module": "builtins", "type-path": ["NoneType"]}
    if value is typing.Any:
        return {"type-any?": True}
    if any(value is marker for marker in (getattr(typing, "Never", None), typing.NoReturn)):
        return {"type-never?": True}
    if value is getattr(typing, "Self", None):
        return runtime_type(owner) if owner is not None else {}
    if exact_type(value, TYPE_PARAMETER_TYPES):
        result = {"typevar": value.__name__}
        if type(value) is PARAMSPEC_TYPE:
            result["parameter-spec?"] = True
            return result
        if type(value) is TYPEVARTUPLE_TYPE:
            result["variadic?"] = True
            return result
        # PEP 695 bounds/constraints/defaults are lazily evaluated descriptors.
        # Auto-variance marks these parameters; static AST inspection can resolve
        # their declarations without executing the deferred expressions.
        if getattr(value, "__infer_variance__", False):
            return result
        if value.__bound__ is not None:
            result["type-bound"] = runtime_type(value.__bound__, module, owner, depth + 1, seen)
        if value.__constraints__:
            result["type-constraints"] = [runtime_type(v, module, owner, depth + 1, seen) for v in value.__constraints__]
        default = getattr(value, "__default__", inspect.Signature.empty)
        if default is not getattr(typing, "NoDefault", inspect.Signature.empty):
            result["type-default"] = runtime_type(default, module, owner, depth + 1, seen)
        return result
    if exact_type(value, (PARAMSPEC_ARGS_TYPE, PARAMSPEC_KWARGS_TYPE)):
        result = runtime_type(value.__origin__, module, owner, depth + 1, seen)
        result["parameter-part"] = "args" if type(value) is PARAMSPEC_ARGS_TYPE else "kwargs"
        return result
    if is_class(value):
        cls_module = class_attribute(value, "__module__")
        name = class_attribute(value, "__qualname__")
        return {"type-module": cls_module, "type-path": name.split(".")} if type(cls_module) is str and type(name) is str else {}
    trusted = exact_type(value, TYPING_TYPES) or exact_type(value, (types.GenericAlias, types.UnionType))
    origin = typing.get_origin(value) if trusted else None
    args = typing.get_args(value) if trusted else ()
    if origin is typing.Union or origin is types.UnionType:
        return union_type([runtime_type(arg, module, owner, depth + 1, seen) for arg in args])
    wrappers = (typing.Annotated, typing.ClassVar, typing.Final,
                getattr(typing, "Required", None), getattr(typing, "NotRequired", None),
                getattr(typing, "ReadOnly", None))
    if any(origin is wrapper for wrapper in wrappers if wrapper is not None):
        return runtime_type(args[0], module, owner, depth + 1, seen) if args else {}
    if any(origin is marker for marker in (getattr(typing, "TypeGuard", None), getattr(typing, "TypeIs", None))
           if marker is not None):
        name = "TypeIs" if origin is getattr(typing, "TypeIs", None) else "TypeGuard"
        return {"type-module": "typing", "type-path": [name],
                "type-arguments": [runtime_type(arg, module, owner, depth + 1, seen) for arg in args]}
    if origin is typing.Literal:
        values = [arg for arg in args if exact_type(arg, (str, int, bool, float, bytes, type(None)))]
        serializable = [arg for arg in values if type(arg) is not bytes]
        result = union_type([runtime_type(type(arg)) for arg in values])
        if len(serializable) == len(args):
            result["literal-values"] = serializable
        return result
    if origin is getattr(typing, "Unpack", None) and origin is not None:
        result = runtime_type(args[0], module, owner, depth + 1, seen) if args else {}
        return {**result, "unpack?": True}
    if value is Ellipsis:
        return {"type-ellipsis?": True}
    if origin is not None:
        result = runtime_type(origin, module, owner, depth + 1, seen)
        if origin is getattr(typing, "Concatenate", None):
            result = {"type-module": "typing", "type-path": ["Concatenate"]}
        if args:
            arguments = []
            for arg in args:
                if type(arg) is list:
                    arguments.append({"parameter-list?": True, "type-arguments": [
                        runtime_type(item, module, owner, depth + 1, seen) for item in arg]})
                else:
                    arguments.append(runtime_type(arg, module, owner, depth + 1, seen))
            result["type-arguments"] = arguments
        return result
    if type(value) is typing.ForwardRef:
        value = value.__forward_arg__
    if type(value) is str:
        try:
            node = ast.parse(value, mode="eval").body
        except (SyntaxError, ValueError):
            return {}
        return runtime_annotation_node(node, module, owner, depth + 1, seen)
    return {}


# Runtime annotations and explicit module requests share one bounded source graph.
# Scope it to the inspection call so library use cannot mix separate environments.
_runtime_inspector = contextvars.ContextVar("blt_runtime_inspector", default=None)


@functools.lru_cache(maxsize=32)
def runtime_source_context(module, filename, mtime, size, inspector=None):
    import tokenize
    with tokenize.open(filename) as stream:
        tree = ast.parse(stream.read(), filename=filename, type_comments=True)
    inspector = inspector or StaticInspector([], [entry for entry in sys.path if type(entry) is str])
    inspector.source_packages.add(module.split(".")[0])
    inspector.dependency(Path(filename))
    return StaticModule(inspector, module, Path(filename), tree)


def source_context(module):
    loaded = sys.modules.get(module) if type(module) is str else None
    filename = vars(loaded).get("__file__") if type(loaded) is types.ModuleType else None
    if type(filename) is not str or not filename.endswith((".py", ".pyi")):
        return None
    try:
        stat = Path(filename).stat()
        return runtime_source_context(module, filename, stat.st_mtime_ns, stat.st_size, _runtime_inspector.get())
    except (OSError, ValueError, SyntaxError, UnicodeError, RecursionError):
        return None


def runtime_source_reference(node, module, owner=None):
    context = source_context(module)
    root = (expr_text(node) or "").split(".")[0]
    if context is None or not (root in context.aliases or root in context.alias_nodes
                               or root in context.typevars or (root,) in context.classes):
        return {}
    owner_path = ()
    if is_class(owner):
        name = class_attribute(owner, "__qualname__")
        if type(name) is str:
            owner_path = tuple(name.split("."))
    return context.reference(node, owner_path)


def runtime_class_annotations(cls):
    # Reading a class namespace bypasses PEP 649's annotation evaluator.
    declared = class_attribute(cls, "__dict__").get("__annotations__", {})
    result = {key: value for key, value in declared.items() if type(key) is str} if type(declared) is dict else {}
    context = source_context(class_attribute(cls, "__module__"))
    name = class_attribute(cls, "__qualname__")
    node = context.classes.get(tuple(name.split("."))) if context and type(name) is str else None
    if node is not None:
        for statement in node.body:
            if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                result.setdefault(statement.target.id, expr_text(statement.annotation))
    return result


def runtime_annotation_node(node, module=None, owner=None, depth=0, seen=frozenset()):
    if depth > 24:
        return {}
    if isinstance(node, ast.Constant):
        if node.value is None:
            return runtime_type(None)
        if node.value is Ellipsis:
            return {"type-ellipsis?": True}
        if isinstance(node.value, str):
            return runtime_type(node.value, module, owner, depth + 1, seen)
        return {}
    if isinstance(node, ast.List):
        return {"parameter-list?": True, "type-arguments": [
            runtime_annotation_node(n, module, owner, depth + 1, seen) for n in node.elts]}
    if isinstance(node, ast.Starred):
        return {**runtime_annotation_node(node.value, module, owner, depth + 1, seen), "unpack?": True}
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return union_type([runtime_annotation_node(n, module, owner, depth + 1, seen)
                           for n in (node.left, node.right)])
    if isinstance(node, ast.Subscript):
        name = expr_text(node.value).split(".")[-1]
        args = list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]
        if name == "Unpack":
            return {**runtime_annotation_node(args[0], module, owner, depth + 1, seen), "unpack?": True}
        if name == "Concatenate":
            return {"type-module": "typing", "type-path": ["Concatenate"], "type-arguments": [
                runtime_annotation_node(n, module, owner, depth + 1, seen) for n in args]}
        if name in ("Union", "Optional"):
            values = [runtime_annotation_node(n, module, owner, depth + 1, seen) for n in args]
            return union_type(values + ([runtime_type(None)] if name == "Optional" else []))
        if name in ("Annotated", "ClassVar", "Final", "Required", "NotRequired", "ReadOnly"):
            return runtime_annotation_node(args[0], module, owner, depth + 1, seen)
        if name == "Literal":
            try:
                values = [ast.literal_eval(n) for n in args]
            except (ValueError, TypeError):
                return {}
            result = union_type([runtime_type(type(v)) for v in values])
            if all(exact_type(v, (str, int, float, bool, type(None))) for v in values):
                result["literal-values"] = values
            return result
        result = runtime_annotation_node(node.value, module, owner, depth + 1, seen)
        if result:
            result["type-arguments"] = [runtime_annotation_node(n, module, owner, depth + 1, seen) for n in args]
        return result
    parts = (expr_text(node) or "").split(".")
    if not all(part.isidentifier() for part in parts):
        return {}
    if parts == ["Self"] and owner is not None:
        return runtime_type(owner)
    namespace = vars(sys.modules[module]) if type(module) is str and module in sys.modules else {}
    value = namespace.get(parts[0], vars(sys.modules["builtins"]).get(parts[0]))
    if value is None:
        return runtime_source_reference(node, module, owner)
    for part in parts[1:]:
        if type(value) is types.ModuleType:
            value = vars(value).get(part)
        elif is_class(value):
            value = static_class_member(value, part)
        elif type(value) is PARAMSPEC_TYPE and part in ("args", "kwargs"):
            return {**runtime_type(value, module, owner, depth + 1, seen), "parameter-part": part}
        else:
            return {}
    return runtime_type(value, module, owner, depth + 1, seen) if value is not None else {}

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


def expand_typed_keywords(info, resolve):
    """PEP 692 kwargs are separate keyword-only parameters, not scalar TypedDict values."""
    if "parameters" not in info or not any(
            p.get("kind") == "var-keyword" and p.get("unpack?") for p in info["parameters"]):
        return info
    parameters = []
    occupied = {p["name"] for p in info["parameters"]
                if p.get("kind") in ("positional-or-keyword", "keyword-only")}
    for parameter in info["parameters"]:
        if parameter.get("kind") != "var-keyword" or not parameter.get("unpack?"):
            parameters.append(parameter)
            continue
        schema = resolve(parameter) or {}
        fields = schema.get("typed-dict-fields")
        if schema.get("typed-dict?") and isinstance(fields, dict):
            bindings = bind_type_parameters(schema.get("type-parameters", []),
                                            parameter.get("type-arguments", []))
            fields = substitute_types(fields, bindings)
            for name, field in fields.items():
                if name not in occupied:
                    parameters.append({**type_fields(field), "name": name,
                                       "kind": "keyword-only", "required?": bool(field.get("required?")),
                                       **({"annotation": field["annotation"]} if field.get("annotation") else {})})
        else:
            # An unresolved Unpack isn't the scalar type of every keyword value.
            parameters.append({key: value for key, value in parameter.items() if key not in TYPE_KEYS})
    return {**info, "parameters": parameters,
            "signature": signature_text(parameters, info.get("return-type"))}


def runtime_keyword_schema(reference):
    module = reference.get("type-module")
    path = reference.get("type-path", [])
    loaded = sys.modules.get(module) if type(module) is str else None
    value = loaded
    for name in path:
        if type(value) is types.ModuleType:
            value = vars(value).get(name)
        elif is_class(value):
            value = static_class_member(value, name)
        else:
            return {}
    if not is_class(value):
        return {}
    namespace = class_attribute(value, "__dict__")
    required = namespace.get("__required_keys__")
    optional = namespace.get("__optional_keys__")
    if type(required) is not frozenset or type(optional) is not frozenset:
        return {}
    # Source handles Required/NotRequired correctly even with postponed annotations,
    # where Python's runtime required-key sets can be incomplete.
    context = source_context(module)
    if context is not None:
        declared = context.resolve(module, path)
        if declared and declared.get("typed-dict?"):
            return declared
    required = frozenset(key for key in required if type(key) is str)
    optional = frozenset(key for key in optional if type(key) is str)
    hints = runtime_class_annotations(value)
    fields = {name: {"name": name, "required?": name in required,
                     **runtime_type(hints.get(name, inspect.Signature.empty), module, value)}
              for name in required | optional}
    parameters = namespace.get("__parameters__", ())
    return {"typed-dict?": True, "typed-dict-fields": fields,
            "type-parameters": [runtime_type(param) for param in parameters
                                if exact_type(param, TYPE_PARAMETER_TYPES)] if type(parameters) is tuple else []}


def guard_metadata(result):
    if (result.get("type-module") in ("typing", "typing_extensions")
            and result.get("type-path") in (["TypeGuard"], ["TypeIs"])
            and result.get("type-arguments")):
        target = result["type-arguments"][0]
        is_type = result["type-path"] == ["TypeIs"]
        result = {key: value for key, value in result.items() if key not in TYPE_KEYS}
        result.update({"type-module": "builtins", "type-path": ["bool"],
                       "type-guard": target, "type-is?": is_type})
    return result


def function_yields(node):
    pending = list(node.body)
    while pending:
        current = pending.pop()
        if isinstance(current, (ast.Yield, ast.YieldFrom)):
            return True
        if not isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            pending.extend(ast.iter_child_nodes(current))
    return False


@functools.lru_cache(maxsize=32)
def source_signatures(filename, modified, size):
    """Short-lived worker cache of source annotations, never evaluated."""
    try:
        import tokenize
        with tokenize.open(filename) as stream:
            tree = ast.parse(stream.read(), filename=filename, type_comments=True)
    except (OSError, SyntaxError, UnicodeError):
        return {}
    result = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            first = min([node.lineno] + [decorator.lineno for decorator in node.decorator_list])
            result[(first, node.name)] = static_signature(node)
    return result


def source_signature(value):
    try:
        filename = value.__code__.co_filename
        stat = Path(filename).stat()
        return source_signatures(filename, stat.st_mtime_ns, stat.st_size).get(
            (value.__code__.co_firstlineno, value.__name__), {})
    except (OSError, ValueError):
        return {}


def runtime_signature(value, drop_first=False, owner=None):
    # Never ask arbitrary callable instances for __signature__ or __wrapped__.
    if not (exact_type(value, CALLABLE_TYPES)
            or (is_class(value) and type(value) is type)):
        return {}
    if is_class(value):
        # inspect.signature itself uses ordinary attribute lookup for class
        # constructors. Reject custom descriptors before reaching that code.
        for key in ("__new__", "__init__"):
            constructor = inspect.getattr_static(value, key, None)
            if type(constructor) is staticmethod:
                constructor = constructor.__func__
            if constructor is not None and not exact_type(constructor, CALLABLE_TYPES):
                return {}
        text_sig = inspect.getattr_static(value, "__text_signature__", None)
        if text_sig is not None and not exact_type(text_sig, (str, types.GetSetDescriptorType)):
            return {}
    seen = set()
    while type(value) is types.FunctionType and id(value) not in seen:
        seen.add(id(value))
        wrapped = value.__dict__.get("__wrapped__")
        if not exact_type(wrapped, CALLABLE_TYPES):
            break
        value = wrapped
    override = inspect.getattr_static(value, "__signature__", None)
    if override is not None and type(override) is not inspect.Signature:
        return {}
    if sys.version_info >= (3, 14) and is_class(value):
        constructor = static_class_member(value, "__init__")
        if type(constructor) is types.FunctionType:
            return runtime_signature(constructor, drop_first=True, owner=value)
        constructor = static_class_member(value, "__new__")
        if type(constructor) is staticmethod:
            constructor = constructor.__func__
        if type(constructor) is types.FunctionType:
            return runtime_signature(constructor, drop_first=True, owner=value)
    original = value
    declared = {}
    if type(value) is types.FunctionType:
        if ((value.__defaults__ is not None and type(value.__defaults__) is not tuple)
                or (value.__kwdefaults__ is not None and type(value.__kwdefaults__) is not dict)):
            return {}
        if sys.version_info < (3, 14) and type(value.__annotations__) is not dict:
            return {}
    if sys.version_info >= (3, 14) and type(value) is types.FunctionType:
        declared = source_signature(value)
        # A fresh function has no lazy annotation evaluator. It is never called.
        value = types.FunctionType(value.__code__, value.__globals__, value.__name__,
                                   value.__defaults__, value.__closure__)
        value.__kwdefaults__ = original.__kwdefaults__
        if override is not None:
            value.__signature__ = override
    try:
        sig = inspect.signature(value, follow_wrapped=False, eval_str=False)
    except (TypeError, ValueError, AttributeError):
        return {}
    module = getattr(value, "__module__", None) if exact_type(value, CALLABLE_TYPES) else None
    if any(type(p) is not inspect.Parameter for p in sig.parameters.values()):
        return {}
    params = [{
        "name": p.name,
        "kind": KINDS[p.kind],
        "required?": p.default is inspect.Parameter.empty
                     and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD),
        "annotation": annotation(p.annotation),
        **runtime_type(p.annotation, module, owner),
    } for p in sig.parameters.values()]
    if drop_first and params:
        params = params[1:]
    returns = annotation(sig.return_annotation)
    module = getattr(original, "__module__", None) if exact_type(original, CALLABLE_TYPES) else None
    if not declared and type(original) is types.FunctionType:
        original_params = list(sig.parameters.values())[1:] if drop_first else list(sig.parameters.values())
        if any(raw.annotation is not inspect.Parameter.empty and not type_fields(param)
               for raw, param in zip(original_params, params)):
            # Backport typing objects need not have stdlib typing's exact runtime
            # classes. Their source annotation text is safe to parse without
            # invoking custom typing objects or adding worker dependencies.
            declared = source_signature(original)
    if declared:
        by_name = {param["name"]: param for param in declared["parameters"]}
        for param in params:
            hint = by_name.get(param["name"], {}).get("annotation")
            if hint and param.get("annotation") is None:
                param["annotation"] = hint
                param.update(runtime_type(hint, module, owner))
        if returns is None:
            returns = declared.get("return-type")
    result_type = runtime_type(sig.return_annotation, module, owner)
    if not result_type and returns:
        result_type = runtime_type(returns, module, owner)
    return expand_typed_keywords(
        guard_metadata({"parameters": params, "return-type": returns,
                        "signature": signature_text(params, returns), **result_type}),
        runtime_keyword_schema)


def safe_member(name, value, depth=1, owner=None):
    try:
        result = runtime_member(name, value, depth, owner)
        namespace = vars(sys.modules["builtins"])
        if type(name) is str and value is namespace.get(name) and name in (
                "list", "tuple", "dict", "set", "frozenset", "iter", "next", "enumerate",
                "zip", "sorted", "reversed", "map", "filter", "min", "max", "sum", "range",
                "len", "hash", "id", "ord", "repr", "ascii", "bin", "oct", "hex", "chr", "any", "all"):
            result["builtin-call"] = name
        elif any(owner is cls for cls in (dict, list, set, frozenset, tuple)):
            original = type.__dict__["__dict__"].__get__(owner, type(owner)).get(name)
            if value is original and name in ("keys", "values", "items", "get", "copy", "pop", "setdefault"):
                result["builtin-call"] = class_attribute(owner, "__name__") + "." + name
        return result
    except BaseException as error:
        return {"status": "unknown", "name": name, "reason": type(error).__name__}


def stored_attribute(value, name, default=None):
    field = inspect.getattr_static(value, name, default)
    if type(field) is types.MemberDescriptorType:
        return field.__get__(value, type(value))
    return field


def pydantic_parameters(value):
    namespace = class_attribute(value, "__dict__")
    fields = namespace.get("__pydantic_fields__")
    if type(fields) is not dict:
        return None
    params = []
    config = namespace.get("model_config", {})
    config = config if type(config) is dict else {}
    for name, field in fields.items():
        cls = type(field)
        fields_module = sys.modules.get("pydantic.fields")
        trusted = vars(fields_module).get("FieldInfo") if type(fields_module) is types.ModuleType else None
        if cls is not trusted:
            continue
        hint = stored_attribute(field, "annotation", inspect.Signature.empty)
        alias = stored_attribute(field, "validation_alias")
        if type(alias) is not str:
            alias = stored_attribute(field, "alias")
        default = stored_attribute(field, "default")
        factory = stored_attribute(field, "default_factory")
        required = (class_attribute(type(default), "__name__") == "PydanticUndefinedType"
                    and factory is None)
        if type(alias) is str and alias.isidentifier():
            name = alias
            if config.get("populate_by_name") or config.get("validate_by_name"):
                required = False  # Both names are accepted; avoid demanding one spelling.
        params.append({"name": name, "kind": "keyword-only", "required?": required,
                       "annotation": annotation(hint), **runtime_type(hint)})
    if config.get("extra") != "forbid":
        params.append({"name": "data", "kind": "var-keyword", "required?": False})
    return params


def runtime_member(name, value, depth=1, owner=None):
    result = {"status": "known", "name": name}
    bound_method = type(value) is types.MethodType and type(value.__func__) is types.FunctionType
    if bound_method:
        # MethodType's intrinsic fields do not invoke receiver descriptors.
        # Exported instance/class methods already have their first argument bound.
        receiver = value.__self__
        owner = receiver if is_class(receiver) else type(receiver)
        value = value.__func__
    static = type(value) is staticmethod
    cls_method = type(value) is classmethod or type(value) is types.ClassMethodDescriptorType
    if static or type(value) is classmethod:
        value = value.__func__
    if type(value) is property:
        result.update(kind="property")
        if type(value.fget) is types.FunctionType:
            info = runtime_signature(value.fget, owner=owner)
            result.update({key: item for key, item in info.items()
                           if key == "return-type" or key in TYPE_KEYS})
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
        model_params = pydantic_parameters(value)
        if model_params is not None:
            result.update(parameters=model_params, signature=signature_text(model_params, result["return-type"]))
            result["pydantic-model?"] = True
        class_dict = class_attribute(value, "__dict__")
        parameters = class_dict.get("__parameters__", ())
        if type(parameters) is tuple:
            result["type-parameters"] = [runtime_type(param) for param in parameters
                                         if exact_type(param, TYPE_PARAMETER_TYPES)]
            if result["type-parameters"]:
                result["type-arguments"] = [{**param, **({"unpack?": True} if param.get("variadic?") else {})}
                                            for param in result["type-parameters"]]
        result["protocol?"] = class_dict.get("_is_protocol") is True
        generic_bindings = {}
        original_bases = class_dict.get("__orig_bases__", ())
        result["bases"] = [runtime_type(base) for base in class_attribute(value, "__bases__")]
        if type(original_bases) is tuple:
            if original_bases:
                result["bases"] = [runtime_type(base) for base in original_bases]
            for base in original_bases:
                if not exact_type(base, TYPING_TYPES) and type(base) is not types.GenericAlias:
                    continue
                origin = typing.get_origin(base)
                if not is_class(origin):
                    continue
                base_params = class_attribute(origin, "__dict__").get("__parameters__", ())
                if type(base_params) is tuple:
                    generic_bindings.update(bind_type_parameters(
                        [runtime_type(param) for param in base_params if exact_type(param, TYPE_PARAMETER_TYPES)],
                        [runtime_type(arg) for arg in typing.get_args(base)]))
        if depth:
            members = {}
            # Bypass custom metaclass __dir__ and descriptor lookup.
            for base in reversed(class_attribute(value, "__mro__")):
                for key, child in tuple(class_attribute(base, "__dict__").items()):
                    if not key.startswith("_") or key in ("__getitem__", "__iter__", "__next__", "__call__",
                                                          "__enter__", "__exit__", "__aenter__", "__anext__"):
                        members[key] = substitute_types(safe_member(key, child, depth=0, owner=value),
                                                        generic_bindings)
                annotations = runtime_class_annotations(base)
                if type(annotations) is dict:
                    for key, hint in annotations.items():
                        if type(key) is str and not key.startswith("_") and (
                                key not in members or members[key].get("kind") == "variable"):
                            members[key] = {"status": "known", "name": key, "kind": "variable",
                                            "return-type": annotation(hint),
                                            **substitute_types(runtime_type(hint, class_attribute(base, "__module__"), base),
                                                               generic_bindings)}
            result["members"] = members
            required_keys = class_dict.get("__required_keys__")
            optional_keys = class_dict.get("__optional_keys__")
            if type(required_keys) is frozenset and type(optional_keys) is frozenset:
                result["typed-dict?"] = True
                fields = {}
                for key in required_keys | optional_keys:
                    if type(key) is str:
                        fields[key] = {"name": key, "required?": key in required_keys,
                                       **type_fields(members.get(key, {}))}
                result["typed-dict-fields"] = fields
                params = [{"kind": "keyword-only", **field} for field in fields.values()]
                result.update(parameters=params, signature=signature_text(params, result["return-type"]))
            named_fields = class_dict.get("_fields")
            if type(named_fields) is tuple and all(type(field) is str for field in named_fields):
                result["named-tuple?"] = True
            # super forwards through an instance-specific MRO that this class
            # metadata cannot know without executing the analyzed program.
            result["members-complete?"] = value is not super and not any(
                any(key in class_attribute(base, "__dict__")
                    for key in ("__getattr__", "__dict__"))
                for base in class_attribute(value, "__mro__"))
    elif exact_type(value, CALLABLE_TYPES):
        result["kind"] = "function"
        result.update(runtime_signature(value, drop_first=cls_method or bound_method, owner=owner))
        if type(value) is types.FunctionType and depth:
            attributes = value.__dict__
            result["members"] = {key: safe_member(key, child, depth=depth - 1)
                                 for key, child in attributes.items()
                                 if type(key) is str and not key.startswith("_")}
        if type(value) is types.FunctionType and hasattr(typing, "get_overloads"):
            overloads = [{"status": "known", "instance-method?": bool(owner is not None and not static and not cls_method and not bound_method),
                          **runtime_signature(fn, drop_first=cls_method or bound_method, owner=owner)}
                         for fn in typing.get_overloads(value)]
            if overloads:
                result["overloads"] = overloads
        result["instance-method?"] = bool(owner is not None and not static and not cls_method and not bound_method)
        if type(value) is types.FunctionType:
            result["async?"] = bool(value.__code__.co_flags & inspect.CO_COROUTINE)
            result["generator?"] = bool(value.__code__.co_flags & (inspect.CO_GENERATOR | inspect.CO_ASYNC_GENERATOR))
            result.update(filename=value.__code__.co_filename, row=value.__code__.co_firstlineno)
    else:
        result.update(kind="variable", **{"return-type": annotation(type(value)), **runtime_type(type(value))})
    doc = (value.__doc__ if exact_type(value, CALLABLE_TYPES)
           else static_class_member(value, "__doc__") if is_class(value) else None)
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
        self.remaining = 64
        self.dependencies = {}
        self.source_packages = set()
        self.paths = {}
        self.variables = {}

    def type_variables(self, value):
        """Memoize variable use in finalized declarations shared by subclasses."""
        if not isinstance(value, (dict, list)):
            return frozenset()
        key = id(value)
        if key not in self.variables:
            own = {"__Self__"} if isinstance(value, dict) and value.get("type-self?") else set()
            if isinstance(value, dict) and value.get("typevar"):
                own.add(value["typevar"])
            for child in value.values() if isinstance(value, dict) else value:
                own.update(self.type_variables(child))
            # Retain the value as well, so Python cannot recycle its identity.
            self.variables[key] = (value, frozenset(own))
        return self.variables[key][1]

    def substitute(self, value, bindings):
        if not self.type_variables(value).intersection(bindings):
            return value
        return substitute_types(value, bindings, self.type_variables)

    def path(self, name):
        source = name.split(".")[0] in self.source_packages
        key = (name, source)
        if key not in self.paths:
            self.paths[key] = (local_file(name, self.roots) or stub_file(name, self.installed)
                              or (local_file(name, self.installed) if source else None))
        return self.paths[key]

    def dependency(self, path):
        stat = path.stat()
        self.dependencies[str(path)] = [str(path), stat.st_mtime_ns, stat.st_size]

    def module(self, name, path=None):
        if path is not None:
            self.source_packages.add(name.split(".")[0])
        if name in self.modules:
            return self.modules[name]
        if not self.active:
            # Independent requested modules each get a bounded import graph;
            # a large package must not exhaust the budget for later imports.
            self.remaining = 64
        if name in self.active or self.remaining <= 0:
            return {"status": "unknown", "name": name, "reason": "cyclic-or-large-import"}
        path = path or self.path(name)
        if path is None:
            return {"status": "unknown", "name": name, "reason": "external-import"}
        self.remaining -= 1
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
        self.alias_parameters = {}
        self.alias_parameter_infos = {}
        self.bounds = {}
        self.typevars = {}
        self.is_stub = path.suffix == ".pyi"
        self.exports = None
        self.transforms = {}
        self.collect(tree.body)
        for declaration in tree.body:
            if isinstance(declaration, (ast.FunctionDef, ast.ClassDef)):
                for decorator in declaration.decorator_list:
                    if isinstance(decorator, ast.Call) and expr_text(decorator.func).split(".")[-1] == "dataclass_transform":
                        options = {kw.arg: kw.value for kw in decorator.keywords}
                        specifiers = options.get("field_specifiers")
                        self.transforms[declaration.name] = {
                            "dataclass-transform?": True,
                            "dataclass-kw-only?": isinstance(options.get("kw_only_default"), ast.Constant)
                                                  and options["kw_only_default"].value is True,
                            "dataclass-field-specifiers": [expr_text(item).split(".")[-1] for item in specifiers.elts]
                                                          if isinstance(specifiers, (ast.Tuple, ast.List)) else []}

        for key, value in self.alias_nodes.items():
            if isinstance(value, ast.Call) and expr_text(value.func).split(".")[-1] in ("TypeVar", "ParamSpec", "TypeVarTuple"):
                self.typevars[key] = self.typevar_info(key, value)

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
                self.alias_parameters[node.name.id] = [p.name for p in getattr(node, "type_params", [])]
                self.alias_parameter_infos[node.name.id] = list(getattr(node, "type_params", []))
            elif not owner and isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        self.alias_nodes[target.id] = node.value
                        factory = self.factory_class(target.id, node.value, node)
                        if factory is not None:
                            self.classes[(target.id,)] = factory
                        if target.id == "__all__":
                            try:
                                value = ast.literal_eval(node.value)
                                if isinstance(value, (list, tuple)) and all(isinstance(x, str) for x in value):
                                    self.exports = set(value)
                            except (ValueError, TypeError):
                                pass
            elif isinstance(node, ast.If) and self.type_checking(node.test):
                self.collect(node.body, owner)

    def factory_class(self, name, value, source):
        if not isinstance(value, ast.Call) or len(value.args) < 2:
            return None
        factory = expr_text(value.func).split(".")[-1]
        fields = value.args[1]
        pairs = []
        if factory == "TypedDict" and isinstance(fields, ast.Dict):
            pairs = [(key.value, hint) for key, hint in zip(fields.keys, fields.values)
                     if isinstance(key, ast.Constant) and isinstance(key.value, str)]
        elif factory == "NamedTuple" and isinstance(fields, (ast.List, ast.Tuple)):
            pairs = [(pair.elts[0].value, pair.elts[1]) for pair in fields.elts
                     if isinstance(pair, (ast.List, ast.Tuple)) and len(pair.elts) == 2
                     and isinstance(pair.elts[0], ast.Constant) and isinstance(pair.elts[0].value, str)]
        else:
            return None
        body = [ast.copy_location(ast.AnnAssign(target=ast.Name(id=key, ctx=ast.Store()),
                                               annotation=hint, value=None, simple=1), source)
                for key, hint in pairs]
        result = ast.ClassDef(name=name, bases=[ast.Name(id=factory, ctx=ast.Load())],
                              keywords=value.keywords, body=body, decorator_list=[])
        return ast.fix_missing_locations(ast.copy_location(result, source))

    def type_checking(self, node):
        return expr_text(node) in ("TYPE_CHECKING", "typing.TYPE_CHECKING")

    def location(self, node, name):
        return {"status": "known", "name": name, "filename": str(self.path),
                "row": node.lineno, "end-row": getattr(node, "end_lineno", node.lineno),
                "col": node.col_offset + 1}

    def typevar_info(self, name, node):
        info = {"typevar": name}
        factory = expr_text(node.func).split(".")[-1] if isinstance(node, ast.Call) else type(node).__name__
        if factory == "ParamSpec":
            info["parameter-spec?"] = True
        elif factory == "TypeVarTuple":
            info["variadic?"] = True
        if isinstance(node, ast.Call):
            options = {kw.arg: kw.value for kw in node.keywords}
            if "bound" in options:
                info["type-bound"] = self.reference(options["bound"], seen=(name,))
            if len(node.args) > 1:
                info["type-constraints"] = [self.reference(arg, seen=(name,)) for arg in node.args[1:]]
            if "default" in options:
                info["type-default"] = self.reference(options["default"], seen=(name,))
        elif getattr(node, "bound", None) is not None:
            bound = node.bound
            if isinstance(bound, ast.Tuple):
                info["type-constraints"] = [self.reference(arg) for arg in bound.elts]
            else:
                info["type-bound"] = self.reference(bound)
        if getattr(node, "default_value", None) is not None:
            info["type-default"] = self.reference(node.default_value)
        return info

    def reference(self, node, owner=(), seen=()):
        if node is None or len(seen) > 24:
            return {}
        if isinstance(node, ast.Constant):
            if node.value is None:
                return runtime_type(None)
            if node.value is Ellipsis:
                return {"type-ellipsis?": True}
            if isinstance(node.value, str):
                try:
                    return self.reference(ast.parse(node.value, mode="eval").body, owner, seen + ("string",))
                except (SyntaxError, ValueError):
                    return {}
        if isinstance(node, ast.Call):
            base = expr_text(node.func).split(".")[-1]
            if base in ("TypeVar", "ParamSpec", "TypeVarTuple"):
                name = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else "T"
                return self.typevar_info(name, node)
            if base == "NewType" and len(node.args) > 1:
                return self.reference(node.args[1], owner, seen)
            return {}
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return union_type([self.reference(n, owner, seen) for n in (node.left, node.right)])
        if isinstance(node, ast.Subscript):
            base = expr_text(node.value).split(".")[-1]
            args = list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]
            if base == "Unpack":
                return {**self.reference(args[0], owner, seen), "unpack?": True}
            if base == "Concatenate":
                return {"type-module": "typing", "type-path": ["Concatenate"],
                        "type-arguments": [self.reference(n, owner, seen) for n in args]}
            if base in ("Annotated", "ClassVar", "Final", "Required", "NotRequired", "ReadOnly", "InitVar"):
                return self.reference(args[0], owner, seen)
            if base in ("Union", "Optional"):
                refs = [self.reference(n, owner, seen) for n in args]
                return union_type(refs + ([runtime_type(None)] if base == "Optional" else []))
            if base == "Literal":
                try:
                    values = [ast.literal_eval(n) for n in args]
                except (ValueError, TypeError):
                    return {}
                result = union_type([runtime_type(type(v)) for v in values])
                if all(exact_type(v, (str, int, float, bool, type(None))) for v in values):
                    result["literal-values"] = values
                return result
            result = self.reference(node.value, owner, seen)
            if result:
                result = dict(result)
                arguments = [self.reference(n, owner, seen) for n in args]
                if isinstance(node.value, ast.Name):
                    variable = node.value.id
                    if variable in self.alias_nodes:
                        variables = self.alias_parameters.get(variable) or find_typevars(result)
                        if variables:
                            return substitute_types(result, bind_type_parameters(type_parameter_infos(result, variables), arguments))
                    elif variable in self.aliases:
                        module, path = self.aliases[variable]
                        declared = self.resolve(module, path) or {}
                        if declared.get("type-alias?"):
                            variables = declared.get("alias-parameters") or find_typevars(result)
                            return substitute_types(result, bind_type_parameters(type_parameter_infos(result, variables), arguments))
                result["type-arguments"] = arguments
            return result
        if isinstance(node, ast.Starred):
            return {**self.reference(node.value, owner, seen), "unpack?": True}
        if isinstance(node, ast.List):
            return {"parameter-list?": True, "type-arguments": [self.reference(n, owner, seen) for n in node.elts]}
        text = expr_text(node)
        if not text or not all(part.isidentifier() for part in text.split(".")):
            return {}
        parts = text.split(".")
        name = parts[0]
        if name == "Self" or text in ("typing.Self", "typing_extensions.Self"):
            return {"type-module": self.name, "type-path": list(owner), "type-self?": True} if owner else {}
        if name in self.typevars:
            result = dict(self.typevars[name])
            if result.get("parameter-spec?") and len(parts) == 2 and parts[1] in ("args", "kwargs"):
                result["parameter-part"] = parts[1]
            return result
        if name in self.bounds and name not in seen:
            return {"typevar": name, "type-bound": self.reference(self.bounds[name], owner, seen + (name,))}
        if name in self.aliases:
            module, path = self.aliases[name]
            full = path + parts[1:]
            if module in ("typing", "typing_extensions") and full:
                aliases = {"List": "list", "Dict": "dict", "Set": "set", "FrozenSet": "frozenset",
                           "Tuple": "tuple", "Type": "type"}
                collections = {"Iterable", "Iterator", "Sequence", "MutableSequence", "Mapping",
                               "MutableMapping", "Collection", "Container", "Set", "Callable",
                               "Awaitable", "Coroutine", "AsyncIterable", "AsyncIterator", "Generator"}
                if full[0] in aliases:
                    return {"type-module": "builtins", "type-path": [aliases[full[0]]]}
                if full[0] in collections:
                    return {"type-module": "collections.abc", "type-path": [full[0]]}
                if full[0] == "Any":
                    return {"type-any?": True}
                if full[0] in ("Never", "NoReturn"):
                    return {"type-never?": True}
            if full and (module, tuple(full)) not in seen:
                value = self.resolve(module, full)
                if value and type_fields(value):
                    return type_fields(value)
            return {"type-module": module, "type-path": full} if full else {}
        if tuple(parts) in self.classes:
            return {"type-module": self.name, "type-path": parts}
        for count in range(len(owner), 0, -1):
            candidate = owner[:count] + tuple(parts)
            if candidate in self.classes:
                return {"type-module": self.name, "type-path": list(candidate)}
        if name in self.alias_nodes and name not in seen:
            previous = self.typevars
            self.typevars = {**previous, **{p.name: self.typevar_info(p.name, p)
                                          for p in self.alias_parameter_infos.get(name, [])}}
            try:
                return self.reference(self.alias_nodes[name], owner, seen + (name,))
            finally:
                self.typevars = previous
        builtin = vars(sys.modules["builtins"]).get(name)
        if is_class(builtin):
            return {"type-module": "builtins", "type-path": parts}
        return {}

    def resolve(self, module, path):
        if not module:
            return None
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
        info.update(self.transforms.get(node.name, {}))
        info.update(static_signature(node, drop_first=cls_method))
        if getattr(node, "type_comment", None):
            try:
                comment = ast.parse(node.type_comment, mode="func_type")
                raw_params = list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
                if len(comment.argtypes) == len(raw_params):
                    for param, hint in zip(raw_params, comment.argtypes):
                        if param.annotation is None:
                            param.annotation = hint
                if node.returns is None:
                    node.returns = comment.returns
                info.update(static_signature(node, drop_first=cls_method))
            except (SyntaxError, ValueError):
                pass
        previous_bounds = self.bounds
        self.bounds = {**self.bounds, **{p.name: p.bound for p in getattr(node, "type_params", [])
                                      if hasattr(p, "bound") and p.bound is not None}}
        previous_vars = self.typevars
        self.typevars = {**self.typevars, **{p.name: self.typevar_info(p.name, p)
                                           for p in getattr(node, "type_params", [])}}
        parameters = list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
        parameters += [p for p in (node.args.vararg, node.args.kwarg) if p is not None]
        by_name = {p.arg: p for p in parameters}
        for param in info["parameters"]:
            param.update(self.reference(by_name[param["name"]].annotation, owner))
        info.update(self.reference(node.returns, owner))
        info = expand_typed_keywords(
            guard_metadata(info), lambda ref: self.resolve(ref.get("type-module"), ref.get("type-path", [])))
        if self.typevars:
            used = set()
            for param in info["parameters"]:
                used.update(find_typevars(param))
            used.update(find_typevars(type_fields(info)))
            info["type-parameters"] = [self.typevars[name] for name in self.typevars if name in used]
        self.typevars = previous_vars
        self.bounds = previous_bounds
        if isinstance(node, ast.AsyncFunctionDef):
            info["async?"] = True
        if "contextmanager" in decorators or "asynccontextmanager" in decorators:
            info["context-manager?"] = True
            info["async-context-manager?"] = "asynccontextmanager" in decorators
        info["generator?"] = function_yields(node)
        known = {"classmethod", "staticmethod", "property", "cached_property", "overload",
                 "abstractmethod", "final", "override", "cache", "lru_cache",
                 "wraps", "no_type_check", "deprecated", "validate_call", "contextmanager", "asynccontextmanager", "dataclass_transform"}
        unknown = decorators - known
        safe_decorators = set()
        for decorator in unknown:
            declaration = next((n for n in self.tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                                and n.name == decorator), None)
            if declaration is not None:
                args = list(declaration.args.posonlyargs) + list(declaration.args.args)
                if args and isinstance(args[0].annotation, ast.Subscript):
                    hint = args[0].annotation
                    if expr_text(hint.value).split(".")[-1] == "Callable" and expr_text(hint) == expr_text(declaration.returns):
                        safe_decorators.add(decorator)
        if unknown - safe_decorators or prop:
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
            previous_vars = self.typevars
            self.typevars = {**self.typevars, **{p.name: self.typevar_info(p.name, p)
                                               for p in getattr(node, "type_params", [])}}
            children = {}
            bases = []
            inherited_parameters = None
            unresolved_bases = []
            transform = next((self.transforms[name] for name in self.decorators(node) if name in self.transforms), {})
            for keyword in node.keywords:
                if keyword.arg == "metaclass":
                    transform = self.transforms.get(expr_text(keyword.value).split(".")[-1], transform)
            for base in node.bases:
                ref = self.reference(base, owner[:-1])
                if ref:
                    bases.append(ref)
                    base_info = self.resolve(ref.get("type-module"), ref.get("type-path", []))
                    if base_info:
                        if base_info.get("dataclass-transform?"):
                            transform = {key: value for key, value in base_info.items() if key.startswith("dataclass-")}
                        bindings = bind_type_parameters(base_info.get("type-parameters", []),
                                                        ref.get("type-arguments", []))
                        bindings["__Self__"] = {"type-module": self.name, "type-path": list(owner), "type-self?": True}
                        base_info = self.inspector.substitute(base_info, bindings)
                        children = {**base_info.get("members", {}), **children}
                        if inherited_parameters is None and "parameters" in base_info:
                            inherited_parameters = base_info["parameters"]
                    elif ref.get("type-module") not in ("typing", "typing_extensions"):
                        unresolved_bases.append(ref)
            for decorator in self.decorators(node):
                if decorator in self.aliases:
                    module, path = self.aliases[decorator]
                    declaration = self.resolve(module, path) or {}
                    if declaration.get("dataclass-transform?"):
                        transform = {key: value for key, value in declaration.items() if key.startswith("dataclass-")}
            own, complete = self.members(node.body, owner)
            children.update(own)
            if "dataclass" in self.decorators(node):
                for field in node.body:
                    if isinstance(field, ast.AnnAssign) and isinstance(field.target, ast.Name):
                        marker = expr_text(field.annotation).split("[")[0].split(".")[-1]
                        if marker in ("InitVar", "KW_ONLY"):
                            children.pop(field.target.id, None)
            info = {**self.location(node, owner[-1]), "kind": "class",
                    "doc": (ast.get_docstring(node) or "")[:2000],
                    "return-type": self.name + "." + ".".join(owner),
                    "type-module": self.name, "type-path": list(owner),
                    "members": children, "members-complete?": False, "bases": bases,
                    "unresolved-bases": unresolved_bases}
            info.update(self.transforms.get(node.name, {}))
            if transform:
                info.update(transform)
            variable_names = []
            for parameter in getattr(node, "type_params", []):
                variable_names.append(parameter.name)
            for base in node.bases:
                for name in find_typevars(self.reference(base, owner[:-1])):
                    if name not in variable_names:
                        variable_names.append(name)
            info["type-parameters"] = [self.typevars.get(name, {"typevar": name}) for name in variable_names]
            if variable_names:
                info["type-arguments"] = [{**param, **({"unpack?": True} if param.get("variadic?") else {})}
                                          for param in info["type-parameters"]]
            base_names = {expr_text(base).split("[")[0].split(".")[-1] for base in node.bases}
            enum = any(ref.get("type-module") == "enum"
                       and ref.get("type-path", [""])[0] in ("Enum", "IntEnum", "StrEnum", "Flag", "IntFlag") for ref in bases)
            if enum:
                info["enum?"] = True
                for name, member in children.items():
                    if not name.startswith("_") and member.get("kind") == "variable":
                        member = {key: value for key, value in member.items() if key not in TYPE_KEYS}
                        member.update({"type-module": self.name, "type-path": list(owner),
                                       "return-type": info["return-type"]})
                        children[name] = member
            info["protocol?"] = "Protocol" in base_names or any(
                child.get("protocol?") for child in [self.resolve(b.get("type-module"), b.get("type-path", [])) or {}
                                                    for b in bases])
            model = any(ref.get("type-module", "").startswith("pydantic")
                        and ref.get("type-path") == ["BaseModel"] for ref in bases)
            model = model or any((self.resolve(ref.get("type-module"), ref.get("type-path", [])) or {}).get("pydantic-model?")
                                 for ref in bases)
            constructor = own.get("__init__") or own.get("__new__")
            if model:
                info["pydantic-model?"] = True
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
            elif model:
                params = self.model_parameters(node, inherited_parameters or [])
                info.update(parameters=params, signature=signature_text(params, info["return-type"]))
            elif "dataclass" in self.decorators(node) or transform:
                params = self.dataclass_parameters(node, inherited_parameters or [], transform)
                info.update(parameters=params, signature=signature_text(params, info["return-type"]))
            elif inherited_parameters is not None:
                info.update(parameters=inherited_parameters,
                            signature=signature_text(inherited_parameters, info["return-type"]))
            elif not node.bases:
                info.update(parameters=[], signature=signature_text([], info["return-type"]))
            if self.decorators(node) - {"dataclass", "dataclass_transform", "final", "runtime_checkable"} and not transform:
                for key in ("parameters", "signature", "overloads"):
                    info.pop(key, None)
            if "TypedDict" in base_names or any(child.get("typed-dict?") for child in
                    [self.resolve(b.get("type-module"), b.get("type-path", [])) or {} for b in bases]):
                self.typed_dict_info(info, node, bases)
            elif "NamedTuple" in base_names:
                params = [{"name": field.target.id, "kind": "positional-or-keyword",
                           "required?": field.value is None, "annotation": expr_text(field.annotation),
                           **self.reference(field.annotation, owner)}
                          for field in node.body if isinstance(field, ast.AnnAssign)
                          and isinstance(field.target, ast.Name)]
                info.update(parameters=params, signature=signature_text(params, info["return-type"]))
                info["named-tuple?"] = True
            self.typevars = previous_vars
            self.class_results[owner] = info
            return info
        finally:
            self.active_classes.remove(owner)

    def model_parameters(self, node, inherited):
        params = {param["name"]: param for param in inherited if param["kind"] != "var-keyword"}
        forbid = False
        for item in node.body:
            if isinstance(item, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "model_config" for t in item.targets):
                if isinstance(item.value, ast.Call):
                    forbid = any(k.arg == "extra" and isinstance(k.value, ast.Constant) and k.value.value == "forbid"
                                 for k in item.value.keywords)
            if not isinstance(item, ast.AnnAssign) or not isinstance(item.target, ast.Name):
                continue
            hint = expr_text(item.annotation)
            if item.target.id.startswith("_") or hint.split("[")[0].split(".")[-1] == "ClassVar":
                continue
            name = item.target.id
            default = item.value is not None and not (isinstance(item.value, ast.Constant) and item.value.value is Ellipsis)
            if isinstance(item.value, ast.Call) and expr_text(item.value.func).split(".")[-1] == "Field":
                options = {k.arg: k.value for k in item.value.keywords}
                default_node = options.get("default") or (item.value.args[0] if item.value.args else None)
                default = ("default_factory" in options or (default_node is not None and
                           not (isinstance(default_node, ast.Constant) and default_node.value is Ellipsis)))
                alias = options.get("validation_alias") or options.get("alias")
                if isinstance(alias, ast.Constant) and isinstance(alias.value, str) and alias.value.isidentifier():
                    name = alias.value
            params[name] = {"name": name, "kind": "keyword-only", "required?": not default,
                            "annotation": hint, **self.reference(item.annotation, (node.name,))}
        result = list(params.values())
        if not forbid:
            result.append({"name": "data", "kind": "var-keyword", "required?": False})
        return result

    def typed_dict_info(self, info, node, bases):
        fields = {}
        for ref in bases:
            base = self.resolve(ref.get("type-module"), ref.get("type-path", [])) or {}
            fields.update(base.get("typed-dict-fields", {}))
        total = not any(kw.arg == "total" and isinstance(kw.value, ast.Constant) and kw.value.value is False
                        for kw in node.keywords)
        for field in node.body:
            if not isinstance(field, ast.AnnAssign) or not isinstance(field.target, ast.Name):
                continue
            hint = field.annotation
            wrapper = expr_text(hint).split("[")[0].split(".")[-1]
            required = True if wrapper == "Required" else False if wrapper == "NotRequired" else total
            fields[field.target.id] = {"name": field.target.id, "required?": required,
                                      "annotation": expr_text(hint), **self.reference(hint, (node.name,))}
        params = [{"kind": "keyword-only", **field} for field in fields.values()]
        info.update(parameters=params, signature=signature_text(params, info["return-type"]))
        info["typed-dict?"] = True
        info["typed-dict-fields"] = fields

    def dataclass_parameters(self, node, inherited, transform=None):
        transform = transform or {}
        options = {"kw_only": transform.get("dataclass-kw-only?", False)}
        field_specifiers = {"field"} | set(transform.get("dataclass-field-specifiers", []))
        for deco in node.decorator_list:
            if isinstance(deco, ast.Call) and expr_text(deco.func).split(".")[-1] == "dataclass":
                options.update({kw.arg: kw.value.value for kw in deco.keywords if isinstance(kw.value, ast.Constant)})
            elif transform and isinstance(deco, ast.Call):
                options.update({kw.arg: kw.value.value for kw in deco.keywords if isinstance(kw.value, ast.Constant)})
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
            if isinstance(field.value, ast.Call) and expr_text(field.value.func).split(".")[-1] in field_specifiers:
                field_options = {kw.arg: kw.value for kw in field.value.keywords}
                if isinstance(field_options.get("init"), ast.Constant) and field_options["init"].value is False:
                    continue
                default = "default" in field_options or "default_factory" in field_options
            keyword = field_options.get("kw_only")
            keyword = keyword.value if isinstance(keyword, ast.Constant) else kw_only
            name = field.target.id
            alias = field_options.get("alias")
            if transform and isinstance(alias, ast.Constant) and isinstance(alias.value, str):
                name = alias.value
            params[field.target.id] = {
                "name": name, "kind": "keyword-only" if keyword else "positional-or-keyword",
                "required?": not default, "annotation": hint,
                **self.reference(field.annotation, (node.name,))}
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
                                         "return-type": expr_text(node.value), "type-alias?": True,
                                         "alias-parameters": self.alias_parameters.get(node.name.id, []),
                                         **self.reference(ast.Name(id=node.name.id, ctx=ast.Load()), owner)}
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if not isinstance(target, ast.Name):
                        continue
                    if not owner and (target.id,) in self.classes and isinstance(node.value, ast.Call):
                        members[target.id] = self.class_info((target.id,))
                        continue
                    returns = expr_text(node.annotation) if isinstance(node, ast.AnnAssign) else None
                    alias = (isinstance(node, ast.AnnAssign)
                             and expr_text(node.annotation).split(".")[-1] == "TypeAlias")
                    ref = self.reference(node.value if alias else node.annotation, owner) if isinstance(node, ast.AnnAssign) else {}
                    if getattr(node, "type_comment", None):
                        try:
                            ref = self.reference(ast.parse(node.type_comment, mode="eval").body, owner)
                            returns = node.type_comment
                        except (SyntaxError, ValueError):
                            pass
                    if isinstance(node.value, ast.Constant) and returns is None:
                        returns = annotation(type(node.value.value))
                        ref = runtime_type(type(node.value.value))
                    if isinstance(node.value, ast.Call) and not ref:
                        factory = expr_text(node.value.func).split(".")[-1]
                        ref = self.reference(node.value if factory in ("TypeVar", "NewType") else node.value.func, owner)
                        returns = expr_text(node.value.func)
                    if isinstance(node.value, ast.Subscript) and not ref:
                        ref = self.reference(node.value, owner)
                        returns = expr_text(node.value)
                    if isinstance(node.value, (ast.Name, ast.Attribute)):
                        alias_ref = self.reference(node.value, owner)
                        value = self.resolve(alias_ref.get("type-module"), alias_ref.get("type-path", [])) if alias_ref else None
                        if value:
                            members[target.id] = {**value, "name": target.id}
                            continue
                    members[target.id] = {**self.location(node, target.id), "kind": "variable",
                                          "return-type": returns, **ref}
                    if alias or (isinstance(node.value, ast.Subscript) and find_typevars(ref)):
                        members[target.id]["type-alias?"] = True
                        members[target.id]["alias-parameters"] = find_typevars(ref)
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
            refs = [type_fields(sig) for sig in signatures]
            for key in TYPE_KEYS:
                info.pop(key, None)
            info.update(union_type(refs))
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
                "package-paths": [str(self.path.parent)] if self.path.name.startswith("__init__.") else [],
                "inspection": "static"}


def static_module(name, path, inspector=None):
    inspector = inspector or StaticInspector([str(Path(path).parent)], [])
    return inspector.module(name, Path(path))


def inspect_module(name, roots, enabled, inspector=None, skip_stubs=False):
    inspector = inspector or StaticInspector(roots, [p for p in sys.path if p])
    token = _runtime_inspector.set(inspector)
    try:
        return _inspect_module(name, roots, enabled, inspector, skip_stubs)
    finally:
        _runtime_inspector.reset(token)


def _inspect_module(name, roots, enabled, inspector, skip_stubs=False):
    if name == "python":
        name = "builtins"
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
        package_paths = vars(module).get("__path__", [])
        package_paths = [p for p in package_paths if type(p) is str] if type(package_paths) in (list, tuple) else []
        return {"status": "known", "name": name, "kind": "module", "members": members,
                "package-paths": package_paths,
                "members-complete?": "__getattr__" not in vars(module),
                "filename": vars(module).get("__file__"),
                "doc": (vars(module).get("__doc__") or "")[:2000], "inspection": "runtime"}
    except BaseException as error:
        return {"status": "unknown", "name": name, "reason": type(error).__name__}


def encode_graph(value):
    """Encode shared JSON dictionaries and lists without duplicating subtrees."""
    records, known = [], {}
    def encode(item):
        if type(item) in (dict, list):
            key = id(item)
            if key not in known:
                record = (["dict", [[name, encode(child)] for name, child in item.items()]]
                          if type(item) is dict else ["list", [encode(child) for child in item]])
                known[key] = (item, len(records))
                records.append(record)
            return {"$ref": known[key][1]}
        return item
    root = encode(value)
    return {"$blt_graph": 1, "records": records, "root": root}


def decode_graph(value, object_hook=None):
    """Restore only JSON containers and backward references, never executable objects."""
    if type(value) is not dict or set(value) != {"$blt_graph", "records", "root"} or value["$blt_graph"] != 1:
        raise ValueError("Invalid metadata graph")
    records = []
    def resolve(item):
        if type(item) is dict:
            index = item.get("$ref")
            if set(item) != {"$ref"} or type(index) is not int or not 0 <= index < len(records):
                raise ValueError("Invalid metadata reference")
            return records[index]
        if type(item) is list:
            raise ValueError("Unencoded metadata list")
        return item
    for kind, children in value["records"]:
        if kind == "dict":
            record = {key: resolve(child) for key, child in children}
            if object_hook is not None:
                record = object_hook(record)
        elif kind == "list":
            record = [resolve(child) for child in children]
        else:
            raise ValueError("Invalid metadata record")
        records.append(record)
    return resolve(value["root"])


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
                   "dependencies", "bases", "unresolved-bases", "type-union", "type-parameters", "type-constraints",
                   "literal-values", "dataclass-field-specifiers", "package-paths"}
    enum_keys = {"status", "kind", "inspection"}
    type_shapes = {key: (bool if key.endswith("?") else list if key in vector_keys
                         or key == "literal-values" else str)
                   for key in TYPE_KEYS if key not in ("type-bound", "type-default")}
    def decode(raw):
        if (raw.get("$blt_graph") == 1 and set(raw) == {"$blt_graph", "records", "root"}
                and type(raw["records"]) is list):
            return decode_graph(raw, object_hook=decode)
        # Member tables use arbitrary Python names, including "status" and
        # "typevar". A schema key alone cannot distinguish them from records.
        record = (type(raw.get("status")) is str or type(raw.get("required?")) is bool
                  or any(type(raw.get(key)) is shape for key, shape in type_shapes.items()))
        if not record:
            return raw
        result = {}
        for key, value in raw.items():
            if key in ("members", "typed-dict-fields"):
                value = persistent_map(value)
            elif key in vector_keys:
                value = vector(value)
            elif key in enum_keys and value is not None:
                value = intern(value)
            result[intern(key)] = value
        return persistent_map(result)
    return decode

class InspectionProcesses:
    """Own inspection children so server shutdown cannot leave workers behind.

    Admission and registration share a lock with shutdown. The calling thread
    owns pipe communication; shutdown only signals and reaps registered children.
    """

    def __init__(self):
        import threading
        self._lock = threading.Lock()
        self._processes = set()
        self._closed = False

    def run(self, args, *, input=None, capture_output=False, timeout=None,
            check=False, **kwargs):
        """Run an owned child with the subprocess.run interface."""
        import subprocess
        if input is not None:
            if kwargs.get("stdin") is not None:
                raise ValueError("stdin and input arguments may not both be used.")
            kwargs["stdin"] = subprocess.PIPE
        if capture_output:
            if kwargs.get("stdout") is not None or kwargs.get("stderr") is not None:
                raise ValueError("stdout and stderr arguments may not be used with capture_output.")
            kwargs["stdout"] = subprocess.PIPE
            kwargs["stderr"] = subprocess.PIPE
        with self._lock:
            if self._closed:
                raise OSError("Python inspection has stopped")
            process = subprocess.Popen(args, **kwargs)
            self._processes.add(process)
        try:
            with process:
                try:
                    stdout, stderr = process.communicate(input, timeout=timeout)
                except subprocess.TimeoutExpired as error:
                    process.kill()
                    if sys.platform == "win32":
                        error.stdout, error.stderr = process.communicate()
                    else:
                        # POSIX communicate already attached partial byte output.
                        process.wait()
                    raise
                except BaseException:
                    process.kill()
                    process.wait()
                    raise
                if check and process.returncode:
                    raise subprocess.CalledProcessError(process.returncode, process.args,
                                                        output=stdout, stderr=stderr)
                return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
        finally:
            with self._lock:
                self._processes.discard(process)

    def stop(self):
        """Reject new children, then terminate, kill if needed, and reap all owned children."""
        import subprocess
        import time
        with self._lock:
            if self._closed:
                return
            self._closed = True
            processes = tuple(self._processes)
        for process in processes:
            process.terminate()
        deadline = time.monotonic() + 0.2
        for process in processes:
            try:
                process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
        for process in processes:
            process.wait()


def main():
    request = json.load(sys.stdin)
    if request.get("operation") in ("environment", "environment-info"):
        paths = [p for p in sys.path if p]
        result = (paths if request["operation"] == "environment" else
                  {"paths": paths, "version": list(sys.version_info[:2])})
        json.dump(result, sys.stdout)
        return
    roots = request.get("paths", [])
    inspector = StaticInspector(roots, [p for p in sys.path if p])
    results = {}
    for name in request["modules"]:
        result = inspect_module(name, roots, request.get("enabled", True), inspector)
        result["dependencies"] = list(inspector.dependencies.values())
        if request.get("stream"):
            payload = {name: result}
            if request.get("compact") == "graph":
                payload = encode_graph(payload)
            print(json.dumps(payload, ensure_ascii=True), flush=True)
        else:
            results[name] = result
    if not request.get("stream"):
        json.dump(results, sys.stdout, ensure_ascii=True)


if __name__ == "__main__":
    main()
