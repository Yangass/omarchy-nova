#!/usr/bin/env python3
"""Fresh-VM run 5 polish (2026-10-08): beginner words, catalog names, layout.

Run under a private compositor. Covers: MODEL NEEDED without quantization or
"install checks" wording, the "Ollama too old" reason and summary, catalog rows
named by display name, catalog details inside the download catalog, and the
downloads button kept clear of the list.
"""
import json
from pathlib import Path
import subprocess
import tempfile

import onboarding_preview as preview

QML_CHECK = r'''
  TestCase {
    name: "PolishRun5"
    when: novaPanel.opened
    function check(value, message) { if (!value) throw new Error(message) }
    function item(name) { var r = findChild(novaPanel, name); check(r !== null, "missing " + name); return r }
    function test_polish() {
      var results = []
      function test(name, fn) {
        try { fn(); results.push({name:name, passed:true}) }
        catch (e) { results.push({name:name, passed:false, error:String(e)}) }
      }
      var small = {catalog_id:"llama3.2-1b-q8-0", display_name:"Llama 3.2 1B (Q8_0)", source:{upstream_id:"llama3.2:1b"}}
      var gemma = {catalog_id:"gemma4-12b-q4-k-m", display_name:"Gemma 4 12B (Q4_K_M)", source:{upstream_id:"gemma4:12b"}}
      novaPanel.catalogModels = [small, gemma]
      wait(50)
      test("MODEL NEEDED speaks plainly", function() {
        novaPanel.onboardingState = "NEEDS_MODEL"; novaPanel.onboardingActions = ["PROPOSE_INSTALL"]; novaPanel.onboardingTarget = "llama3.2-1b-q8-0"
        wait(50)
        check(novaPanel.onboardingMessage() === "No local model is installed yet. Llama 3.2 1B fits this computer.", "message: " + novaPanel.onboardingMessage())
        check(novaPanel.onboardingActionLabel() === "INSTALL LLAMA 3.2 1B", "label: " + novaPanel.onboardingActionLabel())
      })
      test("a too-old Ollama is named with both versions", function() {
        novaPanel.preflightByCatalog = {"gemma4-12b-q4-k-m": {status:"BLOCKED", blockers:["PROVIDER_TOO_OLD"], provider_version:{installed:"0.29.1", required:"0.30.9"}}}
        check(novaPanel.blockedReason(gemma) === "Needs Ollama 0.30.9 or newer; this computer has 0.29.1. Update the system to update Ollama.", "reason: " + novaPanel.blockedReason(gemma))
        novaPanel.onboardingActions = []; novaPanel.onboardingLimitations = ["PREFLIGHT:gemma4-12b-q4-k-m:PROVIDER_TOO_OLD", "PREFLIGHT:llama3.2-1b-q8-0:PROVIDER_TOO_OLD"]
        check(novaPanel.installBlockSummary() === "No catalog model can be installed right now: all 2 need a newer Ollama.", "summary: " + novaPanel.installBlockSummary())
        novaPanel.onboardingLimitations = []
      })
      test("catalog rows use the model's name, not its runtime id", function() {
        novaPanel.panelView = "models"; novaPanel.catalogDownloadsExpanded = true; wait(150)
        var rows = item("catalogModelRepeater"), titles = []
        for (var i = 0; i < rows.count; i++) titles.push(findChild(rows.itemAt(i), "catalogRowTitle").text)
        check(titles.indexOf("Llama 3.2 1B") >= 0 && titles.indexOf("Gemma 4 12B") >= 0, "titles: " + titles)
      })
      test("catalog details live in the download catalog; no idle LOCAL MODELS label", function() {
        novaPanel.panelView = "models"; novaPanel.catalogDownloadsExpanded = true; wait(150)
        var details = item("catalogDetailsToggle"), catalog = item("downloadCatalog")
        var p = details.parent, inside = false
        while (p) { if (p === catalog) inside = true; p = p.parent }
        check(inside, "details toggle outside the download catalog")
        check(!item("modelSwitchStatusText").visible, "idle LOCAL MODELS label shown")
        novaPanel.modelSwitchStatus = "MODEL SWITCH FAILED"; wait(30)
        check(item("modelSwitchStatusText").visible, "switch status hidden")
        novaPanel.modelSwitchStatus = ""
      })
      test("MODELS v2: the downloads button sits next to REFRESH, at the top", function() {
        var list = item("modelList"), button = item("browseModelDownloadsButton"), refresh = item("refreshInstalledModelsButton")
        check(button.parent === refresh.parent && button.x > refresh.x, "not next to REFRESH")
        var top = button.mapToItem(list, 0, 0).y
        check(top >= 0 && top < 40, "not at the top of the list: " + top)
      })
      test("models the user hasn't seen are marked NEW until the catalog is closed", function() {
        novaPanel.catalogDownloadsExpanded = false
        novaPanel.seenCatalogIds = ["llama3.2-1b-q8-0"]; wait(50)
        check(JSON.stringify(novaPanel.newCatalogIds) === JSON.stringify(["gemma4-12b-q4-k-m"]), "new: " + JSON.stringify(novaPanel.newCatalogIds))
        check(item("browseModelDownloadsButton").text === "BROWSE DOWNLOADS · 1 NEW", "button: " + item("browseModelDownloadsButton").text)
        novaPanel.toggleCatalogDownloads(); wait(150)
        var rows = item("catalogModelRepeater"), titles = []
        for (var i = 0; i < rows.count; i++) titles.push(findChild(rows.itemAt(i), "catalogRowTitle").text)
        check(titles.indexOf("Gemma 4 12B") >= 0 && titles.indexOf("Llama 3.2 1B") >= 0, "titles: " + titles)
        // UX Phase 2 step 2c: NEW is a tag on the card now.
        function tags(name) { for (var i = 0; i < rows.count; i++) if (findChild(rows.itemAt(i), "catalogRowTitle").text === name) {
            var out = [], row = rows.itemAt(i); (function walk(o) { if (!o) return; if (o.objectName === "catalogTag" && o.visible) out.push(o.modelData); for (var k = 0; k < o.children.length; k++) walk(o.children[k]) })(row); return out } return [] }
        check(tags("Gemma 4 12B").indexOf("NEW") >= 0 && tags("Llama 3.2 1B").indexOf("NEW") < 0, "NEW tags: " + tags("Gemma 4 12B") + " / " + tags("Llama 3.2 1B"))
        novaPanel.toggleCatalogDownloads(); wait(50)
        check(novaPanel.newCatalogIds.length === 0 && item("browseModelDownloadsButton").text === "BROWSE DOWNLOADS", "not marked seen: " + item("browseModelDownloadsButton").text)
      })
      test("a first run marks nothing as new", function() {
        novaPanel.seenCatalogIds = []; wait(50)
        check(novaPanel.seenCatalogIds.length === 2 && novaPanel.newCatalogIds.length === 0, "first run: " + JSON.stringify(novaPanel.seenCatalogIds))
      })
      console.warn("POLISH_RESULTS " + JSON.stringify(results))
    }
  }
'''


