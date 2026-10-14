#!/usr/bin/env python3
"""NOVA's text fields never let Qt read the clipboard (2026-10-13, follow-up 2026-10-14).

Marketplace review #11195: on released Qt (6.11.2, 6.12.0) every *editable* TextInput
reads the whole clipboard text whenever the clipboard changes, and again the moment it
turns editable (QQuickTextInput::q_canPasteChanged calls QMimeData::text(); fixed upstream
2026-09-13 but not yet released). The read has no size limit and no overall deadline, so
another clipboard owner could grow or stall the shared shell. NOVA's fields therefore stay
read-only to Qt and edit their own text; paste goes through wl-paste in its own process,
capped in size and time.

Run under a private compositor. A stub puts a large text on the clipboard with wl-copy
and measures the shell's memory before and after: a read shows up as a jump of at least
the clipboard's size. A plain editable TextInput is the positive control (the measurement
works). A wl-paste stub on PATH can stall, to check the paste deadline.
NOVA_CLIPBOARD_CAPTURE=<dir> saves the message box (3x) with the caret before and after typing.
"""
import json
import os
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
from threading import Thread
import time

import onboarding_preview as preview

PAYLOAD_MB = 96
SMALL_TEXT = "nova paste \u2713 ok"
WTYPE_TEXT = "H\u00e9llo \u0105\u017e \u043f\u0440\u0438\u0432\u0435\u0442"
STATE = {"config": "", "payload": "", "copies": 0, "stall": ""}


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
        if self.path == "/__rss__":
            self.reply(200, {"kb": shell_rss_kb()}); return
        if self.path == "/__small__":
            subprocess.run(["wl-copy", "--type", "text/plain;charset=utf-8", SMALL_TEXT], check=True)
            time.sleep(0.5)
            self.reply(200, {"text": SMALL_TEXT}); return
        if self.path in ("/__stall__?on=1", "/__stall__?on=0"):
            flag = Path(STATE["stall"])
            if self.path.endswith("1"):
                flag.touch()
            else:
                flag.unlink(missing_ok=True)
            self.reply(200, {"stall": flag.exists()}); return
        if self.path in ("/__wtype_text__", "/__wtype_paste__"):
            # Real Wayland key events (virtual keyboard) through Qt's xkb and input-method path.
            if not shutil.which("wtype"):
                self.reply(200, {"skipped": True}); return
            args = [WTYPE_TEXT] if self.path.endswith("text__") else ["-M", "ctrl", "v", "-m", "ctrl"]
            subprocess.run(["wtype", *args], check=True, timeout=10)
            time.sleep(0.5)
            self.reply(200, {"text": WTYPE_TEXT}); return
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
    function get(path) {
      var xhr = new XMLHttpRequest(), complete = false, result = null
      xhr.onreadystatechange = function() { if (xhr.readyState === XMLHttpRequest.DONE) { result = xhr.responseText; complete = true } }
      xhr.open("GET", "http://127.0.0.1:@PORT@" + path); xhr.send()
      var n = 0; while (!complete && n++ < 1500) wait(10)
      check(complete, "clipboard stub did not answer " + path)
      return JSON.parse(result)
    }
    function clip() { return get("/__clip__").grew_mb }
    function rss() { return get("/__rss__").kb }
    function waitFor(fn, ms) { var start = Date.now(); while (!fn() && Date.now() - start < ms) wait(20); return Date.now() - start }
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
      test("typing edits the box, which stays read-only to Qt", function() {
        keyClick(Qt.Key_H); keyClick(Qt.Key_I)
        check(chat.text === "hi", "text: '" + chat.text + "'")
        keyClick(Qt.Key_Backspace)
        check(chat.text === "h", "backspace: '" + chat.text + "'")
        check(chat.readOnly, "typing made the box editable")
        capture("caret-typing")
      })
      test("a box the user typed in does not read a new clipboard", function() {
        var grew = clip()
        check(grew < @SMALL@, "shell grew " + grew + " MB")
      })
      test("editing keys: Home, Delete, Ctrl+Backspace, undo, redo, Select All, Cut", function() {
        chat.text = ""
        keyClick(Qt.Key_A); keyClick(Qt.Key_B); keyClick(Qt.Key_Space); keyClick(Qt.Key_C)
        check(chat.text === "ab c", "typed: '" + chat.text + "'")
        keyClick(Qt.Key_Home); keyClick(Qt.Key_X)
        check(chat.text === "xab c", "Home then x: '" + chat.text + "'")
        keyClick(Qt.Key_Delete)
        check(chat.text === "xb c", "Delete: '" + chat.text + "'")
        keyClick(Qt.Key_End); keyClick(Qt.Key_Backspace, Qt.ControlModifier)
        check(chat.text === "xb ", "Ctrl+Backspace: '" + chat.text + "'")
        keySequence(StandardKey.Undo)
        check(chat.text === "xb c", "undo: '" + chat.text + "'")
        keySequence(StandardKey.Redo)
        check(chat.text === "xb ", "redo: '" + chat.text + "'")
        keySequence(StandardKey.SelectAll); keyClick(Qt.Key_Z)
        check(chat.text === "z", "Select All then z: '" + chat.text + "'")
        keySequence(StandardKey.SelectAll); keySequence(StandardKey.Cut)
        check(chat.text === "", "cut: '" + chat.text + "'")
        check(chat.readOnly, "editing keys made the box editable")
      })
      test("Enter submits and the chat box's own key handler still runs", function() {
        var accepted = 0, count = function() { accepted++ }
        chat.accepted.connect(count)
        keyPress(Qt.Key_Return)
        check(novaPanel.chatSubmissionKeyDown, "chat box key handler did not run")
        keyRelease(Qt.Key_Return)
        chat.accepted.disconnect(count)
        check(accepted === 1, "accepted " + accepted + " times")
      })
      test("real keyboard events (wtype): typing, including non-ASCII, and Ctrl+V", function() {
        chat.text = ""; chat.forceActiveFocus(); wait(50)
        var typed = get("/__wtype_text__")
        if (typed.skipped) return
        waitFor(function() { return chat.text === typed.text }, 2000)
        check(chat.text === typed.text, "typed '" + chat.text + "'")
        var small = get("/__small__").text
        chat.text = ""
        get("/__wtype_paste__")
        waitFor(function() { return chat.text !== "" }, 3000)
        check(chat.text === small, "pasted '" + chat.text + "'")
        check(chat.readOnly, "real keys made the box editable")
        chat.text = ""
      })
      test("Alt+2 still switches views from the chat box, typing nothing", function() {
        keyClick(Qt.Key_2, Qt.AltModifier); wait(50)
        check(novaPanel.shownView === "models", "view " + novaPanel.shownView)
        check(chat.text === "", "typed '" + chat.text + "'")
        novaPanel.showView("chat"); wait(100); chat.forceActiveFocus()
      })
      test("Ctrl+V pastes through the bounded reader", function() {
        var small = get("/__small__").text
        chat.text = ""; chat.forceActiveFocus()
        keySequence(StandardKey.Paste)
        waitFor(function() { return chat.text !== "" }, 3000)
        check(chat.text === small, "pasted '" + chat.text + "'")
        check(chat.readOnly, "paste made the box editable")
      })
      test("a huge clipboard is refused at the cap; the shell never reads it", function() {
        check(clip() < @SMALL@, "the new offer alone grew the shell")
        var before = rss(), had = chat.text
        novaPanel.chatError = ""
        keySequence(StandardKey.Paste)
        var took = waitFor(function() { return novaPanel.chatError !== "" }, 4000)
        var grew = Math.round((rss() - before) / 1024)
        check(novaPanel.chatError.indexOf("too long") >= 0, "note: '" + novaPanel.chatError + "' after " + took + " ms")
        check(chat.text === had, "text changed to " + chat.text.length + " characters")
        check(grew < @SMALL@, "shell grew " + grew + " MB")
      })
      test("a clipboard that never answers: paste gives up after 2 s, the shell keeps running", function() {
        get("/__stall__?on=1")
        var last = Date.now(), worst = 0
        var ticker = Qt.createQmlObject("import QtQuick; Timer { interval: 50; repeat: true; running: true }", novaPanel)
        ticker.triggered.connect(function() { var now = Date.now(); worst = Math.max(worst, now - last); last = now })
        novaPanel.chatError = ""
        keySequence(StandardKey.Paste)
        var took = waitFor(function() { return novaPanel.chatError !== "" }, 6000)
        ticker.destroy(); get("/__stall__?on=0")
        check(novaPanel.chatError.indexOf("didn't answer") >= 0, "note: '" + novaPanel.chatError + "'")
        check(took >= 1500 && took < 4000, "gave up after " + took + " ms")
        check(worst < 500, "the shell stalled for " + worst + " ms")
        novaPanel.chatError = ""
      })
      test("MODELS: the ADD A MODEL field nobody typed in does not read the clipboard", function() {
        novaPanel.showView("models"); novaPanel.catalogDownloadsExpanded = true; wait(150)
        check(add.visible, "ADD A MODEL field hidden")
        add.forceActiveFocus(); wait(50)
        var grew = clip()
        check(grew < @SMALL@, "shell grew " + grew + " MB")
      })
      test("clicking ADD A MODEL with a big clipboard already there does not read it (review follow-up)", function() {
        var before = rss()
        mouseClick(add); wait(2500)
        var grew = Math.round((rss() - before) / 1024)
        check(add.readOnly, "the click made the field editable")
        check(grew < @SMALL@, "shell grew " + grew + " MB on the click")
        keyClick(Qt.Key_G)
        check(add.text === "g", "text: '" + add.text + "'")
      })
      test("measurement works: a plain editable TextInput reads a new clipboard (Qt's own behaviour)", function() {
        var probe = Qt.createQmlObject("import QtQuick; TextInput {}", novaPanel)
        var grew = clip()
        probe.destroy(); wait(50)
        check(grew >= @LARGE@, "shell grew only " + grew + " MB")
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
            STATE.update(config=raw, payload=str(payload), stall=str(Path(raw) / "stall"))
            stubs = Path(raw) / "bin"
            stubs.mkdir()
            (stubs / "wl-paste").write_text(f'''#!/bin/sh
[ -e "{STATE["stall"]}" ] && exec sleep 30
exec {shutil.which("wl-paste")} "$@"
''')
            (stubs / "wl-paste").chmod(0o755)
            env = dict(os.environ, PATH=f"{stubs}:{os.environ['PATH']}")
            shell = preview.prepare_config(Path(raw), fixture)
            text = shell.read_text().replace("import QtQuick\n", "import QtQuick\nimport QtTest\n", 1)
            check = (QML_CHECK.replace("@PORT@", str(server.server_port))
                     .replace("@CAPTURE@", os.environ.get("NOVA_CLIPBOARD_CAPTURE", ""))
                     .replace("@SMALL@", str(PAYLOAD_MB // 4)).replace("@LARGE@", str(PAYLOAD_MB * 2 // 3)))
            shell.write_text(text.rsplit("\n}", 1)[0] + check + "\n}\n")
            try:
                result = subprocess.run(["quickshell", "--path", raw], capture_output=True, text=True, timeout=180, env=env)
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
