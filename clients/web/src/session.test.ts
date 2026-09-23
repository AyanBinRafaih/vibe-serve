import {describe, expect, test} from 'bun:test';
import {webSocketUrlFromLocation} from './session.js';

describe('webSocketUrlFromLocation', () => {
  test('uses the gateway capability URL from the browser harness', () => {
    expect(
      webSocketUrlFromLocation({
        href: 'http://127.0.0.1:5173/?gateway=http%3A%2F%2F127.0.0.1%3A8765%2F%3Ftoken%3Dsecret',
      } as Location),
    ).toBe('ws://127.0.0.1:8765/ws?token=secret');
  });

  test('uses the direct gateway page URL', () => {
    expect(
      webSocketUrlFromLocation({
        href: 'https://127.0.0.1:8765/?token=secret',
      } as Location),
    ).toBe('wss://127.0.0.1:8765/ws?token=secret');
  });
});
