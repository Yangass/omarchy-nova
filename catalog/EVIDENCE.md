# NOVA evidence foundation — schema v1 / v0.11C

Storage: `catalog/evidence.json`. Validator/loader: `novad/evidence.py`.
Runtime is v0.11.0. This foundation is separate from catalog schema v1,
installed inventory, hardware fit and recommendation ranking. The advisory API
loads it through a narrow read-only integration layer. No provider, network,
runtime, installer or calibration interface is imported by the evidence module.

## Facts and judgments

A FACT records a declaration or measurement, not a quality assessment. A provider
declaring tools does not demonstrate correct tool use; native context does not
establish sustainable context. A reported benchmark value remains scoped to that
benchmark and protocol. Source claims are not independently verified by parsing.

A QUALITY_JUDGMENT is a curated comparison with explicit model subjects, task,
scope, rationale and references to valid measurement records in this file.
There is no naked `quality` number, global score, averaging, ranking or automatic
conversion from parameter count, name, popularity, downloads or installed state.
Unknown values remain null. A missing judgment is absent, not a tie or failure.

## Envelope and record schema

The authoritative executable schema is `validate_evidence(value)`.

| Envelope field | Contract |
|---|---|
| schema_version | Required integer 1; booleans and other versions are rejected. |
| evidence_revision | Required maintainer-supplied revision string, independent of catalog/runtime version. |
| records | Required list, at most 256 records; an empty valid list is allowed. |

| Record field | Contract |
|---|---|
| evidence_id | Unique opaque lowercase ID, maximum 128 characters, letters/digits/dot/underscore/hyphen. |
| evidence_type | `upstream_declared_capability`, `benchmark_result`, `local_calibration`, `curated_comparison`, `observed_runtime_fact`. |
| claim_kind | FACT for declarations/measurements; QUALITY_JUDGMENT only for curated comparisons. |
| models | One subject for a fact, two ordered distinct subjects for a comparison. Each has catalog_id, provider, model_id, nullable revision and variant. Identifiers are exact/case-sensitive; no inference or fuzzy matching. |
| profiles | Nonempty subset of GENERAL, CODING, DEEP_REASONING, FAST_BACKGROUND, VISION. Describes lookup relevance, not task suitability. |
| source | Required reference; nullable version, observed_at and published_at. Timestamps use UTC `YYYY-MM-DDTHH:MM:SSZ`. |
| trust_class | Required class from the table below. UNKNOWN is explicit. |
| confidence | HIGH/MEDIUM/LOW/INSUFFICIENT or null; claim confidence, not quality. |
| provenance | Required method; references list (default empty); nullable corroborated boolean. |
| scope | Nullable workload, conditions, comparison_group strings. All three are required nonempty strings for a judgment. |
| facts | Declaration payload or null. Nullable capabilities and reported model properties below. |
| measurement | Measurement payload or null. |
| comparison | Curated comparison payload or null. |
| notes | Nullable bounded explanation/limitations. |

Only the payload associated with the evidence type can be non-null. Unknown keys
are rejected rather than silently ignored. Omitted optional values normalize to
null. All nonempty strings are bounded to 4096 characters, subordinate lists to
32 entries, and the file to 1 MiB. JSON duplicate keys and non-finite numeric
constants are invalid. Numeric strings and booleans are not measurements.

Declaration `facts.capabilities` contains nullable chat, tools, thinking, coding,
vision and instruction_following. `facts.properties` contains nullable positive
integer native_context, weight_size_bytes and parameter_count, plus nullable
architecture and quantization strings. This records the fact that these values
were declared; it does not prove observed task performance or current installation.

Measurement fields are benchmark (required for benchmark_result, otherwise nullable),
version, metric (required), value (finite number or null), direction
(`higher_is_better`, `lower_is_better`, or null), and unit (nullable). Zero remains
zero. Negative finite measurements are allowed because metric scales vary.
Unknown versions/conditions may be stored, but cannot establish comparability.

`local_calibration` stores an already-existing measurement/protocol reference only.
It is not the complete `recommendation.Calibration` trial contract, does not qualify
UNKNOWN fit, and never causes a measurement or inference run. An observed runtime
fact similarly cannot certify sustained full-context use.

## Trust, provenance and freshness

| Trust class | Meaning and limit |
|---|---|
| LOCAL_OBSERVED | Captured locally; may observe that a provider made a declaration. Does not imply independent upstream verification or quality testing. |
| TRUSTED_UPSTREAM | Maintainer-identified upstream source with explicit reference; no automatic official-source claim. |
| CURATED_NOVA | Maintainer interpretation with rationale and factual supporting records. Trust does not substitute for those records. |
| REPRODUCIBLE_BENCHMARK | Reported benchmark with an identified protocol; applicability and reproducibility still require review. |
| UNKNOWN | Source trust has not been established; cannot support a recommendation confidence claim. |

Classes identify provenance, not a universal trust ladder. `confidence_ceiling`
is a pure upper bound on a validated record's claim confidence: UNKNOWN trust or
null confidence gives INSUFFICIENT; lack of corroboration caps at MEDIUM; missing
observation time caps at LOW, matching the evaluator's conservative time handling.
It never upgrades confidence or generates quality. A future normalizer must also
cap a judgment by the confidence/applicability of its supporting facts.

observed_at describes the source observation; published_at describes publication
when known. Copying a record does not refresh either. Evidence revision and catalog
revision are independent. No TTL, current/stale labeling, wall clock, automated
refresh or network verification exists. Even a newly checked declaration is not
fresh quality evidence. Retain missing dates and unknown conditions explicitly.

