import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { gzipSync } from 'node:zlib'
import { readFile } from 'node:fs/promises'
import { transform } from 'esbuild'
import { createServer } from 'node:http'

const source = await readFile(new URL('../src/lib/dataTransport.ts', import.meta.url), 'utf8')
const { code } = await transform(source, { loader: 'ts', format: 'esm', target: 'es2022' })
const { createDataClient } = await import(`data:text/javascript;base64,${Buffer.from(code+'\n//# sourceURL=atlas-data-transport.test.js').toString('base64')}`)
const sha = bytes => createHash('sha256').update(bytes).digest('hex')

function fixture() {
  const bodies = new Map()
  const calls = []
  const index = { schema: 'c2s-atlas-transport-v1', atlas_schema: 'c2s-atlas-v4', files: {}, annotations: {} }
  function add(logical, payload, compressed = true, raw = null) {
    const decoded = Buffer.from(raw ?? JSON.stringify(payload))
    const encoded = compressed ? gzipSync(decoded) : decoded
    const path = compressed ? logical.replace('.json', `.${sha(encoded).slice(0, 16)}.json.gz`) : logical
    const descriptor = { path, encoding: compressed ? 'gzip' : 'identity', encoded_bytes: encoded.length,
      decoded_bytes: decoded.length, encoded_sha256: sha(encoded), decoded_sha256: sha(decoded) }
    index.files[logical] = descriptor
    bodies.set(path, { bytes: encoded, status: 200 })
    return descriptor
  }
  for (const name of ['global_summary.json', 'gene_index_counts.json', 'ontology_index_counts.json']) add(name, {}, false)
  const details = id => ({ genes: [{ n: `GENE${id}`, a: 1.234567890123, fc: 8 }],
    significant_term_ids: id === 513 ? [] : ['GO_BP::λ process'],
    anns: id === 513 ? [] : [{ o: 'GO_BP', t: 'λ process', id: 'GO_BP::λ process',
      q: .000123456789, p: .000123456789, fold: 4.5, or: 4.5, n: 2, g: ['GENE1'] }], network_evidence: [] })
  const records = { 0: { '0': details(0), '255': details(255) },
    1: { '256': details(256), '511': details(511) }, 2: { '512': details(512), '513': details(513) } }
  index.annotations['0'] = { chunk_size: 256, n_features: 6, chunks: {} }
  for (const [chunk, payload] of Object.entries(records)) {
    const resource = `annotations/L00/chunk_${chunk}.json`
    add(resource, payload)
    index.annotations['0'].chunks[chunk] = { resource, first_feature_id: Number(chunk)*256,
      last_feature_id: (Number(chunk)+1)*256-1, n_features: Object.keys(payload).length }
  }
  const fetcher = async url => {
    const path = String(url).replace('/data/', '').split('?')[0]
    calls.push(String(url))
    if (path === 'data_transport.json') return new Response(JSON.stringify(index))
    const entry = bodies.get(path)
    return new Response(entry?.bytes ?? 'missing', { status: entry?.status ?? 404, headers: entry?.headers })
  }
  return { index, bodies, calls, add, records, fetcher }
}

test('loads complete boundary/dead feature records on demand and deduplicates a chunk', async () => {
  const f = fixture()
  const client = createDataClient('/data/', f.fetcher)
  const [first, boundary] = await Promise.all([client.getFeatureAnnotations(0, 0), client.getFeatureAnnotations(0, 255)])
  assert.deepEqual(first, f.records[0]['0'])
  assert.deepEqual(boundary, f.records[0]['255'])
  for (const id of [256, 511, 512, 513]) assert.deepEqual(await client.getFeatureAnnotations(0, id), f.records[Math.floor(id/256)][id])
  assert.equal(f.calls.filter(path => path.includes('chunk_0.')).length, 1)
  assert.equal(f.calls.filter(path => path.includes('.json.gz')).length, 3)
  assert.ok(f.calls.filter(path => path.includes('.json.gz')).every(path => path.includes('?v=')))
  assert.ok(!f.calls.some(path => path.includes('layer_00_annotations.json')))
})

test('hashes decoded bytes before parsing, including noncanonical numbers and Unicode', async () => {
  const f = fixture()
  f.add('exact.json', null, true, '{"number":1.0,"name":"λ","q":1.2300e-7}')
  assert.deepEqual(await createDataClient('/data/', f.fetcher).getJson('exact.json'), { number: 1, name: 'λ', q: .000000123 })
})

