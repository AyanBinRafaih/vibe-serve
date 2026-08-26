import type {CliRenderer, KeyEvent, ScrollBoxRenderable} from '@opentui/core';
import type {SessionController} from '../session-controller.js';
import {chatPaneFocused, chatPaneVisible, experimentLogVisible} from '../session-model.js';
import type {ClipboardCopyResult, SelectionClipboard} from './clipboard.js';

export interface KeybindingActions {
  completeInput(): boolean;
  navigateSuggestions(direction: 1 | -1): boolean;
  inputIsEmpty(): boolean;
  closeChat(): void;
  toggleLatestPrompt(): void;
  /** Brings the entry the cursor moved to into view. */
  revealSelectedEntry(): void;
  selectNextAgent(): void;
  selectPreviousAgent(): void;
  selectNextRound(): void;
  selectPreviousRound(): void;
  toggleTodos(): void;
  scrollRightPane(delta: number): void;
  scrollChatPane(delta: number): void;
  scrollErrorBanner(delta: number): void;
  clearTransientStatus(): void;
  showClipboardStatus(result: Exclude<ClipboardCopyResult, 'no-selection'>): void;
}

export function bindKeybindings(
  renderer: CliRenderer,
  controller: SessionController,
  viewport: ScrollBoxRenderable,
  clipboard: SelectionClipboard,
  actions: KeybindingActions,
): () => void {
  const onKey = (key: KeyEvent): void => {
    if (key.ctrl && !key.shift && key.name === 'c') {
      key.preventDefault();
      const result = clipboard.copySelection();
      if (result === 'no-selection') renderer.destroy();
      else actions.showClipboardStatus(result);
      return;
    }
    actions.clearTransientStatus();
    if (
      key.name === 'f4' &&
      controller.state.chatOpen === false &&
      controller.state.overlay === null &&
      controller.state.themePicker === null
    ) {
      controller.togglePaneZoom();
      key.preventDefault();
      return;
    }
    if (
      controller.state.errorBanner !== null &&
      key.ctrl &&
      (key.name === 'pageup' || key.name === 'pagedown')
    ) {
      actions.scrollErrorBanner(key.name === 'pageup' ? -1 : 1);
      key.preventDefault();
      return;
    }
    if (controller.state.errorBanner !== null && key.name === 'escape') {
      controller.dismissErrorBanner();
      key.preventDefault();
      return;
    }
    if (
      key.ctrl &&
      key.name === 'w' &&
      (controller.state.layout.right !== null || chatPaneVisible(controller.state))
    ) {
      controller.cyclePaneFocus();
      key.preventDefault();
      return;
    }
    if (
      controller.state.layout.focus === 'right' &&
      controller.state.layout.right !== null &&
      (key.name === 'pageup' || key.name === 'pagedown' || key.name === 'escape')
    ) {
      if (key.name === 'escape') controller.closeOverlays();
      else actions.scrollRightPane(key.name === 'pageup' ? -1 : 1);
      key.preventDefault();
      return;
    }
    if (
      chatPaneFocused(controller.state) &&
      (key.name === 'pageup' || key.name === 'pagedown' || key.name === 'escape')
    ) {
      if (key.name === 'escape') controller.focusPane('left');
      else actions.scrollChatPane(key.name === 'pageup' ? -1 : 1);
      key.preventDefault();
      return;
    }
    if (chatPaneFocused(controller.state)) return;
    if (controller.state.themePicker !== null) {
      if (key.name === 'up') controller.moveThemeSelection(-1);
      else if (key.name === 'down') controller.moveThemeSelection(1);
      else if (key.name === 'pageup') controller.moveThemeSelection(-10);
      else if (key.name === 'pagedown') controller.moveThemeSelection(10);
      else if (key.name === 'escape') controller.closeThemePicker();
      else if (key.name === 'return' || key.name === 'enter') {
        if (!actions.inputIsEmpty()) return;
        controller.applySelectedTheme();
      } else return;
      key.preventDefault();
      return;
    }
    if (controller.state.chatOpen) {
      if (key.name === 'escape') {
        if (controller.state.layout.right !== null) controller.closeOverlays();
        else actions.closeChat();
        key.preventDefault();
      }
      return;
    }
    if (key.name === 'escape' && controller.state.overlay !== null) {
      controller.live();
      viewport.scrollTo(viewport.scrollHeight);
      key.preventDefault();
      return;
    }
    if (experimentLogVisible(controller.state)) {
      if (key.name === 'up') {
        if (!actions.navigateSuggestions(-1)) controller.moveExperimentSelection(-1);
      } else if (key.name === 'down') {
        if (!actions.navigateSuggestions(1)) controller.moveExperimentSelection(1);
      } else if (key.name === 'pageup') controller.moveExperimentSelection(-10);
      else if (key.name === 'pagedown') controller.moveExperimentSelection(10);
      else if (key.name === 'return' || key.name === 'enter') {
        if (!actions.inputIsEmpty()) return;
        if (controller.state.overlay === null) controller.enterExperimentDrilldown();
      } else return;
      key.preventDefault();
      return;
    }
    if (key.name === 'escape' && controller.state.hypothesisScope !== null) {
      if (controller.state.selectedEntryId !== null) controller.clearEntrySelection();
      else if (controller.state.selectedAgentKind !== null) controller.clearAgentSelection();
      else controller.leaveExperimentDrilldown();
      key.preventDefault();
      return;
    }
    if ((key.ctrl && key.name === 'p') || key.name === 'f3') {
      actions.toggleLatestPrompt();
      key.preventDefault();
      return;
    }
    if ((key.ctrl && key.name === 't') || key.name === 'f2') {
      actions.toggleTodos();
      key.preventDefault();
      return;
    }
    if (controller.state.todosExpanded) {
      if (key.name === 'up' || key.name === 'down') {
        controller.selectNextTodo(key.name === 'down' ? 1 : -1);
        key.preventDefault();
        return;
      }
      if (key.name === 'escape') {
        controller.toggleTodos();
        key.preventDefault();
        return;
      }
    }
    if (key.name === 'left' || key.name === 'right') {
      controller.focusRound(key.name === 'left' ? 'agents' : 'transcript');
      key.preventDefault();
      return;
    }
    if (key.name === 'up' || key.name === 'down') {
      if (!actions.navigateSuggestions(key.name === 'up' ? -1 : 1)) {
        if (controller.state.roundFocus === 'agents') {
          if (key.name === 'down') controller.selectNextAgent();
          else controller.selectPreviousAgent();
        } else {
          controller.selectNextEntry(key.name === 'down' ? 1 : -1);
          actions.revealSelectedEntry();
        }
      }
      key.preventDefault();
      return;
    }
    if (key.ctrl && key.name === 'l') {
      controller.live();
      viewport.scrollTo(viewport.scrollHeight);
      key.preventDefault();
      return;
    }
    if (key.name === 'tab' && !key.shift && actions.completeInput()) {
      key.preventDefault();
      return;
    }
    if (key.name === 'tab') {
      if (key.shift) actions.selectPreviousAgent();
      else actions.selectNextAgent();
      viewport.scrollTo(viewport.scrollHeight);
      key.preventDefault();
      return;
    }
    if (key.name === ']') {
      actions.selectNextRound();
      viewport.scrollTo(viewport.scrollHeight);
      key.preventDefault();
      return;
    }
    if (key.name === '[') {
      actions.selectPreviousRound();
      viewport.scrollTo(viewport.scrollHeight);
      key.preventDefault();
      return;
    }
    if (key.name === 'pageup') viewport.scrollBy(-1, 'viewport');
    else if (key.name === 'pagedown') viewport.scrollBy(1, 'viewport');
    else if (key.ctrl && key.name === 'up') viewport.scrollBy(-1);
    else if (key.ctrl && key.name === 'down') viewport.scrollBy(1);
    else if (key.name === 'home') viewport.scrollTo(0);
    else if (key.name === 'end') viewport.scrollTo(viewport.scrollHeight);
    else return;
    key.preventDefault();
  };

  renderer.keyInput.on('keypress', onKey);
  return () => renderer.keyInput.off('keypress', onKey);
}