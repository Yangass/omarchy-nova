#!/usr/bin/env python3
"""REC-01: attributed suggestions render on download-catalog rows (isolated HTTP stub).

Run under a private compositor. No real recommendation, catalog or model service is used.
"""
import json
from http.server import BaseHTTPRequestHandler
from pathlib import Path
import subprocess
import tempfile
from threading import Thread
from urllib.parse import urlsplit, parse_qs

import fake_nova
import onboarding_preview as preview

CATALOG = [{"catalog_id": cid, "display_name": cid, "source": {"provider": "ollama", "upstream_id": up},
            "installed_match": {"match_status": "not_installed", "installed": False}}
           for cid, up in (("big-general", "big:1"), ("small-general", "small:1"),
                           ("coder", "coder:1"), ("plain", "plain:1"))]


def candidate(cid, storage, publisher, summary, selectable=True, usable=True):
    return {"catalog_id": cid, "selectable": selectable, "installed": False, "inputs": {"storage_bytes": storage},
            "evidence": [{"policy_usable": usable, "attribution": {"publisher": publisher, "summary": summary,
                                                                    "url": "https://x.example", "retrieved_at": "t"}}]}


PAYLOADS = {
    "general": {"status": "NO_DEFENSIBLE_RECOMMENDATION", "candidates": [
        candidate("big-general", 9_000, "guide.one", "Balanced general model."),
        candidate("small-general", 1_000, "guide.two", "Lightweight general use."),
        candidate("plain", 500, "guide.three", "Not usable evidence.", usable=False),
        candidate("coder", 5_000, "guide.one", "Unselectable here.", selectable=False)]},
    "coding": {"status": "RECOMMENDATION_AVAILABLE", "candidates": [
        candidate("coder", 5_000, "guide.one", "Best local coding."),
        candidate("big-general", 9_000, "guide.one", "Balanced general model.")]},
}
REQUESTS = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/recommendations/models":
            query = parse_qs(url.query)
            REQUESTS.append(query)
            self.reply(200, PAYLOADS[query["profile"][0]])
        else:
            self.reply(503, {"error": "fixture_unavailable"})

    def reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


