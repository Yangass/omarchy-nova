# Changelog

## 0.16.0

- **MODELS reads at a glance.** Each model is a card: the model on top, its coding CLI
  below it, and each row has its own buttons. The facts are short words: **FITS**, **TIGHT**
  or **TOO BIG**, the working context, and what the model is doing. The long explanations
  are behind **DETAILS**. **BROWSE DOWNLOADS** sits next to REFRESH, and online CLIs take one
  line each.
- **Downloads: two lines per model, and filters.** Each model shows its name, size and
  INSTALL, then FITS · context · what it's for. **SUGGESTED** (the default), **FITS** and
  **ALL** replace the long suggestion banner and the "too big" toggle. The guide's quote and
  how sure NOVA is are behind DETAILS. "Not listed?" (any Ollama model) is at the end.
- **The biggest model that fits comes first** on a computer with a graphics card. On a
  32 GB card, a 1B model came first because the guides NOVA quotes are about small cards.
  Without a graphics card, the smallest still comes first, because it answers faster.
- **SET UP and TUNE are simpler.** SET UP is three labelled choices: folder, when it asks,
  and context, with the fit as one word. TUNE shows one number, one word, a memory bar and
  one short reason, with marks only at the recommended context and at what the model was
  trained for.
- **Remove NOVA from the panel.** **MACHINE → REMOVE NOVA…**, or right-click NOVA's icon in
  the bar. Choose Everything (NOVA, its settings and chats, every model and Ollama), NOVA
  and its models, or NOVA only. Each choice shows the space it frees, and every model that
  goes is listed first. A terminal shows each step and asks for your password only to
  remove Ollama.

## 0.15.1

- **Installing a model works again with Ollama 0.40 and newer.** Every model downloaded
  completely, and then NOVA said "installation failed". Ollama 0.40 stores each model's
  description in a new place. It also adds one entry to the old copy that NOVA checked, so
  NOVA's check that the download is exactly the model it offered always failed. NOVA now
  reads both places the way Ollama does: the new one first, and the old one through its
  new entry. The same fix makes model updates and the "frees … GiB" figure on delete
  correct again.
- **A model's coding assistant goes with it.** Deleting a model now also deletes its coding
  assistant build and its OPEN CLI, and the approval says so. Before, the build stayed
  behind, out of sight in the model list, and kept the model's files: the delete freed
  almost nothing. A build whose model was removed outside NOVA now shows in the list, so
  you can delete it.
- **After a model update, its coding assistant uses the new version.** NOVA rebuilds the
  build with the same working context. Before, the assistant kept running the old version,
  and the old files stayed on disk.

## 0.15.0

- **A coding assistant on your own model, in one approval.** Omarchy installs OpenCode
  for every user. **SET UP CODING CLI** on a model's card connects it: NOVA makes a build
  of the model with the right working context (from the files you have, no download),
  adds **OPEN CLI** for a folder you choose, and starts OpenCode with NOVA's settings only.
  Your own OpenCode settings are not touched. It asks before it edits files or runs commands
  (or edits freely and asks before commands, if you choose).
- **TUNE: how much it keeps in mind, with what fits.** A slider over the working contexts
  NOVA offers, a meter of your graphics card's memory, and plain words: fits, tight (little
  room left for the desktop), doesn't fit (it spills into system memory: several times
  slower), or past what the model was trained for. Anything but a plain fit asks
  "are you sure?" first.
- **The coding assistant can ask NOVA for more context itself.** NOVA shows its request
  with the same warnings and, when it doesn't fit, the most that does; you decide.
- **A welcome once your first model is ready** (and once after this update): what NOVA set
  up, the working context it chose and why, three things to try, and the coding CLI.

## 0.14.15

- **Chat replies finish, and approvals show up.** A reply ended with "NOVA · INCOMPLETE" and
  "novad stream ended early" although its text was all there, and a request that needed your
  approval (such as opening an app) never showed its card and expired after two minutes. The
  panel waited for the empty line that ends each message from NOVA's service, and the shell
  hands over an empty line only when more text follows, so the last message of each burst
  waited: the approval until it expired, the end of the reply for good. Every message now
  counts as soon as its data arrives. Found on a friend's new install; thanks for the reports.
- **A reply's time limit counts from the last thing NOVA's service sent**, so a slow approval
  or a model that thinks for a long time no longer cuts the reply short (NOVA's service now
  says "still thinking" every few seconds).
- **Models get the working context NOVA estimated for them.** NOVA showed an estimate (for
  example 32K) but never asked Ollama for it, so models ran with Ollama's default of 4096
  tokens and lost the start of longer conversations. Now NOVA loads and chats with the model's
  own maximum, up to 32K, as far as it fits your graphics card. A model you built with its own
  context size keeps it.

## 0.14.14

- **Security hardening (same review):** a registry read now has a time limit for the whole
  answer, not only for each piece of it, so a server that sends a byte now and then can't
  keep a model check or ADD A MODEL waiting for hours.

## 0.14.13

- **Security fix (marketplace review):** reading Ollama's model list can't fill NOVA's
  memory. When an address answers with a redirect, Python reads the whole redirect body
  before following it, before NOVA's 1 MB read limit applies, so a compromised registry
  or CDN could send an endless one. NOVA now drops a redirect's body unread, follows the
  registry's redirects only to https, and novad's service has a 1 GB memory cap as a
  backstop. Thanks again to HANCORE-linux.

## 0.14.12

- **Text is readable again after the Qt 6.12 update.** Qt 6.12, which came with Omarchy's
  October update, added its own `Color`, and it hid the Omarchy theme's colours from NOVA:
  the panel's text turned black and the bar icon lost its colour. NOVA now asks Omarchy's
  theme for its colours by their full name, as Omarchy's own shell does.

## 0.14.11

- **OPEN CLI waits for its model.** A local coding CLI opens on a model that is already
  in GPU memory. Until it is, and while NOVA is loading it, OPEN CLI is greyed out and its
  tooltip says to load the model first. Before, a terminal opened and closed at once
  without a word. If the CLI still can't start, its window now stays open with the reason
  until you press Enter.

## 0.14.10

- **Security hardening (NOVA's own review):** a chat answer can no longer slow down the
  shell. Formatting a long answer full of unclosed `**`, `[` or `![` took up to a tenth
  of a second, and NOVA did it again for every word as the answer streamed in, so such
  an answer could keep the top bar and panels busy for minutes. Formatting now takes
  about the same time as for plain text, and a streaming answer is redrawn at most every
  80 ms (a finished one at once).
- **Security hardening (NOVA's own review):** OPEN CLI reads a coding agent's project
  only as small regular files. A link out of the project, a pipe or a huge file left
  there by the agent can't stall NOVA's action helper or fill its memory.

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
