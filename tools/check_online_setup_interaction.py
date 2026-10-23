#!/usr/bin/env python3
"""ONLINE-01 step 2 and OpenCode detection (2026-10-23, Konstantin's friend: "he has no
Claude or Codex CLI" in NOVA, and OpenCode never showed as running).

Against a fake novad: ONLINE CLIS offers Claude Code and Codex when Omarchy's launchers are
there and nothing is set up; SET UP shows the folders and the sign-in, sends exactly the tool
and folder, and the approval adds OPEN CLI; a running OpenCode shows CLI RUNNING and hides
LOAD AND VERIFY. NOVA_ONLINE_CAPTURE=<dir> saves pictures.
"""
import json
import os
from http.server import BaseHTTPRequestHandler
from pathlib import Path
import subprocess
import tempfile
from threading import Thread
import time

import fake_nova
import onboarding_preview as preview

STATE = {"set_up": False, "opencode_running": False, "agy": False, "posts": []}
OPENCODE = {"id": "opencode-qwen3-8b", "kind": "opencode", "label": "OpenCode", "tool": "opencode", "provider": "ollama",
            "model": "qwen3:8b", "build": "nova-qwen3-8b-cli", "project_label": "~/Work", "project_place": "~/Work", "care": "ask",
            "valid": True, "available": True, "blocked": None, "context": 32768, "conflicting_models": []}
