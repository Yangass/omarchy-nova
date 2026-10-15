#!/usr/bin/env python3
"""Model updates (0.14.0, mock previews/model-updates).

Run under a private compositor against an isolated HTTP stub: the UPDATE tag and
the MODELS tab dot, what changed and the download in plain words, skip / show
again, the update approval card (nothing downloads before Approve), progress
while updating, the quiet lines when no update is offered, and REFRESH forcing
a check. No model is downloaded.
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

REQUESTS = {"checks": [], "skips": [], "proposals": []}
MODELS = [{"id": "qwen3:8b", "provider": "ollama", "available": True, "metadata": {"size_bytes": 5225388164},
           "resident": False, "vram_bytes": None},
          {"id": "llama3.2:1b", "provider": "ollama", "available": True, "metadata": {"size_bytes": 1321098329},
           "resident": False, "vram_bytes": None}]
SETTINGS = {"catalog_id": "llama3-2-1b-q8-0", "model_id": "llama3.2:1b", "status": "available", "kind": "settings",
            "revision": "b" * 64, "installed_revision": "a" * 64, "download_bytes": 12288, "download_exact": True,
            "size_before": 1321098329, "size_after": 1321110617, "skipped": False}
WEIGHTS = dict(SETTINGS, kind="weights", download_bytes=1395864371, size_before=1321098329, size_after=1395864371)
STATE = {"llama": SETTINGS, "qwen": None}


def view():
    models = {"llama3.2:1b": STATE["llama"]} if STATE["llama"] else {}
    if STATE["qwen"]: models["qwen3:8b"] = STATE["qwen"]
    available = sum(1 for m in models.values() if m["status"] == "available" and not m["skipped"])
    return {"checked_at": 1000, "failed": False, "available": available, "checking": False, "models": models}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path == "/__requests__":
            self.reply(200, REQUESTS)
        elif self.path.startswith("/__state__/"):
            mode = self.path.rsplit("/", 1)[-1]
            STATE["llama"] = {"settings": SETTINGS, "weights": WEIGHTS, "none": None,
                              "changed": {"catalog_id": "llama3-2-1b-q8-0", "model_id": "llama3.2:1b", "status": "changed_too_much",
                                          "reasons": ["architecture_changed"], "skipped": False},
                              "gone": {"catalog_id": "llama3-2-1b-q8-0", "model_id": "llama3.2:1b", "status": "gone", "skipped": False}}[mode]
            self.reply(200, view())
        elif self.path == "/models/updates":
            REQUESTS.setdefault("headers", []).append(sorted(self.headers.keys()))
            self.reply(200, view())
        elif self.path == "/models":
            self.reply(200, {"models": MODELS, "active_provider": "ollama", "active_model": "qwen3:8b"})
        else:
            self.reply(503, {"error": "fixture_unavailable"})

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.path == "/models/updates/check":
            REQUESTS["checks"].append("forced"); self.reply(202, view()); return
        if self.path.startswith("/models/updates/"):
            catalog_id, verb = self.path[len("/models/updates/"):].rsplit("/", 1)
            REQUESTS["skips"].append(verb)
            STATE["llama"] = dict(STATE["llama"], skipped=verb == "skip")
            self.reply(200, view()); return
        if self.path.endswith("/propose-update"):
            REQUESTS["proposals"].append(self.path)
            self.reply(201, {"id": "acq_update", "expires_at": 4102444800, "upstream": {"status": "repinned"},
                             "preflight": {"intent": "update", "replaces_revision": "a" * 64, "fit_state": "GOOD",
                                           "trust_basis": "backend_allowlist", "disk": {"total_required": 3 << 30, "free_space": 22 << 30},
                                           "descriptor": {"catalog_id": "llama3-2-1b-q8-0", "provider": "ollama",
                                                          "artifact_id": "llama3.2:1b", "expected_artifact_size": 1321110617,
                                                          "provenance_summary": []}}}); return
        self.reply(404, {"error": "fixture_path"})

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
    name: "ModelUpdatesInteraction"
    when: novaPanel.opened
    function check(value, message) { if (!value) throw new Error(message) }
    function get(path) {
      var xhr = fakeNova.request(), complete = false, result = null
      xhr.onreadystatechange = function() { if (xhr.readyState === XMLHttpRequest.DONE) { result = xhr.responseText; complete = true } }
      xhr.open("GET", path); xhr.send()
      var n = 0; while (!complete && n++ < 100) wait(10)
      return JSON.parse(result)
    }
    function row(index) { if (!novaPanel.pendingApproval) novaPanel.panelView = "models"; return findChild(novaPanel, "installedModelsRepeater").itemAt(index) }
    function child(index, name) { var r = findChild(row(index), name); check(r !== null, name + " missing"); return r }
    function dot() { return findChild(findChild(novaPanel, "viewTabRepeater").itemAt(1), "modelsUpdateDot") }
    function state(mode) { novaPanel.applyModelUpdates(get("/__state__/" + mode)); wait(80) }
    function capture(name) {
      if (!"@CAPTURE@") return
      var done = false; findChild(novaPanel, "panelKeyCatcher").grabToImage(function(i) { i.saveToFile("@CAPTURE@/" + name + ".png"); done = true })
      var n = 0; while (!done && n++ < 100) wait(20)
    }
    function test_model_updates() {
      var results = []
      function test(name, fn) {
        try { fn(); results.push({name:name, passed:true}) }
        catch (e) { results.push({name:name, passed:false, error:String(e)}) }
      }
      previewHost.novaClient = fakeNova
      previewHost.acceptancePreview = false
      novaPanel.panelView = "models"; novaPanel.modelsUnavailable = false; novaPanel.installedModelsLoading = false
      novaPanel.discoveredModels = @MODELS@
      wait(200)
      test("an offered update shows an UPDATE tag, what changed in plain words, and a dot on MODELS", function() {
        state("settings")
        check(child(1, "modelUpdateTag").visible && !child(0, "modelUpdateTag").visible, "tag on the wrong card")
        check(child(1, "modelUpdateBlock").visible && !child(0, "modelUpdateBlock").visible, "update block on the wrong card")
        check(child(1, "modelUpdateWhat").text === "Ollama has a newer build: the chat template and settings changed, the model weights are the same.", "what: " + child(1, "modelUpdateWhat").text)
        check(child(1, "modelUpdateFacts").text === "DOWNLOAD about 12 KB", "facts: " + child(1, "modelUpdateFacts").text)
        check(dot().visible, "no MODELS dot")
        check(child(1, "modelUpdateButton").text === "UPDATE" && child(1, "modelUpdateButton").enabled, "UPDATE button")
        capture("update-settings")
      })
      test("new weights say so, with an upper bound and the size change", function() {
        state("weights")
        check(child(1, "modelUpdateWhat").text.indexOf("new model weights") > 0, "what: " + child(1, "modelUpdateWhat").text)
        check(child(1, "modelUpdateFacts").text === "DOWNLOAD about 1.3 GiB · SIZE 1.2 GiB → 1.3 GiB", "facts: " + child(1, "modelUpdateFacts").text)
      })
      test("skip hides the offer, keeps a quiet line, and show again brings it back", function() {
        state("settings")
        var skip = child(1, "modelUpdateSkip")
        mouseClick(skip); wait(250)
        check(get("/__requests__").skips.join(",") === "skip", "skip not sent: " + JSON.stringify(get("/__requests__").skips))
        check(!child(1, "modelUpdateTag").visible && !dot().visible, "still offered after skip")
        check(child(1, "modelUpdateQuiet").text === "Newer build skipped." && child(1, "modelUpdateShowAgain").visible, "skipped line")
        mouseClick(child(1, "modelUpdateShowAgain")); wait(250)
        check(child(1, "modelUpdateTag").visible, "show again did not bring it back")
      })
      test("UPDATE opens the update approval; nothing downloads before Approve", function() {
        state("settings")
        mouseClick(child(1, "modelUpdateButton")); var n = 0; while (!novaPanel.pendingApproval && n++ < 200) wait(10)
        var a = novaPanel.pendingApproval
        check(a && a.update === true && a.acquisition === true && a.id === "acq_update", "no update approval")
        check(get("/__requests__").proposals[0].indexOf("/acquisition/models/llama3-2-1b-q8-0/propose-update") >= 0, "wrong proposal")
        check(findChild(novaPanel, "approvalTitle").text === "UPDATE APPROVAL", "title: " + findChild(novaPanel, "approvalTitle").text)
        check(a.summary === "Update llama3.2:1b to Ollama's newer build" || a.summary.indexOf("Update ") === 0, "summary: " + a.summary)
        var line = findChild(novaPanel, "approvalSummaryLine").text
        check(line.indexOf("What changed: chat template and settings (same weights) · download about 12 KB") === 0, "summary line: " + line)
        check(novaPanel.trackedAcquisitionId === "", "download started before approval")
        capture("update-approval")
        novaPanel.clearPendingApproval(); wait(50)
      })
      test("while updating, the card says so and the current build keeps working", function() {
        state("weights")
        novaPanel.acquisitionByCatalog = ({ "llama3-2-1b-q8-0": { state: "DOWNLOADING", preflight: { intent: "update", descriptor: { catalog_id: "llama3-2-1b-q8-0" } },
          progress: { current_blob_index: 1, current_blob_completed_bytes: 536870912, current_blob_total_bytes: 1395864371 } } })
        wait(80)
        var progress = child(1, "modelUpdateProgress")
        check(progress.visible && progress.text.indexOf("Updating to the newer build · 0.5 GiB / 1.3 GiB") === 0, "progress: " + progress.text)
        check(progress.text.indexOf("The current build keeps working until this finishes.") > 0, "keeps-working note")
        check(!child(1, "modelUpdateButton").visible, "UPDATE still offered while updating")
        check(novaPanel.acquisitionLifecycleStatus(novaPanel.acquisitionByCatalog["llama3-2-1b-q8-0"]).indexOf("UPDATING // ") === 0, "status strip wording")
        novaPanel.acquisitionByCatalog = ({}); wait(50)
      })
      test("no offer when Ollama changed the model too much or dropped it; a quiet line says why", function() {
        state("changed")
        check(!child(1, "modelUpdateTag").visible && !dot().visible, "offered anyway")
        check(child(1, "modelUpdateQuiet").text.indexOf("Ollama changed this model a lot") === 0, "changed: " + child(1, "modelUpdateQuiet").text)
        state("gone")
        check(child(1, "modelUpdateQuiet").text === "Ollama no longer offers this model. Your copy keeps working.", "gone: " + child(1, "modelUpdateQuiet").text)
        state("none")
        check(!child(1, "modelUpdateBlock").visible, "block without a result")
      })
      test("a refused update says why", function() {
        check(novaPanel.modelUpdateRefusal({ status: "BLOCKED", blockers: ["INSUFFICIENT_STORAGE"] }) === "NOT ENOUGH FREE DISK SPACE", "storage")
        check(novaPanel.modelUpdateRefusal({ error: "stale_or_blocked_proposal" }) === "STALE OR BLOCKED PROPOSAL", "unknown code")
        check(novaPanel.modelUpdateRefusal(null) === "NOVA'S SERVICE DIDN'T ANSWER", "no answer")
      })
      test("requests reach NOVA over its socket and carry no key", function() {
        var before = (get("/__requests__").headers || []).length
        novaPanel.refreshModelUpdates(false); wait(250)
        var headers = get("/__requests__").headers || []
        check(headers.length === before + 1, "the panel's request did not arrive")
        check(headers[headers.length - 1].map(function(h) { return h.toLowerCase() }).indexOf("x-nova-key") < 0, "a key was sent: " + JSON.stringify(headers[headers.length - 1]))
      })
      test("REFRESH also asks Ollama's library for newer builds", function() {
        var before = get("/__requests__").checks.length
        mouseClick(findChild(novaPanel, "refreshInstalledModelsButton")); wait(250)
        check(get("/__requests__").checks.length === before + 1, "REFRESH did not force a check")
      })
      console.warn("MODEL_UPDATES_RESULTS " + JSON.stringify(results))
    }
  }
'''


def main():
    server = fake_nova.server(Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        fixture = preview.scenarios()["installed-no-selection"]
        with tempfile.TemporaryDirectory(prefix="nova-updates-", dir="/tmp") as raw:
            shell = preview.prepare_config(Path(raw), fixture)
            text = shell.read_text().replace("import QtQuick\n", "import QtQuick\nimport QtTest\n", 1)
            check = (QML_CHECK.replace("@SOCKET@", server.server_address).replace("@MODELS@", json.dumps(MODELS))
                     .replace("@CAPTURE@", os.environ.get("NOVA_UPDATES_CAPTURE", "")))
            shell.write_text(text.rsplit("\n}", 1)[0] + check + "\n}\n")
            result = subprocess.run(["quickshell", "--path", raw], capture_output=True, text=True, timeout=90)
            output = result.stdout + result.stderr
        line = next((row.split("MODEL_UPDATES_RESULTS ", 1)[1] for row in output.splitlines() if "MODEL_UPDATES_RESULTS " in row), None)
        if line is None:
            print(output)
            raise SystemExit("Model updates interaction did not complete")
        rows = json.loads(line)
        for row in rows:
            print(("PASS " if row["passed"] else "FAIL ") + row["name"] + ((": " + row["error"]) if not row["passed"] else ""))
        errors = [row for row in output.splitlines() if any(word in row for word in ("ReferenceError", "TypeError", "Binding loop", "Unable to assign [undefined]"))]
        if errors or any(not row["passed"] for row in rows):
            print(output)
            raise SystemExit(1)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    main()
