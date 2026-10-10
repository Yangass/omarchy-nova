#!/usr/bin/env python3
"""Exercise the installed-models inventory section in canonical Panel.qml.

Isolated fixture: never touches a live provider. It drives the production
refresh-result classifier (applyInstalledModelsResult(status, body)) directly to
verify the installed list, refresh updates, truthful empty inventory, provider
failure, malformed-200 handling, and the usable normal refresh control without
any inference.

The stale-reply / exactly-once / overlap guarantees live in the request
lifecycle (refreshModels' finish closure) and are exercised over the real HTTP
path by tools/check_model_refresh_interaction.py, not by direct classifier
calls.
"""
import json
from pathlib import Path
import subprocess
import tempfile

import onboarding_preview as preview


QML_CHECK = r'''
  TestCase {
    name: "IsolatedInstalledModelsInteraction"
    when: novaPanel.opened && novaPanel.catalogModels.length > 0
    function check(value, message) { if (!value) throw new Error(message) }
    function installedRepeater() {
      var repeater = findChild(novaPanel, "installedModelsRepeater")
      check(repeater !== null, "installedModelsRepeater missing")
      return repeater
    }
    function aliasText(repeater, index) {
      var row = repeater.itemAt(index)
      check(row !== null, "installed row " + index + " missing")
      var label = findChild(row, "installedModelId")
      return label !== null ? String(label.text) : ""
    }
    function refreshButton() {
      var button = findChild(novaPanel, "refreshInstalledModelsButton")
      check(button !== null, "refreshInstalledModelsButton missing")
      return button
    }
    function renderedTexts(item) {
      if (!item || item.visible === false) return []
      var result = []
      if (typeof item.text === "string") result.push(item.text)
      if (item.children) for (var i = 0; i < item.children.length; ++i)
        result = result.concat(renderedTexts(item.children[i]))
      return result
    }
    function check_alias_verbatim() {
      novaPanel.applyInstalledModelsResult(200, JSON.stringify({
        models: [{ id: "nova-code-qwen38-96k:latest", provider: "ollama", available: true }]
      }))
      wait(100)
      check(novaPanel.discoveredModels.length === 1, "installed inventory not applied")
      var repeater = installedRepeater()
      check(repeater.count === 1, "installed repeater did not render one row")
      // One naming rule with the model bar (Konstantin, 2026-10-08): plain name in the
      // row, the exact Ollama name in its details.
      check(aliasText(repeater, 0) === "nova-code-qwen38-96k",
        "installed row not shown by its plain name: " + aliasText(repeater, 0))
      var exact = findChild(repeater.itemAt(0), "installedModelExactName")
      check(exact !== null && exact.text === "Exact name in Ollama: nova-code-qwen38-96k:latest", "exact name missing from details")
      check(novaPanel.modelsUnavailable === false, "available inventory reported unavailable")
      check(refreshButton().enabled === true, "normal refresh disabled after success")
    }
    function check_refresh_updates() {
      novaPanel.applyInstalledModelsResult(200, JSON.stringify({
        models: [
          { id: "qwen3:8b", provider: "ollama", available: true },
          { id: "external-alias:latest", provider: "ollama", available: false }
        ]
      }))
      wait(100)
      check(novaPanel.discoveredModels.length === 2, "refresh did not replace list")
      var repeater = installedRepeater()
      check(repeater.count === 2, "refresh row count wrong")
      check(aliasText(repeater, 0) === "Qwen 3 8B", "first row not named by the catalog: " + aliasText(repeater, 0))
      check(aliasText(repeater, 1) === "external-alias", "second alias: " + aliasText(repeater, 1))
    }
    function check_empty_inventory() {
      novaPanel.applyInstalledModelsResult(200, JSON.stringify({ models: [] }))
      wait(100)
      var repeater = installedRepeater()
      check(repeater.count === 0, "empty inventory still renders rows")
      check(novaPanel.discoveredModels.length === 0, "empty inventory not truthfully empty")
      check(novaPanel.modelsUnavailable === false, "empty inventory mislabeled as provider failure")
      check(refreshButton().enabled === true, "normal refresh disabled after valid empty")
    }
    function check_provider_failure() {
      novaPanel.applyInstalledModelsResult(503, "")
      wait(100)
      check(novaPanel.modelsUnavailable === true, "provider failure not reported unavailable")
      check(novaPanel.discoveredModels.length === 0, "failure left stale inventory")
    }
    function check_malformed_200_is_failure() {
      novaPanel.applyInstalledModelsResult(200, "not-json")
      wait(100)
      check(novaPanel.modelsUnavailable === true, "malformed 200 body accepted as success")
      novaPanel.applyInstalledModelsResult(200, JSON.stringify({ unexpected: true }))
      wait(100)
      check(novaPanel.modelsUnavailable === true, "200 without models array accepted as success")
      check(novaPanel.discoveredModels.length === 0, "non-array models left stale inventory")
    }
    function check_installed_details_control() {
      // Ensure one installed model is present.
      novaPanel.applyInstalledModelsResult(200, JSON.stringify({
        models: [{ id: "qwen3:8b", provider: "ollama", available: true }]
      }))
      wait(100)
      var repeater = installedRepeater()
      var row = repeater.itemAt(0)
      check(row !== null, "installed row missing")
      var detailsBtn = findChild(row, "installedModelDetailsButton")
      check(detailsBtn !== null, "installedModelDetailsButton missing")
      check(detailsBtn.visible === true, "details button not visible")
      check(detailsBtn.focusable === true, "details button not focusable")
      check(detailsBtn.text === "DETAILS", "details button label wrong: " + detailsBtn.text)
      // Toggle open
      detailsBtn.clicked()
      wait(50)
      check(novaPanel.installedModelDetailsOpen(novaPanel.discoveredModels[0]), "details did not open")
      check(detailsBtn.text === "HIDE DETAILS", "details button did not change label")
      // Toggle closed
      detailsBtn.clicked()
      wait(50)
      check(!novaPanel.installedModelDetailsOpen(novaPanel.discoveredModels[0]), "details did not close")
    }
    function check_provider_scoped_details_and_unknown_alias() {
      var catalog = shell.fixture.catalog_models[0]
      var local = {id:"qwen3:8b",provider:"ollama",available:true}
      var other = {id:"qwen3:8b",provider:"other-provider",available:true}
      var unknown = {id:"unknown-local-alias:latest",provider:"ollama",available:true}
      novaPanel.catalogModels = [catalog]
      novaPanel.applyInstalledModelsResult(200, JSON.stringify({models:[local,other,unknown]}))
      wait(100)
      check(novaPanel.matchedCatalogEntryFor(local) === catalog, "Ollama identity did not match")
      check(novaPanel.matchedCatalogEntryFor(other) === null, "other provider inherited Ollama catalog")
      check(novaPanel.matchedCatalogEntryFor(unknown) === null, "unknown alias inherited catalog")
      novaPanel.installedDetailsOpenId = ""
      novaPanel.toggleInstalledModelDetails(local); wait(50)
      check(!novaPanel.installedModelDetailsOpen(other), "same-name provider details opened together")
      var rows = installedRepeater()
      novaPanel.toggleInstalledModelDetails(other); wait(50)
      check(!novaPanel.installedModelDetailsOpen(local), "other provider changed Ollama detail state")
      var otherText = renderedTexts(rows.itemAt(1)).join("\n")
      check(otherText.indexOf("Hardware fit hasn’t been verified yet.") >= 0
            && otherText.indexOf("Working context hasn’t been estimated yet.") >= 0,
            "other provider was not shown as unassessed")
      check(otherText.indexOf("Expected to run well.") < 0 && otherText.indexOf("32K") < 0
            && otherText.indexOf("Native maximum:") < 0 && otherText.indexOf("Fit estimate confidence:") < 0,
            "other provider inherited Ollama fit or catalog metadata")
      novaPanel.toggleInstalledModelDetails(unknown); wait(50)
      var unknownText = renderedTexts(rows.itemAt(2)).join("\n")
      check(unknownText.indexOf("Hardware fit hasn’t been verified yet.") >= 0
            && unknownText.indexOf("Native maximum:") < 0,
            "unknown alias was assigned catalog evidence")
    }
    function check_matched_rendered_details() {
      var catalog = shell.fixture.catalog_models[0]
      var local = {id:"qwen3:8b",provider:"ollama",available:true}
      novaPanel.catalogModels = [catalog]
      novaPanel.applyInstalledModelsResult(200, JSON.stringify({models:[local]}))
      var history = {}; history[catalog.catalog_id] = [{id:"fixture-history",state:"INSTALLED"}]
      novaPanel.acquisitionHistoryByCatalog = history
      var active = {}; active[catalog.catalog_id] = {id:"fixture-history",state:"INSTALLED"}
      novaPanel.acquisitionByCatalog = active
      novaPanel.installedDetailsOpenId = ""
      novaPanel.toggleInstalledModelDetails(local); wait(100)
      var rendered = renderedTexts(installedRepeater().itemAt(0)).join("\n")
      check(rendered.indexOf("Expected to run well.") >= 0 && rendered.indexOf("Estimated working context: 32K.") >= 0,
            "matched fit and estimated context missing")
      check(rendered.indexOf("Working context is how much conversation") >= 0,
            "working-context explanation missing")
      check(rendered.indexOf("Native maximum: 40K") >= 0 && rendered.indexOf("4.9 GiB") >= 0
            && rendered.indexOf("qwen3") >= 0 && rendered.indexOf("Q4_K_M") >= 0,
            "catalog native maximum, weight, family or quantization missing")
      check(rendered.indexOf("Fit estimate confidence: MEDIUM.") >= 0,
            "fit-confidence caveat missing")
      check(rendered.indexOf("VERIFIED // NOT IN USE YET") >= 0
            && rendered.indexOf("Previous attempt: fixture-history") >= 0,
            "acquisition detail or history missing")
    }
    function test_installed_models_behavior() {
      var results = []
      function test(name, fn) {
        try { fn(); results.push({ name: name, passed: true }) }
        catch (e) { results.push({ name: name, passed: false, error: String(e) }) }
      }
      test("installed non-catalog alias is shown verbatim", check_alias_verbatim)
      test("refresh updates the installed list", check_refresh_updates)
      test("empty inventory is reported truthfully", check_empty_inventory)
      test("provider failure is reported unavailable", check_provider_failure)
      test("malformed or model-less 200 is a failure, not empty", check_malformed_200_is_failure)
      test("installed details control toggles", check_installed_details_control)
      test("same-name providers and unknown alias remain separate", check_provider_scoped_details_and_unknown_alias)
      test("matched installed row renders catalog detail and history", check_matched_rendered_details)
      console.warn("INSTALLED_MODELS_RESULTS " + JSON.stringify(results))
    }
  }
'''


def main():
    fixture = preview.scenarios()["installed-no-selection"]
    with tempfile.TemporaryDirectory(prefix="nova-installed-models-", dir="/tmp") as raw:
        target = Path(raw)
        shell = preview.prepare_config(target, fixture)
        text = shell.read_text().replace("import QtQuick\n", "import QtQuick\nimport QtTest\n", 1)
        text = text.rsplit("\n}", 1)[0] + QML_CHECK + "\n}\n"
        shell.write_text(text)
        result = subprocess.run(["quickshell", "--path", str(target)], capture_output=True,
                                text=True, timeout=45)
        output = result.stdout + result.stderr
    marker = "INSTALLED_MODELS_RESULTS "
    line = next((row.split(marker, 1)[1] for row in output.splitlines() if marker in row), None)
    if line is None:
        print(output)
        raise SystemExit("Installed-models interaction harness did not complete")
    rows = json.loads(line)
    for row in rows:
        print(("PASS " if row["passed"] else "FAIL ") + row["name"] +
              ((": " + row["error"]) if not row["passed"] else ""))
    if any(not row["passed"] for row in rows):
        print(output)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
