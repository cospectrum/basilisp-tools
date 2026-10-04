# Compatibility

blt follows cljfmt, clj-kondo, and clojure-lsp where their behavior applies to
Basilisp. Compatibility means matching the supported behavior against upstream
implementations; it does not mean full replacement of every upstream feature.

## Tested scope

| Command | Upstream comparison | Additional coverage |
| --- | --- | --- |
| `blt format` | Formatting output, idempotence, option readers, discovery, and literal Leiningen configuration precedence | Basilisp syntax preservation, CLI overrides, file selection, parallel execution, and batch write validation |
| `blt check` | Diagnostic type, message, severity, location, scoped configuration, exclusions, configuration merges, output filters, and exit status | Basilisp forms, project resolution, Python imports, members, and callable inspection |
| `blt lsp` | Actual upstream settings helpers and startup merge behavior | Real stdio protocol tests for settings, indexing, diagnostics, Python interop, navigation, rename, and formatting |

The comparison scripts run real pinned upstream code or binaries. They are not
copies of blt's implementation. LSP settings comparisons do not establish
identical server responses or feature coverage.

CI runs the comparisons, the complete test suite on macOS and Linux with Python
3.10 and 3.13, blt's own formatter and checker, workflow validation, and smoke
tests against the installed wheel.

## Upstream versions

- cljfmt 0.16.6:
  [`baab500`](https://github.com/weavejester/cljfmt/tree/baab5008032945434cbca23ef5eda516e3ea97b0).
- clj-kondo 2026.08.04, supplied by the locked Nix compatibility environment.
- clojure-lsp:
  [`8ad65c1`](https://github.com/clojure-lsp/clojure-lsp/tree/8ad65c1d681d2fc9022b3854f6dcaf1677d29631).

Run the comparisons inside `nix develop .#compatibility`:

```sh
uv run --locked python scripts/check_cljfmt.py --cljfmt /path/to/cljfmt
uv run --locked python scripts/check_config.py --cljfmt /path/to/cljfmt
uv run --locked python scripts/check_kondo.py
uv run --locked python scripts/check_kondo_config.py
uv run --locked python scripts/check_lsp_config.py --clojure-lsp /path/to/clojure-lsp
```

Use checkouts at the revisions above. The formatter oracle also checks original
Basilisp fixtures and dynamically reads applicable upstream tests.

## Differences and unsupported behavior

- **CLI:** blt keeps the `format`, `check`, and `lsp` subcommands. Supported
  upstream options are adapted to these commands; not every upstream flag or
  output mode exists. `blt COMMAND --help` lists the available interface.
- **Configuration:** data is never evaluated. Clojure reader evaluation,
  executable Leiningen profiles, arbitrary tagged readers, and Clojure hooks
  are unsupported. Regexes use Python semantics, so Java-specific constructs
  may differ. Formatter option validation is stricter for unknown or invalid keys.
- **Checking:** the supported linter set is smaller than clj-kondo's. Full
  clj-kondo analysis output, SARIF, and progress reporting are unsupported.
  Dynamic macro expansion and Python behavior cannot always be resolved.
- **LSP:** JVM classpath and project discovery, dependency-exported settings,
  custom linters, semantic tokens, code actions, code lenses, inlay hints,
  structural refactorings, call hierarchy, and custom extensions are unsupported.
  Ancestor settings discovery deliberately visits real ancestor directories,
  instead of repeating the root file as the pinned upstream implementation does.
- **Language:** blt analyzes Basilisp and Python interop. JVM/ClojureScript-only
  semantics are outside its scope.

See [Formatting](formatting.md), [Checking](checking.md), and [Language server](lsp.md)
for supported options and operational details. A passing comparison suite covers
its fixtures, not every possible program or configuration.
