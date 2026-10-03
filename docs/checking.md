# Checking reference

`blt check` analyzes Basilisp source without evaluating its forms or expanding
project macros. Files are read without modification. Directories are searched
recursively for `.lpy` files, excluding generated directories and symlinks.

```sh
blt check src tests
blt check --format json src
blt check --filename example.lpy - < example.lpy
blt check --fail-level error .
```

Exit codes follow clj-kondo: **0** for no findings at the failure threshold,
**2** for warnings, **3** for analysis errors. Invalid command arguments or
configuration return **2**. `--fail-level error` permits warnings without a
nonzero exit. `--report-level` filters text output; JSON and EDN retain findings.

## Analysis

The analyzer tracks namespaces, definitions, function arities, lexical bindings,
destructuring, and references across the selected files. It understands common
binding and control forms, quoted/discarded data, and Basilisp reader branches.
Checks include unresolved symbols and namespaces, invalid arities, unused
bindings/imports/requires/refers, redefinitions, and syntax errors. Diagnostic
rows and columns are one-based; columns count UTF-16 code units, as in clj-kondo.

All selected files are indexed before analysis, so a definition can be resolved
in another selected namespace without importing or executing that namespace.
Installed Basilisp source is read statically when available.

The analyzer returns immutable analysis maps. `check.lpy` handles project file
selection, configuration, reporting, and exit status; `analyzer.lpy` owns language
analysis; `python.lpy` supplies Python metadata.

## Python inspection

```sh
blt check --python .venv/bin/python src
blt check --no-python-inspection src
blt check --python-timeout 10 src
```

By default the checker uses the project's `.venv` interpreter when present,
otherwise its own interpreter. Use `--python` when dependencies live elsewhere.

Local `.py` and `.pyi` files are parsed as Python ASTs without importing them.
Installed modules can be imported in a separate process to inspect members,
signatures, and annotations. The worker uses only Python's standard library;
the selected interpreter does not need Basilisp or blt installed.

Python checks distinguish positional-only, keyword-only, optional, and variadic
parameters. Known constructors and object members can be followed through local
bindings. Dynamic attributes and unavailable signatures remain unknown, so the
checker avoids claiming errors it cannot establish.

Installed-package inspection can execute that package's import initialization
and its transitive imports. The subprocess limits duration and separates failures;
it is not a security sandbox. `--no-python-inspection` disables runtime inspection.
Analyzed Basilisp forms are never evaluated; local Python files are inspected
statically.

## Configuration

The checker reads clj-kondo data configuration. Use the nearest
`.clj-kondo/config.edn`, `--config-dir DIR`, or repeat `--config` with an EDN
map or filename:

```clojure
{:linters {:unused-binding {:level :warning}
           :unresolved-symbol {:level :error}}
 :output {:format :json}}
```

```sh
blt check --config '{:linters {:unused-binding {:level :off}}}' src
blt check --repro --format json src
```

Configuration merges home settings, imported config paths, project settings,
`CLJ_KONDO_EXTRA_CONFIG_DIR`, and explicit configurations in that order.
Maps merge recursively; `^:replace` replaces a value. Later explicit configs win.
`--repro` ignores home configuration. An explicit `--format` overrides the
configured output format. Clojure hooks are rejected rather than executed.

## Compatibility and limits

CI compares original shared-language examples with clj-kondo **2026.08.04**,
pinned through the Nix lockfile. It checks diagnostic types, levels, messages,
source ranges, and exit codes. The core namespace differs intentionally:
`basilisp.core` replaces `clojure.core`.

This is a Basilisp analyzer with a tested subset of clj-kondo behavior, not
complete clj-kondo feature parity. Clojure hooks, arbitrary macro expansion,
full type checking, and every clj-kondo linter are not implemented. Python
inspection has separate tests because JVM Clojure has different interop.

```sh
nix develop .#compatibility --command uv run --locked python scripts/check_kondo.py
```
