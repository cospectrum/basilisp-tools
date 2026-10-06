# Python typing corpus audit

`scripts/check_python_typing.py` translates Python consumer expressions from pinned
typing, Pyright, mypy, Pyrefly, and ty fixtures into Basilisp interop expressions.
It checks call diagnostics and structured return types. This is an interop audit;
it does not claim that Basilisp implements Python's declaration, assignment, or
control-flow type system.

The source pins and released oracle versions are in
[`python_typing_sources.json`](../scripts/python_typing_sources.json). The ty
fixtures live in the Ruff repository, where ty is developed. Source revisions
and released checker versions are recorded separately: a current source snapshot
can include cases added after the latest release.

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
pyright --outputjson \
  /tmp/python-typing-corpus/microsoft-pyright/packages/pyright-internal/src/tests/samples \
  > /tmp/pyright-native.json

python scripts/check_python_typing.py --corpus /tmp/python-typing-corpus \
  --provider pyright --pyright-oracle /tmp/pyright-native.json \
  --output /tmp/pyright-interop.json
```

Native Pyright is expected to exit nonzero because the corpus contains invalid
examples. The report records that oracle's version and source digest. Its profile
is the CLI defaults plus in-file directives; the upstream TypeScript runner's
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
- `error`: the replay itself raised an exception.

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
| Unknown contracts or types | 7,625 | 7,968 |
| Explicit exclusions | 76,950 | 76,950 |
| Replay-error assertion rows | 7 | 0 |

| Provider | Fixtures | Exact matches before → after | Remaining differences | Unknown | Excluded |
| --- | ---: | ---: | ---: | ---: | ---: |
| typing | 152 | 335 → 347 | 59 | 384 | 1,910 |
| Pyright | 1,375 | 866 → 883 | 303 | 1,934 | 6,531 |
| mypy | 8,327 | 678 → 684 | 234 | 648 | 25,451 |
| Pyrefly | 5,136 | 856 → 873 | 237 | 1,536 | 11,127 |
| ty | 6,812 | 1,146 → 1,162 | 573 | 3,466 | 31,931 |

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

Each provider uses the same frozen adapter, fixture content, and oracle on both
engines. Mypy and Pyrefly were replayed after corrections to configuration and
Rust invocation inventory; the artifact records the two adapter segments
separately. The candidate snapshot contains the static typing implementation
in `f21a456`. A subsequent runtime-only native-stub reexport safety fix changed
the production worker hash; that delta and its separate native validation are
recorded. Fixture dependency execution is disabled, so this static replay does
not exercise that changed runtime path. Audit timings were collected under
concurrent load and are not performance measurements.

The historical reports retained expression-row findings, but did not retain the
whole document's findings. Their zero candidate error-row count therefore does
not prove zero inspection failures: a static metadata worker failure on the
namespace row could have been omitted. Direct analyzer exceptions were recorded,
because this replay calls the analyzer without the CLI's exception-to-finding
wrapper. The current harness retains all document findings and makes syntax,
file, internal-analysis, and Python-inspection failures fatal before checking
any expression, including expected-invalid calls. The historical differential
One fixture failure can mark several assertions as errors. The historical
counts above retain this evidence limitation; they are not a complete audit of
document-level failures.
Scanning the retained findings also found ten repeated-keyword syntax
diagnostics per engine classified as unknown. The current adapter explicitly
excludes those transformations, as described above.
A full comparison using the guarded adapter and exact `f21a456` production
sources is being repeated. Its results are not included in the table above.

CI checks four typing fixtures with 36 resolved assertions: 26 valid calls and
10 rejected calls. It requires the exact fixture counts 17, 8, 5, and 6 for
`dataclasses_kwonly.py`, `dataclasses_transform_class.py`,
`dataclasses_transform_field.py`, and `generics_basic.py`, respectively, with
zero unknowns, differences, or replay errors. The full audit remains failing
and supplies a work inventory beyond that regression gate.

To repeat the comparison, prepare the pinned corpus and Pyright oracle as above,
then run the current adapter on both source trees:

```sh
mkdir -p /tmp/python-typing-before /tmp/python-typing-after
git archive 4240b2e src | tar -x -C /tmp/python-typing-before
git archive f21a456 src | tar -x -C /tmp/python-typing-after

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
