# NOVA telemetry and local-model-state suitability pilot

**Status:** proposed protocol only. It is not evidence, does not change
`catalog/evidence.json`, and must not be used for a recommendation until it has
been run, reviewed and recorded through the existing evidence process.

## Scope

This is an instruction-following and telemetry-explanation pilot: explain
explicitly supplied telemetry and local-model state accurately to a nonexpert.
It does not establish broad `GENERAL` suitability, coding, tool use, vision,
broad reasoning quality, real sensor access, or a model's sustainable maximum
context. A tie is a useful result.

The same six cases, system instruction, provider request settings, 4K context
limit and output allowance apply to every run. No model receives unstated live
facts. The test profile is a protocol choice, not a claim about the user's
preferred workload.

## Frozen candidates and conditions

Run only these exact installed Ollama artifacts, after reconfirming their local
manifest revisions at execution time:

| Catalog ID | Ollama model ID | Required revision |
|---|---|---|
| `qwen3-8b-q4-k-m` | `qwen3:8b` | `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41` |
| `qwen3-14b-q4-k-m` | `qwen3:14b` | `bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8` |
| `qwen3-30b-q4-k-m` | `qwen3:30b` | `ad815644918f0eaab341c12b67837cc6dd4562342cdaf118f83d5d554cb37226` |

For every run, record: catalog/evidence revision; NOVA/novad build identifier;
Ollama version; GPU driver; model revision; provider/model settings; cache and
offload settings if reported; prompt-template version; host hardware profile;
and test timestamp. Unknown fields stay `UNKNOWN`; do not reconstruct them from
model names or current memory occupancy.

### Required shared inference settings and current blocker

The desired fixed evaluation settings are: 4096-token context, 256-token output
allowance, a predeclared seed schedule `1001, 1002, 1003` (one seed per
repetition), and the same temperature for every run. These settings are not yet
enforceable through NOVA's public chat route, so this protocol must **not** be
executed until a separately authorized path can enforce and record them.

The current route is `POST /chat` or `POST /chat/stream` in
`novad/server.py:Handler.do_POST`. `chat_message()` accepts only `message`;
`run_agent_loop()` always adds NOVA's production `SYSTEM_INSTRUCTIONS` and
`MODEL_TOOLS`; `stream_model_turn()` calls
`OllamaProvider.stream_chat()` in `novad/providers.py`. That adapter sends
`think: true` and `keep_alive: "45m"`, but sends no Ollama `options` object.

Consequently, the concrete current shared settings are only `think: true`,
production system instructions/tools, and a 45-minute keep-alive. Temperature,
seed, `num_ctx`, `num_predict`, a custom system instruction, and a tool-free
request are **unsupported controls on this path**. Their effective values are
`UNKNOWN`; no seed schedule or temperature can be claimed. Thinking is enabled,
but the adapter only notices that thinking occurred and discards its content and
token/time counters. Its cost is therefore `UNKNOWN`.

The 4K/256 limits above remain the protocol target, not a current guarantee. A
future authorized execution path must obtain counts from the *rendered Ollama chat
template*, including role markers and special tokens, after the actual frozen
system/user messages are assembled. Raw text character counts are not sufficient.
Current NOVA intentionally withholds raw templates/tokenizer arrays and exposes
no tokenization endpoint or template-aware count preflight. Provider-reported
prompt/evaluation counts are also discarded from the terminal stream chunk.
This is the blocking preflight gap; do not silently truncate or substitute a raw
text count.

### Frozen system instruction

> Explain the supplied facts for a nonexpert. Use only the supplied facts. State
> uncertainty, missing data and estimates plainly. Do not infer model readiness,
> active work, recovery success, hardware compatibility, sustainable context or
> task performance from memory use, selection, a native maximum, or an omitted
> value. Keep the answer concise.

## Fixed cases

Each case supplies the system instruction above followed by its exact facts and
the question, unchanged across candidates and repetitions. The reviewer checks
the listed expected facts and unacceptable claims literally or by an equivalent
plain-language statement.

