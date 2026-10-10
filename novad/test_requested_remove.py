"""QWEN-10: remove a model the user added (requested-models store + DELETE endpoint).

No network: the store is seeded through ``requested_models.request_model`` with a
fake registry fetch (the same ``FakeRegistry`` shape as the QWEN-08/09 tests), in
a temporary ``XDG_STATE_HOME``. Endpoint tests patch ``remove_requested``.
"""
import contextlib
import hashlib
import io
import json
import os
import struct
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import requested_models
import server

GIB = 1024 ** 3


def _gguf(arch="testarch", tensors=((4096, 1000), (4096, 4096)), file_type=15, vocab=50):
    """A valid GGUF v3 header blob for the fake registry (same shape as the QWEN-08 fixture)."""
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
    """Serves a manifest plus the config/template/license/model blobs the probe reads (no network).

    Copied from test_requested_integration.py (QWEN-09) and test_requested_models.py (QWEN-08).
    """

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


class SeedStoreMixin:
    """Seed a throwaway requested-models store with one or more records (no network)."""

    def seed_store(self, name="granite3.3:2b", second=None):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state"
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.state)}):
            result = requested_models.request_model(
                name, fetch=FakeRegistry(header=_gguf(), template=".Tools {{ .Tools }}", projector=True))
            if result["status"] != "added":
                raise AssertionError(f"store seed failed: {result}")
            if second is not None:
                result2 = requested_models.request_model(
                    second, fetch=FakeRegistry(header=_gguf(), template=".Tools {{ .Tools }}", projector=True))
                if result2["status"] != "added":
                    raise AssertionError(f"store seed failed: {result2}")
        self.store = self.state / "nova" / "requested"

    def tearDown(self):
        self.tmp.cleanup()


class RemoveStoreTests(SeedStoreMixin, unittest.TestCase):
    def test_removed_deletes_from_both_files_and_keeps_others(self):
        self.seed_store("granite3.3:2b", second="gemma4:12b")
        models_before = (self.store / "models.json").read_bytes()
        fit_before = (self.store / "acquisition_fit.json").read_bytes()
        ids_before = [m["catalog_id"] for m in json.loads(models_before)["models"]]
        self.assertEqual(len(ids_before), 2)

        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.state)}):
            result = requested_models.remove_requested(ids_before[0], store=self.store)

        self.assertEqual(result["status"], "removed")
        self.assertEqual(result["catalog_id"], ids_before[0])

        models_after = json.loads((self.store / "models.json").read_text())
        fit_after = json.loads((self.store / "acquisition_fit.json").read_text())
        self.assertNotIn(ids_before[0], [m["catalog_id"] for m in models_after["models"]])
        self.assertIn(ids_before[1], [m["catalog_id"] for m in models_after["models"]])
        self.assertNotIn(ids_before[0], [r["catalog_id"] for r in fit_after["records"]])
        self.assertIn(ids_before[1], [r["catalog_id"] for r in fit_after["records"]])

        self.assertEqual((self.store / "models.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.store / "acquisition_fit.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.stat().st_mode & 0o777, 0o700)

    def test_not_found_leaves_files_unchanged(self):
        self.seed_store("granite3.3:2b")
        models_before = (self.store / "models.json").read_bytes()
        fit_before = (self.store / "acquisition_fit.json").read_bytes()

        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.state)}):
            result = requested_models.remove_requested("requested-unknown-id", store=self.store)

        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["catalog_id"], "requested-unknown-id")
        self.assertEqual((self.store / "models.json").read_bytes(), models_before)
        self.assertEqual((self.store / "acquisition_fit.json").read_bytes(), fit_before)

    def test_invalid_ids_rejected_without_creating_store(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state"
        self.store = self.state / "nova" / "requested"
        self.assertFalse(self.store.exists())
        invalid = ["", "qwen3-8b-q4-k-m", "requested-../x", "requested-A", None, 42]
        for catalog_id in invalid:
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.state)}):
                result = requested_models.remove_requested(catalog_id, store=self.store)
            self.assertEqual(result["status"], "invalid_id", catalog_id)
            self.assertEqual(result, {"status": "invalid_id"})
        self.assertFalse(self.store.exists(), "invalid id must not create the store")


class Wire:
    def __init__(self, request):
        self.request = io.BytesIO(request)
        self.response = bytearray()

    def makefile(self, *args, **kwargs):
        return self.request

    def sendall(self, data):
        self.response.extend(data)


class DeleteEndpointTests(unittest.TestCase):
    def delete(self, path, extra_headers=b""):
        head = f"DELETE {path} HTTP/1.0\r\nHost: localhost\r\n".encode() + extra_headers + b"\r\n"
        wire = Wire(head)
        server.Handler(wire, ("127.0.0.1", 1), None)
        raw = bytes(wire.response)
        status_line, rest = raw.split(b"\r\n", 1)
        headers, payload = rest.split(b"\r\n\r\n", 1)
        return int(status_line.split()[1]), json.loads(payload)

    def test_status_mapping(self):
        cases = [
            ({"status": "removed", "catalog_id": "requested-x"}, 200),
            ({"status": "not_found", "catalog_id": "requested-x"}, 404),
            ({"status": "invalid_id"}, 400),
        ]
        for result, code in cases:
            fake = mock.MagicMock(return_value=result)
            out = io.StringIO()
            with mock.patch.object(requested_models, "remove_requested", fake), contextlib.redirect_stdout(out):
                got, got_body = self.delete("/models/requested/requested-x")
            self.assertEqual(got, code, result)
            self.assertEqual(got_body, result)
            fake.assert_called_once()
            args = fake.call_args
            self.assertEqual(args[0][0], "requested-x")

    def test_rejections_do_not_call_remove_requested(self):
        fake = mock.MagicMock()
        with mock.patch.object(requested_models, "remove_requested", fake):
            # Query string.
            self.assertEqual(self.delete("/models/requested/requested-x?y=1")[0], 400)
            # Body (Content-Length > 0).
            self.assertEqual(self.delete("/models/requested/requested-x", extra_headers=b"Content-Length: 2\r\n")[0], 400)
            # Nested path.
            self.assertEqual(self.delete("/models/requested/a/b")[0], 400)
            # Empty id.
            self.assertEqual(self.delete("/models/requested/")[0], 400)
        fake.assert_not_called()

    def test_unknown_path_returns_404(self):
        code, body = self.delete("/other")
        self.assertEqual(code, 404)
        self.assertEqual(body, {"error": "Unknown endpoint."})


if __name__ == "__main__":
    unittest.main()
