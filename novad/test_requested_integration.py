"""QWEN-09: requested models in the catalog view, fit, trust, and HTTP endpoint.

No network: the store is seeded through ``requested_models.request_model`` with a
fake registry fetch (the same ``FakeRegistry`` shape as the QWEN-08 tests), and
the endpoint tests patch ``requested_models.request_model``.
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

import acquisition_fit
import acquisition_integration
import catalog_observability as obs
import model_fit
import requested_models
import server
from acquisition import DiskSnapshot

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

    Copied from test_requested_models.py (QWEN-08).
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
    """Seed a throwaway requested-models store (no network). The default model is not
    in the shipped catalog; gemma4:12b is, so it seeds a duplicate."""

    def seed_store(self, name="granite3.3:2b"):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state"
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.state)}):
            result = requested_models.request_model(
                name, fetch=FakeRegistry(header=_gguf(), template=".Tools {{ .Tools }}", projector=True))
        if result["status"] != "added":
            raise AssertionError(f"store seed failed: {result}")
        store = self.state / "nova" / "requested"
        self.record = json.loads((store / "models.json").read_text())["models"][0]

    def tearDown(self):
        self.tmp.cleanup()


class ViewTests(SeedStoreMixin, unittest.TestCase):
    def setUp(self):
        self.seed_store()
        self.inventory = {'provider': 'ollama', 'source': 'test', 'status': 'ready',
                          'observed_at': '2026-10-07T00:00:00Z', 'records': [], 'errors': []}
        self.shipped = obs.load_catalog(obs.DEFAULT_PATH)

    def view(self):
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.state)}), \
                mock.patch.object(obs, 'local_inventory', return_value=self.inventory):
            return obs.catalog_view()

    def test_requested_record_joins_view_with_flags(self):
        view = self.view()
        by_id = {m['catalog_id']: m for m in view['models']}
        shipped_ids = {r['catalog_id'] for r in self.shipped.records}
        new_ids = [cid for cid in by_id if cid not in shipped_ids]
        self.assertEqual(len(new_ids), 1)
        self.assertTrue(new_ids[0].startswith('requested-'))
        self.assertIs(by_id[new_ids[0]]['user_requested'], True)
        self.assertEqual(len(by_id), len(self.shipped.records) + 1)
        self.assertTrue(all(by_id[cid]['user_requested'] is False for cid in shipped_ids))
        self.assertEqual(view['catalog_revision'], self.shipped.revision)
        self.assertEqual(view['status'], self.shipped.status)
        self.assertEqual(view['loader_errors'], list(self.shipped.errors))

    def test_requested_duplicate_of_a_shipped_model_is_hidden(self):
        self.tmp.cleanup(); self.seed_store("gemma4:12b")
        view = self.view()
        self.assertEqual(len(view['models']), len(self.shipped.records))
        self.assertFalse(any(m['user_requested'] for m in view['models']))

    def test_corrupt_store_view_equals_shipped(self):
        (self.state / "nova" / "requested" / "models.json").write_text("{not json")
        view = self.view()
        baseline = obs.present_catalog(self.shipped, self.inventory)
        self.assertEqual(view['record_count'], baseline['record_count'])
        self.assertEqual(view['catalog_revision'], baseline['catalog_revision'])
        self.assertEqual(view['status'], baseline['status'])
        self.assertEqual(view['loader_errors'], baseline['loader_errors'])
        self.assertEqual(view['models'], [dict(m, user_requested=False) for m in baseline['models']])


class FitTests(SeedStoreMixin, unittest.TestCase):
    def setUp(self):
        self.seed_store()
        self.budget = model_fit.operating_budget(
            {"local_ai": {"available": True}, "capacity": {"memory": {"total_bytes": 64 * GIB}, "gpus": []}})

    def snapshot(self):
        return {"provider_available": True, "budget": self.budget}

    def test_saved_fit_reads_store_fit_file(self):
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.state)}):
            fit = acquisition_fit.saved_fit(self.record, self.snapshot())
        self.assertIsNotNone(fit)
        self.assertEqual(fit['weights_bytes'], self.record['properties']['weight_size_bytes'])

    def test_saved_fit_none_when_revision_differs(self):
        store = self.state / "nova" / "requested"
        fit_doc = json.loads((store / "acquisition_fit.json").read_text())
        fit_doc["records"][0]["revision"] = "0" * 64
        (store / "acquisition_fit.json").write_text(json.dumps(fit_doc))
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.state)}):
            self.assertIsNone(acquisition_fit.saved_fit(self.record, self.snapshot()))


class TrustTests(SeedStoreMixin, unittest.TestCase):
    def setUp(self):
        self.seed_store()
        self.disk = DiskSnapshot(1024 ** 4, 512 * GIB)

    def preflight(self, record):
        catalog_snapshot = {'models': [record], 'catalog_revision': 'test-revision'}
        return acquisition_integration.build_preflight(
            catalog_snapshot, {'provider_available': True}, record['catalog_id'], self.disk)

    def test_trust_state_trusted(self):
        self.assertEqual(acquisition_integration.trust_state(self.record), 'TRUSTED')

    def test_preflight_trust_basis_user_requested(self):
        self.assertEqual(self.preflight(self.record)['trust_basis'], 'user_requested')

    def test_preflight_trust_basis_shipped_allowlist(self):
        shipped = obs.load_catalog(obs.DEFAULT_PATH)
        record = next(r for r in shipped.records if isinstance(r.get('acquisition'), dict))
        self.assertEqual(self.preflight(record)['trust_basis'], 'backend_allowlist')


class Wire:
    def __init__(self, request):
        self.request = io.BytesIO(request)
        self.response = bytearray()

    def makefile(self, *args, **kwargs):
        return self.request

    def sendall(self, data):
        self.response.extend(data)


class EndpointTests(unittest.TestCase):
    def post(self, path, body, extra_headers=b""):
        head = f"POST {path} HTTP/1.0\r\nHost: localhost\r\nContent-Length: {len(body)}\r\n".encode() + extra_headers + b"\r\n"
        wire = Wire(head + body)
        server.Handler(wire, ("127.0.0.1", 1), None)
        raw = bytes(wire.response)
        status_line, rest = raw.split(b"\r\n", 1)
        headers, payload = rest.split(b"\r\n\r\n", 1)
        return int(status_line.split()[1]), json.loads(payload)

    def test_status_mapping_and_audit(self):
        cases = [
            ({"status": "added", "catalog_id": "requested-x", "display_name": "X", "name": "x:latest"}, 201),
            ({"status": "invalid_reference"}, 400),
            ({"status": "not_found", "name": "x:latest"}, 404),
            ({"status": "unsupported_model", "name": "x:latest"}, 422),
            ({"status": "registry_unavailable", "name": "x:latest"}, 502),
        ]
        body = json.dumps({"reference": "granite3.3:2b"}).encode()
        for result, code in cases:
            fake = mock.MagicMock(return_value=result)
            out = io.StringIO()
            with mock.patch.object(requested_models, "request_model", fake), contextlib.redirect_stdout(out):
                got, got_body = self.post("/models/requested", body)
            self.assertEqual(got, code, result)
            self.assertEqual(got_body, result)
            fake.assert_called_once_with("granite3.3:2b")
            audit_lines = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
            self.assertTrue(any(line.get("event") == "model_requested"
                                and line.get("status") == result["status"]
                                and line.get("name") == result.get("name")
                                and "reference" not in line and "granite3.3:2b" not in json.dumps(line)
                                for line in audit_lines), out.getvalue())

    def test_model_already_in_catalog_is_not_requested_again(self):
        # 2026-10-08, Konstantin: "try ADD A MODEL for the 30b coder" (it ships in the catalog).
        fake = mock.MagicMock()
        out = io.StringIO()
        with mock.patch.object(requested_models, "request_model", fake), contextlib.redirect_stdout(out):
            code, body = self.post("/models/requested", json.dumps({"reference": "https://ollama.com/library/qwen3-coder:30b"}).encode())
        self.assertEqual(code, 200)
        self.assertEqual(body, {"status": "in_catalog", "name": "qwen3-coder:30b", "catalog_id": "qwen3-coder-30b-q4-k-m"})
        fake.assert_not_called()

    def test_rejections_do_not_call_request_model(self):
        fake = mock.MagicMock()
        with mock.patch.object(requested_models, "request_model", fake):
            big = json.dumps({"reference": "a" * 1008}).encode()
            self.assertEqual(len(big), 1025)
            rejections = [
                ("/models/requested?x=1", json.dumps({"reference": "x"}).encode()),
                ("/models/requested", json.dumps({"reference": "x", "pad": 1}).encode()),
                ("/models/requested", json.dumps({"reference": 42}).encode()),
                ("/models/requested", b""),
                ("/models/requested", big),
            ]
            for path, payload in rejections:
                code, body = self.post(path, payload)
                self.assertEqual((code, body), (400, {"error": "invalid_model_request"}), (path, payload[:40]))
        fake.assert_not_called()


if __name__ == "__main__":
    unittest.main()
