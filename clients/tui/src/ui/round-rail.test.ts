import {describe, expect, test} from 'bun:test';
import type {RoundSummary} from '@vibesys/core-state';
import {initialSessionState, type SessionState} from '../session-model.js';
import {
  RAIL_COMPACT_WIDTH,
  RAIL_FULL_WIDTH,
  railWindow,
  roundRailVisible,
  roundRailWidth,
} from './round-rail.js';

function rounds(count: number): RoundSummary[] {
  return Array.from({length: count}, (_, index) => ({
    number: index + 1,
    status: 'completed' as const,
  }));
}

/** A run that owns the round view: the log is dismissed and rounds exist. */
function railState(count: number): SessionState {
  const base = initialSessionState();
  return {
    ...base,
    experimentLog: null,
    core: {...base.core, rounds: rounds(count)},
  };
}

describe('railWindow', () => {
  test('shows every round when they all fit', () => {
    const view = railWindow(rounds(5), 1, 20);
    expect(view.rounds).toHaveLength(5);
    expect(view.hiddenBefore).toBe(0);
    expect(view.hiddenAfter).toBe(0);
  });

  test('keeps the selected round visible however far into the run it is', () => {
    for (const selected of [1, 2, 37, 99, 100]) {
      const view = railWindow(rounds(100), selected, 10);
      expect(view.rounds.some(round => round.number === selected)).toBe(true);
    }
  });

  test('reports what it had to hide on each side', () => {
    const view = railWindow(rounds(100), 50, 10);
    expect(view.hiddenBefore).toBeGreaterThan(0);
    expect(view.hiddenAfter).toBeGreaterThan(0);
    expect(view.hiddenBefore + view.rounds.length + view.hiddenAfter).toBe(100);
  });

  test('reserves two rows for the overflow counts when the run does not fit', () => {
    const rows = 10;
    const view = railWindow(rounds(100), 50, rows);
    // Both indicators show, so the rounds take the rows the counts do not.
    expect(view.hiddenBefore).toBeGreaterThan(0);
    expect(view.hiddenAfter).toBeGreaterThan(0);
    expect(view.rounds.length).toBe(rows - 2);
  });

  test('never exceeds the rows it was given', () => {
    for (const selected of [1, 8, 44, 100]) {
      const rows = 10;
      const view = railWindow(rounds(100), selected, rows);
      expect(view.rounds.length).toBeLessThanOrEqual(rows);
    }
  });

  test('fills the rail when the selection sits at either end', () => {
    const atStart = railWindow(rounds(100), 1, 10);
    const atEnd = railWindow(rounds(100), 100, 10);
    expect(atStart.rounds.length).toBeGreaterThan(3);
    expect(atEnd.rounds.length).toBeGreaterThan(3);
    expect(atStart.hiddenBefore).toBe(0);
    expect(atEnd.hiddenAfter).toBe(0);
  });

  test('slides by one as the selection steps, so the run scrolls rather than pages', () => {
    const all = rounds(100);
    let previous = railWindow(all, 20, 10);
    for (let selected = 21; selected < 30; selected += 1) {
      const next = railWindow(all, selected, 10);
      expect(next.rounds.some(round => round.number === selected)).toBe(true);
      // The window moves at most one round per step: no jumping.
      expect(Math.abs(next.hiddenBefore - previous.hiddenBefore)).toBeLessThanOrEqual(1);
      previous = next;
    }
  });

  test('keeps round order stable, top to bottom', () => {
    const view = railWindow(rounds(100), 50, 10);
    const numbers = view.rounds.map(round => round.number);
    expect(numbers).toEqual([...numbers].sort((a, b) => a - b));
  });

  test('handles an empty run and a one-round run', () => {
    expect(railWindow([], null, 10).rounds).toEqual([]);
    expect(railWindow(rounds(1), 1, 10).rounds).toHaveLength(1);
  });

  test('has nothing to show when it is given no rows', () => {
    const view = railWindow(rounds(10), 5, 0);
    expect(view.rounds).toEqual([]);
    expect(view.hiddenAfter).toBe(10);
  });

  test('still shows the selection in a very short rail', () => {
    const view = railWindow(rounds(100), 60, 3);
    expect(view.rounds.some(round => round.number === 60)).toBe(true);
  });
});

describe('roundRailWidth', () => {
  test('gives the full rail at wide terminals', () => {
    expect(roundRailWidth(120)).toBe(RAIL_FULL_WIDTH);
    expect(roundRailWidth(100)).toBe(RAIL_FULL_WIDTH);
  });

  test('falls back to the compact column between the thresholds', () => {
    expect(roundRailWidth(99)).toBe(RAIL_COMPACT_WIDTH);
    expect(roundRailWidth(72)).toBe(RAIL_COMPACT_WIDTH);
  });

  test('collapses to nothing below the narrow threshold', () => {
    expect(roundRailWidth(71)).toBe(0);
    expect(roundRailWidth(40)).toBe(0);
  });
});

describe('roundRailVisible', () => {
  test('is on for a run that owns the round view at a usable width', () => {
    expect(roundRailVisible(railState(3), 120)).toBe(true);
    expect(roundRailVisible(railState(3), 80)).toBe(true);
  });

  test('is off before the run has any rounds', () => {
    expect(roundRailVisible(railState(0), 120)).toBe(false);
  });

  test('is off while the experiment log is the landing view', () => {
    const state = {
      ...railState(3),
      experimentLog: {entries: [], selectedId: null, pending: true, error: null},
    };
    expect(roundRailVisible(state, 120)).toBe(false);
  });

  test('is off when a pane is zoomed', () => {
    const state = railState(3);
    const zoomed = {...state, layout: {...state.layout, zoomedPane: 'agents' as const}};
    expect(roundRailVisible(zoomed, 120)).toBe(false);
  });

  test('is off when a right-pane split takes the row at a fitting width', () => {
    const state = railState(3);
    const split = {
      ...state,
      layout: {
        ...state.layout,
        right: {view: 'perf' as const, title: 'Perf', content: '', pending: false, error: null},
      },
    };
    // Wide enough for the split to open, so the rail yields the row to it.
    expect(roundRailVisible(split, 120)).toBe(false);
    // Too narrow for the split, so the rail keeps the row.
    expect(roundRailVisible(split, 80)).toBe(true);
  });

  test('is off below the collapse width even for a live run', () => {
    expect(roundRailVisible(railState(3), 60)).toBe(false);
  });
});
