# basilisp-tools

Development tools for [Basilisp](https://github.com/basilisp-lang/basilisp), a
Clojure-compatible Lisp on Python. `blt` provides formatting, static analysis,
and a language server with Python inspection.

## Installation

Requires Python 3.10+.

```sh
uv tool install git+https://github.com/cospectrum/basilisp-tools.git
```

## Usage

```sh
blt format .          # Format .lpy files in place
blt format --check .  # Check without writing
blt check .           # Check code and Python interop
blt lsp               # Start the stdio language server
```

[Formatting](docs/formatting.md) · [Checking](docs/checking.md) · [Language server](docs/lsp.md) · [Compatibility](docs/compatibility.md) · [Syntax API](docs/syntax.md)

Licensed under [Apache 2.0](LICENSE).
