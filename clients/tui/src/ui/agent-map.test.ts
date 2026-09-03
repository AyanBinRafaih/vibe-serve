import {describe, expect, test} from 'bun:test';
import {agentPaneWidth, STACKED_WIDTH, TRANSCRIPT_MIN} from './agent-map.js';
import {RAIL_COMPACT_WIDTH, roundRailWidth} from './round-rail.js';

/**
 * The round view lays out rail -> agents -> transcript. app.ts sizes the agent
 * pane against the room left of the rail (`terminalWidth - railWidth`), then the
 * transcript fills the remainder. These tests reproduce that pipeline and pin
 * the transcript floor across the rail breakpoints: at widths 72-84 the compact
 * rail must not appear and squeeze the transcript below its minimum.
 */
describe('agentPaneWidth transcript floor beside the rail', () => {
  /** The transcript width app.ts would give this terminal for a round shape. */
  function transcriptWidth(terminalWidth: number, stageCount: number): number {
    const railWidth = roundRailWidth(terminalWidth);
    const available = terminalWidth - railWidth;
    // app.ts turns a null pane width into the stacked fallback, exactly as the
    // finding described; the floor has to hold through that fallback too.
    const paneWidth = agentPaneWidth(available, stageCount) ?? STACKED_WIDTH;
    return available - paneWidth;
  }

  test('holds the transcript floor from the collapse width up, for every round shape', () => {
    // 72 = STACKED_WIDTH (30) + TRANSCRIPT_MIN (42): the narrowest width where
    // both floors can coexist at all. Below it neither the rail nor the agents
    // pane can keep the transcript readable, so the run view is not expected to.
    for (let terminalWidth = 72; terminalWidth <= 140; terminalWidth += 1) {
      for (const stageCount of [1, 2, 3, 4, 6]) {
        expect(transcriptWidth(terminalWidth, stageCount)).toBeGreaterThanOrEqual(TRANSCRIPT_MIN);
      }
    }
  });

  test('keeps the exact widths the finding measured above the floor', () => {
    // The reviewer saw transcript widths 29, 37, and 41 at these terminals while
    // a 13-column rail was visible; the rail now collapses there instead.
    for (const terminalWidth of [72, 80, 84]) {
      expect(roundRailWidth(terminalWidth)).toBe(0);
      for (const stageCount of [1, 2, 3, 4]) {
        expect(transcriptWidth(terminalWidth, stageCount)).toBeGreaterThanOrEqual(TRANSCRIPT_MIN);
      }
    }
  });

  test('the compact rail only appears where both floors still fit beside it', () => {
    for (let terminalWidth = 60; terminalWidth <= 140; terminalWidth += 1) {
      if (roundRailWidth(terminalWidth) !== RAIL_COMPACT_WIDTH) continue;
      // rail + agents floor + transcript floor never exceeds the terminal.
      expect(terminalWidth - RAIL_COMPACT_WIDTH - STACKED_WIDTH).toBeGreaterThanOrEqual(
        TRANSCRIPT_MIN,
      );
    }
  });
});
