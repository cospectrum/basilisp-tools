# Language server

Configure your editor's LSP client to run `blt lsp` for `.lpy` files, using your
project directory as the workspace root.

```sh
blt lsp
blt lsp --python .venv/bin/python
blt lsp --settings '{:source-paths ["src" "tests"]}'
```

The server communicates over stdio. Logs go to stderr.

## Features

- Diagnostics from the same analyzer as `blt check`.
- Formatting using the same formatter as `blt format`.
- Completion for local bindings, project definitions, Basilisp core, and Python members.
- Hover documentation, definitions, references, and signature help, including Python overloads.
- Rename for resolved Basilisp bindings, with conflict checks.
- Semantic highlighting, reference lenses, and incoming/outgoing Basilisp calls.
- Quick fixes for unused bindings when a safe rename is possible.
- Document and workspace symbols.

Analysis includes unsaved changes and never evaluates Basilisp code. Range
formatting expands the selection to complete top-level forms.

## Configuration

Put settings in `.lsp/config.edn`. For example, to include a `tests` directory,
use incremental document updates, and hide file locations in hover information:

```clojure
{:source-paths ["src" "tests"]
 :text-document-sync-kind :incremental
 :hover {:hide-file-location? true}}
```

Settings load in this order, with later values taking precedence:

1. The client's `initializationOptions`.
2. Global configuration: `$XDG_CONFIG_HOME/clojure-lsp/config.edn` if that
   directory exists, otherwise `~/.lsp/config.edn`. When unset, `XDG_CONFIG_HOME`
   defaults to `~/.config`.
3. Ancestor and project `.lsp/config.edn` files, with nearer files taking precedence.
4. `blt lsp --settings EDN`.

Config files merge maps recursively, combine sets, and concatenate sequences.
At startup, client/file/CLI overrides merge maps but replace other values.
Configs are literal data with `#re` regex tags; only the first form is read.
Empty or `nil` configs are ignored. Code evaluation and `#include` are unsupported.

### Workspace and diagnostics

The default source paths are `src` and `test`. `:source-paths-ignore-regex` filters
relative paths (default `["target.*"]`); `:paths-ignore-regex` filters absolute
paths. Patterns must match the entire path. Changing source paths reloads the
workspace index.

Diagnostics use the nearest `.clj-kondo/config.edn`. In LSP settings,
`:linters {:clj-kondo {:level :off}}` disables them; the same map accepts
`:ns-exclude-regex` and `:report-duplicates`. `:diagnostics {:range-type :simple}`
uses shorter ranges than `:full`. `:notify-references-on-file-change` controls
whether changes trigger diagnostics for referencing files.

### Formatting and editor behavior

Use `:cljfmt` for inline formatter options. Options from `:cljfmt-config-path`
(default `.cljfmt.edn`, relative to the workspace root) override them recursively
if the file exists. An explicit path may select an EDN or literal Clojure `.clj`
file. Unlike the CLI, the server does not search ancestors for formatter config.
`:document-formatting?` and `:document-range-formatting?` enable or disable the
respective editor features.

Document synchronization defaults to `:full`; `:incremental` sends changed ranges.
Restart the server after changing synchronization or formatting capabilities.
Hover settings also include `:arity-on-same-line?`. Semantic highlighting defaults
to enabled; set `:semantic-tokens? false` and restart to disable it. Reference
lenses use the `code-lens-references` command, which returns LSP locations; the
editor needs a handler to display them. `:code-lens {:segregate-test-references true}`
separates test references using `:test-locations-regex`.

### Python

Each workspace uses its own `.venv` when present, otherwise the interpreter
running blt. Select a different environment in `.lsp/config.edn`:

```clojure
{:python {:executable ".venv/bin/python"
          :paths ["src"]}}
```

An explicit `--python` takes precedence. `--python-timeout` and
`--no-python-inspection` work as described in [Checking](checking.md#python-inspection).
Use `:python {:inspection? false}` to disable inspection for a workspace. Runtime
inspection executes package import code; source and stub inspection do not.

Completion, hover, and signature help follow inspected Python members and inferred
return types. Workspace caches are cleared on settings or environment-file changes
reported by the client. Restart the server after changing an environment if your
client does not send file-watch notifications.

## Limitations

The server supports a subset of clojure-lsp settings and features. See
[Compatibility](compatibility.md) for the supported scope and remaining gaps.

Rename does not modify Python dependencies or installed Basilisp libraries. It
rejects vars referred through `:rename`, map-destructured bindings, record and
type names, generated constructors, and protocol methods when a token replacement
could change the program's meaning. Unknown macros and dynamic Python behavior
have the same analysis limits as `blt check`.
