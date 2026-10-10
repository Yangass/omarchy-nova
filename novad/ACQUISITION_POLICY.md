# NOVA trusted local-model acquisition policy v1 — v0.12.0

Status: implemented through the narrow local Ollama adapter, immutable approval,
durable lifecycle, and compact native visibility. The policy still prohibits
removal, arbitrary source selection, arbitrary URLs, shell/sudo, filesystem
destinations, automatic activation, and cancellation claims that Ollama cannot
confirm.

## Purpose and authority boundary

Acquisition is a narrow semantic capability: a model may request
`install_catalog_model(catalog_id)`, but it never supplies a URL, registry host,
command, destination, executable, provider argument or privilege instruction.
NOVA policy code resolves the catalog identity to an immutable, trusted artifact
descriptor. A future provider adapter receives only that descriptor. It is not a
general command or filesystem authority.

Acquisition is distinct from recommendation. A user may explicitly acquire a
policy-eligible model that is not recommended for the current task. Acquisition
does not imply quality, and successful acquisition never changes the active
model. The existing explicit, transactional model-selection path remains the
only activation path.

## Trusted source model

The initial provider allowlist is intentionally one entry: **Ollama registry**.
Its registry endpoint and provider installation root are deployment-owned
configuration, never request, UI, catalog-display-name, or model inputs.

Every acquisition source is classified as one of:

| State | Meaning | Install eligibility |
|---|---|---|
| `TRUSTED` | Recognized provider, fixed backend-owned registry configuration, explicit catalog artifact identity and recorded provenance/pin. | May pass preflight. |
| `UNVERIFIED` | Identity or provenance is incomplete, including a tag with no defensible recorded revision. | Blocked. |
| `UNSUPPORTED` | Provider/source type is not implemented by NOVA. | Blocked. |
| `UNKNOWN` | Source cannot be observed or classified safely. | Blocked. |

The v0.12C seed has explicit descriptors pinned to local artifact observations.
Backend source policy assigns trust; a public-looking reference or model name
never grants permission to fetch an artifact.

## Catalog-to-artifact resolution and pinning

A future resolver accepts only a validated `catalog_id` and produces this
immutable conceptual descriptor:

```text
catalog_id, catalog_revision,
provider, provider_artifact_id, exact_tag,
expected_revision, expected_download_bytes,
trusted_source_state, provenance summary
```

`provider_artifact_id` and `exact_tag` come from explicit catalog fields; display
name, family, parameter label and task tags are never inputs to resolution.
Expected download/storage bytes are distinct from an installed artifact's
observed size. Unknown required fields block the proposal.

The preferred pin is an exact provider-observable revision/digest plus the
specific provider tag used to request it. If the provider only accepts a tag,
NOVA may request that exact tag, then must observe the installed manifest and
verify it against the descriptor's expected revision before reporting success.
Tag resolution to any other revision is `REVISION_MISMATCH`, not a successful
install and not a silent update. A mutable `latest` tag is never accepted unless
the catalog explicitly records the intended tag and expected revision.

## Preflight and storage policy

Before an approval may be proposed, policy must establish all of the following:

1. The recognized provider/backend is available and its fixed configured storage
   location is writable by that provider.
2. The descriptor is `TRUSTED`, complete, and bound to the current catalog record.
3. Inventory does not already contain that exact provider artifact and revision.
4. The proposed artifact has a provider-neutral fit assessment for its exact
   metadata: `EXCELLENT`/`GOOD` are normally eligible; `TIGHT` requires an
   explicit user opt-in and visible trade-off; `POOR`/`UNSUPPORTED` block;
   `UNKNOWN` blocks unless separately qualified calibration evidence meets the
   existing recommendation policy. Fit is an acquisition safety gate, not a
   recommendation or quality claim.
5. Expected download bytes and the relevant filesystem free/total capacity are
   known and satisfy the centralized `AcquisitionStoragePolicy`.
6. No acquisition for the same exact descriptor is already active.

The initial deterministic storage requirement is:

```text
required_free = expected_download_bytes
              + max(2 GiB, 25% of expected_download_bytes)  # transient/provider reserve
              + max(4 GiB, 5% of filesystem_total_bytes)    # filesystem safety reserve
```

No assumed shared-blob saving reduces this requirement. Unknown expected size,
filesystem capacity, or provider write readiness blocks rather than guessing.
All amounts are policy values, centralized and configurable in the future; they
are not tailored to one machine or model name.

