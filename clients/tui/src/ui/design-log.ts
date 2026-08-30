import type {DesignFileChange, DesignRound} from '@vibesys/backend-client';

/**
 * Pure formatting for the per-round design log.
 *
 * Two surfaces share these helpers: the `/design` overlay, which summarizes
 * every round in a few lines each, and the hypothesis drill-down, which shows
 * the selected round's full change list. Neither recomputes anything: the
 * backend derives the file lists and stage outcomes, and this module only
 * lays them out.
 */

/** Rounds the overlay lists in full; earlier ones collapse into one count. */
const SUMMARY_ROUND_LIMIT = 10;
/** File names shown inline in the overlay before the rest become a count. */
const SUMMARY_FILE_LIMIT = 4;
/** Enough of a checkpoint hash to paste into git without dominating the row. */
const CHECKPOINT_WIDTH = 10;

export function fileChangeGlyph(change: DesignFileChange['change']): string {
  if (change === 'added') return '+';
  if (change === 'deleted') return '-';
  if (change === 'renamed') return '→';
  return '~';
}

export function formatFileChange(change: DesignFileChange): string {
  const glyph = fileChangeGlyph(change.change);
  if (change.change === 'renamed' && change.renamed_from) {
    return `${glyph} ${change.path} (was ${change.renamed_from})`;
  }
  return `${glyph} ${change.path}`;
}

/** Compact per-kind tally, e.g. `+2 ~1 →1`, or null when nothing changed. */
export function fileChangeCounts(files: readonly DesignFileChange[]): string | null {
  const order: ReadonlyArray<DesignFileChange['change']> = [
    'added',
    'modified',
    'deleted',
    'renamed',
  ];
  const parts = order.flatMap(kind => {
    const count = files.filter(file => file.change === kind).length;
    return count > 0 ? [`${fileChangeGlyph(kind)}${count}`] : [];
  });
  return parts.length > 0 ? parts.join(' ') : null;
}

/**
 * The round's stage conclusions on one line: empirical outcome, review,
 * official evaluation, candidate decision, and the checkpoint that holds the
 * changes. Only what the round actually recorded appears.
 */
export function designStageSummary(round: DesignRound): string | null {
  const parts: string[] = [];
  if (round.hypothesis_outcome) parts.push(`Outcome ${round.hypothesis_outcome}`);
  if (round.judge_verdict) parts.push(`Judge ${round.judge_verdict}`);
  if (round.official_evaluation === true) parts.push('Official evaluation');
  if (round.candidate_disposition) parts.push(`Candidate ${round.candidate_disposition}`);
  if (round.commit) parts.push(`Checkpoint ${round.commit.slice(0, CHECKPOINT_WIDTH)}`);
  return parts.length > 0 ? parts.join(' · ') : null;
}

/** `Round 3 · H-01 · Batch decode requests`, dropping what is not recorded. */
export function designRoundHeading(round: DesignRound): string {
  const parts = [`Round ${round.round}`];
  if (round.hypothesis_id) parts.push(round.hypothesis_id);
  const title = round.title?.trim() || round.claim?.trim();
  if (title) parts.push(title);
  return parts.join(' · ');
}

function measuredLabel(round: DesignRound): string | null {
  if (typeof round.perf_metric !== 'number') return null;
  const value = Number.isInteger(round.perf_metric)
    ? String(round.perf_metric)
    : round.perf_metric.toFixed(2).replace(/\.?0+$/, '');
  const unit = round.perf_unit ? ` ${round.perf_unit}` : '';
  const delta = round.perf_delta_pct;
  const deltaLabel =
    typeof delta === 'number'
      ? ` (${delta > 0 ? '+' : ''}${delta.toFixed(Math.abs(delta) >= 10 ? 0 : 1)}%)`
      : '';
  return `${value}${unit}${deltaLabel}`;
}

function summaryFileLine(round: DesignRound): string {
  const files = round.files ?? null;
  if (files === null) return '  file changes not recorded';
  if (files.length === 0) return '  no workspace files changed';
  const counts = fileChangeCounts(files) ?? '';
  const names = files.slice(0, SUMMARY_FILE_LIMIT).map(file => file.path);
  const more = files.length - names.length;
  const suffix = more > 0 ? `, +${more} more` : '';
  return `  ${counts}  ${names.join(', ')}${suffix}`;
}

/**
 * The `/design` overlay: every round in two lines, newest still on screen
 * because earlier rounds beyond the limit collapse into one count line. The
 * hypothesis drill-down carries the full per-round list.
 */
export function renderDesignSummary(rounds: readonly DesignRound[]): string {
  if (rounds.length === 0) {
    return [
      'Design changes by round',
      '',
      'No rounds have been recorded yet.',
      'Rounds appear here once the agent finishes its first one.',
    ].join('\n');
  }
  const shown = rounds.slice(-SUMMARY_ROUND_LIMIT);
  const omitted = rounds.length - shown.length;
  const lines = ['Design changes by round', ''];
  if (omitted > 0)
    lines.push(`(${omitted} earlier ${omitted === 1 ? 'round' : 'rounds'} not shown)`);
  for (const round of shown) {
    const measured = measuredLabel(round);
    const heading = measured
      ? `${designRoundHeading(round)} · ${measured}`
      : designRoundHeading(round);
    lines.push(heading, summaryFileLine(round));
  }
  lines.push('', 'Open a hypothesis and select a round for its full change list.');
  return lines.join('\n');
}
