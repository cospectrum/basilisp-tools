# Formatting

`blt format` formats files in place. Directories are searched recursively for
`.lpy` files; use `-` to read from stdin and write to stdout.

```sh
blt format src tests
blt format --check .  # Report files that need formatting
blt format --diff .   # Show changes without writing
blt format - < example.lpy
```

Exit codes are **0** for success, **1** when `--check` or `--diff` finds changes,
and **2** for errors.

## Configuration

Put settings in `.cljfmt.edn` at the project root:

```clojure
{:normalize-newlines-at-file-end? true
 :remove-multiple-non-indenting-spaces? true}
```

The CLI searches the working directory and its parents, using the nearest
configuration. Files in different directories are not merged. Within a directory,
the order is `.cljfmt.edn`, `.cljfmt.clj`, `cljfmt.edn`, `cljfmt.clj`.

Discovering `.clj` files requires `--read-clj-config-files`; otherwise they are
skipped with a warning. `--config FILE` selects a file directly, including
`.clj` files. EDN regexes use `#re "pattern"`; Clojure files use `#"pattern"`.
Only the first form is read. Configuration is never evaluated.

For existing projects, literal `:cljfmt` settings in `project.clj` are also
supported. During discovery, the nearest `project.clj` takes precedence over
formatter files; `:load-config-file? true` loads EDN settings before applying
project overrides with a shallow merge. Configured project paths are relative
to the project root. Literal profiles from the project, `profiles.clj`,
user `$LEIN_HOME/profiles.d`, and user/system profiles are merged with Leiningen
metadata rules.
Use `--lein-profile release` to select profiles explicitly; repeat the flag to
combine them, including `default` when desired. Executable profile expressions
are never run. Use `--config .cljfmt.edn` to bypass this compatibility behavior.

## Indentation

`:extra-indents` adds rules to the defaults; `:indents` replaces them.
Argument indexes start at zero after the function or macro name.

| Rule | Behavior |
| --- | --- |
| `[:default]` | Ordinary list indentation |
| `[:inner depth]` | Body indentation at the given nesting depth |
| `[:inner depth argument-index]` | Body indentation within one argument |
| `[:block argument-count]` | Body indentation after the leading arguments |

```clojure
{:extra-indents
 {with-session [[:block 1]]
  my.app/widget [[:inner 0]]
  #re "^defcomponent" [[:inner 0]]
  [my.app #re "^with-"] [[:block 1]]}}
```

Rule keys can be symbols, regexes, or `[namespace-pattern name-pattern]` pairs.
Regexes support Java-style quoting, named groups, Unicode properties, and
character-class intersections, and match the symbol's name. The formatter reads
namespace names, aliases, and refers from the `ns` form; `:alias-map` and
`:refer-map` override those values.

## Options

| Option | Default |
| --- | --- |
| `:indentation?` | `true` |
| `:function-arguments-indentation` | `:community`; also `:cursive`, `:zprint` |
| `:indent-line-comments?` | `false`; indents `;;` comments when enabled |
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

`:aligned-forms` and `:blank-line-forms` map form names to argument-index sets
or `:all`. Their `:extra-aligned-forms` and `:extra-blank-line-forms` counterparts
extend the defaults. Binding vectors, such as those in `let`, are alignment
targets by default and can retain blank lines.

The old `:align-binding-columns?` option is accepted but has no effect; use
`:align-form-columns?` instead. `:legacy/merge-indents? true` treats `:indents`
as additions to the defaults. Unknown keys are ignored for compatibility with
shared configurations. Invalid formatting rules still produce errors.

### Command-line overrides

Flags override file settings. For example, `--indentation` enables indentation
and `--no-indentation` disables it. Styles, alignment limits, and file patterns
also have CLI options; see `blt format --help`.

Use `:paths ["src" "tests"]` to set default input paths; explicit CLI paths win.
A `:file-pattern #re "\\.lpy$"` matches paths relative to each searched directory.
`--project-root` changes displayed paths without relocating inputs.

`--parallel` prepares files concurrently and keeps output ordered. `--quiet`
hides file status messages, and `--verbose` lists processed files. These also
accept `:parallel?`, `:quiet?`, and `:verbose?` in configuration.
`:ansi?` controls colored diffs, which require a terminal and respect `NO_COLOR`.

## Behavior and limits

The formatter preserves token spelling, comments, literal contents, and all
reader-conditional branches. Whitespace line endings become LF; line endings
inside literals stay unchanged. It checks every result for syntax preservation
and prepares the whole selection before writing any files.

Namespace sorting and column alignment are optional. Sorting keeps attached and
dangling comments. Formatting does not wrap lines to a maximum length. Inputs
nested more than 128 levels deep are rejected.

The supported options follow cljfmt. The formatter also preserves Clojure
reader forms such as read-eval text and legacy metadata without executing them.
See [Compatibility](compatibility.md) for details and development checks.

## Library API

`basilisp-tools.format/format-string` takes source and an optional options map.
It returns formatted source or raises `ExceptionInfo` for malformed syntax or
invalid options. `load-config`, in the same namespace, accepts a file or directory
and defaults to the working directory. Neither function evaluates project code.
