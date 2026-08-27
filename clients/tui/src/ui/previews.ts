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
  const formatted = formatJsonObject(content);
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