QML_CHECK = r'''
  NovaClient { id: fakeNova; socketPath: "@SOCKET@" }
  TestCase {
    name: "SuggestionsInteraction"
    when: novaPanel.opened
    function check(value, message) { if (!value) throw new Error(message) }
    function rowFor(cid) {
      var rows = findChild(novaPanel, "catalogModelRepeater")
      for (var i = 0; i < rows.count; i++) if (rows.itemAt(i).modelData.catalog_id === cid) return rows.itemAt(i)
      throw new Error("row missing " + cid)
    }
    function line(cid) { return findChild(rowFor(cid), "catalogSuggestion") }
    function test_suggestions() {
      var results = []
      function test(name, fn) {
        try { fn(); results.push({name:name, passed:true}) }
        catch (e) { results.push({name:name, passed:false, error:String(e)}) }
      }
      previewHost.novaClient = fakeNova
      previewHost.acceptancePreview = false
      novaPanel.panelView = "models"
      novaPanel.catalogDownloadsExpanded = true
      novaPanel.catalogModels = @CATALOG@
      novaPanel.refreshSuggestions()
      var n = 0; while (novaPanel.suggestionSummary === "" && n++ < 200) wait(10)
      wait(80)
      test("summary lists only usable, selectable suggestions, biggest first (smallest without a GPU)", function() {
        check(novaPanel.suggestionSummary === "SUGGESTED FOR THIS HARDWARE · low confidence · from published guides, biggest first: big-general, coder, small-general",
              "summary wrong: " + novaPanel.suggestionSummary)
        previewHost.gpuAvailable = false; wait(50)
        check(novaPanel.suggestionSummary === "SUGGESTED FOR THIS HARDWARE · low confidence · from published guides, smallest first: small-general, coder, big-general",
              "cpu summary wrong: " + novaPanel.suggestionSummary)
        previewHost.gpuAvailable = true; wait(50)
      })
      test("each suggested row names its source, use and confidence", function() {
        // MODELS v2: the guide's words sit behind DETAILS; the row itself says SUGGESTED.
        var big = line("big-general")
        check(!big.visible, "suggestion text shown before DETAILS")
        novaPanel.toggleModelDetails("big-general"); wait(50)
        check(big.visible && big.text === "Suggested for everyday use and coding · guide.one: Balanced general model. · low confidence: from a published guide, not tested by NOVA",
              "big row wrong: " + big.text)
        novaPanel.toggleModelDetails("big-general"); wait(50)
        check(line("coder").text.indexOf("Suggested for coding · guide.one: Best local coding.") === 0, "coder row wrong: " + line("coder").text)
      })
      test("catalog lists suggested models first, biggest first, then the rest", function() {
        var order = novaPanel.orderedCatalogModels.map(function(m) { return m.catalog_id }).join(",")
        check(order === "big-general,coder,small-general,plain", "order: " + order)
        previewHost.gpuAvailable = false; wait(50)
        order = novaPanel.orderedCatalogModels.map(function(m) { return m.catalog_id }).join(",")
        check(order === "small-general,coder,big-general,plain", "cpu order: " + order)
        previewHost.gpuAvailable = true; wait(50)
      })
      test("summary uses display names and drops a model installed since", function() {
        var models = JSON.parse(JSON.stringify(novaPanel.catalogModels))
        models[1].display_name = "Small General 1B"
        models[2].installed_match = {match_status: "exact", installed: true}
        novaPanel.catalogModels = models; wait(50)
        check(novaPanel.suggestionSummary === "SUGGESTED FOR THIS HARDWARE · low confidence · from published guides, biggest first: big-general, Small General 1B",
              "summary: " + novaPanel.suggestionSummary)
        novaPanel.catalogModels = @CATALOG@; wait(50)
      })
      test("a RAM-only budget explains why the list is short", function() {
        var GiB = 1024 * 1024 * 1024
        // The wording is novad's (model_fit explanation); the panel only decides when to show it.
        var sentence = "This computer's graphics share its main memory, so models run in RAM. NOVA keeps 4 GiB for your desktop and apps, which leaves about 3.7 GiB for a model. Larger models are listed but can't be installed here."
        var text = novaPanel.budgetNoteText({mode: "cpu", graphics: "integrated_or_virtual", reserve_bytes: 4 * GiB, operating_budget_bytes: 3.7 * GiB, explanation: sentence})
        check(text === sentence, "note: " + text)
        check(novaPanel.budgetNoteText({mode: "cpu", reserve_bytes: 4 * GiB, operating_budget_bytes: 3.7 * GiB}) === "", "note invented without a backend explanation")
        check(novaPanel.budgetNoteText({mode: "cpu", reserve_bytes: 8 * GiB, operating_budget_bytes: 0, explanation: sentence}) === "", "note shown with no room for a model")
        check(novaPanel.budgetNoteText({mode: "gpu_candidate", reserve_bytes: GiB, operating_budget_bytes: 20 * GiB}) === "", "GPU budget got a RAM note")
        check(novaPanel.budgetNoteText({mode: "unknown"}) === "", "unknown budget got a note")
        novaPanel.budgetNote = text; wait(50)
        var shown = findChild(novaPanel, "budgetNote")
        check(shown.visible && shown.text === text, "note not shown in the download catalog")
        novaPanel.budgetNote = ""
      })
      test("rows without usable guide evidence are not marked", function() {
        check(!line("plain").visible, "unusable evidence produced a suggestion")
      })
      test("download rows show the catalog estimate instead of 'not verified'", function() {
        var next = ({}); next["small-general"] = {status: "BLOCKED", fit_estimate: {rating: "GOOD", recommended_context: 32768, source: "catalog_metadata_estimate"}}
        novaPanel.preflightByCatalog = next; wait(50)
        var text = findChild(rowFor("small-general"), "catalogFitText").text
        check(text === "Estimated to run well (from catalog metadata; not yet tested here).", "row fit text: " + text)
        check(findChild(rowFor("plain"), "catalogFitText").text === "Hardware fit hasn’t been verified yet.", "unknown row changed")
        var models = JSON.parse(JSON.stringify(novaPanel.catalogModels))
        models.push({catalog_id: "other", display_name: "other", source: {provider: "ollama", upstream_id: "other:1"},
                     installed_match: {match_status: "not_installed", installed: false}})
        novaPanel.catalogModels = models
        var blocked = JSON.parse(JSON.stringify(next)); blocked["plain"] = {status: "BLOCKED"}
        novaPanel.preflightByCatalog = blocked; wait(50)
        var order = novaPanel.orderedCatalogModels.map(function(m) { return m.catalog_id }).join(",")
        check(order === "big-general,coder,small-general,other,plain", "blocked rows go last: " + order)
        novaPanel.catalogModels = @CATALOG@; wait(50)
      })
      test("SUGGESTED, FITS and ALL: too-big models only under ALL, with a plain muted reason", function() {
        var pre = ({}); pre["plain"] = {status: "BLOCKED", blockers: ["FIT_POOR"]}; pre["coder"] = {status: "BLOCKED", blockers: ["INSUFFICIENT_STORAGE"]}
        novaPanel.preflightByCatalog = pre; novaPanel.catalogFilter = "suggested"; wait(50)
        var labels = novaPanel.catalogFilterChoices().map(function(c) { return c.label + " · " + c.count }).join(" | ")
        check(labels === "SUGGESTED · 3 | FITS · 3 | ALL · 4", "filters: " + labels)
        check(novaPanel.tooBigModelCount === 1 && !rowFor("plain").visible && rowFor("big-general").visible, "SUGGESTED shows the wrong rows")
        check(findChild(rowFor("big-general"), "catalogTag") === null || findChild(rowFor("big-general"), "catalogTag").modelData !== "SUGGESTED", "SUGGESTED tag repeated under SUGGESTED")
        novaPanel.catalogFilter = "fits"; wait(50)
        check(!rowFor("plain").visible, "too-big row shown under FITS")
        check(rowFor("coder").visible && novaPanel.blockedReason(rowFor("coder").modelData) === "Needs more free disk space.", "disk reason")
        check(findChild(rowFor("coder"), "acquisitionStatusBadge").visible, "blocked row says nothing")
        novaPanel.catalogFilter = "all"; wait(50)
        check(novaPanel.catalogFilter === "all" && rowFor("plain").visible, "ALL did not reveal")
        var badge = findChild(rowFor("plain"), "acquisitionStatusBadge")
        check(!badge.warning, "too-big row still red")
        check(novaPanel.blockedReason(rowFor("plain").modelData) === "Too big for this computer's memory.", "fit reason")
        var saved = novaPanel.modelSuggestions; novaPanel.modelSuggestions = ({}); novaPanel.catalogFilter = "suggested"; wait(50)
        check(novaPanel.effectiveCatalogFilter === "fits" && novaPanel.catalogFilterChoices()[0].id === "fits", "no suggestions still filtered to SUGGESTED")
        novaPanel.modelSuggestions = saved; novaPanel.catalogFilter = "suggested"; novaPanel.preflightByCatalog = ({}); wait(50)
      })
      test("an available row says nothing extra; its facts are words", function() {
        var next = ({}); next["big-general"] = {status: "ELIGIBLE", fit_estimate: {rating: "GOOD", recommended_context: 32768, source: "catalog_metadata_estimate"}}
        novaPanel.preflightByCatalog = next; wait(50)
        check(findChild(rowFor("big-general"), "catalogFitWord").text === "FITS", "fit word: " + findChild(rowFor("big-general"), "catalogFitWord").text)
        check(findChild(rowFor("big-general"), "catalogFacts").text.indexOf("32K") === 0, "facts: " + findChild(rowFor("big-general"), "catalogFacts").text)
        novaPanel.preflightByCatalog = ({}); wait(50)
      })
      test("run 6: too-big models come after a tight fit, smallest first", function() {
        var GiB = 1024 * 1024 * 1024
        var models = [
          {catalog_id: "huge", display_name: "huge", properties: {weight_size_bytes: 22 * GiB}, source: {provider: "ollama", upstream_id: "huge:1"}, installed_match: {match_status: "not_installed", installed: false}},
          {catalog_id: "big", display_name: "big", properties: {weight_size_bytes: 9 * GiB}, source: {provider: "ollama", upstream_id: "big:1"}, installed_match: {match_status: "not_installed", installed: false}},
          {catalog_id: "tight", display_name: "tight", properties: {weight_size_bytes: 2 * GiB}, source: {provider: "ollama", upstream_id: "tight:1"}, installed_match: {match_status: "not_installed", installed: false}},
          {catalog_id: "mid", display_name: "mid", acquisition: {expected_size_bytes: 3 * GiB}, source: {provider: "ollama", upstream_id: "mid:1"}, installed_match: {match_status: "not_installed", installed: false}},
          {catalog_id: "fits", display_name: "fits", properties: {weight_size_bytes: 1 * GiB}, source: {provider: "ollama", upstream_id: "fits:1"}, installed_match: {match_status: "not_installed", installed: false}}]
        var pre = ({})
        pre["huge"] = {status: "BLOCKED", blockers: ["FIT_POOR"]}; pre["big"] = {status: "BLOCKED", blockers: ["FIT_UNSUPPORTED"]}
        pre["mid"] = {status: "BLOCKED", blockers: ["FIT_POOR"]}; pre["tight"] = {status: "BLOCKED", blockers: ["FIT_TIGHT"]}
        var saved = novaPanel.modelSuggestions; novaPanel.modelSuggestions = ({})
        novaPanel.catalogModels = models; novaPanel.preflightByCatalog = pre; wait(50)
        var order = novaPanel.orderedCatalogModels.map(function(m) { return m.catalog_id }).join(",")
        check(order === "fits,tight,mid,big,huge", "order: " + order)
        novaPanel.modelSuggestions = saved; novaPanel.catalogModels = @CATALOG@; novaPanel.preflightByCatalog = ({}); wait(50)
      })
      test("approval details and load buttons use words and names", function() {
        check(novaPanel.provenancePlain(["registry_manifest_probe", "2026-10-04T11:59:26Z"]) === "Size and fingerprint read from Ollama's model registry, checked 2026-10-04",
              "provenance: " + novaPanel.provenancePlain(["registry_manifest_probe", "2026-10-04T11:59:26Z"]))
        check(novaPanel.provenancePlain([]) === "From NOVA's own model list", "empty provenance")
        var models = JSON.parse(JSON.stringify(novaPanel.catalogModels)); models[1].display_name = "Small General 1B"
        novaPanel.catalogModels = models; wait(30)
        check(novaPanel.runtimeDisplayName("small:1") === "Small General 1B" && novaPanel.runtimeDisplayName("other:9") === "other:9", "runtime names")
        novaPanel.onboardingSnapshotFresh = true; novaPanel.onboardingState = "NEEDS_ACTIVATION"; novaPanel.onboardingActions = ["ACTIVATE_MODEL"]; novaPanel.onboardingTarget = "small:1"
        check(novaPanel.onboardingActionLabel() === "LOAD AND USE SMALL GENERAL 1B", "label: " + novaPanel.onboardingActionLabel())
        novaPanel.catalogModels = @CATALOG@; wait(30)
      })
      test("blocked summary names every cause instead of blaming disk", function() {
        novaPanel.onboardingLimitations = ["PREFLIGHT:a:FIT_POOR", "PREFLIGHT:b:FIT_POOR", "PREFLIGHT:b:INSUFFICIENT_STORAGE",
                                           "PREFLIGHT:c:TIGHT_OPT_IN_REQUIRED", "QUALITY_RECOMMENDATION_NO_DEFENSIBLE_RECOMMENDATION"]
        var text = novaPanel.installBlockSummary()
        check(text === "No catalog model can be installed right now: 2 of 3 are too large for this computer's memory; 1 would fit only tightly; 1 of 3 need more free disk space.",
              "summary: " + text)
        novaPanel.onboardingLimitations = []
        check(novaPanel.installBlockSummary() === "", "empty limitations should give no summary")
      })
      console.warn("SUGGESTIONS_RESULTS " + JSON.stringify(results))
    }
  }
'''


