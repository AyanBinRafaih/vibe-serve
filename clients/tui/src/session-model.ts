import type {Diagnostic, HypothesisEntry, RunEvent, RunSnapshot} from '@vibesys/backend-client';
import {
  type ActiveAgentExecution,
  type ActiveExecutionCheckpoint,
  type AgentPhase,
  type CoreDiagnostic,
  type CoreState,
  type ExecutionTodos,
  initialCoreState,
  latestDiagnosticChange,
  phasesForRound,
  type RoundSummary,
  reconcileActiveExecutions,
  reduceEvent,
  reduceSnapshot,
  type TodoItem,
  type TranscriptEntry,
} from '@vibesys/core-state';
import {DEFAULT_THEME_NAME, THEME_NAMES, type ThemeName} from './ui/theme.js';

export interface SessionState {
  /** Pure projection of backend snapshots, events, and execution checkpoints. */
  readonly core: CoreState;
  /** False after the frontend loses a trustworthy backend event stream. */
  eventStreamAvailable: boolean;
  selectedRound: number | null;
  selectedAgentKind: string | null;
  /** Transcript entry the arrow keys are on, so a trace is readable without a mouse. */
  selectedEntryId: string | null;
  /** Todo row the arrow keys are on while the todo box holds focus. */
  selectedTodoIndex: number | null;
  /**
   * Which half of the round view the arrow keys act on. The round view is two
   * panes side by side, and both are navigable, so the keys have to belong to
   * one of them at a time.
   */
  roundFocus: RoundFocus;
  overlay: OverlayPanel | null;
  chatOpen: boolean;
  chatConversation: ConversationEntry[];
  chatPending: boolean;
  todosExpanded: boolean;
  themeName: ThemeName;
  experimentLog: ExperimentLogState | null;
  hypothesisScope: HypothesisScope | null;
  layout: LayoutState;
  /**
   * Whether the terminal is wide enough to carry the docked chat beside the
   * log. Measured by the renderer and reported in, because where a question's
   * answer is displayed has to agree with what is actually on screen: too
   * narrow to dock and the chat is the modal it has always been.
   */
  chatDockFits: boolean;
  /** Non-null while the theme list is open as a keyboard selection. */
  themePicker: ThemePicker | null;
  /** Root-level error state, independent of the active transcript or log view. */
  errorBanner: ErrorBannerState | null;
}

export type ErrorSeverity = 'recoverable' | 'fatal';
export type ErrorScope =
  | 'configuration'
  | 'invocation'
  | 'phase'
  | 'run'
  | 'protocol'
  | 'request'
  | 'transport'
  | 'input';

export interface ErrorBannerState {
  title: string;
  /** Human-facing summary, shown before structured detail and hint. */
  message: string;
  detail: string | null;
  hint: string | null;
  diagnosticId: string | null;
  severity: ErrorSeverity;
  scope: ErrorScope;
  agentKind: string | null;
  roundLabel: string | null;
  invocationId: string | null;
  /** Equivalent reports are folded into one banner. */
  count: number;
}

/** The agent graph on the left, or the transcript on the right. */
export type RoundFocus = 'agents' | 'transcript';

/**
 * The experiment log is open when this is non-null. Selection is held as a
 * hypothesis id rather than a row index so a refresh that inserts rows keeps
 * the operator on the same hypothesis.
 */
export interface ExperimentLogState {
  entries: HypothesisEntry[];
  selectedId: string | null;
  /** The transient planning activity is selected instead of a recorded claim. */
  selectedActivity?: boolean;
  /** Round anchor retained while selected planning becomes a persisted hypothesis. */
  selectedActivityRound?: number | null;
  /** A recorded round that has no hypothesis entry is selected. */
  selectedUnownedRound?: number | null;
  pending: boolean;
  error: string | null;
}

/** A planning phase that has not produced a hypothesis record yet. */
export interface HypothesisPlanningActivity {
  stage: 'pre' | 'profile' | 'plan';
  roundNumber: number;
  startedAt?: string;
}

/** Every selectable item in the hypotheses-first index. */
export type ExperimentIndexItem =
  | {kind: 'activity'; key: 'activity'; activity: HypothesisPlanningActivity}
  | {kind: 'hypothesis'; key: string; entry: HypothesisEntry}
  | {kind: 'round'; key: `round-${number}`; roundNumber: number};

/**
 * The hypothesis whose rounds the operator opened. While this is set the
 * client shows the ordinary per-round trajectory, filtered to these rounds,
 * and the log table steps aside without losing its selection.
 */
export interface HypothesisScope {
  id: string;
  label: string;
  rounds: number[];
  /** A single recorded round not yet associated with a hypothesis. */
  source?: 'hypothesis' | 'round';
}

/**
 * A visualization command's output, rendered beside the transcript rather than
 * over it. Content is pre-rendered text so the pane stays agnostic about which
 * command produced it and a new command needs no new layout code.
 */
export type PaneView = 'perf';

export interface RightPane {
  view: PaneView;
  title: string;
  content: string;
  pending: boolean;
  error: string | null;
}

/**
 * Which column the pane keys act on. ``left`` is whatever holds the middle of
 * the row, the experiment log or the transcript; ``chat`` and ``right`` are the
 * panes beside it.
 */
export type PaneFocus = 'chat' | 'left' | 'right';

export interface LayoutState {
  /** null means no visualization pane: the left side has the rest of the row. */
  right: RightPane | null;
  focus: PaneFocus;
  /** The pane temporarily occupying the full content row, or null for the split layout. */
  zoomedPane: PaneId | null;
}

/** Semantic pane identities, independent of their current screen position. */
export type PaneId = 'agents' | 'chat' | 'experiments' | 'performance' | 'transcript';

export interface ThemePicker {
  selected: ThemeName;
}

export interface OverlayPanel {
  kind: 'detail' | 'help' | 'error';
  content: string;
}

