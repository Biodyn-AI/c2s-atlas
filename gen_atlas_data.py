#!/usr/bin/env python
"""Generate C2S-Scale-2B SAE atlas JSON for the geneformer-atlas React template.
Reads runs/2b_atlas/*, writes atlas/public/data/*.json. Follows the build spec.
Usable layers = catalog INTERSECT annotations, minus corrupt L08."""
import json, os, glob
from collections import Counter, defaultdict
import numpy as np
import torch
import umap

BASE = "/Volumes/Crucial X6/MacBook/Code/biomi_automation/projects/c2s-scale"
RUN = f"{BASE}/runs/2b_atlas"
OUT = f"{BASE}/atlas/public/data"
D_SAE = 9216
PERT_LAYER = 16


def _valid_json(p):
    try:
        with open(p) as _f:
            json.load(_f)
        return True
    except Exception:
        return False


# Include only layers whose catalog AND annotations both parse (skips corrupt files:
# L08 catalog, L09 annotations, and anything else that's truncated/malformed).
USABLE = [L for L in range(26)
          if _valid_json(f"{RUN}/feature_catalog_L{L:02d}.json")
          and _valid_json(f"{RUN}/feature_annotations_L{L:02d}.json")]
MODULE_LAYERS = [L for L in USABLE if _valid_json(f"{RUN}/coactivation_modules_L{L:02d}.json")]
print(f"USABLE layers ({len(USABLE)}): {USABLE}", flush=True)
print(f"MODULE layers: {MODULE_LAYERS}", flush=True)

os.makedirs(OUT, exist_ok=True)
for f in glob.glob(f"{OUT}/*.json"):
    os.remove(f)  # clear template's geneformer data


def loadj(p): return json.load(open(p))
def cat(L): return loadj(f"{RUN}/feature_catalog_L{L:02d}.json")
def ann(L): return loadj(f"{RUN}/feature_annotations_L{L:02d}.json")
def train(L): return loadj(f"{RUN}/sae/train_L{L:02d}.json")
def modf(L): return loadj(f"{RUN}/coactivation_modules_L{L:02d}.json")
def best_ann(alist):
    # only enrichment entries carry p_adjusted; edge entries (STRING/TRRUST) fall back to p=1.0
    return min(alist, key=lambda a: a.get("p_adjusted", 1.0)) if alist else None


gene_index, ontology_index = {}, {}

for L in USABLE:
    c = cat(L); a = ann(L)["feature_annotations"]
    modassign = modf(L)["module_assignment"] if L in MODULE_LAYERS else {}
    feats = c["features"]
    cat_by_idx = {f["feature_idx"]: f for f in feats}

    out_feats = []
    for f in feats:
        fid = f["feature_idx"]; alist = a.get(str(fid), [])
        ba = best_ann(alist); lb = ba["term"] if ba else ""; to = ba["ontology"] if ba else "none"
        m = modassign.get(str(fid), -1)
        tg = [{"n": g["gene_name"], "a": round(g["mean_activation"], 4)} for g in f["top_genes"][:5]]
        out_feats.append({"i": fid, "d": int(f["is_dead"]), "f": round(f["activation_freq"], 6),
                          "ma": round(f["mean_activation"], 4), "fc": f["fire_count"],
                          "na": len(alist), "m": m, "sv": int(f["is_svd_aligned"]),
                          "lb": lb, "to": to, "tg": tg})
        for r, g in enumerate(f["top_genes"][:10]):
            gene_index.setdefault(g["gene_name"].upper(), []).append({"l": L, "i": fid, "r": r, "lb": lb, "m": m})
    json.dump(out_feats, open(f"{OUT}/layer_{L:02d}_features.json", "w"))

    out_ann = {}
    for fid_str, alist in a.items():
        if not alist: continue
        fid = int(fid_str); cf = cat_by_idx.get(fid)
        genes = [{"n": g["gene_name"], "a": round(g["mean_activation"], 4), "fc": g["fire_count"]}
                 for g in (cf["top_genes"][:20] if cf else [])]
        anns = [{"o": x["ontology"], "t": x["term"], "p": x.get("p_adjusted", 1.0),
                 "or": round(x.get("odds_ratio", x.get("density", 0.0)), 3),
                 "n": x.get("n_overlap", x.get("n_edges", 0)), "g": x.get("overlap_genes", [])} for x in alist]
        out_ann[fid_str] = {"genes": genes, "anns": anns}
        for x in alist:
            ontology_index.setdefault(x["term"], []).append(
                {"l": L, "i": fid, "p": x.get("p_adjusted", 1.0), "o": x["ontology"]})
    json.dump(out_ann, open(f"{OUT}/layer_{L:02d}_annotations.json", "w"))

    ck = torch.load(f"{RUN}/sae/sae_L{L:02d}.pt", map_location="cpu", weights_only=False)
    W = ck["model_state_dict"]["W_dec.weight"].float().numpy()
    dirs = W if W.shape[0] == D_SAE else W.T          # -> (d_sae, d_model)
    coords = umap.UMAP(n_neighbors=15, min_dist=0.1, metric="cosine",
                       random_state=42, n_components=2).fit_transform(dirs)
    json.dump([[round(float(x), 5), round(float(y), 5)] for x, y in coords],
              open(f"{OUT}/layer_{L:02d}_positions.json", "w"))
    print(f"L{L:02d}: {len(out_feats)} feats, {len(out_ann)} annotated, positions {coords.shape}", flush=True)

