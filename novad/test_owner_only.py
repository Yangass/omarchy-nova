"""Only this desktop user may use NOVA's loopback services (marketplace review 2026-10-10).

Another local account could reach 127.0.0.1:8790 and have novad act as the desktop
owner. All three services now accept a connection only when the kernel's socket
table says its client end belongs to this user.
"""
import importlib.util
import os
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "novad"))
import server as novad  # noqa: E402


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ACTIONS = load("nova_actions_for_owner_test", ROOT / "nova-actions" / "server.py")
TOOLS = load("nova_tools_for_owner_test", ROOT / "nova-system-tools" / "server.py")
SERVICES = (("novad", novad), ("nova-actions", ACTIONS), ("nova-system-tools", TOOLS))
HEADER = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"


def row(local, remote, uid, state="01"):
    return f"   0: {local} {remote} {state} 00000000:00000000 00:00000000 00000000  {uid}        0 12345 1\n"


class Hello(BaseHTTPRequestHandler):
    def log_message(self, *_args): pass

    def do_GET(self):
        self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"ok")


class PeerTable(unittest.TestCase):
    def table(self, *rows, name="tcp"):
        path = Path(self.tmp.name) / name
        path.write_text(HEADER + "".join(rows))
        return str(path)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)

    def test_names_the_owner_of_the_client_end(self):
        # 127.0.0.1:40000 -> 127.0.0.1:8790 as printed on little-endian Linux.
        client, server = "0100007F:9C40", "0100007F:2256"
        if sys.byteorder == "big": client, server = "7F000001:9C40", "7F000001:2256"
        tables = (self.table(row(server, client, 1000), row(client, server, 1001)),)
        for name, module in SERVICES:
            with self.subTest(service=name):
                self.assertEqual(module.peer_uid(("127.0.0.1", 40000), ("127.0.0.1", 8790), tables), 1001)
                self.assertIsNone(module.peer_uid(("127.0.0.1", 40001), ("127.0.0.1", 8790), tables))

    def test_unclear_answers_are_refused(self):
        client, server = ("0100007F:9C40", "0100007F:2256") if sys.byteorder == "little" else ("7F000001:9C40", "7F000001:2256")
        two_owners = (self.table(row(client, server, 1000), row(client, server, 1001)),)
        not_established = (self.table(row(client, server, 1000, state="06")),)
        unreadable = (str(Path(self.tmp.name) / "missing"),)
        garbage = (self.table("   0: nonsense\n"),)
        for tables in (two_owners, not_established, unreadable, garbage):
            with self.subTest(tables=tables):
                self.assertIsNone(novad.peer_uid(("127.0.0.1", 40000), ("127.0.0.1", 8790), tables))

    def test_ipv4_mapped_rows_count(self):
        # An IPv4 connection listed in tcp6 as ::ffff:127.0.0.1.
        mapped = lambda port: ("0000000000000000FFFF00000100007F" if sys.byteorder == "little" else "00000000000000000000FFFF7F000001") + ":" + port
        tables = (self.table(row(mapped("9C40"), mapped("2256"), 1002), name="tcp6"),)
        self.assertEqual(novad.peer_uid(("127.0.0.1", 40000), ("127.0.0.1", 8790), tables), 1002)

    def test_the_three_copies_are_identical(self):
        def block(path):
            text = path.read_text()
            start = text.index("# --- owner-only loopback")
            return text[start:text.index("# --- end owner-only loopback ---", start)]
        copies = {block(ROOT / p) for p in ("novad/server.py", "nova-actions/server.py", "nova-system-tools/server.py")}
        self.assertEqual(len(copies), 1)

    def test_every_service_starts_the_owner_only_server(self):
        for path in ("novad/server.py", "nova-actions/server.py", "nova-system-tools/server.py"):
            with self.subTest(path=path):
                main = (ROOT / path).read_text().split('__name__')[-1].replace(" ", "")
                self.assertIn("OwnerOnlyHTTPServer((HOST,PORT),Handler)", main)
                self.assertNotIn("ThreadingHTTPServer((HOST", main)


