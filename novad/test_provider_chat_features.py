"""Chat requests only ask Ollama for features the model declares (walkthrough run 2).

A fresh user's llama3.2:1b has no thinking mode; sending think=true made every
chat fail with "Ollama stream failed".
"""
import io
import json
import unittest
import urllib.error
from unittest.mock import patch

import providers
from providers import OllamaProvider


class FakeResponse:
    def __init__(self, lines, status=200):
        self.lines, self.status = lines, status
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __iter__(self): return iter(self.lines)
    def read(self, n=-1): return b"".join(self.lines)


def stream(text="Hello!"):
    return [json.dumps({"message": {"content": text}}).encode() + b"\n",
            json.dumps({"message": {}, "done": True}).encode() + b"\n"]


class ChatFeatureTests(unittest.TestCase):
    def run_chat(self, capabilities, reject=None, parameter_size="8.0B"):
        sent = []
        def urlopen(request, timeout=None):
            url = request if isinstance(request, str) else request.full_url
            body = json.loads(request.data) if not isinstance(request, str) and request.data else None
            if url.endswith("/api/show"):
                if capabilities is None: raise urllib.error.URLError("down")
                return FakeResponse([json.dumps({"capabilities": capabilities, "details": {"parameter_size": parameter_size}}).encode()])
            sent.append(body)
            if reject and len(sent) == 1:
                raise urllib.error.HTTPError(url, 400, "bad", {}, io.BytesIO(json.dumps({"error": reject}).encode()))
            return FakeResponse(stream())
        provider = OllamaProvider("http://ollama.test", "5m")
        with patch.object(providers.urllib.request, "urlopen", urlopen):
            result = provider.stream_chat("m:1", [{"role": "user", "content": "hi"}], [{"type": "function"}])
        return result, sent

    def test_model_without_thinking_gets_no_think_flag(self):
        result, [payload] = self.run_chat(["completion", "tools"])
        self.assertEqual(result["content"], "Hello!")
        self.assertNotIn("think", payload)
        self.assertEqual(payload["tools"], [{"type": "function"}])

    def test_thinking_model_keeps_think_and_tools(self):
        _result, [payload] = self.run_chat(["completion", "thinking", "tools"])
        self.assertIs(payload["think"], True)
        self.assertIn("tools", payload)

    def test_model_without_tools_gets_no_tools(self):
        _result, [payload] = self.run_chat(["completion"])
        self.assertNotIn("tools", payload)
        self.assertNotIn("think", payload)

    def test_unknown_capabilities_omit_think_but_keep_tools(self):
        _result, [payload] = self.run_chat(None)
        self.assertNotIn("think", payload)
        self.assertIn("tools", payload)

    def test_feature_rejection_retries_once_without_optional_features(self):
        result, sent = self.run_chat(None, reject='"m:1" does not support tools')
        self.assertEqual(result["content"], "Hello!")
        self.assertEqual(len(sent), 2)
        self.assertIn("tools", sent[0])
        self.assertNotIn("tools", sent[1]); self.assertNotIn("think", sent[1])

    def test_tiny_model_chats_without_tools(self):
        # Run 2: llama3.2:1b given 15 tool schemas wrote an endless buffered tool call.
        result, [payload] = self.run_chat(["completion", "tools"], parameter_size="1.2B")
        self.assertEqual(result["content"], "Hello!")
        self.assertNotIn("tools", payload)

    def test_model_without_tools_is_told_it_cannot_act(self):
        sent = []
        def urlopen(request, timeout=None):
            url = request if isinstance(request, str) else request.full_url
            if url.endswith("/api/show"):
                return FakeResponse([json.dumps({"capabilities": ["completion"], "details": {"parameter_size": "1.2B"}}).encode()])
            sent.append(json.loads(request.data)); return FakeResponse(stream())
        with patch.object(providers.urllib.request, "urlopen", urlopen):
            OllamaProvider("http://ollama.test", "5m").stream_chat(
                "m:1", [{"role": "system", "content": "You are NOVA."}, {"role": "user", "content": "open a terminal"}], [{"type": "function"}])
        [payload] = sent
        self.assertNotIn("tools", payload)
        self.assertEqual(payload["messages"][0]["content"], "You are NOVA.\n\n" + providers.NO_TOOLS_NOTE)
        self.assertEqual(payload["messages"][1], {"role": "user", "content": "open a terminal"})

    def test_model_with_tools_gets_no_note(self):
        _result, [payload] = self.run_chat(["completion", "tools"])
        self.assertIn("tools", payload)
        self.assertNotIn(providers.NO_TOOLS_NOTE, json.dumps(payload["messages"]))

    def test_feature_rejection_retry_adds_the_note(self):
        _result, sent = self.run_chat(["completion", "tools"], reject="registry.ollama.ai/library/m does not support tools")
        self.assertNotIn("tools", sent[1])
        self.assertIn(providers.NO_TOOLS_NOTE, sent[1]["messages"][0]["content"])

    def test_every_reply_has_a_length_cap(self):
        _result, [payload] = self.run_chat(["completion", "tools"])
        self.assertEqual(payload["options"]["num_predict"], providers.MAX_REPLY_TOKENS)

    def test_thinking_models_get_room_to_think(self):
        # 2026-10-05: Qwen 27B thought past 4096 tokens and returned no answer.
        _result, [payload] = self.run_chat(["completion", "thinking", "tools"])
        self.assertEqual(payload["options"]["num_predict"], providers.MAX_THINKING_REPLY_TOKENS)
        self.assertGreater(providers.MAX_THINKING_REPLY_TOKENS, 4 * providers.MAX_REPLY_TOKENS - 1)

    def test_budget_spent_thinking_says_so(self):
        provider = OllamaProvider("http://ollama.test", "5m")
        lines = [json.dumps({"message": {"thinking": "hmm"}}).encode() + b"\n",
                 json.dumps({"message": {}, "done": True, "done_reason": "length"}).encode() + b"\n"]
        def urlopen(request, timeout=None):
            url = request if isinstance(request, str) else request.full_url
            if url.endswith("/api/show"): return FakeResponse([json.dumps({"capabilities": ["completion", "thinking"]}).encode()])
            return FakeResponse(lines)
        with patch.object(providers.urllib.request, "urlopen", urlopen):
            with self.assertRaises(providers.ProviderUnavailable) as caught:
                provider.stream_chat("m:1", [], [])
        self.assertIn("reply budget thinking", str(caught.exception))

    def test_parameter_billions(self):
        self.assertEqual(providers.parameter_billions("1.2B"), 1.2)
        self.assertEqual(providers.parameter_billions("500M"), 0.5)
        self.assertIsNone(providers.parameter_billions("big"))
        self.assertIsNone(providers.parameter_billions(None))

    def test_other_errors_still_fail_with_detail(self):
        provider = OllamaProvider("http://ollama.test", "5m")
        def urlopen(request, timeout=None):
            url = request if isinstance(request, str) else request.full_url
            if url.endswith("/api/show"): return FakeResponse([json.dumps({"capabilities": ["completion"]}).encode()])
            raise urllib.error.HTTPError(url, 500, "boom", {}, io.BytesIO(b'{"error":"model runner crashed"}'))
        with patch.object(providers.urllib.request, "urlopen", urlopen):
            with self.assertRaises(providers.ProviderUnavailable) as caught:
                provider.stream_chat("m:1", [], [])
        self.assertIn("model runner crashed", str(caught.exception))


