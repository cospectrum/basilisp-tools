# Upstream test audit

The compatibility workflow reuses tests from pinned cljfmt, clj-kondo, and
clojure-lsp checkouts. Upstream tests remain external; the adapters read their
inputs and expectations without modifying the checkouts. Reports are uploaded
as the `upstream-test-reports` GitHub Actions artifact.

These are selected compatibility experiments, not the full upstream suites or
a claim that Basilisp implements JVM Clojure. Strict mode is the default for the
audit scripts. CI checks both exact-match baselines and reviewed Basilisp
differences. Each difference records its reason, supporting evidence, an input
digest, and the exact expected Basilisp output. Unclassified differences, changed
outputs, obsolete exceptions, execution errors, and changed extraction coverage
fail CI. Structural similarity alone never passes. Existing strict comparisons
remain in place. `--report-only` is available for exploring new differences.

## Formatter

`check_cljfmt.py` already runs the actual `cljfmt.core-test` namespace under
Clojure, checks its assertions, and captures calls to `reformat-string`. It
replays those calls against our formatter and checks idempotence. The upstream
revision is `baab5008032945434cbca23ef5eda516e3ea97b0`.

The 2026-10-06 run captured 398 distinct upstream formatting cases and added 24
repository regression cases. All 422 matched. Of 68 Basilisp corpus files, 65
matched; cljfmt could not handle three files, which our formatter parsed and
formatted idempotently. The upstream namespace itself ran 32 tests with 432
assertions. These counts differ because not every assertion invokes formatting
and repeated formatting inputs are deduplicated.

```sh
uv run --locked python scripts/check_cljfmt.py \
  --cljfmt /path/to/cljfmt --corpus /path/to/basilisp \
  --report cljfmt-upstream-report.json
```

## Checker

`check_kondo_upstream.py` uses `capture_kondo_tests.clj` to read literal `lint!`
calls from upstream `.clj` tests. It retains the upstream base configuration and
per-call settings, including merge metadata. The source revision is
`6267607412e55ed3b91710ef542f3fee2ad7aa05`, matching the pinned native clj-kondo
release `2026.08.04`.

This replays test inputs, not the upstream Clojure assertions or their surrounding
setup. The native executable supplies expected diagnostic types, levels, messages,
ranges, and exit codes. Known equivalent core library namespaces are adapted for
Basilisp, including corresponding message and column adjustments. Dynamic input
expressions, other dialects, executable hooks, and external fixtures are recorded
as skips. Existing hook tests continue to run separately.

The 2026-10-06 extraction found 2,480 `lint!` call sites: 1,366 were replayed
from 68 test files and 1,114 were skipped. After the fixes, there are 1,120 exact
diagnostic and exit-code matches, up from 771, plus 246 reviewed native
differences and no execution errors. Skips comprise
959 dynamic arguments, 127 other-dialect
inputs (including seven selected by filename), ten hook fixtures, sixteen calls
with unsupported CLI options, and two non-string inputs.

The fixes cover arity and type inference, protocol implementations, lexical
bindings and destructuring, namespace changes and aliases, configuration scope,
metadata and quoted reader forms, and diagnostic messages and ranges. Regression
tests also execute representative forms in Basilisp so that Clojure expectations
do not override native semantics.

The report includes every extracted input, configuration, comparison, and skip
reason. The checked-in baseline protects exact matches and extraction counts.
`scripts/kondo_upstream_differences.json` records the remaining native differences:
Python interop and runtime contracts, reader behavior, and JVM or third-party
Clojure APIs without Basilisp counterparts. These cases remain in the audit and
must preserve their reviewed Basilisp diagnostics.

```sh
uv run --locked python scripts/check_kondo_upstream.py \
  --checkout /path/to/clj-kondo \
  --baseline scripts/kondo_upstream_baseline.json \
  --differences scripts/kondo_upstream_differences.json --report kondo-upstream-report.json
```

## Language server

`check_lsp_upstream.py` reads selected tests from `transform_test.clj` and
`thread_get_test.clj` at revision
`8ad65c1d681d2fc9022b3854f6dcaf1677d29631`. It interprets a small set of fixture
forms as data, retaining cursor positions, settings, and expected strings. It
distinguishes assertions about whole documents from assertions about individual
edit replacements and repeated transformations. It exercises the refactoring
engine; existing protocol tests exercise the language server transport.

The audit selects 18 of the 38 `deftest` forms in these two files and replays
164 output expectations. It records 39 skipped assertions about availability,
nil zipper inputs, or upstream edit ranges. The other 20 tests contain 237
assertion forms and are listed as unselected. Other upstream test files are
outside this audit. Unsupported fixture forms within selected tests are errors.

After the fixes, 137 expectations match exactly, up from 42. There are no
structural-only results or execution errors. The remaining 27 cases have
reviewed expectations in `scripts/lsp_upstream_differences.json`:

- 23 lookup rewrites change results for native Python dictionaries. Each case
  has an executed counterexample comparing the original and upstream output.
- Two binding expansions change call arguments or produce invalid function
  syntax. Basilisp preserves the call's argument count or declines the edit.
- One variadic function promotion incorrectly nests the rest arguments upstream.
  Basilisp forwards them through `partial`.
- One collection conversion would create an odd-length map. Basilisp declines
  that invalid edit and supports repairing an odd map into a vector.

The fixes cover threading layout and reader discards, nested cursor selection,
binding introduction/movement/expansion, privacy metadata, function promotion
with captures, and function demotion with comments and variadic parameters.
Execution regressions check values, side effects, lexical scope, and argument
forwarding. The original empty-target crash regression remains covered.

```sh
uv run --locked python scripts/check_lsp_upstream.py \
  --clojure-lsp /path/to/clojure-lsp \
  --baseline scripts/lsp_upstream_baseline.json \
  --differences scripts/lsp_upstream_differences.json --report lsp-upstream-report.json
```
