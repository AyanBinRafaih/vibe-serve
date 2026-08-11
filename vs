#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$root_dir"

temporary_files=()
cleanup() {
  if [[ "${#temporary_files[@]}" -gt 0 ]]; then
    rm -f "${temporary_files[@]}"
  fi
}
trap cleanup EXIT

interactive=true
if [[ ! -t 0 || ! -t 1 ]]; then
  interactive=false
fi
if [[ "${1:-}" == "validate" ]]; then
  interactive=false
fi
for argument in "$@"; do
  if [[ "$argument" == "--headless" ]]; then
    interactive=false
    break
  fi
done

# VS_PREPARE_PLAN inspects the preparation logic without needing a toolchain:
#   VS_PREPARE_PLAN=1       print the steps this tree needs, then exit
#   VS_PREPARE_PLAN=record  record the tree as prepared, then exit, for a
#                           caller (CI, a packager) that built the client itself
plan_only="${VS_PREPARE_PLAN:-}"

entrypoint="clients/tui/dist/index.js"
generated_schema="clients/tui/src/generated/protocol.schema.json"
# Each step records the content it last succeeded against. Comparing content
# rather than timestamps means a checkout, a pull, or a rewrite that leaves the
# bytes identical costs nothing.
stamp_dir=".vs-cache"

# The Python the generated client types are derived from. The rest of
# src/vibesys/server is backend implementation the client never sees, so
# editing it does not imply a client rebuild.
protocol_sources=(
  src/vibesys/server/protocol.py
  src/vibesys/server/events.py
  src/vibesys/server/schema.py
)
client_sources=(
  clients/tui/src
  clients/tui/package.json
  clients/tui/tsconfig.json
  clients/tui/tsconfig.check.json
)
dependency_sources=(
  package.json
  pnpm-lock.yaml
  pnpm-workspace.yaml
  clients/tui/package.json
)

if command -v shasum >/dev/null 2>&1; then
  hash_command=(shasum -a 256)
elif command -v sha256sum >/dev/null 2>&1; then
  hash_command=(sha256sum)
else
  hash_command=(cksum)
fi

# One digest over the content of the given paths, directories walked. A path
# that does not exist contributes its absence, so deleting an input is a change.
content_digest() {
  local path
  for path in "$@"; do
    if [[ -d "$path" ]]; then
      find "$path" -type f -print | LC_ALL=C sort | while IFS= read -r file; do
        "${hash_command[@]}" "$file"
      done
    elif [[ -f "$path" ]]; then
      "${hash_command[@]}" "$path"
    else
      echo "missing $path"
    fi
  done | "${hash_command[@]}" | cut -d' ' -f1
}

stamp_matches() {
  local name="$1"
  shift
  local stamp="$stamp_dir/$name"
  [[ -f "$stamp" ]] && [[ "$(cat "$stamp")" == "$(content_digest "$@")" ]]
}

write_stamp() {
  local name="$1"
  shift
  mkdir -p "$stamp_dir"
  content_digest "$@" >"$stamp_dir/$name"
}

# Which preparation steps this tree needs, one per line, in run order. Each
# step answers to its own inputs, so an unrelated edit costs nothing.
prepare_plan() {
  local install=false protocol=false build=false
  if [[ ! -d node_modules || ! -d clients/tui/node_modules ]] ||
    ! stamp_matches install "${dependency_sources[@]}"; then
    install=true
  fi
  if [[ ! -f "$generated_schema" ]] || ! stamp_matches protocol "${protocol_sources[@]}"; then
    protocol=true
  fi
  if [[ ! -f "$entrypoint" ]] || ! stamp_matches build "${client_sources[@]}"; then
    build=true
  fi
  # Regeneration rewrites the client's generated types, and new dependencies
  # change what the compiler sees; either one implies a build.
  if [[ "$protocol" == true || "$install" == true ]]; then
    build=true
  fi
  if [[ "$install" == true ]]; then
    echo install
  fi
  if [[ "$protocol" == true ]]; then
    echo protocol
  fi
  if [[ "$build" == true ]]; then
    echo build
  fi
}

