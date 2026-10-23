"""Coding CLI for everyone (0.15): tailored builds, the fit verdict, profiles, approvals."""
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import coding_cli as cc

GIB = cc.GIB
# Qwen 3 8B on an RTX 5080 (16 GiB): model_fit's numbers, rounded.
QWEN3_8B_16GB = {"budget_bytes": int(13.6 * GIB), "weights_bytes": int(4.9 * GIB), "runtime_bytes": GIB,
                 "fixed_cache_bytes": 0, "kv_bytes_per_token": 184320, "native_context": 40960,
                 "recommended_context": 32768, "tight_margin_bytes": int(13.6 * GIB * 0.05),
                 "steps": [s for s in cc.STEPS if s <= 81920]}


class NameTests(unittest.TestCase):
    def test_build_names_are_stable_valid_and_tag_free(self):
        self.assertEqual(cc.build_name("qwen3:8b"), "nova-qwen3-8b-cli")
        self.assertEqual(cc.build_name("llama3.2:latest"), "nova-llama3.2-cli")
        self.assertEqual(cc.build_name("hf.co/Org/Model-GGUF:Q4_K_M"), "nova-hf.co-org-model-gguf-q4-k-m-cli")
        for base in ("qwen3:8b", "llama3.2:latest", "../../etc", "x" * 300):
            self.assertRegex(cc.build_name(base), cc.BUILD_NAME)

    def test_profile_ids_fit_nova_actions_rules(self):
        import re
        for base in ("qwen3:8b", "llama3.2:3b", "gpt-oss:20b", "a" * 120):
            self.assertRegex(cc.profile_id("opencode", base), re.compile(r"[a-z0-9][a-z0-9-]{0,39}"))
        self.assertEqual(cc.profile_id("opencode", "qwen3:8b"), "opencode-qwen3-8b")


class VerdictTests(unittest.TestCase):
    def test_the_recommended_context_fits(self):
        self.assertEqual(cc.verdict(QWEN3_8B_16GB, 32768)["level"], "fits")

    def test_more_context_gets_tight_then_spills(self):
        levels = [cc.verdict(QWEN3_8B_16GB, s)["level"] for s in (32768, 40960, 49152, 65536)]
        self.assertEqual(levels[0], "fits")
        self.assertIn("spills", levels)
        self.assertEqual(levels, sorted(levels, key=["fits", "tight", "spills"].index))

    def test_past_the_models_own_maximum_is_flagged_even_when_it_fits(self):
        roomy = {**QWEN3_8B_16GB, "budget_bytes": 40 * GIB, "tight_margin_bytes": 2 * GIB}
        result = cc.verdict(roomy, 65536)
        self.assertEqual((result["level"], result["past_native"]), ("fits", True))
        self.assertIn("trained for (40K)", result["text"])

    def test_best_fitting_never_passes_the_models_maximum(self):
        roomy = {**QWEN3_8B_16GB, "budget_bytes": 40 * GIB, "tight_margin_bytes": 2 * GIB}
        self.assertEqual(cc.best_fitting(roomy, 262144), 40960)
        self.assertEqual(cc.best_fitting(QWEN3_8B_16GB, 65536), cc.best_fitting(QWEN3_8B_16GB, 40960))

    def test_numbers_come_from_model_fit_or_not_at_all(self):
        row = {"fit": {"weights_bytes": 5 * GIB, "kv_bytes_per_token_estimate": 184320, "runtime_reserve_bytes": GIB,
                       "native_context": 40960, "recommended_context": 32768, "fixed_cache_bytes": 0}}
        numbers = cc.tune_numbers(row, {"operating_budget_bytes": 14 * GIB})
        self.assertEqual(numbers["native_context"], 40960)
        self.assertIn(32768, numbers["steps"]); self.assertNotIn(131072, numbers["steps"])
        self.assertIsNone(cc.tune_numbers({"fit": {}}, {"operating_budget_bytes": 14 * GIB}))
        self.assertIsNone(cc.tune_numbers(row, {"operating_budget_bytes": None}))


class FileTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="nova-cc-", dir="/var/tmp"))

    def test_builds_are_recorded_and_retuned_in_place(self):
        builds = cc.Builds(self.dir / "tailored-builds.json")
        made = []
        name = cc.make_build(lambda n, b, c: made.append((n, b, c)), builds, "qwen3:8b", 32768)
        cc.make_build(lambda n, b, c: made.append((n, b, c)), builds, "qwen3:8b", 40960)
        self.assertEqual(made, [(name, "qwen3:8b", 32768), (name, "qwen3:8b", 40960)])
        self.assertEqual(builds.for_base("qwen3:8b"), (name, {**builds.load()[name]}))
        self.assertEqual(builds.load()[name]["context"], 40960)
        self.assertEqual(os.stat(self.dir / "tailored-builds.json").st_mode & 0o777, 0o600)

    def test_only_offered_contexts_are_built(self):
        with self.assertRaises(ValueError):
            cc.make_build(lambda *a: None, cc.Builds(self.dir / "b.json"), "qwen3:8b", 33000)

    def test_a_damaged_builds_file_reads_as_none(self):
        path = self.dir / "b.json"; path.write_text("{not json")
        self.assertEqual(cc.Builds(path).load(), {})

    def test_profiles_keep_every_other_entry_and_never_overwrite_a_file_they_cant_read(self):
        path = self.dir / "cli-profiles.json"
        mine = {"id": "claude-code", "tool": "claude-code", "provider": "online"}
        path.write_text(json.dumps({"schema": 1, "profiles": [mine]}))
        cc.add_profile({"id": "opencode-qwen3-8b", "tool": "opencode"}, path)
        cc.add_profile({"id": "opencode-qwen3-8b", "tool": "opencode", "care": "edits"}, path)
        profiles = json.loads(path.read_text())["profiles"]
        self.assertEqual([p["id"] for p in profiles], ["claude-code", "opencode-qwen3-8b"])
        self.assertEqual(profiles[1]["care"], "edits")
        path.write_text("// my notes\n{")
        with self.assertRaises(ValueError): cc.add_profile({"id": "x"}, path)
        self.assertEqual(path.read_text(), "// my notes\n{")

    def test_tool_command_is_omarchys_user_owned_wrapper(self):
        home = self.dir / "home"; (home / ".local" / "bin").mkdir(parents=True)
        self.assertIsNone(cc.tool_command("opencode", home))
        wrapper = home / ".local" / "bin" / "opencode"
        wrapper.write_text("#!/bin/bash\n"); wrapper.chmod(0o755)
        self.assertEqual(cc.tool_command("opencode", home), wrapper)
        wrapper.chmod(0o777)
        self.assertIsNone(cc.tool_command("opencode", home))
        self.assertIsNone(cc.tool_command("rm", home))

    def test_projects_are_existing_folders_inside_home_but_not_home_itself(self):
        inside = Path.home() / ".cache"
        self.assertEqual(cc.project_ok(str(inside)), inside.resolve())
        self.assertEqual(cc.project_ok("~/.cache"), inside.resolve())
        for bad in ("~", "/etc", "relative/path", "/nonexistent-nova", "", None, "~/" + "x" * 500):
            self.assertIsNone(cc.project_ok(bad), bad)


class ProjectListTests(unittest.TestCase):
    def test_code_folders_first_then_others_never_media_or_hidden(self):
        home = Path(tempfile.mkdtemp(prefix="nova-home-", dir="/var/tmp"))
        for name in ("Downloads", "Music", ".config", "notes", "Work", "Code", "pulsar"): (home / name).mkdir()
        (home / "file.txt").write_text("x")
        listed = cc.candidate_projects(home)
        self.assertEqual(listed[:2], ["~/Code", "~/Work"])
        self.assertEqual(sorted(listed[2:]), ["~/notes", "~/pulsar"])
        self.assertEqual(cc.candidate_projects(home, limit=1), ["~/Code"])
        self.assertEqual(cc.candidate_projects(home / "missing"), [])


class ProposalTests(unittest.TestCase):
    def test_approve_runs_the_change_once_and_wakes_the_waiter(self):
        store, ran = cc.Proposals(), []
        item = store.create({"summary": "s"}, lambda decision: ran.append(decision) or {"status": "done"})
        self.assertTrue(item["id"].startswith("cli_") and item["coding_cli"])
        self.assertEqual(store.pending()["id"], item["id"])
        waiter = {}
        thread = threading.Thread(target=lambda: waiter.update(store.wait(item["id"], 5))); thread.start()
        self.assertEqual(store.decide(item["id"], "approve"), (200, {"status": "done"}))
        thread.join(5)
        self.assertEqual((ran, waiter), (["approve"], {"status": "done"}))
        self.assertEqual(store.decide(item["id"], "approve")[0], 409)
        self.assertIsNone(store.pending())

    def test_deny_changes_nothing(self):
        store, ran = cc.Proposals(), []
        item = store.create({}, lambda decision: ran.append(decision) or {})
        self.assertEqual(store.decide(item["id"], "deny"), (200, {"status": "denied"}))
        self.assertEqual(ran, [])

    def test_alternative_only_when_offered(self):
        store = cc.Proposals()
        plain = store.create({}, lambda d: {"status": d})
        self.assertEqual(store.decide(plain["id"], "alternative")[0], 400)
        offered = store.create({"alternative_context": 40960}, lambda d: {"status": d})
        self.assertEqual(store.decide(offered["id"], "alternative"), (200, {"status": "alternative"}))

    def test_expired_and_replaced_requests_change_nothing(self):
        store, ran = cc.Proposals(), []
        first = store.create({}, lambda d: ran.append(d) or {})
        second = store.create({}, lambda d: ran.append(d) or {})
        self.assertEqual(store.wait(first["id"], 1), {"status": "replaced"})
        with patch.object(cc.time, "time", return_value=time.time() + 1000):
            self.assertEqual(store.decide(second["id"], "approve")[0], 410)
        self.assertEqual(ran, [])

    def test_a_failing_change_reports_instead_of_crashing(self):
        store = cc.Proposals()
        def broken(_): raise OSError("ollama said no")
        item = store.create({}, broken)
        self.assertEqual(store.decide(item["id"], "approve"), (200, {"status": "failed", "error": "ollama said no"}))


if __name__ == "__main__":
    unittest.main()
