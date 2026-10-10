# NOVA recommendation policy v1 — v0.11A

Status: implemented as the policy for the v0.11.0 advisory recommendation
foundation. This document remains the normative evaluator contract. Catalog
schema v1 and the existing fit engine remain independent inputs.

## Purpose and boundaries

Recommend the best supported sustainable option with strong usable context and
adequate system headroom. Parameter count, artifact size and model names are not
quality or speed measurements. “Recommended” means best supported under the
specified evidence, hardware, task and policy, never objectively best worldwide.

Keep four independent inputs/outputs: inventory describes installed artifacts;
catalog describes known artifacts and evidence; fit assesses operating feasibility;
recommendation compares eligible choices for a task. Recommendation must not
rewrite fit ratings, catalog declarations, inventory or active configuration.
No recommendation confers action authority. Selection, approvals, request
snapshots and provider boundaries remain unchanged.

## Decision factors

| Factor | Role |
|---|---|
| Artifact identity, backend compatibility, sustainable memory, required context and mandatory modalities | Eligibility gates; unresolved gates block a normal recommendation. |
| Sustainable context relative to the task target | Primary ranking band after sustainability gates. |
| Task-relevant tool/reasoning reliability and capability | Primary comparisons within a sustainable/context band, using applicable evidence. |
| General quality | Secondary comparison using comparable evidence, never inferred from fit. |
| Measured performance | Task dimension for FAST_BACKGROUND; otherwise a tie-breaker and basis for FASTER. |
| Storage cost | Acquisition feasibility gate; otherwise last substantive tie-breaker. |
| Provenance, coverage, freshness, uncertainty | Confidence modifiers and limits on which comparisons are justified. |
| Installed/active state | Scope or convenience information, never quality evidence. |

There is no weighted sum. GOOD and EXCELLENT belong to the same normal
sustainability band: surplus VRAM must not defeat a demonstrated capability
advantage once both meet context and headroom requirements. TIGHT belongs to a
conditional band, below normal sustainable candidates for the default choice.

## Task profiles and context requirements

GENERAL is the default. Profiles contain desired capabilities, not model names.
All profiles require chat compatibility. Additional required capabilities are
explicit request inputs; tools are mandatory only for a tool-using task.
Declared support establishes compatibility, not reliability or quality.

| Profile | Evidence relevant to suitability and comparison |
|---|---|
| GENERAL | Instruction following, representative conversation/task completion; tool reliability when tools are requested. |
| CODING | Reproducible code correctness, edits and tests on relevant languages/tasks; tool reliability for an agentic coding request. A coding tag alone is insufficient. |
| DEEP_REASONING | Reproducible reasoning correctness on relevant problems; thinking support alone is not evidence of better reasoning. |
| FAST_BACKGROUND | Acceptable correctness on the requested bounded task, then measured latency/throughput at matching workload and context. Smaller weights do not prove faster execution. |
| VISION | Confirmed vision support and representative image-task evidence. Text-only fit cannot establish vision workload sustainability. |

Every request carries minimum_context and target_context, with target >= minimum.
If unspecified, inherit the centralized fit policy defaults (currently 4096 and
32768 tokens). These are initial operating preferences, not universal task needs.
An explicit task may choose different values. Do not lower a requested minimum
to a model's native maximum. Report inadequate or unsupported context instead.
The current fit engine's native-capped minimum is not the recommendation gate.

Distinguish native_context, estimated_sustainable_context and
observed_validated_context. Advertised/native limits are never sustainability
evidence. A configured runtime context is not proof that actual workloads reached
that token occupancy. Keep each value, its scope and evidence references separate.

Context bands are TARGET_MET, MINIMUM_ONLY and UNRESOLVED/BELOW_MINIMUM.
Within the same sustainability band, TARGET_MET precedes MINIMUM_ONLY. Greater
context beyond the target earns no preference without a task requirement.
MINIMUM_ONLY must disclose reduced context. An explicit task-specific tier may
trade target context for demonstrated capability, but may never violate minimum.
No universal KV-cache formula is introduced. Unsupported architecture, image
workload or concurrency estimates remain unknown.

## Eligibility and conditional cases

Eligibility is ELIGIBLE, CONDITIONAL, EXCLUDED or UNRESOLVED, with reason codes.
It is separate from fit rating, recommendation confidence and rank.

