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
    """A real loopback server: this user gets an answer, anyone else a closed connection."""

    def serve(self):
        httpd = novad.OwnerOnlyHTTPServer(("127.0.0.1", 0), Hello)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
        self.addCleanup(lambda: (httpd.shutdown(), httpd.server_close(), thread.join(2)))
        return f"http://127.0.0.1:{httpd.server_address[1]}/"

    def test_this_user_is_served(self):
        with urllib.request.urlopen(self.serve(), timeout=5) as response:
            self.assertEqual(response.read(), b"ok")

    def test_another_user_is_refused(self):
        url = self.serve()
        with mock.patch.object(novad.os, "getuid", return_value=os.getuid() + 1):
            with self.assertRaises((urllib.error.URLError, ConnectionError, socket.error, http_errors())):
                urllib.request.urlopen(url, timeout=5).read()

    def test_unknown_owner_is_refused(self):
        url = self.serve()
        with mock.patch.object(novad, "peer_uid", return_value=None):
            with self.assertRaises((urllib.error.URLError, ConnectionError, socket.error, http_errors())):
                urllib.request.urlopen(url, timeout=5).read()


def http_errors():
    import http.client
    return http.client.HTTPException


if __name__ == "__main__":
    unittest.main()
