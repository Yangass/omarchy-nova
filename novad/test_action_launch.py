"""Focused contracts for fixed application launches and workspace targeting."""
from __future__ import annotations
import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import patch
import unittest

NOVA_ROOT = Path(os.environ.get("NOVA_ROOT", str(Path(__file__).resolve().parents[1])))
ACTIONS_PATH = NOVA_ROOT / "nova-actions" / "server.py"
NOVAD_PATH = NOVA_ROOT / "novad" / "server.py"
spec = importlib.util.spec_from_file_location("nova_actions_server_for_test", ACTIONS_PATH)
actions = importlib.util.module_from_spec(spec)
assert spec and spec.loader
with patch("os.chmod"):
    spec.loader.exec_module(actions)

sys.path.insert(0, str(NOVA_ROOT / "novad"))
import server as novad


class Process:
    def __init__(self, result=None, timeout=False):
        self.result, self.timeout = result, timeout

    def wait(self, timeout):
        if self.timeout:
            raise actions.subprocess.TimeoutExpired(["fixed"], timeout)
        return self.result


class ApplicationLaunchResultTests(unittest.TestCase):
    def terminal_session(self):
        return patch.dict(actions.os.environ, {
            "XDG_RUNTIME_DIR": "/run/user/test",
            "WAYLAND_DISPLAY": "wayland-test",
            "HYPRLAND_INSTANCE_SIGNATURE": "test-signature",
            "XDG_CURRENT_DESKTOP": "Hyprland",
        }, clear=False)

    def test_untargeted_zero_exit_preserves_dispatch_semantics_and_command(self):
        with self.terminal_session(), patch.object(actions.subprocess, "Popen", return_value=Process(0)) as popen:
            result = actions.open_app({"application_id": "terminal"})
        self.assertEqual(result["status"], "dispatched")
        self.assertTrue(result["launch_invoked"])
        self.assertEqual(result["launcher_exit_code"], 0)
        self.assertEqual(result["window_observation"], "not_observed")
        self.assertNotIn("workspace", result)
        self.assertEqual(popen.call_args.args[0], actions.APPLICATIONS["terminal"][1])

    def test_still_running_launcher_is_dispatch_not_window_success(self):
        with patch.object(actions.subprocess, "Popen", return_value=Process(timeout=True)):
            result = actions.open_app({"application_id": "file_manager"})
        self.assertEqual((result["status"], result["launcher_exit_code"], result["window_observation"]), ("dispatched", None, "not_observed"))

    def test_nonzero_launcher_exit_is_a_visible_failure_result(self):
        with self.terminal_session(), patch.object(actions.subprocess, "Popen", return_value=Process(7)):
            result = actions.open_app({"application_id": "terminal"})
        self.assertEqual((result["status"], result["error"], result["launcher_exit_code"]), ("failure", "launcher_failed", 7))
        self.assertFalse(result["launch_invoked"])
        self.assertEqual(result["window_observation"], "not_observed")

    def test_valid_targeted_terminal_uses_fixed_silent_hyprland_rule(self):
        with self.terminal_session(), patch.object(actions.subprocess, "Popen", return_value=Process(0)) as popen:
            result = actions.open_app({"application_id": "terminal", "workspace": 3})
        self.assertEqual(result["status"], "dispatched")
        self.assertEqual(result["workspace"], 3)
        self.assertEqual(result["workspace_placement"], "requested_not_observed")
        self.assertEqual(popen.call_args.args[0], [
            "/usr/bin/hyprctl", "eval",
            'hl.exec_cmd("/usr/bin/foot", { workspace = "3 silent" })',
        ])
        self.assertIn("workspace 3", result["message"])

    def test_executor_rejects_invalid_or_unsupported_target_without_dispatch(self):
        with patch.object(actions.subprocess, "Popen") as popen:
            invalid = actions.open_app({"application_id": "terminal", "workspace": 0})
            unsupported = actions.open_app({"application_id": "file_manager", "workspace": 3})
        self.assertEqual(invalid["error"], "invalid_workspace_target")
        self.assertEqual(unsupported["error"], "workspace_target_unsupported_for_application")
        self.assertEqual(unsupported["workspace"], 3)
        popen.assert_not_called()

    def test_targeted_launcher_failure_retains_workspace_for_audit(self):
        with self.terminal_session(), patch.object(actions.subprocess, "Popen", return_value=Process(7)):
            result = actions.open_app({"application_id": "terminal", "workspace": 3})
        self.assertEqual((result["status"], result["workspace"]), ("failure", 3))

    def test_terminal_without_a_graphical_session_is_not_reported_as_dispatched(self):
        with patch.dict(actions.os.environ, {name: "" for name in actions.TERMINAL_SESSION_VARIABLES}, clear=False), patch.object(actions.subprocess, "Popen") as popen:
            result = actions.open_app({"application_id": "terminal", "workspace": 3})
        self.assertEqual((result["status"], result["error"], result["workspace"]), ("failure", "desktop_session_unavailable", 3))
        self.assertEqual(result["missing_session_environment"], list(actions.TERMINAL_SESSION_VARIABLES))
        self.assertFalse(result["launch_invoked"])
        popen.assert_not_called()

    def test_execution_audits_bind_frozen_application_and_workspace(self):
        source = ACTIONS_PATH.read_text()
        self.assertIn('audit("execution_started",action=action,application_id=snapshot.get("application_id")', source)
        self.assertIn('workspace=result.get("workspace", snapshot.get("workspace")', source)

    def test_unknown_application_never_dispatches(self):
        self.assertEqual(actions.open_app({"application_id": "not_allowed"}), {"status": "failure", "error": "unknown_application"})


