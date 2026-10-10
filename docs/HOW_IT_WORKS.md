# How NOVA works

## The pieces

```
 Omarchy bar ── NOVA panel (Panel.qml, QML in the Omarchy shell)
                    │  HTTP, 127.0.0.1 only
                    ▼
               novad :8790 ──────────► Ollama :11434   (chat, models, downloads)
                 │        └──────────► Ollama library  (model size + exact version, when you install)
                 ├─► nova-system-tools :8787   read-only telemetry (CPU, memory, GPU, disk, network)
                 └─► nova-actions :8791        a fixed list of approved actions, token-protected
```

- **Panel** (`Panel.qml`, `BarWidget.qml`): everything you see. It holds no secrets
  and makes no decisions about safety; it shows what the services report and sends
  your clicks. Chat answers get light formatting (bold, code, lists); the model's
  text is escaped first, so an answer can't load images, follow links or add markup.
- **novad** (`novad/`): the brain. Chat with tool calls, model selection and loading,
  the model catalog, memory-fit estimates, approvals and the install lifecycle.
  Python standard library only.
- **nova-system-tools**: read-only system telemetry for the MACHINE view and for
  NOVA's own answers.
- **nova-actions**: the only service that changes anything outside NOVA. It knows a
  fixed set of actions (restart a NOVA service, end one of your processes, open a
  known app such as the terminal) and runs one only after you approve it in the panel.

All three run as **systemd user services** for your account (`~/.config/systemd/user`),
installed by `tools/nova_install.py` when you click SET UP NOVA. Nothing runs as root.

## Models

`catalog/models.json` is NOVA's curated list. Every entry pins one exact Ollama build
(the sha256 of its registry manifest) with its size, architecture and context length,
read from the model file's header without downloading it (`tools/catalog_probe.py`).

**Fit.** For your machine NOVA estimates whether a model fits: weights plus the
memory its context needs (from the model's attention layout), against your graphics
memory or, without a dedicated card, your RAM. Models that would not fit, or only
tightly, are marked before you download anything.

**Install.** You click INSTALL → novad checks Ollama's library for the current build:

- same build as the list → nothing changes;
- a newer build of the **same** model (same architecture and quantization, size
  within 25%) → NOVA re-reads its size and fit and pins that build for this install;
- a different model under the same name, or a removed one → NOVA refuses and says why.

Then an approval card shows size, disk space after install and fit. After you
approve, Ollama downloads the model and NOVA verifies the result is exactly the
pinned build; anything else is reported as a failure, never silently accepted.

**ADD A MODEL** probes any model from Ollama's library the same way and lists it
as one you added (not curated by NOVA).

**Updates.** Once a week (or when you click REFRESH) novad reads the live manifest of
every installed model NOVA knows, a few KB each. Same build: nothing to do. A newer
build of the same model (the guard above) is offered with what changed: if its weights
layer matches the one on disk, only the chat template or settings changed and the
download is just the missing small layers; otherwise it's new weights. A different
model under the same name, or a removed one, is not offered; the card says why.
UPDATE goes through the same approval and verification as an install, and Ollama may
replace only the exact older build named in the approved proposal. Until the new build
is complete, the old one keeps working.

## Approvals

Anything that changes your computer, including model downloads and deletions,
service restarts, ending a process and opening apps, goes through an approval card in the
panel. The model in CHAT can *propose* an action; it cannot approve one. Approvals
expire, and each new action needs a new approval.

## Only your account

Any account on a computer can connect to `127.0.0.1`, so a loopback port alone doesn't
say who is asking. All three helpers accept a connection only after the kernel's socket
table (`/proc/net/tcp`) shows that its client end belongs to the user they run as;
anything else, including an unclear answer, closes the connection before a request is
read. nova-actions additionally requires a secret key that only you can read
(`~/.config/nova/actions.token`, mode 0600). Thanks to the Omarchy marketplace review for
spotting that an earlier version trusted any local caller.

## Files NOVA keeps

| Where | What |
|---|---|
| `~/.config/omarchy/plugins/io.github.yangass.nova/` | the plugin (this repository) |
| `~/.local/share/nova/runtime/` | the installed services |
| `~/.config/nova/` | settings (selected model, coding CLI profiles, action token) |
| `~/.local/state/nova/` | install records, panel state, models you added, update check results |

Models themselves are stored by Ollama, not by NOVA.

## Testing

- `cd novad && python3 -m unittest discover -p 'test_*.py'`: backend unit tests.
- `python3 -m unittest discover -s tests`: installer, catalog and update tools.
- `tools/run_qml_checks.sh`: the real panel driven in a private nested Hyprland
  against fake services, with click-through checks of every view.
