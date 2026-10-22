"""Redirects never read a body whole (marketplace review 2026-10-22). Local HTTP server only."""
from __future__ import annotations

import http.server
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import catalog_repins
import local_socket
import registry_probe

ENDLESS = 64 << 20  # what an unbounded read would take; the server stops there


class Server(http.server.BaseHTTPRequestHandler):
    sent = 0

    def do_GET(self):
        if self.path == "/ok":
            self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers()
            self.wfile.write(b"ok"); return
        self.send_response(302); self.send_header("Location", "/ok"); self.end_headers()
        chunk = b"x" * 65536
        try:
            while Server.sent < ENDLESS:
                self.wfile.write(chunk); Server.sent += len(chunk)
        except OSError: pass

    def log_message(self, *args): pass


class RedirectBounds(unittest.TestCase):
    def setUp(self):
        Server.sent = 0
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Server)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/"

    def test_a_redirect_with_an_endless_body_is_followed_without_reading_it(self):
        opener = urllib.request.build_opener(local_socket.BoundedRedirectHandler())
        with opener.open(self.url, timeout=10) as response: self.assertEqual(response.read(), b"ok")
        self.assertLess(Server.sent, ENDLESS)

    def test_services_follow_redirects_the_same_way(self):
        saved = urllib.request._opener
        self.addCleanup(urllib.request.install_opener, saved)
        local_socket.check_loopback_peers()
        with patch.object(local_socket, "tcp_owner", return_value=local_socket.os.getuid()), \
             urllib.request.urlopen(self.url, timeout=10) as response:
            self.assertEqual(response.read(), b"ok")
        self.assertLess(Server.sent, ENDLESS)

    def test_registry_reads_refuse_a_redirect_away_from_https(self):
        for fetch in (registry_probe.http_get, catalog_repins.manifest_fetch, catalog_repins.probe_fetch):
            with self.subTest(fetch=fetch), self.assertRaises(urllib.error.HTTPError) as caught: fetch(self.url)
            self.assertEqual(caught.exception.code, 302)
        self.assertLess(Server.sent, ENDLESS)


if __name__ == "__main__":
    unittest.main()
