# NOVA v0.9 local AI operating fit

Prefer the best sustainable model with strong usable context and adequate system
headroom over the largest model that barely loads. Parameter count and model name
never determine fit. This is conditional memory fit, not quality, speed prediction,
or a best-model recommendation. No catalog, model search, downloads or routing.

## Data and APIs

`GET /hardware/profile` returns `capacity` (CPU, RAM total, GPU inventory),
`dynamic` (available RAM, GPU used/free memory and utilization, model-filesystem
capacity/free space), `local_ai`, sources, sample time and operating `budget`.
`GET /models/fit` returns that profile, budget, serialized policy and installed
local model records with separate `fit` and `observed_runtime` objects. Offline
Ollama gives HTTP 200 with partial hardware, `provider_available: false`, empty
models and `error: provider_unavailable`; this does not imply an empty installation.
Fixed read-only tools `get_hardware_profile` and `get_local_model_fit` make these
facts available to NOVA chat. They accept no arguments and retain the tool output
size limit. No action registry, approval or switching authority is added.

Sources: `/proc/cpuinfo`, `/proc/meminfo`, PCI/sysfs and optional `lspci` names;
existing system-tools NVIDIA telemetry; optional `nvidia-smi` driver CUDA API
version (not proof of Ollama CUDA use); filesystem stat of Ollama model storage.
Storage uses the system `ollama.service` OLLAMA_MODELS environment setting, or
its configured service home default. Custom non-systemd deployments/EnvironmentFile
or command-line overrides are not resolved; missing evidence is unknown. Paths,
serials, UUIDs, hostnames and raw environment values are not returned. Disk free
is explicitly dynamic, even though it describes the model-storage filesystem.

Ollama `/api/tags` supplies current installed artifact size. `/api/show` supplies
architecture, native context, KV dimensions and capabilities. Raw templates,
paths and tokenizer data are not exposed. Details are cached for 60 seconds;
failed details remain partial. Each inventory request requires online tags and
limits fresh detail lookups to an approximately 8-second budget (2-second calls).
Ollama `/api/ps` supplies resident state, runtime size, VRAM size and runtime
context separately. Unknown runtime availability is not confused with unloaded.

## Centralized initial policy

All constants live in `HeadroomPolicy` in `model_fit.py`, injectable for tests
and future tuning; v0.9 has no settings UI for them. Units are binary bytes/GiB.

- GPU reserve = max(2 GiB, 15% of total VRAM), capped at capacity.
- CPU/RAM reserve = max(4 GiB, 25% of total RAM), capped at capacity.
- Operating budget = total capacity minus that reserve.
- Per-model runtime allowance = max(1 GiB, 10% of installed artifact size).
- Context consumes additional budget using the bounded method below.
- Context allocation retains at least 5% of operating budget as further margin.

The system reserve is for the desktop, display, other apps and contention. The
runtime allowance covers backend/graph/transient allocations beyond weight size.
Neither is a measured guarantee. Other concurrent workloads can exceed reserves.
Current available RAM/VRAM is shown but never subtracted in the stable estimate.
Each model is assessed individually, not as a set of concurrently loaded models.

A sole known dedicated GPU receives a **conditional GPU candidate** assessment.
This does not claim it is Ollama's selected device, even when integrated/unknown
GPUs are also present. All devices are reported; VRAM is never summed. Multiple
known dedicated GPUs, unknown GPU capacity or unknown memory kind leave the
operating device/budget unknown. No detected GPUs enables CPU/RAM assessment.
AMD/Intel PCI devices are reported, with missing names/VRAM/backend left unknown.
GPU offload is estimated and conditional, not an observation of backend support.

## Context estimation

Only GGUF architectures `qwen3` and `qwen3moe` currently have an estimator, using
explicit positive scalar layer count, KV head count, key length and value length:

```
KV bytes/token = layers × KV_heads × (key_length + value_length) × 2 × 1.25
```

The 2 assumes FP16 cache elements. 1.25 is an additional safety factor. This is
for one text sequence and full attention, not a universal bytes/token constant.
It does not derive head size from embedding size or parameter labels. Missing
metadata, hybrid/recurrent and unknown architectures abstain. Qwen3.5's hybrid
architecture is intentionally unsupported by the context estimator, not marked
as an unsupported executable model. Vision workloads and concurrent sequences
are outside this estimate. Cache settings/batching changes may invalidate it.

Recommended context is the minimum of native maximum, 32768-token policy target,
and context affordable after weights, runtime allowance and 5% budget margin,
rounded down to 1024 tokens. Less than min(native maximum, 4096) is POOR.
Recommendations are advisory: v0.9 does not change Ollama/chat `num_ctx` or cache
settings. Advertised maximum and observed current context are different fields.

`estimated_runtime_bytes` includes weights proxy + runtime allowance + KV.
`estimated_headroom_bytes` is budget remaining after that estimate; desktop
reserve is additional. If context is unknown both are null; the explicitly
separate `weight_headroom_bytes` is only budget minus artifact size, not safe
runtime headroom. All computed contexts/positive fits have medium confidence,
not certainty. Current resident measurements never promote an estimate's rating.

## Rating semantics

- EXCELLENT: full policy target (or smaller native max) and >=15% unused budget.
- GOOD: full target and >=5% unused budget.
- TIGHT: practical context is reduced below target, or margin is below 5%.
- POOR: weights/runtime exceed budget or minimum practical context cannot fit;
  GPU assessment may need partial/CPU offload. Not proof of inability to run.
- UNSUPPORTED: provider metadata explicitly lacks chat capability for NOVA.
- UNKNOWN: missing capacity/weight/provider inputs or unbounded context memory.

These are centralized initial heuristics, not universal thresholds. They do not
incorporate coding/reasoning quality, tool reliability, benchmarks or freshness.

## Source references

Ollama's [show metadata API](https://docs.ollama.com/api/show) and
[running models API](https://docs.ollama.com/api/ps) define provider observations.
The [Ollama FAQ](https://docs.ollama.com/faq) documents KV-cache precision and
parallel context memory considerations. The above operating reserves, target
context and confidence policy are NOVA heuristics, not Ollama guarantees.

## Validation and human acceptance

Run `python -m unittest discover -v` from `novad/`.
Automated/live evidence and rollback copies for this milestone are in
a local development archive (not versioned). No real state-changing controlled action was approved
for regression testing. Human acceptance should check the BRAIN list, scrolling,
context wording and native selection/chat feel. v0.8.0 remains the last accepted
milestone until the product owner accepts v0.9.0.
