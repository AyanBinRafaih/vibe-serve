import {describe, expect, it} from 'bun:test';
import type {RunEvent, RunSnapshot} from '@vibesys/backend-client';
import {
  initialCoreState,
  latestDiagnosticChange,
  reconcileActiveExecutions,
  reduceEvent,
  reduceEventBatch,
  reduceSnapshot,
} from './core-state.js';

describe('core state projection', () => {
  it('projects snapshots without changing event-derived history', () => {
    const prior = reduceEvent(initialCoreState(), outputEvent(4, 'kept'));
    const snapshot = {
      run_id: 'run',
      status: 'running',
      sequence: 9,
      agent_kind: 'judge',
      round_label: 'round-2-judge',
      active_executions: [checkpoint('exec-1')],
    } satisfies RunSnapshot;

    const state = reduceSnapshot(prior, snapshot);

    expect(state.sequence).toBe(4);
    expect(state.transcript.map(entry => entry.content)).toEqual(['kept']);
    expect(state.agentKind).toBe('judge');
    expect(state.activeExecutions['exec-1']?.roundNumber).toBe(2);
  });

  it('rejects a snapshot older than the projected event cursor', () => {
    const current = reduceEvent(initialCoreState(), outputEvent(5, 'current'));
    const stale = {
      run_id: 'run',
      status: 'running',
      sequence: 4,
      agent_kind: 'judge',
      round_label: 'round-2-judge',
      active_executions: [checkpoint('stale')],
    } satisfies RunSnapshot;

    expect(reduceSnapshot(current, stale)).toBe(current);
  });

  it('ignores duplicate replay events', () => {
    const once = reduceEvent(initialCoreState(), outputEvent(1, 'one'));
    const replayed = reduceEvent(once, outputEvent(1, 'duplicate'));

    expect(replayed).toBe(once);
    expect(replayed.transcript.map(entry => entry.content)).toEqual(['one']);
  });

  it('applies event batches before reconciling their execution checkpoint', () => {
    const started = executionEvent(1, 'agent_execution_started', 'stale', {
      kind: 'agent_execution_started',
      stage: 'implementation',
      attempt: 1,
      system_prompt: '',
      user_prompt: 'Implement the queue',
      activity: {
        kind: 'agent_execution_activity_changed',
        mode: 'thinking',
        summary: 'Inspecting',
        tool: null,
      },
    });

    const state = reduceEventBatch(initialCoreState(), [started, outputEvent(2, 'done')], [], 2);

    expect(state.sequence).toBe(2);
    expect(state.activeExecutions).toEqual({});
    expect(state.transcript.at(-1)?.content).toBe('done');
  });

  it('rejects an older execution checkpoint', () => {
    const current = reduceEvent(initialCoreState(), outputEvent(5, 'current'));

    expect(reconcileActiveExecutions(current, [checkpoint('stale')], 4)).toBe(current);
  });

  it('tracks concurrent executions independently through activity and completion', () => {
    let state = initialCoreState();
    state = reduceEvent(
      state,
      executionEvent(1, 'agent_execution_started', 'first', startedData('First')),
    );
    state = reduceEvent(
      state,
      executionEvent(2, 'agent_execution_started', 'second', startedData('Second')),
    );
    state = reduceEvent(
      state,
      executionEvent(3, 'agent_execution_activity_changed', 'second', {
        kind: 'agent_execution_activity_changed',
        mode: 'tool',
        summary: 'Running tests',
        tool: 'Bash',
      }),
    );
    state = reduceEvent(
      state,
      executionEvent(4, 'agent_execution_finished', 'first', {
        kind: 'agent_execution_finished',
        error: null,
      }),
    );

    expect(Object.keys(state.activeExecutions)).toEqual(['second']);
    expect(state.activeExecutions['second']?.activity).toEqual({
      mode: 'tool',
      summary: 'Running tests',
      tool: 'Bash',
    });
  });

  it('captures runtime identity from agent_execution_started when present', () => {
    const state = reduceEvent(
      initialCoreState(),
      executionEvent(1, 'agent_execution_started', 'first', {
        ...startedData('Implement the queue'),
        driver: 'agentshim',
        provider: 'codex',
        model: 'gpt-5.1-codex-max',
      }),
    );

    expect(state.activeExecutions['first']).toMatchObject({
      driver: 'agentshim',
      provider: 'codex',
      model: 'gpt-5.1-codex-max',
    });
  });

  it('defaults runtime identity to null when the event omits it', () => {
    const state = reduceEvent(
      initialCoreState(),
      executionEvent(1, 'agent_execution_started', 'first', startedData('Implement the queue')),
    );

    expect(state.activeExecutions['first']).toMatchObject({
      driver: null,
      provider: null,
      model: null,
    });
  });

  it('coalesces streamed assistant chunks by invocation', () => {
    let state = initialCoreState();
    state = reduceEvent(state, outputEvent(1, 'hello ', 'turn-1'));
    state = reduceEvent(state, outputEvent(2, 'world', 'turn-1'));
    state = reduceEvent(state, outputEvent(3, 'separate', 'turn-2'));

    expect(state.transcript.map(entry => entry.content)).toEqual(['hello world', 'separate']);
  });

  it('correlates parallel tool results by call id', () => {
    let state = initialCoreState();
    state = reduceEvent(state, toolEvent(1, 'tool_call', 'call-a', 'first'));
    state = reduceEvent(state, toolEvent(2, 'tool_call', 'call-b', 'second'));
    state = reduceEvent(state, toolEvent(3, 'tool_result', 'call-b', 'second result'));
    state = reduceEvent(state, toolEvent(4, 'tool_result', 'call-a', 'first result'));

    expect(state.transcript).toHaveLength(2);
    expect(state.transcript[0]?.toolResult?.content).toBe('first result');
    expect(state.transcript[1]?.toolResult?.content).toBe('second result');
  });

  it('retains typed tool arguments and results without presentation loss', () => {
    const arguments_ = {
      text: 'x'.repeat(200),
      nested: {items: [1, {enabled: true, labels: ['alpha', 'beta']}]},
    };
    let state = reduceEvent(initialCoreState(), {
      ...baseEvent(1, 'tool_call'),
      invocation_id: 'turn',
      data: {kind: 'tool_call', tool: 'Edit', call_id: 'call-long', args: arguments_},
    });
    state = reduceEvent(state, {
      ...baseEvent(2, 'tool_result'),
      invocation_id: 'turn',
      data: {
        kind: 'tool_result',
        tool: 'Edit',
        call_id: 'call-long',
        content: 'result '.repeat(40),
        is_error: true,
      },
    });

    expect(state.transcript[0]?.toolArguments).toEqual(arguments_);
    expect(state.transcript[0]?.toolResult).toEqual({
      kind: 'tool_result',
      tool: 'Edit',
      call_id: 'call-long',
      content: 'result '.repeat(40),
      is_error: true,
    });
    expect(state.transcript[0]?.toolCall).toBeUndefined();
    expect(state.transcript[0]?.toolResponse).toBeUndefined();
  });

  it('carries the typed result payload onto the merged transcript entry', () => {
    let state = reduceEvent(initialCoreState(), {
      ...baseEvent(1, 'tool_call'),
      invocation_id: 'turn',
      data: {kind: 'tool_call', tool: 'shell', call_id: 'call-1', args: {cmd: 'ls'}},
    });
    state = reduceEvent(state, {
      ...baseEvent(2, 'tool_result'),
      invocation_id: 'turn',
      data: {
        kind: 'tool_result',
        tool: 'shell',
        call_id: 'call-1',
        content: 'file.txt',
        payload: {kind: 'command', stdout: 'file.txt', stderr: '', exit_code: 0, duration: 0.1},
      },
    });

    expect(state.transcript).toHaveLength(1);
    expect(state.transcript[0]?.toolResult?.payload).toEqual({
      kind: 'command',
      stdout: 'file.txt',
      stderr: '',
      exit_code: 0,
      duration: 0.1,
    });
  });

  it('keeps chat-agent events out of the experiment transcript', () => {
    const chat = {
      ...outputEvent(1, 'answer'),
      agent_kind: 'chat',
      round_label: 'experiment-chat',
    } satisfies RunEvent;

    const state = reduceEvent(initialCoreState(), chat);

    expect(state.transcript).toEqual([]);
    expect(state.chatTranscript.map(entry => entry.content)).toEqual(['answer']);
  });

  it('partitions chat transcripts by thread, defaulting unstamped events', () => {
    let state = initialCoreState();
    state = reduceEvent(state, chatAnswerEvent(1, 'default answer'));
    state = reduceEvent(state, chatAnswerEvent(2, 'thread answer', 'thread-a'));

    // Neither thread sees the other's answer, and unstamped events land on
    // the default thread so pre-thread logs replay unchanged.
    expect(state.chatTranscripts['default']?.map(entry => entry.content)).toEqual([
      'default answer',
    ]);
    expect(state.chatTranscripts['thread-a']?.map(entry => entry.content)).toEqual([
      'thread answer',
    ]);
    // The legacy selector still reads the default thread.
    expect(state.chatTranscript.map(entry => entry.content)).toEqual(['default answer']);
    expect(state.transcript).toEqual([]);
  });

  it('replays the thread list from creation events after the implicit default', () => {
    let state = initialCoreState();
    state = reduceEvent(state, threadCreatedEvent(1, 'thread-a', 'claude'));
    state = reduceEvent(state, threadCreatedEvent(2, 'thread-b', 'codex'));

    expect(state.chatThreads.map(thread => thread.id)).toEqual(['default', 'thread-a', 'thread-b']);
    // The implicit default carries no backend title; consumers name it.
    expect(state.chatThreads[0]).toMatchObject({title: '', driver: null, provider: null});
    expect(state.chatThreads[1]).toMatchObject({
      title: '',
      driver: 'agentshim',
      provider: 'claude',
      model: 'opus',
    });
    // A created thread has a transcript from the start, even before it talks.
    expect(state.chatTranscripts['thread-b']).toEqual([]);
  });

  it('adopts the backend-derived title carried on a chat event', () => {
    let state = initialCoreState();
    state = reduceEvent(state, threadCreatedEvent(1, 'thread-a', 'claude'));
    state = reduceEvent(state, {
      ...chatAnswerEvent(2, 'first answer', 'thread-a'),
      data: {kind: 'chat', answer: 'first answer', thread_title: 'why did r2 regress'},
    });

    expect(state.chatThreads.find(thread => thread.id === 'thread-a')?.title).toBe(
      'why did r2 regress',
    );
  });

  it('names a thread from a titled turn even when its creation replayed away', () => {
    const state = reduceEvent(initialCoreState(), {
      ...chatAnswerEvent(1, 'answer', 'thread-x'),
      data: {kind: 'chat', answer: 'answer', thread_title: 'orphan thread'},
    });

    expect(state.chatThreads.find(thread => thread.id === 'thread-x')?.title).toBe('orphan thread');
    expect(state.chatTranscripts['thread-x']?.map(entry => entry.content)).toEqual(['answer']);
  });

  it('drops legacy chat tool chunks per thread once typed events appear', () => {
    let state = initialCoreState();
    state = reduceEvent(state, {
      ...baseEvent(1, 'tool_call'),
      agent_kind: 'chat',
      chat_thread_id: 'thread-a',
      data: {kind: 'tool_call', tool: 'read_file', args: {}},
    });
    // The default thread saw no typed events, so its legacy chunks survive.
    state = reduceEvent(state, {
      ...outputEvent(2, 'legacy default output'),
      agent_kind: 'chat',
    });

    expect(state.chatTypedToolEvents).toEqual({'thread-a': true});
    expect(state.chatTranscript.map(entry => entry.content)).toEqual(['legacy default output']);
  });

  it('scopes todo snapshots by execution', () => {
    let state = initialCoreState();
    state = reduceEvent(state, todoEvent(1, 'exec-a', 'first'));
    state = reduceEvent(state, todoEvent(2, 'exec-b', 'second'));
    state = reduceEvent(state, todoEvent(3, 'exec-a', 'updated'));

    expect(state.todos).toMatchObject([
      {executionId: 'exec-b', items: [{content: 'second'}]},
      {executionId: 'exec-a', items: [{content: 'updated'}]},
    ]);
  });

  it('retains semantic benchmark data independently of rendered charts', () => {
    const state = reduceEvent(initialCoreState(), {
      ...baseEvent(8, 'benchmark_result'),
      data: {kind: 'benchmark_result', metric: 'ops', value: 42, unit: 'ops/s'},
    });

    expect(state.benchmarks).toEqual([
      {sequence: 8, roundNumber: 1, metric: 'ops', value: 42, unit: 'ops/s'},
    ]);
  });

  it('records structured diagnostics as durable facts', () => {
    const state = reduceEvent(initialCoreState(), {
      ...baseEvent(3, 'run_failed'),
      diagnostic: {
        id: 'diag-1',
        code: 'agent_failed',
        summary: 'Agent failed.',
        detail: 'Exit 2',
        hint: 'Retry.',
        scope: 'run',
        severity: 'fatal',
        retryability: 'manual',
        cause_id: null,
        debug_ref: null,
      },
    });

    expect(state.terminal).toBe(true);
    expect(state.diagnostics).toMatchObject([
      {id: 'diag-1', summary: 'Agent failed.', severity: 'fatal', sequence: 3},
    ]);
  });

  it('promotes a repeated diagnostic id with richer terminal detail', () => {
    const initialFailure = reduceEvent(
      initialCoreState(),
      diagnosticEvent(1, 'invocation_finished', 'diag-1', 'error', 'Agent failed.', {
        invocationId: 'invocation-1',
        detail: null,
      }),
    );
    const state = reduceEvent(
      initialFailure,
      diagnosticEvent(2, 'run_failed', 'diag-1', 'fatal', 'Agent failed terminally.', {
        detail: 'Exit 2',
      }),
    );

    expect(state.diagnostics).toHaveLength(1);
    const updatedDiagnostic = state.diagnostics[0];
    if (updatedDiagnostic === undefined) throw new Error('Expected a projected diagnostic');
    expect(updatedDiagnostic).toMatchObject({
      id: 'diag-1',
      summary: 'Agent failed terminally.',
      detail: 'Exit 2',
      severity: 'fatal',
      invocationId: 'invocation-1',
      sequence: 2,
    });
    expect(latestDiagnosticChange(initialFailure, state)).toBe(updatedDiagnostic);
  });

  it('preserves distinct diagnostic ids from the same invocation', () => {
    let state = reduceEvent(
      initialCoreState(),
      diagnosticEvent(1, 'invocation_finished', 'diag-1', 'error', 'First failure.', {
        invocationId: 'invocation-1',
      }),
    );
    state = reduceEvent(
      state,
      diagnosticEvent(2, 'phase_finished', 'diag-2', 'error', 'Second failure.', {
        invocationId: 'invocation-1',
      }),
    );

    expect(state.diagnostics.map(diagnostic => diagnostic.id)).toEqual(['diag-1', 'diag-2']);
  });

  it('retains structured configuration failure detail in the transcript', () => {
    const state = reduceEvent(initialCoreState(), {
      ...baseEvent(3, 'configuration_failed'),
      data: {
        kind: 'configuration_failed',
        code: 'resume_limit_exhausted',
        message: 'This run has completed 30 rounds.',
        usage: 'Use a larger limit.',
        stage: 'configuration',
        exit_code: 2,
      },
    });

    expect(state.transcript[0]?.content).toContain('resume_limit_exhausted');
    expect(state.transcript[0]?.content).toContain('Use a larger limit.');
    expect(state.diagnostics[0]).toMatchObject({
      failureKind: 'configuration',
      summary:
        'This run has completed 30 rounds.\n\nUse a larger limit.\n\nCode: resume_limit_exhausted · Stage: configuration',
    });
  });

  it('projects an interruption discriminator without presentation labels', () => {
    const state = reduceEvent(initialCoreState(), {
      ...baseEvent(3, 'run_interrupted'),
      data: {kind: 'run_interrupted', reason: 'launcher_terminated', signal: 'SIGTERM'},
    });

    expect(state.diagnostics[0]).toMatchObject({
      failureKind: 'run_interruption',
      scope: 'run',
      summary: 'launcher_terminated (SIGTERM)',
      severity: 'fatal',
    });
    expect('title' in (state.diagnostics[0] ?? {})).toBe(false);
  });

  it('distinguishes failed and interrupted terminal transcript entries', () => {
    const failed = reduceEvent(initialCoreState(), {
      ...baseEvent(1, 'run_failed'),
      text: '',
    });
    const interrupted = reduceEvent(initialCoreState(), {
      ...baseEvent(1, 'run_interrupted'),
      text: '',
      data: {kind: 'run_interrupted', reason: 'Operator stopped the run', signal: 'SIGINT'},
    });

    expect(failed.transcript.at(-1)).toMatchObject({
      content: 'Run failed.',
      label: 'Run failed',
    });
    expect(interrupted.transcript.at(-1)).toMatchObject({
      content: 'Operator stopped the run (SIGINT)',
      label: 'Run interrupted',
    });
  });

  it('exposes experiment changes only as stream-derived invalidation', () => {
    const state = reduceEvent(initialCoreState(), {
      ...baseEvent(12, 'experiments_changed'),
      data: {kind: 'experiments_changed', reason: 'round_persisted'},
    });

    expect(state.experimentsRevision).toBe(12);
    expect('experimentLog' in state).toBe(false);
  });
});

