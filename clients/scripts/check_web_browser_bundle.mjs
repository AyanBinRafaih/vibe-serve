#!/usr/bin/env node

import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const bundle = await readFile(join(root, 'web', '.browser-dist', 'browser-entry.js'), 'utf8');
const forbidden = [...bundle.matchAll(/(?:node:|node_modules\/node:)[^'"\s]*/g)].map(
  match => match[0],
);
if (forbidden.length > 0) {
  console.error(`Browser bundle contains Node builtins: ${[...new Set(forbidden)].join(', ')}`);
  process.exitCode = 1;
} else {
  console.log('Browser transport bundle contains no Node builtin imports.');
}
