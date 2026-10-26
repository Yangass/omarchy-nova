#!/usr/bin/env python3
"""Executor for NOVA's fixed, approved action catalog, for this user only."""
from __future__ import annotations
import json, os, re, secrets, signal, stat, subprocess, time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread
from typing import Any

import local_socket
from local_socket import OwnerOnlyHTTPServer

TOKEN_PATH = Path.home() / ".config" / "nova" / "actions.token"
USER_UID = os.getuid()
SERVICES = {"restart_nova_system_tools": ("nova-system-tools.service", "nova-system-tools"), "restart_novad": ("novad.service", "novad"), "restart_nova_actions": ("nova-actions.service", "nova-actions")}
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
# When each coding CLI profile was last launched (picker default, "last opened" in its row).
CLI_LAST_USED_PATH = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "nova" / "cli-last-used.json"
CLI_TOOLS = {"qwen-code"}
# 0.15: OpenCode on a local model (NOVA's tailored build), set up in one approval in the panel.
# NOVA's settings reach it at launch only (OPENCODE_CONFIG_CONTENT); the user's own stay put.
OPENCODE_CARE = {"ask": {"edit": "ask", "bash": "ask"}, "edits": {"edit": "allow", "bash": "ask"}}
OPENCODE_BUILD = re.compile(r"nova-[a-z0-9][a-z0-9.-]{0,80}-cli")
NOVA_MCP = Path(__file__).resolve().parent / "nova_mcp.py"
# OpenCode runs inside this (marketplace review #12377): its own loopback, where 11434 is a
# relay through NOVA's checked connection, and the internet through pasta.
OPENCODE_SANDBOX = Path(__file__).resolve().parent / "opencode_sandbox.py"
# Tells the model about NOVA's two tools: OpenCode 2 offers MCP tools inside its code
# runtime, where a model doesn't always look for them on its own (2026-10-23, live test).
NOVA_INSTRUCTIONS = Path(__file__).resolve().parent / "opencode-instructions.md"
ONLINE_CLI_TOOLS = {"claude-code": "claude", "codex": "codex", "antigravity": "agy"}
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
def health(service: str) -> bool: return local_socket.running(service)
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
    service, name=SERVICES[action]; before=service_info(service)
    if action in ("restart_nova_actions", "restart_novad"):
        Thread(target=lambda: (time.sleep(.25), subprocess.run(["systemctl","--user","restart",service],capture_output=True,text=True,timeout=15,check=False)),daemon=True).start()
        return {"status":"scheduled","action":action,"service":service,"pid_before":before["pid"]}
    result=subprocess.run(["systemctl","--user","restart",service],capture_output=True,text=True,timeout=15,check=False); deadline=time.monotonic()+12; after=service_info(service)
    while time.monotonic()<deadline and not (result.returncode==0 and after["active"] and health(name)): time.sleep(.25); after=service_info(service)
    return {"status":"success" if result.returncode==0 and after["active"] and health(name) else "failure","action":action,"service":service,"active":after["active"],"health":"ok" if health(name) else "unavailable","pid_before":before["pid"],"pid_after":after["pid"],"pid_changed":before["pid"]!=after["pid"]}
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
    if item.get("tool") == "opencode":
        fields = {"id", "label", "tool", "provider", "model", "build", "command", "project", "project_label", "care"}
        if set(item) != fields or not all(isinstance(item[key], str) and item[key] for key in fields): return None, "invalid_profile"
        if not CLI_PROFILE_ID.fullmatch(item["id"]) or len(item["label"]) > 80 or len(item["project_label"]) > 80 or len(item["model"]) > 200: return None, "invalid_profile"
        if item["provider"] != "ollama": return None, "unsupported_provider"
        if item["care"] not in OPENCODE_CARE or not OPENCODE_BUILD.fullmatch(item["build"]): return None, "invalid_profile"
        if not _online_command_available(Path(item["command"]), "opencode"): return None, "command_unavailable"
        project = Path(item["project"])
        if not project.is_absolute() or not _cli_owned_path(project, directory=True): return None, "project_unavailable"
        return dict(item), None
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
def cli_client_active() -> bool:
    """A Qwen client is running somewhere: NOVA opens one at a time (the launcher agrees)."""
    return subprocess.run(["pgrep", "-u", str(USER_UID), "-f", CLI_CLIENT_PATTERN], capture_output=True).returncode == 0
