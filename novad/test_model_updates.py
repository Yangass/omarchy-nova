"""Model updates: weekly check, what changed, exact download size, guard, skip (no network)."""
import contextlib
import json
import os
import stat
import tempfile
import unittest
import urllib.error
from unittest import mock
from pathlib import Path

import model_updates
import registry_probe
import server
from acquisition_lifecycle import AcquisitionLifecycle
from test_acquisition_integration import Wire, response
from test_acquisition_lifecycle import fixture as lifecycle_fixture
from test_requested_models import FakeRegistry, _gguf


def installed_record(fake, *, match="exact"):
    """The catalog record of the build on disk, as catalog_view presents it."""
    found = registry_probe.probe("llama3.2:1b", fetch=fake)
    found = dict(found, architecture="llama", quantization="Q8_0")
    record, _ = registry_probe.records(found, [], None, "2026-10-01T00:00:00Z")
    record["installed_match"] = {"match_status": match, "installed_digest": found["manifest_sha256"],
                                 "installed_model_id": "llama3.2:1b"}
    return record


def write_local(root, fake):
    """Put the fake registry's manifest and same-sized blobs where Ollama keeps them."""
    manifest = dict(fake.manifest, schemaVersion=2)
    path = Path(root) / "manifests/registry.ollama.ai/library/llama3.2/1b"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest))
    (Path(root) / "blobs").mkdir(exist_ok=True)
    for layer in [*manifest["layers"], manifest["config"]]:
        with open(Path(root) / "blobs" / layer["digest"].replace(":", "-"), "wb") as blob:
            blob.truncate(layer["size"])


def gone(url, headers=None, limit=None):
    raise urllib.error.HTTPError(url, 404, "not found", {}, None)


def offline(url, headers=None, limit=None):
    raise urllib.error.URLError("offline")


