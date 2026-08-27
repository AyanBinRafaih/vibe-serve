import type {SlashCommand} from '../commands.js';

/**
 * Selection, navigation, and Tab-completion mechanics shared by every slash
 * command suggestion list in the client: the command bar's global commands
 * and the chat composer's own command set. Owns the current matches and the
 * highlighted index; callers own when matches are recomputed and how (or
 * whether) the result is drawn.
 */
export class SuggestionMenu {
  #matches: readonly SlashCommand[] = [];
  #selectedIndex = 0;

  get matches(): readonly SlashCommand[] {
    return this.#matches;
  }

  get selectedIndex(): number {
    return this.#selectedIndex;
  }

  get visible(): boolean {
    return this.#matches.length > 0;
  }

  /** Replaces the matches for newly typed text. The highlight resets to the first match. */
  setMatches(matches: readonly SlashCommand[]): void {
    this.#matches = matches;
    this.#selectedIndex = 0;
  }

  /** Moves the highlight. Returns false, and does nothing, when there is nothing to navigate. */
  navigate(direction: 1 | -1): boolean {
    if (this.#matches.length === 0) return false;
    this.#selectedIndex =
      (this.#selectedIndex + direction + this.#matches.length) % this.#matches.length;
    return true;
  }

  /**
   * The command name to fill in for the highlighted match, or null when
   * there is no highlighted match or it already equals `value`.
   */
  complete(value: string): string | null {
    const suggestion = this.#matches[this.#selectedIndex];
    if (suggestion === undefined || suggestion.name === value) return null;
    this.#selectedIndex = 0;
    return suggestion.name;
  }

  /** Render-ready lines, one per match, marking the highlighted row. */
  renderLines(value: string): string[] {
    return this.#matches.map(
      (command, index) =>
        `${index === this.#selectedIndex ? '›' : ' '} ${command.name.padEnd(10)} ${command.description}${
          index === this.#selectedIndex && command.name !== value ? '  [Tab]' : ''
        }`,
    );
  }
}
