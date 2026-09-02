import {describe, expect, it} from 'bun:test';
import type {RunEvent} from '@vibesys/backend-client';
import {
  type CoreState,
  DEFAULT_CHAT_THREAD_ID,
  initialCoreState,
  reduceEventBatch,
  reduceEventPrefix,
} from './core-state.js';
import type {RoundSummary} from './run-map.js';

/**
 * Equivalence harness for the tail bootstrap.
 *
 * The property under test: folding a whole event log in one batch must produce
 * the same `CoreState` as folding its tail and then backfilling the preceding
 * chunks through `reduceEventPrefix`.
 *
 * One exception is scoped out of the comparison, see `mergeRoundLists`: an agent
 * execution whose `agent_execution_started` falls in a backfilled chunk and
 * whose `agent_execution_finished` falls after the boundary loses its timing
 * interval, because the newer fold saw a finish with no start and dropped the
 * timestamp. `withoutRoundTiming` scopes exactly that, and the round-aligned
 * case below asserts full equality including timing.
 *
 * Two further divergences are inherent to folding a bare suffix, and each has a
 * test of its own below rather than a normalization here:
 * - `status` and the expected-phase seeding both need `run_started`, which a
 *   suffix does not carry.
 * - The typed-tool latch is stateful, so a producer that emits both typed tool
 *   events and legacy `tool`-channel chunks leaks the legacy chunks a suffix
 *   sees before its first typed event.
 */

describe('prefix backfill equivalence', () => {
  for (const seed of [1, 7, 42, 1234]) {
    for (const typedTools of [true, false]) {
      const producer = typedTools ? 'typed tool events' : 'legacy tool chunks';
      it(`reproduces a full fold from a tail plus backfilled chunks (seed ${seed}, ${producer})`, () => {
        const events = generateRunEvents(seed, {typedTools});
        const full = reduceEventBatch(initialCoreState(), events);

        for (const tail of [1, 23, 137, 400, events.length - 1, events.length]) {
          const bootstrapped = backfill(events, tail, 97);

          expect(bootstrapped.historyAfterSequence).toBe(0);
          expect(withoutRoundTiming(bootstrapped)).toEqual(withoutRoundTiming(full));
        }
      });
    }
  }

  it('reproduces a full fold exactly, timings included, on round boundaries', () => {
    const events = generateRunEvents(9, {typedTools: true});
    const boundaries = events.flatMap((event, index) =>
      event.type === 'round_finished' ? [index + 1] : [],
    );
    expect(boundaries.length).toBeGreaterThan(2);

    const full = reduceEventBatch(initialCoreState(), events);
    const bootstrapped = backfillAt(events, boundaries);

    expect(bootstrapped).toEqual(full);
  });

  it('keeps the tail fold a suffix of the full transcript at a round boundary', () => {
    const events = generateRunEvents(3, {typedTools: true});
    const roundEnds = events.flatMap((event, index) =>
      event.type === 'round_finished' ? [index + 1] : [],
    );
    // One round short of the end, so the tail carries real transcript content.
    const boundary = roundEnds.at(-2) as number;
    const floor = sequenceAt(events, boundary - 1);

    const full = reduceEventBatch(initialCoreState(), events);
    const tail = reduceEventBatch(
      initialCoreState(),
      events.slice(boundary),
      undefined,
      undefined,
      floor,
    );

    // A round boundary splits no merged entry, so the tail's transcript is a
    // plain suffix. At an arbitrary boundary it is not: the entry the boundary
    // falls inside is split in two, which is why `mergeTranscriptPrefix`
    // re-folds instead of concatenating.
    const carried = full.transcript.filter(entry => Number(entry.id) <= floor).length;
    expect(tail.transcript.length).toBeGreaterThan(0);
    expect(tail.transcript).toEqual(full.transcript.slice(carried));
    expect(tail.historyAfterSequence).toBe(floor);
  });

  it('leaves the history floor alone for callers that do not pass one', () => {
    const bootstrapped = reduceEventBatch(initialCoreState(), [chunkEvent(1)], undefined, 1, 40);
    const extended = reduceEventBatch(bootstrapped, [chunkEvent(41)], [], 41);

    expect(bootstrapped.historyAfterSequence).toBe(40);
    expect(extended.historyAfterSequence).toBe(40);
  });
});

