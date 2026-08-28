/**
 * The client half of the boot trace (`src/vibesys/boot_trace.py`).
 *
 * The backend times its own boot into the run log; the client can measure
 * things the backend cannot see, such as how long the landing view sits on
 * "Loading experiments...", which spans the request, the backend gate, and
 * the reply. Those measurements are developer diagnostics, not session
 * state, so they are off unless `VIBESYS_BOOT_TRACE=1` asks for them, and
 * they go to stderr rather than into the UI. The renderer holds the
 * alternate screen, so a traced line never lands in the operator's
 * scrollback: `VIBESYS_BOOT_TRACE=1 vibesys ... 2>trace.log` captures it.
 */

/** Sink for one-off boot measurements the client takes of itself. */
export type StartupTrace = (line: string) => void;

const noop: StartupTrace = () => {};

/**
 * Build the trace sink for this process.
 *
 * Returns a no-op unless the trace is switched on, so the controller can
 * report unconditionally and stay ignorant of both the switch and the launch
 * anchor. When `VIBESYS_LAUNCH_START_MS` is present (the CLI sets it before
 * spawning anything: see `boot_trace.child_env`), every reported line gains
 * the wall time since the user actually ran the command, which no timer
 * inside this process can measure.
 */
export function resolveStartupTrace(
  env: NodeJS.ProcessEnv,
  write: (line: string) => void,
): StartupTrace {
  if (env['VIBESYS_BOOT_TRACE'] !== '1') return noop;
  const rawLaunchStartMs = env['VIBESYS_LAUNCH_START_MS'];
  const launchStartMs = rawLaunchStartMs ? Number(rawLaunchStartMs) : Number.NaN;
  const anchored = Number.isFinite(launchStartMs);
  return line => {
    const sinceLaunch = anchored
      ? `; ${Math.round(Date.now() - launchStartMs)}ms since launch`
      : '';
    write(`${line}${sinceLaunch}`);
  };
}
