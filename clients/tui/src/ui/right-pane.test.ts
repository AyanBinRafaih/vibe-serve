import {describe, expect, it} from 'bun:test';
import {MIN_SPLIT_WIDTH, rightPaneWidth, splitFits} from './right-pane.js';

describe('split thresholds', () => {
  it('splits only once both panes would be readable', () => {
    expect(splitFits(MIN_SPLIT_WIDTH)).toBe(true);
    expect(splitFits(MIN_SPLIT_WIDTH - 1)).toBe(false);
    expect(splitFits(80)).toBe(false);
    expect(splitFits(200)).toBe(true);
  });
});

describe('pane sizing', () => {
  it('scales with the terminal instead of a fixed percentage', () => {
    // The old overlay was 70% at every size; these differ from each other.
    expect(rightPaneWidth(100)).not.toBe(rightPaneWidth(160));
    expect(rightPaneWidth(160)).toBeGreaterThan(rightPaneWidth(120));
  });

  it('always leaves the transcript a readable column', () => {
    for (let width = MIN_SPLIT_WIDTH; width <= 300; width += 1) {
      const left = width - rightPaneWidth(width);
      expect(left, `left pane at ${width}`).toBeGreaterThanOrEqual(38);
    }
  });

  it('keeps the chart within the pane at the narrowest split', () => {
    // The performance chart is 48 plot columns plus an 8-column axis gutter;
    // below that the visualization is the thing that breaks.
    expect(rightPaneWidth(MIN_SPLIT_WIDTH)).toBeGreaterThanOrEqual(56);
  });

  it('stops widening the pane on very wide terminals', () => {
    expect(rightPaneWidth(400)).toBe(rightPaneWidth(300));
  });
});
