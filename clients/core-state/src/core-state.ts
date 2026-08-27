import type {Diagnostic, RunEvent, RunSnapshot} from '@vibesys/backend-client';
import {
  type AgentPhase,
  applyRunMapEvent,
  type RoundSummary,
  roundNumberFromLabel,
} from './run-map.js';

export type AgentExecutionMode = 'thinking' | 'responding' | 'tool' | 'waiting';

export interface ActiveAgentExecution {
  executionId: string;
  agentKind: string;
  roundLabel: string | null;
  roundNumber: number | null;
  stage: string;
  attempt: number | null;
  assignment: string;
  startedAt: string;
  activity: {mode: AgentExecutionMode; summary: string; tool?: string | null};
}

export interface TodoItem {
  content: string;
  status: string;
}

export interface ExecutionTodos {
  executionId?: string | null;
  agentKind: string | null;
  roundNumber: number | null;
  items: TodoItem[];
}

export interface UsageMeter {
  inputTokens: number;
  contextWindow: number | null;
  model: string | null;
}

export interface BenchmarkRecord {
  sequence: number;
  roundNumber: number | null;
  metric: string;
  value: number;
  unit: string;
}

type RunEventData = NonNullable<RunEvent['data']>;
export type TypedToolResult = Extract<RunEventData, {kind?: 'tool_result'}>;

/** Thread id used for chat events recorded without one. */
export const DEFAULT_CHAT_THREAD_ID = 'default';

/**
 * One experiment-chat thread, replayed from `chat_thread_created` events. The
 * default thread is implicit: it exists from the first frame and carries no
 * agent selection of its own.
 */
export interface ChatThread {
  id: string;
  /** Backend-owned title; empty until the server has derived or been given one. */
  title: string;
  driver: string | null;
  provider: string | null;
  model: string | null;
}

export interface TranscriptEntry {
  id: string;
  kind:
    | 'assistant'
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
  toolResult?: TypedToolResult;
}

/** Backend diagnostic facts. Visibility and dismissal belong to the UI. */
export interface CoreDiagnostic {
  id: string | null;
  code: string | null;
  failureKind: Diagnostic['scope'] | 'run_interruption';
  summary: string;
  detail: string | null;
  hint: string | null;
  severity: 'warning' | 'error' | 'fatal';
  scope: Diagnostic['scope'];
  agentKind: string | null;
  roundLabel: string | null;
  invocationId: string | null;
  sequence: number;
}

export interface CoreState {
  sequence: number;
  status: string;
  agentKind: string | null;
  roundLabel: string | null;
  outerLoop: string | null;
  maxRounds: number | null;
  terminal: boolean;
  rounds: RoundSummary[];
  phases: AgentPhase[];
  activeExecutions: Record<string, ActiveAgentExecution>;
  transcript: TranscriptEntry[];
  /** The default thread's transcript; equals `chatTranscripts[DEFAULT_CHAT_THREAD_ID]`. */
  chatTranscript: TranscriptEntry[];
  /** Every thread's transcript, keyed by thread id. */
  chatTranscripts: Record<string, TranscriptEntry[]>;
  /** The default thread first, then created threads in replay order. */
  chatThreads: ChatThread[];
  todos: ExecutionTodos[];
  usage: UsageMeter | null;
  benchmarks: BenchmarkRecord[];
  diagnostics: CoreDiagnostic[];
  /** Sequence of the latest semantic experiment invalidation. */
  experimentsRevision: number;
  typedToolEvents: boolean;
  /** Per thread id: typed tool events seen, so legacy tool chunks are dropped. */
  chatTypedToolEvents: Record<string, boolean>;
}

