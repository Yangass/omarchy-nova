# Changelog

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
