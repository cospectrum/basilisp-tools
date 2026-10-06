# Upstream test audit

The compatibility workflow reuses tests from pinned cljfmt, clj-kondo, and
clojure-lsp checkouts. Upstream tests remain external; the adapters read their
inputs and expectations without modifying the checkouts. Reports are uploaded
as the `upstream-test-reports` GitHub Actions artifact.

These are selected compatibility experiments, not the full upstream suites or
a claim that Basilisp implements JVM Clojure. Strict mode is the default for the
audit scripts. CI uses checked-in baselines for the new checker and LSP audits:
previously exact cases must remain exact, and extraction counts must stay stable.
Known differences remain visible without claiming parity. Execution errors and
baseline regressions fail those audits. Existing strict comparisons remain in
place. `--report-only` is available for exploring differences without a baseline.

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
from 68 test files and 1,114 were skipped. There were 771 exact diagnostic and
exit-code matches, 595 mismatches, and no execution errors. Skips comprise
959 dynamic arguments, 127 other-dialect
inputs (including seven selected by filename), ten hook fixtures, sixteen calls
with unsupported CLI options, and two non-string inputs.

For example, blt missed the upstream invalid-arity diagnostic for `(def x 1 2)`
and the type-mismatch diagnostic for `(inc ())`. It also reported an unresolved
namespace for `(ns foo (:require bar)) ::bar/bar`, where clj-kondo reported none.
Other mismatches involve platform features and libraries without direct Basilisp
equivalents, so the mismatch count is not a count of confirmed implementation bugs.

The report includes every extracted input, configuration, comparison, and skip
reason. The checked-in baseline protects exact matches and extraction counts.
Remaining differences are findings to investigate, rather than automatically
classified as bugs or accepted language differences.

```sh
uv run --locked python scripts/check_kondo_upstream.py \
  --checkout /path/to/clj-kondo \
  --baseline scripts/kondo_upstream_baseline.json --report kondo-upstream-report.json
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

The 2026-10-06 results were 42 exact matches, 11 structurally similar outputs,
and 111 mismatches, with no execution errors after the fix below. Structural
similarity omits trivia and reader discards and normalizes core namespace
qualification; it does not prove semantic equivalence and does not count as an
exact pass.

Differences include binding edits, generated function names, nested unwind
selection, conservative lookup rewrites for targets with unknown runtime types,
and refusals to rewrite reader discards or produce an odd-length map. The report
contains every input, expected output, actual output, and source test location;
these differences need individual review before changing behavior.

The initial run exposed a crash when `introduce-let` had no expression at the
cursor. The fix handles an absent target, with 21 regression scenarios covering
`introduce-let`, `extract-function`, and `extract-to-def` on empty text and
whitespace.

```sh
uv run --locked python scripts/check_lsp_upstream.py \
  --clojure-lsp /path/to/clojure-lsp \
  --baseline scripts/lsp_upstream_baseline.json --report lsp-upstream-report.json
```
