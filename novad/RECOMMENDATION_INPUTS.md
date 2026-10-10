# Pure evaluator input contract — v0.11B1

`recommendation.py` implements policy v1 from `RECOMMENDATION_POLICY.md`.
v0.11B2 imports it through `recommendation_integration.py`, a narrow read-only
adapter used by `GET /recommendations/models`. It adds no provider, UI,
benchmark reader or calibration runner. This contract underpins runtime v0.11.0.

## Calling convention

```python
from recommendation import Candidate, Request, Policy, evaluate, InputError

# Caller constructs normalized frozen dataclasses; no raw API dictionary parsing.
result = evaluate(
    candidates=(),
    request=Request(workload="general-text-v1", environment="local-config-v1"),
    comparisons=(),
    policy=Policy(),
)
assert result["status"] == "NO_DEFENSIBLE_RECOMMENDATION"
```

All input types are immutable dataclasses containing primitives, other frozen
dataclasses and tuples. The evaluator validates types (including bool versus int),
enums, nonnegative quantities, context ranges, duplicate identities, comparison
references and timezone-aware ISO timestamps. Unknown nullable values remain None.
Malformed caller input raises `InputError` (a ValueError); missing usable evidence
returns an ordinary abstention result. Each tuple is bounded to 256 entries and
each nonempty string to 4096 characters. No hidden clock, randomization or I/O.

Policy v1 defaults are minimum 4096 and target 32768, pinned to the existing fit
policy at implementation. A future adapter should pass active `HeadroomPolicy`
context values into `Policy`; no fit/hardware modules are imported here. Explicit
request overrides must satisfy 0 < minimum <= target. No native-maximum clamping
reduces the requested minimum.

## Normalized inputs

| Structure | Responsibilities |
|---|---|
| Artifact | Exact provider, model_id and nullable revision; labels are opaque. |
| Candidate | catalog_id, artifact, verified identity, nullable installed/provider state, catalog revision/check time, fit, capability declarations, task evidence, optional calibration, storage/acquisition. |
| Fit | Rating/confidence/support, artifact revision, workload/environment, native and sustainable context, headroom, conditional offload, modality, timestamp and policy revision. Existing API outputs need explicit normalization; no recalculation occurs. |
| Capabilities | Nullable chat/tools/thinking/coding/vision/instruction_following declarations; no truth-value coercion. |
| Evidence | Reference, method, source class, FACT/QUALITY_JUDGMENT, exact artifacts, task profile/workload, confidence, applicability, timestamp, corroboration, supporting references and optional environment. |
| TaskEvidence | Nullable suitability for the stated task; directly evidenced supported/reliable capabilities and supporting Evidence. |
| Comparison | Two catalog IDs, TASK/GENERAL_QUALITY/PERFORMANCE/SPECIALIST dimension, LEFT/RIGHT/EQUIVALENT outcome, applicable Evidence and plain explanation; SPECIALIST also names its narrow scope. |
| Request | GENERAL/CODING/DEEP_REASONING/FAST_BACKGROUND/VISION, installed/known scope, workload/environment, context overrides, required capabilities, allow_tight (default false), optional calibration conditions/budget and specialist scope. |
| Acquisition | FEASIBLE/BLOCKED/UNKNOWN plus download, incremental, staging, reserve and available bytes; accounting reference and sampled timestamp. |

Workload/environment identifiers are caller-defined exact scope signatures, not
inferred hardware facts. A future adapter must include relevant modality,
concurrency, context/cache/runtime settings when constructing them. Fit identity,
revision and workload/environment must match the candidate/request. Known models
need their own feasibility assessment; passing installed=true to reuse the current
installed-only fit function is prohibited.

