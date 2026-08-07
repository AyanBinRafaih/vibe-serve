import type {RequestInput} from './protocol.js';
import {isThemeName, listThemes, THEME_NAMES, type ThemeName} from './ui/theme.js';

export type ParsedInput = {
  localView?: 'chat' | 'help' | 'theme';
  chatMessage?: string;
  themeName?: ThemeName;
  request?: RequestInput;
  responseView?: 'history' | 'perf';
  error?: string;
};

export interface SlashCommand {
  name: string;
  description: string;
}

export const SLASH_COMMANDS: readonly SlashCommand[] = [
  {name: '/help', description: 'Show this help'},
  {name: '/chat', description: 'Open experiment chat'},
  {name: '/history', description: 'List rounds and their elapsed time'},
  {name: '/perf', description: 'Plot performance by round'},
  {name: '/theme', description: 'List themes, or switch with /theme <name>'},
];

export function themeListText(current: ThemeName): string {
  return [
    'Themes',
    ...listThemes().map(
      theme =>
        `  ${theme.name === current ? '›' : ' '} ${theme.name.padEnd(20)} ${theme.label} (${theme.appearance})`,
    ),
    '',
    'Switch with /theme <name>. This applies to the current session only;',
    'set [tui].theme in agent.toml or pass --theme to change the default.',
  ].join('\n');
}

export const HELP_TEXT = [
  'Available',
  ...SLASH_COMMANDS.map(command => `  ${command.name.padEnd(18)} ${command.description}`),
  '',
  'Planned',
  '  /pause             Pause at the next safe point',
  '  /resume            Resume a paused run',
  '  /steer <message>   Guide a future agent invocation',
  '  /round <number>    Inspect a completed round',
  '  /invocation <id>   Inspect an agent invocation',
].join('\n');

export function suggestSlashCommands(text: string): readonly SlashCommand[] {
  if (!text.startsWith('/') || /\s/.test(text)) return [];
  return SLASH_COMMANDS.filter(command => command.name.startsWith(text));
}

export function slashCommandRange(text: string): {start: number; end: number} | null {
  const match = /^\/[a-z][a-z0-9-]*/i.exec(text);
  if (match === null) return null;
  return {start: 0, end: match[0].length};
}

export function parseInput(text: string): ParsedInput {
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
  if (text === '/history') return {request: {type: 'query.history'}, responseView: 'history'};
  if (text === '/perf') return {request: {type: 'query.performance'}, responseView: 'perf'};
  if (text.startsWith('/')) return {error: `Unknown command: ${text}. Use /help.`};
  if (text === '') return {error: 'Enter a question or use /help.'};
  return {request: {type: 'query.chat', text}};
}
