import {describe, expect, it} from 'bun:test';
import type {RunEvent} from '@vibesys/backend-client';
import {applyRunMapEvent, type RunMapState, roundAgentElapsedMs} from './run-map.js';

describe('run map projection', () => {
  it('tracks concurrent same-role executions independently', () => {
    let state = emptyRunMap();
    state = applyRunMapEvent(state, execution(1, 'agent_execution_started', 'a'));
    state = applyRunMapEvent(state, execution(2, 'agent_execution_started', 'b'));
    state = applyRunMapEvent(state, execution(3, 'agent_execution_finished', 'a'));

    expect(state.phases.filter(phase => phase.kind === 'implementer')).toMatchObject([
      {executionId: 'a', status: 'completed'},
      {executionId: 'b', status: 'active'},
    ]);
  });

  it('does not double count compatibility phase events', () => {
    let state = emptyRunMap();
    state = applyRunMapEvent(state, execution(1, 'agent_execution_started', 'a'));
    state = applyRunMapEvent(state, execution(2, 'phase_started', 'a'));
    state = applyRunMapEvent(state, execution(3, 'agent_execution_finished', 'a'));
    state = applyRunMapEvent(state, execution(4, 'phase_finished', 'a'));

    const round = requiredRound(state);
    expect(roundAgentElapsedMs(round, new Date('2026-01-01T00:00:05Z'))).toBe(2000);
  });

  it('closes active timing when a run is interrupted', () => {
    let state = applyRunMapEvent(emptyRunMap(), execution(1, 'agent_execution_started', 'a'));
    state = applyRunMapEvent(state, {
      ...execution(4, 'run_interrupted', 'a'),
      data: {kind: 'run_interrupted', reason: 'operator', signal: 'SIGINT'},
    });

    const round = requiredRound(state);
    expect(round.status).toBe('failed');
    expect(roundAgentElapsedMs(round, new Date('2026-01-01T00:00:10Z'))).toBe(3000);
  });
});

function emptyRunMap(): RunMapState {
  return {outerLoop: 'agent', rounds: [], phases: []};
}

function requiredRound(state: RunMapState): RunMapState['rounds'][number] {
  const round = state.rounds[0];
  if (round === undefined) throw new Error('Expected one projected round');
  return round;
}

function execution(sequence: number, type: RunEvent['type'], executionId: string): RunEvent {
  const started = type === 'agent_execution_started';
  return {
    sequence,
    timestamp: `2026-01-01T00:00:0${sequence}Z`,
    type,
    execution_id: executionId,
    invocation_id: executionId,
    agent_kind: 'implementer',
    round_label: 'round-1-implementer',
    ...(started
      ? {
          data: {
            kind: 'agent_execution_started',
            stage: 'implementation',
            attempt: 1,
            system_prompt: '',
            user_prompt: 'Implement',
            activity: {
              kind: 'agent_execution_activity_changed',
              mode: 'thinking',
              summary: 'Starting',
              tool: null,
            },
          },
        }
      : type === 'agent_execution_finished'
        ? {data: {kind: 'agent_execution_finished', error: null}}
        : {}),
  };
}
