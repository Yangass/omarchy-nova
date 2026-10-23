#!/usr/bin/env python3
"""Measure installed-list/catalog disclosure in the canonical fixture panel."""
import json
from http.server import BaseHTTPRequestHandler
from pathlib import Path
import subprocess
import tempfile
from threading import Thread

import fake_nova
import onboarding_preview as preview


REQUESTS = []


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        REQUESTS.append(("GET", self.path))
        self.send_error(503)

    def do_POST(self):
        REQUESTS.append(("POST", self.path))
        self.send_error(503)


QML_CHECK = r'''
  NovaClient { id: fakeNova; socketPath: "@SOCKET@" }
  TestCase {
    name: "ModelCatalogDisclosure"
    when: novaPanel.opened && novaPanel.catalogModels.length > 0
    function check(value, message) { if (!value) throw new Error(message) }
    function child(name) {
      var item = findChild(novaPanel, name)
      check(item !== null, "missing " + name)
      return item
    }
    function catalogChild(name) {
      var rows = child("catalogModelRepeater")
      check(rows.count > 0, "catalog row missing")
      var item = findChild(rows.itemAt(0), name)
      check(item !== null, "catalog control missing: " + name)
      return item
    }
    function click(item) {
      var list = child("modelList")
      var top = item.mapToItem(list.contentItem, 0, 0).y
      list.contentY = Math.max(0, Math.min(list.contentHeight-list.height, top-12))
      wait(80)
      var catcher = child("panelKeyCatcher")
      var p = item.mapToItem(catcher, item.width/2, item.height/2)
      mouseClick(catcher, p.x, p.y, Qt.LeftButton)
    }
    function test_disclosure() {
      var results = [], geometry = {}
      previewHost.novaClient = fakeNova
      function test(name, fn) {
        try { fn(); results.push({name:name, passed:true}) }
        catch (e) { results.push({name:name, passed:false, error:String(e)}) }
      }
      var list = child("modelList"), disclosure = child("browseModelDownloadsButton")
      var catalog = child("downloadCatalog")
      var model = shell.fixture.catalog_models[0]
      test("installed list first and catalog collapsed without focusable hidden controls", function() {
        check(novaPanel.catalogDownloadsExpanded === false && !catalog.visible, "catalog starts expanded")
        check(disclosure.visible && disclosure.focusable && disclosure.text === "BROWSE DOWNLOADS", "browse control missing")
        check(!catalogChild("modelDetailsButton").visible, "hidden details still visible/focusable")
        var rows = child("installedModelsRepeater")
        check(rows.count === 1, "installed inventory lost")
        check(findChild(rows.itemAt(0), "loadInstalledModelButton") !== null, "load control lost")
        geometry.initialContent = list.contentHeight
        geometry.viewport = list.height
        var inHeader = false, up = disclosure.parent
        while (up) { if (up === child("installedModelsSection")) inHeader = true; up = up.parent }
        check(inHeader && disclosure.mapToItem(list, 0, 0).y < findChild(rows.itemAt(0), "loadInstalledModelButton").mapToItem(list, 0, 0).y, "browse is not in the INSTALLED header above the models")
      })
      test("compact selector budgets a usable list and contained footer", function() {
        var selector = child("modelSelector")
        var load = findChild(child("installedModelsRepeater").itemAt(0), "loadInstalledModelButton")
        function inspect(label) {
          var footerBottom = disclosure.mapToItem(selector, 0, disclosure.height).y
          var selectorBottom = selector.mapToItem(novaPanel, 0, selector.height).y
          var chatTop = child("fixedBottom").mapToItem(novaPanel, 0, 0).y  // Phase 2: no transcript in MODELS
          geometry[label] = {selector:selector.height, viewport:list.height,
                             footerBottom:footerBottom, selectorBottom:selectorBottom, chatTop:chatTop}
          check(list.height >= load.height + 6, label + " list cannot show a load control")
          check(footerBottom <= selector.height + 1 && disclosure.y >= 0,
                label + " disclosure leaves selector bounds")
          check(selectorBottom < chatTop, label + " selector overlaps the bottom bar")
        }
        inspect("previewChrome")
        previewHost.acceptancePreview = false; wait(100)
        inspect("normalChrome")
        previewHost.acceptancePreview = true; wait(100)
      })
      test("MODELS v2: DETAILS sits on the facts line, LOAD AND USE on its own, nothing overlaps", function() {
        var first = child("installedModelsRepeater").itemAt(0)
        var actions = findChild(first, "installedModelActions")
        var load = findChild(first, "loadInstalledModelButton")
        var details = findChild(first, "installedModelDetailsButton")
        var status = findChild(first, "installedModelStatusText")
        check(actions && load && details && status, "installed card parts missing")
        check(load.height >= 18 && load.height <= 23 && details.height === load.height,
              "model buttons are not compact and readable")
        var l = load.mapToItem(first, 0, 0), d = details.mapToItem(first, 0, 0), t = status.mapToItem(first, 0, 0)
        check(d.y + details.height <= l.y + 1, "LOAD AND USE is not below the facts line")
        check(t.x + status.width <= d.x + 1, "facts run under DETAILS")
        check(d.x + details.width <= first.width, "DETAILS outside the card")
      })
      test("mouse and programmatic disclosure only alter presentation", function() {
        var prior = novaPanel.onboardingState
        // Expand first: quickshell masks child visible under an invisible parent.
        var catalog = child("catalogModelRepeater")
        click(disclosure); wait(100)
        check(novaPanel.catalogDownloadsExpanded && catalog.visible, "mouse did not expand")
        check(catalog.itemAt(0).visible === false, "exact installed catalog row not hidden")
        var absent = JSON.parse(JSON.stringify(shell.fixture.catalog_models[0]))
        absent.installed_match = {installed:false,match_status:"not_installed"}
        novaPanel.catalogModels = [absent]
        wait(50)
        check(catalog.itemAt(0).visible === true, "non-installed catalog row not visible")
        check(catalogChild("modelDetailsButton").visible, "catalog details inaccessible")
        geometry.expandedContent = list.contentHeight
        check(geometry.expandedContent > geometry.initialContent, "expanded catalog did not add layout height")
        novaPanel.toggleCatalogDownloads(); wait(100)
        check(!catalog.visible && !catalogChild("modelDetailsButton").visible, "programmatic collapse left controls visible")
        check(Math.abs(list.contentHeight - geometry.initialContent) < 2, "collapsed catalog still occupies layout")
        check(novaPanel.onboardingState === prior && !novaPanel.modelSwitching
              && novaPanel.pendingApproval === null && novaPanel.trackedAcquisitionId === "",
              "disclosure started an operation")
      })
      test("exact installed identity is hidden; progress keeps it visible", function() {
        // Restore exact-installed catalog model.
        novaPanel.catalogModels = JSON.parse(JSON.stringify([shell.fixture.catalog_models[0]]))
        wait(50)
        // Expand the catalog so row visibility reflects the row's own binding,
        // not a collapsed parent (parent visible=false masks descendants).
        novaPanel.catalogDownloadsExpanded = true
        wait(50)
        var catalog = child("catalogModelRepeater")
        check(catalog.itemAt(0).visible === false, "exact installed row should be hidden")
        check(catalog.itemAt(0).height === 0, "hidden row should not occupy layout")
        novaPanel.applyInstalledModelsResult(200, JSON.stringify({models:[
          {id:"qwen3:8b",provider:"other-provider",available:true}
        ]})); wait(50)
        check(catalog.itemAt(0).visible && catalog.itemAt(0).height > 0,
              "same-name other provider hid the Ollama catalog row")
        novaPanel.applyInstalledModelsResult(200, JSON.stringify({models:[
          {id:"qwen3:8b",available:true}
        ]})); wait(50)
        check(catalog.itemAt(0).visible,
              "installed row without a provider hid the catalog identity")
        novaPanel.applyInstalledModelsResult(200, JSON.stringify({models:[]})); wait(50)
        check(catalog.itemAt(0).visible && catalog.itemAt(0).height > 0,
              "successful empty inventory hid the only catalog details/history route")
        novaPanel.applyInstalledModelsResult(503, ""); wait(50)
        check(catalog.itemAt(0).visible && catalog.itemAt(0).height > 0,
              "unavailable inventory hid the only catalog details/history route")
        novaPanel.applyInstalledModelsResult(200, JSON.stringify({models:[
          {id:"qwen3:8b",provider:"ollama",available:true}
        ]})); wait(50)
        check(catalog.itemAt(0).visible === false,
              "catalog duplicate remained after installed identity returned")
        // Active progress must keep the row visible.
        var mapped = {}
        mapped[model.catalog_id] = {state:"DOWNLOADING",progress:{current_blob_index:1,
          current_blob_completed_bytes:10,current_blob_total_bytes:100}}
        novaPanel.acquisitionByCatalog = mapped
        wait(50)
        check(catalog.itemAt(0).visible === true, "active progress row not visible")
        check(catalog.itemAt(0).height > 0, "active progress row has no height")
        // Unresolved recovery must keep the row visible.
        novaPanel.acquisitionByCatalog = ({})
        var recovery = {}
        recovery[model.catalog_id] = {state:"RECOVERY_REQUIRED",recovery_resolution:null}
        novaPanel.acquisitionByCatalog = recovery
        wait(50)
        check(catalog.itemAt(0).visible === true, "unresolved recovery row not visible")
        // Resolved recovery should hide the row again. Assign a fresh object
        // reference: reassigning the same object does not retrigger the binding.
        var resolved = {}
        resolved[model.catalog_id] = {state:"RECOVERY_REQUIRED",recovery_resolution:{outcome:"RETRY_ALLOWED"}}
        novaPanel.acquisitionByCatalog = resolved
        wait(50)
        check(catalog.itemAt(0).visible === false, "resolved recovery row still visible")
        // Clear state.
        novaPanel.acquisitionByCatalog = ({})
        wait(50)
      })
      test("refresh and panel reopen preserve disclosure; five long rows scroll", function() {
        // Normalize to expanded regardless of prior test state, then verify
        // refresh and reopen preserve the expanded disclosure choice.
        if (!novaPanel.catalogDownloadsExpanded) novaPanel.toggleCatalogDownloads()
        wait(80)
        novaPanel.applyInstalledModelsResult(200, JSON.stringify({models:[
          {id:"qwen3:8b",provider:"ollama",available:true},
          {id:"qwen3:14b",provider:"ollama",available:true},
          {id:"qwen3.8:27b-q4_K_M",provider:"ollama",available:true},
          {id:"nova-code-qwen38-96k:latest",provider:"ollama",available:true},
          {id:"an-extremely-long-locally-installed-model-alias-that-must-wrap-without-hiding-its-load-control:latest",provider:"ollama",available:true}
        ]}))
        wait(100)
        check(novaPanel.catalogDownloadsExpanded && catalog.visible, "refresh collapsed catalog")
        novaPanel.close(); wait(80); novaPanel.open(); wait(120)
        check(novaPanel.catalogDownloadsExpanded && catalog.visible, "reopen lost disclosure choice")
        // Preview open reapplies its frozen one-model fixture; restore a synthetic
        // inventory long enough to overflow the full-height MODELS list on any screen
        // (UX Phase 2: five rows no longer overflow it).
        var many = []
        for (var m = 0; m < 13; m++) many.push({id:"fixture-model-" + m + ":latest",provider:"ollama",available:true})
        many.push({id:"an-extremely-long-locally-installed-model-alias-that-must-wrap-without-hiding-its-load-control:latest",provider:"ollama",available:true})
        novaPanel.applyInstalledModelsResult(200, JSON.stringify({models:many}))
        wait(100)
        var rows = child("installedModelsRepeater"), last = rows.itemAt(13)
        check(rows.count === 14 && last !== null, "fourteen rows not rendered")
        var label = findChild(last, "installedModelId"), button = findChild(last, "loadInstalledModelButton")
        check(label && button, "long-alias row lost label or action")
        check(label.height > 18, "long alias did not wrap")
        check(button.mapToItem(last, 0, 0).y >= label.mapToItem(last, 0, label.height).y,
              "long alias overlaps action")
        check(list.contentHeight > list.height, "long model list is not scrollable")
        var top = button.mapToItem(list.contentItem, 0, 0).y
        list.contentY = Math.max(0, Math.min(list.contentHeight-list.height, top+button.height-list.height+8))
        wait(80)
        var p = button.mapToItem(list, 0, 0)
        check(p.y >= -2 && p.y+button.height <= list.height+2, "fifth load action cannot be scrolled into view")
        geometry.fiveContent = list.contentHeight
        geometry.fiveViewport = list.height
        geometry.fifthButtonY = p.y
        geometry.fifthButtonHeight = button.height
        geometry.selectorBottom = child("modelSelector").mapToItem(novaPanel,0,child("modelSelector").height).y
        geometry.chatTop = child("fixedBottom").mapToItem(novaPanel,0,0).y
        geometry.disclosureBottom = disclosure.mapToItem(child("modelSelector"),0,disclosure.height).y
        check(geometry.disclosureBottom <= child("modelSelector").height+1,
              "disclosure footer falls outside selector")
        check(geometry.selectorBottom < geometry.chatTop, "selector overlaps the bottom bar")
      })
      test("provider failure retains explicit retry", function() {
        novaPanel.applyInstalledModelsResult(503, "")
        wait(50)
        check(novaPanel.modelsUnavailable, "provider failure not visible")
        var retry = novaPanel.catalogDownloadsExpanded
          ? child("refreshInstalledModelsButton") : child("modelsRefreshControl")
        check(retry.visible && retry.enabled, "retry lost on provider failure")
        var absent = JSON.parse(JSON.stringify(model)), eligible = {}
        absent.installed_match = {installed:false,match_status:"not_installed"}
        eligible[model.catalog_id] = {status:"ELIGIBLE"}
        novaPanel.catalogModels = [absent]
        novaPanel.preflightByCatalog = eligible
        wait(50)
        check(catalogChild("installModelButton").visible && !catalogChild("installModelButton").enabled,
              "stale preflight action remained enabled without inventory")
        novaPanel.catalogModels = [model]
        novaPanel.applyInstalledModelsResult(200, JSON.stringify({models:[]}))
        wait(50)
      })
      test("active progress and unresolved recovery have explicit reveal", function() {
        var mapped = {}
        mapped[model.catalog_id] = {state:"DOWNLOADING",progress:{current_blob_index:2,
          current_blob_completed_bytes:25,current_blob_total_bytes:100}}
        novaPanel.acquisitionByCatalog = mapped
        novaPanel.catalogDownloadsExpanded = false
        wait(80)
        check(disclosure.text.indexOf("Show installation progress") === 0,
              "active acquisition hidden without a reveal")
        novaPanel.toggleCatalogDownloads(); wait(100)
        check(catalog.visible && novaPanel.acquisitionBlobProgress(model).determinate,
              "per-blob progress unavailable after reveal")
        var recovery = {}
        recovery[model.catalog_id] = {state:"RECOVERY_REQUIRED",recovery_resolution:null}
        novaPanel.acquisitionByCatalog = recovery
        novaPanel.catalogDownloadsExpanded = false
        wait(80)
        check(disclosure.text.indexOf("Review installation recovery") === 0,
              "recovery hidden without a reveal")
        novaPanel.toggleCatalogDownloads(); wait(100)
        check(catalogChild("reviewRecoveryButton").visible, "recovery action inaccessible")
      })
      test("expanded catalog retains installation action and details", function() {
        novaPanel.acquisitionByCatalog = ({})
        var eligible = {}; eligible[model.catalog_id] = {status:"ELIGIBLE"}
        novaPanel.preflightByCatalog = eligible
        var absent = JSON.parse(JSON.stringify(model))
        absent.installed_match = {installed:false,match_status:"not_installed"}
        novaPanel.catalogModels = [absent]
        wait(80)
        check(catalogChild("installModelButton").visible, "eligible install action inaccessible")
        check(catalogChild("modelDetailsButton").visible, "catalog details inaccessible")
        novaPanel.toggleModelDetails(model.catalog_id); wait(80)
        check(novaPanel.modelDetailsOpen(model.catalog_id), "details did not open")
      })
      console.warn("CATALOG_DISCLOSURE_RESULTS " + JSON.stringify({cases:results,geometry:geometry}))
    }
  }
'''


