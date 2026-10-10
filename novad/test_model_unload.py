import io
import json
import unittest
from http import HTTPStatus
from urllib.parse import quote

import server
from server import (
    Handler,
    MODEL_OPERATION_LOCK,
    OllamaProvider,
    ProviderOperationFailed,
    ProviderUnavailable,
    RUNTIME,
)

MODEL = "qwen3.5:27b"
UNLOAD_PATH = "/models/installed/%s/unload" % quote(MODEL, safe="")

class FakeOllama(OllamaProvider):
    def __init__(self):
        super().__init__("http://127.0.0.1:11434", "10m")
        self.loaded = True
        self.vram = 1000
        self.size = 1000
        self.ps_error = None
        self.unload_error = None
        self.unload_calls = []
    def models(self):
        return [{"id": MODEL, "provider": "ollama", "display_name": MODEL, "local": True,
                 "installed": True, "available": True, "capabilities": {"chat": True},
                 "metadata": {"size_bytes": 4 * 10**9}}]
    def running_models(self, timeout=3):
        if self.ps_error: raise self.ps_error
        if not self.loaded: return []
        entry = {"name": MODEL, "size_vram": self.vram}
        if self.size is not None: entry["size"] = self.size
        return [entry]
    def is_loaded(self, model, timeout=3):
        return self.loaded and model == MODEL
    def unload(self, model, timeout=30):
        self.unload_calls.append(model)
        if self.unload_error: raise self.unload_error

class ServerTestBase(unittest.TestCase):
    def setUp(self):
        self.provider = FakeOllama()
        self.save_provider = server.PROVIDERS
        server.PROVIDERS = {"ollama": self.provider}
        self.save_selection = (server.ACTIVE_PROVIDER, server.ACTIVE_MODEL)
        self.save_audit = server.audit
        self.audits = []
        server.audit = lambda event, **fields: self.audits.append((event, fields))
        self.cli_state = (HTTPStatus.OK, {"configured": True, "profiles": []})
        self.save_cli = server.cli_profiles_state
        server.cli_profiles_state = lambda: self.cli_state

    def tearDown(self):
        server.PROVIDERS = self.save_provider
        server.ACTIVE_PROVIDER, server.ACTIVE_MODEL = self.save_selection
        server.audit = self.save_audit
        server.cli_profiles_state = self.save_cli

    def request(self, method: str, path: str, body: bytes | None = None) -> tuple[int, dict]:
        handler = object.__new__(Handler)
        handler.path = path
        handler.command = method
        handler.headers = {"Content-Length": str(len(body) if body else 0)}
        handler.rfile = io.BytesIO(body or b"")
        handler.wfile = io.BytesIO()
        status_holder = [None]
        payload_holder = [None]
        def send_json(status, payload):
            status_holder[0] = status
            payload_holder[0] = payload
            return True
        handler.send_json = send_json
        handler.empty_request_body = lambda: body is None and not handler.headers.get("Transfer-Encoding")
        if method == "GET": Handler.do_GET(handler)
        else: Handler.do_POST(handler)
        if status_holder[0] is None:
            return 500, {"error": "not_handled"}
        return status_holder[0], payload_holder[0]

