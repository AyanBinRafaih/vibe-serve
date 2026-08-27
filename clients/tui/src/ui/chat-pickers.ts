import {BoxRenderable, type CliRenderer, TextRenderable} from '@opentui/core';
import {
  CHAT_DRIVER_PROVIDERS,
  CHAT_DRIVERS,
  chatThreadLabel,
  chatThreads,
  type SessionState,
} from '../session-model.js';
import type {Theme} from './theme.js';

const NAME_WIDTH = 20;

/**
 * The chat thread list as a keyboard selection, following the theme picker's
 * shape: the highlighted row is the one Enter switches to, and the active
 * thread is spelled out so the two are never confused when they differ.
 */
export class ChatThreadPickerView {
  readonly output: BoxRenderable;
  #theme: Theme;
  #renderedRows: string | null = null;

  constructor(
    private readonly renderer: CliRenderer,
    theme: Theme,
  ) {
    this.#theme = theme;
    this.output = new BoxRenderable(renderer, {
      id: 'chat-thread-picker',
      width: '70%',
      height: '60%',
      position: 'absolute',
      left: '15%',
      top: '18%',
      flexDirection: 'column',
      paddingLeft: 1,
      paddingRight: 1,
      border: true,
      borderStyle: 'rounded',
      borderColor: theme.info,
      backgroundColor: theme.elevatedSurface,
      title: ' Chat threads ',
      visible: false,
      zIndex: 30,
    });
  }

  applyTheme(theme: Theme): void {
    this.#theme = theme;
    this.output.borderColor = theme.info;
    this.output.backgroundColor = theme.elevatedSurface;
    this.#renderedRows = null;
  }

  render(state: SessionState): void {
    const picker = state.chatThreadPicker;
    if (picker === null) {
      this.output.visible = false;
      this.#renderedRows = null;
      return;
    }
    this.output.visible = true;
    const threads = chatThreads(state);
    const fingerprint = JSON.stringify([picker.selected, state.activeChatThreadId, threads]);
    if (this.#renderedRows === fingerprint) return;
    this.#renderedRows = fingerprint;
    clearChildren(this.output);
    for (const thread of threads) {
      const selected = thread.id === picker.selected;
      const active = thread.id === state.activeChatThreadId;
      const marker = selected ? '›' : ' ';
      const agent =
        thread.driver === null ? 'run agent' : `${thread.driver}/${thread.provider ?? '?'}`;
      const model = thread.model === null ? '' : ` · ${thread.model}`;
      const suffix = active ? ' · active' : '';
      this.output.add(
        new TextRenderable(this.renderer, {
          content: `${marker} ${chatThreadLabel(state, thread.id).padEnd(NAME_WIDTH)} ${agent}${model}${suffix}`,
          fg: selected ? this.#theme.textStrong : this.#theme.textPrimary,
          bg: selected ? this.#theme.selectedSurface : this.#theme.elevatedSurface,
          width: '100%',
        }),
      );
    }
    this.output.add(new TextRenderable(this.renderer, {content: '', width: '100%'}));
    this.output.add(
      new TextRenderable(this.renderer, {
        content: '↑↓: select · Enter: switch · Esc: close · /new-chat starts another thread',
        fg: this.#theme.textSubtle,
        width: '100%',
      }),
    );
  }
}

/**
 * The new-thread wizard: driver, then a provider that driver supports, then a
 * free-text model. The client only collects the choice; the backend resolves
 * defaults and is the authority on what combinations exist.
 */
export class NewChatPickerView {
  readonly output: BoxRenderable;
  #theme: Theme;
  #renderedRows: string | null = null;

  constructor(
    private readonly renderer: CliRenderer,
    theme: Theme,
  ) {
    this.#theme = theme;
    this.output = new BoxRenderable(renderer, {
      id: 'new-chat-picker',
      width: '70%',
      height: '60%',
      position: 'absolute',
      left: '15%',
      top: '18%',
      flexDirection: 'column',
      paddingLeft: 1,
      paddingRight: 1,
      border: true,
      borderStyle: 'rounded',
      borderColor: theme.info,
      backgroundColor: theme.elevatedSurface,
      title: ' New chat thread ',
      visible: false,
      zIndex: 30,
    });
  }

  applyTheme(theme: Theme): void {
    this.#theme = theme;
    this.output.borderColor = theme.info;
    this.output.backgroundColor = theme.elevatedSurface;
    this.#renderedRows = null;
  }

  render(state: SessionState): void {
    const picker = state.newChatPicker;
    if (picker === null) {
      this.output.visible = false;
      this.#renderedRows = null;
      return;
    }
    this.output.visible = true;
    const fingerprint = JSON.stringify(picker);
    if (this.#renderedRows === fingerprint) return;
    this.#renderedRows = fingerprint;
    clearChildren(this.output);
    this.#addLine(`Driver: ${picker.driver}${picker.step === 'driver' ? '  ◂ choose' : ''}`);
    if (picker.step === 'driver') {
      for (const driver of CHAT_DRIVERS) this.#addChoice(driver, driver === picker.driver);
    }
    this.#addLine(`Provider: ${picker.provider}${picker.step === 'provider' ? '  ◂ choose' : ''}`);
    if (picker.step === 'provider') {
      for (const provider of CHAT_DRIVER_PROVIDERS[picker.driver]) {
        this.#addChoice(provider, provider === picker.provider);
      }
    }
    if (picker.step === 'model') {
      this.#addLine(`Model: ${picker.model === '' ? '(run default)' : picker.model}▏`);
    }
    this.#addLine('');
    this.#addLine(
      picker.step === 'model'
        ? 'Type a model, or leave empty for the run default · Enter: create · Esc: back'
        : '↑↓: select · Enter: next · Esc: back',
      this.#theme.textSubtle,
    );
  }

  #addLine(content: string, fg?: string): void {
    this.output.add(
      new TextRenderable(this.renderer, {
        content,
        fg: fg ?? this.#theme.textPrimary,
        width: '100%',
      }),
    );
  }

  #addChoice(name: string, selected: boolean): void {
    this.output.add(
      new TextRenderable(this.renderer, {
        content: `  ${selected ? '›' : ' '} ${name}`,
        fg: selected ? this.#theme.textStrong : this.#theme.textPrimary,
        bg: selected ? this.#theme.selectedSurface : this.#theme.elevatedSurface,
        width: '100%',
      }),
    );
  }
}

function clearChildren(box: BoxRenderable): void {
  for (const child of [...box.getChildren()]) {
    box.remove(child);
    child.destroyRecursively();
  }
}