| ID | Supplied facts and question | Expected facts | Unacceptable claims |
|---|---|---|---|
| C1 — occupied memory, quiet activity | `GPU memory: 29.7 / 32 GiB. GPU activity: 3%. Sample age: 2 seconds. No process or model identity was supplied.` Ask: “What does this show?” | Memory is highly occupied; current GPU activity is low; occupancy alone does not identify a model or prove active computation/readiness. | High memory means high activity; a named model is running/ready; an unspecified process caused the use. |
| C2 — similar memory, missing/stale activity | `GPU memory: 29.7 / 32 GiB. GPU activity: unavailable. Last successful telemetry sample: 5 minutes ago. No process or model identity was supplied.` Ask: “Is the GPU idle?” | Current activity is unknown because the activity reading is unavailable/stale; memory occupancy does not resolve it. | The GPU is idle or busy now; the stale reading is current; a named model is responsible. |
| C3 — selected is not ready | `Configured model: ollama / qwen3.5:35b. Selected: yes. Runtime-ready confirmation: false. No load attempt result was supplied.` Ask: “Can I assume it is ready to chat?” | Selection/configuration is separate from confirmed readiness; readiness has not been confirmed. | Selected means loaded/ready; it will definitely fail; a load succeeded. |
| C4 — unknown fit is not incompatibility | `Model: qwen3.5:35b. Hardware fit: UNKNOWN. Recommended working context: UNKNOWN. Native advertised maximum: 262144 tokens. Reason: context memory cannot currently be bounded responsibly.` Ask: “Does UNKNOWN mean this model cannot run?” | Fit/context have not been verified; UNKNOWN is not evidence of incompatibility; the native maximum is not a sustainable-context claim. | It cannot run; it supports sustainable 262K; a 32K configuration is proven safe. |
| C5 — estimate differs from advertised maximum | `Model: qwen3:14b. Hardware fit: EXCELLENT. Estimated working context: 32768 tokens (medium confidence). Native advertised maximum: 40960 tokens. Estimate is advisory and does not change chat configuration.` Ask: “How much can it work with at once?” | Explain estimated working context as the practical estimate; distinguish it from the 40960 advertised maximum and label the estimate. | Guaranteed 40960 working context; configuration was changed automatically; native maximum is an observed workload result. |
| C6 — failed switch, no invented recovery | `Configured model before request: qwen3.5:35b. Requested target: qwen3:30b. Switch transaction: FAILED. Target verification: failed. Current configured model observed after failure: qwen3.5:35b. Runtime-ready state of qwen3.5:35b after failure: UNKNOWN.` Ask: “What happened, and what can I safely assume?” | The target was not verified/committed; the configured model remained qwen3.5:35b; post-failure runtime readiness is unknown and needs a fresh check. | qwen3:30b became active/ready; rollback restored a ready chat session; automatic recovery succeeded. |

**Required contrast checks:** C1 and C2 have the same high memory occupancy but
different activity evidence; their correct conclusions differ. C4 and C5 both
mention a native maximum, but only C5 has an estimated working context. A response
that gives them the same conclusion fails the relevant factual or uncertainty
criterion.

## Repetitions and scoring

Run each case three times per model: **3 models × 6 cases × 3 repetitions = 54
responses**. The intended cap is **256 output tokens per response** (13,824
maximum requested output tokens across the pilot, excluding any separately
reported thinking tokens), but that cap is currently blocked as described above.
Three repetitions test repeatability of the same model/settings/input combination;
they are a pilot sample, not general reliability proof.

Score every response against supplied facts with a written checklist; a human
reviewer makes the final score. Another LLM may assist transcription only and
cannot be the sole judge.

| Dimension | Points | Rule |
|---|---:|---|
| Factual correctness | 0–2 | 2: all required facts/conclusions are correct; 1: no false claim but a material required fact is omitted; 0: contradiction or any listed unacceptable claim. |
| Uncertainty handling | 0–2 | 2: missing, stale, estimated or unknown status is preserved; 1: wording is incomplete but does not assert certainty; 0: turns uncertainty into a fact or guarantee. |
| Understandability | 0–1 | 1: concise plain-language explanation for a nonexpert; 0: materially opaque, unexplained jargon, or fails to answer the question. |

Each response totals 0–5. Keep all raw responses, scores, reviewer notes and
disagreements. Correctness and uncertainty take priority over latency or memory.
For each model and case, take the median of its three response scores. Sum the
six per-case medians for the model's **0–30 pilot total**. The predeclared
two-point winner margin is on this 0–30 scale, not on one response or one case.

A model is not a pilot leader if any repetition receives zero on factual
correctness or uncertainty. A case is inconsistent when repetitions differ in a
factual-correctness or uncertainty verdict, include an unacceptable claim,
truncate/error, or span two or more total-score points. Mere wording differences
that preserve the same claims and score do not count as inconsistent. A winner
requires a two-point total advantage over every other candidate and no unresolved
reviewer disagreement or inconsistent case. Equal totals, a smaller margin,
crossed case results, truncation, or inconsistency yield **NO WINNER**. This
pilot alone does not create a recommendation or quality judgment.

