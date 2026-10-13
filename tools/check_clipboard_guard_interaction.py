#!/usr/bin/env python3
"""NOVA's text fields leave the clipboard alone until the user types (2026-10-13).

Marketplace review #11195: on released Qt (6.11.2, 6.12.0) every *editable* TextInput
reads the whole clipboard text whenever the clipboard changes (QQuickTextInput::
q_canPasteChanged calls QMimeData::text(); fixed upstream 2026-09-13 but not yet
released). The read has no size limit and no overall deadline, so another clipboard
owner could grow or stall the shared shell before anything is pasted. NOVA's two
fields are therefore read-only until the user clicks or types in them, and read-only
again once hidden or the panel closes.

Run under a private compositor. A stub puts a large text on the clipboard with wl-copy
and measures the shell's memory before and after: a read shows up as a jump of at least
the clipboard's size. The typed case is the positive control (the measurement works).
NOVA_CLIPBOARD_CAPTURE=<dir> saves the message box (3x) with the caret before and after typing.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
from threading import Thread
import time

import onboarding_preview as preview

PAYLOAD_MB = 96
STATE = {"config": "", "payload": "", "copies": 0}


def shell_rss_kb():
    pid = subprocess.run(["pgrep", "-n", "-f", "quickshell --path " + STATE["config"]],
                         capture_output=True, text=True).stdout.strip()
    for row in Path(f"/proc/{pid}/status").read_text().splitlines():
        if row.startswith("VmRSS:"):
            return int(row.split()[1])
    raise RuntimeError("no VmRSS")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path != "/__clip__":
            self.reply(404, {"error": "fixture_path"}); return
        # Empty the clipboard first: the shell drops what it read from the last offer,
        # so the next jump is this offer's alone.
        subprocess.run(["wl-copy", "--clear"], check=True)
        time.sleep(1.5)
        before = shell_rss_kb()
        STATE["copies"] += 1
        # A new text each time, so it is a new clipboard offer the shell has never read.
        with open(STATE["payload"], "r+b") as payload:
            payload.write(b"%08d" % STATE["copies"])
        with open(STATE["payload"], "rb") as payload:
            subprocess.run(["wl-copy", "--type", "text/plain;charset=utf-8"], stdin=payload, check=True)
        time.sleep(2.5)
        self.reply(200, {"grew_mb": round((shell_rss_kb() - before) / 1024)})

    def reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


QML_CHECK = r'''
  TestCase {
    name: "ClipboardGuardInteraction"
    when: novaPanel.opened
    function check(value, message) { if (!value) throw new Error(message) }
    function item(name) { var r = findChild(novaPanel, name); check(r !== null, "missing " + name); return r }
    function clip() {
      var xhr = new XMLHttpRequest(), complete = false, result = null
      xhr.onreadystatechange = function() { if (xhr.readyState === XMLHttpRequest.DONE) { result = xhr.responseText; complete = true } }
      xhr.open("GET", "http://127.0.0.1:@PORT@/__clip__"); xhr.send()
      var n = 0; while (!complete && n++ < 1500) wait(10)
      check(complete, "clipboard stub did not answer")
      return JSON.parse(result).grew_mb
    }
    function capture(name) {
      if (!"@CAPTURE@") return
      var done = false, row = item("chatInputRow")
      row.grabToImage(function(image) { image.saveToFile("@CAPTURE@/" + name + ".png"); done = true }, Qt.size(row.width * 3, row.height * 3))
      var n = 0; while (!done && n++ < 100) wait(20)
    }
    function test_clipboard_guard() {
      var results = []
      function test(name, fn) {
        try { fn(); results.push({name:name, passed:true}) }
        catch (e) { results.push({name:name, passed:false, error:String(e)}) }
      }
      var chat = item("chatInput"), add = item("addModelInput")
      test("both fields start read-only", function() {
        check(chat.readOnly && add.readOnly, "chat " + chat.readOnly + ", add " + add.readOnly)
      })
      test("a focused chat box nobody typed in does not read the clipboard", function() {
        novaPanel.showView("chat"); wait(100)
        chat.forceActiveFocus(); wait(100)
        check(chat.activeFocus, "chat box not focused")
        var grew = clip()
        check(grew < @SMALL@, "shell grew " + grew + " MB")
        check(!chat.canPaste, "Qt inspected the clipboard")
        capture("caret-untouched")
      })
      test("Enter does not make the box editable; the chat box's own key handler still runs", function() {
        keyPress(Qt.Key_Return)
        check(novaPanel.chatSubmissionKeyDown, "chat box key handler did not run")
        keyRelease(Qt.Key_Return)
        check(chat.readOnly, "Enter made the box editable")
      })
      test("typing makes the box editable and keeps the first key", function() {
        keyClick(Qt.Key_H); keyClick(Qt.Key_I)
        check(!chat.readOnly, "still read-only")
        check(chat.text === "hi", "text: '" + chat.text + "'")
        keyClick(Qt.Key_Backspace)
        check(chat.text === "h", "backspace: '" + chat.text + "'")
        capture("caret-typing")
      })
      test("measurement works: the editable box reads a new clipboard (Qt's own behaviour)", function() {
        var grew = clip()
        check(grew >= @LARGE@, "shell grew only " + grew + " MB")
      })
      test("a paste shortcut in an untouched box pastes", function() {
        chat.text = ""
        novaPanel.showView("models"); wait(100)
        novaPanel.showView("chat"); wait(100)
        check(chat.readOnly, "not read-only again after the view switch")
        chat.forceActiveFocus(); wait(50)
        keySequence(StandardKey.Paste)
        wait(200)
        check(!chat.readOnly && chat.text.length > 0, "paste: readOnly " + chat.readOnly + ", length " + chat.text.length)
        chat.text = ""
      })
      test("MODELS: the ADD A MODEL field nobody typed in does not read the clipboard", function() {
        novaPanel.showView("models"); novaPanel.catalogDownloadsExpanded = true; wait(150)
        check(add.visible, "ADD A MODEL field hidden")
        check(chat.readOnly, "hidden chat box still editable")
        add.forceActiveFocus(); wait(50)
        var grew = clip()
        check(grew < @SMALL@, "shell grew " + grew + " MB")
      })
      test("clicking the ADD A MODEL field makes it editable", function() {
        mouseClick(add)
        wait(50)
        check(!add.readOnly, "still read-only after a click")
        keyClick(Qt.Key_G)
        check(add.text === "g", "text: '" + add.text + "'")
      })
      test("a closed panel does not read the clipboard", function() {
        novaPanel.close(); wait(300)
        var grew = clip()
        check(grew < @SMALL@, "shell grew " + grew + " MB")
        check(chat.readOnly && add.readOnly, "editable while closed: chat " + !chat.readOnly + ", add " + !add.readOnly)
      })
      console.warn("CLIPBOARD_GUARD_RESULTS " + JSON.stringify(results))
    }
  }
'''


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        fixture = preview.scenarios()["installed-no-selection"]
        with tempfile.TemporaryDirectory(prefix="nova-clipboard-", dir="/tmp") as raw:
            payload = Path(raw) / "clipboard.txt"
            payload.write_bytes(b"a" * (PAYLOAD_MB * 1024 * 1024))
            STATE.update(config=raw, payload=str(payload))
            shell = preview.prepare_config(Path(raw), fixture)
            text = shell.read_text().replace("import QtQuick\n", "import QtQuick\nimport QtTest\n", 1)
            check = (QML_CHECK.replace("@PORT@", str(server.server_port))
                     .replace("@CAPTURE@", os.environ.get("NOVA_CLIPBOARD_CAPTURE", ""))
                     .replace("@SMALL@", str(PAYLOAD_MB // 4)).replace("@LARGE@", str(PAYLOAD_MB * 2 // 3)))
            shell.write_text(text.rsplit("\n}", 1)[0] + check + "\n}\n")
            try:
                result = subprocess.run(["quickshell", "--path", raw], capture_output=True, text=True, timeout=120)
            finally:
                subprocess.run(["wl-copy", "--clear"], check=False)
            output = result.stdout + result.stderr
        line = next((row.split("CLIPBOARD_GUARD_RESULTS ", 1)[1] for row in output.splitlines() if "CLIPBOARD_GUARD_RESULTS " in row), None)
        if line is None:
            print(output)
            raise SystemExit("Clipboard guard interaction did not complete")
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
