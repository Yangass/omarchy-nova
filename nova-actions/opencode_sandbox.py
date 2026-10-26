#!/usr/bin/env python3
"""OpenCode in its own network space (marketplace review 2026-10-26, #12377).

OpenCode's Ollama provider talks to http://127.0.0.1:11434 itself, without NOVA's check
of who answers there (local_socket.LoopbackHTTPConnection). While Ollama was stopped,
another account on the machine could take that port and receive the prompts and the
project's files. So OPEN CLI no longer starts OpenCode directly; it starts this, which:

- checks Ollama answers through NOVA's checked connection, else refuses (nothing starts);
- serves a relay on a Unix socket in a private directory of this user's runtime
  directory: only this user's processes may connect, only the calls OpenCode makes for
  its own build pass (/api/version, /api/tags (the build alone), /api/show and
  /v1/chat/completions for the build, /v1/models), and each one goes through the
  checked connection again, so a stand-in that takes the port mid-session gets nothing;
- starts OpenCode in a new user and network namespace (`unshare --user
  --map-current-user --net`: the same user, its own 127.0.0.1), where a small relay on
  127.0.0.1:11434 forwards to that socket. Nothing else listens on that loopback, and no
  other account can enter it;
- gives the space the internet through `pasta` (passt), without the host's loopback:
  only port 53 is forwarded, to the system's resolver (a privileged port, so no other
  account can hold it). OpenCode keeps its web tools and its first-run download.

Without pasta, OpenCode runs with no internet and says so. Without user namespaces it
doesn't run at all: there is no fallback to the direct connection.
"""
from __future__ import annotations

import argparse
import ctypes
import http.client
import json
import os
import shutil
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import local_socket  # noqa: E402  (beside this file)

UNSHARE = "/usr/bin/unshare"
NSENTER = "/usr/bin/nsenter"
IP = "/usr/bin/ip"
PASTA_PATHS = ("/usr/bin/pasta",)
OLLAMA = ("127.0.0.1", int(os.environ.get("NOVA_SANDBOX_OLLAMA_PORT", "11434")))  # tests move it
INSIDE_PORT = 11434
MAX_BODY = 32 * 1024 * 1024  # a 96k-token context of code is a few MB of JSON
CHUNK = 65536


