import { useState, useEffect } from 'react'
import { createDataClient } from '../lib/dataTransport'
import type {
  GlobalSummary, FeatureCompact,
  Module, CausalFeature, PerturbationTarget,
  GeneIndexEntry, OntologyIndexEntry, CrossLayerPair,
  CellTypeData, OntologyIndexCounts, GeneIndexCounts,
} from '../lib/types'

const BASE = import.meta.env.BASE_URL + 'data/'
const client = createDataClient(BASE)

async function fetchJson<T>(path: string): Promise<T> {
  return client.getJson<T>(path)
}

function useAsync<T>(loader: () => Promise<T>, deps: unknown[]): { data: T | null; loading: boolean; error: string | null } {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let cancelled = false
    setData(null)
    setLoading(true)
    setError(null)
    loader()
      .then(d => { if (!cancelled) { setData(d); setLoading(false) } })
      .catch(e => { if (!cancelled) { setError(e.message); setLoading(false) } })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return { data, loading, error }
}

export function useGlobalSummary() {
  return useAsync(() => fetchJson<GlobalSummary>('global_summary.json'), [])
}

export function useLayerFeatures(layer: number) {
  return useAsync(
    () => fetchJson<FeatureCompact[]>(`layer_${String(layer).padStart(2, '0')}_features.json`),
    [layer]
  )
}

export function useLayerPositions(layer: number) {
  return useAsync(
    () => fetchJson<number[][]>(`layer_${String(layer).padStart(2, '0')}_positions.json`),
    [layer]
  )
}

export function useFeatureAnnotations(layer: number, feature: number) {
  return useAsync(
    () => client.getFeatureAnnotations(layer, feature),
    [layer, feature]
  )
}

export function useModules() {
  return useAsync(() => fetchJson<Module[]>('modules.json'), [])
}

export function useCrossLayerGraph() {
  return useAsync(() => fetchJson<Record<string, CrossLayerPair>>('cross_layer_graph.json'), [])
}

export function useCausalPatching() {
  return useAsync(() => fetchJson<{ summary: Record<string, unknown>; features: CausalFeature[] }>('causal_patching.json'), [])
}

export function usePerturbationResponse() {
  return useAsync(() => fetchJson<{ summary: Record<string, unknown>; targets: PerturbationTarget[] }>('perturbation_response.json'), [])
}

export function useSvdComparison() {
  return useAsync(() => fetchJson<{ aggregate: Record<string, unknown>; per_layer: Record<string, Record<string, number>> }>('svd_comparison.json'), [])
}

export function useCrossLayerTracking() {
  return useAsync(() => fetchJson<{ adjacent_persistence: Array<Record<string, unknown>>; long_range_persistence?: Array<Record<string, unknown>> }>('cross_layer_tracking.json'), [])
}

export function useNovelClusters() {
  return useAsync(() => fetchJson<Record<string, { summary: Record<string, unknown>; clusters: Array<Record<string, unknown>> }>>('novel_clusters.json'), [])
}

export function useGeneIndex() {
  return useAsync(() => fetchJson<Record<string, GeneIndexEntry[]>>('gene_index.json'), [])
}

export function useGeneIndexCounts() {
  return useAsync(() => client.getOptionalJson<Record<string, GeneIndexCounts>>('gene_index_counts.json'), [])
}

export function useOntologyIndex() {
  return useAsync(() => fetchJson<Record<string, OntologyIndexEntry[]>>('ontology_index.json'), [])
}

export function useOntologyIndexCounts() {
  // Older atlas bundles have no count file; their displayed index remains usable.
  return useAsync(() => client.getOptionalJson<Record<string, OntologyIndexCounts>>('ontology_index_counts.json'), [])
}

export function useLayerCellTypes(layer: number) {
  return useAsync(
    async () => {
      if (layer < 0) return null
      return await client.getOptionalJson<CellTypeData>(
          `layer_${String(layer).padStart(2, '0')}_celltypes.json`
        )
    },
    [layer]
  )
}
