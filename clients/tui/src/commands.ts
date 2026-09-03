import type {RequestInput} from '@vibesys/backend-client';
import type {PaneView} from './session-model.js';
import {isThemeName, THEME_NAMES, type ThemeName} from './ui/theme.js';

type CommandRequest = Exclude<RequestInput, {type: 'query.chat'}>;

export type ParsedCommand = {
  localView?: 'chat' | 'help' | 'theme';
  /**
   * Surfaces that also have a key. macOS reserves the function keys for system
   * controls and a terminal may keep a Control chord for itself, so every
   * toggle is reachable by name as well.
   */
  toggle?: 'todos' | 'prompt';
  chatMessage?: string;
  themeName?: ThemeName;
  request?: CommandRequest;
  responseView?: 'perf';
  /** Opens a hypothesis trajectory: the selected row, or the given round. */
  openRound?: {round?: number};
  /**
   * Renders the response in the right pane beside the transcript. Every
   * visualization command opts in through this field; modal surfaces such as
   * /help and errors leave it unset.
   */
  paneView?: PaneView;
  error?: string;
};

export interface SlashCommand {
  name: string;
  description: string;
}

export const SLASH_COMMANDS: readonly SlashCommand[] = [
  {name: '/help', description: 'Show this help'},
  {name: '/chat', description: 'Open experiment chat'},
  {name: '/pause', description: 'Pause after the current agent call'},
  {name: '/resume', description: 'Resume a paused run'},
  {name: '/steer', description: 'Guide the next agent invocation: /steer <message>'},
  {
    name: '/open-round',
    description: 'Open the selected hypothesis, or /open-round --N for round N',
  },
  {name: '/perf', description: 'Plot performance by round in the right pane'},
  {name: '/design', description: 'Summarize each round’s file changes in the right pane'},
  {name: '/todos', description: "Expand or collapse the visible agent's todo list"},
  {name: '/prompt', description: 'Expand or collapse the latest prompt in view'},
  {name: '/theme', description: 'List themes, or switch with /theme <name>'},
];

/**
 * Where the chat is a pane of the current view there is nothing for `/chat` to
 * open, so it leaves the command surface rather than sitting in it as a command
 * that does nothing new. It is still accepted, and still opens the chat
 * anywhere the chat is not already on screen.
 */
export interface CommandContext {
  chatDocked?: boolean;
}

export function availableCommands(context: CommandContext = {}): readonly SlashCommand[] {
  if (!context.chatDocked) return SLASH_COMMANDS;
  return SLASH_COMMANDS.filter(command => command.name !== '/chat');
}

export function helpText(context: CommandContext = {}): string {
  return [
    'Available',
    ...availableCommands(context).map(
      command => `  ${command.name.padEnd(18)} ${command.description}`,
    ),
    '',
    'Planned',
    '  /round <number>    Inspect a completed round',
    '  /invocation <id>   Inspect an agent invocation',
  ].join('\n');
}

export function suggestSlashCommands(
  text: string,
  context: CommandContext = {},
): readonly SlashCommand[] {
  if (!text.startsWith('/') || /\s/.test(text)) return [];
  return availableCommands(context).filter(command => command.name.startsWith(text));
}

export function slashCommandRange(text: string): {start: number; end: number} | null {
  const match = /^\/[a-z][a-z0-9-]*/i.exec(text);
  if (match === null) return null;
  return {start: 0, end: match[0].length};
}

/**
 * The chat composer's own command set. Chat is controlled from the chat, so
 * these live here rather than in the global registry above, and the composer
 * resolves them before anything else it might otherwise forward.
 */
export const CHAT_SLASH_COMMANDS: readonly SlashCommand[] = [
  {name: '/clear', description: 'Start a fresh thread with this thread’s agent and model'},
  {name: '/model', description: 'Pick a harness and model, and start a thread on it'},
  {name: '/resume', description: 'Switch to another chat thread'},
];

