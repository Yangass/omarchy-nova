"""OpenCode on a local model (0.15): the profile, its launch, and NOVA's MCP tool."""
from __future__ import annotations
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from test_open_cli import ProfileFixture, Process, SESSION, actions, novad

NOVA_ROOT = Path(os.environ.get("NOVA_ROOT", str(Path(__file__).resolve().parents[1])))
sys.path.insert(0, str(NOVA_ROOT / "nova-actions"))
mcp_spec = importlib.util.spec_from_file_location("nova_mcp_for_test", NOVA_ROOT / "nova-actions" / "nova_mcp.py")
mcp = importlib.util.module_from_spec(mcp_spec)
assert mcp_spec and mcp_spec.loader
mcp_spec.loader.exec_module(mcp)


class OpenCodeProfileTests(ProfileFixture):
    def setUp(self):
        super().setUp()
        self.command = self.root / "bin" / "opencode"
        self.command.parent.mkdir(); self.command.write_text("#!/bin/bash\n"); self.command.chmod(0o755)

    def opencode(self, **changes):
        item = {"id": "opencode-qwen3-8b", "label": "OpenCode", "tool": "opencode", "provider": "ollama",
                "model": "qwen3:8b", "build": "nova-qwen3-8b-cli", "command": str(self.command),
                "project": str(self.project), "project_label": "~/Work", "care": "ask"}
        item.update(changes)
        return item

    def test_a_valid_profile_is_listed_as_opencode_without_paths(self):
        self.write(self.opencode())
        [item] = actions.list_cli_profiles()["profiles"]
        self.assertEqual((item["kind"], item["valid"], item["model"], item["build"], item["care"]),
                         ("opencode", True, "qwen3:8b", "nova-qwen3-8b-cli", "ask"))
        self.assertNotIn("command", item); self.assertNotIn("project", item)

    def test_invalid_profiles_are_refused(self):
        for changes, error in (({"care": "yolo"}, "invalid_profile"), ({"build": "qwen3:8b"}, "invalid_profile"),
                               ({"provider": "online"}, "unsupported_provider"), ({"command": "/usr/bin/rm"}, "command_unavailable"),
                               ({"project": "/nonexistent"}, "project_unavailable"), ({"extra": "x"}, "invalid_profile")):
            self.write(self.opencode(**changes))
            [item] = actions.list_cli_profiles()["profiles"]
            self.assertEqual((item["valid"], item["error"]), (False, error), changes)
        self.command.chmod(0o777); self.write(self.opencode())
        self.assertEqual(actions.list_cli_profiles()["profiles"][0]["error"], "command_unavailable")

    def test_launch_hands_opencode_novas_settings_and_never_a_shell(self):
        self.write(self.opencode(care="edits"))
        with patch.dict(os.environ, SESSION), patch.object(actions.subprocess, "Popen", return_value=Process(timeout=True)) as popen:
            result = actions.open_coding_cli({"profile_id": "opencode-qwen3-8b", "model": "qwen3:8b"})
        self.assertEqual(result["status"], "dispatched")
        argv = popen.call_args.args[0]
        self.assertEqual(argv[:6], ["/usr/bin/omarchy", "launch", "tui", "--app-id=" + actions.CLI_APP_ID, "/usr/bin/env", "-C"])
        self.assertEqual(argv[6], str(self.project))
        self.assertEqual(argv[-2:], [str(self.command), "--standalone"])
        config = json.loads(argv[7].split("=", 1)[1])
        self.assertEqual(config["model"], "ollama/nova-qwen3-8b-cli:latest")
        self.assertEqual(config["permission"], {"edit": "allow", "bash": "ask"})
        [instructions] = config["instructions"]
        self.assertTrue(instructions.endswith("nova-actions/opencode-instructions.md") and Path(instructions).is_file())
        self.assertIn("nova_change_my_working_context", Path(instructions).read_text())
        server = config["mcp"]["nova"]
        self.assertEqual((server["type"], server["environment"], server["timeout"]), ("local", {"NOVA_CLI_BUILD": "nova-qwen3-8b-cli"}, 130000))
        self.assertTrue(server["command"][-1].endswith("nova-actions/nova_mcp.py"))
        self.assertNotIn("sh", [Path(part).name for part in argv])

    def test_opencode_starts_only_inside_novas_private_network_space(self):
        # Marketplace review #12377 (2026-10-26): OpenCode reached 127.0.0.1:11434 itself,
        # without NOVA's check of who answers there. It now starts through the sandbox,
        # which gives it its own loopback and a checked relay to Ollama.
        self.write(self.opencode())
        with patch.dict(os.environ, SESSION), patch.object(actions.subprocess, "Popen", return_value=Process(timeout=True)) as popen:
            actions.open_coding_cli({"profile_id": "opencode-qwen3-8b", "model": "qwen3:8b"})
        argv = popen.call_args.args[0]
        sandbox = argv.index(str(actions.OPENCODE_SANDBOX))
        self.assertEqual(argv[sandbox - 2:sandbox], [actions.CLI_PYTHON, "-B"])
        self.assertEqual(argv[sandbox + 1:sandbox + 4], ["--build", "nova-qwen3-8b-cli", "--"])
        self.assertEqual(argv[sandbox + 4:], [str(self.command), "--standalone"])
        self.assertTrue(actions.OPENCODE_SANDBOX.is_file())

    def test_the_wrong_model_is_refused(self):
        self.write(self.opencode())
        with patch.dict(os.environ, SESSION), patch.object(actions.subprocess, "Popen") as popen:
            result = actions.open_coding_cli({"profile_id": "opencode-qwen3-8b", "model": "llama3.2:1b"})
        self.assertEqual(result["error"], "profile_model_mismatch"); popen.assert_not_called()

    def test_novad_blocks_only_invalid_profiles_a_model_switch_or_a_missing_build(self):
        profile = {"id": "opencode-qwen3-8b", "kind": "opencode", "valid": True, "model": "qwen3:8b", "build": "nova-qwen3-8b-cli"}
        payload = {"profiles": [profile], "client_active": True}
        with patch.object(novad, "actions_get", return_value=(200, payload)), patch.object(novad, "resident_models", return_value=["other:1b"]), \
             patch.object(novad.CLI_BUILDS, "load", return_value={"nova-qwen3-8b-cli": {"base": "qwen3:8b", "context": 32768}}):
            [state] = novad.cli_profiles_state()[1]["profiles"]
            self.assertEqual((state["available"], state["blocked"], state["context"]), (True, None, 32768))
            with patch.object(novad.RUNTIME, "snapshot", return_value={"state": "model_switching"}):
                self.assertEqual(novad.cli_profiles_state()[1]["profiles"][0]["blocked"], "model_loading")
        with patch.object(novad, "actions_get", return_value=(200, payload)), patch.object(novad, "resident_models", return_value=[]), \
             patch.object(novad.CLI_BUILDS, "load", return_value={}):
            self.assertEqual(novad.cli_profiles_state()[1]["profiles"][0]["blocked"], "build_missing")
        # Ollama not answering through NOVA's checked connection (stopped, or another
        # account holds its port): OpenCode doesn't start (review #12377).
        with patch.object(novad, "actions_get", return_value=(200, payload)), patch.object(novad, "resident_models", return_value=None), \
             patch.object(novad.CLI_BUILDS, "load", return_value={"nova-qwen3-8b-cli": {"base": "qwen3:8b", "context": 32768}}):
            self.assertEqual(novad.cli_profiles_state()[1]["profiles"][0]["blocked"], "provider_unavailable")


