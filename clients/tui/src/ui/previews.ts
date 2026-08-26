const MAX_TOOL_OUTPUT_LINES = 12;
const MAX_PROMPT_LINES = 12;
const MAX_TOOL_ARG_LENGTH = 80;

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

export function toolOutputPreview(content: string, maxLines = MAX_TOOL_OUTPUT_LINES): string {
  const lines = content.split('\n');
  const hasTrailingNewline = lines.at(-1) === '';
  if (hasTrailingNewline) lines.pop();
  if (lines.length <= maxLines) return content;
  const hidden = lines.length - maxLines;
  return `${lines.slice(0, maxLines).join('\n')}\n… ${hidden} more line${hidden === 1 ? '' : 's'} hidden`;
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
