import {describe, expect, it} from 'bun:test';
import type {ActiveAgentExecution} from '../session-model.js';
import {activitySummary} from './activity-bar.js';

const STARTED_AT = '2026-08-25T12:00:00.000Z';
function execution(
  activity: ActiveAgentExecution['activity'],
  executionId = 'execution-1',
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
