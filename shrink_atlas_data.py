#!/usr/bin/env python
"""Shrink the large atlas index/annotation files to sane sizes for GitHub Pages (pitfall #1).
Regenerates gene_index.json, ontology_index.json, and per-layer annotations from source with caps.
Positions/features/other singletons are untouched (no UMAP re-run)."""
import json, glob, os
from collections import defaultdict

BASE = "/Volumes/Crucial X6/MacBook/Code/biomi_automation/projects/c2s-scale"
RUN = f"{BASE}/runs/2b_atlas"
OUT = f"{BASE}/atlas/public/data"
GENE_TOPK, GENE_CAP, ONTO_CAP, ANN_CAP, G_CAP = 5, 30, 100, 25, 8
MODULE_LAYERS = [0, 1, 2, 3, 4, 5, 6, 7]

USABLE = sorted(int(os.path.basename(f).split("_")[1]) for f in glob.glob(f"{OUT}/layer_*_features.json"))
print("USABLE:", USABLE)

def cat(L): return json.load(open(f"{RUN}/feature_catalog_L{L:02d}.json"))
def ann(L): return json.load(open(f"{RUN}/feature_annotations_L{L:02d}.json"))
def modf(L): return json.load(open(f"{RUN}/coactivation_modules_L{L:02d}.json"))
def best_ann(al): return min(al, key=lambda a: a.get("p_adjusted", 1.0)) if al else None

gene_index, ontology_index = defaultdict(list), defaultdict(list)
for L in USABLE:
    c = cat(L); a = ann(L)["feature_annotations"]
    cbi = {f["feature_idx"]: f for f in c["features"]}
    modassign = modf(L)["module_assignment"] if L in MODULE_LAYERS else {}
    out_ann = {}
    for fid_str, al in a.items():
        if not al: continue
        fid = int(fid_str); cf = cbi.get(fid)
        al = sorted(al, key=lambda x: x.get("p_adjusted", 1.0))[:ANN_CAP]
        genes = [{"n": g["gene_name"], "a": round(g["mean_activation"], 4), "fc": g["fire_count"]}
                 for g in (cf["top_genes"][:20] if cf else [])]
        anns = [{"o": x["ontology"], "t": x["term"], "p": x.get("p_adjusted", 1.0),
                 "or": round(x.get("odds_ratio", x.get("density", 0.0)), 3),
                 "n": x.get("n_overlap", x.get("n_edges", 0)),
                 "g": x.get("overlap_genes", [])[:G_CAP]} for x in al]
        out_ann[fid_str] = {"genes": genes, "anns": anns}
        for x in al:
            ontology_index[x["term"]].append({"l": L, "i": fid, "p": x.get("p_adjusted", 1.0), "o": x["ontology"]})
    json.dump(out_ann, open(f"{OUT}/layer_{L:02d}_annotations.json", "w"))
    for f in c["features"]:
        fid = f["feature_idx"]; ba = best_ann(a.get(str(fid), [])); lb = ba["term"] if ba else ""
        m = modassign.get(str(fid), -1)
        for r, g in enumerate(f["top_genes"][:GENE_TOPK]):
            gene_index[g["gene_name"].upper()].append({"l": L, "i": fid, "r": r, "lb": lb, "m": m})

gi = {g: sorted(v, key=lambda e: (e["r"], e["l"]))[:GENE_CAP] for g, v in gene_index.items()}
oi = {t: sorted(v, key=lambda e: e["p"])[:ONTO_CAP] for t, v in ontology_index.items()}
json.dump(gi, open(f"{OUT}/gene_index.json", "w"))
json.dump(oi, open(f"{OUT}/ontology_index.json", "w"))
print(f"gene_index: {len(gi)} genes | ontology_index: {len(oi)} terms | DONE")
