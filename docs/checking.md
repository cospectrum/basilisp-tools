# Checking

`blt check` finds problems in Basilisp code without evaluating it or modifying
files. Directories are searched recursively for `.lpy` files, skipping generated
directories and symlinks.

```sh
blt check src tests
blt check --format json src
blt check --filename example.lpy - < example.lpy
blt check --fail-level error .
```

Exit codes are **0** for no findings at the failure threshold, **2** for warnings,
and **3** for analysis errors. Invalid arguments or configuration also return
**2**. Use `--fail-level error` to allow warnings. `--report-level` filters text
output; JSON and EDN retain all findings. `--lint PATH...` is an alias for
positional paths.

## Configuration

Put clj-kondo settings in `.clj-kondo/config.edn`:

```clojure
{:linters {:unused-binding {:level :warning}
           :unresolved-symbol {:level :error}}
 :output {:format :json}}
```

The nearest configuration directory is used, or you can select one with
`--config-dir DIR`. Add overrides with `--config`, accepting an EDN map or a
filename; repeat it to load multiple configurations.

```sh
blt check --config '{:linters {:unused-binding {:level :off}}}' src
blt check --repro --format json src
```

Settings merge in this order, with later values taking precedence: home settings,
imported config paths, project settings, `CLJ_KONDO_EXTRA_CONFIG_DIR`, then explicit
configurations. Maps merge recursively; `^:replace` replaces a value. `--repro`
skips home settings, and `--format` overrides the configured output format.
`#include "relative.edn"` loads another file relative to its containing config;
nested includes work, but cycles are rejected. Clojure hooks are unsupported.

You can scope settings by namespace (`:ns-groups`, `:config-in-ns`), call
(`:config-in-call`), or reader tag (`:config-in-tag`). Namespace and macro
`:clj-kondo/config` metadata and `:clj-kondo/ignore` directives are also supported.
Supported linters honor exclusions, including regular expressions and call-scoped
exclusions. Unused-binding options cover destructured function arguments, `:as`
bindings, and `defmulti` arguments. `:skip-comments`, scoped `:skip-args`, and
`:lint-as` work for recognized forms.

Output settings include `:include-files`, `:exclude-files`, custom `:pattern`,
and `:linter-name` (also called `:show-rule-name-in-message`). Top-level
`:exclude-files` also applies to stdin's `--filename`. The legacy `:if` linter
name is accepted as an alias for `:missing-else-branch`.

## What it checks

Checks include unresolved symbols and namespaces, invalid arities, unused
bindings/imports/requires/refers, redefinitions, and syntax errors. The analyzer
understands common binding and control forms, destructuring, quoted or discarded
data, and Basilisp reader branches. All selected files are indexed first, so
references can resolve across namespaces. Installed Basilisp source is also read
statically when available. Diagnostic rows and columns are one-based; columns
count UTF-16 code units, as in clj-kondo.

## Python inspection

```sh
blt check --python .venv/bin/python src
blt check --no-python-inspection src
blt check --python-timeout 10 src
```

The checker uses the project's `.venv` when present, otherwise its own interpreter.
Use `--python` when dependencies live elsewhere. That interpreter does not need
Basilisp or blt installed.

Local `.py` and `.pyi` files are parsed without importing them. Installed modules
can be imported in a separate process to inspect members, signatures, and
annotations. Checks distinguish positional-only, keyword-only, optional, and
variadic parameters, and can follow known constructors and members through local
bindings. Dynamic attributes and unavailable signatures remain unknown.

**Installed-package inspection runs import initialization code**, including
transitive imports. The subprocess has a timeout but is not a security sandbox.
Use `--no-python-inspection` to disable runtime inspection; local Python files
will still be inspected statically.

## Limitations

The checker supports a subset of clj-kondo behavior. It does not expand arbitrary
macros, perform full type checking, or implement every linter. Inline macro
configuration applies within its declaring namespace; automatic export to other
namespaces is incomplete. See [Compatibility](compatibility.md) for supported
behavior and remaining differences.
