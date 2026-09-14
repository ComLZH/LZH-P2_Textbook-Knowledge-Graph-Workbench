import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { performance } from 'node:perf_hooks';
import { fileURLToPath } from 'node:url';


const [inputPath, outputPath] = process.argv.slice(2);
if (!inputPath || !outputPath) {
  console.error('usage: node elk_layout_runner.mjs <input.json> <output.json>');
  process.exit(2);
}

const runtimeRoot = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const ELK = require(path.join(runtimeRoot, 'elk', 'elk.bundled.js'));
const input = JSON.parse(await fs.readFile(inputPath, 'utf8'));
const started = performance.now();
const elk = new ELK();
if (input && input.protocol_version === 'p2_elk_batch_v1') {
  const results = [];
  const writeCheckpoint = async (complete) => {
    await fs.writeFile(
      outputPath,
      JSON.stringify({
        protocol_version: 'p2_elk_batch_v1',
        request_id: input.request_id,
        complete,
        elapsed_ms: performance.now() - started,
        results,
      }),
      'utf8',
    );
  };
  for (const job of input.component_jobs || []) {
    const jobStarted = performance.now();
    try {
      const graph = await elk.layout(job.graph);
      results.push({
        component_id: job.component_id,
        status: 'completed',
        elapsed_ms: performance.now() - jobStarted,
        graph,
      });
    } catch (error) {
      results.push({
        component_id: job.component_id,
        status: 'failed',
        elapsed_ms: performance.now() - jobStarted,
        error: String(error && error.message ? error.message : error).slice(0, 500),
      });
    }
    await writeCheckpoint(false);
  }
  await writeCheckpoint(true);
} else {
  const graph = await elk.layout(input);
  await fs.writeFile(
    outputPath,
    JSON.stringify({ elapsed_ms: performance.now() - started, graph }),
    'utf8',
  );
}