def main():
    fixture = preview.scenarios()["installed-no-selection"]
    with tempfile.TemporaryDirectory(prefix="nova-polish-", dir="/tmp") as raw:
        shell = preview.prepare_config(Path(raw), fixture)
        text = shell.read_text().replace("import QtQuick\n", "import QtQuick\nimport QtTest\n", 1)
        check = QML_CHECK
        shell.write_text(text.rsplit("\n}", 1)[0] + check + "\n}\n")
        result = subprocess.run(["quickshell", "--path", raw], capture_output=True, text=True, timeout=90)
        output = result.stdout + result.stderr
    line = next((row.split("POLISH_RESULTS ", 1)[1] for row in output.splitlines() if "POLISH_RESULTS " in row), None)
    if line is None:
        print(output)
        raise SystemExit("Polish check did not complete")
    rows = json.loads(line)
    for row in rows:
        print(("PASS " if row["passed"] else "FAIL ") + row["name"] + ((": " + row["error"]) if not row["passed"] else ""))
    errors = [r for r in output.splitlines() if any(w in r for w in ("ReferenceError", "TypeError", "Binding loop"))]
    if errors or any(not row["passed"] for row in rows):
        print("\n".join(errors) or output[-3000:])
        raise SystemExit(1)


if __name__ == "__main__":
    main()
