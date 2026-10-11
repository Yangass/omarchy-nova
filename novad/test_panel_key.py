"""Every request the panel and the bar make to NOVA goes through the keyed opener
(marketplace review 2026-10-11: forged browser requests must not drive NOVA)."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PANEL = (ROOT / "Panel.qml").read_text()
BAR = (ROOT / "BarWidget.qml").read_text()


class PanelKey(unittest.TestCase):
    def test_panel_opens_requests_only_through_open_nova(self):
        opener = PANEL[PANEL.index("function openNova("):]
        opener = opener[:opener.index("\n  }\n")]
        self.assertEqual(PANEL.count("xhr.open("), opener.count("xhr.open("), "a request bypasses openNova")
        self.assertIn('xhr.setRequestHeader("X-Nova-Key", novaKey)', opener)
        self.assertGreaterEqual(PANEL.count("root.openNova(xhr, "), 28)

    def test_bar_reads_the_private_key_file_and_sends_it(self):
        self.assertIn('"/.config/nova/api.token"', BAR)
        self.assertEqual(BAR.count("xhr.open("), 1)
        self.assertIn('xhr.setRequestHeader("X-Nova-Key", apiKey)', BAR)

    def test_the_header_name_matches_the_services(self):
        for path in ("novad/server.py", "nova-actions/server.py", "nova-system-tools/server.py"):
            self.assertIn('API_KEY_HEADER = "X-Nova-Key"', (ROOT / path).read_text())


if __name__ == "__main__":
    unittest.main()
