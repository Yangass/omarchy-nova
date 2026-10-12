#!/usr/bin/env python3
"""Delete an installed model from this computer (2026-10-08, QWEN-11 contract).

Run under a private compositor against an isolated HTTP stub: the red trash icon
with its tooltip, a removal approval card worded for deletion, and the results
(deleted, blocked, failed). No model is deleted.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
from threading import Thread

import onboarding_preview as preview

REQUESTS = {"proposed": [], "decided": [], "model_reads": 0}
MODE = {"approve": "deleted"}
MODELS = [{"id": "qwen3:8b", "provider": "ollama", "available": True, "metadata": {"size_bytes": 5225388164},
           "resident": False, "vram_bytes": None},
          {"id": "qwen3:14b", "provider": "ollama", "available": True, "metadata": {"size_bytes": 9276198565},
           "resident": True, "vram_bytes": 10737418240}]
UNLOAD = {"mode": "unloaded"}
RESIDENT = {"qwen3:14b": True}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path == "/__requests__":
            self.reply(200, REQUESTS)
        elif self.path.startswith("/__unload__/"):
            UNLOAD["mode"] = self.path.rsplit("/", 1)[-1]; self.reply(200, UNLOAD)
        elif self.path.startswith("/__resident__/"):
            RESIDENT["qwen3:14b"] = self.path.endswith("/1"); self.reply(200, RESIDENT)
        elif self.path.startswith("/__approve__/"):
            MODE["approve"] = self.path.rsplit("/", 1)[-1]; self.reply(200, MODE)
        elif self.path == "/models":
            REQUESTS["model_reads"] += 1
            models = [dict(m, resident=RESIDENT.get(m["id"], m["resident"])) for m in MODELS]
            self.reply(200, {"models": models, "active_provider": "ollama", "active_model": "qwen3:8b"})
        else:
            self.reply(503, {"error": "fixture_unavailable"})

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.path.startswith("/models/installed/") and self.path.endswith("/remove"):
            model = self.path[len("/models/installed/"):-len("/remove")].replace("%3A", ":")
            REQUESTS["proposed"].append(model)
            if model == "qwen3:8b":
                self.reply(409, {"status": "blocked", "model": model, "blockers": ["SELECTED_MODEL", "LOADED_IN_MEMORY"]}); return
            proposal = {"status": "proposed", "id": "del_fixture", "model": model, "size_bytes": 9276198565,
                        "digest": "abc", "expires_at": 4102444800}
            if MODE["approve"] == "shared":  # 2026-10-12: built on another model, shares its files
                proposal.update(frees_bytes=500, shared_with=["nova-code-qwen38-96k:latest"])
            self.reply(201, proposal); return
        if self.path.startswith("/models/installed/") and self.path.endswith("/unload"):
            model = self.path[len("/models/installed/"):-len("/unload")].replace("%3A", ":")
            REQUESTS.setdefault("unloaded", []).append(model)
            mode = UNLOAD["mode"]
            if mode == "busy": self.reply(409, {"status": "busy"}); return
            if mode == "blocked": self.reply(409, {"status": "blocked", "blockers": ["CLI_SESSION_RUNNING"]}); return
            RESIDENT[model] = False
            if mode == "cpu":  # QWEN-16 contract on a machine without a dedicated GPU
                self.reply(200, {"status": "unloaded", "model": model, "freed_bytes": 1610612736, "freed_vram_bytes": 0}); return
            if mode == "cpu-old":  # before QWEN-16: size_vram only, 0 in RAM
                self.reply(200, {"status": "unloaded", "model": model, "freed_bytes": 0}); return
            self.reply(200, {"status": "unloaded", "model": model, "freed_bytes": 10737418240}); return
        if self.path.startswith("/approvals/del_fixture/"):
            REQUESTS["decided"].append(self.path.rsplit("/", 1)[-1])
            if self.path.endswith("/deny"):
                self.reply(202, {"status": "denied"}); return
            outcome = MODE["approve"]
            if outcome == "shared":
                self.reply(200, {"status": "deleted", "model": "qwen3:14b", "freed_bytes": 0, "size_bytes": 9276198565,
                                 "shared_with": ["nova-code-qwen38-96k:latest"]}); return
            if outcome == "deleted":
                self.reply(200, {"status": "deleted", "model": "qwen3:14b", "freed_bytes": 8589934592, "size_bytes": 9276198565}); return
            if outcome == "blocked":
                self.reply(409, {"status": "blocked", "blockers": ["CLI_SESSION_RUNNING"]}); return
            self.reply(502, {"status": "failed", "error": "delete_failed"}); return
        self.reply(404, {"error": "fixture_path"})

    def reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


QML_CHECK = r'''
  TestCase {
    name: "RemovalInteraction"
    when: novaPanel.opened
    function check(value, message) { if (!value) throw new Error(message) }
    function get(path) {
      var xhr = new XMLHttpRequest(), complete = false, result = null
      xhr.onreadystatechange = function() { if (xhr.readyState === XMLHttpRequest.DONE) { result = xhr.responseText; complete = true } }
      xhr.open("GET", previewHost.api + path); xhr.send()
      var n = 0; while (!complete && n++ < 100) wait(10)
      return JSON.parse(result)
    }
    // UX Phase 2: an approval moves the panel to CHAT; the rows live in MODELS.
    function row(index) { if (!novaPanel.pendingApproval) novaPanel.panelView = "models"; var rows = findChild(novaPanel, "installedModelsRepeater"); return rows.itemAt(index) }
    function child(index, name) { var r = findChild(row(index), name); check(r !== null, name + " missing"); return r }
    function rowNote(index) { var t = child(index, "removalStatusText"); return t.visible ? t.text : "" }
    function lastChat() {
      var lines = findChild(novaPanel, "chatHistoryRepeater"); check(lines !== null && lines.count > 0, "no chat lines")
      var line = lines.itemAt(lines.count - 1); return line.role + " | " + line.text
    }
    function settle() { var n = 0; while (novaPanel.removalBusy && n++ < 200) wait(10); wait(80) }
    function test_removal() {
      var results = []
      function test(name, fn) {
        try { fn(); results.push({name:name, passed:true}) }
        catch (e) { results.push({name:name, passed:false, error:String(e)}) }
      }
      previewHost.api = "http://127.0.0.1:@PORT@"
      previewHost.acceptancePreview = false
      novaPanel.panelView = "models"; novaPanel.modelsUnavailable = false
      novaPanel.discoveredModels = @MODELS@
      wait(200)
      test("each installed model has a red trash icon whose tooltip says what happens", function() {
        var icon = child(1, "deleteModelButton")
        check(icon.visible && icon.iconText !== "" && icon.text === undefined, "not an icon button")
        check(Qt.colorEqual(icon.foreground, novaPanel.destructiveColor), "not the destructive colour")
        var c = novaPanel.destructiveColor
        check((c.hslHue <= 20 / 360 || c.hslHue >= 340 / 360) && c.hslSaturation > 0.3, "destructive colour is not red: " + c)
        var details = findChild(row(1), "installedModelDetailsButton")
        // UX Phase 2 step 2: the icons sit at the row's right edge, after the buttons.
        var iconX = icon.mapToItem(row(1), 0, 0).x, detailsEnd = details.mapToItem(row(1), details.width, 0).x
        check(icon.width > 0 && iconX >= detailsEnd, "icon overlaps DETAILS: " + iconX + " vs " + detailsEnd)
        check(icon.mapToItem(row(1), icon.width, 0).x >= row(1).width - Style.spacing.sm - 1, "icons not at the right edge")
        check(icon.tooltipText === "Delete qwen3:14b from this computer · frees up to 8.6 GiB (less if another model shares its files) · asks for approval first", "tooltip: " + icon.tooltipText)
        if ("@CAPTURE@") { var done = false; row(1).grabToImage(function(i) { i.saveToFile("@CAPTURE@/installed-row.png"); done = true }); var n = 0; while (!done && n++ < 100) wait(20) }
      })
      test("a blocked model explains why and opens no approval", function() {
        novaPanel.proposeModelRemoval(@MODELS@[0]); settle()
        check(novaPanel.pendingApproval === null, "approval opened for a blocked model")
        var why = "Not deleted: it's NOVA's selected model (choose another first); it's loaded in GPU memory (it unloads 10 minutes after its last use)."
        check(rowNote(0) === why, "blocked row: " + rowNote(0))
        check(lastChat() === "system | qwen3:8b: " + why, "blocked chat: " + lastChat())
      })
      test("a removable model asks for approval, worded for deletion", function() {
        novaPanel.pendingApproval = { id: "act_fixture", summary: "OPEN", target: "t", process: "p", pid: 7, owner: "o", application: "a", workspace: 2 }
        wait(50); novaPanel.pendingApproval = null; wait(50)
        novaPanel.proposeModelRemoval(@MODELS@[1]); settle()
        var a = novaPanel.pendingApproval
        check(a && a.removal === true && a.id === "del_fixture", "no removal approval")
        check(findChild(novaPanel, "approvalTitle").text === "DELETE APPROVAL", "title: " + findChild(novaPanel, "approvalTitle").text)
        check(a.summary === "DELETE QWEN3:14B FROM THIS COMPUTER", "summary: " + a.summary)
        check(findChild(novaPanel, "approvalSummaryLine").text === "Frees up to 8.6 GiB (less if another model shares its files) · you can download it again later", "line: " + findChild(novaPanel, "approvalSummaryLine").text)
        // 2026-10-09: visible bindings like "pendingApproval && pendingApproval.target" gave
        // undefined, the assignment failed and lines of other card kinds stayed visible.
        var shown = [], kids = findChild(novaPanel, "approvalTitle").parent.children
        for (var i = 0; i < kids.length; i++) if (kids[i].visible && kids[i].text !== undefined) shown.push(kids[i].text)
        check(shown.every(function(t) { return t.indexOf("undefined") < 0 && !/^(TARGET|PROCESS|PID|OWNER|APPLICATION|WORKSPACE)/.test(t) }), "stray card lines: " + JSON.stringify(shown))
      })
      test("approving deletes, reports the freed space and re-reads the models", function() {
        var reads = get("/__requests__").model_reads
        // Run 6 (2026-10-10): the finished install's strip outlived the deleted model.
        novaPanel.acquisitionStatus = "INSTALLED AND CHECKED // QWEN3 14B // NOT IN USE YET"
        novaPanel.decideApproval("approve"); wait(400)
        check(novaPanel.pendingApproval === null, "card stayed")
        check(novaPanel.approvalFeedback === "", "feedback stayed: " + novaPanel.approvalFeedback)
        check(lastChat() === "system | Deleted qwen3:14b · freed 8.0 GiB.", "deleted chat: " + lastChat())
        check(rowNote(1) === "", "row note on a deleted model: " + rowNote(1))
        check(get("/__requests__").model_reads > reads, "models not refreshed")
        check(novaPanel.acquisitionStatus === "", "install strip outlived the delete: " + novaPanel.acquisitionStatus)
      })
      test("a blocker at approval time and a failed delete are said plainly", function() {
        get("/__approve__/blocked"); novaPanel.proposeModelRemoval(@MODELS@[1]); settle(); novaPanel.decideApproval("approve"); wait(400)
        check(rowNote(1) === "Not deleted: a coding CLI is using it (close it first).", "blocked late row: " + rowNote(1))
        check(lastChat() === "system | qwen3:14b: Not deleted: a coding CLI is using it (close it first).", "blocked late chat: " + lastChat())
        check(novaPanel.approvalFeedback === "", "feedback stayed: " + novaPanel.approvalFeedback)
        get("/__approve__/failed"); novaPanel.proposeModelRemoval(@MODELS@[1]); settle(); novaPanel.decideApproval("approve"); wait(400)
        check(lastChat() === "system | Ollama couldn't delete qwen3:14b. Nothing was removed.", "failed chat: " + lastChat())
        check(rowNote(1) === "", "failed row note: " + rowNote(1))
      })
      test("declining deletes nothing", function() {
        novaPanel.proposeModelRemoval(@MODELS@[1]); settle(); novaPanel.decideApproval("deny"); wait(300)
        check(get("/__requests__").decided.slice(-1)[0] === "deny" && novaPanel.pendingApproval === null, "deny not sent")
        check(lastChat() === "system | Kept qwen3:14b. Nothing was deleted.", "deny chat: " + lastChat())
        check(novaPanel.approvalFeedback === "", "feedback stayed: " + novaPanel.approvalFeedback)
      })
      test("a model in GPU memory says so and offers an eject icon", function() {
        check(child(1, "installedModelStatusText").text.indexOf("IN GPU MEMORY") > 0, "status: " + child(1, "installedModelStatusText").text)
        check(child(0, "installedModelStatusText").text.indexOf("IN GPU MEMORY") < 0, "non-resident marked")
        var eject = child(1, "unloadModelButton")
        check(eject.visible && eject.iconText === "⏏" && !child(0, "unloadModelButton").visible, "eject icon visibility")
        check(eject.tooltipText === "Free 10.0 GiB of GPU memory now · qwen3:14b loads again when it's next used", "tooltip: " + eject.tooltipText)
      })
      test("unloading frees GPU memory and says so; busy and CLI are explained", function() {
        get("/__unload__/unloaded"); novaPanel.unloadModel(@MODELS@[1]); settle()
        check(get("/__requests__").unloaded.slice(-1)[0] === "qwen3:14b", "wrong unload request")
        check(lastChat() === "system | Freed 10.0 GiB of GPU memory. qwen3:14b loads again when it's next used.", "unloaded chat: " + lastChat())
        check(rowNote(1) === "Freed 10.0 GiB of GPU memory. qwen3:14b loads again when it's next used.", "unloaded row note: " + rowNote(1))
        get("/__unload__/busy"); novaPanel.unloadModel(@MODELS@[1]); settle()
        check(rowNote(1) === "NOVA is busy right now; try again when it's idle.", "busy row: " + rowNote(1))
        check(lastChat() === "system | qwen3:14b: NOVA is busy right now; try again when it's idle.", "busy chat: " + lastChat())
        get("/__unload__/blocked"); novaPanel.unloadModel(@MODELS@[1]); settle()
        check(rowNote(1) === "Not unloaded: a coding CLI is using it (close it first).", "blocked row: " + rowNote(1))
        check(lastChat() === "system | qwen3:14b: Not unloaded: a coding CLI is using it (close it first).", "blocked chat: " + lastChat())
      })
      test("run 6: the unload note clears once the model is back in memory", function() {
        get("/__unload__/unloaded"); novaPanel.unloadModel(@MODELS@[1]); settle()
        novaPanel.refreshModels(); wait(300)
        check(rowNote(1).indexOf("Freed 10.0 GiB") === 0, "note gone too early: " + rowNote(1))
        get("/__resident__/1"); novaPanel.refreshModels(); wait(300)
        check(rowNote(1) === "", "note stayed after reload: " + rowNote(1))
        check(child(1, "installedModelStatusText").text.indexOf("IN GPU MEMORY") > 0, "status: " + child(1, "installedModelStatusText").text)
      })
      test("run 6: without a GPU it says memory, never 'Freed 0.0 GiB of GPU memory'", function() {
        previewHost.gpuAvailable = false; wait(50)
        get("/__resident__/1"); novaPanel.refreshModels(); wait(300)
        check(child(1, "installedModelStatusText").text.indexOf("IN MEMORY (unloads") > 0, "cpu status: " + child(1, "installedModelStatusText").text)
        check(child(1, "unloadModelButton").tooltipText === "Free 10.0 GiB of memory now · qwen3:14b loads again when it's next used", "cpu tooltip: " + child(1, "unloadModelButton").tooltipText)
        get("/__unload__/cpu"); novaPanel.unloadModel(@MODELS@[1]); settle()
        check(lastChat() === "system | Freed 1.5 GiB of memory. qwen3:14b loads again when it's next used.", "cpu chat: " + lastChat())
        get("/__resident__/1"); novaPanel.refreshModels(); wait(300)
        get("/__unload__/cpu-old"); novaPanel.unloadModel(@MODELS@[1]); settle()
        check(lastChat() === "system | Memory freed. qwen3:14b loads again when it's next used.", "cpu-old chat: " + lastChat())
        previewHost.gpuAvailable = true; get("/__resident__/1"); novaPanel.refreshModels(); wait(300)
      })
      test("run 6: download progress replaces 'being checked'; loading clears the install strip", function() {
        novaPanel.approvalFeedback = novaPanel.installCheckingFeedback
        novaPanel.applyTrackedAcquisition({ id: "acq_fixture", state: "DOWNLOADING", preflight: { descriptor: { catalog_id: "llama3.2-1b" } } })
        check(novaPanel.approvalFeedback === "", "being-checked line stayed: " + novaPanel.approvalFeedback)
        novaPanel.acquisitionStatus = "INSTALLED AND CHECKED // LLAMA 3.2 1B // NOT IN USE YET"
        novaPanel.approvalFeedback = "Download finished and checked. Choose LOAD AND USE to start it."
        novaPanel.clearFinishedInstallStatus()
        check(novaPanel.acquisitionStatus === "" && novaPanel.approvalFeedback === "", "finished install kept")
        novaPanel.acquisitionStatus = "INSTALLATION FAILED // LLAMA 3.2 1B"; novaPanel.clearFinishedInstallStatus()
        check(novaPanel.acquisitionStatus === "INSTALLATION FAILED // LLAMA 3.2 1B", "a failure must stay")
        novaPanel.acquisitionStatus = ""; novaPanel.onboardingState = "CHECKING"
      })
      test("a result during a streaming answer goes above it and the answer keeps streaming", function() {
        novaPanel.addChat("assistant", "", [], "streaming"); novaPanel.appendAssistantText("Part one")
        get("/__unload__/unloaded"); novaPanel.unloadModel(@MODELS@[1]); settle()
        novaPanel.appendAssistantText(", part two")
        check(lastChat() === "assistant | Part one, part two", "stream broken: " + lastChat())
        var lines = findChild(novaPanel, "chatHistoryRepeater"), prev = lines.itemAt(lines.count - 2)
        check(prev.role === "system" && prev.text.indexOf("Freed 10.0 GiB") === 0, "system line not above: " + prev.role + " " + prev.text)
        check(prev.canCopy === false, "system line offers copy")
      })
      test("a model that shares its files says what a delete really frees and who keeps them", function() {
        var models = JSON.parse(JSON.stringify(@MODELS@))
        models[1].delete_frees_bytes = 500; models[1].shares_files_with = ["nova-code-qwen38-96k:latest"]
        novaPanel.discoveredModels = models; wait(80)
        check(child(1, "deleteModelButton").tooltipText === "Delete qwen3:14b from this computer · frees 1 KB (its main files stay: nova-code-qwen38-96k uses them too) · asks for approval first",
              "tooltip: " + child(1, "deleteModelButton").tooltipText)
        get("/__approve__/shared"); novaPanel.proposeModelRemoval(models[1]); settle()
        check(findChild(novaPanel, "approvalSummaryLine").text === "Frees 1 KB (its main files stay: nova-code-qwen38-96k uses them too) · you can download it again later",
              "line: " + findChild(novaPanel, "approvalSummaryLine").text)
        novaPanel.decideApproval("approve"); wait(400)
        var lines = findChild(novaPanel, "chatHistoryRepeater"), found = false
        for (var i = 0; i < lines.count; i++) {
          var line = lines.itemAt(i)
          if (line.role === "system" && line.text === "Deleted qwen3:14b · freed 1 KB · its main files stay because nova-code-qwen38-96k uses them.") found = true
        }
        check(found, "shared result missing; last: " + lastChat())
        get("/__approve__/deleted"); novaPanel.discoveredModels = @MODELS@; wait(80)
      })
      console.warn("REMOVAL_RESULTS " + JSON.stringify(results))
    }
  }
'''


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        fixture = preview.scenarios()["installed-no-selection"]
        with tempfile.TemporaryDirectory(prefix="nova-removal-", dir="/tmp") as raw:
            shell = preview.prepare_config(Path(raw), fixture)
            text = shell.read_text().replace("import QtQuick\n", "import QtQuick\nimport QtTest\n", 1)
            check = (QML_CHECK.replace("@PORT@", str(server.server_port)).replace("@MODELS@", json.dumps(MODELS))
                     .replace("@CAPTURE@", os.environ.get("NOVA_REMOVAL_CAPTURE", ""))
                     .replace("@CAPTURE@", os.environ.get("NOVA_REMOVAL_CAPTURE", "")))
            shell.write_text(text.rsplit("\n}", 1)[0] + check + "\n}\n")
            result = subprocess.run(["quickshell", "--path", raw], capture_output=True, text=True, timeout=90)
            output = result.stdout + result.stderr
        line = next((row.split("REMOVAL_RESULTS ", 1)[1] for row in output.splitlines() if "REMOVAL_RESULTS " in row), None)
        if line is None:
            print(output)
            raise SystemExit("Removal interaction did not complete")
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
