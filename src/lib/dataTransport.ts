import type { FeatureAnnotations } from './types'

interface Resource {
  path: string
  encoding: 'identity' | 'gzip'
  encoded_bytes: number
  decoded_bytes: number
  encoded_sha256: string
  decoded_sha256: string
}

interface AnnotationChunk {
  resource: string
  first_feature_id: number
  last_feature_id: number
  n_features: number
}

interface TransportIndex {
  schema: 'c2s-atlas-transport-v1'
  atlas_schema: 'c2s-atlas-v4'
  files: Record<string, Resource>
  annotations: Record<string, { chunk_size: number; n_features: number; chunks: Record<string, AnnotationChunk> }>
}

class HttpError extends Error {
  status: number
  constructor(path: string, status: number) {
    super(`Failed to load ${path}: HTTP ${status}`)
    this.status = status
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function validateIndex(value: unknown): TransportIndex {
  if (!isRecord(value) || value.schema !== 'c2s-atlas-transport-v1' || value.atlas_schema !== 'c2s-atlas-v4'
      || !isRecord(value.files) || !isRecord(value.annotations)) throw new Error('Invalid atlas transport schema')
  const index = value as unknown as TransportIndex
  for (const required of ['global_summary.json', 'gene_index_counts.json', 'ontology_index_counts.json']) {
    if (!(required in index.files)) throw new Error(`Missing required atlas transport resource: ${required}`)
  }
  for (const resource of Object.values(index.files)) {
    if (!isRecord(resource) || typeof resource.path !== 'string' || resource.path.startsWith('/')
        || resource.path.split('/').includes('..') || /[:?#]/.test(resource.path)
        || !['identity', 'gzip'].includes(resource.encoding)
        || !Number.isSafeInteger(resource.encoded_bytes) || resource.encoded_bytes < 1
        || !Number.isSafeInteger(resource.decoded_bytes) || resource.decoded_bytes < 1
        || !/^[a-f0-9]{64}$/.test(resource.encoded_sha256) || !/^[a-f0-9]{64}$/.test(resource.decoded_sha256)) {
      throw new Error('Invalid atlas transport resource descriptor')
    }
  }
  for (const layer of Object.values(index.annotations)) {
    if (!isRecord(layer) || !Number.isSafeInteger(layer.chunk_size) || layer.chunk_size < 1 || layer.chunk_size > 512
        || !Number.isSafeInteger(layer.n_features) || layer.n_features < 0 || !isRecord(layer.chunks)) {
      throw new Error('Invalid annotation layer descriptor')
    }
    let count = 0
    for (const [key, chunk] of Object.entries(layer.chunks)) {
      const bin = Number(key)
      if (!isRecord(chunk) || !Number.isSafeInteger(bin) || bin < 0 || String(bin) !== key
          || !(chunk.resource in index.files) || index.files[chunk.resource].encoding !== 'gzip'
          || chunk.first_feature_id !== bin*layer.chunk_size || chunk.last_feature_id !== (bin+1)*layer.chunk_size-1
          || !Number.isSafeInteger(chunk.n_features) || chunk.n_features < 1 || chunk.n_features > layer.chunk_size) {
        throw new Error('Invalid annotation chunk descriptor')
      }
      count += chunk.n_features
    }
    if (count !== layer.n_features) throw new Error('Annotation chunk counts do not match the layer count')
  }
  return index
}

async function sha256(bytes: ArrayBuffer): Promise<string> {
  if (!globalThis.crypto?.subtle) throw new Error('Atlas integrity checks require HTTPS or localhost')
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
}

async function verifyBytes(bytes: ArrayBuffer, length: number, digest: string, path: string): Promise<void> {
  if (bytes.byteLength !== length) throw new Error(`Unexpected bytes in atlas resource: ${path}`)
  if (await sha256(bytes) !== digest) throw new Error(`SHA256 mismatch in atlas resource: ${path}`)
}

// All normal resources share successful/in-flight promises. Large annotation chunks
// use a separate small LRU so browsing cannot retain the entire decoded atlas.
function memoized<T>(cache: Map<string, Promise<unknown>>, key: string, loader: () => Promise<T>, limit = Infinity): Promise<T> {
  const existing = cache.get(key)
  if (existing) {
    cache.delete(key)
    cache.set(key, existing)
    return existing as Promise<T>
  }
  const promise = loader().catch(error => {
    if (cache.get(key) === promise) cache.delete(key)
    throw error
  })
  cache.set(key, promise)
  while (cache.size > limit) cache.delete(cache.keys().next().value as string)
  return promise
}

export function createDataClient(base: string, fetcher: typeof fetch = fetch, maxAnnotationChunks = 4) {
  if (!Number.isSafeInteger(maxAnnotationChunks) || maxAnnotationChunks < 1) throw new Error('Invalid annotation cache size')
  const prefix = base.endsWith('/') ? base : base+'/'
  const cache = new Map<string, Promise<unknown>>()
  const chunks = new Map<string, Promise<unknown>>()
  let indexPromise: Promise<TransportIndex | null> | null = null

  function transport(): Promise<TransportIndex | null> {
    if (!indexPromise) {
      indexPromise = (async () => {
        const response = await fetcher(prefix+'data_transport.json', { cache: 'no-cache' })
        // Only a genuinely absent index means an old plain-JSON bundle.
        if (response.status === 404) return null
        if (!response.ok) throw new HttpError('data_transport.json', response.status)
        return validateIndex(await response.json())
      })().catch(error => { indexPromise = null; throw error })
    }
    return indexPromise
  }

  async function loadResource<T>(resource: Resource): Promise<T> {
    const response = await fetcher(prefix+resource.path+'?v='+resource.encoded_sha256.slice(0, 16))
    if (!response.ok) throw new HttpError(resource.path, response.status)
    const received = await response.arrayBuffer()
    let decoded: ArrayBuffer
    if (resource.encoding === 'gzip') {
      const magic = new Uint8Array(received, 0, Math.min(2, received.byteLength))
      if (magic[0] === 0x1f && magic[1] === 0x8b) {
        await verifyBytes(received, resource.encoded_bytes, resource.encoded_sha256, resource.path)
        if (typeof DecompressionStream === 'undefined') throw new Error('This browser cannot open gzip atlas data; use a current browser')
        decoded = await new Response(new Blob([received]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer()
      } else if (response.headers.get('content-encoding')?.toLowerCase().includes('gzip')) {
        // Fetch can transparently decode Content-Encoding. The original encoded
        // bytes are then unavailable; the exact decoded-byte hash remains mandatory.
        decoded = received
      } else {
        throw new Error(`Expected a gzip file in atlas resource: ${resource.path}`)
      }
    } else {
      await verifyBytes(received, resource.encoded_bytes, resource.encoded_sha256, resource.path)
      decoded = received
    }
    await verifyBytes(decoded, resource.decoded_bytes, resource.decoded_sha256, resource.path)
    return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(decoded)) as T
  }

  async function getJson<T>(path: string): Promise<T> {
    const index = await transport()
    if (index) {
      const resource = index.files[path]
      if (!resource) throw new Error(`Undeclared atlas resource: ${path}`)
      return memoized(cache, resource.path+resource.encoded_sha256, () => loadResource<T>(resource))
    }
    return memoized(cache, path, async () => {
      const response = await fetcher(prefix+path)
      if (!response.ok) throw new HttpError(path, response.status)
      return await response.json() as T
    })
  }

  async function getOptionalJson<T>(path: string): Promise<T | null> {
    const index = await transport()
    if (index && !(path in index.files)) return null // Explicitly not exported, e.g. optional cell-type data.
    try {
      return await getJson<T>(path)
    } catch (error) {
      if (!index && error instanceof HttpError && error.status === 404) return null
      throw error
    }
  }

  async function getFeatureAnnotations(layer: number, feature: number): Promise<FeatureAnnotations | null> {
    if (!Number.isSafeInteger(layer) || layer < 0 || !Number.isSafeInteger(feature) || feature < 0) throw new Error('Invalid feature ID')
    const index = await transport()
    if (!index) {
      const legacy = await getJson<Record<string, FeatureAnnotations>>(`layer_${String(layer).padStart(2, '0')}_annotations.json`)
      return legacy[String(feature)] ?? null
    }
    const layout = index.annotations[String(layer)]
    if (!layout) throw new Error(`Missing annotation layer: ${layer}`)
    const bin = Math.floor(feature/layout.chunk_size)
    const chunk = layout.chunks[String(bin)]
    if (!chunk) throw new Error(`Missing annotation chunk for L${layer}:F${feature}`)
    const resource = index.files[chunk.resource]
    const records = await memoized(chunks, resource.path+resource.encoded_sha256, async () => {
      const decoded = await loadResource<Record<string, FeatureAnnotations>>(resource)
      if (!isRecord(decoded) || Object.keys(decoded).length !== chunk.n_features
          || Object.keys(decoded).some(key => !Number.isSafeInteger(Number(key)) || String(Number(key)) !== key
            || Number(key) < chunk.first_feature_id || Number(key) > chunk.last_feature_id)) {
        throw new Error('Annotation chunk feature IDs/counts do not match transport metadata')
      }
      return decoded
    }, maxAnnotationChunks)
    const record = records[String(feature)]
    if (!record || !Array.isArray(record.genes) || !Array.isArray(record.anns)
        || !Array.isArray(record.significant_term_ids) || !Array.isArray(record.network_evidence)) {
      throw new Error(`Missing or invalid annotation feature L${layer}:F${feature}`)
    }
    return record
  }

  return { getJson, getOptionalJson, getFeatureAnnotations }
}