export interface ConversationEntry {
  id: string;
  kind:
    | 'assistant'
    | 'user'
    | 'prompt'
    | 'analysis'
    | 'tool'
    | 'diagnostic'
    | 'subprocess'
    | 'status'
    | 'result';
  content: string;
  label?: string;
  tone?: 'normal' | 'success' | 'failure';
  agentKind?: string;
  roundLabel?: string;
  roundNumber?: number;
  turnId?: string;
  invocationId?: string;
  startsTurn?: boolean;
  toolCall?: string;
  toolResponse?: string;
  toolName?: string;
  toolCallId?: string;
  toolArguments?: Record<string, unknown>;
  toolResult?: TranscriptEntry['toolResult'];
}

export function initialSessionState(themeName: ThemeName = DEFAULT_THEME_NAME): SessionState {
  return {
    core: initialCoreState(),
    eventStreamAvailable: true,
    selectedRound: null,
    selectedAgentKind: null,
    selectedEntryId: null,
    selectedTodoIndex: null,
    roundFocus: 'transcript',
    overlay: null,
    chatOpen: false,
    chatConversation: [],
    chatPending: false,
    todosExpanded: false,
    themeName,
    // The experiment log is the landing view: a run's history reads as a short
    // list of claims before it reads as a long list of rounds.
    experimentLog: {entries: [], selectedId: null, pending: true, error: null},
    hypothesisScope: null,
    layout: {right: null, focus: 'left', zoomedPane: null},
    // Docked until the renderer measures otherwise, so the landing view carries
    // the chat from the first frame rather than after a resize.
    chatDockFits: true,
    themePicker: null,
    errorBanner: null,
  };
}

/**
 * Rows are keyed by hypothesis identity rather than response position, so
 * selection survives refreshes even when the backend returns a different
 * order. The canonical index sorts the rows before presenting them.
 */
export function entryKey(entry: HypothesisEntry, index: number): string {
  return entry.identified === false
    ? `${entry.hypothesis_id}#${entry.first_round}`
    : entry.hypothesis_id || `#${index}`;
}

/**
 * True when the table itself is on screen rather than a hypothesis trajectory.
 * The chat does not enter into it: docked it is part of this view, and as a
 * modal it floats over it, so asking a question never swaps the table for the
 * per-round transcript underneath.
 */
export function experimentLogVisible(state: SessionState): boolean {
  return state.experimentLog !== null && state.hypothesisScope === null;
}

/**
 * The chat is docked on the landing view: it is part of that view rather than a
 * dialog over it, so a question never hides the table it is about. Inside a
 * hypothesis, and in a terminal too narrow for two columns, it stays the modal
 * it was.
 */
export function chatDocked(state: SessionState): boolean {
  return state.chatDockFits && state.experimentLog !== null && state.hypothesisScope === null;
}

/** True when the docked chat is the thing on screen rather than the modal. */
export function chatPaneVisible(state: SessionState): boolean {
  return chatDocked(state) && !state.chatOpen;
}

export function chatPaneFocused(state: SessionState): boolean {
  return chatPaneVisible(state) && state.layout.focus === 'chat';
}

export function rightPaneFocused(state: SessionState): boolean {
  return state.layout.right !== null && state.layout.focus === 'right';
}

export function setChatDockFits(state: SessionState, fits: boolean): SessionState {
  if (state.chatDockFits === fits) return state;
  const layout =
    fits || state.layout.focus !== 'chat'
      ? state.layout
      : {...state.layout, focus: 'left' as const};
  return {...state, chatDockFits: fits, layout};
}

/**
 * What ``/chat`` does. Docked, the chat is already on screen, so opening it
 * means putting the pane keys on it rather than covering the table.
 */
export function openChat(state: SessionState): SessionState {
  if (chatDocked(state)) {
    return {...state, overlay: null, layout: {...state.layout, focus: 'chat'}};
  }
  return {...state, overlay: null, chatOpen: true};
}

export function openExperimentLog(state: SessionState): SessionState {
  const existing = state.experimentLog;
  return {
    ...state,
    overlay: null,
    chatOpen: false,
    hypothesisScope: null,
    selectedRound: null,
    selectedAgentKind: null,
    experimentLog: existing ?? {entries: [], selectedId: null, pending: true, error: null},
  };
}

export function setExperiments(state: SessionState, entries: HypothesisEntry[]): SessionState {
  const log = state.experimentLog;
  if (log === null) return state;
  const activity = hypothesisPlanningActivity(state);
  const selectedActivityRound =
    log.selectedActivity === true
      ? (log.selectedActivityRound ?? activity?.roundNumber ?? null)
      : null;
  const orderedEntries = [...entries].sort(compareHypothesisEntries);
  const keys = orderedEntries.map(entryKey);
  const materializedActivity =
    selectedActivityRound === null
      ? undefined
      : orderedEntries.find(entry => scopeRounds(entry).includes(selectedActivityRound));
  // Keep the operator's row when it still exists; otherwise fall back to the
  // active hypothesis, then to the first row.
  const selectedId =
    materializedActivity !== undefined
      ? entryKeyFor(orderedEntries, materializedActivity)
      : log.selectedId !== null && keys.includes(log.selectedId)
        ? log.selectedId
        : (keys[orderedEntries.findIndex(entry => entry.active === true)] ?? keys[0] ?? null);
  const unownedRounds = unownedExperimentRounds(state, orderedEntries);
  const currentUnownedRound = log.selectedUnownedRound;
  const selectedUnownedRound =
    currentUnownedRound !== undefined &&
    currentUnownedRound !== null &&
    unownedRounds.includes(currentUnownedRound)
      ? currentUnownedRound
      : orderedEntries.length === 0
        ? (unownedRounds[0] ?? null)
        : null;
  return {
    ...state,
    experimentLog: {
      ...log,
      entries: orderedEntries,
      selectedId,
      selectedActivity: materializedActivity === undefined && log.selectedActivity === true,
      selectedActivityRound: materializedActivity === undefined ? selectedActivityRound : null,
      selectedUnownedRound,
      pending: false,
      error: null,
    },
  };
}

export function failExperiments(state: SessionState, error: string): SessionState {
  const log = state.experimentLog;
  if (log === null) return state;
  return {...state, experimentLog: {...log, pending: false, error}};
}

