import type {RunEvent} from '@vibesys/backend-client';
import {
  activeTimingElapsedMs,
  closeActiveAgentTimings,
  finishAgentTiming,
  type RoundTimingState,
  startAgentTiming,
} from './round-timing.js';

export type AgentPhaseStatus =
  | 'pending'
  | 'active'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'interrupted';
export type RoundStatus = 'active' | 'completed' | 'failed' | 'planned';

export interface RoundSummary extends RoundTimingState {
  number: number;
  status: RoundStatus;
  startedAt?: string;
  finishedAt?: string;
}

export interface AgentPhase {
  kind: string;
  status: AgentPhaseStatus;
  roundNumber: number | null;
  roundLabel: string | null;
  executionId?: string;
  invocationId?: string;
  startedAt?: string;
  finishedAt?: string;
  driver?: string | null;
  provider?: string | null;
  model?: string | null;
}

export interface RunMapState {
  outerLoop: string | null;
  rounds: RoundSummary[];
  phases: AgentPhase[];
}

export function applyRunMapEvent(state: RunMapState, event: RunEvent): RunMapState {
  const outerLoop =
    event.type === 'run_started' && event.data?.kind === 'run_started'
      ? event.data.outer_loop
      : state.outerLoop;
  const rounds = applyRoundEvent(state.rounds, state.phases, event);
  const phases = applyPhaseEvent({...state, outerLoop, rounds}, event);
  return {outerLoop, rounds, phases};
}

export function roundNumberFromLabel(label: string | null | undefined): number | null {
  if (!label) return null;
  const match = label.match(/(?:round|iter(?:ation)?)\D*(\d+)/i);
  return match ? Number(match[1]) : null;
}

export function phasesForRound(phases: AgentPhase[], roundNumber: number | null): AgentPhase[] {
  return phases.filter(phase => phase.roundNumber === roundNumber);
}

/**
 * Merges a round list folded from older events under one folded from newer
 * events, as a backfilled history prefix does.
 *
 * `mergeRound` already resolves every scalar the way replay would: the newer
 * patch wins, the earliest start survives. Agent timing is the exception.
 * Intervals recorded on either side are both real, so they concatenate instead
 * of last-write-wins, and open starts union.
 *
 * Known boundary: an agent execution whose start is in `older` and whose finish
 * is in `newer` loses its interval. The newer fold saw a finish with no start
 * and dropped it, and the finish timestamp is not recoverable from the merged
 * state. Rounds that do not straddle the boundary are exact.
 */
export function mergeRoundLists(
  older: readonly RoundSummary[],
  newer: readonly RoundSummary[],
): RoundSummary[] {
  const merged = new Map<number, RoundSummary>();
  for (const round of older) merged.set(round.number, round);
  for (const round of newer) {
    const existing = merged.get(round.number);
    merged.set(round.number, existing === undefined ? round : mergeRoundPrefix(existing, round));
  }
  return [...merged.values()].sort((left, right) => left.number - right.number);
}

/**
 * Merges a phase list folded from older events under one folded from newer
 * events.
 *
 * A phase is identified by role, round, and execution id. A newer phase that
 * carries an execution id lands on the matching older phase, else on the slot
 * the older fold seeded for that role, following `upsertPhase`'s precedence. A
 * newer phase with no execution id is a slot the newer fold seeded for itself;
 * replay would never have seeded it once the older phases existed, so it is
 * dropped when the older list already covers that role and round.
 */
export function mergePhaseLists(
  older: readonly AgentPhase[],
  newer: readonly AgentPhase[],
): AgentPhase[] {
  const merged = [...older];
  for (const phase of newer) {
    const target = prefixPhaseTarget(merged, phase);
    const existing = merged[target];
    if (existing !== undefined) {
      merged[target] = mergePhase(existing, phase);
      continue;
    }
    if (phase.executionId === undefined && merged.some(candidate => sameSlot(candidate, phase))) {
      continue;
    }
    merged.push(phase);
  }
  return merged;
}

function mergeRoundPrefix(older: RoundSummary, newer: RoundSummary): RoundSummary {
  const round = mergeRound(older, newer);
  const agentIntervals =
    older.agentIntervals === undefined && newer.agentIntervals === undefined
      ? undefined
      : [...(older.agentIntervals ?? []), ...(newer.agentIntervals ?? [])];
  const activeAgentStarts =
    older.activeAgentStarts === undefined && newer.activeAgentStarts === undefined
      ? undefined
      : {...older.activeAgentStarts, ...newer.activeAgentStarts};
  return {
    ...round,
    ...(agentIntervals === undefined ? {} : {agentIntervals}),
    ...(activeAgentStarts === undefined ? {} : {activeAgentStarts}),
  };
}

