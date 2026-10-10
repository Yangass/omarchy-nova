import json
import tempfile
import unittest
from pathlib import Path

import server
from providers import ProviderOperationFailed, ProviderTimeout


class FakeProvider:
    def __init__(self, *, fail=None, loaded=None):
        self.loaded = {"old"} if loaded is None else set(loaded)
        self.fail = fail

    def models(self):
        return [{"id": name, "available": True} for name in ("old", "target")]

    def is_loaded(self, model, timeout=3):
        return model in self.loaded

    def unload(self, model, timeout=30):
        self.loaded.discard(model)

    def preload(self, model, timeout):
        if model == "target" and self.fail:
            raise self.fail("load failed")
        self.loaded.add(model)
        return {"model": model, "done": True, "done_reason": "load", "load_duration": 1}


class ModelSwitchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_config = server.CONFIG_PATH
        self.old_providers = server.PROVIDERS
        server.CONFIG_PATH = Path(self.temp.name) / "model.json"
        server.ACTIVE_PROVIDER, server.ACTIVE_MODEL = "ollama", "old"
        server.RUNTIME.set("idle")

    def tearDown(self):
        server.CONFIG_PATH = self.old_config
        server.PROVIDERS = self.old_providers
        server.ACTIVE_PROVIDER, server.ACTIVE_MODEL = "ollama", "old"
        server.RUNTIME.set("idle")
        self.temp.cleanup()

    def test_verified_commit_unloads_old_and_persists_target(self):
        fake = FakeProvider()
        server.PROVIDERS = {"ollama": fake}
        status, result = server.select_model("ollama", "target")
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "model_ready")
        self.assertEqual(fake.loaded, {"target"})
        self.assertEqual(json.loads(server.CONFIG_PATH.read_text())["active_model"], "target")

    def test_failed_load_rolls_back_resident_old_model(self):
        fake = FakeProvider(fail=ProviderOperationFailed)
        server.PROVIDERS = {"ollama": fake}
        original = {"active_provider": "ollama", "active_model": "old"}
        server.CONFIG_PATH.write_text(json.dumps(original))
        status, result = server.select_model("ollama", "target")
        self.assertEqual(status, 503)
        self.assertEqual(result["rollback"], "rollback_complete")
        self.assertEqual(server.ACTIVE_MODEL, "old")
        self.assertEqual(fake.loaded, {"old"})
        self.assertEqual(json.loads(server.CONFIG_PATH.read_text()), original)

    def test_failed_first_activation_preserves_no_selection(self):
        fake = FakeProvider(fail=ProviderOperationFailed, loaded=())
        server.PROVIDERS = {"ollama": fake}
        server.ACTIVE_PROVIDER, server.ACTIVE_MODEL = None, None
        status, result = server.select_model("ollama", "target")
        self.assertEqual(status, 503)
        self.assertEqual(result["rollback"], "rollback_complete")
        self.assertIsNone(server.ACTIVE_PROVIDER)
        self.assertIsNone(server.ACTIVE_MODEL)
        self.assertEqual(fake.loaded, set())
        self.assertFalse(server.CONFIG_PATH.exists())

    def test_timeout_is_bounded_error_and_rolls_back(self):
        fake = FakeProvider(fail=ProviderTimeout)
        server.PROVIDERS = {"ollama": fake}
        status, result = server.select_model("ollama", "target")
        self.assertEqual(status, 504)
        self.assertEqual(result["error"], "model_switch_timeout")
        self.assertEqual(fake.loaded, {"old"})

    def test_stale_installed_target_is_rejected_without_changing_selection(self):
        fake = FakeProvider()
        server.PROVIDERS = {"ollama": fake}
        original = {"active_provider": "ollama", "active_model": "old"}
        server.CONFIG_PATH.write_text(json.dumps(original))
        status, result = server.select_model("ollama", "removed:latest")
        self.assertEqual((status, result["error"]), (404, "model_not_installed"))
        self.assertEqual(server.ACTIVE_MODEL, "old")
        self.assertEqual(fake.loaded, {"old"})
        self.assertEqual(json.loads(server.CONFIG_PATH.read_text()), original)

    def test_concurrent_operation_is_rejected(self):
        server.PROVIDERS = {"ollama": FakeProvider()}
        server.MODEL_OPERATION_LOCK.acquire()
        try:
            status, result = server.select_model("ollama", "target")
        finally:
            server.MODEL_OPERATION_LOCK.release()
        self.assertEqual(status, 409)
        self.assertEqual(result["error"], "busy")


if __name__ == "__main__":
    unittest.main()