export function moveExperimentSelection(state: SessionState, delta: number): SessionState {
  const log = state.experimentLog;
  if (log === null || state.hypothesisScope !== null) return state;
  const items = experimentIndexItems(state);
  if (items.length === 0) return state;
  const selected = selectedExperimentIndexItem(state);
  const current = selected === null ? -1 : items.findIndex(item => item.key === selected.key);
  const index = current === -1 ? 0 : Math.min(items.length - 1, Math.max(0, current + delta));
  return selectExperimentIndexItem(state, items[index]);
}

/** Selects the current planning work so Enter opens its live agent turns. */
export function selectExperimentActivity(state: SessionState): SessionState {
  const log = state.experimentLog;
  if (
    log === null ||
    state.hypothesisScope !== null ||
    hypothesisPlanningActivity(state) === null
  ) {
    return state;
  }
  const activity = hypothesisPlanningActivity(state);
  return {
    ...state,
    experimentLog: {
      ...log,
      selectedActivity: true,
      selectedActivityRound: activity?.roundNumber ?? null,
      selectedUnownedRound: null,
    },
  };
}

/**
 * Opens the rounds behind the selected hypothesis. The log keeps its selection
 * so leaving the trajectory lands the operator back on the same row.
 */
export function enterExperimentDrilldown(state: SessionState): SessionState {
  const activity = hypothesisPlanningActivity(state);
  if (
    activity !== null &&
    (state.experimentLog?.selectedActivity === true ||
      (state.experimentLog?.entries.length === 0 &&
        (state.experimentLog?.selectedUnownedRound ?? null) === null))
  ) {
    return enterUnownedExperimentRound(state, activity.roundNumber) ?? state;
  }
  const selectedRound =
    state.experimentLog?.selectedUnownedRound ??
    (selectedExperiment(state) === null ? unownedExperimentRounds(state)[0] : undefined);
  if (selectedRound !== undefined && selectedRound !== null) {
    return enterUnownedExperimentRound(state, selectedRound) ?? state;
  }
  const entry = selectedExperiment(state);
  if (entry === null || state.hypothesisScope !== null) return state;
  const rounds = scopeRounds(entry);
  if (rounds.length === 0) return state;
  return {
    ...state,
    overlay: null,
    // A visualization opened from the log belongs to the log. Carrying it into
    // the round view would leave the operator reading one view's answer beside
    // another view's transcript.
    layout: {right: null, focus: 'left', zoomedPane: null},
    hypothesisScope: {
      id: entry.hypothesis_id,
      label: hypothesisLabel(entry),
      rounds,
      source: 'hypothesis',
    },
    // Land on the hypothesis's latest round rather than on "no round". The
    // round view is built around one round: the strip marks it, the agent graph
    // draws its phases, the transcript carries its turns. Leaving the round
    // unset drew an empty strip and an empty graph, and `[` walks back through
    // the earlier rounds from here anyway.
    selectedRound: rounds.at(-1) ?? null,
    selectedAgentKind: null,
    selectedEntryId: null,
  };
}

/** Leaves the trajectory and returns to the table with the selection intact. */
export function leaveExperimentDrilldown(state: SessionState): SessionState {
  if (state.hypothesisScope === null) return state;
  return {...state, hypothesisScope: null, selectedRound: null, selectedAgentKind: null};
}

/**
 * Opens the hypothesis that owns a round number, landing on that round rather
 * than on the whole trajectory. Returns null when no hypothesis claims it, so
 * the caller can report the round rather than silently doing nothing.
 */
export function enterExperimentRound(
  state: SessionState,
  roundNumber: number,
): SessionState | null {
  const entry = (state.experimentLog?.entries ?? []).find(candidate =>
    scopeRounds(candidate).includes(roundNumber),
  );
  if (entry === undefined) return null;
  const scoped: SessionState = {
    ...state,
    overlay: null,
    chatOpen: false,
    layout: {right: null, focus: 'left', zoomedPane: null},
    hypothesisScope: {
      id: entry.hypothesis_id,
      label: hypothesisLabel(entry),
      rounds: scopeRounds(entry),
      source: 'hypothesis',
    },
    selectedRound: roundNumber,
    selectedAgentKind: null,
    selectedEntryId: null,
    experimentLog:
      state.experimentLog === null
        ? null
        : {...state.experimentLog, selectedId: entryKeyFor(state.experimentLog.entries, entry)},
  };
  return scoped;
}

/**
 * Opens a recorded round that is not currently indexed by a hypothesis. This
 * is intentionally derived only from observed rounds, never from the planned
 * round count, so an empty future round is not presented as historical work.
 */
export function enterUnownedExperimentRound(
  state: SessionState,
  roundNumber: number,
): SessionState | null {
  if (!state.core.rounds.some(round => round.number === roundNumber)) return null;
  return {
    ...state,
    overlay: null,
    chatOpen: false,
    layout: {right: null, focus: 'left', zoomedPane: null},
    hypothesisScope: {
      id: `round-${roundNumber}`,
      label: `Round ${roundNumber}`,
      rounds: [roundNumber],
      source: 'round',
    },
    selectedRound: roundNumber,
    selectedAgentKind: null,
    selectedEntryId: null,
  };
}

function entryKeyFor(entries: HypothesisEntry[], entry: HypothesisEntry): string | null {
  const index = entries.indexOf(entry);
  return index === -1 ? null : entryKey(entry, index);
}

function scopeRounds(entry: HypothesisEntry): number[] {
  const listed = (entry.rounds ?? []).map(round => round.round);
  if (listed.length > 0) return [...listed].sort((a, b) => a - b);
  // A record can summarize a continuation before its per-round outcomes have
  // been persisted. Its declared range is still the server's ownership claim.
  if (entry.first_round <= 0 || entry.last_round < entry.first_round) return [];
  return Array.from(
    {length: Math.max(0, entry.last_round - entry.first_round + 1)},
    (_, index) => entry.first_round + index,
  );
}

function hypothesisLabel(entry: HypothesisEntry): string {
  const range =
    entry.first_round === entry.last_round
      ? `r${entry.first_round}`
      : `r${entry.first_round}-${entry.last_round}`;
  return `${entry.hypothesis_id} · ${range}`;
}

export function selectedExperiment(state: SessionState): HypothesisEntry | null {
  const log = state.experimentLog;
  if (log === null || log.selectedId === null) return null;
  const index = log.entries.map(entryKey).indexOf(log.selectedId);
  return index === -1 ? null : (log.entries[index] ?? null);
}

