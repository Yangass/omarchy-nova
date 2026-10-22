#!/usr/bin/env python3
"""A fast model's stream through the real Panel.qml: does the panel keep up?

2026-10-22, a friend's RTX 5080 (qwen3:8b, a token every ~7 ms): replies ended
"NOVA · INCOMPLETE / novad stream ended early", and an approval card never showed
(the panel saw the request only after it had expired). novad itself streamed on
time (curl), so the panel fell behind. This stub streams like that GPU and records
when each event was sent and when the panel had it. Isolated: a Unix-socket stub,
no production NOVA, model or action.
"""
from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
import subprocess
import tempfile
from threading import Event, Lock, Thread
import time

import fake_nova
import onboarding_preview as preview

SESSION = "chat_" + "F" * 32
# Model-sized pieces of a markdown answer (lists, bold, paragraphs), as a chat reply is.
PIECES = ["Napoleon", " Bonaparte", " rose", " to", " **power**", " after", " the", " French",
          " Revolution", ".", "\n\n", "1", ".", " **Reforms**", ":", " the", " Civil", " Code",
          " made", " laws", " equal", ";", "\n", "2", ".", " **Wars**", ":", " Austerlitz",
          " (", "1805", ")", " and", " Russia", " (", "1812", ")", ".", "\n\n"]
TOKENS = 900          # about 600 words
INTERVAL = float(__import__("os").environ.get("NOVA_FAST_INTERVAL", "0.007"))  # the 5080's pace


QML_CHECK = r'''
  NovaClient { id: fakeNova; socketPath: "@SOCKET@" }
  property var seen: ({})
  Connections {
    target: novaPanel
    function onPendingApprovalChanged() { if (novaPanel.pendingApproval && !seen.approval) seen.approval = Date.now() }
    function onChatBusyChanged() { if (!novaPanel.chatBusy && !seen.idle) seen.idle = Date.now() }
  }
  TestCase {
    name: "FastStream"
    when: novaPanel.opened
    function waitUntil(predicate, timeout) {
      var elapsed = 0
      while (!predicate() && elapsed < timeout) { wait(20); elapsed += 20 }
      return predicate()
    }
    function send(text) {
      var field = findChild(novaPanel, "chatInput")
      field.text = text; field.forceActiveFocus(); keyClick(Qt.Key_Return)
    }
    function test_fast() {
      var out = {}
      previewHost.acceptancePreview = false
      novaPanel.pendingApproval = null
      novaPanel.onboardingState = "READY"; novaPanel.onboardingActions = ["CHAT"]
      novaPanel.onboardingSnapshotFresh = true; novaPanel.onboardingReceivedAt = Date.now()
      novaPanel.onboardingExplicitSelection = true
      novaPanel.onboardingSelectedProvider = "ollama"; novaPanel.onboardingSelectedModel = "fixture:model"
      ;["long", "approval"].forEach(function(kind) {
        seen = ({})
        var started = Date.now()
        send(kind)
        waitUntil(function() { return seen.idle || (kind === "approval" && seen.approval) }, 60000)
        if (kind === "approval") waitUntil(function() { return seen.idle }, 60000)
        out[kind] = {started: started, approval: seen.approval || 0, idle: seen.idle || 0,
                     status: novaPanel.chatError === "" ? "complete" : "incomplete", error: novaPanel.chatError}
        novaPanel.clearPendingApproval(); novaPanel.chatError = ""
        novaPanel.onboardingSnapshotFresh = true; novaPanel.onboardingReceivedAt = Date.now()
      })
      console.log("FAST_STREAM_RESULTS " + JSON.stringify(out))
      Qt.quit()
    }
  }
'''


