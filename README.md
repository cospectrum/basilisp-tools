# basilisp-tools

Development tooling for Basilisp, bootstrapped with the official
`org.basilisp/basilisp` Leiningen template.

The lossless syntax parser is implemented in Basilisp; see [the syntax API](docs/syntax.md).
The language server, analyzer, linter, and formatter are not implemented yet.
The CLI is named `blt` and currently prints a greeting.

## Development

```sh
uv sync
uv run blt
uv run basilisp repl
uv run basilisp test -p tests --include-unsafe-path=false
uv build
```

## License

MIT. See LICENSE.