/**
 * Observed numeric rounds not represented by any hypothesis record. Events
 * without a numeric round label remain in the run transcript but cannot be
 * honestly assigned an index row.
 */
export function unownedExperimentRounds(
  state: SessionState,
  entries: HypothesisEntry[] = state.experimentLog?.entries ?? [],
): number[] {
  const owned = new Set(entries.flatMap(scopeRounds));
  const planningRound = hypothesisPlanningActivity(state)?.roundNumber;
  return state.core.rounds
    .filter(
      round =>
        !owned.has(round.number) && round.number !== planningRound && round.status !== 'planned',
    )
    .map(round => round.number)
    .sort((left, right) => left - right);
}

/**
 * The canonical visual index. Historical work is chronological regardless of
 * server response order, and transient planning is always the newest item.
 */
export function experimentIndexItems(state: SessionState): ExperimentIndexItem[] {
  const history: ExperimentIndexItem[] = [];
  for (const [index, entry] of (state.experimentLog?.entries ?? []).entries()) {
    history.push({kind: 'hypothesis', key: entryKey(entry, index), entry});
  }
  for (const roundNumber of unownedExperimentRounds(state)) {
    history.push({kind: 'round', key: `round-${roundNumber}`, roundNumber});
  }
  history.sort((left, right) => experimentItemRound(left) - experimentItemRound(right));
  const activity = hypothesisPlanningActivity(state);
  return activity === null ? history : [...history, {kind: 'activity', key: 'activity', activity}];
}

function experimentItemRound(item: ExperimentIndexItem): number {
  if (item.kind === 'hypothesis') return item.entry.first_round;
  if (item.kind === 'round') return item.roundNumber;
  return item.activity.roundNumber;
}

function compareHypothesisEntries(left: HypothesisEntry, right: HypothesisEntry): number {
  return (
    left.first_round - right.first_round ||
    left.last_round - right.last_round ||
    left.hypothesis_id.localeCompare(right.hypothesis_id)
  );
}

export function selectedExperimentIndexItem(state: SessionState): ExperimentIndexItem | null {
  const log = state.experimentLog;
  if (log === null) return null;
  const items = experimentIndexItems(state);
  if (log.selectedActivity === true) {
    const activity = items.find(item => item.kind === 'activity');
    if (activity !== undefined) return activity;
  }
  if (log.selectedUnownedRound !== undefined && log.selectedUnownedRound !== null) {
    return (
      items.find(item => item.kind === 'round' && item.roundNumber === log.selectedUnownedRound) ??
      null
    );
  }
  return log.selectedId === null
    ? null
    : (items.find(item => item.kind === 'hypothesis' && item.key === log.selectedId) ?? null);
}

function selectExperimentIndexItem(
  state: SessionState,
  item: ExperimentIndexItem | undefined,
): SessionState {
  const log = state.experimentLog;
  if (log === null || item === undefined) return state;
  if (item.kind === 'activity')
    return {
      ...state,
      experimentLog: {
        ...log,
        selectedActivity: true,
        selectedActivityRound: item.activity.roundNumber,
        selectedUnownedRound: null,
      },
    };
  if (item.kind === 'round') {
    return {
      ...state,
      experimentLog: {
        ...log,
        selectedActivity: false,
        selectedActivityRound: null,
        selectedUnownedRound: item.roundNumber,
      },
    };
  }
  return {
    ...state,
    experimentLog: {
      ...log,
      selectedId: item.key,
      selectedActivity: false,
      selectedActivityRound: null,
      selectedUnownedRound: null,
    },
  };
}

/**
 * The loop publishes these labels before it writes a hypothesis record. Keep
 * this derived from active, named phases: an old phase must never make the
 * experiment log look live, and unrelated profiler work must not be presented
 * as hypothesis planning.
 */
export function hypothesisPlanningActivity(state: SessionState): HypothesisPlanningActivity | null {
  if (state.core.terminal || state.experimentLog === null) return null;
  const phase = [...state.core.phases]
    .reverse()
    .find(
      candidate =>
        candidate.status === 'active' && planningStage(candidate.kind, candidate.roundLabel),
    );
  if (phase === undefined || phase.roundNumber === null) return null;
  const roundNumber = phase.roundNumber;
  if (state.experimentLog.entries.some(entry => scopeRounds(entry).includes(roundNumber)))
    return null;
  const stage = planningStage(phase.kind, phase.roundLabel);
  if (stage === null) return null;
  const startedAt = earliestPlanningStartedAt(state.core.phases, roundNumber);
  return {
    stage,
    roundNumber,
    ...(startedAt === undefined ? {} : {startedAt}),
  };
}

function earliestPlanningStartedAt(phases: AgentPhase[], roundNumber: number): string | undefined {
  const starts = phases
    .filter(
      phase => phase.roundNumber === roundNumber && planningStage(phase.kind, phase.roundLabel),
    )
    .flatMap(phase =>
      phase.startedAt === undefined
        ? []
        : [[phase.startedAt, Date.parse(phase.startedAt)] as const],
    )
    .filter(([, timestamp]) => Number.isFinite(timestamp));
  if (starts.length === 0) return undefined;
  return starts.reduce((earliest, candidate) =>
    candidate[1] < earliest[1] ? candidate : earliest,
  )[0];
}

function planningStage(
  agentKind: string,
  roundLabel: string | null,
): HypothesisPlanningActivity['stage'] | null {
  if (roundLabel === null) return null;
  if (agentKind === 'orchestrator' && /^round-\d+-pre$/.test(roundLabel)) return 'pre';
  if (agentKind === 'profiler' && /^round-\d+-profiler$/.test(roundLabel)) return 'profile';
  if (agentKind === 'orchestrator' && /^round-\d+-plan$/.test(roundLabel)) return 'plan';
  return null;
}

export const PANE_TITLES: Record<PaneView, string> = {
  perf: 'Performance',
};

/**
 * Opens or retargets the right pane. A second visualization command replaces
 * the pane's contents rather than stacking, which is why the view is set here
 * rather than pushed onto anything.
 */
