"""0.16: a model's coding-CLI build follows its model. Deleting the model deletes the build
(which shares its files) and NOVA's OPEN CLI for it; updating the model rebuilds the build.

Before, the build stayed behind, hidden from the list: deleting qwen3:8b freed a few
kilobytes ("its main files stay: nova-qwen3-8b-cli uses them") and the user couldn't delete
the build. After an update the CLI kept running the old model and its old files stayed.
"""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import coding_cli as cc
import model_removal
from model_removal import RemovalStore
from providers import ProviderOperationFailed
import server

GIB = 1 << 30
W_OLD, W_NEW, P, CTX = ("sha256:" + c * 64 for c in "abcd")
BUILD = "nova-qwen3-8b-cli"
INVENTORY = [{"id": "qwen3:8b", "provider": "ollama", "digest": "d8", "metadata": {"size_bytes": 5 * GIB}},
             {"id": BUILD + ":latest", "provider": "ollama", "digest": "dbuild", "metadata": {"size_bytes": 5 * GIB}},
             {"id": "llama3.2:1b", "provider": "ollama", "digest": "d1", "metadata": {"size_bytes": GIB}}]
RECORDS = [{"id": "qwen3:8b", "layers": {W_OLD: 5 * GIB, P: 500}},
           {"id": BUILD + ":latest", "layers": {W_OLD: 5 * GIB, CTX: 520}},
           {"id": "llama3.2:1b", "layers": {"sha256:" + "f" * 64: GIB}}]
BUILDS = {BUILD: {"base": "qwen3:8b", "context": 40960, "created_at": 1}}


def inputs(**changes):
    value = {"inventory": INVENTORY, "selected_model": "llama3.2:1b", "resident": [], "cli_models": [], "builds": [BUILD]}
    value.update(changes)
    return value


class Temp(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="nova-tailored-", dir="/var/tmp"))


class CodingCliFileTests(Temp):
    def test_forget_drops_only_that_build(self):
        builds = cc.Builds(self.dir / "b.json")
        builds.record(BUILD, "qwen3:8b", 40960)
        builds.record("nova-llama3.2-1b-cli", "llama3.2:1b", 32768)
        self.assertTrue(builds.forget(BUILD))
        self.assertEqual(list(builds.load()), ["nova-llama3.2-1b-cli"])
        self.assertFalse(builds.forget(BUILD))

    def test_remove_profiles_takes_only_novas_profiles_for_that_build(self):
        path = self.dir / "cli-profiles.json"
        keep = [{"id": "claude-code", "tool": "claude-code", "provider": "online"},
                {"id": "nova-dev", "tool": "qwen-code", "model": "qwen3:8b", "build": BUILD},  # not NOVA's: kept
                {"id": "opencode-llama3-2-1b", "tool": "opencode", "build": "nova-llama3.2-1b-cli"}]
        path.write_text(json.dumps({"schema": 1, "profiles": keep[:2] + [{"id": "opencode-qwen3-8b", "tool": "opencode", "build": BUILD}] + keep[2:]}))
        self.assertEqual(cc.remove_profiles(BUILD, path), 1)
        self.assertEqual(json.loads(path.read_text())["profiles"], keep)
        self.assertEqual(cc.remove_profiles(BUILD, path), 0)
        self.assertEqual(cc.remove_profiles(BUILD, self.dir / "missing.json"), 0)
        path.write_text("// my notes\n{")
        with self.assertRaises(ValueError): cc.remove_profiles(BUILD, path)
        self.assertEqual(path.read_text(), "// my notes\n{")

    def test_builds_of_counts_installed_builds_only(self):
        ids = [item["id"] for item in INVENTORY]
        self.assertEqual(cc.builds_of("qwen3:8b", BUILDS, ids), [BUILD])
        self.assertEqual(cc.builds_of("qwen3:8b", BUILDS, ["qwen3:8b"]), [])
        self.assertEqual(cc.builds_of("llama3.2:1b", BUILDS, ids), [])
        latest = {"nova-llama3.2-cli": {"base": "llama3.2:latest", "context": 32768}}
        self.assertEqual(cc.builds_of("llama3.2", latest, ["llama3.2:latest", "nova-llama3.2-cli:latest"]), ["nova-llama3.2-cli"])

    def test_a_build_is_stale_once_its_model_has_other_weights(self):
        self.assertEqual(cc.stale_builds(BUILDS, RECORDS), [])
        updated = [{"id": "qwen3:8b", "layers": {W_NEW: 5 * GIB, P: 500}}] + RECORDS[1:]
        self.assertEqual(cc.stale_builds(BUILDS, updated), [(BUILD, "qwen3:8b", 40960)])
        # Unknown files, a missing model or a missing build: never called stale.
        self.assertEqual(cc.stale_builds(BUILDS, [{"id": "qwen3:8b"}] + RECORDS[1:]), [])
        self.assertEqual(cc.stale_builds(BUILDS, RECORDS[1:]), [])
        self.assertEqual(cc.stale_builds(BUILDS, updated[:1]), [])


