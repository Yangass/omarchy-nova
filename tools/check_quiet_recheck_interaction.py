#!/usr/bin/env python3
"""The panel's background re-checks don't blink (2026-10-23, Konstantin's friend: "a jitter
every 15-30 seconds, feels like NOVA refreshes"). Against a fake novad that answers slowly:

- the 30 s setup re-check keeps the last answer on screen until the new one arrives (it
  blanked to CHECKING: the MODEL line, IN USE and the buttons flipped for as long as the
  answer took);
- a changed answer still shows, an explicit check still says CHECKING, and a failed
  re-check still says NOVA is unavailable;
- the 5 s coding CLI refresh replaces the rows only when something changed;
- an online CLI's name shows in full ("Claude Code · NOVA" was cut to "Claude Code · NO…").
"""
import json
from http.server import BaseHTTPRequestHandler
from pathlib import Path
import subprocess
import tempfile
from threading import Thread
import time

import fake_nova
import onboarding_preview as preview

MODE = {"onboarding": "ready", "cli": "one"}
MODEL = "qwen3:8b"
ONLINE = {"id": "claude-code", "kind": "online", "label": "Claude Code · NOVA", "tool": "claude-code", "provider": "online",
          "project": "/srv/nova", "project_label": "NOVA source", "valid": True, "available": True, "blocked": None}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path.startswith("/__onboarding__/"):
            MODE["onboarding"] = self.path.rsplit("/", 1)[-1]
            self.reply(200, MODE)
        elif self.path.startswith("/__cli__/"):
            MODE["cli"] = self.path.rsplit("/", 1)[-1]
            self.reply(200, MODE)
        elif self.path == "/onboarding/state":
            mode = MODE["onboarding"]
            if mode.startswith("slow"): time.sleep(.8)
            if mode == "fail":
                time.sleep(.3)
                self.reply(503, {"error": "fixture_unavailable"})
                return
            selected = mode != "slow_none"
            self.reply(200, {"schema_version": 1, "primary_state": "READY" if selected else "NEEDS_ACTIVATION",
                             "allowed_actions": ["SEND_FIRST_MESSAGE"] if selected else ["CHOOSE_INSTALLED_MODEL"],
                             "explanation_codes": ["SELECTED_MODEL_NOT_LOADED"] if selected else [],
                             "configuration": {"explicit_selection": selected, "provider": "ollama" if selected else "",
                                               "model": MODEL if selected else ""}})
        elif self.path == "/cli/profiles":
            profiles = [ONLINE] + ([{**ONLINE, "id": "codex", "label": "Codex · NOVA", "tool": "codex"}] if MODE["cli"] == "two" else [])
            self.reply(200, {"configured": True, "error": None, "profiles": profiles, "client_active": False,
                             "checked_at": "2026-10-23T12:00:%02d.%06dZ" % (time.time() % 60, time.time() % 1 * 1e6)})
        elif self.path == "/models":
            self.reply(200, {"models": [{"id": MODEL, "provider": "ollama", "available": True}]})
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
    name: "QuietRecheck"
    when: novaPanel.opened
    function check(value, message) { if (!value) throw new Error(message) }
    function fixture(path) {
      var xhr = fakeNova.request(), complete = false
      xhr.onreadystatechange = function() { if (xhr.readyState === XMLHttpRequest.DONE) complete = true }
      xhr.open("GET", path); xhr.send()
      var n = 0; while (!complete && n++ < 100) wait(10)
      check(complete, "fixture request failed: " + path)
    }
    function settle() { var n = 0; while (novaPanel.onboardingRequestInFlight && n++ < 300) wait(10); check(!novaPanel.onboardingRequestInFlight, "check never finished") }
    function inUse() {
      var rows = findChild(novaPanel, "installedModelsRepeater")
      var tag = rows && rows.count ? findChild(rows.itemAt(0), "inUseTag") : null
      return tag !== null && tag.visible
    }
    function screen() { return novaPanel.onboardingState + " | " + novaPanel.brainSummary() + " | IN USE " + inUse() + " | fresh " + novaPanel.onboardingSnapshotFresh }
    function recheck() { findChild(novaPanel, "onboardingRecheck").triggered() }
    function test_quiet() {
      var results = []
      function test(name, fn) {
        try { fn(); results.push({name:name, passed:true}) }
        catch (e) { results.push({name:name, passed:false, error:String(e)}) }
      }
      try {
      previewHost.novaClient = fakeNova
      previewHost.acceptancePreview = false
      previewHost.activeProvider = "ollama"; previewHost.activeModel = "@MODEL@"
      previewHost.activeModelAvailable = true; previewHost.activeModelRuntimeReady = false
      novaPanel.modelsUnavailable = false
      novaPanel.discoveredModels = [{id: "@MODEL@", provider: "ollama", available: true}]
      novaPanel.panelView = "models"
      wait(100); settle()
      novaPanel.refreshOnboarding(); settle(); wait(60)
      var ready = screen(), receivedAt = novaPanel.onboardingReceivedAt
      test("first answer: ready, in use", function() {
        check(ready === "READY | MODEL · Qwen 3 8B | IN USE true | fresh true", "screen: " + ready)
      })
      test("a background re-check keeps the answer on screen until the new one arrives", function() {
        fixture("/__onboarding__/slow"); wait(20)
        recheck()
        check(novaPanel.onboardingRequestInFlight && novaPanel.onboardingRecheckQuiet, "not a quiet re-check")
        for (var i = 0; i < 7; i++) { wait(100); check(screen() === ready, "blinked at " + (i + 1) * 100 + " ms: " + screen()) }
        settle(); wait(40)
        check(screen() === ready && novaPanel.onboardingReceivedAt > receivedAt, "after: " + screen())
      })
      test("a changed answer still shows", function() {
        fixture("/__onboarding__/slow_none"); recheck(); settle(); wait(40)
        check(novaPanel.onboardingState === "NEEDS_ACTIVATION" && novaPanel.brainSummary() === "MODEL · NONE SELECTED" && !inUse(), "screen: " + screen())
        fixture("/__onboarding__/ready"); novaPanel.refreshOnboarding(); settle()
      })
      test("an explicit check still says CHECKING", function() {
        fixture("/__onboarding__/slow"); novaPanel.refreshOnboarding(); wait(100)
        check(novaPanel.onboardingState === "CHECKING" && !novaPanel.onboardingSnapshotFresh, "screen: " + screen())
        settle(); wait(40)
        check(screen() === ready, "after: " + screen())
      })
      test("a failed background re-check still says NOVA is unavailable", function() {
        fixture("/__onboarding__/fail"); recheck(); settle(); wait(40)
        check(novaPanel.onboardingState === "NEEDS_USER_SETUP" && novaPanel.onboardingCodes.indexOf("NOVAD_UNAVAILABLE") >= 0, "screen: " + screen())
        fixture("/__onboarding__/ready"); novaPanel.refreshOnboarding(); settle()
      })
      test("the same coding CLI answer keeps the rows; a change replaces them", function() {
        var rows = findChild(novaPanel, "onlineCliRepeater")
        novaPanel.refreshCliProfiles(); wait(300)
        check(rows.count === 1, "online rows: " + rows.count)
        var state = novaPanel.cliState, row = rows.itemAt(0)
        novaPanel.refreshCliProfiles(); wait(300)
        check(novaPanel.cliState === state && rows.itemAt(0) === row, "rows were rebuilt for the same answer")
        fixture("/__cli__/two"); novaPanel.refreshCliProfiles(); wait(300)
        check(novaPanel.cliState !== state && rows.count === 2, "a change did not show: " + rows.count)
      })
      test("an online CLI's name shows in full", function() {
        var name = findChild(findChild(novaPanel, "onlineCliRepeater").itemAt(0), "onlineCliName")
        check(name.text === "Claude Code · NOVA" && !name.truncated, "name cut: " + name.text + " " + name.width + "/" + name.implicitWidth)
      })
      } catch (e) { results.push({name: "harness setup", passed: false, error: String(e)}) }
      console.warn("QUIET_RESULTS " + JSON.stringify(results))
    }
  }
