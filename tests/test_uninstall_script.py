"""REMOVE-01: tools/nova-uninstall.sh with fake ollama, pacman, sudo and omarchy commands that
only log their arguments. HOME is a temporary folder; nothing outside it is touched."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LOGGER = '#!/bin/bash\necho "$(basename "$0") $*" >> "$NOVA_TEST_LOG"\n'
FAKE_OLLAMA = '''#!/bin/bash
echo "ollama $*" >> "$NOVA_TEST_LOG"
case "$1" in
  list) printf 'NAME ID SIZE MODIFIED\\nqwen3:8b a 5 GB now\\nnova-qwen3-8b-cli:latest b 5 GB now\\n' ;;
  ps) printf 'NAME ID SIZE PROCESSOR UNTIL\\nqwen3:8b a 6 GB 100%% GPU 4m\\n' ;;
esac
'''
FAKE_PACMAN = '''#!/bin/bash
echo "pacman $*" >> "$NOVA_TEST_LOG"
[ "$1" = -Qq ] && { [ "$2" = ollama ] || [ "$2" = ollama-cuda ]; }
'''
FAKE_INSTALLER = 'import os, sys\nopen(os.environ["NOVA_TEST_LOG"], "a").write("nova_install " + " ".join(sys.argv[1:]) + "\\n")\n'


class UninstallScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR")))
        self.home = self.tmp / "home"
        self.plugin = self.home / ".config/omarchy/plugins/io.github.yangass.nova"
        (self.plugin / "tools").mkdir(parents=True)
        shutil.copy(ROOT / "tools/nova-uninstall.sh", self.plugin / "tools")
        (self.plugin / "tools/nova_install.py").write_text(FAKE_INSTALLER)
        for folder in (".config/nova", ".local/state/nova/installer", ".local/share/nova/runtime", ".local/state/nova-dev"):
            (self.home / folder).mkdir(parents=True, exist_ok=True)
        (self.home / ".config/nova/cli-profiles.json").write_text("{}")
        (self.home / ".local/state/nova/installer/ownership.json").write_text("{}")
        self.bin = self.tmp / "bin"; self.bin.mkdir()
        for name, text in (("ollama", FAKE_OLLAMA), ("pacman", FAKE_PACMAN), ("sudo", LOGGER), ("omarchy", LOGGER),
                           ("omarchy-pkg-drop", LOGGER), ("sleep", "#!/bin/sh\n")):
            (self.bin / name).write_text(text); (self.bin / name).chmod(0o755)
        self.log = self.tmp / "log"; self.log.write_text("")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_script(self, *args, stdin=""):
        env = {"PATH": f"{self.bin}:{os.environ['PATH']}", "HOME": str(self.home), "NOVA_TEST_LOG": str(self.log)}
        result = subprocess.run(["bash", str(self.plugin / "tools/nova-uninstall.sh"), *args], input=stdin,
                                capture_output=True, text=True, env=env, timeout=30)
        return result, [line for line in self.log.read_text().splitlines() if not line.startswith(("pacman", "ollama list", "ollama ps"))]

    def nova_files_left(self):
        return [folder for folder in (".config/nova", ".local/state/nova", ".local/share/nova") if (self.home / folder).exists()]

    def test_a_dry_run_says_every_step_and_changes_nothing(self):
        result, calls = self.run_script("--everything", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls, [])
        self.assertEqual(self.nova_files_left(), [".config/nova", ".local/state/nova", ".local/share/nova"])
        for step in ("ollama rm qwen3:8b", "ollama rm nova-qwen3-8b-cli:latest", "nova_install.py remove", "sudo systemctl disable --now ollama.service",
                     "omarchy-pkg-drop ollama ollama-cuda", "sudo rm -rf -- /var/lib/ollama", "omarchy plugin remove io.github.yangass.nova --yes"):
            self.assertIn("[dry run] " + step if not step.startswith("nova_install") else step, result.stdout)

    def test_nova_only_keeps_ollama_and_models_and_needs_no_password(self):
        result, calls = self.run_script("--nova")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls, ["nova_install remove --idle-state idle --confirm-idle", "omarchy plugin remove io.github.yangass.nova --yes"])
        self.assertEqual(self.nova_files_left(), [])
        self.assertTrue((self.home / ".local/state/nova-dev").exists(), "a folder that isn't NOVA's went")
        self.assertIn("Ollama and your models stay", result.stdout)

    def test_models_go_through_ollama_first_then_nova_then_the_plugin_last(self):
        result, calls = self.run_script("--models")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls, ["ollama stop qwen3:8b", "ollama rm qwen3:8b", "ollama rm nova-qwen3-8b-cli:latest",
                                 "nova_install remove --idle-state idle --confirm-idle", "omarchy plugin remove io.github.yangass.nova --yes"])
        self.assertNotIn("sudo", "\n".join(calls))

    def test_everything_removes_ollama_with_one_password_and_its_folder(self):
        result, calls = self.run_script("--everything")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls[-5:], ["sudo systemctl disable --now ollama.service", "omarchy-pkg-drop ollama ollama-cuda",
                                      "sudo rm -rf -- /var/lib/ollama", "sudo ls -A /var/lib/ollama",  # checks it's gone
                                      "omarchy plugin remove io.github.yangass.nova --yes"])
        self.assertIn("Nothing of it is left on this computer.", result.stdout)

    def test_run_by_hand_it_asks_first_and_does_nothing_when_cancelled(self):
        result, calls = self.run_script(stdin="")  # the window closed: no Enter
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])
        self.assertEqual(len(self.nova_files_left()), 3)

    def test_a_helper_that_won_t_go_stops_everything_after_it(self):
        (self.plugin / "tools/nova_install.py").write_text(FAKE_INSTALLER + "sys.exit(1)\n")
        result, calls = self.run_script("--everything")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("sudo", "\n".join(calls))
        self.assertEqual(len(self.nova_files_left()), 3)

    def test_unknown_options_are_refused(self):
        result, calls = self.run_script("--all-the-things")
        self.assertEqual((result.returncode, calls), (2, []))


if __name__ == "__main__":
    unittest.main()
