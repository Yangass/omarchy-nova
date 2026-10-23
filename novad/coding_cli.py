"""A coding CLI for every NOVA user (0.15, Konstantin 2026-10-22).

Omarchy installs OpenCode for every user. NOVA connects it to a local model in one approval:
a tailored build of the model (its working context built in, the same files: no download),
an OPEN CLI profile, and NOVA's settings handed to OpenCode at launch (OPENCODE_CONFIG_CONTENT,
so the user's own OpenCode settings are never touched). The working context can be tuned in
NOVA's panel, or the CLI can ask NOVA for a change through a tool; NOVA shows the fit and the
user approves. Nothing here runs a shell or reads model output as commands.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import stat
import time
from pathlib import Path
from threading import Event, Lock
from typing import Any, Callable

GIB = 1024 ** 3
CONFIG_DIR = Path.home() / ".config" / "nova"
BUILDS_PATH = CONFIG_DIR / "tailored-builds.json"
PROFILES_PATH = CONFIG_DIR / "cli-profiles.json"
# Working contexts NOVA offers (tokens). Past the model's own maximum is allowed with a warning.
STEPS = (4096, 8192, 12288, 16384, 24576, 32768, 40960, 49152, 65536, 98304, 131072, 196608, 262144)
TOOLS = {"opencode": {"label": "OpenCode", "binary": "opencode"}}
CARE = {"ask": {"edit": "ask", "bash": "ask"}, "edits": {"edit": "allow", "bash": "ask"}}
PROPOSAL_TTL_SECONDS = 120
MAX_BUILDS = 32
BUILD_NAME = re.compile(r"nova-[a-z0-9][a-z0-9.-]{0,80}-cli")


def build_name(base: str) -> str:
    """The tailored build's name: stable across tuning, so a running CLI keeps finding it."""
    name = base[:-len(":latest")] if base.endswith(":latest") else base
    slug = re.sub(r"[^a-z0-9.]+", "-", name.lower()).strip("-.")[:80] or "model"
    return f"nova-{slug}-cli"


def context_label(tokens: int) -> str:
    return f"{tokens // 1024}K"


# --- fit ---------------------------------------------------------------------------------
def tune_numbers(row: dict[str, Any], budget: dict[str, Any]) -> dict[str, Any] | None:
    """What the context meter needs for one installed model (model_fit's estimate), or None."""
    fit = row.get("fit") if isinstance(row.get("fit"), dict) else {}
    total = budget.get("operating_budget_bytes")
    weights, per_token = fit.get("weights_bytes"), fit.get("kv_bytes_per_token_estimate")
    overhead, native = fit.get("runtime_reserve_bytes"), fit.get("native_context")
    if not all(type(value) is int and value > 0 for value in (total, weights, per_token, overhead, native)): return None
    fixed = fit.get("fixed_cache_bytes") if type(fit.get("fixed_cache_bytes")) is int else 0
    return {"budget_bytes": total, "weights_bytes": weights, "runtime_bytes": overhead, "fixed_cache_bytes": fixed,
            "kv_bytes_per_token": per_token, "native_context": native,
            "recommended_context": fit.get("recommended_context") if type(fit.get("recommended_context")) is int else None,
            "tight_margin_bytes": int(total * 0.05),
            "steps": [step for step in STEPS if step <= max(2 * native, 32768)]}


def verdict(numbers: dict[str, Any], context: int) -> dict[str, Any]:
    """fits / tight (little room left for the desktop) / spills (doesn't fit: slower, may
    stutter), and whether it is past what the model was trained for."""
    need = numbers["weights_bytes"] + numbers["runtime_bytes"] + numbers["fixed_cache_bytes"] + context * numbers["kv_bytes_per_token"]
    spare = numbers["budget_bytes"] - need
    level = "spills" if spare < 0 else "tight" if spare < numbers["tight_margin_bytes"] else "fits"
    past = context > numbers["native_context"]
    gib = lambda value: f"{abs(value) / GIB:.1f} GiB"
    texts = {"fits": f"Fits: {gib(need)} of {gib(numbers['budget_bytes'])}, {gib(spare)} to spare.",
             "tight": f"Tight: only {gib(spare)} of graphics memory left for the desktop and other apps; video, games or another model may stutter or fail to load.",
             "spills": f"Doesn't fit: {gib(spare)} would spill into system memory. Replies get several times slower, and the desktop may stutter while it runs."}
    text = texts[level]
    if past: text += f" Past what the model was trained for ({context_label(numbers['native_context'])}): it starts to lose track of long conversations and code."
    return {"level": level, "past_native": past, "need_bytes": need, "spare_bytes": spare, "text": text}


def best_fitting(numbers: dict[str, Any], up_to: int) -> int | None:
    """The largest offered context up to `up_to` (and the model's own maximum) that fits."""
    fitting = [step for step in numbers["steps"] if step <= min(up_to, numbers["native_context"]) and verdict(numbers, step)["level"] == "fits"]
    return fitting[-1] if fitting else None


