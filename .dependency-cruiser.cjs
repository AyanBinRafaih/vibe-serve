/** @type {import('dependency-cruiser').IConfiguration} */
module.exports = {
  forbidden: [
    {
      name: 'no-circular-dependencies',
      severity: 'error',
      from: {path: '^clients/'},
      to: {circular: true},
    },
    {
      name: 'no-unresolvable-imports',
      severity: 'error',
      from: {path: '^clients/'},
      to: {couldNotResolve: true, pathNot: '^bun:test$'},
    },
    {
      name: 'backend-client-is-lowest-layer',
      severity: 'error',
      from: {path: '^clients/backend-client/src/'},
      to: {
        path: ['^clients/(?:core-state|tui)/', '/node_modules/@vibesys/(?:core-state|tui)/'],
      },
    },
    {
      name: 'core-state-does-not-depend-on-tui',
      severity: 'error',
      from: {path: '^clients/core-state/src/'},
      to: {path: ['^clients/tui/', '/node_modules/@vibesys/tui/']},
    },
    {
      name: 'workspace-packages-use-public-exports',
      severity: 'error',
      from: {path: '^clients/([^/]+)/src/'},
      to: {
        path: '^clients/',
        pathNot: '^clients/$1/',
        dependencyTypes: ['local', 'localmodule'],
        dependencyTypesNot: ['aliased-tsconfig-paths'],
      },
    },
    {
      name: 'core-state-has-no-node-runtime',
      severity: 'error',
      from: {path: '^clients/core-state/src/', pathNot: '\\.test\\.[cm]?[jt]sx?$'},
      to: {dependencyTypes: ['core']},
    },
    {
      name: 'core-state-has-no-ui-runtime',
      severity: 'error',
      from: {path: '^clients/core-state/src/'},
      to: {path: '@opentui[+/]'},
    },
    {
      name: 'production-dependencies-are-declared',
      severity: 'error',
      from: {path: '^clients/[^/]+/src/', pathNot: '\\.test\\.[cm]?[jt]sx?$'},
      to: {dependencyTypes: ['npm-no-pkg', 'npm-unknown']},
    },
  ],
  options: {
    tsConfig: {fileName: 'tsconfig.architecture.json'},
    doNotFollow: {path: 'node_modules'},
    preserveSymlinks: true,
    tsPreCompilationDeps: true,
    enhancedResolveOptions: {
      conditionNames: ['types', 'import', 'node', 'default'],
      extensions: ['.ts', '.tsx', '.mts', '.cts', '.js', '.jsx', '.mjs', '.cjs', '.d.ts', '.json'],
    },
    reporterOptions: {
      text: {highlightFocused: true},
    },
  },
};
