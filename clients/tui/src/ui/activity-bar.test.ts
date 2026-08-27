import {describe, expect, it} from 'bun:test';
import type {ActiveAgentExecution} from '@vibesys/core-state';
import {activitySummary, runtimeSuffix} from './activity-bar.js';

const STARTED_AT = '2026-08-25T12:00:00.000Z';
function execution(
  activity: ActiveAgentExecution['activity'],
  executionId = 'execution-1',
  runtime: {provider?: string | null; model?: string | null} = {},
): ActiveAgentExecution {
  return {
    executionId,
    agentKind: 'implementer',
    roundLabel: 'round-1-implementer',
    roundNumber: 1,
    stage: 'implementation',
    attempt: 1,
    assignment: 'Implement the queue',
    startedAt: STARTED_AT,
    activity,
    ...runtime,
  };
}

describe('activity summary', () => {
  it('uses Working for every activity update', () => {
    expect(activitySummary(execution({mode: 'thinking', summary: ''}))).toBe('Working');
    expect(activitySummary(execution({mode: 'thinking', summary: 'Planning'}))).toBe('Working');
    expect(activitySummary(execution({mode: 'responding', summary: 'Writing a response'}))).toBe(
      'Working',
    );
    expect(
      activitySummary(execution({mode: 'tool', summary: 'Running queue tests', tool: 'Bash'})),
    ).toBe('Working');
    expect(activitySummary(execution({mode: 'waiting', summary: 'Waiting for output'}))).toBe(
      'Working',
    );
  });
});

describe('runtime suffix', () => {
  it('renders the harness and model when both are known', () => {
    const exec = execution({mode: 'thinking', summary: ''}, 'execution-1', {
      provider: 'codex',
      model: 'gpt-5.1-codex-max',
    });
    expect(runtimeSuffix(exec)).toBe(' · Codex (GPT 5.1 Codex Max)');
  });

  it('is empty when neither the provider nor the model is known', () => {
    expect(runtimeSuffix(execution({mode: 'thinking', summary: ''}))).toBe('');
  });
});
