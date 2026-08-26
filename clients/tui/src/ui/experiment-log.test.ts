import {describe, expect, it} from 'bun:test';
import type {HypothesisEntry} from '@vibesys/backend-client';
import {
  entryKey,
  initialSessionState,
  moveExperimentSelection,
  openExperimentLog,
  type SessionState,
  setExperiments,
} from '../session-model.js';
import {
  entryRow,
  formatMeasured,
  formatRounds,
  headerRow,
  outcomeColor,
  resolveColumns,
  sentenceCase,
} from './experiment-log.js';
import {resolveTheme, THEME_NAMES} from './theme.js';

const WIDE = 120;
const NARROW = 44;

function entry(overrides: Partial<HypothesisEntry> = {}): HypothesisEntry {
  return {
    hypothesis_id: 'H-07',
    identified: true,
    claim: 'batch the prefill step',
    action: 'batch prefill',
    first_round: 41,
    last_round: 41,
    rounds: [],
    resolved_outcome: 'proven',
    judge_verdict: 'pass',
    perf_delta_pct: 12,
    kept: true,
    active: false,
    ...overrides,
  };
}

function logState(entries: HypothesisEntry[]): SessionState {
  return setExperiments(openExperimentLog(initialSessionState()), entries);
}

describe('experiment log rows', () => {
  it('renders the columns the issue asks for at a wide terminal', () => {
    const columns = resolveColumns(WIDE);
    const header = headerRow(columns);
    const row = entryRow(entry(), columns);

    for (const label of [
      'Hypothesis',
      'Rounds',
      'Implementation Details',
      'Measured',
      'Verdict',
      'Outcome',
      'Kept',
    ]) {
      expect(header).toContain(label);
    }
    expect(row).toContain('H-07');
    expect(row).toContain('41');
    expect(row).toContain('Batch the prefill step');
    expect(row).toContain('+12%');
    expect(row).toContain('Pass');
    expect(row).toContain('Proven');
    expect(row.trimEnd().endsWith('Yes')).toBe(true);
  });

  it('keeps hypothesis, rounds, and outcome when the terminal is narrow', () => {
    const columns = resolveColumns(NARROW);

    expect(columns.claim).toBe(false);
    expect(columns.kept).toBe(false);
    const row = entryRow(entry(), columns);
    expect(row).toContain('H-07');
    expect(row).toContain('41');
    expect(row).toContain('Proven');
    expect(row).not.toContain('Batch the prefill step');
  });

  it('shows a round range for a hypothesis spanning continuations', () => {
    expect(formatRounds(entry({first_round: 42, last_round: 43}))).toBe('42-43');
    expect(formatRounds(entry({first_round: 44, last_round: 44}))).toBe('44');
  });

  it('marks the active hypothesis and leaves its outcome open', () => {
    const row = entryRow(
      entry({active: true, resolved_outcome: null, judge_verdict: null, perf_delta_pct: null}),
      resolveColumns(WIDE),
    );

    expect(row.startsWith('▸')).toBe(true);
    expect(row).toContain('Active');
  });

  it('falls back from a delta to an absolute metric and then to a placeholder', () => {
    expect(formatMeasured(entry({perf_delta_pct: -2}))).toBe('-2.0%');
    expect(formatMeasured(entry({perf_delta_pct: null, perf_metric: 2412.5}))).toBe('2412.5');
    expect(formatMeasured(entry({perf_delta_pct: null, perf_metric: null}))).toBe('—');
  });

  it('renders a record with no hypothesis id as an explicit placeholder', () => {
    const row = entryRow(
      entry({
        hypothesis_id: '(unidentified)',
        identified: false,
        claim: null,
        action: null,
        resolved_outcome: null,
        perf_delta_pct: null,
      }),
      resolveColumns(WIDE),
    );

    expect(row).toContain('(unidentified)');
    expect(row).toContain('—');
  });
});

