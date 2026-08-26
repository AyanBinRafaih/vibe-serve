import type {CliRenderer} from '@opentui/core';

export type ClipboardCopyResult = 'no-selection' | 'copied' | 'unsupported';

/** Clipboard behavior owned by the terminal renderer boundary. */
export interface SelectionClipboard {
  copySelection(): ClipboardCopyResult;
}

type ClipboardRenderer = Pick<
  CliRenderer,
  'copyToClipboardOSC52' | 'getSelection' | 'isOsc52Supported'
>;

/**
 * Copies OpenTUI's current text selection without exposing renderer details to
 * key routing or session state.
 *
 * A failed copy deliberately leaves the selection intact. The operator can
 * then use the terminal's native selection and copy path instead.
 */
export class RendererSelectionClipboard implements SelectionClipboard {
  constructor(private readonly renderer: ClipboardRenderer) {}

  copySelection(): ClipboardCopyResult {
    const text = this.renderer.getSelection()?.getSelectedText() ?? '';
    if (text.length === 0) return 'no-selection';

    try {
      if (!this.renderer.isOsc52Supported()) return 'unsupported';
      return this.renderer.copyToClipboardOSC52(text) ? 'copied' : 'unsupported';
    } catch {
      return 'unsupported';
    }
  }
}
