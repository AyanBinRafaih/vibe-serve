import {describe, expect, it} from 'bun:test';
import {elapsedLabel, promptPreview, toolCallPreview, toolOutputPreview} from './previews.js';

describe('conversation previews', () => {
  it('formats and truncates typed tool arguments without changing the source data', () => {
    const args = {
      text: 'x'.repeat(200),
      nested: {count: 3, flags: [true, false]},
    };
    const preview = toolCallPreview('Edit', args);

    expect(preview).toContain(`text="${'x'.repeat(80)}..."`);
    expect(preview).toContain('nested={"count":3,"flags":[true,false]}');
    expect(args.text).toHaveLength(200);
    expect(args.nested).toEqual({count: 3, flags: [true, false]});
  });

  it('limits tool output without discarding the underlying content', () => {
    const content = Array.from({length: 20}, (_, index) => `line ${index + 1}`).join('\n');
    const preview = toolOutputPreview(content);

    expect(preview.content).toContain('line 6');
    expect(preview.content).not.toContain('line 7');
    expect(preview).toMatchObject({hiddenLines: 14, collapsible: true});
    expect(content).toContain('line 20');
  });

  it('pretty-prints JSON responses and restores the complete value when expanded', () => {
    const content = JSON.stringify(
      Object.fromEntries(Array.from({length: 10}, (_, index) => [`field_${index}`, index])),
    );

    const collapsed = toolOutputPreview(content);
    const expanded = toolOutputPreview(content, true);

    expect(collapsed.content).toStartWith('{\n  "field_0": 0,');
    expect(collapsed.content).not.toContain('"field_9"');
    expect(collapsed.hiddenLines).toBeGreaterThan(0);
    expect(expanded.content).toContain('  "field_9": 9\n}');
    expect(expanded).toMatchObject({hiddenLines: 0, hiddenCharacters: 0, collapsible: true});
  });

  it('collapses long single-line responses by character count', () => {
    const content = 'x'.repeat(1_000);
    const preview = toolOutputPreview(content);

    expect(preview.content).toHaveLength(600);
    expect(preview).toMatchObject({hiddenLines: 0, hiddenCharacters: 400, collapsible: true});
  });

  it('collapses and expands long prompts', () => {
    const content = Array.from({length: 20}, (_, index) => `prompt line ${index + 1}`).join('\n');

    expect(promptPreview(content, false)).toMatchObject({hiddenLines: 8});
    expect(promptPreview(content, false).content).not.toContain('prompt line 13');
    expect(promptPreview(content, true).content).toContain('prompt line 20');
  });
});

describe('elapsed labels', () => {
  it('drops to the coarsest unit the duration reaches', () => {
    expect(elapsedLabel(45_000)).toBe('45s');
    expect(elapsedLabel(65_000)).toBe('1m 5s');
    expect(elapsedLabel(3_725_000)).toBe('1h 2m');
    expect(elapsedLabel(0)).toBe('0s');
    expect(elapsedLabel(-1)).toBe('0s');
  });
});