class RemovalTests(unittest.TestCase):
    def test_the_build_counts_as_going_so_the_whole_model_is_freed(self):
        self.assertEqual(model_removal.sharing("qwen3:8b", RECORDS)["shared_with"], [BUILD])  # 0.15: the trap
        info = model_removal.sharing("qwen3:8b", RECORDS, [BUILD])
        self.assertEqual(info, {"frees_bytes": 5 * GIB + 500 + 520, "shared_bytes": 0, "shared_with": []})

    def test_the_build_in_use_blocks_like_the_model(self):
        store = RemovalStore()
        for given, blockers in ((inputs(selected_model=BUILD + ":latest"), ["SELECTED_MODEL"]),
                                (inputs(resident=[BUILD + ":latest"]), ["LOADED_IN_MEMORY"])):
            with self.subTest(blockers=blockers):
                self.assertEqual(store.propose("qwen3:8b", now=0, **given)["blockers"], blockers)

    def test_the_proposal_names_the_build(self):
        self.assertEqual(RemovalStore().propose("qwen3:8b", now=0, **inputs())["also_deletes"], [BUILD])
        self.assertEqual(RemovalStore().propose("llama3.2:1b", now=0, **inputs(selected_model=None, builds=[]))["also_deletes"], [])

    def decide(self, after, delete=None):
        store, forgotten = RemovalStore(), []
        identifier = store.propose("qwen3:8b", now=0, **inputs())["id"]
        states = [inputs(), after]
        delete = delete or mock.MagicMock()
        result = store.decide(identifier, "approved", recheck=lambda model: states.pop(0), delete=delete,
                              forget=forgotten.append, now=1)
        return result, delete, forgotten

    def test_approving_deletes_the_build_first_then_the_model_and_forgets_both(self):
        (status, body), delete, forgotten = self.decide(inputs(inventory=INVENTORY[2:]))
        self.assertEqual((status, body["status"], body["also_deleted"]), (200, "deleted", [BUILD]))
        self.assertEqual([c.args[0] for c in delete.call_args_list], [BUILD, "qwen3:8b"])
        self.assertEqual(forgotten, [BUILD, "qwen3:8b"])

    def test_a_build_that_wont_delete_leaves_the_model_alone(self):
        delete = mock.MagicMock(side_effect=ProviderOperationFailed("no"))
        (status, body), _, forgotten = self.decide(inputs(), delete)
        self.assertEqual((status, body), (502, {"status": "failed", "error": "delete_failed"}))
        delete.assert_called_once_with(BUILD)
        self.assertEqual(forgotten, [])

    def test_a_build_still_there_afterwards_is_a_failure(self):
        (status, body), _, _ = self.decide(inputs(inventory=INVENTORY[1:]))
        self.assertEqual((status, body), (502, {"status": "failed", "error": "still_installed"}))