# --- files ----------------------------------------------------------------------------------
def _read_json(path: Path, limit: int = 65_536) -> Any:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > limit: raise ValueError("unsafe_file")
    return json.loads(path.read_text())


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "." + secrets.token_hex(4))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as handle: handle.write(json.dumps(value, indent=2) + "\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists(): temporary.unlink()


class Builds:
    """NOVA's tailored builds: {build name: {base, context, created_at}} in ~/.config/nova."""

    def __init__(self, path: Path = BUILDS_PATH) -> None:
        self.path, self.lock = path, Lock()

    def load(self) -> dict[str, dict[str, Any]]:
        try: value = _read_json(self.path)
        except FileNotFoundError: return {}
        except (OSError, ValueError): return {}
        builds = value.get("builds") if isinstance(value, dict) and value.get("schema") == 1 else None
        if not isinstance(builds, dict): return {}
        return {name: item for name, item in builds.items() if BUILD_NAME.fullmatch(name) and isinstance(item, dict)
                and isinstance(item.get("base"), str) and type(item.get("context")) is int}

    def for_base(self, base: str) -> tuple[str, dict[str, Any]] | None:
        name = build_name(base)
        item = self.load().get(name)
        return (name, item) if item and item["base"] == base else None

    def record(self, name: str, base: str, context: int) -> None:
        with self.lock:
            builds = self.load()
            if name not in builds and len(builds) >= MAX_BUILDS: raise ValueError("too_many_builds")
            builds[name] = {"base": base, "context": context, "created_at": int(time.time())}
            _write_json(self.path, {"schema": 1, "builds": builds})

    def forget(self, name: str) -> bool:
        """Drop a deleted build's record; False when NOVA had none."""
        with self.lock:
            builds = self.load()
            if builds.pop(name, None) is None: return False
            _write_json(self.path, {"schema": 1, "builds": builds})
            return True


def _plain(name: str) -> str:
    return name[:-len(":latest")] if name.endswith(":latest") else name


def builds_of(base: str, builds: dict[str, dict[str, Any]], installed: list[str]) -> list[str]:
    """The installed tailored builds of `base`: they go when the model goes."""
    present = {_plain(name) for name in installed}
    return sorted(name for name, item in builds.items() if _plain(item["base"]) == _plain(base) and name in present)


def stale_builds(builds: dict[str, dict[str, Any]], records: list[dict[str, Any]]) -> list[tuple[str, str, int]]:
    """(build, base, context) for each build whose model was updated after it was made. Ollama
    copies the model's file list at create, so the CLI would keep running the old model and its
    old files would stay on disk. Judged by the model's largest file (its weights); a model or
    build whose files are unknown is never called stale."""
    layers = {_plain(r["id"]): r["layers"] for r in records if isinstance(r.get("id"), str) and isinstance(r.get("layers"), dict)}
    stale = []
    for name, item in sorted(builds.items()):
        base, mine = layers.get(_plain(item["base"])), layers.get(name)
        if not base or not mine: continue
        if max(base, key=base.get) not in mine: stale.append((name, item["base"], item["context"]))
    return stale


def make_build(create: Callable[[str, str, int], None], builds: Builds, base: str, context: int) -> str:
    """Create (or retune in place) the tailored build of `base` with `context` tokens."""
    if context not in STEPS: raise ValueError("invalid_context")
    name = build_name(base)
    create(name, base, context)
    builds.record(name, base, context)
    return name


# --- the OPEN CLI profile ----------------------------------------------------------------------
def tool_command(tool: str, home: Path | None = None) -> Path | None:
    """Omarchy's wrapper for the tool (~/.local/bin/<name>, which installs it on first run)."""
    spec = TOOLS.get(tool)
    if spec is None: return None
    path = (home or Path.home()) / ".local" / "bin" / spec["binary"]
    try: info = path.lstat()
    except OSError: return None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH) or not info.st_mode & stat.S_IXUSR:
        return None
    return path


def project_ok(project: str) -> Path | None:
    """An existing folder of this user's, inside the home folder (where the CLI may work)."""
    if not isinstance(project, str) or not project or len(project) > 400: return None
    path = Path(os.path.expanduser(project))
    if not path.is_absolute(): return None
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except (OSError, RuntimeError): return None
    home = Path.home().resolve()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or resolved == home or not resolved.is_relative_to(home): return None
    return resolved


NOT_PROJECTS = {"Desktop", "Downloads", "Music", "Pictures", "Videos", "Public", "Templates", "snap"}


def candidate_projects(home: Path | None = None, limit: int = 8) -> list[str]:
    """Folders in the home folder to offer as the CLI's project (the setup card lists them):
    the usual code folders first, then the others, most recently changed first."""
    home = home or Path.home()
    usual = [name for name in ("Code", "code", "Work", "Projects", "projects", "src", "dev") if (home / name).is_dir()]
    try:
        others = sorted((entry for entry in home.iterdir() if entry.is_dir() and not entry.is_symlink()
                         and not entry.name.startswith(".") and entry.name not in NOT_PROJECTS and entry.name not in usual),
                        key=lambda entry: entry.stat().st_mtime, reverse=True)
    except OSError: others = []
    return ["~/" + name for name in usual + [entry.name for entry in others]][:limit]


