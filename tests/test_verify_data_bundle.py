"""Test the release-bundle guard using tiny files, never public/data."""
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("verify_data_bundle", Path(__file__).parents[1] / "scripts/verify_data_bundle.py")
guard = importlib.util.module_from_spec(SPEC)
if SPEC.loader:
    SPEC.loader.exec_module(guard)


def write_fixture(root):
    decoded = b'{"message":"complete"}'
    encoded = gzip.compress(decoded, mtime=0)
    descriptor = {"path": "resource.json.gz", "encoding": "gzip", "encoded_bytes": len(encoded),
        "decoded_bytes": len(decoded), "encoded_sha256": hashlib.sha256(encoded).hexdigest(),
        "decoded_sha256": hashlib.sha256(decoded).hexdigest()}
    (root / descriptor["path"]).write_bytes(encoded)
    transport = {"schema": "c2s-atlas-transport-v1", "atlas_schema": "c2s-atlas-v4",
        "files": {"resource.json": descriptor}, "annotations": {}}
    (root / "data_transport.json").write_text(json.dumps(transport))
    inventory = {}
    for path in root.iterdir():
        payload = path.read_bytes()
        inventory[path.name] = {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    (root / "export_manifest.json").write_text(json.dumps({"schema_version": "c2s-atlas-v4",
        "status": "complete", "output_files": inventory}))
    return descriptor


class DataBundleGuardTests(unittest.TestCase):
    def test_verifies_every_encoded_and_decoded_resource(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(root)
            result = guard.verify_files(root)
            self.assertEqual(result["file_count"], 3)
            self.assertEqual(result["transport_resource_count"], 1)
            self.assertEqual(result["status"], "verified")

    def test_rejects_missing_extra_or_corrupted_files(self):
        for kind in ("missing", "extra", "corrupt"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                write_fixture(root)
                if kind == "missing":
                    (root / "resource.json.gz").unlink()
                elif kind == "extra":
                    (root / "stale_annotations.json").write_text("{}")
                else:
                    (root / "resource.json.gz").write_bytes(b"corrupted")
                with self.assertRaises(ValueError):
                    guard.verify_files(root)

    def test_rejects_wrong_decoded_hash_even_when_manifest_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(root)
            path = root / "data_transport.json"
            transport = json.loads(path.read_text())
            transport["files"]["resource.json"]["decoded_sha256"] = "0" * 64
            path.write_text(json.dumps(transport))
            manifest = json.loads((root / "export_manifest.json").read_text())
            payload = path.read_bytes()
            manifest["output_files"][path.name] = {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
            (root / "export_manifest.json").write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):
                guard.verify_files(root)


if __name__ == "__main__":
    unittest.main()
