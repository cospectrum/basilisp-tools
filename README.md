# basilisp-tools

Development tooling for Basilisp. Initial scaffold generated with the official
`org.basilisp/basilisp` Leiningen template.

The language server, analyzer, linter, and formatter are not implemented yet.
The CLI currently prints a greeting.

## Development

```sh
uv sync
uv run basilisp-tools
uv run basilisp repl
uv run basilisp test -p tests --include-unsafe-path=false
uv build
```

## License

MIT. See LICENSE.
