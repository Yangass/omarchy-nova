"""A stand-in novad for the QML checks, on a Unix socket like the real one (local_socket.py).

The panel reaches NOVA through NovaClient.qml, which reads the socket line by line, so
every answer must end with a newline (the real services add one). The checks' fixture
handlers don't, so server() adds it to every body (counted in its Content-Length).

In a check's QML: NovaClient { id: fakeNova; socketPath: "@SOCKET@" }, then
previewHost.novaClient = fakeNova, and replace @SOCKET@ with server.server_address.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from socketserver import ThreadingUnixStreamServer


class _Body:
    """The response stream: one newline after a body of the announced length."""

    def __init__(self, raw):
        self.raw, self.remaining, self.last = raw, None, b"\n"

    def write(self, data):
        written = self.raw.write(data)
        if data: self.last = bytes(data[-1:])
        if self.remaining is not None:
            self.remaining -= len(data)
            if self.remaining <= 0:
                self.remaining = None
                self.raw.write(b"\n")
                self.last = b"\n"
        return written

    def __getattr__(self, name):
        return getattr(self.raw, name)


def _newline_bodies(handler):
    class Fixture(handler):
        def setup(self):
            super().setup()
            self.wfile = _Body(self.wfile)
            self._announced = None

        def address_string(self):
            return "local"

        def send_header(self, keyword, value):
            if keyword.lower() == "content-length" and int(value) > 0:
                self._announced = int(value)
                value = str(int(value) + 1)
            super().send_header(keyword, value)

        def end_headers(self):
            super().end_headers()
            self.wfile.remaining, self._announced = self._announced, None

        def finish(self):
            # A body without a length ends when the socket closes: end its last line too.
            if self.wfile.last != b"\n":
                try: self.wfile.raw.write(b"\n")
                except OSError: pass
            super().finish()

    Fixture.__name__ = Fixture.__qualname__ = handler.__name__
    return Fixture


class _Server(ThreadingUnixStreamServer):
    daemon_threads = True

    def server_close(self):
        super().server_close()
        shutil.rmtree(os.path.dirname(self.server_address), ignore_errors=True)


def server(handler) -> _Server:
    folder = tempfile.mkdtemp(prefix="nova-fake-", dir="/tmp")
    return _Server(os.path.join(folder, "novad.sock"), _newline_bodies(handler))