def profile_id(tool: str, base: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", f"{tool}-{build_name(base)[5:-4]}".lower())[:40].strip("-")


def add_profile(profile: dict[str, Any], path: Path = PROFILES_PATH) -> None:
    """Add or replace (same id) NOVA's profile in cli-profiles.json, keeping every other one.
    A file NOVA can't read safely is never overwritten."""
    try:
        data = _read_json(path)
        if not isinstance(data, dict) or data.get("schema") != 1 or not isinstance(data.get("profiles"), list): raise ValueError("invalid_profile_file")
    except FileNotFoundError:
        data = {"schema": 1, "profiles": []}
    profiles = [item for item in data["profiles"] if not (isinstance(item, dict) and item.get("id") == profile["id"])]
    if len(profiles) >= 16: raise ValueError("too_many_profiles")
    data["profiles"] = profiles + [profile]
    _write_json(path, data)


def remove_profiles(build: str, path: Path = PROFILES_PATH) -> int:
    """Remove NOVA's profiles that run `build` (deleted with its model), keeping every other
    one; returns how many went. A file NOVA can't read safely is never overwritten."""
    try: data = _read_json(path)
    except FileNotFoundError: return 0
    if not isinstance(data, dict) or data.get("schema") != 1 or not isinstance(data.get("profiles"), list): raise ValueError("invalid_profile_file")
    kept = [item for item in data["profiles"] if not (isinstance(item, dict) and item.get("tool") in TOOLS and item.get("build") == build)]
    if len(kept) == len(data["profiles"]): return 0
    _write_json(path, {**data, "profiles": kept})
    return len(data["profiles"]) - len(kept)


def opencode_profile(base: str, build: str, command: Path, project: Path, care: str) -> dict[str, Any]:
    place = "~/" + str(project.relative_to(Path.home().resolve())) if project.is_relative_to(Path.home().resolve()) else project.name
    return {"id": profile_id("opencode", base), "label": "OpenCode", "tool": "opencode", "provider": "ollama",
            "model": base, "build": build, "command": str(command), "project": str(project),
            "project_label": place, "care": care}


# --- approvals (cli_...) -------------------------------------------------------------------------
class Proposals:
    """Coding-CLI changes waiting for the user's approval in NOVA's panel."""

    def __init__(self) -> None:
        self.lock, self.items = Lock(), {}

    def create(self, public: dict[str, Any], execute: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
        identifier = "cli_" + secrets.token_urlsafe(24)
        item = {**public, "id": identifier, "coding_cli": True, "expires_at": time.time() + PROPOSAL_TTL_SECONDS}
        with self.lock:
            # One at a time: a newer request replaces an older one still waiting.
            for old in self.items.values():
                if old["decision"] is None: old["decision"], old["result"] = "replaced", {"status": "replaced"}; old["event"].set()
            self.items = {key: value for key, value in self.items.items() if value["decision"] is None or time.time() - value["created"] < 600}
            self.items[identifier] = {"public": item, "execute": execute, "decision": None, "result": None,
                                      "event": Event(), "created": time.time()}
        return item

    def pending(self) -> dict[str, Any] | None:
        with self.lock:
            for entry in self.items.values():
                if entry["decision"] is None and time.time() < entry["public"]["expires_at"]: return entry["public"]
        return None

    def decide(self, identifier: str, decision: str) -> tuple[int, dict[str, Any]]:
        with self.lock:
            entry = self.items.get(identifier)
            if entry is None: return 404, {"error": "unknown_proposal"}
            if entry["decision"] is not None: return 409, {"error": "already_decided"}
            if time.time() >= entry["public"]["expires_at"]:
                entry["decision"], entry["result"] = "expired", {"status": "expired"}; entry["event"].set()
                return 410, {"error": "expired_approval"}
            if decision not in ("approve", "deny", "alternative"): return 400, {"error": "invalid_decision"}
            if decision == "alternative" and not entry["public"].get("alternative_context"): return 400, {"error": "invalid_decision"}
            entry["decision"] = decision
        if decision == "deny": result = {"status": "denied"}
        else:
            try: result = entry["execute"](decision)
            except (OSError, ValueError, RuntimeError) as error: result = {"status": "failed", "error": str(error)[:120]}
        with self.lock:
            entry["result"] = result
            entry["event"].set()
        return 200, result

    def wait(self, identifier: str, timeout: float = PROPOSAL_TTL_SECONDS + 5) -> dict[str, Any]:
        with self.lock: entry = self.items.get(identifier)
        if entry is None: return {"status": "unknown"}
        if not entry["event"].wait(timeout):
            with self.lock:
                if entry["decision"] is None: entry["decision"], entry["result"] = "expired", {"status": "expired"}
        return entry["result"] or {"status": "expired"}