## Minimal execution and measurement plan

1. Capture the actual configured selection at experiment start through the
   existing read-only `/model/active` endpoint. Do not assume it is any named
   model. Capture `/models/fit`, `/hardware/profile`, catalog revision/match, and
   any in-progress model operation before beginning.
2. Stop without running if a model operation is already active, a revision differs,
   the provider is unavailable, thinking mode cannot be made common, or the 4K
   occupancy check fails.
3. The existing explicit transactional switch is `POST /model/active` →
   `select_model()` in `novad/server.py`. It validates, preloads, verifies runtime
   identity/readiness, then commits configuration. Time it externally from request
   dispatch to that verified terminal response as **switch-to-verified time**.
   `OllamaProvider.preload()` may return Ollama `load_duration`; record it
   separately when present as provider load timing, never as response time or as
   proof that the model started cold.
4. Do not issue the 18 planned requests until the shared-control and template-count
   blockers above are resolved through separately authorized work. If later
   unblocked, issue them one at a time and measure response time from request
   dispatch to final stream completion. First-token time is unavailable from the
   current stream contract unless a future path exposes it uniformly. Terminal
   token counts and detailed durations are also unavailable on the present route.
5. Use the existing telemetry cadence for every candidate and record its effective
   value: `max(5 seconds, refreshSec)` (the current default is 10 seconds). Do
   not shorten it for one model. Capture read-only hardware/fit snapshots before,
   during and after each response. Record GPU memory, system available memory,
   GPU activity, provider residency and errors when exposed. Report any maximum
   as a **sampled peak**, not a guaranteed true peak. Do not infer offload, model
   identity or readiness from a memory sample.
6. Stop scheduling further requests on OOM, allocation failure, an unexpected
   offload reported by the provider/runtime, provider/model transition error,
   truncation, or interference from another active model operation. Record the
   failed run and reason; do not retry automatically. If actual offload is not
   exposed, record it as `UNKNOWN`; the pilot cannot certify its absence. Stopping
   scheduling does not cancel an already-running `/chat` request: the current API
   has no request-cancellation contract, so no immediate cancellation is promised.
7. Before every switch or request, compare the observed configured selection and
   operation state with the pilot's last confirmed state. If a user selection or
   other operation intervenes, stop scheduling requests and preserve the user's
   latest choice. Restore the exact starting selection only if the pilot still owns
   the operation and no intervening selection occurred. Use the same transactional
   switch path; record success only after normal verification. If restoration fails,
   leave the observed state unchanged and report it. Do not claim a recovered ready
   state without its own readiness evidence.

Existing sources can capture selection, switching, fit and sampled telemetry:
`/model/active`, `/state`, `/models/fit`, `/hardware/profile`, `/catalog/models`,
the `POST /model/active` response, and normal NOVA stream timing. They do not
enforce evaluation options, render/tokenize the exact template for preflight,
preserve terminal token/timing counters, create a benchmark runner, prove true
hardware peaks, or provide an independent judge.

## Safety and cleanup boundaries

The pilot performs no downloads, installation, removal, approval creation, shell
action or catalog/evidence mutation. It uses only normal, explicit model switches
and text inference. Supported cleanup is limited to the normal transactional
restoration attempt; there is no automatic retry, provider cancellation, model
removal, cache cleanup or broad process termination. If a stop condition occurs,
retain logs and state snapshots for review before any separate recovery decision.

## Runtime prerequisites and limitations

- Exact revisions must still match the table at run time.
- All three candidates have current metadata-based fit estimates; this pilot does
  not calibrate Qwen3.5:35B or make it a candidate.
- Dynamic telemetry is sampled and can include NOVA, Ollama and other workloads.
- The current public route blocks execution because it cannot enforce or verify
  temperature, seed, 4K context, 256-token cap, tool-free frozen instructions, or
  rendered-template occupancy. Thinking is fixed on and its token/time cost is not
  exposed. Unknown fields remain unknown.
- If later unblocked, a successful pilot can support only this frozen
  instruction-following/telemetry-explanation scope, environment and context. It
  cannot establish broad GENERAL suitability, coding ability, vision ability,
  sustained maximum context or a global best model.
