#!/usr/bin/env python3
"""Click the canonical Open CLI control against an isolated HTTP stub (CODE-01B).

Run under a private compositor. No real launcher, model or action service is used.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
from threading import Thread

import onboarding_preview as preview

ALIAS = "nova-code-qwen38-96k:latest"
MODELS = [{"id": ALIAS, "provider": "ollama", "available": True},
          {"id": "qwen3.8:27b-q4_K_M", "provider": "ollama", "available": True},
          {"id": "qwen3:14b", "provider": "ollama", "available": True}]
REQUESTS = {"launch": [], "activation": []}
MODE = {"profiles": "available", "launch": "dispatched"}


def profile(**changes):
    item = {"id": "qwen-nova-dev", "label": "Qwen Code · NOVA dev project", "tool": "qwen-code",
            "provider": "ollama", "model": ALIAS, "project_label": "NOVA dev project",
            "valid": True, "error": None, "running": False, "available": True, "blocked": None,
            "conflicting_models": [], "project_sync": "will_update"}
    item.update(changes)
    return item


def online(**changes):
    item = {"id": "claude-code", "label": "Claude Code · NOVA", "tool": "claude-code", "provider": "online",
            "project_label": "NOVA source", "kind": "online", "valid": True, "error": None, "running": False,
            "project_sync": "not_applicable", "available": True, "blocked": None, "conflicting_models": []}
    item.update(changes)
    return item


ONLINE = [online(), online(id="codex", label="Codex · NOVA", tool="codex", valid=False, error="command_unavailable",
                          available=False, blocked="command_unavailable")]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path.startswith("/__profiles__/"):
            MODE["profiles"] = self.path.rsplit("/", 1)[-1]; self.reply(200, MODE)
        elif self.path.startswith("/__launch__/"):
            MODE["launch"] = self.path.rsplit("/", 1)[-1]; self.reply(200, MODE)
        elif self.path == "/__requests__":
            self.reply(200, REQUESTS)
        elif self.path == "/__requests__/reset":
            for key in REQUESTS: REQUESTS[key].clear()
            self.reply(200, REQUESTS)
        elif self.path == "/models":
            self.reply(200, {"models": MODELS})
        elif self.path == "/cli/profiles":
            mode = MODE["profiles"]
            if mode == "unavailable":
                self.reply(503, {"error": "action_executor_unavailable"}); return
            if mode == "none":
                self.reply(200, {"configured": False, "error": None, "profiles": [], "checked_at": "fixture"}); return
            if mode == "online":
                self.reply(200, {"configured": True, "error": None, "checked_at": "fixture",
                                 "profiles": [profile()] + ONLINE}); return
            item = {"available": profile(),
                    "running": profile(running=True, available=False, blocked="cli_session_active"),
                    "conflict": profile(available=False, blocked="conflicting_resident_model",
                                        conflicting_models=["qwen3:14b"])}[mode]
            self.reply(200, {"configured": True, "error": None, "profiles": [item], "checked_at": "fixture"})
        else:
            self.reply(503, {"error": "fixture_unavailable"})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        try: body = json.loads(raw)
        except ValueError: body = {"invalid": True}
        if self.path == "/cli/launch":
            REQUESTS["launch"].append(body)
            if MODE["launch"] == "dispatched":
                self.reply(200, {"status": "dispatched", "action": "open_coding_cli", "window_observation": "not_observed"})
            else:
                self.reply(409, {"status": "failure", "error": "cli_session_active"})
        elif self.path == "/model/active":
            REQUESTS["activation"].append(body); self.reply(503, {"error": "fixture_activation"})
        else:
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
    name: "OpenCliInteraction"
    when: novaPanel.opened
    function check(value, message) { if (!value) throw new Error(message) }
    function row(index) {
      var rows = findChild(novaPanel, "installedModelsRepeater")
      check(rows && rows.count === 3, "installed rows missing")
      return rows.itemAt(index)
    }
    function child(index, name) { var item = findChild(row(index), name); check(item !== null, name + " missing"); return item }
    function click(button) {
      var list = findChild(novaPanel, "modelList")
      var top = button.mapToItem(list.contentItem, 0, 0).y
      list.contentY = Math.max(0, Math.min(list.contentHeight - list.height, top - 12))
      wait(60)
      var catcher = findChild(novaPanel, "panelKeyCatcher")
      var point = button.mapToItem(catcher, button.width / 2, button.height / 2)
      mouseClick(catcher, point.x, point.y, Qt.LeftButton)
      wait(120)
    }
    function get(path) {
      var xhr = new XMLHttpRequest(), complete = false, result = null
      xhr.onreadystatechange = function() { if (xhr.readyState === XMLHttpRequest.DONE) { result = xhr.responseText; complete = true } }
      xhr.open("GET", previewHost.api + path); xhr.send()
      var n = 0; while (!complete && n++ < 100) wait(10)
      check(complete, "fixture request failed: " + path)
      return JSON.parse(result)
    }
    function capture(name) {
      if (!"@CAPTURE@") return
      var done = false
      findChild(novaPanel, "panelKeyCatcher").parent.parent.grabToImage(function(image) { image.saveToFile("@CAPTURE@/" + name + ".png"); done = true })
      var n = 0; while (!done && n++ < 100) wait(20)
    }
    function profiles(mode) {
      get("/__profiles__/" + mode); novaPanel.cliState = null; novaPanel.refreshCliProfiles()
      var n = 0; while (novaPanel.cliState === null && mode !== "unavailable" && n++ < 100) wait(10)
      wait(60)
    }
    function test_open_cli() {
      var results = []
      function test(name, fn) {
        try { fn(); results.push({name:name, passed:true}) }
        catch (e) { results.push({name:name, passed:false, error:String(e)}) }
      }
      previewHost.api = "http://127.0.0.1:@PORT@"
      previewHost.acceptancePreview = false
      novaPanel.panelView = "models"
      novaPanel.modelsUnavailable = false
      novaPanel.discoveredModels = @MODELS@
      wait(100)
      test("every installed row offers Open CLI; only the profiled alias is launchable", function() {
        profiles("available")
        check(child(0, "openCliButton").visible && child(0, "openCliButton").launchable, "alias not launchable")
        for (var i = 1; i < 3; i++) {
          var button = child(i, "openCliButton")
          check(button.visible && !button.launchable && button.text === "OPEN CLI", "row " + i + " wrongly launchable")
        }
      })
      test("unprofiled row explains why and never opens a card or launches", function() {
        click(child(2, "openCliButton"))
        var status = child(2, "cliStatusText")
        check(status.visible && status.text.indexOf("No coding CLI profile for this model. Configured for: nova-code-qwen38-96k:latest") === 0, "reason missing: " + status.text)
        check(!child(2, "cliConfirmCard").visible && get("/__requests__").launch.length === 0, "unprofiled row launched")
      })
      test("confirm card names profile, model and project; cancel sends nothing", function() {
        click(child(0, "openCliButton"))
        var card = child(0, "cliConfirmCard")
        check(card.visible && card.profile.label === "Qwen Code · NOVA dev project", "card missing")
        check(child(0, "cliSyncText").text.indexOf("Project will update to the latest NOVA master") >= 0, "sync status missing: " + child(0, "cliSyncText").text)
        capture("confirm-card")
        click(child(0, "cliConfirmCancel"))
        check(!card.visible && get("/__requests__").launch.length === 0, "cancel launched")
      })
      test("the confirm card is a visible call to action: in view, amber core, Enter and Escape", function() {
        var list = findChild(novaPanel, "modelList"), input = findChild(novaPanel, "chatInput")
        // As on Konstantin's screen: row 0's details open, so the card opens below the
        // visible part of the list.
        click(child(0, "installedModelDetailsButton")); wait(60)
        list.contentY = 0; input.forceActiveFocus(); wait(30)
        click(child(0, "openCliButton")); wait(120)
        var card = child(0, "cliConfirmCard"), top = card.mapToItem(list.contentItem, 0, 0).y
        check(top >= list.contentY - 0.5 && top + card.height <= list.contentY + list.height + 0.5,
              "card not fully in view: card " + top + "+" + card.height + " view " + list.contentY + "+" + list.height)
        check(novaPanel.coreMode === "waiting" && findChild(novaPanel, "coreStatus").text === "CORE // CONFIRM OPEN CLI",
              "core not awaiting: " + novaPanel.coreMode + " " + findChild(novaPanel, "coreStatus").text)
        check(child(0, "cliConfirmLaunch").text === "LAUNCH [ENTER]" && child(0, "cliConfirmCancel").text === "CANCEL [ESC]", "key hints missing")
        capture("confirm-card-in-view")
        keyClick(Qt.Key_Escape); wait(50)
        check(!card.visible && novaPanel.opened && get("/__requests__").launch.length === 0, "Escape did not cancel (or closed the panel)")
        check(novaPanel.coreMode !== "waiting", "core stayed awaiting after cancel: " + novaPanel.coreMode)
        // An Enter already down when the card opens must not launch; after release, Enter launches once.
        click(child(0, "openCliButton")); keyPress(Qt.Key_Return); wait(30)
        check(card.visible && get("/__requests__").launch.length === 0, "held Enter launched immediately")
        keyRelease(Qt.Key_Return); wait(30)
        var armed = "armed=" + novaPanel.cliConfirmArmed + " seen=" + novaPanel.cliConfirmReturnSeen + " card=" + novaPanel.cliConfirmModel
        keyClick(Qt.Key_Return); wait(300)
        armed += " after: card=" + novaPanel.cliConfirmModel + " status=" + novaPanel.cliStatus
        check(!card.visible && get("/__requests__").launch.length === 1, "Enter did not launch exactly once: " + get("/__requests__").launch.length + " " + armed)
        click(child(0, "installedModelDetailsButton")); wait(30)
        get("/__requests__/reset"); profiles("available")
      })
      test("launch sends the exact profile and model once and stays honest about the window", function() {
        click(child(0, "openCliButton")); click(child(0, "cliConfirmLaunch")); wait(300)
        var requests = get("/__requests__")
        check(requests.launch.length === 1 && JSON.stringify(requests.launch[0]) === JSON.stringify({profile_id:"qwen-nova-dev", model:"@ALIAS@"}), "wrong launch request: " + JSON.stringify(requests.launch))
        var status = child(0, "cliStatusText")
        check(status.text === "CLI LAUNCH REQUESTED // NOVA CANNOT CONFIRM THE TERMINAL APPEARED" && !novaPanel.cliStatusFailed, "status wrong: " + status.text)
        check(requests.activation.length === 0, "Open CLI touched NOVA's chat model")
      })
      test("running session shows CLI RUNNING and blocks a duplicate", function() {
        profiles("running")
        var button = child(0, "openCliButton")
        check(button.text === "CLI RUNNING" && !button.launchable, "running state wrong")
        // Seeing the session running answers "cannot confirm the terminal appeared".
        check(!child(0, "cliStatusText").visible && !novaPanel.cliLaunchUnconfirmed, "unconfirmed note kept: " + child(0, "cliStatusText").text)
        click(button)
        check(child(0, "cliStatusText").text === "A coding CLI session is already running." && get("/__requests__").launch.length === 1, "duplicate allowed")
        check(findChild(novaPanel, "cliRunningWatch").running, "no watch while a session runs")
      })
      test("while the CLI loads the selected model, NOVA offers no second load and says so", function() {
        profiles("running")
        novaPanel.onboardingSnapshotFresh = true; novaPanel.onboardingState = "NEEDS_LOAD"
        novaPanel.onboardingActions = ["LOAD_AND_VERIFY"]; novaPanel.onboardingTarget = "@ALIAS@"; wait(50)
        check(novaPanel.onboardingActionLabel() === "", "load offered: " + novaPanel.onboardingActionLabel())
        check(novaPanel.onboardingMessage() === "The coding CLI is loading this model. NOVA shows it as ready once it's loaded.", "message: " + novaPanel.onboardingMessage())
        check(!child(0, "loadInstalledModelButton").visible, "row still offers a load")
        novaPanel.onboardingTarget = "qwen3:14b"; wait(30)
        check(novaPanel.onboardingActionLabel().indexOf("LOAD AND VERIFY") === 0, "other model lost its load action")
        novaPanel.onboardingTarget = "@ALIAS@"
      })
      test("closing the CLI clears CLI RUNNING and the stale reason by itself", function() {
        get("/__profiles__/available")
        var n = 0; while (child(0, "openCliButton").text !== "OPEN CLI" && n++ < 400) wait(20)
        check(child(0, "openCliButton").text === "OPEN CLI" && child(0, "openCliButton").launchable, "button stayed: " + child(0, "openCliButton").text)
        check(!child(0, "cliStatusText").visible, "stale reason kept: " + child(0, "cliStatusText").text)
        check(!findChild(novaPanel, "cliRunningWatch").running, "watch kept polling after the session ended")
        check(novaPanel.onboardingActionLabel().indexOf("LOAD AND VERIFY") === 0, "load action did not return: " + novaPanel.onboardingActionLabel())
      })
      test("conflicting resident model is named", function() {
        profiles("conflict"); click(child(0, "openCliButton"))
        check(child(0, "cliStatusText").text.indexOf("Blocked: qwen3:14b is loaded in GPU memory") === 0, "conflict reason wrong")
        capture("conflict-reason")
      })
      test("executor failure is shown as failure", function() {
        profiles("available"); get("/__launch__/failure")
        click(child(0, "openCliButton")); click(child(0, "cliConfirmLaunch")); wait(300)
        check(novaPanel.cliStatusFailed && child(0, "cliStatusText").text === "CLI LAUNCH FAILED // CLI SESSION ACTIVE", "failure text wrong")
      })
      test("no online profiles: no ONLINE CLIS section", function() {
        profiles("available")
        check(!findChild(novaPanel, "onlineCliSection").visible, "empty online section shown")
      })
      test("ONLINE CLIS lists online profiles apart from installed models", function() {
        get("/__launch__/dispatched"); get("/__requests__/reset"); profiles("online")
        var section = findChild(novaPanel, "onlineCliSection"), rows = findChild(novaPanel, "onlineCliRepeater")
        check(section.visible && rows.count === 2, "online section wrong: " + section.visible + " " + (rows && rows.count))
        check(findChild(section, "onlineCliTitle").text === "ONLINE CLIS · 2", "title: " + findChild(section, "onlineCliTitle").text)
        check(findChild(rows.itemAt(0), "onlineCliOpenButton").launchable && !findChild(rows.itemAt(1), "onlineCliOpenButton").launchable, "launchable wrong")
        check(child(0, "openCliButton").launchable && !child(1, "openCliButton").launchable, "local rows changed by online profiles")
        capture("online-clis")
      })
      test("invalid online profile explains itself and never launches", function() {
        var row = findChild(novaPanel, "onlineCliRepeater").itemAt(1)
        click(findChild(row, "onlineCliOpenButton"))
        var status = findChild(row, "onlineCliStatusText")
        check(status.visible && status.text.indexOf("The CLI program is missing") === 0, "reason: " + status.text)
        check(!findChild(row, "onlineCliConfirmCard").visible && get("/__requests__").launch.length === 0, "invalid profile launched")
      })
      test("online confirm card launches with model online once; NOVA's chat model untouched", function() {
        var row = findChild(novaPanel, "onlineCliRepeater").itemAt(0)
        click(findChild(row, "onlineCliOpenButton"))
        var card = findChild(row, "onlineCliConfirmCard")
        check(card.visible && findChild(card, "onlineCliProject").text === "PROJECT  NOVA source", "card wrong")
        check(novaPanel.coreMode === "waiting", "core not awaiting: " + novaPanel.coreMode)
        capture("online-confirm")
        keyClick(Qt.Key_Escape); wait(50)
        check(!card.visible && get("/__requests__").launch.length === 0, "Escape launched")
        click(findChild(row, "onlineCliOpenButton")); wait(350); keyClick(Qt.Key_Return); wait(300)
        var requests = get("/__requests__")
        check(requests.launch.length === 1 && JSON.stringify(requests.launch[0]) === JSON.stringify({profile_id:"claude-code", model:"online"}), "wrong request: " + JSON.stringify(requests.launch))
        check(findChild(row, "onlineCliStatusText").text === "CLI LAUNCH REQUESTED // NOVA CANNOT CONFIRM THE TERMINAL APPEARED", "status: " + findChild(row, "onlineCliStatusText").text)
        check(!child(0, "cliStatusText").visible, "status leaked to a local row")
        check(requests.activation.length === 0, "online CLI touched NOVA's chat model")
        get("/__requests__/reset")
      })
      test("unknown availability is stated, not guessed", function() {
        profiles("unavailable"); wait(200)
        check(novaPanel.cliState === null && !child(0, "openCliButton").launchable, "unknown treated as available")
        click(child(0, "openCliButton"))
        check(child(0, "cliStatusText").text.indexOf("Coding CLI availability is unknown") === 0, "unknown reason wrong")
      })
      test("no profile file hides OPEN CLI (fresh install)", function() {
        profiles("none"); wait(200)
        check(novaPanel.cliState && novaPanel.cliState.configured === false, "state not loaded")
        check(!child(0, "openCliButton").visible, "OPEN CLI shown without any profile")
        profiles("available"); wait(200)
        check(child(0, "openCliButton").visible, "OPEN CLI hidden with a profile")
      })
      console.warn("OPEN_CLI_RESULTS " + JSON.stringify(results))
    }
  }
'''


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        fixture = preview.scenarios()["installed-no-selection"]
        with tempfile.TemporaryDirectory(prefix="nova-open-cli-", dir="/tmp") as raw:
            shell = preview.prepare_config(Path(raw), fixture)
            text = shell.read_text().replace("import QtQuick\n", "import QtQuick\nimport QtTest\n", 1)
            check = (QML_CHECK.replace("@PORT@", str(server.server_port))
                     .replace("@MODELS@", json.dumps(MODELS)).replace("@ALIAS@", ALIAS)
                     .replace("@CAPTURE@", os.environ.get("NOVA_OPEN_CLI_CAPTURE", "")))
            shell.write_text(text.rsplit("\n}", 1)[0] + check + "\n}\n")
            result = subprocess.run(["quickshell", "--path", raw], capture_output=True, text=True, timeout=90)
            output = result.stdout + result.stderr
        line = next((row.split("OPEN_CLI_RESULTS ", 1)[1] for row in output.splitlines() if "OPEN_CLI_RESULTS " in row), None)
        if line is None:
            print(output)
            raise SystemExit("Open CLI interaction did not complete")
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
