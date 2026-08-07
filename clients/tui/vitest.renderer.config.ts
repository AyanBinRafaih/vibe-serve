import {defineConfig} from 'vitest/config';

// The OpenTUI renderer tests only initialize under Bun, and Bun cannot host
// Vitest's worker_threads pools -- `threads` and `vmThreads` collect the files,
// run nothing, and still exit 0 -- so this has to use `forks`. Confining it to
// the one file that needs Bun keeps the spawned-Bun-worker count to a minimum,
// which is where CI hits EACCES.
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/ui/app.test.ts'],
    pool: 'forks',
    poolOptions: {forks: {singleFork: true}},
    fileParallelism: false,
  },
});
