#!/usr/bin/env bash
# Removes NOVA: its three background services, then the plugin itself
# (`omarchy plugin remove`). Ollama, your downloaded models and ~/.config/nova
# stay; the last lines say how to remove those too if you want.
set -uo pipefail

# Everything runs from main(): the last step deletes this very file.
main() {
  local dir id
  dir=$(cd -- "$(dirname -- "$0")/.." && pwd)
  id=$(basename -- "$dir")

  echo "This removes NOVA's background services and the NOVA plugin."
  echo "Ollama and the models you downloaded stay on this computer."
  echo "Finish any NOVA chat or model download first."
  read -r -p "Press Enter to remove NOVA, or close this window to cancel. " _ || return 1
  echo

  if [ -e "$HOME/.local/state/nova/installer/ownership.json" ]; then
    if ! python3 "$dir/tools/nova_install.py" remove --idle-state idle --confirm-idle; then
      echo
      echo "NOVA's services were not removed. The message above says why; nothing was changed."
      return 1
    fi
  fi
  if ! omarchy plugin remove "$id" --yes; then
    echo
    echo "The services are gone, but the plugin is still listed. Remove it from"
    echo "Omarchy menu > Setup > Plugins, or run: omarchy plugin remove $id"
    return 1
  fi
  echo
  echo "NOVA is removed."
  echo "Optional: list models with 'ollama list', delete one with 'ollama rm <name>'."
  echo "Optional: NOVA's settings and chats are in ~/.config/nova and ~/.local/state/nova."
}
main "$@"
