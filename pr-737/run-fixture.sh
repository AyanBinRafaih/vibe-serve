#!/usr/bin/env bash
set -euo pipefail
worktree=$1
socket_path=/tmp/pr737-$PPID.sock
python3 /tmp/pr737-reconnect-server.py --socket "$socket_path" &
server_pid=$!
cleanup() {
  kill "$server_pid" 2>/dev/null || true
  wait "$server_pid" 2>/dev/null || true
}
trap cleanup EXIT
for _ in $(seq 1 100); do
  if [[ -S "$socket_path" ]]; then
    break
  fi
  sleep 0.02
done
export VIBESYS_CONTROL_SOCKET="$socket_path"
export VIBESYS_THEME=dark
exec /tmp/vibesys-bun/bin/bun "$worktree/clients/tui/src/index.ts"
