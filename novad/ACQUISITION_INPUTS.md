# Acquisition preflight and lifecycle input contract — v0.12.0

`acquisition.py` is a pure, provider-neutral evaluator. It does not load catalog
files, contact Ollama, inspect disk, create approvals, alter model selection, or
write state. Callers supply immutable normalized dataclasses and receive a detached
JSON-compatible result.

## Required inputs

- `AcquisitionDescriptor`: catalog ID/revision; provider; trust state; explicit
  provider artifact ID/tag; expected revision; expected artifact size; provenance.
  The pure `descriptor_from_catalog()` helper defaults its trust state to
  `UNVERIFIED`; the read-only integration layer assigns `TRUSTED` only after its
  backend-controlled provider/source allowlist accepts optional catalog
  acquisition metadata. Catalog text cannot grant trust by itself.
- `InventorySnapshot`: `NOT_INSTALLED`, `INSTALLED_EXACT`,
  `INSTALLED_DIFFERENT_REVISION`, `AMBIGUOUS`, or `UNKNOWN`.
- `FitSnapshot`: existing fit rating plus an explicit separate qualification flag
  for an otherwise `UNKNOWN` fit. It does not invent calibration evidence.
- `DiskSnapshot`: relevant filesystem total/free bytes, nullable only when the
  evaluator must return an unknown storage result.
- `ProviderPreflight`: caller-observed support, availability, write readiness,
  duplicate activity and optional prior-failure state.
- `PreflightRequest`: current catalog revision and explicit `allow_tight` opt-in.

No display name, URL, filesystem path, executable, provider command, shell input,
or active-model state is accepted.

## Live read-only integration

`acquisition_integration.py` combines an already loaded catalog snapshot, current
installed-inventory match state, existing model-fit results, and capacity of the
configured Ollama model-storage filesystem. It supplies these facts to the pure
evaluator; the evaluator retains all trust and disk-policy decisions.

`GET /acquisition/models/{catalog_id}/preflight` accepts the catalog ID in the
path only. It accepts no query parameters or client overrides for a source, URL,
artifact, digest, size, path, or provider command. The response is advisory and
records the catalog revision used. It never creates an approval or provider
activity. Write readiness is `UNKNOWN` when it cannot be demonstrated without a
write probe.

## Storage calculation

With known integer byte inputs, `AcquisitionStoragePolicy` calculates:

```text
required = expected_artifact_size
         + max(2 GiB, 25% expected_artifact_size)
         + max(4 GiB, 5% filesystem_total)
```

It returns all components, free space and remaining bytes. Unknown required values
remain unknown; the evaluator never estimates them.

## Result semantics

`ELIGIBLE` means only that a trusted, complete descriptor passed pure acquisition
safety checks. It is not a recommendation and creates no provider activity.
`NO_OP_ALREADY_INSTALLED` means an exact installed artifact needs no acquisition.
`BLOCKED` reports a known blocker such as revision mismatch, unavailable provider,
insufficient storage or unsafe fit. `UNKNOWN` is a conservative blocker when
critical identity, inventory, catalog revision, provider or storage facts are
unknown. `eligible` is false for every status except `ELIGIBLE`.

## Proposal and simulated lifecycle

`POST /acquisition/models/{catalog_id}/propose` accepts an empty body and no query
parameters. It reuses live preflight: exact-installed returns its no-op response,
blocked returns 409, unknown catalog ID returns 404, and eligible returns 201 with
a durable frozen proposal. Current live seed models are already installed, so
they create no proposal. Synthetic test fixtures exercise eligible execution.

The proposal freezes the descriptor, preflight disk/fit/provider observations,
catalog revision, provenance and timestamps. Its opaque ID works with the existing
`POST /approvals/{id}/approve|deny` empty-body path. Approval expires after 120
seconds, is single-use, and revalidates fresh preflight plus exact descriptor
equality before persisting APPROVED and starting a simulated worker.

`GET /acquisitions/{id}` returns a detached read-only lifecycle snapshot with
progress, verification, timestamps and bounded error codes. GET cannot advance
execution. Terminal real-provider failures expose `historical_progress` with
its observation time, separately from nullable current `progress`, plus a
bounded structured `failure_detail`. Provider text is sanitized and truncated;
raw responses, paths and credentials are not retained. Every record includes
action class
`acquire_catalog_model`, and `activation: not_performed`. INSTALLED means only a
successful simulated download and digest verification; real inventory is never
changed. No client selects a provider adapter, scenario, path or execution input.

