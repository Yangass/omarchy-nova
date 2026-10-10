"""UPD-01: tools/nova-update.sh with a fake `omarchy` command and real git repositories."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x",
       "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
# Mimics `omarchy plugin update <id> --yes` (fetch + fast-forward) and logs every call.
FAKE_OMARCHY = '''#!/bin/bash
echo "omarchy $*" >> "$NOVA_TEST_LOG"
if [ "$1 $2" = "plugin update" ]; then
  dir="$NOVA_TEST_PLUGINS/$3"
  git -C "$dir" fetch -q origin HEAD && git -C "$dir" merge -q --ff-only FETCH_HEAD || exit 1
fi
'''
FAKE_SETUP = '#!/bin/bash\necho "setup $(git -C "$(dirname "$0")/.." log -1 --format=%s)" >> "$NOVA_TEST_LOG"\n'


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                          env={**ENV, "PATH": os.environ["PATH"]}).stdout.strip()


class UpdateScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR")))
        self.upstream = self.tmp / "upstream"
        (self.upstream / "tools").mkdir(parents=True)
        for name in ("nova-update.sh", "nova-update-check.sh"):
            shutil.copy(ROOT / "tools" / name, self.upstream / "tools")
        (self.upstream / "tools/nova-setup.sh").write_text(FAKE_SETUP)
        (self.upstream / "tools/nova-setup.sh").chmod(0o755)
        git(self.upstream, "init", "-q", "-b", "master")
        git(self.upstream, "add", "-A"); git(self.upstream, "commit", "-q", "-m", "one")
        self.plugins = self.tmp / "plugins"
        self.plugin = self.plugins / "io.github.yangass.nova"
        subprocess.run(["git", "clone", "-q", str(self.upstream), str(self.plugin)], check=True,
                       env={**ENV, "PATH": os.environ["PATH"]})
        # The new version rewrites the update script itself (longer, shifted lines).
        script = self.upstream / "tools/nova-update.sh"
        script.write_text("#!/usr/bin/env bash\n" + "# padding line that shifts every offset\n" * 40
                          + script.read_text().split("\n", 1)[1])
        git(self.upstream, "commit", "-q", "-am", "two")
        self.bin = self.tmp / "bin"; self.bin.mkdir()
        (self.bin / "omarchy").write_text(FAKE_OMARCHY); (self.bin / "omarchy").chmod(0o755)
        (self.bin / "sleep").write_text("#!/bin/sh\n"); (self.bin / "sleep").chmod(0o755)
        self.log = self.tmp / "log"

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_update(self, stdin):
        env = {**ENV, "PATH": f"{self.bin}:{os.environ['PATH']}", "NOVA_TEST_LOG": str(self.log),
               "NOVA_TEST_PLUGINS": str(self.plugins), "XDG_STATE_HOME": str(self.tmp / "state")}
        return subprocess.run([str(self.plugin / "tools/nova-update.sh")], input=stdin, capture_output=True,
                              text=True, env=env, timeout=60)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_enter_updates_sets_up_and_restarts_the_shell_even_though_the_script_changed(self):
        result = self.run_update("\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls(), ["omarchy plugin update io.github.yangass.nova --yes", "setup two", "omarchy restart shell"])
        self.assertEqual(git(self.plugin, "log", "-1", "--format=%s"), "two")
        self.assertIn("Done.", result.stdout)
        cached = (self.tmp / "state/nova/update-check").read_text().split()[0]
        self.assertEqual(cached, "current")

    def test_closing_without_enter_changes_nothing(self):
        result = self.run_update("")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])
        self.assertEqual(git(self.plugin, "log", "-1", "--format=%s"), "one")

    def test_failed_plugin_update_stops_before_setup_and_restart(self):
        git(self.plugin, "remote", "set-url", "origin", str(self.tmp / "missing"))
        result = self.run_update("\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), ["omarchy plugin update io.github.yangass.nova --yes"])
        self.assertIn("NOVA was not updated", result.stdout)


if __name__ == "__main__":
    unittest.main()
