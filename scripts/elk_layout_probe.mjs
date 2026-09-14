import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { performance } from 'node:perf_hooks';


const [moduleRoot, inputPath, outputPath] = process.argv.slice(2);
if (!moduleRoot || !inputPath || !outputPath) {
  console.error('usage: node elk_layout_probe.mjs <elk-package-root> <input.json> <output.json>');
  process.exit(2);
}

const require = createRequire(import.meta.url);
const ELK = require(path.join(path.resolve(moduleRoot), 'lib', 'elk.bundled.js'));
const input = JSON.parse(await fs.readFile(inputPath, 'utf8'));
const started = performance.now();
const result = await new ELK().layout(input);
const elapsedMs = performance.now() - started;
await fs.writeFile(
  outputPath,
  JSON.stringify({ elapsed_ms: elapsedMs, graph: result }, null, 2),
  'utf8',
);
