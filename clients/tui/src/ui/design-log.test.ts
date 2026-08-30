import {describe, expect, it} from 'bun:test';
import type {DesignRound} from '@vibesys/backend-client';
import {
  designRoundHeading,
  designStageSummary,
  fileChangeCounts,
  fileChangeGlyph,
  formatFileChange,
  renderDesignSummary,
} from './design-log.js';

function round(overrides: Partial<DesignRound> = {}): DesignRound {
  return {round: 1, ...overrides};
}

describe('file change formatting', () => {
  it('gives each change kind its own glyph', () => {
    expect(fileChangeGlyph('added')).toBe('+');
    expect(fileChangeGlyph('deleted')).toBe('-');
    expect(fileChangeGlyph('renamed')).toBe('→');
    expect(fileChangeGlyph('modified')).toBe('~');
  });

  it('names the old path of a rename and only that', () => {
    expect(
      formatFileChange({path: 'src/lib.rs', change: 'renamed', renamed_from: 'src/queue.rs'}),
    ).toBe('→ src/lib.rs (was src/queue.rs)');
    expect(formatFileChange({path: 'src/ring.rs', change: 'added'})).toBe('+ src/ring.rs');
    expect(formatFileChange({path: 'src/ffi.rs', change: 'deleted'})).toBe('- src/ffi.rs');
  });

  it('tallies per kind in a fixed order and drops empty kinds', () => {
    expect(
      fileChangeCounts([
        {path: 'a', change: 'modified'},
        {path: 'b', change: 'added'},
        {path: 'c', change: 'added'},
        {path: 'd', change: 'renamed', renamed_from: 'e'},
      ]),
    ).toBe('+2 ~1 →1');
    expect(fileChangeCounts([])).toBeNull();
  });
});

describe('designStageSummary', () => {
  it('lists only what the round recorded', () => {
    expect(
      designStageSummary(
        round({
          hypothesis_outcome: 'proven',
          judge_verdict: 'pass',
          official_evaluation: true,
          candidate_disposition: 'retained',
          commit: '0123456789abcdef0123456789abcdef01234567',
        }),
      ),
    ).toBe(
      'Outcome proven · Judge pass · Official evaluation · Candidate retained · Checkpoint 0123456789',
    );
  });

  it('is absent for a round with no recorded stages', () => {
    expect(designStageSummary(round())).toBeNull();
  });
});

describe('designRoundHeading', () => {
  it('prefers the title and falls back to the claim', () => {
    expect(designRoundHeading(round({hypothesis_id: 'H-01', title: 'Pad the indices'}))).toBe(
      'Round 1 · H-01 · Pad the indices',
    );
    expect(designRoundHeading(round({hypothesis_id: 'H-01', claim: 'Padding helps'}))).toBe(
      'Round 1 · H-01 · Padding helps',
    );
    expect(designRoundHeading(round())).toBe('Round 1');
  });
});

describe('renderDesignSummary', () => {
  it('explains an empty log instead of rendering nothing', () => {
    expect(renderDesignSummary([])).toContain('No rounds have been recorded yet.');
  });

  it('renders a heading and a file line per round', () => {
    const rendered = renderDesignSummary([
      round({
        hypothesis_id: 'H-01',
        title: 'Pad the indices',
        perf_metric: 2400,
        perf_unit: 'ops/s',
        perf_delta_pct: 12.5,
        files: [
          {path: 'src/ring.rs', change: 'added'},
          {path: 'src/lib.rs', change: 'modified'},
        ],
      }),
    ]);
    expect(rendered).toContain('Round 1 · H-01 · Pad the indices · 2400 ops/s (+13%)');
    expect(rendered).toContain('  +1 ~1  src/ring.rs, src/lib.rs');
  });

  it('distinguishes unrecorded changes from a round that changed nothing', () => {
    const rendered = renderDesignSummary([round({round: 1}), round({round: 2, files: []})]);
    expect(rendered).toContain('file changes not recorded');
    expect(rendered).toContain('no workspace files changed');
  });

  it('elides file names past the inline limit into a count', () => {
    const files = Array.from({length: 6}, (_, index) => ({
      path: `src/file-${index}.rs`,
      change: 'modified' as const,
    }));
    const rendered = renderDesignSummary([round({files})]);
    expect(rendered).toContain('src/file-3.rs, +2 more');
    expect(rendered).not.toContain('src/file-4.rs');
  });

  it('keeps the newest rounds and collapses the earlier ones into a count', () => {
    const rounds = Array.from({length: 12}, (_, index) => round({round: index + 1}));
    const rendered = renderDesignSummary(rounds);
    expect(rendered).toContain('(2 earlier rounds not shown)');
    expect(rendered).toContain('Round 12');
    expect(rendered).not.toContain('Round 2\n');
  });
});
