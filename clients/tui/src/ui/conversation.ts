import {
  BoxRenderable,
  type CliRenderer,
  MarkdownRenderable,
  type SyntaxStyle,
  TextRenderable,
} from '@opentui/core';
import type {SessionController} from '../session-controller.js';
import type {ConversationEntry, SessionState} from '../session-model.js';
import {visibleConversation} from '../session-model.js';
import {promptPreview, toolOutputPreview} from './previews.js';
import {entryPalette} from './styles.js';
import type {Theme} from './theme.js';

export interface ConversationViewOptions {
  selectConversation?: (state: SessionState) => ConversationEntry[];
  emptyContent?: string;
  renderMarkdown?: boolean;
  /** Whether this view draws the entry cursor. */
  showsSelection?: boolean;
}

export class ConversationView {
  readonly output: BoxRenderable;
  #theme: Theme;
  #markdownStyle: SyntaxStyle;
  readonly #expandedPrompts = new Set<string>();
  readonly #selectConversation: (state: SessionState) => ConversationEntry[];
  #emptyContent: string;
  readonly #renderMarkdown: boolean;
  readonly #showsSelection: boolean;
  #renderedConversation: ConversationEntry[] = [];
  #renderedCards: BoxRenderable[] = [];
  #renderedSelection: string | null = null;
  #selectedId: string | null = null;

  constructor(
    private readonly renderer: CliRenderer,
    private readonly controller: SessionController,
    markdownStyle: SyntaxStyle,
    theme: Theme,
    options: ConversationViewOptions = {},
  ) {
    this.#markdownStyle = markdownStyle;
    this.#theme = theme;
    this.#selectConversation = options.selectConversation ?? visibleConversation;
    this.#emptyContent = options.emptyContent ?? 'Waiting for run events…';
    this.#renderMarkdown = options.renderMarkdown ?? true;
    this.#showsSelection = options.showsSelection ?? false;
    // The bordered surface owns horizontal inset so transcript siblings, such
    // as a fixed footer, share the same content origin as these turn cards.
    this.output = new BoxRenderable(renderer, {
      id: 'output',
      width: '100%',
      flexDirection: 'column',
    });
  }

  render(state: SessionState): void {
    const selection = this.#selectionFor(state);
    if (selection !== this.#renderedSelection) {
      // The cursor is drawn into the cards, so a change has to redraw them even
      // when the entries are identical.
      this.#renderedConversation = [];
      this.#renderedSelection = selection;
    }
    this.#selectedId = selection;
    this.#renderConversation(this.#selectConversation(state));
  }