function baseEvent(sequence: number, type: RunEvent['type']): RunEvent {
  return {
    sequence,
    timestamp: `2026-01-01T00:00:0${sequence}Z`,
    type,
    agent_kind: 'implementer',
    round_label: 'round-1-implementer',
  };
}

function chatAnswerEvent(sequence: number, answer: string, threadId?: string): RunEvent {
  return {
    ...baseEvent(sequence, 'chat'),
    agent_kind: 'chat',
    round_label: 'experiment-chat',
    ...(threadId === undefined ? {} : {chat_thread_id: threadId}),
    data: {kind: 'chat', answer},
  };
}

function threadCreatedEvent(sequence: number, threadId: string, provider: string): RunEvent {
  return {
    ...baseEvent(sequence, 'chat_thread_created'),
    agent_kind: 'chat',
    round_label: 'experiment-chat',
    chat_thread_id: threadId,
    data: {
      kind: 'chat_thread_created',
      thread_id: threadId,
      title: '',
      driver: 'agentshim',
      provider,
      model: 'opus',
      created_at: `2026-01-01T00:00:0${sequence}Z`,
    },
  };
}

function outputEvent(sequence: number, content: string, invocationId = 'turn'): RunEvent {
  return {
    ...baseEvent(sequence, 'agent_output_chunk'),
    invocation_id: invocationId,
    data: {kind: 'agent_output_chunk', channel: 'assistant', content},
  };
}

