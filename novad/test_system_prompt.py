"""The chat system prompt says what NOVA is and which local model answers (2026-10-10)."""
import unittest

import server


class SystemPromptTests(unittest.TestCase):
    def test_identity_views_privacy_and_active_model(self):
        prompt = server.system_prompt("qwen3:8b")
        for fact in ("plugin for Omarchy", "Konstantin Tarasevic", "CHAT", "MODELS", "MACHINE", "ADD A MODEL",
                     "through Ollama", "never go to a cloud service", "running on the local model qwen3:8b"):
            self.assertIn(fact, prompt)
        self.assertTrue(prompt.endswith(server.SYSTEM_INSTRUCTIONS))

    def test_chat_uses_it(self):
        self.assertIn("system_prompt(model_id)", open(server.__file__).read())


if __name__ == "__main__":
    unittest.main()