export function initialCoreState(): CoreState {
  return {
    sequence: 0,
    status: 'connecting',
    agentKind: null,
    roundLabel: null,
    outerLoop: null,
    maxRounds: null,
    terminal: false,
    rounds: [],
    phases: [],
    activeExecutions: {},
    transcript: [],
    chatTranscript: [],
    chatTranscripts: {[DEFAULT_CHAT_THREAD_ID]: []},
    // The default thread has no backend record, so it has no backend-owned
    // title either. Naming it is the consumer's job.
    chatThreads: [
      {id: DEFAULT_CHAT_THREAD_ID, title: '', driver: null, provider: null, model: null},
    ],
    todos: [],
    usage: null,
    benchmarks: [],
    diagnostics: [],
    experimentsRevision: 0,
    typedToolEvents: false,
    chatTypedToolEvents: {},
  };
}

/** The transcript for one chat thread; unknown threads read as empty. */
export function chatTranscriptFor(state: CoreState, threadId: string): TranscriptEntry[] {
  return state.chatTranscripts[threadId] ?? [];
}

export function reduceSnapshot(state: CoreState, snapshot: RunSnapshot): CoreState {
  if (snapshot.sequence < state.sequence) return state;
  return {
    ...state,
    status: snapshot.status,
    agentKind: snapshot.agent_kind ?? null,
    roundLabel: snapshot.round_label ?? null,
    terminal: snapshot.status === 'completed' || snapshot.status === 'failed',
    activeExecutions: activeExecutionsFromCheckpoint(snapshot.active_executions ?? []),
  };
}

export type ActiveExecutionCheckpoint = NonNullable<RunSnapshot['active_executions']>;

/** Checkpoints reconcile liveness without changing the event replay cursor. */
export function reconcileActiveExecutions(
  state: CoreState,
  executions: ActiveExecutionCheckpoint,
  throughSequence?: number,
): CoreState {
  if (throughSequence !== undefined && throughSequence < state.sequence) return state;
  return {...state, activeExecutions: activeExecutionsFromCheckpoint(executions)};
}

/** Fold an ordered batch before applying its backend liveness checkpoint. */
export function reduceEventBatch(
  state: CoreState,
  events: readonly RunEvent[],
  activeExecutions?: ActiveExecutionCheckpoint,
  throughSequence?: number,
): CoreState {
  const reduced = events.reduce(reduceEvent, state);
  return activeExecutions === undefined
    ? reduced
    : reconcileActiveExecutions(reduced, activeExecutions, throughSequence);
}

export function reduceEvent(state: CoreState, event: RunEvent): CoreState {
  const sequence = event.sequence ?? 0;
  if (sequence > 0 && sequence <= state.sequence) return state;
  let next: CoreState = {...state, sequence: Math.max(state.sequence, sequence)};
  next = applyDiagnosticEvent(next, event);
  next = applyAgentExecutionEvent(next, event);
  if (event.agent_kind === 'chat') return applyChatEvent(next, event);
  if (event.agent_kind) next.agentKind = event.agent_kind;
  if (event.round_label) next.roundLabel = event.round_label;
  const runMap = applyRunMapEvent(next, event);
  next.outerLoop = runMap.outerLoop;
  next.rounds = runMap.rounds;
  next.phases = runMap.phases;

  const data = event.data;
  if (data?.kind === 'tool_call' || data?.kind === 'tool_result') next.typedToolEvents = true;
  if (data?.kind === 'todo_update') next.todos = updateTodos(next.todos, event);
  if (data?.kind === 'usage_update') {
    next.usage = {
      inputTokens: data.input_tokens,
      contextWindow: data.context_window ?? null,
      model: data.model ?? null,
    };
  }
  if (data?.kind === 'benchmark_result') {
    next.benchmarks = [
      ...next.benchmarks,
      {
        sequence,
        roundNumber: roundNumberFromLabel(event.round_label),
        metric: data.metric,
        value: data.value,
        unit: data.unit,
      },
    ];
  }
  if (data?.kind === 'experiments_changed') next.experimentsRevision = sequence;

  const legacyToolChunk =
    data?.kind === 'agent_output_chunk' && data.channel === 'tool' && next.typedToolEvents;
  if (!legacyToolChunk) {
    const entry = eventToTranscriptEntry(event);
    if (entry !== null) next.transcript = appendTranscript(next.transcript, entry);
  }

  if (event.type === 'run_started') {
    next.status = 'running';
    if (data?.kind === 'run_started') next.maxRounds = data.max_rounds;
  }
  if (event.type === 'configuration_failed') return terminate(next, 'failed');
  if (event.type === 'run_finished') return terminate(next, 'completed');
  if (event.type === 'run_failed' || event.type === 'run_interrupted') {
    return terminate(next, 'failed');
  }
  return next;
}

