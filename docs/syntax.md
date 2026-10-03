# Syntax API

The Basilisp namespace `basilisp-tools.syntax` parses source without evaluating
forms, loading project namespaces, expanding macros, resolving aliases, or
invoking tagged-literal readers.

## Parse and inspect

```clojure
(require '[basilisp-tools.syntax :as syntax])

(def parsed (syntax/parse "(inc ; note\n  1)"))
(:root parsed)
(:diagnostics parsed)
(syntax/text (:root parsed)) ; original source, exactly
```

The result is an immutable map with `:source`, `:root`, `:diagnostics`, and
`:line-starts`. Every node has `:kind`, `:start`, and `:end`. Leaves have
`:text`; branches have `:children`. Unfinished forms have `:incomplete? true`
when their own delimiter or operand is missing.

Ranges are zero-based, half-open Unicode code-point offsets. Each character
belongs to exactly one leaf; branch children partition their parent's range.
Delimiters, prefixes, commas, whitespace, CRLF endings, comments, and invalid
input are retained. No synthetic characters are inserted.

## Forms

Supported syntax includes:

- Lists, vectors, maps, sets, and anonymous functions.
- Symbols, keywords and auto-resolved keywords, nil, and booleans.
- Integers, bigints, decimals, floats, octal/hex/arbitrary-base numbers, ratios,
  scientific notation, imaginary numbers, and symbolic numeric constants.
- Character literals and named/Unicode characters.
- Strings, regex literals, byte strings, and f-strings. Interpolations contain
  ordinary syntax nodes, accessible to later analysis.
- Quote, syntax quote, unquote, unquote-splicing, dereference, var quote,
  metadata, nested reader discards, namespaced maps, and tagged literals.
- Reader conditionals and splicing conditionals, preserving all branches.
- Line comments, shebang comments, whitespace, and commas.

Grammar is informed by Basilisp's upstream reader and reader tests. We do not
reproduce permissive historical reader behavior such as accepting a lone colon
or backslash as an empty value; these receive editor diagnostics.

## Traversal

| Function | Result |
|---|---|
| `nodes` | Lazy preorder sequence including the supplied node |
| `tokens` | Leaf tokens in order, including punctuation and trivia |
| `text` | Exact source reconstructed from leaves |
| `forms` | Direct forms, excluding trivia, punctuation, string text, and discards |
| `trivia?` | Whether a node is whitespace, a newline, or a comment |
| `node-at` | Deepest node containing an offset; nil at EOF |

Reader conditionals in `forms` remain syntax, not expanded runtime values.

## Positions

`offset->position` returns a zero-based `{:line ... :character ...}` map.
`position->offset` converts valid line positions back to offsets. Characters
count code points, not UTF-16 units. CRLF is one line break; positions inside
line endings are not valid inputs to `position->offset`. An LSP adapter must
convert to its negotiated encoding.

## Recovery

Diagnostics contain `:code`, `:message`, `:severity`, `:start`, and `:end`.
Missing tokens can use zero-width diagnostic ranges.

Mismatched delimiters recover at an enclosing delimiter when possible.
Unexpected closers remain error leaves. Unterminated strings retain the
remaining input; ambiguous malformed strings cannot always expose later forms
as distinct expressions.

The maximum recursive depth is 64, configurable downwards with
`(parse source {:max-depth n})`. Excess nesting becomes error tokens.
Parsing is a full-document pass; incremental parsing is not implemented.

## Syntax versus analysis

The parser checks token spelling, escapes, delimiters, required reader operands,
map form counts, reader-conditional structure, and f-string expression counts.
Map arity is deferred when reader conditionals can affect the count.

Namespace resolution, gensym context, nested anonymous-function restrictions,
metadata target types, duplicate evaluated collection values, regex compilation,
and tagged-literal value validation belong to analysis.

## Tests

```sh
uv run basilisp test -p tests --include-unsafe-path=false
```

Tests cover forms, recovery, exact leaf/branch ranges, Unicode positions,
deep nesting, large literals, and deterministic randomized editor input.