| Fit | Policy |
|---|---|
| EXCELLENT / GOOD | Normally eligible when required context, compatibility and task requirements are established. Conditional offload assumptions remain visible and limit confidence. |
| TIGHT | Conditional only; minimum context must hold. Default RECOMMENDED prefers an eligible GOOD/EXCELLENT alternative. May appear as a disclosed trade-off or fallback only when request allow_tight is explicitly true. Default false. |
| POOR | Excluded from v1 recommendations, irrespective of quality. A future revised fit assessment is needed; recommendation cannot override it. |
| UNSUPPORTED | Always excluded. |
| UNKNOWN | Unresolved unless a qualifying observed sustainability record covers the requested workload; see below. Never promoted by weight headroom alone. |

Explicitly false mandatory capabilities exclude. Unknown mandatory compatibility
is unresolved unless applicable direct evidence establishes it; retain the original
catalog null. Missing comparative quality does not imply inability to run, but
can leave every tier empty. Offline provider blocks an executable installed-choice
recommendation, not a declaration that the artifact is unsupported. Known-choice
evaluation may remain conditional on provider availability.

Exact provider/identifier matching is required. Artifact-specific evidence must
also match the revision. A revision mismatch does not mean broken: exclude that
stale evidence from the installed artifact's assessment. Independent evidence for
the actual revision may still qualify it. Ambiguous identity is unresolved;
never transfer quality or runtime evidence by name/parameter scale guessing.

### Observed qualification of UNKNOWN

Use OBSERVED_QUALIFIED as a recommendation evidence status; keep static fit
UNKNOWN. Qualification requires a locally recorded, reproducible calibration
protocol with all of these fields established:

- Exact artifact revision and relevant hardware, backend/version, device/offload,
  cache precision, context settings, modality, concurrency and runtime options.
- Representative completed workloads reaching the claimed usable token occupancy,
  including declared prompt/output allowance; repeated trials under a named,
  versioned protocol with its repetition count and acceptance criteria recorded.
- Peak runtime memory and remaining system/device headroom measured through the
  workload, preserving the active fit policy's system reserve and operating margin.
- Successful completion without OOM, failed allocation, undeclared offload or
  unacceptable contention under the protocol; failures are retained, not omitted.
- Observation times, measurement sources, tested bounds and a protocol result
  that can be checked from the recorded evidence, not a bare trusted boolean.

v0.11B may consume such records but must not run calibration or invent a protocol
pass. Until a protocol is specified and its evidence supplied, qualification is
unavailable. A single Ollama /api/ps sample, preload success, ordinary short chat,
configured 32K context or current low VRAM use does not meet this contract.
No model-family exception exists. Evidence applies only within its tested bounds;
material changes invalidate applicability. Conflicting estimate/observation is
unresolved pending investigation, not resolved by choosing the optimistic value.
Qualified UNKNOWN may enter the normal sustainability band with confidence capped
at MEDIUM, without manufacturing an estimated context or memory value.

## Evidence and confidence

Evidence records must distinguish claim_kind FACT or QUALITY_JUDGMENT, the
specific claim, source class, artifact/workload scope, method, reference,
observation time and applicability. Different source classes support different
claims; they are not a universal trust ladder.

| Class | What it can support |
|---|---|
| Local observed facts | Installed revision, runtime settings, measured memory; a sample alone proves neither sustained usability nor quality. |
| Trusted upstream metadata | Declared architecture, supported modalities, limits and license; upstream marketing is not benchmark evidence. |
| Curated NOVA declarations | Maintainer assertions with provenance. Current seed capabilities are declarations, not reliability measurements. |
| Reproducible benchmarks | Comparative task quality under an identified dataset, protocol, version and compatible settings; include limitations and uncertainty. |
| Empirical local calibration | Workload-specific correctness, reliability, speed and sustained resource observations under an explicit reproducible protocol. |

Facts such as “tools supported” do not establish tool-call correctness. Quality
claims need supporting benchmark/calibration evidence or a curated judgment
traceable to such evidence. No precise quality numbers may be invented from the
current seed. Different benchmark suites/settings cannot silently be averaged.
Missing evidence remains null/UNKNOWN; it is never false, zero or proof of parity.

Confidence is separate from rank and uses the weakest decision-critical evidence:

- HIGH: all decisive gates and comparisons have applicable, reproducible,
  corroborated evidence with adequate coverage and no unresolved material conflict.
- MEDIUM: all decisive claims are supported, but some rely on bounded estimates
  or limited replication. Current medium-confidence fit estimates cap the result.
- LOW: positive task-suitability evidence exists and gates are established, but
  comparative coverage or provenance is limited. A first-ranked option may be LOW.
- INSUFFICIENT: a gate or the evidence needed to support preference is unresolved,
  contradictory or absent. Do not fill a recommendation tier.