class NovadTests(Temp):
    def setUp(self):
        super().setUp()
        self.builds = cc.Builds(self.dir / "builds.json")
        self.builds.record(BUILD, "qwen3:8b", 40960)
        for target, value in ((server, {"CLI_BUILDS": self.builds}),):
            patcher = mock.patch.multiple(target, **value); patcher.start(); self.addCleanup(patcher.stop)
        self.created = []
        patcher = mock.patch.object(server.PROVIDERS["ollama"], "create_build", lambda *a: self.created.append(a))
        patcher.start(); self.addCleanup(patcher.stop)
        self.out = io.StringIO()
        patcher = mock.patch("sys.stdout", new=self.out); patcher.start(); self.addCleanup(patcher.stop)

    def test_removal_inputs_carry_the_models_installed_builds(self):
        with mock.patch.object(server, "model_inventory", return_value=INVENTORY), \
             mock.patch.object(server, "cli_profiles_state", return_value=(200, {"profiles": []})), \
             mock.patch.object(server, "resident_models", return_value=[]):
            self.assertEqual(server.removal_inputs("qwen3:8b")["builds"], [BUILD])
            self.assertEqual(server.removal_inputs("llama3.2:1b")["builds"], [])

    def test_forgetting_a_deleted_build_drops_its_record_profile_and_chat_context(self):
        profiles = self.dir / "cli-profiles.json"
        profiles.write_text(json.dumps({"schema": 1, "profiles": [{"id": "opencode-qwen3-8b", "tool": "opencode", "build": BUILD}]}))
        server.CHAT_CONTEXTS["qwen3:8b"] = 40960
        original = cc.remove_profiles
        with mock.patch.object(server.coding_cli, "remove_profiles", lambda build: original(build, profiles)):
            server.forget_deleted("llama3.2:1b")  # not a build: nothing happens
            self.assertIn(BUILD, self.builds.load())
            server.forget_deleted(BUILD + ":latest")
        self.assertEqual((self.builds.load(), json.loads(profiles.read_text())["profiles"]), ({}, []))
        self.assertNotIn("qwen3:8b", server.CHAT_CONTEXTS)

    def test_a_profile_file_nova_cant_read_is_reported_not_raised(self):
        with mock.patch.object(server.coding_cli, "remove_profiles", side_effect=ValueError("invalid_profile_file")):
            server.forget_deleted(BUILD)
        self.assertIn("coding_cli_forget_failed", self.out.getvalue())

    def test_an_updated_model_gets_its_build_rebuilt_with_the_same_context(self):
        updated = [{"id": "qwen3:8b", "layers": {W_NEW: 5 * GIB, P: 500}}] + RECORDS[1:]
        with mock.patch.object(server, "local_inventory", return_value={"records": RECORDS}):
            self.assertEqual(server.refresh_stale_builds(), [])
        with mock.patch.object(server, "local_inventory", return_value={"records": updated}):
            self.assertEqual(server.refresh_stale_builds(), [BUILD])
        self.assertEqual(self.created, [(BUILD, "qwen3:8b", 40960)])

    def test_only_a_finished_install_or_update_refreshes_builds(self):
        for state, refreshed in (("INSTALLED", 1), ("FAILED", 0), ("RECOVERY_REQUIRED", 0)):
            store = mock.MagicMock(); store.get.return_value = {"state": state}
            with self.subTest(state=state), mock.patch.object(server, "refresh_stale_builds") as refresh:
                server.run_acquisition(store, "acq_x")
                store.run.assert_called_once_with("acq_x")
                self.assertEqual(refresh.call_count, refreshed)

    def get_models(self, inventory):
        provider = server.PROVIDERS[server.inventory_provider_id()]
        with mock.patch.object(server, "model_inventory", return_value=[dict(item) for item in inventory]), \
             mock.patch.object(server, "active_model_state", return_value={}), \
             mock.patch.object(server, "model_sharing", return_value={}), \
             mock.patch.object(provider, "running_models", return_value=[], create=True):
            wire = Wire(b"GET /models HTTP/1.0\r\nHost: localhost\r\n\r\n")
            server.Handler(wire, ("127.0.0.1", 1), None)
        head, body = bytes(wire.response).split(b"\r\n\r\n", 1)
        return {item["id"]: item for item in json.loads(body)["models"]}

    def test_a_build_sits_on_its_models_card_but_is_listed_once_its_model_is_gone(self):
        self.assertEqual(self.get_models(INVENTORY)[BUILD + ":latest"]["tailored_for"], "qwen3:8b")
        orphan = self.get_models(INVENTORY[1:])[BUILD + ":latest"]
        self.assertNotIn("tailored_for", orphan)


class Wire:
    def __init__(self, request):
        self.request, self.response = io.BytesIO(request), bytearray()
    def makefile(self, *args, **kwargs):
        return self.request
    def sendall(self, data):
        self.response.extend(data)


if __name__ == "__main__":
    unittest.main()