function executionEvent(
  sequence: number,
  type: RunEvent['type'],
  executionId: string,
  data: NonNullable<RunEvent['data']>,
): RunEvent {
  return {...baseEvent(sequence, type), execution_id: executionId, data};
}

function startedData(assignment: string): NonNullable<RunEvent['data']> {
  return {
    kind: 'agent_execution_started',
    stage: 'implementation',
    attempt: 1,
    system_prompt: '',
    user_prompt: assignment,
    activity: {
      kind: 'agent_execution_activity_changed',
      mode: 'thinking',
      summary: 'Starting',
      tool: null,
    },
  };
}

function checkpoint(executionId: string): NonNullable<RunSnapshot['active_executions']>[number] {
  return {
    execution_id: executionId,
    agent_kind: 'judge',
    round_label: 'round-2-judge',
    stage: 'judging',
    attempt: 1,
    assignment: 'Review',
    started_at: '2026-01-01T00:00:00Z',
    activity: {
      kind: 'agent_execution_activity_changed',
      mode: 'thinking',
      summary: 'Reviewing',
      tool: null,
    },
  };
}

function toolEvent(
  sequence: number,
  kind: 'tool_call' | 'tool_result',
  callId: string,
  content: string,
): RunEvent {
  return {
    ...baseEvent(sequence, kind),
    invocation_id: 'turn',
    data:
      kind === 'tool_call'
        ? {kind, tool: 'Bash', call_id: callId, args: {command: content}}
        : {kind, tool: 'Bash', call_id: callId, content, is_error: false},
  };
}

function todoEvent(sequence: number, executionId: string, content: string): RunEvent {
  return {
    ...baseEvent(sequence, 'todo_update'),
    execution_id: executionId,
    data: {kind: 'todo_update', todos: [{content, status: 'in_progress'}]},
  };
}

function diagnosticEvent(
  sequence: number,
  type: RunEvent['type'],
  id: string,
  severity: 'warning' | 'error' | 'fatal',
  summary: string,
  options: {invocationId?: string; detail?: string | null} = {},
): RunEvent {
  return {
    ...baseEvent(sequence, type),
    ...(options.invocationId === undefined ? {} : {invocation_id: options.invocationId}),
    diagnostic: {
      id,
      code: 'agent_failed',
      summary,
      detail: options.detail ?? null,
      hint: null,
      scope: 'invocation',
      severity,
      retryability: 'manual',
      cause_id: null,
      debug_ref: null,
    },
  };
}