Evidence source classes are LOCAL_FACT, UPSTREAM_METADATA, CURATED,
REPRODUCIBLE_BENCHMARK and LOCAL_CALIBRATION. Quality requires an applicable
QUALITY_JUDGMENT from benchmark/calibration or a curated judgment with supporting
references to such evidence. Performance comparisons require empirical FACT
evidence matching the environment. Evidence references are inert: nothing is
opened or fetched. This module checks normalized evidence applicability and
consistency, not whether a caller's claimed measurement actually occurred.

Suitability is positive task evidence, not a catalog capability flag. Required
tool use additionally requires evidenced tool reliability. VISION requires vision
compatibility and a vision-scoped fit or qualifying vision calibration. Other
profiles select their own evidence; FAST_BACKGROUND puts measured performance
before general quality once task suitability and task comparison are established.

## Calibration evidence consumption only

Calibration contains a named/versioned supplied protocol and acceptance-criteria
reference, measurement source, repetition requirement, exact Conditions, Budget,
configured/usable context, prompt/output allowances, all-trials-recorded assertion,
conflict flag and Trial records. There is no protocol registry or execution code.

Conditions include hardware, backend/version, device/offload, cache precision,
runtime options, modality and concurrency. They must exactly match Request.
Budget carries the active fit policy revision, operating memory after system
reserve, required operating margin, and required system reserve. It must match the
request and fit policy revision. Callers must supply the real centralized reserve
policy values, not zeros or a smaller invented budget requirement.

The supplied protocol must require at least two trials (the minimum meaning of
repeated evidence), and all its required trials must be present with unique
references. Every trial must complete, reach prompt/output allowances, stay within
configured context, preserve operating and system margins, and report no OOM,
allocation/offload/contention failure. Prompt plus output allowance must cover
the claimed usable context. An incomplete/failed/mismatched record does not qualify.
There is no new universal runtime duration, throughput threshold or KV formula.
The normalizer remains responsible for truthful protocol coverage and retaining
all failures; the evaluator cannot independently certify arbitrary protocols.

UNKNOWN fit may then use OBSERVED_QUALIFIED context, capped at MEDIUM confidence.
The original static fit/context/headroom remain unchanged in the report. POOR,
UNSUPPORTED, revision conflicts or missing compatibility cannot be bypassed.
A resident sample alone cannot be represented as a qualifying Calibration.

## Ordering, confidence and output

After eligibility gates, comparisons follow sustainability band, context band,
task evidence, general quality, performance and finally storage. GOOD/EXCELLENT
share a band; explicitly permitted TIGHT remains below them. Greater context beyond
target earns no advantage. Missing/contradictory comparisons are incomparable,
not equivalent. Lower-priority differences cannot resolve that uncertainty.

A balanced winner must beat or be evidenced equivalent to every other selectable
candidate. Cycles/incomparable contenders produce abstention. Real final ties
produce coequal candidates sorted by catalog ID for display only. Selection is
independent of candidate/comparison input order. There is no composite score.

MAX_QUALITY and FASTER compare against the balanced choice and retain all minimum
context/eligibility gates. SPECIALIST requires an explicitly requested narrow scope
and evidence of advantage; it may succeed among otherwise incomparable candidates.
Alternatives are compared within their tier as well. A candidate occupies one
tier; when it qualifies for several, presentation priority is balanced, quality,
speed, specialist. This is not an additional model-quality ranking.

Confidence uses the weakest decisive supplied evidence, with caps: conditional
offload/observed qualification at MEDIUM; absent decisive quality timestamp at LOW;
uncorroborated evidence at MEDIUM. Missing task suitability yields INSUFFICIENT.
Known scope with unavailable provider is conditional and capped at LOW. Catalog
timestamps are preserved independently; they do not refresh quality evidence or
expire immutable facts. Confidence never depends on a numerical winning margin.

Report fields: schema_version, policy_version, profile/scope, request snapshot,
effective minimum/target, status, confidence, tiers, candidates, excluded and
limitations. Each row includes eligibility/selectability, reason codes, fit state,
usable context/source/band, why/trade_offs, evidence references, installation state
and detached original inputs. `excluded` includes every nonselectable row, including
UNRESOLVED gates and feasible candidates lacking suitability evidence; eligibility
distinguishes those cases. No input object is mutated.

