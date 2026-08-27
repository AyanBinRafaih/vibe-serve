import {afterEach, describe, expect, it} from 'bun:test';
import {CliRenderEvents, InputRenderable, rgbToHex, ScrollBoxRenderable} from '@opentui/core';
import {createTestRenderer, type TestRendererSetup} from '@opentui/core/testing';
import type {HypothesisEntry} from '@vibesys/backend-client';
import type {SessionController} from '../session-controller.js';
import {
  advanceNewChatPicker,
  clearAgentSelection,
  clearEntrySelection,
  closeChatThreadPicker,
  closeOverlays,
  closePane,
  closeThemePicker,
  moveChatThreadSelection,
  moveNewChatSelection,
  openChatThreadPicker,
  openNewChatPicker,
  retreatNewChatPicker,
  setNewChatModel,
  switchChatThread,
  cyclePaneFocus,
  dismissErrorBanner,
  enterExperimentDrilldown,
  enterExperimentRound,
  enterUnownedExperimentRound,
  focusPane,
  focusRound,
  initialSessionState,
  leaveExperimentDrilldown,
  moveExperimentSelection,
  moveThemeSelection,
  normalizeFocus,
  openExperimentLog,
  openPane,
  type PaneFocus,
  type PaneView,
  type RoundFocus,
  reportError,
  type SessionState,
  selectAgent,
  selectExperimentActivity,
  selectNextEntry,
  selectNextRound,
  selectNextTodo,
  selectPreviousRound,
  setChatDockFits,
  setExperiments,
  setPaneContent,
  setTheme,
  togglePaneZoom,
} from '../session-model.js';
import {createOpenTuiApp, type OpenTuiApp} from './app.js';
import type {ClipboardCopyResult, SelectionClipboard} from './clipboard.js';
import {resolveTheme, type ThemeName} from './theme.js';

const cleanup: Array<() => void> = [];

afterEach(() => {
  for (const destroy of cleanup.splice(0).reverse()) destroy();
});

