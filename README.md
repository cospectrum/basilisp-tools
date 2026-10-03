# basilisp-tools

Development tools for [Basilisp](https://github.com/basilisp-lang/basilisp), a
Clojure-compatible Lisp on Python. Currently provides the `blt` formatter with
[cljfmt](https://github.com/weavejester/cljfmt)-compatible configuration.

## Installation

Requires Python 3.10+.

```sh
uv tool install git+https://github.com/cospectrum/basilisp-tools.git
```

## Usage

```sh
blt format .          # Format .lpy files in place
blt format --check .  # Check without writing
blt --help
blt format --help
```

[Formatting reference](docs/formatting.md) · [Syntax API](docs/syntax.md)

Licensed under [Apache 2.0](LICENSE).
