// Exercise the production transport client against an explicit real bundle.
// This uses native HTTP Fetch/DecompressionStream and never writes bundle files.
import assert from 'node:assert/strict'
import { readFile, writeFile, stat } from 'node:fs/promises'
import { createServer } from 'node:http'
import { resolve, sep } from 'node:path'
import { transform } from 'esbuild'

const args = process.argv.slice(2)
const argument = name => args.includes(name) ? args[args.indexOf(name) + 1] : null
assert.ok(argument('--data'), 'Pass --data /absolute/path/to/data')
const root = resolve(argument('--data'))
const source = await readFile(new URL('../src/lib/dataTransport.ts', import.meta.url), 'utf8')
const { code } = await transform(source, { loader: 'ts', format: 'esm', target: 'es2022' })
const { createDataClient } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`)
let requests = 0
const server = createServer(async (request, response) => {
  try {
    const relative = decodeURIComponent(new URL(request.url, 'http://localhost').pathname).slice(1)
    const path = resolve(root, relative)
    if (!path.startsWith(root + sep) || !(await stat(path)).isFile()) throw new Error('missing')
    requests++
    response.writeHead(200, { 'Content-Type': path.endsWith('.gz') ? 'application/gzip' : 'application/json' })
    response.end(await readFile(path))
  } catch {
    response.writeHead(404)
    response.end('missing')
  }
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const started = performance.now()
try {
  const client = createDataClient(`http://127.0.0.1:${server.address().port}/`)
  const summary = await client.getJson('global_summary.json')
  assert.equal(summary.n_layers, summary.layers.length)
  assert.equal(summary.total_features, summary.layers.reduce((sum, row) => sum + row.alive + row.dead, 0))
  let detailChecks = 0
  for (const layer of summary.layers) {
    const ids = [...new Set([0, 255, 256, 511, 512, layer.alive + layer.dead - 1])]
    for (const id of ids) {
      const detail = await client.getFeatureAnnotations(layer.layer, id)
      assert.ok(detail && Array.isArray(detail.genes) && Array.isArray(detail.anns)
        && Array.isArray(detail.significant_term_ids) && Array.isArray(detail.network_evidence))
      detailChecks++
    }
  }
  assert.ok(await client.getOptionalJson('gene_index_counts.json'))
  assert.ok(await client.getOptionalJson('ontology_index_counts.json'))
  assert.equal(await client.getOptionalJson('not_exported_optional.json'), null)
  const result = { status: 'passed', data: root, layers: summary.n_layers, details_checked: detailChecks,
    http_requests: requests, seconds: (performance.now() - started) / 1000,
    scope: 'production data client with native HTTP Fetch and gzip decoding; no browser visual QA' }
  if (argument('--out')) await writeFile(argument('--out'), JSON.stringify(result, null, 2) + '\n')
  console.log(JSON.stringify(result, null, 2))
} finally {
  server.closeAllConnections()
  await new Promise(resolve => server.close(resolve))
}
