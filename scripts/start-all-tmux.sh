#!/usr/bin/env bash
# Start every process in one tmux session, one window per process.
# Usage: scripts/start-all-tmux.sh        then: tmux attach -t incident-demo
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux isn't installed. Either install it (macOS: brew install tmux; Ubuntu: sudo apt install tmux),"
  echo "or use the launcher that needs no tmux:  scripts/start-all.sh   (or: make up)"
  exit 1
fi
SESSION=incident-demo

tmux has-session -t "$SESSION" 2>/dev/null && { echo "Session $SESSION already exists"; exit 0; }

tmux new-session -d -s "$SESSION" -n temporal "make temporal"
sleep 3
tmux new-window -t "$SESSION" -n mcp    "make mcp"
tmux new-window -t "$SESSION" -n java   "make java"
tmux new-window -t "$SESSION" -n worker "make worker"
tmux new-window -t "$SESSION" -n api    "make api"
tmux new-window -t "$SESSION" -n ui     "make ui"
tmux new-window -t "$SESSION" -n shell
echo "Started. Attach with: tmux attach -t $SESSION   (Ctrl-b n / p to switch windows)"
