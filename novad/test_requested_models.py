import hashlib
import json
import os
import struct
import tempfile
import unittest
import urllib.error
from pathlib import Path

import catalog
import registry_probe
import requested_models


def _gguf(arch="testarch", tensors=((4096, 1000), (4096, 4096)), file_type=15, vocab=50):
    """A valid GGUF v3 header blob for the fake registry (copied from tests/test_catalog_probe.py)."""
    out = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", len(tensors)) + struct.pack("<Q", 0)
    for dim in tensors:
        name = f"layer-{dim[0]}x{dim[1]}".encode()
        out += struct.pack("<Q", len(name)) + name
        out += struct.pack("<I", len(dim))
        for d in dim:
            out += struct.pack("<Q", d)
        out += struct.pack("<I", 1) + struct.pack("<Q", 0)
    return out


class FakeRegistry:
    """Serves a manifest plus the config/template/license/model blobs the probe reads (no network)."""

    def __init__(self, header, template=".Tools {{ .Tools }}", projector=False, config=None, with_template=True):
        self.requests = []
        self.blobs = {}
        header_sha = hashlib.sha256(header).hexdigest()
        template_sha = hashlib.sha256(template.encode()).hexdigest()
        config_bytes = json.dumps(
            {"parser": "qwen3.5", "file_type": "Q4_K_M"} if config is None else config).encode()
        config_sha = hashlib.sha256(config_bytes).hexdigest()
        license_sha = hashlib.sha256(b"MIT").hexdigest()
        self.manifest = {
            "config": {"digest": f"sha256:{config_sha}", "size": len(config_bytes)},
            "layers": [
                {"mediaType": "application/vnd.ollama.parameter.model",
                 "digest": f"sha256:{header_sha}", "size": 4096 * 4096 * 2},
                {"mediaType": "application/vnd.ollama.parameter.license",
                 "digest": f"sha256:{license_sha}", "size": 3},
            ],
        }
        self.blobs[header_sha] = header
        self.blobs[config_sha] = config_bytes
        self.blobs[license_sha] = b"MIT"
        if with_template:
            self.manifest["layers"].append(
                {"mediaType": "application/vnd.ollama.parameter.template",
                 "digest": f"sha256:{template_sha}", "size": len(template)})
            self.blobs[template_sha] = template.encode()
        if projector:
            project_bytes = b"\x00\x01"
            project_sha = hashlib.sha256(project_bytes).hexdigest()
            self.manifest["layers"].append(
                {"mediaType": "application/vnd.ollama.parameter.projector",
                 "digest": f"sha256:{project_sha}", "size": len(project_bytes)})
            self.blobs[project_sha] = project_bytes

    def __call__(self, url, headers=None, limit=1 << 20):
        self.requests.append((url, headers, limit))
        if "/manifests/" in url:
            return json.dumps(self.manifest).encode()
        digest = url.rsplit(":", 1)[-1]
        data = self.blobs.get(digest)
        if data is None:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        if headers and "Range" in headers:
            start = int(headers["Range"].split("=")[1].split("-")[0])
            return data[start:start + limit]
        return data[:limit]


class ParseReferenceTests(unittest.TestCase):
    def test_accepts(self):
        self.assertEqual(requested_models.parse_reference("gemma4:12b"), "gemma4:12b")
        self.assertEqual(requested_models.parse_reference("gemma4"), "gemma4:latest")
        self.assertEqual(requested_models.parse_reference("  llama3.2:1b  "), "llama3.2:1b")
        self.assertEqual(requested_models.parse_reference("https://ollama.com/library/gemma4:12b"), "gemma4:12b")
        self.assertEqual(requested_models.parse_reference("ollama.com/library/qwen3:8b"), "qwen3:8b")
        self.assertEqual(requested_models.parse_reference("registry.ollama.ai/library/phi4-mini"), "phi4-mini:latest")

    def test_rejects(self):
        for bad in ("", "Gemma4:12b", "someone/model:1b",
                    "https://ollama.com/library/someone/model", "https://hf.co/x/y",
                    "https://huggingface.co/x", "gemma4:12b?x=1", "gemma4:12b#a",
                    "gemma 4", "gpt-oss:120b-cloud", "https://evil.example/library/gemma4:12b",
                    "a" * 201):
            self.assertIsNone(requested_models.parse_reference(bad), bad)


