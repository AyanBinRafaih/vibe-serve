import {
  type EventSubscription,
  type ProtocolResponse,
  type RequestInput,
  type RunEvent,
  type ServerMessage,
  SupervisionError,
} from '@vibesys/backend-client';
import {helpText, parseCommand} from './commands.js';
import {renderPerformanceCurve} from './performance-chart.js';
import {
  advanceNewChatPicker,
  applyEvent,
  applyEventBatch,
  applySnapshot,
  chatDocked,
  chatPaneVisible,
  clearAgentSelection,
  clearEntrySelection,
  closeChatThreadPicker,
  closeOverlays,
  closePane,
  closeThemePicker,
  cyclePaneFocus,
  dismissErrorBanner,
  enterExperimentDrilldown,
  enterExperimentRound,
  enterUnownedExperimentRound,
  failExperiments,
  failPane,
  focusPane,
  focusRound,
  initialSessionState,
  leaveExperimentDrilldown,
  leaveHypothesisDetail,
  markEventStreamUnavailable,
  moveChatThreadSelection,
  moveExperimentSelection,
  moveHypothesisRoundSelection,
  moveNewChatSelection,
  moveThemeSelection,
  type NewChatPicker,
  normalizeFocus,
  openChat,
  openChatThreadPicker,
  openExperimentLog,
  openHypothesisDetail,
  openNewChatPicker,
  openPane,
  openThemePicker,
  type PaneFocus,
  type PaneView,
  retreatNewChatPicker,
  type RoundFocus,
  reportError,
  type SessionState,
  selectAgent,
  selectExperimentActivity,
  selectNextAgent,
  selectNextEntry,
  selectNextRound,
  selectNextTodo,
  selectPreviousAgent,
  selectPreviousRound,
  selectRound,
  setChatDockFits,
  setChatThreadPending,
  setExperiments,
  setNewChatModel,
  setPaneContent,
  setTheme,
  showDetail,
  showLive,
  switchChatThread,
  togglePaneZoom,
  toggleTodos,
  updateChatConversation,
} from './session-model.js';
import {DEFAULT_CHAT_THREAD_ID} from '@vibesys/core-state';
import {DEFAULT_THEME_NAME, type ThemeName} from './ui/theme.js';

