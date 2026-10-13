from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/nova_install.py"
spec = importlib.util.spec_from_file_location("nova_install", TOOL)
install = importlib.util.module_from_spec(spec); assert spec and spec.loader
sys.modules[spec.name] = install; spec.loader.exec_module(install)

class MockOps(install.Operations):
    isolated = True
    def __init__(self, fail=None, states=None): self.calls, self.fail, self.states = [], fail, states or {}
    def service(self, *args):
        self.calls.append(("service", *args))
        if self.fail == args: raise RuntimeError("simulated service failure")
    def shell(self, *args):
        self.calls.append(("shell", *args))
        if self.fail == args: raise RuntimeError("simulated shell failure")
    def plugin_validate(self, folder): self.calls.append(("plugin_validate", str(folder)))
    def unit_validate(self, unit): self.calls.append(("unit_validate", str(unit)))
    def python_version(self, executable): return (3, 11)
    def service_state(self, unit): return self.states.get(unit, {"enabled": "enabled", "active": "active"})
    def plugin_enabled(self): return True

class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nova installer test ")
        self.root = Path(self.temp.name)
        self.source = self.root / "source checkout with spaces"; self.source.mkdir()
        self.populate_source(self.source)
        self.home = self.root / "target home with spaces"; self.home.mkdir()
        self.ops = MockOps(); self.python = Path("/usr/bin/python3")
    def tearDown(self): self.temp.cleanup()
    def populate_source(self, target):
        for name in (*install.PLUGIN_FILES, *install.RUNTIME_FILES):
            path = target / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes((ROOT / name).read_bytes())
        for name in install.TEMPLATE_FOR_UNIT.values():
            path = target / "packaging/systemd" / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes((ROOT / "packaging/systemd" / name).read_bytes())
    def app(self, mode="copy", ops=None, source=None, home=None, python=None):
        return install.Installer(install.Paths(source or self.source, home or self.home), python or self.python, mode, ops or self.ops)
    def mutate(self, operation, app=None, **extra):
        return (app or self.app()).apply(operation, restart_shell=True, confirm_idle=True, idle_state="idle", **extra)

    def legacy_backup(self, *, absolute_manifest=False, python=None):
        plugin = self.home / ".config/omarchy/plugins" / install.PLUGIN_ID
        units = self.home / ".config/systemd/user"
        plugin.mkdir(parents=True, exist_ok=True); units.mkdir(parents=True, exist_ok=True)
        for name in install.LEGACY_PLUGIN_FILES:
            shutil.copy2(self.source / name, plugin / name)
        for unit, directory in install.LEGACY_WORKING_DIRS.items():
            (units / unit).write_text("\n".join((
                "[Service]", "WorkingDirectory=" + install.systemd_path(str(self.source / directory)),
                "ExecStart=" + install.systemd_quote(str(python or self.python)) + " server.py", "Environment=PYTHONUNBUFFERED=1",
                "Restart=on-failure", "RestartSec=2", "")))
        extra = plugin / "historical-panel-backup.qml"; extra.write_text("preserve")
        backup = Path(tempfile.mkdtemp(prefix="private backup ", dir=self.root)); (backup / "plugin").mkdir(); (backup / "units").mkdir()
        for name in install.LEGACY_PLUGIN_FILES: shutil.copy2(plugin / name, backup / "plugin" / name)
        for unit in install.UNIT_NAMES: shutil.copy2(units / unit, backup / "units" / unit)
        metadata = backup / "metadata"; metadata.mkdir()
        entries = []
        def entry(item):
            name = str(item) if absolute_manifest else item.relative_to(backup).as_posix()
            return f"{install.digest(item)}  {name}"
        for item in sorted((backup / "plugin").iterdir()): entries.append(entry(item))
        for item in sorted((backup / "units").iterdir()): entries.append(entry(item))
        (metadata / "SHA256SUMS").write_text("\n".join(entries) + "\n")
        os.chmod(metadata / "SHA256SUMS", 0o600)
        os.chmod(backup, 0o700)
        return backup, extra

    def adopt(self, backup, app=None, **extra):
        return (app or self.app()).apply("adopt-legacy", backup=backup, **extra)

    def test_first_setup_repeat_update_and_dry_run(self):
        dry = self.app().apply("setup", dry_run=True)
        self.assertTrue(dry["dry_run"]); self.assertFalse(self.home.joinpath(".local/share/nova/runtime").exists())
        self.assertTrue(self.mutate("setup")["applied"])
        self.mutate("update")
        self.assertIn(("service", "stop", "novad.service"), self.ops.calls)
        self.assertIn(("service", "start", "novad.service"), self.ops.calls)
        self.assertIn(("shell", "restart", "shell"), self.ops.calls)

    def test_copy_managed_ui_identity_is_staged_and_versioned(self):
        app = self.app()
        first = app._stage_plugin()
        self.assertIsNotNone(first)
        first_panel = (first / "Panel.qml").read_text()
        self.assertIn('packagedUiIdentity: "v0.14.6 · ui-', first_panel)
        self.assertNotIn(install.PANEL_IDENTITY_MARKER, first_panel)
        repeat = app._stage_plugin()
        self.assertEqual(first_panel, (repeat / "Panel.qml").read_text())
        (self.source / "BarWidget.qml").write_text((self.source / "BarWidget.qml").read_text() + "\n// changed UI source\n")
        changed = app._stage_plugin()
        self.assertNotEqual(first_panel, (changed / "Panel.qml").read_text())
        self.assertEqual((first / "manifest.json").read_bytes(), (self.source / "manifest.json").read_bytes())
        for folder in (first, repeat, changed): shutil.rmtree(folder)

    def test_ui_identity_upgrade_rollback_removal_and_git_managed_fallback(self):
        app = self.app()
        self.mutate("setup", app)
        installed = self.home / ".config/omarchy/plugins" / install.PLUGIN_ID / "Panel.qml"
        original = installed.read_bytes()
        self.assertIn(b'packagedUiIdentity: "v0.14.6', original)
        (self.source / "Panel.qml").write_text((self.source / "Panel.qml").read_text() + "\n// next UI\n")
        failing = self.app(ops=MockOps(fail=("start", "novad.service")))
        with self.assertRaises(RuntimeError): self.mutate("update", failing)
        self.assertEqual(installed.read_bytes(), original)
        self.mutate("update", app)
        self.assertNotEqual(installed.read_bytes(), original)
        state = json.loads(app.paths.state.read_text())
        self.assertEqual(state["hashes"]["plugin/Panel.qml"], install.digest(installed))
        self.mutate("remove", app)
        self.assertFalse(installed.exists())
        git_source = self.home / ".config/omarchy/plugins" / install.PLUGIN_ID
        shutil.copytree(self.source, git_source, dirs_exist_ok=True)
        (git_source / ".git").mkdir()
        git_app = self.app(mode="git-managed", source=git_source)
        self.assertIsNone(git_app._stage_plugin())
        self.assertIn(install.PANEL_IDENTITY_MARKER, (git_source / "Panel.qml").read_text())

    def test_malformed_ui_metadata_falls_back_and_missing_marker_rejects(self):
        manifest = self.source / "manifest.json"
        payload = json.loads(manifest.read_text()); payload["version"] = "not-a-version"
        manifest.write_text(json.dumps(payload))
        stage = self.app()._stage_plugin()
        self.assertIn('packagedUiIdentity: "version unknown · ui-', (stage / "Panel.qml").read_text())
        shutil.rmtree(stage)
        panel = self.source / "Panel.qml"
        panel.write_text(panel.read_text().replace(install.PANEL_IDENTITY_MARKER, "missing marker"))
        with self.assertRaisesRegex(install.InstallError, "build-identity marker"):
            self.app()._stage_plugin()
        self.assertFalse(list(self.home.glob("plugin.stage-*")))

    def test_pre_shader_install_gains_shader_rollback_removes_it(self):
        app = self.app()
        self.mutate("setup", app)
        plugin = self.home / ".config/omarchy/plugins" / install.PLUGIN_ID
        shader = plugin / "wave.frag.qsb"
        # Recreate the ownership a three-file (pre-UI-02C) install recorded.
        shader.unlink()
        state = json.loads(app.paths.state.read_text())
        del state["hashes"]["plugin/wave.frag.qsb"]
        state["owned"]["plugin_files"] = list(install.LEGACY_PLUGIN_FILES)
        app.paths.state.write_text(json.dumps(state))
        failing = self.app(ops=MockOps(fail=("start", "novad.service")))
        with self.assertRaises(RuntimeError): self.mutate("update", failing)
        self.assertFalse(shader.exists())
        self.mutate("update", app)
        self.assertEqual(shader.read_bytes(), (ROOT / "wave.frag.qsb").read_bytes())
        state = json.loads(app.paths.state.read_text())
        self.assertEqual(state["hashes"]["plugin/wave.frag.qsb"], install.digest(shader))
        self.mutate("remove", app)
        self.assertFalse(shader.exists())

    def test_four_file_install_gains_the_mark_rollback_removes_it(self):
        app = self.app()
        self.mutate("setup", app)
        plugin = self.home / ".config/omarchy/plugins" / install.PLUGIN_ID
        mark = plugin / "NovaMark.qml"
        # Recreate the ownership a four-file (pre-logo) install recorded.
        mark.unlink()
        state = json.loads(app.paths.state.read_text())
        del state["hashes"]["plugin/NovaMark.qml"]
        state["owned"]["plugin_files"] = [name for name in install.PLUGIN_FILES if name != "NovaMark.qml"]
        app.paths.state.write_text(json.dumps(state))
        failing = self.app(ops=MockOps(fail=("start", "novad.service")))
        with self.assertRaises(RuntimeError): self.mutate("update", failing)
        self.assertFalse(mark.exists())
        self.mutate("update", app)
        self.assertEqual(mark.read_bytes(), (ROOT / "NovaMark.qml").read_bytes())
        state = json.loads(app.paths.state.read_text())
        self.assertEqual(state["hashes"]["plugin/NovaMark.qml"], install.digest(mark))
        self.mutate("remove", app)
        self.assertFalse(mark.exists())

    def test_legacy_adoption_records_only_verified_core_files(self):
        backup, extra = self.legacy_backup()
        plugin = self.home / ".config/omarchy/plugins" / install.PLUGIN_ID
        units = self.home / ".config/systemd/user"
        before = {str(path): path.read_bytes() for path in [*(plugin / name for name in install.LEGACY_PLUGIN_FILES), *(units / name for name in install.UNIT_NAMES)]}
        preview = self.adopt(backup, dry_run=True)
        self.assertTrue(preview["dry_run"])
        self.assertFalse((self.home / ".local/state/nova/installer/ownership.json").exists())
        result = self.adopt(backup)
        state = self.home / ".local/state/nova/installer/ownership.json"
        self.assertTrue(result["adopted"]); self.assertFalse((self.home / ".local/share/nova/runtime").exists())
        self.assertEqual({str(path): path.read_bytes() for path in [*(plugin / name for name in install.LEGACY_PLUGIN_FILES), *(units / name for name in install.UNIT_NAMES)]}, before)
        value = json.loads(state.read_text())
        self.assertEqual(value["installation_state"], "legacy_adopted")
        self.assertEqual(value["owned"]["runtime_files"], [])
        self.assertEqual(extra.read_text(), "preserve")
        self.assertNotIn("plugin/historical-panel-backup.qml", value["hashes"])
        self.assertEqual(self.ops.calls, [])

    def test_legacy_adoption_accepts_relative_and_absolute_checksum_entries(self):
        for absolute in (False, True):
            backup, _ = self.legacy_backup(absolute_manifest=absolute)
            preview = self.adopt(backup, dry_run=True)
            self.assertTrue(preview["dry_run"])

    def test_legacy_adoption_preserves_literal_python_alias(self):
        versioned = self.root / "python/3.14.7/bin/python3.14"; versioned.parent.mkdir(parents=True)
        versioned.symlink_to(self.python)
        latest = self.root / "python/latest/bin/python"; latest.parent.mkdir(parents=True)
        latest.symlink_to(versioned)
        self.assertEqual(install.absolute_python_path(latest), latest.absolute())
        self.assertNotEqual(install.absolute_python_path(latest), latest.resolve())
        backup, _ = self.legacy_backup(python=latest)
        self.assertTrue(self.adopt(backup, app=self.app(python=latest), dry_run=True)["dry_run"])
        alias = self.root / "python/other/bin/python"; alias.parent.mkdir(parents=True); alias.symlink_to(versioned)
        with self.assertRaisesRegex(install.ConflictError, "ExecStart differs"):
            self.adopt(backup, app=self.app(python=alias), dry_run=True)

    def test_legacy_adoption_rejects_absolute_sibling_and_traversal_entries(self):
        backup, _ = self.legacy_backup()
        sibling = self.root / (backup.name + " sibling"); sibling.mkdir()
        foreign = sibling / "Panel.qml"; foreign.write_bytes((backup / "plugin/Panel.qml").read_bytes())
        manifest = backup / "metadata/SHA256SUMS"
        manifest.write_text("\n".join(
            f"{install.digest(foreign)}  {foreign}" if line.endswith("plugin/Panel.qml") else line
            for line in manifest.read_text().splitlines()) + "\n")
        with self.assertRaisesRegex(install.InstallError, "outside the supplied backup"):
            self.adopt(backup)
        backup, _ = self.legacy_backup()
        manifest = backup / "metadata/SHA256SUMS"
        manifest.write_text("\n".join(
            f"{install.digest(backup / 'plugin/Panel.qml')}  plugin/../plugin/Panel.qml" if line.endswith("plugin/Panel.qml") else line
            for line in manifest.read_text().splitlines()) + "\n")
        with self.assertRaisesRegex(install.InstallError, "unsafe path"):
            self.adopt(backup)

    def test_legacy_adoption_rejects_manifest_symlink_escape(self):
        backup, _ = self.legacy_backup()
        outside = self.root / "outside"; outside.mkdir()
        foreign = outside / "foreign.qml"; foreign.write_text("outside")
        (backup / "plugin/escape").symlink_to(outside, target_is_directory=True)
        manifest = backup / "metadata/SHA256SUMS"
        manifest.write_text(manifest.read_text() + f"{install.digest(foreign)}  plugin/escape/foreign.qml\n")
        with self.assertRaisesRegex(install.InstallError, "crosses a symlink"):
            self.adopt(backup)

    def test_legacy_adoption_rejects_mismatch_links_and_existing_state(self):
        backup, _ = self.legacy_backup()
        plugin = self.home / ".config/omarchy/plugins" / install.PLUGIN_ID
        (plugin / "Panel.qml").write_text("changed")
        with self.assertRaisesRegex(install.ConflictError, "verified backup"):
            self.adopt(backup)
        shutil.copy2(self.source / "Panel.qml", plugin / "Panel.qml")
        (plugin / "Panel.qml").unlink(); (plugin / "Panel.qml").symlink_to("BarWidget.qml")
        with self.assertRaisesRegex(install.ConflictError, "symlink"):
            self.adopt(backup)
        (plugin / "Panel.qml").unlink(); shutil.copy2(self.source / "Panel.qml", plugin / "Panel.qml")
        self.app()._write_json_atomic(self.home / ".local/state/nova/installer/ownership.json", {"version": install.STATE_VERSION})
        with self.assertRaisesRegex(install.ConflictError, "ownership state"):
            self.adopt(backup)

    def test_legacy_adoption_rejects_pending_transaction_and_unit_override(self):
        backup, _ = self.legacy_backup()
        pending = self.home / ".local/state/nova/installer/pending.json"
        self.app()._write_json_atomic(pending, {"transaction": "ignored", "backup": {}})
        with self.assertRaisesRegex(install.ConflictError, "pending transaction"):
            self.adopt(backup)
        pending.unlink()
        override = self.home / ".config/systemd/user/novad.service.d"; override.mkdir()
        with self.assertRaisesRegex(install.ConflictError, "override"):
            self.adopt(backup)

    def test_legacy_adoption_rejects_backup_source_and_pending_mismatches(self):
        backup, _ = self.legacy_backup()
        (backup / "plugin/Panel.qml").write_text("changed")
        with self.assertRaisesRegex(install.ConflictError, "integrity"):
            self.adopt(backup)
        backup, _ = self.legacy_backup()
        (self.source / "Panel.qml").write_text("new canonical source")
        with self.assertRaisesRegex(install.ConflictError, "canonical source"):
            self.adopt(backup)

    def test_legacy_adoption_requires_manifest_entries_for_each_target(self):
        backup, _ = self.legacy_backup()
        manifest = backup / "metadata/SHA256SUMS"
        manifest.write_text("\n".join(line for line in manifest.read_text().splitlines() if "units/nova-actions.service" not in line) + "\n")
        with self.assertRaisesRegex(install.ConflictError, "omits adoption target"):
            self.adopt(backup)

    def test_legacy_adoption_metadata_write_is_atomic_and_recoverable(self):
        backup, _ = self.legacy_backup()
        state = self.home / ".local/state/nova/installer/ownership.json"
        with mock.patch.object(install.os, "replace", side_effect=OSError("interrupted")):
            with self.assertRaisesRegex(OSError, "interrupted"):
                self.adopt(backup)
        self.assertFalse(state.exists())
        self.assertFalse(list(state.parent.glob("tmp*")))
        self.assertTrue(self.adopt(backup)["adopted"])

    def test_adopt_then_setup_and_failed_setup_restore_legacy_state(self):
        backup, _ = self.legacy_backup()
        self.adopt(backup)
        state = self.home / ".local/state/nova/installer/ownership.json"
        legacy_state = state.read_bytes()
        legacy_unit = (self.home / ".config/systemd/user/novad.service").read_bytes()
        failing = MockOps(("restart", "shell"))
        with self.assertRaisesRegex(RuntimeError, "simulated"):
            self.mutate("setup", app=self.app(ops=failing))
        self.assertEqual(state.read_bytes(), legacy_state)
        self.assertEqual((self.home / ".config/systemd/user/novad.service").read_bytes(), legacy_unit)
        self.assertFalse((self.home / ".local/share/nova/runtime").exists())
        result = self.mutate("setup")
        self.assertTrue(result["applied"])
        self.assertEqual(json.loads(state.read_text())["installation_state"], "managed")

    def test_service_environment_imports_runtime_without_bytecode_and_allows_lifecycle(self):
        self.mutate("setup")
        runtime = self.home / ".local/share/nova/runtime"
        unit = (self.home / ".config/systemd/user/novad.service").read_text()
        self.assertIn("Environment=PYTHONDONTWRITEBYTECODE=1", unit)
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run([str(self.python), "-c", "import acquisition, chat_sessions, onboarding_integration; print(acquisition.__file__, chat_sessions.__file__, onboarding_integration.__file__)"],
                                cwd=runtime / "novad", env=environment, text=True, capture_output=True, check=True)
        self.assertIn("acquisition.py", result.stdout)
        self.assertIn("chat_sessions.py", result.stdout)
        self.assertIn("onboarding_integration.py", result.stdout)
        self.assertFalse(list(runtime.rglob("__pycache__")))
        self.assertFalse(list(runtime.rglob("*.pyc")))
        self.mutate("update")
        self.mutate("remove")

    def test_runtime_inventory_covers_all_production_novad_modules(self):
        expected = {"novad/" + path.name for path in (ROOT / "novad").glob("*.py")
                    if not path.name.startswith("test_")}
        actual = {name for name in install.RUNTIME_FILES if name.startswith("novad/")}
        self.assertEqual(actual, expected)

    def test_update_can_add_a_new_owned_runtime_file_without_weakening_prior_inventory(self):
        self.mutate("setup")
        new_name = "novad/future_runtime_module.py"
        (self.source / new_name).write_text("VALUE = 1\n")
        with mock.patch.object(install, "RUNTIME_FILES", (*install.RUNTIME_FILES, new_name)):
            result = self.mutate("update")
        self.assertTrue(result["applied"])
        state = json.loads((self.home / ".local/state/nova/installer/ownership.json").read_text())
        self.assertIn(new_name, state["owned"]["runtime_files"])
        self.assertEqual((self.home / ".local/share/nova/runtime" / new_name).read_text(),
                         "VALUE = 1\n")

    def test_runtime_sentinel_and_symlink_block_update_and_remove(self):
        self.mutate("setup")
        runtime = self.home / ".local/share/nova/runtime"
        sentinel = runtime / "do-not-delete"; sentinel.write_text("keep")
        calls = len(self.ops.calls)
        with self.assertRaisesRegex(install.ConflictError, "unowned"):
            self.mutate("update")
        with self.assertRaisesRegex(install.ConflictError, "unowned"):
            self.mutate("remove")
        self.assertEqual(sentinel.read_text(), "keep"); self.assertEqual(len(self.ops.calls), calls)
        sentinel.unlink(); (runtime / "novad/link").symlink_to("server.py")
        with self.assertRaisesRegex(install.ConflictError, "symlink"):
            self.mutate("update")

    def test_interrupted_apply_recovers_before_conflict_checks_and_restores_state(self):
        self.mutate("setup")
        runtime = self.home / ".local/share/nova/runtime"; state = self.home / ".local/state/nova/installer/ownership.json"
        original_runtime, original_state = (runtime / "novad/server.py").read_bytes(), state.read_bytes()
        tx = self.home / ".local/state/nova/transaction-fixture"; tx.mkdir(parents=True)
        shutil.copytree(runtime, tx / "runtime")
        (tx / "ownership.json").write_bytes(original_state)
        (runtime / "novad/server.py").write_text("partial replacement")
        journal = {"transaction": str(tx), "operation": "update", "backup": {"plugin": [], "units": [], "runtime": True, "state": True, "service_state": {u:{"enabled":"enabled","active":"active"} for u in install.UNIT_NAMES}}}
        self.app()._write_json_atomic(self.home / ".local/state/nova/installer/pending.json", journal)
        result = self.mutate("update")
        self.assertTrue(result["recovery_performed"])
        self.assertNotEqual((runtime / "novad/server.py").read_bytes(), b"partial replacement")
        self.assertFalse(self.home.joinpath(".local/state/nova/installer/pending.json").exists())
        self.assertTrue(state.exists())

    def test_plan_reports_pending_recovery_without_mutating(self):
        self.mutate("setup")
        self.app()._write_json_atomic(self.home / ".local/state/nova/installer/pending.json", {"transaction": "/missing", "backup": {}})
        result = self.app().apply("setup", dry_run=True)
        self.assertTrue(result["pending_recovery"]["required"])
        self.assertTrue(self.home.joinpath(".local/state/nova/installer/pending.json").exists())

    def test_failed_update_restores_files_and_attempts_service_restore(self):
        self.mutate("setup")
        before = (self.home / ".local/share/nova/runtime/novad/server.py").read_bytes()
        (self.source / "novad/server.py").write_text("new source")
        failing = MockOps(("start", "novad.service"))
        with self.assertRaisesRegex(install.InstallError, "service restoration requires manual inspection"):
            self.mutate("update", app=self.app(ops=failing))
        self.assertEqual((self.home / ".local/share/nova/runtime/novad/server.py").read_bytes(), before)
        self.assertIn(("service", "start", "novad.service"), failing.calls)
        self.assertIn(("service", "daemon-reload"), failing.calls)
        self.assertTrue(self.home.joinpath(".local/state/nova/installer/pending.json").exists())

    def test_remove_disables_plugin_before_manifest_and_preserves_external_data(self):
        self.mutate("setup")
        model = self.home / ".local/share/ollama/models/sentinel"; model.parent.mkdir(parents=True); model.write_text("keep")
        data = self.home / ".config/nova/model.json"; data.parent.mkdir(parents=True); data.write_text("keep")
        self.ops.calls.clear()
        removed_unit = self.home / ".config/systemd/user/novad.service"
        original_service = self.ops.service
        def observe_service(*args):
            if args == ("daemon-reload",): self.assertFalse(removed_unit.exists())
            return original_service(*args)
        self.ops.service = observe_service
        result = self.mutate("remove")
        self.assertEqual(result["status"], "removed"); self.assertEqual(model.read_text(), "keep"); self.assertEqual(data.read_text(), "keep")
        disable = self.ops.calls.index(("shell", "plugin", "disable", install.PLUGIN_ID))
        restart = self.ops.calls.index(("shell", "restart", "shell"))
        self.assertLess(disable, restart)
        self.assertEqual(self.mutate("remove")["status"], "nothing_owned")

    def test_removal_failure_after_file_deletion_recovers_via_public_operation(self):
        self.mutate("setup")
        failing = MockOps(("daemon-reload",))
        with self.assertRaisesRegex(install.InstallError, "service reconciliation requires manual inspection"):
            self.mutate("remove", app=self.app(ops=failing))
        runtime = self.home / ".local/share/nova/runtime"
        self.assertTrue((runtime / "novad/server.py").exists())
        self.assertTrue(self.home.joinpath(".local/state/nova/installer/pending.json").exists())
        healthy = MockOps()
        result = self.mutate("remove", app=self.app(ops=healthy))
        self.assertEqual(result["status"], "removed")
        self.assertFalse(runtime.exists())

    def test_final_shell_failure_restores_removal_files_and_plugin(self):
        self.mutate("setup")
        failing = MockOps(("restart", "shell"))
        with self.assertRaisesRegex(RuntimeError, "simulated"):
            self.mutate("remove", app=self.app(ops=failing))
        self.assertTrue((self.home / ".local/share/nova/runtime/novad/server.py").exists())
        self.assertTrue((self.home / ".config/omarchy/plugins/io.github.yangass.nova/manifest.json").exists())
        self.assertIn(("shell", "plugin", "enable", install.PLUGIN_ID), failing.calls)
        self.assertFalse(self.home.joinpath(".local/state/nova/installer/pending.json").exists())

    def test_pending_recovery_skips_previously_missing_unit(self):
        self.mutate("setup")
        runtime = self.home / ".local/share/nova/runtime"; state = self.home / ".local/state/nova/installer/ownership.json"
        tx = self.home / ".local/state/nova/transaction-missing"; tx.mkdir(parents=True)
        shutil.copytree(runtime, tx / "runtime"); (tx / "ownership.json").write_bytes(state.read_bytes())
        journal = {"transaction": str(tx), "operation": "remove", "backup": {"plugin": [], "units": [], "runtime": True, "state": True, "plugin_enabled": False, "service_state": {"novad.service": {"enabled": "missing", "active": "missing"}, **{u:{"enabled":"enabled","active":"active"} for u in install.UNIT_NAMES if u != "novad.service"}}}}
        missing = MockOps(states={"novad.service": {"enabled": "missing", "active": "missing"}})
        app = self.app(ops=missing); app._write_json_atomic(self.home / ".local/state/nova/installer/pending.json", journal)
        recovery = app.recover_pending(); app._restore_services(recovery["prior_service_state"])
        self.assertFalse(any(call[-1] == "novad.service" for call in missing.calls))

    def test_alternate_root_rejects_live_operations(self):
        realish = install.Installer(install.Paths(self.source, self.home), self.python, ops=install.Operations())
        with self.assertRaisesRegex(install.InstallError, "not a sandbox"):
            realish.apply("setup", restart_shell=True, confirm_idle=True, idle_state="idle")

    def test_git_managed_actual_checkout_is_source_and_is_preserved(self):
        plugin = self.home / ".config/omarchy/plugins" / install.PLUGIN_ID; plugin.mkdir(parents=True)
        self.populate_source(plugin); (plugin / ".git").mkdir(); marker = plugin / ".git/keep"; marker.write_text("git metadata")
        app = self.app("auto", source=plugin)
        self.assertEqual(app.plugin_mode, "git-managed")
        plan = app.apply("setup", dry_run=True); self.assertEqual(plan["plugin"]["validation_target"], str(plugin))
        app.apply("setup", confirm_idle=True, idle_state="idle")
        app.apply("remove", confirm_idle=True, idle_state="idle")
        self.assertEqual(marker.read_text(), "git metadata")

    def test_idle_attestation_is_required_for_backend_changes(self):
        with self.assertRaisesRegex(install.InstallError, "activity state is unknown"):
            self.app().apply("setup", restart_shell=True, confirm_idle=True)
        with self.assertRaisesRegex(install.InstallError, "confirm-idle"):
            self.app().apply("setup", restart_shell=True, idle_state="idle")
        with self.assertRaisesRegex(install.InstallError, "requires --restart-shell"):
            self.app().apply("setup", confirm_idle=True, idle_state="idle")

    def test_rendered_units_pass_systemd_analyze_with_spaces(self):
        app = self.app(); target = self.root / "rendered service files"; target.mkdir()
        for unit in install.UNIT_NAMES:
            rendered = target / unit; rendered.write_text(app._render_unit(unit))
            subprocess.run(["systemd-analyze", "verify", str(rendered)], check=True)
            self.assertNotIn("@NOVA_ROOT@", rendered.read_text())
            self.assertIn("\\x20", rendered.read_text())
        action_unit = (target / "nova-actions.service").read_text()
        self.assertIn("After=graphical-session.target", action_unit)
        self.assertIn("PartOf=graphical-session.target", action_unit)
        self.assertIn("ConditionEnvironment=WAYLAND_DISPLAY", action_unit)
        self.assertIn("EnvironmentFile=-%t/uwsm/env_session.conf", action_unit)