def main():
    server = fake_nova.server(Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        fixture = preview.scenarios()["installed-no-selection"]
        with tempfile.TemporaryDirectory(prefix="nova-suggestions-", dir="/tmp") as raw:
            shell = preview.prepare_config(Path(raw), fixture)
            text = shell.read_text().replace("import QtQuick\n", "import QtQuick\nimport QtTest\n", 1)
            check = QML_CHECK.replace("@SOCKET@", server.server_address).replace("@CATALOG@", json.dumps(CATALOG))
            shell.write_text(text.rsplit("\n}", 1)[0] + check + "\n}\n")
            result = subprocess.run(["quickshell", "--path", raw], capture_output=True, text=True, timeout=90)
            output = result.stdout + result.stderr
        line = next((row.split("SUGGESTIONS_RESULTS ", 1)[1] for row in output.splitlines() if "SUGGESTIONS_RESULTS " in row), None)
        if line is None:
            print(output)
            raise SystemExit("Suggestions interaction did not complete")
        rows = json.loads(line)
        for row in rows:
            print(("PASS " if row["passed"] else "FAIL ") + row["name"] + ((": " + row["error"]) if not row["passed"] else ""))
        profiles = sorted(q["profile"][0] for q in REQUESTS)
        print(("PASS" if profiles == ["coding", "general"] and all(q["scope"] == ["known"] for q in REQUESTS) else "FAIL")
              + " requests both profiles at known scope: " + repr(profiles))
        errors = [r for r in output.splitlines() if any(w in r for w in ("ReferenceError", "TypeError", "Binding loop"))]
        if errors or any(not row["passed"] for row in rows) or profiles != ["coding", "general"]:
            print(output)
            raise SystemExit(1)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    main()
