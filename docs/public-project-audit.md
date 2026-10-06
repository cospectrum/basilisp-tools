# Public-project audit

The October 6, 2026 audit selects **968 tracked `.lpy` and `.cljc` files,
8,888,940 bytes, from 44 pinned public repositories**. These include libraries,
applications, examples, bug reproductions, and the portable Clojure test suite.
They are not all production applications, and this is not an exhaustive survey
of the internet.

The [original manifest](../scripts/public_projects.json) contains ten projects,
the [extended manifest](../scripts/public_projects_extended.json) contains 25,
and the [discovery manifest](../scripts/public_projects_discovered.json) adds
nine. GitHub repository/readme searches for Basilisp and code searches for `.lpy`
and `:lpy` branches supplied candidates. Empty repositories, unrelated projects,
duplicate copies of the Clojure suite, and a commented-out `:lpy` search match
were excluded. Mixed Clojure repositories have explicit source selections.
Every checkout uses a full commit ID; no floating branch is used in validation.
The [machine-readable results](public-project-results.json) record each
repository's scope and outcome, source hashes, timeout budgets, and controlled
performance samples.

Formatting succeeds for **964 files**, with 489 files reformatted and no
idempotence, token, or literal preservation failures. The four refused files
remain byte-identical; their native-reader failures are described below.

The checker completes all **44 projects and 968 files** with structured output
and no internal analysis/file failures. The 283-file options project exceeds the
initial 900-second limit, then finishes in 967 seconds with a 1,800-second limit.
Both attempts are retained. Diagnostic totals include deliberately invalid
fixtures, project-specific linter settings, and missing macro/dependency models;
successful checking does not mean every source file is free of findings.
Whole-project reports were captured during the audit, followed by targeted
replays of files affected by fixes. The results retain those corrections and
earlier attempts; their totals are audit evidence, not a claim that every project
was rerun after the last source edit.

The LSP audit completes **43 of 44 workspaces**, each passing 17 assertions under
its project settings. The remaining workspace, `vandyand/options-basilisp`, times
out on global references after 900 seconds. No completed assertion reports an
incorrect response or internal server failure. Some successful large-workspace
requests still take minutes; those timings and earlier timeout attempts remain
in the results.

## What is exercised

- **Formatter:** the real CLI receives every selected file explicitly, including
  hidden files and files excluded by project directory-discovery settings. A
  second `--check` must succeed. The audit checks parser round trips and exact
  non-whitespace token/literal preservation on temporary copies.
- **Checker:** every selected file must produce structured findings, with the
  reported file count matching the manifest. A crash, timeout, malformed output,
  or internal `Analysis failed:`/file error fails the audit. Normal diagnostics
  remain visible for review; negative test fixtures are not required to lint
  cleanly. Broad runs disable Python inspection to isolate source analysis.
- **LSP:** protocol probes cover diagnostics after edits and repairs, UTF-16
  positions, completion, hover, definitions, references, rename,
  formatting, and shutdown. Rename edits must include the actual declaration.
  Internal analysis/configuration errors and incomplete or interrupted runs are
  failures. Initial and repeated-process timings are recorded separately.
  The harness respects confirmed global diagnostic disables and records waived
  probes. Steno disables syntax findings; its run verifies that setting and uses
  an enabled unused-value warning for the exact UTF-16 range assertion.
- **Runtime preservation:** the pinned `EnigmaCurry/calc` suite passes the same
  **258 tests before and after formatting**, including identical collected test
  identities. The runtime harness copies the checkout and rejects empty,
  skipped, failed, or changed test sets.

The broad audit does not install and execute every project's dependencies.
Blender, Pyxel, notebook, GUI, and service integrations need their host runtimes.
The pprint suite fails during import with a native map-construction error on
Basilisp 0.5.1; the hiccup suite exceeded a 180-second runtime-test budget.
Neither is counted as a passing runtime suite. Source-analysis validation is
separate from these upstream execution results.

## Reader-version differences

Two upstream fixture files contain symbol/keyword spellings rejected by the
installed **Basilisp 0.5.1** reader:

| Repository | File |
| --- | --- |
| `basilisp-lang/basilisp` | `tests/basilisp/test_edn.lpy` |
| `jank-lang/clojure-test-suite` | `test/clojure/edn_test/read_string.cljc` |

Examples include `ns//`, `some/ns/sym`, and Hebrew combining marks. Minimal
native-reader probes independently reproduce the rejection. Ordinary Hebrew
symbols work. The formatter correctly refuses the full selection containing
these fixtures; the remaining files are then validated with explicit supported
file lists. These two files are reported as version incompatibilities, not
successful formatting cases. The checker retains their syntax diagnostics.

Two other published sources are malformed even in the native reader:
`zachcp/molnodes-utils/scripts/basilisp/molecularnodes.lpy` has an unclosed form
(unexpected EOF at line 330), and
`zhaoyul/qt6-tutorials/basilisp/06_network/04_websocket/main.lpy` has an odd map
at line 76. Their formatter refusals are expected. The remaining files in both
repositories pass the full formatting audit.

## Changes prompted by the audit

The checker now handles initial definitions inside `basilisp.core`, the valid
function name `/`, `basilisp.pprint/print-length-loop` bindings, nil short-circuit
threading, missing-key return values from typed maps, namespace inference after
qualified-name predicates, and qualified
references to vars referred into the current namespace. Runtime-backed
regressions cover these cases. On the actual public `basilisp/repl.lpy`, the
spurious unresolved `*1`/`*2` findings disappear; the genuine unused import stays.
Across the original ten-project corpus, these corrections remove nine false
errors and 554 false warnings. Remaining findings include deliberately invalid
test cases and have not all been individually classified.

String ordering follows Basilisp's runtime behavior. Division uses Python
operator signatures when available and retains an unknown result for uninspected
receivers, rather than assuming every operand is numeric. Runtime-backed tests
cover path joins, reflected operators, chained division, and invalid scalar
pairs. Supplied Python signatures still enforce their argument contracts.

The formatter uses a private append buffer in its normalization pass and avoids
measured variadic-call overhead. Public node values remain immutable. The LSP
can answer local references and rename without waiting for unrelated workspace
analysis. Its rename guards reuse a parse only when its source matches the
document and examine the binding's ancestors rather than unrelated subtrees.
Global references still require workspace analysis.
In the broad audit, global references take 630 seconds on the Basilisp repository
and 401 seconds on the portable Clojure suite. The options workspace exceeds a
900-second global-reference budget. mmllm passes the protocol checks, but edited
document diagnostics take 76–180 seconds. These are exploratory timings, but they
demonstrate a remaining usability problem on large workspaces and large files.
Declaration indexing also skips `deftest` bodies when shallow analysis is
requested, including macros configured with `lint-as`. Full checking still
analyzes those bodies; a regression checks both modes and declaration metadata.

Project macro hooks, missing dependencies, unsupported runtime versions, and
deliberately invalid upstream tests still affect diagnostics. For example,
Blender's odd-length binding vector is a real source error, while custom binding
macros need hooks or `lint-as`. Counts respect project linter configuration;
zero findings do not certify dependencies that were not inspected.

## Controlled performance measurements

The comparison uses baseline commit `cc965a5e8f6f8117d30da53cb7b6bfac7b850f22`
on an Apple M5, macOS 26.5, Python 3.13.2, and Basilisp 0.5.1. Other audit
processes were stopped during measurement. Compiler and filesystem caches were
warm; initial cache-priming runs are retained separately and excluded from the
medians. These are measurements of particular inputs, not general speed claims.

| Workload | Before | After | Measurement |
| --- | ---: | ---: | --- |
| Format Basilisp core, already formatted | 8.767 s | 6.915 s | Median wall time, four calls per version |
| CLI `format --check` on the same file | 9.783 s | 7.753 s | Median wall time, two processes per version |
| CLI checker on nREPL, four files | 15.696 s | 15.417 s | Median wall time, three processes per version |
| LSP first local references, nREPL | 31.746 s | 1.146 s | One paired run, fresh analysis caches |
| LSP repeated local references, nREPL | 2.381 s | 0.196 s | Second request in those same servers |
| LSP first local rename, nREPL | 2.713 s | 0.456 s | One paired run, after the references request |

