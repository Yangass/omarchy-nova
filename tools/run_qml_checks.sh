#!/usr/bin/env bash
# Run QML interaction checks in a private nested Hyprland (no labwc needed).
#   tools/run_qml_checks.sh                     # every tools/check_*.py
#   tools/run_qml_checks.sh check_a.py check_b.py
# Prints each check's output and "exit <code>"; the log is kept in a temp dir.
# Baseline (2026-10-08): all pass except a geometry pair that depends on the model
# list's height in the nested compositor and trades places whenever the panel's
# content height changes: either check_ui02a's two scroll cases or check_open_cli
# "card in view" (and once check_short_screen "telemetry again");
# check_approval_interaction is occasionally timing-flaky.
set -uo pipefail
tools=$(cd -- "$(dirname -- "$0")" && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/nova-qml-checks.XXXXXX")
log="$work/checks.log"; : > "$log"
if [ $# -eq 0 ]; then set -- $(cd "$tools" && ls check_*.py); fi
{
  echo '#!/bin/bash'
  echo "cd '$tools'"
  # Checks must never touch the real user state (2026-10-08: a check wrote the
  # panel's seen-catalog file into ~/.local/state/nova).
  echo "mkdir -p '$work/state'; export XDG_STATE_HOME='$work/state'"
  for check in "$@"; do
    echo "echo '=== $check' >> '$log'; python3 '$check' >> '$log' 2>&1; echo \"exit \$?\" >> '$log'"
  done
  echo 'hyprctl dispatch exit'
} > "$work/inner.sh"
chmod +x "$work/inner.sh"
printf 'monitor=,1920x1080,auto,1\nexec-once = %s\nmisc { disable_hyprland_logo = true }\n' "$work/inner.sh" > "$work/hypr.conf"
timeout 900 Hyprland -c "$work/hypr.conf" >/dev/null 2>&1
cat "$log"
echo "(log: $log)"
