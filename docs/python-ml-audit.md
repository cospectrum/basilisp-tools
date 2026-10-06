# Python ML interoperability audit

The ML audit checks executable Basilisp programs against Torch 2.14.1 and ONNX
1.23.2, and records whether Python signatures, return types, and receiver members
are available. A clean diagnostic list alone does not count as successful type
coverage. Unknown contracts and missed invalid arguments fail the audit.

Create a separate interpreter containing these packages and Basilisp, then run:

```sh
uv venv --python 3.13 /tmp/blt-ml
uv pip install --python /tmp/blt-ml/bin/python \
  torch==2.14.1 onnx==1.23.2 numpy==2.5.3 basilisp==0.5.1
PYTHONPATH=src .venv/bin/python scripts/check_ml_packages.py \
  --python /tmp/blt-ml/bin/python --output /tmp/ml-packages.json
```

The 35 cases cover tensors, devices and dtypes, methods and arithmetic, neural
network layers, an optimizer training step, data loading, distributions, ONNX
graphs, model checking, shape inference, and CPU reference inference. The 22
positive programs must execute with the expected result and produce no
diagnostics on either their first or repeated analysis. The 13 negative programs
must fail at runtime and produce their expected argument diagnostic. Reports
retain all findings and unknown metadata, interpreter package versions, source
hashes, and first/repeated timings.

The default audit fails on every unmet check. CI can supply `--baseline FILE`
containing explicitly reviewed `{ "allowed_failures": [{ "case": "...",
"reason": "..." }] }` entries. This compares exact case/reason pairs and fails
on both new failures and stale allowances. Original failure counts remain in
the report. Only missing API contracts, unresolved result members, and missed
negative diagnostics can be listed; positive diagnostics, native failures, and
analyzer crashes cannot be allowed.

The selected inspection timeout defaults to 30 seconds for this audit. This is
longer than the product's default five seconds; audit success must not be read
as proof that a cold Torch inspection finishes within the product default. Test
that separately with `--python-timeout 5 --case tensor`.

## Upstream Torch fixtures

```sh
PYTHONPATH=src .venv/bin/python scripts/check_torch_upstream.py \
  --root /tmp/torch-typing --fetch --python /tmp/blt-ml/bin/python \
  --output /tmp/torch-upstream.json
```

`scripts/torch_typing_sources.json` pins and hashes every file in the release's
`test/typing/pass`, `fail`, and `reveal` directories, plus the Python operator
test drivers and license. The fetched corpus retains the upstream license.
Every report records the manifest digest, release, runtime versions, and selected
files, so a `--file` replay is distinguishable from a complete run.
The adapter inventories all original statements and translates
their executable consumer expressions. Its operator matrix is generated from
the pinned driver's operand and operator lists. Original Python classes and
functions remain in companion modules so their annotations and dispatch rules
are preserved. Three Dynamo narrowing assertions use typed input factories
with the original parameter annotations and conditions.

PyTorch's own typing driver skips files beginning `disabled_`. This audit also
executes `pass/disabled_jit.py` and `fail/disabled_bitwise_ops.py` as extra
coverage and retains their results; they are not active upstream test cases.

Every translated expression executes in both Python and Basilisp. The harness
compares result types or exception classes before applying static expectations;
it does not assert numerical equality for random samples. Device literals are
adapted from CUDA to CPU and recorded individually. The CUDA stream function
body requires accelerator hardware and remains explicitly unexercised. Other
upstream examples that reject at runtime remain recorded with their exact
exceptions instead of becoming valid-program oracles. Their explicit static
type assertions are still checked and counted separately; a dtype-dependent
runtime failure does not excuse missing type information. Upstream type assertions
that disagree with the installed runtime are reported separately.
Their inferred types must agree with the observed runtime result type, so an
incorrect upstream expectation cannot make an unrelated concrete type pass.

Unknown inferred types, type assertion mismatches, false positive diagnostics,
missed argument errors, failed adaptations, and runtime translation differences
all remain visible. A nonzero exit status is an audit failure, not an expected
success with a hidden baseline allowance.

## Generated workloads and profiling

```sh
.venv/bin/python scripts/generate_ml_projects.py --output /tmp/ml-large --size 128
PYTHONPATH=src .venv/bin/python scripts/check_ml_packages.py \
  --manifest /tmp/ml-large/cases.json --python /tmp/blt-ml/bin/python \
  --case-timeout 600 --output /tmp/ml-large.json
```

The generator creates 128 independent training functions and 128 checked ONNX
inference graphs, plus negative variants. All functions in positive projects
execute on CPU; no models, data, or weights are downloaded. The inventory
records exact source bytes, line counts, and hashes.

Each case runs in a fresh analyzer process. Repeated calls share one inspection
cache. Timings include analyzer calls and inspection work, but exclude host
interpreter startup and analyzer module loading. Parent CPU time excludes
inspection child processes. Use the same immutable source snapshot, inputs,
dependencies, cache conditions, and a quiet machine for comparisons; ordinary
audit timings are exploratory.

Pass `--profile-dir /tmp/ml-profiles` to profile repeated analysis with cProfile.
These instrumented timings include substantial profiling overhead and must
not be presented as benchmark measurements. Set `PYTHONPATH` to an immutable
checkout's `src` directory when collecting baseline results while development
continues in the working tree.

