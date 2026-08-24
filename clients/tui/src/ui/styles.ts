import {SyntaxStyle} from '@opentui/core';
import type {ConversationEntry} from '../session-model.js';
import type {ConversationRole, ConversationRoleColors, Theme} from './theme.js';

export type EntryPalette = ConversationRoleColors;

export function createMarkdownStyle(theme: Theme): SyntaxStyle {
  const {markdown} = theme;
  return SyntaxStyle.fromStyles({
    default: {fg: markdown.default},
    heading: {fg: markdown.heading, bold: true},
    strong: {fg: markdown.strong, bold: true},
    em: {fg: markdown.em, italic: true},
    code: {fg: markdown.code, bg: markdown.codeBackground},
    link: {fg: markdown.link, underline: true},
    blockquote: {fg: markdown.blockquote, italic: true},
  });
}

export function conversationRole(entry: ConversationEntry): ConversationRole {
  if (entry.tone === 'failure') return 'failure';
  if (entry.tone === 'success') return 'success';
  if (entry.kind === 'assistant') return 'assistant';
  if (entry.kind === 'user') return 'user';
  if (entry.kind === 'prompt') return 'prompt';
  // An agent narrating its own work is analysis whichever channel carried it:
  // the diagnostic channel is where most backends put that narration, and
  // slate-on-slate buried it. Tool turns keep the neutral surface.
  if (entry.kind === 'analysis' || entry.kind === 'diagnostic') return 'analysis';
  if (entry.kind === 'tool' || entry.kind === 'subprocess') return 'tool';
  return 'neutral';
}

export function entryPalette(entry: ConversationEntry, theme: Theme): EntryPalette {
  return theme.conversation[conversationRole(entry)];
}