describe('prefix merges across the chunk boundary', () => {
  it('lands a tail tool result on a tool call from the chunk', () => {
    const events = [
      toolCallEvent(1, 'call-a'),
      toolCallEvent(2, 'call-b'),
      toolResultEvent(3, 'call-b', 'second result'),
      toolResultEvent(4, 'call-a', 'first result'),
    ];

    const merged = foldAsPrefix(events, 2);

    expect(merged).toEqual(reduceEventBatch(initialCoreState(), events));
    expect(merged.transcript).toHaveLength(2);
    expect(merged.transcript.map(entry => entry.toolResult?.content)).toEqual([
      'first result',
      'second result',
    ]);
  });

  it('concatenates streamed assistant text split across the boundary', () => {
    const events = [
      chunkEvent(1, 'hello '),
      chunkEvent(2, 'brave '),
      chunkEvent(3, 'new '),
      chunkEvent(4, 'world'),
    ];

    const merged = foldAsPrefix(events, 2);

    expect(merged).toEqual(reduceEventBatch(initialCoreState(), events));
    expect(merged.transcript.map(entry => entry.content)).toEqual(['hello brave new world']);
  });

  it('titles a chunk-created chat thread from a tail turn', () => {
    const events = [
      threadCreatedEvent(1, 'thread-a'),
      chatEvent(2, 'thread-a', 'first answer'),
      chatEvent(3, 'thread-a', 'second answer', 'Ring buffer sizing'),
    ];

    const merged = foldAsPrefix(events, 2);

    expect(merged).toEqual(reduceEventBatch(initialCoreState(), events));
    expect(merged.chatThreads).toEqual([
      {id: DEFAULT_CHAT_THREAD_ID, title: '', driver: null, provider: null, model: null},
      {
        id: 'thread-a',
        title: 'Ring buffer sizing',
        driver: 'agentshim',
        provider: 'anthropic',
        model: 'opus',
      },
    ]);
    // Each chat answer is a complete turn and stays a separate entry. The point
    // is that the prefix fold agrees with the in-batch fold even when the two
    // answers land on opposite sides of the boundary.
    expect(merged.chatTranscripts['thread-a']?.map(entry => entry.content)).toEqual([
      'first answer',
      'second answer',
    ]);
  });

  it('folds a tail answer over a streamed chat turn left in the chunk', () => {
    const events = [
      threadCreatedEvent(1, 'thread-a'),
      chatChunkEvent(2, 'thread-a', 'partial '),
      chatChunkEvent(3, 'thread-a', 'stream'),
      chatEvent(4, 'thread-a', 'complete answer'),
    ];

    // The turn's streamed chunks fall below the floor; only its terminal answer
    // lands in the tail. A full replay folds the answer over its streamed turn
    // and renders it once, so the backfilled merge must too rather than leaving
    // the streamed entry beside a second answer entry.
    const merged = foldAsPrefix(events, 3);

    expect(merged).toEqual(reduceEventBatch(initialCoreState(), events));
    expect(merged.chatTranscripts['thread-a']?.map(entry => entry.content)).toEqual([
      'complete answer',
    ]);
  });

  it('keeps a round started in the chunk and finished in the tail', () => {
    const events = [chunkEvent(1, 'work'), roundFinishedEvent(2)];

    const merged = foldAsPrefix(events, 1);

    expect(merged).toEqual(reduceEventBatch(initialCoreState(), events));
    expect(merged.rounds).toEqual([
      {
        number: 1,
        status: 'completed',
        startedAt: timestamp(1),
        finishedAt: timestamp(2),
        agentIntervals: [],
        activeAgentStarts: {},
      },
    ]);
  });

  it('merges a chunk diagnostic with its tail update by id', () => {
    const events = [
      diagnosticEvent(1, 'diag-1', 'warning', 'Agent stalled', null),
      diagnosticEvent(2, 'diag-1', 'error', 'Agent failed', 'exit 1'),
    ];

    const merged = foldAsPrefix(events, 1);

    expect(merged).toEqual(reduceEventBatch(initialCoreState(), events));
    expect(merged.diagnostics).toHaveLength(1);
    expect(merged.diagnostics[0]).toMatchObject({
      severity: 'error',
      summary: 'Agent failed',
      detail: 'exit 1',
      sequence: 2,
    });
  });

  it('replaces a chunk todo list from the tail and keeps unrelated ones', () => {
    const events = [
      todoEvent(1, 'exec-a', 'chunk plan'),
      todoEvent(2, 'exec-b', 'other plan'),
      todoEvent(3, 'exec-a', 'tail plan'),
    ];

    const merged = foldAsPrefix(events, 1);

    expect(merged).toEqual(reduceEventBatch(initialCoreState(), events));
    expect(merged.todos.map(todo => [todo.executionId, todo.items[0]?.content])).toEqual([
      ['exec-b', 'other plan'],
      ['exec-a', 'tail plan'],
    ]);
  });

  // Documents the one field the merge cannot reconstruct. The chunk holds an
  // open start, the tail holds a finish with nothing to close, and the finish
  // timestamp is gone by the time the two states meet.
  it('loses the timing interval of an execution split across the boundary', () => {
    const events = [
      executionStartedEvent(1, 'exec-a'),
      executionFinishedEvent(2, 'exec-a'),
      roundFinishedEvent(3),
    ];
    const full = reduceEventBatch(initialCoreState(), events);

    const merged = foldAsPrefix(events, 1);

    expect(withoutRoundTiming(merged)).toEqual(withoutRoundTiming(full));
    expect(full.rounds[0]?.agentIntervals).toEqual([
      {startedAt: timestamp(1), finishedAt: timestamp(2)},
    ]);
    expect(merged.rounds[0]?.agentIntervals).toEqual([]);
    expect(merged.rounds[0]?.activeAgentStarts).toEqual({'implementer:exec-a': timestamp(1)});
  });

  // A bare suffix has no `run_started`, so the tail fold has no run status and
  // no outer loop to seed a round's expected roles from. Both come back once the
  // server carries the run-level events into the bootstrap batch; until then the
  // merge cannot invent them, and the newer state owns run status by contract.
  it('cannot recover run status or expected-phase seeding from a bare suffix', () => {
    const events = [runStartedEvent(1), chunkEvent(2, 'work'), chunkEvent(3, ' more')];
    const full = reduceEventBatch(initialCoreState(), events);

    const merged = foldAsPrefix(events, 1);

    expect(full.status).toBe('running');
    expect(merged.status).toBe('connecting');
    expect(merged.outerLoop).toBe('plain');
    expect(full.phases.map(phase => phase.kind)).toEqual(['implementer', 'judge', 'perf_eval']);
    expect(merged.phases.map(phase => phase.kind)).toEqual(['implementer']);
    expect(merged.transcript.map(entry => entry.content)).toEqual(['work more']);
  });

  // The typed-tool latch is stateful: once a typed tool event is seen, legacy
  // `tool`-channel chunks are dropped as duplicates. A suffix starts unlatched,
  // so it keeps the legacy chunks it sees before its own first typed event, and
  // no state-level merge can take them back out.
  it('leaks legacy tool chunks a mixed producer emitted before the tail latched', () => {
    const events = [
      toolCallEvent(1, 'call-a'),
      toolResultEvent(2, 'call-a', 'first result'),
      legacyToolChunkEvent(3, 'duplicate of the next call'),
      toolCallEvent(4, 'call-b'),
      toolResultEvent(5, 'call-b', 'second result'),
    ];
    const full = reduceEventBatch(initialCoreState(), events);

    const merged = foldAsPrefix(events, 2);

    expect(full.transcript).toHaveLength(2);
    expect(merged.transcript).toHaveLength(3);
    expect(merged.transcript[1]?.content).toBe('duplicate of the next call');
  });
});

