# Formatting reference

`basilisp-tools.format/format-string` accepts a source string and an optional map
of options. It returns formatted source or raises `ExceptionInfo` for malformed
syntax or invalid options. It never evaluates source, expands macros, resolves
runtime variables, or imports dependencies from the formatted project.

Configuration loading lives in the same namespace. `load-config` takes a file
or a directory; without an argument it starts at the current working directory.
An explicit CLI `--config` selects an existing file and bypasses discovery.
Otherwise the nearest ancestor `project.clj` selects Leiningen configuration;
without a project, configuration is discovered in the nearest ancestor directory.
The order is `.cljfmt.edn`, `.cljfmt.clj`, `cljfmt.edn`, `cljfmt.clj`.
Discovery of `.clj` files requires `--read-clj-config-files`; without it, they
produce a warning and EDN discovery continues. An explicit `--config FILE.clj`
works without this flag. EDN uses `#re "pattern"`; `.clj` uses `#"pattern"`.
Both readers consume the first form, as upstream does. Literal `.clj` metadata
and unqualified automatic namespaces are supported; reader evaluation is not.

## Leiningen configuration

A literal `:cljfmt` map in the top-level `defproject` form is supported:

```clojure
(defproject example "0.1.0"
  :cljfmt {:load-config-file? true
           :extra-indents {with-session [[:block 1]]
                           #"^with-" [[:inner 0]]}})
```

Precedence follows the cljfmt Leiningen plugin:

- Project options alone are used by default, even when an EDN file exists.
- `:load-config-file? true` enables EDN discovery from the project root upward.
  The nearest directory wins; `.cljfmt.edn` precedes `cljfmt.edn`. Parent files
  are not merged.
- Project options override file options with a shallow merge: a nested map is
  replaced as a whole. File validation and legacy-key conversion happen first.
- Project `:cljfmt :paths` wins over `:source-paths` and `:test-paths` (defaults
  `src` and `test`, restricted to existing directories). File-config paths do
  not override this selection. Configured project paths are relative to its
  root; explicit CLI paths remain relative to the working directory.

`project.clj` is parsed as data without running Leiningen or project code.
Symbols, regex literals, collections, and explicit namespaced maps are supported.
Unquoted computations and metadata in selected settings are rejected. Inline
profiles affecting formatter settings are rejected; external Leiningen profiles
are not loaded. Use top-level literal settings or an explicit EDN file.
The static configuration scope does not reproduce Leiningen project evaluation.

## Indentation

These rules use cljfmt's argument indexes and nesting depths:

| Rule | Behavior |
| --- | --- |
| `[:default]` | Ordinary list indentation |
| `[:inner depth]` | Body indentation at the specified nesting depth |
| `[:inner depth argument-index]` | Body indentation within one argument |
| `[:block argument-count]` | Indent the body after the leading arguments |

Argument indexes start at zero after the head symbol. `:extra-indents` extends
the defaults; `:indents` replaces them. Keys can be unqualified symbols, qualified
symbols, regexes, or `[namespace-pattern name-pattern]` vectors.

```clojure
{:extra-indents
 {with-session [[:block 1]]
  my.app/widget [[:inner 0]]
  #re "^defcomponent" [[:inner 0]]
  [my.app #re "^with-"] [[:block 1]]}

 :alias-map {app my.app}
 :refer-map {widget my.app}}
```

Namespace names, aliases, and explicit refers are inferred from the `ns` form.
Explicit `:alias-map` and `:refer-map` entries override inferred entries.
Regexes use Python's regular-expression syntax and match a symbol's name.

## Options

| Option | Default |
| --- | --- |
| `:indentation?` | `true` |
| `:function-arguments-indentation` | `:community`; also `:cursive`, `:zprint` |
| `:indent-line-comments?` | `false`; when enabled, indents `;;` comments |
| `:insert-missing-whitespace?` | `true` |
| `:remove-surrounding-whitespace?` | `true` |
| `:remove-trailing-whitespace?` | `true` |
| `:remove-consecutive-blank-lines?` | `true` |
| `:remove-multiple-non-indenting-spaces?` | `false` |
| `:remove-blank-lines-in-forms?` | `false` |
| `:normalize-newlines-at-file-end?` | `false` |
| `:split-keypairs-over-multiple-lines?` | `false` |
| `:sort-ns-references?` | `false` |
| `:align-map-columns?` | `false` |
| `:align-form-columns?` | `false` |
| `:align-single-column-lines?` | `false` |
| `:blank-lines-separate-alignment?` | `false` |
| `:max-column-alignment-gap` | `nil` |
| `:max-column-alignment-width` | `nil` |

