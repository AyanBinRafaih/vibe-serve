import {defineConfig} from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'node',
    // OpenTUI's renderer tests need Bun's native FFI, so the suite runs under
    // Bun. Bun cannot host Vitest's worker_threads pools: `threads` and
    // `vmThreads` collect the files, run nothing, and still exit 0. The pool
    // therefore has to be `forks`, and a single fork keeps that to one spawned
    // Bun process rather than the one-per-CPU that `_ensureMinimumWorkers`
    // requests, which is where CI hit EACCES.
    pool: 'forks',
    poolOptions: {forks: {singleFork: true}},
    fileParallelism: false,
  },
});
