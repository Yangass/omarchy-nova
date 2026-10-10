#!/usr/bin/env python3
"""UX Phase 1 (2026-10-08, docs/UX_REVIEW_2026-10-08.md).

Run under a private compositor. One health
signal instead of five, plain words, one primary action per card, and an install
approval that leads with what, how big and whether it fits.
"""
import json
from pathlib import Path
import subprocess
import tempfile

import onboarding_preview as preview


QML_CHECK = r'''
  TestCase {
    name: "UxPhase1"
    when: novaPanel.opened
    function check(value, message) { if (!value) throw new Error(message) }
    function item(name) { var r = findChild(novaPanel, name); check(r !== null, "missing " + name); return r }
    function test_phase1() {
      var results = []
      function test(name, fn) {
        try { fn(); results.push({name:name, passed:true}) }
        catch (e) { results.push({name:name, passed:false, error:String(e)}) }
      }
      novaPanel.catalogModels = [{catalog_id:"qwen3-8b-q4-k-m", display_name:"Qwen 3 8B (Q4_K_M)", source:{upstream_id:"qwen3:8b"}}]
      novaPanel.onboardingState = "READY"; novaPanel.onboardingCodes = []
      novaPanel.onboardingSnapshotFresh = true; novaPanel.onboardingExplicitSelection = true
      novaPanel.onboardingSelectedProvider = "ollama"; novaPanel.onboardingSelectedModel = "qwen3:8b"
      previewHost.activeModelAvailable = true
      wait(200)
      test("an empty chat says nothing technical", function() {
        var lines = item("chatLines"), found = false
        for (var i = 0; i < lines.children.length; i++) if (lines.children[i].text === "SESSION CHANNEL READY" && lines.children[i].visible) found = true
        check(!found, "SESSION CHANNEL READY still shown")
      })
      test("the model bar shows the model's name, the tooltip the exact identity", function() {
        check(item("brainLabel").text === "MODEL · Qwen 3 8B", "bar: " + item("brainLabel").text)
        check(item("brainIdentityTooltip").text === "Selected model: ollama / qwen3:8b", "tooltip: " + item("brainIdentityTooltip").text)
      })
      test("a model outside the catalog keeps its own name without :latest", function() {
        check(novaPanel.runtimeDisplayName("nova-code-qwen38-96k:latest") === "nova-code-qwen38-96k", "alias: " + novaPanel.runtimeDisplayName("nova-code-qwen38-96k:latest"))
        check(novaPanel.runtimeDisplayName("qwen3:8b") === "Qwen 3 8B (Q4_K_M)", "catalog name lost")
      })
      test("one health signal: no NOVA LINK line or LINK STABLE when all is well", function() {
        check(item("linkStatus").text === "", "link line: " + item("linkStatus").text)
        check(findChild(novaPanel, "footerLinkHint") === null, "footer still repeats the link state")
        previewHost.online = false; wait(80)
        check(item("linkStatus").text === "NOVA is offline", "offline line: " + item("linkStatus").text)
        previewHost.online = true; wait(80)
      })
      test("one primary action per card", function() {
        check(item("onboardingActionButton").selected === true, "setup action not primary")
        check(item("refreshInstalledModelsButton").selected !== true, "refresh is primary")
      })
      test("the install approval leads with size, fit and free space", function() {
        check(novaPanel.approvalFitShort("GOOD") === "runs well" && novaPanel.approvalFitShort("EXCELLENT") === "runs comfortably"
              && novaPanel.approvalFitShort("TIGHT") === "fits with little room" && novaPanel.approvalFitShort(undefined) === "fit not estimated", "fit words")
        check(!novaPanel.approvalDetailsOpen, "details open by default")
      })
      console.warn("PHASE1_RESULTS " + JSON.stringify(results))
    }
  }
'''


def main():
    fixture = preview.scenarios()["installed-no-selection"]
    with tempfile.TemporaryDirectory(prefix="nova-phase1-", dir="/tmp") as raw:
        shell = preview.prepare_config(Path(raw), fixture)
        text = shell.read_text().replace("import QtQuick\n", "import QtQuick\nimport QtTest\n", 1)
        check = QML_CHECK
        shell.write_text(text.rsplit("\n}", 1)[0] + check + "\n}\n")
        result = subprocess.run(["quickshell", "--path", raw], capture_output=True, text=True, timeout=90)
        output = result.stdout + result.stderr
    line = next((row.split("PHASE1_RESULTS ", 1)[1] for row in output.splitlines() if "PHASE1_RESULTS " in row), None)
    if line is None:
        print(output)
        raise SystemExit("Phase 1 check did not complete")
    rows = json.loads(line)
    for row in rows:
        print(("PASS " if row["passed"] else "FAIL ") + row["name"] + ((": " + row["error"]) if not row["passed"] else ""))
    errors = [r for r in output.splitlines() if any(w in r for w in ("ReferenceError", "TypeError", "Binding loop"))]
    if errors or any(not row["passed"] for row in rows):
        print("\n".join(errors) or output[-3000:])
        raise SystemExit(1)


if __name__ == "__main__":
    main()