export interface SessionController {
  readonly state: SessionState;
  start(): Promise<void>;
  stop(): Promise<void>;
  submitCommand(value: string): Promise<void>;
  closeChat(): void;
  sendChat(value: string): Promise<void>;
  submitChat(value: string): Promise<void>;
  /** Makes one thread the chat surfaces' subject. */
  switchChatThread(threadId: string): void;
  openChatThreadPicker(): void;
  moveChatThreadSelection(delta: number): void;
  /** Enter in the thread picker: switch to the highlighted thread. */
  applySelectedChatThread(): void;
  closeChatThreadPicker(): void;
  openNewChatPicker(): void;
  moveNewChatSelection(delta: number): void;
  /** Enter in the wizard: next step, or create the thread on the last one. */
  confirmNewChatPicker(): Promise<void>;
  retreatNewChatPicker(): void;
  typeNewChatModel(text: string): void;
  backspaceNewChatModel(): void;
  live(): void;
  selectNextAgent(): void;
  selectPreviousAgent(): void;
  selectNextRound(): void;
  selectPreviousRound(): void;
  selectRound(roundNumber: number): void;
  selectAgent(kind: string): void;
  selectNextEntry(delta: number, id?: string): void;
  clearEntrySelection(): void;
  clearAgentSelection(): void;
  focusRound(focus: RoundFocus): void;
  selectNextTodo(delta: number): void;
  toggleTodos(): void;
  /** Expands the latest prompt in view; the view owns what "latest" means. */
  togglePrompt(): void;
  onTogglePrompt(handler: () => void): void;
  setTheme(themeName: ThemeName): void;
  openExperimentLog(): Promise<void>;
  openRound(roundNumber?: number): void;
  openPane(view: PaneView): Promise<void>;
  closePane(): void;
  closeOverlays(): void;
  dismissErrorBanner(): void;
  cyclePaneFocus(): void;
  focusPane(focus: PaneFocus): void;
  togglePaneZoom(): void;
  setChatDockFits(fits: boolean): void;
  moveExperimentSelection(delta: number): void;
  openHypothesisDetail(entryKey?: string): void;
  moveHypothesisRoundSelection(delta: number): void;
  selectExperimentActivity(): void;
  enterExperimentDrilldown(): void;
  leaveExperimentDrilldown(): void;
  leaveHypothesisDetail(): void;
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
  readonly #chatQueue: Array<{id: string; text: string; threadId: string}> = [];
  #chatDrain: Promise<void> | null = null;
  /** Single-flight guard for semantic experiment-log invalidations. */
  #experimentFetch: Promise<void> | null = null;
  #experimentRefreshPending = false;
  #paneFetch: Promise<void> | null = null;
  #streamProtocolError = false;

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
    try {
      const response = await this.client.request({type: 'query.snapshot'});
      if (response.snapshot) this.#setState(applySnapshot(this.#state, response.snapshot));
    } catch (error) {
      this.#setState(reportCaughtError(this.#state, error, 'request'));
    }
    // The log is the landing view, so it is populated before the first frame
    // rather than on demand.
    await this.#loadExperiments();
    try {
      this.#eventSubscription = await this.client.subscribe(
        0,
        message => this.#onMessage(message),
        error => {
          // A terminal event already carries the actual outcome. The socket
          // closing afterward is lifecycle cleanup, not a second failure that
          // should replace the useful diagnostic in the banner.
          if (!this.#state.core.terminal && !this.#streamProtocolError) {
            this.#setState(
              reportCaughtError(markEventStreamUnavailable(this.#state), error, 'transport'),
            );
          }
        },
      );
    } catch (error) {
      this.#setState(
        reportCaughtError(markEventStreamUnavailable(this.#state), error, 'transport'),
      );
    }
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

  selectAgent(kind: string): void {
    this.#setState(selectAgent(this.#state, kind));
  }

  selectNextEntry(delta: number, id?: string): void {
    this.#setState(selectNextEntry(this.#state, delta, id));
  }

  clearEntrySelection(): void {
    this.#setState(clearEntrySelection(this.#state));
  }

  clearAgentSelection(): void {
    this.#setState(clearAgentSelection(this.#state));
  }

  focusRound(focus: RoundFocus): void {
    this.#setState(focusRound(this.#state, focus));
  }

  selectNextTodo(delta: number): void {
    this.#setState(selectNextTodo(this.#state, delta));
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

  #promptToggle: (() => void) | null = null;

  /**
   * The prompt lives in the transcript, so the view performs the toggle; the
   * controller only routes the request, which is what lets a slash command and
   * a key do the same thing.
   */
  onTogglePrompt(handler: () => void): void {
    this.#promptToggle = handler;
  }

  togglePrompt(): void {
    this.#promptToggle?.();
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

  switchChatThread(threadId: string): void {
    this.#setState(switchChatThread(this.#state, threadId));
  }

  openChatThreadPicker(): void {
    this.#setState(openChatThreadPicker(this.#state));
  }

  moveChatThreadSelection(delta: number): void {
    this.#setState(moveChatThreadSelection(this.#state, delta));
  }

  applySelectedChatThread(): void {
    const picker = this.#state.chatThreadPicker;
    if (picker === null) return;
    this.switchChatThread(picker.selected);
  }

  closeChatThreadPicker(): void {
    this.#setState(closeChatThreadPicker(this.#state));
  }

  openNewChatPicker(): void {
    this.#setState(openNewChatPicker(this.#state));
  }

  moveNewChatSelection(delta: number): void {
    this.#setState(moveNewChatSelection(this.#state, delta));
  }

  async confirmNewChatPicker(): Promise<void> {
    const picker = this.#state.newChatPicker;
    if (picker === null) return;
    if (picker.step !== 'model') {
      this.#setState(advanceNewChatPicker(this.#state));
      return;
    }
    this.#setState({...this.#state, newChatPicker: null});
    await this.#createChatThread(picker);
  }

  retreatNewChatPicker(): void {
    this.#setState(retreatNewChatPicker(this.#state));
  }

  typeNewChatModel(text: string): void {
    const picker = this.#state.newChatPicker;
    if (picker === null) return;
    this.#setState(setNewChatModel(this.#state, picker.model + text));
  }

  backspaceNewChatModel(): void {
    const picker = this.#state.newChatPicker;
    if (picker === null) return;
    this.#setState(setNewChatModel(this.#state, picker.model.slice(0, -1)));
  }

  /**
   * Asks the backend for a new thread. The response's replayed events carry
   * the authoritative thread record; the client only switches to it.
   */
  async #createChatThread(picker: NewChatPicker): Promise<void> {
    const model = picker.model.trim();
    try {
      const response = await this.client.request({
        type: 'query.chat_thread_create',
        driver: picker.driver,
        provider: picker.provider,
        ...(model === '' ? {} : {model}),
      });
      let state = this.#state;
      for (const event of response.events ?? []) state = applyEvent(state, event);
      const threadId = response.chat_thread?.thread_id;
      this.#setState(threadId === undefined ? state : switchChatThread(state, threadId));
    } catch (error) {
      this.#setState(reportCaughtError(this.#state, error, 'request'));
    }
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
        enterUnownedExperimentRound(this.#state, roundNumber) ??
        showDetail(this.#state, `Round ${roundNumber} has not been recorded.`)
      );
    }
    const scope = this.#state.hypothesisScope;
    if (scope !== null) {
      return showDetail(
        this.#state,
        `Already inside ${scope.id}. Esc returns to the experiment log.`,
      );
    }
    const firstStep = enterExperimentDrilldown(this.#state);
    // The ordinary UI stops at the hypothesis summary. `/open-round` names a
    // round-level action explicitly, so it advances through that summary to
    // the currently selected (latest by default) round.
    const opened =
      firstStep.hypothesisDetail !== null && firstStep.hypothesisScope === null
        ? enterExperimentDrilldown(firstStep)
        : firstStep;
    return opened === this.#state
      ? showDetail(this.#state, 'Select a hypothesis first, or use /open-round --N.')
      : opened;
  }

  async openPane(view: PaneView): Promise<void> {
    this.#setState(openPane(this.#state, view));
    await this.#loadPane(view);
  }

  closePane(): void {
    this.#setState(closePane(this.#state));
  }

  closeOverlays(): void {
    this.#setState(closeOverlays(this.#state));
  }

  dismissErrorBanner(): void {
    this.#setState(dismissErrorBanner(this.#state));
  }

  cyclePaneFocus(): void {
    this.#setState(cyclePaneFocus(this.#state));
  }

  focusPane(focus: PaneFocus): void {
    this.#setState(focusPane(this.#state, focus));
  }

  togglePaneZoom(): void {
    this.#setState(togglePaneZoom(this.#state));
  }

  /**
   * Reported by the renderer, which is the only part that knows how many
   * columns there are. It decides whether a question opens the modal or lands
   * in the pane beside the log.
   */
  setChatDockFits(fits: boolean): void {
    this.#setState(setChatDockFits(this.#state, fits));
  }

  /**
   * Re-runs the query behind whichever visualization is on screen. The pane
   * holds rendered text, so refreshing it is the same path as opening it.
   */
  async #loadPane(view: PaneView): Promise<void> {
    if (this.#paneFetch !== null) return this.#paneFetch;
    const fetch = this.#requestPane(view).finally(() => {
      this.#paneFetch = null;
    });
    this.#paneFetch = fetch;
    return fetch;
  }

  async #requestPane(view: PaneView): Promise<void> {
    try {
      const response = await this.client.request({type: 'query.performance'});
      const content = renderPerformanceCurve(response.performance ?? [], response.events ?? []);
      this.#setState(setPaneContent(this.#state, view, content));
    } catch (error) {
      const message = errorMessage(error);
      this.#setState(reportCaughtError(failPane(this.#state, view, message), error, 'request'));
    }
  }

  /**
   * Both visualizations are functions of completed rounds and recorded
   * metrics, so those two events bound every change either can show.
   */
  #refreshPaneFor(events: readonly RunEvent[]): void {
    const right = this.#state.layout.right;
    if (right === null) return;
    const relevant = events.some(
      event =>
        event.type === 'round_finished' ||
        event.type === 'benchmark_result' ||
        event.data?.kind === 'benchmark_result',
    );
    if (relevant) void this.#loadPane(right.view);
  }

