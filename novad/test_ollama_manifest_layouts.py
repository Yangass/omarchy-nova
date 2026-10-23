"""Ollama 0.40 manifest layouts (found 2026-10-23 in a live test of NOVA 0.16).

Ollama 0.40 keeps each manifest as a blob named by its sha256 and links to it from
manifests-v2/ollama.com/<namespace>/<model>/<tag>. A pull still writes the old tree, but
with the real manifest added as one extra layer; `ollama create` and `cp` write only the new
tree. NOVA read only the old tree and hashed the old file: every catalog install on Ollama
0.40+ ended "INSTALLATION FAILED · revision_mismatch" (digest and size both differed), and
NOVA's own coding-CLI builds were invisible to it. These fixtures copy the layout seen on
the host (Ollama 0.40.7).
"""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import catalog_observability as obs
import model_updates
import ollama_acquisition as adapter
from acquisition_lifecycle import frozen_descriptor
from test_acquisition_lifecycle import fixture


class Store:
    """A minimal Ollama 0.40 storage folder."""

    def __init__(self, root):
        self.root = root
        (root / "blobs").mkdir(); (root / "manifests").mkdir()

    def blob(self, data):
        digest = hashlib.sha256(data).hexdigest()
        (self.root / "blobs" / ("sha256-" + digest)).write_bytes(data)
        return {"mediaType": "application/vnd.ollama.image.model", "digest": "sha256:" + digest, "size": len(data)}

    def manifest(self, *layers):
        config = self.blob(b'{"model_format":"gguf"}')
        raw = json.dumps({"schemaVersion": 2, "mediaType": obs.MANIFEST_LAYER, "config": config, "layers": list(layers)}).encode()
        return raw, self.blob(raw)

    def link(self, name, tag, manifest_layer, host="ollama.com", namespace="library"):
        path = self.root / "manifests-v2" / host / namespace / name / tag
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to("../../../../blobs/" + manifest_layer["digest"].replace(":", "-"))
        return path

    def old(self, name, tag, raw, manifest_layer=None):
        """The old tree; Ollama 0.40 adds the real manifest as one more layer."""
        value = json.loads(raw)
        if manifest_layer: value["layers"].append({**manifest_layer, "mediaType": obs.MANIFEST_LAYER})
        path = self.root / "manifests/registry.ollama.ai/library" / name / tag
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(json.dumps(value).encode())
        return path


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nova-manifests-", dir="/var/tmp"); self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.weights = self.store.blob(b"weights" * 100)
        self.raw, self.layer = self.store.manifest(self.weights)
        self.digest = hashlib.sha256(self.raw).hexdigest()

    def records(self, **kwargs):
        result = obs.local_inventory(self.store.root, **kwargs)
        return result, {record["id"]: record for record in result["records"]}

    def test_a_pulled_model_has_the_registrys_digest_and_its_own_files(self):
        self.store.link("tiny", "1b", self.layer)
        self.store.old("tiny", "1b", self.raw, self.layer)
        result, records = self.records(with_layers=True)
        self.assertEqual((result["status"], list(records)), ("ready", ["tiny:1b"]))
        self.assertEqual(records["tiny:1b"]["digest"], self.digest)
        self.assertEqual(set(records["tiny:1b"]["layers"]), {self.weights["digest"], json.loads(self.raw)["config"]["digest"]})

    def test_the_old_file_alone_is_read_through_its_manifest_layer(self):
        # The old tree only (e.g. the new tree's link missing): same digest, wrapper not counted.
        self.store.old("tiny", "1b", self.raw, self.layer)
        _, records = self.records(with_layers=True)
        self.assertEqual(records["tiny:1b"]["digest"], self.digest)
        self.assertNotIn(self.layer["digest"], records["tiny:1b"]["layers"])

    def test_a_created_model_exists_only_in_the_new_tree_and_is_seen(self):
        build_raw, build_layer = self.store.manifest(self.weights, self.store.blob(b"num_ctx 32768"))
        self.store.link("nova-tiny-1b-cli", "latest", build_layer)
        self.store.old("tiny", "1b", self.raw)  # a model from before Ollama 0.40: old tree, no wrapper
        _, records = self.records(with_layers=True)
        self.assertEqual(sorted(records), ["nova-tiny-1b-cli:latest", "tiny:1b"])
        self.assertEqual(records["nova-tiny-1b-cli:latest"]["digest"], hashlib.sha256(build_raw).hexdigest())
        self.assertEqual(records["tiny:1b"]["digest"], self.digest)

    def test_other_hosts_keep_their_prefix(self):
        self.store.link("model-gguf", "q4", self.layer, host="hf.co", namespace="org")
        _, records = self.records()
        self.assertEqual(list(records), ["hf.co/org/model-gguf:q4"])

    def test_links_go_only_to_a_manifest_blob_with_that_digest(self):
        outside = self.store.root / "elsewhere"; outside.write_bytes(self.raw)
        path = self.store.root / "manifests-v2/ollama.com/library/evil/1b"; path.parent.mkdir(parents=True)
        path.symlink_to(outside)
        renamed = self.store.root / "blobs" / ("sha256-" + "0" * 64); renamed.write_bytes(self.raw)  # name ≠ content
        self.store.link("swapped", "1b", {"digest": "sha256:" + "0" * 64})
        result, records = self.records()
        self.assertEqual(result["status"], "partial")
        self.assertEqual((records["evil:1b"]["installed"], records["swapped:1b"]["installed"]), (None, None))

    def test_a_link_in_the_old_tree_is_still_refused(self):
        path = self.store.root / "manifests/registry.ollama.ai/library/tiny/1b"; path.parent.mkdir(parents=True)
        path.symlink_to("../../../../blobs/" + self.layer["digest"].replace(":", "-"))
        result, records = self.records()
        self.assertEqual((result["status"], records["tiny:1b"]["installed"]), ("partial", None))

    def test_a_manifest_that_wraps_another_wrapper_is_refused(self):
        wrapper = json.loads(self.raw); wrapper["layers"].append({**self.layer, "mediaType": obs.MANIFEST_LAYER})
        _, outer = self.store.manifest(*wrapper["layers"])
        self.store.old("tiny", "1b", json.dumps({**wrapper, "layers": [self.weights]}).encode(), outer)
        result, records = self.records()
        self.assertEqual((result["status"], records["tiny:1b"]["installed"]), ("partial", None))

    def test_install_check_and_update_check_read_the_new_tree(self):
        self.store.link("tiny", "1b", self.layer)
        self.store.old("tiny", "1b", self.raw, self.layer)
        size = self.weights["size"] + json.loads(self.raw)["config"]["size"]
        descriptor = replace(frozen_descriptor(fixture()["descriptor"]), artifact_id="tiny:1b", exact_tag="1b",
                             expected_revision=self.digest, expected_artifact_size=size)
        tags = {"models": [{"name": "tiny:1b", "digest": self.digest}]}
        with patch.object(adapter, "OLLAMA_STORAGE", self.store.root), patch.object(adapter, "local_json", return_value=tags), \
             patch.object(adapter, "local_inventory", lambda: obs.local_inventory(self.store.root)):
            self.assertEqual(adapter.inspect_artifact(descriptor), {"state": "exact", "revision": self.digest, "footprint_bytes": size})
        layers = model_updates.local_layers("tiny:1b", self.store.root)
        self.assertEqual(set(layers), {self.weights["digest"], json.loads(self.raw)["config"]["digest"]})


if __name__ == "__main__":
    unittest.main()
