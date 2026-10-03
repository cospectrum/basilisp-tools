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

The test suite includes independent examples checked against cljfmt, covering
default formatting, custom rules, whitespace options, alignment, namespace
references, and idempotence. A JVM or Clojure installation is not required to
run `blt`.

The formatter targets Basilisp source. It preserves spelling of numbers,
symbols, reader tags, comments, and literal text, including multiline strings
and f-string interpolation text. Reader conditionals retain all branches.
Whitespace line endings become LF; line endings inside literals are preserved.

Namespace sorting is opt-in. Clauses with comments or reader discards retain
their order so annotations stay attached to the original references. Executable
`.clj` configuration, Leiningen configuration discovery, and cljfmt's Clojure
CLI entry points are not supported. Use `blt format` and EDN configuration.

There is no automatic line-length wrapping. The parser has a nesting limit of
64; input exceeding it is rejected. Syntax diagnostics prevent formatting, but
formatting is not a substitute for compiler or semantic validation.
