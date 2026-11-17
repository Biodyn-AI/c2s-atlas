"""Verify an exported atlas bundle before promotion or publication.

This reads files and optionally writes a separate audit report. It never changes
the bundle, performs annotation scoring, or regenerates data.
"""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import time


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def safe_path(root, relative):
    path = Path(relative)
    require(not path.is_absolute() and ".." not in path.parts, f"unsafe resource path {relative}")
    candidate = root / path
    require(not candidate.is_symlink() and candidate.resolve().is_relative_to(root.resolve()),
            f"resource escapes bundle: {relative}")
    return candidate


def decode_resource(root, descriptor):
    encoded = safe_path(root, descriptor["path"]).read_bytes()
    require(len(encoded) == descriptor["encoded_bytes"], f"encoded size: {descriptor['path']}")
    require(sha256(encoded) == descriptor["encoded_sha256"], f"encoded SHA256: {descriptor['path']}")
    require(descriptor["encoding"] in ("gzip", "identity"), "unknown encoding")
    decoded = gzip.decompress(encoded) if descriptor["encoding"] == "gzip" else encoded
    require(len(decoded) == descriptor["decoded_bytes"], f"decoded size: {descriptor['path']}")
    require(sha256(decoded) == descriptor["decoded_sha256"], f"decoded SHA256: {descriptor['path']}")
    return json.loads(decoded.decode("utf-8"))


