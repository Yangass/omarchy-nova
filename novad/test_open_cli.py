"""CODE-01B Open CLI: profile validation, fixed launch command and panel-facing state."""
from __future__ import annotations
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest

NOVA_ROOT = Path(os.environ.get("NOVA_ROOT", str(Path(__file__).resolve().parents[1])))
spec = importlib.util.spec_from_file_location("nova_actions_server_for_cli_test", NOVA_ROOT / "nova-actions" / "server.py")
actions = importlib.util.module_from_spec(spec)
assert spec and spec.loader
with patch("os.chmod"):
    spec.loader.exec_module(actions)

sys.path.insert(0, str(NOVA_ROOT / "novad"))
import server as novad

MODEL = "nova-code-qwen38-96k:latest"
REAL_RUN = actions.subprocess.run
SESSION = {"XDG_RUNTIME_DIR": "/run/user/test", "WAYLAND_DISPLAY": "wayland-test",
           "HYPRLAND_INSTANCE_SIGNATURE": "sig", "XDG_CURRENT_DESKTOP": "Hyprland"}


class Process:
    def __init__(self, result=0, timeout=False): self.result, self.timeout = result, timeout
    def wait(self, timeout):
        if self.timeout: raise actions.subprocess.TimeoutExpired(["fixed"], timeout)
        return self.result


class NoClient:
    returncode = 1


_KEY_DIR = tempfile.TemporaryDirectory()


def setUpModule():
    # novad's calls to nova-actions carry the API key; never create the user's real one here.
    patcher = patch.object(novad, "API_KEY_PATH", os.path.join(_KEY_DIR.name, "api.token"))
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


class ProfileFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.launcher = self.root / "nova/pilots/qwen-dev/launch.py"
        self.launcher.parent.mkdir(parents=True); self.launcher.write_text("# launcher\n")
        self.project = self.root / "nova-dev"; self.project.mkdir()
        (self.project / "identity.json").write_text("{}")
        self.config = self.root / "cli-profiles.json"
        patcher = patch.object(actions, "CLI_PROFILES_PATH", self.config)
        patcher.start(); self.addCleanup(patcher.stop)
        pgrep = patch.object(actions.subprocess, "run", return_value=NoClient())
        self.pgrep = pgrep.start(); self.addCleanup(pgrep.stop)

    def tearDown(self): self.temp.cleanup()

    def profile(self, **changes):
        item = {"id": "qwen-nova-dev", "label": "Qwen Code · NOVA dev project", "tool": "qwen-code",
                "provider": "ollama", "model": MODEL, "launcher": str(self.launcher),
                "project": str(self.project), "project_label": "NOVA dev project"}
        item.update(changes)
        return item

    def write(self, *profiles, schema=1):
        self.config.write_text(json.dumps({"schema": schema, "profiles": list(profiles)}))


class ProfileValidationTests(ProfileFixture):
    def test_missing_file_is_not_configured(self):
        self.assertEqual(actions.list_cli_profiles(), {"configured": False, "error": None, "profiles": []})

    def test_valid_profile_is_listed_without_paths(self):
        self.write(self.profile())
        state = actions.list_cli_profiles()
        self.assertTrue(state["configured"])
        [item] = state["profiles"]
        self.assertEqual((item["valid"], item["error"], item["running"], item["model"]), (True, None, False, MODEL))
        self.assertNotIn("launcher", item); self.assertNotIn("project", item)

    def test_malformed_file_and_schema(self):
        self.config.write_text("{not json")
        self.assertEqual(actions.list_cli_profiles()["error"], "invalid_profile_file")
        self.write(self.profile(), schema=2)
        self.assertEqual(actions.list_cli_profiles()["error"], "invalid_profile_file")

    def test_rejections(self):
        cases = {
            "unsupported_tool": self.profile(tool="bash"),
            "unsupported_provider": self.profile(provider="openai"),
            "invalid_profile": dict(self.profile(), argv=["sh", "-c", "x"]),
            "project_unavailable": self.profile(project=str(self.root / "missing")),
        }
        for expected, item in cases.items():
            profile, error = actions.validate_cli_profile(item)
            self.assertIsNone(profile); self.assertEqual(error, expected, item)

    def test_launcher_inside_project_is_rejected(self):
        inside = self.project / "pilots/qwen-dev/launch.py"
        inside.parent.mkdir(parents=True); inside.write_text("# editable by the agent\n")
        self.assertEqual(actions.validate_cli_profile(self.profile(launcher=str(inside)))[1], "launcher_inside_project")

    def test_writable_or_symlinked_launcher_is_rejected(self):
        os.chmod(self.launcher, 0o664)
        self.assertEqual(actions.validate_cli_profile(self.profile())[1], "launcher_unavailable")
        os.chmod(self.launcher, 0o644)
        link = self.root / "link.py"; link.symlink_to(self.launcher)
        self.assertEqual(actions.validate_cli_profile(self.profile(launcher=str(link)))[1], "launcher_unavailable")

    def test_held_lock_from_this_boot_means_running_and_stale_does_not(self):
        self.write(self.profile())
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        lock = self.project / "lock"
        lock.write_text(json.dumps({"pid": os.getpid(), "uid": os.getuid(), "boot_id": boot}))
        self.assertTrue(actions.list_cli_profiles()["profiles"][0]["running"])
        lock.write_text(json.dumps({"pid": os.getpid(), "uid": os.getuid(), "boot_id": "older-boot"}))
        self.assertFalse(actions.list_cli_profiles()["profiles"][0]["running"])
        lock.write_text("")
        self.assertFalse(actions.list_cli_profiles()["profiles"][0]["running"])


