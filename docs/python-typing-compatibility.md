# Python typing corpus audit

`scripts/check_python_typing.py` translates Python consumer expressions from pinned
typing, Pyright, mypy, Pyrefly, and ty fixtures into Basilisp interop expressions.
It checks call and attribute-assignment diagnostics and structured expression
types. This interop audit covers operations expressible in Basilisp; Python
declaration and control-flow rules require separate adaptation.

The source pins and released oracle versions are in
[`python_typing_sources.json`](../scripts/python_typing_sources.json). The ty
fixtures live in the Ruff repository, where ty is developed. Source revisions
and released checker versions are recorded separately: a current source snapshot
can include cases added after the latest release.
The [upstream license notices](data/python-typing-licenses/README.md) accompany
the adapted fixtures and source excerpts retained in the audit evidence.

The adapters inventory every fixture in the selected source trees:

| Provider | Fixture unit |
| --- | --- |
| typing | A conformance `.py` file |
| Pyright | A Python sample file |
| mypy | A `[case ...]` block in `check-*.test` |
| Pyrefly | A Rust `testcase!` invocation |
| ty | A Markdown test section, retaining its named source files |

Each consumer expression or explicit assertion receives a stable source-based
identifier. Raw reports retain the fixture source digest, original expression,
expected assertion, translated expression, actual type, diagnostics, and result.
Unparsed sources retain their diagnostic-marker count. Fixture totals and
assertion totals are different measurements.

## Repeating the audit

Use Python 3.13 or later to parse the largest portion of the current corpora.
Older syntax hosts explicitly exclude unsupported source syntax.

```sh
python scripts/check_python_typing.py --corpus /tmp/python-typing-corpus \
  --fetch --inventory-only --output /tmp/python-typing-inventory.json

python scripts/check_python_typing.py --corpus /tmp/python-typing-corpus \
  --provider typing --output /tmp/typing-interop.json

python scripts/check_python_typing.py --corpus /tmp/python-typing-corpus \
  --provider typing --fixture dataclasses_kwonly.py --require-resolved \
  --output /tmp/typing-ci.json
```

Fetching uses immutable GitHub archives and extracts only fixture and license
paths. It neither installs nor runs the projects' setup code. The translated
audit reads fixture Python metadata with dependency execution disabled and
batches expressions from each fixture into one analysis with a shared cache.

Pyright's sample comments are often prose, so its direct consumer calls require
an independently executed native oracle. Install the version pinned in the
manifest into an isolated environment, then run:

```sh
pyright --outputjson --pythonversion 3.13 \
  /tmp/python-typing-corpus/microsoft-pyright/packages/pyright-internal/src/tests/samples \
  > /tmp/pyright-native.json

python scripts/check_python_typing.py --corpus /tmp/python-typing-corpus \
  --provider pyright --pyright-oracle /tmp/pyright-native.json \
  --output /tmp/pyright-interop.json
```

Native Pyright is expected to exit nonzero because the corpus contains invalid
examples. The report records that oracle's version and source digest. Its profile
uses an explicit Python target in CI plus in-file directives; the upstream TypeScript runner's
per-test options are not reproduced. Diagnostics unrelated to the adapted call
are excluded rather than treated as argument errors.

The other adapters read upstream expected-diagnostic and type-assertion markers.
They do not run the providers' complete native test runners. `--provider` and
`--fixture` selections are recorded in the report, so a filtered run cannot be
mistaken for full-corpus coverage.
Unknown provider names and selections with no discovered or translated assertions
fail. `--require-resolved` also fails on unknown metadata, so a small regression
gate cannot silently lose previously available contracts.

## Reading results

- `passed`: the translated assertion matched; ordinary calls require a resolved
  signature, and return assertions additionally require resolved matching types.
- `failed`: a differential result requiring review. This is not automatically a
  confirmed product bug: declaration semantics, fixture setup, and adaptation
  need checking against the original source.
- `unknown`: metadata needed for the assertion was unavailable. Absence of a
  diagnostic with an unresolved contract does not count as a pass.
- `excluded`: a concrete adapter or language boundary is recorded for that unit.
- `error`: the replay raised an exception, the analyzer reported a document or
  inspection failure, or an adapted expression contained an unresolved lexical
  symbol outside the supported diagnostic mapping.