def say(text: str) -> None:
    print(f"NOVA: {text}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- outside: the checked relay

def allowed(method: str, path: str, body: bytes, build: str) -> bool:
    """Only the calls OpenCode's Ollama provider makes, and only for its own build."""
    names = (build, build + ":latest")
    if method == "GET" and path in ("/api/version", "/api/tags", "/v1/models"):
        return True
    if method != "POST" or path not in ("/api/show", "/v1/chat/completions"):
        return False
    try:
        request = json.loads(body)
    except ValueError:
        return False
    return isinstance(request, dict) and request.get("model") in names


def only_build(payload: bytes, build: str) -> bytes:
    """/api/tags with every other model left out (OpenCode sees its build alone)."""
    try:
        data = json.loads(payload)
        models = [m for m in data.get("models", []) if isinstance(m, dict) and m.get("model", m.get("name")) in (build, build + ":latest")]
        return json.dumps({"models": models}).encode()
    except (ValueError, AttributeError):
        return b'{"models":[]}'


def relay_handler(build: str):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # method, path and status only: never a prompt
            pass

        def refuse(self, code: int, text: str) -> None:
            body = json.dumps({"error": text}).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def handle_one(self) -> None:
            if self.headers.get("Transfer-Encoding"):
                return self.refuse(400, "chunked requests are not relayed")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self.refuse(400, "bad length")
            if not 0 <= length <= MAX_BODY:
                return self.refuse(413, "request too large")
            body = self.rfile.read(length)
            path = self.path.split("?", 1)[0]
            if not allowed(self.command, path, body, build):
                return self.refuse(403, "NOVA relays only this OpenCode build's calls to Ollama")
            if path == "/v1/models":
                listing = json.dumps({"object": "list", "data": [{"id": build, "object": "model", "owned_by": "local"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(listing)))
                self.end_headers()
                self.wfile.write(listing)
                return
            # A fresh checked connection for every call: whoever holds the port now must be
            # Ollama's account, root or this user, or nothing is written.
            upstream = local_socket.LoopbackHTTPConnection(*OLLAMA, timeout=1800)
            try:
                upstream.request(self.command, path, body or None, {"Content-Type": "application/json"})
                response = upstream.getresponse()
            except (OSError, http.client.HTTPException):
                upstream.close()
                return self.refuse(502, "Ollama is not running, or another account holds its port")
            try:
                if path == "/api/tags":
                    data = only_build(response.read(MAX_BODY), build)
                    self.send_response(response.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self.send_response(response.status)
                self.send_header("Content-Type", response.getheader("Content-Type", "application/json"))
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                while data := response.read1(CHUNK):
                    self.wfile.write(data)
                    self.wfile.flush()
            except (OSError, http.client.HTTPException):
                self.close_connection = True
            finally:
                upstream.close()

        do_GET = do_POST = handle_one

        def do_PUT(self):
            self.refuse(403, "not relayed")

        do_DELETE = do_HEAD = do_PATCH = do_PUT

    return Handler


class CheckedRelay(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """The relay's Unix socket: this user's processes only (kernel peer credentials)."""
    daemon_threads = True

    def verify_request(self, request, client_address) -> bool:
        try:
            return local_socket.peer_uid(request) == os.getuid()
        except OSError:
            return False


def ollama_ready() -> bool:
    connection = local_socket.LoopbackHTTPConnection(*OLLAMA, timeout=5)
    try:
        connection.request("GET", "/api/version")
        return connection.getresponse().status == 200
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def namespaces_work() -> bool:
    try:
        return subprocess.run([UNSHARE, "--user", "--map-current-user", "--net", "true"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def pasta_path() -> str | None:
    return next((path for path in PASTA_PATHS if os.access(path, os.X_OK)), None)


def outside(build: str, command: list[str]) -> int:
    if not os.access(UNSHARE, os.X_OK) or not namespaces_work():
        say("this system doesn't allow a private network space for OpenCode (user namespaces are off).")
        say("It won't start without one: its prompts could go to whatever holds 127.0.0.1:11434.")
        return 78
    if not ollama_ready():
        say("Ollama isn't answering, or another account holds its port (127.0.0.1:11434).")
        say("Start Ollama (NOVA's MODELS view) and open the CLI again. Nothing was sent.")
        return 69
    pasta = pasta_path()
    private = tempfile.mkdtemp(prefix="opencode-", dir=local_socket.runtime_dir())
    os.chmod(private, 0o700)
    path = os.path.join(private, "ollama.sock")
    relay = CheckedRelay(path, relay_handler(build))
    os.chmod(path, 0o600)
    threading.Thread(target=relay.serve_forever, daemon=True).start()
    me = [sys.executable, "-B", str(Path(__file__).resolve()), "--inside", "--socket", path, "--", *command]
    # The terminal's Ctrl+C reaches OpenCode (same process group); this waits it out.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    child = subprocess.Popen([UNSHARE, "--user", "--map-current-user", "--net", "--", *me])
    network = None
    try:
        if pasta:
            # The internet without the host's loopback; port 53 only, to the resolver.
            network = subprocess.Popen([pasta, "--config-net", "--foreground", "--quiet", "--no-map-gw",
                                        "-t", "none", "-u", "none", "-T", "53", "-U", "53", str(child.pid)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            say("passt (pasta) isn't installed, so OpenCode has no internet here: its web tools won't work.")
            say("Your local model works as usual. Install passt for the internet: sudo pacman -S passt")
            for _ in range(50):
                if subprocess.run([NSENTER, "--target", str(child.pid), "--user", "--net", "--preserve-credentials",
                                   IP, "link", "set", "lo", "up"], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL).returncode == 0:
                    break
                time.sleep(0.02)
        return child.wait()
    finally:
        if child.poll() is None:
            child.terminate()
        if network and network.poll() is None:
            network.terminate()
            try: network.wait(timeout=3)
            except subprocess.TimeoutExpired: network.kill()
        relay.shutdown()
        relay.server_close()
        shutil.rmtree(private, ignore_errors=True)


# ------------------------------------------------------------ inside: 127.0.0.1:11434 -> socket

def pump(source: socket.socket, sink: socket.socket) -> None:
    try:
        while data := source.recv(CHUNK):
            sink.sendall(data)
    except OSError:
        pass
    finally:
        for end in (source, sink):
            try: end.shutdown(socket.SHUT_RDWR)
            except OSError: pass


def inside_relay(listener: socket.socket, path: str) -> None:
    while True:
        client, _ = listener.accept()
        upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            upstream.connect(path)
        except OSError:
            client.close()
            upstream.close()
            continue
        threading.Thread(target=pump, args=(client, upstream), daemon=True).start()
        threading.Thread(target=pump, args=(upstream, client), daemon=True).start()


def loopback_up(timeout: float = 5.0) -> socket.socket | None:
    """The namespace's 127.0.0.1:11434, once its loopback is up (pasta or nsenter does it)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind(("127.0.0.1", INSIDE_PORT))
            listener.listen(16)
            return listener
        except OSError:
            listener.close()
            time.sleep(0.02)
    return None


def inside(path: str, command: list[str]) -> int:
    listener = loopback_up()
    if listener is None:
        say("OpenCode's private network didn't come up; nothing was started.")
        return 69
    relay = os.fork()
    if relay == 0:
        # The relay goes when OpenCode (its parent, after exec) goes.
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            inside_relay(listener, path)
        finally:
            os._exit(0)
    listener.close()
    # Wait for the network (pasta's interface) so OpenCode's first download finds it.
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not any(name != "lo" for _, name in socket.if_nameindex()):
        time.sleep(0.02)
    os.execvp(command[0], command)
    return 127


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--build")
    parser.add_argument("--inside", action="store_true")
    parser.add_argument("--socket")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("the command to run is missing")
    if args.inside:
        return inside(args.socket, command)
    if not args.build:
        parser.error("--build is required")
    return outside(args.build, command)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
