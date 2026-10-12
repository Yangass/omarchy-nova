#!/usr/bin/env python3
"""Loopback-only executor for NOVA's fixed, approved action catalog."""
from __future__ import annotations
import json, os, re, secrets, signal, stat, subprocess, time, urllib.error, urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

HOST, PORT = "127.0.0.1", 8791
TOKEN_PATH = Path.home() / ".config" / "nova" / "actions.token"
USER_UID = os.getuid()
SERVICES = {"restart_nova_system_tools": ("nova-system-tools.service", "http://127.0.0.1:8787/health"), "restart_novad": ("novad.service", "http://127.0.0.1:8790/health"), "restart_nova_actions": ("nova-actions.service", "http://127.0.0.1:8791/health")}
APPLICATIONS = {"terminal": ("Terminal", ["/usr/bin/omarchy", "launch", "terminal"]), "file_manager": ("Files", ["/usr/bin/omarchy", "launch", "nautilus"]), "browser": ("Web browser", ["/usr/bin/omarchy", "launch", "browser"]), "system_monitor": ("System monitor", ["/usr/bin/omarchy", "launch", "tui", "btop"])}
# Omarchy's configured normal-workspace bindings and layout cover 1 through 10.
# Only the direct terminal command is safe to target: apps that reuse processes
# (browser/files) cannot truthfully promise placement for a new window.
NORMAL_WORKSPACE_MIN, NORMAL_WORKSPACE_MAX = 1, 10
WORKSPACE_TARGET_COMMANDS = {"terminal": "/usr/bin/foot"}
HYPRCTL = "/usr/bin/hyprctl"
TERMINAL_SESSION_VARIABLES = ("XDG_RUNTIME_DIR", "WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE", "XDG_CURRENT_DESKTOP")
# Coding CLI profiles are explicit user configuration; the command is built here
# from validated paths, never from model names, chat output or request strings.
CLI_PROFILES_PATH = Path.home() / ".config" / "nova" / "cli-profiles.json"
CLI_TOOLS = {"qwen-code"}
ONLINE_CLI_TOOLS = {"claude-code": "claude", "codex": "codex"}
# Omarchy tags TUI.* windows as terminals (default/hypr/apps/terminals.lua), so its
# universal copy/paste (Super+C/V) sends Ctrl+Insert/Shift+Insert instead of Ctrl+C/V.
CLI_APP_ID = "TUI.nova-cli"
CLI_PYTHON = "/usr/bin/python3"
CLI_PROFILE_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
CLI_CLIENT_PATTERN = r"/qwen-code/.*/(cli-entry\.js|bin/qwen)"
PROTECTED = {"systemd", "dbus-daemon", "hyprland", "xwayland", "pipewire", "wireplumber", "quickshell", "omarchy-shell", "polkit"}
def audit(event: str, **fields: Any) -> None: print(json.dumps({"event":event, **fields}, separators=(",", ":")), flush=True)
def token() -> str:
    TOKEN_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try: value = TOKEN_PATH.read_text().strip()
    except FileNotFoundError:
        value = secrets.token_urlsafe(32)
        try:
            fd = os.open(TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as handle: handle.write(value + "\n")
        except FileExistsError: value = TOKEN_PATH.read_text().strip()
    os.chmod(TOKEN_PATH, 0o600); return value
TOKEN = token()
def health(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response: return response.status == HTTPStatus.OK
    except (urllib.error.URLError, TimeoutError, OSError): return False
def service_info(service: str) -> dict[str, Any]:
    result = subprocess.run(["systemctl", "--user", "show", service, "--property=ActiveState", "--property=MainPID", "--value"], capture_output=True, text=True, timeout=5, check=False); values=result.stdout.splitlines()
    return {"active":bool(values and values[0] == "active"), "pid":int(values[1]) if len(values)>1 and values[1].isdigit() else 0}
def nova_pids() -> set[int]: return {service_info(s)["pid"] for s, _ in SERVICES.values()} - {0}
def proc(pid: int) -> dict[str, Any] | None:
    if not isinstance(pid, int) or pid <= 1: return None
    try:
        tail=Path(f"/proc/{pid}/stat").read_text().rsplit(")",1)[1].split(); name=Path(f"/proc/{pid}/comm").read_text().strip(); cmd=Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0",b" ").decode(errors="replace").strip()
        return {"pid":pid,"uid":os.stat(f"/proc/{pid}").st_uid,"name":name,"start_time":tail[19],"cmdline":cmd}
    except (FileNotFoundError, PermissionError, IndexError, OSError): return None
def bearer_ok(value: Any) -> bool:
    # Constant-time comparison (security review 2026-10-11).
    import hmac
    return isinstance(value, str) and hmac.compare_digest(value.encode(), ("Bearer " + TOKEN).encode())
def is_protected(item: dict[str, Any]) -> bool:
    name=item["name"].lower(); cmd=item["cmdline"].lower()
    return item["pid"] in nova_pids() or not cmd or "ollama" in cmd or any(word in name or word in cmd for word in PROTECTED)
def matches(item: dict[str, Any]) -> bool:
    current=proc(item.get("pid")); return bool(current and current["uid"] == USER_UID and current["start_time"] == item.get("start_time") and current["name"] == item.get("name") and not is_protected(current))
def restart(action: str) -> dict[str, Any]:
    service, url=SERVICES[action]; before=service_info(service)
    if action in ("restart_nova_actions", "restart_novad"):
        Thread(target=lambda: (time.sleep(.25), subprocess.run(["systemctl","--user","restart",service],capture_output=True,text=True,timeout=15,check=False)),daemon=True).start()
        return {"status":"scheduled","action":action,"service":service,"pid_before":before["pid"]}
    result=subprocess.run(["systemctl","--user","restart",service],capture_output=True,text=True,timeout=15,check=False); deadline=time.monotonic()+12; after=service_info(service)
    while time.monotonic()<deadline and not (result.returncode==0 and after["active"] and health(url)): time.sleep(.25); after=service_info(service)
    return {"status":"success" if result.returncode==0 and after["active"] and health(url) else "failure","action":action,"service":service,"active":after["active"],"health":"ok" if health(url) else "unavailable","pid_before":before["pid"],"pid_after":after["pid"],"pid_changed":before["pid"]!=after["pid"]}
def terminate(item: dict[str, Any]) -> dict[str, Any]:
    if not matches(item): return {"status":"failure","action":"terminate_user_process","error":"stale_action_target"}
    try: os.kill(item["pid"], signal.SIGTERM)
    except (ProcessLookupError, PermissionError): return {"status":"failure","action":"terminate_user_process","error":"stale_action_target"}
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        if proc(item["pid"]) is None: return {"status":"success","action":"terminate_user_process","pid":item["pid"],"process":item["name"],"signal":"SIGTERM","exited":True}
        time.sleep(.2)
    return {"status":"failure","action":"terminate_user_process","pid":item["pid"],"process":item["name"],"signal":"SIGTERM","exited":False,"error":"process_still_running"}
def workspace_target(value: Any) -> int | None:
    # bool is an int subclass; reject it rather than treating true as workspace 1.
    if isinstance(value, bool) or not isinstance(value, int): return None
    return value if NORMAL_WORKSPACE_MIN <= value <= NORMAL_WORKSPACE_MAX else None

def workspace_argv(app: str, workspace: int) -> list[str] | None:
    command = WORKSPACE_TARGET_COMMANDS.get(app)
    if not command or workspace_target(workspace) is None: return None
    # The only interpolated value is a prevalidated normal-workspace integer.
    # The command is a backend-owned fixed executable, not user/model text.
    code = 'hl.exec_cmd("' + command + '", { workspace = "' + str(workspace) + ' silent" })'
    return [HYPRCTL, "eval", code]

def missing_terminal_session_environment() -> list[str]:
    """Return safe variable names only; never expose session values."""
    return [name for name in TERMINAL_SESSION_VARIABLES if not os.environ.get(name)]

def open_app(item: dict[str, Any]) -> dict[str, Any]:
    app=item.get("application_id")
    if app not in APPLICATIONS: return {"status":"failure","error":"unknown_application"}
    friendly, argv=APPLICATIONS[app]
    workspace = item.get("workspace")
    if workspace is not None:
        workspace = workspace_target(workspace)
        if workspace is None: return {"status":"failure","action":"open_known_application","application":friendly,"application_id":app,"error":"invalid_workspace_target"}
        argv = workspace_argv(app, workspace)
        if argv is None: return {"status":"failure","action":"open_known_application","application":friendly,"application_id":app,"workspace":workspace,"error":"workspace_target_unsupported_for_application"}
    if app == "terminal":
        missing = missing_terminal_session_environment()
        if missing:
            result = {"status":"failure","action":"open_known_application","application":friendly,"application_id":app,"launch_invoked":False,"error":"desktop_session_unavailable","missing_session_environment":missing,"window_observation":"not_observed","message":"The terminal launch request could not access the active desktop session."}
            if workspace is not None: result["workspace"] = workspace
            return result
    try:
        launched=subprocess.Popen(argv,start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        code=launched.wait(timeout=5)
    except subprocess.TimeoutExpired:
        code=None
    except OSError:
        result = {"status":"failure","action":"open_known_application","application":friendly,"application_id":app,"error":"launch_failed","window_observation":"not_observed","message":"The fixed launch request could not be started."}
        if workspace is not None: result["workspace"] = workspace
        return result
    if code not in (0,None):
        result = {"status":"failure","action":"open_known_application","application":friendly,"application_id":app,"launch_invoked":False,"launcher_exit_code":code,"error":"launcher_failed","window_observation":"not_observed","message":"The fixed launcher reported a failure; no application window was confirmed."}
        if workspace is not None: result["workspace"] = workspace
        return result
    result = {"status":"dispatched","action":"open_known_application","application":friendly,"application_id":app,"launch_invoked":True,"launcher_exit_code":code,"window_observation":"not_observed","message":"Launch request sent. NOVA cannot confirm that an application window appeared."}
    if workspace is not None:
        result.update({"workspace":workspace,"workspace_placement":"requested_not_observed","message":"Launch request sent to workspace " + str(workspace) + ". NOVA cannot confirm that an application window appeared there."})
    return result
def _cli_owned_path(path: Path, *, directory: bool) -> bool:
    try: info = path.lstat()
    except OSError: return False
    kind_ok = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    return kind_ok and info.st_uid == USER_UID and not info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
def _online_command_available(path: Path, binary: str) -> bool:
    # Symlinks are followed here (version-manager shims), unlike local launchers.
    if not path.is_absolute() or path.name != binary: return False
    try: info = path.resolve(strict=True).lstat()
    except (OSError, RuntimeError, ValueError): return False
    return (stat.S_ISREG(info.st_mode) and info.st_uid == USER_UID
            and not info.st_mode & (stat.S_IWGRP | stat.S_IWOTH) and bool(info.st_mode & stat.S_IXUSR))
def validate_cli_profile(item: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(item, dict): return None, "invalid_profile"
    if item.get("provider") == "online":
        fields = {"id", "label", "tool", "provider", "command", "project", "project_label"}
        if set(item) != fields or not all(isinstance(item[key], str) and item[key] for key in fields): return None, "invalid_profile"
        if not CLI_PROFILE_ID.fullmatch(item["id"]) or len(item["label"]) > 80 or len(item["project_label"]) > 80: return None, "invalid_profile"
        binary = ONLINE_CLI_TOOLS.get(item["tool"])
        if binary is None: return None, "unsupported_tool"
        if not _online_command_available(Path(item["command"]), binary): return None, "command_unavailable"
        project = Path(item["project"])
        if not project.is_absolute() or not _cli_owned_path(project, directory=True): return None, "project_unavailable"
        return dict(item), None
    fields = {"id", "label", "tool", "provider", "model", "launcher", "project", "project_label"}
    if set(item) != fields or not all(isinstance(item[key], str) and item[key] for key in fields): return None, "invalid_profile"
    if not CLI_PROFILE_ID.fullmatch(item["id"]) or len(item["label"]) > 80 or len(item["project_label"]) > 80 or len(item["model"]) > 200: return None, "invalid_profile"
    if item["tool"] not in CLI_TOOLS: return None, "unsupported_tool"
    if item["provider"] != "ollama": return None, "unsupported_provider"
    launcher, project = Path(item["launcher"]), Path(item["project"])
    if not launcher.is_absolute() or not project.is_absolute(): return None, "invalid_profile"
    if not _cli_owned_path(launcher, directory=False): return None, "launcher_unavailable"
    if not _cli_owned_path(project, directory=True) or not _cli_owned_path(project / "identity.json", directory=False): return None, "project_unavailable"
    # The agent can edit its project; its launcher must live outside it.
    if launcher.resolve().is_relative_to(project.resolve()): return None, "launcher_inside_project"
    return dict(item), None
def load_cli_profiles() -> tuple[list[tuple[dict[str, Any], dict[str, Any] | None, str | None]], str | None]:
    if not CLI_PROFILES_PATH.exists(): return [], None
    try:
        if CLI_PROFILES_PATH.is_symlink() or CLI_PROFILES_PATH.stat().st_size > 65_536: return [], "invalid_profile_file"
        data = json.loads(CLI_PROFILES_PATH.read_text())
    except (OSError, ValueError, UnicodeDecodeError): return [], "invalid_profile_file"
    if not isinstance(data, dict) or data.get("schema") != 1 or not isinstance(data.get("profiles"), list) or len(data["profiles"]) > 16: return [], "invalid_profile_file"
    entries = []
    for item in data["profiles"]:
        profile, error = validate_cli_profile(item)
        entries.append((item if isinstance(item, dict) else {}, profile, error))
    return entries, None
def cli_session_active(profile: dict[str, Any]) -> bool:
    # A live client anywhere, or a held project lock from this boot, means busy.
    if subprocess.run(["pgrep", "-u", str(USER_UID), "-f", CLI_CLIENT_PATTERN], capture_output=True).returncode == 0: return True
    try: record = json.loads((Path(profile["project"]) / "lock").read_text() or "null")
    except (OSError, ValueError): return False
    if not isinstance(record, dict) or record.get("uid") != USER_UID or not isinstance(record.get("pid"), int): return False
    try: current_boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError: return False
    return record.get("boot_id") == current_boot and proc(record["pid"]) is not None
def _read_checkout_head(checkout: Path) -> str | None:
    # Files only: the agent can write this .git, and git config can run programs.
    git_dir = checkout / ".git"
    try:
        head = (git_dir / "HEAD").read_text(encoding="ascii").strip()
        if head.startswith("ref: "):
            ref = head[5:]
            if not re.fullmatch(r"refs/heads/[A-Za-z0-9._/-]+", ref) or ".." in ref: return None
            loose = git_dir / ref
            head = loose.read_text(encoding="ascii").strip() if loose.is_file() else next(
                (line.split(" ", 1)[0] for line in (git_dir / "packed-refs").read_text(encoding="ascii").splitlines() if line.endswith(" " + ref)), "")
    except (OSError, UnicodeDecodeError): return None
    return head if re.fullmatch(r"[0-9a-f]{40}", head) else None
CLI_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}")
def _cli_canonical(profile: dict[str, Any]) -> tuple[Path, str]:
    """The project's trusted source repository and canonical branch.
    identity.json is written by the launcher outside the agent's sandbox; it may name the
    repository the project was cloned from (NOVA, Pulsar, ...) and its branch. Anything
    missing or odd falls back to the launcher's own repository and master."""
    project = Path(profile["project"]).resolve()
    canonical, branch = Path(profile["launcher"]).resolve().parents[2], "master"
    try: identity = json.loads((project / "identity.json").read_text())
    except (OSError, ValueError): identity = {}
    source = identity.get("source_repository") if isinstance(identity, dict) else None
    if isinstance(source, str) and Path(source).is_absolute():
        candidate = Path(source).resolve()
        if (candidate / ".git").is_dir() and not candidate.is_relative_to(project) and _cli_owned_path(candidate, directory=True):
            canonical = candidate
    named = identity.get("canonical_branch") if isinstance(identity, dict) else None
    if isinstance(named, str) and CLI_BRANCH.fullmatch(named) and ".." not in named:
        branch = named
    return canonical, branch
def cli_project_sync(profile: dict[str, Any]) -> str:
    """Whether an interactive launch will fast-forward the project to its canonical branch.
    Git runs only in the trusted canonical repo, never in the agent's checkout."""
    canonical, branch = _cli_canonical(profile)
    head = _read_checkout_head(Path(profile["project"]) / "project")
    if head is None or not (canonical / ".git").is_dir(): return "unknown"
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(canonical), *args], capture_output=True, text=True, timeout=10)
    ref = "refs/heads/" + branch
    try:
        tip = git("rev-parse", "--verify", "-q", ref).stdout.strip()
        if head == tip: return "up_to_date"
        return "will_update" if git("merge-base", "--is-ancestor", head, ref).returncode == 0 else "unreviewed_commits"
    except (OSError, subprocess.SubprocessError): return "unknown"
def list_cli_profiles() -> dict[str, Any]:
    entries, error = load_cli_profiles()
    profiles = []
    for raw, profile, problem in entries:
        if raw.get("provider") == "online":
            public = {key: raw.get(key) for key in ("id", "label", "tool", "provider", "project_label") if isinstance(raw.get(key), str)}
            public.update(kind="online", valid=profile is not None, error=problem, running=False, project_sync="not_applicable")
        else:
            public = {key: raw.get(key) for key in ("id", "label", "tool", "provider", "model", "project_label") if isinstance(raw.get(key), str)}
            public.update(kind="local", valid=profile is not None, error=problem, running=bool(profile) and cli_session_active(profile),
                          project_sync=cli_project_sync(profile) if profile else "unknown")
        profiles.append(public)
    return {"configured": CLI_PROFILES_PATH.exists(), "error": error, "profiles": profiles}
def open_coding_cli(snapshot: dict[str, Any]) -> dict[str, Any]:
    base = {"action": "open_coding_cli", "profile_id": snapshot.get("profile_id")}
    if set(snapshot) != {"profile_id", "model"} or not all(isinstance(snapshot[key], str) for key in snapshot): return {**base, "status": "failure", "error": "invalid_cli_request"}
    entries, error = load_cli_profiles()
    if error: return {**base, "status": "failure", "error": error}
    match = next(((profile, problem) for raw, profile, problem in entries if raw.get("id") == snapshot["profile_id"]), None)
    if match is None: return {**base, "status": "failure", "error": "unknown_profile"}
    profile, problem = match
    if profile is None: return {**base, "status": "failure", "error": problem}
    online = profile.get("provider") == "online"
    if ("online" if online else profile["model"]) != snapshot["model"]: return {**base, "status": "failure", "error": "profile_model_mismatch"}
    if not online and cli_session_active(profile): return {**base, "status": "failure", "error": "cli_session_active"}
    missing = missing_terminal_session_environment()
    if missing: return {**base, "status": "failure", "error": "desktop_session_unavailable", "missing_session_environment": missing}
    if online:
        argv = ["/usr/bin/omarchy", "launch", "tui", "--app-id=" + CLI_APP_ID, "/usr/bin/env", "-C", profile["project"], profile["command"]]
        extra: dict[str, Any] = {}
        message = "Launch request sent. NOVA cannot confirm the terminal appeared."
    else:
        argv = ["/usr/bin/omarchy", "launch", "tui", "--app-id=" + CLI_APP_ID, CLI_PYTHON, "-B", profile["launcher"], "--project", profile["project"], "--interactive"]
        extra = {"model": profile["model"]}
        message = "Launch request sent. NOVA cannot confirm the terminal appeared; the launcher re-checks the model, lock and disk itself."
    try:
        launched = subprocess.Popen(argv, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try: code = launched.wait(timeout=5)
        except subprocess.TimeoutExpired: code = None  # still running: dispatched, as for fixed apps
    except (OSError, subprocess.SubprocessError): return {**base, "status": "failure", "error": "launch_failed", "window_observation": "not_observed"}
    if code not in (0, None): return {**base, "status": "failure", "error": "launcher_failed", "launcher_exit_code": code, "window_observation": "not_observed"}
    return {**base, "status": "dispatched", "label": profile["label"], **extra, "launch_invoked": True, "launcher_exit_code": code, "window_observation": "not_observed", "message": message}
def body(handler: BaseHTTPRequestHandler) -> dict[str, Any] | None:
    try: size=int(handler.headers.get("Content-Length") or "0")
    except ValueError: return None
    if size<=0 or size>4096: return None
    try:
        value=json.loads(handler.rfile.read(size).decode()); return value if isinstance(value,dict) else None
    except (UnicodeDecodeError,ValueError): return None
# --- owner-only loopback (identical in novad, nova-actions and nova-system-tools) ---
# Every local account can connect to 127.0.0.1, so a loopback port alone does not say
# who is asking (Omarchy marketplace review, 2026-10-10). Before any request is read,
# the kernel's socket table names the account that owns the client end of the
# connection; anything but this desktop user, or an answer that is not exactly one
# account, is refused by closing the connection.
TCP_TABLES = ("/proc/net/tcp", "/proc/net/tcp6")


def _proc_address(text):
    import ipaddress, sys
    host, port = text.split(":")
    raw = bytes.fromhex(host)
    if sys.byteorder == "little": raw = b"".join(raw[i:i + 4][::-1] for i in range(0, len(raw), 4))
    address = ipaddress.ip_address(raw)
    if address.version == 6 and address.ipv4_mapped: address = address.ipv4_mapped
    return str(address), int(port, 16)


def peer_uid(client, server, tables=TCP_TABLES):
    """UID owning the established connection client -> server, or None unless exactly one."""
    owners = set()
    for table in tables:
        try:
            with open(table) as file: rows = file.read().splitlines()[1:]
        except OSError: continue
        for row in rows:
            fields = row.split()
            try:
                if fields[3] != "01": continue  # ESTABLISHED
                if _proc_address(fields[1]) == (client[0], client[1]) and _proc_address(fields[2]) == (server[0], server[1]):
                    owners.add(int(fields[7]))
            except (IndexError, ValueError): continue
    return owners.pop() if len(owners) == 1 else None


# The desktop user's own web browser passes the UID check, so a web page could still
# send requests (cross-site request forgery) or pose as 127.0.0.1 (DNS rebinding;
# review, 2026-10-11). Every request must therefore name this service's loopback Host
# and, except /health, carry the key from a file only this user can read
# (~/.config/nova/api.token, 0600). Pages can neither read that file nor add the
# header to a cross-site request without a preflight, which is never answered.
API_KEY_PATH = os.path.join(os.path.expanduser("~"), ".config", "nova", "api.token")
API_KEY_HEADER = "X-Nova-Key"
OPEN_PATHS = ("/health",)


def api_key(path=None):
    """The shared key, created once (0600) by whichever NOVA service starts first."""
    import secrets
    path = path or API_KEY_PATH
    try:
        with open(path) as file: value = file.read().strip()
        if len(value) >= 32: return value
    except FileNotFoundError: pass
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as file: file.write(secrets.token_urlsafe(32) + "\n")
    except FileExistsError: pass
    with open(path) as file: value = file.read().strip()
    if len(value) < 32: raise RuntimeError("NOVA API key unavailable")
    return value


def _guarded(handler):
    class Guarded(handler):
        def parse_request(self):
            from hmac import compare_digest
            from urllib.parse import urlsplit
            if not super().parse_request(): return False
            port = self.server.server_address[1]
            if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
                self.send_error(403, "Unexpected Host"); return False
            if urlsplit(self.path).path not in OPEN_PATHS and not compare_digest(
                    (self.headers.get(API_KEY_HEADER) or "").encode(), api_key().encode()):
                self.send_error(401, "NOVA key required"); return False
            return True
    Guarded.__name__ = Guarded.__qualname__ = handler.__name__
    return Guarded


class OwnerOnlyHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler):
        api_key()  # create the key before the first request
        super().__init__(address, _guarded(handler))

    def verify_request(self, request, client_address):
        return peer_uid(client_address, self.server_address[:2]) == os.getuid()
# --- end owner-only loopback ---


class Handler(BaseHTTPRequestHandler):
    server_version="nova-actions/0.6.0"
    def log_message(self,*_: Any)->None:return
    def reply(self,status:HTTPStatus,value:dict[str,Any])->None:
        raw=json.dumps(value,separators=(",",":")).encode(); self.send_response(status); self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(raw))); self.send_header("Cache-Control","no-store"); self.end_headers(); self.wfile.write(raw)
    def do_GET(self)->None:
        if self.path=="/health":self.reply(HTTPStatus.OK,{"status":"ok","loopback_only":True,"pid":os.getpid()})
        elif self.path=="/v1/coding-cli-profiles":
            if not bearer_ok(self.headers.get("Authorization")):self.reply(HTTPStatus.UNAUTHORIZED,{"error":"unauthorized"});return
            self.reply(HTTPStatus.OK,list_cli_profiles())
        else:self.reply(HTTPStatus.NOT_FOUND,{"error":"unknown_endpoint"})
    def do_POST(self)->None:
        routes={"/v1/restart-nova-system-tools":"restart_nova_system_tools","/v1/restart-novad":"restart_novad","/v1/restart-nova-actions":"restart_nova_actions","/v1/terminate-user-process":"terminate_user_process","/v1/open-known-application":"open_known_application","/v1/open-coding-cli":"open_coding_cli"}; action=routes.get(self.path)
        if not action:self.reply(HTTPStatus.NOT_FOUND,{"error":"unknown_endpoint"});return
        if not bearer_ok(self.headers.get("Authorization")):self.reply(HTTPStatus.UNAUTHORIZED,{"error":"unauthorized"});return
        data=body(self)
        if not data or data.get("action")!=action or not isinstance(data.get("snapshot"),dict):self.reply(HTTPStatus.BAD_REQUEST,{"error":"invalid_approved_snapshot"});return
        snapshot=data["snapshot"]
        audit("execution_started",action=action,application_id=snapshot.get("application_id") if action=="open_known_application" else None,workspace=snapshot.get("workspace") if action=="open_known_application" else None)
        if action.startswith("restart_"):
            if snapshot!={"service":SERVICES[action][0]}:self.reply(HTTPStatus.BAD_REQUEST,{"error":"invalid_approved_snapshot"});return
            result=restart(action)
        elif action=="terminate_user_process":result=terminate(snapshot)
        elif action=="open_coding_cli":result=open_coding_cli(snapshot)
        else:result=open_app(snapshot)
        audit("execution_completed",action=action,application_id=result.get("application_id", snapshot.get("application_id") if action=="open_known_application" else None),workspace=result.get("workspace", snapshot.get("workspace") if action=="open_known_application" else None),status=result.get("status"),error=result.get("error"));self.reply(HTTPStatus.OK if result.get("status") in ("success","scheduled","dispatched") else HTTPStatus.CONFLICT,result)
    def do_PUT(self)->None:self.reply(HTTPStatus.METHOD_NOT_ALLOWED,{"error":"post_only"})
    def do_DELETE(self)->None:self.do_PUT()
if __name__=="__main__":OwnerOnlyHTTPServer((HOST,PORT),Handler).serve_forever()
