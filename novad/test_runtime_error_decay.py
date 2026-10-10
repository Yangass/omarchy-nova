"""A failed reply's error state clears on its own (walkthrough run 2)."""
import unittest
from unittest.mock import patch

import server


class RuntimeErrorDecayTests(unittest.TestCase):
    def test_error_shows_briefly_then_reads_idle(self):
        runtime = server.Runtime()
        with patch.object(server.time, "time", return_value=1000.0):
            runtime.set("error")
            self.assertEqual(runtime.snapshot()["state"], "error")
        with patch.object(server.time, "time", return_value=1000.0 + server.CHAT_ERROR_DISPLAY_SECONDS + 1):
            self.assertEqual(runtime.snapshot()["state"], "idle")

    def test_model_switch_error_is_not_decayed(self):
        runtime = server.Runtime()
        with patch.object(server.time, "time", return_value=1000.0):
            runtime.set("model_switch_error", "m:1")
        with patch.object(server.time, "time", return_value=5000.0):
            self.assertEqual(runtime.snapshot()["state"], "model_switch_error")


if __name__ == "__main__":
    unittest.main()