if [[ -n "$plan_only" ]]; then
  if [[ "$plan_only" == "record" ]]; then
    write_stamp install "${dependency_sources[@]}"
    write_stamp protocol "${protocol_sources[@]}"
    write_stamp build "${client_sources[@]}"
  else
    prepare_plan
  fi
  exit 0
fi

if [[ "$interactive" == true ]]; then
  if ! command -v bun >/dev/null 2>&1 && [[ -x "$HOME/.bun/bin/bun" ]]; then
    export PATH="$HOME/.bun/bin:$PATH"
  fi
  if ! command -v bun >/dev/null 2>&1; then
    echo "vs: Bun is required by the OpenTUI client. Install it from https://bun.sh." >&2
    exit 1
  fi

  if ! command -v node >/dev/null 2>&1 && [[ -s "${NVM_DIR:-$HOME/.nvm}/nvm.sh" ]]; then
    # nvm is optional. Load it only as one possible way to satisfy the
    # frontend rebuild and launcher dependency; runtime agent CLIs are checked
    # separately.
    source "${NVM_DIR:-$HOME/.nvm}/nvm.sh"
    nvm use node >/dev/null 2>&1 || true
  fi
  if ! command -v node >/dev/null 2>&1; then
    echo "vs: Node.js 20+ is required by the interactive client." >&2
    exit 1
  fi

  node_major="$(node --version | sed -E 's/^v([0-9]+).*/\1/')"
  if [[ "$node_major" -lt 20 ]]; then
    echo "vs: Node.js 20+ is required; found $(node --version)." >&2
    exit 1
  fi

  # macOS ships bash 3.2, so no mapfile: the plan is a newline-separated list
  # of single-word steps, which word-splits safely.
  plan="$(prepare_plan)"

  if [[ -n "$plan" ]]; then
    step_total="$(printf '%s\n' "$plan" | grep -c .)"
    if command -v pnpm >/dev/null 2>&1; then
      pnpm_command=(pnpm)
    elif command -v corepack >/dev/null 2>&1; then
      pnpm_command=(corepack pnpm)
    else
      echo "vs: pnpm is required. Install pnpm or enable Corepack." >&2
      exit 1
    fi

    echo "Launching VibeSys..." >&2
    preparation_log="$(mktemp -t vibesys-prepare.XXXXXX)"
    temporary_files+=("$preparation_log")
    step_index=0
    for step in $plan; do
      step_index=$((step_index + 1))
      case "$step" in
        install)
          description="installing dependencies"
          step_command=("${pnpm_command[@]}" install --frozen-lockfile)
          ;;
        protocol)
          description="generating client types from the Python protocol"
          step_command=("${pnpm_command[@]}" --dir clients/tui generate:protocol)
          ;;
        build)
          description="compiling the client"
          step_command=("${pnpm_command[@]}" --dir clients/tui build)
          ;;
        *)
          echo "vs: unknown preparation step: $step" >&2
          exit 1
          ;;
      esac
      echo "vs: [${step_index}/${step_total}] ${description}..." >&2
      if ! "${step_command[@]}" >>"$preparation_log" 2>&1; then
        echo "vs: failed while ${description}:" >&2
        sed 's/^/  /' "$preparation_log" >&2
        exit 1
      fi
      # Stamp only what succeeded, so a failed step is retried next launch.
      case "$step" in
        install) write_stamp install "${dependency_sources[@]}" ;;
        protocol) write_stamp protocol "${protocol_sources[@]}" ;;
        build) write_stamp build "${client_sources[@]}" ;;
      esac
    done
    rm -f "$preparation_log"
  fi
fi

if [[ "$interactive" == true ]]; then
  python_executable="$(uv run python -c 'import sys; print(sys.executable)')"
  exec env VIBESYS_PYTHON="$python_executable" node clients/tui/dist/launcher.js "$@"
fi
exec uv run python -m vibesys "$@"
