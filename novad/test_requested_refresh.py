import json
import os
import struct
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import requested_models
from test_requested_models import FakeRegistry, _gguf


def _gguf_with_arch(arch="llama", tensors=((4096, 1000), (4096, 4096))):
    """A valid GGUF v3 header blob with the ``general.architecture`` metadata pair."""
    key = b"general.architecture"
    val = arch.encode()
    out = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", len(tensors)) + struct.pack("<Q", 1)
    out += struct.pack("<Q", len(key)) + key
    out += struct.pack("<I", 8)  # GGUF type string
    out += struct.pack("<Q", len(val)) + val
    for dim in tensors:
        name = f"layer-{dim[0]}x{dim[1]}".encode()
        out += struct.pack("<Q", len(name)) + name
        out += struct.pack("<I", len(dim))
        for d in dim:
            out += struct.pack("<Q", d)
        out += struct.pack("<I", 1) + struct.pack("<Q", 0)
    return out


def _store_bytes(store: Path) -> dict:
    return {
        "models.json": (store / "models.json").read_bytes(),
        "acquisition_fit.json": (store / "acquisition_fit.json").read_bytes(),
    }


class RefreshRequestedTests(unittest.TestCase):
    def test_unchanged_same_registry_leaves_store_untouched_and_skips_blob_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)
            first = FakeRegistry(header=_gguf_with_arch())
            added = requested_models.request_model("gemma4:12b", fetch=first, store=store)
            self.assertEqual(added["status"], "added")
            before = _store_bytes(store)

            second = FakeRegistry(header=_gguf_with_arch())
            result = requested_models.refresh_requested(added["catalog_id"], fetch=second, digest_fetch=second, store=store)

            self.assertEqual(result, {"status": "unchanged"})
            self.assertEqual(_store_bytes(store), before)
            # only the manifest URL is read; no blob URLs (no model/config/template/license read)
            manifest_requests = [r for r in second.requests if "/manifests/" in r[0]]
            blob_requests = [r for r in second.requests if "/blobs/" in r[0]]
            self.assertEqual(len(manifest_requests), 1)
            self.assertEqual(blob_requests, [])

    def test_republished_within_guard_replaces_record_in_place_with_new_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)
            # two models in the store, so we can assert in-place position
            a = requested_models.request_model("model-a", fetch=FakeRegistry(header=_gguf_with_arch(), template="A {{ .A }}"), store=store)
            b = requested_models.request_model("model-b", fetch=FakeRegistry(header=_gguf_with_arch(), template="B {{ .B }}"), store=store)
            doc = json.loads((store / "models.json").read_text())
            old_revision = doc["models"][1]["acquisition"]["expected_revision"]

            # re-publish model-b: same header, template and config content (so
            # total_size is identical) but the manifest bytes differ because we
            # add a field — that changes the manifest sha256 and triggers the
            # re-pin path without changing any sizes.
            new_registry = FakeRegistry(header=_gguf_with_arch(), template="B {{ .B }}")
            new_registry.manifest["mediaType"] = "application/vnd.docker.distribution.manifest.v2+json"
            result = requested_models.refresh_requested(
                b["catalog_id"], fetch=new_registry, digest_fetch=new_registry, store=store,
                observed="2026-10-10T00:00:00Z")

            self.assertEqual(result["status"], "repinned")
            self.assertEqual(result["upstream_id"], "model-b:latest")
            self.assertEqual(result["previous_revision"], old_revision)
            self.assertNotEqual(result["revision"], old_revision)
            self.assertEqual(result["pinned_size"], result["live_size"])  # size unchanged in this fake

            new_doc = json.loads((store / "models.json").read_text())
            new_fit = json.loads((store / "acquisition_fit.json").read_text())
            # same list position, same catalog_id
            self.assertEqual(len(new_doc["models"]), 2)
            self.assertEqual(new_doc["models"][0]["catalog_id"], a["catalog_id"])
            self.assertEqual(new_doc["models"][1]["catalog_id"], b["catalog_id"])
            self.assertEqual(new_doc["models"][1]["acquisition"]["expected_revision"], result["revision"])
            self.assertEqual(new_fit["records"][0]["catalog_id"], a["catalog_id"])
            self.assertEqual(new_fit["records"][1]["catalog_id"], b["catalog_id"])
            self.assertEqual(new_fit["records"][1]["revision"], result["revision"])
            # permissions still private
            self.assertEqual(os.stat(store / "models.json").st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(store / "acquisition_fit.json").st_mode & 0o777, 0o600)

    def test_guard_model_layer_size_change_over_threshold_is_changed_too_much(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)
            first = FakeRegistry(header=_gguf_with_arch())
            added = requested_models.request_model("gemma4:12b", fetch=first, store=store)
            before = _store_bytes(store)

            # bump the model layer size by +30% (guard threshold is 25%)
            bigger = FakeRegistry(header=_gguf_with_arch())
            bigger.manifest["layers"][0]["size"] = int(first.manifest["layers"][0]["size"] * 1.3)

            result = requested_models.refresh_requested(
                added["catalog_id"], fetch=bigger, digest_fetch=bigger, store=store)

            self.assertEqual(result["status"], "changed_too_much")
            self.assertEqual(result["reasons"], ["size_changed_too_much"])
            self.assertEqual(result["pinned_size"], first.manifest["config"]["size"] + first.manifest["layers"][0]["size"] + first.manifest["layers"][1]["size"] + first.manifest["layers"][2]["size"])
            self.assertEqual(_store_bytes(store), before)

    def test_manifest_404_is_gone(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)
            added = requested_models.request_model("gemma4:12b", fetch=FakeRegistry(header=_gguf_with_arch()), store=store)
            before = _store_bytes(store)

            def fetch_404(url, headers=None, limit=1 << 20):
                raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

            result = requested_models.refresh_requested(added["catalog_id"], fetch=fetch_404, digest_fetch=fetch_404, store=store)
            self.assertEqual(result, {"status": "gone", "upstream_id": "gemma4:12b"})
            self.assertEqual(_store_bytes(store), before)

    def test_manifest_urLError_is_unchecked_registry_unreachable(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)
            added = requested_models.request_model("gemma4:12b", fetch=FakeRegistry(header=_gguf_with_arch()), store=store)
            before = _store_bytes(store)

            def fetch_down(url, headers=None, limit=1 << 20):
                raise urllib.error.URLError("connection refused")

            result = requested_models.refresh_requested(added["catalog_id"], fetch=fetch_down, digest_fetch=fetch_down, store=store)
            self.assertEqual(result, {"status": "unchecked", "reason": "registry_unreachable"})
            self.assertEqual(_store_bytes(store), before)

    def test_invalid_manifest_bytes_is_unchecked_registry_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)
            added = requested_models.request_model("gemma4:12b", fetch=FakeRegistry(header=_gguf_with_arch()), store=store)
            before = _store_bytes(store)

            def fetch_invalid(url, headers=None, limit=1 << 20):
                return b"not-a-manifest"

            result = requested_models.refresh_requested(added["catalog_id"], fetch=fetch_invalid, digest_fetch=fetch_invalid, store=store)
            self.assertEqual(result, {"status": "unchecked", "reason": "registry_invalid"})
            self.assertEqual(_store_bytes(store), before)

    def test_unknown_catalog_id_is_not_requested(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)
            result = requested_models.refresh_requested("requested-unknown", fetch=FakeRegistry(header=_gguf_with_arch()), store=store)
            self.assertEqual(result, {"status": "unchecked", "reason": "not_requested"})
            self.assertFalse((store / "models.json").exists())

    def test_record_without_acquisition_pin_is_no_acquisition_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)
            store.mkdir(parents=True, exist_ok=True)
            record = {
                "catalog_id": "requested-x", "display_name": "x", "family": None,
                "release_label": None,
                "source": {"kind": "upstream_metadata", "provider": "ollama",
                           "upstream_id": "x:latest",
                           "reference": "registry.ollama.ai/library/x:latest",
                           "revision_digest": None},
                "properties": {}, "capabilities": {}, "task_tags": [],
                "metadata": {"release_date": None, "license": None, "checked_at": None,
                             "provenance": {"method": "m", "references": [], "notes": None}},
            }
            (store / "models.json").write_text(
                json.dumps({"schema_version": 1, "catalog_revision": "requested",
                            "models": [record]}, indent=2))
            result = requested_models.refresh_requested("requested-x", fetch=FakeRegistry(header=_gguf_with_arch()), store=store)
            self.assertEqual(result, {"status": "unchecked", "reason": "no_acquisition_pin"})


