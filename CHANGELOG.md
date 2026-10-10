# Changelog

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