class NovadApplicationProposalTests(unittest.TestCase):
    def test_schema_accepts_only_optional_bounded_workspace(self):
        source = NOVAD_PATH.read_text()
        self.assertIn('"workspace":{"type":"integer","minimum":NORMAL_WORKSPACE_MIN,"maximum":NORMAL_WORKSPACE_MAX}', source)
        self.assertIn('set(arguments) - {"application_id", "workspace"}', source)
        self.assertIn('app not in WORKSPACE_TARGET_APPLICATIONS', source)

    def test_omitted_workspace_preserves_current_snapshot(self):
        snapshot, error = novad.proposal("open_known_application", {"application_id": "terminal"})
        self.assertIsNone(error)
        self.assertEqual(snapshot, {"application_id": "terminal", "application": "Terminal"})

    def test_valid_workspace_is_frozen_into_pending_approval_and_public_payload(self):
        snapshot, error = novad.proposal("open_known_application", {"application_id": "terminal", "workspace": 3})
        self.assertIsNone(error)
        self.assertEqual(snapshot, {"application_id": "terminal", "application": "Terminal", "workspace": 3})
        public = novad.Pending("open_known_application", snapshot).public()
        self.assertEqual(public["workspace"], 3)
        self.assertIsInstance(public["expires_at"], float)
        self.assertIn("stay on the current workspace", public["impact"])

    def test_invalid_and_reused_app_targets_are_rejected_before_approval(self):
        for arguments, expected in (
            ({"application_id": "terminal", "workspace": 0}, "invalid_workspace_target"),
            ({"application_id": "terminal", "workspace": True}, "invalid_workspace_target"),
            ({"application_id": "terminal", "workspace": 11}, "invalid_workspace_target"),
            ({"application_id": "file_manager", "workspace": 3}, "workspace_target_unsupported_for_application"),
            ({"application_id": "terminal", "workspace": "3"}, "invalid_workspace_target"),
        ):
            snapshot, error = novad.proposal("open_known_application", arguments)
            self.assertIsNone(snapshot)
            self.assertEqual(error, expected)

    def test_prompt_and_card_preserve_dispatch_and_placement_uncertainty(self):
        source = NOVAD_PATH.read_text()
        self.assertIn("result of dispatched means only that NOVA sent the fixed launch request", source)
        self.assertIn("does not verify placement", source)
        panel = Path(os.environ.get("NOVA_PANEL_PATH", str(NOVA_ROOT / "Panel.qml")))
        source_panel = panel.read_text()
        self.assertIn('WORKSPACE: ', source_panel)
        self.assertIn('· STAY HERE', source_panel)


if __name__ == "__main__":
    unittest.main()
