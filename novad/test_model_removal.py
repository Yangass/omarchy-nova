"""QWEN-11 (done by Vega): delete an installed model from this computer behind an approval."""
import io
import json
import unittest
import urllib.error
from http import HTTPStatus
from unittest import mock

import model_removal
from model_removal import RemovalStore
import providers
from providers import OllamaProvider, ProviderOperationFailed
import server

GIB = 1 << 30
INVENTORY = [{"id": "qwen3:14b", "provider": "ollama", "digest": "d14", "metadata": {"size_bytes": 9 * GIB}},
             {"id": "nova-code-qwen38-96k:latest", "provider": "ollama", "digest": "dnova", "metadata": {"size_bytes": 17 * GIB}}]


def inputs(**changes):
    value = {"inventory": INVENTORY, "selected_model": "nova-code-qwen38-96k:latest", "resident": [], "cli_models": []}
    value.update(changes)
    return value


class ProposeTests(unittest.TestCase):
    def test_each_blocker_alone_and_all_together(self):
        store = RemovalStore()
        cases = [("absent:1b", inputs(), ["NOT_INSTALLED"]),
                 ("nova-code-qwen38-96k", inputs(), ["SELECTED_MODEL"]),  # :latest is the same model
                 ("qwen3:14b", inputs(resident=["qwen3:14b"]), ["LOADED_IN_MEMORY"]),
                 ("qwen3:14b", inputs(cli_models=["qwen3:14b"]), ["CLI_SESSION_RUNNING"]),
                 ("qwen3:14b", inputs(cli_models=None), ["CLI_STATE_UNKNOWN"]),
                 ("qwen3:14b", inputs(resident=None), ["MEMORY_STATE_UNKNOWN"])]
        for model, given, blockers in cases:
            with self.subTest(model=model, blockers=blockers):
                self.assertEqual(store.propose(model, now=0, **given),
                                 {"status": "blocked", "model": model, "blockers": blockers})
        everything = store.propose("qwen3:14b", now=0, **inputs(selected_model="qwen3:14b", resident=["qwen3:14b"],
                                                                   cli_models=["qwen3:14b"]))
        self.assertEqual(everything["blockers"], ["SELECTED_MODEL", "LOADED_IN_MEMORY", "CLI_SESSION_RUNNING"])

    def test_clean_model_is_proposed_with_size_and_digest(self):
        result = RemovalStore().propose("qwen3:14b", now=100, **inputs())
        self.assertEqual(result["status"], "proposed")
        self.assertTrue(result["id"].startswith("del_") and len(result["id"]) > 20)
        self.assertEqual((result["model"], result["size_bytes"], result["digest"], result["expires_at"]),
                         ("qwen3:14b", 9 * GIB, "d14", 220))


class DecideTests(unittest.TestCase):
    def setUp(self):
        self.store = RemovalStore()
        self.id = self.store.propose("qwen3:14b", now=0, **inputs())["id"]
        self.delete = mock.MagicMock()

    def decide(self, decision="approved", before=None, after=None, now=10):
        states = [before or inputs(), after or inputs(inventory=INVENTORY[1:])]
        frees = [100 * GIB, 108 * GIB]  # 8 GiB actually freed (files shared with another model stay)
        return self.store.decide(self.id, decision, recheck=lambda model: states.pop(0), delete=self.delete,
                                 disk_free=lambda: frees.pop(0), now=now)

    def test_deny_deletes_nothing(self):
        self.assertEqual(self.decide("denied"), (202, {"status": "denied"}))
        self.delete.assert_not_called()

    def test_approve_deletes_once_and_reports_freed_space(self):
        self.assertEqual(self.decide(), (200, {"status": "deleted", "model": "qwen3:14b", "freed_bytes": 8 * GIB,
                                                "size_bytes": 9 * GIB, "shared_with": []}))
        self.delete.assert_called_once_with("qwen3:14b")
        # Without a disk measurement NOVA claims nothing about freed space.
        self.store = RemovalStore(); self.id = self.store.propose("qwen3:14b", now=0, **inputs())["id"]
        states = [inputs(), inputs(inventory=INVENTORY[1:])]
        self.assertEqual(self.store.decide(self.id, "approved", recheck=lambda m: states.pop(0), delete=self.delete, now=1)[1]["freed_bytes"], None)

    def test_blocker_or_changed_digest_at_approval_deletes_nothing(self):
        status, body = self.decide(before=inputs(cli_models=["qwen3:14b"]))
        self.assertEqual((status, body), (409, {"status": "blocked", "blockers": ["CLI_SESSION_RUNNING"]}))
        self.store = RemovalStore(); self.id = self.store.propose("qwen3:14b", now=0, **inputs())["id"]
        changed = [dict(INVENTORY[0], digest="other")] + INVENTORY[1:]
        self.assertEqual(self.decide(before=inputs(inventory=changed)), (409, {"status": "blocked", "blockers": ["DIGEST_CHANGED"]}))
        self.delete.assert_not_called()

    def test_still_installed_and_provider_failure_are_failures(self):
        self.assertEqual(self.decide(after=inputs()), (502, {"status": "failed", "error": "still_installed"}))
        self.store = RemovalStore(); self.id = self.store.propose("qwen3:14b", now=0, **inputs())["id"]
        self.delete.side_effect = ProviderOperationFailed("nope")
        self.assertEqual(self.decide(), (502, {"status": "failed", "error": "delete_failed"}))

    def test_reuse_unknown_and_expired(self):
        self.decide("denied")
        self.assertEqual(self.decide(), (409, {"error": "approval_already_used"}))
        self.assertEqual(self.store.decide("del_unknown", "approved", recheck=None, delete=None, now=0),
                         (404, {"error": "unknown_approval"}))
        self.store = RemovalStore(); self.id = self.store.propose("qwen3:14b", now=0, **inputs())["id"]
        self.assertEqual(self.decide(now=121), (410, {"error": "expired_approval"}))
        self.delete.assert_not_called()