Statuses: RECOMMENDATION_AVAILABLE, SPECIALIST_ONLY or NO_DEFENSIBLE_RECOMMENDATION.
Each tier has candidates (possibly empty) and an abstention_reason. Top-level
confidence describes the balanced selection, or the specialist-only selection.
Tier candidates retain their own confidence when an alternative has weaker evidence.

Installed scope requires verified installation and online provider. Known scope
preserves non-installed identity; an actionable non-installed tier requires complete
FEASIBLE acquisition accounting. Insufficient disk yields BLOCKED even if the caller
supplied FEASIBLE; missing accounting yields UNKNOWN. Neither permits a tier.
No shared-blob savings, download, installation or deletion is inferred/performed.

## Validation and proposed v0.11B2 boundary

`python -B -m unittest -v test_recommendation` runs symbolic A–J plus uncertainty,
profiles, confidence, revision, calibration, storage, order and no-I/O tests.

A separately authorized B2 adapter should join existing catalog/inventory/fit
snapshots by exact identity, normalize this contract, preserve unknowns and pass
the active fit policy values. It should expose typed input errors and abstention
as distinct results, sanitize presentation references/condition identifiers and
avoid forwarding the internal diagnostic input snapshots wholesale into a public
API. It must not fabricate comparisons from the current seed's declarations.
## v0.11B2 live adapter

`recommendation_integration.py` accepts already collected `catalog_view` and
`local_model_fit` snapshots, then makes exact provider/identifier joins only. It
uses a fit result only when one current model entry exists. A catalog revision is
attached to that fit only after the local manifest observation reports `exact`.
`revision_mismatch`, `ambiguous` and `unknown` matches keep fit revision unknown;
quality/runtime evidence is not transferred by tag or family.

The adapter carries catalog capability nulls, provenance, checked timestamps,
installed-match state and fit uncertainty into evaluator candidates and the API
response. It adds no TaskEvidence, Comparison, benchmark score, calibration or
acquisition claim. Catalog v1 therefore normally returns
`NO_DEFENSIBLE_RECOMMENDATION`: this is an advisory successful result, rather
than an absence of installed models. Non-installed seed records remain visibly
non-installed and have unknown independent fit/acquisition state.

`GET /recommendations/models` accepts only these optional query parameters:

| Parameter | Default | Semantics |
|---|---|---|
| `profile` | `GENERAL` | Case-insensitive `GENERAL`, `CODING`, `DEEP_REASONING`, `FAST_BACKGROUND` or `VISION`. |
| `scope` | `installed` | `installed` or `known`; it does not perform discovery outside the local catalog. |
| `allow_tight` | `false` | Exact `true`/`false`; true permits conditional TIGHT eligibility but supplies no quality evidence. |
| `required_context` | policy default | Positive integer through 1,048,576; becomes both hard minimum and target context. |

Repeated/unknown parameters and malformed values return HTTP 400 before any
snapshot read. Other NOVA routes still reject query parameters. Catalog
unavailable/invalid returns an HTTP 200 diagnostic response with
`RECOMMENDATION_UNAVAILABLE`; unavailable fit data produces a normal conservative
abstention where catalog data remains inspectable. The endpoint returns no model
paths, raw Ollama payloads, approval data or mutable state.

The adapter itself is pure: it performs no I/O. The server obtains the same
bounded local manifest view used by the catalog API and the same existing
loopback-only Ollama fit snapshot used by `/models/fit`; it makes no external
network request. It never switches, loads, downloads, installs or removes models,
creates approvals, or writes catalog/config/model state.

No adapters, HTTP handlers, UI, benchmark ingestion or calibration framework were
implemented in B1. B2 is limited to the adapter and advisory endpoint. Current
seed-only evidence is expected to abstain.
