# Checking

`blt check` analyzes Basilisp without evaluating source forms or modifying files.
It indexes selected files together so references resolve across namespaces.

```sh
blt check src tests
blt check --format json src
blt check --filename example.lpy - < example.lpy
blt check --lang edn - < settings.edn
blt check --fail-level error .
```

Directories are searched for `.lpy` and `.cljc` files. Explicit `.edn` files are
checked as data. Inputs can also be archives, platform-separated path lists, or stdin.
Generated directories and symlinks are skipped.

Exit codes are **0** for success, **2** for warnings, and **3** for errors.
Invalid arguments or configuration return **2**. `--fail-level error` permits
warnings; `--report-level` filters text output. JSON, EDN, and SARIF retain all
findings. `--lint PATH...` accepts the same inputs as positional paths.
`--parallel` or `--parallel true` enables concurrent analysis with ordered output.

## Configuration

Put clj-kondo settings in `.clj-kondo/config.edn`:

```clojure
{:linters {:unused-binding {:level :warning}
           :unresolved-symbol {:level :error}}
 :output {:format :json}}
```

The nearest configuration directory is used. `--config-dir DIR` selects another;
`--config EDN_OR_FILE` adds overrides and can be repeated.

Settings merge in this order: home settings, imported config paths, project
settings, `CLJ_KONDO_EXTRA_CONFIG_DIR`, then explicit configurations. Later
values win. Maps merge recursively; `^:replace` replaces a value. `--repro`
skips home settings. `#include "relative.edn"` resolves relative to its containing
file; cycles are rejected.

Settings can be scoped by namespace (`:ns-groups`, `:config-in-ns`), call
(`:config-in-call`), or reader tag (`:config-in-tag`). Namespace and macro
`:clj-kondo/config` metadata, `:clj-kondo/ignore`, exclusions, `:skip-comments`,
`:skip-args`, and `:lint-as` are supported. Regex settings use EDN strings,
including Java-style quoting and Unicode properties.

Output options include file filters, a custom `:pattern`, `:linter-name`,
`:canonical-paths`, and `:progress`. Use `:analysis true` to export analysis,
or select details such as `:analysis {:locals true :keywords true :arglists true}`.
Exports include namespaces, definitions, usages, bindings, protocol implementations,
and configured context data. `--format sarif` supports code-scanning integrations.

### Dependencies and caching

```sh
blt check --dependencies --copy-configs path/to/library.whl
blt check --copy-configs --skip-lint path/to/dependencies
blt check --cache false src
```

Exported dependency configurations are copied from `clj-kondo.exports` into
the project configuration directory and loaded on subsequent checks. Combining
`--copy-configs` with `--dependencies` loads them in the same run.
`--dependencies` analyzes silently and populates the namespace cache.
`--skip-lint` supports configuration-copy tasks without reporting findings.

Declarations are cached under `.clj-kondo/.cache` when a configuration directory
exists. Source changes invalidate entries. Use `--cache-dir DIR` for another
location, `--cache false` to disable it, or `--debug` to inspect cache activity.

### Hooks

`:hooks {:analyze-call {...} :macroexpand {...}}` supports Clojure-style hook
namespaces and `clj-kondo.hooks-api` node transformations. Hook files run in a
restricted interpreter with pure collection, string, tree, and node operations.
They cannot import Python, access files, or call arbitrary host functions.

String hooks remain disabled unless explicitly enabled by
`:hooks {:__dangerously-allow-string-hooks__ true}`. They use the same restricted
interpreter. Ordinary source macros are not executed.

## Diagnostics

Checks cover resolution, arities and argument types, bindings and destructuring,
namespace declarations, definitions, protocols, control flow, duplicate data,
test assertions, docstrings, and optional style rules. Shared clj-kondo linter
names and severities are used where the behavior applies to Basilisp.

Basilisp semantics take precedence: for example, `format` uses Python's
percent-formatting rules, and reader conditionals select Basilisp branches.
Version branches such as `:lpy310+` follow the selected Python interpreter.
Source diagnostic rows and columns are one-based; columns count UTF-16 code units.

## Python support

```sh
blt check --python .venv/bin/python src
blt check --no-python-inspection src
blt check --python-timeout 10 src
```

The project's `.venv` is preferred; otherwise blt uses its own interpreter.
The selected interpreter does not need Basilisp or blt installed.

Local Python source and stubs are read without importing them. Installed modules
can be inspected in a separate process. Type stubs, overloads, inherited members,
properties, dataclasses, unions, generics, TypedDicts, and protocols inform call
checking and member lookup. Callable signatures, ParamSpec forwarding, and
variadic type parameters preserve information through higher-order calls. Known
types also flow through bindings, returns, common builtins, threaded calls, and
supported asynchronous or context-manager operations.

Runtime inspection executes package import initialization code. The subprocess
has a timeout and is not a security sandbox. The timeout covers an inspection
batch; completed results are retained if a later module times out.
`--no-python-inspection` disables runtime inspection while retaining static source and stub analysis.

Dynamic attributes, unknown macros, and missing annotations can leave types
unknown. The checker avoids reporting a mismatch without enough information.
See [Compatibility](compatibility.md) for the tested scope.