describe('OpenTUI presentation', () => {
  it('renders model state with a persistent input panel', async () => {
    const testRenderer = await createTestRenderer({width: 80, height: 20});
    const controller = new FakeController({
      ...initialSessionState(),
      core: {
        ...initialSessionState().core,
        status: 'running',
        agentKind: 'optimizer',
        roundLabel: 'round 2',
        phases: [
          {
            kind: 'optimizer',
            status: 'active',
            roundNumber: null,
            roundLabel: 'round 2',
          },
        ],
        transcript: [
          {
            id: '1',
            kind: 'assistant',
            label: 'optimizer · round 2',
            agentKind: 'optimizer',
            content: '## Result\n\nUse `fast_path()`.',
          },
        ],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    const frame = await testRenderer.waitForFrame(value => value.includes('fast_path()'));
    expect(frame).toContain('running · optimizer · round 2');
    // No round is selected, so the agent strip is headed by the run.
    expect(frame).toContain('Run flow');
    expect(frame).toContain('Rounds');
    expect(frame).toContain('● optimizer');
    expect(frame).toContain('Result');
    expect(frame).toContain('Command');
    expect(frame).toContain('Type /help for commands');
  });

  it('shows and dismisses a fatal error above the empty experiment log', async () => {
    const testRenderer = await createTestRenderer({width: 100, height: 24});
    const controller = new FakeController(initialSessionState());
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openPane('perf');
    const fatalBanner: NonNullable<SessionState['errorBanner']> = {
      title: 'Run failed',
      message: 'RuntimeError: app-server initialization was denied\nOperation not permitted',
      detail: 'The run server exited before accepting a client.',
      hint: 'Check the startup log and retry.',
      diagnosticId: 'diagnostic-1',
      severity: 'fatal',
      scope: 'run',
      agentKind: 'orchestrator',
      roundLabel: 'round-1-pre',
      invocationId: null,
      count: 1,
    };
    controller.publish({
      ...controller.state,
      experimentLog: {entries: [], selectedId: null, pending: false, error: null},
      errorBanner: fatalBanner,
    });

    let frame = await testRenderer.waitForFrame(value =>
      value.includes('app-server initialization was denied'),
    );
    expect(frame).toContain('Run failed · orchestrator · round-1-pre');
    expect(frame).toContain('Operation not permitted');
    expect(frame).toContain('Detail: The run server exited before accepting a client.');
    expect(frame).toContain('Hint: Check the startup log and retry.');
    expect(frame).toContain('Experiments');

    expect(frame).toContain('[× Dismiss] · Esc: dismiss · Ctrl+PgUp/PgDn: scroll');

    const lines = frame.split('\n');
    const row = lines.findIndex(line => line.includes('[× Dismiss]'));
    const column = (lines[row]?.indexOf('[× Dismiss]') ?? 0) + 2;
    await testRenderer.mockMouse.click(column, row);
    frame = await frameAfter(testRenderer);
    expect(controller.state.errorBanner).toBeNull();
    expect(frame).not.toContain('app-server initialization was denied');
    expect(frame).toContain('Experiments');

    controller.publish({
      ...controller.state,
      errorBanner: {...fatalBanner, title: 'Request failed', message: 'A later failure.'},
    });
    frame = await testRenderer.waitForFrame(value => value.includes('A later failure.'));
    expect(frame).toContain('Esc: dismiss');

    testRenderer.mockInput.pressKey('ESCAPE');
    frame = await frameAfterEscape(testRenderer);
    expect(controller.state.errorBanner).toBeNull();
    expect(controller.state.layout.right?.view).toBe('perf');
    expect(frame).not.toContain('A later failure.');
  });

  it('renders quiet round labels without status text or symbols', async () => {
    const testRenderer = await createTestRenderer({width: 100, height: 18});
    const activeStartedAt = new Date(Date.now() - 65_000).toISOString();
    const controller = new FakeController({
      ...initialSessionState(),
      core: {
        ...initialSessionState().core,
        rounds: [
          {number: 1, status: 'completed'},
          {
            number: 2,
            status: 'active',
            startedAt: activeStartedAt,
            activeAgentStarts: {'judge:judge-1': activeStartedAt},
          },
          {number: 3, status: 'failed'},
        ],
        transcript: [{id: 'live', kind: 'assistant', label: 'Agent', content: 'live output'}],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    const frame = await testRenderer.waitForFrame(value => value.includes('r2'));

    expect(frame).toContain('r1');
    expect(frame).toContain('r2');
    expect(frame).toMatch(/r2\s+1m\s+\d+s/);
    expect(frame).toContain('r3');
    expect(frame).not.toMatch(/[◐◓◑◒]/);
    expect(frame).not.toContain('done');
    expect(frame).not.toContain(':run');
    expect(frame).not.toContain('fail');
  });

  it('heads the agent strip with the elapsed time of the running round', async () => {
    const testRenderer = await createTestRenderer({width: 100, height: 18});
    const activeStartedAt = new Date(Date.now() - 65_000).toISOString();
    const controller = new FakeController({
      ...initialSessionState(),
      core: {
        ...initialSessionState().core,
        rounds: [
          {
            number: 2,
            status: 'active',
            startedAt: activeStartedAt,
            activeAgentStarts: {'judge:judge-1': activeStartedAt},
          },
        ],
        phases: [{kind: 'judge', status: 'active', roundNumber: 2, roundLabel: 'round-2-judge'}],
        transcript: [{id: 'live', kind: 'assistant', label: 'Agent', content: 'live output'}],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    const frame = await testRenderer.waitForFrame(value => value.includes('Round 2 flow'));

    expect(frame).toMatch(/Round 2 flow · 1m \d+s/);
  });

  it('draws the round as a left-to-right graph when the terminal has room', async () => {
    const testRenderer = await createTestRenderer({width: 150, height: 24});
    const controller = new FakeController({
      ...initialSessionState(),
      core: {
        ...initialSessionState().core,
        rounds: [{number: 1, status: 'active'}],
        phases: [
          {kind: 'orchestrator', status: 'completed', roundNumber: 1, roundLabel: 'round-1-plan'},
          {
            kind: 'implementer',
            status: 'active',
            roundNumber: 1,
            roundLabel: 'round-1-implementer',
          },
          {kind: 'judge', status: 'pending', roundNumber: 1, roundLabel: 'round-1-judge'},
        ],
        transcript: [{id: 'live', kind: 'assistant', label: 'Agent', content: 'live output'}],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    const frame = await testRenderer.waitForFrame(value => value.includes('orchestrator'));

    // Stages share a row and are joined by edges, rather than stacked with ↓.
    const stageRow = frame
      .split('\n')
      .find(line => line.includes('orchestrator') && line.includes('implementer'));
    expect(stageRow).toBeDefined();
    expect(stageRow).toContain('judge');
    expect(frame).toContain('▶');
    // The stacked strip's connector, not the arrow glyphs in the key help.
    expect(frame).not.toContain('        ↓');
  });

  it('walks the transcript with the arrow keys and filters it to a clicked agent', async () => {
    const testRenderer = await createTestRenderer({width: 140, height: 26});
    const controller = new FakeController({
      ...initialSessionState(),
      selectedRound: 1,
      core: {
        ...initialSessionState().core,
        rounds: [{number: 1, status: 'active'}],
        phases: [
          {kind: 'implementer', status: 'completed', roundNumber: 1, roundLabel: 'round-1-impl'},
          {kind: 'judge', status: 'active', roundNumber: 1, roundLabel: 'round-1-judge'},
        ],
        activeExecutions: {
          'judge-1': {
            executionId: 'judge-1',
            agentKind: 'judge',
            roundLabel: 'round-1-judge',
            roundNumber: 1,
            stage: 'evaluation',
            attempt: 1,
            assignment: 'Evaluate the candidate',
            startedAt: new Date().toISOString(),
            activity: {mode: 'thinking', summary: 'Checking the diff'},
          },
        },
        transcript: [
          {
            id: 'e1',
            kind: 'assistant',
            label: 'implementer · round-1',
            content: 'edited the kernel',
            agentKind: 'implementer',
            roundNumber: 1,
          },
          {
            id: 'e2',
            kind: 'assistant',
            label: 'judge · round-1',
            content: 'checking the diff',
            agentKind: 'judge',
            roundNumber: 1,
          },
        ],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    // A phase is running, but individual turn cards do not claim ownership of
    // that activity because the transcript may be filtered to another agent.
    const live = await testRenderer.waitForFrame(value => value.includes('checking the diff'));
    expect(live).toContain('Judge · Working');
    expect(live).not.toContain('Judge · Checking the diff');

    // Arrows put a cursor on an entry without touching the input.
    testRenderer.mockInput.pressKey('ARROW_UP');
    const cursored = await frameAfter(testRenderer);
    expect(controller.state.selectedEntryId).not.toBeNull();
    expect(cursored).toContain('▸');

    // Selecting an agent filters the transcript to that agent's turns.
    controller.selectAgent('implementer');
    const filtered = await frameAfter(testRenderer);
    expect(filtered).toContain('edited the kernel');
    expect(filtered).not.toContain('checking the diff');
    // A completed filtered transcript must not inherit another agent's live
    // activity. The global working indicator remains available on the experiment log.
    expect(filtered).not.toContain('Judge · Working');
  });

  it('summarizes concurrent executions and disappears when they finish', async () => {
    const testRenderer = await createTestRenderer({width: 120, height: 24});
    const base = initialSessionState();
    const controller = new FakeController({
      ...base,
      selectedRound: 2,
      core: {
        ...base.core,
        activeExecutions: {
          implementer: {
            executionId: 'implementer',
            agentKind: 'implementer',
            roundLabel: 'round-2-implementer',
            roundNumber: 2,
            stage: 'implementation',
            attempt: 1,
            assignment: 'Implement the queue',
            startedAt: new Date().toISOString(),
            activity: {mode: 'tool', summary: 'Running queue tests', tool: 'Bash'},
          },
          reviewer: {
            executionId: 'reviewer',
            agentKind: 'reviewer',
            roundLabel: 'round-2-review',
            roundNumber: 2,
            stage: 'review',
            attempt: 1,
            assignment: 'Review the diff',
            startedAt: new Date().toISOString(),
            activity: {mode: 'thinking', summary: 'Inspecting the diff'},
          },
        },
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    const active = await testRenderer.waitForFrame(value => value.includes('2 agents active'));
    expect(active).toContain('Implementer: Working');
    expect(active).toContain('Reviewer: Working');
    expect(active).not.toContain('Running queue tests');
    expect(active).not.toContain('Inspecting the diff');

    controller.publish({
      ...controller.state,
      core: {
        ...controller.state.core,
        activeExecutions: {},
      },
    });
    const finished = await frameAfter(testRenderer);
    expect(finished).not.toContain('agents active');
    expect(finished).not.toContain('Running queue tests');
  });

  it('shows activity when an execution starts after its agent conversation is opened', async () => {
    const testRenderer = await createTestRenderer({width: 100, height: 20});
    const controller = new FakeController({
      ...initialSessionState(),
      selectedRound: 1,
      selectedAgentKind: 'implementer',
      core: {
        ...initialSessionState().core,
        rounds: [{number: 1, status: 'active'}],
        phases: [
          {
            kind: 'implementer',
            status: 'pending',
            roundNumber: 1,
            roundLabel: 'round-1-implementer',
          },
        ],
        transcript: [
          {
            id: 'prompt',
            kind: 'prompt',
            content: 'Implement the queue',
            agentKind: 'implementer',
            roundNumber: 1,
          },
        ],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    const idle = await testRenderer.waitForFrame(value => value.includes('Implement the queue'));
    expect(idle).not.toContain('Implementer · Working');

    controller.publish({
      ...controller.state,
      core: {
        ...controller.state.core,
        activeExecutions: {
          'impl-1': {
            executionId: 'impl-1',
            agentKind: 'implementer',
            roundLabel: 'round-1-implementer',
            roundNumber: 1,
            stage: 'implementation',
            attempt: 1,
            assignment: 'Implement the queue',
            startedAt: new Date().toISOString(),
            activity: {mode: 'responding', summary: 'Editing the queue'},
          },
        },
      },
    });

    const active = await testRenderer.waitForFrame(value =>
      value.includes('Implementer · Working'),
    );
    expect(active).toMatch(/[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏] Implementer · Working · \d+s/);
    expect(active).not.toContain('Editing the queue');
    const activityLine = active.split('\n').find(line => line.includes('Implementer · Working'));
    expect(activityLine?.indexOf('Implementer')).toBeGreaterThan(20);
    const lines = active.split('\n');
    const promptLine = lines.findIndex(line => line.includes('Implement the queue'));
    const activityLineIndex = lines.findIndex(line => line.includes('Implementer · Working'));
    const helpLine = lines.findIndex(line => line.includes('[/]: round'));
    const viewportBottomBorder = lines.findIndex(
      (line, index) =>
        index > activityLineIndex && index < helpLine && line.trimEnd().endsWith('╯'),
    );
    expect(activityLineIndex).toBeGreaterThan(promptLine);
    expect(activityLine?.trimEnd().endsWith('│')).toBe(true);
    expect(viewportBottomBorder).toBeGreaterThan(activityLineIndex);
    expect(viewportBottomBorder).toBeLessThan(helpLine);
    const transcriptColumn = Math.max(0, (activityLine?.indexOf('Implementer') ?? 2) - 2);
    expect(
      lines
        .slice(activityLineIndex + 1, viewportBottomBorder)
        .every(line => line.slice(transcriptColumn).replaceAll('│', '').trim() === ''),
    ).toBe(true);

    controller.selectAgent('implementer');
    await frameAfter(testRenderer);
    controller.selectAgent('implementer');
    const reopened = await frameAfter(testRenderer);
    expect(reopened).toContain('Implementer · Working');
  });

  it('keeps activity fixed and aligned while a new turn changes the scroll height', async () => {
    const conversation = Array.from({length: 12}, (_, index) => ({
      id: `turn-${index}`,
      kind: 'status' as const,
      content: `recorded turn ${index}`,
      agentKind: 'implementer',
      roundNumber: 1,
    }));
    const testRenderer = await createTestRenderer({width: 100, height: 20});
    const controller = new FakeController({
      ...initialSessionState(),
      selectedRound: 1,
      selectedAgentKind: 'implementer',
      core: {
        ...initialSessionState().core,
        transcript: conversation,
        rounds: [{number: 1, status: 'active'}],
        activeExecutions: {
          'impl-1': {
            executionId: 'impl-1',
            agentKind: 'implementer',
            roundLabel: 'round-1-implementer',
            roundNumber: 1,
            stage: 'implementation',
            attempt: 1,
            assignment: 'Implement the queue',
            startedAt: new Date().toISOString(),
            activity: {mode: 'thinking', summary: 'Thinking'},
          },
        },
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await testRenderer.waitForFrame(value => value.includes('Implementer · Working'));

    const frames: string[] = [];
    const captureFrame = (): void => {
      frames.push(testRenderer.captureCharFrame());
    };
    testRenderer.renderer.on(CliRenderEvents.FRAME, captureFrame);
    controller.publish({
      ...controller.state,
      core: {
        ...controller.state.core,
        transcript: [
          ...conversation,
          {
            id: 'new-turn',
            kind: 'assistant',
            content: 'newly rendered turn',
            agentKind: 'implementer',
            roundNumber: 1,
          },
        ],
      },
    });
    await testRenderer.waitForVisualIdle();
    testRenderer.renderer.off(CliRenderEvents.FRAME, captureFrame);

    expect(frames.length).toBeGreaterThan(0);
    const activityRows = frames.map(frame =>
      frame.split('\n').findIndex(line => line.includes('Implementer · Working')),
    );
    expect(activityRows.every(row => row >= 0)).toBe(true);
    expect(new Set(activityRows).size).toBe(1);
    expect(frames.at(-1)).toContain('newly rendered turn');

    const frame = testRenderer.renderer.root.findDescendantById('viewport');
    const scroll = testRenderer.renderer.root.findDescendantById('transcript-scroll');
    const activity = testRenderer.renderer.root.findDescendantById('conversation-activity-bar');
    const firstTurn = testRenderer.renderer.root.findDescendantById('event-turn-0');
    if (frame === undefined || activity === undefined)
      throw new Error('transcript frame was missing');
    if (!(scroll instanceof ScrollBoxRenderable)) throw new Error('transcript was not scrollable');
    expect(scroll.parent).toBe(frame);
    expect(activity?.parent).toBe(frame);
    expect(firstTurn?.x).toBe(activity.x);
  });

  it('keeps working indicators scoped to agent conversations', async () => {
    const testRenderer = await createTestRenderer({width: 100, height: 20});
    const controller = new FakeController({
      ...initialSessionState(),
      core: {
        ...initialSessionState().core,
        activeExecutions: {
          'impl-1': {
            executionId: 'impl-1',
            agentKind: 'implementer',
            roundLabel: 'round-1-implementer',
            roundNumber: 1,
            stage: 'implementation',
            attempt: 1,
            assignment: 'Implement the queue',
            startedAt: new Date().toISOString(),
            activity: {mode: 'responding', summary: 'Editing the queue'},
          },
        },
      },
    });
    controller.publish({...controller.state, experimentLog: initialSessionState().experimentLog});
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    const frame = await frameAfter(testRenderer);
    expect(frame).toContain('Experiments');
    expect(frame).not.toContain('Implementer · Working');
    expect(frame).not.toContain('Editing the queue');
  });

  it('shows the whole run in the strip and keeps early rounds reachable', async () => {
    const testRenderer = await createTestRenderer({width: 120, height: 20});
    const controller = new FakeController({
      ...initialSessionState(),
      core: {
        ...initialSessionState().core,
        maxRounds: 100,
        rounds: Array.from({length: 12}, (_, index) => ({
          number: index + 1,
          status: index === 11 ? ('active' as const) : ('completed' as const),
        })),
        phases: [{kind: 'judge', status: 'active', roundNumber: 12, roundLabel: 'round-12-judge'}],
        transcript: [{id: 'live', kind: 'assistant', label: 'Agent', content: 'out'}],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    const frame = await testRenderer.waitForFrame(value => value.includes('r12'));
    // Rounds the run has not reached are still part of the strip, and the strip
    // says how many it could not fit.
    expect(frame).toMatch(/r1[34]/);
    expect(frame).toMatch(/\d+ ›/);

    // `[` walks back to the first round, and the strip follows the selection
    // rather than leaving it hidden past the edge.
    let early = frame;
    for (let step = 0; step < 11; step += 1) {
      testRenderer.mockInput.pressKey('[');
      early = await frameAfter(testRenderer);
    }
    expect(controller.state.selectedRound).toBe(1);
    expect(early).toContain('[ r1 ]');
  });

  it('filters the transcript to an agent node that is clicked', async () => {
    const testRenderer = await createTestRenderer({width: 150, height: 24});
    const controller = new FakeController({
      ...initialSessionState(),
      selectedRound: 1,
      core: {
        ...initialSessionState().core,
        rounds: [{number: 1, status: 'active'}],
        phases: [
          {kind: 'implementer', status: 'completed', roundNumber: 1, roundLabel: 'round-1-impl'},
          {kind: 'judge', status: 'active', roundNumber: 1, roundLabel: 'round-1-judge'},
        ],
        transcript: [
          {
            id: 'e1',
            kind: 'assistant',
            label: 'implementer',
            content: 'edited the kernel',
            agentKind: 'implementer',
            roundNumber: 1,
          },
          {
            id: 'e2',
            kind: 'assistant',
            label: 'judge',
            content: 'checking the diff',
            agentKind: 'judge',
            roundNumber: 1,
          },
        ],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    const frame = await testRenderer.waitForFrame(value => value.includes('implementer'));

    // Click the node's own label, which is what a pointer lands on.
    const lines = frame.split('\n');
    const row = lines.findIndex(line => line.includes('✓ implementer'));
    const column = (lines[row]?.indexOf('implementer') ?? 0) + 2;
    await testRenderer.mockMouse.click(column, row);
    const filtered = await frameAfter(testRenderer);

    expect(controller.state.selectedAgentKind).toBe('implementer');
    expect(filtered).not.toContain('checking the diff');
  });

  it('moves the round view keys between the graph and the transcript', async () => {
    const testRenderer = await createTestRenderer({width: 150, height: 26});
    const controller = new FakeController({
      ...initialSessionState(),
      selectedRound: 1,
      core: {
        ...initialSessionState().core,
        rounds: [{number: 1, status: 'active'}],
        phases: [
          {kind: 'implementer', status: 'completed', roundNumber: 1, roundLabel: 'round-1-impl'},
          {kind: 'judge', status: 'active', roundNumber: 1, roundLabel: 'round-1-judge'},
        ],
        transcript: [
          {
            id: 'e1',
            kind: 'assistant',
            label: 'implementer',
            content: 'edited the kernel',
            agentKind: 'implementer',
            roundNumber: 1,
          },
          {
            id: 'e2',
            kind: 'assistant',
            label: 'implementer',
            content: 'guarded the tail tile',
            agentKind: 'implementer',
            roundNumber: 1,
          },
          {
            id: 'e3',
            kind: 'assistant',
            label: 'judge',
            content: 'checking the diff',
            agentKind: 'judge',
            roundNumber: 1,
          },
        ],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await testRenderer.waitForFrame(value => value.includes('implementer'));

    // Left reaches the graph, and the pane says it holds the keys.
    testRenderer.mockInput.pressKey('ARROW_LEFT');
    const onAgents = await frameAfter(testRenderer);
    expect(controller.state.roundFocus).toBe('agents');
    expect(onAgents).toContain('▸ Agents');

    // There, up and down walk the agents rather than the transcript.
    testRenderer.mockInput.pressKey('ARROW_DOWN');
    await frameAfter(testRenderer);
    const firstAgent = controller.state.selectedAgentKind;
    expect(firstAgent).not.toBeNull();
    expect(controller.state.selectedEntryId).toBeNull();

    // Right hands them to the transcript, where they walk its entries instead.
    testRenderer.mockInput.pressKey('ARROW_RIGHT');
    await frameAfter(testRenderer);
    expect(controller.state.roundFocus).toBe('transcript');
    testRenderer.mockInput.pressKey('ARROW_UP');
    await frameAfter(testRenderer);
    expect(controller.state.selectedEntryId).not.toBeNull();
    // The agent picked on the left is still the filter: moving the keys is not
    // the same as giving up the selection.
    expect(controller.state.selectedAgentKind).toBe(firstAgent);

    // And Tab still works after coming back, from where the operator left off.
    testRenderer.mockInput.pressKey('ARROW_LEFT');
    testRenderer.mockInput.pressKey('TAB');
    await frameAfter(testRenderer);
    expect(controller.state.selectedAgentKind).not.toBe(firstAgent);
  });

  it('focuses round panes from blank and interactive click targets', async () => {
    const testRenderer = await createTestRenderer({width: 150, height: 26});
    const controller = new FakeController({
      ...initialSessionState(),
      experimentLog: null,
      selectedRound: 1,
      core: {
        ...initialSessionState().core,
        rounds: [{number: 1, status: 'active'}],
        phases: [
          {kind: 'implementer', status: 'completed', roundNumber: 1, roundLabel: 'round-1-impl'},
          {kind: 'judge', status: 'active', roundNumber: 1, roundLabel: 'round-1-judge'},
        ],
        transcript: [
          {
            id: 'e1',
            kind: 'assistant',
            label: 'implementer',
            content: 'edited the kernel',
            roundNumber: 1,
          },
          {
            id: 'e2',
            kind: 'assistant',
            label: 'judge',
            content: 'checking the diff',
            roundNumber: 1,
          },
        ],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    let frame = await testRenderer.waitForFrame(value => value.includes('edited the kernel'));

    // The graph heading has no action of its own. Clicking it still focuses
    // the containing pane rather than requiring a click on an agent.
    let lines = frame.split('\n');
    let row = lines.findIndex(line => line.includes('Round 1'));
    let column = (lines[row]?.indexOf('Round 1') ?? 0) + 2;
    await testRenderer.mockMouse.click(column, row);
    frame = await frameAfter(testRenderer);
    expect(controller.state.roundFocus).toBe('agents');
    expect(frame).toContain('▸ Agents');

    // Entering the pane selects its active agent. Clicking that inner node
    // keeps Agents focused and clears the filter, preserving node semantics.
    lines = frame.split('\n');
    row = lines.findIndex(line => line.includes('● judge'));
    column = (lines[row]?.indexOf('judge') ?? 0) + 2;
    await testRenderer.mockMouse.click(column, row);
    frame = await frameAfter(testRenderer);
    expect(controller.state.roundFocus).toBe('agents');
    expect(controller.state.selectedAgentKind).toBeNull();

    // A turn card has its own selection action. It composes pane focus with
    // that action, and the next arrow is consequently routed to transcript.
    lines = frame.split('\n');
    row = lines.findIndex(line => line.includes('edited the kernel'));
    column = (lines[row]?.indexOf('edited the kernel') ?? 0) + 2;
    await testRenderer.mockMouse.click(column, row);
    await frameAfter(testRenderer);
    expect(controller.state.roundFocus).toBe('transcript');
    expect(controller.state.selectedEntryId).toBe('e1');
    testRenderer.mockInput.pressKey('ARROW_DOWN');
    await frameAfter(testRenderer);
    expect(controller.state.selectedEntryId).toBe('e2');

    // Agent nodes likewise keep their selection behavior while taking focus.
    frame = testRenderer.captureCharFrame();
    lines = frame.split('\n');
    row = lines.findIndex(line => line.includes('✓ implementer'));
    column = (lines[row]?.indexOf('implementer') ?? 0) + 2;
    await testRenderer.mockMouse.click(column, row);
    await frameAfter(testRenderer);
    expect(controller.state.roundFocus).toBe('agents');
    expect(controller.state.selectedAgentKind).toBe('implementer');
    testRenderer.mockInput.pressKey('TAB');
    await frameAfter(testRenderer);
    expect(controller.state.selectedAgentKind).toBe('judge');
  });

  it('marks the chat input as focused when it is clicked', async () => {
    const testRenderer = await createTestRenderer({width: 150, height: 22});
    const controller = new FakeController({...initialSessionState(), chatDockFits: true});
    controller.experiments = [
      logEntry('H-01', 1, 1, {
        claim: 'fuse the epilogue',
        rounds: [{round: 1, passed: true, reviewed: true}],
      }),
    ];
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();
    const docked = await testRenderer.waitForFrame(value =>
      value.includes('Ask about this experiment'),
    );
    // The hint says the keys are elsewhere, which is the state being reported.
    expect(docked).toContain('Ctrl+W to type here');

    // Click the box the operator types into, not the conversation above it.
    const lines = docked.split('\n');
    const row = lines.findIndex(line => line.includes('Ask about this experiment'));
    const column = (lines[row]?.indexOf('Ask about this experiment') ?? 0) + 2;
    await testRenderer.mockMouse.click(column, row);
    const focused = await frameAfter(testRenderer);

    expect(controller.state.layout.focus).toBe('chat');
    // And the box says so: clicking must move the border, not only the cursor.
    expect(focused).not.toContain('Ctrl+W to type here');
    expect(spanColors(testRenderer, 'Message')?.fg).toBe(resolveTheme('dark').borderFocus);
  });

  it('gives the chat the keys when it is clicked, not only on Ctrl+W', async () => {
    const testRenderer = await createTestRenderer({width: 150, height: 22});
    const controller = new FakeController({
      ...initialSessionState(),
      chatDockFits: true,
      chatConversation: [
        {id: 'a1', kind: 'assistant', label: 'Answer', content: 'the epilogue was fused'},
      ],
    });
    controller.experiments = [
      logEntry('H-01', 1, 1, {
        claim: 'fuse the epilogue',
        rounds: [{round: 1, passed: true, reviewed: true}],
      }),
    ];
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();
    const docked = await testRenderer.waitForFrame(value => value.includes('Experiment chat'));
    expect(controller.state.layout.focus).toBe('left');

    // Click inside the chat's body, which is where a pointer actually lands.
    const row = docked.split('\n').findIndex(line => line.includes('the epilogue was fused'));
    const column = docked.split('\n')[row]?.indexOf('the epilogue') ?? 0;
    await testRenderer.mockMouse.click(column, row);
    await frameAfter(testRenderer);

    expect(controller.state.layout.focus).toBe('chat');
  });

  it('says so when a round has not run instead of looking broken', async () => {
    const testRenderer = await createTestRenderer({width: 120, height: 20});
    const controller = new FakeController({
      ...initialSessionState(),
      selectedRound: 9,
      core: {
        ...initialSessionState().core,
        maxRounds: 20,
        rounds: [{number: 1, status: 'completed'}],
        phases: [{kind: 'judge', status: 'completed', roundNumber: 1, roundLabel: 'round-1-judge'}],
        transcript: [
          {id: 'e1', kind: 'assistant', label: 'judge', content: 'done', roundNumber: 1},
        ],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    const frame = await testRenderer.waitForFrame(value => value.includes('has not run yet'));
    expect(frame).toContain('Round 9 has not run yet.');
    // The strip still shows it as a round of this run, marked as the one open.
    expect(frame).toContain('[ r9 ]');
  });

  it('closes a visualization and the chat together on one Escape', async () => {
    const testRenderer = await createTestRenderer({width: 130, height: 22});
    const controller = new FakeController({
      ...initialSessionState(),
      hypothesisScope: {id: 'H-01', label: 'H-01 · r1', rounds: [1]},
      selectedRound: 1,
      core: {
        ...initialSessionState().core,
        rounds: [{number: 1, status: 'active'}],
        phases: [{kind: 'judge', status: 'active', roundNumber: 1, roundLabel: 'round-1-judge'}],
        transcript: [
          {id: 'e1', kind: 'assistant', label: 'judge', content: 'weighing it', roundNumber: 1},
        ],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openPane('perf');
    controller.publish({...controller.state, chatOpen: true});
    await frameAfter(testRenderer);

    testRenderer.mockInput.pressKey('ESCAPE');
    await frameAfterEscape(testRenderer);

    // Back on the round in one press, not part way with the chat still over it.
    expect(controller.state.chatOpen).toBe(false);
    expect(controller.state.layout.right).toBeNull();
    expect(controller.state.hypothesisScope).not.toBeNull();
  });

  it('hands the keys back when the pane holding them closes', async () => {
    const testRenderer = await createTestRenderer({width: 130, height: 22});
    const controller = new FakeController({
      ...initialSessionState(),
      selectedRound: 1,
      core: {
        ...initialSessionState().core,
        rounds: [{number: 1, status: 'active'}],
        phases: [{kind: 'judge', status: 'active', roundNumber: 1, roundLabel: 'round-1-judge'}],
        transcript: [
          {id: 'e1', kind: 'assistant', label: 'judge', content: 'weighing it', roundNumber: 1},
        ],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openPane('perf');
    controller.focusPane('right');
    await frameAfter(testRenderer);
    controller.closePane();
    await frameAfter(testRenderer);

    // Focus cannot be left pointing at a pane that is gone, or every key after
    // it goes nowhere and the client looks frozen.
    expect(controller.state.layout.focus).toBe('left');
    testRenderer.mockInput.pressKey(']');
    await frameAfter(testRenderer);
    expect(controller.state.selectedRound).toBe(1);
  });

  it('keeps the chat in one conversation across the log and a round', async () => {
    const testRenderer = await createTestRenderer({width: 120, height: 22});
    const controller = new FakeController({
      ...initialSessionState(),
      chatConversation: [
        {id: 'q1', kind: 'user', label: 'You', content: 'what changed?'},
        {id: 'a1', kind: 'assistant', label: 'Answer', content: 'the epilogue was fused'},
      ],
      chatOpen: true,
      core: {
        ...initialSessionState().core,
        rounds: [{number: 1, status: 'active'}],
        phases: [{kind: 'judge', status: 'active', roundNumber: 1, roundLabel: 'round-1-judge'}],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    // The same exchange is on screen inside a round, not only on the log.
    const frame = await testRenderer.waitForFrame(value =>
      value.includes('the epilogue was fused'),
    );
    expect(frame).toContain('what changed?');
  });

  it('draws a round with many agents per stage without overlap', async () => {
    const testRenderer = await createTestRenderer({width: 150, height: 34});
    const phase = (kind: string, status: 'completed' | 'active' | 'pending', index: number) => ({
      kind,
      status,
      roundNumber: 1,
      roundLabel: `round-1-${kind}`,
      invocationId: `${kind}-${index}`,
    });
    const controller = new FakeController({
      ...initialSessionState(),
      core: {
        ...initialSessionState().core,
        rounds: [{number: 1, status: 'active'}],
        phases: [
          phase('orchestrator', 'completed', 0),
          phase('implementer', 'completed', 1),
          phase('implementer', 'active', 2),
          phase('implementer', 'active', 3),
          phase('judge', 'pending', 4),
          phase('judge', 'pending', 5),
          phase('profiler', 'pending', 6),
        ],
        transcript: [{id: 'live', kind: 'assistant', label: 'Agent', content: 'out'}],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    const frame = await testRenderer.waitForFrame(value => value.includes('orchestrator'));

    expect(frame).toContain('7 agents');
    expect(frame).toContain('2 active');
    // Every stage is drawn, and the fan-out rows do not collapse onto each other.
    const nodeRows = frame.split('\n').filter(line => line.includes('implementer'));
    expect(nodeRows.length).toBeGreaterThanOrEqual(3);
    expect(frame).toContain('▶');
  });

  it('falls back to the stacked strip when the terminal is too narrow for a graph', async () => {
    const testRenderer = await createTestRenderer({width: 80, height: 30});
    const controller = new FakeController({
      ...initialSessionState(),
      core: {
        ...initialSessionState().core,
        rounds: [{number: 1, status: 'active'}],
        phases: [
          {kind: 'orchestrator', status: 'completed', roundNumber: 1, roundLabel: 'round-1-plan'},
          {
            kind: 'implementer',
            status: 'active',
            roundNumber: 1,
            roundLabel: 'round-1-implementer',
          },
          {kind: 'judge', status: 'pending', roundNumber: 1, roundLabel: 'round-1-judge'},
        ],
        transcript: [{id: 'live', kind: 'assistant', label: 'Agent', content: 'live output'}],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    const frame = await testRenderer.waitForFrame(value => value.includes('orchestrator'));

    expect(frame).toContain('        ↓');
    expect(frame).not.toContain('▶');
  });

  it('holds the agent-active elapsed time of a finished round', async () => {
    const testRenderer = await createTestRenderer({width: 100, height: 18});
    const controller = new FakeController({
      ...initialSessionState(),
      core: {
        ...initialSessionState().core,
        rounds: [
          {
            number: 1,
            status: 'completed',
            // 60s of wall clock with a 15s gap where no agent was running.
            agentIntervals: [
              {startedAt: '2026-01-01T00:00:00Z', finishedAt: '2026-01-01T00:00:30Z'},
              {startedAt: '2026-01-01T00:00:45Z', finishedAt: '2026-01-01T00:01:00Z'},
            ],
          },
        ],
        phases: [{kind: 'judge', status: 'completed', roundNumber: 1, roundLabel: 'round-1-judge'}],
        transcript: [{id: 'live', kind: 'assistant', label: 'Agent', content: 'live output'}],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    const frame = await testRenderer.waitForFrame(value => value.includes('Round 1 flow'));

    expect(frame).toContain('Round 1 flow · 45s');
  });

  it('omits the elapsed time for a round with no recorded agent time', async () => {
    const testRenderer = await createTestRenderer({width: 100, height: 18});
    const controller = new FakeController({
      ...initialSessionState(),
      core: {
        ...initialSessionState().core,
        rounds: [{number: 1, status: 'completed'}],
        phases: [{kind: 'judge', status: 'completed', roundNumber: 1, roundLabel: 'round-1-judge'}],
        transcript: [{id: 'live', kind: 'assistant', label: 'Agent', content: 'live output'}],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    const frame = await testRenderer.waitForFrame(value => value.includes('Round 1 flow'));

    expect(frame).not.toContain('Round 1 flow ·');
  });

  it('submits typed commands when Enter is pressed', async () => {
    const testRenderer = await createTestRenderer({width: 80, height: 16});
    const controller = new FakeController(initialSessionState());
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    await testRenderer.mockInput.typeText('/help');
    testRenderer.mockInput.pressEnter();
    await testRenderer.waitForFrame(() => controller.submissions.length === 1);
    expect(controller.submissions).toEqual(['/help']);
  });

  it('rejects ordinary text from the command input without sending chat', async () => {
    const testRenderer = await createTestRenderer({width: 80, height: 16});
    const controller = new FakeController(initialSessionState());
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    await testRenderer.mockInput.typeText('what is running?');
    testRenderer.mockInput.pressEnter();
    const frame = await testRenderer.waitForFrame(value => value.includes('Commands start with /'));
    expect(frame).toContain('Use Experiment chat for questions.');
    expect(controller.submissions).toEqual([]);
    expect(controller.chatSubmissions).toEqual([]);
  });

  it('suggests and completes slash commands with Tab', async () => {
    const testRenderer = await createTestRenderer({width: 80, height: 16});
    const controller = new FakeController(initialSessionState());
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    await testRenderer.mockInput.typeText('/pa');
    const suggestions = await testRenderer.waitForFrame(value => value.includes('[Tab]'));
    expect(suggestions).toContain('/pause');
    expect(suggestions).not.toContain('/help  ');
    expect(suggestions).not.toContain('/perf');
    expect(suggestions.indexOf('/pause')).toBeLessThan(suggestions.indexOf('Command'));
    expect(testRenderer.renderer.root.findDescendantById('command-input-box')?.height).toBe(3);

    testRenderer.mockInput.pressKey('TAB');
    testRenderer.mockInput.pressEnter();
    await testRenderer.waitForFrame(() => controller.submissions.length === 1);
    expect(controller.submissions).toEqual(['/pause']);
  });

  it('highlights a leading slash-command token', async () => {
    const testRenderer = await createTestRenderer({width: 80, height: 16});
    const controller = new FakeController(initialSessionState());
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    await testRenderer.mockInput.typeText('/steer inspect the cache');
    const input = testRenderer.renderer.root.findDescendantById('command-input');
    expect(input).toBeInstanceOf(InputRenderable);
    if (!(input instanceof InputRenderable)) throw new Error('input was not rendered');
    expect(input.getLineHighlights(0)).toMatchObject([{start: 0, end: 6}]);
  });

  it('exits on the first Ctrl-C even while the input is focused', async () => {
    const testRenderer = await createTestRenderer({width: 80, height: 16, exitOnCtrlC: false});
    const controller = new FakeController(initialSessionState());
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    cleanup.push(() => app.destroy());
    const destroyed = new Promise<void>(resolve => testRenderer.renderer.once('destroy', resolve));

    testRenderer.mockInput.pressKey('c', {ctrl: true});

    await destroyed;
  });

  it('copies a selected range on Ctrl-C without exiting', async () => {
    const testRenderer = await createTestRenderer({width: 80, height: 16, exitOnCtrlC: false});
    const controller = new FakeController(initialSessionState());
    const clipboard = clipboardReturning('copied');
    const app = createOpenTuiApp(testRenderer.renderer, controller, clipboard);
    registerCleanup(testRenderer.renderer, app);
    await testRenderer.waitForFrame(value => value.includes('Command'));

    testRenderer.mockInput.pressKey('c', {ctrl: true});

    const frame = await testRenderer.waitForFrame(value => value.includes('Copied selected text'));
    expect(frame).toContain('Ctrl+C exits when no text is selected');
    expect(clipboard.calls).toBe(1);

    testRenderer.mockInput.pressKey('x');
    await testRenderer.waitForFrame(value => !value.includes('Copied selected text'));
  });

  it('keeps running and explains the fallback when OSC52 copy is unavailable', async () => {
    const testRenderer = await createTestRenderer({width: 90, height: 16, exitOnCtrlC: false});
    const controller = new FakeController(initialSessionState());
    const clipboard = clipboardReturning('unsupported');
    const app = createOpenTuiApp(testRenderer.renderer, controller, clipboard);
    registerCleanup(testRenderer.renderer, app);
    await testRenderer.waitForFrame(value => value.includes('Command'));

    testRenderer.mockInput.pressKey('c', {ctrl: true});

    const frame = await testRenderer.waitForFrame(value => value.includes('Copy unavailable'));
    expect(frame).toContain('selection kept');
    expect(frame).toContain('terminal copy command');
    expect(clipboard.calls).toBe(1);
  });

  it('advertises Escape and returns a non-live view to live output', async () => {
    const testRenderer = await createTestRenderer({width: 100, height: 16});
    const controller = new FakeController({
      ...initialSessionState(),
      overlay: {kind: 'help', content: 'Available commands'},
      core: {
        ...initialSessionState().core,
        transcript: [{id: 'live', kind: 'assistant', label: 'Agent', content: 'live output'}],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    const overlay = await testRenderer.waitForFrame(value => value.includes('Esc: close dialog'));
    expect(overlay).toContain('Available commands');
    expect(overlay).toContain('Rounds');
    expect(overlay).toContain('Agents');
    testRenderer.mockInput.pressKey('ESCAPE');
    await testRenderer.waitForFrame(value => !value.includes('Esc: close dialog'));
    expect(controller.liveCalls).toBe(1);
  });

  it('uses the native scrollbox for long output', async () => {
    const lines = Array.from({length: 50}, (_, index) => `tool output line ${index + 1}`).join(
      '\n',
    );
    const testRenderer = await createTestRenderer({width: 80, height: 16});
    const controller = new FakeController({
      ...initialSessionState(),
      core: {
        ...initialSessionState().core,
        transcript: [{id: 'assistant', kind: 'assistant', label: 'Agent', content: lines}],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    await testRenderer.waitForFrame(value => value.includes('tool output line 50'));
    testRenderer.mockInput.pressKey('HOME');
    const frame = await testRenderer.waitForFrame(value => value.includes('tool output line 1'));
    expect(frame).not.toContain('tool output line 50');
  });

  it('renders a tool call and response as two regions in one card', async () => {
    const testRenderer = await createTestRenderer({width: 80, height: 16});
    const controller = new FakeController({
      ...initialSessionState(),
      core: {
        ...initialSessionState().core,
        transcript: [
          {
            id: 'tool',
            kind: 'tool',
            label: 'implementer · round 1',
            content: '2 passed',
            toolName: 'Bash',
            toolArguments: {command: 'pytest'},
            toolResult: {kind: 'tool_result', tool: 'Bash', content: '2 passed'},
          },
        ],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    const frame = await testRenderer.waitForFrame(value => value.includes('2 passed'));
    expect(frame).toContain('→ Bash(command="pytest")');
    expect(frame.match(/╭/g)).toHaveLength(5);
  });

  it('collapses prompts and expands the latest prompt with Ctrl+P', async () => {
    const content = Array.from({length: 20}, (_, index) => `prompt line ${index + 1}`).join('\n');
    const testRenderer = await createTestRenderer({width: 80, height: 20});
    const controller = new FakeController({
      ...initialSessionState(),
      core: {
        ...initialSessionState().core,
        transcript: [{id: 'prompt', kind: 'prompt', label: 'Prompt', content}],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    const collapsed = await testRenderer.waitForFrame(value => value.includes('8 more lines'));
    expect(collapsed).not.toContain('prompt line 20');
    testRenderer.mockInput.pressKey('p', {ctrl: true});
    const expanded = await testRenderer.waitForFrame(value => value.includes('prompt line 20'));
    expect(expanded).toContain('collapse');
  });

  it('expands the latest visible prompt without dropping agent filters', async () => {
    const visiblePrompt = Array.from(
      {length: 20},
      (_, index) => `implementer prompt ${index + 1}`,
    ).join('\n');
    const hiddenPrompt = Array.from({length: 20}, (_, index) => `judge prompt ${index + 1}`).join(
      '\n',
    );
    const testRenderer = await createTestRenderer({width: 100, height: 20});
    const controller = new FakeController({
      ...initialSessionState(),
      selectedRound: 1,
      selectedAgentKind: 'implementer',
      core: {
        ...initialSessionState().core,
        rounds: [{number: 1, status: 'active'}],
        transcript: [
          {
            id: 'implementer-prompt',
            kind: 'prompt',
            agentKind: 'implementer',
            roundNumber: 1,
            content: visiblePrompt,
          },
          {
            id: 'judge-prompt',
            kind: 'prompt',
            agentKind: 'judge',
            roundNumber: 1,
            content: hiddenPrompt,
          },
        ],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    await testRenderer.waitForFrame(value => value.includes('implementer prompt 1'));
    testRenderer.mockInput.pressKey('p', {ctrl: true});
    const expanded = await testRenderer.waitForFrame(value =>
      value.includes('implementer prompt 20'),
    );
    expect(expanded).not.toContain('judge prompt');
  });

  it('preserves existing cards when a large transcript receives state-only and tail updates', async () => {
    const conversation = Array.from({length: 1_000}, (_, index) => ({
      id: `entry-${index}`,
      kind: 'status' as const,
      content: `event ${index}`,
    }));
    const testRenderer = await createTestRenderer({width: 100, height: 20});
    const initial = initialSessionState();
    const controller = new FakeController({
      ...initial,
      core: {...initial.core, transcript: conversation},
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await testRenderer.waitForFrame(value => value.includes('event 999'));
    const firstCard = testRenderer.renderer.root.findDescendantById('event-entry-0');
    const lastCard = testRenderer.renderer.root.findDescendantById('event-entry-999');

    controller.publish({
      ...controller.state,
      core: {
        ...controller.state.core,
        status: 'paused',
      },
    });
    expect(testRenderer.renderer.root.findDescendantById('event-entry-0')).toBe(firstCard);
    expect(testRenderer.renderer.root.findDescendantById('event-entry-999')).toBe(lastCard);

    const previousLast = conversation.at(-1);
    if (previousLast === undefined) throw new Error('large transcript is unexpectedly empty');
    const updatedLast = {...previousLast, content: 'updated tail'};
    controller.publish({
      ...controller.state,
      core: {
        ...controller.state.core,
        transcript: [...conversation.slice(0, -1), updatedLast],
      },
    });
    await testRenderer.waitForFrame(value => value.includes('updated tail'));
    expect(testRenderer.renderer.root.findDescendantById('event-entry-0')).toBe(firstCard);
    expect(testRenderer.renderer.root.findDescendantById('event-entry-999')).not.toBe(lastCard);
  });

  it('selects an agent with Tab and filters the transcript', async () => {
    const testRenderer = await createTestRenderer({width: 100, height: 20});
    const controller = new FakeController({
      ...initialSessionState(),
      core: {
        ...initialSessionState().core,
        phases: [
          {kind: 'implementer', status: 'completed', roundNumber: 1, roundLabel: 'round-1'},
          {kind: 'judge', status: 'active', roundNumber: 1, roundLabel: 'round-1'},
        ],
        rounds: [{number: 1, status: 'active'}],
        transcript: [
          {
            id: 'implementer',
            kind: 'assistant',
            label: 'implementer · round 1',
            agentKind: 'implementer',
            roundNumber: 1,
            content: 'edited files',
          },
          {
            id: 'judge',
            kind: 'assistant',
            label: 'judge · round 1',
            agentKind: 'judge',
            roundNumber: 1,
            content: 'checking behavior',
          },
        ],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    await testRenderer.waitForFrame(value => value.includes('checking behavior'));
    testRenderer.mockInput.pressKey('TAB');
    const filtered = await testRenderer.waitForFrame(value =>
      value.includes('selected implementer'),
    );
    expect(filtered).toContain('edited files');
    expect(filtered).not.toContain('checking behavior');
  });

  it('summarizes the active agent’s todos and expands them with Ctrl+T', async () => {
    const testRenderer = await createTestRenderer({width: 100, height: 24});
    const controller = new FakeController({
      ...initialSessionState(),
      core: {
        ...initialSessionState().core,
        agentKind: 'implementer',
        todos: [
          {
            agentKind: 'implementer',
            roundNumber: null,
            items: [
              {content: 'Profile the hot loop', status: 'completed'},
              {content: 'Vectorize the kernel', status: 'in_progress'},
              {content: 'Re-run the benchmark', status: 'pending'},
            ],
          },
        ],
        transcript: [{id: 'live', kind: 'assistant', label: 'Agent', content: 'live output'}],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    const collapsed = await testRenderer.waitForFrame(value => value.includes('Todo 1/3'));
    expect(collapsed).toContain('▶ Vectorize the kernel');
    expect(collapsed).not.toContain('Re-run the benchmark');

    testRenderer.mockInput.pressKey('t', {ctrl: true});
    const expanded = await testRenderer.waitForFrame(value =>
      value.includes('Re-run the benchmark'),
    );
    expect(expanded).toContain('✓ Profile the hot loop');
    expect(expanded).toContain('○ Re-run the benchmark');
  });

  it('hides the todo strip when the visible agent has no todos', async () => {
    const testRenderer = await createTestRenderer({width: 100, height: 20});
    const controller = new FakeController({
      ...initialSessionState(),
      core: {
        ...initialSessionState().core,
        agentKind: 'judge',
        todos: [
          {
            agentKind: 'implementer',
            roundNumber: null,
            items: [{content: 'Edit files', status: 'completed'}],
          },
        ],
        transcript: [{id: 'live', kind: 'assistant', label: 'Agent', content: 'live output'}],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    const frame = await testRenderer.waitForFrame(value => value.includes('live output'));
    expect(frame).not.toContain('Todo');
    expect(frame).not.toContain('Edit files');
  });

  it('keeps terminal results visible until the operator exits', async () => {
    const testRenderer = await createTestRenderer({width: 80, height: 16});
    const controller = new FakeController({
      ...initialSessionState(),
      core: {
        ...initialSessionState().core,
        status: 'failed',
        terminal: true,
        transcript: [
          {
            id: 'configuration-error',
            kind: 'result',
            label: 'Configuration failed',
            content: 'Invalid --max-rounds value',
            tone: 'failure',
          },
        ],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    let destroyed = false;
    testRenderer.renderer.once('destroy', () => {
      destroyed = true;
    });

    const frame = await testRenderer.waitForFrame(value =>
      value.includes('Invalid --max-rounds value'),
    );
    expect(frame).toContain('Configuration failed');
    await new Promise(resolve => setTimeout(resolve, 150));
    expect(destroyed).toBe(false);
  });

  it('opens a focused chat popup after a configuration failure', async () => {
    const testRenderer = await createTestRenderer({width: 100, height: 24});
    const controller = new FakeController({
      ...initialSessionState(),
      core: {
        ...initialSessionState().core,
        status: 'failed',
        terminal: true,
        transcript: [
          {
            id: 'configuration-error',
            kind: 'result',
            label: 'Configuration failed',
            content: 'agent.toml was not found',
            tone: 'failure',
          },
        ],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    await testRenderer.mockInput.typeText('/chat');
    testRenderer.mockInput.pressEnter();
    const popup = await testRenderer.waitForFrame(value => value.includes('Experiment chat'));
    expect(popup).toContain('Ask about this experiment');
    expect(popup).toContain('Message');

    await testRenderer.mockInput.typeText('why did startup fail?');
    testRenderer.mockInput.pressEnter();
    const answer = await testRenderer.waitForFrame(value => value.includes('Recorded diagnostic'));
    expect(controller.chatSubmissions).toEqual(['why did startup fail?']);
    expect(answer).toContain('Inspecting configuration events');
    expect(answer).toContain('→ Read(run-events.jsonl)');

    const overlay = testRenderer.renderer.root.findDescendantById('chat-overlay');
    const transcript = testRenderer.renderer.root.findDescendantById('chat-transcript');
    const turn = testRenderer.renderer.root.findDescendantById('event-chat-user');
    const input = testRenderer.renderer.root.findDescendantById('chat-modal-composer-box');
    if (
      overlay === undefined ||
      transcript === undefined ||
      turn === undefined ||
      input === undefined
    )
      throw new Error('modal chat geometry was missing');
    expect(transcript.x).toBe(overlay.x + 2);
    expect(turn.x).toBe(transcript.x);
    expect(input.x).toBe(transcript.x);

    testRenderer.mockInput.pressKey('ESCAPE');
    await testRenderer.waitForFrame(value => !value.includes('Experiment chat'));
    expect(controller.state.chatOpen).toBe(false);
  });

  it('accepts another chat message while an agent turn is pending', async () => {
    const testRenderer = await createTestRenderer({width: 100, height: 24});
    const controller = new FakeController({
      ...initialSessionState(),
      chatPending: true,
      chatConversation: [
        {id: 'active-question', kind: 'user', label: 'You', content: 'first question'},
      ],
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    await testRenderer.mockInput.typeText('/chat');
    testRenderer.mockInput.pressEnter();
    await testRenderer.waitForFrame(value => value.includes('Experiment chat'));
    await testRenderer.mockInput.typeText('queued follow-up');
    testRenderer.mockInput.pressEnter();

    await testRenderer.waitForFrame(value => value.includes('Recorded diagnostic'));
    expect(controller.chatSubmissions).toEqual(['queued follow-up']);
  });
});

describe('theming', () => {
  const assistantEntry = {
    id: 'themed',
    kind: 'assistant' as const,
    label: 'implementer',
    agentKind: 'implementer',
    content: 'themed body text',
  };

  it('paints the whole surface from the selected theme', async () => {
    const light = resolveTheme('light');
    const testRenderer = await createTestRenderer({width: 90, height: 20});
    const controller = new FakeController({
      ...initialSessionState('light'),
      core: {
        ...initialSessionState('light').core,
        status: 'running',
        transcript: [assistantEntry],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await testRenderer.waitForFrame(value => value.includes('themed body text'));

    expect(spanColors(testRenderer, 'VibeSys')?.fg).toBe(light.accent);
    const body = spanColors(testRenderer, 'themed body text');
    expect(body?.fg).toBe(light.conversation.assistant.content);
    expect(body?.bg).toBe(light.conversation.assistant.background);
    expect(spanColors(testRenderer, 'implementer')?.fg).toBe(light.conversation.assistant.label);
  });

  it('keeps the dark baseline identical to the pre-theme palette', async () => {
    const testRenderer = await createTestRenderer({width: 90, height: 20});
    const controller = new FakeController({
      ...initialSessionState(),
      core: {
        ...initialSessionState().core,
        status: 'running',
        transcript: [assistantEntry],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await testRenderer.waitForFrame(value => value.includes('themed body text'));

    expect(spanColors(testRenderer, 'VibeSys')?.fg).toBe('#22d3ee');
    const body = spanColors(testRenderer, 'themed body text');
    expect(body?.fg).toBe('#e2e8f0');
    // Assistant cards derive their fill from the canvas and the role accent.
    expect(body?.bg).toBe('#0e283d');
    expect(spanColors(testRenderer, 'implementer')?.fg).toBe('#5cb6cc');
  });

  it('repaints live when the selected theme changes', async () => {
    const testRenderer = await createTestRenderer({width: 90, height: 20});
    const controller = new FakeController({
      ...initialSessionState(),
      core: {
        ...initialSessionState().core,
        status: 'running',
        transcript: [assistantEntry],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await testRenderer.waitForFrame(value => value.includes('themed body text'));
    expect(spanColors(testRenderer, 'VibeSys')?.fg).toBe(resolveTheme('dark').accent);

    controller.setTheme('solarized-light');
    await testRenderer.waitForVisualIdle();

    const solarized = resolveTheme('solarized-light');
    expect(spanColors(testRenderer, 'VibeSys')?.fg).toBe(solarized.accent);
    const body = spanColors(testRenderer, 'themed body text');
    expect(body?.fg).toBe(solarized.conversation.assistant.content);
    expect(body?.bg).toBe(solarized.conversation.assistant.background);
  });

  it('navigates the theme list with the keyboard and applies on Enter', async () => {
    const testRenderer = await createTestRenderer({width: 90, height: 24});
    const controller = new FakeController({
      ...initialSessionState(),
      core: {
        ...initialSessionState().core,
        rounds: [
          {number: 1, status: 'completed'},
          {number: 2, status: 'active'},
        ],
        transcript: [{id: 'live', kind: 'assistant', label: 'Agent', content: 'live output'}],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    await testRenderer.mockInput.typeText('/theme');
    testRenderer.mockInput.pressEnter();
    const opened = await testRenderer.waitForFrame(value => value.includes('Themes'));
    // The view opens on the theme in use.
    expect(opened).toContain('\u203a dark');
    expect(opened).toContain('active');

    testRenderer.mockInput.pressKey('ARROW_DOWN');
    const moved = await testRenderer.waitForFrame(value => value.includes('\u203a light'));
    expect(moved).not.toContain('\u203a dark');
    // Navigating the list leaves the view behind it alone.
    expect(controller.state.selectedRound).toBeNull();
    expect(controller.state.selectedAgentKind).toBeNull();
    expect(controller.state.themeName).toBe('dark');

    testRenderer.mockInput.pressEnter();
    await testRenderer.waitForVisualIdle();

    expect(controller.state.themeName).toBe('light');
    expect(controller.state.themePicker).toBeNull();
    expect(spanColors(testRenderer, 'VibeSys')?.fg).toBe(resolveTheme('light').accent);
  });

  it('closes the theme list on Escape without switching theme', async () => {
    const testRenderer = await createTestRenderer({width: 90, height: 24});
    const controller = new FakeController({
      ...initialSessionState(),
      core: {
        ...initialSessionState().core,
        transcript: [{id: 'live', kind: 'assistant', label: 'Agent', content: 'live output'}],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    await testRenderer.mockInput.typeText('/theme');
    testRenderer.mockInput.pressEnter();
    await testRenderer.waitForFrame(value => value.includes('Themes'));
    testRenderer.mockInput.pressKey('ARROW_DOWN');
    await testRenderer.waitForFrame(value => value.includes('\u203a light'));

    testRenderer.mockInput.pressKey('ESCAPE');
    // A bare ESC is held by the stdin parser until its escape-sequence
    // timeout expires, so the key lands a beat after it is pressed.
    await new Promise(resolve => setTimeout(resolve, 40));
    await testRenderer.flush();
    const closed = testRenderer.captureCharFrame();

    expect(closed).not.toContain('\u203a light');
    expect(closed).toContain('live output');
    expect(controller.state.themePicker).toBeNull();
    expect(controller.state.themeName).toBe('dark');
    // Escape closed the picker rather than resetting the view behind it.
    expect(controller.liveCalls).toBe(0);
  });

  it('leaves Enter to a typed command while the theme list is open', async () => {
    const testRenderer = await createTestRenderer({width: 90, height: 24});
    const controller = new FakeController(initialSessionState());
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    await testRenderer.mockInput.typeText('/theme');
    testRenderer.mockInput.pressEnter();
    await testRenderer.waitForFrame(value => value.includes('Themes'));
    testRenderer.mockInput.pressKey('ARROW_DOWN');
    await testRenderer.waitForFrame(value => value.includes('\u203a light'));

    await testRenderer.mockInput.typeText('/help');
    testRenderer.mockInput.pressEnter();
    await testRenderer.waitForFrame(() => controller.submissions.length === 2);

    // The command ran; the highlighted theme was not applied by its Enter.
    expect(controller.submissions).toEqual(['/theme', '/help']);
    expect(controller.state.themeName).toBe('dark');
    expect(controller.state.themePicker?.selected).toBe('light');
  });

  it('owns its keys over the chat it was opened from', async () => {
    const testRenderer = await createTestRenderer({width: 90, height: 26});
    const controller = new FakeController({...initialSessionState(), chatOpen: true});
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await testRenderer.waitForFrame(value => value.includes('Ask a question about'));

    await controller.submitChat('/theme');
    await testRenderer.waitForFrame(value => value.includes('Themes'));
    testRenderer.mockInput.pressKey('ARROW_DOWN');
    await testRenderer.waitForFrame(value => value.includes('\u203a light'));

    // The chat is still open behind the picker, and neither the arrows nor
    // Escape reached it.
    expect(controller.state.chatOpen).toBe(true);
    testRenderer.mockInput.pressKey('ESCAPE');
    // A bare ESC is held by the stdin parser until its escape-sequence
    // timeout expires, so the key lands a beat after it is pressed.
    await new Promise(resolve => setTimeout(resolve, 40));
    await testRenderer.flush();
    expect(testRenderer.captureCharFrame()).not.toContain('\u203a light');
    expect(controller.state.themePicker).toBeNull();
    expect(controller.state.chatOpen).toBe(true);
    expect(controller.chatSubmissions).toEqual([]);
  });

  it('themes the overlay and the chat panel from the same theme', async () => {
    const latte = resolveTheme('catppuccin-latte');
    const testRenderer = await createTestRenderer({width: 90, height: 30});
    const controller = new FakeController(initialSessionState('catppuccin-latte'));
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    controller.publish({
      ...controller.state,
      overlay: {kind: 'error', content: 'configuration failed'},
    });
    await testRenderer.waitForFrame(value => value.includes('configuration failed'));
    expect(spanColors(testRenderer, 'configuration failed')?.fg).toBe(
      latte.conversation.failure.content,
    );
    expect(spanColors(testRenderer, 'Esc to close')?.fg).toBe(latte.textSubtle);

    controller.publish({...controller.state, overlay: null, chatOpen: true});
    await testRenderer.waitForFrame(value => value.includes('Ask a question about'));
    expect(spanColors(testRenderer, 'Ask a question about')?.fg).toBe(latte.textSubtle);
  });

  it('conveys agent and todo status with glyphs and words, not color alone', async () => {
    const testRenderer = await createTestRenderer({width: 100, height: 36});
    const controller = new FakeController({
      ...initialSessionState('high-contrast-light'),
      todosExpanded: true,
      core: {
        ...initialSessionState('high-contrast-light').core,
        rounds: [{number: 1, status: 'active'}],
        phases: [
          {kind: 'implementer', status: 'completed', roundNumber: 1, roundLabel: 'round-1'},
          {kind: 'judge', status: 'failed', roundNumber: 1, roundLabel: 'round-1'},
          {kind: 'profiler', status: 'cancelled', roundNumber: 1, roundLabel: 'round-1'},
          {kind: 'reviewer', status: 'interrupted', roundNumber: 1, roundLabel: 'round-1'},
        ],
        todos: [
          {
            agentKind: null,
            roundNumber: null,
            items: [
              {content: 'write the kernel', status: 'completed'},
              {content: 'benchmark it', status: 'in_progress'},
            ],
          },
        ],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    const frame = await testRenderer.waitForFrame(value => value.includes('benchmark it'));

    expect(frame).toContain('✓ implementer');
    expect(frame).toContain('× judge');
    expect(frame).toContain('completed');
    expect(frame).toContain('failed');
    expect(frame).toContain('■ profiler');
    expect(frame).toContain('cancelled');
    expect(frame).toContain('! reviewer');
    expect(frame).toContain('interrupted');
    expect(frame).toContain('✓ write the kernel');
    expect(frame).toContain('▶ benchmark it');
  });
  it('lands on the experiment log instead of the per-round transcript', async () => {
    const testRenderer = await createTestRenderer({width: 120, height: 22});
    const controller = new FakeController({
      ...initialSessionState(),
      core: {
        ...initialSessionState().core,
        rounds: [{number: 41, status: 'completed'}],
        transcript: [
          {
            id: 'a',
            kind: 'assistant',
            label: 'implementer',
            content: 'round 41 detail',
            roundNumber: 41,
          },
        ],
      },
    });
    // The landing view is what a fresh client starts on.
    controller.publish({...controller.state, experimentLog: initialSessionState().experimentLog});
    controller.experiments = [
      logEntry('H-07', 41, 41, {
        claim: 'batch the prefill step',
        resolved_outcome: 'proven',
        judge_verdict: 'pass',
        rounds: [{round: 41, passed: true, reviewed: true}],
      }),
    ];
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();

    const landing = await frameAfter(testRenderer);
    expect(landing).toContain('Experiments');
    expect(landing).toContain('Implementation Details');
    expect(landing).toContain('Outcome');
    expect(landing).toContain('H-07');
    expect(landing).toContain('Accepted');
    expect(landing).not.toContain('Verdict');
    expect(landing).not.toContain('Pass');
    // Per-round detail is what the operator opts into, not what greets them.
    expect(landing).not.toContain('round 41 detail');
    // The rounds strip and agent map are per-round chrome; neither is drawn.
    expect(landing).not.toContain('─ Rounds ─');
    expect(landing).not.toContain('Agents');
  });

  it('opens the round trajectory behind a hypothesis and steps back out', async () => {
    const testRenderer = await createTestRenderer({width: 120, height: 24});
    const controller = new FakeController({
      ...initialSessionState(),
      core: {
        ...initialSessionState().core,
        rounds: [
          {number: 41, status: 'completed'},
          {number: 42, status: 'completed'},
          {number: 43, status: 'completed'},
        ],
        transcript: [
          {
            id: 'a',
            kind: 'assistant',
            label: 'implementer',
            content: 'unrelated round 41',
            roundNumber: 41,
          },
          {
            id: 'b',
            kind: 'assistant',
            label: 'implementer',
            content: 'grew the block',
            roundNumber: 42,
          },
          {
            id: 'c',
            kind: 'assistant',
            label: 'judge',
            content: 'regression found',
            roundNumber: 43,
          },
        ],
      },
    });
    controller.experiments = [
      logEntry('H-07', 41, 41, {
        claim: 'batch the prefill step',
        resolved_outcome: 'proven',
        rounds: [{round: 41, passed: true, reviewed: true}],
      }),
      logEntry('H-08', 42, 43, {
        claim: 'bigger KV cache block',
        resolved_outcome: 'rejected',
        rounds: [
          {round: 42, passed: false, reviewed: false},
          {round: 43, passed: false, reviewed: true},
        ],
      }),
    ];
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();
    const table = await frameAfter(testRenderer);
    expect(table).toContain('42-43');
    expect(table).not.toContain('grew the block');

    testRenderer.mockInput.pressKey('ARROW_DOWN');
    testRenderer.mockInput.pressEnter();
    const trajectory = await frameAfter(testRenderer);

    // Opening a hypothesis lands on its latest round, and the earlier ones are
    // one `[` away.
    expect(trajectory).toContain('r43');
    expect(trajectory).toContain('regression found');
    expect(trajectory).not.toContain('unrelated round 41');
    expect(trajectory).toContain('H-08 · r42-43');
    expect(trajectory).toContain('r42');
    expect(trajectory).toContain('r43');
    // The strip covers the whole run, so rounds outside this hypothesis are
    // reachable from it; the transcript still shows only the selected round.
    expect(trajectory).toContain('r41');
    expect(controller.state.hypothesisScope).toMatchObject({id: 'H-08', rounds: [42, 43]});

    testRenderer.mockInput.pressKey('ESCAPE');
    const back = await frameAfterEscape(testRenderer);
    expect(back).toContain('Implementation Details');
    expect(back).not.toContain('grew the block');
    expect(controller.state.experimentLog?.selectedId).toBe('H-08');
  });

  it('runs a typed command from the log on the first Enter', async () => {
    const testRenderer = await createTestRenderer({width: 120, height: 20});
    const controller = new FakeController(initialSessionState());
    controller.experiments = [
      logEntry('H-07', 41, 41, {
        claim: 'batch the prefill step',
        resolved_outcome: 'proven',
        rounds: [{round: 41, passed: true, reviewed: true}],
      }),
    ];
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();
    await frameAfter(testRenderer);

    await testRenderer.mockInput.typeText('/help');
    testRenderer.mockInput.pressEnter();
    await testRenderer.waitForFrame(() => controller.submissions.length === 1);

    // One Enter runs the command; the table is still the view behind it.
    expect(controller.submissions).toEqual(['/help']);
    expect(controller.state.hypothesisScope).toBeNull();
    expect(await frameAfter(testRenderer)).toContain('Implementation Details');
  });

  it('opens a command overlay on the log and leaves the table behind it', async () => {
    const testRenderer = await createTestRenderer({width: 120, height: 24});
    const controller = new FakeController(initialSessionState());
    controller.experiments = [
      logEntry('H-07', 41, 41, {
        claim: 'batch the prefill step',
        resolved_outcome: 'proven',
        rounds: [{round: 41, passed: true, reviewed: true}],
      }),
    ];
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();
    controller.publish({
      ...controller.state,
      overlay: {kind: 'help', content: 'Available commands'},
    });

    const overlaid = await frameAfter(testRenderer);
    expect(overlaid).toContain('Available commands');
    expect(controller.state.experimentLog).not.toBeNull();

    // Enter behind an overlay must not move the operator somewhere unseen.
    testRenderer.mockInput.pressEnter();
    await frameAfter(testRenderer);
    expect(controller.state.hypothesisScope).toBeNull();

    testRenderer.mockInput.pressKey('ESCAPE');
    const back = await frameAfterEscape(testRenderer);
    expect(back).toContain('Implementation Details');
    expect(back).not.toContain('Available commands');
  });

  it('colors the outcome cell from the active theme in light and dark', async () => {
    for (const name of ['dark', 'light'] as const) {
      const theme = resolveTheme(name);
      const testRenderer = await createTestRenderer({width: 120, height: 18});
      const controller = new FakeController(initialSessionState(name));
      controller.experiments = [
        logEntry('H-07', 41, 41, {claim: 'batch the prefill step', resolved_outcome: 'proven'}),
        logEntry('H-08', 42, 43, {claim: 'bigger KV cache block', resolved_outcome: 'disproven'}),
        logEntry('H-09', 44, 44, {
          claim: 'retry with tuning',
          resolved_outcome: null,
          active: true,
        }),
      ];
      const app = createOpenTuiApp(testRenderer.renderer, controller);
      registerCleanup(testRenderer.renderer, app);
      await controller.openExperimentLog();
      await frameAfter(testRenderer);

      expect(spanColors(testRenderer, 'Accepted')?.fg).toBe(theme.success);
      expect(spanColors(testRenderer, 'Rejected')?.fg).toBe(theme.error);
      expect(spanColors(testRenderer, 'Active')?.fg).toBe(theme.warning);
      // The claim keeps body text: only the resolution is colored.
      expect(spanColors(testRenderer, 'Batch the prefill step')?.fg).toBe(theme.textPrimary);
    }
  });

  it('scrolls a log taller than the panel and keeps ordering stable', async () => {
    const testRenderer = await createTestRenderer({width: 120, height: 20});
    const controller = new FakeController(initialSessionState());
    controller.experiments = Array.from({length: 120}, (_, index) =>
      logEntry(`H-${String(index + 1).padStart(3, '0')}`, index + 1, index + 1, {
        resolved_outcome: index % 2 === 0 ? 'proven' : 'rejected',
      }),
    );
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();

    const first = await frameAfter(testRenderer);
    expect(first).toContain('1/120');
    expect(first).not.toContain('H-120');

    // No named page key in the mock input; send the raw terminal sequence.
    for (let index = 0; index < 12; index += 1) testRenderer.mockInput.pressKey('\x1B[6~');
    const scrolled = await frameAfter(testRenderer);
    expect(scrolled).toContain('120/120');
    expect(scrolled).not.toContain('H-001');
  });

  it('scrolls the log with the wheel, independently of the selection', async () => {
    const testRenderer = await createTestRenderer({width: 120, height: 18});
    const controller = new FakeController(initialSessionState());
    controller.experiments = Array.from({length: 60}, (_, index) =>
      logEntry(`H-${String(index + 1).padStart(3, '0')}`, index + 1, index + 1, {
        resolved_outcome: 'proven',
      }),
    );
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();
    const top = await frameAfter(testRenderer);
    expect(top).toContain('H-001');

    const rows = testRenderer.renderer.root.findDescendantById('experiment-rows');
    if (!(rows instanceof ScrollBoxRenderable)) throw new Error('rows were not a scroll box');
    for (let index = 0; index < 20; index += 1) {
      await testRenderer.mockMouse.scroll(60, 8, 'down');
    }
    const scrolled = await frameAfter(testRenderer);

    expect(rows.scrollTop).toBeGreaterThan(0);
    expect(scrolled).not.toContain('H-001');
    // The wheel moves the viewport, not the cursor.
    expect(controller.state.experimentLog?.selectedId).toBe('H-001');
  });

  it('lands with the chat docked beside the hypothesis table', async () => {
    const testRenderer = await createTestRenderer({width: 140, height: 20});
    const controller = logController();
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();

    const landing = await frameAfter(testRenderer);

    // Both columns at once, and the table keeps the claim it came to show.
    expect(landing).toContain('Experiment chat');
    expect(landing).toContain('Experiments');
    expect(landing).toContain('Implementation Details');
    expect(landing).toContain('Batch the prefill step');

    // Each column has its own input, under the surface it writes to, and the
    // command box starts where the chat column ends rather than running under
    // it.
    // The cursor starts in the command box, and the chat says how to reach it.
    expect(landing).toContain('Ctrl+W to type here');
    const lines = landing.split('\n');
    const paneTop = lines.find(line => line.includes('╭─ Experiment chat')) ?? '';
    const messageTop = lines.find(line => line.includes('╭─ Message ')) ?? '';
    const commandTop = lines.find(line => line.includes('╭─ Command')) ?? '';
    expect(messageTop).not.toBe('');
    expect(commandTop.indexOf('╭─ Command')).toBe(paneTop.indexOf('╭─ ▸ Experiments'));
  });

  it('routes typing to whichever input Ctrl+W points at', async () => {
    const testRenderer = await createTestRenderer({width: 140, height: 20});
    const controller = logController();
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();
    await frameAfter(testRenderer);

    await testRenderer.mockInput.typeText('this belongs in chat');
    testRenderer.mockInput.pressEnter();
    await testRenderer.waitForFrame(value => value.includes('Commands start with /'));
    expect(controller.submissions).toEqual([]);
    expect(controller.chatSubmissions).toEqual([]);
    testRenderer.mockInput.pressKey('ESCAPE');
    await frameAfterEscape(testRenderer);

    testRenderer.mockInput.pressKey('w', {ctrl: true});
    await frameAfter(testRenderer);
    await testRenderer.mockInput.typeText('why is r41 slow?');
    testRenderer.mockInput.pressEnter();
    await testRenderer.waitForFrame(() => controller.chatSubmissions.length === 1);

    // The chat's own box took it, not the command input.
    expect(controller.chatSubmissions).toEqual(['why is r41 slow?']);
    expect(controller.submissions).toEqual([]);

    testRenderer.mockInput.pressKey('w', {ctrl: true});
    await frameAfter(testRenderer);
    await testRenderer.mockInput.typeText('/perf');
    testRenderer.mockInput.pressEnter();
    await testRenderer.waitForFrame(() => controller.submissions.length === 1);

    expect(controller.submissions).toEqual(['/perf']);
    expect(controller.chatSubmissions).toHaveLength(1);
  });

  it('wraps a long docked question, caps its growth, and submits every character', async () => {
    const testRenderer = await createTestRenderer({width: 120, height: 22});
    const controller = logController();
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();
    await frameAfter(testRenderer);

    testRenderer.mockInput.pressKey('w', {ctrl: true});
    await frameAfter(testRenderer);
    const question = Array.from({length: 28}, (_, index) => `word-${index}`).join(' ');
    await testRenderer.mockInput.typeText(question);
    await frameAfter(testRenderer);

    const composer = testRenderer.renderer.root.findDescendantById('chat-dock-composer');
    const editor = testRenderer.renderer.root.findDescendantById('chat-dock-composer-editor');
    if (composer === undefined || editor === undefined)
      throw new Error('chat composer was missing');
    expect(composer.height).toBe(9);
    expect(editor.height).toBe(6);

    testRenderer.mockInput.pressEnter();
    await testRenderer.waitForFrame(() => controller.chatSubmissions.length === 1);
    expect(controller.chatSubmissions).toEqual([question]);
    await frameAfter(testRenderer);
    expect(composer.height).toBe(4);
  });

  it('keeps multiline editor keys out of experiment navigation', async () => {
    const testRenderer = await createTestRenderer({width: 140, height: 20, kittyKeyboard: true});
    const controller = logController();
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();
    await frameAfter(testRenderer);
    const selected = controller.state.experimentLog?.selectedId;

    testRenderer.mockInput.pressKey('w', {ctrl: true});
    await frameAfter(testRenderer);
    await testRenderer.mockInput.typeText('first line');
    testRenderer.mockInput.pressEnter({shift: true});
    await testRenderer.mockInput.typeText('second line');
    testRenderer.mockInput.pressArrow('up');
    await frameAfter(testRenderer);

    expect(controller.state.experimentLog?.selectedId).toBe(selected);
    testRenderer.mockInput.pressEnter();
    await testRenderer.waitForFrame(() => controller.chatSubmissions.length >= 1);
    expect(controller.chatSubmissions).toEqual(['first line\nsecond line']);
  });

  it('preserves a draft when a resize moves chat from dock to modal', async () => {
    const testRenderer = await createTestRenderer({width: 140, height: 20});
    const controller = logController();
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();
    await frameAfter(testRenderer);

    testRenderer.mockInput.pressKey('w', {ctrl: true});
    await frameAfter(testRenderer);
    await testRenderer.mockInput.typeText('draft survives the layout change');
    testRenderer.renderer.resize(80, 20);
    await frameAfter(testRenderer);
    controller.publish({...controller.state, chatOpen: true});
    const modal = await testRenderer.waitForFrame(value =>
      value.includes('draft survives the layout change'),
    );

    expect(modal).toContain('Experiment chat');
    testRenderer.mockInput.pressEnter();
    await testRenderer.waitForFrame(() => controller.chatSubmissions.length === 1);
    expect(controller.chatSubmissions).toEqual(['draft survives the layout change']);
  });

  it('lets the docked chat span the table and command surface', async () => {
    const testRenderer = await createTestRenderer({width: 140, height: 20});
    const controller = logController();
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();
    await frameAfter(testRenderer);

    const chatPane = testRenderer.renderer.root.findDescendantById('chat-pane');
    const workspace = testRenderer.renderer.root.findDescendantById('workspace');
    const composer = testRenderer.renderer.root.findDescendantById('chat-dock-composer-box');
    if (chatPane === undefined || workspace === undefined || composer === undefined)
      throw new Error('landing layout was missing');
    expect(chatPane.y).toBe(workspace.y);
    expect(chatPane.y + chatPane.height).toBe(workspace.y + workspace.height);
    expect(composer.x).toBeGreaterThan(chatPane.x);
    expect(composer.x + composer.width).toBeLessThan(chatPane.x + chatPane.width);
  });

  it('raises the command list out of the command input, clear of the chat', async () => {
    const testRenderer = await createTestRenderer({width: 140, height: 20});
    const controller = logController();
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();
    await frameAfter(testRenderer);

    await testRenderer.mockInput.typeText('/pe');
    const frame = await testRenderer.waitForFrame(value => value.includes('[Tab]'));

    const lines = frame.split('\n');
    const suggestion = lines.find(line => line.includes('/perf')) ?? '';
    const commandInput = lines.find(line => line.includes('╭─ Command')) ?? '';
    // The list belongs to the box it completes, so it starts where that box
    // starts rather than running back across the chat column.
    expect(suggestion.indexOf('/perf')).toBeGreaterThan(commandInput.indexOf('╭─ Command'));
  });

  it('drops /chat from the command surface while the chat is already docked', async () => {
    const testRenderer = await createTestRenderer({width: 140, height: 20});
    const controller = logController();
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();
    await frameAfter(testRenderer);

    await testRenderer.mockInput.typeText('/c');
    const frame = await frameAfter(testRenderer);

    // Nothing to open: the chat is the column beside the table. The thread
    // commands (/chats, /new-chat) remain, so match /chat as a whole word.
    expect(frame).not.toMatch(/\/chat\s/);
  });

  it('says when the docked chat is waiting on the agent', async () => {
    const testRenderer = await createTestRenderer({width: 140, height: 20});
    const controller = logController();
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();

    controller.publish({...controller.state, chatPending: true});

    expect(await frameAfter(testRenderer)).toContain('Awaiting the agent');
  });

  it('answers in the docked chat without covering the table', async () => {
    const testRenderer = await createTestRenderer({width: 140, height: 20});
    const controller = logController();
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();

    testRenderer.mockInput.pressKey('w', {ctrl: true});
    await frameAfter(testRenderer);
    await testRenderer.mockInput.typeText('why is r41 slow?');
    testRenderer.mockInput.pressEnter();
    await testRenderer.waitForFrame(() => controller.chatSubmissions.length === 1);
    controller.publish({
      ...controller.state,
      chatConversation: [
        {id: 'q', kind: 'user', label: 'You', content: 'why is r41 slow?'},
        {id: 'a', kind: 'assistant', label: 'Answer', content: 'Prefill dominates.'},
      ],
    });

    const answered = await frameAfter(testRenderer);

    expect(answered).toContain('Prefill dominates.');
    expect(answered).toContain('H-07');
    expect(answered).toContain('Implementation Details');

    const pane = testRenderer.renderer.root.findDescendantById('chat-pane');
    const scroll = testRenderer.renderer.root.findDescendantById('chat-pane-scroll');
    const turn = testRenderer.renderer.root.findDescendantById('event-q');
    if (pane === undefined || scroll === undefined || turn === undefined)
      throw new Error('docked chat geometry was missing');
    expect(scroll.x).toBe(pane.x + 2);
    expect(turn.x).toBe(scroll.x);
  });

  it('switches chat threads, swapping the transcript and the composer draft', async () => {
    const testRenderer = await createTestRenderer({width: 140, height: 20});
    const controller = logController();
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();
    controller.publish({
      ...controller.state,
      core: {
        ...controller.state.core,
        chatThreads: [
          ...controller.state.core.chatThreads,
          {
            id: 'thread-a',
            title: 'GPU stalls',
            driver: 'omnigent',
            provider: 'claude',
            model: 'opus',
          },
        ],
      },
      chatConversations: {
        default: [
          {id: 'd1', kind: 'assistant', label: 'Answer', content: 'Default thread answer.'},
        ],
        'thread-a': [
          {id: 't1', kind: 'assistant', label: 'Answer', content: 'Stalls come from prefill.'},
        ],
      },
      chatConversation: [
        {id: 'd1', kind: 'assistant', label: 'Answer', content: 'Default thread answer.'},
      ],
    });

    // Focus the docked chat and leave a half-typed question on the default thread.
    testRenderer.mockInput.pressKey('w', {ctrl: true});
    await frameAfter(testRenderer);
    await testRenderer.mockInput.typeText('half-typed question');
    let frame = await frameAfter(testRenderer);
    expect(frame).toContain('Default thread answer.');
    expect(frame).toContain('half-typed question');

    controller.switchChatThread('thread-a');
    frame = await frameAfter(testRenderer);
    // The pane is titled by the backend-owned thread title, shows the
    // thread's own transcript, and the other thread's draft is parked.
    expect(frame).toContain('GPU stalls');
    expect(frame).toContain('Stalls come from prefill.');
    expect(frame).not.toContain('Default thread answer.');
    expect(frame).not.toContain('half-typed question');

    controller.switchChatThread('default');
    frame = await frameAfter(testRenderer);
    expect(frame).toContain('Default thread answer.');
    expect(frame).not.toContain('Stalls come from prefill.');
    // The parked draft returns with its thread.
    expect(frame).toContain('half-typed question');
  });

  it('walks the new-thread wizard from driver to provider to model', async () => {
    const testRenderer = await createTestRenderer({width: 90, height: 26});
    const controller = new FakeController(initialSessionState());
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    await testRenderer.mockInput.typeText('/new-chat');
    testRenderer.mockInput.pressEnter();
    await testRenderer.waitForFrame(value => value.includes('New chat thread'));
    expect(testRenderer.captureCharFrame()).toContain('› agentshim');

    testRenderer.mockInput.pressKey('ARROW_DOWN');
    await testRenderer.waitForFrame(value => value.includes('› omnigent'));
    testRenderer.mockInput.pressEnter();
    // The provider list is narrowed to what the chosen driver supports.
    let frame = await testRenderer.waitForFrame(value =>
      value.includes('Provider: claude  ◂ choose'),
    );
    expect(frame).toContain('› claude');
    expect(frame).not.toContain('gemini');

    testRenderer.mockInput.pressEnter();
    await testRenderer.waitForFrame(value => value.includes('Model:'));
    // The model step takes ordinary typing instead of the composer beneath it.
    await testRenderer.mockInput.typeText('opus');
    frame = await testRenderer.waitForFrame(value => value.includes('Model: opus'));
    expect(controller.state.newChatPicker).toMatchObject({
      step: 'model',
      driver: 'omnigent',
      provider: 'claude',
      model: 'opus',
    });
    expect(controller.chatSubmissions).toEqual([]);
  });

  it('lists the chat threads and switches to the highlighted one', async () => {
    const testRenderer = await createTestRenderer({width: 90, height: 26});
    const controller = new FakeController(initialSessionState());
    controller.publish({
      ...controller.state,
      core: {
        ...controller.state.core,
        chatThreads: [
          ...controller.state.core.chatThreads,
          {
            id: 'thread-a',
            title: 'GPU stalls',
            driver: 'omnigent',
            provider: 'claude',
            model: 'opus',
          },
        ],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    await testRenderer.mockInput.typeText('/chats');
    testRenderer.mockInput.pressEnter();
    const frame = await testRenderer.waitForFrame(value => value.includes('Chat threads'));
    // The implicit default is named by the client; a created thread shows the
    // backend-owned title beside the agent it was created with.
    expect(frame).toContain('Experiment chat');
    expect(frame).toContain('active');
    expect(frame).toContain('GPU stalls');
    expect(frame).toContain('omnigent/claude');

    testRenderer.mockInput.pressKey('ARROW_DOWN');
    await testRenderer.waitForFrame(value => value.includes('› GPU stalls'));
    testRenderer.mockInput.pressEnter();
    await testRenderer.waitForFrame(() => controller.state.chatThreadPicker === null);

    expect(controller.state.activeChatThreadId).toBe('thread-a');
  });

  it('keeps the chat, the table, and the visualization on screen together', async () => {
    const testRenderer = await createTestRenderer({width: 200, height: 20});
    const controller = logController();
    controller.paneContent = 'Performance · tok_s\n    1135 ┤   ●\nbest r7 1135 tok_s';
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();
    controller.publish({
      ...controller.state,
      chatConversation: [{id: 'a', kind: 'assistant', label: 'Answer', content: 'Prefill.'}],
    });

    await controller.openPane('perf');
    const frame = await frameAfter(testRenderer);

    // Three columns: chat, table, visualization. None replaced another.
    expect(frame).toContain('Experiment chat');
    expect(frame).toContain('H-07');
    expect(frame).toContain('best r7 1135 tok_s');
    expect(frame).toContain('Ctrl+W: switch pane');
  });

  it('gives the row to the table alone when the chat cannot fit beside it', async () => {
    const testRenderer = await createTestRenderer({width: 84, height: 20});
    const controller = logController();
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();

    const landing = await frameAfter(testRenderer);

    // Two columns would both be unreadable here, so the table keeps the row.
    expect(landing).not.toContain('Experiment chat');
    expect(landing).toContain('H-07');
    expect(controller.state.chatDockFits).toBe(false);
  });

  it('keeps the table behind the chat modal instead of the round transcript', async () => {
    const testRenderer = await createTestRenderer({width: 84, height: 20});
    const controller = logController();
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();
    // What a question does where the chat cannot dock.
    controller.publish({...controller.state, chatOpen: true});

    const frame = await frameAfter(testRenderer);

    expect(frame).toContain('Experiment chat');
    expect(frame).toContain('H-07');
    // The per-round chrome belongs to a hypothesis the operator never opened.
    expect(frame).not.toContain('─ Rounds ─');
    expect(frame).not.toContain('─ Agents ─');
  });

  it('moves the pane keys onto the docked chat and back with Ctrl+W', async () => {
    const testRenderer = await createTestRenderer({width: 140, height: 20});
    const controller = logController();
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();
    await frameAfter(testRenderer);

    testRenderer.mockInput.pressKey('w', {ctrl: true});
    const focused = await frameAfter(testRenderer);
    expect(focused).toContain('▸ Experiment chat');
    expect(focused).toContain('Ask about this run');
    expect(controller.state.layout.focus).toBe('chat');

    testRenderer.mockInput.pressKey('w', {ctrl: true});
    await frameAfter(testRenderer);
    expect(controller.state.layout.focus).toBe('left');
  });

  it('leaves the table its own keys while the chat is docked', async () => {
    const testRenderer = await createTestRenderer({width: 140, height: 20});
    const controller = logController();
    controller.experiments = [
      logEntry('H-07', 41, 41, {claim: 'batch the prefill step', resolved_outcome: 'proven'}),
      logEntry('H-08', 42, 42, {claim: 'bigger KV cache block', resolved_outcome: 'disproven'}),
    ];
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();
    await frameAfter(testRenderer);

    // Arrows still belong to the table, docked chat or not.
    testRenderer.mockInput.pressKey('ARROW_DOWN');
    await frameAfter(testRenderer);
    expect(controller.state.experimentLog?.selectedId).toBe('H-08');
  });

  it('renders the transcript and the visualization side by side', async () => {
    const testRenderer = await createTestRenderer({width: 140, height: 20});
    const controller = splitController();
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    await controller.openPane('perf');
    const frame = await frameAfter(testRenderer);

    // Both live at once; neither obscures the other.
    expect(frame).toContain('batched the prefill step');
    expect(frame).toContain('best r7 1135 tok_s');
    expect(frame).toContain('Performance');
    expect(frame).toContain('Ctrl+W: switch pane');
  });

  it('moves focus between panes and shows which one has it', async () => {
    const testRenderer = await createTestRenderer({width: 140, height: 20});
    const controller = splitController();
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openPane('perf');

    const onRight = await frameAfter(testRenderer);
    expect(onRight).toContain('▸ Performance');
    const theme = resolveTheme('dark');
    expect(spanColors(testRenderer, '▸ Performance')?.fg).toBe(theme.borderFocus);

    testRenderer.mockInput.pressKey('w', {ctrl: true});
    const onLeft = await frameAfter(testRenderer);

    expect(controller.state.layout.focus).toBe('left');
    expect(onLeft).toContain('▸ Transcript');
  });

  it('marks exactly one focused pane across the hypothesis layout', async () => {
    const testRenderer = await createTestRenderer({width: 200, height: 20});
    const controller = logController();
    controller.paneContent = 'Performance · tok_s\nbest r7 1135 tok_s';
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();
    await controller.openPane('perf');

    expect(await frameAfter(testRenderer)).toContain('▸ Performance');
    testRenderer.mockInput.pressKey('w', {ctrl: true});
    expect(await frameAfter(testRenderer)).toContain('▸ Experiment chat');
    testRenderer.mockInput.pressKey('w', {ctrl: true});
    expect(await frameAfter(testRenderer)).toContain('▸ Experiments');
    expect(controller.state.layout.focus).toBe('left');
  });

  it('focuses hypothesis panes from their inner content and routes the next key', async () => {
    const testRenderer = await createTestRenderer({width: 200, height: 22});
    const controller = logController();
    controller.experiments = [
      logEntry('H-07', 41, 41, {claim: 'batch the prefill step'}),
      logEntry('H-08', 42, 42, {claim: 'increase the cache block'}),
    ];
    controller.paneContent = 'Performance · tok_s\nbest r7 1135 tok_s';
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();
    await controller.openPane('perf');
    let frame = await frameAfter(testRenderer);
    expect(controller.state.layout.focus).toBe('right');

    // A table row both selects its hypothesis and focuses Experiments.
    let lines = frame.split('\n');
    let row = lines.findIndex(line => line.includes('H-08'));
    let column = (lines[row]?.indexOf('H-08') ?? 0) + 2;
    await testRenderer.mockMouse.click(column, row);
    frame = await frameAfter(testRenderer);
    expect(controller.state.layout.focus).toBe('left');
    expect(controller.state.experimentLog?.selectedId).toBe('H-08');
    expect(frame).toContain('▸ Experiments');
    testRenderer.mockInput.pressKey('ARROW_UP');
    await frameAfter(testRenderer);
    expect(controller.state.experimentLog?.selectedId).toBe('H-07');

    // The chart body is inside a scroll surface. Clicking it focuses the
    // performance pane, so Escape is routed there and closes it.
    frame = testRenderer.captureCharFrame();
    lines = frame.split('\n');
    row = lines.findIndex(line => line.includes('best r7 1135 tok_s'));
    column = (lines[row]?.indexOf('best r7 1135 tok_s') ?? 0) + 2;
    await testRenderer.mockMouse.click(column, row);
    await frameAfter(testRenderer);
    expect(controller.state.layout.focus).toBe('right');
    testRenderer.mockInput.pressKey('ESCAPE');
    await frameAfterEscape(testRenderer);
    expect(controller.state.layout.right).toBeNull();
  });

  it('zooms and restores every pane without replacing its model state', async () => {
    const testRenderer = await createTestRenderer({width: 200, height: 20});
    const controller = logController();
    controller.paneContent = 'Performance · tok_s\nbest r7 1135 tok_s';
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();
    await controller.openPane('perf');
    const right = controller.state.layout.right;

    testRenderer.mockInput.pressKey('F4');
    const performance = await frameAfter(testRenderer);
    expect(performance).toContain('best r7 1135 tok_s');
    expect(performance).not.toContain('H-07');
    expect(performance).not.toContain('Experiment chat');

    testRenderer.mockInput.pressKey('F4');
    const restored = await frameAfter(testRenderer);
    expect(restored).toContain('H-07');
    expect(restored).toContain('Experiment chat');
    expect(controller.state.layout.right).toBe(right);

    testRenderer.mockInput.pressKey('w', {ctrl: true});
    testRenderer.mockInput.pressKey('F4');
    const chat = await frameAfter(testRenderer);
    expect(chat).toContain('Experiment chat');
    expect(chat).not.toContain('H-07');

    testRenderer.mockInput.pressKey('F4');
    testRenderer.mockInput.pressKey('w', {ctrl: true});
    testRenderer.mockInput.pressKey('F4');
    const experiments = await frameAfter(testRenderer);
    expect(experiments).toContain('H-07');
    expect(experiments).not.toContain('Experiment chat');
    expect(experiments).not.toContain('best r7 1135 tok_s');
  });

  it('zooms the selected agents or transcript pane in a round', async () => {
    const testRenderer = await createTestRenderer({width: 140, height: 20});
    const controller = splitController();
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);

    controller.focusRound('agents');
    testRenderer.mockInput.pressKey('F4');
    const agents = await frameAfter(testRenderer);
    expect(agents).toContain('▸ Agents');
    expect(agents).not.toContain('batched the prefill step');

    testRenderer.mockInput.pressKey('F4');
    controller.focusRound('transcript');
    testRenderer.mockInput.pressKey('F4');
    const transcript = await frameAfter(testRenderer);
    expect(transcript).toContain('batched the prefill step');
    expect(transcript).not.toContain('Agents');
  });

  it('closes the pane with Escape and restores the full-width transcript', async () => {
    const testRenderer = await createTestRenderer({width: 140, height: 20});
    const controller = splitController();
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openPane('perf');
    await frameAfter(testRenderer);

    testRenderer.mockInput.pressKey('ESCAPE');
    const frame = await frameAfterEscape(testRenderer);

    expect(controller.state.layout.right).toBeNull();
    expect(frame).not.toContain('best r7 1135 tok_s');
    expect(frame).toContain('batched the prefill step');
    expect(frame).toContain('Agents');
  });

  it('falls back to the modal below the split threshold and recovers on resize', async () => {
    const testRenderer = await createTestRenderer({width: 140, height: 20});
    const controller = splitController();
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openPane('perf');
    const wide = await frameAfter(testRenderer);
    expect(wide).toContain('Ctrl+W: switch pane');

    testRenderer.renderer.resize(80, 20);
    const narrow = await frameAfter(testRenderer);

    // Single view, chart in the modal it used before the split existed.
    expect(narrow).toContain('best r7 1135 tok_s');
    expect(narrow).toContain('Command');
    expect(narrow).not.toContain('Ctrl+W: switch pane');

    testRenderer.renderer.resize(140, 20);
    const recovered = await frameAfter(testRenderer);

    expect(recovered).toContain('Ctrl+W: switch pane');
    expect(recovered).toContain('batched the prefill step');
  });

  it('puts a visualization beside the experiment log rather than replacing it', async () => {
    const testRenderer = await createTestRenderer({width: 140, height: 20});
    const controller = new FakeController(initialSessionState());
    controller.publish({...controller.state, experimentLog: initialSessionState().experimentLog});
    controller.experiments = [
      logEntry('H-07', 41, 41, {claim: 'batch the prefill step', resolved_outcome: 'proven'}),
    ];
    controller.paneContent = 'Performance · tok_s\nbest r41 1135 tok_s';
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();
    await controller.openPane('perf');

    const frame = await frameAfter(testRenderer);

    expect(frame).toContain('H-07');
    expect(frame).toContain('best r41 1135 tok_s');
    // The table gives up its widest column to make room, rather than vanishing.
    expect(frame).toContain('Hypothesis');
  });

  it('styles both panes and the focus indicator from the selected theme', async () => {
    const theme = resolveTheme('solarized-light');
    const testRenderer = await createTestRenderer({width: 140, height: 20});
    const controller = splitController();
    controller.publish({...controller.state, themeName: 'solarized-light'});
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openPane('perf');
    await frameAfter(testRenderer);

    expect(spanColors(testRenderer, '▸ Performance')?.fg).toBe(theme.borderFocus);
    expect(spanColors(testRenderer, 'best r7 1135 tok_s')?.fg).toBe(theme.textPrimary);

    controller.cyclePaneFocus();
    await frameAfter(testRenderer);

    // Focus moved to the transcript, so the pane border drops back to the
    // ordinary border colour and the transcript takes the focus colour.
    expect(spanColors(testRenderer, 'Performance')?.fg).toBe(theme.border);
  });

  it('keeps the pane current while the run advances', async () => {
    const testRenderer = await createTestRenderer({width: 140, height: 20});
    const controller = splitController();
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openPane('perf');
    expect(await frameAfter(testRenderer)).toContain('best r7 1135 tok_s');

    // A later round lands and the controller refetches; the pane redraws in
    // place rather than needing to be reopened.
    controller.paneContent = 'Performance · tok_s\n    1180 ┤    ●\nbest r8 1180 tok_s';
    await controller.openPane('perf');
    const updated = await frameAfter(testRenderer);

    expect(updated).toContain('best r8 1180 tok_s');
    expect(updated).not.toContain('best r7 1135 tok_s');
  });

  it('shows an explicit placeholder for records with no hypothesis id', async () => {
    const testRenderer = await createTestRenderer({width: 120, height: 16});
    const controller = new FakeController(initialSessionState());
    controller.experiments = [
      logEntry('(unidentified)', 1, 1, {identified: false, claim: null, resolved_outcome: null}),
    ];
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();

    const frame = await frameAfter(testRenderer);
    expect(frame).toContain('—');
  });

  it('says so plainly when a run has no hypotheses yet', async () => {
    const testRenderer = await createTestRenderer({width: 100, height: 16});
    const controller = new FakeController(initialSessionState());
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();

    const frame = await frameAfter(testRenderer);
    expect(frame).toContain('No hypotheses have been recorded yet.');
    expect(frame).toContain('once the orchestrator has planned a round');
    // The log is the root view: nothing offers a way out of it.
    expect(frame).not.toContain('Esc');
  });

  it('uses the empty hypotheses screen as a truthful planning kickoff', async () => {
    const testRenderer = await createTestRenderer({width: 100, height: 16});
    const planningStartedAt = new Date(Date.now() - 65_000).toISOString();
    const controller = new FakeController({
      ...initialSessionState(),
      core: {
        ...initialSessionState().core,
        status: 'running',
        phases: [
          {
            kind: 'orchestrator',
            status: 'active',
            roundNumber: 1,
            roundLabel: 'round-1-pre',
            startedAt: planningStartedAt,
          },
        ],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();

    const frame = await frameAfter(testRenderer);
    expect(frame).toContain('Planning Hypothesis 1 · Round 1');
    expect(frame).toContain('Run kickoff');
    expect(frame).toContain('Decide whether profiling is needed');
    expect(frame).toContain('Profile if needed');
    expect(frame).toContain('Form Hypothesis 1');
    expect(frame).toMatch(/1m \d+s/);
    expect(frame).toContain('This activity becomes');
    expect(frame).not.toContain('No hypotheses have been recorded yet.');
  });

  it('keeps earlier unassociated rounds visible and openable during kickoff', async () => {
    const testRenderer = await createTestRenderer({width: 100, height: 18});
    const controller = new FakeController({
      ...initialSessionState(),
      core: {
        ...initialSessionState().core,
        status: 'running',
        rounds: [{number: 1, status: 'completed'}],
        phases: [
          {kind: 'orchestrator', status: 'active', roundNumber: 2, roundLabel: 'round-2-pre'},
        ],
        transcript: [
          {id: 'r1', kind: 'assistant', content: 'earlier unassociated turn', roundNumber: 1},
          {id: 'r2', kind: 'assistant', content: 'planning turn', roundNumber: 2},
        ],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();

    const kickoff = await frameAfter(testRenderer);
    expect(kickoff).toContain('Planning Hypothesis 1');
    expect(kickoff).toContain('Round 2');
    expect(kickoff).toContain('Round 1 · recorded agent turns · no hypothesis');

    testRenderer.mockInput.pressEnter();
    const round = await frameAfter(testRenderer);
    expect(round).toContain('earlier unassociated turn');
    expect(round).not.toContain('planning turn');
  });

  it('indexes and opens recorded rounds that have no hypothesis', async () => {
    const testRenderer = await createTestRenderer({width: 100, height: 18});
    const controller = new FakeController({
      ...initialSessionState(),
      core: {
        ...initialSessionState().core,
        rounds: [{number: 7, status: 'completed'}],
        transcript: [
          {id: 'r6', kind: 'assistant', content: 'other round', roundNumber: 6},
          {id: 'r7', kind: 'assistant', content: 'unindexed turn', roundNumber: 7},
        ],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();

    expect(await frameAfter(testRenderer)).toContain(
      'Round 7 · recorded agent turns · no hypothesis',
    );

    testRenderer.mockInput.pressEnter();
    const detail = await frameAfter(testRenderer);
    expect(detail).toContain('Round 7');
    expect(detail).toContain('unindexed turn');
    expect(detail).not.toContain('other round');

    testRenderer.mockInput.pressKey('ESCAPE');
    expect(await frameAfterEscape(testRenderer)).toContain(
      'Round 7 · recorded agent turns · no hypothesis',
    );
  });

  it('keeps later hypothesis planning below the existing history', async () => {
    const testRenderer = await createTestRenderer({width: 120, height: 18});
    const controller = new FakeController({
      ...initialSessionState(),
      core: {
        ...initialSessionState().core,
        status: 'running',
        phases: [
          {kind: 'orchestrator', status: 'active', roundNumber: 42, roundLabel: 'round-42-plan'},
        ],
      },
    });
    controller.experiments = [logEntry('H-07', 41, 41, {claim: 'batch the prefill step'})];
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();

    const frame = await frameAfter(testRenderer);
    expect(frame).toContain('CURRENT ACTIVITY');
    expect(frame).toContain('Planning Hypothesis 2 · forming it · Round 42');
    expect(frame).toContain('H-07');
    expect(frame.indexOf('H-07')).toBeLessThan(frame.indexOf('Planning Hypothesis 2'));
    expect(frame).not.toContain('UNASSOCIATED ROUNDS');
  });

  it('renders shuffled hypotheses and unassociated rounds in one ascending index', async () => {
    const testRenderer = await createTestRenderer({width: 120, height: 20});
    const controller = new FakeController({
      ...initialSessionState(),
      core: {
        ...initialSessionState().core,
        status: 'running',
        rounds: [
          {number: 4, status: 'completed'},
          {number: 2, status: 'completed'},
          {number: 5, status: 'active'},
        ],
        phases: [
          {kind: 'orchestrator', status: 'active', roundNumber: 5, roundLabel: 'round-5-plan'},
        ],
      },
    });
    controller.experiments = [
      logEntry('H-03', 3, 3, {claim: 'third'}),
      logEntry('H-01', 1, 1, {claim: 'first'}),
    ];
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();

    const frame = await frameAfter(testRenderer);
    const positions = [
      frame.indexOf('H-01'),
      frame.indexOf('Round 2 · recorded'),
      frame.indexOf('H-03'),
      frame.indexOf('Round 4 · recorded'),
      frame.indexOf('Planning Hypothesis 3'),
    ];
    expect(positions.every(position => position >= 0)).toBe(true);
    expect(positions).toEqual([...positions].sort((left, right) => left - right));
  });

  it('labels an explicit profiler phase without claiming it will happen earlier', async () => {
    const testRenderer = await createTestRenderer({width: 100, height: 16});
    const controller = new FakeController({
      ...initialSessionState(),
      core: {
        ...initialSessionState().core,
        status: 'running',
        phases: [
          {kind: 'profiler', status: 'active', roundNumber: 1, roundLabel: 'round-1-profiler'},
        ],
      },
    });
    const app = createOpenTuiApp(testRenderer.renderer, controller);
    registerCleanup(testRenderer.renderer, app);
    await controller.openExperimentLog();

    expect(await frameAfter(testRenderer)).toContain('Profile before Hypothesis 1');
  });
});

/**
 * The experiment log settles synchronously, so there is no later frame to wait
 * for. Flush pending work, then read the single settled frame.
 */
async function frameAfter(testRenderer: TestRendererSetup): Promise<string> {
  await testRenderer.flush();
  return testRenderer.captureCharFrame();
}

/**
 * A bare ESC is held by the stdin parser until its escape-sequence timeout
 * expires, so the key lands a beat after it is pressed.
 */
async function frameAfterEscape(testRenderer: TestRendererSetup): Promise<string> {
  await new Promise(resolve => setTimeout(resolve, 40));
  return frameAfter(testRenderer);
}

/** A client on the landing view, with one hypothesis to show. */
function logController(): FakeController {
  const controller = new FakeController({
    ...initialSessionState(),
    core: {
      ...initialSessionState().core,
      status: 'running',
      rounds: [{number: 41, status: 'completed'}],
    },
  });
  controller.publish({...controller.state, experimentLog: initialSessionState().experimentLog});
  controller.experiments = [
    logEntry('H-07', 41, 41, {
      claim: 'batch the prefill step',
      resolved_outcome: 'proven',
      judge_verdict: 'pass',
      rounds: [{round: 41, passed: true, reviewed: true}],
    }),
  ];
  return controller;
}

function splitController(): FakeController {
  const controller = new FakeController({
    ...initialSessionState(),
    core: {
      ...initialSessionState().core,
      status: 'running',
      rounds: [{number: 7, status: 'active'}],
      transcript: [
        {
          id: 'a',
          kind: 'assistant',
          label: 'implementer · round 7',
          content: 'batched the prefill step',
          roundNumber: 7,
        },
      ],
    },
  });
  controller.paneContent = 'Performance · tok_s\n    1135 ┤   ●\nbest r7 1135 tok_s';
  return controller;
}

function logEntry(
  id: string,
  firstRound: number,
  lastRound: number,
  overrides: Partial<HypothesisEntry> = {},
): HypothesisEntry {
  return {
    hypothesis_id: id,
    identified: true,
    first_round: firstRound,
    last_round: lastRound,
    rounds: [],
    kept: false,
    active: false,
    ...overrides,
  };
}

function spanColors(
  testRenderer: TestRendererSetup,
  needle: string,
): {fg: string; bg: string} | undefined {
  for (const line of testRenderer.captureSpans().lines) {
    for (const span of line.spans) {
      if (span.text.includes(needle)) {
        return {fg: rgbToHex(span.fg).toLowerCase(), bg: rgbToHex(span.bg).toLowerCase()};
      }
    }
  }
  return undefined;
}

function registerCleanup(
  renderer: Awaited<ReturnType<typeof createTestRenderer>>['renderer'],
  app: OpenTuiApp,
): void {
  cleanup.push(() => {
    app.destroy();
    renderer.destroy();
  });
}

function clipboardReturning(
  result: ClipboardCopyResult,
): SelectionClipboard & {readonly calls: number} {
  let calls = 0;
  return {
    get calls(): number {
      return calls;
    },
    copySelection(): ClipboardCopyResult {
      calls += 1;
      return result;
    },
  };
}

class FakeController implements SessionController {
  readonly #listeners = new Set<(state: SessionState) => void>();
  readonly submissions: string[] = [];
  readonly chatSubmissions: string[] = [];
  liveCalls = 0;

  /**
   * Tests that exercise the transcript start past the landing view. The
   * experiment log has its own tests that open it explicitly.
   */
  constructor(state: SessionState) {
    this.state = {...state, experimentLog: null};
  }

  state: SessionState;

  publish(state: SessionState): void {
    // The real controller normalizes focus on every state change; the fake has
    // to as well, or tests pass on a focus the client could never be in.
    this.state = normalizeFocus(state);
    for (const listener of this.#listeners) listener(this.state);
  }

  start(): Promise<void> {
    return Promise.resolve();
  }
  stop(): Promise<void> {
    return Promise.resolve();
  }
  submitCommand(value: string): Promise<void> {
    if (!value.trim().startsWith('/')) {
      this.publish(
        reportError(this.state, 'Commands start with /. Use Experiment chat for questions.', {
          scope: 'input',
        }),
      );
      return Promise.resolve();
    }
    this.submissions.push(value);
    if (value.trim() === '/chat') {
      this.state = {...this.state, chatOpen: true, overlay: null};
      this.#notify();
    }
    if (value.trim() === '/theme') this.openThemePicker();
    if (value.trim() === '/new-chat') this.openNewChatPicker();
    if (value.trim() === '/chats') this.openChatThreadPicker();
    return Promise.resolve();
  }
  closeChat(): void {
    this.state = {...this.state, chatOpen: false};
    this.#notify();
  }
  switchChatThread(threadId: string): void {
    this.publish(switchChatThread(this.state, threadId));
  }
  openChatThreadPicker(): void {
    this.publish(openChatThreadPicker(this.state));
  }
  moveChatThreadSelection(delta: number): void {
    this.publish(moveChatThreadSelection(this.state, delta));
  }
  applySelectedChatThread(): void {
    const picker = this.state.chatThreadPicker;
    if (picker !== null) this.switchChatThread(picker.selected);
  }
  closeChatThreadPicker(): void {
    this.publish(closeChatThreadPicker(this.state));
  }
  openNewChatPicker(): void {
    this.publish(openNewChatPicker(this.state));
  }
  moveNewChatSelection(delta: number): void {
    this.publish(moveNewChatSelection(this.state, delta));
  }
  confirmNewChatPicker(): Promise<void> {
    const picker = this.state.newChatPicker;
    if (picker === null) return Promise.resolve();
    if (picker.step !== 'model') this.publish(advanceNewChatPicker(this.state));
    else this.publish({...this.state, newChatPicker: null});
    return Promise.resolve();
  }
  retreatNewChatPicker(): void {
    this.publish(retreatNewChatPicker(this.state));
  }
  typeNewChatModel(text: string): void {
    const picker = this.state.newChatPicker;
    if (picker !== null) this.publish(setNewChatModel(this.state, picker.model + text));
  }
  backspaceNewChatModel(): void {
    const picker = this.state.newChatPicker;
    if (picker !== null) this.publish(setNewChatModel(this.state, picker.model.slice(0, -1)));
  }
  setTheme(themeName: ThemeName): void {
    this.state = {...this.state, themeName};
    this.#notify();
  }
  openThemePicker(): void {
    this.publish({...this.state, overlay: null, themePicker: {selected: this.state.themeName}});
  }
  moveThemeSelection(delta: number): void {
    this.publish(moveThemeSelection(this.state, delta));
  }
  applySelectedTheme(): void {
    const picker = this.state.themePicker;
    if (picker !== null) this.publish(setTheme(this.state, picker.selected));
  }
  closeThemePicker(): void {
    this.publish(closeThemePicker(this.state));
  }
  submitChat(value: string): Promise<void> {
    const text = value.trim();
    if (!text.startsWith('/')) return this.sendChat(value);
    return this.submitCommand(text);
  }

  sendChat(value: string): Promise<void> {
    this.chatSubmissions.push(value);
    this.state = {
      ...this.state,
      chatConversation: [
        ...this.state.chatConversation,
        {id: 'chat-user', kind: 'user', label: 'You', content: value},
        {
          id: 'chat-analysis',
          kind: 'analysis',
          label: 'Chat analysis',
          content: 'Inspecting configuration events',
        },
        {
          id: 'chat-tool',
          kind: 'tool',
          label: 'Chat tool',
          content: '→ Read(run-events.jsonl)\nFound config_load_failed',
          toolCall: '→ Read(run-events.jsonl)\n',
          toolResponse: 'Found config_load_failed',
        },
        {
          id: 'chat-answer',
          kind: 'assistant',
          label: 'Answer',
          content: 'Recorded diagnostic: agent.toml was not found.',
        },
      ],
    };
    this.#notify();
    return Promise.resolve();
  }
  live(): void {
    this.liveCalls += 1;
    this.state = {...this.state, overlay: null, selectedRound: null, selectedAgentKind: null};
    for (const listener of this.#listeners) listener(this.state);
  }
  selectNextAgent(): void {
    const current = this.state.selectedAgentKind;
    const visibleRound =
      this.state.selectedRound ??
      this.state.core.rounds.find(round => round.status === 'active')?.number ??
      null;
    const phases = this.state.core.phases.filter(phase => phase.roundNumber === visibleRound);
    const index = current === null ? -1 : phases.findIndex(phase => phase.kind === current);
    const next = phases[(index + 1 + phases.length) % phases.length];
    this.state = {...this.state, selectedAgentKind: next?.kind ?? null, overlay: null};
    for (const listener of this.#listeners) listener(this.state);
  }
  selectPreviousAgent(): void {
    this.selectNextAgent();
  }
  selectNextRound(): void {
    this.publish(selectNextRound(this.state));
  }
  selectPreviousRound(): void {
    this.publish(selectPreviousRound(this.state));
  }
  selectAgent(kind: string): void {
    this.publish(selectAgent(this.state, kind));
  }
  selectNextEntry(delta: number, id?: string): void {
    this.publish(selectNextEntry(this.state, delta, id));
  }
  clearEntrySelection(): void {
    this.publish(clearEntrySelection(this.state));
  }
  clearAgentSelection(): void {
    this.publish(clearAgentSelection(this.state));
  }
  focusRound(focus: RoundFocus): void {
    this.publish(focusRound(this.state, focus));
  }
  selectNextTodo(delta: number): void {
    this.publish(selectNextTodo(this.state, delta));
  }
  selectRound(roundNumber: number): void {
    this.state = {...this.state, selectedRound: roundNumber, selectedAgentKind: null};
    for (const listener of this.#listeners) listener(this.state);
  }
  #promptToggle: (() => void) | null = null;
  onTogglePrompt(handler: () => void): void {
    this.#promptToggle = handler;
  }
  togglePrompt(): void {
    this.#promptToggle?.();
  }
  toggleTodos(): void {
    this.state = {...this.state, todosExpanded: !this.state.todosExpanded};
    for (const listener of this.#listeners) listener(this.state);
  }

  /** Rows the fake server returns for query.experiments. */
  experiments: HypothesisEntry[] = [];

  openExperimentLog(): Promise<void> {
    this.publish(setExperiments(openExperimentLog(this.state), this.experiments));
    return Promise.resolve();
  }
  moveExperimentSelection(delta: number): void {
    this.publish(moveExperimentSelection(this.state, delta));
  }
  selectExperimentActivity(): void {
    this.publish(selectExperimentActivity(this.state));
  }
  enterExperimentDrilldown(): void {
    this.publish(enterExperimentDrilldown(this.state));
  }
  openPane(view: PaneView): Promise<void> {
    this.publish(setPaneContent(openPane(this.state, view), view, this.paneContent));
    return Promise.resolve();
  }
  closePane(): void {
    this.publish(closePane(this.state));
  }
  closeOverlays(): void {
    this.publish(closeOverlays(this.state));
  }
  dismissErrorBanner(): void {
    this.publish(dismissErrorBanner(this.state));
  }
  cyclePaneFocus(): void {
    this.publish(cyclePaneFocus(this.state));
  }
  focusPane(focus: PaneFocus): void {
    this.publish(focusPane(this.state, focus));
  }
  togglePaneZoom(): void {
    this.publish(togglePaneZoom(this.state));
  }
  setChatDockFits(fits: boolean): void {
    this.publish(setChatDockFits(this.state, fits));
  }

  /** Content the fake server returns for whichever visualization is opened. */
  paneContent = 'Performance · median_tok_per_sec\n  1200 ┤●';

  openRound(roundNumber?: number): void {
    if (roundNumber === undefined) {
      this.enterExperimentDrilldown();
      return;
    }
    const scoped = enterExperimentRound(this.state, roundNumber);
    this.publish(scoped ?? enterUnownedExperimentRound(this.state, roundNumber) ?? this.state);
  }
  leaveExperimentDrilldown(): void {
    this.publish(leaveExperimentDrilldown(this.state));
  }

  subscribe(listener: (state: SessionState) => void): () => void {
    this.#listeners.add(listener);
    listener(this.state);
    return () => this.#listeners.delete(listener);
  }

  #notify(): void {
    for (const listener of this.#listeners) listener(this.state);
  }
}
