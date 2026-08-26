import {
  BoxRenderable,
  type CliRenderer,
  InputRenderable,
  InputRenderableEvents,
  SyntaxStyle,
  TextRenderable,
} from '@opentui/core';
import {
  type CommandContext,
  type SlashCommand,
  slashCommandRange,
  suggestSlashCommands,
} from '../commands.js';
import type {Theme} from './theme.js';

export interface CommandInputPanel {
  box: BoxRenderable;
  suggestions: BoxRenderable;
  /** Narrows the completions to the commands the current view offers. */
  setCommandContext(context: CommandContext): void;
  completeSuggestion(): boolean;
  navigateSuggestions(direction: 1 | -1): boolean;
  /** True when nothing is typed, so Enter belongs to whatever pane is behind. */
  isEmpty(): boolean;
  focus(): void;
  setFocused(focused: boolean): void;
  applyTheme(theme: Theme): void;
  destroy(): void;
}

function commandSyntaxStyle(theme: Theme): SyntaxStyle {
  return SyntaxStyle.fromStyles({'slash-command': {fg: theme.accent, bold: true}});
}

function renderSuggestions(
  matches: readonly SlashCommand[],
  selectedIndex: number,
  value: string,
): string {
  return matches
    .map(
      (command, index) =>
        `${index === selectedIndex ? '›' : ' '} ${command.name.padEnd(10)} ${command.description}${
          index === selectedIndex && command.name !== value ? '  [Tab]' : ''
        }`,
    )
    .join('\n');
}

export function createCommandInputPanel(
  renderer: CliRenderer,
  onSubmit: (value: string) => void,
  theme: Theme,
  /** Called when the box is clicked, so the pane focus follows the cursor. */
  onFocusRequest: () => void = () => {},
): CommandInputPanel {
  const box = new BoxRenderable(renderer, {
    id: 'command-input-box',
    height: 3,
    width: '100%',
    border: true,
    borderStyle: 'rounded',
    borderColor: theme.borderFocus,
    title: ' Command ',
    paddingLeft: 1,
    paddingRight: 1,
    onMouseUp: onFocusRequest,
  });
  let syntaxStyle = commandSyntaxStyle(theme);
  let commandStyleId = syntaxStyle.getStyleId('slash-command');
  const input = new InputRenderable(renderer, {
    id: 'command-input',
    width: '100%',
    placeholder: 'Type /help for commands',
    textColor: theme.textStrong,
    focusedTextColor: theme.textStrong,
    syntaxStyle,
    onMouseUp: onFocusRequest,
  });
  const suggestions = new BoxRenderable(renderer, {
    id: 'command-input-suggestions',
    position: 'absolute',
    bottom: 3,
    left: 0,
    width: '100%',
    height: 3,
    visible: false,
    zIndex: 5,
    border: true,
    borderStyle: 'rounded',
    borderColor: theme.border,
    backgroundColor: theme.selectedSurface,
    paddingLeft: 1,
    paddingRight: 1,
  });
  const suggestionList = new TextRenderable(renderer, {
    id: 'command-input-suggestion-list',
    width: '100%',
    height: 1,
    fg: theme.textMuted,
    wrapMode: 'none',
    truncate: true,
    content: '',
  });
  suggestions.add(suggestionList);
  let matches: readonly SlashCommand[] = [];
  let selectedIndex = 0;
  let context: CommandContext = {};

  const updateDecorations = (value: string): void => {
    input.clearAllHighlights();
    const range = slashCommandRange(value);
    if (range !== null && commandStyleId !== null) {
      input.addHighlightByCharRange({...range, styleId: commandStyleId});
    }

    matches = suggestSlashCommands(value, context);
    selectedIndex = 0;
    const visible = matches.length > 0;
    suggestions.visible = visible;
    suggestions.height = matches.length + 2;
    suggestionList.height = Math.max(1, matches.length);
    suggestionList.content = renderSuggestions(matches, selectedIndex, value);
  };
  const submit = (value: string): void => {
    input.value = '';
    onSubmit(value);
  };
  input.on(InputRenderableEvents.INPUT, updateDecorations);
  input.on(InputRenderableEvents.ENTER, submit);
  box.add(input);
  let focused = true;
  let current = theme;
  return {
    box,
    suggestions,
    setCommandContext(next: CommandContext): void {
      if (next.chatDocked === context.chatDocked) return;
      context = next;
      updateDecorations(input.value);
    },
    completeSuggestion(): boolean {
      const suggestion = matches[selectedIndex];
      if (suggestion === undefined || suggestion.name === input.value) return false;
      input.value = suggestion.name;
      selectedIndex = 0;
      return true;
    },
    navigateSuggestions(direction: 1 | -1): boolean {
      if (matches.length === 0) return false;
      selectedIndex = (selectedIndex + direction + matches.length) % matches.length;
      suggestionList.content = renderSuggestions(matches, selectedIndex, input.value);
      return true;
    },
    isEmpty: () => input.value.trim() === '',
    focus: () => input.focus(),
    setFocused(next: boolean): void {
      focused = next;
      box.borderColor = next ? current.borderFocus : current.border;
    },
    applyTheme(next: Theme): void {
      current = next;
      box.borderColor = focused ? next.borderFocus : next.border;
      input.textColor = next.textStrong;
      input.focusedTextColor = next.textStrong;
      suggestions.borderColor = next.border;
      suggestions.backgroundColor = next.selectedSurface;
      suggestionList.fg = next.textMuted;
      const previous = syntaxStyle;
      syntaxStyle = commandSyntaxStyle(next);
      commandStyleId = syntaxStyle.getStyleId('slash-command');
      input.syntaxStyle = syntaxStyle;
      previous.destroy();
      updateDecorations(input.value);
    },
    destroy(): void {
      input.off(InputRenderableEvents.INPUT, updateDecorations);
      input.off(InputRenderableEvents.ENTER, submit);
      if (!input.isDestroyed) input.syntaxStyle = null;
      syntaxStyle.destroy();
    },
  };
}