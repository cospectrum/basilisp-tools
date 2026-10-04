# Language server

Configure your editor to launch `blt lsp` for `.lpy` and `.cljc` files, with the
project directory as its workspace root.

```sh
blt lsp
blt lsp --python .venv/bin/python
blt lsp --settings '{:source-paths ["src" "tests"]}'
```

The server uses stdio. Logs go to stderr, or to `--log-path FILE`.
`--trace-level messages` and `--trace-level verbose` enable protocol logging.

## Editor features

- Diagnostics and formatting shared with `blt check` and `blt format`.
- Completion, documentation, signatures, definitions, references, and type navigation
  for Basilisp and inspected Python objects.
- Rename, document/workspace symbols, highlights, and call hierarchy.
- Semantic highlighting, reference lenses, inlay hints, folding, and selection ranges.
- Namespace cleanup, imports, extraction/inlining, threading, collection transforms,
  and structural editing.
- Workspace file operations that update namespaces and their consumers.
- Dependency source views, test creation/navigation, and project/test trees.

Basilisp analysis includes unsaved changes and never evaluates source forms.
Refactorings use the editor's workspace-edit mechanism. They can decline an
ambiguous change; they do not rename definitions in installed Python packages.
Range formatting expands a selection to complete top-level forms.

## Project commands

The same workspace operations are available without an editor:

```sh
blt lsp diagnostics --project-root .
blt lsp clean-ns --dry
blt lsp format --filenames src/example.lpy
blt lsp references --from example.core/run
blt lsp rename --from example.core/run --to example.core/start --dry
blt lsp dump --output '{:format :json :filter-keys [:analysis]}'
```

Use `--dry` to preview edits. Namespace renames also move the source file.
`--namespace NS`, `--filenames PATHS`, and `--ns-exclude-regex REGEX` select
files for diagnostics, formatting, cleanup, and dumps; namespace selection takes
precedence over filenames. Rename searches the project, and references includes
required Basilisp dependencies by default. Use `--analysis '{:type :project-only}'`
to limit analysis, or see `--help` for namespace-only and dependency modes.

Dry formatting and cleanup return **1** when changes are needed. Diagnostics
return **3** for errors, **2** for warnings, or **0** otherwise.
`--output` accepts EDN options for JSON/EDN output; `--raw` hides progress text.
Run `blt lsp --help` for all flags.

## Configuration

Put settings in `.lsp/config.edn`:

```clojure
{:source-paths ["src" "tests"]
 :text-document-sync-kind :incremental
 :hover {:hide-file-location? true}}
```

Precedence, from lowest to highest:

1. Client `initializationOptions`.
2. Dependency settings selected by `:classpath-config-paths`.
3. Global configuration: `$XDG_CONFIG_HOME/clojure-lsp/config.edn`, or
   `~/.lsp/config.edn` when that directory does not exist.
4. Ancestor and project `.lsp/config.edn` files, nearest last.
5. `blt lsp --settings EDN`.

`XDG_CONFIG_HOME` defaults to `~/.config`. Dependency settings come from
`clojure-lsp.exports/vendor/library/config.edn` on the selected Python search
paths, including archives; use `:classpath-config-paths ["vendor/library"]`
to select them.

Config files merge maps recursively, combine sets, and concatenate sequences.
Startup overrides merge maps and replace other values. Files contain literal
data; `#re` supports Java-style regexes. Only the first form is read. Empty or
`nil` configs are ignored; configuration code is not evaluated.

`:cache-path` selects the persistent analysis cache (default `.lsp/.cache`).
Changed sources, settings, and Python environments invalidate cached results;
set it to `false` to disable persistence. Corrupt cache entries are ignored.

`:log-path` selects a log file; the CLI flag takes precedence. OpenTelemetry log
export is disabled by default. Enable it with `:otlp {:enable true :config {...}}`,
using standard `otel.*` properties for the endpoint, headers, TLS, compression,
and batching. Supported log protocols are `grpc`, `http/protobuf`, and `http/json`.

### Workspace and diagnostics

Default source paths are `src` and `test`. `:source-paths-ignore-regex` filters
relative paths (default `["target.*"]`); `:paths-ignore-regex` filters absolute
paths. Patterns match the entire path. Changing source paths reloads the index.
`:auto-add-ns-to-new-files?` controls namespace insertion in new empty files.

Diagnostics use the nearest `.clj-kondo/config.edn`; `:kondo-config-dir` selects
another directory.
`:linters {:clj-kondo {:level :off}}` disables checker diagnostics; the same map
accepts `:ns-exclude-regex` and `:report-duplicates`.
`:diagnostics {:range-type :simple}` selects shorter ranges.
`:notify-references-on-file-change` controls diagnostics for consumers.

Project-wide linters include `:clojure-lsp/unused-public-var`,
`:clojure-lsp/different-aliases`, and `:clojure-lsp/cyclic-dependencies`.
Configure their levels and exclusions under `:linters`. Architectural dependency
rules use `:clj-depend` settings or `.clj-depend/config.edn`.

Custom linters configured under `:linters :custom` use
`clojure-lsp.custom-linters-api` queries and finding registration. They run
through the same restricted interpreter as checker hooks.

### Formatting and presentation

Inline `:cljfmt` options are overridden by `:cljfmt-config-path`
(default `.cljfmt.edn`, relative to the workspace root) when that file exists.
An explicit path may select EDN or literal Clojure configuration.
`:document-formatting?` and `:document-range-formatting?` control the two
formatting capabilities. Namespace cleanup accepts layout and sorting settings
under `:clean`, including `:ns-inner-blocks-indentation` and `:sort`.

Editor formatting also reads macro `:style/indent` metadata. For batch formatting,
use `blt lsp format --analysis '{:type :project-only}'` to include that metadata;
the default batch mode analyzes namespace declarations only.

Synchronization defaults to `:full`; `:incremental` sends changed ranges.
Restart after changing synchronization or advertised capabilities such as
`:semantic-tokens?`. Hover options include `:hide-file-location?` and
`:arity-on-same-line?`. Reference lenses can separate tests with
`:code-lens {:segregate-test-references true}` and `:test-locations-regex`.

### Python environments

Each workspace prefers its own `.venv`. Configure another interpreter or
additional source paths with:

```clojure
{:python {:executable ".venv/bin/python"
          :paths ["src"]}}
```

`--python` takes precedence. `--no-python-inspection` or
`:python {:inspection? false}` disables runtime inspection while keeping
static source/stub support. Runtime inspection executes package import code;
see [Checking](checking.md#python-support).

Python completion, hover, signatures, navigation, and checking share the same
type information. Workspace caches are invalidated by settings and environment
changes reported by the client. Restart after changing an environment if the
client does not report those changes.

See [Compatibility](compatibility.md) for the tested scope and remaining differences.
