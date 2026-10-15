"""Only this desktop user's processes can reach NOVA's services, or answer in their place.

Marketplace reviews: 2026-10-10, another local account could drive novad through
127.0.0.1; 2026-10-11, a web page could forge requests; 2026-10-14, while NOVA was off,
another account could take its TCP port and receive the key the bar sent there. The
services now listen only on Unix sockets in $XDG_RUNTIME_DIR/nova (local_socket.py),
both ends check the kernel's peer credentials, and NOVA writes nothing to Ollama's
loopback port until the kernel says the answering end is Ollama's, root's or this user's.
"""
import http.client
import os
import socket
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "novad"))
import local_socket  # noqa: E402
import server as novad  # noqa: E402

SERVICES = ("novad/server.py", "nova-actions/server.py", "nova-system-tools/server.py")
HEADER = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"


def row(local, remote, uid, state="01"):
    return f"   0: {local} {remote} {state} 00000000:00000000 00:00000000 00000000  {uid}        0 12345 1\n"


def recording_sendall(sent):
    real = socket.socket.sendall
    def sendall(sock, data, *args):
        sent.append(data)
        return real(sock, data, *args)
    return sendall


class Hello(BaseHTTPRequestHandler):
    def log_message(self, *_args): pass

    def do_GET(self):
        self.send_response(200); self.send_header("Content-Length", "3"); self.end_headers(); self.wfile.write(b"ok\n")