Exclusions currently include lexical/control-flow environments, temporal global
rebinding, optional/grouped or ambiguous same-line error oracles, custom mypy
builtins, flags, configuration files and incremental edits, unreproduced Rust
environment helpers, known-bug Pyrefly oracles, ty environment/rule configurations,
auxiliary-file assertions, dynamic
argument expansion, repeated keyword names, and expressions without an exact
adapter. Repeated keys within one literal keyword dictionary require Python's
overwrite and evaluation rules; repeated explicit or unpacked keywords require
its compilation or runtime rejection rules. The adapter excludes these cases
before constructing a Basilisp call with invalid keyword syntax. Some are useful
future adapter work, not features judged inapplicable to Basilisp.

Module-level source assignments are inspected as Python declarations; their
inferred value type is distinct from proving that the initializer call is valid.
The audit keeps those checks separate. Source hashes at the start and end expose
concurrent implementation changes, and raw elapsed times are exploratory, not
controlled performance comparisons. A report with differences or unknowns is
evidence for further work, not a whole-suite conformance claim.

Mypy display-only `builtins` and `__main__` names are normalized when constructing
return expectations. An unresolved expectation is unknown; it never becomes an
implicit expectation of `Any`. Explicit `Any` assertions remain distinct.

One reviewed constructor limit remains: when overloaded `__new__` mixes a foreign
return with `Self`, the selected instance branch retains its nominal class but
does not infer generic arguments from a separate `__init__`. For example, the ty
constructor fixture expects `E[int]` for `E("ok", 1)` while this checker reports
`E`. The native call is valid and produces no diagnostic. Related mixed
constructor branches can also miss an initializer argument error. These are
precision limits, not passing conformance assertions.

`scripts/compare_python_typing.py` compares two complete reports and requires the
same adapter, fixture content, inventory, source pins, and oracle. It retains
every status transition and lists newly failing expected-valid assertions for
review. `failed → unknown` is reported separately from an exact match; reducing
unsupported diagnostics does not establish return-type precision.

## Recorded comparison

[The compact evidence](data/python-typing-results.json) records 21,802 fixture
units and 90,273 discovered units across all five pinned providers. These units
include explicitly excluded declarations and markers; they are not 90,273
executed interoperability assertions.

| Result | Baseline `4240b2e` | Candidate |
| --- | ---: | ---: |
| Exact matches | 3,881 | 3,949 |
| Differences requiring review | 1,810 | 1,406 |
| Unknown contracts or types | 7,615 | 7,958 |
| Explicit exclusions | 76,960 | 76,960 |
| Replay-error assertion rows | 7 | 0 |

| Provider | Fixtures | Exact matches before → after | Remaining differences | Unknown | Excluded |
| --- | ---: | ---: | ---: | ---: | ---: |
| typing | 152 | 335 → 347 | 59 | 384 | 1,910 |
| Pyright | 1,375 | 866 → 883 | 303 | 1,932 | 6,533 |
| mypy | 8,327 | 678 → 684 | 234 | 647 | 25,452 |
| Pyrefly | 5,136 | 856 → 873 | 237 | 1,533 | 11,130 |
| ty | 6,812 | 1,146 → 1,162 | 573 | 3,462 | 31,935 |

There are 102 difference-to-match transitions, 11 unknown-to-match transitions,
and two error-to-match transitions. Separately, 314 differences and five
error rows become unknown. Thirty-five former matches become unknown, and
twelve become differences: nine missed negative cases and three reviewed return
differences. None of the newly failing expected-valid cases introduces a call
diagnostic. Existing expected-valid diagnostic differences fall from 214 to 155;
those remaining cases still require triage, so this is not a claim of zero false
positives throughout the corpus.

Two reviewed return differences follow an inherited `__new__` annotation that
permits returning the base class itself. Mypy's fixtures deliberately preserve
the subclass for compatibility; native Python can return the base instance.
The third is the mixed-constructor generic precision limit described above.
The nine newly missed negatives remain visible in the artifact, including
secondary initializer checks after mixed constructor branches. No remaining
full-corpus difference is a baseline allowance or a passing assertion.

All five providers use the same frozen adapter, fixture content, and oracle on
both engines. The candidate source snapshot exactly matches `f21a456`, including
the final native-stub safety fixes. Source and adapter hashes are unchanged
throughout each run. Fixture dependency execution is disabled. Audit timings
were collected under concurrent load and are not performance measurements.