CLAUDE = {"id": "claude-code", "kind": "online", "label": "Claude Code", "tool": "claude-code", "provider": "online",
          "project_label": "~/nova", "valid": True, "available": True, "blocked": None, "running": False}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path.startswith("/__set__/"):
            key, value = self.path.split("/")[2:4]
            STATE[key] = value == "1"
            self.reply(200, {"ok": True})
        elif self.path == "/__posts__":
            self.reply(200, {"posts": STATE["posts"]})
        elif self.path == "/cli/profiles":
            profiles = [{**OPENCODE, "running": STATE["opencode_running"]}] + ([CLAUDE] if STATE["set_up"] else [])
            self.reply(200, {"configured": True, "error": None, "profiles": profiles, "client_active": False, "checked_at": "now",
                             "online_tools": [{"id": "claude-code", "label": "Claude Code", "service": "Anthropic", "available": True, "set_up": STATE["set_up"], "install_hint": "Omarchy's update installs it"},
                                              {"id": "codex", "label": "Codex", "service": "OpenAI", "available": True, "set_up": False, "install_hint": "Omarchy's update installs it"},
                                              {"id": "antigravity", "label": "Antigravity", "service": "Google", "available": STATE["agy"], "set_up": False, "install_hint": "get it from antigravity.google"}]})
        elif self.path == "/cli/setup/online":
            self.reply(200, {"tools": [{"id": "claude-code", "label": "Claude Code", "service": "Anthropic", "available": True, "downloads_on_first_start": True},
                                       {"id": "codex", "label": "Codex", "service": "OpenAI", "available": True, "downloads_on_first_start": True},
                                       {"id": "antigravity", "label": "Antigravity", "service": "Google", "available": STATE["agy"], "downloads_on_first_start": False}],
                             "projects": ["~/Work", "~/nova"]})
        else:
            self.reply(503, {"error": "fixture_unavailable"})

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
        STATE["posts"].append([self.path, json.loads(body) if body else None])
        if self.path == "/cli/setup/online/proposals":
            self.reply(201, {"id": "cli_online_test", "coding_cli": True, "action": "coding_cli_online_setup",
                             "summary": "Set up Claude Code in ~/nova", "impact": "NOVA makes exactly these changes; nothing else on your computer changes.",
                             "changes": ["Add OPEN CLI for Claude Code under ONLINE CLIS. It opens in ~/nova.",
                                         "It runs online: what you type goes to Anthropic under your own account. NOVA stores no keys and never sees your sign-in.",
                                         "The first OPEN CLI installs Claude Code (Omarchy's launcher; the terminal shows the progress), then asks you to sign in."],
                             "model": "online:claude-code", "expires_at": time.time() + 120})
        elif self.path == "/approvals/cli_online_test/approve":
            STATE["set_up"] = True
            self.reply(200, {"status": "online_set_up", "profile_id": "claude-code", "label": "Claude Code", "project_label": "~/nova"})
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
  NovaClient { id: fakeNova; socketPath: "@SOCKET@" }
  TestCase {
    name: "OnlineSetup"
    when: novaPanel.opened
    function check(value, message) { if (!value) throw new Error(message) }
    function get(path) {
      var xhr = fakeNova.request(), complete = false, result = null
      xhr.onreadystatechange = function() { if (xhr.readyState === XMLHttpRequest.DONE) { result = xhr.responseText; complete = true } }
      xhr.open("GET", path); xhr.send()
      var n = 0; while (!complete && n++ < 100) wait(10)
      return JSON.parse(result)
    }
    function offer(index) { var rows = findChild(novaPanel, "onlineCliOfferRepeater"); return rows && rows.count > index ? rows.itemAt(index) : null }
    function inside(parent, name) { var r = findChild(parent, name); check(r !== null, "missing " + name); return r }
    function click(item) {
      novaPanel.revealInModelList(item); wait(80)
      var catcher = findChild(novaPanel, "panelKeyCatcher"), point = item.mapToItem(catcher, item.width / 2, item.height / 2)
      mouseClick(catcher, point.x, point.y, Qt.LeftButton); wait(150)
    }
    function shot(name) {
      if ("@CAPTURE@" === "") return
      wait(150)
      var done = false
      findChild(novaPanel, "panelKeyCatcher").parent.parent.grabToImage(function(image) { image.saveToFile("@CAPTURE@/" + name + ".png"); done = true })
      var n = 0; while (!done && n++ < 150) wait(20)
    }
    function refresh() { novaPanel.refreshCliProfiles(); wait(250) }
    function test_online_setup() {
      var results = []
      function test(name, fn) {
        try { fn(); results.push({name:name, passed:true}) }
        catch (e) { results.push({name:name, passed:false, error:String(e)}) }
      }
      try {
      previewHost.novaClient = fakeNova
      previewHost.acceptancePreview = false
      novaPanel.modelsUnavailable = false
      novaPanel.discoveredModels = [{id: "qwen3:8b", provider: "ollama", available: true}]
      novaPanel.panelView = "models"
      refresh()
      test("nothing set up: ONLINE CLIS offers Claude Code and Codex with SET UP", function() {
        check(findChild(novaPanel, "onlineCliSection").visible, "no ONLINE CLIS section")
        check(findChild(novaPanel, "onlineCliTitle").text === "ONLINE CLIS · 2", "title: " + findChild(novaPanel, "onlineCliTitle").text)
        check(findChild(novaPanel, "onlineCliOfferRepeater").count === 2 && inside(offer(0), "onlineOfferName").text === "Claude Code" && inside(offer(1), "onlineOfferName").text === "Codex", "offers wrong")
        check(inside(offer(0), "onlineSetUpButton").text === "SET UP", "no SET UP")
        var missing = findChild(novaPanel, "onlineCliMissingRepeater")
        check(missing.count === 1 && inside(missing.itemAt(0), "onlineMissingName").text === "Antigravity"
              && inside(missing.itemAt(0), "onlineMissingHint").text === "not on this computer · get it from antigravity.google", "Antigravity's line wrong")
        shot("1-offers")
      })
      test("SET UP shows the folders and the sign-in; the first folder is chosen", function() {
        click(inside(offer(0), "onlineSetUpButton")); wait(200)
        var card = inside(offer(0), "onlineSetupCard")
        check(card.visible && inside(offer(0), "onlineSetUpButton").text === "CLOSE", "card not open")
        check(novaPanel.onlineSetupProject === "~/Work", "chosen: " + novaPanel.onlineSetupProject)
        check(inside(card, "onlineSignInText").text.indexOf("your own Anthropic account") === 0, "sign-in: " + inside(card, "onlineSignInText").text)
        check(!inside(offer(1), "onlineSetupCard").visible, "Codex's card opened too")
        shot("2-setup")
      })
      test("choosing a folder and SET UP sends exactly the tool and folder, then the approval lists the changes", function() {
        var folders = [], walk = function(o) { if (!o) return; if (o.objectName === "onlineProjectButton") folders.push(o); for (var i = 0; i < o.children.length; i++) walk(o.children[i]) }
        walk(inside(offer(0), "onlineSetupCard"))
        check(folders.length === 2 && folders[1].text === "~/nova", "folders: " + folders.length)
        click(folders[1])
        click(inside(offer(0), "onlineSetupConfirm")); wait(300)
        var posts = get("/__posts__").posts
        check(posts.length === 1 && posts[0][0] === "/cli/setup/online/proposals" && JSON.stringify(posts[0][1]) === JSON.stringify({tool: "claude-code", project: "~/nova"}), "posts: " + JSON.stringify(posts))
        check(novaPanel.pendingApproval && novaPanel.pendingApproval.action === "coding_cli_online_setup", "no approval")
        check(findChild(novaPanel, "approvalChanges").visible && findChild(novaPanel, "approvalChanges").text.indexOf("Anthropic under your own account") > 0, "changes not shown")
        check(!findChild(novaPanel, "approvalVerdict").visible, "a verdict on an online setup")
        shot("3-approval")
      })
      test("approving adds OPEN CLI and says where", function() {
        novaPanel.decideApproval("approve"); wait(400); refresh()
        check(get("/__posts__").posts[1][0] === "/approvals/cli_online_test/approve", "decision not sent")
        check(novaPanel.approvalFeedback.indexOf("Claude Code set up") === 0, "feedback: " + novaPanel.approvalFeedback)
        check(novaPanel.panelView === "models", "not back on MODELS")
        var rows = findChild(novaPanel, "onlineCliRepeater")
        check(rows.count === 1 && inside(rows.itemAt(0), "onlineCliName").text === "Claude Code" && inside(rows.itemAt(0), "onlineCliOpenButton").text === "OPEN CLI", "no OPEN CLI row")
        check(findChild(novaPanel, "onlineCliOfferRepeater").count === 1 && inside(offer(0), "onlineOfferName").text === "Codex", "Claude Code still offered")
        check(findChild(novaPanel, "onlineCliTitle").text === "ONLINE CLIS · 2", "title after: " + findChild(novaPanel, "onlineCliTitle").text)
        shot("4-set-up")
      })
      test("installed, Antigravity gets SET UP with a Google sign-in", function() {
        get("/__set__/agy/1"); refresh()
        check(findChild(novaPanel, "onlineCliMissingRepeater").count === 0, "still shown as missing")
        var row = null; for (var i = 0; i < findChild(novaPanel, "onlineCliOfferRepeater").count; i++) if (inside(offer(i), "onlineOfferName").text === "Antigravity") row = offer(i)
        check(row !== null, "no Antigravity offer")
        click(inside(row, "onlineSetUpButton")); wait(250)
        check(inside(row, "onlineSignInText").text.indexOf("your own Google account") === 0, "sign-in: " + inside(row, "onlineSignInText").text)
        click(inside(row, "onlineSetupCancel")); wait(100)
      })
      test("a running OpenCode says CLI RUNNING on its model's card", function() {
        // Back on MODELS the panel re-read the models; this fake novad has none to give.
        novaPanel.modelsUnavailable = false; novaPanel.discoveredModels = [{id: "qwen3:8b", provider: "ollama", available: true}]; wait(100)
        var card = findChild(novaPanel, "installedModelsRepeater").itemAt(0), button = inside(card, "openCliButton")
        check(button.text === "OPEN CLI", "before: " + button.text)
        get("/__set__/opencode_running/1"); refresh()
        check(button.text === "CLI RUNNING" && novaPanel.cliLoadingModel("qwen3:8b"), "after: " + button.text)
        shot("5-running")
        get("/__set__/opencode_running/0"); refresh()
        check(button.text === "OPEN CLI", "after it closed: " + button.text)
      })
      } catch (e) { results.push({name: "harness setup", passed: false, error: String(e)}) }
      console.warn("ONLINE_RESULTS " + JSON.stringify(results))
    }
  }
'''


def main():
    server = fake_nova.server(Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    capture = os.environ.get("NOVA_ONLINE_CAPTURE", "")
    try:
        fixture = preview.scenarios()["installed-no-selection"]
        with tempfile.TemporaryDirectory(prefix="nova-online-", dir="/tmp") as raw:
            shell = preview.prepare_config(Path(raw), fixture)
            text = shell.read_text().replace("import QtQuick\n", "import QtQuick\nimport QtTest\n", 1)
            check = QML_CHECK.replace("@SOCKET@", server.server_address).replace("@CAPTURE@", capture)
            shell.write_text(text.rsplit("\n}", 1)[0] + check + "\n}\n")
            result = subprocess.run(["quickshell", "--path", raw], capture_output=True, text=True, timeout=60)
            output = result.stdout + result.stderr
        line = next((row.split("ONLINE_RESULTS ", 1)[1] for row in output.splitlines() if "ONLINE_RESULTS " in row), None)
        if line is None:
            print(output)
            raise SystemExit("Online setup interaction did not complete")
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
