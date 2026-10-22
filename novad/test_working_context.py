"""NOVA runs each model with its working context (2026-10-22: a friend's qwen3:8b ran at
Ollama's 4096 default because NOVA never asked for more)."""
import json
import unittest
from unittest.mock import patch

import providers
import server
from providers import OllamaProvider
from test_provider_chat_features import FakeResponse, stream


def capture(provider, call):
    sent = []
    def urlopen(request, timeout=None):
        url = request if isinstance(request, str) else request.full_url
        body = json.loads(request.data) if not isinstance(request, str) and request.data else None
        if url.endswith("/api/show"):
            return FakeResponse([json.dumps({"capabilities": ["completion", "tools"], "details": {"parameter_size": "8.2B"}}).encode()])
        if url.endswith("/api/ps"):
            return FakeResponse([json.dumps({"models": [{"name": "qwen3:8b"}]}).encode()])
        sent.append((url.rsplit("/", 1)[1], body))
        if body and body.get("messages") == []:
            return FakeResponse([json.dumps({"model": body["model"], "done": True, "done_reason": "load"}).encode()])
        return FakeResponse(stream())
    with patch.object(providers.urllib.request, "urlopen", urlopen):
        call()
    return sent


class ProviderContextTests(unittest.TestCase):
    def provider(self, context):
        provider = OllamaProvider("http://ollama.test", "10m")
        provider.context_for = context
        return provider

    def test_chat_and_load_ask_for_the_same_working_context(self):
        provider = self.provider(lambda model: 32768 if model == "qwen3:8b" else None)
        sent = capture(provider, lambda: (provider.preload("qwen3:8b", 30),
                                          provider.stream_chat("qwen3:8b", [{"role": "user", "content": "hi"}], [])))
        load, chat = [body for _path, body in sent]
        self.assertEqual(load["options"], {"num_ctx": 32768})
        self.assertEqual(chat["options"]["num_ctx"], 32768)
        self.assertIn("num_predict", chat["options"])

    def test_no_context_known_leaves_ollama_and_the_models_own_setting_alone(self):
        for context in (None, lambda model: None, lambda model: 0, lambda model: "32768"):
            provider = self.provider(context)
            sent = capture(provider, lambda: provider.stream_chat("qwen3:8b", [{"role": "user", "content": "hi"}], []))
            self.assertNotIn("num_ctx", sent[0][1]["options"])

    def test_a_failing_context_lookup_never_breaks_chat(self):
        def broken(model): raise RuntimeError("fit unavailable")
        provider = self.provider(broken)
        sent = capture(provider, lambda: provider.stream_chat("qwen3:8b", [{"role": "user", "content": "hi"}], []))
        self.assertNotIn("num_ctx", sent[0][1]["options"])

    def test_unload_sends_no_context(self):
        # Ollama unloads an empty keep_alive 0 chat before it looks at options.
        provider = self.provider(lambda model: 32768)
        def urlopen(request, timeout=None):
            body = json.loads(request.data) if not isinstance(request, str) and request.data else None
            if not isinstance(request, str) and request.full_url.endswith("/api/chat"):
                self.assertNotIn("options", body)
                return FakeResponse([json.dumps({"model": body["model"], "done": True, "done_reason": "unload"}).encode()])
            return FakeResponse([json.dumps({"models": []}).encode()])
        with patch.object(providers.urllib.request, "urlopen", urlopen):
            provider.unload("qwen3:8b")


class ServerContextTests(unittest.TestCase):
    def fit(self, rows):
        server.CHAT_CONTEXTS.clear()
        self.addCleanup(server.CHAT_CONTEXTS.clear)
        return patch.object(server, "local_model_fit", return_value={"models": rows})

    def test_the_fit_recommendation_is_the_working_context(self):
        with self.fit([{"id": "qwen3:8b", "metadata": {"own_context": None}, "fit": {"recommended_context": 32768}}]):
            self.assertEqual(server.chat_context("qwen3:8b"), 32768)

    def test_a_tailored_build_keeps_its_own_context(self):
        # Konstantin's nova-code-qwen38-96k sets num_ctx 98304 for the coding CLI; forcing
        # another size would reload it between chat and the CLI.
        with self.fit([{"id": "nova-code-qwen38-96k:latest", "metadata": {"own_context": 98304},
                        "fit": {"recommended_context": 32768}}]):
            self.assertIsNone(server.chat_context("nova-code-qwen38-96k"))

    def test_untagged_names_match_latest(self):
        with self.fit([{"id": "llama3.2:latest", "metadata": {}, "fit": {"recommended_context": 16384}}]):
            self.assertEqual(server.chat_context("llama3.2"), 16384)

    def test_unknown_or_unfit_models_leave_the_default(self):
        with self.fit([{"id": "qwen3:14b", "metadata": {}, "fit": {"recommended_context": None}}]):
            self.assertIsNone(server.chat_context("qwen3:14b"))
            self.assertIsNone(server.chat_context("not-installed:1b"))

    def test_the_provider_asks_novad(self):
        self.assertIs(server.PROVIDERS["ollama"].context_for, server.chat_context)


class OwnContextTests(unittest.TestCase):
    def test_fit_models_reads_a_modelfiles_own_num_ctx(self):
        provider = OllamaProvider("http://ollama.test", "10m")
        tags = {"models": [{"name": "custom:latest", "model": "custom:latest", "size": 1000, "details": {}}]}
        show = {"model_info": {"general.architecture": "qwen3"}, "capabilities": ["completion"],
                "parameters": "stop \"<|im_end|>\"\nnum_ctx                        98304\ntemperature 0.6"}
        def urlopen(request, timeout=None):
            url = request if isinstance(request, str) else request.full_url
            return FakeResponse([json.dumps(show if url.endswith("/api/show") else tags).encode()])
        with patch.object(providers.urllib.request, "urlopen", urlopen):
            models = provider.fit_models()
        self.assertEqual([m["metadata"]["own_context"] for m in models], [98304])


if __name__ == "__main__":
    unittest.main()