/** Folds the tail of `events` and backfills the rest in `chunk`-sized steps. */
function backfill(events: readonly RunEvent[], tail: number, chunk: number): CoreState {
  const start = Math.max(0, events.length - tail);
  const boundaries: number[] = [];
  for (let at = start; at > 0; at -= chunk) boundaries.unshift(at);
  return backfillAt(events, boundaries);
}

/**
 * Folds the events after the last boundary, then each preceding chunk as a
 * prefix, so the history floor walks back down to zero.
 */
function backfillAt(events: readonly RunEvent[], boundaries: readonly number[]): CoreState {
  const edges = [...new Set(boundaries)].sort((left, right) => left - right);
  const start = edges.at(-1) ?? 0;
  let state = reduceEventBatch(
    initialCoreState(),
    events.slice(start),
    undefined,
    undefined,
    sequenceAt(events, start - 1),
  );
  for (let index = edges.length - 1; index >= 0; index -= 1) {
    const from = index === 0 ? 0 : (edges[index - 1] as number);
    const to = edges[index] as number;
    state = reduceEventPrefix(state, events.slice(from, to), sequenceAt(events, from - 1));
  }
  return state;
}

function foldAsPrefix(events: readonly RunEvent[], boundary: number): CoreState {
  return backfillAt(events, [boundary]);
}

