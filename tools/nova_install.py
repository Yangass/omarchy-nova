#!/usr/bin/env python3
"""NOVA's explicit P0.2A installation lifecycle.

The tool owns only its fixed source inventory and the hashes recorded in its
ownership manifest. Omarchy plugin add/update remains separate: it never runs
backend setup hooks. Dry-run is read-only. An alternate --root is a filesystem
target, not a sandbox, and cannot use live service/shell operations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

PLUGIN_ID = "io.github.yangass.nova"
# wave.frag.qsb is the compiled living-wave shader (tools/build_wave_shader.py).
# NovaMark.qml is NOVA's drawn logo, shared by the bar and the panel header.
PLUGIN_FILES = ("manifest.json", "BarWidget.qml", "Panel.qml", "wave.frag.qsb", "NovaMark.qml")
# Pre-managed installs predate the shader; adoption verifies only these.
LEGACY_PLUGIN_FILES = ("manifest.json", "BarWidget.qml", "Panel.qml")
PANEL_IDENTITY_MARKER = 'readonly property string packagedUiIdentity: "__NOVA_PACKAGED_UI_ID__"'
RUNTIME_FILES = (
    "novad/acquisition.py", "novad/acquisition_fit.py", "novad/acquisition_integration.py",
    "novad/acquisition_lifecycle.py", "novad/catalog.py", "novad/catalog_observability.py",
    "novad/catalog_drift.py", "novad/catalog_repins.py", "novad/model_updates.py",
    "novad/chat_sessions.py",
    "novad/evidence.py", "novad/hardware.py", "novad/model_fit.py", "novad/ollama_acquisition.py",
    "novad/ollama_readiness.py", "novad/providers.py", "novad/recommendation.py",
    "novad/model_removal.py", "novad/recommendation_integration.py", "novad/registry_probe.py",
    "novad/requested_models.py",
    "novad/onboarding.py",
    "novad/onboarding_integration.py", "novad/server.py", "nova-actions/server.py",
    "nova-system-tools/server.py", "nova-system-tools/pyproject.toml", "catalog/models.json",
    "catalog/evidence.json", "catalog/acquisition_fit.json",
)
UNIT_NAMES = ("novad.service", "nova-system-tools.service", "nova-actions.service")
TEMPLATE_FOR_UNIT = {name: name + ".in" for name in UNIT_NAMES}
STATE_VERSION = 3
BYTECODE_ENV = {"PYTHONDONTWRITEBYTECODE": "1"}
LEGACY_WORKING_DIRS = {
    "novad.service": "novad",
    "nova-system-tools.service": "nova-system-tools",
    "nova-actions.service": "nova-actions",
}

class InstallError(RuntimeError): pass
class ConflictError(InstallError): pass


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""): h.update(chunk)
    return h.hexdigest()


def exists_or_symlink(path: Path) -> bool:
    """Unlike Path.exists(), treat a dangling symlink as an existing target."""
    return path.exists() or path.is_symlink()


def absolute_python_path(path: Path) -> Path:
    """Keep the operator's absolute spelling for unit identity checks."""
    value = path.expanduser()
    if not value.is_absolute():
        raise InstallError("selected Python path must be absolute")
    return value


def copy_file(source: Path, target: Path) -> None:
    if source.is_symlink(): raise ConflictError("symlink source is not installable: " + str(source))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _check_path_value(value: str) -> None:
    if "\n" in value or "\x00" in value: raise InstallError("installation paths must not contain newline or NUL")

def systemd_quote(value: str) -> str:
    _check_path_value(value)
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

def systemd_path(value: str) -> str:
    """Escape a directive path; WorkingDirectory= does not accept shell quotes."""
    _check_path_value(value)
    return value.replace("\\", "\\\\").replace(" ", "\\x20").replace("\t", "\\x09")


def expected_dirs(files: tuple[str, ...]) -> set[str]:
    result: set[str] = set()
    for name in files:
        parent = Path(name).parent
        while str(parent) != ".": result.add(parent.as_posix()); parent = parent.parent
    return result

RUNTIME_DIRS = expected_dirs(RUNTIME_FILES)

@dataclass(frozen=True)
class Paths:
    source: Path
    home: Path
    @property
    def plugin(self) -> Path: return self.home / ".config/omarchy/plugins" / PLUGIN_ID
    @property
    def runtime(self) -> Path: return self.home / ".local/share/nova/runtime"
    @property
    def units(self) -> Path: return self.home / ".config/systemd/user"
    @property
    def state_dir(self) -> Path: return self.home / ".local/state/nova/installer"
    @property
    def state(self) -> Path: return self.state_dir / "ownership.json"
    @property
    def journal(self) -> Path: return self.state_dir / "pending.json"