export function openPane(state: SessionState, view: PaneView): SessionState {
  const existing = state.layout.right;
  return {
    ...state,
    overlay: null,
    layout: {
      right: {
        view,
        title: PANE_TITLES[view],
        // Keep the old content while the new query is in flight only when the
        // pane is not changing view, so the pane never shows one command's
        // output under another command's title.
        content: existing !== null && existing.view === view ? existing.content : '',
        pending: true,
        error: null,
      },
      focus: 'right',
      zoomedPane: state.layout.zoomedPane,
    },
  };
}

export function setPaneContent(state: SessionState, view: PaneView, content: string): SessionState {
  const right = state.layout.right;
  // A slower response for a pane the operator has since replaced or closed
  // must not overwrite what is on screen now.
  if (right === null || right.view !== view) return state;
  return {
    ...state,
    layout: {...state.layout, right: {...right, content, pending: false, error: null}},
  };
}

export function failPane(state: SessionState, view: PaneView, error: string): SessionState {
  const right = state.layout.right;
  if (right === null || right.view !== view) return state;
  return {...state, layout: {...state.layout, right: {...right, pending: false, error}}};
}

export function closePane(state: SessionState): SessionState {
  if (state.layout.right === null) return state;
  return {...state, layout: {right: null, focus: 'left', zoomedPane: null}};
}

/**
 * Moves the pane keys one column to the right, wrapping. Only the columns
 * actually on screen take part, so the operator never lands on a pane they
 * cannot see.
 */
export function cyclePaneFocus(state: SessionState): SessionState {
  if (state.layout.zoomedPane !== null) return state;
  const order = visiblePaneOrder(state);
  if (order.length < 2) return state;
  const next = order[(order.indexOf(state.layout.focus) + 1) % order.length] ?? 'left';
  return {...state, layout: {...state.layout, focus: next}};
}

/**
 * Focus has to name a column that is actually on screen. A pane can close, or a
 * view can change, while the keys still point at it, and every keystroke then
 * goes to a surface that is not there: the client looks frozen. Called on every
 * state change, so focus can never be left pointing at nothing.
 */
export function normalizeFocus(state: SessionState): SessionState {
  const order = visiblePaneOrder(state);
  const focus = order.includes(state.layout.focus) ? state.layout.focus : 'left';
  const zoomedPane =
    state.layout.zoomedPane === null ||
    visiblePaneIds({...state, layout: {...state.layout, focus}}).includes(state.layout.zoomedPane)
      ? state.layout.zoomedPane
      : null;
  if (focus === state.layout.focus && zoomedPane === state.layout.zoomedPane) return state;
  return {...state, layout: {...state.layout, focus, zoomedPane}};
}

/** Escape from a round view: close whatever is layered over it, all of it. */
export function closeOverlays(state: SessionState): SessionState {
  if (state.layout.right === null && !state.chatOpen && state.overlay === null) return state;
  return {
    ...state,
    overlay: null,
    chatOpen: false,
    layout: {right: null, focus: 'left', zoomedPane: null},
  };
}

export function focusPane(state: SessionState, focus: PaneFocus): SessionState {
  if (state.layout.focus === focus) return state;
  if (!visiblePaneOrder(state).includes(focus)) return state;
  return {...state, layout: {...state.layout, focus}};
}

/** The semantic pane currently receiving pane navigation keys. */
export function focusedPane(state: SessionState): PaneId {
  if (experimentLogVisible(state)) {
    if (state.layout.focus === 'chat' && chatPaneVisible(state)) return 'chat';
    if (state.layout.focus === 'right' && state.layout.right !== null) return 'performance';
    return 'experiments';
  }
  // Opening a visualization replaces the agent summary in the left column
  // with the transcript. Keep roundFocus intact so closing the visualization
  // can restore it, but never report the hidden Agents pane as focused.
  if (state.layout.right !== null) {
    return state.layout.focus === 'right' ? 'performance' : 'transcript';
  }
  return state.roundFocus;
}

/**
 * Gives the focused pane the content row, or restores the existing split.
 * Pane content and selection stay in their owning models; zoom only changes
 * presentation, so toggling cannot replace or reconstruct pane state.
 */
export function togglePaneZoom(state: SessionState): SessionState {
  return {
    ...state,
    layout: {
      ...state.layout,
      zoomedPane: state.layout.zoomedPane === null ? focusedPane(state) : null,
    },
  };
}

/** Every pane currently available to focus or zoom in the active view. */
export function visiblePaneIds(state: SessionState): PaneId[] {
  if (experimentLogVisible(state)) {
    return [
      ...(chatPaneVisible(state) ? (['chat'] as const) : []),
      'experiments',
      ...(state.layout.right !== null ? (['performance'] as const) : []),
    ];
  }
  return state.layout.right === null ? ['agents', 'transcript'] : ['transcript', 'performance'];
}

function visiblePaneOrder(state: SessionState): PaneFocus[] {
  return [
    ...(chatPaneVisible(state) ? (['chat'] as const) : []),
    'left' as const,
    ...(state.layout.right !== null ? (['right'] as const) : []),
  ];
}

export function setTheme(state: SessionState, themeName: ThemeName): SessionState {
  // Applying the theme that is already active is still an answer to the
  // picker, so the picker closes either way.
  if (state.themeName === themeName) {
    return state.themePicker === null ? state : {...state, themePicker: null};
  }
  return {...state, themeName, overlay: null, themePicker: null};
}

/** Opens the theme list as a selection, starting on the active theme. */
export function openThemePicker(state: SessionState): SessionState {
  return {...state, overlay: null, themePicker: {selected: state.themeName}};
}

/**
 * Moves the highlighted theme. The list has ends rather than a cycle, so the
 * selection clamps instead of wrapping.
 */
export function moveThemeSelection(state: SessionState, delta: number): SessionState {
  const picker = state.themePicker;
  if (picker === null) return state;
  const current = THEME_NAMES.indexOf(picker.selected);
  const index = Math.min(THEME_NAMES.length - 1, Math.max(0, current + delta));
  const selected = THEME_NAMES[index];
  if (selected === undefined || selected === picker.selected) return state;
  return {...state, themePicker: {selected}};
}

