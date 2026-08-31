import type {ToolResultPayload} from '@vibesys/core-state';

const MAX_TOOL_OUTPUT_LINES = 6;
const MAX_TOOL_OUTPUT_CHARACTERS = 600;
const MAX_PROMPT_LINES = 12;
const MAX_TOOL_ARG_LENGTH = 80;

export interface CollapsiblePreview {
  content: string;
  hiddenLines: number;
  hiddenCharacters: number;
  collapsible: boolean;
}

export function toolCallPreview(tool: string, args: Record<string, unknown>): string {
  const parts = Object.entries(args).map(([key, value]) => {
    const stringValue = typeof value === 'string';
    let rendered = stringValue ? value : (JSON.stringify(value) ?? String(value));
    if (rendered.length > MAX_TOOL_ARG_LENGTH) {
      rendered = `${rendered.slice(0, MAX_TOOL_ARG_LENGTH)}...`;
    }
    return stringValue ? `${key}="${rendered}"` : `${key}=${rendered}`;
  });
  return `→ ${tool}(${parts.join(', ')})\n`;
}

export function toolOutputPreview(
  content: string,
  expanded = false,
  maxLines = MAX_TOOL_OUTPUT_LINES,
  maxCharacters = MAX_TOOL_OUTPUT_CHARACTERS,
): CollapsiblePreview {
  return collapsePreview(formatJsonObject(content), expanded, maxLines, maxCharacters);
}

/**
 * Renders a tool result from its typed payload when the backend preserved
 * one, falling back to the string-sniffing path for older event logs.
 */
export function toolResultPreview(
  content: string,
  payload: ToolResultPayload | null | undefined,
  expanded = false,
): CollapsiblePreview {
  if (payload == null) return toolOutputPreview(content, expanded);
  return collapsePreview(
    formatToolResultPayload(payload, content),
    expanded,
    MAX_TOOL_OUTPUT_LINES,
    MAX_TOOL_OUTPUT_CHARACTERS,
  );
}

function formatToolResultPayload(payload: ToolResultPayload, fallback: string): string {
  if (payload.kind === 'json') return JSON.stringify(payload.value, null, 2);
  if (payload.kind === 'command') {
    const sections: string[] = [];
    if (payload.stdout !== '') sections.push(payload.stdout.replace(/\n+$/, ''));
    if (payload.stderr !== '') sections.push(`stderr:\n${payload.stderr.replace(/\n+$/, '')}`);
    if (payload.exit_code != null) sections.push(`exit code: ${payload.exit_code}`);
    if (sections.length > 0) return sections.join('\n');
  }
  return fallback;
}

function collapsePreview(
  formatted: string,
  expanded: boolean,
  maxLines: number,
  maxCharacters: number,
): CollapsiblePreview {
  const lines = formatted.split('\n');
  if (lines.at(-1) === '') lines.pop();
  const full = lines.join('\n');
  const collapsible = lines.length > maxLines || full.length > maxCharacters;
  if (expanded || !collapsible) {
    return {content: full, hiddenLines: 0, hiddenCharacters: 0, collapsible};
  }

  const lineLimited = lines.slice(0, maxLines).join('\n');
  const preview = lineLimited.slice(0, maxCharacters).trimEnd();
  const visibleLines = preview === '' ? 0 : preview.split('\n').length;
  return {
    content: preview,
    hiddenLines: Math.max(0, lines.length - visibleLines),
    hiddenCharacters: Math.max(0, full.length - preview.length),
    collapsible,
  };
}

function formatJsonObject(content: string): string {
  const trimmed = content.trim();
  if (!(trimmed.startsWith('{') || trimmed.startsWith('['))) return content;
  try {
    const parsed: unknown = JSON.parse(trimmed);
    if (parsed === null || typeof parsed !== 'object') return content;
    return JSON.stringify(parsed, null, 2);
  } catch {
    return content;
  }
}

export function promptPreview(
  content: string,
  expanded: boolean,
  maxLines = MAX_PROMPT_LINES,
): {content: string; hiddenLines: number} {
  const lines = content.split('\n');
  if (lines.at(-1) === '') lines.pop();
  const hiddenLines = Math.max(0, lines.length - maxLines);
  return {
    content: expanded || hiddenLines === 0 ? content : lines.slice(0, maxLines).join('\n'),
    hiddenLines,
  };
}

export function elapsedLabel(elapsedMs: number): string {
  const totalSeconds = Math.max(0, Math.floor(elapsedMs / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}