def cli_session_active(profile: dict[str, Any]) -> bool:
    # A live client anywhere, or a held project lock from this boot, means busy.
    return cli_client_active() or cli_project_locked(profile)
def cli_project_locked(profile: dict[str, Any]) -> bool:
    """This project's CLI is open: its launcher holds the project lock from this boot."""
    try: record = json.loads((Path(profile["project"]) / "lock").read_text() or "null")
    except (OSError, ValueError): return False
    if not isinstance(record, dict) or record.get("uid") != USER_UID or not isinstance(record.get("pid"), int): return False
    try: current_boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError: return False
    return record.get("boot_id") == current_boot and proc(record["pid"]) is not None
def _read_agent_file(git_dir: Path, name: str, limit: int) -> str | None:
    """A small regular file inside the agent's .git, or None (audit 2026-10-16). The agent
    can put a symlink to /dev/zero, a FIFO or a huge file there: never follow a symlink or
    leave the folder, never wait on a FIFO or device, never read more than `limit` bytes."""
    path = git_dir / name
    try:
        if not path.resolve().is_relative_to(git_dir.resolve()): return None
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except (OSError, RuntimeError): return None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit: return None
        raw = os.read(descriptor, limit + 1)
    except OSError: return None
    finally: os.close(descriptor)
    try: return raw.decode("ascii") if len(raw) <= limit else None
    except UnicodeDecodeError: return None
def _read_checkout_head(checkout: Path) -> str | None:
    # Files only: the agent can write this .git, and git config can run programs.
    git_dir = checkout / ".git"
    try:
        if not stat.S_ISDIR(git_dir.lstat().st_mode): return None
    except OSError: return None
    head = (_read_agent_file(git_dir, "HEAD", 4096) or "").strip()
    if head.startswith("ref: "):
        ref = head[5:]
        if not re.fullmatch(r"refs/heads/[A-Za-z0-9._/-]+", ref) or ".." in ref: return None
        loose = _read_agent_file(git_dir, ref, 4096)
        packed = _read_agent_file(git_dir, "packed-refs", 1 << 20) if loose is None else ""
        head = loose.strip() if loose is not None else next(
            (line.split(" ", 1)[0] for line in (packed or "").splitlines() if line.endswith(" " + ref)), "")
    return head if re.fullmatch(r"[0-9a-f]{40}", head) else None
CLI_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}")
def _cli_canonical(profile: dict[str, Any]) -> tuple[Path, str]:
    """The project's trusted source repository and canonical branch.
    identity.json is written by the launcher outside the agent's sandbox; it may name the
    repository the project was cloned from (NOVA, Pulsar, ...) and its branch. Anything
    missing or odd falls back to the launcher's own repository and master."""
    project = Path(profile["project"]).resolve()
    fallback = Path(profile["launcher"]).resolve().parents[2], "master"
    try: identity = json.loads((project / "identity.json").read_text())
    except (OSError, ValueError): identity = {}
    if not isinstance(identity, dict): return fallback
    # Only identities written since 2026-10-12 record a branch with their source; an older
    # one names the clone it was first copied from (NOVA's: a stale workflow clone) and
    # keeps following the launcher's repository, as before (launch.py project_source).
    named, source = identity.get("canonical_branch"), identity.get("source_repository")
    if not (isinstance(named, str) and CLI_BRANCH.fullmatch(named) and ".." not in named): return fallback
    if not (isinstance(source, str) and Path(source).is_absolute()): return fallback
    candidate = Path(source).resolve()
    if (candidate / ".git").is_dir() and not candidate.is_relative_to(project) and _cli_owned_path(candidate, directory=True):
        return candidate, named
    return fallback
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
def cli_last_used() -> dict[str, int]:
    """Profile id -> seconds since 1970 of its last launch; a missing or odd file is empty."""
    try:
        if CLI_LAST_USED_PATH.is_symlink() or CLI_LAST_USED_PATH.stat().st_size > 16_384: return {}
        data = json.loads(CLI_LAST_USED_PATH.read_text())
    except (OSError, ValueError, UnicodeDecodeError): return {}
    entries = data.get("profiles") if isinstance(data, dict) and data.get("schema") == 1 else None
    if not isinstance(entries, dict): return {}
    return {key: value for key, value in entries.items()
            if isinstance(key, str) and CLI_PROFILE_ID.fullmatch(key) and isinstance(value, int) and not isinstance(value, bool) and value > 0}