class Response:
    def __init__(self, status, body=b""):
        self.status, self.body = status, body
    def read(self, *_):
        return self.body
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False


class ProviderTests(unittest.TestCase):
    def test_delete_sends_exact_request_and_raises_on_failure(self):
        provider = OllamaProvider("http://ollama", "10m")
        with mock.patch.object(providers.urllib.request, "urlopen", return_value=Response(200)) as urlopen:
            provider.delete("qwen3:14b")
        request = urlopen.call_args.args[0]
        self.assertEqual((request.get_method(), request.full_url, json.loads(request.data)),
                         ("DELETE", "http://ollama/api/delete", {"model": "qwen3:14b"}))
        error = urllib.error.HTTPError("http://ollama/api/delete", 404, "not found", {}, io.BytesIO(b"{}"))
        with mock.patch.object(providers.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(ProviderOperationFailed):
                provider.delete("qwen3:14b")

    def test_models_carry_the_digest(self):
        provider = OllamaProvider("http://ollama", "10m")
        tags = {"models": [{"name": "qwen3:14b", "digest": "d14", "size": 9}, {"name": "x:1b"}]}
        with mock.patch.object(provider, "_json", return_value=(200, tags)):
            self.assertEqual([m["digest"] for m in provider.models()], ["d14", None])


class Wire:
    def __init__(self, request):
        self.request, self.response = io.BytesIO(request), bytearray()
    def makefile(self, *args, **kwargs):
        return self.request
    def sendall(self, data):
        self.response.extend(data)


def post(path, body=b"", extra=b""):
    head = f"POST {path} HTTP/1.0\r\nHost: localhost\r\nContent-Length: {len(body)}\r\n".encode() + extra + b"\r\n"
    wire = Wire(head + body)
    with mock.patch("sys.stdout", new=io.StringIO()):
        server.Handler(wire, ("127.0.0.1", 1), None)
    status_line, rest = bytes(wire.response).split(b"\r\n", 1)
    return int(status_line.split()[1]), json.loads(rest.split(b"\r\n\r\n", 1)[1])


class EndpointTests(unittest.TestCase):
    def test_propose_status_mapping(self):
        with mock.patch.object(server, "removal_inputs", return_value=inputs()):
            code, body = post("/models/installed/qwen3%3A14b/remove")
            self.assertEqual((code, body["status"], body["model"]), (201, "proposed", "qwen3:14b"))
            code, body = post("/models/installed/nova-code-qwen38-96k%3Alatest/remove")
            self.assertEqual((code, body["blockers"]), (409, ["SELECTED_MODEL"]))

    def test_bad_requests_never_propose(self):
        with mock.patch.object(server, "removal_inputs") as gather:
            for path, body in (("/models/installed/qwen3%3A14b/remove?x=1", b""),
                               ("/models/installed/qwen3%3A14b/remove", b"{}"),
                               ("/models/installed//remove", b""),
                               ("/models/installed/a%2Fb/remove", b""),
                               ("/models/installed/a/b/remove", b"")):
                with self.subTest(path=path):
                    self.assertEqual(post(path, body), (400, {"error": "invalid_model_request"}))
        gather.assert_not_called()

    def test_approvals_route_removals_and_still_route_acquisitions(self):
        with mock.patch.object(server.REMOVALS, "decide", return_value=(202, {"status": "denied"})) as removal:
            self.assertEqual(post("/approvals/del_abc/deny"), (202, {"status": "denied"}))
        self.assertEqual(removal.call_args.args[:2], ("del_abc", "denied"))
        store = mock.MagicMock()
        store.decide.side_effect = server.LifecycleError("unknown_acquisition", 404)
        with mock.patch.object(server, "acquisition_store", return_value=store):
            self.assertEqual(post("/approvals/acq_abc/deny"), (404, {"error": "unknown_acquisition"}))


if __name__ == "__main__":
    unittest.main()


class SharingTests(unittest.TestCase):
    """2026-10-12: deleting a base model that a tuned model is built on freed 0.0 GiB with no
    explanation. NOVA now says up front what a delete frees and which models keep the files."""
    W, P, LIC = "sha256:" + "a" * 64, "sha256:" + "b" * 64, "sha256:" + "c" * 64

    def records(self):
        return [
            {"id": "qwen3.8:27b-q4_K_M", "layers": {self.W: 16 * GIB, self.P: GIB, "sha256:" + "d" * 64: 500, self.LIC: 11_000}},
            {"id": "nova-code-qwen38-96k:latest", "layers": {self.W: 16 * GIB, self.P: GIB, "sha256:" + "e" * 64: 128, self.LIC: 11_000}},
            {"id": "llama3.2:1b", "layers": {"sha256:" + "f" * 64: GIB, self.LIC: 11_000}},  # shares only a licence file
        ]

    def test_a_model_built_on_another_frees_only_its_own_small_files(self):
        info = model_removal.sharing("qwen3.8:27b-q4_K_M", self.records())
        self.assertEqual(info, {"frees_bytes": 500, "shared_bytes": 17 * GIB + 11_000,
                                "shared_with": ["nova-code-qwen38-96k"]})

    def test_tiny_shared_files_do_not_name_unrelated_models(self):
        info = model_removal.sharing("llama3.2:1b", self.records())
        self.assertEqual((info["frees_bytes"], info["shared_with"]), (GIB, []))

    def test_latest_tag_and_unknown_files(self):
        self.assertEqual(model_removal.sharing("nova-code-qwen38-96k", self.records())["shared_with"], ["qwen3.8:27b-q4_K_M"])
        self.assertIsNone(model_removal.sharing("missing:1b", self.records()))
        unreadable = self.records() + [{"id": "broken:1b", "layers": None}]
        self.assertIsNone(model_removal.sharing("llama3.2:1b", unreadable), "an unreadable model might share files")

    def test_proposal_and_result_carry_the_explanation(self):
        store = model_removal.RemovalStore()
        inputs = {"inventory": [{"id": "qwen3.8:27b-q4_K_M", "digest": "d1", "metadata": {"size_bytes": 17 * GIB}}],
                  "selected_model": None, "resident": [], "cli_models": []}
        shares = model_removal.sharing("qwen3.8:27b-q4_K_M", self.records())
        proposal = store.propose("qwen3.8:27b-q4_K_M", shares=shares, **inputs)
        self.assertEqual((proposal["frees_bytes"], proposal["shared_with"]), (500, ["nova-code-qwen38-96k"]))
        states = [inputs, dict(inputs, inventory=[])]
        status, result = store.decide(proposal["id"], "approved", recheck=lambda _m: states.pop(0),
                                      delete=lambda _m: None, disk_free=iter([100, 100]).__next__)
        self.assertEqual((status, result["freed_bytes"], result["shared_with"]), (200, 0, ["nova-code-qwen38-96k"]))


class InventoryLayersTests(unittest.TestCase):
    def test_local_inventory_lists_each_models_files_only_when_asked(self):
        import tempfile
        from pathlib import Path
        import catalog_observability
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            blobs = {"sha256:" + c * 64: size for c, size in (("1", 2048), ("2", 30), ("3", 10))}
            (root / "blobs").mkdir()
            for digest, size in blobs.items():
                with open(root / "blobs" / digest.replace(":", "-"), "wb") as f: f.truncate(size)
            manifest = {"schemaVersion": 2, "config": {"digest": "sha256:" + "3" * 64, "size": 10},
                        "layers": [{"mediaType": "application/vnd.ollama.image.model", "digest": "sha256:" + "1" * 64, "size": 2048},
                                   {"mediaType": "application/vnd.ollama.image.params", "digest": "sha256:" + "2" * 64, "size": 30}]}
            path = root / "manifests/registry.ollama.ai/library/tiny/1b"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(manifest))
            plain = catalog_observability.local_inventory(root)["records"][0]
            self.assertNotIn("layers", plain)
            record = catalog_observability.local_inventory(root, with_layers=True)["records"][0]
            self.assertEqual((record["id"], record["layers"]), ("tiny:1b", blobs))