class Operations:
    """Effect boundary. ``isolated`` is true only for an injected test adapter."""
    isolated = False
    # --quiet drops systemd's "Created symlink" lines from the beginner's setup terminal; errors still print.
    def service(self, *args: str) -> None: subprocess.run(["systemctl", "--user", "--quiet", *args], check=True)
    def shell(self, *args: str) -> None: subprocess.run(["omarchy", *args], check=True)
    def plugin_validate(self, folder: Path) -> None: subprocess.run(["omarchy", "plugin", "validate", str(folder)], check=True)
    def unit_validate(self, unit: Path) -> None: subprocess.run(["systemd-analyze", "verify", str(unit)], check=True)
    def python_version(self, executable: Path) -> tuple[int, int]:
        result = subprocess.run([str(executable), "-c", "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"], check=True, text=True, stdout=subprocess.PIPE)
        return tuple(map(int, result.stdout.strip().split(".")))
    def service_state(self, unit: str) -> dict[str, str]:
        def query(*args: str) -> str:
            result = subprocess.run(["systemctl", "--user", *args, unit], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            value = result.stdout.strip()
            if value == "not-found": return "missing"
            if value: return value
            return "missing" if result.returncode in (3, 4) else "unknown"
        return {"enabled": query("is-enabled"), "active": query("is-active")}

    def plugin_enabled(self) -> bool:
        result = subprocess.run(["omarchy", "plugin", "list", "--json"], check=True,
                                text=True, stdout=subprocess.PIPE)
        try:
            entries = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise InstallError("cannot read Omarchy plugin state") from error
        for entry in entries:
            if isinstance(entry, dict) and entry.get("id") == PLUGIN_ID:
                value = entry.get("enabled")
                if isinstance(value, bool):
                    return value
                break
        raise InstallError("NOVA plugin state is unavailable from Omarchy")

class Installer:
    def __init__(self, paths: Paths, python: Path, plugin_mode: str = "auto", ops: Operations | None = None):
        self.paths, self.python, self.ops = paths, python, ops or Operations()
        self.plugin_mode = self._plugin_mode(plugin_mode)
        self._validate_source(); self._validate_locations()

    def _plugin_mode(self, requested: str) -> str:
        if requested != "auto": return requested
        return "git-managed" if (self.paths.plugin / ".git").exists() else "copy"

    def _validate_source(self) -> None:
        required = (*PLUGIN_FILES, *RUNTIME_FILES, *("packaging/systemd/" + v for v in TEMPLATE_FOR_UNIT.values()))
        missing = [name for name in required if not (self.paths.source / name).is_file()]
        links = [name for name in required if (self.paths.source / name).is_symlink()]
        if missing: raise InstallError("source inventory is incomplete: " + ", ".join(missing))
        if links: raise ConflictError("source inventory contains symlink: " + ", ".join(links))

    def _validate_locations(self) -> None:
        source, plugin = self.paths.source.resolve(), self.paths.plugin.resolve()
        allowed_plugin_source = self.plugin_mode == "git-managed" and source == plugin
        for destination in (self.paths.plugin, self.paths.runtime, self.paths.units, self.paths.state_dir):
            if allowed_plugin_source and destination.resolve() == source: continue
            try: destination.resolve().relative_to(source)
            except ValueError: continue
            raise InstallError("installation destination must not be inside source checkout: " + str(destination))
        if self.plugin_mode == "copy" and (self.paths.plugin / ".git").exists():
            raise ConflictError("plugin destination is git-managed; use --plugin-mode git-managed")
        if self.plugin_mode == "git-managed" and not (self.paths.plugin / ".git").exists():
            raise ConflictError("git-managed plugin checkout is missing .git: " + str(self.paths.plugin))

    def _state(self) -> dict | None:
        if not self.paths.state.exists(): return None
        try: value = json.loads(self.paths.state.read_text())
        except (OSError, json.JSONDecodeError) as error: raise InstallError("installer ownership state is unreadable; recover manually") from error
        if value.get("version") != STATE_VERSION or value.get("plugin_id") != PLUGIN_ID:
            raise InstallError("installer ownership state is unsupported")
        return value

    def _pending(self) -> dict | None:
        if not self.paths.journal.exists(): return None
        try: value = json.loads(self.paths.journal.read_text())
        except (OSError, json.JSONDecodeError) as error: raise InstallError("pending installer journal is unreadable; recover manually") from error
        if not isinstance(value.get("transaction"), str) or not isinstance(value.get("backup"), dict):
            raise InstallError("pending installer journal is unsupported; recover manually")
        return value

    def _check_python(self) -> tuple[int, int]:
        try:
            executable = self.python.resolve(strict=True)
        except FileNotFoundError as error:
            raise InstallError("selected Python is not executable: " + str(self.python)) from error
        if not executable.is_file() or not os.access(executable, os.X_OK): raise InstallError("selected Python is not executable: " + str(self.python))
        version = self.ops.python_version(executable)
        if version < (3, 11): raise InstallError("NOVA requires Python 3.11 or newer")
        return version

    def _render_unit(self, unit: str) -> str:
        source = self.paths.source / "packaging/systemd" / TEMPLATE_FOR_UNIT[unit]
        working_dir = {"novad.service": "novad", "nova-system-tools.service": "nova-system-tools", "nova-actions.service": "nova-actions"}[unit]
        value = source.read_text().replace("@NOVA_ROOT@/" + working_dir, systemd_path(str(self.paths.runtime / working_dir)))
        value = value.replace("@PYTHON@", systemd_quote(str(self.python)))
        if "@NOVA_ROOT@" in value or "@PYTHON@" in value: raise InstallError("unrendered service placeholder")
        return value

    def _assert_runtime_tree(self, *, owned_files: tuple[str, ...] | list[str] | None = None,
                             allow_subset: bool = False) -> None:
        root = self.paths.runtime
        if not root.exists(): return
        if root.is_symlink() or not root.is_dir(): raise ConflictError("runtime root is not a real directory: " + str(root))
        files: set[str] = set(); dirs: set[str] = set()
        for current, names, children in os.walk(root, followlinks=False):
            current_path = Path(current); relative_current = current_path.relative_to(root)
            if relative_current != Path("."): dirs.add(relative_current.as_posix())
            for name in list(names) + list(children):
                item = current_path / name
                if item.is_symlink(): raise ConflictError("runtime tree contains symlink: " + str(item))
            for name in children: files.add((current_path / name).relative_to(root).as_posix())
        inventory = tuple(owned_files) if owned_files is not None else RUNTIME_FILES
        allowed_files, allowed_dirs = set(inventory), expected_dirs(inventory)
        bad_files, bad_dirs = files - allowed_files, dirs - allowed_dirs
        if bad_files or bad_dirs: raise ConflictError("runtime tree contains unowned entries: " + ", ".join(sorted(bad_files | bad_dirs)))
        if not allow_subset and (files != allowed_files or dirs != allowed_dirs):
            raise ConflictError("runtime tree is incomplete or differs from ownership inventory")

    def _assert_target(self, path: Path, state: dict | None, key: str) -> None:
        if not path.exists(): return
        if path.is_symlink(): raise ConflictError("target must not be symlink: " + str(path))
        expected = (state or {}).get("hashes", {}).get(key)
        if expected is None: raise ConflictError("unmanaged file would be overwritten: " + str(path))
        if digest(path) != expected: raise ConflictError("user-modified owned file would be overwritten: " + str(path))

    def _check_conflicts(self, state: dict | None) -> None:
        if self.paths.runtime.exists():
            if state is None: raise ConflictError("unmanaged runtime directory exists: " + str(self.paths.runtime))
            prior_inventory = state.get("owned", {}).get("runtime_files")
            if not isinstance(prior_inventory, list) or not all(isinstance(name, str) for name in prior_inventory):
                raise ConflictError("ownership runtime inventory is invalid")
            self._assert_runtime_tree(owned_files=prior_inventory)
        if self.plugin_mode == "copy":
            for name in PLUGIN_FILES: self._assert_target(self.paths.plugin / name, state, "plugin/" + name)
        for name in RUNTIME_FILES: self._assert_target(self.paths.runtime / name, state, "runtime/" + name)
        for unit in UNIT_NAMES: self._assert_target(self.paths.units / unit, state, "unit/" + unit)

    def plan(self, operation: str) -> dict:
        pending = self._pending()
        result = {"operation": operation, "source": str(self.paths.source),
                  "plugin": {"mode": self.plugin_mode, "path": str(self.paths.plugin),
                             "files": list(PLUGIN_FILES) if self.plugin_mode == "copy" else [],
                             "validation_target": str(self.paths.plugin if self.plugin_mode == "git-managed" else self.paths.source),
                             "note": "Git checkout is never copied, removed, or modified by NOVA" if self.plugin_mode == "git-managed" else "NOVA-owned source files only"},
                  "runtime": {"path": str(self.paths.runtime), "files": list(RUNTIME_FILES)},
                  "services": {"directory": str(self.paths.units), "units": list(UNIT_NAMES), "state": "not queried in plan"},
                  "state": str(self.paths.state), "credentials": "preserved; existing ~/.config/nova credentials are never provisioned or replaced",
                  "preserves": ["~/.config/nova", "Ollama and downloaded models", "unrelated shell settings", "source checkout"],
                  "requires_operator_attested_idle": operation != "plan",
                  "pending_recovery": {"required": bool(pending), "note": "apply performs file recovery before conflict checks; dry-run never mutates"}}
        if pending: return result
        if operation != "remove":
            version = self._check_python(); result["python"] = {"path": str(self.python), "version": ".".join(map(str, version))}
            self._check_conflicts(self._state())
        return result

    def _write_json_atomic(self, path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as file:
                json.dump(value, file, indent=2, sort_keys=True); file.write("\n"); temporary = Path(file.name)
            os.chmod(temporary, 0o600); os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def _verify_backup(self, backup: Path) -> set[str]:
        if backup.is_symlink() or not backup.is_dir():
            raise ConflictError("backup root must be a real directory: " + str(backup))
        if (backup.stat().st_mode & 0o077) != 0:
            raise ConflictError("backup root permissions are not restrictive: " + str(backup))
        for directory in (backup / "metadata", backup / "plugin", backup / "units"):
            if directory.is_symlink() or not directory.is_dir():
                raise ConflictError("backup layout has a missing or symlinked directory: " + str(directory))
        manifest = backup / "metadata/SHA256SUMS"
        if manifest.is_symlink() or not manifest.is_file():
            raise ConflictError("backup checksum manifest is missing or symlinked")
        if (manifest.stat().st_mode & 0o077) != 0:
            raise ConflictError("backup checksum manifest permissions are not restrictive")
        root = backup.resolve()
        verified: set[str] = set()
        for line in manifest.read_text().splitlines():
            match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
            if not match:
                raise InstallError("backup checksum manifest has an invalid entry")
            entry = Path(match.group(2))
            if entry.is_absolute():
                try:
                    relative = entry.relative_to(root)
                except ValueError as error:
                    raise InstallError("backup checksum manifest path is outside the supplied backup") from error
            else:
                relative = entry
            if not relative.parts or ".." in relative.parts:
                raise InstallError("backup checksum manifest has an unsafe path")
            item = root / relative
            cursor = root
            for part in relative.parts:
                cursor = cursor / part
                if cursor.is_symlink():
                    raise InstallError("backup checksum manifest path crosses a symlink")
            try:
                item.resolve(strict=True).relative_to(root)
            except FileNotFoundError as error:
                raise ConflictError("backup integrity check failed: " + relative.as_posix()) from error
            except ValueError as error:
                raise InstallError("backup checksum manifest path escapes the supplied backup") from error
            if not item.is_file() or digest(item) != match.group(1):
                raise ConflictError("backup integrity check failed: " + relative.as_posix())
            verified.add(relative.as_posix())
        return verified

    def _assert_adoption_file(self, current: Path, saved: Path, source: Path | None = None) -> str:
        if current.is_symlink() or saved.is_symlink():
            raise ConflictError("adoption target must not be a symlink: " + str(current))
        if not current.is_file() or not saved.is_file():
            raise ConflictError("adoption target is missing: " + str(current))
        value = digest(current)
        if value != digest(saved):
            raise ConflictError("current file differs from verified backup: " + str(current))
        if source is not None:
            if source.is_symlink() or not source.is_file() or value != digest(source):
                raise ConflictError("plugin file differs from canonical source: " + str(current))
        return value

    def _assert_legacy_unit_structure(self, unit: str, path: Path) -> None:
        lines = path.read_text().splitlines()
        expected_working = "WorkingDirectory=" + systemd_path(str(self.paths.source / LEGACY_WORKING_DIRS[unit]))
        if lines.count(expected_working) != 1:
            raise ConflictError("legacy unit has an unexpected working directory: " + str(path))
        expected_restart = ("Restart=on-failure", "RestartSec=2", "Environment=PYTHONUNBUFFERED=1")
        if any(lines.count(item) != 1 for item in expected_restart):
            raise ConflictError("legacy unit does not preserve reviewed restart environment: " + str(path))
        starts = [line.partition("=")[2] for line in lines if line.startswith("ExecStart=")]
        if len(starts) != 1:
            raise ConflictError("legacy unit must have one explicit ExecStart: " + str(path))
        try:
            command = shlex.split(starts[0])
        except ValueError as error:
            raise ConflictError("legacy unit has an unreadable ExecStart: " + str(path)) from error
        if command != [str(self.python), "server.py"]:
            raise ConflictError("legacy unit ExecStart differs from selected Python: " + str(path))

    def _adoption_state(self, hashes: dict[str, str], plugin_enabled: bool, backup: Path) -> dict:
        return {"version": STATE_VERSION, "plugin_id": PLUGIN_ID, "plugin_mode": "copy",
                "installation_state": "legacy_adopted", "plugin_enabled": plugin_enabled,
                "adopted_at": int(time.time()), "backup_manifest_sha256": digest(backup / "metadata/SHA256SUMS"),
                "hashes": hashes,
                "owned": {"plugin_files": list(LEGACY_PLUGIN_FILES), "runtime_files": [], "units": list(UNIT_NAMES)}}

    def _plan_adoption(self, backup: Path | None) -> dict:
        if backup is None:
            raise InstallError("adopt-legacy requires --backup")
        if self.plugin_mode != "copy":
            raise ConflictError("adopt-legacy only supports the verified copy-managed legacy plugin")
        if exists_or_symlink(self.paths.state) or exists_or_symlink(self.paths.journal):
            raise ConflictError("existing installer ownership state or pending transaction blocks adoption")
        if self.paths.state_dir.is_symlink():
            raise ConflictError("installer state directory must not be a symlink")
        if exists_or_symlink(self.paths.runtime):
            raise ConflictError("legacy adoption requires the target runtime directory to be absent")
        if self.paths.plugin.is_symlink() or not self.paths.plugin.is_dir() or self.paths.units.is_symlink() or not self.paths.units.is_dir():
            raise ConflictError("legacy plugin and unit directories must be real directories")
        verified = self._verify_backup(backup)
        expected_backup = {*("plugin/" + name for name in LEGACY_PLUGIN_FILES), *("units/" + unit for unit in UNIT_NAMES)}
        missing_verified = expected_backup - verified
        if missing_verified:
            raise ConflictError("backup checksum manifest omits adoption target: " + ", ".join(sorted(missing_verified)))
        hashes: dict[str, str] = {}
        for name in LEGACY_PLUGIN_FILES:
            hashes["plugin/" + name] = self._assert_adoption_file(self.paths.plugin / name, backup / "plugin" / name, self.paths.source / name)
        for unit in UNIT_NAMES:
            current = self.paths.units / unit
            hashes["unit/" + unit] = self._assert_adoption_file(current, backup / "units" / unit)
            self._assert_legacy_unit_structure(unit, current)
            override = self.paths.units / (unit + ".d")
            if exists_or_symlink(override):
                raise ConflictError("legacy unit has an unexpected override directory: " + str(override))
        return {"operation": "adopt-legacy", "backup": str(backup), "plugin": {"mode": "copy", "files": list(LEGACY_PLUGIN_FILES)},
                "runtime": {"path": str(self.paths.runtime), "installed": False}, "units": list(UNIT_NAMES),
                "hashes": hashes, "plugin_enabled": self.ops.plugin_enabled(),
                "preserves": ["extra plugin files", "source checkout", "~/.config/nova", "NOVA user data", "Ollama and downloaded models"]}

    def _adopt_legacy(self, backup: Path | None, *, dry_run: bool = False) -> dict:
        candidate = backup.absolute() if backup is not None else None
        plan = self._plan_adoption(candidate)
        if dry_run:
            plan["dry_run"] = True
            return plan
        self._assert_effect_isolation(False)
        self._write_json_atomic(self.paths.state, self._adoption_state(plan["hashes"], plan["plugin_enabled"], candidate))  # type: ignore[arg-type]
        plan["adopted"] = True
        return plan

    def _stage_plugin(self) -> Path | None:
        if self.plugin_mode == "git-managed": return None
        stage = Path(tempfile.mkdtemp(prefix="plugin.stage-", dir=self.paths.home))
        try:
            for name in PLUGIN_FILES: copy_file(self.paths.source / name, stage / name)
            panel = stage / "Panel.qml"
            source_text = panel.read_text()
            if source_text.count(PANEL_IDENTITY_MARKER) != 1:
                raise InstallError("Panel.qml build-identity marker is missing or duplicated")
            try:
                version = json.loads((stage / "manifest.json").read_text()).get("version")
            except (OSError, ValueError, AttributeError):
                version = None
            version_label = "v" + version if isinstance(version, str) and re.fullmatch(r"\d+\.\d+\.\d+", version) else "version unknown"
            fingerprint = hashlib.sha256()
            for name in PLUGIN_FILES:
                payload = (stage / name).read_bytes()
                fingerprint.update(name.encode() + b"\0" + len(payload).to_bytes(8, "big") + payload)
            identity = version_label + " · ui-" + fingerprint.hexdigest()[:12]
            panel.write_text(source_text.replace(PANEL_IDENTITY_MARKER,
                "readonly property string packagedUiIdentity: " + json.dumps(identity, ensure_ascii=False)))
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise
        return stage

    def _stage_runtime(self) -> Path:
        self.paths.runtime.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix="runtime.stage-", dir=self.paths.runtime.parent))
        for name in RUNTIME_FILES: copy_file(self.paths.source / name, stage / name)
        if not (stage / "novad/server.py").is_file() or not (stage / "catalog/models.json").is_file(): raise InstallError("runtime staging validation failed")
        return stage

    def _stage_units(self) -> Path:
        stage = Path(tempfile.mkdtemp(prefix="units.stage-", dir=self.paths.home))
        for unit in UNIT_NAMES:
            target = stage / unit; target.write_text(self._render_unit(unit)); self.ops.unit_validate(target)
        return stage

    def _validate_staged(self, plugin: Path | None) -> None:
        self.ops.plugin_validate(self.paths.plugin if self.plugin_mode == "git-managed" else plugin)  # type: ignore[arg-type]

    def _backup(self, transaction: Path, state: dict | None, service_state: dict[str, dict[str, str]]) -> dict:
        backup = {"plugin": [], "units": [], "runtime": False, "state": False,
                  "service_state": service_state, "plugin_enabled": bool((state or {}).get("plugin_enabled"))}
        if self.plugin_mode == "copy":
            for name in PLUGIN_FILES:
                source = self.paths.plugin / name
                if source.exists(): copy_file(source, transaction / "plugin" / name); backup["plugin"].append(name)
        for unit in UNIT_NAMES:
            source = self.paths.units / unit
            if source.exists(): copy_file(source, transaction / "units" / unit); backup["units"].append(unit)
        if self.paths.runtime.exists(): shutil.copytree(self.paths.runtime, transaction / "runtime"); backup["runtime"] = True
        if self.paths.state.exists(): copy_file(self.paths.state, transaction / "ownership.json"); backup["state"] = True
        return backup

    def _restore_files(self, journal: dict) -> None:
        transaction, backup = Path(journal["transaction"]), journal["backup"]
        if self.plugin_mode == "copy":
            for name in PLUGIN_FILES:
                target, saved = self.paths.plugin / name, transaction / "plugin" / name
                if saved.exists(): copy_file(saved, target)
                elif target.exists(): target.unlink()
        for unit in UNIT_NAMES:
            target, saved = self.paths.units / unit, transaction / "units" / unit
            if saved.exists(): copy_file(saved, target)
            elif target.exists(): target.unlink()
        if self.paths.runtime.exists():
            self._assert_runtime_tree(allow_subset=True); shutil.rmtree(self.paths.runtime)
        if backup.get("runtime"): shutil.copytree(transaction / "runtime", self.paths.runtime)
        saved_state = transaction / "ownership.json"
        if backup.get("state") and saved_state.exists(): copy_file(saved_state, self.paths.state)
        elif self.paths.state.exists(): self.paths.state.unlink()

    def recover_pending(self) -> dict | None:
        journal = self._pending()
        if journal is None: return None
        self._restore_files(journal)
        return {"recovered": True, "journal": journal, "service_reconciliation_required": True,
                "prior_service_state": journal["backup"].get("service_state", {})}

    def _finish_transaction(self, journal: dict) -> None:
        self.paths.journal.unlink(missing_ok=True)
        shutil.rmtree(Path(journal["transaction"]), ignore_errors=True)

    def _service_snapshot(self, existing: bool) -> dict[str, dict[str, str]]:
        values = {unit: self.ops.service_state(unit) for unit in UNIT_NAMES}
        for unit, value in values.items():
            if value.get("enabled") == "unknown" or value.get("active") == "unknown":
                raise InstallError("service state is unknown for " + unit + "; do not mutate backend files")
            if existing and value.get("active") == "missing":
                raise InstallError("recorded installation has missing service state for " + unit + "; recover manually")
        return values

    def _restore_services(self, snapshot: dict[str, dict[str, str]]) -> None:
        self.ops.service("daemon-reload")
        for unit, state in snapshot.items():
            # A missing prior unit must stay missing; do not ask systemd to act
            # on it merely because a later transaction touched other units.
            if state.get("enabled") == "missing":
                if state.get("active") not in ("missing", "inactive", "failed"):
                    raise InstallError("inconsistent missing service state for " + unit)
                continue
            if state.get("enabled") == "enabled": self.ops.service("enable", unit)
            elif state.get("enabled") == "disabled": self.ops.service("disable", unit)
            else: raise InstallError("cannot restore unknown enablement for " + unit)
            if state.get("active") == "active": self.ops.service("start", unit)
            elif state.get("active") in ("inactive", "failed", "missing"): self.ops.service("stop", unit)
            else: raise InstallError("cannot restore unknown activity for " + unit)

    def _restore_plugin_state(self, backup: dict) -> None:
        if self.plugin_mode == "copy" and backup.get("plugin_enabled"):
            self.ops.shell("plugin", "enable", PLUGIN_ID)

    def _stop_active_services(self, snapshot: dict[str, dict[str, str]]) -> None:
        # Stop old active services before replacing their runtime. enable --now is not a restart.
        for unit, state in snapshot.items():
            if state.get("active") == "active": self.ops.service("stop", unit)

    def _start_staged_services(self) -> None:
        self.ops.service("daemon-reload")
        for unit in UNIT_NAMES:
            self.ops.service("enable", unit)
            self.ops.service("start", unit)

    def _install_files(self, plugin_stage: Path | None, runtime_stage: Path, units_stage: Path,
                       prior_runtime_files: tuple[str, ...] | list[str] | None = None) -> None:
        if self.plugin_mode == "copy":
            for name in PLUGIN_FILES: copy_file(plugin_stage / name, self.paths.plugin / name)  # type: ignore[operator]
        if self.paths.runtime.exists():
            self._assert_runtime_tree(owned_files=prior_runtime_files)
            shutil.rmtree(self.paths.runtime)
        shutil.move(str(runtime_stage), str(self.paths.runtime))
        for unit in UNIT_NAMES: copy_file(units_stage / unit, self.paths.units / unit)

    def _new_state(self) -> dict:
        hashes = {"runtime/" + name: digest(self.paths.runtime / name) for name in RUNTIME_FILES}
        hashes.update({"unit/" + unit: digest(self.paths.units / unit) for unit in UNIT_NAMES})
        if self.plugin_mode == "copy": hashes.update({"plugin/" + name: digest(self.paths.plugin / name) for name in PLUGIN_FILES})
        return {"version": STATE_VERSION, "plugin_id": PLUGIN_ID, "plugin_mode": self.plugin_mode,
                "installation_state": "managed",
                "plugin_enabled": self.plugin_mode == "copy", "installed_at": int(time.time()), "hashes": hashes,
                "owned": {"plugin_files": list(PLUGIN_FILES) if self.plugin_mode == "copy" else [], "runtime_files": list(RUNTIME_FILES), "units": list(UNIT_NAMES)}}

    def _assert_effect_isolation(self, dry_run: bool) -> None:
        if dry_run: return
        if self.paths.home.resolve() != Path.home().resolve() and not self.ops.isolated:
            raise InstallError("--root is not a sandbox; alternate target homes require an injected isolated operations adapter")

    def _require_idle(self, confirm_idle: bool, idle_state: str, restart_shell: bool) -> None:
        if idle_state != "idle": raise InstallError("backend/QML change blocked: NOVA activity state is " + idle_state + "; unknown or unreachable is not idle")
        if not confirm_idle: raise InstallError("backend/QML change requires --confirm-idle after checking chat, approval, action, and acquisition activity")
        if self.plugin_mode == "copy" and not restart_shell: raise InstallError("copy-managed QML deployment requires --restart-shell after idle confirmation")

    def apply(self, operation: str, *, backup: Path | None = None, dry_run: bool = False, restart_shell: bool = False, confirm_idle: bool = False, idle_state: str = "unknown") -> dict:
        if operation == "plan": result = self.plan("plan"); return result
        if operation == "adopt-legacy":
            if restart_shell or confirm_idle or idle_state != "unknown":
                raise InstallError("adopt-legacy records ownership only; it does not accept deployment or idle flags")
            return self._adopt_legacy(backup, dry_run=dry_run)
        if operation not in ("setup", "update", "remove"): raise InstallError("unknown operation")
        if dry_run:
            result = self.plan(operation); result["dry_run"] = True; return result
        self._assert_effect_isolation(False); self._require_idle(confirm_idle, idle_state, restart_shell)
        recovery = self.recover_pending()  # Must happen before state/conflict checks.
        if recovery:
            try:
                self._restore_services(recovery["prior_service_state"])
                self._restore_plugin_state(recovery["journal"]["backup"])
            except Exception as error:
                raise InstallError("pending files were restored, but service reconciliation requires manual inspection") from error
            self._finish_transaction(recovery["journal"])
        if operation == "remove": return self._remove(restart_shell=restart_shell)
        plan = self.plan(operation); state = self._state(); self._check_conflicts(state)
        snapshot = self._service_snapshot(existing=state is not None)
        plugin_stage = self._stage_plugin(); runtime_stage = self._stage_runtime(); units_stage = self._stage_units()
        self._validate_staged(plugin_stage)  # all validation precedes deployed-file mutation
        self.paths.state_dir.parent.mkdir(parents=True, exist_ok=True)
        transaction = Path(tempfile.mkdtemp(prefix="transaction-", dir=self.paths.state_dir.parent))
        journal = {"transaction": str(transaction), "operation": operation, "backup": self._backup(transaction, state, snapshot)}
        self._write_json_atomic(self.paths.journal, journal)
        completed = False
        rollback_complete = False
        try:
            self._stop_active_services(snapshot)
            prior_runtime_files = state.get("owned", {}).get("runtime_files") if state else None
            self._install_files(plugin_stage, runtime_stage, units_stage, prior_runtime_files)
            self._start_staged_services()
            if self.plugin_mode == "copy": self.ops.shell("plugin", "enable", PLUGIN_ID)
            if restart_shell: self.ops.shell("restart", "shell")
            self._write_json_atomic(self.paths.state, self._new_state())
            completed = True
        except Exception as error:
            try:
                self._restore_files(journal); self._restore_services(snapshot); rollback_complete = True
            except Exception:
                pass
            if not rollback_complete:
                raise InstallError("deployment failed; files were restored where possible, but service restoration requires manual inspection") from error
            raise
        finally:
            if completed or rollback_complete:
                self._finish_transaction(journal)
            if plugin_stage: shutil.rmtree(plugin_stage, ignore_errors=True)
            shutil.rmtree(runtime_stage, ignore_errors=True); shutil.rmtree(units_stage, ignore_errors=True)
        plan.update({"applied": True, "recovery_performed": bool(recovery), "service_snapshot": snapshot})
        return plan

    def _remove(self, *, restart_shell: bool) -> dict:
        state = self._state()
        if state is None:
            return {"operation": "remove", "status": "nothing_owned", "preserves": ["~/.config/nova", "Ollama and downloaded models", "source checkout"]}
        if state.get("installation_state") == "legacy_adopted":
            raise ConflictError("legacy-adopted files must migrate through setup; removal is not a legacy rollback")
        self._check_conflicts(state)
        snapshot = self._service_snapshot(existing=True)
        # Validate every owned file before the first service or filesystem mutation.
        for unit in UNIT_NAMES: self._assert_target(self.paths.units / unit, state, "unit/" + unit)
        if state.get("plugin_mode") == "copy":
            for name in PLUGIN_FILES: self._assert_target(self.paths.plugin / name, state, "plugin/" + name)
        self._assert_runtime_tree(owned_files=state.get("owned", {}).get("runtime_files"))
        self.paths.state_dir.parent.mkdir(parents=True, exist_ok=True)
        transaction = Path(tempfile.mkdtemp(prefix="transaction-", dir=self.paths.state_dir.parent))
        journal = {"transaction": str(transaction), "operation": "remove", "backup": self._backup(transaction, state, snapshot)}
        self._write_json_atomic(self.paths.journal, journal)
        completed = False
        rollback_complete = False
        try:
            for unit in UNIT_NAMES: self.ops.service("disable", "--now", unit)
            # Disable while the manifest still exists, then remove owned content.
            if state.get("plugin_mode") == "copy": self.ops.shell("plugin", "disable", PLUGIN_ID)
            for unit in UNIT_NAMES:
                path = self.paths.units / unit
                if path.exists(): path.unlink()
            if self.paths.runtime.exists(): shutil.rmtree(self.paths.runtime)
            # Publish the removed unit set before declaring success.
            self.ops.service("daemon-reload")
            if state.get("plugin_mode") == "copy":
                for name in PLUGIN_FILES:
                    path = self.paths.plugin / name
                    if path.exists(): path.unlink()
                if restart_shell: self.ops.shell("restart", "shell")
            self.paths.state.unlink(missing_ok=True)
            completed = True
        except Exception as error:
            try:
                self._restore_files(journal)
                self._restore_services(snapshot)
                self._restore_plugin_state(journal["backup"])
                rollback_complete = True
            except Exception:
                pass
            if not rollback_complete:
                raise InstallError("removal failed; files were restored where possible, but service reconciliation requires manual inspection") from error
            raise
        finally:
            if completed or rollback_complete:
                self._finish_transaction(journal)
                try: self.paths.state_dir.rmdir()
                except OSError: pass
        return {"operation": "remove", "status": "removed", "service_snapshot": snapshot,
                "preserves": ["~/.config/nova", "Ollama and downloaded models", "unrelated shell settings", "source checkout"]}