class CheckRecord(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.old = FakeRegistry(_gguf())
        write_local(self.root, self.old)
        self.record = installed_record(self.old)

    def tearDown(self): self.tmp.cleanup()

    def probed(self, live, arch="llama", quant="Q8_0"):
        """The registry's GGUF answer for the live build (the shared fake has no architecture metadata)."""
        found = dict(registry_probe.probe("llama3.2:1b", fetch=live), architecture=arch, quantization=quant)
        return mock.patch.object(model_updates.registry_probe, "probe", return_value=found)

    def check(self, live, **kw):
        return model_updates.check_record(self.record, fetch=live, digest_fetch=live, root=self.root,
                                          observed="2026-10-10T00:00:00Z", **kw)

    def test_same_build_is_current_and_reads_only_the_manifest(self):
        live = FakeRegistry(_gguf())
        result = self.check(live)
        self.assertEqual(result["status"], "current")
        self.assertTrue(all("/manifests/" in url for url, _, _ in live.requests))

    def test_new_template_is_a_settings_update_with_an_exact_small_download(self):
        live = FakeRegistry(_gguf(), template="{{ .Prompt }} new")
        result = self.check(live)
        self.assertEqual((result["status"], result["kind"]), ("available", "settings"))
        self.assertEqual(result["download_bytes"], len("{{ .Prompt }} new"))
        self.assertTrue(result["download_exact"])
        self.assertEqual(result["installed_revision"], self.record["installed_match"]["installed_digest"])
        self.assertTrue(all("/manifests/" in url for url, _, _ in live.requests), "settings-only change must not probe weights")

    def test_new_weights_within_the_guard_are_a_weights_update(self):
        live = FakeRegistry(_gguf(tensors=((4096, 1000), (4096, 4097))))
        with self.probed(live): result = self.check(live)
        self.assertEqual((result["status"], result["kind"]), ("available", "weights"))
        self.assertGreater(result["download_bytes"], 4096 * 4096)  # the model layer is new
        self.assertEqual(result["size_after"], sum(l["size"] for l in [*live.manifest["layers"], live.manifest["config"]]))

    def test_a_different_model_under_the_name_is_not_offered(self):
        live = FakeRegistry(_gguf(tensors=((4096, 1000), (4096, 4098))))
        with self.probed(live, arch="qwen3"): result = self.check(live)
        self.assertEqual(result["status"], "changed_too_much")
        self.assertIn("architecture_changed", result["reasons"])

    def test_local_layers_never_reads_a_path_from_a_name(self):
        for name in ("../../etc:passwd", "llama3.2/../x:1b", "a..b:1b", "user/model:tag", None):
            with self.subTest(name=name):
                self.assertIsNone(model_updates.local_layers(name, self.root))
        self.assertIsNotNone(model_updates.local_layers("llama3.2:1b", self.root))

    def test_gone_and_offline(self):
        self.assertEqual(self.check(gone)["status"], "gone")
        result = self.check(offline)
        self.assertEqual((result["status"], result["reason"]), ("unchecked", "registry_unreachable"))

    def test_unreadable_local_store_still_offers_with_the_full_size(self):
        live = FakeRegistry(_gguf(), template="changed")
        with self.probed(live):
            result = model_updates.check_record(self.record, fetch=live, digest_fetch=live,
                                                root=self.root + "/missing", observed="2026-10-10T00:00:00Z")
        self.assertEqual((result["status"], result["kind"], result["download_exact"]), ("available", "weights", False))
        self.assertEqual(result["download_bytes"], result["size_after"])


class StateAndView(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root, self.path = self.tmp.name + "/ollama", self.tmp.name + "/state/model-updates.json"
        self.old = FakeRegistry(_gguf())
        write_local(self.root, self.old)
        self.record = installed_record(self.old)
        self.live = FakeRegistry(_gguf(), template="new")

    def tearDown(self): self.tmp.cleanup()

    def run_check(self, records=None, live=None, now=1000.0):
        live = live or self.live
        return model_updates.check_all(records or [self.record], path=self.path, now=now, fetch=live,
                                       digest_fetch=live, root=self.root, observed="2026-10-10T00:00:00Z")

    def test_weekly_and_retry_schedule(self):
        state = model_updates.empty_state()
        self.assertTrue(model_updates.due(state, 1000))
        state = self.run_check()
        self.assertFalse(model_updates.due(state, 1000 + 6 * 86400))
        self.assertTrue(model_updates.due(state, 1000 + 7 * 86400))
        failed = self.run_check(live=offline)
        self.assertTrue(failed["failed"])
        self.assertTrue(model_updates.due(failed, 1000 + 86400))

    def test_state_file_is_private_and_not_installed_models_are_skipped(self):
        other = dict(self.record, catalog_id="other", installed_match={"match_status": "not_installed"})
        state = self.run_check([self.record, other])
        self.assertEqual(list(state["results"]), [self.record["catalog_id"]])
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(model_updates.load(self.path)["results"], state["results"])

    def test_view_counts_updates_by_model_id_and_clears_once_installed(self):
        state = self.run_check()
        view = model_updates.view(state, [self.record])
        self.assertEqual(view["available"], 1)
        self.assertEqual(view["models"]["llama3.2:1b"]["status"], "available")
        updated = dict(self.record, installed_match=dict(self.record["installed_match"],
                       installed_digest=state["results"][self.record["catalog_id"]]["revision"]))
        view = model_updates.view(state, [updated])
        self.assertEqual((view["available"], view["models"]["llama3.2:1b"]["status"]), (0, "current"))

    def test_skip_hides_only_that_build(self):
        self.run_check()
        state = model_updates.set_skipped(self.record["catalog_id"], True, self.path)
        view = model_updates.view(state, [self.record])
        self.assertEqual(view["available"], 0)
        self.assertTrue(view["models"]["llama3.2:1b"]["skipped"])
        # A newer build than the skipped one is offered again.
        state = self.run_check(live=FakeRegistry(_gguf(), template="newer still"))
        self.assertEqual(model_updates.view(state, [self.record])["available"], 1)
        self.assertEqual(state["skipped"], {})
        self.assertIsNone(model_updates.set_skipped("unknown", True, self.path))

    def test_show_again(self):
        self.run_check()
        model_updates.set_skipped(self.record["catalog_id"], True, self.path)
        state = model_updates.set_skipped(self.record["catalog_id"], False, self.path)
        self.assertEqual(model_updates.view(state, [self.record])["available"], 1)

    def test_corrupt_state_reads_as_empty(self):
        Path(self.path).parent.mkdir(parents=True)
        Path(self.path).write_text("{not json")
        self.assertEqual(model_updates.load(self.path), model_updates.empty_state())


class Http(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = AcquisitionLifecycle(Path(self.tmp.name) / "acq.json", clock=lambda: 1000)

    def call(self, method, path, **patches):
        wire = Wire(f"{method} {path} HTTP/1.0\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n".encode())
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(server, "ACQUISITIONS", self.store))
            stack.enter_context(mock.patch.object(server, "catalog_view", return_value={"models": []}))
            stack.enter_context(mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.tmp.name}))
            for name, value in patches.items(): stack.enter_context(mock.patch.object(server, name, value))
            server.Handler(wire, ("127.0.0.1", 1), None)
        return response(wire)

    def test_get_starts_a_due_check_and_refresh_forces_one(self):
        with mock.patch.object(server, "start_model_update_check", return_value=True) as start:
            code, body = self.call("GET", "/models/updates")
            self.assertEqual((code, body["checking"], body["available"]), (200, True, 0))
            self.assertEqual(start.call_args.args, (False,))
            code, body = self.call("POST", "/models/updates/check")
            self.assertEqual((code, start.call_args.args), (202, (True,)))

    def test_skip_needs_an_offered_update(self):
        code, body = self.call("POST", "/models/updates/llama3-2-1b-q8-0/skip")
        self.assertEqual((code, body["error"]), (404, "no_update_offered"))
        self.assertEqual(self.call("POST", "/models/updates/a/b/skip")[0], 400)

    def test_propose_update_asks_for_an_update_preflight(self):
        calls = []
        def preflight(catalog_id, allow_update=False):
            calls.append(allow_update)
            return dict(lifecycle_fixture(), intent="update", replaces_revision="b" * 64)
        code, body = self.call("POST", "/acquisition/models/fixture/propose-update",
                               refresh_shipped_pin=lambda _: {"status": "unchanged"}, local_acquisition_preflight=preflight)
        self.assertEqual((code, calls, body["preflight"]["intent"]), (201, [True], "update"))
        code, body = self.call("POST", "/acquisition/models/fixture/propose-update",
                               refresh_shipped_pin=lambda _: {"status": "gone", "upstream_id": "x:y"})
        self.assertEqual((code, body["error"]), (409, "model_gone_upstream"))

    def test_approval_rechecks_with_the_same_intent(self):
        seen = []
        def preflight(catalog_id, allow_update=False):
            seen.append(allow_update)
            return dict(lifecycle_fixture(), intent="update", replaces_revision="b" * 64)
        identifier = self.store.propose(preflight("fixture", True))["id"]
        with mock.patch.object(server, "ACQUISITIONS", self.store), mock.patch.object(server, "local_acquisition_preflight", preflight), \
             mock.patch.object(server, "Thread"):
            status, body = server.APPROVALS.decide(identifier, "approved")
        self.assertEqual((status, body["state"], seen[-1]), (202, "APPROVED", True))


if __name__ == "__main__":
    unittest.main()
