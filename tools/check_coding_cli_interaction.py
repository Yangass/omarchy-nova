#!/usr/bin/env python3
"""NOVA 0.15 through the real Panel.qml against a stub novad: the welcome, SET UP CODING CLI and
its approval, TUNE with its warnings, and an approval the coding CLI asked for. Isolated: a
Unix-socket stub; no production NOVA, model, OpenCode or action.
"""
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import subprocess
import tempfile
from threading import Lock, Thread
from urllib.parse import parse_qs, urlsplit

import fake_nova
import onboarding_preview as preview

GIB = 1024 ** 3
NUMBERS = {"budget_bytes": int(13.6 * GIB), "weights_bytes": int(4.9 * GIB), "runtime_bytes": GIB, "fixed_cache_bytes": 0,
           "kv_bytes_per_token": 184320, "native_context": 40960, "recommended_context": 32768,
           "tight_margin_bytes": int(13.6 * GIB * 0.05), "steps": [4096, 8192, 12288, 16384, 24576, 32768, 40960, 49152, 65536]}
VERDICT = {"level": "fits", "past_native": False, "need_bytes": int(10.4 * GIB), "spare_bytes": int(3.2 * GIB),
           "text": "Fits: 10.4 GiB of 13.6 GiB, 3.2 GiB to spare."}
MODELS = {"models": [
    {"id": "qwen3:8b", "provider": "ollama", "available": True, "resident": False, "metadata": {"size_bytes": 5_200_000_000}},
    {"id": "nova-qwen3-8b-cli:latest", "provider": "ollama", "available": True, "resident": False,
     "metadata": {"size_bytes": 5_200_000_000}, "tailored_for": "qwen3:8b", "tailored_context": 32768}]}
PROFILE = {"id": "opencode-qwen3-8b", "kind": "opencode", "label": "OpenCode", "tool": "opencode", "provider": "ollama",
           "model": "qwen3:8b", "build": "nova-qwen3-8b-cli", "project_label": "~/Code", "project_place": "~/Code",
           "care": "edits", "valid": True, "error": None, "running": False, "available": True, "blocked": None, "context": 32768}