  moveExperimentSelection(delta: number): void {
    this.#setState(moveExperimentSelection(this.#state, delta));
  }

  openHypothesisDetail(entryKey?: string): void {
    this.#setState(openHypothesisDetail(this.#state, entryKey));
  }

  moveHypothesisRoundSelection(delta: number): void {
    this.#setState(moveHypothesisRoundSelection(this.#state, delta));
  }

  selectExperimentActivity(): void {
    this.#setState(selectExperimentActivity(this.#state));
  }

  enterExperimentDrilldown(): void {
    this.#setState(enterExperimentDrilldown(this.#state));
  }

  leaveExperimentDrilldown(): void {
    this.#setState(leaveExperimentDrilldown(this.#state));
  }

  leaveHypothesisDetail(): void {
    this.#setState(leaveHypothesisDetail(this.#state));
  }

  async #loadExperiments(): Promise<void> {
    if (this.#experimentFetch !== null) return this.#experimentFetch;
    const fetch = this.#requestExperiments().finally(() => {
      this.#experimentFetch = null;
      if (this.#experimentRefreshPending) {
        this.#experimentRefreshPending = false;
        void this.#loadExperiments();
      }
    });
    this.#experimentFetch = fetch;
    return fetch;
  }

  async #requestExperiments(): Promise<void> {
    try {
      const response = await this.client.request({type: 'query.experiments'});
      if (response.experiments_ready === false) return;
      this.#setState(setExperiments(this.#state, response.experiments ?? []));
    } catch (error) {
      const message = errorMessage(error);
      this.#setState(reportCaughtError(failExperiments(this.#state, message), error, 'request'));
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
    return this.submitCommand(text);
  }

  sendChat(value: string): Promise<void> {
    const text = value.trim();
    if (!text) return Promise.resolve();
    const id = `chat-user-${++this.#chatMessageId}`;
    // The message belongs to the thread on screen when it was typed, even if
    // the operator switches threads before the agent gets to it.
    const threadId = this.#state.activeChatThreadId;
    const queued = this.#state.chatPending || this.#chatQueue.length > 0;
    this.#chatQueue.push({id, text, threadId});
    this.#setState(
      updateChatConversation(
        {
          ...this.#state,
          // Docked, the answer lands in the pane the operator is already
          // looking at, so nothing has to open over the log to show it.
          ...(chatDocked(this.#state) ? {} : {chatOpen: true}),
        },
        threadId,
        entries => [
          ...entries,
          {id, kind: 'user', label: queued ? 'You · queued' : 'You', content: text},
        ],
      ),
    );
    if (this.#chatDrain === null) {
      const drain = this.#drainChatQueue();
      this.#chatDrain = drain.finally(() => {
        this.#chatDrain = null;
      });
    }
    return this.#chatDrain;
  }

  async #drainChatQueue(): Promise<void> {
    const pendingThreads = new Set<string>();
    try {
      while (this.#chatQueue.length > 0) {
        // One request per thread: batching across threads would hand one
        // agent another thread's question.
        const threadId = this.#chatQueue[0]?.threadId ?? DEFAULT_CHAT_THREAD_ID;
        const messages = this.#chatQueue.filter(message => message.threadId === threadId);
        for (const message of messages) {
          this.#chatQueue.splice(this.#chatQueue.indexOf(message), 1);
        }
        const messageIds = new Set(messages.map(message => message.id));
        pendingThreads.add(threadId);
        this.#setState(
          updateChatConversation(
            setChatThreadPending(this.#state, threadId, true),
            threadId,
            entries =>
              entries.map(entry => (messageIds.has(entry.id) ? {...entry, label: 'You'} : entry)),
          ),
        );
        await this.#requestChat(messages.map(message => message.text).join('\n\n'), threadId);
        pendingThreads.delete(threadId);
        this.#setState(setChatThreadPending(this.#state, threadId, false));
      }
    } finally {
      let state = this.#state;
      for (const threadId of pendingThreads) {
        state = setChatThreadPending(state, threadId, false);
      }
      this.#setState(state);
    }
  }

  async #requestChat(text: string, threadId: string): Promise<void> {
    try {
      const response = await this.client.request({
        type: 'query.chat',
        text,
        ...(threadId === DEFAULT_CHAT_THREAD_ID ? {} : {thread_id: threadId}),
      });
      const answer = response.chat?.answer ?? 'No chat answer was returned.';
      let state = this.#state;
      for (const event of response.events ?? []) state = applyEvent(state, event);
      if (!(response.events ?? []).some(event => event.data?.kind === 'chat')) {
        state = updateChatConversation(state, threadId, entries => [
          ...entries,
          {
            id: `chat-answer-${++this.#chatMessageId}`,
            kind: 'assistant',
            label: 'Answer',
            content: answer,
          },
        ]);
      }
      this.#setState(state);
    } catch (error) {
      const message = errorMessage(error);
      this.#setState(
        updateChatConversation(
          reportCaughtError(this.#state, error, 'request'),
          threadId,
          entries => [
            ...entries,
            {
              id: `chat-error-${++this.#chatMessageId}`,
              kind: 'result',
              label: 'Chat failed',
              tone: 'failure',
              content: message,
            },
          ],
        ),
      );
    }
  }

  async submitCommand(value: string): Promise<void> {
    const parsed = parseCommand(value.trim());
    if (parsed.error)
      return this.#setState(reportError(this.#state, parsed.error, {scope: 'input'}));
    if (parsed.localView === 'help') {
      return this.#setState(
        showDetail(this.#state, helpText({chatDocked: chatPaneVisible(this.#state)}), 'help'),
      );
    }
    if (parsed.localView === 'chat') {
      this.#setState(openChat(this.#state));
      if (parsed.chatMessage) await this.sendChat(parsed.chatMessage);
      return;
    }
    if (parsed.localView === 'new-chat') {
      this.openNewChatPicker();
      return;
    }
    if (parsed.localView === 'chats') {
      this.openChatThreadPicker();
      return;
    }
    if (parsed.toggle === 'todos') {
      this.toggleTodos();
      return;
    }
    if (parsed.toggle === 'prompt') {
      this.togglePrompt();
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
    if (parsed.paneView !== undefined) {
      await this.openPane(parsed.paneView);
      return;
    }
    try {
      const response = await this.client.request(parsed.request);
      const rendered = renderResponse(parsed.request, response, parsed.responseView);
      if (rendered !== null) this.#setState(showDetail(this.#state, rendered));
    } catch (error) {
      this.#setState(reportCaughtError(this.#state, error, 'request'));
    }
  }

  #onMessage(message: ServerMessage): void {
    if (message.type === 'event') {
      this.#setState(applyEvent(this.#state, message.event));
      this.#refreshExperimentsFor([message.event]);
      this.#refreshPaneFor([message.event]);
    }
    if (message.type === 'event_batch') {
      this.#setState(
        applyEventBatch(
          this.#state,
          message.events,
          message.active_executions,
          message.through_sequence,
        ),
      );
      this.#refreshExperimentsFor(message.events);
      this.#refreshPaneFor(message.events);
    }
    if (message.type === 'protocol_error') {
      this.#streamProtocolError = true;
      this.#setState(
        reportError(markEventStreamUnavailable(this.#state), message.message, {
          scope: 'protocol',
          diagnostic: message.diagnostic ?? null,
        }),
      );
    }
  }

  /**
   * The backend publishes this semantic event after the canonical project is
   * attached and whenever persisted hypothesis state changes. The client does
   * not infer storage readiness or mutations from execution phases.
   */
  #refreshExperimentsFor(events: readonly RunEvent[]): void {
    if (this.#state.experimentLog === null) return;
    const relevant = events.some(event => event.type === 'experiments_changed');
    if (!relevant) return;
    if (this.#experimentFetch !== null) this.#experimentRefreshPending = true;
    void this.#loadExperiments();
  }

  #setState(state: SessionState): void {
    // Every state change goes through here, which makes it the one place that
    // can guarantee focus still names a column that exists. A pane that closes
    // while it holds the keys would otherwise swallow every keystroke.
    this.#state = normalizeFocus(state);
    for (const listener of this.#listeners) listener(this.#state);
  }
}

function reportCaughtError(
  state: SessionState,
  error: unknown,
  scope: 'request' | 'transport',
): SessionState {
  return reportError(state, errorMessage(error), {
    scope,
    diagnostic: error instanceof SupervisionError ? error.diagnostic : null,
  });
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
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
