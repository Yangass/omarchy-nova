#!/usr/bin/env python3
"""ADD A MODEL in the download catalog against an isolated HTTP stub (2026-10-08).

Run under a private compositor. No registry, model or real novad is used: the stub
answers POST /models/requested by the reference's name and records every request.
"""
import json
import os
from http.server import BaseHTTPRequestHandler
from pathlib import Path
import subprocess
import tempfile
from threading import Thread

import fake_nova
import onboarding_preview as preview

REQUESTS = {"requested": [], "catalog_reads": 0, "removed": []}
OUTCOMES = {"gemma4:12b": (201, {"status": "added", "catalog_id": "requested-gemma4-12b-q4-k-m",
                                 "display_name": "gemma4:12b 12B (Q4_K_M)", "name": "gemma4:12b"}),
            "nothere:1b": (404, {"status": "not_found", "name": "nothere:1b"}),
            "offline:1b": (502, {"status": "registry_unavailable", "name": "offline:1b"}),
            "qwen3-coder:30b": (200, {"status": "in_catalog", "name": "qwen3-coder:30b", "catalog_id": "qwen3-coder-30b-q4-k-m"}),
            "weird:1b": (422, {"status": "unsupported_model", "name": "weird:1b"})}

# catalog_repins (2026-10-10): novad asks the registry before an install is proposed.
DESCRIPTOR = {"provider": "ollama", "expected_artifact_size": 5225387164, "provenance_summary": ["registry_manifest_probe"]}
PROPOSALS = {"gone-model": (409, {"error": "model_gone_upstream", "upstream": {"status": "gone"}}),
             "changed-model": (409, {"error": "model_changed_too_much_upstream",
                                     "upstream": {"status": "changed_too_much", "reasons": ["architecture_changed"]}}),
             "repinned-model": (201, {"id": "acq_repinned", "expires_at": 9999999999, "upstream": {"status": "repinned"},
                                      "preflight": {"descriptor": DESCRIPTOR, "fit_state": "GOOD"}}),
             "same-model": (201, {"id": "acq_same", "expires_at": 9999999999, "upstream": {"status": "unchanged"},
                                  "preflight": {"descriptor": DESCRIPTOR, "fit_state": "GOOD"}})}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path == "/__requests__":
            self.reply(200, REQUESTS)
        elif self.path.startswith("/catalog/models"):
            REQUESTS["catalog_reads"] += 1
            self.reply(200, {"status": "ready", "catalog_revision": "test", "record_count": 0, "models": []})
        else:
            self.reply(503, {"error": "fixture_unavailable"})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        try: body = json.loads(raw)
        except ValueError: body = {}
        if self.path.startswith("/acquisition/models/") and self.path.endswith("/propose"):
            self.reply(*PROPOSALS.get(self.path.split("/")[3], (404, {"error": "catalog_model_not_found"}))); return
        if self.path != "/models/requested":
            self.reply(404, {"error": "fixture_path"}); return
        REQUESTS["requested"].append(body)
        reference = str(body.get("reference", "")).strip()
        status, payload = OUTCOMES.get(reference, (400, {"status": "invalid_reference"}))
        self.reply(status, payload)

    def do_DELETE(self):
        prefix = "/models/requested/"
        if not self.path.startswith(prefix):
            self.reply(404, {"error": "Unknown endpoint."}); return
        catalog_id = self.path[len(prefix):]
        REQUESTS["removed"].append(catalog_id)
        if catalog_id == "requested-gone":
            self.reply(404, {"status": "not_found", "catalog_id": catalog_id}); return
        self.reply(200, {"status": "removed", "catalog_id": catalog_id})

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
    name: "AddModelInteraction"
    when: novaPanel.opened
    function check(value, message) { if (!value) throw new Error(message) }
    function item(name) { var r = findChild(novaPanel, name); check(r !== null, "missing " + name); return r }
    function get(path) {
      var xhr = fakeNova.request(), complete = false, result = null
      xhr.onreadystatechange = function() { if (xhr.readyState === XMLHttpRequest.DONE) { result = xhr.responseText; complete = true } }
      xhr.open("GET", path); xhr.send()
      var n = 0; while (!complete && n++ < 100) wait(10)
      return JSON.parse(result)
    }
    function add(text) {
      item("addModelInput").text = text
      novaPanel.addRequestedModel(item("addModelInput").text)
      var n = 0; while (novaPanel.addModelBusy && n++ < 200) wait(10)
      wait(50)
      return item("addModelStatusText").text
    }
    function test_add_model() {
      var results = []
      function test(name, fn) {
        try { fn(); results.push({name:name, passed:true}) }
        catch (e) { results.push({name:name, passed:false, error:String(e)}) }
      }
      previewHost.novaClient = fakeNova
      previewHost.acceptancePreview = false
      novaPanel.panelView = "models"; novaPanel.catalogDownloadsExpanded = true
      wait(200)
      test("the download catalog offers ADD A MODEL with an example", function() {
        check(item("addModelInput").visible && item("addModelButton").visible, "field hidden")
        check(item("addModelInput").placeholderText.indexOf("gemma4:12b") >= 0, "no example: " + item("addModelInput").placeholderText)
      })
      test("an empty field sends nothing", function() {
        var before = get("/__requests__").requested.length
        novaPanel.addRequestedModel("   "); wait(50)
        check(get("/__requests__").requested.length === before, "empty reference sent")
      })
      test("a found model is added and the catalog is re-read", function() {
        var reads = get("/__requests__").catalog_reads
        var text = add("gemma4:12b")
        check(text.indexOf("Added gemma4:12b") === 0 && text.indexOf("usual checks") > 0, "added text: " + text)
        check(JSON.stringify(get("/__requests__").requested.slice(-1)[0]) === JSON.stringify({reference:"gemma4:12b"}), "wrong body")
        check(get("/__requests__").catalog_reads > reads, "catalog not refreshed")
        check(item("addModelInput").text === "", "field not cleared after success")
      })
      test("each failure is explained in plain words", function() {
        check(add("nothere:1b") === "Ollama's library has no model called nothere:1b.", "404: " + item("addModelStatusText").text)
        check(add("offline:1b") === "Couldn't reach Ollama's library. Check the internet connection and try again.", "502")
        check(add("weird:1b") === "NOVA can't read weird:1b from Ollama's library, so it can't check it.", "422")
        check(add("someone/model") === "Use a model name from Ollama's library, like gemma4:12b, or its ollama.com/library link.", "400")
        check(novaPanel.addModelFailed, "failure not marked")
        check(add("qwen3-coder:30b") === "qwen3-coder:30b is already in NOVA's catalog below." && !novaPanel.addModelFailed, "in catalog: " + item("addModelStatusText").text)
      })
      test("a requested model is labelled in the catalog and on the approval", function() {
        check(novaPanel.requestedTag({user_requested: true}) === "  YOU ADDED" && novaPanel.requestedTag({user_requested: false}) === "", "row tag")
        check(novaPanel.approvalSourceText("user_requested") === "Not in NOVA's catalog: you asked for this one", "approval source")
        check(novaPanel.approvalSourceText("backend_allowlist") === "On NOVA's list of trusted sources", "trusted source changed")
      })
      test("a model the user added can be removed from the list (not uninstalled)", function() {
        var added = {catalog_id: "requested-granite3-3-2b-q4-k-m", user_requested: true, display_name: "granite-3.3 2B (Q4_K_M)", source: {upstream_id: "granite3.3:2b"}}
        check(novaPanel.canRemoveRequested(added), "added row not removable")
        check(!novaPanel.canRemoveRequested({catalog_id: "qwen3-8b-q4-k-m", user_requested: false}), "shipped row removable")
        var reads = get("/__requests__").catalog_reads
        novaPanel.removeRequestedModel(added)
        var n = 0; while (novaPanel.addModelBusy && n++ < 200) wait(10); wait(50)
        check(get("/__requests__").removed.slice(-1)[0] === "requested-granite3-3-2b-q4-k-m", "wrong DELETE")
        check(item("addModelStatusText").text === "Removed granite-3.3 2B from your list. Nothing was uninstalled.", "removed text: " + item("addModelStatusText").text)
        check(get("/__requests__").catalog_reads > reads, "catalog not refreshed after removal")
        novaPanel.removeRequestedModel({catalog_id: "requested-gone", user_requested: true, display_name: "Gone"})
        n = 0; while (novaPanel.addModelBusy && n++ < 200) wait(10); wait(50)
        check(item("addModelStatusText").text === "Gone was already gone from your list.", "not found text: " + item("addModelStatusText").text)
      })
      test("an install Ollama re-published: followed when checked, explained when not", function() {
        function propose(id) {
          novaPanel.pendingApproval = null
          novaPanel.proposeCatalogAcquisition(id, "Qwen 3 8B")
          var n = 0; while (novaPanel.acquisitionStatus.indexOf("PREPARING") === 0 && n++ < 200) wait(10)
          wait(50)
        }
        propose("gone-model")
        check(novaPanel.acquisitionStatus === "INSTALL NOT AVAILABLE · OLLAMA NO LONGER OFFERS THIS MODEL", "gone: " + novaPanel.acquisitionStatus)
        check(novaPanel.pendingApproval === null, "gone model reached approval")
        propose("changed-model")
        check(novaPanel.acquisitionStatus === "INSTALL NOT AVAILABLE · OLLAMA CHANGED THIS MODEL; WAIT FOR A NOVA UPDATE", "changed: " + novaPanel.acquisitionStatus)
        propose("repinned-model")
        check(novaPanel.pendingApproval && novaPanel.pendingApproval.impact.indexOf("Ollama updated this model after NOVA's list was made") === 0,
              "repinned approval: " + (novaPanel.pendingApproval && novaPanel.pendingApproval.impact))
        propose("same-model")
        check(novaPanel.pendingApproval && novaPanel.pendingApproval.impact.indexOf("Ollama downloads the model") === 0,
              "unchanged approval: " + (novaPanel.pendingApproval && novaPanel.pendingApproval.impact))
        novaPanel.pendingApproval = null
      })
      console.warn("ADD_MODEL_RESULTS " + JSON.stringify(results))
    }
  }
