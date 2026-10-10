#!/usr/bin/env bash
# Run by NOVA's panel ("SET UP NOVA") after `omarchy plugin add <NOVA git URL>`,
# inside Omarchy's floating terminal. Installs NOVA's three background services
# for the current user from this plugin checkout. No administrator password.
set -uo pipefail
dir=$(cd -- "$(dirname -- "$0")/.." && pwd)

echo "Setting up NOVA's background services for $USER."
echo "They run only for you, on this computer, and never install or remove models."
echo
if ! command -v python3 >/dev/null; then
  echo "NOVA needs Python 3, which a standard Omarchy install includes. It wasn't found."
  exit 1
fi
operation=(setup --source "$dir")
if [ -e "$HOME/.local/state/nova/installer/ownership.json" ]; then
  # Already installed (for example after `omarchy plugin update`): bring the
  # services up to date. You started this yourself, so it counts as confirming
  # that no NOVA chat, approval or download is running right now.
  echo "NOVA is already installed; updating its background services to this version."
  echo "Close any running NOVA chat or download first."
  echo
  operation=(update --source "$dir" --idle-state idle --confirm-idle)
fi
if python3 "$dir/tools/nova_install.py" "${operation[@]}"; then
  echo
  echo "Next: in the NOVA panel, follow the next step it shows (for example installing Ollama)."
else
  echo
  echo "Setup did not finish. Nothing half-installed is left behind: NOVA restores the"
  echo "previous state on failure. The message above says what blocked it."
  exit 1
fi
