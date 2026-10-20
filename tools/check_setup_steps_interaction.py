#!/usr/bin/env python3
"""Beginner setup steps (SET UP NOVA / INSTALL OLLAMA / START OLLAMA) in the canonical panel.

Run under a private compositor. Commands are captured by a test hook; nothing is executed.
"""
import json
from pathlib import Path
import subprocess
import tempfile

import onboarding_preview as preview

QML_CHECK = r'''
  TestCase {
    name: "SetupSteps"
    when: novaPanel.opened
    function check(value, message) { if (!value) throw new Error(message) }
    function item(name) { var r = findChild(novaPanel, name); check(r !== null, "missing " + name); return r }
    function test_steps() {
      var results = [], launched = []
      function test(name, fn) {
        try { fn(); results.push({name:name, passed:true}) }
        catch (e) { results.push({name:name, passed:false, error:String(e)}) }
      }
      previewHost.acceptancePreview = false
      novaPanel.panelView = "chat"  // UX Phase 2: setup lives in CHAT, where messages will be
      try {
      novaPanel.terminalLauncher = function(command) { launched.push(command) }
      function state(codes) { novaPanel.onboardingState = "NEEDS_USER_SETUP"; novaPanel.onboardingCodes = codes; wait(60) }
      test("missing backend in a git checkout offers SET UP NOVA with the plugin's own script", function() {
        previewHost.online = false; novaPanel.novaSetupScriptPresent = true; state(["NOVAD_UNAVAILABLE"])
        check(item("setupStepBox").visible && item("setupStepButton").text === "SET UP NOVA", "button: " + item("setupStepButton").text)
        check(item("setupStepText").text.indexOf("no administrator password") >= 0, "text: " + item("setupStepText").text)
        item("setupStepButton").clicked(); wait(30)
        check(launched.length === 1 && launched[0] === "omarchy-launch-floating-terminal-with-presentation " + novaPanel.shellQuote(novaPanel.shellQuote(novaPanel.pluginDir + "/tools/nova-setup.sh")), "command: " + launched[0])
        check(item("setupStepFollow").visible, "follow-up hint missing")
        check(item("onlineStatusText").text === "SETUP NEEDED" && !Qt.colorEqual(item("onlineStatusText").color, Commons.Color.urgent),
              "header still alarms during first setup: " + item("onlineStatusText").text)
      })
      test("copy install with stopped services offers START NOVA", function() {
        novaPanel.novaSetupScriptPresent = false; novaPanel.setupLaunched = ""; state(["NOVAD_UNAVAILABLE"])
        check(item("setupStepButton").text === "START NOVA", "button: " + item("setupStepButton").text)
        item("setupStepButton").clicked(); wait(30)
        check(launched[1].indexOf("systemctl --user restart novad.service") > 0, "command: " + launched[1])
      })
      test("missing Ollama offers INSTALL OLLAMA through Omarchy's package tool, CUDA on NVIDIA", function() {
        previewHost.online = true; novaPanel.ollamaInstalled = false; novaPanel.setupLaunched = ""; previewHost.gpuAvailable = true; state(["PROVIDER_UNAVAILABLE"])
        check(item("setupStepButton").text === "INSTALL OLLAMA", "button: " + item("setupStepButton").text)
        item("setupStepButton").clicked(); wait(30)
        check(launched[2] === "omarchy-launch-floating-terminal-with-presentation '{ pacman -Si ollama-cuda >/dev/null 2>&1 || omarchy update; } && omarchy pkg add ollama-cuda && sudo systemctl --quiet enable --now ollama || { echo; echo '\\''Ollama was not installed. The message above says why; then try INSTALL OLLAMA again in the NOVA panel.'\\''; }'", "command: " + launched[2])
        previewHost.gpuAvailable = false; wait(30)
        check(item("setupStepText").text.indexOf("installs ollama with") >= 0, "non-NVIDIA package: " + item("setupStepText").text)
      })
      test("installed but stopped Ollama offers START OLLAMA", function() {
        novaPanel.ollamaInstalled = true; state(["PROVIDER_UNAVAILABLE"])
        check(item("setupStepButton").text === "START OLLAMA", "button: " + item("setupStepButton").text)
        check(item("onlineStatusText").text !== "SETUP NEEDED", "Ollama setup marked as NOVA setup")
        check(!item("onboardingMessageText").visible, "generic setup line repeats the step text")
      })
      test("too-old Ollama offers UPDATE OLLAMA through Omarchy's update", function() {
        novaPanel.setupLaunched = ""; novaPanel.providerVersion = "0.6.4"; novaPanel.providerMinimumVersion = "0.6.5"
        state(["PROVIDER_OUTDATED"])
        check(item("setupStepButton").text === "UPDATE OLLAMA", "button: " + item("setupStepButton").text)
        check(item("setupStepText").text.indexOf("This computer has Ollama 0.6.4; NOVA needs 0.6.5 or newer. UPDATE OLLAMA opens") === 0,
              "text: " + item("setupStepText").text)
        check(!item("onboardingMessageText").visible, "generic line repeats the update step")
        item("setupStepButton").clicked(); wait(30)
        var last = launched[launched.length - 1]
        check(last.indexOf("omarchy update && sudo systemctl --quiet restart ollama") > 0, "command: " + last)
      })
      test("ready or other states show no setup step", function() {
        novaPanel.onboardingState = "NEEDS_MODEL"; novaPanel.onboardingCodes = []; wait(60)
        check(!item("setupStepBox").visible, "setup step shown outside setup states")
        check(item("onboardingMessageText").visible, "onboarding message hidden outside setup steps")
      })
      } catch (e) { results.push({name: "harness setup", passed: false, error: String(e)}) }
      console.warn("SETUP_RESULTS " + JSON.stringify(results))
    }
  }
'''


def main():
    fixture = preview.scenarios()["installed-no-selection"]
    with tempfile.TemporaryDirectory(prefix="nova-setup-steps-", dir="/tmp") as raw:
        shell = preview.prepare_config(Path(raw), fixture)
        text = shell.read_text().replace("import QtQuick\n", "import QtQuick\nimport QtTest\n", 1)
        shell.write_text(text.rsplit("\n}", 1)[0] + QML_CHECK + "\n}\n")
        result = subprocess.run(["quickshell", "--path", raw], capture_output=True, text=True, timeout=90)
        output = result.stdout + result.stderr
    line = next((row.split("SETUP_RESULTS ", 1)[1] for row in output.splitlines() if "SETUP_RESULTS " in row), None)
    if line is None:
        print(output)
        raise SystemExit("Setup-step interaction did not complete")
    rows = json.loads(line)
    for row in rows:
        print(("PASS " if row["passed"] else "FAIL ") + row["name"] + ((": " + row["error"]) if not row["passed"] else ""))
    errors = [r for r in output.splitlines() if any(w in r for w in ("ReferenceError", "TypeError", "Binding loop"))]
    if errors or any(not row["passed"] for row in rows):
        print(output)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