def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="NOVA explicit installation lifecycle")
    value.add_argument("operation", choices=("plan", "setup", "update", "remove", "adopt-legacy"))
    value.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    value.add_argument("--root", type=Path, default=Path.home(), help="target home; not an execution sandbox")
    value.add_argument("--python", type=Path, default=Path(sys.executable), help="Python 3.11+ for NOVA services")
    value.add_argument("--plugin-mode", choices=("auto", "copy", "git-managed"), default="auto")
    value.add_argument("--backup", type=Path, help="private verified legacy backup; required only by adopt-legacy")
    value.add_argument("--dry-run", action="store_true")
    value.add_argument("--restart-shell", action="store_true")
    value.add_argument("--confirm-idle", action="store_true")
    value.add_argument("--idle-state", choices=("idle", "busy", "unreachable", "unknown"), default="unknown", help="operator-attested state; it is not automatic verification")
    value.add_argument("--json", action="store_true", help="print the full machine-readable result")
    return value


def novad_reachable(url: str = "http://127.0.0.1:8790/health") -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response: return response.status == 200
    except (urllib.error.URLError, OSError, ValueError): return False


def first_install(paths: Paths) -> bool:
    """Nothing of NOVA's is installed or running, so there is no activity to protect."""
    return (not exists_or_symlink(paths.state) and not exists_or_symlink(paths.journal)
            and not novad_reachable())