Catalog checked_at describes a catalog observation, not a quality reassessment
or a newly installed artifact. Carry catalog revision, evidence dates and source
references into the explanation. No arbitrary TTL or automatic refresh exists.
Missing freshness/applicability metadata on decisive quality evidence caps
confidence at LOW; known revision/workload incompatibility makes it unusable.
Age alone neither invalidates immutable facts nor proves current comparative
coverage. A recent timestamp alone never increases quality confidence.

## Ranking and tiers

Within each scope, filter gates first, then compare sustainability band, context
band, task-relevant reliability/capability, general quality, measured performance,
and finally storage cost. For FAST_BACKGROUND, after a positively evidenced task
correctness floor, measured performance precedes further general-quality gains.
The requested correctness floor must be explicit or supported by the task evidence;
do not invent a universal benchmark threshold.

At each evidence-based comparison use ADVANTAGE, EQUIVALENT or INCOMPARABLE.
Only applicable comparable evidence may establish an advantage. Equivalence must
be justified by the comparison protocol/tolerance, not by missing data. A material
incomparable earlier dimension cannot be bypassed by storage or a smaller model
name. Return unranked feasible options and abstain from the affected tier.
Evidence providers must supply justified comparisons or compatible measurements
with declared uncertainty; v0.11B must not turn capability booleans into scores.

| Tier | Admission rule |
|---|---|
| RECOMMENDED | Best supported balanced choice by the default ordering; require positive task suitability and a defensible preference, or evidenced equivalence. A sole feasible candidate still needs suitability evidence. |
| FASTER | Demonstrably lower relevant latency/higher throughput than the balanced choice, with acceptable task correctness and required context. No measurement means no FASTER tier. |
| MAX_QUALITY | Demonstrated task/general-quality advantage over the balanced choice, while meeting sustainability/minimum context. Explain any reduced target context or explicitly allowed TIGHT margin. Never infer this from size. |
| SPECIALIST | Demonstrated advantage on a narrower requested subtask or modality. Name that scope and trade-off. Do not create it merely because a catalog tag exists. |

FASTER and MAX_QUALITY require a supported comparison baseline; omit them if
none exists. SPECIALIST can stand independently with explicit narrow-task evidence.
Do not force tiers, duplicate a candidate into every tier, or label a fallback
high quality. Conditional selections carry warnings and eligibility status.

True ties after substantive comparisons produce a coequal candidate set; stable
catalog-ID ordering is display-only. Installed/active state may explain convenience
but must not manufacture a unique quality winner. Unknown is not a tie.

## Installed, known and storage scopes

BEST_INSTALLED_OPTION considers verified installed artifacts with current provider
availability. BEST_KNOWN_OPTION_FOR_HARDWARE considers locally cataloged artifacts,
installed or not; it does not search externally. Compute scopes independently.
The current fit API covers installed models only: a non-installed artifact needs
a separate trustworthy feasibility assessment input. Never fake installed=true
or reuse another quantization's fit to make it eligible.

Known non-installed options expose requires_installation and installation_feasibility
(FEASIBLE, BLOCKED, UNKNOWN). An actionable known-choice tier requires FEASIBLE:
known artifact/download size, incremental storage accounting for reliably known
shared blobs, declared staging overhead/reserve and a time-stamped available-disk
sample. Missing accounting means UNKNOWN; insufficient space means BLOCKED.
Both may remain visible as non-actionable evaluated options, never install advice.
No universal staging multiplier is invented; future installer/storage policy must
supply it. Revalidation would be required before any future installation.

Within feasible candidates, materially better capability may justify larger disk
cost. Disk is only a final comparison when earlier dimensions are equivalent.
Do not count unavailable shared-blob savings, recommend deletion or install anything.

## Explainability contract

Each evaluation identifies policy version, scope, task, context requirements,
catalog/evidence revision references and input observation times. For every
candidate retain original fit/context data, eligibility, reason codes, confidence,
missing evidence and installation feasibility where relevant. Each tier contains
zero or more coequal candidate IDs, reasons, trade-offs and evidence references.
An empty tier includes an abstention reason, not an invented winner.

Example wording, only if those facts are supported:

> RECOMMENDED // Candidate A
> WHY // GOOD fit; estimated sustainable 32K context; supported coding advantage;
> 6 GiB estimated operating headroom (system reserve additional).
> TRADE-OFF // Larger storage footprint than Candidate B.
> CONFIDENCE // MEDIUM — context/offload remain conditional estimates.

Use stable reason codes such as BELOW_MINIMUM_CONTEXT, FIT_POOR,
FIT_UNSUPPORTED, FIT_UNRESOLVED, REQUIRED_CAPABILITY_UNKNOWN,
REVISION_EVIDENCE_MISMATCH, TASK_EVIDENCE_INSUFFICIENT, COMPARISON_INCOMPARABLE,
TIGHT_OPT_IN_REQUIRED, STORAGE_BLOCKED and STORAGE_UNKNOWN, alongside plain text.
No opaque composite score is required or exposed.