'''


def main():
    server = fake_nova.server(Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        fixture = preview.scenarios()["installed-no-selection"]
        with tempfile.TemporaryDirectory(prefix="nova-add-model-", dir="/tmp") as raw:
            shell = preview.prepare_config(Path(raw), fixture)
            text = shell.read_text().replace("import QtQuick\n", "import QtQuick\nimport QtTest\n", 1)
            check = (QML_CHECK.replace("@SOCKET@", server.server_address)
                     .replace("@CAPTURE@", os.environ.get("NOVA_ADD_MODEL_CAPTURE", "")))
            shell.write_text(text.rsplit("\n}", 1)[0] + check + "\n}\n")
            result = subprocess.run(["quickshell", "--path", raw], capture_output=True, text=True, timeout=90)
            output = result.stdout + result.stderr
        line = next((row.split("ADD_MODEL_RESULTS ", 1)[1] for row in output.splitlines() if "ADD_MODEL_RESULTS " in row), None)
        if line is None:
            print(output)
            raise SystemExit("Add-model interaction did not complete")
        rows = json.loads(line)
        for row in rows:
            print(("PASS " if row["passed"] else "FAIL ") + row["name"] + ((": " + row["error"]) if not row["passed"] else ""))
        errors = [row for row in output.splitlines() if any(word in row for word in ("ReferenceError", "TypeError", "Binding loop"))]
        if errors or any(not row["passed"] for row in rows):
            print(output)
            raise SystemExit(1)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    main()