class ServerRouteTests(unittest.TestCase):
    def test_refresh_shipped_pin_routes_requested_ids_to_refresh_requested(self):
        import server
        sentinel = {"status": "repinned", "upstream_id": "model-x:latest",
                    "previous_revision": "old", "revision": "new"}
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(server.requested_models, "refresh_requested", return_value=sentinel) as spy:
                with patch.object(server, "audit") as audit:
                    self.assertEqual(server.refresh_shipped_pin("requested-abc"), sentinel)
                    spy.assert_called_once_with("requested-abc")
                    audit.assert_called_once()
                    kwargs = audit.call_args.kwargs
                    self.assertEqual(kwargs["catalog_id"], "requested-abc")
                    self.assertEqual(kwargs["status"], "repinned")

    def test_refresh_shipped_pin_does_not_touch_requested_models_for_shipped_ids(self):
        import server
        shipped = {
            "catalog_id": "shipped-1",
            "acquisition": {
                "provider": "ollama", "artifact_id": "shipped:1b", "exact_tag": "1b",
                "expected_revision": "abc", "expected_size_bytes": 10,
                "source": "ollama_registry",
                "provenance": {"method": "m", "observed_at": "2026-10-01T00:00:00Z", "notes": None},
            },
        }
        sentinel = {"status": "unchanged"}

        class _Stub:
            def __init__(self, record):
                self._record = record
            def get(self, cid):
                return self._record if cid == "shipped-1" else None

        with patch.object(server.requested_models, "refresh_requested") as spy:
            with patch.object(server.catalog_repins, "refresh", return_value=sentinel) as refresh:
                with patch.object(server, "audit") as audit:
                    with patch.object(server, "load_catalog", return_value=_Stub(shipped)):
                        result = server.refresh_shipped_pin("shipped-1")
                        self.assertEqual(result, sentinel)
                        refresh.assert_called_once_with(shipped)
                        audit.assert_not_called()
                        spy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