`can_download` means these safety checks pass. It is separate from whether a
model is a task recommendation.

## Approval and immutable execution

Installation is state-changing and always needs explicit human approval. The
proposal freezes a detached descriptor containing catalog ID/revision, provider,
artifact/tag/revision, expected download and storage requirement, and concise
trust/provenance data. The UI and model receive only a public representation;
they cannot edit the frozen target. Existing opaque, expiring, single-use
approval semantics apply, and the model cannot approve itself.

At execution time, NOVA re-runs preflight and compares the complete descriptor.
v0.12D conservatively invalidates even a catalog revision-only change. A missing
record, changed source trust, tag, revision, expected size, provider or newly
blocked preflight invalidates the approval and requires a fresh proposal.

## v0.12D implemented simulation contract

The existing `/approvals/{id}/approve|deny` decision dispatcher supports a distinct
`acquire_catalog_model` action class. Acquisition IDs are opaque random values;
only the trusted HTTP decision path can consume them. No approval capability is
in the model tool registry. The native UI remains unchanged in this slice.

The durable transition graph is centralized in `acquisition_lifecycle.py`:

`PROPOSED → APPROVED → STARTING → DOWNLOADING → VERIFYING → INSTALLED`.

PROPOSED may become REJECTED, EXPIRED or FAILED. Execution may become FAILED or
RECOVERY_REQUIRED. DOWNLOADING may become CANCEL_REQUESTED, which becomes
CANCELLED only after adapter confirmation, otherwise RECOVERY_REQUIRED. Terminal
states cannot execute again. All API records carry `simulation: true` and
`activation: not_performed`; INSTALLED is a **simulated lifecycle outcome**, not
an assertion that real inventory changed. No real acquisition adapter exists.

State lives in the backend-owned `~/.local/state/nova/acquisitions/state.json`,
with a private 0700 directory and 0600 atomic replacement file. Writes fsync the
file and parent directory. Limits are 128 records and 1 MiB; capacity exhaustion
blocks new proposals rather than discarding replay history. A malformed/unsafe
state file disables acquisition operations and is left intact for diagnosis.
Normal chat/actions remain independent. Persistence failure stops execution.

Restart preserves terminal outcomes, expires unconsumed proposals, and changes
incomplete approved jobs to RECOVERY_REQUIRED. Simulation has no durable provider
proof to resume from; no job automatically restarts. A retry needs new preflight
and explicit approval. Successful simulated artifacts cannot be rerun from the
same descriptor. Duplicate live provider/artifact requests conflict.

The adapter exposes begin(frozen descriptor), observe_progress(), verify(frozen
descriptor), request_cancel(), and recover(). Only immutable descriptor data is
passed to it. Scenario injection is test-only; clients cannot choose a scenario
or adapter. Cancellation is an internal lifecycle operation tested in this slice;
there is no cancellation UI or HTTP endpoint yet.

## Lifecycle, verification, failure and cancellation

The future observable lifecycle is:

`PROPOSED → APPROVED → DOWNLOADING → VERIFYING → INSTALLED`

Terminal/exception states are `REJECTED`, `FAILED`, `CANCEL_REQUESTED`,
`CANCELLED`, `REVISION_MISMATCH`, and `RECOVERY_REQUIRED`. Progress is an
observation of the provider job, not an assertion derived from elapsed time.
Cancellation is best-effort: NOVA reports `CANCELLED` only after the provider and
inventory confirm the job stopped and no valid new artifact was installed.

Network interruption, provider error, disk exhaustion, verification mismatch,
or provider/NOVA restart never produces `INSTALLED` by implication. On recovery,
NOVA must inspect the provider's exact artifact/inventory state and either verify
the descriptor or report `RECOVERY_REQUIRED`/`FAILED`. An incomplete artifact
must never be selected ACTIVE.

A persisted `RECOVERY_REQUIRED` attempt remains immutable evidence of failure.
Recovery assessment and resolution are separate durable observations; they do
not turn the failed attempt into `INSTALLED`. Exact, absent, mismatched and
unverifiable artifact observations remain distinct. When Ollama cannot expose a
queryable acquisition job, inactivity is `UNKNOWN`; empty inference residency
does not resolve it. An absent artifact may be released for a later fresh
proposal only after an explicit operator attestation that no provider
acquisition is active. This attestation is identified as human evidence, bound
to a current assessment, single-use, and never described as machine verification.