class GetModelsResidentTest(ServerTestBase):
    def test_get_models_adds_resident_and_vram(self):
        status, payload = self.request("GET", "/models")
        self.assertEqual(status, HTTPStatus.OK)
        entry = next(m for m in payload["models"] if m["id"] == MODEL)
        self.assertIs(entry["resident"], True)
        self.assertEqual(entry["vram_bytes"], 1000)
        self.assertEqual(entry["memory_bytes"], 1000)

    def test_get_models_memory_bytes_is_size_not_vram(self):
        # Vega review: size == vram in the fixture above could not tell the two apart.
        self.provider.size, self.provider.vram = 1500000000, 0
        status, payload = self.request("GET", "/models")
        entry = next(m for m in payload["models"] if m["id"] == MODEL)
        self.assertEqual((entry["memory_bytes"], entry["vram_bytes"]), (1500000000, 0))

    def test_negative_sizes_are_not_reported(self):
        self.provider.size, self.provider.vram = -5, -1
        status, payload = self.request("POST", UNLOAD_PATH)
        self.assertEqual(status, HTTPStatus.OK)
        self.assertIsNone(payload["freed_bytes"])
        self.assertIsNone(payload["freed_vram_bytes"])

    def test_get_models_model_not_loaded(self):
        self.provider.loaded = False
        status, payload = self.request("GET", "/models")
        self.assertEqual(status, HTTPStatus.OK)
        entry = next(m for m in payload["models"] if m["id"] == MODEL)
        self.assertIs(entry["resident"], False)
        self.assertIsNone(entry["vram_bytes"])
        self.assertIsNone(entry["memory_bytes"])

    def test_get_models_ps_failing(self):
        self.provider.ps_error = ProviderUnavailable("ps down")
        status, payload = self.request("GET", "/models")
        self.assertEqual(status, HTTPStatus.OK)
        entry = next(m for m in payload["models"] if m["id"] == MODEL)
        self.assertIsNone(entry["resident"])
        self.assertIsNone(entry["vram_bytes"])
        self.assertIsNone(entry["memory_bytes"])
        self.assertEqual(len(payload["models"]), 1)

class UnloadTest(ServerTestBase):
    def test_unloaded(self):
        status, payload = self.request("POST", UNLOAD_PATH)
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(payload, {"status": "unloaded", "model": MODEL, "freed_bytes": 1000, "freed_vram_bytes": 1000})
        self.assertEqual(self.provider.unload_calls, [MODEL])
        self.assertTrue(any(e == "model_unload" and f.get("model") == MODEL and f.get("status") == "unloaded"
                            for e, f in self.audits))

    def test_not_loaded(self):
        self.provider.loaded = False
        status, payload = self.request("POST", UNLOAD_PATH)
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(payload, {"status": "not_loaded", "model": MODEL})
        self.assertEqual(self.provider.unload_calls, [])

    def test_busy_runtime(self):
        RUNTIME.set("generating")
        try:
            status, payload = self.request("POST", UNLOAD_PATH)
            self.assertEqual(status, HTTPStatus.CONFLICT)
            self.assertEqual(payload, {"status": "busy"})
            self.assertEqual(self.provider.unload_calls, [])
        finally:
            RUNTIME.set("idle")

    def test_busy_lock(self):
        self.assertTrue(MODEL_OPERATION_LOCK.acquire(blocking=False))
        try:
            status, payload = self.request("POST", UNLOAD_PATH)
            self.assertEqual(status, HTTPStatus.CONFLICT)
            self.assertEqual(payload, {"status": "busy"})
            self.assertEqual(self.provider.unload_calls, [])
        finally:
            MODEL_OPERATION_LOCK.release()

    def test_cli_session_running(self):
        self.cli_state = (HTTPStatus.OK, {"profiles": [
            {"kind": "local", "valid": True, "running": True, "model": MODEL}]})
        status, payload = self.request("POST", UNLOAD_PATH)
        self.assertEqual(status, HTTPStatus.CONFLICT)
        self.assertEqual(payload, {"status": "blocked", "blockers": ["CLI_SESSION_RUNNING"]})
        self.assertEqual(self.provider.unload_calls, [])

    def test_cli_state_unknown(self):
        self.cli_state = (HTTPStatus.SERVICE_UNAVAILABLE, {"error": "action_executor_unavailable"})
        status, payload = self.request("POST", UNLOAD_PATH)
        self.assertEqual(status, HTTPStatus.CONFLICT)
        self.assertEqual(payload, {"status": "blocked", "blockers": ["CLI_STATE_UNKNOWN"]})
        self.assertEqual(self.provider.unload_calls, [])

    def test_provider_failure(self):
        self.provider.unload_error = ProviderOperationFailed("no")
        status, payload = self.request("POST", UNLOAD_PATH)
        self.assertEqual(status, HTTPStatus.BAD_GATEWAY)
        self.assertEqual(payload, {"status": "failed", "model": MODEL})

    def test_selected_model_unchanged(self):
        self.request("POST", UNLOAD_PATH)
        self.assertEqual(server.ACTIVE_MODEL, self.save_selection[1])
        self.assertEqual(server.ACTIVE_PROVIDER, self.save_selection[0])

