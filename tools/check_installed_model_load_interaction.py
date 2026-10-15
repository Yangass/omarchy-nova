#!/usr/bin/env python3
"""Click the canonical installed-row load control against an isolated HTTP stub."""
import json
from http.server import BaseHTTPRequestHandler
from pathlib import Path
import subprocess
import tempfile
from threading import Thread
import time

import fake_nova
import onboarding_preview as preview


REQUESTS = []
MODE = {"value": "ready"}
STATE = {"selected": "", "onboarding": "ready"}
ALIAS = "external-code-alias:latest"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path.startswith("/__mode__/"):
            MODE["value"] = self.path.rsplit("/", 1)[-1]
            self.reply(200, {"mode": MODE["value"]})
        elif self.path.startswith("/__selection__/"):
            STATE["selected"] = "" if self.path.endswith("/none") else "prior:latest"
            self.reply(200, {"selected": STATE["selected"]})
        elif self.path.startswith("/__onboarding__/"):
            STATE["onboarding"] = self.path.rsplit("/", 1)[-1]
            self.reply(200, {"onboarding": STATE["onboarding"]})
        elif self.path == "/__requests__":
            self.reply(200, {"requests": REQUESTS})
        elif self.path == "/models":
            self.reply(200, {"models": [{"id": ALIAS, "provider": "ollama", "available": True}]})
        elif self.path == "/onboarding/state":
            selected = STATE["selected"]  # freeze identity before any delayed reply
            behavior = STATE["onboarding"]
            if behavior == "slow_old":
                time.sleep(.45)
            if behavior == "unavailable":
                self.reply(503, {"error": "fixture_unavailable"})
                return
            if behavior == "invalid_ready":
                self.reply(200, {"schema_version": 1, "primary_state": "READY",
                                 "allowed_actions": ["SEND_FIRST_MESSAGE"],
                                 "configuration": {"explicit_selection": False}})
                return
            self.reply(200, {"schema_version": 1,
                             "primary_state": "READY" if selected else "NEEDS_ACTIVATION",
                             "allowed_actions": ["SEND_FIRST_MESSAGE"] if selected else ["CHOOSE_INSTALLED_MODEL"],
                             "configuration": {"explicit_selection": bool(selected),
                                               "provider": "ollama" if selected else "",
                                               "model": selected}})
        else:
            self.reply(503, {"error": "fixture_unavailable"})

    def do_POST(self):
        if self.path != "/model/active":
            self.reply(404, {"error": "fixture_path"})
            return
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        try:
            REQUESTS.append(json.loads(raw))
        except ValueError:
            REQUESTS.append({"invalid": True})
        mode = MODE["value"]
        if mode == "slow_success":
            time.sleep(.35)
        if mode == "missing":
            self.reply(404, {"error": "model_not_installed"})
        elif mode == "failed":
            self.reply(503, {"error": "model_switch_failed"})
        elif mode == "malformed":
            self.reply(200, {"status": "model_ready"})
        else:
            if mode in ("ready", "slow_success"):
                STATE["selected"] = ALIAS
            self.reply(200, {"provider": "ollama", "model": "wrong:latest" if mode == "wrong" else ALIAS,
                             "available": True, "runtime_ready": True, "status": "model_ready"})

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
    name: "InstalledModelLoadInteraction"
    when: novaPanel.opened
    function check(value, message) { if (!value) throw new Error(message) }
    function rowButton() {
      var rows = findChild(novaPanel, "installedModelsRepeater")
      check(rows && rows.count === 1, "installed row missing")
      var button = findChild(rows.itemAt(0), "loadInstalledModelButton")
      check(button !== null, "load button missing")
      return button
    }
    function click(button) {
      var list = findChild(novaPanel, "modelList")
      var top = button.mapToItem(list.contentItem, 0, 0).y
      list.contentY = Math.max(0, Math.min(list.contentHeight - list.height, top - 12))
      wait(60)
      var catcher = findChild(novaPanel, "panelKeyCatcher")
      var point = button.mapToItem(catcher, button.width / 2, button.height / 2)
      mouseClick(catcher, point.x, point.y, Qt.LeftButton)
    }
    function fixture(path) {
      var xhr = fakeNova.request(), complete = false
      xhr.onreadystatechange = function() { if (xhr.readyState === XMLHttpRequest.DONE) complete = true }
      xhr.open("GET", path)
      xhr.send()
      var n = 0; while (!complete && n++ < 100) wait(10)
      check(complete, "fixture request failed: " + path)
    }
    function mode(value) { fixture("/__mode__/" + value) }
    function test_installed_load() {
      var results = []
      function test(name, fn) {
        try { fn(); results.push({name:name, passed:true}) }
        catch (e) { results.push({name:name, passed:false, error:String(e)}) }
      }
      previewHost.novaClient = fakeNova
      previewHost.acceptancePreview = false
      novaPanel.panelView = "models"
      novaPanel.onboardingSnapshotFresh = true
      novaPanel.onboardingExplicitSelection = false
      novaPanel.modelsUnavailable = false
      novaPanel.discoveredModels = [{id:"external-code-alias:latest", provider:"ollama", available:true}]
      wait(100)
      test("alias click dispatches exact identity once and confirms matching ready", function() {
        var button = rowButton()
        check(button.enabled && button.text === "LOAD AND USE", "alias action unavailable")
        click(button); wait(250)
        check(previewHost.activeModel === "external-code-alias:latest" && previewHost.activeModelRuntimeReady,
              "matching response did not update verified state")
      })
      test("selected not ready requests verification; ready selection does not reload", function() {
        novaPanel.onboardingSnapshotFresh = true
        novaPanel.onboardingExplicitSelection = true
        novaPanel.onboardingSelectedProvider = "ollama"
        novaPanel.onboardingSelectedModel = "external-code-alias:latest"
        previewHost.activeModelRuntimeReady = false
        wait(50)
        var button = rowButton()
        check(button.text === "LOAD AND VERIFY" && button.enabled, "selected unloaded action missing")
        click(button); wait(250)
        check(previewHost.activeModelRuntimeReady, "verification did not finish")
        novaPanel.onboardingSnapshotFresh = true
        novaPanel.onboardingExplicitSelection = true
        novaPanel.onboardingSelectedProvider = "ollama"
        novaPanel.onboardingSelectedModel = "external-code-alias:latest"
        wait(50)
        check(!button.visible && !button.enabled && !button.focusable,
              "ready selected model retained a load target")
      })
      test("a stale host model is not an explicit configured selection", function() {
        novaPanel.onboardingSnapshotFresh = true
        novaPanel.onboardingExplicitSelection = false
        previewHost.activeModel = "external-code-alias:latest"
        previewHost.activeModelRuntimeReady = true
        var button = rowButton()
        check(button.enabled && button.text === "LOAD AND USE", "stale host value blocked first selection")
        click(button); wait(250)
        check(previewHost.activeModel === "external-code-alias:latest", "first selection failed")
      })
      test("operation guards and stale target prevent dispatch", function() {
        novaPanel.onboardingExplicitSelection = false
        previewHost.activeModel = "prior:latest"
        previewHost.activeModelRuntimeReady = false
        novaPanel.onboardingSnapshotFresh = true
        var button = rowButton()
        novaPanel.chatBusy = true; wait(20); check(!button.enabled, "chat guard failed"); novaPanel.chatBusy = false
        novaPanel.chatSessionSetupInFlight = true; wait(20); check(!button.enabled, "context guard failed"); novaPanel.chatSessionSetupInFlight = false
        novaPanel.chatResetInFlight = true; wait(20); check(!button.enabled, "reset guard failed"); novaPanel.chatResetInFlight = false
        novaPanel.pendingApproval = {id:"fixture"}; wait(20); check(!button.enabled, "approval guard failed"); novaPanel.pendingApproval = null; novaPanel.panelView = "models"  // an approval moves NOVA to CHAT
        novaPanel.trackedAcquisitionId = "fixture"; wait(20); check(!button.enabled, "acquisition guard failed"); novaPanel.trackedAcquisitionId = ""
        novaPanel.installedModelsLoading = true; wait(20); check(!button.enabled, "refresh guard failed"); novaPanel.installedModelsLoading = false
        novaPanel.discoveredModels = [{id:"external-code-alias:latest", provider:"other", available:true}]
        novaPanel.loadInstalledModel({id:"external-code-alias:latest", provider:"other", available:true})
        check(novaPanel.modelSwitching === false, "unsupported provider started a load")
        novaPanel.discoveredModels = [{id:"external-code-alias:latest", provider:"ollama", available:true}]
        novaPanel.discoveredModels = [{id:"other:latest", provider:"ollama", available:true}]
        novaPanel.loadInstalledModel({id:"external-code-alias:latest", provider:"ollama", available:true})
        check(novaPanel.modelSwitching === false, "stale target started a load")
        novaPanel.discoveredModels = [{id:"external-code-alias:latest", provider:"ollama", available:true}]
      })
      test("wrong, malformed, missing and failed replies preserve prior selection", function() {
        var variants = ["wrong", "malformed", "missing", "failed"]
        for (var i = 0; i < variants.length; ++i) {
          mode(variants[i])
          novaPanel.onboardingSnapshotFresh = true
          var button = rowButton()
          check(button.enabled, "retry unavailable before " + variants[i])
          click(button); wait(250)
          check(previewHost.activeModel === "prior:latest", variants[i] + " changed selection")
          check(!previewHost.activeModelRuntimeReady, variants[i] + " claimed ready")
          check(novaPanel.modelSwitchStatus.indexOf("FAILED") >= 0 || novaPanel.modelSwitchStatus.indexOf("UNAVAILABLE") >= 0,
                variants[i] + " has no visible failure")
        }
      })
      console.warn("INSTALLED_LOAD_RESULTS " + JSON.stringify(results))
    }
    function test_zz_reconciliation() {
      var results = []
      function test(name, fn) {
        try { fn(); results.push({name:name, passed:true}) }
        catch (e) { results.push({name:name, passed:false, error:String(e)}) }
      }
      function resetPrior() {
        fixture("/__selection__/prior")
        fixture("/__onboarding__/ready")
        mode("ready")
        previewHost.runtimeState = "idle"
        previewHost.modelTarget = ""
        previewHost.activeProvider = "ollama"
        previewHost.activeModel = "prior:latest"
        previewHost.activeModelAvailable = true
        previewHost.activeModelRuntimeReady = false
        novaPanel.refreshOnboarding()
        tryVerify(function() { return novaPanel.onboardingSelectedModel === "prior:latest" && novaPanel.onboardingSnapshotFresh }, 1500)
        check(rowButton().enabled, "prior selection did not allow load")
      }
      function checkSettled() {
        var elapsed = 0
        while (!(novaPanel.onboardingState === "READY" && novaPanel.onboardingCanSend) && elapsed < 2500) { wait(50); elapsed += 50 }
        check(novaPanel.onboardingState === "READY" && novaPanel.onboardingCanSend,
              "readiness did not settle: " + novaPanel.onboardingState + "/" + novaPanel.onboardingCodes + "/" + novaPanel.onboardingCanSend)
        var button = rowButton()
        check(!button.visible && !button.enabled && !button.focusable, "ready action still occupies the row")
        check(novaPanel.onboardingTitle() === "LOCAL MODEL READY", "banner did not settle")
        check(novaPanel.installedModelReady({id:"external-code-alias:latest",provider:"ollama"}), "row not ready")
        var send = findChild(novaPanel, "sendButton")
        check(novaPanel.onboardingCanSend, "composer readiness gate stayed closed")
      }
      test("activation response before delayed idle poll reconciles promptly", function() {
        resetPrior()
        mode("slow_success")
        var before = requests().length
        var button = rowButton()
        novaPanel.loadInstalledModel({id:"external-code-alias:latest",provider:"ollama",available:true})
        previewHost.runtimeState = "model_switching"
        wait(30)
        check(button.text === "LOADING…" && !button.enabled, "target action not loading: " + button.text + "/" + button.enabled + "/" + novaPanel.modelSwitching + "/" + novaPanel.transitionTarget)
        wait(450)
        check(novaPanel.onboardingState !== "READY", "ready manufactured before idle poll")
        previewHost.runtimeState = "idle"
        checkSettled()
        check(requests().length === before + 1, "activation POST count wrong")
      })
      test("idle poll before activation response reconciles promptly", function() {
        resetPrior()
        mode("slow_success")
        var before = requests().length
        novaPanel.loadInstalledModel({id:"external-code-alias:latest",provider:"ollama",available:true})
        wait(30)
        check(rowButton().text === "LOADING…", "in-flight target label absent: " + rowButton().text + "/" + novaPanel.modelSwitching)
        previewHost.runtimeState = "idle"
        checkSettled()
        check(requests().length === before + 1, "activation POST count wrong")
      })
      test("late old-selection snapshot cannot overwrite completed target", function() {
        resetPrior()
        mode("slow_success")
        fixture("/__onboarding__/slow_old")
        novaPanel.refreshOnboarding()
        check(novaPanel.onboardingRequestInFlight, "old snapshot was not in flight")
        var before = requests().length
        novaPanel.selectModel("external-code-alias:latest", false, "ollama")
        check(novaPanel.modelSwitching, "activation did not start")
        wait(600)
        checkSettled()
        check(novaPanel.onboardingSelectedModel === "external-code-alias:latest", "old snapshot replaced target")
        check(requests().length === before + 1, "stale reply caused extra activation")
      })
      test("unavailable reconciliation expires without claiming ready", function() {
        resetPrior()
        fixture("/__onboarding__/unavailable")
        var before = requests().length
        novaPanel.loadInstalledModel({id:"external-code-alias:latest",provider:"ollama",available:true})
        tryVerify(function() { return novaPanel.activationReconcileModel !== "" }, 1000)
        novaPanel.activationReconcileDeadline = Date.now() + 500
        wait(900)
        check(novaPanel.onboardingState === "NEEDS_USER_SETUP" && !novaPanel.onboardingCanSend,
              "unavailable check kept activating or claimed ready")
        check(novaPanel.activationReconcileModel === "", "reconciliation did not terminate")
        check(requests().length === before + 1, "unavailable status retried activation")
      })
      test("host readiness loss immediately closes chat gate", function() {
        resetPrior()
        var before = requests().length
        novaPanel.loadInstalledModel({id:"external-code-alias:latest",provider:"ollama",available:true})
        checkSettled()
        function checkConflict(label) {
          var elapsed = 0
          while (novaPanel.onboardingCodes.indexOf("READINESS_CONFLICT") < 0 && elapsed < 1500) { wait(50); elapsed += 50 }
          check(novaPanel.onboardingCodes.indexOf("READINESS_CONFLICT") >= 0,
                label + " did not show conflict: " + novaPanel.onboardingState + "/" + novaPanel.onboardingCodes)
          check(novaPanel.onboardingState === "NEEDS_LOAD" && novaPanel.onboardingTitle() === "MODEL NOT READY",
                label + " restored a contradictory READY banner")
          check(!novaPanel.onboardingCanSend, label + " left chat ready")
          check(novaPanel.installedModelSelected({id:"external-code-alias:latest",provider:"ollama"})
                && !novaPanel.installedModelReady({id:"external-code-alias:latest",provider:"ollama"}),
                label + " lost selection or claimed runtime ready")
          check(rowButton().text === "LOAD AND VERIFY", label + " selected action wrong")
        }
        previewHost.activeModelRuntimeReady = false
        checkConflict("readiness loss")
        previewHost.activeModelRuntimeReady = true
        checkSettled()
        previewHost.activeModelAvailable = false
        checkConflict("availability loss")
        previewHost.activeModelAvailable = true
        checkSettled()
        previewHost.activeModel = "other:latest"
        checkConflict("identity change")
        previewHost.activeModel = "external-code-alias:latest"
        checkSettled()
        previewHost.activeProvider = "other"
        checkConflict("provider change")
        previewHost.activeProvider = "ollama"
        checkSettled()
        check(requests().length === before + 1, "readiness recovery launched another activation")
      })
      test("READY without explicit identity never claims selected or chat ready", function() {
        fixture("/__onboarding__/invalid_ready")
        novaPanel.refreshOnboarding()
        var elapsed = 0
        while (novaPanel.onboardingRequestInFlight && elapsed < 1500) { wait(50); elapsed += 50 }
        check(novaPanel.onboardingState === "CHECKING" && !novaPanel.onboardingCanSend
              && !novaPanel.onboardingExplicitSelection, "invalid READY response was promoted")
        fixture("/__onboarding__/ready")
        novaPanel.refreshOnboarding()
        checkSettled()
      })
      console.warn("RECONCILIATION_RESULTS " + JSON.stringify(results))
    }
    function requests() {
      var xhr = fakeNova.request(), complete = false, result = []
      xhr.onreadystatechange = function() { if (xhr.readyState === XMLHttpRequest.DONE) { result = JSON.parse(xhr.responseText).requests; complete = true } }
      xhr.open("GET", "/__requests__"); xhr.send()
      var n = 0; while (!complete && n++ < 100) wait(10)
      check(complete, "request log unavailable")
      return result
    }
  }