def verify_files(root):
    root = Path(root).resolve()
    manifest_bytes = (root / "export_manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    transport_bytes = (root / "data_transport.json").read_bytes()
    transport = json.loads(transport_bytes)
    require(manifest["schema_version"] == "c2s-atlas-v4" and manifest["status"] == "complete",
            "complete v4 manifest required")
    require(transport["schema"] == "c2s-atlas-transport-v1" and transport["atlas_schema"] == "c2s-atlas-v4",
            "v4 transport contract required")
    actual_files = {str(path.relative_to(root)): path for path in root.rglob("*") if path.is_file()}
    require(set(actual_files) == set(manifest["output_files"]) | {"export_manifest.json"},
            "bundle file inventory differs from manifest; missing or stale extra files")
    total_bytes, largest_bytes = 0, 0
    for name, path in actual_files.items():
        require(not path.is_symlink(), f"symlink in bundle: {name}")
        payload = path.read_bytes()
        total_bytes += len(payload)
        largest_bytes = max(largest_bytes, len(payload))
        if name != "export_manifest.json":
            expected = manifest["output_files"][name]
            require(len(payload) == expected["bytes"], f"manifest size: {name}")
            require(sha256(payload) == expected["sha256"], f"manifest SHA256: {name}")
        require(len(payload) < 100 * 1024 * 1024, f"file reaches 100MiB: {name}")
    require(total_bytes < 950_000_000, "data bundle reaches 950MB delivery guard")
    physical = [entry["path"] for entry in transport["files"].values()]
    require(len(physical) == len(set(physical)), "duplicate physical transport resources")
    require(set(physical) | {"data_transport.json", "export_manifest.json"} == set(actual_files),
            "unindexed or missing transport resource")
    chunk_descriptors = {}
    seen_by_layer = {}
    for layer, layout in transport["annotations"].items():
        seen_by_layer[layer] = set()
        for chunk, entry in layout["chunks"].items():
            resource_name = entry["resource"]
            require(resource_name not in chunk_descriptors, "reused annotation resource")
            require(entry["first_feature_id"] == int(chunk) * layout["chunk_size"] and
                    entry["last_feature_id"] == (int(chunk) + 1) * layout["chunk_size"] - 1,
                    "annotation chunk must use inclusive fixed ID-bin bounds")
            chunk_descriptors[resource_name] = (layer, entry)
    whole_json_count = 0
    for logical, descriptor in transport["files"].items():
        value = decode_resource(root, descriptor)
        if logical not in chunk_descriptors:
            whole_json_count += 1
            continue
        layer, entry = chunk_descriptors[logical]
        ids = {int(key) for key in value}
        require(len(ids) == len(value) == entry["n_features"], "chunk feature count/IDs mismatch")
        require(all(entry["first_feature_id"] <= feature <= entry["last_feature_id"] for feature in ids),
                "feature lies outside fixed chunk bin")
        require(not ids & seen_by_layer[layer], "duplicate feature ID across chunks")
        seen_by_layer[layer].update(ids)
    for layer, ids in seen_by_layer.items():
        require(len(ids) == transport["annotations"][layer]["n_features"], f"incomplete layer {layer}")
    return {"status": "verified", "bundle": str(root), "file_count": len(actual_files),
        "total_encoded_bytes": total_bytes, "largest_file_bytes": largest_bytes,
        "transport_resource_count": len(transport["files"]), "annotation_chunk_count": len(chunk_descriptors),
        "ordinary_json_resource_count": whole_json_count,
        "annotation_feature_count": sum(len(ids) for ids in seen_by_layer.values()),
        "layers": sorted(int(layer) for layer in seen_by_layer),
        "export_manifest_sha256": sha256(manifest_bytes), "transport_index_sha256": sha256(transport_bytes)}


def verify_reporting_agreement(root, reporting_path):
    root, reporting_path = Path(root), Path(reporting_path)
    summary = json.loads((root / "global_summary.json").read_text())
    manifest = json.loads((root / "export_manifest.json").read_text())
    payload = reporting_path.read_bytes()
    report = json.loads(payload)
    layers = {row["layer"]: row for row in report["layers"]}
    require(set(layers) == {row["layer"] for row in summary["layers"]}, "report layer coverage mismatch")
    checks = 0
    mapping = {"alive": "n_alive", "dead": "catalogue_inactive_count", "annotated": "n_ontology",
        "annotated_any": "n_any", "network_evidence": "n_network_evidence", "n_modules": "n_modules",
        "n_svd_aligned": "n_svd_aligned", "n_novel": "n_novel_non_svd_aligned_alive",
        "variance_explained": "variance_explained_fresh", "heldout_dead_features": "heldout_inactive_count",
        "annotation_rate": "ontology_rate_all_features", "annotation_rate_alive": "ontology_rate_alive",
        "annotation_rate_any": "any_rate_all_features", "annotation_rate_any_alive": "any_rate_alive"}
    for actual in summary["layers"]:
        expected = layers[actual["layer"]]
        for field, report_field in mapping.items():
            require(math.isclose(actual[field], expected[report_field], rel_tol=1e-12, abs_tol=1e-12),
                    f"atlas/report mismatch L{actual['layer']} {field}")
            checks += 1
        for kind in ("freq", "uniform"):
            for union in ("ontology", "any"):
                saved = actual[f"annotation_floors_{union}"][kind]
                floor = expected["floors"][kind]
                for key in ("mean", "values"):
                    report_key = f"{union}_{key}_all_features"
                    actual_values = saved[key] if isinstance(saved[key], list) else [saved[key]]
                    expected_values = floor[report_key] if isinstance(floor[report_key], list) else [floor[report_key]]
                    require(len(actual_values) == len(expected_values) and all(
                        math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12) for a, b in zip(actual_values, expected_values)),
                        f"atlas/report floor mismatch L{actual['layer']} {kind} {union} {key}")
                    checks += len(actual_values)
        require(actual["ontology_counts"] == {k: v for k, v in expected["n_by_source"].items()
                if k in {"GO_BP", "KEGG", "Reactome", "TRRUST"}}, "ontology source counts mismatch")
        require(actual["network_counts"] == {k: v for k, v in expected["n_by_source"].items()
                if k in {"STRING", "TRRUST_pairs", "TF_membership"}}, "network source counts mismatch")
        checks += 2
    for field, report_field in {"total_features": "n_features", "total_alive": "n_alive",
        "total_annotated": "n_ontology", "total_annotated_any": "n_any",
        "total_network_evidence": "n_network_evidence", "total_modules": "n_modules",
        "total_novel": "n_novel_non_svd_aligned_alive"}.items():
        require(summary[field] == report["pooled_totals"][report_field], f"pooled atlas/report mismatch: {field}")
        checks += 1
    input_checks = 0
    for name, descriptor in report["input_files"].items():
        basename = Path(name).name
        if basename in manifest["inputs_sha256"]:
            expected_hash = descriptor["sha256"] if isinstance(descriptor, dict) else descriptor
            require(manifest["inputs_sha256"][basename] == expected_hash, f"atlas/report input hash mismatch: {basename}")
            input_checks += 1
    return {"status": "verified", "reporting_sha256": sha256(payload), "layer_value_checks": checks,
            "common_input_hash_checks": input_checks,
            "global_counts": {key: summary[key] for key in ("total_features", "total_alive", "total_annotated",
                "total_annotated_any", "total_network_evidence", "total_modules", "n_layers")}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--reporting", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    started = time.monotonic()
    result = verify_files(args.data)
    if args.reporting:
        result["reporting_agreement"] = verify_reporting_agreement(args.data, args.reporting)
    result.update(created_utc=datetime.now(timezone.utc).isoformat(), runtime_seconds=time.monotonic() - started,
                  verifier_sha256=sha256(Path(__file__).read_bytes()))
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.out:
        require(not args.out.resolve().is_relative_to(args.data.resolve()), "audit output must be outside bundle")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
