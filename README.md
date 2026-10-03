# basilisp-tools

[![CI](https://github.com/cospectrum/basilisp-tools/actions/workflows/ci.yml/badge.svg)](https://github.com/cospectrum/basilisp-tools/actions/workflows/ci.yml)

Development tools for [Basilisp](https://github.com/basilisp-lang/basilisp), a
Clojure-compatible Lisp that runs on Python.

The `blt` CLI currently provides a source formatter, written in Basilisp. It follows
[cljfmt](https://github.com/weavejester/cljfmt) formatting conventions and accepts
cljfmt-style EDN configuration and custom indentation rules. Formatting reads
syntax without evaluating your code or importing your project's dependencies.

## Installation

Requires Python 3.10 or later. Install the current formatter from GitHub with
[uv](https://docs.astral.sh/uv/):

```sh
uv tool install git+https://github.com/cospectrum/basilisp-tools.git
blt --version
```

Or install into an activated Python virtual environment:

```sh
python -m pip install git+https://github.com/cospectrum/basilisp-tools.git
```

The package is named **basilisp-tools**; the command is **blt**. The existing
PyPI 0.0.1 release predates the formatter, so use the GitHub installation above.

## Usage

Format a file, directories, or the current project in place:

```sh
blt format src/example/core.lpy
blt format src tests
blt format .
```

Check formatting in CI, or preview changes without writing files:

```sh
blt format --check src tests
blt format --diff src tests
```

Read from stdin and write formatted source to stdout:

```sh
blt format - < src/example/core.lpy
```

With no paths, `blt format` uses the configured `:paths`, or the current directory.
Directories are searched recursively for `.lpy` files. Virtual environments,
version-control directories, caches, `node_modules`, `build`, and `dist` are
skipped. Symbolic links are not followed. Explicit files are formatted regardless
of their extension.

Invalid syntax or configuration produces an error. All selected files are
formatted and validated before any writes; files that do not need changes are
left untouched. Status messages go to stderr. Use `--quiet` to suppress them.

| Exit status | Meaning |
| --- | --- |
| `0` | Formatting succeeded, or the check found no changes |
| `1` | `--check` or `--diff` found changes |
| `2` | Invalid arguments, syntax, configuration, or an I/O error |

## Configuration

Create `.cljfmt.edn` in your project root:

```clojure
{:remove-multiple-non-indenting-spaces? true
 :normalize-newlines-at-file-end? true
 :extra-indents {with-session [[:block 1]]
                 #re "^defwidget" [[:inner 0]]}}
```

Configuration is discovered from the current working directory upward. At the
nearest matching directory, `.cljfmt.edn` takes precedence over `cljfmt.edn`.
Use an explicit file to override discovery:

```sh
blt format --config config/format.edn src tests
```

`:extra-indents` extends the default rules; `:indents` replaces them. EDN symbol
keys are unquoted, and regular expressions use `#re "pattern"`. Configuration is
data only; executable `.clj` configuration is not loaded.

The formatter adjusts whitespace and indentation without imposing a line length.
Strings, byte strings, regex literals, and f-strings retain their literal text.
See the [formatting reference](docs/formatting.md) for options and compatibility
details.

## Library API

```clojure
(require '[basilisp-tools.format :as fmt])

(fmt/format-string "(let [x 1]\nx)")
;; => "(let [x 1]\n  x)"

(fmt/format-string source (fmt/load-config))
```

`format-string` is a pure function and does not discover configuration implicitly.
`reformat-string` is an alias. The [lossless syntax API](docs/syntax.md) is also
available for working with source trees.

## Development

```sh
git clone https://github.com/cospectrum/basilisp-tools.git
cd basilisp-tools
uv sync --locked
uv run basilisp test -p tests --include-unsafe-path=false
uv run blt format --check src tests
uv build
```

With Nix installed, the pinned development shell provides `uv` and `actionlint`:

```sh
nix develop --command uv run --locked basilisp test -p tests --include-unsafe-path=false
nix develop --command uv run --locked blt format --check src tests
nix develop --command actionlint
```

GitHub Actions runs tests on Linux and macOS with Python 3.10 and 3.13, checks
formatting and workflow syntax, builds distributions, and tests the installed
wheel.

## License

[MIT](LICENSE).