def summary(operation: str, result: dict, mode: str) -> str:
    """Plain-language outcome for people; --json keeps the full record."""
    if result.get("dry_run"):
        return "Plan only: nothing was changed. Re-run without --dry-run to apply."
    if operation == "remove":
        return "NOVA's services and owned files were removed. Your models, settings and install records are kept."
    if operation in ("setup", "update"):
        lines = ["\u2713 NOVA is " + ("installed." if operation == "setup" else "updated."),
                 "  Background services: novad, nova-system-tools, nova-actions (running for your user).",
                 "  Your models, Ollama and NOVA settings were not touched."]
        lines.append("  Open NOVA from the robot icon in your top bar." if mode == "copy"
                     else "  The NOVA panel connects by itself within a few seconds.")
        return "\n".join(lines)
    return json.dumps(result, indent=2, sort_keys=True)

def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        paths = Paths(args.source.resolve(), args.root.resolve())
        installer = Installer(paths, absolute_python_path(args.python), args.plugin_mode)
        confirm, idle, restart = args.confirm_idle, args.idle_state, args.restart_shell
        if (args.operation == "setup" and not args.dry_run and idle == "unknown" and not confirm
                and paths.home.resolve() == Path.home().resolve() and first_install(paths)):
            # First install: no ownership record, no pending journal, no running novad.
            confirm, idle = True, "idle"
            restart = restart or installer.plugin_mode == "copy"
        result = installer.apply(args.operation, backup=args.backup, dry_run=args.dry_run,
                                 restart_shell=restart, confirm_idle=confirm, idle_state=idle)
    except InstallError as error:
        print("NOVA install: " + str(error), file=sys.stderr); return 2
    print(json.dumps(result, indent=2, sort_keys=True) if args.json else summary(args.operation, result, installer.plugin_mode))
    return 0

if __name__ == "__main__": raise SystemExit(main())
