"""ONLINE-01 step 2 and OpenCode detection (2026-10-23, Konstantin's friend).

- Claude Code and Codex: Omarchy ships a launcher for each (~/.local/bin/claude, codex), but
  NOVA listed online CLIs only from a hand-written cli-profiles.json, so a fresh install
  showed none. NOVA now offers them and adds the OPEN CLI in one approval.
- OpenCode: nova-actions said running=False always, so its row never said CLI RUNNING and
  unload/delete weren't blocked while it ran. OpenCode carries NOVA's settings
  (OPENCODE_CONFIG_CONTENT) from launch; that is how nova-actions tells it is running.
"""
from __future__ import annotations
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_open_cli import ProfileFixture, actions, novad


ADD = novad.coding_cli.add_profile  # the real one, writing to a test file


def fake_proc(root: Path, processes: dict[str, bytes]) -> Path:
    for pid, environ in processes.items():
        (root / pid).mkdir(parents=True)
        (root / pid / "environ").write_bytes(environ)
    return root


def settings(model: str) -> bytes:
    return b"OPENCODE_CONFIG_CONTENT=" + json.dumps({"model": model, "permission": {"bash": "ask"}}).encode()


class OpenCodeRunning(ProfileFixture):
    def setUp(self):
        super().setUp()
        self.command = self.root / "bin" / "opencode"
        self.command.parent.mkdir(); self.command.write_text("#!/bin/bash\n"); self.command.chmod(0o755)
        self.proc = Path(tempfile.mkdtemp(prefix="nova-proc-", dir="/var/tmp"))

    def opencode(self):
        return {"id": "opencode-qwen3-8b", "label": "OpenCode", "tool": "opencode", "provider": "ollama",
                "model": "qwen3:8b", "build": "nova-qwen3-8b-cli", "command": str(self.command),
                "project": str(self.project), "project_label": "~/Work", "care": "ask"}

    def test_the_build_comes_from_novas_settings_in_its_processes(self):
        proc = fake_proc(self.proc, {"100": b"HOME=/x\0" + settings("ollama/nova-qwen3-8b-cli:latest") + b"\0TERM=xterm\0",
                                     "101": settings("ollama/nova-gemma3-12b-cli:latest"),
                                     "102": b"HOME=/x\0PATH=/bin\0",                       # anything else
                                     "103": b"OPENCODE_CONFIG_CONTENT=not json\0",
                                     "104": settings("openai/gpt-5"),                       # not a local build
                                     "105": b"XOPENCODE_CONFIG_CONTENT=" + settings("ollama/nova-x-cli")[24:],
                                     "self": b""})
        self.assertEqual(actions.opencode_running_builds(proc), {"nova-qwen3-8b-cli", "nova-gemma3-12b-cli"})

    def test_a_running_opencode_is_listed_as_running(self):
        self.write(self.opencode())
        with patch.object(actions, "opencode_running_builds", return_value={"nova-qwen3-8b-cli"}):
            [item] = actions.list_cli_profiles()["profiles"]
        self.assertTrue(item["running"])
        with patch.object(actions, "opencode_running_builds", return_value=set()):
            self.assertFalse(actions.list_cli_profiles()["profiles"][0]["running"])

    def test_a_real_process_is_seen_and_forgotten_when_it_ends(self):
        env = {**os.environ, "OPENCODE_CONFIG_CONTENT": json.dumps({"model": "ollama/nova-probe-test-cli:latest"})}
        process = subprocess.Popen(["sleep", "30"], env=env)
        try:
            time.sleep(0.1)
            self.assertIn("nova-probe-test-cli", actions.opencode_running_builds())
        finally:
            process.kill(); process.wait()
        self.assertNotIn("nova-probe-test-cli", actions.opencode_running_builds())

    def test_while_it_runs_its_model_is_busy(self):
        payload = {"profiles": [{"kind": "opencode", "model": "qwen3:8b", "running": True}]}
        self.assertEqual(novad.cli_busy_models(payload), ["qwen3:8b"])


