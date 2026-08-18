import type {EventSubscription} from './client.js';
import {HELP_TEXT, parseInput} from './commands.js';
import {renderPerformanceCurve} from './performance-chart.js';
import type {ProtocolResponse, RequestInput, RunEvent, ServerMessage} from './protocol.js';
import {
  applyEvent,
  applySnapshot,
  type ConversationEntry,
  closeThemePicker,
  enterExperimentDrilldown,
  enterExperimentRound,
  failExperiments,
  initialSessionState,
  leaveExperimentDrilldown,
  moveExperimentSelection,
  moveThemeSelection,
  openExperimentLog,
  openThemePicker,
  type SessionState,
  selectNextAgent,
  selectNextRound,
  selectPreviousAgent,
  selectPreviousRound,
  selectRound,
  setExperiments,
  setTheme,
  showDetail,
  showLive,
  toggleTodos,
} from './session-model.js';
import {DEFAULT_THEME_NAME, type ThemeName} from './ui/theme.js';

export interface SessionController {
  readonly state: SessionState;
  start(): Promise<void>;
  stop(): Promise<void>;
  submit(value: string): Promise<void>;
  closeChat(): void;
  sendChat(value: string): Promise<void>;
  submitChat(value: string): Promise<void>;
  live(): void;
  selectNextAgent(): void;
  selectPreviousAgent(): void;
  selectNextRound(): void;
  selectPreviousRound(): void;
  selectRound(roundNumber: number): void;
  toggleTodos(): void;
  setTheme(themeName: ThemeName): void;
  openExperimentLog(): Promise<void>;
  openRound(roundNumber?: number): void;
  moveExperimentSelection(delta: number): void;
  enterExperimentDrilldown(): void;
  leaveExperimentDrilldown(): void;
  openThemePicker(): void;
  moveThemeSelection(delta: number): void;
  applySelectedTheme(): void;
  closeThemePicker(): void;
  subscribe(listener: (state: SessionState) => void): () => void;
}

export interface SupervisionTransport {
  request(input: RequestInput): Promise<ProtocolResponse>;
  subscribe(
    afterSequence: number,
    onMessage: (message: ServerMessage) => void,
    onDisconnect: (error: Error) => void,
  ): Promise<EventSubscription>;
  close(): Promise<void>;
}

export class SocketSessionController implements SessionController {
  #state: SessionState;
  readonly #listeners = new Set<(state: SessionState) => void>();
  #eventSubscription: EventSubscription | null = null;
  #chatMessageId = 0;
  readonly #chatQueue: Array<{id: string; text: string}> = [];
  #chatDrain: Promise<void> | null = null;
  /** Single-flight guard: phase events arrive faster than the fetch settles. */
  #experimentFetch: Promise<void> | null = null;

  constructor(
    private readonly client: SupervisionTransport,
    themeName: ThemeName = DEFAULT_THEME_NAME,
  ) {
    this.#state = initialSessionState(themeName);
  }

  get state(): SessionState {
    return this.#state;
  }

  async start(): Promise<void> {
    const response = await this.client.request({type: 'query.snapshot'});
    if (response.snapshot) this.#setState(applySnapshot(this.#state, response.snapshot));
    // The log is the landing view, so it is populated before the first frame
    // rather than on demand.
    await this.#loadExperiments();
    this.#eventSubscription = await this.client.subscribe(
      0,
      message => this.#onMessage(message),
      error => {
        if (!this.#state.terminal) this.#setState(showDetail(this.#state, String(error), 'error'));
      },
    );
  }

  async stop(): Promise<void> {
    await this.#eventSubscription?.close();
    this.#eventSubscription = null;
    await this.client.close();
  }