class RequestModelTests(unittest.TestCase):
    def test_adds_and_stores_with_permissions_and_validates(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)
            fake = FakeRegistry(header=_gguf(), template=".Tools {{ .Tools }}", projector=True)
            result = requested_models.request_model("gemma4:12b", fetch=fake, store=store)
            self.assertEqual(result["status"], "added")
            self.assertTrue(result["catalog_id"].startswith("requested-"))
            self.assertEqual(result["name"], "gemma4:12b")
            models_file = store / "models.json"
            fit_file = store / "acquisition_fit.json"
            self.assertTrue(models_file.exists() and fit_file.exists())
            self.assertEqual(os.stat(store).st_mode & 0o777, 0o700)
            self.assertEqual(os.stat(models_file).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(fit_file).st_mode & 0o777, 0o600)
            models_doc = json.loads(models_file.read_text())
            fit_doc = json.loads(fit_file.read_text())
            record = models_doc["models"][0]
            self.assertTrue(record["catalog_id"].startswith("requested-"))
            self.assertEqual(record["acquisition"]["source"], "ollama_registry_requested")
            self.assertEqual(record["metadata"]["provenance"]["method"], "registry_probe+user_requested")
            self.assertEqual(fit_doc["records"][0]["catalog_id"], record["catalog_id"])
            loaded = catalog.validate_catalog(models_doc)
            self.assertEqual(loaded.status, "ready")

    def test_invalid_reference_never_fetches_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)
            fake = FakeRegistry(header=_gguf())
            result = requested_models.request_model("someone/model:1b", fetch=fake, store=store)
            self.assertEqual(result, {"status": "invalid_reference"})
            self.assertEqual(fake.requests, [])
            self.assertFalse((store / "models.json").exists())
            self.assertFalse((store / "acquisition_fit.json").exists())

    def test_not_found(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)

            def fetch(url, headers=None, limit=1 << 20):
                raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

            result = requested_models.request_model("gemma4:12b", fetch=fetch, store=store)
            self.assertEqual(result["status"], "not_found")
            self.assertEqual(result["name"], "gemma4:12b")
            self.assertFalse((store / "models.json").exists())

    def test_registry_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)

            def fetch(url, headers=None, limit=1 << 20):
                raise urllib.error.URLError("connection timeout")

            result = requested_models.request_model("gemma4:12b", fetch=fetch, store=store)
            self.assertEqual(result["status"], "registry_unavailable")
            self.assertEqual(result["name"], "gemma4:12b")
            self.assertFalse((store / "models.json").exists())

    def test_unsupported_model(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)
            fake = FakeRegistry(header=b"NOTGGUF" + b"\x00" * 32)
            result = requested_models.request_model("gemma4:12b", fetch=fake, store=store)
            self.assertEqual(result["status"], "unsupported_model")
            self.assertEqual(result["name"], "gemma4:12b")
            self.assertFalse((store / "models.json").exists())

    def test_same_name_deduplicates_to_one_record(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)
            first = requested_models.request_model("gemma4:12b", fetch=FakeRegistry(header=_gguf()), store=store)
            second = requested_models.request_model("gemma4:12b", fetch=FakeRegistry(header=_gguf()), store=store)
            self.assertEqual(first["catalog_id"], second["catalog_id"])
            doc = json.loads((store / "models.json").read_text())
            self.assertEqual(len(doc["models"]), 1)

    def test_keeps_at_most_twenty_most_recent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)
            for i in range(21):
                requested_models.request_model(f"model{i:02d}", fetch=FakeRegistry(header=_gguf()), store=store)
            doc = json.loads((store / "models.json").read_text())
            names = [m["source"]["upstream_id"] for m in doc["models"]]
            self.assertEqual(len(names), 20)
            self.assertIn("model20:latest", names)
            self.assertNotIn("model00:latest", names)


if __name__ == "__main__":
    unittest.main()
