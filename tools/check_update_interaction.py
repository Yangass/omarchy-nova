#!/usr/bin/env python3
"""UPD-01: update check and UPDATE NOVA in the canonical panel.

Run under a private compositor. The first case runs the real check script in a
throwaway clone one commit behind its upstream; the rest use test hooks. Nothing
is updated and no terminal is opened.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import onboarding_preview as preview

ROOT = Path(__file__).resolve().parents[1]
ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x",
       "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "PATH": os.environ["PATH"]}

QML_CHECK = r'''
  TestCase {
    name: "UpdateInteraction"
    when: novaPanel.opened
    function check(value, message) { if (!value) throw new Error(message) }
    function item(name) { var r = findChild(novaPanel, name); check(r !== null, "missing " + name); return r }
    function test_update() {
      var results = [], launched = [], requests = []
      function test(name, fn) {
        try { fn(); results.push({name:name, passed:true}) }
        catch (e) { results.push({name:name, passed:false, error:String(e)}) }
      }
      previewHost.acceptancePreview = false
      try {
      novaPanel.terminalLauncher = function(command) { launched.push(command) }
      test("the real check script finds the newer upstream commit", function() {
        var n = 0; while (novaPanel.gitRevision === "" && n++ < 250) wait(20)
        check(novaPanel.gitRevision !== "", "footer never read the commit")
        novaPanel.checkForUpdates(false)
        n = 0; while (novaPanel.updateStatus !== "available" && n++ < 500) wait(20)
        check(novaPanel.updateStatus === "available", "status: " + novaPanel.updateStatus)
        check(novaPanel.updateRemote === "@NEWER@", "remote: " + novaPanel.updateRemote)
        check(item("updateLink").visible && item("updateLink").text === "UPDATE NOVA", "link: " + item("updateLink").text)
      })
      var answer = "current"
      novaPanel.updateCheckRunner = function(args, done) { requests.push(args.join(" ")); done(answer + " " + "a".repeat(40) + " " + "b".repeat(40) + " 1791300000") }
      test("UPDATE NOVA opens the update script in Omarchy's terminal", function() {
        answer = "available"; novaPanel.updateStatus = ""; novaPanel.checkForUpdates(false); wait(30)
        mouseClick(item("updateLink"))
        check(launched.length === 1 && launched[0] === "omarchy-launch-floating-terminal-with-presentation "
              + novaPanel.shellQuote(novaPanel.shellQuote(novaPanel.pluginDir + "/tools/nova-update.sh")), "command: " + launched[0])
        check(novaPanel.updateTooltipText().indexOf("A newer NOVA (bbbbbbb) is available") === 0, "tooltip: " + novaPanel.updateTooltipText())
      })
      test("no update starts while a chat is running", function() {
        novaPanel.chatBusy = true; wait(30)
        check(!novaPanel.updateAllowed, "update allowed during chat")
        mouseClick(item("updateLink"))
        check(launched.length === 1, "launched during chat")
        novaPanel.chatBusy = false
      })
      test("opening the panel uses the weekly answer; clicking the version checks now", function() {
        answer = "current"; requests = []; novaPanel.updateStatus = ""; novaPanel.updateFeedback = false
        novaPanel.checkForUpdates(false); wait(30)
        check(requests[0] === "" && !item("updateLink").visible, "background check: " + JSON.stringify(requests) + " " + item("updateLink").text)
        mouseClick(item("uiBuildText"))
        wait(30)
        check(requests[1] === "--now", "manual check args: " + JSON.stringify(requests))
        check(item("updateLink").visible && item("updateLink").text === "UP TO DATE", "feedback: " + item("updateLink").text)
        wait(6300)
        check(!item("updateLink").visible, "feedback did not clear")
      })
      test("a failed manual check says so", function() {
        answer = "failed"; novaPanel.checkForUpdates(true); wait(30)
        check(item("updateLink").text === "CHECK FAILED", "feedback: " + item("updateLink").text)
        check(novaPanel.updateTooltipText().indexOf("Couldn't reach NOVA's update source") === 0, "tooltip: " + novaPanel.updateTooltipText())
      })
      test("copy installs show nothing and the version is not clickable", function() {
        answer = "unsupported"; novaPanel.updateStatus = ""; novaPanel.updateFeedback = false
        novaPanel.checkForUpdates(false); wait(30)
        check(!novaPanel.updateSupported && !item("updateLink").visible, "unsupported shows update UI")
        requests = []; mouseClick(item("uiBuildText")); wait(30)
        check(requests.length === 0, "copy install checked on click")
        novaPanel.updateStatus = ""; novaPanel.gitRevision = ""
        novaPanel.checkForUpdates(false); novaPanel.checkForUpdates(true); wait(30)
        check(requests.length === 0, "a non-git install started the check")
      })
      } catch (e) { results.push({name: "harness setup", passed: false, error: String(e)}) }
      console.warn("UPDATE_RESULTS " + JSON.stringify(results))
    }
  }
'''


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True, env=ENV).stdout.strip()


def main():
    fixture = preview.scenarios()["installed-no-selection"]
    with tempfile.TemporaryDirectory(prefix="nova-update-", dir="/tmp") as raw:
        root = Path(raw)
        upstream, plugin, state = root / "upstream", root / "plugin", root / "state"
        upstream.mkdir()
        git(upstream, "init", "-q", "-b", "master")
        (upstream / "file").write_text("one")
        git(upstream, "add", "file"); git(upstream, "commit", "-q", "-m", "one")
        subprocess.run(["git", "clone", "-q", str(upstream), str(plugin)], check=True, env=ENV)
        (upstream / "file").write_text("two")
        git(upstream, "commit", "-q", "-am", "two")
        newer = git(upstream, "rev-parse", "HEAD")
        (plugin / "tools").mkdir()
        shutil.copy(ROOT / "tools/nova-update-check.sh", plugin / "tools")
        shell = preview.prepare_config(plugin, fixture)
        text = shell.read_text().replace("import QtQuick\n", "import QtQuick\nimport QtTest\n", 1)
        shell.write_text(text.rsplit("\n}", 1)[0] + QML_CHECK.replace("@NEWER@", newer) + "\n}\n")
        result = subprocess.run(["quickshell", "--path", str(plugin)], capture_output=True, text=True, timeout=120,
                                env={**os.environ, **{k: v for k, v in ENV.items() if k != "PATH"}, "XDG_STATE_HOME": str(state)})
        output = result.stdout + result.stderr
    line = next((row.split("UPDATE_RESULTS ", 1)[1] for row in output.splitlines() if "UPDATE_RESULTS " in row), None)
    if line is None:
        print(output)
        raise SystemExit("Update interaction did not complete")
    rows = json.loads(line)
    for row in rows:
        print(("PASS " if row["passed"] else "FAIL ") + row["name"] + ((": " + row["error"]) if not row["passed"] else ""))
    errors = [r for r in output.splitlines() if any(w in r for w in ("ReferenceError", "TypeError", "Binding loop"))]
    if errors or any(not row["passed"] for row in rows):
        print(output)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