The resolution operation performs no provider mutation. It is unavailable
during known active NOVA acquisition work and rejects stale or duplicate
requests. Any later acquisition has a new identity, fresh live preflight and
fresh human approval. Recovery never deletes shared blobs, terminates Ollama,
resumes a pull, retries automatically, activates a model, or clears history.

Cleanup may target only data demonstrably created exclusively for that acquisition.
NOVA must not delete arbitrary provider files or blobs, and must never assume a
blob is unshared. If exclusive ownership cannot be proven, it leaves the data in
place and reports cleanup required. Model removal is explicitly out of scope;
any future removal capability needs separate approval and provider blob-sharing
analysis.

## Idempotency and activation

| Situation | Policy result |
|---|---|
| Exact artifact already installed | Return `ALREADY_INSTALLED`; no duplicate download or activation. |
| Same tag installed at different revision | Return `REVISION_MISMATCH`; do not replace it. |
| Same descriptor in progress | Return the existing acquisition state; do not start a second job. |
| Prior failure | A new attempt requires a fresh preflight and approval. |
| Descriptor changed after approval | Invalidate the approval; never substitute the new descriptor. |
| Installation verified | Report `INSTALLED`; keep active model unchanged. |

Activation is a separate explicit user transition after verified installation.
It uses the existing target-load, runtime-verification, rollback and persistent
selection transaction. It is not bundled into installation approval, and there is
no automatic fallback if activation later fails.

## Provider adapter boundary

A future provider-neutral adapter may expose bounded operations conceptually:

```text
resolve(descriptor) -> provider resolution observation
preflight(descriptor) -> provider readiness observation
acquire(frozen_descriptor) -> opaque acquisition handle
observe_progress(handle) -> provider progress observation
verify(frozen_descriptor) -> exact installed-artifact observation
cancel(handle) -> cancellation request observation
```

`novad` retains trust classification, catalog resolution, headroom/storage policy,
approval lifecycle, concurrency ownership, audit, presentation and final success
decision. Adapters may only call their fixed provider protocol with validated
descriptors. They receive no arbitrary URL, shell command, executable, path,
sudo credential, action token, or UI/model payload.

## Security invariants

- No arbitrary shell, sudo, URL, executable, package operation, filesystem
  destination, provider command, or deletion.
- Trusted catalog identity and immutable revision-aware descriptor are required.
- Explicit human approval is required before state change.
- Provider success is insufficient until NOVA verifies exact installed identity.
- No automatic activation, switching, fallback, routing, or recommendation claim.
- Services remain loopback-only; the plugin continues to speak only to novad.
- Acquisition recovery/cleanup cannot affect unrelated or shared provider data.

## Deterministic implementation vectors

| Vector | Scenario | Required policy result |
|---|---|---|
| A | Trusted descriptor, eligible fit, writable provider and enough reserved disk. | Create immutable approval proposal; no download before approval. |
| B | Arbitrary URL or untrusted source request. | Reject before provider call; no proposal. |
| C | Exact artifact already installed. | `ALREADY_INSTALLED`; no duplicate job or activation. |
| D | Same identity but different installed revision. | `REVISION_MISMATCH`; no replacement. |
| E | Disk free is below required-free policy. | `INSUFFICIENT_STORAGE`; no proposal/job. |
| F | Catalog descriptor changes after proposal. | Invalidate approval; require new preflight/approval. |
| G | Duplicate request while acquisition is active. | Return same acquisition state; no second download. |
| H | Network interruption. | `FAILED` or `RECOVERY_REQUIRED`; no false install. |
| I | Provider reports completed but manifest revision differs. | `REVISION_MISMATCH`; never report installed/active. |
| J | User cancels while downloading. | `CANCEL_REQUESTED`, then `CANCELLED` only after provider observation. |
| K | Verified install then user explicitly selects model. | Separate transactional activation may proceed. |
| L | Verified install then activation fails. | Model remains installed; previous active model/config remains intact. |
| M | Ollama unavailable at preflight or execution. | No proposal or `FAILED`; no guessed state. |
| N | NOVA restarts during acquisition. | Recover from provider observation; verify or report recovery required. |

## v0.12E real Ollama adapter (controlled live test passed)