if __name__ == "__main__":
    unittest.main()


class VersionTests(unittest.TestCase):
    """UPD-01: NOVA needs an Ollama that reports model capabilities (0.6.5+)."""
    def test_parse_version(self):
        self.assertEqual(providers.parse_version("0.33.3"), (0, 33, 3))
        self.assertEqual(providers.parse_version("0.6.5-rc1"), (0, 6, 5))
        for bad in (None, "", "0.6", "v0.6.5", "0.6.x", 65):
            self.assertIsNone(providers.parse_version(bad), bad)
        self.assertLess(providers.parse_version("0.6.4"), providers.MIN_OLLAMA_VERSION)
        self.assertFalse(providers.parse_version("0.10.0") < providers.MIN_OLLAMA_VERSION)

    def test_provider_reads_api_version(self):
        def urlopen(url, timeout=None):
            self.assertTrue(url.endswith("/api/version"))
            return FakeResponse([b'{"version":"0.33.3"}'])
        with patch.object(providers.urllib.request, "urlopen", urlopen):
            self.assertEqual(OllamaProvider("http://ollama.test", "5m").version(), (0, 33, 3))
        def down(url, timeout=None): raise urllib.error.URLError("down")
        with patch.object(providers.urllib.request, "urlopen", down):
            self.assertIsNone(OllamaProvider("http://ollama.test", "5m").version())