QML_CHECK = r'''
  NovaClient { id: fakeNova; socketPath: "@SOCKET@" }
  TestCase {
    name: "CodingCli"
    when: novaPanel.opened
    function check(value, message) { if (!value) throw new Error(message) }
    function waitUntil(predicate, message, timeout) {
      var elapsed = 0, maximum = timeout || 5000
      while (!predicate() && elapsed < maximum) { wait(40); elapsed += 40 }
      check(predicate(), message)
    }
    function child(name) {
      var item = findChild(novaPanel, name)
      // Model cards are Repeater delegates: look inside the first card too.
      var cards = findChild(novaPanel, "installedModelsRepeater")
      if (!item && cards && cards.count) item = findChild(cards.itemAt(0), name)
      check(item !== null, "missing " + name); return item
    }
    // Pictures for review when NOVA_CAPTURE names a folder (none by default).
    function shot(name) {
      if ("@CAPTURE@" === "") return
      wait(350)  // slider and meter animations settle
      var done = false
      findChild(novaPanel, "panelKeyCatcher").parent.parent.grabToImage(function(image) { image.saveToFile("@CAPTURE@/" + name + ".png"); done = true })
      waitUntil(function() { return done }, "picture " + name, 3000)
    }
    function press(name) { var item = child(name); check(item.visible && item.enabled, name + " not clickable"); item.clicked() }
    function test_flow() {
      var results = []
      function test(name, fn) {
        try { fn(); results.push({name: name, passed: true}) }
        catch (error) { results.push({name: name, passed: false, error: String(error)}) }
      }
      previewHost.acceptancePreview = false
      novaPanel.pendingApproval = null
      novaPanel.onboardingState = "READY"; novaPanel.onboardingActions = ["CHAT"]
      novaPanel.onboardingSnapshotFresh = true; novaPanel.onboardingReceivedAt = Date.now()
      novaPanel.setPanelView("chat")

      test("the welcome names the model, its card and its working context, once", function() {
        waitUntil(function() { return novaPanel.welcomeVisible }, "welcome never showed")
        var context = child("welcomeContext").text
        check(context.indexOf("Working context 32K") === 0, "context line: " + context)
        check(context.indexOf("its maximum is 40K") > 0 && context.indexOf("GiB of graphics memory") > 0, "context detail: " + context)
        check(child("welcomeSetUpCliButton").visible, "no SET UP CODING CLI in the welcome")
        shot("1-welcome")
      })
      test("SET UP CODING CLI in the welcome opens the setup card on the model and marks the welcome seen", function() {
        press("welcomeSetUpCliButton")
        check(!novaPanel.welcomeVisible && novaPanel.welcomeSeen, "welcome still showing")
        novaPanel.applyInstalledModelsResult(200, JSON.stringify(@MODELS@))
        check(novaPanel.discoveredModels.length === 1 && novaPanel.discoveredModels[0].id === "qwen3:8b", "tailored build listed as a model")
        waitUntil(function() { return novaPanel.cliSetupModel === "qwen3:8b" && novaPanel.cliSetupState !== null }, "setup card never loaded")
        check(novaPanel.shownView === "models", "not on MODELS")
        check(child("codingCliSetupCard").visible, "setup card hidden")
        check(novaPanel.cliSetupProject === "~/Code", "first folder not chosen: " + novaPanel.cliSetupProject)
        check(child("codingCliSetupVerdict").text.indexOf("Fits") === 0, "setup verdict missing")
        shot("2-setup")
      })
      test("SET UP asks for one approval that lists exactly what changes", function() {
        press("codingCliCareEdits")
        press("codingCliSetUpButton")
        waitUntil(function() { return novaPanel.pendingApproval !== null }, "no approval")
        check(novaPanel.pendingApproval.coding_cli === true, "not a coding CLI approval")
        check(child("approvalTitle").text === "CODING CLI APPROVAL", "title " + child("approvalTitle").text)
        var changes = child("approvalChanges").text
        check(changes.indexOf("1. Create nova-qwen3-8b-cli") === 0 && changes.indexOf("2. Add OPEN CLI") > 0, "changes: " + changes)
        check(child("approvalVerdict").text.indexOf("Fits") === 0, "approval verdict missing")
        shot("3-approval")
      })
      test("approving sets it up and the card shows the coding CLI", function() {
        novaPanel.decideApproval("approve")
        waitUntil(function() { return novaPanel.pendingApproval === null }, "approval never settled")
        check(novaPanel.approvalFeedback.indexOf("Coding CLI set up") === 0, "feedback: " + novaPanel.approvalFeedback)
        waitUntil(function() { return novaPanel.openCodeProfile({id: "qwen3:8b"}) !== null }, "profile not picked up")
        check(child("tuneCodingCliButton").visible && !child("setUpCodingCliButton").visible, "buttons not switched")
        check(child("codingCliLine").text.indexOf("CODING CLI · OpenCode · ~/Code · 32K") === 0, "line: " + child("codingCliLine").text)
        shot("4-ready")
      })
      test("TUNE warns live and asks before a setting that doesn't fit", function() {
        press("tuneCodingCliButton")
        waitUntil(function() { return novaPanel.cliTuneState !== null }, "tune never loaded")
        check(child("codingCliTuneValue").text === "32K", "start value " + child("codingCliTuneValue").text)
        check(child("codingCliTuneVerdict").text.indexOf("FITS") === 0, "start verdict " + child("codingCliTuneVerdict").text)
        novaPanel.cliTuneIndex = 6  // 40K: the model's maximum, little room left here
        check(child("codingCliTuneVerdict").text.indexOf("TIGHT") === 0, "40K verdict " + child("codingCliTuneVerdict").text)
        novaPanel.cliTuneIndex = 8  // 64K
        var verdict = child("codingCliTuneVerdict").text
        check(verdict.indexOf("DOESN'T FIT") === 0 && verdict.indexOf("PAST WHAT IT WAS TRAINED FOR (40K)") > 0, "64K verdict " + verdict)
        check(child("codingCliSaveButton").text === "SAVE ANYWAY…", "save label " + child("codingCliSaveButton").text)
        shot("5-tune-64k")
        press("codingCliSaveButton")
        check(child("codingCliTuneConfirm").visible, "no are-you-sure")
        check(child("codingCliKeepButton").text === "KEEP 32K", "keep label")
        shot("6-are-you-sure")
        press("codingCliAnywayButton")
        waitUntil(function() { return novaPanel.cliTuneModel === "" }, "save never finished")
        check(novaPanel.cliStatus.indexOf("Working context 64K") === 0, "status " + novaPanel.cliStatus)
      })
      test("the CLI's own request shows, opens the panel, and can take the fitting alternative", function() {
        novaPanel.close(); wait(50)
        novaPanel.considerCodingCliApproval({id: "cli_asked", coding_cli: true, action: "coding_cli_tune", expires_at: Date.now() / 1000 + 120,
          summary: "Your coding CLI asks: working context 32K → 64K", impact: "Only you can, here.",
          changes: ['Its reason: "big repo"', "Rebuild nova-qwen3-8b-cli with a 64K working context (no download)."],
          verdict: {level: "spills", past_native: true, text: "Doesn't fit: 3.1 GiB would spill into system memory."},
          approve_label: "Use 64K anyway", alternative_context: 32768, alternative_label: "Use 32K · fits"})
        check(novaPanel.opened, "panel stayed closed")
        check(novaPanel.pendingApproval && novaPanel.pendingApproval.id === "cli_asked", "request not shown")
        check(child("alternativeApprovalButton").visible && child("alternativeApprovalButton").text === "Use 32K · fits", "no alternative button")
        shot("7-cli-asks")
        novaPanel.considerCodingCliApproval({id: "cli_asked", coding_cli: true})
        check(novaPanel.pendingApproval.id === "cli_asked", "shown twice or dropped")
        novaPanel.decideApproval("alternative")
        waitUntil(function() { return novaPanel.pendingApproval === null }, "alternative never settled")
        check(novaPanel.approvalFeedback.indexOf("Working context is now 32K") === 0, "feedback " + novaPanel.approvalFeedback)
      })
      test("a CLI request answered elsewhere leaves this panel", function() {
        novaPanel.considerCodingCliApproval({id: "cli_other", coding_cli: true, action: "coding_cli_tune", expires_at: Date.now() / 1000 + 120,
          summary: "s", impact: "i", changes: [], verdict: null})
        check(novaPanel.pendingApproval && novaPanel.pendingApproval.id === "cli_other", "not shown")
        novaPanel.considerCodingCliApproval(null)
        check(novaPanel.pendingApproval === null, "stale request kept")
      })
      console.log("CODING_CLI_RESULTS " + JSON.stringify(results))
      Qt.quit()
    }
  }
'''