class FirstInstallCliTests(unittest.TestCase):
    """Beginner path: a first install needs no idle attestation; updates still do."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.home = Path(self.temp.name)
    def tearDown(self): self.temp.cleanup()

    def run_main(self, argv, reachable=False, state=False):
        paths = install.Paths(ROOT, self.home)
        if state:
            paths.state.parent.mkdir(parents=True, exist_ok=True); paths.state.write_text("{}")
        calls = {}
        def fake_apply(app, operation, **kw):
            calls.update(kw, operation=operation, mode=app.plugin_mode); return {"applied": True}
        with patch.object(install.Path, "home", return_value=self.home), \
             patch.object(install, "novad_reachable", return_value=reachable), \
             patch.object(install.Installer, "apply", fake_apply), \
             patch("builtins.print") as printed:
            code = install.main([*argv, "--root", str(self.home)])
        return code, calls, " ".join(str(c.args[0]) for c in printed.call_args_list)

    def test_first_setup_needs_no_flags_and_prints_plain_language(self):
        code, calls, out = self.run_main(["setup"])
        self.assertEqual((code, calls["confirm_idle"], calls["idle_state"]), (0, True, "idle"))
        self.assertTrue(calls["restart_shell"])  # copy mode must restart to show the panel
        self.assertIn("\u2713 NOVA is installed.", out)
        self.assertNotIn("{", out)

    def test_existing_install_or_running_novad_keeps_explicit_attestation(self):
        for kwargs in ({"state": True}, {"reachable": True}):
            _code, calls, _out = self.run_main(["setup"], **kwargs)
            self.assertEqual((calls["confirm_idle"], calls["idle_state"]), (False, "unknown"), kwargs)
        _code, calls, _out = self.run_main(["update"])
        self.assertEqual((calls["confirm_idle"], calls["idle_state"]), (False, "unknown"))

    def test_json_flag_keeps_machine_output(self):
        _code, _calls, out = self.run_main(["setup", "--json"])
        self.assertEqual(json.loads(out), {"applied": True})


if __name__ == "__main__": unittest.main()