## Recorded results

[The compact evidence](python-ml-results.json) compares commit `4240b2e` with
`f21a456` using identical source inputs and the pinned CPU environment. It records
source hashes, per-file counts, failures, and the hashes of the original reports.

| Check | Before | After |
| --- | ---: | ---: |
| ML cases with unmet checks, out of 35 | 21 | 3 |
| ML API signatures or returns unresolved | 9 each | 0 |
| ML negative cases missed, out of 13 | 10 | 2 |
| Torch valid expressions with diagnostics, out of 837 | 10 | 0 |
| Torch unresolved positive type assertions, out of 550 | 509 | 362 |
| Torch concrete positive type mismatches | 4 | 3 |
| Torch explicit negative cases missed, out of 10 | 10 | 5 |

All 22 positive ML programs execute correctly and have no diagnostics. One
result member remains unknown: `MultivariateNormal.covariance_matrix` uses a
custom lazy property, so the subsequent `numel` receiver type is unavailable.
Two rejected ONNX inputs remain undiagnosed: a string passed as `GraphProto`
and a list passed as `ndarray`. The strict ML audit therefore fails; CI uses the
three exact allowances in `scripts/ml_typing_baseline.json`. Improved cases
invalidate their old allowances, and positive diagnostics cannot be waived.

The Torch replay adapts 969 expressions from 22 files, including the generated
operator matrix and the two disabled upstream files described above. Both
versions execute the same adapted sources. No adaptation failed and all Python
and Basilisp runtime result types or exception classes agreed. The 122 other
runtime rejections comprise 46 upstream examples and 76 operator combinations;
they are not counted as valid-program oracles. The 44 explicit type assertions
among rejected upstream examples remain checked: 42 are still unknown. Twelve
upstream positive assertions disagree with the installed runtime's result type;
all twelve candidate results remain unknown. They contribute to the unknown
count, and none counts as a validated concrete result.

The remaining three concrete mismatches concern Dynamo narrowing. The five
missed negatives concern JIT compilation of a module class, three unsupported
float bitwise operations, and a dtype-dependent bitwise operation. The full
Torch audit remains failing because these gaps and unknown assertions remain.
The CUDA stream function body is explicitly unexecuted on this CPU host.

The smaller CI workload executes all 16 generated Torch training functions and
all 16 ONNX graphs. Both positive projects have resolved API and member metadata
with no diagnostics; both negative variants report their invalid calls. The
same four checks also pass at 128 functions/graphs against the final production
source. An LSP integration probe verifies Torch factory and arithmetic-result
hovers, ONNX helper hover, a missing-argument diagnostic, and its removal after
repair. Its concurrent-load timings are excluded from performance comparisons.

## Controlled performance comparison

The 128-function Torch source is 42,531 bytes; the 128-graph ONNX source is
84,840 bytes. Four samples ran sequentially on a quiet host in
baseline/candidate/candidate/baseline order. Each sample analyzed both projects
twice: once with a fresh inspection cache and once with that cache warm. The
table gives median wall seconds from two observations per version and cache
state. Exact individual wall and parent CPU measurements, source hashes, and
identical empty-diagnostic hashes are retained in the evidence JSON.

| Project | Fresh inspection, before → after | Warm inspection, before → after |
| --- | ---: | ---: |
| Torch training, 128 functions | 25.001 → 42.801 s | 16.050 → 20.088 s |
| ONNX inference, 128 graphs | 47.343 → 47.595 s | 44.207 → 42.512 s |

The final Torch analysis takes 25.2% longer when warm and now resolves members
that the baseline left unknown. ONNX takes 3.8% less time when warm. This compares
the complete feature change; it does not isolate individual optimizations or
establish a general speedup. An earlier single-pair observation of the call
preparation optimization is recorded separately with its older source hashes.

Host interpreter startup and analyzer module loading are excluded. Inspection
worker startup is included in the first analysis; filesystem and Basilisp
compiler caches were already warm. Parent CPU excludes child worker CPU, so
wall time remains necessary to assess inspection costs.

To reproduce the order, use the current harness with immutable source checkouts
at the two recorded commits and the same generated manifest:

```sh
sample=0
for snapshot in /tmp/blt-before/src /tmp/blt-after/src /tmp/blt-after/src /tmp/blt-before/src; do
  PYTHONPATH="$snapshot" .venv/bin/python scripts/check_ml_packages.py \
    --python /tmp/blt-ml/bin/python --manifest /tmp/ml-large/cases.json \
    --case torch-large --case onnx-large --repeats 2 --case-timeout 900 \
    --output "/tmp/ml-benchmark-$sample.json"
  sample=$((sample + 1))
done
```

The baseline Torch capture exits nonzero for unknown member metadata; retain
that failure when comparing its timings. All measured programs execute
correctly and produce no diagnostics. The final five-second inspection-budget
probe also passes for the tensor construction and `sum` case: both analyses
resolve the requested metadata without diagnostics. Its first complete analysis
takes 10.785 seconds because the budget applies to each inspection request,
not the whole analysis. This one fixture does not establish that every cold ML
program fits the default budget.