  subscribe(listener: (state: SessionState) => void): () => void {
    this.#listeners.add(listener);
    listener(this.#state);
    return () => this.#listeners.delete(listener);
  }

  live(): void {
    this.#setState(showLive(this.#state));
  }

  selectNextAgent(): void {
    this.#setState(selectNextAgent(this.#state));
  }

  selectPreviousAgent(): void {
    this.#setState(selectPreviousAgent(this.#state));
  }

  selectNextRound(): void {
    this.#setState(selectNextRound(this.#state));
  }

  selectPreviousRound(): void {
    this.#setState(selectPreviousRound(this.#state));
  }

  selectRound(roundNumber: number): void {
    this.#setState(selectRound(this.#state, roundNumber));
  }

  toggleTodos(): void {
    this.#setState(toggleTodos(this.#state));
  }

  setTheme(themeName: ThemeName): void {
    this.#setState(setTheme(this.#state, themeName));
  }

  openThemePicker(): void {
    this.#setState(openThemePicker(this.#state));
  }

  moveThemeSelection(delta: number): void {
    this.#setState(moveThemeSelection(this.#state, delta));
  }

  /** Enter in the picker: the highlighted theme becomes the session's. */
  applySelectedTheme(): void {
    const picker = this.#state.themePicker;
    if (picker === null) return;
    this.setTheme(picker.selected);
  }

  closeThemePicker(): void {
    this.#setState(closeThemePicker(this.#state));
  }

  closeChat(): void {
    this.#setState({...this.#state, chatOpen: false});
  }

  async openExperimentLog(): Promise<void> {
    this.#setState(openExperimentLog(this.#state));
    await this.#loadExperiments();
  }

  openRound(roundNumber?: number): void {
    this.#setState(this.#openRoundState(roundNumber));
  }

  #openRoundState(roundNumber: number | undefined): SessionState {
    if (roundNumber !== undefined) {
      return (
        enterExperimentRound(this.#state, roundNumber) ??
        showDetail(this.#state, `Round ${roundNumber} is not in any recorded hypothesis.`)
      );
    }
    const scope = this.#state.hypothesisScope;
    if (scope !== null) {
      return showDetail(
        this.#state,
        `Already inside ${scope.id}. Esc returns to the experiment log.`,
      );
    }
    const opened = enterExperimentDrilldown(this.#state);
    return opened === this.#state
      ? showDetail(this.#state, 'Select a hypothesis first, or use /open-round --N.')
      : opened;
  }

  moveExperimentSelection(delta: number): void {
    this.#setState(moveExperimentSelection(this.#state, delta));
  }

  enterExperimentDrilldown(): void {
    this.#setState(enterExperimentDrilldown(this.#state));
  }

  leaveExperimentDrilldown(): void {
    this.#setState(leaveExperimentDrilldown(this.#state));
  }

  async #loadExperiments(): Promise<void> {
    if (this.#experimentFetch !== null) return this.#experimentFetch;
    const fetch = this.#requestExperiments().finally(() => {
      this.#experimentFetch = null;
    });
    this.#experimentFetch = fetch;
    return fetch;
  }

  async #requestExperiments(): Promise<void> {
    try {
      const response = await this.client.request({type: 'query.experiments'});
      this.#setState(setExperiments(this.#state, response.experiments ?? []));
    } catch (error) {
      this.#setState(failExperiments(this.#state, String(error)));
    }
  }

  /**
   * What the chat input submits. A slash command runs through exactly the same
   * path as the main input, so the two surfaces cannot disagree about what a
   * command does; anything else is a question for the chat agent.
   */
  submitChat(value: string): Promise<void> {
    const text = value.trim();
    if (!text.startsWith('/')) return this.sendChat(value);
    return this.submit(text);
  }

  sendChat(value: string): Promise<void> {
    const text = value.trim();
    if (!text) return Promise.resolve();
    const id = `chat-user-${++this.#chatMessageId}`;
    const queued = this.#state.chatPending || this.#chatQueue.length > 0;
    this.#chatQueue.push({id, text});
    this.#setState({
      ...this.#state,
      chatOpen: true,
      chatConversation: appendChatEntry(this.#state.chatConversation, {
        id,
        kind: 'user',
        label: queued ? 'You · queued' : 'You',
        content: text,
      }),
    });
    if (this.#chatDrain === null) {
      const drain = this.#drainChatQueue();
      this.#chatDrain = drain.finally(() => {
        this.#chatDrain = null;
      });
    }
    return this.#chatDrain;
  }

  async #drainChatQueue(): Promise<void> {
    try {
      while (this.#chatQueue.length > 0) {
        const messages = this.#chatQueue.splice(0);
        const messageIds = new Set(messages.map(message => message.id));
        this.#setState({
          ...this.#state,
          chatPending: true,
          chatConversation: this.#state.chatConversation.map(entry =>
            messageIds.has(entry.id) ? {...entry, label: 'You'} : entry,
          ),
        });
        await this.#requestChat(messages.map(message => message.text).join('\n\n'));
      }
    } finally {
      this.#setState({...this.#state, chatPending: false});
    }
  }

  async #requestChat(text: string): Promise<void> {
    try {
      const response = await this.client.request({type: 'query.chat', text});
      const answer = response.chat?.answer ?? 'No chat answer was returned.';
      let state = this.#state;
      for (const event of response.events ?? []) state = applyEvent(state, event);
      if (!(response.events ?? []).some(event => event.data?.kind === 'chat')) {
        state = {
          ...state,
          chatConversation: appendChatEntry(state.chatConversation, {
            id: `chat-answer-${++this.#chatMessageId}`,
            kind: 'assistant',
            label: 'Answer',
            content: answer,
          }),
        };
      }
      this.#setState(state);
    } catch (error) {
      this.#setState({
        ...this.#state,
        chatConversation: appendChatEntry(this.#state.chatConversation, {
          id: `chat-error-${++this.#chatMessageId}`,
          kind: 'result',
          label: 'Chat failed',
          tone: 'failure',
          content: String(error),
        }),
      });
    }
  }

  async submit(value: string): Promise<void> {
    const parsed = parseInput(value.trim());
    if (parsed.error) return this.#setState(showDetail(this.#state, parsed.error, 'error'));
    if (parsed.localView === 'help') {
      return this.#setState(showDetail(this.#state, HELP_TEXT, 'help'));
    }
    if (parsed.localView === 'chat') {
      this.#setState({...this.#state, overlay: null, chatOpen: true});
      if (parsed.chatMessage) await this.sendChat(parsed.chatMessage);
      return;
    }
    if (parsed.openRound) {
      this.openRound(parsed.openRound.round);
      return;
    }
    if (parsed.localView === 'theme') {
      if (parsed.themeName === undefined) return this.openThemePicker();
      return this.setTheme(parsed.themeName);
    }
    if (!parsed.request) return;
    if (parsed.request.type === 'query.chat') {
      await this.sendChat(parsed.request.text);
      return;
    }
    try {
      const response = await this.client.request(parsed.request);
      const rendered = renderResponse(parsed.request, response, parsed.responseView);
      if (rendered !== null) this.#setState(showDetail(this.#state, rendered));
    } catch (error) {
      this.#setState(showDetail(this.#state, String(error), 'error'));
    }
  }

  #onMessage(message: ServerMessage): void {
    if (message.type === 'event') {
      this.#setState(applyEvent(this.#state, message.event));
      this.#refreshExperimentsFor([message.event]);
    }
    if (message.type === 'event_batch') {
      let state = this.#state;
      for (const event of message.events) state = applyEvent(state, event);
      this.#setState(state);
      this.#refreshExperimentsFor(message.events);
    }
    if (message.type === 'protocol_error') {
      this.#setState(showDetail(this.#state, message.message, 'error'));
    }
  }

  /**
   * A hypothesis appears when the orchestrator phase finishes writing its plan
   * and resolves when the round finishes, so those two events bound every
   * change to the log. Refetching on them keeps the landing view live without
   * polling; the single-flight guard absorbs bursts.
   */
  #refreshExperimentsFor(events: readonly RunEvent[]): void {
    if (this.#state.experimentLog === null) return;
    const relevant = events.some(
      event => event.type === 'round_finished' || event.type === 'phase_finished',
    );
    if (!relevant) return;
    void this.#loadExperiments();
  }

  #setState(state: SessionState): void {
    this.#state = state;
    for (const listener of this.#listeners) listener(state);
  }
}

function appendChatEntry(
  conversation: ConversationEntry[],
  entry: ConversationEntry,
): ConversationEntry[] {
  return [...conversation, entry].slice(-500);
}

function renderResponse(
  request: RequestInput,
  response: ProtocolResponse,
  responseView?: 'perf',
): string | null {
  if (response.ack) return `${response.ack.action}: ${response.ack.status}`;
  if (request.type === 'query.performance' || responseView === 'perf') {
    return renderPerformanceCurve(response.performance ?? [], response.events ?? []);
  }
  return null;
}