  /** The transcript owns the entry cursor; the chat panes never show one. */
  #selectionFor(state: SessionState): string | null {
    return this.#showsSelection ? state.selectedEntryId : null;
  }

  /**
   * What an empty transcript says. A round with no turns because it has not run
   * is a different thing from a round whose turns have not arrived, and the
   * operator should not have to guess which one they are looking at.
   */
  setEmptyContent(content: string): void {
    if (content === this.#emptyContent) return;
    this.#emptyContent = content;
    this.#renderedConversation = [];
  }

  /** Scrolls the selected card into view; the viewport owns the scrolling. */
  selectedCard(): BoxRenderable | null {
    if (this.#selectedId === null) return null;
    const index = this.#renderedConversation.findIndex(entry => entry.id === this.#selectedId);
    return index === -1 ? null : (this.#renderedCards[index] ?? null);
  }

  applyTheme(theme: Theme, markdownStyle: SyntaxStyle): void {
    this.#theme = theme;
    this.#markdownStyle = markdownStyle;
    this.#clear();
    this.#renderedConversation = [];
  }

  toggleLatestPrompt(): void {
    const latestPrompt = [...this.#selectConversation(this.controller.state)]
      .reverse()
      .find(entry => entry.kind === 'prompt');
    if (latestPrompt) this.#togglePrompt(latestPrompt.id);
  }

  #clear(): void {
    for (const child of [...this.output.getChildren()]) {
      this.output.remove(child);
      child.destroyRecursively();
    }
    this.#renderedCards = [];
  }

  #renderConversation(entries: ConversationEntry[]): void {
    if (
      sameEntries(entries, this.#renderedConversation) &&
      (entries.length > 0 || this.output.getChildren().length > 0)
    )
      return;
    if (isEntryPrefix(this.#renderedConversation, entries)) {
      for (const entry of entries.slice(this.#renderedConversation.length)) {
        const card = this.#renderEntry(entry);
        this.output.add(card);
        this.#renderedCards.push(card);
      }
      this.#renderedConversation = entries;
      return;
    }
    const changedIndex = singleChangedEntryIndex(this.#renderedConversation, entries);
    if (changedIndex !== -1) {
      const previousCard = this.#renderedCards[changedIndex];
      const entry = entries[changedIndex];
      if (previousCard !== undefined && entry !== undefined) {
        this.output.remove(previousCard);
        previousCard.destroyRecursively();
        const card = this.#renderEntry(entry);
        this.output.add(card, changedIndex);
        this.#renderedCards[changedIndex] = card;
        this.#renderedConversation = entries;
        return;
      }
    }
    this.#clear();
    this.#renderedConversation = entries;
    if (entries.length === 0) {
      const card = new TextRenderable(this.renderer, {
        content: this.#emptyContent,
        fg: this.#theme.textSubtle,
      });
      this.output.add(card);
      return;
    }
    for (const entry of entries) {
      const card = this.#renderEntry(entry);
      this.output.add(card);
      this.#renderedCards.push(card);
    }
  }

  #togglePrompt(id: string): void {
    if (this.#expandedPrompts.has(id)) this.#expandedPrompts.delete(id);
    else this.#expandedPrompts.add(id);
    this.#renderedConversation = [];
    this.#renderConversation(this.#selectConversation(this.controller.state));
  }

  #renderEntry(entry: ConversationEntry): BoxRenderable {
    const palette = entryPalette(entry, this.#theme);
    const selected = this.#selectedId === entry.id;
    const card = new BoxRenderable(this.renderer, {
      id: `event-${entry.id}`,
      width: '100%',
      flexDirection: 'column',
      marginTop: 1,
      paddingLeft: entry.kind === 'status' ? 0 : 1,
      paddingRight: 1,
      border: entry.kind !== 'status',
      borderStyle: 'rounded',
      // The cursor is the card's border, not a fill: a filled card reads as
      // selected text, and the transcript already uses fills for roles.
      borderColor: selected ? this.#theme.borderFocus : palette.border,
      backgroundColor: palette.background,
      ...(this.#showsSelection
        ? {
            onMouseUp: () =>
              entry.kind === 'prompt'
                ? this.#togglePrompt(entry.id)
                : this.controller.selectNextEntry(0, entry.id),
          }
        : entry.kind === 'prompt'
          ? {onMouseUp: () => this.#togglePrompt(entry.id)}
          : {}),
    });
    const heading = new BoxRenderable(this.renderer, {
      id: `event-${entry.id}-heading`,
      width: '100%',
      height: 1,
      flexDirection: 'row',
      justifyContent: 'space-between',
    });
    heading.add(
      new TextRenderable(this.renderer, {
        content: `${selected ? '▸ ' : ''}${entry.label ?? entry.kind}`,
        fg: selected ? this.#theme.textStrong : palette.label,
        height: 1,
      }),
    );
    card.add(heading);
    if (
      this.#renderMarkdown &&
      (entry.kind === 'assistant' || entry.kind === 'prompt' || entry.kind === 'user')
    ) {
      this.#renderMarkdownEntry(card, entry);
    } else if (entry.kind === 'tool' && entry.toolCall) {
      this.#renderToolTurn(card, entry);
    } else {
      const prompt =
        entry.kind === 'prompt'
          ? promptPreview(entry.content, this.#expandedPrompts.has(entry.id))
          : null;
      const content = prompt
        ? prompt.content
        : entry.kind === 'tool' || entry.kind === 'diagnostic' || entry.kind === 'subprocess'
          ? toolOutputPreview(entry.content)
          : entry.content;
      card.add(new TextRenderable(this.renderer, {content, fg: palette.content, width: '100%'}));
      if (prompt && (prompt.hiddenLines > 0 || this.#expandedPrompts.has(entry.id))) {
        card.add(
          new TextRenderable(this.renderer, {
            content: this.#expandedPrompts.has(entry.id)
              ? '▴ click to collapse'
              : `▾ ${prompt.hiddenLines} more lines · click to expand`,
            fg: this.#theme.info,
            width: '100%',
          }),
        );
      }
    }
    return card;
  }

  #renderMarkdownEntry(card: BoxRenderable, entry: ConversationEntry): void {
    const expanded = this.#expandedPrompts.has(entry.id);
    const preview =
      entry.kind === 'prompt'
        ? promptPreview(entry.content, expanded)
        : {content: entry.content, hiddenLines: 0};
    card.add(
      new MarkdownRenderable(this.renderer, {
        content: preview.content,
        syntaxStyle: this.#markdownStyle,
        conceal: true,
        streaming: !this.controller.state.terminal,
        width: '100%',
      }),
    );
    if (entry.kind === 'prompt' && (preview.hiddenLines > 0 || expanded)) {
      card.add(
        new TextRenderable(this.renderer, {
          content: expanded
            ? '▴ click or Ctrl+P to collapse'
            : `▾ ${preview.hiddenLines} more lines · click or Ctrl+P to expand`,
          fg: this.#theme.info,
          width: '100%',
        }),
      );
    }
  }

  #renderToolTurn(card: BoxRenderable, entry: ConversationEntry): void {
    card.add(
      new TextRenderable(this.renderer, {
        content: entry.toolCall?.trimEnd() ?? '',
        fg: this.#theme.toolCall.foreground,
        bg: this.#theme.toolCall.background,
        width: '100%',
      }),
    );
    if (entry.toolResponse) {
      card.add(
        new TextRenderable(this.renderer, {
          content: toolOutputPreview(entry.toolResponse),
          fg: this.#theme.toolResult.foreground,
          bg: this.#theme.toolResult.background,
          width: '100%',
        }),
      );
    }
  }
}

function sameEntries(left: ConversationEntry[], right: ConversationEntry[]): boolean {
  return left.length === right.length && left.every((entry, index) => entry === right[index]);
}

function isEntryPrefix(prefix: ConversationEntry[], entries: ConversationEntry[]): boolean {
  return (
    prefix.length > 0 &&
    prefix.length < entries.length &&
    prefix.every((entry, index) => entry === entries[index])
  );
}

function singleChangedEntryIndex(
  previous: ConversationEntry[],
  entries: ConversationEntry[],
): number {
  if (previous.length === 0 || previous.length !== entries.length) return -1;
  let changedIndex = -1;
  for (let index = 0; index < entries.length; index += 1) {
    if (previous[index] === entries[index]) continue;
    if (changedIndex !== -1 || previous[index]?.id !== entries[index]?.id) return -1;
    changedIndex = index;
  }
  return changedIndex;
}
