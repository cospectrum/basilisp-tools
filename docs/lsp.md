# Language server

Start the stdio server with:

```sh
blt lsp
blt lsp --python .venv/bin/python
blt lsp --no-python-inspection
```

Configure your editor's LSP client to launch `["blt", "lsp"]` for `.lpy` files,
using your project directory as the workspace root. The server writes only
LSP messages to stdout; logs go to stderr.

## Supported features

- Diagnostics from the same analyzer as `blt check`.
- Completion for lexical bindings, project definitions, Basilisp core, and
  inspected Python modules and object members.
- Hover documentation and signatures, definition navigation, and references.
- Rename for resolved Basilisp bindings, with conflict checks and versioned edits
  when supported by the client.
- Document and workspace symbols.
- Whole-document formatting using the same settings as `blt format`.

The server indexes workspace `.lpy` files without evaluating them. Open buffers
replace disk content in analysis. Incremental changes use the position encoding
negotiated with the client; UTF-16 is the default. Diagnostics are debounced and
stale analysis results are discarded. Closing a buffer restores its disk content.

## Configuration

Diagnostics load the nearest `.clj-kondo/config.edn`; formatting loads the
formatter configuration described in [Formatting](formatting.md). Python
inspection uses the workspace's `.venv` when present, otherwise the interpreter
running blt. `--python`, `--python-timeout`, and `--no-python-inspection` have the
same meaning as in [Checking](checking.md), including its inspection limitations.

## Compatibility and limits

The server implements standard LSP methods used by editors that support
[clojure-lsp](https://clojure-lsp.io/capabilities/). This does not imply identical
results or full clojure-lsp feature parity. Semantic tokens, code actions,
structural refactorings, call hierarchy, and clojure-lsp's custom extensions are
not implemented. Only supported capabilities are advertised.

Rename does not modify Python dependencies or installed Basilisp libraries.
It rejects vars referred through `:rename`, map-destructured bindings, record and
type names, generated constructors, and protocol methods when changing a token
alone could change the program's meaning.
Unknown macros and dynamic Python behavior have the same analysis limits as
`blt check`.

Integration tests exchange framed JSON-RPC messages with a real server process,
covering lifecycle, incremental Unicode edits, diagnostics, navigation, rename,
completion, formatting, and workspace updates.