The formatter input is the identical 299,376-byte formatted core file, derived
from the pinned 298,280-byte original. Both revisions produce the same SHA-256
output. Alternating balanced runs show 21.1% lower in-process CPU time and 20.7%
lower CLI CPU time. CLI measurements include interpreter startup; the in-process
measurements exclude imports and warm-up.

The checker comparison disables declaration caching and Python inspection. It
checks the same 78,358 bytes in alternating baseline/candidate processes after
one warm-up each. All findings hashes match. Its roughly 2% improvement is small;
it does not establish a substantial checker speedup.

The LSP probe checks exact reference locations and rename edits, without editing
the document between requests. It uses one process per revision and precompiled
modules; these numbers are paired observations, not medians. Local navigation
can now finish while unrelated workspace analysis continues. A separate guard
probe on a 145,394-byte pprint source takes 2.158 seconds before and 0.00429 seconds
after, returning the same result. That last figure measures the guard function,
not a complete LSP request. Global navigation still has workspace-sized costs.

## CI and reproduction

The [workflow](../.github/workflows/ci.yml) now runs the formatter, checker, and
LSP public harnesses on pinned `basilisp-nrepl-async` and `basilisp-flask` sources,
and the calc runtime suite before and after formatting. It uploads the reports
even on failure. Existing upstream differential tests, generated-project
execution, real Python-package checks, and macOS/Linux tests remain in CI.
The larger 44-repository corpus is an explicitly repeatable audit.

For one manifest:

```sh
uv run python scripts/fetch_public_projects.py /tmp/blt-public --source-only
uv run python scripts/check_public_format.py --corpus /tmp/blt-public --report /tmp/blt-format.json
uv run python scripts/check_public_check.py --corpus /tmp/blt-public --no-python-inspection --output /tmp/blt-check
uv run python scripts/check_public_lsp.py --corpus /tmp/blt-public --output /tmp/blt-lsp
uv run python scripts/check_public_runtime.py --corpus /tmp/blt-public --output /tmp/blt-runtime
```

Pass `--manifest scripts/public_projects_extended.json` or
`--manifest scripts/public_projects_discovered.json` to the fetch/format/check/LSP
scripts for the other corpora, using separate checkout directories. Their
`--project` option selects a repository. `--source-only` uses a partial, sparse
Git checkout containing source and configuration, avoiding large models and
datasets while retaining the pinned revision. It made the large mmllm repository
practical to inspect. Fetching refuses changed or mismatched existing checkouts.
The largest LSP retries use the script's default 900-second reference budget,
180 seconds for other requests, and 300 seconds for diagnostics. Other retries
use `--request-timeout 300 --diagnostic-timeout 300`; this also caps references at
300 seconds. Shutdown has a separate 30-second budget. The results record each
actual budget. Increasing a budget can establish functional correctness while
still revealing unacceptable interactive latency.

The full formatter command intentionally reports the four native-reader
refusals above. To reproduce the supported-file pass, derive an
explicit `files` list from each entry's existing selection, using
`git ls-files '*.lpy' '*.cljc'` only when the entry has no `files` list. Remove
only the named refused file for its repository and pass that manifest. This
preserves exclusions such as the Clojure suite's unexpanded test template.
Preserve the full rejection report alongside the supported-file report.

Use `--python /path/to/python` for checker dependency inspection and
`--python-executable /path/to/python` for LSP inspection. Checker `--repeat 2
--isolated-cache` distinguishes an empty declaration cache from its reused state.
Compiler and operating-system caches are separate. `scripts/benchmark.py`
measures warmed formatting and can emit a cProfile; run competing revisions on
identical input without other audits running. Concurrent audit wall times are
not speedup measurements.

See [Compatibility](compatibility.md) for language and static-analysis limits.
