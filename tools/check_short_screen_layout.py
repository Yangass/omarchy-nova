#!/usr/bin/env python3
"""Short screens: the fixed bottom (input, messages) never overlaps the top block.

Walkthrough run 3: on the VM's short screen the setup box pushed the console and
input over the telemetry. Run under a private compositor with a short monitor
(for example a nested Hyprland at 1280x720); on a tall screen there is no
overlap to provoke and the check only proves the layout stays separated.
"""
import json
from pathlib import Path
import subprocess
import tempfile

import onboarding_preview as preview

QML_CHECK = r'''
  TestCase {
    name: "ShortScreenLayout"
    when: novaPanel.opened
    function check(value, message) { if (!value) throw new Error(message) }
    function item(name) { var r = findChild(novaPanel, name); check(r !== null, "missing " + name); return r }
    function test_layout() {
      var results = []
      function test(name, fn) {
        try { fn(); results.push({name:name, passed:true}) }
        catch (e) { results.push({name:name, passed:false, error:String(e)}) }
      }
      previewHost.acceptancePreview = false
      function separated(label) {
        var top = item("fixedTop"), bottom = item("fixedBottom"), area = item("layoutArea")
        var info = label + ": area " + area.height + " top " + top.height + " bottom y " + bottom.y
                   + " view " + novaPanel.panelView + " telemetry " + item("telemetryGrid").implicitHeight + "+" + item("telemetryTitle").implicitHeight
        console.warn("LAYOUT " + info)
        check(top.y + top.height <= bottom.y + 0.5, "overlap " + info)
      }
      try {
      test("setup box with a two-line message keeps the bottom clear", function() {
        novaPanel.ollamaInstalled = true
        novaPanel.onboardingState = "NEEDS_USER_SETUP"; novaPanel.onboardingCodes = ["PROVIDER_UNAVAILABLE"]
        novaPanel.approvalFeedback = "Download finished, but NOVA couldn't confirm its check. Nothing was started."
        wait(400)
        separated("setup")
      })
      test("leaving setup restores room", function() {
        novaPanel.approvalFeedback = ""
        novaPanel.onboardingState = "NEEDS_MODEL"; novaPanel.onboardingCodes = []
        wait(400)
        separated("after")
      })
      test("every view keeps the bottom clear (UX Phase 2)", function() {
        novaPanel.onboardingState = "READY"; novaPanel.onboardingCodes = []
        ;["chat", "models", "machine"].forEach(function(view) {
          novaPanel.panelView = view; wait(300)
          separated(view)
        })
        check(item("telemetryFrame").visible && item("corePanel").height >= Style.space(72) - .5, "MACHINE lost its core or telemetry")
        novaPanel.panelView = "chat"
      })
      } catch (e) { results.push({name: "harness setup", passed: false, error: String(e)}) }
      console.warn("LAYOUT_RESULTS " + JSON.stringify(results))
    }
  }
'''


def main():
    fixture = preview.scenarios()["installed-no-selection"]
    with tempfile.TemporaryDirectory(prefix="nova-short-screen-", dir="/tmp") as raw:
        shell = preview.prepare_config(Path(raw), fixture)
        text = shell.read_text().replace("import QtQuick\n", "import QtQuick\nimport QtTest\n", 1)
        shell.write_text(text.rsplit("\n}", 1)[0] + QML_CHECK + "\n}\n")
        result = subprocess.run(["quickshell", "--path", raw], capture_output=True, text=True, timeout=90)
        output = result.stdout + result.stderr
    for row in output.splitlines():
        if "LAYOUT " in row and "LAYOUT_RESULTS" not in row: print(row.split("LAYOUT ", 1)[1])
    line = next((row.split("LAYOUT_RESULTS ", 1)[1] for row in output.splitlines() if "LAYOUT_RESULTS " in row), None)
    if line is None:
        print(output)
        raise SystemExit("Short-screen layout check did not complete")
    rows = json.loads(line)
    for row in rows:
        print(("PASS " if row["passed"] else "FAIL ") + row["name"] + ((": " + row["error"]) if not row["passed"] else ""))
    errors = [r for r in output.splitlines() if any(w in r for w in ("ReferenceError", "TypeError", "Binding loop"))]
    if errors or any(not row["passed"] for row in rows):
        print("\n".join(errors) or output[-3000:])
        raise SystemExit(1)


if __name__ == "__main__":
    main()
