"""OpenCode's sandbox (marketplace review #12377, 2026-10-26): the checked relay lets only
OpenCode's own calls through, never writes to a stand-in that holds Ollama's port, and,
where this machine allows user namespaces, OpenCode's 127.0.0.1:11434 is that relay while
the host's own loopback is out of reach."""
from __future__ import annotations

import http.client
import http.server
import importlib.util
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ACTIONS = ROOT / "nova-actions"
sys.path.insert(0, str(ACTIONS))
spec = importlib.util.spec_from_file_location("opencode_sandbox_for_test", ACTIONS / "opencode_sandbox.py")
sandbox = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(sandbox)

BUILD = "nova-qwen3-8b-cli"


class FakeOllama(http.server.ThreadingHTTPServer):
    """Answers like Ollama on 127.0.0.1 and remembers every request it was sent."""

    def __init__(self):
        self.seen: list[tuple[str, str, bytes]] = []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass

            def reply(self, payload: dict) -> None:
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                server.seen.append(("GET", self.path, b""))
                if self.path == "/api/tags":
                    return self.reply({"models": [{"name": BUILD + ":latest", "model": BUILD + ":latest"},
                                                  {"name": "private:7b", "model": "private:7b"}]})
                self.reply({"version": "0.40.7"})

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                server.seen.append(("POST", self.path, body))
                self.reply({"ok": True})

        super().__init__(("127.0.0.1", 0), Handler)


class Fixture(unittest.TestCase):
    """A fake Ollama on a free loopback port and the checked relay on a socket."""

    def setUp(self):
        self.ollama = FakeOllama()
        threading.Thread(target=self.ollama.serve_forever, daemon=True).start()
        self.addCleanup(self.ollama.shutdown)
        self.port = mock.patch.object(sandbox, "OLLAMA", ("127.0.0.1", self.ollama.server_address[1]))
        self.port.start(); self.addCleanup(self.port.stop)
        self.dir = tempfile.TemporaryDirectory(); self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "ollama.sock")
        self.relay = sandbox.CheckedRelay(self.path, sandbox.relay_handler(BUILD))
        threading.Thread(target=self.relay.serve_forever, daemon=True).start()
        self.addCleanup(self.relay.server_close); self.addCleanup(self.relay.shutdown)

    def ask(self, method: str, path: str, body: dict | None = None) -> tuple[int, bytes]:
        class Unix(http.client.HTTPConnection):
            def connect(inner):
                inner.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                inner.sock.connect(self.path)
        connection = Unix("localhost", timeout=5)
        data = json.dumps(body).encode() if body is not None else None
        connection.request(method, path, data, {"Content-Type": "application/json"})
        response = connection.getresponse()
        result = (response.status, response.read())
        connection.close()
        return result


class RelayTests(Fixture):
    def test_opencodes_own_calls_pass_and_nothing_else(self):
        self.assertEqual(self.ask("GET", "/api/version")[0], 200)
        self.assertEqual(self.ask("POST", "/api/show", {"model": BUILD + ":latest"})[0], 200)
        self.assertEqual(self.ask("POST", "/v1/chat/completions", {"model": BUILD, "messages": []})[0], 200)
        for method, path, body in (("POST", "/api/show", {"model": "private:7b"}),
                                   ("POST", "/v1/chat/completions", {"model": "private:7b", "messages": []}),
                                   ("POST", "/api/pull", {"model": BUILD}), ("POST", "/api/delete", {"model": BUILD}),
                                   ("POST", "/api/create", {"model": BUILD}), ("POST", "/api/generate", {"model": BUILD})):
            self.assertEqual(self.ask(method, path, body)[0], 403, path)
        self.assertEqual(self.ask("DELETE", "/api/delete")[0], 403)
        sent = [(m, p) for m, p, _ in self.ollama.seen]
        self.assertEqual(sent, [("GET", "/api/version"), ("POST", "/api/show"), ("POST", "/v1/chat/completions")])

    def test_opencode_sees_its_build_alone(self):
        status, body = self.ask("GET", "/api/tags")
        self.assertEqual(status, 200)
        self.assertEqual([m["model"] for m in json.loads(body)["models"]], [BUILD + ":latest"])
        models = json.loads(self.ask("GET", "/v1/models")[1])["data"]
        self.assertEqual([m["id"] for m in models], [BUILD])

    def test_a_stand_in_on_ollamas_port_gets_nothing(self):
        # The kernel says another account holds the port (Ollama stopped, someone bound it).
        with mock.patch.object(sandbox.local_socket, "tcp_owner", return_value=4242), \
             mock.patch.object(sandbox.local_socket, "trusted_uids", return_value={os.getuid(), 0}):
            status, _ = self.ask("POST", "/v1/chat/completions", {"model": BUILD, "messages": [{"role": "user", "content": "secret"}]})
            self.assertFalse(sandbox.ollama_ready())
        self.assertEqual(status, 502)
        self.assertEqual(self.ollama.seen, [], "the stand-in received a request")

    def test_another_account_cannot_use_the_relay(self):
        with mock.patch.object(sandbox.local_socket, "peer_uid", side_effect=_peer_uid):
            self.assertTrue(self.relay.verify_request(_Peer(os.getuid()), None))
            self.assertFalse(self.relay.verify_request(_Peer(4242), None))

    def test_the_launch_refuses_without_namespaces_or_ollama(self):
        with mock.patch.object(sandbox, "namespaces_work", return_value=False):
            self.assertEqual(sandbox.outside(BUILD, ["/bin/true"]), 78)
        with mock.patch.object(sandbox, "namespaces_work", return_value=True), \
             mock.patch.object(sandbox, "ollama_ready", return_value=False), \
             mock.patch.object(sandbox.subprocess, "Popen") as popen:
            self.assertEqual(sandbox.outside(BUILD, ["/bin/true"]), 69)
            popen.assert_not_called()