class Runtime(unittest.TestCase):
    """A short private directory (AF_UNIX paths are limited to 107 bytes)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir="/tmp"); self.addCleanup(self.tmp.cleanup)
        self.run_dir = Path(self.tmp.name) / "run"; self.run_dir.mkdir(mode=0o700)
        patcher = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.run_dir)})
        patcher.start(); self.addCleanup(patcher.stop)


class RuntimeDirectory(Runtime):
    def test_the_nova_folder_is_made_private(self):
        path = Path(local_socket.runtime_dir())
        self.assertEqual(path, self.run_dir / "nova")
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o700")

    def test_a_loose_nova_folder_of_this_user_is_tightened(self):
        (self.run_dir / "nova").mkdir(); os.chmod(self.run_dir / "nova", 0o755)
        local_socket.runtime_dir()
        self.assertEqual(oct((self.run_dir / "nova").stat().st_mode & 0o777), "0o700")

    def test_an_open_or_odd_runtime_directory_is_refused(self):
        os.chmod(self.run_dir, 0o755)
        with self.assertRaises(local_socket.UnsafeRuntimeDirectory): local_socket.runtime_dir()
        os.chmod(self.run_dir, 0o700)
        for value in ("relative/run", str(Path(self.tmp.name) / "missing")):
            with self.subTest(value=value), mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": value}):
                with self.assertRaises(OSError): local_socket.runtime_dir()

    def test_a_symlink_in_place_of_the_nova_folder_is_refused(self):
        target = Path(self.tmp.name) / "elsewhere"; target.mkdir(mode=0o700)
        (self.run_dir / "nova").symlink_to(target)
        with self.assertRaises(local_socket.UnsafeRuntimeDirectory): local_socket.runtime_dir()

    def test_another_accounts_folder_is_refused(self):
        real = os.lstat(self.run_dir)
        fake = os.stat_result((real.st_mode, real.st_ino, real.st_dev, real.st_nlink, os.getuid() + 1, real.st_gid,
                               real.st_size, int(real.st_atime), int(real.st_mtime), int(real.st_ctime)))
        with mock.patch.object(local_socket.os, "lstat", return_value=fake):
            with self.assertRaises(local_socket.UnsafeRuntimeDirectory): local_socket.runtime_dir()


class LiveSocket(Runtime):
    def serve(self, name="novad"):
        httpd = local_socket.OwnerOnlyHTTPServer(name, Hello)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
        self.addCleanup(lambda: (httpd.shutdown(), httpd.server_close(), thread.join(2)))
        return httpd

    def test_this_users_request_is_served_over_a_private_socket(self):
        httpd = self.serve()
        path = Path(httpd.server_address)
        self.assertEqual(path, self.run_dir / "nova" / "novad.sock")
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
        self.assertEqual(local_socket.call("novad", "GET", "/state"), (200, b"ok\n"))
        self.assertTrue(local_socket.running("novad"))

    def test_a_socket_left_by_an_earlier_run_is_replaced(self):
        (self.run_dir / "nova").mkdir(mode=0o700)
        stale = socket.socket(socket.AF_UNIX); stale.bind(str(self.run_dir / "nova" / "novad.sock")); stale.close()
        self.serve()
        self.assertEqual(local_socket.call("novad")[0], 200)

    def test_another_account_is_refused_by_the_service(self):
        httpd = self.serve()
        answers = iter([os.getuid() + 1])
        with mock.patch.object(httpd, "verify_request", wraps=lambda request, address: next(answers) == os.getuid()):
            with self.assertRaises((ConnectionError, http.client.HTTPException, OSError)):
                local_socket.call("novad", "GET", "/state", timeout=3)

    def test_the_service_asks_the_kernel_who_connected(self):
        httpd = self.serve()
        left, right = socket.socketpair()
        with left, right:
            self.assertTrue(httpd.verify_request(left, ""))
            with mock.patch.object(local_socket, "peer_uid", return_value=os.getuid() + 1):
                self.assertFalse(httpd.verify_request(left, ""))

    def test_a_server_of_another_account_gets_nothing(self):
        self.serve()
        sent = []
        with mock.patch.object(local_socket, "peer_uid", return_value=os.getuid() + 1), \
                mock.patch.object(socket.socket, "sendall", recording_sendall(sent)):
            with self.assertRaises(ConnectionRefusedError): local_socket.call("novad", "POST", "/chat", b'{"message":"secret"}')
        self.assertEqual(sent, [])

    def test_the_kernel_names_the_peer(self):
        left, right = socket.socketpair()
        with left, right: self.assertEqual(local_socket.peer_uid(left), os.getuid())

    def test_no_service_is_running_is_an_os_error(self):
        with self.assertRaises(OSError): local_socket.call("nova-actions", timeout=1)
        self.assertFalse(local_socket.running("nova-actions"))

    def test_the_old_key_file_is_retired_but_nothing_else(self):
        key = Path(self.tmp.name) / "api.token"; key.write_text("k" * 40)
        local_socket.retire_old_key(str(key))
        self.assertFalse(key.exists())
        target = Path(self.tmp.name) / "keep"; target.write_text("x"); key.symlink_to(target)
        local_socket.retire_old_key(str(key))
        self.assertTrue(key.is_symlink() and target.exists())


class Copies(unittest.TestCase):
    def test_the_three_copies_are_identical(self):
        copies = {(ROOT / d / "local_socket.py").read_bytes() for d in ("novad", "nova-actions", "nova-system-tools")}
        self.assertEqual(len(copies), 1)

    def test_every_service_serves_only_on_its_socket(self):
        names = {"novad/server.py": "novad", "nova-actions/server.py": "nova-actions", "nova-system-tools/server.py": "nova-system-tools"}
        for path, name in names.items():
            with self.subTest(path=path):
                text = (ROOT / path).read_text()
                main = text.split('__name__')[-1].replace(" ", "")
                self.assertIn(f'OwnerOnlyHTTPServer("{name}",Handler)', main)
                for tcp in ("ThreadingHTTPServer", "HTTPServer((", "AF_INET", "HOST", "127.0.0.1:87", "127.0.0.1:879", "X-Nova-Key", "api.token"):
                    self.assertNotIn(tcp, text)

    def test_every_answer_ends_with_a_newline(self):
        # NovaClient.qml reads line by line; Quickshell drops an unfinished last line.
        for path, needle in (("novad/server.py", 'separators=(",", ":")).encode() + b"\\n"'),
                             ("nova-system-tools/server.py", '.encode("utf-8") + b"\\n"'),
                             ("nova-actions/server.py", '.encode()+b"\\n"')):
            with self.subTest(path=path): self.assertIn(needle, (ROOT / path).read_text())

    def test_the_chat_stream_closes_when_it_ends(self):
        self.assertNotIn('"Connection", "keep-alive"', (ROOT / "novad/server.py").read_text())


class LoopbackOwner(unittest.TestCase):
    """NOVA's requests to Ollama (127.0.0.1:11434) go only to a trusted account's socket."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)

    def table(self, *rows, name="tcp"):
        path = Path(self.tmp.name) / name
        path.write_text(HEADER + "".join(rows))
        return str(path)

    def ends(self):
        # The server end 127.0.0.1:11434 and the client end 127.0.0.1:40000 as Linux prints them.
        return ("0100007F:2CAA", "0100007F:9C40") if sys.byteorder == "little" else ("7F000001:2CAA", "7F000001:9C40")

    def test_names_the_owner_of_the_answering_end(self):
        server, client = self.ends()
        tables = (self.table(row(server, client, 960), row(client, server, 1000)),)
        self.assertEqual(local_socket.tcp_owner(("127.0.0.1", 11434), ("127.0.0.1", 40000), tables), 960)
        self.assertIsNone(local_socket.tcp_owner(("127.0.0.1", 11434), ("127.0.0.1", 40001), tables))

    def test_unclear_answers_are_refused(self):
        server, client = self.ends()
        two_owners = (self.table(row(server, client, 960), row(server, client, 1001)),)
        not_established = (self.table(row(server, client, 960, state="06")),)
        unreadable = (str(Path(self.tmp.name) / "missing"),)
        garbage = (self.table("   0: nonsense\n"),)
        for tables in (two_owners, not_established, unreadable, garbage):
            with self.subTest(tables=tables):
                self.assertIsNone(local_socket.tcp_owner(("127.0.0.1", 11434), ("127.0.0.1", 40000), tables))

    def test_ipv4_mapped_rows_count(self):
        mapped = lambda port: ("0000000000000000FFFF00000100007F" if sys.byteorder == "little" else "00000000000000000000FFFF7F000001") + ":" + port
        tables = (self.table(row(mapped("2CAA"), mapped("9C40"), 960), name="tcp6"),)
        self.assertEqual(local_socket.tcp_owner(("127.0.0.1", 11434), ("127.0.0.1", 40000), tables), 960)

    def listener(self):
        sock = socket.create_server(("127.0.0.1", 0)); self.addCleanup(sock.close)
        def answer():
            try:
                connection, _ = sock.accept()
                with connection:
                    connection.recv(4096)
                    connection.send(b"HTTP/1.0 200 OK\r\nContent-Length: 3\r\n\r\nok\n")  # not sendall: that one is watched
            except OSError: pass
        threading.Thread(target=answer, daemon=True).start()
        return sock.getsockname()[1]

    def test_a_trusted_owner_gets_the_request(self):
        port = self.listener()  # owned by this user, who is trusted
        connection = local_socket.LoopbackHTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", "/api/version")
        self.assertEqual(connection.getresponse().read(), b"ok\n")

    def test_a_stand_in_of_another_account_gets_nothing(self):
        port = self.listener()
        sent = []
        with mock.patch.object(local_socket, "tcp_owner", return_value=os.getuid() + 12345), \
                mock.patch.object(socket.socket, "sendall", recording_sendall(sent)):
            connection = local_socket.LoopbackHTTPConnection("127.0.0.1", port, timeout=5)
            with self.assertRaises(ConnectionRefusedError): connection.request("POST", "/api/chat", body=b'{"messages":"secret"}')
        self.assertEqual(sent, [])

    def test_through_urllib_too(self):
        port = self.listener()
        opener = urllib.request.build_opener(local_socket.LoopbackHandler())
        with mock.patch.object(local_socket, "tcp_owner", return_value=os.getuid() + 12345):
            with self.assertRaises(OSError): opener.open(f"http://127.0.0.1:{port}/api/tags", timeout=5)

    def test_ollamas_account_root_and_this_user_are_trusted(self):
        trusted = local_socket.trusted_uids()
        self.assertTrue({os.getuid(), 0} <= trusted)
        try:
            import pwd
            self.assertIn(pwd.getpwnam("ollama").pw_uid, trusted)
        except KeyError: pass

    def test_novad_checks_every_plain_http_request_and_the_acquisition_connection(self):
        main = (ROOT / "novad/server.py").read_text().split('__name__')[-1]
        self.assertIn("local_socket.check_loopback_peers()", main)
        self.assertIn("LoopbackHTTPConnection('127.0.0.1', 11434", (ROOT / "novad/ollama_acquisition.py").read_text())
        before = urllib.request._opener
        try:
            local_socket.check_loopback_peers()
            self.assertTrue(any(isinstance(h, local_socket.LoopbackHandler) for h in urllib.request._opener.handlers))
        finally:
            urllib.request.install_opener(before)


class Hardening(unittest.TestCase):
    def test_no_service_sends_cors_headers(self):
        for path in SERVICES:
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


class NovadCalls(unittest.TestCase):
    def test_novad_reaches_system_tools_and_actions_over_their_sockets(self):
        seen = []
        def fake_call(service, method="GET", path="/health", body=None, headers=None, timeout=10.0):
            seen.append((service, method, path))
            return 200, b'{"ok":true}\n'
        with mock.patch.object(novad.local_socket, "call", fake_call), \
                mock.patch.object(novad.Path, "read_text", return_value="token"):
            self.assertEqual(novad.system_tools_json("/v1/cpu"), (200, {"ok": True}))
            self.assertEqual(novad.actions_get("/v1/coding-cli-profiles"), (200, {"ok": True}))
            novad.tool_result("get_cpu_usage", {})
        self.assertEqual(seen[:2], [("nova-system-tools", "GET", "/v1/cpu"), ("nova-actions", "GET", "/v1/coding-cli-profiles")])
        self.assertEqual([s[0] for s in seen[2:]], ["nova-system-tools"])


if __name__ == "__main__":
    unittest.main()
