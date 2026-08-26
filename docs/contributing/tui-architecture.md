# TUI architecture

The TypeScript frontend has three packages with one allowed dependency direction:

```text
@vibesys/backend-client <- @vibesys/core-state <- @vibesys/tui
                         \_______________________^
```

`@vibesys/tui` may depend on both packages. Reverse imports and cross-package relative imports are
forbidden and checked by `pnpm check:ts-architecture`.

## Ownership

| State or behavior | Owner |
| --- | --- |
| Generated protocol types, socket framing, connection lifecycle, requests, event subscription | `backend-client` |
| Status, rounds, phases, executions, transcripts, todos, usage, benchmarks, diagnostics | `core-state` |
| Focus, selection, layout, zoom, theme, modals, drafts, query progress | `tui` |
| Terminal widgets, rendering, keyboard and mouse events | `tui` |

The backend client performs I/O and exposes validated protocol messages. Core state is a pure fold
over snapshots, ordered events, and active-execution checkpoints. The TUI owns all interaction and
presentation state, renders the combined state, and sends user intents through the backend client.

Only backend messages change core state. A frontend action may send a command, but the command does
not optimistically change backend-authoritative state. The resulting backend event does.

`core-state` has no Node runtime, OpenTUI, theme, layout, focus, or query-result dependencies. Its
time-dependent selectors require an explicit clock value so tests remain deterministic. Transcript
labels and tones are semantic annotations derived from event fields; the TUI decides whether and how
to display them.

Experiment entries currently come from `query.experiments`. The event stream supplies an
`experiments_changed` invalidation, not the entries themselves. Query progress and results therefore
remain outside core state until the backend event contract becomes complete enough to project them.

## Validation

Run all package checks from the repository root:

```bash
pnpm check:ts-architecture
pnpm check:clients
pnpm test:clients
pnpm build:clients
```

Each package also supports its own `check`, `test`, and `build` scripts. Package builds consume only
public workspace exports. The release build uses the same dependency-aware build chain before pnpm
deploys the self-contained TUI payload.
