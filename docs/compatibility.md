# Compatibility

blt follows familiar Clojure tooling conventions for Basilisp and Python interop.
It supports part of each tool's behavior:

| Command | Based on | Compared in CI |
| --- | --- | --- |
| `blt format` | [cljfmt](https://github.com/weavejester/cljfmt) | Formatting output and configuration |
| `blt check` | [clj-kondo](https://github.com/clj-kondo/clj-kondo) | Diagnostics, configuration, output, and exit codes |
| `blt lsp` | [clojure-lsp](https://clojure-lsp.io/) | Configuration loading and merging |

Separate tests exercise the language server through LSP, including Python interop,
navigation, rename, and formatting. Matching these tests does not imply complete
feature parity.

## Current limits

- **Formatting:** unsupported reader features include read-eval, legacy metadata,
  aliased namespaced maps, and Clojure array-class symbols.
- **Checking:** some clj-kondo linters, full type checking, Clojure hooks, complete
  analysis-output coverage, and progress reporting are missing. Dynamic macros
  and Python attributes cannot always be resolved.
- **LSP:** inlay hints, structural refactorings, custom linters, and most
  clojure-lsp extensions are not implemented. Code actions currently cover safe
  fixes for unused bindings. Ancestor configuration files are read from their
  actual directories, which differs from the clojure-lsp version used in our comparisons.
- **Configuration:** settings are read as data. Reader evaluation, executable
  Leiningen profiles, and arbitrary tagged readers are unsupported. Regexes use
  Python syntax, so Java-specific patterns may differ.
- **Language:** JVM and ClojureScript behavior, classpath discovery, and
  dependency-exported LSP settings are outside the current scope.

See [Formatting](formatting.md), [Checking](checking.md), and [Language server](lsp.md)
for supported options.

## Development

CI runs on macOS and Linux, checks blt's own source, and tests the installed wheel.
Formatter comparisons include Basilisp's source corpus. Python integration tests
exercise NumPy, Requests with type stubs, and Pydantic.
The [CI workflow](../.github/workflows/ci.yml) and [Nix lockfile](../flake.lock)
record the exact tool versions used for comparisons.

```sh
nix develop --command uv run --locked basilisp test -p tests --include-unsafe-path=false
nix develop --command uv run --locked blt format --check src tests
```

To run comparisons, use the reference versions from the workflow and enter
`nix develop .#compatibility`:

```sh
uv run --locked python scripts/check_cljfmt.py --cljfmt /path/to/cljfmt
uv run --locked python scripts/check_config.py --cljfmt /path/to/cljfmt
uv run --locked python scripts/check_kondo.py
uv run --locked python scripts/check_kondo_config.py
uv run --locked python scripts/check_kondo_output.py
uv run --locked python scripts/check_lsp_config.py --clojure-lsp /path/to/clojure-lsp
```