json.dump(gene_index, open(f"{OUT}/gene_index.json", "w"))
json.dump(ontology_index, open(f"{OUT}/ontology_index.json", "w"))


def remap_onto(oc):
    return {"GO_BP": oc.get("GO_BP", 0), "KEGG": oc.get("KEGG", 0), "Reactome": oc.get("Reactome", 0),
            "STRING": oc.get("STRING_edges", 0), "TRRUST": oc.get("TRRUST_TF_enrichment", 0) + oc.get("TRRUST_edges", 0)}


layers_sum = []
for L in USABLE:
    cs = cat(L)["summary"]; asum = ann(L)["summary"]; tr = train(L)
    nmod = modf(L)["summary"]["n_modules"] if L in MODULE_LAYERS else 0
    layers_sum.append({"layer": L, "alive": cs["n_alive"], "dead": cs["n_dead"],
                       "annotated": asum["n_annotated"], "annotation_rate": round(asum["annotation_rate"], 4),
                       "n_modules": nmod, "n_svd_aligned": cs["n_svd_aligned"], "n_novel": cs["n_novel"],
                       "ontology_counts": remap_onto(asum["ontology_counts"]),
                       "mean_feature_cosine": round(cs["mean_feature_cosine"], 4),
                       "variance_explained": round(tr["variance_explained"], 4)})
json.dump({"total_features": D_SAE * len(USABLE),
           "total_alive": sum(l["alive"] for l in layers_sum),
           "total_annotated": sum(l["annotated"] for l in layers_sum),
           "total_modules": sum(l["n_modules"] for l in layers_sum),
           "total_novel": sum(l["n_novel"] for l in layers_sum),
           "n_layers": len(USABLE), "n_features_per_layer": D_SAE, "layers": layers_sum},
          open(f"{OUT}/global_summary.json", "w"))

modules_out = []
for L in MODULE_LAYERS:
    a = ann(L)["feature_annotations"]
    for mod in modf(L)["modules"]:
        cnt = Counter()
        for fid in mod["features"]:
            for x in a.get(str(fid), []):
                cnt[x["term"]] += 1
        modules_out.append({"layer": L, "id": mod["module_id"], "n": mod["n_features"],
                            "features": mod["features"][:100],
                            "top_anns": [{"t": t, "c": c} for t, c in cnt.most_common(5)]})
json.dump(modules_out, open(f"{OUT}/modules.json", "w"))

