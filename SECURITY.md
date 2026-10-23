# Security

NOVA runs background helpers on your computer and can download models and start
apps, so it is built to do that only for you and only after you approve.

## What NOVA protects against

- **Other accounts on the same computer.** NOVA's helpers don't use network ports. They
  listen only on Unix sockets in your private runtime folder (`$XDG_RUNTIME_DIR/nova`),
  which no other account can open. So another account can't reach them, and can't pose
  as them either, even while NOVA is off. Both ends also ask the Linux kernel which
  account is on the other side, on every connection.
- **A stand-in for Ollama.** Ollama listens on `127.0.0.1:11434`. Before NOVA sends it
  anything (your chat messages included), the kernel must confirm that the answering end
  belongs to Ollama's service account, root or you. Another account listening on that
  port while Ollama is stopped gets nothing.
- **Web pages in your browser.** A browser can't open a Unix socket, so no web page can
  send NOVA a request. NOVA sends no CORS headers, and there is no key to steal.
- **The chat model.** It can suggest an action but never approve one. Every change
  (downloads, updates, deletions, restarts, ending a process, opening an app) waits for
  your click on an approval card, once, before it expires. Model answers are escaped
  before formatting, so they can't load images, follow links or inject markup, and
  formatting takes about as long as for plain text, so an odd answer can't slow down
  the shell.
- **Wrong downloads.** Every model is verified against the exact build (manifest
  digest and size) shown on its approval card. An update may replace only the exact
  older build you approved replacing.
- **Text that looks like HTML.** Every label in NOVA shows text as plain text, so a
  model name, a typed reference or a service answer can't add images or markup, and
  can't make the shell load a file. Chat answers are escaped before their light
  formatting is added.
- **The coding CLI asking for changes.** OpenCode gets one NOVA tool. It talks to novad
  over NOVA's private socket, reads its own model settings, and can ask for a different
  working context. Nothing changes until you approve it in NOVA's panel, at most one
  request every 20 seconds. By default it asks before it edits files or runs commands.
- **Other apps' clipboard contents.** NOVA's text boxes are always read-only to Qt, so
  the shell never reads the clipboard for them, not even while you type. Paste reads it
  in a separate helper, at most 128 KB and for at most 2 seconds. Another app can't make
  the shell take in a huge or never-ending clipboard text through NOVA.
- **Privileges.** Nothing runs as root. The only password prompt is the one-time Ollama
  install, in a terminal you can read first.

Details: [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md).

## Not covered by NOVA

- **Ollama** has its own API on `127.0.0.1:11434`, which other local programs can use
  (that is how Ollama is designed).
- **Updates** come from this repository through Omarchy's plugin updater, so they are as
  trustworthy as this repository.
- **A compromised user account**: malware already running as you can do what you can.

## Reporting a problem

Please report security problems privately through
[GitHub's private vulnerability reporting](https://github.com/Yangass/omarchy-nova/security/advisories/new)
rather than a public issue. Include what you found and how to reproduce it. You'll
get an answer, and credit in the changelog if you'd like.
