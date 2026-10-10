"""CATALOG-01a: registry probe parsing and record shape, fully offline."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("catalog_probe", ROOT / "tools/catalog_probe.py")
probe = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(probe)
sys.path.insert(0, str(ROOT / "novad"))
import catalog  # noqa: E402


def gguf(arch="testarch", tensors=((4096, 1000), (4096, 4096)), file_type=15, vocab=50) -> bytes:
    def s(text): data = text.encode(); return struct.pack("<Q", len(data)) + data
    kv = [("general.architecture", 8, s(arch)), ("general.file_type", 4, struct.pack("<I", file_type)),
          ("general.basename", 8, s("Test")), ("general.size_label", 8, s("7B")),
          (f"{arch}.context_length", 4, struct.pack("<I", 32768)), (f"{arch}.block_count", 4, struct.pack("<I", 28)),
          (f"{arch}.attention.head_count_kv", 4, struct.pack("<I", 4)),
          (f"{arch}.attention.key_length", 4, struct.pack("<I", 128)),
          (f"{arch}.attention.value_length", 4, struct.pack("<I", 128)),
          ("tokenizer.ggml.tokens", 9, struct.pack("<IQ", 8, vocab) + b"".join(s(f"t{i}") for i in range(vocab))),
          ("tokenizer.ggml.scores", 9, struct.pack("<IQ", 6, vocab) + b"\0" * 4 * vocab)]
    out = b"GGUF" + struct.pack("<IQQ", 3, len(tensors), len(kv))
    for key, kind, payload in kv: out += s(key) + struct.pack("<I", kind) + payload
    for i, dims in enumerate(tensors):
        out += s(f"blk.{i}.weight") + struct.pack("<I", len(dims)) + b"".join(struct.pack("<Q", d) for d in dims)
        out += struct.pack("<IQ", 12, 0)
    return out + b"\0" * 64


class AttentionShapeTests(unittest.TestCase):
    def test_standard_head_width_is_derived(self):
        meta = {"llama.block_count": 32, "llama.attention.head_count_kv": 8, "llama.attention.head_count": 32, "llama.embedding_length": 4096}
        self.assertEqual(probe.attention_shape(meta, "llama"), {"layers": 32, "kv_heads": 8, "key_length": 128, "value_length": 128})

    def test_hybrid_interval_counts_full_attention_layers(self):
        meta = {"qwen35.block_count": 65, "qwen35.attention.head_count_kv": [4, 0, 0, 4], "qwen35.attention.key_length": 256,
                "qwen35.attention.value_length": 256, "qwen35.full_attention_interval": 4}
        shape = probe.attention_shape(meta, "qwen35")
        self.assertEqual((shape["full_attention_layers"], shape["kv_heads"]), (16, 4))

    def test_sliding_pattern_splits_full_and_window_cache(self):
        pattern = [True] * 5 + [False]
        meta = {"gemma4.block_count": 6, "gemma4.attention.head_count_kv": [8] * 5 + [1], "gemma4.attention.key_length": 512,
                "gemma4.attention.value_length": 512, "gemma4.attention.sliding_window": 1024,
                "gemma4.attention.sliding_window_pattern": pattern,
                "gemma4.attention.key_length_swa": 256, "gemma4.attention.value_length_swa": 256}
        shape = probe.attention_shape(meta, "gemma4")
        self.assertEqual((shape["full_attention_layers"], shape["kv_heads"]), (1, 1))
        self.assertEqual(shape["window_cache_elements"], 5 * 8 * 512 * 1024)

    def test_incomplete_pattern_does_not_invent_values(self):
        meta = {"gemma4.block_count": 6, "gemma4.attention.head_count_kv": [8, 1], "gemma4.attention.key_length": 512,
                "gemma4.attention.value_length": 512}
        shape = probe.attention_shape(meta, "gemma4")
        self.assertIsNone(shape["kv_heads"]); self.assertNotIn("full_attention_layers", shape)


class FakeRegistry:
    def __init__(self, header: bytes, template="{{ .Tools }} <think>", projector=False, config=None, with_template=True):
        self.header, self.requests = header, []
        layers = [{"mediaType": "application/vnd.ollama.image.model", "digest": "sha256:model", "size": 4_000_000_000},
                  {"mediaType": "application/vnd.ollama.image.license", "digest": "sha256:lic", "size": 20}]
        if with_template: layers.append({"mediaType": "application/vnd.ollama.image.template", "digest": "sha256:tmpl", "size": 40})
        if projector: layers.append({"mediaType": "application/vnd.ollama.image.projector", "digest": "sha256:proj", "size": 600_000_000})
        self.manifest = json.dumps({"config": {"size": 500, "digest": "sha256:cfg"}, "layers": layers}).encode()
        self.blobs = {"tmpl": template.encode(), "lic": b"\n Apache License\nVersion 2.0", "proj": b"",
                      "cfg": json.dumps(config or {}).encode()}

    def __call__(self, url, headers=None, limit=1 << 20):
        self.requests.append((url, headers or {}))
        if "/manifests/" in url: return self.manifest
        digest = url.rsplit(":", 1)[1]
        if digest == "model":
            end = int(headers["Range"].split("-")[1]) + 1
            return self.header[:end]
        return self.blobs[digest][:limit]


class ProbeTests(unittest.TestCase):
    def test_header_parse_counts_parameters_and_reads_attention(self):
        metadata, parameters = probe.parse_gguf(gguf())
        self.assertEqual(parameters, 4096 * 1000 + 4096 * 4096)
        self.assertEqual((metadata["general.architecture"], metadata["testarch.block_count"]), ("testarch", 28))
        with self.assertRaises(probe.Truncated):
            probe.parse_gguf(gguf()[:100])
        with self.assertRaises(ValueError):
            probe.parse_gguf(b"NOPE" + gguf()[4:])

    def test_probe_pins_identity_and_declarations_without_full_download(self):
        registry = FakeRegistry(gguf())
        found = probe.probe("test-model:7b", fetch=registry)
        self.assertEqual(found["manifest_sha256"], hashlib.sha256(registry.manifest).hexdigest())
        self.assertEqual(found["total_size"], 500 + 4_000_000_000 + 20 + 40)
        self.assertEqual((found["quantization"], found["context_length"], found["license_line"]), ("Q4_K_M", 32768, "Apache License"))
        self.assertEqual(found["capabilities"], {"chat": True, "tools": True, "thinking": True, "vision": None})
        model_reads = [h for u, h in registry.requests if u.endswith(":model")]
        self.assertTrue(model_reads and all("Range" in h for h in model_reads))

    def test_large_header_grows_the_ranged_read(self):
        big = gguf(vocab=500_000)  # tokenizer arrays push tensor infos past 8 MiB
        self.assertGreater(len(big), 8 << 20)
        registry = FakeRegistry(big, template="{{ .Prompt }}", projector=True)
        found = probe.probe("big:latest", fetch=registry)
        self.assertEqual(found["capabilities"], {"chat": True, "tools": None, "thinking": None, "vision": True})
        self.assertEqual(len([u for u, _ in registry.requests if u.endswith(":model")]), 2)

    def test_renderer_models_use_config_quantization_and_only_verified_parsers(self):
        known = probe.probe("q:9b", fetch=FakeRegistry(gguf(file_type=99), with_template=False,
                            config={"file_type": "MXFP4", "renderer": "qwen3.5", "parser": "qwen3.5", "requires": "0.17.1"}))
        self.assertEqual((known["quantization"], known["requires_ollama"]), ("MXFP4", "0.17.1"))
        self.assertEqual(known["capabilities"], {"chat": True, "tools": True, "thinking": True, "vision": None})
        unknown = probe.probe("g:12b", fetch=FakeRegistry(gguf(), with_template=False,
                              config={"renderer": "gemma4", "parser": "gemma4"}, projector=True))
        self.assertEqual(unknown["capabilities"], {"chat": True, "tools": None, "thinking": None, "vision": True})
        record, _ = probe.records(known, [], None, "2026-10-04T12:00:00Z")
        self.assertIn("Requires Ollama >= 0.17.1", record["metadata"]["provenance"]["notes"])

    def test_records_write_minimum_provider_version_for_dotted_triples(self):
        found = probe.probe("m:1b", fetch=FakeRegistry(gguf(), config={"requires": "0.30.9"}))
        self.assertEqual(found["requires_ollama"], "0.30.9")
        record, fit = probe.records(found, ["general"], None, "2026-10-07T12:00:00Z")
        self.assertEqual(record["acquisition"]["minimum_provider_version"], "0.30.9")
        parsed = catalog.validate_catalog({"schema_version": 1, "catalog_revision": "test", "models": [record]})
        self.assertEqual(parsed.status, "ready")
        self.assertEqual(parsed.records[0]["acquisition"]["minimum_provider_version"], "0.30.9")
        self.assertNotIn("minimum_provider_version", json.dumps(fit))

    def test_records_skip_minimum_provider_version_for_other_shapes(self):
        for requires in (None, "0.30", "0.6.5-rc1"):
            with self.subTest(requires=requires):
                config = {"requires": requires} if requires is not None else {}
                found = probe.probe("m:1b", fetch=FakeRegistry(gguf(), config=config))
                record, _ = probe.records(found, ["general"], None, "2026-10-07T12:00:00Z")
                self.assertNotIn("minimum_provider_version", record["acquisition"])
                parsed = catalog.validate_catalog({"schema_version": 1, "catalog_revision": "test", "models": [record]})
                self.assertEqual(parsed.status, "ready")

    def test_records_validate_against_the_catalog_schema(self):
        found = probe.probe("test-model:7b", fetch=FakeRegistry(gguf()))
        record, fit = probe.records(found, ["general", "coding"], None, "2026-10-04T12:00:00Z")
        self.assertEqual(record["catalog_id"], "test-model-7b-q4-k-m")
        parsed = catalog.validate_catalog({"schema_version": 1, "catalog_revision": "test", "models": [record]})
        self.assertEqual((parsed.status, len(parsed.records)), ("ready", 1))
        self.assertEqual(fit["metadata"]["fit_metadata_source"], "registry/gguf_header")
        self.assertEqual((fit["revision"], fit["metadata"]["size_bytes"]), (record["source"]["revision_digest"], record["properties"]["weight_size_bytes"]))

    def test_shipped_fit_file_uses_an_accepted_source(self):
        import acquisition_fit
        data = json.loads((ROOT / "catalog/acquisition_fit.json").read_text())
        self.assertIn(data["source"], acquisition_fit.FIT_SOURCES)


if __name__ == "__main__":
    unittest.main()