per_layer = {}
tot_al = tot_nov = 0
for L in USABLE:
    cs = cat(L)["summary"]; tr = train(L)
    sv = cs["svd_info"]["variance_explained_top50"]; se = tr["variance_explained"]
    per_layer[str(L)] = {"svd_variance": round(sv, 4), "sae_variance": round(se, 4),
                         "gain": round(se / max(sv, 1e-9), 3), "n_aligned": cs["n_svd_aligned"], "n_novel": cs["n_novel"]}
    tot_al += cs["n_svd_aligned"]; tot_nov += cs["n_novel"]
json.dump({"aggregate": {"total_aligned": tot_al, "total_novel": tot_nov,
                         "pct_novel": round(tot_nov / max(tot_al + tot_nov, 1), 4)},
           "per_layer": per_layer}, open(f"{OUT}/svd_comparison.json", "w"))

json.dump(loadj(f"{RUN}/cross_layer_tracking.json"), open(f"{OUT}/cross_layer_tracking.json", "w"))

p8 = loadj(f"{RUN}/phase8_L{PERT_LAYER:02d}.json"); ps = p8["summary"]
c16 = {f["feature_idx"]: f for f in cat(PERT_LAYER)["features"]}; a16 = ann(PERT_LAYER)["feature_annotations"]
def flabel(fid):
    ba = best_ann(a16.get(str(fid), []))
    if ba: return ba["term"]
    return c16[fid]["top_genes"][0]["gene_name"] if fid in c16 and c16[fid]["top_genes"] else ""
targets = [{"gene": t["target"], "tf": bool(t["is_trrust_tf"]), "nk": t["n_known_targets"],
            "nr": t["n_responding"], "ns": t["n_specific"],
            "top": [{"i": x["feature_idx"], "es": round(x["effect"], 4), "lb": flabel(x["feature_idx"])} for x in t["top_changed"]]}
           for t in p8["targets"]]
json.dump({"summary": {"n_targets_processed": ps["n_targets"], "n_trrust_tfs": ps["n_trrust_tfs"],
                       "n_tfs_with_specific_response": ps["n_tfs_with_specific_response"],
                       "frac_tfs_specific": round(ps["tf_specificity_rate"], 4),
                       "mean_responding_features": round(ps["mean_responding"], 2),
                       "mean_specific_features": round(float(np.mean([t["n_specific"] for t in p8["targets"]])), 2) if p8["targets"] else 0,
                       "n_alive_features": D_SAE}, "targets": targets},
          open(f"{OUT}/perturbation_response.json", "w"))

json.dump({"summary": {}, "features": []}, open(f"{OUT}/causal_patching.json", "w"))  # honest stub

cg = loadj(f"{RUN}/circuit_graph.json")
pairs = defaultdict(lambda: defaultdict(list))
for e in cg["edges"]:
    pairs[(e["source_layer"], e["target_layer"])][e["source_feature"]].append(
        (e["target_feature"], abs(e["cohens_d"]), e.get("source_label", "")))
clg = {}
for (sl, tl) in [(0, 7), (7, 16), (0, 16)]:
    if (sl, tl) not in pairs: continue
    srcmap = pairs[(sl, tl)]; deps = []
    for sf, tlist in srcmap.items():
        tlist.sort(key=lambda x: -x[1])
        deps.append({"a": sf, "deps": [{"b": tf, "pmi": round(d, 3), "lb": lb} for tf, d, lb in tlist[:5]]})
    deps.sort(key=lambda x: -max((dd["pmi"] for dd in x["deps"]), default=0))
    clg[f"L{sl:02d}_L{tl:02d}"] = {"summary": {"n_sources": len(srcmap),
                                               "n_edges": sum(len(v) for v in srcmap.values())}, "deps": deps[:50]}
json.dump(clg, open(f"{OUT}/cross_layer_graph.json", "w"))

print("DONE — atlas data written to", OUT)
