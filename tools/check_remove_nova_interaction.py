#!/usr/bin/env python3
"""REMOVE-01: removing NOVA from the panel (MACHINE's button; the bar icon's menu opens the
same card). Three choices with sizes, the models that go (marked when NOVA didn't install
them), are-you-sure, then one fixed terminal command. Run under a private compositor; the
command is captured by a test hook and nothing is executed.

NOVA_REMOVE_CAPTURE=<dir> saves the card's states as pictures.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile

import onboarding_preview as preview

GIB = 1024 ** 3
MODELS = [{"id": "qwen3:8b", "provider": "ollama", "available": True, "metadata": {"size_bytes": int(4.9 * GIB)}},
          {"id": "llama3.2:1b", "provider": "ollama", "available": True, "metadata": {"size_bytes": int(1.2 * GIB)}}]

QML_CHECK = r'''
  TestCase {
    name: "RemoveNova"
    when: novaPanel.opened
    function check(value, message) { if (!value) throw new Error(message) }
    function item(name) { var r = findChild(novaPanel, name); check(r !== null, "missing " + name); return r }
    function choice(id) {
      var found = null, walk = function(o) { if (!o || found) return; if (o.objectName === "removeNovaChoice" && o.choiceId === id) found = o; for (var i = 0; i < o.children.length; i++) walk(o.children[i]) }
      walk(item("removeNovaCard")); check(found !== null, "missing choice " + id); return found
    }
    function shot(name) {
      if ("@CAPTURE@" === "") return
      wait(200)
      var done = false
      findChild(novaPanel, "panelKeyCatcher").parent.parent.grabToImage(function(image) { image.saveToFile("@CAPTURE@/" + name + ".png"); done = true })
      var n = 0; while (!done && n++ < 150) wait(20)
    }
    function test_remove() {
      var results = [], launched = []
      function test(name, fn) {
        try { fn(); results.push({name:name, passed:true}) }
        catch (e) { results.push({name:name, passed:false, error:String(e)}) }
      }
      previewHost.acceptancePreview = false
      try {
      novaPanel.terminalLauncher = function(command) { launched.push(command) }
      novaPanel.removeScriptPresent = true
      novaPanel.discoveredModels = @MODELS@
      novaPanel.modelStorageBytes = 6.1 * 1024 * 1024 * 1024
      // qwen3:8b came through NOVA's catalog download; llama3.2:1b did not.
      novaPanel.catalogModels = [{catalog_id: "qwen3-8b", display_name: "Qwen 3 8B", source: {provider: "ollama", upstream_id: "qwen3:8b"},
                                  installed_match: {match_status: "exact", installed: true}}]
      novaPanel.acquisitionByCatalog = {"qwen3-8b": {state: "INSTALLED"}}
      novaPanel.panelView = "machine"; wait(80)
      test("MACHINE ends with REMOVE NOVA…, and it opens the card in CHAT", function() {
        check(item("removeNovaButton").visible && item("removeNovaButton").text === "REMOVE NOVA…", "no button in MACHINE")
        item("removeNovaButton").clicked(); wait(80)
        check(novaPanel.removeNovaOpen && novaPanel.shownView === "chat" && item("removeNovaCard").visible, "card not shown in CHAT")
      })
      test("three choices, Everything first and chosen, with what each frees", function() {
        check(novaPanel.removeNovaChoice === "everything", "default: " + novaPanel.removeNovaChoice)
        check(choice("everything").chosen && !choice("nova").chosen, "Everything not chosen")
        check(novaPanel.removeNovaFrees("everything") === "6.1 GiB" && novaPanel.removeNovaFrees("nova") === "< 0.1 GiB", "frees: " + novaPanel.removeNovaFrees("everything"))
        check(item("removeNovaNote").text.indexOf("password is asked once") >= 0, "password note: " + item("removeNovaNote").text)
        shot("1-what-goes")
        novaPanel.panelView = "machine"; novaPanel.closeRemoveNova(); wait(120); shot("0-machine"); novaPanel.openRemoveNova(); wait(80)
      })
      test("every model that goes is listed; one NOVA didn't install is marked", function() {
        check(item("removeNovaModels").visible, "models not listed")
        check(!novaPanel.modelFromNova(@MODELS@[1]) && novaPanel.modelFromNova(@MODELS@[0]), "who installed what")
        var marks = [], walk = function(o) { if (!o) return; if (o.objectName === "removeNovaNotFromNova" && o.visible) marks.push(o.text); for (var i = 0; i < o.children.length; i++) walk(o.children[i]) }
        walk(item("removeNovaModels"))
        check(marks.length === 1 && marks[0] === "NOT FROM NOVA", "marks: " + JSON.stringify(marks))
        novaPanel.removeNovaChoice = "nova"; wait(40)
        check(!item("removeNovaModels").visible && item("removeNovaNote").text.indexOf("No password needed") === 0, "NOVA only still lists models")
        novaPanel.removeNovaChoice = "everything"; wait(40)
      })
      test("REMOVE… asks are-you-sure first; KEEP NOVA goes back; nothing runs", function() {
        item("removeNovaGo").clicked(); wait(40)
        check(novaPanel.removeNovaSure && item("removeNovaGo").text === "REMOVE EVERYTHING", "no are-you-sure: " + item("removeNovaGo").text)
        check(item("removeNovaSureText").text === "Are you sure? This removes NOVA, 2 models and Ollama for good. Getting the models back means downloading 6.1 GiB again. A terminal opens and shows each step.",
              "sure: " + item("removeNovaSureText").text)
        shot("2-are-you-sure")
        item("removeNovaCancel").clicked(); wait(40)
        check(!novaPanel.removeNovaSure && novaPanel.removeNovaOpen && launched.length === 0, "KEEP NOVA did not go back")
      })
      test("a running coding CLI or download blocks it, with the reason", function() {
        novaPanel.cliState = {configured: true, profiles: [{id: "opencode-qwen3-8b", kind: "opencode", model: "qwen3:8b", provider: "ollama", running: true}]}; wait(40)
        check(item("removeNovaBlocker").visible && item("removeNovaBlocker").text === "A coding CLI is running: close it first." && !item("removeNovaGo").enabled, "CLI not blocking")
        novaPanel.cliState = null; novaPanel.trackedAcquisitionId = "acq_x"; wait(40)
        check(item("removeNovaBlocker").text === "A model is downloading: let it finish first." && !item("removeNovaGo").enabled, "download not blocking")
        novaPanel.trackedAcquisitionId = ""; wait(40)
        check(!item("removeNovaBlocker").visible && item("removeNovaGo").enabled, "still blocked")
      })
      test("confirmed, it opens one terminal with the plugin's script and the choice", function() {
        item("removeNovaGo").clicked(); wait(40); item("removeNovaGo").clicked(); wait(40)
        var script = novaPanel.shellQuote(novaPanel.pluginDir + "/tools/nova-uninstall.sh")
        check(launched.length === 1 && launched[0] === "omarchy-launch-floating-terminal-with-presentation " + novaPanel.shellQuote(script + " --everything"), "command: " + launched[0])
        check(!novaPanel.removeNovaOpen, "card stayed open")
        novaPanel.openRemoveNova(); novaPanel.removeNovaChoice = "models"; item("removeNovaGo").clicked(); item("removeNovaGo").clicked(); wait(40)
        check(launched[1] === "omarchy-launch-floating-terminal-with-presentation " + novaPanel.shellQuote(script + " --models"), "models command: " + launched[1])
      })
      test("Escape steps back: from are-you-sure to the choices, then closes", function() {
        novaPanel.openRemoveNova(); wait(60); item("removeNovaGo").clicked(); wait(40)
        keyClick(Qt.Key_Escape); wait(40)
        check(novaPanel.removeNovaOpen && !novaPanel.removeNovaSure, "Escape did not step back")
        keyClick(Qt.Key_Escape); wait(40)
        check(!novaPanel.removeNovaOpen, "Escape did not close")
      })
      test("an install without the script says where to look instead of failing", function() {
        novaPanel.removeScriptPresent = false; novaPanel.openRemoveNova(); wait(40)
        check(!item("removeNovaGo").enabled && item("removeNovaBlocker").text.indexOf("README") > 0, "no script: " + item("removeNovaBlocker").text)
        novaPanel.closeRemoveNova()
      })
      } catch (e) { results.push({name: "harness setup", passed: false, error: String(e)}) }
      console.warn("REMOVE_RESULTS " + JSON.stringify(results))
    }
  }
'''


def main():
    fixture = preview.scenarios()["installed-no-selection"]
    capture = os.environ.get("NOVA_REMOVE_CAPTURE", "")
    with tempfile.TemporaryDirectory(prefix="nova-remove-", dir="/tmp") as raw:
        shell = preview.prepare_config(Path(raw), fixture)
        text = shell.read_text().replace("import QtQuick\n", "import QtQuick\nimport QtTest\n", 1)
        check = QML_CHECK.replace("@CAPTURE@", capture).replace("@MODELS@", json.dumps(MODELS))
        shell.write_text(text.rsplit("\n}", 1)[0] + check + "\n}\n")
        result = subprocess.run(["quickshell", "--path", raw], capture_output=True, text=True, timeout=90)
        output = result.stdout + result.stderr
    line = next((row.split("REMOVE_RESULTS ", 1)[1] for row in output.splitlines() if "REMOVE_RESULTS " in row), None)
    if line is None:
        print(output)
        raise SystemExit("Remove NOVA interaction did not complete")
    rows = json.loads(line)
    for row in rows:
        print(("PASS " if row["passed"] else "FAIL ") + row["name"] + ((": " + row["error"]) if not row["passed"] else ""))
    errors = [r for r in output.splitlines() if any(w in r for w in ("ReferenceError", "TypeError", "Binding loop"))]
    if errors or any(not row["passed"] for row in rows):
        print(output)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