/** Returns the one diagnostic added or updated by a reducer transition. */
export function latestDiagnosticChange(
  previous: CoreState,
  current: CoreState,
): CoreDiagnostic | null {
  if (previous.diagnostics === current.diagnostics) return null;
  return (
    current.diagnostics.find((diagnostic, index) => diagnostic !== previous.diagnostics[index]) ??
    null
  );
}

function terminate(state: CoreState, status: string): CoreState {
  return {...state, status, terminal: true, activeExecutions: {}};
}

function activeExecutionsFromCheckpoint(
  executions: ActiveExecutionCheckpoint,
): Record<string, ActiveAgentExecution> {
  return Object.fromEntries(
    executions.map(execution => [
      execution.execution_id,
      {
        executionId: execution.execution_id,
        agentKind: execution.agent_kind,
        roundLabel: execution.round_label ?? null,
        roundNumber: roundNumberFromLabel(execution.round_label),
        stage: execution.stage,
        attempt: execution.attempt ?? null,
        assignment: execution.assignment,
        startedAt: execution.started_at,
        activity: {
          mode: execution.activity.mode,
          summary: execution.activity.summary,
          tool: execution.activity.tool ?? null,
        },
      },
    ]),
  );
}

function applyAgentExecutionEvent(state: CoreState, event: RunEvent): CoreState {
  const executionId = event.execution_id;
  const data = event.data;
  if (executionId == null) return state;
  if (data?.kind === 'agent_execution_started') {
    return {
      ...state,
      activeExecutions: {
        ...state.activeExecutions,
        [executionId]: {
          executionId,
          agentKind: event.agent_kind ?? 'agent',
          roundLabel: event.round_label ?? null,
          roundNumber: roundNumberFromLabel(event.round_label),
          stage: data.stage,
          attempt: data.attempt ?? null,
          assignment: data.user_prompt ?? '',
          startedAt: event.timestamp,
          activity: {
            mode: data.activity.mode,
            summary: data.activity.summary,
            tool: data.activity.tool ?? null,
          },
        },
      },
    };
  }
  if (data?.kind === 'agent_execution_activity_changed') {
    const current = state.activeExecutions[executionId];
    if (current === undefined) return state;
    return {
      ...state,
      activeExecutions: {
        ...state.activeExecutions,
        [executionId]: {
          ...current,
          activity: {mode: data.mode, summary: data.summary, tool: data.tool ?? null},
        },
      },
    };
  }
  if (data?.kind === 'agent_execution_finished') {
    const {[executionId]: _finished, ...remaining} = state.activeExecutions;
    return {...state, activeExecutions: remaining};
  }
  return state;
}

function updateTodos(previous: ExecutionTodos[], event: RunEvent): ExecutionTodos[] {
  const data = event.data;
  if (data?.kind !== 'todo_update') return previous;
  const agentKind = event.agent_kind ?? null;
  const roundNumber = roundNumberFromLabel(event.round_label);
  const executionId = event.execution_id ?? event.invocation_id ?? null;
  const retained = previous.filter(item =>
    executionId === null
      ? item.executionId != null || item.agentKind !== agentKind || item.roundNumber !== roundNumber
      : item.executionId !== executionId,
  );
  return [
    ...retained,
    {
      executionId,
      agentKind,
      roundNumber,
      items: (data.todos ?? []).map(todo => ({
        content: String(todo.content),
        status: String(todo.status),
      })),
    },
  ].slice(-100);
}