## Deterministic conceptual acceptance vectors for v0.11B

These are symbolic fixtures, not claims about real models. Unless overridden:
exact identities, online installed provider, compatible chat, GENERAL, 4K minimum,
32K target, known applicable task-suitability evidence, medium-confidence fits,
adequate storage and allow_tight=false. Quality advantages/equivalence mean
reproducible comparable evidence. Unmentioned differences are evidenced equivalent.

| Vector | Inputs | Required result |
|---|---|---|
| A | A has stronger task/general quality, GOOD and 32K; B weaker, EXCELLENT and 32K. | RECOMMENDED A, MEDIUM. Extra unused memory alone does not make B preferable. FASTER absent without measurements. |
| B | A has strongest quality but POOR; B GOOD/32K with acceptable quality. | Exclude A, recommend B. Never place A in MAX_QUALITY. With only A, all tiers empty. |
| C | A higher capability, TIGHT/8K; B slightly lower capability, GOOD/32K. | Default recommend B; A conditional but no tier without allow_tight. With opt-in, A may occupy MAX_QUALITY with reduced-context/headroom warning; B stays balanced. At minimum 16K, exclude A even with opt-in. |
| D | A static UNKNOWN; complete qualifying observation proves 32K and reserves at matching workload; supported task advantage over GOOD B. | Keep fit UNKNOWN; mark OBSERVED_QUALIFIED, recommend A with at most MEDIUM and observed-only bounds. Replacing evidence with one /api/ps sample must produce E. |
| E | A UNKNOWN, native 128K and resident 32K sample only; B GOOD/32K. | A unresolved, B recommended. If no B exists, no tier; report missing calibration/context evidence. |
| F | A demonstrated coding advantage; B demonstrated general-task advantage; both GOOD/32K. | CODING recommends A; GENERAL recommends B. Null coding metadata without direct coding evidence must not be interpreted as coding failure or success. |
| G | Installed A GOOD; non-installed B GOOD with defensible stronger task quality and independent fit assessment; acquisition FEASIBLE. | Installed scope A; known scope B, requires_installation=true. No active-state change or download. |
| H | Both GOOD/32K; only current-style capability declarations/tags, no task-quality or comparative evidence. | Feasible options remain visible; recommendation tiers empty, INSUFFICIENT. Do not infer quality from fit, parameter counts or list order. |
| I | G but B needs more incremental+staging+reserve space than available. | Known scope excludes B from actionable tiers with STORAGE_BLOCKED; A remains recommended. Unknown required space instead yields STORAGE_UNKNOWN. No deletion/installation. |
| J | A/B near-equivalent within declared evidence tolerance, GOOD/32K, equivalent speed; A reliably uses less disk. | A preferred by final storage tie-breaker, not called higher quality. Equal disk yields coequal set sorted only for display. Missing quality evidence yields H, never a fabricated tie. |

Additional boundary checks: explicitly false vision excludes from VISION;
unknown vision or text-only sustainability leaves it unresolved. UNSUPPORTED
cannot be rescued by quality/observations. Revision mismatch cannot inherit an
old calibration. Permuting input order must not change results except stable
display ordering; repeated evaluation of identical snapshots must be identical.

## v0.11B implementation contract and remaining evidence work

Implement only when separately authorized: a centralized pure deterministic
evaluator with validated inputs and vectors above as executable tests. Required
inputs are policy/version, task and mandatory capabilities, context minimum/target,
scope/allow_tight, normalized catalog+revision joins, inventory/provider snapshot,
fit+headroom policy snapshot, optional validated observations, typed task/quality/
performance comparisons with evidence, and acquisition feasibility when needed.

Apply gates, then categorical comparisons and tier admission as specified. Compute
confidence separately using weakest decisive evidence and documented caps. Return
candidate evaluations, optional/coequal tiers, reasons, trade-offs, evidence links
and abstentions. Missing optional evidence is a normal result, not an exception.
Do not modify input objects, fetch evidence, run inference/benchmarks, select/load
models, change services, or grant tool/action privileges. Any API/UI integration
requires its ticket to authorize it explicitly.

Remaining evidence work: choose reproducible task correctness/reliability protocols,
comparison tolerances and calibration coverage; define storage staging requirements
for a future installer; support fit for known non-installed artifact variants.
These are missing inputs, not permission for v0.11B to invent thresholds or enrich
catalog booleans into quality. It can implement abstention and typed evidence
consumption first. No runtime 0.11.0 release is stamped by this policy slice.
