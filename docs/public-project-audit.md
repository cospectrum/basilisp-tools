# Public-project audit

The audit covers 199 tracked `.lpy` and `.cljc` files (2.36 MB) from ten public
repositories. [The manifest](../scripts/public_projects.json) pins every revision:
Basilisp itself, basilisp-pprint, basilisp-nrepl-async, basilisp-kernel,
basilisp-blender, basilisp-flask, aerob, calc, balli, and steno.

## What the tests establish

- **Formatting:** all 199 files passed the real CLI, a second `--check`, and
  independent token and literal preservation checks. 194 matched cljfmt exactly;
  five use Basilisp-only reader forms. Another 422 comparison fixtures matched
  exactly. Formatting runs on temporary copies, leaving the checkouts unchanged.
- **Checking:** each tracked source is checked with findings retained for review.
  Public test suites deliberately contain invalid code, and project macros and
  optional Python dependencies affect results. A successful audit does not mean
  every third-party source has zero findings.
- **Language server:** semantic protocol probes passed in all ten workspaces.
  They exercise initialization, opening and
  changing documents, diagnostics, completion, hover, signatures, navigation,
  references, rename, formatting, and shutdown, including exit during active
  analysis. Rename edits are inspected without
  changing public sources.
- **Generated projects:** all 18 projects passed (nine scenarios with two seeds).
  Executable examples cover namespaces, Python dataclasses
  and keyword arguments, stubs, reader conditionals, Unicode, macros, context
  managers, `lint-as`, and multi-file workspaces. Valid programs must stay valid;
  deliberately broken calls must produce the expected diagnostics. Formatting and
  cross-file rename must preserve runtime results. Stub edits must invalidate
  Python metadata, and diagnostic columns are checked after emoji.

The audit found and fixed real gaps in `.cljc` discovery, Basilisp binding forms,
Python metadata decoding, generic substitution, cache invalidation, per-file
error recovery, and formatting edge cases. These now have regression tests.

## Findings that remain

| Project | Files | Errors | Warnings |
| --- | ---: | ---: | ---: |
| Basilisp | 68 | 29 | 219 |
| basilisp-pprint | 2 | 21 | 176 |
| basilisp-nrepl-async | 4 | 1 | 30 |
| basilisp-kernel | 3 | 0 | 14 |
| basilisp-blender | 9 | 55 | 52 |
| basilisp-flask | 1 | 0 | 0 |
| aerob | 1 | 0 | 2 |
| calc | 28 | 0 | 16 |
| balli | 66 | 0 | 7 |
| steno | 17 | 0 | 2 |

Basilisp's errors include 22 deliberately invalid test calls, six custom macro
cases, and one inference limitation around `qualified-symbol?`. Pprint and nREPL
need custom macro context. Blender has one genuinely malformed binding vector;
its other errors concern custom macros and a remote namespace alias. Macro
bindings need project hooks or `lint-as`; these findings were not suppressed.

Counts honor each project's configuration. Steno disables several linters, and
its zero-error result does not establish support for unavailable OpenCV or
matplotlib dependencies. The Blender runtime itself was not available.

## Performance

Measurements used an Apple M5 Mac with 32 GiB RAM and Python 3.13. Timings are
workload-specific; large source files remain more expensive than small modules.

| Measured operation | Before | After |
| --- | ---: | ---: |
| Format three real files, 92,400 characters (median CPU time) | 8.48 s | 5.95 s |
| Export keywords from Basilisp core | 54.63 s | 18.63 s |
| Serialize Blender inspection metadata | 131.89 MB | 30.05 MB |
| Resolve Blender `Materials.new` from stubs | 15.38 s | 4.34 s |
| Check Steno with its Python dependencies (17 files) | >600 s timeout | 43.17 s |

Formatter results and keyword records were unchanged by the performance changes.
Blender checks used `fake-bpy-module` stubs, including completion, signature help,
and navigation to the declaration; they do not certify execution inside Blender.
First launch also pays Basilisp's compilation cost. Large-workspace LSP
performance remains a limitation: initial reference scans took several minutes,
and Steno edit diagnostics took 25–61 seconds during the concurrent audit.
Tracing also found a 34-second cache write delaying diagnostics. Persistence now
runs after accepted analysis returns, with stale-write protection and one writer
at a time. Warm Python completion and hover were about 25–32 ms. The timings
precede that final scheduling fix and are workload observations, not guarantees.

## Repeat the audit

From this repository, fetch the pinned sources into a separate directory:

```sh
uv run python scripts/fetch_public_projects.py /tmp/blt-public
uv run python scripts/check_public_format.py --corpus /tmp/blt-public --report /tmp/blt-format.json
uv run python scripts/check_public_check.py --corpus /tmp/blt-public --output /tmp/blt-check
uv run python scripts/check_public_lsp.py --corpus /tmp/blt-public --output /tmp/blt-lsp
uv run python scripts/check_generated_projects.py --output /tmp/blt-generated
```

Use a new output directory for generated projects. The checker accepts
`--python /path/to/python`, and the LSP audit accepts `--python-executable`, to
inspect an environment containing the project's dependencies. Both support
`--project` to narrow a run. Missing optional dependencies limit Python results.
Reports include findings and timings; the LSP harness also retains wire transcripts.
Generated projects run in CI. The larger public corpus can be repeated separately.

This evidence supports the covered behavior, not universal clj-kondo or
clojure-lsp parity. See [Compatibility](compatibility.md) for language and
static-analysis limits.
