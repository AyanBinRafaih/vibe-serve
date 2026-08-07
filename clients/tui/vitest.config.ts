import {defineConfig} from 'vitest/config';

// Everything except the OpenTUI renderer suite, which needs Bun's native FFI
// and runs separately from vitest.renderer.config.ts. Node hosts these so the
// bulk of the suite never spawns a Bun worker.
export default defineConfig({
  test: {
    environment: 'node',
    exclude: ['**/node_modules/**', 'src/ui/app.test.ts'],
  },
});