def main():
    requests, state = [], {"set_up": False}
    lock = Lock()

    class Stub(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def json(self, status, payload):
            body = json.dumps(payload, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path, query = urlsplit(self.path).path, parse_qs(urlsplit(self.path).query)
            with lock: requests.append(("GET", self.path, None))
            if path == "/cli/setup" and query.get("model") == ["qwen3:8b"]:
                self.json(200, {"model": "qwen3:8b", "build": "nova-qwen3-8b-cli", "built": False, "context": 32768,
                                "recommended_context": 32768, "numbers": NUMBERS, "verdict": VERDICT, "gpu": "RTX 5080",
                                "tools": [{"id": "opencode", "label": "OpenCode", "available": True, "downloads_on_first_start": True}],
                                "projects": ["~/Code", "~/Work"], "care": ["ask", "edits"], "profile_id": "opencode-qwen3-8b"})
            elif path == "/cli/tune" and query.get("model") == ["qwen3:8b"]:
                self.json(200, {"model": "qwen3:8b", "build": "nova-qwen3-8b-cli", "built": True, "context": 32768,
                                "recommended_context": 32768, "numbers": NUMBERS, "verdict": VERDICT, "gpu": "RTX 5080"})
            elif path == "/cli/profiles":
                with lock: profiles = [PROFILE] if state["set_up"] else []
                self.json(200, {"configured": state["set_up"], "error": None, "profiles": profiles, "client_active": False})
            elif path == "/models":
                self.json(200, MODELS)
            else:
                self.json(404, {"error": "fixture_unknown"})

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            value = json.loads(body) if body else None
            with lock: requests.append(("POST", self.path, value))
            if self.path == "/cli/setup/proposals":
                self.json(201, {"id": "cli_setup", "coding_cli": True, "action": "coding_cli_setup", "model": "qwen3:8b",
                                "summary": "Set up OpenCode with qwen3:8b in ~/Code", "impact": "NOVA makes exactly these changes.",
                                "changes": ["Create nova-qwen3-8b-cli: qwen3:8b with a 32K working context (no download).",
                                            "Add OPEN CLI for ~/Code to this model's card.", "It edits files freely and asks before it runs commands."],
                                "verdict": VERDICT, "expires_at": __import__("time").time() + 120})
            elif self.path == "/approvals/cli_setup/approve":
                with lock: state["set_up"] = True
                self.json(200, {"status": "set_up", "profile_id": "opencode-qwen3-8b", "build": "nova-qwen3-8b-cli", "context": 32768})
            elif self.path == "/cli/tune":
                self.json(200, {"model": "qwen3:8b", "context": value.get("context"), "verdict": None})
            elif self.path == "/approvals/cli_asked/alternative":
                self.json(200, {"status": "changed", "context": 32768})
            else:
                self.json(404, {"error": "fixture_unknown"})

    with fake_nova.server(Stub) as server:
        Thread(target=server.serve_forever, daemon=True).start()
        with tempfile.TemporaryDirectory(prefix="nova-coding-cli-check-", dir="/tmp") as raw:
            target = Path(raw)
            fixture = preview.scenarios()["verified-awaiting-activation"]
            fixture["label"] = "CODING CLI CHECK // ISOLATED STUB"
            fixture["host"] = {"online": True, "runtime_state": "idle", "active_provider": "ollama", "active_model": "qwen3:8b",
                               "active_available": True, "runtime_ready": True}
            shell_file = preview.prepare_config(target, fixture)
            text = shell_file.read_text().replace("import QtQuick\n", "import QtQuick\nimport QtTest\n", 1)
            text = text.replace('property var novaClient: null', 'property var novaClient: fakeNova')
            check = QML_CHECK.replace("@CAPTURE@", os.environ.get("NOVA_CAPTURE", "")).replace("@SOCKET@", server.server_address).replace("@MODELS@", json.dumps(MODELS))
            text = text.rsplit("\n}", 1)[0] + check + "\n}\n"
            shell_file.write_text(text)
            # Its own state folder: the welcome's "seen" and the saved view never leak to or
            # from the other checks.
            state_home = target / "state"; state_home.mkdir()
            result = subprocess.run(["quickshell", "--path", str(target)], capture_output=True, text=True, timeout=180,
                                    env={**os.environ, "XDG_STATE_HOME": str(state_home)})
            output = result.stdout + result.stderr
        server.shutdown()

    for line in output.splitlines():
        if "DEBUG " in line or "TypeError" in line or "ReferenceError" in line or "Panel.qml" in line: print("  log:", line[-300:])
    marker = "CODING_CLI_RESULTS "
    encoded = next((line.split(marker, 1)[1] for line in output.splitlines() if marker in line), None)
    if encoded is None:
        print(output[-4000:])
        raise SystemExit("Coding CLI harness did not complete")
    failed = False
    for item in json.loads(encoded):
        print(("PASS " if item["passed"] else "FAIL ") + item["name"] + ((": " + item["error"]) if not item["passed"] else ""))
        failed |= not item["passed"]
    if os.environ.get("NOVA_DEBUG"):
        with lock:
            for row in requests: print("  req:", row[0], row[1], str(row[2])[:80])
    with lock:
        setup = [value for method, path, value in requests if method == "POST" and path == "/cli/setup/proposals"]
        tune = [value for method, path, value in requests if method == "POST" and path == "/cli/tune"]
    ok = setup == [{"model": "qwen3:8b", "tool": "opencode", "project": "~/Code", "care": "edits"}]
    print(("PASS " if ok else "FAIL ") + f"the setup request carried exactly the choices made: {setup}")
    failed |= not ok
    ok = tune == [{"model": "qwen3:8b", "context": 65536}]
    print(("PASS " if ok else "FAIL ") + f"TUNE saved exactly the confirmed context: {tune}")
    failed |= not ok
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