`:aligned-forms` and `:blank-line-forms` map form names to sets of argument indexes
or `:all`. The corresponding `:extra-aligned-forms` and
`:extra-blank-line-forms` maps extend the defaults. By default, binding vectors
such as those in `let`, `binding`, and `with-open` are alignment targets and may
retain blank lines. `cond` and `comment` may also retain blank lines.

`:align-binding-columns?` is accepted as an alias for enabling form alignment.
Legacy `:legacy/merge-indents? true` treats `:indents` as additions to defaults.

CLI configuration also accepts `:paths ["src" "tests"]` and a `:file-pattern #re
"\\.lpy$"`. Explicit CLI paths take precedence over `:paths`.
Unknown options and malformed rules produce errors.

Formatter switches can be overridden with `--OPTION` / `--no-OPTION`, such as
`--indentation` or `--no-remove-consecutive-blank-lines`. Explicit flags override
file settings, including explicit `false` values. `--function-arguments-indentation`,
`--max-column-alignment-gap`, `--max-column-alignment-width`, and `--file-pattern`
also override configuration. File patterns match paths relative to each searched
directory. `--project-root` changes displayed paths; it does not relocate CLI
input paths or Leiningen's configured source directories.

`:parallel?` / `--parallel` prepares files concurrently while retaining deterministic
output and validating the complete batch before writes. `:quiet?`, `:verbose?`,
and `:ansi?` have corresponding CLI flags. ANSI colors require a terminal and
`TERM`, and respect `NO_COLOR`. Use `blt format --help` for all switches.

## Compatibility and source preservation

CI compares output with cljfmt at commit
`baab5008032945434cbca23ef5eda516e3ea97b0`, running its tests from an external
checkout and checking that a second formatting pass changes nothing. Local
tests also cover independent examples, generated preservation cases, and
fault injection. A JVM or Clojure installation is not required to run `blt`.

Compatibility covers the supported Basilisp syntax and options above, rather
than every Clojure reader feature. The comparison explicitly excludes legacy
`#^` metadata, read-eval `#=`, aliased namespaced maps, and Clojure array-class
symbols such as `String/1`. Configuration aliases requiring a running Clojure
namespace, custom readers, reader evaluation, and dynamic Leiningen project/profile
execution are not supported. Invalid or unknown formatter options are rejected
more strictly than upstream. The command remains `blt format`; see the
[compatibility matrix](compatibility.md) for tested coverage and differences.

The formatter preserves token spelling and literal text, including multiline
strings and f-string interpolation text. Reader conditionals retain all
branches. Whitespace line endings become LF; line endings inside literals
remain unchanged. Formatting columns follow cljfmt's UTF-16 convention; syntax
offsets and positions count Unicode code points.

Every result is parsed again before it is returned. The guard checks syntax,
nesting, reader attachments, and token text against the original and prepared
trees, allowing layout changes and comment trailing-space cleanup. With
namespace sorting enabled, only reference-clause child ordering may change.
The CLI prepares the entire batch before writing any file.

Namespace sorting is opt-in. Attached comments move with their references;
section and dangling comments are retained. This intentionally differs from
upstream cases that drop dangling comments. Column alignment converges to a
stable result in one invocation, including cases requiring multiple upstream
runs. A cycle or failure to converge raises an error instead of returning
unstable output.

There is no automatic line-length wrapping. The parser has a nesting limit of
64; input exceeding it is rejected. These checks do not replace compiler or
semantic validation.

## Development checks

The normal suite and formatting check need only the default development shell:

```sh
nix develop --command uv run --locked basilisp test -p tests --include-unsafe-path=false
nix develop --command uv run --locked blt format --check src tests
```

For the upstream comparison, use an unmodified cljfmt checkout at the pinned
commit above. The separate shell supplies Clojure and Java:

```sh
nix develop .#compatibility --command uv run --locked python scripts/check_cljfmt.py --cljfmt /path/to/cljfmt
nix develop .#compatibility --command uv run --locked python scripts/check_config.py --cljfmt /path/to/cljfmt
```

Add `--corpus /path/to/basilisp` to compare real `.lpy` files, or
`--report /tmp/cljfmt-report.json` to save mismatches and explicit skips.
Upstream source and generated fixtures are not vendored in this project.

Measure a fixed input corpus with warmup and repeated samples:

```sh
uv run --locked python scripts/benchmark.py src tests --repeat 3
```

Add `--profile /tmp/blt.prof` for a Python cProfile of a formatting pass.