def _git(cwd, *args):
    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    return REAL_RUN(["git", *args], cwd=str(cwd), env=env, capture_output=True, text=True, check=True).stdout.strip()


class ProjectSyncTests(ProfileFixture):
    def setUp(self):
        super().setUp()
        self.pgrep.side_effect = REAL_RUN  # real git here
        canonical = self.root / "nova"
        _git(canonical, "init", "-q", "-b", "master")
        for name in ("one", "two"):
            (canonical / name).write_text(name)
            _git(canonical, "add", "-A"); _git(canonical, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", name)
        self.first, self.second = _git(canonical, "rev-parse", "HEAD~1"), _git(canonical, "rev-parse", "HEAD")
        self.checkout = self.project / "project"
        _git(self.root, "clone", "-q", str(canonical), str(self.checkout))
        _git(self.checkout, "switch", "-q", "-c", "qwen-development")

    def sync(self): return actions.cli_project_sync(self.profile())

    def test_states(self):
        self.assertEqual(self.sync(), "up_to_date")
        _git(self.checkout, "reset", "-q", "--hard", self.first)
        self.assertEqual(self.sync(), "will_update")
        (self.checkout / "worker").write_text("w"); _git(self.checkout, "add", "-A")
        _git(self.checkout, "-c", "user.name=w", "-c", "user.email=w@w", "commit", "-qm", "worker")
        self.assertEqual(self.sync(), "unreviewed_commits")
        _git(self.checkout, "pack-refs", "--all")
        self.assertEqual(self.sync(), "unreviewed_commits")

    def test_untrusted_head_ref_is_unknown_and_git_never_runs_in_checkout(self):
        (self.checkout / ".git" / "HEAD").write_text("ref: refs/heads/../../x\n")
        self.assertEqual(self.sync(), "unknown")
        calls = []
        def spy(argv, *a, **k):
            calls.append(argv); return REAL_RUN(argv, *a, **k)
        (self.checkout / ".git" / "HEAD").write_text("ref: refs/heads/qwen-development\n")
        with patch.object(actions.subprocess, "run", spy):
            self.sync()
        self.assertTrue(calls and all(str(self.checkout) not in " ".join(map(str, c)) for c in calls))


class ProjectIdentityTests(ProfileFixture):
    """2026-10-12: a Pulsar project (repo ~/pulsar, branch main) must not be compared with NOVA's master."""
    def setUp(self):
        super().setUp()
        self.pgrep.side_effect = REAL_RUN
        self.pulsar = self.root / "pulsar"
        _git(self.root, "init", "-q", "-b", "main", str(self.pulsar))
        (self.pulsar / "a").write_text("a"); _git(self.pulsar, "add", "-A")
        _git(self.pulsar, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "a")
        self.checkout = self.project / "project"
        _git(self.root, "clone", "-q", str(self.pulsar), str(self.checkout))

    def identity(self, **values):
        (self.project / "identity.json").write_text(json.dumps(values))

    def test_identity_names_the_source_repository_and_branch(self):
        self.identity(source_repository=str(self.pulsar), canonical_branch="main")
        self.assertEqual(actions.cli_project_sync(self.profile()), "up_to_date")
        (self.pulsar / "b").write_text("b"); _git(self.pulsar, "add", "-A")
        _git(self.pulsar, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "b")
        self.assertEqual(actions.cli_project_sync(self.profile()), "will_update")

    def test_odd_identities_fall_back_to_the_launcher_repository(self):
        for values in ({"source_repository": "relative/path"}, {"source_repository": str(self.checkout)},
                       {"source_repository": str(self.pulsar), "canonical_branch": "../../x"}, {}):
            with self.subTest(values=values):
                self.identity(**values)
                canonical, branch = actions._cli_canonical(self.profile())
                if values.get("canonical_branch") == "../../x":
                    self.assertEqual((canonical, branch), (self.pulsar.resolve(), "master"))
                else:
                    self.assertEqual((canonical, branch), (Path(self.profile()["launcher"]).resolve().parents[2], "master"))


class LaunchTests(ProfileFixture):
    def launch(self, snapshot=None, process=None):
        with patch.dict(actions.os.environ, SESSION, clear=False), \
             patch.object(actions.subprocess, "Popen", return_value=process or Process(0)) as popen:
            result = actions.open_coding_cli(snapshot or {"profile_id": "qwen-nova-dev", "model": MODEL})
        return result, popen

    def test_dispatch_uses_fixed_interactive_command(self):
        self.write(self.profile())
        result, popen = self.launch()
        self.assertEqual((result["status"], result["window_observation"]), ("dispatched", "not_observed"))
        self.assertEqual(popen.call_args.args[0], ["/usr/bin/omarchy", "launch", "tui", "--app-id=TUI.nova-cli",
                                                   "/usr/bin/python3", "-B", str(self.launcher), "--project", str(self.project), "--interactive"])

    def test_still_running_launcher_is_dispatched(self):
        self.write(self.profile())
        result, _ = self.launch(process=Process(timeout=True))
        self.assertEqual((result["status"], result["launcher_exit_code"]), ("dispatched", None))

    def test_failures_never_launch(self):
        self.write(self.profile())
        for snapshot, error in (({"profile_id": "qwen-nova-dev", "model": "qwen3:14b"}, "profile_model_mismatch"),
                                ({"profile_id": "other", "model": MODEL}, "unknown_profile"),
                                ({"profile_id": "qwen-nova-dev", "model": MODEL, "argv": "x"}, "invalid_cli_request")):
            result, popen = self.launch(snapshot)
            self.assertEqual(result["error"], error); popen.assert_not_called()

    def test_active_session_blocks_duplicate(self):
        self.write(self.profile())
        class Running: returncode = 0
        self.pgrep.return_value = Running()
        result, popen = self.launch()
        self.assertEqual(result["error"], "cli_session_active"); popen.assert_not_called()

    def test_nonzero_launcher_exit_is_failure(self):
        self.write(self.profile())
        result, _ = self.launch(process=Process(9))
        self.assertEqual((result["status"], result["error"], result["launcher_exit_code"]), ("failure", "launcher_failed", 9))

    def test_missing_desktop_session_does_not_launch(self):
        self.write(self.profile())
        with patch.dict(actions.os.environ, {}, clear=True), patch.object(actions.subprocess, "Popen") as popen:
            result = actions.open_coding_cli({"profile_id": "qwen-nova-dev", "model": MODEL})
        self.assertEqual(result["error"], "desktop_session_unavailable"); popen.assert_not_called()


class OnlineValidationTests(ProfileFixture):
    def online(self, tool="claude-code", **changes):
        binary = {"claude-code": "claude", "codex": "codex"}.get(tool, tool)
        command = self.root / "bin" / binary
        command.parent.mkdir(parents=True, exist_ok=True)
        command.write_text("# " + tool + "\n")
        os.chmod(command, 0o755)
        item = {"id": tool, "label": tool + " · NOVA", "tool": tool, "provider": "online",
                "command": str(command), "project": str(self.project), "project_label": "NOVA source"}
        item.update(changes)
        return item

    def test_valid_profiles_are_listed_online(self):
        self.write(self.online(), self.online(tool="codex"), self.profile())
        items = {item["id"]: item for item in actions.list_cli_profiles()["profiles"]}
        for tool in ("claude-code", "codex"):
            item = items[tool]
            self.assertEqual((item["kind"], item["valid"], item["error"], item["running"], item["project_sync"]),
                             ("online", True, None, False, "not_applicable"))
            self.assertNotIn("command", item); self.assertNotIn("project", item); self.assertNotIn("model", item)
        self.assertEqual((items["qwen-nova-dev"]["kind"], items["qwen-nova-dev"]["valid"]), ("local", True))

    def test_rejections(self):
        claude = self.root / "bin" / "claude"
        codex = self.root / "bin" / "codex"
        claude.parent.mkdir(parents=True, exist_ok=True)
        for command in (claude, codex):
            command.write_text("x\n"); os.chmod(command, 0o755)
        base = {"id": "claude-code", "label": "Claude Code · NOVA", "tool": "claude-code", "provider": "online",
                "command": str(claude), "project": str(self.project), "project_label": "NOVA source"}
        def check(item, expected):
            profile, error = actions.validate_cli_profile(item)
            self.assertIsNone(profile, item); self.assertEqual(error, expected, item)
        check(dict(base, tool="gemini"), "unsupported_tool")
        check(dict(base, command="bin/claude"), "command_unavailable")
        check(dict(base, command=str(codex)), "command_unavailable")
        os.chmod(claude, 0o775); check(base, "command_unavailable"); os.chmod(claude, 0o755)
        os.chmod(claude, 0o644); check(base, "command_unavailable"); os.chmod(claude, 0o755)
        check(dict(base, project=str(self.root / "missing")), "project_unavailable")
        check(dict(base, model=MODEL), "invalid_profile")
        check(dict(base, launcher=str(self.launcher)), "invalid_profile")

    def test_symlinked_command_is_accepted(self):
        claude = self.root / "bin" / "claude"
        claude.parent.mkdir(parents=True, exist_ok=True)
        claude.write_text("x\n"); os.chmod(claude, 0o755)
        shim = self.root / "shims" / "claude"
        shim.parent.mkdir(parents=True, exist_ok=True)
        shim.symlink_to(claude)
        profile, error = actions.validate_cli_profile({"id": "claude-code", "label": "Claude Code · NOVA",
                                                       "tool": "claude-code", "provider": "online",
                                                       "command": str(shim), "project": str(self.project),
                                                       "project_label": "NOVA source"})
        self.assertIsNone(error); self.assertEqual(profile["command"], str(shim))


class OnlineLaunchTests(ProfileFixture):
    def write_online(self):
        claude = self.root / "bin" / "claude"
        claude.parent.mkdir(parents=True, exist_ok=True)
        claude.write_text("x\n"); os.chmod(claude, 0o755)
        self.write({"id": "claude-code", "label": "Claude Code · NOVA", "tool": "claude-code", "provider": "online",
                    "command": str(claude), "project": str(self.project), "project_label": "NOVA source"})
        return claude

    def launch(self, snapshot, process=None):
        with patch.dict(actions.os.environ, SESSION, clear=False), \
             patch.object(actions.subprocess, "Popen", return_value=process or Process(0)) as popen:
            result = actions.open_coding_cli(snapshot)
        return result, popen

    def test_dispatch_uses_fixed_env_command(self):
        claude = self.write_online()
        result, popen = self.launch({"profile_id": "claude-code", "model": "online"})
        self.assertEqual((result["status"], result["window_observation"], result["label"]),
                         ("dispatched", "not_observed", "Claude Code · NOVA"))
        self.assertNotIn("model", result)
        self.assertEqual(popen.call_args.args[0],
                         ["/usr/bin/omarchy", "launch", "tui", "--app-id=TUI.nova-cli",
                          "/usr/bin/env", "-C", str(self.project), str(claude)])

    def test_model_other_than_online_is_refused_without_launch(self):
        self.write_online()
        for model in ("qwen3:14b", MODEL, ""):
            result, popen = self.launch({"profile_id": "claude-code", "model": model})
            self.assertEqual(result["error"], "profile_model_mismatch", model); popen.assert_not_called()

    def test_second_launch_while_running_is_not_blocked(self):
        self.write_online()
        class Running: returncode = 0
        self.pgrep.return_value = Running()
        result, popen = self.launch({"profile_id": "claude-code", "model": "online"})
        self.assertEqual(result["status"], "dispatched")
        popen.assert_called_once()


class NovadStateTests(unittest.TestCase):
    def state(self, profiles, resident, free=200 * 1024**3):
        usage = type("Usage", (), {"free": free})()
        with patch.object(novad, "actions_get", return_value=(200, {"configured": True, "error": None, "profiles": profiles})), \
             patch.object(novad, "resident_models", return_value=resident), \
             patch.object(novad.shutil, "disk_usage", return_value=usage):
            return novad.cli_profiles_state()

    def item(self, **changes):
        value = {"id": "qwen-nova-dev", "model": MODEL, "valid": True, "error": None, "running": False}
        value.update(changes); return value

    def test_available_when_nothing_or_same_model_resident(self):
        for resident in ([], [MODEL]):
            status, payload = self.state([self.item()], resident)
            self.assertEqual(status, 200)
            self.assertEqual((payload["profiles"][0]["available"], payload["profiles"][0]["blocked"]), (True, None))

    def test_blocked_reasons(self):
        cases = [(self.item(), ["qwen3:14b"], 200 * 1024**3, "conflicting_resident_model"),
                 (self.item(running=True), [], 200 * 1024**3, "cli_session_active"),
                 (self.item(valid=False, error="launcher_unavailable"), [], 200 * 1024**3, "launcher_unavailable"),
                 (self.item(), None, 200 * 1024**3, "provider_unavailable"),
                 (self.item(), [], 10 * 1024**3, "low_disk")]
        for item, resident, free, blocked in cases:
            _, payload = self.state([item], resident, free)
            self.assertEqual((payload["profiles"][0]["available"], payload["profiles"][0]["blocked"]), (False, blocked))
        _, payload = self.state([self.item()], ["qwen3:14b"])
        self.assertEqual(payload["profiles"][0]["conflicting_models"], ["qwen3:14b"])

    def online_item(self, **changes):
        value = {"id": "claude-code", "kind": "online", "valid": True, "error": None, "running": False}
        value.update(changes); return value

    def test_online_available_ignores_resident_and_disk(self):
        for resident, free in (([], 200 * 1024**3), (["qwen3:14b"], 200 * 1024**3),
                               (None, 200 * 1024**3), ([], 10 * 1024**3)):
            status, payload = self.state([self.online_item()], resident, free)
            self.assertEqual(status, 200)
            item = payload["profiles"][0]
            self.assertEqual((item["available"], item["blocked"], item["conflicting_models"]), (True, None, []))

    def test_online_invalid_is_blocked_with_its_error(self):
        _, payload = self.state([self.online_item(valid=False, error="command_unavailable")], ["qwen3:14b"], 10 * 1024**3)
        item = payload["profiles"][0]
        self.assertEqual((item["available"], item["blocked"], item["conflicting_models"]), (False, "command_unavailable", []))

    def test_executor_unavailable(self):
        with patch.object(novad, "actions_get", return_value=(0, {})):
            self.assertEqual(novad.cli_profiles_state()[0], 503)

    def test_cli_launch_is_not_a_chat_model_tool(self):
        names = json.dumps(novad.MODEL_TOOLS)
        self.assertNotIn("open_coding_cli", names)
        self.assertNotIn("open_coding_cli", novad.ACTION_REGISTRY)
        self.assertNotIn("open_coding_cli", novad.SYSTEM_INSTRUCTIONS)

    def test_ui_action_routes_to_fixed_executor_path(self):
        seen = {}
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, n): return b'{"status":"dispatched"}'
        def fake(request, timeout):
            seen["url"], seen["body"] = request.full_url, json.loads(request.data)
            return Response()
        with patch.object(novad.Path, "read_text", return_value="token"), patch.object(novad.urllib.request, "urlopen", fake):
            result = novad.execute_action("open_coding_cli", {"profile_id": "qwen-nova-dev", "model": MODEL})
        self.assertEqual(result["status"], "dispatched")
        self.assertEqual(seen["url"], "http://127.0.0.1:8791/v1/open-coding-cli")
        self.assertEqual(seen["body"], {"action": "open_coding_cli", "snapshot": {"profile_id": "qwen-nova-dev", "model": MODEL}})


if __name__ == "__main__":
    unittest.main()
