# Changelog

## 0.14.9

- **Security fix (marketplace review):** text NOVA shows is always shown as plain text.
  Qt labels guess by default whether a text is HTML; ADD A MODEL showed what you typed in
  such a label, so a short HTML image tag made the shell try to load that file, and a
  special file could make the whole shell (top bar and panels) hang. Every label in NOVA
  now shows text exactly as typed or received, and a test fails if one doesn't. Chat
  answers keep their light formatting, which escapes everything first. Thanks again to
  HANCORE-linux.

## 0.14.8

- **Security fix (marketplace review):** NOVA's helpers no longer use network ports
  (`127.0.0.1:8790`, `8787`, `8791`). Up to 0.14.7, the top bar checked on NOVA every few
  seconds, even while NOVA was off, and sent its private key to whatever was listening
  on NOVA's port. Another account on the same computer could have taken that port while
  NOVA was stopped and received the key. Now the helpers listen only on Unix sockets in
  your private runtime folder (`$XDG_RUNTIME_DIR/nova`), which no other account can
  open, and both ends check with the kernel who is on the other side. So nobody else can
  answer in NOVA's place, and there is no key any more: the old key file
  (`~/.config/nova/api.token`) is removed. The same rule now covers Ollama: NOVA sends a
  request (your chat messages included) to `127.0.0.1:11434` only if the kernel says the
  answering end belongs to Ollama's service account, root or you. Thanks again to
  HANCORE-linux.

## 0.14.7

- **Security fix (marketplace review follow-up):** in 0.14.6 a text box still became a
  normal Qt text box once you clicked or typed in it, and from then on (and the moment
  it switched) the shell read the whole clipboard text, with no size or time limit. Now
  NOVA's boxes never become normal Qt text boxes: they handle typing, Backspace, Delete,
  undo, cut and paste themselves. Paste reads the clipboard in a separate helper
  (`wl-paste`), takes at most 128 KB and gives up after 2 seconds, so a huge or stuck
  clipboard can't make the shell (top bar and panels) use a lot of memory or hang. A
  clipboard text that's too long for the box isn't pasted, and NOVA says so. Known
  limit until Qt ships its fix: input methods (fcitx5 for Chinese, Japanese, Korean)
  and dead keys don't work in NOVA's two boxes. Thanks again to HANCORE-linux.

## 0.14.6

- **Security fix (marketplace review):** NOVA's two text boxes (the chat box and ADD A
  MODEL) no longer let the desktop shell read your clipboard in the background. On
  current Qt versions an editable text box reads the whole clipboard text every time it
  changes, with no size limit, even while hidden. Another app could have put a huge or
  never-ending text there and made the shell (top bar and panels) use a lot of memory or
  hang, without anything being pasted. NOVA's boxes now ignore the clipboard until you
  click or type in them, and again once they're hidden or the panel closes. Typing and
  pasting work as before. Thanks to HANCORE-linux for the report.

## 0.14.5

- **Coding CLI, several projects:** when one model has more than one project set up (for
  example NOVA and a second project), OPEN CLI's confirm card lists them. The project you
  opened last is chosen first; ↑ ↓ or a click choose another, Enter launches. Each row
  shows where the project lives, when it was last opened from NOVA, and whether opening
  it will update it.
- One local coding CLI at a time, as before, but now NOVA says which project's CLI is
  open ("The coding CLI for … is open"), and the update line names the right repository
  and branch.

## 0.14.4

- **Fix:** since 0.14.1, NOVA's hardware check no longer received graphics-card details
  from its system-readings helper (the new private key wasn't sent on that internal
  call), so an NVIDIA card's memory size showed as unknown and model-fit estimates were
  less accurate. NOVA now sends the key on every internal call, and a test checks that
  no caller can miss it again.
- **Coding CLI:** a project opened with OPEN CLI syncs to the repository and branch it
  was created from (not always NOVA's own).

## 0.14.3

- Deleting a model now says what it really frees. A model built on another one (the same
  weights with different settings) shares its files, so deleting one of them frees only
  what no other model uses. The delete tooltip, the approval card and the result now
  show the actual amount and name the models that keep the shared files, instead of
  "frees up to …" and a puzzling "freed 0.0 GiB".

## 0.14.2

From our own security review after the marketplace findings (all hardening, nothing
known to be exploitable since 0.14.1):

- The system-readings helper no longer sends CORS headers (left over from an early
  Open WebUI integration); no NOVA helper lets a web page read its answers.
- NOVA keeps its own folders private (`~/.local/state/nova`, `~/.local/share/nova`),
  even if your home folder isn't.
- Stricter model names before reading Ollama's files; constant-time key checks.
- New [SECURITY.md](SECURITY.md): what NOVA protects against, and how to report a
  problem privately.

## 0.14.1

- **Security:** a web page open in your own browser could send requests to NOVA's
  helpers on `127.0.0.1` (cross-site request forgery) or pose as your computer (DNS
  rebinding). Every request now needs a key that only your account can read
  (`~/.config/nova/api.token`), sent by NOVA's own panel, and must be addressed to
  `127.0.0.1` or `localhost`. Found by the Omarchy marketplace review.

## 0.14.0

- **Security:** NOVA's helpers now serve only your own user account. Before, another
  account on the same computer could reach them on `127.0.0.1` and, for example, open
  a configured coding CLI as you. Each connection is now checked against the kernel's
  socket table and refused unless it is yours (found by the Omarchy marketplace review).
- **Model updates.** NOVA checks once a week (or on REFRESH) whether Ollama has a newer
  build of the models you have. The model's card says what changed in plain words
  (chat template and settings, or new weights) and how much it downloads; a dot on
  MODELS shows there is something. **UPDATE** goes through the normal approval card;
  "skip this version" hides that build. A model Ollama changed too much or removed is
  not offered, and the card says why. Nothing ever updates on its own.
- NOVA now describes itself as what it is: one place to pick, install, update and
  manage your local models.

## 0.13.2

From a fresh-install test that followed this README word for word:

- MACHINE shows the memory a loaded model uses on computers without a graphics card
  (it said 0.0 GiB).
- **OPEN CLI** only appears once a coding CLI is set up; on a fresh install it could
  only say that none was configured.
- NOVA describes Omarchy correctly: the desktop it runs in, not a plugin.
- README: the model download button is called **BROWSE MODEL DOWNLOADS**; Omarchy's
  plugin warning is explained; the coding CLI is mentioned.
- The removal message no longer says chats are kept (NOVA doesn't save chats).

## 0.13.1

- Chat answers are formatted: bold, italic, code, code blocks, headings and lists.
  Model output is escaped first, so an answer can never load an image, open a link
  or inject markup.
- NOVA knows what it is: asked "what are you?", it describes its three views, that
  everything runs locally, and which model it's running on.
- New screenshots.

## 0.13.0 — first public release

- **CHAT** with local models through Ollama, with live system lookups and an
  animated status band; messages sent while a model loads wait and send themselves.
- **MODELS**: installed models as cards (load, unload, delete with approval);
  a curated list checked against your memory, graphics card and disk; suggestions
  from published guides; **ADD A MODEL** for anything in Ollama's library.
- **Installs follow Ollama**: before downloading, NOVA checks whether Ollama has
  re-published the model and, if it is still the same model, verifies the new build
  instead of failing after the download.
- **MACHINE**: CPU, memory, graphics (any vendor), disk, network, models in memory,
  versions and the date of the model list.
- Guided first setup (background services, Ollama, first model), weekly update
  check with one-step **UPDATE NOVA**, and `tools/nova-uninstall.sh`.
