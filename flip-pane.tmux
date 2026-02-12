#!/usr/bin/env bash
# tmux-flip-pane: flip the split that contains the focused pane (v <-> h).
# TPM loads this file; it binds a key to run the script.

CURRENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${CURRENT_DIR}/scripts/flip-pane.py"

# Optional: set in tmux.conf with set -g @flip-pane-key 'M-f' (default: F)
key="$(tmux show-option -gqv @flip-pane-key 2>/dev/null)"
[ -z "$key" ] && key="F"

tmux bind-key "$key" run -b "'$SCRIPT'"