export type ChatCommandName = 'clear' | 'model' | 'resume';

export type ParsedChatCommand = {
  command?: ChatCommandName;
  /**
   * The text names a global command instead, e.g. `/pause`. The composer has
   * always forwarded those, so it still does.
   */
  global?: boolean;
  /** Unknown slash input: the chat answers with its own help, not the global. */
  help?: string;
};

export function chatHelpText(): string {
  return [
    'Chat commands',
    ...CHAT_SLASH_COMMANDS.map(command => `  ${command.name.padEnd(10)} ${command.description}`),
    '',
    'Anything else you type is a question for the chat agent.',
  ].join('\n');
}

export function suggestChatSlashCommands(text: string): readonly SlashCommand[] {
  if (!text.startsWith('/') || /\s/.test(text)) return [];
  return CHAT_SLASH_COMMANDS.filter(command => command.name.startsWith(text));
}

/**
 * Resolves slash input typed into the chat composer.
 *
 * `/resume` is deliberately shadowed here: in the chat it resumes a *thread*,
 * while the global input keeps it for resuming a paused run. Chat commands are
 * matched first, so the two never compete for the same surface.
 */
export function parseChatCommand(text: string): ParsedChatCommand {
  if (text === '/clear') return {command: 'clear'};
  if (text === '/model') return {command: 'model'};
  if (text === '/resume') return {command: 'resume'};
  const name = /^\/[a-z][a-z0-9-]*/i.exec(text)?.[0];
  if (name !== undefined && SLASH_COMMANDS.some(command => command.name === name)) {
    return {global: true};
  }
  return {help: chatHelpText()};
}

/** Parses the command surface. Ordinary questions belong to Experiment chat. */
export function parseCommand(text: string): ParsedCommand {
  if (text === '/help') return {localView: 'help'};
  const chat = text.match(/^\/chat(?:\s+(.*))?$/);
  if (chat) {
    const message = chat[1]?.trim();
    return {localView: 'chat', ...(message ? {chatMessage: message} : {})};
  }
  const theme = text.match(/^\/theme(?:\s+(.*))?$/);
  if (theme) {
    const requested = theme[1]?.trim();
    if (!requested) return {localView: 'theme'};
    if (!isThemeName(requested)) {
      return {error: `Unknown theme: ${requested}. Available: ${THEME_NAMES.join(', ')}.`};
    }
    return {localView: 'theme', themeName: requested};
  }
  if (text === '/pause') return {request: {type: 'command.pause'}};
  if (text === '/resume') return {request: {type: 'command.resume'}};
  const steer = text.match(/^\/steer(?:\s+([\s\S]*))?$/);
  if (steer) {
    const message = steer[1]?.trim();
    if (!message) return {error: 'Usage: /steer <message>'};
    return {request: {type: 'command.steer', text: message}};
  }
  const openRound = text.match(/^\/open-round(?:\s+(.*))?$/);
  if (openRound) {
    const argument = openRound[1]?.trim();
    if (!argument) return {openRound: {}};
    // ``--N`` is the documented form; a bare number is accepted because it is
    // the obvious thing to type.
    const match = argument.match(/^(?:--)?(\d+)$/);
    if (!match) {
      return {error: `Unknown round: ${argument}. Use /open-round or /open-round --N.`};
    }
    return {openRound: {round: Number(match[1])}};
  }
  if (text === '/todos') return {toggle: 'todos'};
  if (text === '/prompt') return {toggle: 'prompt'};
  if (text === '/perf') {
    return {request: {type: 'query.performance'}, responseView: 'perf', paneView: 'perf'};
  }
  if (text === '/design') return {request: {type: 'query.design'}, paneView: 'design'};
  if (text.startsWith('/')) return {error: `Unknown command: ${text}. Use /help.`};
  if (text === '') return {error: 'Enter a slash command. Use /help.'};
  return {error: 'Commands start with /. Use Experiment chat for questions.'};
}
