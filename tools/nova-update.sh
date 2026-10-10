#!/usr/bin/env bash
# Run by NOVA's panel ("UPDATE NOVA") in Omarchy's floating terminal, for a
# git-managed install (Omarchy menu > Add Plugin). Pulls the new version with
# Omarchy's plugin updater, updates NOVA's background services, and restarts the
# shell so the panel loads the new version. Asks before changing anything.
set -uo pipefail

# Everything runs from main(): bash reads a script as it goes, and the plugin
# update below rewrites this very file.
main() {
  local dir id
  dir=$(cd -- "$(dirname -- "$0")/.." && pwd)
  id=$(basename -- "$dir")

  echo "A newer NOVA is available."
  echo "Updating replaces NOVA's files with the new version, updates its background"
  echo "services and restarts the Omarchy shell (the bar blinks once). Your models,"
  echo "chats and settings stay as they are."
  echo
  echo "Finish any NOVA chat or model download first."
  read -r -p "Press Enter to update, or close this window to cancel. " _ || return 1
  echo

  if ! omarchy plugin update "$id" --yes; then
    echo
    echo "NOVA was not updated. The message above says why; nothing was changed."
    return 1
  fi
  echo
  if ! "$dir/tools/nova-setup.sh"; then
    return 1
  fi
  "$dir/tools/nova-update-check.sh" --now >/dev/null 2>&1
  echo
  echo "Done. Restarting the shell so the panel shows the new version."
  sleep 2
  omarchy restart shell
}

main "$@"
exit
