"""0.16.1: a model loaded as its coding CLI build is loaded. A tailored build and its model
share one loaded copy (same files, same context), and Ollama's /api/ps names it after
whichever loaded it (Ollama 0.40.7, checked live 2026-10-23). While OpenCode ran, only its
build was listed, so NOVA said the chat model wasn't loaded and offered LOAD AND VERIFY
(Konstantin's friend), and MACHINE showed nothing in GPU memory (the panel hides builds).
"""
import io
import unittest
from http import HTTPStatus
from unittest import mock

import onboarding_integration as oi
import server
from server import OllamaProvider

BASE, BUILD = "qwen3:8b", "nova-qwen3-8b-cli"
ROW = {"name": BUILD + ":latest", "model": BUILD + ":latest", "size_vram": 7_000, "size": 7_500}


class Fake(OllamaProvider):
    def __init__(self, running):
        super().__init__("http://127.0.0.1:11434", "10m")
        self.running, self.unloaded = running, []
        self.builds_of = lambda model: [BUILD] if model == BASE else []
    def running_models(self, timeout=3): return list(self.running)
    def models(self):
        return [{"id": BASE, "provider": "ollama", "display_name": BASE, "local": True, "installed": True, "available": True,
                 "capabilities": {"chat": True}, "metadata": {"size_bytes": 5 * 10**9}}]
    def unload(self, model, timeout=30):
        self.unloaded.append(model); self.running = []


class CopyNames(unittest.TestCase):
    def test_a_model_answers_from_its_build_s_copy(self):
        provider = Fake([ROW])
        self.assertEqual(provider.copy_names(BASE), {BASE, BUILD, BUILD + ":latest"})
        self.assertIs(provider.resident_copy(BASE, [ROW]), ROW)
        self.assertIsNone(provider.resident_copy("llama3.2:1b", [ROW]))

    def test_its_own_row_comes_first(self):
        own = {"name": BASE, "model": BASE}
        self.assertIs(Fake([]).resident_copy(BASE, [ROW, own]), own)

    def test_without_builds_only_its_own_name(self):
        provider = OllamaProvider("http://127.0.0.1:11434", "10m")
        self.assertEqual(provider.copy_names(BASE), {BASE})
        self.assertIsNone(provider.resident_copy(BASE, [ROW]))


class Server(unittest.TestCase):
    def setUp(self):
        self.provider = Fake([ROW])
        patches = [mock.patch.object(server, "PROVIDERS", {"ollama": self.provider}),
                   mock.patch.object(server, "ACTIVE_PROVIDER", "ollama"), mock.patch.object(server, "ACTIVE_MODEL", BASE),
                   mock.patch.object(server, "audit", lambda *a, **k: None),
                   mock.patch.object(server, "cli_profiles_state", lambda: (HTTPStatus.OK, {"configured": True, "profiles": []}))]
        for patch in patches: patch.start(); self.addCleanup(patch.stop)

    def test_the_bar_sees_the_chat_model_ready(self):
        self.assertTrue(server.active_model_state()["runtime_ready"])
        self.provider.running = []
        self.assertFalse(server.active_model_state()["runtime_ready"])

    def test_models_marks_it_in_memory_as_its_build(self):
        handler = object.__new__(server.Handler)
        handler.path, handler.command, handler.headers = "/models", "GET", {"Content-Length": "0"}
        handler.rfile, handler.wfile = io.BytesIO(), io.BytesIO()
        sent = []
        handler.send_json = lambda status, payload: sent.append((status, payload)) or True
        with mock.patch.object(server, "model_sharing", lambda: {}), mock.patch.object(server, "local_inventory", lambda **k: {"records": []}):
            server.Handler.do_GET(handler)
        entry = next(item for item in sent[0][1]["models"] if item["id"] == BASE)
        self.assertEqual((entry["resident"], entry["vram_bytes"], entry["memory_bytes"], entry["resident_as"]), (True, 7_000, 7_500, BUILD + ":latest"))

    def test_unload_frees_the_copy_under_the_build_s_name(self):
        status, payload = server.unload_model(BASE)
        self.assertEqual(self.provider.unloaded, [BUILD + ":latest"])
        self.assertNotEqual(payload.get("status"), "not_loaded")


class Onboarding(unittest.TestCase):
    def test_the_selected_model_is_ready_not_needing_a_load(self):
        config = mock.Mock(state="VALID", model=BASE)
        inventory = mock.Mock(state="KNOWN", installed_model_ids=(BASE,))
        provider = Fake([ROW])
        self.assertTrue(oi._selection(config, inventory, [ROW], True, provider.copy_names).runtime_ready)
        self.assertFalse(oi._selection(config, inventory, [ROW], True).runtime_ready)  # the old view
        self.assertFalse(oi._selection(config, inventory, [], True, provider.copy_names).runtime_ready)


if __name__ == "__main__":
    unittest.main()
