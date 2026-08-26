import {describe, expect, it} from 'bun:test';
import type {CliRenderer} from '@opentui/core';
import {RendererSelectionClipboard} from './clipboard.js';

type ClipboardRenderer = Pick<
  CliRenderer,
  'copyToClipboardOSC52' | 'getSelection' | 'isOsc52Supported'
>;

describe('renderer selection clipboard', () => {
  it('distinguishes an empty selection from a copy failure', () => {
    const renderer = fakeRenderer({selectedText: ''});

    expect(new RendererSelectionClipboard(renderer).copySelection()).toBe('no-selection');
    expect(renderer.copyCalls).toEqual([]);
  });

  it('copies the exact selected text through OSC52', () => {
    const renderer = fakeRenderer({selectedText: 'line one\nline two'});

    expect(new RendererSelectionClipboard(renderer).copySelection()).toBe('copied');
    expect(renderer.copyCalls).toEqual(['line one\nline two']);
  });

  it('does not attempt a write when OSC52 is unsupported', () => {
    const renderer = fakeRenderer({selectedText: 'keep me selected', supported: false});

    expect(new RendererSelectionClipboard(renderer).copySelection()).toBe('unsupported');
    expect(renderer.copyCalls).toEqual([]);
  });

  it('reports rejected and throwing OSC52 writes as unsupported', () => {
    const rejected = fakeRenderer({selectedText: 'selected', copySucceeds: false});
    const throwing = fakeRenderer({selectedText: 'selected', copyError: new Error('terminal')});

    expect(new RendererSelectionClipboard(rejected).copySelection()).toBe('unsupported');
    expect(new RendererSelectionClipboard(throwing).copySelection()).toBe('unsupported');
  });
});

function fakeRenderer(options: {
  selectedText: string;
  supported?: boolean;
  copySucceeds?: boolean;
  copyError?: Error;
}): ClipboardRenderer & {copyCalls: string[]} {
  const copyCalls: string[] = [];
  return {
    copyCalls,
    getSelection: () => ({getSelectedText: () => options.selectedText}) as never,
    isOsc52Supported: () => options.supported ?? true,
    copyToClipboardOSC52: text => {
      copyCalls.push(text);
      if (options.copyError) throw options.copyError;
      return options.copySucceeds ?? true;
    },
  };
}
