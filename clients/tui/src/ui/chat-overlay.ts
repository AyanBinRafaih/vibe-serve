import {
  BoxRenderable,
  type CliRenderer,
  InputRenderable,
  InputRenderableEvents,
  ScrollBoxRenderable,
  type SyntaxStyle,
  TextRenderable,
} from '@opentui/core';
import type {SessionController} from '../session-controller.js';
import type {SessionState} from '../session-model.js';
import {ConversationView} from './conversation.js';
import type {Theme} from './theme.js';

export class ChatOverlayView {
  readonly output: BoxRenderable;
  readonly #transcript: ScrollBoxRenderable;
  readonly #conversation: ConversationView;
  readonly #input: InputRenderable;
  readonly #inputBox: BoxRenderable;
  readonly #hint: TextRenderable;

  constructor(
    renderer: CliRenderer,
    private readonly controller: SessionController,
    markdownStyle: SyntaxStyle,
    theme: Theme,
  ) {
    this.output = new BoxRenderable(renderer, {
      id: 'chat-overlay',
      width: '80%',
      height: '76%',
      position: 'absolute',
      left: '10%',
      top: '10%',
      flexDirection: 'column',
      paddingLeft: 1,
      paddingRight: 1,
      border: true,
      borderStyle: 'rounded',
      borderColor: theme.conversation.analysis.label,
      backgroundColor: theme.elevatedSurface,
      title: ' Experiment chat ',
      zIndex: 20,
      visible: false,
    });
    this.#transcript = new ScrollBoxRenderable(renderer, {
      id: 'chat-transcript',
      width: '100%',
      flexGrow: 1,
      stickyScroll: true,
      stickyStart: 'bottom',
      viewportCulling: true,
      verticalScrollbarOptions: {showArrows: true},
    });
    this.#conversation = new ConversationView(renderer, controller, markdownStyle, theme, {
      selectConversation: state => state.chatConversation,
      emptyContent: 'Ask a question about the current experiment, its progress, or a failure.',
      renderMarkdown: false,
    });
    this.#inputBox = new BoxRenderable(renderer, {
      id: 'chat-input-box',
      height: 3,
      width: '100%',
      border: true,
      borderStyle: 'rounded',
      borderColor: theme.conversation.analysis.label,
      title: ' Message ',
      paddingLeft: 1,
      paddingRight: 1,
    });
    this.#input = new InputRenderable(renderer, {
      id: 'chat-input',
      width: '100%',
      placeholder: 'Ask about this experiment',
      textColor: theme.textStrong,
      focusedTextColor: theme.textStrong,
    });
    this.#input.on(InputRenderableEvents.ENTER, this.#submit);
    this.#inputBox.add(this.#input);
    this.#transcript.add(this.#conversation.output);
    this.output.add(this.#transcript);
    this.output.add(this.#inputBox);
    this.#hint = new TextRenderable(renderer, {
      content: 'Enter to send · /history and other commands work here · Esc to close',
      fg: theme.textSubtle,
      height: 1,
      width: '100%',
    });
    this.output.add(this.#hint);
  }

  applyTheme(theme: Theme, markdownStyle: SyntaxStyle): void {
    this.output.borderColor = theme.conversation.analysis.label;
    this.output.backgroundColor = theme.elevatedSurface;
    this.#inputBox.borderColor = theme.conversation.analysis.label;
    this.#input.textColor = theme.textStrong;
    this.#input.focusedTextColor = theme.textStrong;
    this.#hint.fg = theme.textSubtle;
    this.#conversation.applyTheme(theme, markdownStyle);
  }

  render(state: SessionState): void {
    this.output.visible = state.chatOpen;
    if (!state.chatOpen) return;
    this.#conversation.render(state);
    this.#transcript.scrollTo(this.#transcript.scrollHeight);
  }

  focus(): void {
    this.#input.focus();
  }

  destroy(): void {
    this.#input.off(InputRenderableEvents.ENTER, this.#submit);
  }

  readonly #submit = (value: string): void => {
    if (!value.trim()) return;
    this.#input.value = '';
    void this.controller.submitChat(value);
  };
}
