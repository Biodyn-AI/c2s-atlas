# C2S feature atlas

Build the browser with `npm install`, `npm test` and `npm run build`. The checked-in `public/data` bundle
is a published artifact; changing analysis code does not regenerate or validate that bundle.

Generate revision data with the canonical `revision/export_revision_atlas.py` in
[Biodyn-AI/c2s-mechinterp](https://github.com/Biodyn-AI/c2s-mechinterp), using real saved
catalogues, complete scorer-v3 annotations and fresh held-out evaluations:

```bash
python revision/export_revision_atlas.py --run "$RUN" \
  --annotations "$WORK/results/annotations/2b" --evaluation-dir "$WORK/results" \
  --positions-dir "$ATLAS_POSITIONS" --compute-missing-positions \
  --phase8-statistics "$WORK/results/phase8_k562/statistics" \
  --out "$WORK/results/atlas_staging"
```

Run expensive position generation on the compute machine. Existing positions must come from
the same dictionaries. Export requires a new empty staging directory outside `public/data`;
review the saved numeric audit and export manifest before copying real data into the browser.
The export records input/output hashes, requires `2b_eval_LXX.json` fresh reconstruction metrics,
and preserves catalogue-wide inactivity independently of held-out inactivity.

The v3 annotation scorer separates ontology/regulon terms from network-pair and TF-membership evidence.
Observed annotation rates and their corresponding random-gene floors use all dictionary features
as denominator; explicit alive-feature rates are also saved. All significant term details are
retained, while browser indices are capped with complete counts stored separately. Circuit data
distinguishes stored edges (up to 50 per source/target-layer) from browser-retained edges (up to 5).
The table shows one strongest retained edge per source, with at most 20 rows.

Gene search indexes only the top five driving genes per feature, followed by the configured
per-gene match cap. `gene_index_counts.json` records complete counts within that top-five scope;
it does not count all memberships in the full per-feature gene lists. The browser states this
coverage limit and marks truncated match lists.

Transport schema v4 keeps complete feature annotations in fixed 256-feature-ID gzip chunks.
The feature page requests one chunk and retains at most four decoded chunks. Every term, complete
significant-term ID, network record and saved gene entry is preserved. `data_transport.json` maps
logical resources to content-hashed gzip files or small plain JSON files and records encoded/decoded
byte counts and SHA256 values. Other JSON files of at least 1 MiB are also gzip-compressed.
The browser verifies raw decoded UTF-8 bytes before parsing; it also verifies encoded gzip bytes
when Fetch exposes them. If a server automatically applies HTTP Content-Encoding decoding, the
decoded-byte hash remains mandatory. Native `DecompressionStream` and a secure origin (HTTPS or
localhost) are required. Integrity and decoding failures are displayed as errors.

The legacy plain-JSON path is used only when the transport index returns HTTP404. A malformed index,
missing declared file or checksum failure cannot silently become an unannotated feature. Optional
complete-count files may be absent in old bundles; errors in declared v4 count files remain visible.
The exporter never writes layer-sized annotation JSON files. Its defaults enforce a strict
950,000,000-byte data budget (including nested files and the final manifest) and files below 100 MiB.
These checks run before a complete export manifest is written. Use a new empty staging directory;
never copy a partial or failed export into `public/data`. Existing same-dictionary positions can be
reused with `--positions-dir`.

Verify a downloaded bundle before promotion with
`python3 scripts/verify_data_bundle.py --data /path/to/atlas_staging`.
The guard checks the exact file inventory, every encoded and decoded hash and byte count,
fixed feature-ID chunk coverage, and delivery-size limits. Add
`--reporting /path/to/atlas_reporting.json --out /path/to/audit.json` to check per-layer
scientific counts, reconstruction metrics, floors and common source hashes against the saved
numeric report. The audit output must stay outside the published data directory.
Its small-fixture tests run with `python3 -m unittest discover -s tests -p test_verify_data_bundle.py`.
For an HTTP and native gzip smoke check against a real bundle, run
`node tests/smoke_real_bundle.mjs --data /absolute/path/to/public/data`.
This exercises the production loader across every exported layer and chunk boundaries;
it does not replace browser visual inspection.

`gen_atlas_data.py` and `shrink_atlas_data.py` are archival pre-revision tools. They stop by
default before any data access. Their explicit `--allow-legacy-scorer` opt-in retains the original
hardcoded input/output paths and legacy schema; it must not be used to produce revision data.
Run the guard tests safely with `python -m unittest test_legacy_generation_guards.py`; the tests
execute only the argument-checking preamble and never execute either archival generator body.