/** Closes the picker, leaving the theme as it was when it opened. */
export function closeThemePicker(state: SessionState): SessionState {
  if (state.themePicker === null) return state;
  return {...state, themePicker: null};
}

export function applySnapshot(state: SessionState, snapshot: RunSnapshot): SessionState {
  const core = reduceSnapshot(state.core, snapshot);
  return core === state.core ? state : {...state, core};
}

/** Record transport health as frontend state without rewriting backend-derived facts. */
export function markEventStreamUnavailable(state: SessionState): SessionState {
  return state.eventStreamAvailable ? {...state, eventStreamAvailable: false} : state;
}

/** Active work is presentable only while its source stream remains trustworthy. */
export function visibleActiveExecutions(state: SessionState): ActiveAgentExecution[] {
  if (!state.eventStreamAvailable) return [];
  const roundNumber = visibleRoundNumber(state);
  return Object.values(state.core.activeExecutions).filter(
    execution =>
      (roundNumber === null || execution.roundNumber === roundNumber) &&
      (state.selectedAgentKind === null || execution.agentKind === state.selectedAgentKind),
  );
}

/** Replace activity with the backend checkpoint without advancing the event replay cursor. */
export function applyActiveExecutionCheckpoint(
  state: SessionState,
  executions: ActiveExecutionCheckpoint,
  throughSequence?: number,
): SessionState {
  const core = reconcileActiveExecutions(state.core, executions, throughSequence);
  return core === state.core ? state : {...state, core};
}

export function applyEvent(state: SessionState, event: RunEvent): SessionState {
  const core = reduceEvent(state.core, event);
  if (core === state.core) return state;
  const diagnostic = latestDiagnosticChange(state.core, core);
  let next: SessionState = {
    ...state,
    core,
    chatConversation: reconcileChatTranscript(state.chatConversation, core.chatTranscript),
  };
  if (diagnostic !== null) next = reportProjectedDiagnostic(next, diagnostic);
  return next;
}

function reconcileChatTranscript(
  conversation: ConversationEntry[],
  transcript: TranscriptEntry[],
): ConversationEntry[] {
  const byId = new Map(transcript.map(entry => [entry.id, entry]));
  const present = new Set<string>();
  const updated = conversation.map(entry => {
    const replacement = byId.get(entry.id);
    if (replacement === undefined) return entry;
    present.add(entry.id);
    return replacement;
  });
  for (const entry of transcript) {
    if (!present.has(entry.id) && !conversation.some(existing => existing.id === entry.id)) {
      updated.push(entry);
    }
  }
  return updated.slice(-500);
}

export function selectNextAgent(state: SessionState): SessionState {
  const phases = visiblePhases(state);
  if (phases.length === 0) return state;
  const current = state.selectedAgentKind;
  const index = current === null ? -1 : phases.findIndex(phase => phase.kind === current);
  const next = phases[(index + 1 + phases.length) % phases.length];
  return {...state, selectedAgentKind: next?.kind ?? null, roundFocus: 'agents', overlay: null};
}

export function selectPreviousAgent(state: SessionState): SessionState {
  const phases = visiblePhases(state);
  if (phases.length === 0) return state;
  const current = state.selectedAgentKind;
  const index = current === null ? 0 : phases.findIndex(phase => phase.kind === current);
  const previous = phases[(index - 1 + phases.length) % phases.length];
  return {
    ...state,
    selectedAgentKind: previous?.kind ?? null,
    roundFocus: 'agents',
    overlay: null,
  };
}

export function selectNextRound(state: SessionState): SessionState {
  const rounds = stripRounds(state);
  if (rounds.length === 0) return state;
  const visible = visibleRoundNumber(state);
  const index = visible === null ? -1 : rounds.findIndex(round => round.number === visible);
  const next = rounds[(index + 1 + rounds.length) % rounds.length];
  return {
    ...state,
    selectedRound: next?.number ?? null,
    selectedAgentKind: null,
    selectedEntryId: null,
    overlay: null,
  };
}

export function selectPreviousRound(state: SessionState): SessionState {
  const rounds = stripRounds(state);
  if (rounds.length === 0) return state;
  const visible = visibleRoundNumber(state);
  const index = visible === null ? 0 : rounds.findIndex(round => round.number === visible);
  const previous = rounds[(index - 1 + rounds.length) % rounds.length];
  return {
    ...state,
    selectedRound: previous?.number ?? null,
    selectedAgentKind: null,
    selectedEntryId: null,
    overlay: null,
  };
}

export function selectRound(state: SessionState, roundNumber: number): SessionState {
  if (!stripRounds(state).some(round => round.number === roundNumber)) return state;
  return {
    ...state,
    selectedRound: roundNumber,
    selectedAgentKind: null,
    selectedEntryId: null,
    overlay: null,
  };
}

export function clearAgentSelection(state: SessionState): SessionState {
  return {
    ...state,
    selectedAgentKind: null,
    selectedEntryId: null,
    roundFocus: 'transcript',
    overlay: null,
  };
}

/**
 * Picks one agent out of the round, or clears the pick when it is already the
 * selected one, so clicking a node twice behaves the way a toggle should. The
 * transcript follows the selection, so the entry cursor starts over with it.
 */
export function selectAgent(state: SessionState, kind: string): SessionState {
  const selected = state.selectedAgentKind === kind ? null : kind;
  return {
    ...state,
    selectedAgentKind: selected,
    selectedEntryId: null,
    roundFocus: 'agents',
    overlay: null,
  };
}

/**
 * Moves the transcript cursor. The cursor is an entry id rather than an index
 * because the visible list changes underneath it as the run streams: an id
 * still points at the same turn after new output arrives.
 */
export function selectNextEntry(state: SessionState, delta: number, id?: string): SessionState {
  const entries = visibleConversation(state);
  if (entries.length === 0) return state;
  // A click names the entry outright; the keys step from wherever the cursor is.
  if (id !== undefined) {
    if (!entries.some(entry => entry.id === id)) return state;
    return {...state, selectedEntryId: state.selectedEntryId === id ? null : id};
  }
  const current =
    state.selectedEntryId === null
      ? -1
      : entries.findIndex(entry => entry.id === state.selectedEntryId);
  // No cursor yet: step in from the end the operator is moving away from.
  const start = current === -1 ? (delta > 0 ? -1 : entries.length) : current;
  const index = Math.min(entries.length - 1, Math.max(0, start + delta));
  return {...state, selectedEntryId: entries[index]?.id ?? null};
}

