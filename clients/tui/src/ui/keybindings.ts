import type {CliRenderer, KeyEvent, ScrollBoxRenderable} from '@opentui/core';
import type {SessionController} from '../session-controller.js';
import {chatPaneFocused, chatPaneVisible, experimentLogVisible} from '../session-model.js';

export interface KeybindingActions {
  completeInput(): boolean;
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
}

export function bindKeybindings(
  renderer: CliRenderer,
  controller: SessionController,
  viewport: ScrollBoxRenderable,
  actions: KeybindingActions,
): () => void {
  const onKey = (key: KeyEvent): void => {
    if (key.ctrl && key.name === 'c') {
      key.preventDefault();
      renderer.destroy();
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
    // Pane switching works from anywhere a second column is on screen, which
    // on the landing view includes the docked chat, so the operator never has
    // to leave the table to scroll back through an answer.
    if (
      key.ctrl &&
      key.name === 'w' &&
      (controller.state.layout.right !== null || chatPaneVisible(controller.state))
    ) {
      controller.cyclePaneFocus();
      key.preventDefault();
      return;
    }
    // The focused pane takes the scroll keys. Everything else the chat or the
    // transcript would normally handle is left alone.
    if (
      controller.state.layout.focus === 'right' &&
      controller.state.layout.right !== null &&
      (key.name === 'pageup' || key.name === 'pagedown' || key.name === 'escape')
    ) {
      // Escape leaves the view the operator was in, not one layer of it: a
      // visualization and a chat opened over a round both go at once, rather
      // than the chat being left behind as a pop-up over the round.
      if (key.name === 'escape') controller.closeOverlays();
      else actions.scrollRightPane(key.name === 'pageup' ? -1 : 1);
      key.preventDefault();
      return;
    }
    // The docked chat takes the scroll keys while it holds focus, so Page Up
    // reads the conversation back instead of moving the table's selection.
    // Escape hands the keys back to the table rather than closing anything:
    // the pane is part of the view, not a dialog over it.
    if (
      chatPaneFocused(controller.state) &&
      (key.name === 'pageup' || key.name === 'pagedown' || key.name === 'escape')
    ) {
      if (key.name === 'escape') controller.focusPane('left');
      else actions.scrollChatPane(key.name === 'pageup' ? -1 : 1);
      key.preventDefault();
      return;
    }
    // The theme picker owns its navigation keys wherever it was opened from,
    // so arrows never reach the view behind it. A typed command still belongs
    // to the input, which is why Enter is claimed only on an empty line.
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
        // The chat may be sitting over a round that also has a pane open.
        // One Escape puts the operator back on the round, not part way.
        if (controller.state.layout.right !== null) controller.closeOverlays();
        else actions.closeChat();
        key.preventDefault();
      }
      return;
    }
    // An overlay floats above whatever pane is behind it, so it takes Escape
    // first whether that pane is the transcript or the experiment log.
    if (key.name === 'escape' && controller.state.overlay !== null) {
      controller.live();
      viewport.scrollTo(viewport.scrollHeight);
      key.preventDefault();
      return;
    }
    // The experiment log owns navigation while the table is on screen: arrows
    // move the selection instead of the input cursor, and Enter opens the
    // rounds behind the selected hypothesis. The input keeps priority over the
    // table, so a typed command runs on its own Enter and its result opens over
    // the log rather than the log swallowing the keystroke.
    if (experimentLogVisible(controller.state)) {
      // Escape has nowhere to go from the root view: the log is the root.
      // Arrows move the table's selection from the moment the log is on screen:
      // the table is the view, so it should not need to be clicked into first.
      if (key.name === 'up') controller.moveExperimentSelection(-1);
      else if (key.name === 'down') controller.moveExperimentSelection(1);
      else if (key.name === 'pageup') controller.moveExperimentSelection(-10);
      else if (key.name === 'pagedown') controller.moveExperimentSelection(10);
      else if (key.name === 'return' || key.name === 'enter') {
        // A typed command belongs to the input; let its own handler run it so
        // one Enter is enough. An overlay is in front of the table, so Enter
        // behind it must not move the operator somewhere they cannot see.
        if (!actions.inputIsEmpty()) return;
        if (controller.state.overlay === null) controller.enterExperimentDrilldown();
      } else return;
      key.preventDefault();
      return;
    }
    // Escape unwinds the round view one step at a time: the entry cursor, then
    // the agent filter, then the hypothesis itself. Leaving outright would throw
    // away a selection the operator may have spent a while arriving at.
    if (key.name === 'escape' && controller.state.hypothesisScope !== null) {
      if (controller.state.selectedEntryId !== null) controller.clearEntrySelection();
      else if (controller.state.selectedAgentKind !== null) controller.clearAgentSelection();
      else controller.leaveExperimentDrilldown();
      key.preventDefault();
      return;
    }
    // F2 and F3 alongside Ctrl+T and Ctrl+P: a terminal is free to keep a
    // Control chord for itself, and on macOS several do, but function keys
    // reach the application everywhere.
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
    // While the todo box is open it owns the arrows, and it is drawn focused so
    // the operator can see where the keys are going. Escape closes it and hands
    // them straight back to the transcript. Every other key falls through: a
    // surface that claims keys it does not act on reads as a frozen client.
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
    // Left and right move between the round view's two panes. The agent graph
    // is on the left and the transcript on the right, so the arrow points at
    // the pane it moves to, and each pane then owns the up and down keys.
    if (key.name === 'left' || key.name === 'right') {
      controller.focusRound(key.name === 'left' ? 'agents' : 'transcript');
      key.preventDefault();
      return;
    }
    // Up and down belong to whichever pane holds the round view's keys: agents
    // within a stage on the left, entries on the right.
    if (key.name === 'up' || key.name === 'down') {
      if (controller.state.roundFocus === 'agents') {
        if (key.name === 'down') controller.selectNextAgent();
        else controller.selectPreviousAgent();
      } else {
        controller.selectNextEntry(key.name === 'down' ? 1 : -1);
        actions.revealSelectedEntry();
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
