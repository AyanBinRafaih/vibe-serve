import {BoxRenderable, type CliRenderer, TextRenderable} from '@opentui/core';
import {hasActiveAgentTiming, type RoundSummary, roundAgentElapsedMs} from '@vibesys/core-state';
import type {SessionController} from '../session-controller.js';
import type {SessionState} from '../session-model.js';
import {stripRounds, visibleRoundNumber} from '../session-model.js';
import {elapsedLabel} from './previews.js';
import type {Theme} from './theme.js';

/** Border on both sides plus a column of padding on both sides. */
const STRIP_CHROME = 4;
/** Width of a `‹ 12 more` marker, so the window leaves room for one per side. */
const MARKER_WIDTH = 10;

interface StripWindow {
  rounds: RoundSummary[];
  hiddenBefore: number;
  hiddenAfter: number;
}

/**
 * The rounds that fit, always including the selected one.
 *
 * A run is normally longer than the strip is wide, so the strip is a window
 * onto it rather than the whole list. The window follows the selection instead
 * of pinning to either end: moving off the visible set slides it by one, which
 * is what makes holding `[` feel like the run scrolling past rather than
 * jumping a page at a time.
 */
export function stripWindow(
  rounds: RoundSummary[],
  selected: number | null,
  availableWidth: number,
  labelWidth: (round: RoundSummary) => number,
): StripWindow {
  if (rounds.length === 0) return {rounds: [], hiddenBefore: 0, hiddenAfter: 0};
  const index = Math.max(
    0,
    rounds.findIndex(round => round.number === selected),
  );
  let first = index;
  let last = index;
  let used = labelWidth(rounds[index] as RoundSummary);
  // Grow outward from the selection, preferring the side that still has rounds,
  // so a selection near either end still fills the strip.
  for (;;) {
    const before = first > 0 ? labelWidth(rounds[first - 1] as RoundSummary) : null;
    const after = last < rounds.length - 1 ? labelWidth(rounds[last + 1] as RoundSummary) : null;
    if (before === null && after === null) break;
    const reserve =
      (first - 1 > 0 || (before === null && first > 0) ? MARKER_WIDTH : 0) +
      (last + 1 < rounds.length - 1 ? MARKER_WIDTH : 0);
    // Take from whichever side is closer to the selection, so the window stays
    // centred on it as it moves.
    const takeAfter =
      after !== null && (before === null || last - index <= index - first) ? after : null;
    const width = takeAfter ?? before;
    if (width === null) break;
    if (used + width + reserve > availableWidth) break;
    used += width;
    if (takeAfter !== null) last += 1;
    else first -= 1;
  }
  return {
    rounds: rounds.slice(first, last + 1),
    hiddenBefore: first,
    hiddenAfter: rounds.length - 1 - last,
  };
}