def main():
    fixture = preview.scenarios()["installed-no-selection"]
    fixture["discovered_models"] = [{"id": preview.MODEL_ID, "provider": "ollama", "available": True}]
    server = fake_nova.server(Stub)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="nova-catalog-disclosure-", dir="/tmp") as raw:
            shell = preview.prepare_config(Path(raw), fixture)
            source = shell.read_text().replace("import QtQuick\n", "import QtQuick\nimport QtTest\n", 1)
            checks = QML_CHECK.replace("@SOCKET@", server.server_address)
            shell.write_text(source.rsplit("\n}", 1)[0] + checks + "\n}\n")
            result = subprocess.run(["quickshell", "--path", raw], capture_output=True, text=True, timeout=45)
            output = result.stdout + result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    marker = "CATALOG_DISCLOSURE_RESULTS "
    line = next((row.split(marker, 1)[1] for row in output.splitlines() if marker in row), None)
    if line is None:
        print(output)
        raise SystemExit("Catalog-disclosure fixture did not complete")
    report = json.loads(line)
    for row in report["cases"]:
        print(("PASS " if row["passed"] else "FAIL ") + row["name"] +
              ((": " + row["error"]) if not row["passed"] else ""))
    print("GEOMETRY " + json.dumps(report["geometry"], sort_keys=True))
    if REQUESTS:
        raise SystemExit("Disclosure fixture unexpectedly contacted an API: " + repr(REQUESTS))
    print("PASS no API requests during fixture disclosure")
    if any(not row["passed"] for row in report["cases"]):
        print(output)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