def remember_cli_launch(profile_id: str) -> None:
    """Record a launch (private file, replaced atomically); failure only loses the hint."""
    entries = cli_last_used(); entries[profile_id] = int(time.time())
    entries = dict(sorted(entries.items(), key=lambda item: -item[1])[:16])
    temporary = CLI_LAST_USED_PATH.with_name(f".{CLI_LAST_USED_PATH.name}.{os.getpid()}.tmp")
    try:
        CLI_LAST_USED_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary.unlink(missing_ok=True)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as file: json.dump({"schema": 1, "profiles": entries}, file)
        os.replace(temporary, CLI_LAST_USED_PATH)
    except OSError:
        try: temporary.unlink(missing_ok=True)
        except OSError: pass
def cli_place(project: str) -> str:
    """The project folder for display: ~/... under the home folder, else only …/its-name."""
    path, home = Path(project), Path.home()
    if path == home: return "~"
    try: return "~/" + str(path.relative_to(home))
    except ValueError: return "…/" + path.name
def opencode_running_builds(proc: Path = Path("/proc")) -> set[str]:
    """The builds an OpenCode NOVA opened is running on. NOVA hands OpenCode its settings at
    launch (OPENCODE_CONFIG_CONTENT), so that OpenCode's processes carry them. Only this
    user's processes are read, and only that one variable's model is kept."""
    found: set[str] = set()
    try: entries = [entry for entry in os.scandir(proc) if entry.name.isdigit()]
    except OSError: return found
    for entry in entries:
        try:
            if entry.stat(follow_symlinks=False).st_uid != USER_UID: continue
            with open(os.path.join(entry.path, "environ"), "rb") as file: data = file.read(262_144)
        except OSError: continue
        start = data.find(b"OPENCODE_CONFIG_CONTENT=")
        if start != 0 and (start < 0 or data[start - 1:start] != b"\0"): continue
        raw = data[start + len("OPENCODE_CONFIG_CONTENT="):].split(b"\0", 1)[0]
        try: model = json.loads(raw.decode()).get("model")
        except (ValueError, UnicodeDecodeError, AttributeError): continue
        if isinstance(model, str) and model.startswith("ollama/"):
            name = model[len("ollama/"):]
            found.add(name[:-len(":latest")] if name.endswith(":latest") else name)
    return found
def list_cli_profiles() -> dict[str, Any]:
    entries, error = load_cli_profiles()
    last_used = cli_last_used()
    opencode_builds = opencode_running_builds() if any(raw.get("tool") == "opencode" for raw, _profile, _problem in entries) else set()
    profiles = []
    for raw, profile, problem in entries:
        place = cli_place(profile["project"]) if profile else None
        if raw.get("provider") == "online":
            public = {key: raw.get(key) for key in ("id", "label", "tool", "provider", "project_label") if isinstance(raw.get(key), str)}
            public.update(kind="online", valid=profile is not None, error=problem, running=False, project_sync="not_applicable")
        elif raw.get("tool") == "opencode":
            public = {key: raw.get(key) for key in ("id", "label", "tool", "provider", "model", "build", "project_label", "care") if isinstance(raw.get(key), str)}
            public.update(kind="opencode", valid=profile is not None, error=problem, running=bool(profile) and profile["build"] in opencode_builds,
                          project_sync="not_applicable")
        else:
            public = {key: raw.get(key) for key in ("id", "label", "tool", "provider", "model", "project_label") if isinstance(raw.get(key), str)}
            public.update(kind="local", valid=profile is not None, error=problem, running=bool(profile) and cli_project_locked(profile),
                          project_sync=cli_project_sync(profile) if profile else "unknown")
            if profile:
                canonical, branch = _cli_canonical(profile)
                public.update(sync_source=canonical.name, sync_branch=branch)
        public.update(project_place=place, last_used=last_used.get(public.get("id")))
        profiles.append(public)
    return {"configured": CLI_PROFILES_PATH.exists(), "error": error, "profiles": profiles, "client_active": cli_client_active()}
