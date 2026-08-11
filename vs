#!/usr/bin/env bash
# Thin compatibility shim. The launcher is now unified in the `vibesys` command
# (src/vibesys/cli.py); `./vs` just runs it in the repo's uv environment so a
# clone still works with no install. See `vibesys --help`.
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$root_dir"

exec uv run vibesys "$@"
