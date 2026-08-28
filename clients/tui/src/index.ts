import {writeFile} from 'node:fs/promises';
import {createCliRenderer} from '@opentui/core';
import {SupervisionClient} from '@vibesys/backend-client';
import {runTuiSession} from './runtime.js';
import {SocketSessionController} from './session-controller.js';
import {createOpenTuiApp} from './ui/app.js';
import {resolveTheme} from './ui/theme.js';

const socketPath = process.env['VIBESYS_CONTROL_SOCKET'];
if (!socketPath) throw new Error('VIBESYS_CONTROL_SOCKET is required');

// Set by `vibesys.cli` before it spawns the launcher, so the StartupTrace can
// report wall time since the user actually ran the command, not just since
// this frontend process started. Absent on any launch path that predates it
// (e.g. `pnpm --dir clients/tui dev`), in which case the trace omits it.
const launchStartMsRaw = process.env['VIBESYS_LAUNCH_START_MS'];
const parsedLaunchStartMs = launchStartMsRaw === undefined ? undefined : Number(launchStartMsRaw);
const launchStartMs =
  parsedLaunchStartMs !== undefined && Number.isFinite(parsedLaunchStartMs)
    ? parsedLaunchStartMs
    : undefined;

const client = await SupervisionClient.connect(socketPath);
// VibeSys owns Ctrl+C so a nonempty OpenTUI selection can be copied before the
// same chord falls back to exiting. Enabling OpenTUI's parallel exit handler
// would make those two outcomes race.
const renderer = await createCliRenderer({exitOnCtrlC: false});
const controller = new SocketSessionController(
  client,
  resolveTheme(process.env['VIBESYS_THEME']).name,
  // Boot measurements go to stderr: they are developer diagnostics, not
  // session state. The renderer holds the alternate screen, so the line never
  // lands in the operator's scrollback; `vs 2>trace.log` captures it.
  line => {
    process.stderr.write(`${line}\n`);
  },
  launchStartMs,
);
const app = createOpenTuiApp(renderer, controller);
const startupSmokeMarker = process.env['VIBESYS_RELEASE_SMOKE_MARKER'];
const completeStartupSmoke = startupSmokeMarker
  ? async () => {
      await writeFile(startupSmokeMarker, 'renderer initialized; control protocol exchanged\n', {
        flag: 'wx',
      });
    }
  : undefined;
await runTuiSession(renderer, controller, app, completeStartupSmoke);