function applyChatEvent(state: CoreState, event: RunEvent): CoreState {
  const data = event.data;
  const threadId = event.chat_thread_id ?? DEFAULT_CHAT_THREAD_ID;
  if (data?.kind === 'chat_thread_created') {
    return upsertChatThread(state, {
      id: data.thread_id,
      title: data.title ?? '',
      driver: data.driver,
      provider: data.provider,
      model: data.model,
    });
  }
  let next = state;
  const typed = data?.kind === 'tool_call' || data?.kind === 'tool_result';
  if (typed && next.chatTypedToolEvents[threadId] !== true) {
    next = {...next, chatTypedToolEvents: {...next.chatTypedToolEvents, [threadId]: true}};
  }
  const legacyToolChunk =
    data?.kind === 'agent_output_chunk' &&
    data.channel === 'tool' &&
    next.chatTypedToolEvents[threadId] === true;
  if (legacyToolChunk) return next;
  if (data?.kind === 'chat' && data.thread_title) {
    next = setChatThreadTitle(next, threadId, data.thread_title);
  }
  const entry = eventToTranscriptEntry(event);
  if (entry === null || (entry.kind !== 'assistant' && entry.kind !== 'result')) return next;
  return appendChatTranscript(next, threadId, entry);
}

/** Registers a replayed thread, or refreshes the record it already has. */
function upsertChatThread(state: CoreState, thread: ChatThread): CoreState {
  const existing = state.chatThreads.find(candidate => candidate.id === thread.id);
  const chatThreads =
    existing === undefined
      ? [...state.chatThreads, thread]
      : state.chatThreads.map(candidate =>
          candidate.id === thread.id
            ? {...thread, title: thread.title || candidate.title}
            : candidate,
        );
  const chatTranscripts =
    state.chatTranscripts[thread.id] === undefined
      ? {...state.chatTranscripts, [thread.id]: []}
      : state.chatTranscripts;
  return {...state, chatThreads, chatTranscripts};
}

function setChatThreadTitle(state: CoreState, threadId: string, title: string): CoreState {
  if (!state.chatThreads.some(thread => thread.id === threadId)) {
    // A titled turn for a thread whose creation event is missing from the
    // replay window still names a thread the operator can select.
    return setChatThreadTitle(
      upsertChatThread(state, {id: threadId, title: '', driver: null, provider: null, model: null}),
      threadId,
      title,
    );
  }
  return {
    ...state,
    chatThreads: state.chatThreads.map(thread =>
      thread.id === threadId ? {...thread, title} : thread,
    ),
  };
}

function appendChatTranscript(
  state: CoreState,
  threadId: string,
  entry: TranscriptEntry,
): CoreState {
  const transcript = appendTranscript(state.chatTranscripts[threadId] ?? [], entry);
  const chatTranscripts = {...state.chatTranscripts, [threadId]: transcript};
  return {
    ...state,
    chatTranscripts,
    chatTranscript: threadId === DEFAULT_CHAT_THREAD_ID ? transcript : state.chatTranscript,
  };
}

function applyDiagnosticEvent(state: CoreState, event: RunEvent): CoreState {
  const diagnostic = diagnosticFromEvent(event);
  if (diagnostic === null) return state;
  const existingIndex = state.diagnostics.findIndex(existing =>
    diagnostic.id !== null
      ? existing.id === diagnostic.id
      : existing.id === null &&
        diagnostic.invocationId !== null &&
        existing.invocationId === diagnostic.invocationId,
  );
  if (existingIndex !== -1) {
    return {
      ...state,
      diagnostics: state.diagnostics.map((existing, index) =>
        index === existingIndex ? mergeDiagnostic(existing, diagnostic) : existing,
      ),
    };
  }
  return {...state, diagnostics: [...state.diagnostics, diagnostic]};
}