The candidate replay analyzes 13,313 assertions across 4,351 fixtures and retains
each completed analysis's whole-document findings. Syntax, file,
internal-analysis, and Python-inspection failures are fatal before checking any
expression, including expected-invalid calls. The candidate has zero fatal
document findings and zero replay-error fixtures. The baseline's seven error
assertion rows come from two fixtures raising `TypeError` on unhashable metadata;
one fixture failure can affect several assertions. Neither engine's counts omit
namespace-row inspection failures.

## Follow-up interop improvements

[The follow-up evidence](data/python-typing-followup.json) records a separate,
same-adapter comparison against `6c96acf`, covering 21,802 fixtures and 91,002
discovered units. Its frozen candidate has 4,851 matches versus 4,410 before,
1,053 differences versus 1,159, 7,518 unknowns, and 77,580 explicit exclusions.
These counts precede the final narrow safety repairs and diagnostic-classifier
correction; their separate source hashes and bounded replays are retained in the
artifact. They must not be compared numerically with the earlier adapter's table.

The changes cover callback protocols, class-valued results, generic defaults and
constraints, TypedDict mapping constructors, and Basilisp `apply-kw`. The adapter
also retains literal and comprehension contexts and translates attribute
writes. Newly introduced diagnostic differences were reviewed individually;
confirmed invalid assumptions were repaired, while native/oracle disagreements
remain visible. For example, Python collapses `Optional[None]` to the `NoneType`
class, and double-underscore class parameters have mangled runtime keyword names.
Unknown or dynamic values do not establish that a union is a non-class value.

The classifier now recognizes the analyzer's exact missing-Python-member message.
It keeps unrelated unresolved lexical names as replay errors. Explicit upstream
attribute-error suppressions affect only member findings. The artifact includes
the twelve affected historical rows as a labeled derived classification, without
rewriting the original full reports or counting adapter errors as matches.

The [third comparison](data/python-typing-third.json) replays all 21,802 fixtures
with one frozen adapter against the second milestone and the initial third
candidate. Matched assertions rise from 4,832 to 4,911, while differences fall
from 1,069 to 959. Unknown results rise from 7,516 to 7,547; 77,577 exclusions
and eight lexical adapter errors remain explicit. The artifact records all 38
introduced diagnostic or precision regressions, separate targeted repairs, and
the complete strict selection manifest. These full-corpus counts describe the
initial candidate; later repair overlays retain their own source hashes.

This checkpoint checks native `set!` writes to Python attributes and module
objects, including declared field types, read-only properties, and mutable
protocol fields. Descriptor reads distinguish class access from instance access
and check calls through a returned `Callable`. Generic inference preserves
explicit `Any` receiver arguments and resolves dependent defaults in parameter
order. Custom attribute hooks, unknown decorators, and metaclasses that can
replace a class keep uncertain contracts unknown.

A controlled ABBA comparison of generated size-16 Torch and ONNX programs
preserved identical findings and member metadata. Median warm analysis time
increased from 2.61 to 2.86 seconds for Torch and from 6.18 to 6.82 seconds for
ONNX. The artifact retains cold and warm samples separately; this checkpoint
adds checking coverage with a measured runtime cost.

The [fourth comparison](data/python-typing-fourth.json) keeps another complete
21,802-fixture pair against `bba2e8`, using one frozen adapter and an independently
run Pyright oracle explicitly targeting Python 3.13. Initial exact matches rise
from 5,085 to 5,207, while differences rise from 966 to 1,016 and unknown results
fall from 7,370 to 7,198; both reports retain 77,581 exclusions and zero replay
errors. These are the initial snapshots, including the regressions they exposed.
The complete final replay reaches 5,262 exact matches, 950 differences and
7,209 unknowns with the same exclusions and zero replay errors. The artifact
keeps both full comparisons, all 86 initially introduced changes, subsequent
whole-fixture repairs, and one additional missed NewType class-value rejection
found by the final replay. It records all 12 remaining expected-valid diagnostic
rows with native or typing-contract evidence; those reviews do not change their
reported status. The broader corpus continues to contain applicable work; the
strict CI selection is a regression gate, not a claim that all upstream suites
pass.

A quiet ABBA comparison on generated size-16 programs preserved findings and
member metadata across all eight processes. Median warm analysis increased
from 2.83 to 2.92 seconds for Torch and from 6.88 to 7.39 seconds for ONNX, a
3.05% and 7.45% cost for this checkpoint. The artifact keeps every cold and warm
timing sample, parent CPU measurement, output digest and source hash separately.

