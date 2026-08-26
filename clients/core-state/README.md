# `@vibesys/core-state`

Pure, deterministic projection of backend snapshots and ordered run events into frontend-friendly
state. It contains no transport, requests, terminal toolkit, theme, focus, selection, layout, or
query-result state.

`TranscriptEntry.label` and `tone` are deterministic semantic annotations derived only from event
fields. They are not terminal styling or layout decisions. A UI remains responsible for choosing
whether and how to render them.

Every reducer returns new state and performs no I/O. Selectors that depend on time require the clock
as an explicit argument.
