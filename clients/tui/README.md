# VibeSys TUI

Terminal client and launcher for VibeSys.

```bash
npm install -g @vibesys/tui
vs --help
```

The package installs `vs` and `vibesys` as aliases for the same launcher. The
launcher starts the Python VibeSys backend with `python -m vibesys --headless`
and then attaches the OpenTUI client. Install the Python `vibesys` package in
the Python environment you want to use, or set `VIBESYS_PYTHON` to that Python
executable.

## Operator interface

Enter ordinary text to ask the supervision backend about the current run. The
available slash commands are:

| Command | Behavior |
| --- | --- |
| `/help` | Show commands and planned controls. |
| `/history` | Return to the experiment log, one row per hypothesis. |
| `/history rounds` | List rounds with agent-active elapsed time, in the right pane. |
| `/experiments` | Same as `/history`. |
| `/open-round` | Open the rounds behind the selected hypothesis. |
| `/open-round --N` | Open round N, inside whichever hypothesis owns it. |
| `/perf` | Plot the recorded performance metric by round, in the right pane. |
| `/theme` | List themes; `/theme <name>` switches immediately. |

### Experiment log

The client opens on the experiment log rather than on the per-round
transcript. It groups rounds by `hypothesis_id`, so one hypothesis held across
continuation rounds is a single row showing the claim, the round range, what
the implementation details, the measured result, the judge verdict, the outcome the loop
resolved (`Proven`, `Rejected`, or a terminal `HypothesisOutcome`), and whether
the candidate was kept. The active hypothesis is marked with `▸` and carries no
outcome until it resolves. `Proven` reads in the theme's success color and
`Disproven` in its error color; the word is always spelled out, so the reading
does not depend on color.

Arrow keys move the selection, the wheel and trackpad scroll the table
independently of it, and clicking a row selects it. Enter, or `/open-round`,
opens the rounds behind the selected hypothesis: the ordinary transcript,
rounds strip, and agent map, filtered to that hypothesis. `/open-round --N`
jumps straight to round N inside whichever hypothesis owns it. Escape steps
back to the table with the selection intact. The log is the root view, so
opening a hypothesis is the only route to per-round output; there is no
unfiltered live transcript to fall back to.

`Measured` shows the verified metric for the round that resolved the
hypothesis, as a delta against the last measurement preceding it once there is
one to compare against. The framework records a verified metric only when its
own official evaluation ran, on the sparse cadence or the final round, so a
hypothesis resolved between evaluations legitimately shows no measurement.

The table refetches when an agent phase or a round finishes, so it stays
current without being reopened. Rows are ordered by first round and never
reshuffle. Records written before hypothesis tracking render as
`(Unidentified)` rather than being dropped. Columns drop widest first as the
terminal narrows; hypothesis, rounds, and outcome always survive.

### Split panes

Visualization commands render beside the current view rather than over it.
`/perf` and `/history rounds` put their output in a right pane and leave the
transcript, the chat, or the experiment log in the left one, both live at the
same time. A second visualization command replaces the pane's contents rather
than stacking another surface on top.

`Ctrl+W` moves focus between the panes; the focused one carries the theme's
focus border and says so in its title. Page Up and Page Down scroll whichever
pane has focus, and Escape on the right pane closes it and restores the
full-width view. Chat and transcript state survive the pane closing.

Pane widths are computed from the terminal, so a wide terminal gives the
visualization real room while the left pane keeps a readable floor. Below 100
columns there is not enough width for both, and visualizations fall back to the
modal they used before panes existed. The layout re-flows on resize in either
direction.

`/help`, `/theme`, and errors stay modal.

Inside a hypothesis the footer shows keyboard navigation. `[` and `]` select rounds, Tab and
Shift+Tab select agents, Page Up/Page Down scroll the transcript, Ctrl+T expands
todos, Ctrl+P expands the latest prompt in the current selection, Ctrl+L and
Escape return to the experiment log, and Ctrl+C exits. Commands listed under "Planned" in `/help`
are not accepted yet.

The launcher retains terminal results until the operator exits. If the backend
fails to start, its log tail is printed before the temporary session directory
is removed. Requests and subscription setup have bounded timeouts; malformed or
incompatible protocol messages are shown as errors instead of crashing a socket
callback.

## Themes

Four light/dark pairs ship: `dark` (default) / `light`, `solarized-dark` /
`solarized-light`, `catppuccin-mocha` / `catppuccin-latte`, and
`high-contrast-dark` / `high-contrast-light`. Selecting `dark` reproduces the
appearance the client had before themes existed: conversation cards, the
tool-call bands, and the Markdown palette are pinned to the original literals.
Four near-duplicate status shades were deliberately folded into the role they
belong to — a completed todo now uses the same green as an active agent phase,
completed phases and prompt-disclosure hints use the same blue as the detail
overlay, round labels use the same body-text color as card content, and the
chat panel's inner border matches its outer one. `theme.test.ts` pins all of
this so the baseline cannot drift.

Pick one with `--theme <name>` or `[tui].theme` in `agent.toml`; the flag wins.
The launcher resolves the name once and passes it to both the pre-launch setup
screen and the main client through `VIBESYS_THEME`. Inside a session, `/theme`
lists the themes and `/theme <name>` re-themes every view in place.

`ui/theme.ts` is the only module holding color literals. A theme declares
semantic roles — `canvas`, `surface`, `elevatedSurface`, `selectedSurface`;
`textPrimary`, `textMuted`, `textSubtle`, `textStrong`; `border`,
`borderStrong`, `borderFocus`; `accent`, `info`; `success`, `warning`, `error`;
per-role conversation card colors; and Markdown/code colors. Views ask for a
role and never for a color.

Adding a theme means adding one `ThemeSpec`: a semantic core plus one accent
per conversation role. Card fills, labels, body text, the tool-call band, and
the Markdown palette are derived from that core, and each derived foreground is
pushed toward the nearest extreme until it clears the theme's `minContrast`
against the surface it actually sits on. The `dark` theme additionally pins its
derived values to the original literals so the baseline is byte-identical.
Status meaning never depends on color: agent phases carry a marker glyph and
the spelled-out status, todos carry a per-status marker, and only the running
round shows elapsed time.

## Architecture

The Python backend owns the validated, append-only event contract and serves it
as JSONL over a private Unix socket. `src/generated/` is generated from those
Pydantic models. The TypeScript client owns framing and request correlation,
`session-controller.ts` owns effects, `session-model.ts` and `run-map.ts` reduce
events into presentation state, and `ui/` owns OpenTUI rendering and input.

Conversation state retains at most 1,000 semantic entries. Rendering is keyed
by entry identity: state-only updates reuse existing cards, streamed tail
updates replace only the final card, and a full rebuild is reserved for filter
or history-window changes. Typed tool calls use stable call IDs so parallel
results return to the correct card; old event logs without IDs use a documented
FIFO-by-tool fallback.

## Development

From the repository root:

```bash
pnpm install --frozen-lockfile
pnpm --dir clients/tui generate:protocol
pnpm --dir clients/tui check
pnpm --dir clients/tui test
pnpm --dir clients/tui build
pnpm check:ts
uv run pytest tests/test_tui.py tests/agents/test_callbacks.py tests/render/test_sink.py
```

After changing Python protocol models, regenerate both files in
`src/generated/` and review their diff. The test suite covers reducer behavior,
OpenTUI frames and navigation, launcher cleanup, socket fragmentation and
timeouts, replay/live delivery, and the Python supervision service.