class LiveSocket(unittest.TestCase):
    """A real loopback server: this user's panel is served; other accounts, forged
    browser requests and rebinding hosts are not."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(novad, "API_KEY_PATH", str(Path(self.tmp.name) / "nova" / "api.token"))
        patcher.start(); self.addCleanup(patcher.stop)

    def serve(self):
        httpd = novad.OwnerOnlyHTTPServer(("127.0.0.1", 0), Hello)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
        self.addCleanup(lambda: (httpd.shutdown(), httpd.server_close(), thread.join(2)))
        self.port = httpd.server_address[1]
        return f"http://127.0.0.1:{self.port}/"

    def get(self, url, headers=None):
        request = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=5) as response: return response.status
        except urllib.error.HTTPError as error: return error.code

    def raw(self, host, path="/state", key=None):
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as conn:
            extra = f"X-Nova-Key: {key}\r\n" if key else ""
            conn.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}\r\n{extra}Connection: close\r\n\r\n".encode())
            data = b""
            while chunk := conn.recv(4096): data += chunk
            return data.split(b" ")[1].decode()

    def test_the_key_file_is_created_private_and_reused(self):
        self.serve()
        path = Path(novad.API_KEY_PATH)
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
        self.assertGreaterEqual(len(novad.api_key()), 32)
        self.assertEqual(novad.api_key(), path.read_text().strip())

    def test_this_users_panel_with_the_key_is_served(self):
        url = self.serve()
        self.assertEqual(self.get(url, {"X-Nova-Key": novad.api_key()}), 200)
        self.assertEqual(self.raw(f"localhost:{self.port}", key=novad.api_key()), "200")

    def test_a_request_without_the_right_key_is_refused(self):
        url = self.serve()
        self.assertEqual(self.get(url), 401)  # what a forged browser request looks like
        self.assertEqual(self.get(url, {"X-Nova-Key": "guess" * 10}), 401)

    def test_health_is_open_but_still_host_checked(self):
        url = self.serve()
        self.assertEqual(self.get(url + "health"), 200)
        self.assertEqual(self.raw("evil.example:80", "/health"), "403")

    def test_dns_rebinding_hosts_are_refused_even_with_the_key(self):
        self.serve()
        for host in ("evil.example", f"evil.example:{self.port}", "127.0.0.1", f"127.0.0.2:{self.port}"):
            with self.subTest(host=host):
                self.assertEqual(self.raw(host, key=novad.api_key()), "403")

    def test_another_user_is_refused(self):
        url = self.serve()
        with mock.patch.object(novad.os, "getuid", return_value=os.getuid() + 1):
            with self.assertRaises((urllib.error.URLError, ConnectionError, socket.error, http_errors())):
                urllib.request.urlopen(urllib.request.Request(url, headers={"X-Nova-Key": novad.api_key()}), timeout=5).read()

    def test_unknown_owner_is_refused(self):
        url = self.serve()
        with mock.patch.object(novad, "peer_uid", return_value=None):
            with self.assertRaises((urllib.error.URLError, ConnectionError, socket.error, http_errors())):
                urllib.request.urlopen(urllib.request.Request(url, headers={"X-Nova-Key": novad.api_key()}), timeout=5).read()


class Hardening(unittest.TestCase):
    def test_no_service_sends_cors_headers(self):
        for path in ("novad/server.py", "nova-actions/server.py", "nova-system-tools/server.py"):
            with self.subTest(path=path):
                text = (ROOT / path).read_text()
                self.assertNotIn('send_header("Access-Control', text)
                self.assertNotIn("def do_OPTIONS", text)

    def test_state_folders_become_private_but_symlinks_and_others_are_left_alone(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            state = home / ".local/state/nova"; (state / "installer").mkdir(parents=True)
            share = home / ".local/share/nova"; share.mkdir(parents=True)
            for path in (state, state / "installer", share): os.chmod(path, 0o755)
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": ""}):
                tightened = novad.private_state_dirs(home)
            self.assertEqual(len(tightened), 3)
            for path in (state, state / "installer", share):
                self.assertEqual(oct(path.stat().st_mode & 0o777), "0o700")
            target = home / "elsewhere"; target.mkdir(); os.chmod(target, 0o755)
            (state / "installer").rmdir(); (state / "installer").symlink_to(target)
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": ""}):
                novad.private_state_dirs(home)
            self.assertEqual(oct(target.stat().st_mode & 0o777), "0o755")


class KeyOnlyWhereItBelongs(unittest.TestCase):
    def test_novad_sends_the_key_to_system_tools_but_never_to_ollama(self):
        seen = []
        def fake_urlopen(request, timeout=None):
            seen.append((request.full_url, request.get_header("X-nova-key")))
            raise urllib.error.URLError("offline")
        with mock.patch.object(novad.urllib.request, "urlopen", fake_urlopen), mock.patch.object(novad, "api_key", return_value="k" * 40):
            novad.upstream_json(novad.SYSTEM_TOOLS_URL + "/telemetry")
            novad.upstream_json(novad.OLLAMA_URL + "/api/ps")
        self.assertEqual(seen, [(novad.SYSTEM_TOOLS_URL + "/telemetry", "k" * 40), (novad.OLLAMA_URL + "/api/ps", None)])


def http_errors():
    import http.client
    return http.client.HTTPException


if __name__ == "__main__":
    unittest.main()