The [fifth comparison](data/python-typing-fifth.json) checks more abstract-class
construction errors, distinguishes `NewType` factories from class values, and
extends generic constructor, receiver, and callback-parameter inference. Its
complete final replay against `87c47a6` reaches 5,293 exact matches, up from
5,262, with 928 differences, 7,200 unknowns, 77,581 explicit exclusions, and zero
replay errors. Both engines use the same frozen adapter, source fixtures, and
Python 3.13 Pyright oracle.

Twenty-four differences and eleven unknowns become matches. Four former matches
become unknown: three protocol return-type assertions and one partial-function
argument error. The partial loss is an avoidable scope-analysis limitation: a
function parameter is confused with a module variable of the same name. That
previously caught invalid call remains explicit follow-up work. The protocol
cases also remain open; unknown results do not satisfy their assertions. Two new
diagnostic differences reject abstract classes that the recorded native Python
3.10 and 3.13 executions also reject. Those oracle disagreements retain their raw
failure status.

Constructor and partial-function inference now declines unproven contracts when
visible mutation or alias escapes can change them. The evidence separates the
initial candidate, rejected drafts, final safety repairs, and the complete final
replay. It also records 2,128 passing tests on Python 3.13 and 2,120 passing tests
with eight skips on Python 3.10, alongside package, public-project, compatibility,
and ML validation on the exact final source. These gates do not turn the 928
remaining corpus differences into accepted results.

The final quiet ABBA run preserved findings and member metadata for both
generated size-16 workloads. Median warm time was 2.804 → 2.789 seconds for Torch
and 7.433 → 7.410 seconds for ONNX. These approximately 0.5% and 0.3% differences
are effectively unchanged in this limited sample, not evidence of a speedup.
The artifact retains every cold and warm sample, parent CPU time, workload and
output digest, and exact source hashes.

CI checks 133 resolved assertions. Four typing fixtures contribute 36 assertions:
26 valid calls and 10 rejected calls. It requires the exact fixture counts
17, 8, 5, and 6 for
`dataclasses_kwonly.py`, `dataclasses_transform_class.py`,
`dataclasses_transform_field.py`, and `generics_basic.py`, respectively, with
zero unknowns, differences, or replay errors.

Thirty-eight additional fixtures from ty, Pyright, mypy, and Pyrefly contribute 97
resolved assertions, including 33 expected diagnostics for calls and assignments.
Their identities, source digests, positive/negative counts, return assertions,
and 42 explicit exclusions are
pinned in [`python_typing_selection.json`](../scripts/python_typing_selection.json).
CI generates independent expectations for the six selected Pyright files using
the pinned Pyright release. Missing, duplicate, or changed fixtures and newly
unknown assertions fail the gate; exclusions never count as matches. Repeat it
with `--selection scripts/python_typing_selection.json --require-resolved`,
supplying the corpus and Pyright oracle as above.

The full audit remains failing and supplies a work inventory beyond these
regression gates. Each comparison records its adapter hashes; comparisons across
different adapter versions are not valid improvement counts.

To repeat the fifth comparison from this checkpoint, prepare the pinned corpus
and Pyright oracle as above, then run the same adapter on its baseline and `HEAD`:

```sh
mkdir -p /tmp/python-typing-before /tmp/python-typing-after
git archive 87c47a6 src | tar -x -C /tmp/python-typing-before
git archive HEAD src | tar -x -C /tmp/python-typing-after

PYTHONPATH=/tmp/python-typing-before/src .venv/bin/python \
  scripts/check_python_typing.py --corpus /tmp/python-typing-corpus \
  --pyright-oracle /tmp/pyright-native.json --output /tmp/typing-before.json
PYTHONPATH=/tmp/python-typing-after/src .venv/bin/python \
  scripts/check_python_typing.py --corpus /tmp/python-typing-corpus \
  --pyright-oracle /tmp/pyright-native.json --output /tmp/typing-after.json
.venv/bin/python scripts/compare_python_typing.py \
  --baseline /tmp/typing-before.json --candidate /tmp/typing-after.json \
  --output /tmp/typing-comparison.json
```

Both full replay commands return nonzero while differences remain. The comparison
also returns nonzero for newly failing expected-valid assertions, including
return differences, so its records need review rather than automatic acceptance.