export class RoundStripView {
  readonly output: BoxRenderable;
  #theme: Theme;
  #renderedState: SessionState | null = null;
  #renderedWidth = 0;
  #elapsedTimer: ReturnType<typeof setInterval> | null = null;
  #runningRound: {
    round: RoundSummary;
    selected: number | null;
    text: TextRenderable;
  } | null = null;
  /** The chips currently on screen, by round number, for in-place repaints. */
  readonly #chips = new Map<number, TextRenderable>();

  constructor(
    private readonly renderer: CliRenderer,
    private readonly controller: SessionController,
    theme: Theme,
  ) {
    this.#theme = theme;
    this.output = new BoxRenderable(renderer, {
      id: 'round-strip',
      width: '100%',
      height: 3,
      border: true,
      borderStyle: 'rounded',
      borderColor: theme.borderStrong,
      paddingLeft: 1,
      paddingRight: 1,
      title: ' Rounds ',
    });
  }

  applyTheme(theme: Theme): void {
    this.#theme = theme;
    this.output.borderColor = theme.borderStrong;
    this.#renderedState = null;
  }

  render(state: SessionState): void {
    const width = this.renderer.terminalWidth;
    if (state === this.#renderedState && width === this.#renderedWidth) return;
    this.#renderedState = state;
    this.#renderedWidth = width;
    // Stepping through rounds changes which chip is marked far more often than
    // it changes which chips are on screen. Repainting the ones already there
    // keeps navigation smooth; the strip is only rebuilt when the window itself
    // moves.
    if (this.#repaint(state)) return;
    this.#clear();
    // Every round of the run, including ones it has not reached: the strip
    // shows the shape of the whole run, not only what has happened so far.
    const rounds = stripRounds(state);
    if (rounds.length === 0) {
      this.output.add(
        new TextRenderable(this.renderer, {
          content: 'Waiting for rounds...',
          fg: this.#theme.textSubtle,
          width: '100%',
        }),
      );
      return;
    }
    const row = new BoxRenderable(this.renderer, {
      id: 'round-strip-row',
      width: '100%',
      flexDirection: 'row',
    });
    const selected = visibleRoundNumber(state);
    const runningRound = latestActiveRoundNumber(rounds);
    const view = stripWindow(
      rounds,
      selected,
      width - STRIP_CHROME,
      round => this.#roundLabel(round, selected).length,
    );
    // The markers are the strip saying the run continues past its edge, and
    // they move the selection so the hidden rounds are reachable by mouse too.
    if (view.hiddenBefore > 0) {
      row.add(this.#marker(`‹ ${view.hiddenBefore} `, -1));
    }
    for (const round of view.rounds) {
      row.add(this.#renderRound(round, {selected, runningRound}));
    }
    if (view.hiddenAfter > 0) {
      row.add(this.#marker(` ${view.hiddenAfter} ›`, 1));
    }
    this.output.add(row);
    this.#syncElapsedTimer();
  }

  destroy(): void {
    this.#stopElapsedTimer();
  }

  /**
   * Repaints the chips in place when the window is unchanged. Returns false
   * when the strip has to be rebuilt, which is when the window slid, the run
   * grew, or nothing has been drawn yet.
   */
  #repaint(state: SessionState): boolean {
    if (this.#chips.size === 0) return false;
    const rounds = stripRounds(state);
    const selected = visibleRoundNumber(state);
    const runningRound = latestActiveRoundNumber(rounds);
    const view = stripWindow(
      rounds,
      selected,
      this.renderer.terminalWidth - STRIP_CHROME,
      round => this.#roundLabel(round, selected).length,
    );
    if (view.rounds.length !== this.#chips.size) return false;
    if (!view.rounds.every(round => this.#chips.has(round.number))) return false;
    this.#runningRound = null;
    for (const round of view.rounds) {
      const chip = this.#chips.get(round.number);
      if (chip === undefined) return false;
      const isSelected = round.number === selected;
      const isRunning = round.number === runningRound;
      const colors = this.#roundColors(round, isSelected, isRunning);
      chip.content = this.#roundLabel(round, selected);
      chip.fg = colors.fg;
      chip.bg = colors.bg ?? this.#theme.canvas;
      if (isRunning && hasActiveAgentTiming(round)) {
        this.#runningRound = {round, selected, text: chip};
      }
    }
    this.#syncElapsedTimer();
    return true;
  }

  #marker(content: string, direction: number): TextRenderable {
    return new TextRenderable(this.renderer, {
      content,
      fg: this.#theme.textSubtle,
      onMouseUp: () => {
        if (direction < 0) this.controller.selectPreviousRound();
        else this.controller.selectNextRound();
      },
    });
  }

  #clear(): void {
    this.#runningRound = null;
    this.#chips.clear();
    this.#stopElapsedTimer();
    for (const child of [...this.output.getChildren()]) {
      this.output.remove(child);
      child.destroyRecursively();
    }
  }

  #renderRound(
    round: RoundSummary,
    viewState: {selected: number | null; runningRound: number | null},
  ): TextRenderable {
    const {selected, runningRound} = viewState;
    const isSelected = round.number === selected;
    const isRunning = round.number === runningRound;
    const text = new TextRenderable(this.renderer, {
      content: this.#roundLabel(round, selected),
      ...this.#roundColors(round, isSelected, isRunning),
      onMouseUp: () => this.controller.selectRound(round.number),
    });
    if (isRunning && hasActiveAgentTiming(round)) this.#runningRound = {round, selected, text};
    this.#chips.set(round.number, text);
    return text;
  }

  /**
   * The round being viewed is marked twice over: the brackets say which one it
   * is, and the theme's accent on its selected surface makes it findable at a
   * glance in a strip of twenty. Colour alone would fail the operator whose
   * terminal drops it, brackets alone are easy to lose in a long strip.
   */
  #roundColors(
    round: RoundSummary,
    isSelected: boolean,
    isRunning: boolean,
  ): {fg: string; bg?: string; attributes?: number} {
    if (isSelected) {
      return {fg: this.#theme.accent, bg: this.#theme.selectedSurface};
    }
    if (isRunning) return {fg: this.#theme.success};
    if (round.status === 'planned') return {fg: this.#theme.textSubtle};
    if (round.status === 'failed') return {fg: this.#theme.error};
    return {fg: this.#theme.textPrimary};
  }

  #roundLabel(round: RoundSummary, selected: number | null): string {
    const isSelected = round.number === selected;
    const elapsed =
      round.status === 'active' ? ` ${elapsedLabel(roundAgentElapsedMs(round, new Date()))}` : '';
    return `${isSelected ? '[' : ' '} r${round.number}${elapsed} ${isSelected ? ']' : ' '}`;
  }

  #syncElapsedTimer(): void {
    if (this.#runningRound === null || this.#elapsedTimer !== null) return;
    this.#elapsedTimer = setInterval(() => {
      if (this.#runningRound === null) return;
      const {round, selected, text} = this.#runningRound;
      text.content = this.#roundLabel(round, selected);
    }, 1000);
  }

  #stopElapsedTimer(): void {
    if (this.#elapsedTimer === null) return;
    clearInterval(this.#elapsedTimer);
    this.#elapsedTimer = null;
  }
}

function latestActiveRoundNumber(rounds: RoundSummary[]): number | null {
  return [...rounds].reverse().find(round => round.status === 'active')?.number ?? null;
}
