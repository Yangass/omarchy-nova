"""The bar and the panel reach NOVA only over its private socket, with no key
(marketplace reviews 2026-10-11 and 2026-10-14; local_socket.py, NovaClient.qml).

2026-10-14: while NOVA was off, another account could take 127.0.0.1:8790 and receive
the key the bar sent there. 2026-10-12 lesson: when the way to reach NOVA changes, every
caller must change with it (two were missed when the key came in), so the whole
repository is checked for the old ports and the old key."""
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "novad"))
PANEL = (ROOT / "Panel.qml").read_text()
BAR = (ROOT / "BarWidget.qml").read_text()
CLIENT = (ROOT / "NovaClient.qml").read_text()


class PanelAndBar(unittest.TestCase):
    def test_panel_requests_go_only_through_nova_request_and_open_nova(self):
        self.assertNotIn("new XMLHttpRequest", PANEL)
        opener = PANEL[PANEL.index("function openNova("):]
        opener = opener[:opener.index("\n")]
        self.assertEqual(PANEL.count("xhr.open("), opener.count("xhr.open("), "a request bypasses openNova")
        self.assertEqual(PANEL.count("root.novaRequest()"), PANEL.count("root.openNova(xhr, "))
        self.assertGreaterEqual(PANEL.count("root.openNova(xhr, "), 28)
        # Paths only: no host, no port.
        for call in re.findall(r'root\.openNova\(xhr, "[A-Z]+", ([^\n]*)', PANEL):
            self.assertTrue(call.startswith('"/'), call)

    def test_the_bar_uses_the_socket_client(self):
        self.assertNotIn("new XMLHttpRequest", BAR)
        self.assertIn("readonly property NovaClient novaClient: NovaClient {}", BAR)
        self.assertIn("hostWidget.novaClient.request()", PANEL)
        self.assertIn("function novaRequest() { return novaClient.request() }", BAR)
        self.assertEqual(BAR.count("xhr.open("), 1)

    def test_the_client_talks_to_novad_socket_in_the_runtime_directory(self):
        self.assertIn('Quickshell.env("XDG_RUNTIME_DIR") + "/nova/novad.sock"', CLIENT)
        self.assertIn("Socket {", CLIENT)
        self.assertNotIn("XMLHttpRequest()", CLIENT)

    def test_no_qml_file_names_an_address_or_a_key(self):
        for path in sorted(ROOT.glob("*.qml")):
            with self.subTest(path=path.name):
                text = path.read_text()
                for word in ("127.0.0.1", "localhost:", "X-Nova-Key", "api.token", "apiKey"):
                    self.assertNotIn(word, text)


class EveryCaller(unittest.TestCase):
    """No code anywhere in the repository (the private checkout also has the Qwen worker's)
    reaches NOVA's services through their old TCP ports or sends the old key."""
    SKIP = {"docs", "previews", ".git", "__pycache__", "node_modules"}
    OLD_PORTS = re.compile(r"(?:127\.0\.0\.1|localhost)[\"']?\s*[:,]\s*(?:8790|8787|8791)\b")

    def test_nothing_uses_the_old_ports_or_the_key(self):
        offenders = []
        for path in sorted(ROOT.rglob("*")):
            relative = path.relative_to(ROOT)
            if path.suffix not in (".py", ".sh", ".mjs", ".js", ".qml") or self.SKIP & set(relative.parts):
                continue
            if path.name in ("test_panel_socket.py", "test_owner_only.py", "local_socket.py", "check_nova_socket_interaction.py",
                             "check_other_account.sh"):
                continue  # these guards, the retirement of the old key file, and the stand-in checks
            text = path.read_text(errors="ignore")
            if self.OLD_PORTS.search(text) or "X-Nova-Key" in text or "api.token" in text:
                offenders.append(str(relative))
        self.assertEqual(offenders, [], "these still use NOVA's old ports or key")

    def test_gpu_telemetry_goes_over_the_socket(self):
        import hardware
        seen = []
        def fake_call(service, method="GET", path="/health", body=None, headers=None, timeout=10.0):
            seen.append((service, method, path)); return 200, b'{"gpus":[]}\n'
        with mock.patch("local_socket.call", fake_call):
            self.assertEqual(hardware.telemetry("gpu"), {"gpus": []})
        self.assertEqual(seen, [("nova-system-tools", "GET", "/v1/gpu")])


if __name__ == "__main__":
    unittest.main()
