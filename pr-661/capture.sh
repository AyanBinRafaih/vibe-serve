#!/usr/bin/env bash
set -euo pipefail

tt=/tmp/vibesys-tui-test-tools/tt
artifact_dir=/tmp/vibesys-pr-screenshots/pr-661
mkdir -p "$artifact_dir"

capture() {
  local session=$1
  local worktree=$2
  local fixture=$3
  local stem=$4

  "$tt" --session "$session" run \
    --backend alacritty \
    --cols 160 \
    --rows 45 \
    --cwd "$worktree" \
    --env TERM=xterm-256color \
    --env PATH=/tmp/vibesys-bun/bin:/usr/local/bin:/usr/bin:/bin \
    --restart \
    "$worktree/clients/tui/dev/mock-ui.sh" --fixture "$fixture" --speed 0 --theme dark
  "$tt" --session "$session" wait idle --timeout 15000
  "$tt" --session "$session" key press Enter
  "$tt" --session "$session" wait idle --timeout 15000
  "$tt" --session "$session" key press Enter
  "$tt" --session "$session" wait idle --timeout 15000
  "$tt" --session "$session" key press F4
  "$tt" --session "$session" wait idle --timeout 5000
  "$tt" --session "$session" expect text "1 attempt(s)" --timeout 15000
  if [[ "$stem" == "before" ]]; then
    "$tt" --session "$session" expect text "bytes omitted" --timeout 15000
    "$tt" --session "$session" expect text "fatal compiler error" --not --timeout 1000
  else
    "$tt" --session "$session" expect text "fatal compiler error" --timeout 15000
    "$tt" --session "$session" expect text "[truncated]" --timeout 15000
  fi
  "$tt" --session "$session" text > "$artifact_dir/$stem.txt"
  "$tt" --session "$session" screenshot -o "$artifact_dir/$stem.svg" --full
  "$tt" --session "$session" record start "$artifact_dir/$stem.png" --fps 1 --zoom 1
  # Recording samples at 1 fps. Leave one sample interval after the settled
  # state so the single-frame PNG contains the complete screen.
  sleep 2
  "$tt" --session "$session" record stop
  "$tt" --session "$session" close
}

trap '"$tt" --session pr661-before close >/dev/null 2>&1 || true; "$tt" --session pr661-after close >/dev/null 2>&1 || true' EXIT

capture pr661-before /tmp/vibesys-pr661-before /tmp/vibesys-pr661-before.jsonl before
capture pr661-after /tmp/vibesys-pr661-after /tmp/vibesys-pr661-after.jsonl after