function mergeDiagnostic(existing: CoreDiagnostic, incoming: CoreDiagnostic): CoreDiagnostic {
  return {
    ...existing,
    ...incoming,
    code: incoming.code ?? existing.code,
    detail: incoming.detail ?? existing.detail,
    hint: incoming.hint ?? existing.hint,
    severity:
      diagnosticSeverityRank(incoming.severity) > diagnosticSeverityRank(existing.severity)
        ? incoming.severity
        : existing.severity,
    agentKind: incoming.agentKind ?? existing.agentKind,
    roundLabel: incoming.roundLabel ?? existing.roundLabel,
    invocationId: incoming.invocationId ?? existing.invocationId,
    sequence: Math.max(existing.sequence, incoming.sequence),
  };
}

function diagnosticSeverityRank(severity: CoreDiagnostic['severity']): number {
  if (severity === 'fatal') return 2;
  if (severity === 'error') return 1;
  return 0;
}

function diagnosticFromEvent(event: RunEvent): CoreDiagnostic | null {
  const diagnostic = event.diagnostic;
  if (diagnostic !== null && diagnostic !== undefined) {
    return fromProtocolDiagnostic(event, diagnostic);
  }
  const data = event.data;
  if (data?.kind === 'configuration_failed') {
    return fallbackDiagnostic(
      event,
      configurationFailureContent(data),
      'configuration',
      'fatal',
      data.code,
    );
  }
  if (
    data?.kind === 'invocation_finished' &&
    ((data.error !== null && data.error !== undefined) || event.status === 'failed')
  ) {
    return fallbackDiagnostic(
      event,
      data.error || event.text || 'Agent invocation failed.',
      'invocation',
      'error',
    );
  }
  if (event.type === 'run_failed' || event.type === 'run_interrupted') {
    const interruption =
      data?.kind === 'run_interrupted'
        ? `${data.reason}${data.signal === null ? '' : ` (${data.signal})`}`
        : '';
    return fallbackDiagnostic(
      event,
      event.text ||
        interruption ||
        (event.type === 'run_failed' ? 'Run failed.' : 'Run interrupted.'),
      'run',
      'fatal',
    );
  }
  return null;
}

function fromProtocolDiagnostic(event: RunEvent, diagnostic: Diagnostic): CoreDiagnostic {
  return {
    id: diagnostic.id ?? null,
    code: diagnostic.code ?? null,
    failureKind: failureKind(diagnostic.scope, event.type),
    summary: diagnostic.summary,
    detail: diagnostic.detail ?? null,
    hint: diagnostic.hint ?? null,
    severity: diagnostic.severity ?? 'error',
    scope: diagnostic.scope,
    agentKind: event.agent_kind ?? null,
    roundLabel: event.round_label ?? null,
    invocationId: event.invocation_id ?? null,
    sequence: event.sequence ?? 0,
  };
}

function fallbackDiagnostic(
  event: RunEvent,
  summary: string,
  scope: Diagnostic['scope'],
  severity: CoreDiagnostic['severity'],
  code: string | null = null,
): CoreDiagnostic {
  return {
    id: null,
    code,
    failureKind: failureKind(scope, event.type),
    summary,
    detail: null,
    hint: null,
    severity,
    scope,
    agentKind: event.agent_kind ?? null,
    roundLabel: event.round_label ?? null,
    invocationId: event.invocation_id ?? null,
    sequence: event.sequence ?? 0,
  };
}

function failureKind(
  scope: Diagnostic['scope'],
  eventType: RunEvent['type'],
): CoreDiagnostic['failureKind'] {
  return eventType === 'run_interrupted' ? 'run_interruption' : scope;
}