/**
 * Moves the round view's keys one pane over, in the direction the arrow points.
 * The agent graph is the left pane and the transcript the right one, so this is
 * the spatial move; at either edge it stays put rather than wrapping, because
 * wrapping across the width of the screen is disorienting.
 */
export function focusRound(state: SessionState, focus: RoundFocus): SessionState {
  if (state.roundFocus === focus) return state;
  // Arriving at the graph with nothing picked out puts the cursor on the agent
  // whose turns are on screen, so Tab starts from where the operator is looking.
  if (focus === 'agents' && state.selectedAgentKind === null) {
    const phases = visiblePhases(state);
    const active = phases.find(phase => phase.status === 'active') ?? phases[0];
    return {...state, roundFocus: focus, selectedAgentKind: active?.kind ?? null};
  }
  return {...state, roundFocus: focus};
}

export function clearEntrySelection(state: SessionState): SessionState {
  if (state.selectedEntryId === null) return state;
  return {...state, selectedEntryId: null};
}

/** Moves the todo cursor within the visible phase's list. */
export function selectNextTodo(state: SessionState, delta: number): SessionState {
  const todos = visibleTodos(state);
  if (todos.length === 0) return state;
  const current = state.selectedTodoIndex;
  const start = current === null ? (delta > 0 ? -1 : todos.length) : current;
  const index = Math.min(todos.length - 1, Math.max(0, start + delta));
  return {...state, selectedTodoIndex: index};
}

export interface ErrorReport {
  scope: ErrorScope;
  severity?: ErrorSeverity;
  title?: string;
  diagnostic?: Diagnostic | null;
  diagnosticId?: string | null;
  detail?: string | null;
  hint?: string | null;
  agentKind?: string | null;
  roundLabel?: string | null;
  invocationId?: string | null;
}

/** Acknowledges the visible diagnostic without changing any run or view state. */
export function dismissErrorBanner(state: SessionState): SessionState {
  if (state.errorBanner === null) return state;
  return {...state, errorBanner: null};
}

/**
 * Records an error independently of any particular view. A terminal event
 * commonly repeats an invocation failure, so equivalent reports promote the
 * current banner instead of burying its cause beneath a duplicate.
 */
export function reportError(
  state: SessionState,
  message: string,
  report: ErrorReport,
): SessionState {
  const diagnostic = report.diagnostic ?? null;
  const scope = diagnostic?.scope ?? report.scope;
  const severity = diagnosticSeverity(diagnostic?.severity) ?? report.severity ?? 'recoverable';
  const banner: ErrorBannerState = {
    title: report.title ?? errorTitle(scope),
    message: diagnostic?.summary || message || 'An unknown error occurred.',
    detail: diagnostic?.detail ?? report.detail ?? null,
    hint: diagnostic?.hint ?? report.hint ?? null,
    diagnosticId: diagnostic?.id ?? report.diagnosticId ?? null,
    severity,
    scope,
    agentKind: report.agentKind ?? null,
    roundLabel: report.roundLabel ?? null,
    invocationId: report.invocationId ?? null,
    count: 1,
  };
  const existing = state.errorBanner;
  if (existing === null || !equivalentError(existing, banner)) {
    return {...state, errorBanner: banner};
  }
  const promoted = existing.severity === 'fatal' || severity === 'fatal' ? 'fatal' : 'recoverable';
  return {
    ...state,
    errorBanner: {
      ...existing,
      message: moreInformativeMessage(existing.message, banner.message),
      detail: moreInformativeMessage(existing.detail ?? '', banner.detail ?? '') || null,
      hint: moreInformativeMessage(existing.hint ?? '', banner.hint ?? '') || null,
      severity: promoted,
      title: promoted === 'fatal' ? banner.title : existing.title,
      scope: promoted === 'fatal' ? banner.scope : existing.scope,
      diagnosticId: existing.diagnosticId ?? banner.diagnosticId,
      agentKind: existing.agentKind ?? banner.agentKind,
      roundLabel: existing.roundLabel ?? banner.roundLabel,
      invocationId: existing.invocationId ?? banner.invocationId,
      count: existing.count + 1,
    },
  };
}

function reportProjectedDiagnostic(state: SessionState, diagnostic: CoreDiagnostic): SessionState {
  return reportError(state, diagnostic.summary, {
    scope: diagnostic.scope,
    severity: diagnostic.severity === 'fatal' ? 'fatal' : 'recoverable',
    title: projectedDiagnosticTitle(diagnostic.failureKind),
    diagnosticId: diagnostic.id,
    detail: diagnostic.detail,
    hint: diagnostic.hint,
    agentKind: diagnostic.agentKind,
    roundLabel: diagnostic.roundLabel,
    invocationId: diagnostic.invocationId,
  });
}

function projectedDiagnosticTitle(kind: CoreDiagnostic['failureKind']): string {
  if (kind === 'run_interruption') return 'Run interrupted';
  return errorTitle(kind);
}

/** Keep a terminal wrapper's extra context when it repeats an invocation error. */
function moreInformativeMessage(current: string, later: string): string {
  if (later.length >= current.length) return later;
  const currentLines = current.split('\n').length;
  const laterLines = later.split('\n').length;
  return laterLines > currentLines ? later : current;
}

function equivalentError(left: ErrorBannerState, right: ErrorBannerState): boolean {
  if (left.diagnosticId !== null && left.diagnosticId === right.diagnosticId) return true;
  if (left.invocationId !== null && left.invocationId === right.invocationId) return true;
  const leftMessage = left.message.trim();
  const rightMessage = right.message.trim();
  if (leftMessage === rightMessage) return true;
  // Terminal wrappers often add only an exception prefix around an invocation
  // error. Fold those together, but do not equate short generic diagnostics.
  return (
    Math.min(leftMessage.length, rightMessage.length) >= 24 &&
    (leftMessage.includes(rightMessage) || rightMessage.includes(leftMessage))
  );
}

