# Compatibility

blt follows cljfmt, clj-kondo, and clojure-lsp conventions while analyzing
Basilisp and Python. Compatibility is checked against pinned upstream versions;
it is not a claim that the Python and JVM language environments are identical.

| Command | Upstream tool | Compared in CI |
| --- | --- | --- |
| `blt format` | [cljfmt](https://github.com/weavejester/cljfmt) | Whole-file output, configuration precedence, and Leiningen profiles |
| `blt check` | [clj-kondo](https://github.com/clj-kondo/clj-kondo) | Diagnostics and ranges, configuration, analysis output, hooks, exit codes, and an upstream test-input audit |
| `blt lsp` | [clojure-lsp](https://clojure-lsp.io/) | Configuration, shared refactoring forms, and an upstream test-assertion audit |

Configuration regexes are also compared with Java Pattern. LSP tests exercise
the actual protocol, including unsaved edits, workspace isolation, navigation,
Python support, and refactoring behavior. Separate execution tests check that
supported refactorings preserve values and evaluation order.

The [upstream test audit](upstream-tests.md) also replays cases taken directly
from the tools' test suites. Each selected case must match upstream exactly or
match a reviewed Basilisp expectation with an explicit reason and evidence.
New differences and changed native outcomes fail CI. Existing strict
compatibility comparisons continue to fail on mismatches.

## Differences that matter

- **Language semantics:** Basilisp reader branches, Python objects, Python
  formatting, and Basilisp's core library govern checking. JVM/ClojureScript
  classes, Java classpaths, and their platform-specific tooling are not emulated.
  Valid native differences, such as variadic protocols, are reported separately
  from exact upstream matches.
- **Static knowledge:** unknown macros, dynamic Python attributes, and missing
  annotations can remain unresolved. Annotations and overloads improve inference;
  they do not make Python fully statically typed.
- **Configuration:** literal Leiningen profiles and merge metadata are supported.
  JVM plugins, reader evaluation, arbitrary tagged readers, and executable project
  expressions are not run.
- **Hooks:** supported hooks and custom linters use a restricted interpreter.
  Supported pure core functions and tooling APIs are available; arbitrary host functions and file
  access are rejected. Ordinary project macros are never executed for analysis.
- **Editing:** refactorings may decline changes whose bindings or evaluation
  behavior cannot be preserved. Python library definitions are not renamed.
- **Discovery:** blt reads ancestor LSP configuration from each actual directory.
  The pinned clojure-lsp version repeats the root configuration instead.

Formatting `.lpy`, `.cljc`, stdin, and LSP documents follows Basilisp token
rules. Explicit `.clj` inputs, configuration, and the existing formatter library
arities use Clojure rules, including read-eval text, without executing it. Native
reader differences have separate preservation tests; the Clojure mode matches
the upstream fixtures. The syntax parser defaults to Basilisp validation.

See [Formatting](formatting.md), [Checking](checking.md), and
[Language server](lsp.md) for usage and supported settings.

## Development

CI runs on macOS and Linux, checks blt's own source, and tests the installed wheel.
Formatter tests include Basilisp's source corpus. The
[public-project audit](public-project-audit.md) selects 968 source files from 44
repositories. CI exercises pinned nREPL and Flask projects and runs calc's own
tests before and after formatting, alongside executable generated projects.
Python integration tests use NumPy, Requests with type stubs, and Pydantic.
The [Python typing audit](python-typing-compatibility.md) adapts pinned fixtures
from typing, Pyright, mypy, Pyrefly, and ty. The
[ML audit](python-ml-audit.md) exercises real Torch and ONNX programs, upstream
Torch expressions, and generated training and inference projects. CI checks a
fully resolved typing subset and the reviewed ML coverage limits on every push.
The [workflow](../.github/workflows/ci.yml) and [Nix lockfile](../flake.lock) record
the exact reference versions.

```sh
nix develop --command uv run --locked basilisp test -p tests --include-unsafe-path=false
nix develop --command uv run --locked blt format --check src tests
nix develop --command uv run --locked blt check --repro --no-python-inspection src
```

For live comparisons, enter `nix develop .#compatibility` and use the reference
checkouts from the workflow:

```sh
uv run --locked python scripts/check_cljfmt.py --cljfmt /path/to/cljfmt
uv run --locked python scripts/check_config.py --cljfmt /path/to/cljfmt
uv run --locked python scripts/check_lein_profiles.py
uv run --locked python scripts/check_regex.py
uv run --locked python scripts/check_kondo.py
uv run --locked python scripts/check_kondo_config.py
uv run --locked python scripts/check_kondo_output.py
uv run --locked python scripts/check_hooks.py
uv run --locked python scripts/check_lsp_config.py --clojure-lsp /path/to/clojure-lsp
uv run --locked python scripts/check_lsp_refactors.py --clojure-lsp /path/to/clojure-lsp
```
