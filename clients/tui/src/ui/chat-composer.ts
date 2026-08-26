import {
  BoxRenderable,
  type CliRenderer,
  type KeyEvent,
  TextareaRenderable,
  TextRenderable,
} from '@opentui/core';
import type {Theme} from './theme.js';

const MIN_EDITOR_ROWS = 1;
const MAX_EDITOR_ROWS = 6;
const COMPOSER_CHROME = 3;
const EDITOR_HORIZONTAL_CHROME = 4;

class ChatTextareaRenderable extends TextareaRenderable {
  override handleKeyPress(key: KeyEvent): boolean {
    if (key.name === 'return' || key.name === 'kpenter' || key.name === 'linefeed') {
      if (key.shift) return this.newLine();
      if (!key.ctrl && !key.meta && !key.super && !key.hyper) return this.submit();
    }
    return super.handleKeyPress(key);
  }
}

/** Draft shared by the docked and modal presentations of experiment chat. */
export interface ChatDraft {
  value: string;
}

export function createChatDraft(): ChatDraft {
  return {value: ''};
}

/**
 * The single experiment-chat composer used by every chat presentation.
 *
 * OpenTUI renderables cannot have two parents, so the dock and modal each own
 * an instance while sharing the draft above. Visibility transitions copy the
 * authoritative draft into the newly visible editor. This keeps resizing from
 * losing a partially written question without coupling either view to the
 * other's render tree.
 */
export class ChatComposerView {
  readonly output: BoxRenderable;
  readonly #box: BoxRenderable;
  readonly #editor: ChatTextareaRenderable;
  readonly #hint: TextRenderable;
  #availableWidth = 1;
  #focused = false;
  #theme: Theme;

  constructor(
    renderer: CliRenderer,
    private readonly draft: ChatDraft,
    private readonly onSubmit: (value: string) => void,
    theme: Theme,
    id: string,
    private readonly onFocusRequest: () => void = () => {},
  ) {
    this.#theme = theme;
    this.output = new BoxRenderable(renderer, {
      id: `${id}-composer`,
      width: '100%',
      height: COMPOSER_CHROME + MIN_EDITOR_ROWS,
      flexDirection: 'column',
      flexShrink: 0,
    });
    this.#box = new BoxRenderable(renderer, {
      id: `${id}-composer-box`,
      width: '100%',
      height: MIN_EDITOR_ROWS + 2,
      border: true,
      borderStyle: 'rounded',
      borderColor: theme.border,
      title: ' Message ',
      paddingLeft: 1,
      paddingRight: 1,
      onMouseUp: this.onFocusRequest,
    });
    this.#editor = new ChatTextareaRenderable(renderer, {
      id: `${id}-composer-editor`,
      width: '100%',
      height: MIN_EDITOR_ROWS,
      initialValue: draft.value,
      placeholder: 'Ask about this experiment',
      wrapMode: 'word',
      textColor: theme.textStrong,
      focusedTextColor: theme.textStrong,
      onMouseUp: this.onFocusRequest,
      onContentChange: () => {
        this.draft.value = this.#editor.plainText;
        this.#resize();
      },
      onSubmit: () => this.#submit(),
    });
    this.#hint = new TextRenderable(renderer, {
      id: `${id}-composer-hint`,
      width: '100%',
      height: 1,
      wrapMode: 'none',
      truncate: true,
      fg: theme.textSubtle,
      content: 'Enter: send · Shift+Enter: newline',
    });
    this.#box.add(this.#editor);
    this.output.add(this.#box);
    this.output.add(this.#hint);
  }

  /** Makes this editor authoritative when its presentation becomes visible. */
  activate(availableWidth: number, focused: boolean, pending: boolean): void {
    this.#availableWidth = Math.max(1, availableWidth);
    if (this.#editor.plainText !== this.draft.value) this.#editor.setText(this.draft.value);
    this.setFocused(focused);
    this.#box.title = pending ? ' Message · awaiting agent ' : ' Message ';
    this.#hint.content = pending
      ? 'Awaiting the agent · Enter: queue follow-up'
      : focused
        ? 'Enter: send · Shift+Enter: newline'
        : 'Ctrl+W to type here';
    this.#resize();
  }

  isEmpty(): boolean {
    return this.draft.value.trim() === '';
  }

  focus(): void {
    this.#editor.focus();
  }

  setFocused(focused: boolean): void {
    this.#focused = focused;
    this.#box.borderColor = focused ? this.#theme.borderFocus : this.#theme.border;
  }

  applyTheme(theme: Theme): void {
    this.#theme = theme;
    this.#box.borderColor = this.#focused ? theme.borderFocus : theme.border;
    this.#editor.textColor = theme.textStrong;
    this.#editor.focusedTextColor = theme.textStrong;
    this.#hint.fg = theme.textSubtle;
  }

  #resize(): void {
    const contentWidth = Math.max(1, this.#availableWidth - EDITOR_HORIZONTAL_CHROME);
    const rows = Math.min(
      MAX_EDITOR_ROWS,
      Math.max(MIN_EDITOR_ROWS, wrappedRows(this.draft.value, contentWidth)),
    );
    this.#editor.height = rows;
    this.#box.height = rows + 2;
    this.output.height = rows + COMPOSER_CHROME;
  }

  #submit(): void {
    const value = this.draft.value;
    if (!value.trim()) return;
    this.draft.value = '';
    this.#editor.clear();
    this.#resize();
    this.onSubmit(value);
  }
}

function wrappedRows(value: string, width: number): number {
  if (value.length === 0) return 1;
  return value
    .split('\n')
    .reduce((rows, line) => rows + Math.max(1, Math.ceil([...line].length / width)), 0);
}