def opencode_config(profile: dict[str, Any]) -> str:
    """NOVA's OpenCode settings for one launch: the tailored build, how careful it is, and the
    nova tool through which it can ask NOVA (and so the user) to change its own settings."""
    model = "ollama/" + profile["build"] + ":latest"
    return json.dumps({"model": model, "small_model": model, "instructions": [str(NOVA_INSTRUCTIONS)], "permission": OPENCODE_CARE[profile["care"]],
                       "mcp": {"nova": {"type": "local", "command": [CLI_PYTHON, "-B", str(NOVA_MCP)],
                                        "environment": {"NOVA_CLI_BUILD": profile["build"]}, "timeout": 130000, "enabled": True}}},
                      separators=(",", ":"))
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
    opencode = profile.get("tool") == "opencode"
    if ("online" if online else profile["model"]) != snapshot["model"]: return {**base, "status": "failure", "error": "profile_model_mismatch"}
    if not online and not opencode and cli_session_active(profile): return {**base, "status": "failure", "error": "cli_session_active"}
    missing = missing_terminal_session_environment()
    if missing: return {**base, "status": "failure", "error": "desktop_session_unavailable", "missing_session_environment": missing}
    if online:
        argv = ["/usr/bin/omarchy", "launch", "tui", "--app-id=" + CLI_APP_ID, "/usr/bin/env", "-C", profile["project"], profile["command"]]
        extra: dict[str, Any] = {}
        message = "Launch request sent. NOVA cannot confirm the terminal appeared."
    elif opencode:
        argv = ["/usr/bin/omarchy", "launch", "tui", "--app-id=" + CLI_APP_ID, "/usr/bin/env", "-C", profile["project"],
                "OPENCODE_CONFIG_CONTENT=" + opencode_config(profile), CLI_PYTHON, "-B", str(OPENCODE_SANDBOX),
                "--build", profile["build"], "--", profile["command"], "--standalone"]
        extra = {"model": profile["model"], "build": profile["build"]}
        message = "Launch request sent. NOVA cannot confirm the terminal appeared; the first start may download OpenCode."
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
    remember_cli_launch(profile["id"])
    return {**base, "status": "dispatched", "label": profile["label"], **extra, "launch_invoked": True, "launcher_exit_code": code, "window_observation": "not_observed", "message": message}
def body(handler: BaseHTTPRequestHandler) -> dict[str, Any] | None:
    try: size=int(handler.headers.get("Content-Length") or "0")
    except ValueError: return None
    if size<=0 or size>4096: return None
    try:
        value=json.loads(handler.rfile.read(size).decode()); return value if isinstance(value,dict) else None
    except (UnicodeDecodeError,ValueError): return None
# Who may talk to this service, and how it reaches the others: local_socket.py (Unix
# sockets in this user's runtime directory).


class Handler(BaseHTTPRequestHandler):
    server_version="nova-actions/0.7.0"
    def log_message(self,*_: Any)->None:return
    def reply(self,status:HTTPStatus,value:dict[str,Any])->None:
        raw=json.dumps(value,separators=(",",":")).encode()+b"\n"; self.send_response(status); self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(raw))); self.send_header("Cache-Control","no-store"); self.end_headers(); self.wfile.write(raw)
    def do_GET(self)->None:
        if self.path=="/health":self.reply(HTTPStatus.OK,{"status":"ok","local_only":True,"pid":os.getpid()})
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
if __name__=="__main__":OwnerOnlyHTTPServer("nova-actions",Handler).serve_forever()