function eventToTranscriptEntry(event: RunEvent): TranscriptEntry | null {
  const data = event.data;
  const id = String(event.sequence ?? `${event.timestamp}-${event.type}`);
  const agentFields = event.agent_kind ? {agentKind: event.agent_kind} : {};
  const roundNumber = roundNumberFromLabel(event.round_label);
  const roundFields = {
    ...(event.round_label ? {roundLabel: event.round_label} : {}),
    ...(roundNumber === null ? {} : {roundNumber}),
  };
  if (data?.kind === 'configuration_failed') {
    return {
      id,
      kind: 'result',
      content: configurationFailureContent(data),
      label: 'Configuration failed',
      tone: 'failure',
    };
  }
  if (data?.kind === 'chat') {
    return {
      id,
      kind: 'assistant',
      content: data.answer,
      label: 'Answer',
      ...agentFields,
      ...roundFields,
    };
  }
  if (data?.kind === 'agent_output_chunk') {
    const kind = outputKind(data.channel);
    const invocationId = event.invocation_id ?? undefined;
    return {
      id,
      kind,
      content: data.content,
      label: labelFor(event, data.channel),
      ...agentFields,
      ...roundFields,
      turnId: invocationId ?? id,
      ...(invocationId === undefined ? {} : {invocationId}),
      ...(kind === 'tool' && data.content.trimStart().startsWith('→ ')
        ? {startsTurn: true, toolCall: data.content}
        : {}),
    };
  }
  if (data?.kind === 'tool_call') {
    const invocationId = event.invocation_id ?? undefined;
    return {
      id,
      kind: 'tool',
      content: '',
      label: labelFor(event, 'tool'),
      ...agentFields,
      ...roundFields,
      turnId: invocationId ?? id,
      ...(invocationId === undefined ? {} : {invocationId}),
      startsTurn: true,
      toolName: data.tool,
      toolArguments: data.args ?? {},
      ...(data.call_id == null ? {} : {toolCallId: data.call_id}),
    };
  }
  if (data?.kind === 'tool_result') {
    const invocationId = event.invocation_id ?? undefined;
    return {
      id,
      kind: 'tool',
      content: data.content,
      label: labelFor(event, 'tool'),
      ...(data.is_error ? {tone: 'failure' as const} : {}),
      ...agentFields,
      ...roundFields,
      turnId: invocationId ?? id,
      toolName: data.tool,
      toolResult: data,
      ...(data.call_id == null ? {} : {toolCallId: data.call_id}),
      ...(invocationId === undefined ? {} : {invocationId}),
    };
  }
  if (data?.kind === 'subprocess_output') {
    return {
      id,
      kind: 'subprocess',
      content: data.content,
      label: `${data.process_kind} · ${data.stream}`,
      ...agentFields,
      ...roundFields,
    };
  }
  if (event.type === 'phase_started') {
    return {
      id,
      kind: 'status',
      content: 'started',
      label: labelFor(event, 'phase'),
      ...agentFields,
      ...roundFields,
    };
  }
  if (data?.kind === 'judge_result') {
    return {
      id,
      kind: 'result',
      content: data.feedback || `Judge returned ${data.verdict}.`,
      label: `Judge · ${data.verdict.toUpperCase()}`,
      tone: data.verdict === 'pass' ? 'success' : 'failure',
      ...agentFields,
      ...roundFields,
    };
  }
  if (data?.kind === 'benchmark_result') {
    return {
      id,
      kind: 'result',
      content: `${data.metric}: ${data.value} ${data.unit}`,
      label: 'Benchmark',
      tone: 'success',
      ...agentFields,
      ...roundFields,
    };
  }
  if (data?.kind === 'round_finished') {
    const tone =
      data.judge_verdict === 'pass'
        ? 'success'
        : data.judge_verdict === 'fail'
          ? 'failure'
          : 'normal';
    return {
      id,
      kind: 'result',
      content: `${data.attempts} attempt(s)`,
      label: `${event.round_label ?? 'Round'} · ${data.judge_verdict.toUpperCase()}`,
      tone,
      ...agentFields,
      ...roundFields,
    };
  }
  if (event.type === 'run_failed' || event.type === 'run_interrupted') {
    const interrupted = event.type === 'run_interrupted';
    const interruption =
      data?.kind === 'run_interrupted'
        ? `${data.reason}${data.signal === null ? '' : ` (${data.signal})`}`
        : '';
    return {
      id,
      kind: 'result',
      content: event.text || interruption || (interrupted ? 'Run interrupted.' : 'Run failed.'),
      label: interrupted ? 'Run interrupted' : 'Run failed',
      tone: 'failure',
      ...agentFields,
      ...roundFields,
    };
  }
  return null;
}

