#!/usr/bin/env bash
# Run by NOVA's panel: is a newer NOVA published? Read-only: it asks the plugin's
# git remote for its HEAD (`git ls-remote`) and changes nothing in the checkout.
# The answer is kept for 7 days (a failed check for 1 day); --now checks anyway.
#
# Prints one line: <status> <local commit> <remote commit|-> <checked epoch>
#   status: current | available | failed | unsupported (not a git checkout)
# Test hooks: NOVA_UPDATE_NOW (epoch), XDG_STATE_HOME.
set -uo pipefail

main() {
  local dir state now force local_sha status cached_local remote checked age limit
  dir=$(cd -- "$(dirname -- "$0")/.." && pwd)
  state="${XDG_STATE_HOME:-$HOME/.local/state}/nova/update-check"
  now=${NOVA_UPDATE_NOW:-$(date +%s)}
  force=0
  [ "${1:-}" = "--now" ] && force=1

  # Only the plugin's own .git counts, not a repository around ~/.config.
  if [ ! -e "$dir/.git" ] || ! local_sha=$(git -C "$dir" rev-parse HEAD 2>/dev/null); then
    echo "unsupported - - $now"
    return 0
  fi

  if [ "$force" = 0 ] && read -r status cached_local remote checked 2>/dev/null < "$state" \
     && [ "$cached_local" = "$local_sha" ] && [[ $checked =~ ^[0-9]+$ ]]; then
    age=$((now - checked)) limit=604800
    [ "$status" = failed ] && limit=86400
    if [ "$age" -ge 0 ] && [ "$age" -lt "$limit" ]; then
      echo "$status $local_sha $remote $checked"
      return 0
    fi
  fi

  remote=$(GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -oBatchMode=yes}" \
           timeout 20 git -C "$dir" ls-remote origin HEAD 2>/dev/null | cut -f1)
  if ! [[ $remote =~ ^[0-9a-f]{40}$ ]]; then
    status=failed remote=-
  elif [ "$remote" = "$local_sha" ] || git -C "$dir" merge-base --is-ancestor "$remote" HEAD 2>/dev/null; then
    status=current  # same commit, or this checkout is already ahead of it
  else
    status=available
  fi

  mkdir -p "$(dirname "$state")" &&
    printf '%s %s %s %s\n' "$status" "$local_sha" "$remote" "$now" > "$state.tmp" &&
    mv "$state.tmp" "$state"
  echo "$status $local_sha $remote $now"
}

main "$@"
exit