test('rejects corrupted transport or decoded data, retries failures and never returns empty annotations', async () => {
  const f = fixture()
  const descriptor = f.index.files['annotations/L00/chunk_0.json']
  const entry = f.bodies.get(descriptor.path)
  const original = entry.bytes
  entry.bytes = Buffer.from(original)
  entry.bytes[15] ^= 1
  const client = createDataClient('/data/', f.fetcher)
  await assert.rejects(client.getFeatureAnnotations(0, 0), /SHA256/)
  entry.bytes = original
  assert.deepEqual(await client.getFeatureAnnotations(0, 0), f.records[0]['0'])
  descriptor.decoded_sha256 = '0'.repeat(64)
  await assert.rejects(createDataClient('/data/', f.fetcher).getFeatureAnnotations(0, 0), /SHA256/)
})

test('supports a browser-decoded Content-Encoding response without inflating twice', async () => {
  const f = fixture()
  const descriptor = f.index.files['annotations/L00/chunk_0.json']
  f.bodies.set(descriptor.path, { bytes: Buffer.from(JSON.stringify(f.records[0])), status: 200,
    headers: { 'Content-Encoding': 'gzip' } })
  assert.deepEqual(await createDataClient('/data/', f.fetcher).getFeatureAnnotations(0, 0), f.records[0]['0'])
})

test('legacy fallback is restricted to an actual transport-index HTTP404', async () => {
  const calls = []
  const fetcher = async url => {
    calls.push(String(url))
    return String(url).endsWith('data_transport.json') ? new Response('', { status: 404 })
      : new Response(JSON.stringify({ '3': { genes: [], anns: [] } }))
  }
  const client = createDataClient('/data/', fetcher)
  assert.deepEqual(await client.getFeatureAnnotations(0, 3), { genes: [], anns: [] })
  assert.equal(await client.getFeatureAnnotations(0, 4), null)
  assert.ok(calls.some(path => path.includes('layer_00_annotations.json')))
  for (const response of [new Response('', { status: 500 }), new Response('{broken'), new Response('{"schema":"wrong"}')]) {
    await assert.rejects(createDataClient('/data/', async () => response).getFeatureAnnotations(0, 3))
  }
})

test('missing chunks/features and optional-v4 integrity failures are errors', async () => {
  const f = fixture()
  await assert.rejects(createDataClient('/data/', f.fetcher).getFeatureAnnotations(0, 1), /feature/i)
  const savedChunk = f.index.annotations['0'].chunks['1']
  delete f.index.annotations['0'].chunks['1']
  await assert.rejects(createDataClient('/data/', f.fetcher).getFeatureAnnotations(0, 256), /chunk/i)
  f.index.annotations['0'].chunks['1'] = savedChunk
  const counts = f.index.files['gene_index_counts.json']
  f.bodies.set(counts.path, { bytes: Buffer.from('{"bad":1}'), status: 200 })
  await assert.rejects(createDataClient('/data/', f.fetcher).getOptionalJson('gene_index_counts.json'), /bytes|SHA256/)
})

test('annotation cache evicts old chunks and unsupported gzip is explicit', async () => {
  const f = fixture()
  const client = createDataClient('/data/', f.fetcher, 2)
  for (const id of [0, 256, 512, 0]) await client.getFeatureAnnotations(0, id)
  assert.equal(f.calls.filter(path => path.includes('chunk_0.')).length, 2)
  const saved = globalThis.DecompressionStream
  try {
    globalThis.DecompressionStream = undefined
    await assert.rejects(createDataClient('/data/', f.fetcher).getFeatureAnnotations(0, 0), /browser.*gzip/i)
  } finally {
    globalThis.DecompressionStream = saved
  }
})

test('real HTTP fetch handles both raw gzip files and automatic Content-Encoding decoding', async () => {
  const f = fixture()
  let encodedHeader = false
  const server = createServer((request, response) => {
    const path = request.url.slice(1).split('?')[0]
    if (path === 'data_transport.json') {
      response.writeHead(200, { 'Content-Type': 'application/json' })
      response.end(JSON.stringify(f.index))
      return
    }
    const entry = f.bodies.get(path)
    const headers = { 'Content-Type': path.endsWith('.gz') ? 'application/gzip' : 'application/json' }
    if (encodedHeader && path.endsWith('.gz')) headers['Content-Encoding'] = 'gzip'
    response.writeHead(entry?.status ?? 404, headers)
    response.end(entry?.bytes ?? 'missing')
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const base = `http://127.0.0.1:${server.address().port}/`
  try {
    const descriptor = f.index.files['annotations/L00/chunk_0.json']
    const response = await fetch(base+descriptor.path)
    assert.equal(response.headers.get('content-encoding'), null)
    assert.equal(Buffer.from(await response.arrayBuffer()).subarray(0, 2).toString('hex'), '1f8b')
    assert.deepEqual(await createDataClient(base).getFeatureAnnotations(0, 0), f.records[0]['0'])
    encodedHeader = true
    assert.deepEqual(await createDataClient(base).getFeatureAnnotations(0, 256), f.records[1]['256'])
  } finally {
    server.closeAllConnections()
    await new Promise(resolve => server.close(resolve))
  }
})
