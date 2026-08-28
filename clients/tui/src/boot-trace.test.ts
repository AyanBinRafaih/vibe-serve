import {describe, expect, it} from 'bun:test';
import {resolveStartupTrace} from './boot-trace.js';

describe('resolveStartupTrace', () => {
  it('discards measurements when the trace is not switched on', () => {
    const written: string[] = [];

    for (const env of [{}, {VIBESYS_BOOT_TRACE: ''}, {VIBESYS_BOOT_TRACE: '0'}]) {
      resolveStartupTrace(env, line => written.push(line))('experiments loaded in 3ms');
    }

    expect(written).toEqual([]);
  });

  it('takes only the exact value 1 as a request', () => {
    const written: string[] = [];

    resolveStartupTrace({VIBESYS_BOOT_TRACE: 'true'}, line => written.push(line))('measured');
    expect(written).toEqual([]);

    resolveStartupTrace({VIBESYS_BOOT_TRACE: '1'}, line => written.push(line))('measured');
    expect(written).toEqual(['measured']);
  });

  it('anchors written lines to the launch time the CLI recorded', () => {
    const written: string[] = [];
    const trace = resolveStartupTrace(
      {VIBESYS_BOOT_TRACE: '1', VIBESYS_LAUNCH_START_MS: String(Date.now() - 40)},
      line => written.push(line),
    );

    trace('experiments loaded in 3ms (1 entries)');

    expect(written[0]).toMatch(/^experiments loaded in 3ms \(1 entries\); \d+ms since launch$/);
  });

  it('writes unanchored lines when the launch anchor is missing or unusable', () => {
    const written: string[] = [];

    for (const raw of [undefined, '', 'not-a-number']) {
      const env = raw === undefined ? {} : {VIBESYS_LAUNCH_START_MS: raw};
      resolveStartupTrace({VIBESYS_BOOT_TRACE: '1', ...env}, line => written.push(line))(
        'measured',
      );
    }

    expect(written).toEqual(['measured', 'measured', 'measured']);
  });
});
