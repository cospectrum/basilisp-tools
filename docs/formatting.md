# Formatting reference

`basilisp-tools.format/format-string` accepts a source string and an optional map
of options. It returns formatted source or raises `ExceptionInfo` for malformed
syntax or invalid options. It never evaluates source, expands macros, resolves
runtime variables, or imports dependencies from the formatted project.

Configuration loading lives in the same namespace. `load-config` takes a file
or a directory; without an argument it starts at the current working directory.
The nearest `.cljfmt.edn` or `cljfmt.edn` is used, without merging parent files.
An explicit CLI `--config` must refer to an existing file.

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

## Compatibility and source preservation

CI compares output with cljfmt at commit
`baab5008032945434cbca23ef5eda516e3ea97b0`, running its tests from an external
checkout and checking that a second formatting pass changes nothing. Local
tests also cover independent examples, generated preservation cases, and
fault injection. A JVM or Clojure installation is not required to run `blt`.

Compatibility covers the supported Basilisp syntax and options above, rather
than every Clojure reader feature. The comparison explicitly excludes legacy
`#^` metadata, read-eval `#=`, aliased namespaced maps, and Clojure array-class
symbols such as `String/1`. Executable `.clj` configuration, Leiningen config
discovery, namespaced-map shorthand in EDN config, and cljfmt's CLI entry points
are not supported. Use ordinary EDN maps, qualified keys, and `blt format`.

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
```

Add `--corpus /path/to/basilisp` to compare real `.lpy` files, or
`--report /tmp/cljfmt-report.json` to save mismatches and explicit skips.
Upstream source and generated fixtures are not vendored in this project.

Measure a fixed input corpus with warmup and repeated samples:

```sh
uv run --locked python scripts/benchmark.py src tests --repeat 3
```

Add `--profile /tmp/blt.prof` for a Python cProfile of a formatting pass.
