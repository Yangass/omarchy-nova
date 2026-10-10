"""UPD-01: tools/nova-update-check.sh against real throwaway git repositories."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DAY = 86400
ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x",
       "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "PATH": os.environ["PATH"]}


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True, env=ENV).stdout.strip()


class UpdateCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR")))
        self.upstream = self.tmp / "upstream"
        self.upstream.mkdir()
        git(self.upstream, "init", "-q", "-b", "master")
        self.commit(self.upstream, "one")
        self.plugin = self.tmp / "plugins" / "io.github.yangass.nova"
        subprocess.run(["git", "clone", "-q", str(self.upstream), str(self.plugin)], check=True, env=ENV)
        (self.plugin / "tools").mkdir()
        shutil.copy(ROOT / "tools/nova-update-check.sh", self.plugin / "tools")
        self.state = self.tmp / "state"

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def commit(self, repo, message):
        (repo / "file").write_text(message)
        git(repo, "add", "file")
        git(repo, "commit", "-q", "-m", message)
        return git(repo, "rev-parse", "HEAD")

    def check(self, *args, now=1_000_000, plugin=None):
        plugin = plugin or self.plugin
        result = subprocess.run([str(plugin / "tools/nova-update-check.sh"), *args], capture_output=True, text=True,
                                env={**ENV, "XDG_STATE_HOME": str(self.state), "NOVA_UPDATE_NOW": str(now)}, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.split()

    def test_same_commit_is_current(self):
        status, local, remote, checked = self.check()
        self.assertEqual((status, local, remote, checked), ("current", git(self.plugin, "rev-parse", "HEAD"), local, "1000000"))

    def test_newer_upstream_commit_is_available(self):
        newer = self.commit(self.upstream, "two")
        self.assertEqual(self.check()[0:3:2], ["available", newer])

    def test_checkout_ahead_of_upstream_is_current(self):
        self.commit(self.plugin, "local")
        self.assertEqual(self.check()[0], "current")

    def test_unreachable_remote_fails_without_changing_the_checkout(self):
        head = git(self.plugin, "rev-parse", "HEAD")
        git(self.plugin, "remote", "set-url", "origin", str(self.tmp / "missing"))
        self.assertEqual(self.check()[0:3:2], ["failed", "-"])
        self.assertEqual(git(self.plugin, "rev-parse", "HEAD"), head)
        self.assertEqual(git(self.plugin, "status", "--porcelain"), "?? tools/")

    def test_copy_install_is_unsupported(self):
        copy = self.tmp / "copy"
        (copy / "tools").mkdir(parents=True)
        shutil.copy(ROOT / "tools/nova-update-check.sh", copy / "tools")
        self.assertEqual(self.check(plugin=copy)[0], "unsupported")

    def test_answer_is_kept_for_seven_days_unless_forced(self):
        self.assertEqual(self.check()[0], "current")
        newer = self.commit(self.upstream, "two")
        self.assertEqual(self.check(now=1_000_000 + 7 * DAY - 1)[0], "current")  # cached, no network
        self.assertEqual(self.check("--now", now=1_000_000 + DAY)[0:3:2], ["available", newer])
        self.commit(self.upstream, "three")
        self.assertEqual(self.check(now=1_000_000 + 2 * DAY)[2], newer)  # cached again
        self.assertEqual(self.check(now=1_000_000 + 8 * DAY)[0], "available")
        self.assertNotEqual(self.check(now=1_000_000 + 8 * DAY)[2], newer)

    def test_failure_is_retried_after_a_day(self):
        url = git(self.plugin, "remote", "get-url", "origin")
        git(self.plugin, "remote", "set-url", "origin", str(self.tmp / "missing"))
        self.assertEqual(self.check()[0], "failed")
        git(self.plugin, "remote", "set-url", "origin", url)
        self.assertEqual(self.check(now=1_000_000 + DAY - 1)[0], "failed")
        self.assertEqual(self.check(now=1_000_000 + DAY)[0], "current")

    def test_a_new_local_commit_invalidates_the_cached_answer(self):
        self.commit(self.upstream, "two")
        self.assertEqual(self.check()[0], "available")
        git(self.plugin, "pull", "-q", "--ff-only")
        self.assertEqual(self.check(now=1_000_000 + 1)[0], "current")


if __name__ == "__main__":
    unittest.main()