class McpTests(unittest.TestCase):
    def call(self, message):
        return mcp.answer(message)

    def test_handshake_and_tool_list(self):
        reply = self.call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}})
        self.assertEqual(reply["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(self.call({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "1999"}})["result"]["protocolVersion"], mcp.PROTOCOLS[0])
        self.assertIsNone(self.call({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        names = [tool["name"] for tool in self.call({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})["result"]["tools"]]
        self.assertEqual(names, ["nova_my_settings", "nova_change_my_working_context"])
        self.assertEqual(self.call({"jsonrpc": "2.0", "id": 4, "method": "resources/list"})["error"]["code"], -32601)

    def tool(self, name, arguments, novad_reply):
        calls = []
        def fake(service, method, path, body=None, headers=None, timeout=10):
            calls.append((service, method, path, json.loads(body) if body else None, timeout))
            return novad_reply
        with patch.object(mcp, "BUILD", "nova-qwen3-8b-cli"), patch.object(mcp.local_socket, "call", fake):
            reply = self.call({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
        return reply["result"], calls

    def test_my_settings_reads_novad(self):
        state = {"model": "qwen3:8b", "build": "nova-qwen3-8b-cli", "gpu": "RTX 5080", "context": 32768, "recommended_context": 32768,
                 "native_context": 40960, "verdict": {"text": "Fits: 10.3 of 13.6 GiB."}, "offered_contexts": [32768, 40960]}
        result, calls = self.tool("nova_my_settings", {}, (200, json.dumps(state).encode()))
        self.assertFalse(result["isError"])
        self.assertIn("Working context: 32K", result["content"][0]["text"])
        self.assertEqual(calls, [("novad", "GET", "/cli/self?build=nova-qwen3-8b-cli", None, 10)])

    def test_a_change_waits_for_the_user_and_reports_their_answer(self):
        reply = {"status": "changed", "context": 40960, "requested_context": 65536, "previous_context": 32768}
        result, calls = self.tool("nova_change_my_working_context", {"context_tokens": 65536, "reason": "big repo"}, (200, json.dumps(reply).encode()))
        self.assertIn("approved: your working context is now 40K", result["content"][0]["text"])
        self.assertEqual(calls[0][1:4], ("POST", "/cli/self/proposals", {"build": "nova-qwen3-8b-cli", "context": 65536, "reason": "big repo"}))
        self.assertGreater(calls[0][4], 130)
        for status, words in (("denied", "declined"), ("expired", "didn't answer")):
            result, _ = self.tool("nova_change_my_working_context", {"context_tokens": 65536, "reason": ""},
                                  (200, json.dumps({"status": status, "previous_context": 32768}).encode()))
            self.assertIn(words, result["content"][0]["text"]); self.assertIn("stays 32K", result["content"][0]["text"])

    def test_bad_arguments_and_a_missing_novad_are_errors_not_crashes(self):
        result, calls = self.tool("nova_change_my_working_context", {"context_tokens": 10 ** 9, "reason": "x"}, (200, b"{}"))
        self.assertTrue(result["isError"]); self.assertEqual(calls, [])
        def down(*_a, **_k): raise OSError("no socket")
        with patch.object(mcp.local_socket, "call", down):
            reply = self.call({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "nova_my_settings", "arguments": {}}})
        self.assertTrue(reply["result"]["isError"])

    def test_the_stdio_loop_answers_line_by_line(self):
        lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}), "not json",
                 json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})]
        out = io.StringIO()
        with patch.object(mcp.sys, "stdin", io.StringIO("\n".join(lines) + "\n")), patch.object(mcp.sys, "stdout", out):
            mcp.main()
        replies = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual([r.get("id") for r in replies], [1, None])
        self.assertEqual(replies[1]["error"]["code"], -32700)


class NovadEndpointTests(unittest.TestCase):
    ROW = {"id": "qwen3:8b", "metadata": {"own_context": None},
           "fit": {"weights_bytes": int(4.9 * 2**30), "kv_bytes_per_token_estimate": 184320, "runtime_reserve_bytes": 2**30,
                   "native_context": 40960, "recommended_context": 32768, "fixed_cache_bytes": 0}}

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="nova-endpoints-", dir="/var/tmp"))
        novad.CLI_SELF_LAST_REQUEST[0] = 0.0
        for target, value in ((novad, "installed_fit"), ):
            p = patch.object(novad, value, return_value=(self.ROW, {"operating_budget_bytes": int(13.6 * 2**30), "device": "gpu0"}, "RTX 5080"))
            p.start(); self.addCleanup(p.stop)
        builds = patch.object(novad, "CLI_BUILDS", novad.coding_cli.Builds(self.dir / "builds.json")); builds.start(); self.addCleanup(builds.stop)
        proposals = patch.object(novad, "CLI_PROPOSALS", novad.coding_cli.Proposals()); proposals.start(); self.addCleanup(proposals.stop)
        self.created = []
        provider = patch.object(novad.PROVIDERS["ollama"], "create_build", lambda name, base, ctx: self.created.append((name, base, ctx)))
        provider.start(); self.addCleanup(provider.stop)

    def test_tune_state_reports_the_recommended_context_and_its_fit(self):
        status, state = novad.cli_tune_state("qwen3:8b")
        self.assertEqual((status, state["build"], state["context"], state["verdict"]["level"], state["gpu"]), (200, "nova-qwen3-8b-cli", 32768, "fits", "RTX 5080"))
        self.assertEqual(novad.cli_tune_state("bad name;rm")[0], 400)

    def test_setup_proposal_lists_exact_changes_and_approving_it_builds_and_adds_the_profile(self):
        project = Path.home() / ".cache"
        command = self.dir / "opencode"; command.write_text("#!/bin/bash\n"); command.chmod(0o755)
        profiles = self.dir / "cli-profiles.json"
        with patch.object(novad.coding_cli, "tool_command", return_value=command), patch.object(novad.coding_cli, "PROFILES_PATH", profiles), \
             patch.object(novad.coding_cli, "add_profile", lambda profile: novad.coding_cli._write_json(profiles, {"schema": 1, "profiles": [profile]})):
            status, item = novad.cli_setup_proposal({"model": "qwen3:8b", "tool": "opencode", "project": "~/.cache", "care": "ask"})
            self.assertEqual(status, 201)
            self.assertTrue(item["id"].startswith("cli_") and item["coding_cli"])
            self.assertIn("nova-qwen3-8b-cli", item["changes"][0]); self.assertIn("32K", item["changes"][0])
            self.assertEqual(novad.CLI_PROPOSALS.pending()["id"], item["id"])
            self.assertEqual(self.created, [])
            result = novad.CLI_PROPOSALS.decide(item["id"], "approve")
        self.assertEqual(result[1]["status"], "set_up")
        self.assertEqual(self.created, [("nova-qwen3-8b-cli", "qwen3:8b", 32768)])
        [profile] = json.loads(profiles.read_text())["profiles"]
        self.assertEqual((profile["tool"], profile["build"], profile["project"], profile["care"]), ("opencode", "nova-qwen3-8b-cli", str(project.resolve()), "ask"))
        # Chat now follows the build's context: one loaded copy for both.
        self.assertEqual(novad.chat_context("qwen3:8b"), 32768)

    def test_setup_refuses_bad_requests_before_asking(self):
        for value in ({"model": "qwen3:8b", "tool": "bash", "project": "~/.cache", "care": "ask"},
                      {"model": "qwen3:8b", "tool": "opencode", "project": "/etc", "care": "ask"},
                      {"model": "qwen3:8b", "tool": "opencode", "project": "~/.cache", "care": "ask", "extra": 1}, None):
            with patch.object(novad.coding_cli, "tool_command", return_value=Path("/bin/true")):
                self.assertIn(novad.cli_setup_proposal(value)[0], (400, 409), value)
        self.assertIsNone(novad.CLI_PROPOSALS.pending())

    def test_the_cli_asking_for_more_waits_for_the_user_and_offers_what_fits(self):
        novad.CLI_BUILDS.record("nova-qwen3-8b-cli", "qwen3:8b", 32768)
        answer = {}
        thread = threading.Thread(target=lambda: answer.update(result=novad.cli_self_proposal({"build": "nova-qwen3-8b-cli", "context": 65536, "reason": "big repo"})))
        thread.start()
        for _ in range(100):
            item = novad.CLI_PROPOSALS.pending()
            if item: break
            threading.Event().wait(0.02)
        self.assertEqual(item["action"], "coding_cli_tune")
        self.assertEqual(item["verdict"]["level"], "spills")
        self.assertTrue(item["approve_label"].endswith("anyway"))
        # 40K is tight on this card, so the best that fits is the current 32K: no alternative offered.
        self.assertIsNone(item["alternative_context"])
        self.assertIn('"big repo"', item["changes"][0])
        novad.CLI_PROPOSALS.decide(item["id"], "deny")
        thread.join(5)
        self.assertEqual(answer["result"][1]["status"], "denied")
        self.assertEqual(self.created, [])

    def test_the_alternative_builds_the_fitting_context(self):
        novad.CLI_BUILDS.record("nova-qwen3-8b-cli", "qwen3:8b", 16384)
        answer = {}
        thread = threading.Thread(target=lambda: answer.update(result=novad.cli_self_proposal({"build": "nova-qwen3-8b-cli", "context": 65536, "reason": ""})))
        thread.start()
        for _ in range(100):
            item = novad.CLI_PROPOSALS.pending()
            if item: break
            threading.Event().wait(0.02)
        alternative = item["alternative_context"]
        self.assertTrue(alternative and alternative <= 40960)
        novad.CLI_PROPOSALS.decide(item["id"], "alternative")
        thread.join(5)
        self.assertEqual(answer["result"][1], {"status": "changed", "context": alternative, "requested_context": 65536, "previous_context": 16384})
        self.assertEqual(self.created, [("nova-qwen3-8b-cli", "qwen3:8b", alternative)])

    def test_a_model_nova_cant_estimate_still_reaches_the_user_with_a_warning(self):
        novad.CLI_BUILDS.record("nova-qwen3-8b-cli", "qwen3:8b", 32768)
        unknown = {**self.ROW, "fit": {}}
        answer = {}
        with patch.object(novad, "installed_fit", return_value=(unknown, {"operating_budget_bytes": int(13.6 * 2**30)}, "RTX 5080")):
            thread = threading.Thread(target=lambda: answer.update(result=novad.cli_self_proposal({"build": "nova-qwen3-8b-cli", "context": 65536})))
            thread.start()
            for _ in range(100):
                item = novad.CLI_PROPOSALS.pending()
                if item: break
                threading.Event().wait(0.02)
            self.assertEqual(item["verdict"]["level"], "unknown")
            self.assertIn("can't estimate", item["verdict"]["text"])
            self.assertTrue(item["approve_label"].endswith("anyway"))
            self.assertIsNone(item["alternative_context"])
            novad.CLI_PROPOSALS.decide(item["id"], "deny")
            thread.join(5)
        self.assertEqual(answer["result"][1]["status"], "denied")

    def test_the_cli_can_ask_once_every_20_seconds(self):
        novad.CLI_BUILDS.record("nova-qwen3-8b-cli", "qwen3:8b", 32768)
        with patch.object(novad.CLI_PROPOSALS, "wait", return_value={"status": "denied"}):
            self.assertEqual(novad.cli_self_proposal({"build": "nova-qwen3-8b-cli", "context": 40960})[0], 200)
            self.assertEqual(novad.cli_self_proposal({"build": "nova-qwen3-8b-cli", "context": 40960}), (429, {"error": "too_many_requests"}))
            with patch.object(novad.time, "monotonic", return_value=novad.time.monotonic() + 21):
                self.assertEqual(novad.cli_self_proposal({"build": "nova-qwen3-8b-cli", "context": 40960})[0], 200)

    def test_unknown_builds_are_refused(self):
        self.assertEqual(novad.cli_self_proposal({"build": "nova-other-cli", "context": 65536})[0], 404)
        self.assertEqual(novad.cli_self_proposal({"build": "x", "context": "lots"})[0], 400)


if __name__ == "__main__":
    unittest.main()