function sequenceAt(events: readonly RunEvent[], index: number): number {
  return index < 0 ? 0 : (events[index]?.sequence ?? 0);
}

/** The state minus the round timing fields a split execution cannot recover. */
function withoutRoundTiming(state: CoreState): CoreState {
  return {
    ...state,
    rounds: state.rounds.map(
      ({agentIntervals: _intervals, activeAgentStarts: _starts, ...rest}) => {
        return rest as RoundSummary;
      },
    ),
  };
}

// A deterministic 32-bit generator, so a failing seed is reproducible.
class Rng {
  #state: number;

  constructor(seed: number) {
    this.#state = (seed * 2654435761) >>> 0;
  }

  float(): number {
    this.#state = (this.#state + 0x6d2b79f5) >>> 0;
    let value = this.#state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  }

  int(min: number, max: number): number {
    return min + Math.floor(this.float() * (max - min + 1));
  }

  pick<T>(values: readonly T[]): T {
    return values[this.int(0, values.length - 1)] as T;
  }
}

const WORDS =
  'the implementer measured allocation pressure across the hot path and adjusted the ring buffer capacity while re-checking cache line alignment for the producer and consumer cursors so false sharing stays bounded'.split(
    ' ',
  );

const AGENT_KINDS = ['orchestrator', 'implementer', 'judge', 'profiler'] as const;
const TOOLS = ['bash', 'read_file', 'edit_file', 'grep', 'write_file'] as const;
const CHANNELS = ['assistant', 'assistant', 'assistant', 'analysis', 'prompt'] as const;

/**
 * A synthetic run log shaped like a real one: rounds of agent executions with
 * streamed output, paired tool calls, todo and usage updates, per-round judge
 * and benchmark results, and chat traffic across several threads.
 *
 * `typedTools` picks the producer flavor: typed `tool_call`/`tool_result`
 * events, or the legacy `tool`-channel chunks a driver without structured tool
 * reporting emits. One log carries one flavor, because the reducer's
 * typed-tool latch makes a log carrying both order-dependent (see the mixed
 * producer test).
 *
 * Sized well under MAX_TRANSCRIPT_ENTRIES so cap eviction, which replay and
 * backfill are not required to agree on, never enters the comparison.
 */
function generateRunEvents(seed: number, options: {typedTools: boolean}, rounds = 5): RunEvent[] {
  const rng = new Rng(seed);
  const events: RunEvent[] = [];
  const threadIds = ['thread-a', 'thread-b', 'thread-c'];
  let clock = 0;

  const emit = (event: Omit<RunEvent, 'sequence' | 'timestamp'>): void => {
    clock += rng.int(5, 400);
    events.push({...event, sequence: events.length + 1, timestamp: isoAt(clock)} as RunEvent);
  };

  emit({type: 'server_started', status: 'active'});
  emit({type: 'server_ready', data: {kind: 'server_ready'}});
  emit({
    type: 'run_started',
    status: 'active',
    data: {
      kind: 'run_started',
      outer_loop: 'agent',
      input: '/synthetic/target',
      max_rounds: rounds,
    },
  });

  for (let round = 1; round <= rounds; round += 1) {
    const roundLabel = `round-${round}`;
    for (const kind of AGENT_KINDS) {
      const executionId = `exec-${round}-${kind}`;
      const prompt = words(rng, 5, 20);
      const context = {agent_kind: kind, round_label: roundLabel, execution_id: executionId};
      emit({
        ...context,
        type: 'agent_execution_started',
        status: 'active',
        data: {
          kind: 'agent_execution_started',
          stage: kind,
          attempt: null,
          system_prompt: prompt,
          user_prompt: prompt,
          activity: {
            kind: 'agent_execution_activity_changed',
            mode: 'thinking',
            summary: 'Thinking',
            tool: null,
          },
          driver: 'agentshim',
          provider: 'anthropic',
          model: 'claude-sonnet',
        },
      });
      emit({
        ...context,
        type: 'phase_started',
        status: 'active',
        data: {kind: 'phase', phase: kind, attempt: null},
      });
      emit({
        ...context,
        type: 'invocation_started',
        status: 'active',
        data: {kind: 'invocation_started', system_prompt: prompt, user_prompt: prompt},
      });

      const turns = rng.int(4, 12);
      for (let turn = 0; turn < turns; turn += 1) {
        emit({
          ...context,
          invocation_id: executionId,
          type: 'agent_output_chunk',
          data: {
            kind: 'agent_output_chunk',
            channel: rng.pick(CHANNELS),
            content: words(rng, 3, 25),
          },
        });
        if (rng.float() < 0.35) {
          const callId = `${executionId}-call-${turn}`;
          const tool = rng.pick(TOOLS);
          if (options.typedTools) {
            emit({
              ...context,
              invocation_id: executionId,
              type: 'tool_call',
              data: {kind: 'tool_call', tool, call_id: callId, args: {pattern: words(rng, 2, 5)}},
            });
            emit({
              ...context,
              invocation_id: executionId,
              type: 'tool_result',
              data: {
                kind: 'tool_result',
                tool,
                call_id: callId,
                content: words(rng, 5, 40),
                is_error: rng.float() < 0.05,
              },
            });
          } else {
            emit({
              ...context,
              invocation_id: executionId,
              type: 'agent_output_chunk',
              data: {
                kind: 'agent_output_chunk',
                channel: 'tool',
                content: `→ ${tool} ${words(rng, 2, 5)}`,
              },
            });
            emit({
              ...context,
              invocation_id: executionId,
              type: 'agent_output_chunk',
              data: {kind: 'agent_output_chunk', channel: 'tool', content: words(rng, 5, 40)},
            });
          }
        }
        if (rng.float() < 0.1) {
          emit({
            ...context,
            type: 'todo_update',
            data: {
              kind: 'todo_update',
              todos: ['completed', 'in_progress', 'pending'].map(status => ({
                content: words(rng, 2, 6),
                status,
              })),
            },
          });
        }
        if (rng.float() < 0.15) {
          emit({
            ...context,
            type: 'usage_update',
            data: {
              kind: 'usage_update',
              input_tokens: rng.int(2000, 180000),
              context_window: 200000,
              model: 'claude-sonnet',
            },
          });
        }
      }

      emit({
        ...context,
        type: 'agent_execution_finished',
        status: 'completed',
        data: {kind: 'agent_execution_finished', error: null},
      });
      emit({
        ...context,
        invocation_id: executionId,
        type: 'invocation_finished',
        status: 'completed',
        data: {kind: 'invocation_finished', error: null},
      });
      emit({
        ...context,
        type: 'phase_finished',
        status: 'completed',
        data: {kind: 'phase', phase: kind, attempt: null},
      });
    }

    emit({
      type: 'judge_result',
      round_label: roundLabel,
      data: {
        kind: 'judge_result',
        verdict: rng.float() < 0.7 ? 'pass' : 'fail',
        feedback: words(rng, 5, 20),
        attempt: 1,
      },
    });
    emit({
      type: 'benchmark_result',
      round_label: roundLabel,
      data: {
        kind: 'benchmark_result',
        metric: 'throughput',
        value: rng.int(100000, 5000000),
        unit: 'ops/s',
      },
    });
    emit({
      type: 'round_finished',
      round_label: roundLabel,
      status: 'completed',
      data: {
        kind: 'round_finished',
        attempts: 1,
        judge_verdict: rng.float() < 0.7 ? 'pass' : 'fail',
        perf_metric: rng.int(100000, 5000000),
        perf_unit: 'ops/s',
      },
    });
    if (rng.float() < 0.3) {
      emit({
        type: 'experiments_changed',
        data: {kind: 'experiments_changed', reason: 'round_persisted'},
      });
    }
    if (rng.float() < 0.5) {
      // A thread the operator opens mid-run, titled only on a later turn, and
      // occasionally the implicit default thread with no id at all.
      const threadId = rng.float() < 0.2 ? undefined : rng.pick(threadIds);
      const chatContext = {
        agent_kind: 'chat',
        round_label: 'experiment-chat',
        ...(threadId === undefined ? {} : {chat_thread_id: threadId}),
      };
      if (threadId !== undefined) {
        emit({
          ...chatContext,
          type: 'chat_thread_created',
          data: {
            kind: 'chat_thread_created',
            thread_id: threadId,
            title: '',
            driver: 'agentshim',
            provider: 'anthropic',
            model: 'claude-sonnet',
            created_at: isoAt(clock),
          },
        });
      }
      emit({
        ...chatContext,
        type: 'chat',
        status: 'answered',
        data: {
          kind: 'chat',
          answer: words(rng, 5, 30),
          ...(rng.float() < 0.5 ? {thread_title: words(rng, 2, 4)} : {}),
        },
      });
    }
  }

  emit({type: 'run_finished', status: 'completed'});
  return events;
}

function words(rng: Rng, min: number, max: number): string {
  const count = rng.int(min, max);
  const chosen: string[] = [];
  for (let at = 0; at < count; at += 1) chosen.push(rng.pick(WORDS));
  return chosen.join(' ');
}

function isoAt(millis: number): string {
  return new Date(Date.UTC(2026, 7, 20) + millis).toISOString();
}

function timestamp(sequence: number): string {
  return `2026-01-01T00:00:0${sequence}Z`;
}

function baseEvent(sequence: number, type: RunEvent['type']): RunEvent {
  return {
    sequence,
    timestamp: timestamp(sequence),
    type,
    agent_kind: 'implementer',
    round_label: 'round-1-implementer',
  };
}

function chunkEvent(sequence: number, content = `entry ${sequence}`): RunEvent {
  return {
    ...baseEvent(sequence, 'agent_output_chunk'),
    invocation_id: 'turn',
    data: {kind: 'agent_output_chunk', channel: 'assistant', content},
  };
}

function toolCallEvent(sequence: number, callId: string): RunEvent {
  return {
    ...baseEvent(sequence, 'tool_call'),
    invocation_id: 'turn',
    data: {kind: 'tool_call', tool: 'Bash', call_id: callId, args: {command: callId}},
  };
}

function toolResultEvent(sequence: number, callId: string, content: string): RunEvent {
  return {
    ...baseEvent(sequence, 'tool_result'),
    invocation_id: 'turn',
    data: {kind: 'tool_result', tool: 'Bash', call_id: callId, content, is_error: false},
  };
}

function legacyToolChunkEvent(sequence: number, content: string): RunEvent {
  return {
    ...baseEvent(sequence, 'agent_output_chunk'),
    invocation_id: 'legacy-turn',
    data: {kind: 'agent_output_chunk', channel: 'tool', content},
  };
}

function runStartedEvent(sequence: number): RunEvent {
  return {
    sequence,
    timestamp: timestamp(sequence),
    type: 'run_started',
    status: 'active',
    data: {kind: 'run_started', outer_loop: 'plain', input: '/target', max_rounds: 3},
  };
}

function roundFinishedEvent(sequence: number): RunEvent {
  return {
    sequence,
    timestamp: timestamp(sequence),
    type: 'round_finished',
    status: 'completed',
    round_label: 'round-1',
    data: {
      kind: 'round_finished',
      attempts: 1,
      judge_verdict: 'pass',
      perf_metric: null,
      perf_unit: null,
    },
  };
}

function executionStartedEvent(sequence: number, executionId: string): RunEvent {
  return {
    ...baseEvent(sequence, 'agent_execution_started'),
    round_label: 'round-1',
    execution_id: executionId,
    status: 'active',
    data: {
      kind: 'agent_execution_started',
      stage: 'implementation',
      attempt: null,
      system_prompt: '',
      user_prompt: 'Implement the queue',
      activity: {
        kind: 'agent_execution_activity_changed',
        mode: 'thinking',
        summary: 'Starting',
        tool: null,
      },
    },
  };
}

function executionFinishedEvent(sequence: number, executionId: string): RunEvent {
  return {
    ...baseEvent(sequence, 'agent_execution_finished'),
    round_label: 'round-1',
    execution_id: executionId,
    status: 'completed',
    data: {kind: 'agent_execution_finished', error: null},
  };
}

function threadCreatedEvent(sequence: number, threadId: string): RunEvent {
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
      provider: 'anthropic',
      model: 'opus',
      created_at: timestamp(sequence),
    },
  };
}

function chatEvent(sequence: number, threadId: string, answer: string, title?: string): RunEvent {
  return {
    ...baseEvent(sequence, 'chat'),
    agent_kind: 'chat',
    round_label: 'experiment-chat',
    chat_thread_id: threadId,
    status: 'answered',
    data: {kind: 'chat', answer, ...(title === undefined ? {} : {thread_title: title})},
  };
}

function chatChunkEvent(sequence: number, threadId: string, content: string): RunEvent {
  return {
    ...baseEvent(sequence, 'agent_output_chunk'),
    agent_kind: 'chat',
    round_label: 'experiment-chat',
    chat_thread_id: threadId,
    invocation_id: `${threadId}-turn`,
    data: {kind: 'agent_output_chunk', channel: 'assistant', content},
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
  id: string,
  severity: 'warning' | 'error' | 'fatal',
  summary: string,
  detail: string | null,
): RunEvent {
  return {
    ...baseEvent(sequence, 'invocation_finished'),
    invocation_id: 'turn',
    diagnostic: {
      id,
      code: 'agent_failed',
      summary,
      detail,
      hint: null,
      scope: 'invocation',
      severity,
      retryability: 'manual',
      cause_id: null,
      debug_ref: null,
    },
  };
}
