import {SyntaxStyle} from '@opentui/core';
import type {ConversationEntry} from '../session-model.js';
import type {ConversationRole, ConversationRoleColors, Theme} from './theme.js';

export type EntryPalette = ConversationRoleColors;

export function createMarkdownStyle(theme: Theme): SyntaxStyle {
  const {markdown} = theme;
  // Style names are the markup.* capture groups the markdown renderer emits.
  // Lookup tries the exact capture and then only its first dotted segment, so
  // the numbered heading captures each need their own entry: a plain
  // "heading" entry is never consulted, which left every markdown color
  // except default unused.
  const heading = {fg: markdown.heading, bold: true};
  const code = {fg: markdown.code, bg: markdown.codeBackground};
  return SyntaxStyle.fromStyles({
    default: {fg: markdown.default},
    'markup.heading': heading,
    'markup.heading.1': heading,
    'markup.heading.2': heading,
    'markup.heading.3': heading,
    'markup.heading.4': heading,
    'markup.heading.5': heading,
    'markup.heading.6': heading,
    'markup.strong': {fg: markdown.strong, bold: true},
    'markup.italic': {fg: markdown.em, italic: true},
    'markup.raw': code,
    'markup.raw.block': code,
    'markup.link': {fg: markdown.link, underline: true},
    'markup.link.url': {fg: markdown.link, underline: true},
    'markup.link.label': {fg: markdown.link},
    'markup.quote': {fg: markdown.blockquote, italic: true},
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