def main():
    sent = {}
    lock = Lock()
    decided = Event()

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
            if self.path.startswith("/chat/sessions/"):
                with lock: turn = sent.get("turn")
                self.json(200, {"session_id": SESSION, "busy": False, "history": {"retained_turns": 1},
                                "last_completion": {"turn_id": turn, "retention": "RETAINED", "evicted_turns": 0}
                                if turn else None})
                return
            self.json(404, {"error": "fixture_unknown"})

        def event(self, name, payload):
            self.wfile.write(f"event: {name}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n".encode())
            self.wfile.flush()

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if self.path == "/chat/sessions":
                self.json(201, {"session_id": SESSION, "busy": False, "history": {"retained_turns": 0},
                                "last_completion": None})
                return
            if self.path.startswith("/approvals/"):
                decided.set()
                self.json(200, {"status": "denied"})
                return
            if self.path != "/chat/stream":
                self.json(404, {"error": "fixture_unknown"})
                return
            kind = json.loads(body).get("message")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            count = TOKENS if kind == "long" else TOKENS // 2
            for index in range(count):
                self.event("token", {"text": PIECES[index % len(PIECES)]})
                time.sleep(INTERVAL)
            if kind == "approval":
                with lock: sent["approval"] = time.time() * 1000
                self.event("approval_required", {"id": "fixture-approval", "action": "open_known_application",
                                                 "expires_at": time.time() + 120, "application": "Terminal",
                                                 "impact": "Fixture only."})
                decided.wait(20)
                self.event("action_result", {"id": "fixture-approval", "status": "denied",
                                             "action": "open_known_application"})
                for index in range(40):
                    self.event("token", {"text": PIECES[index % len(PIECES)]})
                    time.sleep(INTERVAL)
            with lock: sent[kind + "_done"], sent["turn"] = time.time() * 1000, "turn_" + kind
            self.event("done", {"done": True, "session_id": SESSION, "turn_id": "turn_" + kind})

    with fake_nova.server(Stub) as server:
        Thread(target=server.serve_forever, daemon=True).start()
        with tempfile.TemporaryDirectory(prefix="nova-fast-stream-check-", dir="/tmp") as raw:
            target = Path(raw)
            fixture = preview.scenarios()["verified-awaiting-activation"]
            fixture["label"] = "FAST STREAM CHECK // ISOLATED STUB"
            fixture["host"] = {"online": True, "runtime_state": "idle", "active_provider": "ollama",
                               "active_model": "fixture:model", "active_available": True, "runtime_ready": True}
            shell_file = preview.prepare_config(target, fixture)
            text = shell_file.read_text().replace("import QtQuick\n", "import QtQuick\nimport QtTest\n", 1)
            text = text.replace('property var novaClient: null', 'property var novaClient: fakeNova')
            text = text.rsplit("\n}", 1)[0] + QML_CHECK.replace("@SOCKET@", server.server_address) + "\n}\n"
            shell_file.write_text(text)
            result = subprocess.run(["quickshell", "--path", str(target)], capture_output=True, text=True, timeout=240)
            output = result.stdout + result.stderr
        server.shutdown()

    marker = "FAST_STREAM_RESULTS "
    encoded = next((line.split(marker, 1)[1] for line in output.splitlines() if marker in line), None)
    if encoded is None:
        print(output[-3000:])
        raise SystemExit("Fast stream harness did not complete")
    results = json.loads(encoded)
    failed = False
    long, approval = results["long"], results["approval"]
    lag_done = long["idle"] - sent["long_done"] if long["idle"] else None
    ok = long["status"] == "complete" and lag_done is not None and lag_done < 1000
    print(("PASS " if ok else "FAIL ") + f"a {TOKENS}-token reply at {INTERVAL * 1000:.0f} ms a token completes "
          f"(status {long['status']!r}, done handled {lag_done} ms after it was sent, error {long['error']!r})")
    failed |= not ok
    lag_card = approval["approval"] - sent["approval"] if approval["approval"] else None
    ok = lag_card is not None and lag_card < 1000
    print(("PASS " if ok else "FAIL ") + f"the approval card shows within 1 s of the request (after {lag_card} ms)")
    failed |= not ok
    ok = approval["status"] == "complete"
    print(("PASS " if ok else "FAIL ") + f"the reply after the approval completes (status {approval['status']!r}, "
          f"error {approval['error']!r})")
    failed |= not ok
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