class OnlineSetup(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="nova-online-", dir="/var/tmp"))
        for target, name, value in ((novad, "CLI_PROPOSALS", novad.coding_cli.Proposals()), (novad, "audit", lambda *a, **k: None)):
            p = patch.object(target, name, value); p.start(); self.addCleanup(p.stop)
        self.launcher = self.dir / "bin" / "claude"
        self.launcher.parent.mkdir(); self.launcher.write_text("#!/bin/bash\n"); self.launcher.chmod(0o755)

    def tools(self, available=("claude-code",)):
        return patch.object(novad.coding_cli, "tool_command", lambda tool: self.launcher if tool in available else None)

    def test_profiles_say_which_online_tools_are_here_and_set_up(self):
        with self.tools():
            state = novad.online_tools_state([{"kind": "online", "tool": "codex"}, {"kind": "opencode", "tool": "opencode"}])
        self.assertEqual([(t["id"], t["service"], t["available"], t["set_up"]) for t in state],
                         [("claude-code", "Anthropic", True, False), ("codex", "OpenAI", False, True), ("antigravity", "Google", False, False)])
        self.assertEqual([t["install_hint"] for t in state], ["Omarchy's update installs it"] * 2 + ["get it from antigravity.google"])

    def test_setup_lists_the_exact_changes_and_approving_writes_one_profile(self):
        profiles = self.dir / "cli-profiles.json"
        profiles.write_text(json.dumps({"schema": 1, "profiles": [{"id": "qwen-nova-dev", "tool": "qwen-code"}]}))
        with self.tools(), patch.object(novad.coding_cli, "PROFILES_PATH", profiles), \
             patch.object(novad.coding_cli, "add_profile", lambda profile: ADD(profile, profiles)):
            status, item = novad.online_setup_proposal({"tool": "claude-code", "project": "~/.cache"})
            self.assertEqual((status, item["action"], item["model"]), (201, "coding_cli_online_setup", "online:claude-code"))
            self.assertEqual(item["summary"], "Set up Claude Code in ~/.cache")
            self.assertIn("Anthropic under your own account", item["changes"][1])
            self.assertEqual(json.loads(profiles.read_text())["profiles"], [{"id": "qwen-nova-dev", "tool": "qwen-code"}])  # nothing yet
            result = novad.CLI_PROPOSALS.decide(item["id"], "approve")
        self.assertEqual(result[1]["status"], "online_set_up")
        kept, added = json.loads(profiles.read_text())["profiles"]
        self.assertEqual(kept, {"id": "qwen-nova-dev", "tool": "qwen-code"})
        self.assertEqual(added, {"id": "claude-code", "label": "Claude Code", "tool": "claude-code", "provider": "online",
                                 "command": str(self.launcher), "project": str((Path.home() / ".cache").resolve()), "project_label": "~/.cache"})
        # nova-actions accepts what NOVA wrote.
        self.assertIsNone(actions.validate_cli_profile(added)[1])

    def test_bad_requests_are_refused_before_asking(self):
        with self.tools():
            for value in ({"tool": "bash", "project": "~/.cache"}, {"tool": "claude-code", "project": "/etc"},
                          {"tool": "claude-code", "project": "~"}, {"tool": "claude-code", "project": "~/.cache", "x": 1}, None, []):
                self.assertEqual(novad.online_setup_proposal(value)[0], 400, value)
            self.assertEqual(novad.online_setup_proposal({"tool": "codex", "project": "~/.cache"})[0], 409)
        self.assertIsNone(novad.CLI_PROPOSALS.pending())

    def test_the_setup_card_gets_the_tools_and_folders(self):
        with self.tools(), patch.object(novad.coding_cli, "candidate_projects", return_value=["~/Work", "~/nova"]):
            status, state = novad.online_setup_state()
        self.assertEqual((status, [t["id"] for t in state["tools"]], state["projects"]), (200, ["claude-code", "codex", "antigravity"], ["~/Work", "~/nova"]))
        self.assertEqual([t["available"] for t in state["tools"]], [True, False, False])

    def test_antigravity_is_set_up_like_the_others_and_needs_no_omarchy_download(self):
        """2026-10-23 (Konstantin): Antigravity's agy, from Google's own installer in ~/.local/bin."""
        agy = self.dir / "bin" / "agy"; agy.write_text("#!/bin/bash\n"); agy.chmod(0o755)
        profiles = self.dir / "cli-profiles.json"
        with patch.object(novad.coding_cli, "tool_command", lambda tool: agy if tool == "antigravity" else None), \
             patch.object(novad.coding_cli, "PROFILES_PATH", profiles), patch.object(novad.coding_cli, "add_profile", lambda profile: ADD(profile, profiles)):
            status, item = novad.online_setup_proposal({"tool": "antigravity", "project": "~/.cache"})
            self.assertEqual((status, item["summary"]), (201, "Set up Antigravity in ~/.cache"))
            self.assertIn("Google under your own account", item["changes"][1])
            self.assertEqual(item["changes"][2], "Antigravity asks you to sign in the first time, if you haven't yet.")
            novad.CLI_PROPOSALS.decide(item["id"], "approve")
        [added] = json.loads(profiles.read_text())["profiles"]
        self.assertEqual((added["id"], added["tool"], added["command"]), ("antigravity", "antigravity", str(agy)))
        self.assertIsNone(actions.validate_cli_profile(added)[1])
        # Named anything else, the same file is refused.
        other = self.dir / "bin" / "notagy"; other.write_text("#!/bin/bash\n"); other.chmod(0o755)
        self.assertEqual(actions.validate_cli_profile({**added, "command": str(other)})[1], "command_unavailable")


if __name__ == "__main__":
    unittest.main()
