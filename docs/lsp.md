# Language server

Start the stdio server with:

```sh
blt lsp
blt lsp --python .venv/bin/python
blt lsp --settings '{:source-paths ["src" "tests"]}'
```

Configure your editor's LSP client to launch `["blt", "lsp"]` for `.lpy` files,
using your project directory as the workspace root. The server writes only
LSP messages to stdout; logs go to stderr.

## Features

- Diagnostics from the same analyzer as `blt check`.
- Completion for lexical bindings, project definitions, Basilisp core, and
  inspected Python modules and object members.
- Hover documentation and signatures, definition navigation, and references.
- Rename for resolved Basilisp bindings, with conflict checks and versioned edits
  when supported by the client.
- Document and workspace symbols.
- Document and range formatting using the same formatter as `blt format`.
  Range formatting selects complete top-level forms.

The server indexes workspace `.lpy` files without evaluating them. Open buffers
replace disk content in analysis. Diagnostics are debounced and stale analysis
results are discarded. Closing a buffer restores its disk content.

## Configuration

Settings use clojure-lsp's `.lsp/config.edn` format. In ascending precedence:

1. The client's `initializationOptions`.
2. Global configuration: `$XDG_CONFIG_HOME/clojure-lsp/config.edn` if that
   directory exists, otherwise `~/.lsp/config.edn`. When `XDG_CONFIG_HOME` is unset,
   its default is `~/.config`.
3. Ancestor and project `.lsp/config.edn` files, with nearer files taking precedence.
4. `blt lsp --settings EDN`.

File settings recursively merge maps, union sets, and concatenate sequential
values. Startup overlays recursively merge maps and replace other values.
Settings support literal data and `#re` regex tags. The first form is read;
empty files and `nil` contribute no settings. Configuration is never evaluated.
`#include` is not supported in LSP settings.

For example:

```clojure
{:source-paths ["src" "tests"]
 :text-document-sync-kind :incremental
 :document-formatting? true
 :document-range-formatting? true
 :cljfmt {:remove-trailing-whitespace? true}
 :diagnostics {:range-type :full}
 :linters {:clj-kondo {:level :on}}
 :hover {:arity-on-same-line? true
         :hide-file-location? false}}
```

The default source paths are `src` and `test`. `:source-paths-ignore-regex`
matches relative paths (default `["target.*"]`); `:paths-ignore-regex` matches
absolute paths. Patterns use full-match semantics. Settings changes reload
workspace indexing when source paths change.

The default synchronization kind is `:full`; `:incremental` enables incremental
changes. Positions use the encoding negotiated with the client, UTF-16 by default.
Synchronization and formatting capabilities are negotiated at startup.

Diagnostics load the nearest `.clj-kondo/config.edn`. Supported LSP controls are
`:linters {:clj-kondo {:level :on|:off :ns-exclude-regex "..."
:report-duplicates false}}`, `:diagnostics {:range-type :full|:simple}`,
and `:notify-references-on-file-change`.

Formatting uses inline `:cljfmt` settings, recursively overridden by the file
at `:cljfmt-config-path` when it exists. This path defaults to `.cljfmt.edn`
relative to the workspace root; an explicit path can select an EDN or literal
Clojure `.clj` file. This follows clojure-lsp rather than the formatter CLI's
ancestor and Leiningen discovery. `:document-formatting?` and
`:document-range-formatting?` control their respective capabilities.

Python inspection uses the workspace's `.venv` when present, otherwise the
interpreter running blt. `--python`, `--python-timeout`, and
`--no-python-inspection` have the same meaning as in [Checking](checking.md),
including its inspection limitations.

## Compatibility and limits

This implements a tested subset of [clojure-lsp](https://clojure-lsp.io/settings/)
configuration and standard LSP features. It does not implement JVM classpath or
Leiningen project discovery, exported dependency configurations, custom linters,
semantic tokens, code actions, code lenses, inlay hints, structural refactorings,
call hierarchy, or clojure-lsp's custom extensions. Settings for those features
do not make them available. Only implemented capabilities are advertised.

Ancestor configuration discovery intentionally visits actual ancestor directories;
the pinned upstream implementation repeats the root configuration instead.
See [Compatibility](compatibility.md) for the comparison scope and pinned versions.

Rename does not modify Python dependencies or installed Basilisp libraries.
It rejects vars referred through `:rename`, map-destructured bindings, record and
type names, generated constructors, and protocol methods when changing a token
alone could change the program's meaning. Unknown macros and dynamic Python
behavior have the same analysis limits as `blt check`.

Integration tests exchange framed JSON-RPC messages with a real server process,
covering lifecycle, synchronization, Unicode edits, settings, diagnostics,
navigation, rename, completion, formatting, and workspace updates.
