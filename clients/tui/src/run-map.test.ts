import {describe, expect, it} from 'bun:test';
import type {RunEvent} from './protocol.js';
import {applyRunMapEvent, type RunMapState, roundAgentElapsedMs} from './run-map.js';

describe('run map round timing', () => {
  it('stops counting when an agent phase finishes', () => {
    let state = initialRunMapState();
    state = applyRunMapEvent(state, phaseEvent(1, 'phase_started', '2026-01-01T00:00:00Z'));
    state = applyRunMapEvent(state, phaseEvent(2, 'phase_finished', '2026-01-01T00:00:05Z'));

    expect(roundAgentElapsedMs(onlyRound(state), new Date('2026-01-01T00:01:00Z'))).toBe(5000);
  });

  it('closes a phase by agent role when the exact invocation key is absent', () => {
    let state = initialRunMapState();
    state = applyRunMapEvent(state, phaseEvent(1, 'phase_started', '2026-01-01T00:00:00Z'));
    state = applyRunMapEvent(
      state,
      phaseEvent(2, 'phase_finished', '2026-01-01T00:00:05Z', 'finish-only'),
    );

    expect(state.rounds[0]?.activeAgentStarts).toEqual({});
    expect(roundAgentElapsedMs(onlyRound(state), new Date('2026-01-01T00:01:00Z'))).toBe(5000);
    expect(state.phases[0]?.executionId).toBe('invocation-1');
  });

  it('does not reopen a completed round when the run finishes', () => {
    let state = initialRunMapState();
    state = applyRunMapEvent(state, phaseEvent(1, 'phase_started', '2026-01-01T00:00:00Z'));
    state = applyRunMapEvent(state, phaseEvent(2, 'phase_finished', '2026-01-01T00:00:05Z'));
    state = applyRunMapEvent(state, {
      ...phaseEvent(3, 'phase_finished', '2026-01-01T00:00:06Z'),
      type: 'round_finished',
      status: 'completed',
    });
    state = applyRunMapEvent(state, {
      ...phaseEvent(4, 'phase_finished', '2026-01-01T00:00:07Z'),
      type: 'run_finished',
      status: 'completed',
    });

    expect(onlyRound(state).status).toBe('completed');
  });

  it('fails the active round and phase when the run fails', () => {
    let state = initialRunMapState();
    state = applyRunMapEvent(state, phaseEvent(1, 'phase_started', '2026-01-01T00:00:00Z'));
    state = applyRunMapEvent(state, {
      ...phaseEvent(2, 'phase_finished', '2026-01-01T00:00:05Z'),
      type: 'run_failed',
      status: 'failed',
    });

    expect(onlyRound(state).status).toBe('failed');
    expect(state.rounds[0]?.activeAgentStarts).toEqual({});
    expect(state.phases[0]?.status).toBe('failed');
  });

  it('keeps concurrent same-role executions distinct until each one finishes', () => {
    let state = initialRunMapState();
    state = applyRunMapEvent(
      state,
      executionEvent(1, 'agent_execution_started', 'impl-a', 'active'),
    );
    state = applyRunMapEvent(
      state,
      executionEvent(2, 'agent_execution_started', 'impl-b', 'active'),
    );
    state = applyRunMapEvent(
      state,
      executionEvent(3, 'agent_execution_finished', 'impl-a', 'completed'),
    );

    expect(state.phases.map(phase => [phase.executionId, phase.status])).toEqual([
      ['impl-a', 'completed'],
      ['impl-b', 'active'],
    ]);
    expect(state.rounds[0]?.activeAgentStarts).toEqual({
      'implementer:impl-b': '2026-01-01T00:00:02Z',
    });
  });

  it('does not let compatibility phase events close a concurrent execution', () => {
    let state = initialRunMapState();
    state = applyRunMapEvent(
      state,
      executionEvent(1, 'agent_execution_started', 'impl-a', 'active'),
    );
    state = applyRunMapEvent(
      state,
      phaseEvent(2, 'phase_started', '2026-01-01T00:00:02Z', 'impl-a'),
    );
    state = applyRunMapEvent(
      state,
      executionEvent(3, 'agent_execution_started', 'impl-b', 'active'),
    );
    state = applyRunMapEvent(
      state,
      phaseEvent(4, 'phase_started', '2026-01-01T00:00:04Z', 'impl-b'),
    );
    state = applyRunMapEvent(
      state,
      executionEvent(5, 'agent_execution_finished', 'impl-a', 'completed'),
    );
    state = applyRunMapEvent(
      state,
      phaseEvent(6, 'phase_finished', '2026-01-01T00:00:06Z', 'impl-a'),
    );

    expect(state.rounds[0]?.activeAgentStarts).toEqual({
      'implementer:impl-b': '2026-01-01T00:00:03Z',
    });
  });

  it('preserves cancelled and interrupted execution outcomes', () => {
    let state = initialRunMapState();
    state = applyRunMapEvent(
      state,
      executionEvent(1, 'agent_execution_started', 'impl-a', 'active'),
    );
    state = applyRunMapEvent(
      state,
      executionEvent(2, 'agent_execution_finished', 'impl-a', 'cancelled'),
    );
    state = applyRunMapEvent(
      state,
      executionEvent(3, 'agent_execution_started', 'impl-b', 'active'),
    );
    state = applyRunMapEvent(
      state,
      executionEvent(4, 'agent_execution_finished', 'impl-b', 'interrupted'),
    );

    expect(state.phases.map(phase => phase.status)).toEqual(['cancelled', 'interrupted']);
  });
});

function initialRunMapState(): RunMapState {
  return {outerLoop: null, rounds: [], phases: []};
}

function onlyRound(state: RunMapState) {
  expect(state.rounds).toHaveLength(1);
  return state.rounds[0] as NonNullable<(typeof state.rounds)[0]>;
}

function phaseEvent(
  sequence: number,
  type: 'phase_started' | 'phase_finished',
  timestamp: string,
  invocationId = 'invocation-1',
): RunEvent {
  return {
    sequence,
    timestamp,
    type,
    round_label: 'round-1',
    agent_kind: 'implementer',
    ...(invocationId === undefined ? {} : {invocation_id: invocationId}),
  };
}

function executionEvent(
  sequence: number,
  type: 'agent_execution_started' | 'agent_execution_finished',
  executionId: string,
  status: NonNullable<RunEvent['status']>,
): RunEvent {
  return {
    sequence,
    timestamp: `2026-01-01T00:00:0${sequence}Z`,
    type,
    status,
    round_label: 'round-1',
    agent_kind: 'implementer',
    execution_id: executionId,
  };
}
