# novad

Loopback-only local NOVA orchestrator. It health-checks local Ollama and the
read-only system-tools service, lists installed Ollama models, and runs a
bounded native Ollama tool-calling loop for an explicitly configured local model.

Endpoints: `GET /health`, `GET /state`, `GET /models`, `POST /chat`, and
`POST /chat/stream` (SSE `token`, `status`, `done`, and `error` events).

The only automatic tools are fixed no-argument read-only diagnostics:
CPU, memory, GPU, filesystem capacity, disk I/O, temperatures, process list,
and sanitized warning/error logs. They are an explicit allowlist over the
existing loopback system-tools service; tool calls have an eight-round limit,
four-second per-request timeout, and 32 KiB result limit.

Read-only telemetry facade: `GET /telemetry/cpu`, `/telemetry/memory`,
`/telemetry/gpu`, `/telemetry/disks`, and `/telemetry/temperatures`. These are
an explicit allowlist over the existing loopback system-tools service; novad
does not reimplement or mutate diagnostics.

It exposes no shell execution, file tools, privilege escalation, persistence,
remote listener, model downloads, or system mutation capability.

Chat requests ask Ollama to retain the selected model for 45 minutes after
use; novad never preloads the model at service start.
