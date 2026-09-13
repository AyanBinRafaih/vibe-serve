#!/usr/bin/env bash
set -euo pipefail
tt=/tmp/vibesys-tui-test-tools/tt
session=$1
worktree=$2
output_dir=$3
label=$4
mkdir -p "$output_dir"
cleanup() {
  "$tt" --session "$session" close >/dev/null 2>&1 || true
}
trap cleanup EXIT
"$tt" --session "$session" run --restart --backend alacritty --cols 150 --rows 40 --cwd "$worktree" --env TERM=xterm-256color bash /tmp/pr737-run.sh "$worktree"
"$tt" --session "$session" wait idle --timeout 5000 || true
"$tt" --session "$session" click text "Round 1"
"$tt" --session "$session" key press Enter
"$tt" --session "$session" expect text "DURABLE WORK" --timeout 10000
"$tt" --session "$session" wait idle --timeout 5000 || true
"$tt" --session "$session" text > "$output_dir/$label.txt"
"$tt" --session "$session" screenshot -o "$output_dir/$label.svg" --zoom 0.5
"$tt" --session "$session" record start "$output_dir/$label.png" --format apng --fps 1 --zoom 0.5
"$tt" --session "$session" wait idle --timeout 3000 || true
"$tt" --session "$session" record stop
cleanup
trap - EXIT