The durable store and transition graph are in `acquisition_lifecycle.py`.
Recovery fails conservatively, never automatically resumes, and keeps terminal
outcomes. No model tool can approve; existing action-executor authority is
unchanged. The v0.12D paragraphs describe simulation; the current production
contract is below. Runtime remains 0.11.0.

## Real provider execution

Production proposals have `simulation: false`; HTTP clients cannot choose the
adapter. Approval runs only the fixed local Ollama pull protocol and revalidates
the frozen descriptor. Existing body/query restrictions and 120-second single-use
approval remain unchanged. No acquisition/approval tool is exposed to the model.

Status `progress` is a nullable object for real jobs. Aggregate
`provider_completed_bytes` and `provider_total_bytes` cover the latest counters
for each discovered blob; repeated events replace the prior observation for that
blob and do not add it twice. They are not a complete global denominator.
`current_blob_index`, `current_blob_completed_bytes`, and
`current_blob_total_bytes` identify the provider's current blob observation. Its
own percentage is meaningful only when its positive total is known. Cached or
resumed data may be included, while `bytes_transferred` remains null, so none of
these values are labeled measured network transfer. `verified_footprint_bytes`
appears only after exact verification. `cancellation` explicitly returns
`CANCEL_UNSUPPORTED`; `activation` remains `not_performed`.

Expected artifact bytes stay in the frozen descriptor and central disk formula.
Absent models may use locally saved exact-revision metadata from
`catalog/acquisition_fit.json` for the same hardware-fit estimator. It never
changes installed inventory or supplies a recommendation. Readiness uses the
audited systemd preset described in ACQUISITION_POLICY.md and fails unknown for
unsupported configurations.

Restart reconciliation observes `/api/tags` and local manifests/blob sizes only.
It never restarts a pull. Exact verification permits INSTALLED, mismatch FAILED,
and absence/unknown RECOVERY_REQUIRED with duplicate proposals blocked. No cleanup
or model removal capability is introduced. The controlled real test passed:
the user removed qwen3:8b outside NOVA, approved the frozen proposal, and the
adapter reached verified INSTALLED with the expected digest and footprint.
Approval replay was rejected and the primary model configuration stayed unchanged.

## Persisted recovery resolution

`GET /acquisitions/{id}/recovery` performs a read-only exact-identity assessment
for a persisted `RECOVERY_REQUIRED` record. It reports `EXACT`, `ABSENT`,
`MISMATCH`, or `UNVERIFIABLE` separately. Provider activity is independently
`INACTIVE_CONFIRMED`, `ACTIVE`, or `UNKNOWN`. Empty inference residency is never
used as acquisition-activity evidence. The response includes a SHA-256
`assessment_id` bound to the failed record, artifact observation, provider
activity evidence and any existing resolution. GET does not write state.

`POST /acquisitions/{id}/resolve-recovery` accepts only:

```json
{
  "assessment_id": "<the current assessment_id>",
  "operator_attestation": "NO_PROVIDER_ACQUISITION_ACTIVE"
}
```

The attestation is required only when exact identity is `ABSENT` and provider
activity remains `UNKNOWN`. It is explicitly an operator statement, not
machine-verified inactivity. `MISMATCH`, `UNVERIFIABLE`, known active provider
work, other active NOVA acquisition work, stale assessments, and duplicate
resolutions are rejected. For machine-verified `EXACT`, the attestation field
may be omitted and the separate resolution records `EXACT_INSTALLED`; it does
not rewrite the original failed outcome.

Resolution never invokes the adapter. It writes one immutable resolution beside
the preserved attempt. Only an absent-artifact resolution may set
`retry_allowed`; the next proposal still calls live preflight and creates a new
attempt ID in `PROPOSED`, requiring a new single-use human approval before any
pull. No cleanup, provider stop, blob deletion, automatic retry, or activation is
part of either recovery endpoint.

## Native read-only visibility

`GET /acquisitions` returns newest-first detached lifecycle snapshots for the
native selector; it accepts no parameters and cannot advance state. The selector
uses `GET /acquisition/models/{catalog_id}/preflight` before presenting an
install affordance, and submits only that catalog ID to the proposal route. The
backend remains the sole resolver of provider, artifact, revision, size, source,
storage path, and execution method.