'''


def main():
    server = fake_nova.server(Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        fixture = preview.scenarios()["installed-no-selection"]
        with tempfile.TemporaryDirectory(prefix="nova-quiet-", dir="/tmp") as raw:
            shell = preview.prepare_config(Path(raw), fixture)
            text = shell.read_text().replace("import QtQuick\n", "import QtQuick\nimport QtTest\n", 1)
            check = QML_CHECK.replace("@SOCKET@", server.server_address).replace("@MODEL@", MODEL)
            shell.write_text(text.rsplit("\n}", 1)[0] + check + "\n}\n")
            result = subprocess.run(["quickshell", "--path", raw], capture_output=True, text=True, timeout=60)
            output = result.stdout + result.stderr
        line = next((row.split("QUIET_RESULTS ", 1)[1] for row in output.splitlines() if "QUIET_RESULTS " in row), None)
        if line is None:
            print(output)
            raise SystemExit("Quiet re-check interaction did not complete")
        rows = json.loads(line)
        for row in rows:
            print(("PASS " if row["passed"] else "FAIL ") + row["name"] + ((": " + row["error"]) if not row["passed"] else ""))
        errors = [r for r in output.splitlines() if any(w in r for w in ("ReferenceError", "TypeError", "Binding loop"))]
        if errors or any(not row["passed"] for row in rows):
            print(output)
            raise SystemExit(1)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    main()
