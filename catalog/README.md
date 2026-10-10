# NOVA catalog schema v1 — v0.10A

This is local curated knowledge, independent of whether a model is installed,
active, resident, available or a good hardware fit. The four initial records
represent known local artifact variants, not globally best models. No ranking,
recommendation, installer, network access or refresh job is included. v0.10B
adds the read-only API below; UI is unchanged.

Storage: `catalog/models.json`.
Loader: `novad/catalog.py` (Python standard library only).
The authoritative executable validator is `validate_catalog`; this document
specifies its contract. No external JSON Schema package is required.

## Optional trusted acquisition descriptor (v0.12C)

A model record may include an optional `acquisition` object:

```json
{
  "provider": "ollama",
  "artifact_id": "qwen3:8b",
  "exact_tag": "8b",
  "expected_revision": "<local manifest SHA-256>",
  "expected_size_bytes": 5225388164,
  "source": "ollama_registry",
  "provenance": {
    "method": "...",
    "observed_at": "YYYY-MM-DDTHH:MM:SSZ",
    "notes": "..."
  }
}
```

It is an identity/storage assertion for a future acquisition preflight, not a
download instruction. Provider, artifact ID, revision and size must exactly match
the catalog source/artifact facts; `exact_tag` must match the explicit Ollama
artifact tag. There is no URL, registry host, executable, command, path, or
catalog-supplied trust flag in this schema.

The loader accepts only the inert source class `ollama_registry` today. The
backend-owned acquisition integration independently maps that source class and
provider to its fixed supported-provider allowlist. Thus catalog text, arbitrary
source classes, references, display names and URLs cannot grant acquisition
authority. Missing acquisition data yields an unverified descriptor.

Malformed acquisition metadata is isolated: the model record remains browseable
with `acquisition: null`, and the catalog exposes a loader diagnostic. It does
not invalidate a valid sibling record. The four Qwen seed descriptors use their
existing local manifest digests and artifact byte sizes. They remain read-only
metadata; no pull, approval, installation, activation or mutation exists.

## Envelope

- `schema_version`: required integer `1`; incompatible versions are rejected.
- `catalog_revision`: required human-maintained nonempty string, independent of
  NOVA runtime version, e.g. `2026-09-08.seed1`.
- `models`: required list, maximum 256 records. An empty valid list is allowed.

## Record fields

| Field | Contract |
|---|---|
| `catalog_id` | Required unique stable lowercase ID; letters/digits/dot/underscore/hyphen, max 128 chars. Treat as opaque, never parse parameter counts from it. |
| `display_name` | Required nonempty display label, not machine inference input. |
| `family`, `release_label` | String or null; family is the locally reported family, which may name an architecture. |
| `source` | Required object: `kind`, `provider`, `upstream_id`, `reference` required strings; optional nullable `revision_digest`. |
| `properties` | Optional object: nullable positive integers `parameter_count`, `weight_size_bytes`, `native_context`; nullable string `architecture`; nullable unique string list `quantizations`. |
| `capabilities` | Optional object with `chat`, `tools`, `thinking`, `coding`, `vision`, `instruction_following`; each true/false/null, omitted keys become null. |
| `task_tags` | Optional unique list from general/coding/reasoning/fast/vision; default empty. Descriptive annotations, not scores or evidence of speed. |
| `metadata` | Required object with nullable `release_date` (YYYY-MM-DD), `license`, `checked_at` (UTC YYYY-MM-DDTHH:MM:SSZ), and required `provenance`. |
| `metadata.provenance` | Required object: nonempty `method`; optional unique `references` list and nullable `notes`. |

Strings are bounded at 4096 characters, lists at 32 strings, files at 1 MiB.
Source kinds: `local_registry_snapshot`, `upstream_metadata`, `manual_curated`.
Provider identifiers are data, not an adapter registry or new provider support.
References are inert provenance strings: they are never opened or fetched.
Unknown fields are rejected to catch typos and prevent runtime/fit fields from
silently entering this schema. Extend the schema explicitly for new concepts.