describe('experiment log layout', () => {
  it('fits the panel exactly at every width it degrades through', () => {
    for (const width of [120, 104, 90, 72, 62, 54, 40]) {
      const columns = resolveColumns(width);
      const header = headerRow(columns);
      const row = entryRow(entry(), columns);
      expect(header.length, `header at ${width}`).toBeLessThanOrEqual(width);
      expect(row.length, `row at ${width}`).toBeLessThanOrEqual(width);
    }
  });
});

describe('experiment log outcome color', () => {
  it('reads green for a hypothesis that held and red for one that did not', () => {
    const theme = resolveTheme('dark');

    expect(outcomeColor(theme, entry({resolved_outcome: 'proven'}))).toBe(theme.success);
    expect(outcomeColor(theme, entry({resolved_outcome: 'disproven'}))).toBe(theme.error);
    expect(outcomeColor(theme, entry({resolved_outcome: 'rejected'}))).toBe(theme.error);
  });

  it('leaves outcomes with no verdict reading in body text', () => {
    const theme = resolveTheme('dark');

    expect(outcomeColor(theme, entry({resolved_outcome: 'continue'}))).toBe(theme.textPrimary);
    expect(outcomeColor(theme, entry({resolved_outcome: 'inconclusive'}))).toBe(theme.textPrimary);
  });

  it('uses the active accent while a hypothesis is still open', () => {
    const theme = resolveTheme('dark');
    const open = entry({active: true, resolved_outcome: null});

    expect(outcomeColor(theme, open)).toBe(theme.warning);
  });

  it('takes every color from the selected theme, never a literal', () => {
    for (const name of THEME_NAMES) {
      const theme = resolveTheme(name);
      expect(outcomeColor(theme, entry({resolved_outcome: 'proven'}))).toBe(theme.success);
      expect(outcomeColor(theme, entry({resolved_outcome: 'disproven'}))).toBe(theme.error);
    }
  });

  it('spells the outcome out so color is never the only signal', () => {
    const columns = resolveColumns(WIDE);

    expect(entryRow(entry({resolved_outcome: 'proven'}), columns)).toContain('Proven');
    expect(entryRow(entry({resolved_outcome: 'disproven'}), columns)).toContain('Disproven');
  });
});

describe('sentenceCase', () => {
  it('capitalises the first letter and leaves the rest alone', () => {
    expect(sentenceCase('batch the prefill step')).toBe('Batch the prefill step');
    expect(sentenceCase('implementation_failed')).toBe('Implementation_failed');
    expect(sentenceCase('KV cache block')).toBe('KV cache block');
    expect(sentenceCase('—')).toBe('—');
    expect(sentenceCase('')).toBe('');
  });
});

describe('experiment log selection', () => {
  it('keys placeholder rows by round so duplicates stay distinct', () => {
    const rows = [
      entry({hypothesis_id: '(unidentified)', identified: false, first_round: 1, last_round: 1}),
      entry({hypothesis_id: '(unidentified)', identified: false, first_round: 2, last_round: 2}),
    ];

    expect(rows.map(entryKey)).toEqual(['(unidentified)#1', '(unidentified)#2']);
  });

  it('starts on the active hypothesis and clamps at both ends', () => {
    let state = logState([
      entry({hypothesis_id: 'H-01', first_round: 1, last_round: 1}),
      entry({hypothesis_id: 'H-02', first_round: 2, last_round: 2, active: true}),
    ]);
    expect(state.experimentLog?.selectedId).toBe('H-02');

    state = moveExperimentSelection(state, 5);
    expect(state.experimentLog?.selectedId).toBe('H-02');

    state = moveExperimentSelection(state, -5);
    expect(state.experimentLog?.selectedId).toBe('H-01');
  });

  it('drops a selection whose hypothesis disappears from the log', () => {
    const first = logState([entry({hypothesis_id: 'H-01', first_round: 1, last_round: 1})]);
    const replaced = setExperiments(first, [
      entry({hypothesis_id: 'H-99', first_round: 9, last_round: 9}),
    ]);

    expect(replaced.experimentLog?.selectedId).toBe('H-99');
  });
});