function sameSlot(phase: AgentPhase, patch: AgentPhase): boolean {
  return phase.kind === patch.kind && phase.roundNumber === patch.roundNumber;
}

/** Where `patch` lands in `phases` under a prefix merge, or -1 to append. */
function prefixPhaseTarget(phases: readonly AgentPhase[], patch: AgentPhase): number {
  if (patch.executionId === undefined) return -1;
  const byExecution = phases.findIndex(
    phase => sameSlot(phase, patch) && phase.executionId === patch.executionId,
  );
  if (byExecution !== -1) return byExecution;
  const seeded = phases.findIndex(
    phase => sameSlot(phase, patch) && phase.executionId === undefined,
  );
  if (seeded !== -1) return seeded;
  return phases.findIndex(phase => sameSlot(phase, patch) && phase.status === 'active');
}

export function roundAgentElapsedMs(round: RoundSummary, now: Date): number {
  return activeTimingElapsedMs(round, now);
}

function applyPhaseEvent(state: RunMapState, event: RunEvent): AgentPhase[] {
  const kind = event.agent_kind;
  if (!kind) return state.phases;
  const roundNumber = roundNumberFromLabel(event.round_label);
  let phases = state.phases;
  if (roundNumber !== null && state.outerLoop !== null) {
    phases = seedExpectedPhases(state.outerLoop, phases, roundNumber);
  }
  if (event.type === 'run_failed' || event.type === 'run_interrupted') {
    return phases.map(phase =>
      phase.roundNumber === roundNumber && phase.status === 'active'
        ? {...phase, status: 'failed', finishedAt: event.timestamp}
        : phase,
    );
  }
  const started = event.type === 'agent_execution_started' || event.type === 'phase_started';
  const finished = event.type === 'agent_execution_finished' || event.type === 'phase_finished';
  if (!started && !finished) return ensurePhase(phases, kind, roundNumber);
  const executionId = event.execution_id ?? event.invocation_id ?? undefined;
  const data = event.data;
  const runtime =
    started && data?.kind === 'agent_execution_started'
      ? {driver: data.driver ?? null, provider: data.provider ?? null, model: data.model ?? null}
      : {};
  return upsertPhase(phases, {
    kind,
    status: started ? 'active' : terminalPhaseStatus(event.status),
    roundNumber,
    roundLabel: event.round_label ?? null,
    ...(executionId ? {executionId, invocationId: executionId} : {}),
    ...(started ? {startedAt: event.timestamp} : {finishedAt: event.timestamp}),
    ...runtime,
  });
}

function terminalPhaseStatus(status: RunEvent['status']): AgentPhaseStatus {
  if (status === 'failed') return 'failed';
  if (status === 'cancelled') return 'cancelled';
  if (status === 'interrupted') return 'interrupted';
  return 'completed';
}

function applyRoundEvent(
  rounds: RoundSummary[],
  phases: AgentPhase[],
  event: RunEvent,
): RoundSummary[] {
  const number = roundNumberFromLabel(event.round_label);
  if (number === null || event.type === 'run_finished') return rounds;
  const existing = rounds.find(round => round.number === number);
  const terminalFailure = event.type === 'run_failed' || event.type === 'run_interrupted';
  const status = terminalFailure
    ? 'failed'
    : event.type === 'round_finished'
      ? event.status === 'failed'
        ? 'failed'
        : 'completed'
      : existing?.status === 'completed' || existing?.status === 'failed'
        ? existing.status
        : 'active';
  const terminal = terminalFailure || event.type === 'round_finished';
  const patch: RoundSummary = {
    number,
    status,
    ...(terminal ? {finishedAt: event.timestamp} : {startedAt: event.timestamp}),
  };
  const round = existing ? mergeRound(existing, patch) : patch;
  return replaceRound(rounds, updateRoundAgentElapsed(round, phases, event));
}

function seedExpectedPhases(
  outerLoop: string,
  current: AgentPhase[],
  roundNumber: number,
): AgentPhase[] {
  let phases = current;
  for (const kind of expectedRoles(outerLoop)) {
    phases = ensurePhase(phases, kind, roundNumber);
  }
  return phases;
}

function expectedRoles(outerLoop: string): string[] {
  if (outerLoop === 'agent') return ['orchestrator', 'implementer', 'judge', 'profiler'];
  if (outerLoop === 'plain') return ['implementer', 'judge', 'perf_eval'];
  if (outerLoop === 'evolve') return ['implementer', 'judge', 'profiler'];
  return [];
}

