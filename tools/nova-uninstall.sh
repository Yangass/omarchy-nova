#!/usr/bin/env bash
# Removes NOVA (REMOVE-01, Konstantin 2026-10-23). NOVA's panel starts it in a terminal with
# the choice made there; run by hand it asks first.
#
#   nova-uninstall.sh                NOVA only, asks first (the README's command)
#   nova-uninstall.sh --nova         NOVA, its settings and chats; Ollama and models stay
#   nova-uninstall.sh --models       ... and every model Ollama has (no password needed)
#   nova-uninstall.sh --everything   ... and Ollama itself with its model folder (asks for
#                                    your password once)
#   add --dry-run to print each step without doing it
#
# Order: models first (through Ollama, so its folder is empty before the package goes),
# then NOVA's helpers, its settings and chats, Ollama, and the plugin last, so the panel
# disappears at the end and not half-way.
set -uo pipefail

OLLAMA_PACKAGES=(ollama ollama-cuda ollama-rocm ollama-vulkan)
OLLAMA_DIR=/var/lib/ollama

# Everything runs from main(): the last step deletes this very file.
main() {
  local dir id scope="" dry=0 asked=0 arg
  dir=$(cd -- "$(dirname -- "$0")/.." && pwd)
  id=$(basename -- "$dir")
  for arg in "$@"; do
    case "$arg" in
      --nova) scope=nova ;;
      --models) scope=models ;;
      --everything) scope=everything ;;
      --dry-run) dry=1 ;;
      *) echo "Unknown option: $arg"; return 2 ;;
    esac
  done
  if [ -z "$scope" ]; then scope=nova; asked=1; fi

  exec 3>&1  # a dry run says each step here, even where the step's own output is hidden
  run() {  # one step: do it (or, in a dry run, only say it)
    if [ "$dry" = 1 ]; then echo "  [dry run] $*" >&3; return 0; fi
    "$@"
  }
  done_step() { printf '\033[32m✓\033[0m %s\n' "$1"; }
  fail_step() { printf '\033[31m✗\033[0m %s\n' "$1"; }

  case "$scope" in
    nova) echo "Removing NOVA: its helpers, settings and chats. Ollama and your models stay." ;;
    models) echo "Removing NOVA and every model Ollama has. Ollama itself stays." ;;
    everything) echo "Removing NOVA, every model, and Ollama itself." ;;
  esac
  [ "$dry" = 1 ] && echo "Dry run: nothing is changed."
  if [ "$asked" = 1 ]; then
    echo "Finish any NOVA chat or model download first."
    read -r -p "Press Enter to remove NOVA, or close this window to cancel. " _ || return 1
  elif [ "$dry" = 0 ]; then
    local second
    for second in 5 4 3 2 1; do printf '\rClose this window now to cancel. Starting in %s… ' "$second"; sleep 1; done
    printf '\r%60s\r' ""
  fi
  echo

  local left=()
  if [ "$scope" != nova ]; then
    if command -v ollama >/dev/null 2>&1 && ollama list >/dev/null 2>&1; then
      local model running
      while read -r running; do
        [ -n "$running" ] && run ollama stop "$running" >/dev/null 2>&1
      done < <(ollama ps 2>/dev/null | awk 'NR > 1 { print $1 }')
      while read -r model; do
        [ -z "$model" ] && continue
        if run ollama rm "$model" >/dev/null; then done_step "Deleted $model"
        else fail_step "Couldn't delete $model"; left+=("the model $model"); fi
      done < <(ollama list 2>/dev/null | awk 'NR > 1 { print $1 }')
    elif [ "$scope" = models ]; then
      fail_step "Ollama isn't running, so no model was deleted"; left+=("your models (start Ollama, then run this again)")
    fi
  fi

  if [ -e "$HOME/.local/state/nova/installer/ownership.json" ]; then
    if run python3 "$dir/tools/nova_install.py" remove --idle-state idle --confirm-idle >/dev/null; then
      done_step "Removed NOVA's helpers (novad, nova-actions, nova-system-tools)"
    else
      fail_step "NOVA's helpers were not removed; the message above says why. Nothing else was changed."
      return 1
    fi
  fi
  run rm -rf -- "$HOME/.config/nova" "$HOME/.local/state/nova" "$HOME/.local/share/nova"
  done_step "Removed NOVA's settings and chats (~/.config/nova, ~/.local/state/nova)"

  if [ "$scope" = everything ]; then
    local installed=() pkg
    for pkg in "${OLLAMA_PACKAGES[@]}"; do pacman -Qq "$pkg" >/dev/null 2>&1 && installed+=("$pkg"); done
    if [ "${#installed[@]}" -gt 0 ] || [ -d "$OLLAMA_DIR" ]; then
      echo "Removing Ollama needs your password:"
      run sudo systemctl disable --now ollama.service >/dev/null 2>&1
      local dropped=1
      if [ "${#installed[@]}" -gt 0 ]; then run omarchy-pkg-drop "${installed[@]}" || dropped=0; fi
      if [ "$dropped" = 1 ]; then
        # What's left of the models and Ollama's key. On a separate Btrfs subvolume or
        # mount the (now empty) folder itself may stay.
        run sudo rm -rf -- "$OLLAMA_DIR"
        if [ "$dry" = 1 ] || [ ! -e "$OLLAMA_DIR" ] || [ -z "$(sudo ls -A "$OLLAMA_DIR" 2>/dev/null)" ]; then
          done_step "Removed Ollama${installed:+ (${installed[*]})} and $OLLAMA_DIR"
        else
          fail_step "Ollama is removed, but $OLLAMA_DIR is not empty"; left+=("$OLLAMA_DIR")
        fi
      else
        fail_step "Ollama was not removed"; left+=("Ollama (${installed[*]})")
      fi
    fi
  fi

  if run omarchy plugin remove "$id" --yes >/dev/null; then
    done_step "Removed the NOVA plugin from the bar"
  else
    fail_step "The NOVA plugin is still listed; remove it in Omarchy menu > Setup > Plugins"
    left+=("the NOVA plugin")
  fi
  echo
  if [ "${#left[@]}" -gt 0 ]; then
    echo "NOVA is removed, except: ${left[*]}."
  elif [ "$scope" = everything ]; then
    echo "NOVA is removed. Nothing of it is left on this computer."
  elif [ "$scope" = models ]; then
    echo "NOVA and its models are removed. Ollama stays for other tools."
  else
    echo "NOVA is removed. Ollama and your models stay ('ollama list' shows them, 'ollama rm <name>' deletes one)."
  fi
  [ "$dry" = 1 ] && echo "(dry run: nothing was changed)"
  [ "$asked" = 1 ] || read -r -p "Press Enter to close. " _
  return 0
}
main "$@"
