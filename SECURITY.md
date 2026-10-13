# Security

NOVA runs background helpers on your computer and can download models and start
apps, so it is built to do that only for you and only after you approve.

## What NOVA protects against

- **Other accounts on the same computer.** NOVA's helpers listen only on `127.0.0.1`
  and refuse any connection the Linux kernel doesn't attribute to your account.
- **Web pages in your browser.** Every request must carry a private key from
  `~/.config/nova/api.token` (readable only by you, sent only by NOVA's panel) and be
  addressed to `127.0.0.1` or `localhost`. That blocks cross-site request forgery and
  DNS rebinding. NOVA sends no CORS headers.
- **The chat model.** It can suggest an action but never approve one. Every change
  (downloads, updates, deletions, restarts, ending a process, opening an app) waits for
  your click on an approval card, once, before it expires. Model answers are escaped
  before formatting, so they can't load images, follow links or inject markup.
- **Wrong downloads.** Every model is verified against the exact build (manifest
  digest and size) shown on its approval card. An update may replace only the exact
  older build you approved replacing.
- **Other apps' clipboard contents.** NOVA's text boxes stay read-only, so the shell
  doesn't read the clipboard for them, until you click or type in them. Another app
  can't make the shell take in a huge clipboard text through NOVA in the background.
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