function configurationFailureContent(data: {
  message: string;
  usage?: string | null;
  code: string;
  stage: string;
}): string {
  const sections = [data.message];
  if (data.usage) sections.push(data.usage);
  sections.push(`Code: ${data.code} · Stage: ${data.stage}`);
  return sections.join('\n\n');
}

function outputKind(channel: string): TranscriptEntry['kind'] {
  if (channel === 'assistant') return 'assistant';
  if (channel === 'prompt') return 'prompt';
  if (channel === 'analysis') return 'analysis';
  if (channel === 'tool') return 'tool';
  return 'diagnostic';
}

function labelFor(event: RunEvent, fallback: string): string {
  const phase = event.agent_kind ?? fallback;
  return event.round_label ? `${phase} · ${event.round_label}` : phase;
}

function appendTranscript(
  previous: TranscriptEntry[],
  incoming: TranscriptEntry,
): TranscriptEntry[] {
  if (incoming.kind === 'tool' && !incoming.startsTurn && incoming.toolName !== undefined) {
    const target = findToolCall(previous, incoming);
    if (target !== -1) {
      return previous.map((entry, index) =>
        index === target ? mergeToolResult(entry, incoming) : entry,
      );
    }
  }
  const last = previous.at(-1);
  if (
    last?.kind === 'tool' &&
    incoming.kind === 'tool' &&
    last.invocationId === incoming.invocationId &&
    !incoming.startsTurn
  ) {
    return [...previous.slice(0, -1), mergeToolResult(last, incoming)];
  }
  if (
    last !== undefined &&
    last.kind === incoming.kind &&
    last.turnId === incoming.turnId &&
    (incoming.kind === 'assistant' || incoming.kind === 'prompt' || incoming.kind === 'analysis')
  ) {
    return [...previous.slice(0, -1), {...last, content: last.content + incoming.content}];
  }
  return capTranscript([...previous, incoming]);
}

const MAX_TRANSCRIPT_ENTRIES = 20_000;

function capTranscript(entries: TranscriptEntry[]): TranscriptEntry[] {
  if (entries.length <= MAX_TRANSCRIPT_ENTRIES) return entries;
  const oldestRound = entries.find(entry => entry.roundNumber !== undefined)?.roundNumber;
  if (oldestRound === undefined) return entries.slice(-MAX_TRANSCRIPT_ENTRIES);
  const kept = entries.filter(
    entry => entry.roundNumber === undefined || entry.roundNumber > oldestRound,
  );
  return kept.length === entries.length ? entries.slice(-MAX_TRANSCRIPT_ENTRIES) : kept;
}

function findToolCall(previous: TranscriptEntry[], result: TranscriptEntry): number {
  const indices = Array.from(previous.keys());
  if (result.toolCallId !== undefined) indices.reverse();
  for (const index of indices) {
    const candidate = previous[index];
    if (
      candidate?.kind !== 'tool' ||
      (candidate.toolCall === undefined && candidate.toolArguments === undefined) ||
      candidate.toolResponse !== undefined ||
      candidate.toolResult !== undefined ||
      candidate.invocationId !== result.invocationId
    ) {
      continue;
    }
    if (result.toolCallId !== undefined) {
      if (candidate.toolCallId === result.toolCallId) return index;
    } else if (candidate.toolName === result.toolName) return index;
  }
  return -1;
}

function mergeToolResult(call: TranscriptEntry, result: TranscriptEntry): TranscriptEntry {
  if (call.toolArguments !== undefined && result.toolResult !== undefined) {
    return {
      ...call,
      content: result.toolResult.content,
      toolResult: result.toolResult,
      ...(result.tone === undefined ? {} : {tone: result.tone}),
    };
  }
  const separator = call.content.endsWith('\n') || result.content.startsWith('\n') ? '' : '\n';
  return {
    ...call,
    content: call.content + separator + result.content,
    toolResponse: (call.toolResponse ?? '') + (call.toolResponse ? separator : '') + result.content,
    ...(result.tone === undefined ? {} : {tone: result.tone}),
  };
}
