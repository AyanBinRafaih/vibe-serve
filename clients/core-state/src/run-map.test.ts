import {describe, expect, it} from 'bun:test';
import type {RunEvent} from '@vibesys/backend-client';
import {
  applyRunMapEvent,
  expectedRolesForSeeding,
  type RunMapState,
  roundAgentElapsedMs,
} from './run-map.js';

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

  it('captures the agent runtime identity on start and keeps it after finish', () => {
    let state = applyRunMapEvent(emptyRunMap(), {
      ...execution(1, 'agent_execution_started', 'a'),
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
        driver: 'agentshim',
        provider: 'codex',
        model: 'gpt-5.1-codex-max',
      },
    });
    state = applyRunMapEvent(state, execution(2, 'agent_execution_finished', 'a'));

    expect(state.phases.filter(phase => phase.kind === 'implementer')).toMatchObject([
      {
        executionId: 'a',
        status: 'completed',
        driver: 'agentshim',
        provider: 'codex',
        model: 'gpt-5.1-codex-max',
      },
    ]);
  });

  it('defaults the runtime identity to null when the event omits it', () => {
    const state = applyRunMapEvent(emptyRunMap(), execution(1, 'agent_execution_started', 'a'));

    expect(state.phases.filter(phase => phase.kind === 'implementer')).toMatchObject([
      {driver: null, provider: null, model: null},
    ]);
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

describe('expected phase seeding', () => {
  it('seeds pending placeholders for every advertised role, even ones the legacy table never knew', () => {
    let state = applyRunMapEvent(
      initialRunMap(),
      runStarted('swarm', ['scout', 'implementer', 'reviewer']),
    );
    state = applyRunMapEvent(state, execution(2, 'agent_execution_started', 'a'));

    expect(state.phases.map(phase => [phase.kind, phase.status])).toEqual([
      ['scout', 'pending'],
      ['implementer', 'active'],
      ['reviewer', 'pending'],
    ]);
  });

  it('lets a known loop advertise a role the legacy table lacks without a client edit', () => {
    let state = applyRunMapEvent(
      initialRunMap(),
      runStarted('agent', ['orchestrator', 'implementer', 'judge', 'profiler', 'benchmark']),
    );
    state = applyRunMapEvent(state, execution(2, 'agent_execution_started', 'a'));

    expect(state.phases.map(phase => phase.kind)).toEqual([
      'orchestrator',
      'implementer',
      'judge',
      'profiler',
      'benchmark',
    ]);
  });

  it('falls back to the legacy table for a run_started without advertised roles', () => {
    let state = applyRunMapEvent(initialRunMap(), runStarted('plain'));
    state = applyRunMapEvent(state, execution(2, 'agent_execution_started', 'a'));

    expect(state.phases.map(phase => phase.kind)).toEqual(['implementer', 'judge', 'perf_eval']);
  });

  it('treats an empty advertised list as absent and uses the fallback', () => {
    let state = applyRunMapEvent(initialRunMap(), runStarted('plain', []));
    state = applyRunMapEvent(state, execution(2, 'agent_execution_started', 'a'));

    expect(state.phases.map(phase => phase.kind)).toEqual(['implementer', 'judge', 'perf_eval']);
  });

  it('still tracks observed phases for an unknown loop with no advertised roles', () => {
    let state = applyRunMapEvent(initialRunMap(), runStarted('mystery'));
    state = applyRunMapEvent(state, execution(2, 'agent_execution_started', 'a'));

    // The graceful-degradation contract: no role set is known, so nothing is
    // seeded, and consumers can observe the condition through the selector.
    expect(expectedRolesForSeeding(state)).toBeNull();
    expect(state.phases.map(phase => [phase.kind, phase.status])).toEqual([
      ['implementer', 'active'],
    ]);
  });

  it('folds the advertised roles onto the state for consumers and prefix merges', () => {
    const state = applyRunMapEvent(initialRunMap(), runStarted('plain', ['implementer', 'judge']));

    expect(state.expectedRoles).toEqual(['implementer', 'judge']);
    expect(expectedRolesForSeeding(state)).toEqual(['implementer', 'judge']);
  });
});

function initialRunMap(): RunMapState {
  return {outerLoop: null, expectedRoles: null, rounds: [], phases: []};
}

function runStarted(outerLoop: string, expectedRoles?: string[]): RunEvent {
  return {
    sequence: 1,
    timestamp: '2026-01-01T00:00:00Z',
    type: 'run_started',
    status: 'active',
    data: {
      kind: 'run_started',
      outer_loop: outerLoop,
      input: '/target',
      max_rounds: 3,
      ...(expectedRoles === undefined ? {} : {expected_roles: expectedRoles}),
    },
  };
}

function emptyRunMap(): RunMapState {
  return {outerLoop: 'agent', expectedRoles: null, rounds: [], phases: []};
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