class UnloadedMemoryTest(ServerTestBase):
    """QWEN-16: freed_bytes and freed_vram_bytes report honest memory values."""

    def _unload(self):
        return self.request("POST", UNLOAD_PATH)

    def test_cpu_only(self):
        self.provider.size = 1500000000
        self.provider.vram = 0
        status, payload = self._unload()
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(payload["freed_bytes"], 1500000000)
        self.assertEqual(payload["freed_vram_bytes"], 0)

    def test_gpu(self):
        self.provider.size = 5000000000
        self.provider.vram = 5000000000
        status, payload = self._unload()
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(payload["freed_bytes"], 5000000000)
        self.assertEqual(payload["freed_vram_bytes"], 5000000000)

    def test_partly_offloaded(self):
        self.provider.size = 9000000000
        self.provider.vram = 6000000000
        status, payload = self._unload()
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(payload["freed_bytes"], 9000000000)
        self.assertEqual(payload["freed_vram_bytes"], 6000000000)

    def test_only_size_vram(self):
        self.provider.size = None
        self.provider.vram = 4000000000
        status, payload = self._unload()
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(payload["freed_bytes"], 4000000000)
        self.assertEqual(payload["freed_vram_bytes"], 4000000000)

    def test_neither_valid(self):
        self.provider.size = True
        self.provider.vram = "x"
        status, payload = self._unload()
        self.assertEqual(status, HTTPStatus.OK)
        self.assertIsNone(payload["freed_bytes"])
        self.assertIsNone(payload["freed_vram_bytes"])


class UnloadLockTest(ServerTestBase):
    def test_an_unexpected_error_still_releases_the_model_lock(self):
        # Vega follow-up: a held MODEL_OPERATION_LOCK would block every later model switch.
        def boom(model):
            raise RuntimeError("unexpected")
        original = server.unload_model
        server.unload_model = boom
        try:
            with self.assertRaises(RuntimeError):
                self.request("POST", "/models/installed/qwen3%3A14b/unload")
        finally:
            server.unload_model = original
        self.assertTrue(server.MODEL_OPERATION_LOCK.acquire(blocking=False), "lock leaked")
        server.MODEL_OPERATION_LOCK.release()


class UnloadBadRequestTest(ServerTestBase):
    def test_query_rejected(self):
        status, payload = self.request("POST", UNLOAD_PATH + "?x=1")
        self.assertEqual(status, HTTPStatus.BAD_REQUEST)
        self.assertEqual(payload, {"error": "invalid_model_request"})
        self.assertEqual(self.provider.unload_calls, [])

    def test_body_rejected(self):
        status, payload = self.request("POST", UNLOAD_PATH, body=b"{}")
        self.assertEqual(status, HTTPStatus.BAD_REQUEST)
        self.assertEqual(payload, {"error": "invalid_model_request"})
        self.assertEqual(self.provider.unload_calls, [])

    def test_empty_name_rejected(self):
        status, payload = self.request("POST", "/models/installed//unload")
        self.assertEqual(status, HTTPStatus.BAD_REQUEST)
        self.assertEqual(payload, {"error": "invalid_model_request"})
        self.assertEqual(self.provider.unload_calls, [])

    def test_slash_name_rejected(self):
        status, payload = self.request("POST", "/models/installed/a%2Fb/unload")
        self.assertEqual(status, HTTPStatus.BAD_REQUEST)
        self.assertEqual(payload, {"error": "invalid_model_request"})
        self.assertEqual(self.provider.unload_calls, [])

if __name__ == "__main__":
    unittest.main()