The schema version belongs to the file envelope and covers every record.
One record describes one source/artifact variant in this slice. Multi-source
canonical checkpoint relationships and quantization-specific size maps are future
schema work, not inferred or collapsed now.

## Adding models: registry probe (CATALOG-01, 2026-10-04)

`tools/catalog_probe.py name:tag --tags ... --ref "<guide>" --write` describes an
Ollama registry model without downloading it:
- identity pin: the manifest sha256 and summed layer sizes. These match a local
  `ollama pull` exactly; the 14B was checked field by field;
- the config blob: quantization (`file_type`), minimum Ollama version
  (`requires`, recorded in the provenance notes) and renderer/parser;
- a ranged read of the GGUF header: architecture, layers, KV heads, key/value
  length, native context, and a parameter count summed from tensor shapes.

Capabilities are declarations only. `tools`/`thinking` come from the template
or from a parser whose Ollama declarations were checked locally
(`PARSER_CAPABILITIES`); `vision` comes from a projector layer. Everything else
stays null.

Task tags follow one rule:
- `general`/`coding` only when a cited guide (in `metadata.provenance.references`)
  recommends the model for that use;
- `vision` and `reasoning` mirror declared vision/thinking.

Tags are descriptive, not NOVA benchmarks, and fit is never quality. Probed fit
records carry `fit_metadata_source: registry/gguf_header`. Seed records keep their
local provenance; probing a seed with the same digest must not replace it.

## Seed provenance and uncertainty

The four records are qwen3.5:35b, qwen3:30b, qwen3:14b and qwen3:8b. They were
curated from current local Ollama `/api/tags`, `/api/show` and local manifests.
Registry references and digests identify that local observation; they do not
claim independent upstream verification. Nothing was fetched from the internet.

Parameter counts, sizes and native context come from explicit metadata, never
from labels such as "35b". Artifact size is not runtime memory. Quantizations
contains only the locally observed Q4_K_M option, not all options upstream.
The catalog's checked timestamp means local evidence was checked, not that it is
fresh against an upstream release. Release labels are used only where supplied
by explicit metadata; there is no fabricated release date.

A declared capability is true; absence from local capability metadata stays null,
not false. Coding/instruction following are unverified. Only qwen3:8b has a local
`general.license` identifier; other licenses remain null. Licenses are recorded
as reported, not independently verified. Reasoning/general/vision tags reflect
those declared capabilities. No fast/coding tags or benchmark scores are seeded.

## Loader behavior and internal use

```python
from catalog import load_catalog
catalog = load_catalog()  # fixed default path; optional explicit local path
records = catalog.records
record = catalog.get('qwen3-8b-q4-k-m')  # detached copy, or None
matches = catalog.match_installed(installed_inventory)
```

`Catalog.status` is `ready`, `partial`, `invalid` or `unavailable`; errors are a
tuple of safe validation diagnostics. Missing/unreadable files return unavailable.
Bad JSON/encoding/version/envelope/size returns invalid with no records. Invalid
individual records are skipped; valid siblings remain available. Duplicate IDs
or duplicate provider/upstream pairs exclude **all** colliding records so file
order cannot resolve ambiguity. Duplicate JSON object keys are rejected.

Matching accepts provider-neutral inventory dictionaries with `provider`, `id`,
`installed: true`, and optionally `digest`. It returns separate join records;
never mutates the catalog or inventory. Matches are exact and case sensitive,
with no name/family/size guessing. Unknown model IDs remain unmatched. A digest
mismatch is reported as `revision_match: false`; no digest yields null. Alias
matching is not proof the installed artifact still matches a catalog snapshot.

Import does not load the catalog or contact services. Loading only reads the
catalog file. The loader remains independent of model config, providers, downloads, hardware
fit and approvals. v0.10B wraps it in a dedicated read-only API presenter.
No catalog file is rewritten on load. Manual future updates must be validated
and atomically replaced by the maintainer; no updater is implemented here.

