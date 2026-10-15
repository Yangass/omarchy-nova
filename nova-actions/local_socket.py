"""Who NOVA's services talk to, and who may talk to them.

Identical in novad/, nova-actions/ and nova-system-tools/ (a test checks the copies).

Marketplace reviews: 2026-10-10, another local account could drive NOVA through
127.0.0.1; 2026-10-11, a web page could forge requests to it; 2026-10-14, while NOVA was
off, another account could take its TCP port and receive the key the bar sent there.
NOVA's services therefore listen only on Unix sockets in this user's runtime directory
($XDG_RUNTIME_DIR/nova, 0700, inside a directory only this user can enter). No other
account can connect to them or answer in their place, no web page can reach a Unix
socket, and there is no key to send. Both ends also check the kernel's peer credentials
on every connection.

Ollama listens on 127.0.0.1:11434 like any Ollama. Before NOVA writes a request there,
the kernel's socket table must say the answering end belongs to Ollama's service
account, root or this user, so a stand-in on that port while Ollama is stopped gets
nothing (not even the chat message).
"""
from __future__ import annotations

import http.client
import ipaddress
import os
import socket
import socketserver
import stat
import struct
import sys
import urllib.request

NAMES = {"novad": "novad.sock", "nova-system-tools": "system-tools.sock", "nova-actions": "actions.sock"}
# The key file 0.14.1-0.14.7 sent with every request; nothing reads it any more.
OLD_KEY_PATH = os.path.join(os.path.expanduser("~"), ".config", "nova", "api.token")


class UnsafeRuntimeDirectory(PermissionError):
    pass


def _private_dir(path: str, create: bool) -> None:
    uid = os.getuid()
    if create:
        try: os.mkdir(path, 0o700)
        except FileExistsError: pass
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid:
        raise UnsafeRuntimeDirectory(f"{path} is not a directory of this user")
    if info.st_mode & 0o077:
        if not create: raise UnsafeRuntimeDirectory(f"{path} is open to other accounts")
        os.chmod(path, 0o700)


def runtime_dir() -> str:
    """$XDG_RUNTIME_DIR/nova (made 0700). Refused unless the runtime directory is this
    user's own and closed to everyone else."""
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    if not os.path.isabs(base): raise UnsafeRuntimeDirectory("XDG_RUNTIME_DIR is not an absolute path")
    _private_dir(base, create=False)
    path = os.path.join(base, "nova")
    _private_dir(path, create=True)
    return path


def socket_path(service: str) -> str:
    return os.path.join(runtime_dir(), NAMES[service])


def peer_uid(connection: socket.socket) -> int:
    """The UID of the process at the other end of a Unix socket, from the kernel."""
    _pid, uid, _gid = struct.unpack("3i", connection.getsockopt(
        socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
    return uid


def retire_old_key(path: str = OLD_KEY_PATH) -> None:
    try:
        info = os.lstat(path)
        if stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid(): os.unlink(path)
    except FileNotFoundError: pass


def _local(handler):
    class Local(handler):
        def address_string(self) -> str: return "local"
    Local.__name__ = Local.__qualname__ = handler.__name__
    return Local


class OwnerOnlyHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """HTTP on this service's socket, for this user's processes only."""
    daemon_threads = True

    def __init__(self, service: str, handler) -> None:
        path = socket_path(service)
        try:
            if stat.S_ISSOCK(os.lstat(path).st_mode): os.unlink(path)  # left by an earlier run
        except FileNotFoundError: pass
        previous = os.umask(0o177)
        try: super().__init__(path, _local(handler))
        finally: os.umask(previous)
        retire_old_key()

    def verify_request(self, request, client_address) -> bool:
        try: return peer_uid(request) == os.getuid()
        except OSError: return False


class UnixHTTPConnection(http.client.HTTPConnection):
    """http.client over a NOVA service's socket. It refuses a server that isn't this user's."""

    def __init__(self, service: str, timeout: float = 10.0) -> None:
        super().__init__("localhost", timeout=timeout)
        self.service = service

    def connect(self) -> None:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(self.timeout)
        try:
            connection.connect(socket_path(self.service))
            if peer_uid(connection) != os.getuid(): raise ConnectionRefusedError(f"{self.service} is not this user's")
        except BaseException:
            connection.close()
            raise
        self.sock = connection


def call(service: str, method: str = "GET", path: str = "/health", body: bytes | None = None,
         headers: dict[str, str] | None = None, timeout: float = 10.0) -> tuple[int, bytes]:
    """One request to a NOVA service: (status, body). OSError when it isn't running."""
    connection = UnixHTTPConnection(service, timeout)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def running(service: str, timeout: float = 2.0) -> bool:
    try: return call(service, "GET", "/health", timeout=timeout)[0] == 200
    except (OSError, http.client.HTTPException): return False


# --- loopback TCP (Ollama) ---
TCP_TABLES = ("/proc/net/tcp", "/proc/net/tcp6")


def _proc_address(text: str) -> tuple[str, int]:
    host, port = text.split(":")
    raw = bytes.fromhex(host)
    if sys.byteorder == "little": raw = b"".join(raw[i:i + 4][::-1] for i in range(0, len(raw), 4))
    address = ipaddress.ip_address(raw)
    if address.version == 6 and address.ipv4_mapped: address = address.ipv4_mapped
    return str(address), int(port, 16)


def tcp_owner(local: tuple[str, int], remote: tuple[str, int], tables=TCP_TABLES) -> int | None:
    """UID owning the established TCP socket local -> remote, or None unless exactly one."""
    owners = set()
    for table in tables:
        try:
            with open(table) as file: rows = file.read().splitlines()[1:]
        except OSError: continue
        for row in rows:
            fields = row.split()
            try:
                if fields[3] != "01": continue  # ESTABLISHED
                if _proc_address(fields[1]) == (local[0], local[1]) and _proc_address(fields[2]) == (remote[0], remote[1]):
                    owners.add(int(fields[7]))
            except (IndexError, ValueError): continue
    return owners.pop() if len(owners) == 1 else None


def trusted_uids() -> set[int]:
    """This user, root, and Ollama's service account where there is one."""
    uids = {os.getuid(), 0}
    try:
        import pwd
        uids.add(pwd.getpwnam("ollama").pw_uid)
    except (ImportError, KeyError): pass
    return uids


def _plain(address: tuple) -> tuple[str, int]:
    host = ipaddress.ip_address(address[0])
    if host.version == 6 and host.ipv4_mapped: host = host.ipv4_mapped
    return str(host), int(address[1])


class LoopbackHTTPConnection(http.client.HTTPConnection):
    """http.client that, on this machine, writes nothing until the kernel says the
    answering end belongs to a trusted account (see trusted_uids)."""

    def connect(self) -> None:
        super().connect()
        mine, theirs = _plain(self.sock.getsockname()), _plain(self.sock.getpeername())
        if not ipaddress.ip_address(theirs[0]).is_loopback: return
        if tcp_owner(theirs, mine) not in trusted_uids():
            self.sock.close()
            self.sock = None
            raise ConnectionRefusedError(f"{theirs[0]}:{theirs[1]} is held by another account")


class LoopbackHandler(urllib.request.HTTPHandler):
    def http_open(self, request):
        return self.do_open(LoopbackHTTPConnection, request)


def check_loopback_peers() -> None:
    """Send this process's plain-http urllib requests through LoopbackHTTPConnection."""
    urllib.request.install_opener(urllib.request.build_opener(LoopbackHandler()))