The production lifecycle now selects `OllamaAcquisitionAdapter`; simulation is
test-only and historical simulated records are never converted into real jobs.
The only mutation protocol is fixed loopback HTTP `POST /api/pull`, with a frozen
library artifact/tag, `stream: true`, and `insecure: false`. No shell is used.
The adapter constructs `registry.ollama.ai/library/<artifact>` itself; arbitrary
registry/namespace/URL identifiers and cloud tags are rejected. HTTP redirects and
environment proxies are not followed by the local client. The Ollama service owns
its registry transport. A preexisting mismatch is never overwritten by NOVA.

Readiness currently supports one audited systemd deployment preset. It checks
the root-owned vendor unit digest and absence of overrides, a running service
cgroup/process with Ollama UID, unit age relative to process start, configured
model root, owner write/search permission on storage and manifest directories,
mount read-only flags, parent traversal and inactive SELinux/AppArmor enforcement.
It creates no probe files and executes no commands. Different/changed units,
restricted observation, user services or enabled MAC return UNKNOWN until their
configuration can be audited. This is a conservative deployment-specific proof,
not a universal Linux permission evaluator or a guarantee against later disk errors.

`catalog/acquisition_fit.json` preserves locally observed, exact-revision-bound
API metadata so removing an installed model does not destroy its acquisition fit
inputs. The existing fit formula is reused for known artifacts without claiming
they are installed. Missing/mismatched metadata is UNKNOWN. qwen3.5 hybrid-context
uncertainty is unchanged. No external metadata or ranking is introduced.

Catalog expected size means referenced artifact bytes, not necessarily network
transfer. Stream counters are per-blob completed/total bytes and may include cache
hits or resumed data. NOVA replaces repeated observations for a blob rather than
counting them twice. It may present the current blob's percentage when that
blob's positive total is known, but publishes no whole-model percentage from the
sum of discovered blobs and leaves `bytes_transferred` null. Unknown current-blob
totals use an indeterminate presentation. Final verified footprint is
reported separately. Overall success still requires a provider success event,
then agreement of `/api/tags` identity/digest, local SHA-256 manifest digest,
existence and sizes of referenced blobs, and expected total footprint. NOVA does
not independently rehash every large weight blob; Ollama owns transfer integrity.

Cancellation is `CANCEL_UNSUPPORTED`. The inspected Ollama 0.33.3 handler uses
request cancellation, but shared blob downloads use reference counting and no
queryable pull job ID proves a job stopped. NOVA neither disconnects on a user
cancel request nor kills processes or deletes partial/shared blobs. Stream errors
or timeouts become RECOVERY_REQUIRED because disconnection is not proof of stop.

At restart, real interrupted jobs are inspected without a pull: exact artifact
verification gives INSTALLED, mismatch gives FAILED, and absent/incomplete/
ambiguous/unavailable observations remain RECOVERY_REQUIRED. Absence does not
prove an upstream job stopped. Recovery-required targets block duplicate proposals
pending explicit operator reconciliation; no automatic retry is provided.
Ordinary terminal outcomes remain terminal; pending approvals expire on restart.
Activation remains the separate explicit model switch path, never acquisition.

Protocol references inspected for this implementation:
- https://docs.ollama.com/api/pull
- https://github.com/ollama/ollama/blob/v0.33.3/server/routes.go (PullHandler)
- https://github.com/ollama/ollama/blob/v0.33.3/server/download.go (Wait/release)

The controlled qwen3:8b remove/reacquire test passed after manual user removal
and explicit human approval. Real stream progress was observable; the exact
catalog digest and 5,225,388,164-byte footprint verified before INSTALLED.
An expired proposal was rejected, successful approval replay returned 409, and
installed inventory produced a no-op. Primary qwen3.5:35b remained configured.
Terminal state survived novad restart. No release stamp is authorized.

## Historical v0.12B implementation contract and open questions

v0.12B should add a small pure descriptor/preflight module and tests for A–N
before any provider mutation. Required inputs are a validated catalog acquisition
record, exact inventory/manifest observation, fit result, storage observation,
provider readiness and a centralized storage policy. It must return explicit
eligibility/reason states without network, filesystem mutation, approval creation
or provider calls. A later separately authorized slice may add a fixed Ollama
adapter and approval-gated execution.

Open decisions for later evidence-based implementation: the exact Ollama pull
progress/cancel protocol, durable acquisition-handle storage and restart recovery,
provider-owned temporary-data ownership signals, and catalog schema fields that
can assert an upstream artifact revision and expected download bytes. None authorizes
fallback behavior or weakens the policy above.