## Validation

From `novad/`: `python -B -m unittest discover -v`.
The tests cover valid and malformed catalogs, record isolation, null handling,
identity/digest joins, independent inventory, prohibited writes/network access,
and the existing fit/API/action-boundary/model-switch regression suite.

## v0.10B read-only API

`GET /catalog/models` returns normalized records with `installed_match` and
`freshness` added as separate observations. Existing source, metadata/provenance,
nullable capabilities and properties remain unchanged. Root fields include:

- `schema_version` (validated catalog version, null on invalid/unavailable),
  `supported_schema_version`, `catalog_revision`, `status`, `loader_errors`.
- `record_count` and `installed_match_counts` by explicit state.
- `freshness`: oldest/latest record checked timestamps, timestamp coverage,
  and `automatic_refresh: false`. Per-record assessment stays unknown; no TTL.
- `inventory`: filesystem observation source/status/time and safe diagnostics.

`GET /catalog/status` returns the same summary without the models array, using
the same builder. Both return HTTP 200 with explicit diagnostic state when the
optional catalog is missing, partial, malformed or unsupported. They have
`Cache-Control: no-store`; query parameters and mutation methods are rejected.
No catalog paths or configured inventory root appear in the API. No new chat
tool or UI element is added. Runtime version remains 0.9.0.

### Installed-match semantics

`installed_match` contains nullable `installed`, `installed_model_id`,
`catalog_digest`, `installed_digest`, `revision_match`, `match_status` and reason.

- `exact`: one exact provider/tag match and equal known revision digests.
- `revision_mismatch`: one installed identity with a different known digest;
  both are visible, installed remains true, no broken-model claim or mutation.
- `not_installed`: identity absent from a complete local inventory scan.
- `ambiguous`: multiple inventory entries for the identity; no selection guessed.
- `unknown`: unavailable/partial evidence, incomplete artifact, unobserved provider
  or missing digest. An installed tag without revision evidence is not exact.

The v0.10A loader still excludes all duplicate catalog identities and reports
errors. Inventory ambiguity is reported separately on otherwise valid records.

### No-network inventory adapter

`catalog_observability.py` reads local Ollama manifests, hashes only manifest
bytes and verifies referenced blob existence/sizes. It reuses v0.10A exact joins.
It never calls Ollama HTTP, opens outbound sockets or launches subprocesses.
No full weight hash, residency probe or provider-health inference occurs.
Reads are bounded to 512 manifests, 256 KiB per manifest, 128 layers per manifest.
Incomplete scans cannot prove absence; symlinks/unexpected layouts are not guessed.

The default root `/var/lib/ollama` is a verified **deployment setting**, not a
universal Ollama default. Set `NOVA_OLLAMA_MODELS` in novad's environment for a
custom local installation. No request parameter controls this path. API callers
cannot request arbitrary files. Provider-neutral presentation can later accept
another adapter, but v0.10B implements only this local filesystem adapter.

Filesystem observation does not prove runtime availability or cryptographic
integrity of weight contents. Concurrent external installation/removal can make
a read partial; subsequent GET re-reads without changing anything. Registry/tag
layout support is limited to the observed four-level manifest convention; there
is no alias guessing, automatic storage discovery or remote-provider support.

### Freshness and provenance

Seed `metadata.provenance`, `source.kind/reference`, and `metadata.checked_at`
remain visible. They describe local evidence, not independently verified upstream
claims or benchmarks. Latest checked record is not an atomic catalog update time;
oldest/latest timestamps and coverage avoid hiding mixed-age records. No record
is automatically labeled current, stale, obsolete or high quality. Mismatch is
an identity observation, not a freshness verdict.

Suggested v0.10C: bounded integration/regression and milestone acceptance, with
review of deployment configuration and provenance wording. No v0.10C work or
full v0.10.0 release is included here.