class ClassSweepTests(unittest.TestCase):
    """The whole class (reviews #11195 and #12377): nothing NOVA ships talks to Ollama's
    port without the checked connection, and OpenCode starts only inside the sandbox."""

    def test_no_unchecked_connection_to_a_loopback_port_in_shipped_code(self):
        import re
        plain = re.compile(r"(?<!Loopback)HTTPConnection\(\s*['\"]127\.0\.0\.1|create_connection\(\(\s*['\"]127\.0\.0\.1['\"],\s*11434")
        for folder in ("novad", "nova-actions", "nova-system-tools"):
            for path in (ROOT / folder).glob("*.py"):
                if path.name.startswith("test_"): continue
                for number, line in enumerate(path.read_text().splitlines(), 1):
                    self.assertIsNone(plain.search(line), f"{path.relative_to(ROOT)}:{number}: {line.strip()}")

    def test_opencode_is_launched_only_through_the_sandbox(self):
        server = (ACTIONS / "server.py").read_text()
        start = server.index("elif opencode:")
        launch = server[start:server.index("else:", start)]
        self.assertIn("str(OPENCODE_SANDBOX)", launch)
        self.assertLess(launch.index("str(OPENCODE_SANDBOX)"), launch.index('profile["command"]'))


class _Peer:
    def __init__(self, uid): self.uid = uid


def _peer_uid(peer):
    return peer.uid


class LiveSandboxTests(Fixture):
    """OpenCode's stand-in inside a real namespace: its 127.0.0.1:11434 is the relay, the
    host's loopback is out of reach. Skipped where user namespaces are off (some CI)."""

    def setUp(self):
        if not sandbox.namespaces_work() or os.environ.get("CI"):
            self.skipTest("no unprivileged user namespaces here")
        super().setUp()

    def test_inside_11434_is_the_relay_and_the_host_loopback_is_out_of_reach(self):
        host = http.server.ThreadingHTTPServer(("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler)
        threading.Thread(target=host.serve_forever, daemon=True).start()
        self.addCleanup(host.shutdown)
        out = os.path.join(self.dir.name, "seen.json")
        probe = (
            "import json, socket, urllib.request, sys\n"
            "r = urllib.request.urlopen('http://127.0.0.1:11434/api/version', timeout=10).read().decode()\n"
            f"s = socket.socket(); far = s.connect_ex(('127.0.0.1', {host.server_address[1]}))\n"
            f"json.dump({{'version': r, 'host_reachable': far == 0}}, open({out!r}, 'w'))\n"
        )
        env = {**os.environ, "NOVA_SANDBOX_OLLAMA_PORT": str(self.ollama.server_address[1])}
        import subprocess
        code = subprocess.run([sys.executable, "-B", str(ACTIONS / "opencode_sandbox.py"), "--build", BUILD, "--",
                               sys.executable, "-c", probe], env=env, timeout=60).returncode
        self.assertEqual(code, 0)
        seen = json.load(open(out))
        self.assertIn("0.40.7", seen["version"])
        self.assertFalse(seen["host_reachable"], "the host's loopback was reachable from inside")


if __name__ == "__main__":
    unittest.main()
