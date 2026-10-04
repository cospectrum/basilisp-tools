# Syntax API

`basilisp-tools.syntax` parses source into an immutable tree. It preserves the
original text and never runs project code, expands macros, or invokes tagged
literal readers.

## Parse and inspect

```clojure
(require '[basilisp-tools.syntax :as syntax])

(def parsed (syntax/parse "(inc ; note\n  1)"))
(:root parsed)
(:diagnostics parsed)
(syntax/text (:root parsed)) ; original source, exactly
```

The result has `:source`, `:root`, `:diagnostics`, and `:line-starts`. Every node
has `:kind`, `:start`, and `:end`, plus `:text` for leaves or `:children` for
branches. A missing delimiter or operand marks its form `:incomplete? true`.

Ranges are zero-based, half-open Unicode code-point offsets. Each character
belongs to exactly one leaf; children partition their parent's range. The tree
retains punctuation, whitespace, line endings, comments, and invalid input
without inserting synthetic characters.

## Supported forms

- Lists, vectors, maps, sets, and anonymous functions.
- Symbols, keywords, auto-resolved keywords, nil, and booleans.
- Integers, bigints, decimals, floats, octal/hex/arbitrary-base numbers, ratios,
  scientific notation, imaginary numbers, and symbolic numeric constants.
- Character literals and named/Unicode characters.
- Strings, regex literals, byte strings, and f-strings. Interpolations contain
  ordinary syntax nodes for analysis.
- Quote, syntax quote, unquote, unquote-splicing, dereference, var quote,
  metadata, nested reader discards, namespaced maps, and tagged literals.
- Reader conditionals and splicing conditionals, with all branches preserved.
- Line comments, shebang comments, whitespace, and commas.

A lone colon or backslash is reported as an error. The default dialect validates
Basilisp syntax. `(parse source {:dialect :clojure})` additionally accepts
read-eval text, legacy metadata, aliased namespaced maps, and array-class symbols
for lossless tooling; it still never evaluates source.

## Traversal

| Function | Result |
|---|---|
| `nodes` | Lazy preorder sequence including the supplied node |
| `tokens` | Leaf tokens in order, including punctuation and trivia |
| `text` | Exact source reconstructed from leaves |
| `forms` | Direct forms, excluding trivia, punctuation, string text, and discards |
| `trivia?` | Whether a node is whitespace, a newline, or a comment |
| `node-at` | Deepest node containing an offset; nil at EOF |

`forms` preserves reader conditionals as syntax rather than selecting a branch.

## Positions

`offset->position` returns a zero-based `{:line ... :character ...}` map;
`position->offset` converts it back. Characters count code points, not UTF-16
units. CRLF is one line break, and `position->offset` rejects positions inside
line endings. LSP adapters must convert to the negotiated encoding.

## Errors and recovery

Diagnostics have `:code`, `:message`, `:severity`, `:start`, and `:end`. Missing
tokens can have zero-width ranges.

Mismatched delimiters recover at an enclosing delimiter where possible;
unexpected closers remain error leaves. Unterminated strings retain the
remaining input, so later forms may become part of the string.

Form nesting is limited to 128 levels. Lower it with `(parse source {:max-depth n})`;
excess nesting becomes error tokens. Each parse processes the full document.

The parser checks token spelling, escapes, delimiters, reader operands, map form
counts, reader-conditional structure, and f-string expression counts. Map arity
is deferred when reader conditionals can affect it.

Namespace resolution, gensym context, nested anonymous-function restrictions,
metadata target types, duplicate evaluated collection values, regex compilation,
and tagged-literal value validation require analysis.
