import {describe, expect, test} from 'bun:test';
import type {RoundSummary} from '../run-map.js';
import {stripWindow} from './round-strip.js';

function rounds(count: number): RoundSummary[] {
  return Array.from({length: count}, (_, index) => ({
    number: index + 1,
    status: 'completed' as const,
  }));
}

/** ` r7 ` and friends: the width the strip actually asks for. */
const width = (round: RoundSummary): number => `  r${round.number}  `.length;

describe('stripWindow', () => {
  test('shows every round when they all fit', () => {
    const view = stripWindow(rounds(5), 1, 200, width);
    expect(view.rounds).toHaveLength(5);
    expect(view.hiddenBefore).toBe(0);
    expect(view.hiddenAfter).toBe(0);
  });

  test('keeps the selected round visible however far into the run it is', () => {
    for (const selected of [1, 2, 37, 99, 100]) {
      const view = stripWindow(rounds(100), selected, 60, width);
      expect(view.rounds.some(round => round.number === selected)).toBe(true);
    }
  });

  test('reports what it had to hide on each side', () => {
    const view = stripWindow(rounds(100), 50, 60, width);
    expect(view.hiddenBefore).toBeGreaterThan(0);
    expect(view.hiddenAfter).toBeGreaterThan(0);
    expect(view.hiddenBefore + view.rounds.length + view.hiddenAfter).toBe(100);
  });

  test('never exceeds the width it was given', () => {
    for (const selected of [1, 8, 44, 100]) {
      const view = stripWindow(rounds(100), selected, 60, width);
      const used = view.rounds.reduce((total, round) => total + width(round), 0);
      expect(used).toBeLessThanOrEqual(60);
    }
  });

  test('fills the strip when the selection sits at either end', () => {
    const atStart = stripWindow(rounds(100), 1, 60, width);
    const atEnd = stripWindow(rounds(100), 100, 60, width);
    expect(atStart.rounds.length).toBeGreaterThan(3);
    expect(atEnd.rounds.length).toBeGreaterThan(3);
    expect(atStart.hiddenBefore).toBe(0);
    expect(atEnd.hiddenAfter).toBe(0);
  });

  test('slides by one as the selection steps, so the run scrolls rather than pages', () => {
    const all = rounds(100);
    let previous = stripWindow(all, 20, 60, width);
    for (let selected = 21; selected < 30; selected += 1) {
      const next = stripWindow(all, selected, 60, width);
      expect(next.rounds.some(round => round.number === selected)).toBe(true);
      // The window moves at most one round per step: no jumping.
      expect(Math.abs(next.hiddenBefore - previous.hiddenBefore)).toBeLessThanOrEqual(1);
      previous = next;
    }
  });

  test('keeps round order stable', () => {
    const view = stripWindow(rounds(100), 50, 60, width);
    const numbers = view.rounds.map(round => round.number);
    expect(numbers).toEqual([...numbers].sort((a, b) => a - b));
  });

  test('handles an empty run and a one-round run', () => {
    expect(stripWindow([], null, 60, width).rounds).toEqual([]);
    expect(stripWindow(rounds(1), 1, 60, width).rounds).toHaveLength(1);
  });

  test('still shows the selection in a very narrow strip', () => {
    const view = stripWindow(rounds(100), 60, 12, width);
    expect(view.rounds.some(round => round.number === 60)).toBe(true);
  });
});
