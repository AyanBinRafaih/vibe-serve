import {BoxRenderable, type CliRenderer, ScrollBoxRenderable, TextRenderable} from '@opentui/core';
import type {SessionController} from '../session-controller.js';
import {experimentLogVisible, type SessionState, statusText} from '../session-model.js';
import {AgentMapView} from './agent-map.js';
import {ChatOverlayView} from './chat-overlay.js';
import {ConversationView} from './conversation.js';
import {ExperimentLogView} from './experiment-log.js';
import {createInputPanel} from './input.js';
import {bindKeybindings} from './keybindings.js';
import {OverlayView} from './overlay.js';
import {RoundStripView} from './round-strip.js';
import {createMarkdownStyle} from './styles.js';
import {resolveTheme, type ThemeName} from './theme.js';
import {ThemePickerView} from './theme-picker.js';
import {TodoStripView} from './todo-strip.js';

export interface OpenTuiApp {
  destroy(): void;
}

const KEY_HELP =
  '[/]: round · Tab: agent · PgUp/PgDn · Ctrl+T: todos · Ctrl+P: prompt · Ctrl+L: live';
const SCOPED_KEY_HELP =
  '[/]: round · Tab: agent · PgUp/PgDn · Ctrl+T: todos · Ctrl+P: prompt · Esc: experiments';
const LOG_KEY_HELP =
  '↑↓ or scroll: select · Enter: open its rounds · type a command to run it here';

export function createOpenTuiApp(renderer: CliRenderer, controller: SessionController): OpenTuiApp {
  let themeName: ThemeName = controller.state.themeName;
  let theme = resolveTheme(themeName);
  const root = new BoxRenderable(renderer, {
    id: 'app',
    width: '100%',
    height: '100%',
    flexDirection: 'column',
    backgroundColor: theme.canvas,
  });
  const header = new TextRenderable(renderer, {
    id: 'header',
    height: 1,
    fg: theme.accent,
    content: 'VibeSys · connecting',
  });
  const viewport = new ScrollBoxRenderable(renderer, {
    id: 'viewport',
    width: 'auto',
    flexGrow: 1,
    border: true,
    borderStyle: 'rounded',
    borderColor: theme.border,
    stickyScroll: true,
    stickyStart: 'bottom',
    viewportCulling: true,
    verticalScrollbarOptions: {showArrows: true},
  });
  const main = new BoxRenderable(renderer, {
    id: 'main',
    width: '100%',
    flexGrow: 1,
    flexDirection: 'row',
  });
  const help = new TextRenderable(renderer, {
    id: 'key-help',
    height: 1,
    fg: theme.textSubtle,
    content: KEY_HELP,
  });
  let markdownStyle = createMarkdownStyle(theme);
  const roundStrip = new RoundStripView(renderer, controller, theme);
  const todoStrip = new TodoStripView(renderer, controller, theme);
  const agentMap = new AgentMapView(renderer, theme);
  const overlay = new OverlayView(renderer, theme);
  const experimentLog = new ExperimentLogView(renderer, controller, theme);
  const themePicker = new ThemePickerView(renderer, theme);
  const conversation = new ConversationView(renderer, controller, markdownStyle, theme);
  const chat = new ChatOverlayView(renderer, controller, markdownStyle, theme);
  const input = createInputPanel(renderer, value => void controller.submit(value), theme);

  viewport.add(conversation.output);
  main.add(agentMap.output);
  main.add(viewport);
  // The log lives in the main pane rather than floating over it: it is the
  // landing view, not a dialog.
  main.add(experimentLog.output);
  root.add(header);
  root.add(roundStrip.output);
  root.add(main);
  root.add(todoStrip.output);
  root.add(help);
  root.add(input.suggestions);
  root.add(input.box);
  root.add(overlay.output);
  root.add(themePicker.output);
  root.add(chat.output);
  renderer.root.add(root);
  input.focus();

  const applyTheme = (next: ThemeName): (() => void) => {
    themeName = next;
    theme = resolveTheme(next);
    const previousMarkdownStyle = markdownStyle;
    markdownStyle = createMarkdownStyle(theme);
    root.backgroundColor = theme.canvas;
    header.fg = theme.accent;
    viewport.borderColor = theme.border;
    help.fg = theme.textSubtle;
    roundStrip.applyTheme(theme);
    todoStrip.applyTheme(theme);
    agentMap.applyTheme(theme);
    overlay.applyTheme(theme);
    experimentLog.applyTheme(theme);
    themePicker.applyTheme(theme);
    conversation.applyTheme(theme, markdownStyle);
    chat.applyTheme(theme, markdownStyle);
    input.applyTheme(theme);
    return () => previousMarkdownStyle.destroy();
  };

  let chatWasOpen = false;
  const render = (state: SessionState): void => {
    const releasePreviousStyle =
      state.themeName === themeName ? undefined : applyTheme(state.themeName);
    const showLog = experimentLogVisible(state);
    const dialogOpen = state.chatOpen || state.overlay !== null || state.themePicker !== null;
    const returnHint = dialogOpen ? ' · Esc: close dialog' : '';
    const selection = state.selectedAgentKind ? ` · selected ${state.selectedAgentKind}` : '';
    const scope = state.hypothesisScope === null ? '' : ` · ${state.hypothesisScope.label}`;
    header.content = showLog
      ? `VibeSys · ${statusText(state)} · experiments`
      : `VibeSys · ${statusText(state)}${scope}${selection}${returnHint}`;
    help.content = showLog
      ? LOG_KEY_HELP
      : state.hypothesisScope === null
        ? KEY_HELP
        : SCOPED_KEY_HELP;
    // The round strip and agent map are per-round detail. They belong to a
    // hypothesis trajectory, not to the list of claims.
    agentMap.output.visible = !showLog;
    viewport.visible = !showLog;
    roundStrip.output.visible = !showLog;
    todoStrip.output.visible = !showLog;
    if (!showLog) {
      roundStrip.render(state);
      todoStrip.render(state);
      agentMap.render(state);
      conversation.render(state);
    }
    experimentLog.render(state);
    overlay.render(state);
    themePicker.render(state);
    chat.render(state);
    if (state.chatOpen && !chatWasOpen) chat.focus();
    if (!state.chatOpen && chatWasOpen) input.focus();
    chatWasOpen = state.chatOpen;
    releasePreviousStyle?.();
  };
  const unbindKeys = bindKeybindings(renderer, controller, viewport, {
    completeInput: () => input.completeSuggestion(),
    inputIsEmpty: () => input.isEmpty(),
    closeChat: () => controller.closeChat(),
    toggleLatestPrompt: () => conversation.toggleLatestPrompt(),
    selectNextAgent: () => controller.selectNextAgent(),
    selectPreviousAgent: () => controller.selectPreviousAgent(),
    selectNextRound: () => controller.selectNextRound(),
    selectPreviousRound: () => controller.selectPreviousRound(),
    toggleTodos: () => controller.toggleTodos(),
  });
  const unsubscribe = controller.subscribe(render);

  return {
    destroy(): void {
      unsubscribe();
      unbindKeys();
      input.destroy();
      chat.destroy();
      roundStrip.destroy();
      agentMap.destroy();
      root.destroyRecursively();
      markdownStyle.destroy();
    },
  };
}