function errorTitle(scope: ErrorScope): string {
  const titles: Record<ErrorScope, string> = {
    configuration: 'Configuration failed',
    invocation: 'Invocation failed',
    phase: 'Phase failed',
    run: 'Run failed',
    protocol: 'Protocol error',
    request: 'Request failed',
    transport: 'Connection lost',
    input: 'Input error',
  };
  return titles[scope];
}

function diagnosticSeverity(
  severity: Diagnostic['severity'] | undefined,
): ErrorSeverity | undefined {
  if (severity === undefined) return undefined;
  return severity === 'fatal' ? 'fatal' : 'recoverable';
}

/**
 * Returns to the experiment log. Per-round output is reachable only by opening
 * a hypothesis, so there is no unfiltered live view to fall back to and the
 * log is never dismissed.
 */
export function showLive(state: SessionState): SessionState {
  return {
    ...state,
    overlay: null,
    chatOpen: false,
    hypothesisScope: null,
    selectedRound: null,
    selectedAgentKind: null,
    experimentLog: state.experimentLog ?? {
      entries: [],
      selectedId: null,
      pending: true,
      error: null,
    },
  };
}

export function showDetail(
  state: SessionState,
  content: string,
  kind: OverlayPanel['kind'] = 'detail',
): SessionState {
  return {...state, overlay: {kind, content}};
}

export function statusText(state: SessionState): string {
  const base = `${state.core.status} · ${state.core.agentKind ?? 'starting'} · ${state.core.roundLabel ?? 'no round yet'}`;
  if (state.core.usage === null) return base;
  const used = formatTokenCount(state.core.usage.inputTokens);
  const meter =
    state.core.usage.contextWindow === null
      ? used
      : `${used}/${formatTokenCount(state.core.usage.contextWindow)}`;
  return `${base} · ${meter} tokens`;
}

function formatTokenCount(count: number): string {
  if (count < 1_000) return String(count);
  if (count < 1_000_000) return `${Math.floor(count / 1_000)}k`;
  return `${(count / 1_000_000).toFixed(1)}M`;
}

export function visibleConversation(state: SessionState): ConversationEntry[] {
  const roundNumber = visibleRoundNumber(state);
  return state.core.transcript.filter(entry => {
    if (roundNumber !== null && entry.roundNumber !== roundNumber) return false;
    if (state.selectedAgentKind !== null && entry.agentKind !== state.selectedAgentKind) {
      return false;
    }
    return true;
  });
}

export function visiblePhases(state: SessionState): AgentPhase[] {
  return phasesForRound(state.core.phases, visibleRoundNumber(state));
}

export function toggleTodos(state: SessionState): SessionState {
  return {...state, todosExpanded: !state.todosExpanded};
}

/**
 * The todo list for the execution the operator is looking at, following the same
 * scoping rules as the conversation filter. Entries whose events carried no
 * agent or round stamp (legacy streams) match any scope rather than vanish.
 */
export function visibleTodos(state: SessionState): TodoItem[] {
  const roundNumber = visibleRoundNumber(state);
  const matchesRound = (phase: ExecutionTodos): boolean =>
    roundNumber === null || phase.roundNumber === roundNumber || phase.roundNumber === null;
  const latestFirst = [...state.core.todos].reverse();
  if (state.selectedAgentKind !== null) {
    const selected = state.selectedAgentKind;
    return (
      latestFirst.find(
        phase => (phase.agentKind === selected || phase.agentKind === null) && matchesRound(phase),
      )?.items ?? []
    );
  }
  if (state.selectedRound !== null) {
    // Browsing a round without an agent selected: show the round's most
    // recently updated list, i.e. its final todo state.
    return latestFirst.find(matchesRound)?.items ?? [];
  }
  // Live view: follow the currently active agent so a phase that never
  // emits todos shows nothing instead of the previous phase's leftovers.
  return (
    latestFirst.find(
      phase =>
        (phase.agentKind === state.core.agentKind || phase.agentKind === null) &&
        matchesRound(phase),
    )?.items ?? []
  );
}

export function visibleRoundNumber(state: SessionState): number | null {
  const scope = state.hypothesisScope;
  if (scope !== null && state.selectedRound === null) {
    // Opening a hypothesis lands on one of its rounds rather than on nothing.
    // Leaving the round unset used to mean "the whole trajectory", which drew an
    // empty agent strip and an empty transcript for any run whose events are not
    // all round-stamped: a blank view where a round was asked for.
    const rounds = state.core.rounds.filter(round => scope.rounds.includes(round.number));
    const active = [...rounds].reverse().find(round => round.status === 'active');
    return active?.number ?? rounds.at(-1)?.number ?? scope.rounds.at(-1) ?? null;
  }
  if (state.selectedRound !== null) return state.selectedRound;
  const rounds = stripRounds(state);
  const active = [...rounds].reverse().find(round => round.status === 'active');
  return active?.number ?? rounds.at(-1)?.number ?? null;
}

/** The rounds owned by the hypothesis on screen, or every round outside one. */
export function scopedRounds(state: SessionState): RoundSummary[] {
  const scope = state.hypothesisScope;
  if (scope === null) return state.core.rounds;
  return state.core.rounds.filter(round => scope.rounds.includes(round.number));
}

/**
 * What the strip shows and what round navigation moves through: every round of
 * the run, including ones it has not reached yet. A run announces how many
 * rounds it intends to take, so the strip can show the shape of the whole run
 * from the first round rather than growing one chip at a time. Rounds beyond
 * what has happened carry ``planned`` and open an empty view, which is the
 * honest thing to show for a round that has not run.
 */
export function stripRounds(state: SessionState): RoundSummary[] {
  const highest = Math.max(
    state.core.maxRounds ?? 0,
    ...state.core.rounds.map(round => round.number),
    0,
  );
  if (highest === 0) return state.core.rounds;
  const known = new Map(state.core.rounds.map(round => [round.number, round]));
  const rounds: RoundSummary[] = [];
  for (let number = 1; number <= highest; number += 1) {
    rounds.push(known.get(number) ?? {number, status: 'planned'});
  }
  return rounds;
}