'''


def main():
    server = fake_nova.server(Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        fixture = preview.scenarios()["installed-no-selection"]
        with tempfile.TemporaryDirectory(prefix="nova-installed-load-", dir="/tmp") as raw:
            shell = preview.prepare_config(Path(raw), fixture)
            text = shell.read_text().replace("import QtQuick\n", "import QtQuick\nimport QtTest\n", 1)
            text = text.rsplit("\n}", 1)[0] + QML_CHECK.replace("@SOCKET@", server.server_address) + "\n}\n"
            shell.write_text(text)
            result = subprocess.run(["quickshell", "--path", raw], capture_output=True, text=True, timeout=45)
            output = result.stdout + result.stderr
        markers = ("INSTALLED_LOAD_RESULTS ", "RECONCILIATION_RESULTS ")
        lines = [next((row.split(marker, 1)[1] for row in output.splitlines() if marker in row), None)
                 for marker in markers]
        if any(line is None for line in lines):
            print(output)
            raise SystemExit("Installed-model load interaction did not complete")
        rows = [row for line in lines for row in json.loads(line)]
        for row in rows:
            print(("PASS " if row["passed"] else "FAIL ") + row["name"] +
                  ((": " + row["error"]) if not row["passed"] else ""))
        if any(not row["passed"] for row in rows):
            print(output)
            raise SystemExit(1)
        if len(REQUESTS) != 12:
            raise SystemExit("Unexpected model activation requests: " + repr(REQUESTS))
        if any(request != {"provider": "ollama", "model": ALIAS} for request in REQUESTS):
            raise SystemExit("Unexpected activation identity: " + repr(REQUESTS))
        print("PASS exact activation request sequence (12 requests)")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    main()
