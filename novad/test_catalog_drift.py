"""QWEN-18: catalog drift check against the Ollama registry, fully offline.

Every test passes a fake ``fetch``; nothing here touches the network.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import catalog_drift  # noqa: E402
from registry_probe import MANIFEST_ACCEPT, REGISTRY  # noqa: E402

spec = importlib.util.spec_from_file_location("catalog_drift_cmd", ROOT.parent / "tools/catalog_drift_report.py")
cmd = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(cmd)


MODEL_MEDIA = "application/vnd.ollama.image.model"


def manifest_bytes(config_size=10, layer_sizes=(100, 2000), model=True) -> bytes:
    first = MODEL_MEDIA if model else "application/vnd.ollama.image.license"
    layers = [{"mediaType": first if i == 0 else "application/vnd.ollama.image.license",
               "digest": f"sha256:layer{i}", "size": s} for i, s in enumerate(layer_sizes)]
    return json.dumps({"config": {"size": config_size, "digest": "sha256:config"}, "layers": layers}).encode()


OK_DIGEST = hashlib.sha256(manifest_bytes()).hexdigest()
OK_SIZE = 2110


class RecordingFetch:
    """Fake fetch: records (url, headers); returns the bytes it was told to."""

    def __init__(self, payload: bytes = manifest_bytes()):
        self.payload = payload
        self.calls = []

    def __call__(self, url, headers=None, *args, **kwargs):
        self.calls.append((url, dict(headers or {})))
        return self.payload


def model(catalog_id="m-1", upstream_id="qwen3:8b", pinned="0" * 64, pinned_size=1000) -> dict:
    return {
        "catalog_id": catalog_id,
        "source": {"upstream_id": upstream_id, "revision_digest": "f" * 64},
        "properties": {"weight_size_bytes": 900},
        "acquisition": {"artifact_id": upstream_id, "expected_revision": pinned, "expected_size_bytes": pinned_size},
    }


class ManifestCheckTests(unittest.TestCase):
    def test_ok_fills_digest_size_and_model_layer_digest(self):
        fetch = RecordingFetch()
        check = catalog_drift.manifest_check("qwen3:8b", fetch=fetch)
        self.assertEqual(check, {"upstream_id": "qwen3:8b", "status": "ok",
                                 "manifest_sha256": OK_DIGEST, "total_size": OK_SIZE,
                                 "model_layer_digest": "sha256:layer0", "reason": None})
        self.assertEqual(fetch.calls[0], (f"{REGISTRY}/v2/library/qwen3/manifests/8b", {"Accept": MANIFEST_ACCEPT}))

    def test_repo_and_tag_rules(self):
        for name, repo_tag in (("user/model:tag", "user/model/manifests/tag"), ("user/model", "user/model/manifests/latest")):
            with self.subTest(name=name):
                fetch = RecordingFetch()
                catalog_drift.manifest_check(name, fetch=fetch)
                self.assertEqual(fetch.calls[0][0], f"{REGISTRY}/v2/{repo_tag}")

    def test_404_is_gone(self):
        def fetch(url, headers=None, *a, **k): raise HTTPError(url, 404, "no", None, None)
        check = catalog_drift.manifest_check("qwen3:8b", fetch=fetch)
        self.assertEqual(check, {"upstream_id": "qwen3:8b", "status": "gone",
                                 "manifest_sha256": None, "total_size": None,
                                 "model_layer_digest": None, "reason": "not in the registry"})

    def test_http_500_is_unreachable_with_code(self):
        def fetch(url, headers=None, *a, **k): raise HTTPError(url, 500, "oops", None, None)
        check = catalog_drift.manifest_check("qwen3:8b", fetch=fetch)
        self.assertEqual((check["status"], check["reason"]), ("unreachable", "registry answered 500"))

    def test_urlerror_and_timeout_are_network_unavailable(self):
        def urlerror(url, headers=None, *a, **k): raise URLError("dns down")
        def timeout(url, headers=None, *a, **k): raise TimeoutError("slow")
        for fake in (urlerror, timeout):
            with self.subTest(fake=fake.__name__):
                check = catalog_drift.manifest_check("qwen3:8b", fetch=fake)
                self.assertEqual((check["status"], check["reason"]), ("unreachable", "network unavailable"))

    def test_invalid_manifests(self):
        cases = [
            (b"not json", "non-JSON bytes"),
            (json.dumps([1, 2]).encode(), "JSON list"),
            (manifest_bytes(model=False), "no .model layer"),
            (json.dumps({"config": {"size": True, "digest": "d"},
                        "layers": [{"mediaType": "application/vnd.ollama.image.model", "digest": "d", "size": 5}]}).encode(),
             "bool size"),
        ]
        for payload, label in cases:
            with self.subTest(label=label):
                check = catalog_drift.manifest_check("qwen3:8b", fetch=RecordingFetch(payload))
                self.assertEqual((check["status"], check["reason"]), ("invalid", "unexpected manifest"))
                self.assertEqual(check["manifest_sha256"], hashlib.sha256(payload).hexdigest())
                self.assertEqual((check["total_size"], check["model_layer_digest"]), (None, None))


class CompareTests(unittest.TestCase):
    def ok_check(self, digest, size):
        return {"upstream_id": "qwen3:8b", "status": "ok", "manifest_sha256": digest,
                "total_size": size, "model_layer_digest": "d", "reason": None}

    def test_same(self):
        out = catalog_drift.compare(model(pinned="a" * 64, pinned_size=1000), self.ok_check("a" * 64, 1000))
        self.assertEqual(out, {"catalog_id": "m-1", "upstream_id": "qwen3:8b", "verdict": "same",
                               "pinned_digest": "a" * 64, "live_digest": "a" * 64,
                               "pinned_size": 1000, "live_size": 1000, "size_change_pct": None, "reason": None})

    def test_changed_with_pct(self):
        out = catalog_drift.compare(model(pinned="a" * 64, pinned_size=1000), self.ok_check("b" * 64, 925))
        self.assertEqual(out["verdict"], "changed")
        self.assertEqual(out["size_change_pct"], -7.5)
        self.assertEqual((out["pinned_size"], out["live_size"]), (1000, 925))

    def test_pinned_falls_back_to_source_and_properties(self):
        m = {"catalog_id": "m-1",
             "source": {"upstream_id": "qwen3:8b", "revision_digest": "f" * 64},
             "properties": {"weight_size_bytes": 900}}
        out = catalog_drift.compare(m, self.ok_check("f" * 64, 800))
        self.assertEqual((out["pinned_digest"], out["pinned_size"]), ("f" * 64, 900))
        self.assertEqual((out["verdict"], out["size_change_pct"]), ("same", None))

    def test_changed_with_zero_pinned_size_has_no_pct(self):
        out = catalog_drift.compare(model(pinned_size=0), self.ok_check("b" * 64, 500))
        self.assertEqual((out["verdict"], out["size_change_pct"]), ("changed", None))

    def test_non_ok_status_becomes_the_verdict(self):
        for status, reason in (("gone", "not in the registry"),
                               ("unreachable", "network unavailable"),
                               ("invalid", "unexpected manifest")):
            with self.subTest(status=status):
                out = catalog_drift.compare(model(), {"upstream_id": "qwen3:8b", "status": status,
                                                      "manifest_sha256": None, "total_size": None,
                                                      "model_layer_digest": None, "reason": reason})
                self.assertEqual(out["verdict"], status)
                self.assertIsNone(out["size_change_pct"])

    def test_upstream_id_of(self):
        self.assertEqual(catalog_drift.upstream_id_of(model()), "qwen3:8b")
        self.assertEqual(catalog_drift.upstream_id_of({"catalog_id": "x", "source": {"upstream_id": "u:m"}}), "u:m")
        self.assertIsNone(catalog_drift.upstream_id_of({"catalog_id": "x"}))


class DriftReportTests(unittest.TestCase):
    def test_input_order_and_missing_upstream_id(self):
        fetch = RecordingFetch()
        models = [model(catalog_id="b-model"), model(catalog_id="a-model"), {"catalog_id": "no-id"}]
        report = catalog_drift.drift_report(models, fetch=fetch)
        self.assertEqual([row["catalog_id"] for row in report], ["b-model", "a-model", "no-id"])
        self.assertEqual(len(fetch.calls), 2)
        self.assertEqual(report[2]["verdict"], "invalid")
        self.assertIsNone(report[2]["upstream_id"])
        self.assertEqual(set(report[0]), {"catalog_id", "upstream_id", "verdict", "pinned_digest",
                                          "live_digest", "pinned_size", "live_size", "size_change_pct", "reason"})

    def test_unreachable_keeps_the_reason_for_the_command_line(self):
        import io, urllib.error
        def fetch(url, headers=None):
            raise urllib.error.HTTPError(url, 500, "x", {}, io.BytesIO(b""))
        [row] = catalog_drift.drift_report([model(catalog_id="m-1")], fetch=fetch)
        self.assertEqual((row["verdict"], row["reason"]), ("unreachable", "registry answered 500"))
        code, lines = capture(lambda: cmd.main(["--catalog-dir", str(temp_catalog([model(catalog_id="m-1")]))], fetch=fetch))
        self.assertEqual((code, lines[0]), (3, "UNREACHABLE m-1: registry answered 500"))


def capture(fn):
    """Run fn, capture its stdout; return (exit_code, lines)."""
    import io
    from contextlib import redirect_stdout
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer):
            code = fn()
    except SystemExit as exc:
        code = exc.code
    return code, buffer.getvalue().splitlines()


def temp_catalog(models):
    import tempfile
    directory = Path(tempfile.mkdtemp(prefix="nova-drift-"))
    (directory / "models.json").write_text(json.dumps({"schema_version": 1, "catalog_revision": "test", "models": models}))
    return directory


def fetch_by_status(status):
    if status == "gone":
        def gone(url, headers=None, *a, **k): raise HTTPError(url, 404, "no", None, None)
        return gone
    if status == "unreachable":
        def down(url, headers=None, *a, **k): raise URLError("dns")
        return down
    if status == "invalid":
        return RecordingFetch(b"not json")
    return RecordingFetch(manifest_bytes())


class CommandTextTests(unittest.TestCase):
    def test_one_of_each_verdict_exact_lines_order_and_summary(self):
        models = [
            model(catalog_id="a-change", upstream_id="ac:1", pinned="a" * 64, pinned_size=1000),
            model(catalog_id="b-gone", upstream_id="bg:2"),
            model(catalog_id="c-invalid", upstream_id="ci:3"),
            model(catalog_id="d-unreachable", upstream_id="du:4"),
            model(catalog_id="e-same", upstream_id="es:5", pinned=OK_DIGEST, pinned_size=OK_SIZE),
        ]
        by_upstream = {"ac:1": fetch_by_status("ok"), "bg:2": fetch_by_status("gone"),
                       "ci:3": fetch_by_status("invalid"), "du:4": fetch_by_status("unreachable"),
                       "es:5": fetch_by_status("ok")}
        def fetch(url, headers=None, *a, **k):
            for upstream_id, fake in by_upstream.items():
                if url.endswith("/" + upstream_id.split(":")[1]):
                    return fake(url, headers)
            raise AssertionError(url)
        directory = temp_catalog(models)
        code, lines = capture(lambda: cmd.main(["--catalog-dir", str(directory)], fetch=fetch))
        self.assertEqual(code, 1)
        self.assertEqual(lines, [
            "CHANGED a-change: 1000 → 2110 bytes (+111.0%)",
            "GONE b-gone: bg:2 not in the registry",
            "INVALID c-invalid: unexpected manifest",
            "UNREACHABLE d-unreachable: network unavailable",
            "SAME e-same",
            "5 models · 1 changed · 1 gone · 1 invalid · 1 unreachable · 1 same",
        ])

    def test_invalid_model_without_upstream_id(self):
        directory = temp_catalog([{"catalog_id": "no-id"}])
        code, lines = capture(lambda: cmd.main(["--catalog-dir", str(directory)], fetch=fetch_by_status("ok")))
        self.assertEqual(code, 1)
        self.assertEqual(lines, ["INVALID no-id: no upstream id",
                                 "1 models · 0 changed · 0 gone · 1 invalid · 0 unreachable · 0 same"])

    def test_all_same_exits_zero(self):
        models = [model(catalog_id=f"m-{i}", upstream_id=f"m{i}:1", pinned=OK_DIGEST, pinned_size=OK_SIZE)
                  for i in range(3)]
        directory = temp_catalog(models)
        code, lines = capture(lambda: cmd.main(["--catalog-dir", str(directory)], fetch=fetch_by_status("ok")))
        self.assertEqual(code, 0)
        self.assertEqual(lines, ["SAME m-0", "SAME m-1", "SAME m-2",
                                 "3 models · 0 changed · 0 gone · 0 invalid · 0 unreachable · 3 same"])

    def test_same_plus_unreachable_exits_three(self):
        models = [model(catalog_id="a-ok", upstream_id="a:1", pinned=OK_DIGEST, pinned_size=OK_SIZE),
                  model(catalog_id="b-down", upstream_id="b:1")]
        def fetch(url, headers=None, *a, **k):
            if url.endswith("/a/manifests/1"): return manifest_bytes()
            raise URLError("dns")
        directory = temp_catalog(models)
        code, lines = capture(lambda: cmd.main(["--catalog-dir", str(directory)], fetch=fetch))
        self.assertEqual(code, 3)
        self.assertEqual(lines[-1], "2 models · 0 changed · 0 gone · 0 invalid · 1 unreachable · 1 same")

    def test_missing_and_invalid_models_json_exit_two(self):
        import tempfile
        empty = Path(tempfile.mkdtemp(prefix="nova-drift-empty-"))
        bad = Path(tempfile.mkdtemp(prefix="nova-drift-bad-"))
        (bad / "models.json").write_text("not json")
        for directory in (empty, bad):
            with self.subTest(directory=directory.name):
                code, lines = capture(lambda: cmd.main(["--catalog-dir", str(directory)], fetch=fetch_by_status("ok")))
                self.assertEqual(code, 2)
                self.assertEqual(lines, ["ERROR catalog: cannot read models.json"])


class CommandJsonTests(unittest.TestCase):
    def test_json_shape_and_order(self):
        models = [model(catalog_id="a-same", upstream_id="a:1", pinned=OK_DIGEST, pinned_size=OK_SIZE),
                  model(catalog_id="b-change", upstream_id="b:1", pinned="a" * 64, pinned_size=1000)]
        directory = temp_catalog(models)
        code, lines = capture(lambda: cmd.main(["--catalog-dir", str(directory), "--json"], fetch=fetch_by_status("ok")))
        self.assertEqual(code, 1)
        self.assertEqual(len(lines), 1)
        payload = json.loads(lines[0])
        self.assertEqual(list(payload), ["models", "results"])
        self.assertEqual(payload["models"], 2)
        self.assertEqual([row["catalog_id"] for row in payload["results"]], ["b-change", "a-same"])
        self.assertEqual(payload["results"][0]["verdict"], "changed")


class RealCatalogTests(unittest.TestCase):
    def test_offline_fetch_is_unreachable(self):
        code, lines = capture(lambda: cmd.main([], fetch=fetch_by_status("unreachable")))
        self.assertEqual(code, 3)
        models = json.loads((ROOT.parent / "catalog/models.json").read_text())["models"]
        self.assertEqual(lines[-1],
                         f"{len(models)} models · 0 changed · 0 gone · 0 invalid · {len(models)} unreachable · 0 same")


if __name__ == "__main__":
    unittest.main()