function ensurePhase(phases: AgentPhase[], kind: string, roundNumber: number | null): AgentPhase[] {
  if (phases.some(phase => phase.kind === kind && phase.roundNumber === roundNumber)) return phases;
  return [...phases, {kind, status: 'pending', roundNumber, roundLabel: null}];
}

function upsertPhase(phases: AgentPhase[], patch: AgentPhase): AgentPhase[] {
  const sameRoleAndRound = (phase: AgentPhase): boolean =>
    phase.kind === patch.kind && phase.roundNumber === patch.roundNumber;
  let existing =
    patch.executionId === undefined
      ? -1
      : phases.findIndex(
          phase => sameRoleAndRound(phase) && phase.executionId === patch.executionId,
        );
  if (existing === -1 && patch.status === 'active') {
    existing = phases.findIndex(
      phase => sameRoleAndRound(phase) && phase.executionId === undefined,
    );
  }
  if (existing === -1 && patch.status !== 'active') {
    existing = phases.findIndex(phase => sameRoleAndRound(phase) && phase.status === 'active');
  }
  if (existing === -1) return [...phases, patch];
  return phases.map((phase, index) => (index === existing ? mergePhase(phase, patch) : phase));
}

/** Applies `patch` to `phase`, keeping the identity and endpoints it already has. */
function mergePhase(phase: AgentPhase, patch: AgentPhase): AgentPhase {
  return {
    ...phase,
    ...patch,
    ...(phase.executionId !== undefined && phase.executionId !== patch.executionId
      ? {executionId: phase.executionId, invocationId: phase.invocationId}
      : {}),
    ...((patch.startedAt ?? phase.startedAt)
      ? {startedAt: patch.startedAt ?? phase.startedAt}
      : {}),
    ...((patch.finishedAt ?? phase.finishedAt)
      ? {finishedAt: patch.finishedAt ?? phase.finishedAt}
      : {}),
  };
}

function replaceRound(rounds: RoundSummary[], round: RoundSummary): RoundSummary[] {
  const existing = rounds.findIndex(item => item.number === round.number);
  if (existing === -1) return [...rounds, round].sort((left, right) => left.number - right.number);
  return rounds.map((item, index) => (index === existing ? round : item));
}

function mergeRound(round: RoundSummary, patch: RoundSummary): RoundSummary {
  const startedAt = earliestTimestamp(round.startedAt, patch.startedAt);
  return {
    ...round,
    ...patch,
    ...(startedAt ? {startedAt} : {}),
    ...((patch.finishedAt ?? round.finishedAt)
      ? {finishedAt: patch.finishedAt ?? round.finishedAt}
      : {}),
    ...((patch.agentIntervals ?? round.agentIntervals)
      ? {agentIntervals: patch.agentIntervals ?? round.agentIntervals}
      : {}),
    ...((patch.activeAgentStarts ?? round.activeAgentStarts)
      ? {activeAgentStarts: patch.activeAgentStarts ?? round.activeAgentStarts}
      : {}),
  };
}

function earliestTimestamp(
  left: string | undefined,
  right: string | undefined,
): string | undefined {
  if (!left) return right;
  if (!right) return left;
  return new Date(right).getTime() < new Date(left).getTime() ? right : left;
}

function updateRoundAgentElapsed(
  round: RoundSummary,
  phases: AgentPhase[],
  event: RunEvent,
): RoundSummary {
  const started = event.type === 'agent_execution_started' || event.type === 'phase_started';
  const finished = event.type === 'agent_execution_finished' || event.type === 'phase_finished';
  if (!started && !finished) {
    if (
      event.type !== 'round_finished' &&
      event.type !== 'run_failed' &&
      event.type !== 'run_interrupted'
    ) {
      return round;
    }
    return closeActiveAgentTimings(round, event.timestamp);
  }
  if (event.type === 'phase_started' || event.type === 'phase_finished') {
    const executionId = event.execution_id ?? event.invocation_id;
    const existing = phases.find(
      phase =>
        executionId != null &&
        phase.executionId === executionId &&
        phase.kind === event.agent_kind &&
        phase.roundNumber === roundNumberFromLabel(event.round_label),
    );
    if (
      (started && existing?.status === 'active') ||
      (finished && existing !== undefined && existing.status !== 'active')
    ) {
      return round;
    }
  }
  return started ? startAgentTiming(round, event) : finishAgentTiming(round, event);
}