## Benchmark and curated comparison rules

A comparison names dimension TASK/GENERAL_QUALITY/PERFORMANCE/SPECIALIST, outcome
LEFT/RIGHT/EQUIVALENT, a nonempty rationale and supporting_evidence_ids.
LEFT and RIGHT refer to the ordered `models` subjects, never file order or rank.

The second validation pass requires supporting benchmark_result/local_calibration
FACT records that cover both subjects, including exact revision and variant data
as recorded. References to absent, malformed, duplicate, self or other judgment
records cannot qualify. Declarations alone cannot support quality judgments.

Schema v1 deliberately bounds comparisons to one measurement definition:
same benchmark/protocol name, known version, metric, unit, direction and known
value; same explicit workload, conditions and comparison_group; every comparison
profile covered by each fact. Cross-suite/version/condition comparisons are
rejected. Separate unrelated benchmark facts remain valid and inspectable, with
their original values; no normalization or averaging is performed.

Passing these checks only establishes a traceable, structurally compatible claim.
The loader does not interpret benchmark methodology, compute winner/tolerance,
certify artifact applicability or certify truth. Conflicting curator judgments are
retained separately; a future evaluator adapter must not choose whichever appears
first. Unknown subject revisions are allowed for honest archival records; future
recommendation use still requires verified exact artifact applicability.

## Seed provenance and gaps

`v0.11C.seed1` contains four declaration records, one each for qwen3.5:35b,
qwen3:30b, qwen3:14b and qwen3:8b. Values and manifest digests are transcribed from
`catalog/models.json`, revision `2026-09-08.seed1`. The original observation time
`2026-09-08T12:21:36Z` is retained; no fresh Ollama observation was performed.

The existing catalog provenance identifies local Ollama tags/show/manifests.
These references remain traceable through source.reference/source.version and
provenance.references. LOCAL_OBSERVED/MEDIUM describes this transcription of local
declarations; it does not claim those original API responses were independently
rechecked or that the upstream publisher verified them.

Included: native context, artifact size, parameter metadata, architecture,
quantization and nullable capability declarations. Missing: coding/instruction
quality, tool/reasoning reliability, latency/throughput, comparable benchmarks and
sustained runtime calibration. No benchmark values or judgments are seeded.
All five profiles can look up these facts and their unknown capabilities; relevance
does not assert that a model can perform every task.

No defensible quality ordering is added. GENERAL may still correctly return
NO_DEFENSIBLE_RECOMMENDATION with INSUFFICIENT confidence.

## Loader/query behavior and future integration contract

```python
from evidence import load_evidence, confidence_ceiling

store = load_evidence()  # bounded read, only when explicitly called
rows = store.for_model(
    'qwen3-14b-q4-k-m', 'CODING', provider='ollama',
    model_id='qwen3:14b', revision='bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8',
)
judgments = [r for r in rows if r['claim_kind'] == 'QUALITY_JUDGMENT']
```

`EvidenceStore.status` is ready/partial/invalid/unavailable; revision and safe error
messages are separate from records. Missing/unreadable file is unavailable; bad
JSON/encoding/size/schema/envelope is invalid. A malformed record is skipped with
its index reported, retaining valid siblings. All valid records sharing a duplicate
evidence_id are excluded, and dependent comparisons fail reference validation.
Records and lookups return detached copies sorted by evidence_id for deterministic
display only. Optional omitted lookup filters do not certify identity; a missing
model/task/revision returns an empty tuple. Unsupported task profiles raise ValueError.

No evidence load occurs on import. Loading reads only the explicitly selected file
in binary mode; queries, normalization and confidence bounds perform no I/O. No
references are followed and no catalog/runtime state is imported or changed.

The v0.11.0 integration applies these boundaries:

1. Join catalog/provider/model/revision/variant exactly and select task-relevant
   evidence. Preserve original facts, source dates and unknowns.
2. Review conditions, artifact applicability, source integrity, conflicting claims,
   tolerances and supporting records. A valid schema is not sufficient evidence.
3. Map applicable trust/provenance to evaluator Evidence sources: local facts to
   LOCAL_FACT, upstream declarations to UPSTREAM_METADATA, curated judgments to
   CURATED with supporting references, benchmarks to REPRODUCIBLE_BENCHMARK and
   actual local measurements to LOCAL_CALIBRATION. UNKNOWN remains insufficient.
4. Emit TaskEvidence/Comparison only for claims whose evidence establishes the
   required task correctness/reliability or comparison. Never turn declarations
   into suitable=true or benchmark values into a global quality score. Apply the
   minimum confidence bound across the decision's required evidence.
5. Retain all hardware/context gates and the full existing calibration contract.

`recommendation_integration.py` loads the seed explicitly and reports bounded
per-candidate summaries. It joins only exact catalog/provider/model/revision and
profile matches. Schema-v1 FACTS are reported but are not promoted to task
suitability, quality, speed or a recommendation. The only evaluator input it can
currently construct is a validated curated comparison whose artifact revisions,
profile, workload and environment all match. The current seed contains none, so
the live API correctly abstains. Unit tests cover seed fidelity, validation/
isolation, nulls, benchmark scope, reference integrity, lookup, determinism,
read-only behavior and this integration boundary.

## Future evidence work

Future work may curate defensible, reproducible task correctness/reliability,
comparison tolerances and calibration coverage. It does not authorize automated
benchmark fetching, evidence refresh, downloads or installation.
