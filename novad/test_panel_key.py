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


class EveryCallerSendsTheKey(unittest.TestCase):
    """2026-10-12: 0.14.1 made the helpers require the key, but two callers outside the panel
    were missed: novad's GPU telemetry (the RTX lost its memory size) and the Qwen
    launcher's preflight (OPEN CLI closed at once). Any code that calls a NOVA helper on a
    path other than /health must send the key."""
    SKIP = {"tests", "docs", "previews", ".git", "__pycache__", "node_modules"}
    URL = re.compile(r"(?:127\.0\.0\.1|localhost):(?:8790|8787|8791)([^\"' )]*)")

    def test_no_caller_without_the_key(self):
        offenders = []
        # Every code file in the repository (the private checkout also has the Qwen worker's).
        for path in sorted(ROOT.rglob("*")):
            relative = path.relative_to(ROOT)
            if path.suffix not in (".py", ".sh", ".mjs", ".js") or "test" in path.name or self.SKIP & set(relative.parts):
                continue
            text = path.read_text(errors="ignore")
            paths = [m.group(1) for m in self.URL.finditer(text)]
            if not paths:
                continue
            only_health = all(paths) and all(p.startswith("/health") for p in paths)
            sends_key = "X-Nova-Key" in text or "API_KEY_HEADER" in text
            if not only_health and not sends_key:
                offenders.append(str(relative))
        self.assertEqual(offenders, [], "these call a NOVA helper without the key")

    def test_gpu_telemetry_sends_the_key(self):
        import tempfile
        from unittest import mock
        import hardware
        seen = []
        def fake_urlopen(request, timeout=None):
            seen.append(request.get_header("X-nova-key")); raise OSError("offline")
        with tempfile.TemporaryDirectory() as raw:
            key = Path(raw) / "api.token"; key.write_text("k" * 43)
            with mock.patch.object(hardware, "API_KEY_FILE", key), mock.patch.object(hardware.urllib.request, "urlopen", fake_urlopen):
                self.assertEqual(hardware.telemetry("gpu"), {})
        self.assertEqual(seen, ["k" * 43])


if __name__ == "__main__":
    unittest.main()
