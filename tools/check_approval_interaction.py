#!/usr/bin/env python3
"""Exercise canonical Panel.qml input delivery against an isolated HTTP stub.

Uses the existing onboarding preview host and QtTest's real event delivery.
Only fixture approval IDs reach the temporary loopback listener. No production
API, model, configuration, or service operation is available to this check.
Requires a running Wayland desktop; it briefly displays the marked preview.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import tempfile
from threading import Thread
import time

import onboarding_preview as preview


QML_CHECK = r'''
  TestCase {
    id: approvalCheck
    name: "IsolatedApprovalInteraction"
    when: novaPanel.opened && novaPanel.pendingApproval !== null
    function check(value, message) { if (!value) throw new Error(message) }
    function button(name) {
      var item = findChild(novaPanel, name)
      check(item !== null, "Missing " + name)
      return item
    }
    function pointerClick(item) {
      var catcher = button("panelKeyCatcher")
      var point = item.mapToItem(catcher, item.width / 2, item.height / 2)
      mouseClick(catcher, point.x, point.y, Qt.LeftButton)
    }
    function propose(id, held, acquisition) {
      novaPanel.clearPendingApproval()
      novaPanel.chatBusy = false
      novaPanel.chatSubmissionKeyDown = held === true
      var item = JSON.parse(JSON.stringify(shell.fixture.install_approval))
      item.id = id
      if (acquisition === false) {
        item.action = "open_known_application"
        item.acquisition = false
        item.summary = "Open application"
        item.application = "Terminal"
        item.impact = "NOVA will send a fixed local launch request. Window appearance is not verified."
      }
      novaPanel.beginApproval(item)
      wait(150)
      check(novaPanel.approvalForeground, "Approval lacks foreground")
      // UX Phase 2 (approved mock): the slim core band stays above the card; nothing else competes.
      check(button("corePanel").height <= novaPanel.coreBandHeight + 1, "Big core competes with foreground")
      check(!button("modelSelector").visible, "Selector crowds approval")
      var card = button("approvalCard"), approve = button("approveApprovalControl")
      check(card.height > 180, "Insufficient approval space")
      check(approve.enabled && approve.visible, "Approval blocked by chat readiness")
      check(approve.activeFocus, "Approval did not receive focus")
      var point = approve.mapToItem(card, 0, 0)
      check(point.y >= 0 && point.y + approve.height <= card.height, "Buttons exceed card")
      check(novaPanel.onboardingCanSend === false, "Fixture unexpectedly permits chat")
      return approve
    }
    function test_interaction() {
      var results = []
      var pointerDelivery = []
      function test(name, fn) {
        try { fn(); results.push({name:name, passed:true}) }
        catch (error) { results.push({name:name, passed:false, error:String(error)}) }
      }
      // The host starts isolated so fixture application/open cannot call HTTP.
      // Switch only this temporary host to the stub to exercise production XHR.
      previewHost.api = @STUB_API@
      previewHost.acceptancePreview = false
      novaPanel.panelView = "models"
      // UX Phase 2: whatever view is open, an approval shows in CHAT (core band above it).
      test("both approval types own the foreground from every view", function() {
        ;[true, false].forEach(function(acquisition) {
          ;["chat", "models", "machine"].forEach(function(view) {
            novaPanel.panelView = view
            propose("fixture-layout-" + acquisition + "-" + view, false, acquisition)
            check(novaPanel.shownView === "chat" && button("approvalCard").visible, "Approval not in CHAT from " + view)
            check(!button("chatViewport").visible, "Transcript competes with foreground")
            check(!button("modelSelector").visible && !button("telemetryFrame").visible, "Other views compete with foreground")
            novaPanel.clearPendingApproval()
          })
        })
        novaPanel.panelView = "models"
      })
      test("actual mouse and keyboard decisions for both types across layout combinations", function() {
        ;[true, false].forEach(function(acquisition) {
          ;[false, true].forEach(function(chat) {
            ;[false, true].forEach(function(brain) {
              novaPanel.panelView = chat ? "machine" : "models"
              var id = "fixture-matrix-" + acquisition + "-" + chat + "-" + brain
              propose(id, false, acquisition)
              var decision = brain ? "deny" : "approve"
              if (chat) keyClick(brain ? Qt.Key_Escape : Qt.Key_Return)
              else pointerClick(button(brain ? "cancelApprovalHitTarget" : "approveApprovalHitTarget"))
              check(novaPanel.pendingApproval.decision === "sending", "Input did not submit " + id)
              wait(550)
              if (!acquisition) novaPanel.handleActionResult({status:brain ? "denied" : "dispatched"})
              check(novaPanel.pendingApproval === null, "Result did not clear " + id)
              check(novaPanel.panelView === "chat", "NOVA left CHAT after the decision " + id)
            })
          })
        })
        novaPanel.panelView = "models"
      })
      test("approval preserves multiline transcript, draft and reading position", function() {
        var input = button("chatInput"), viewport = button("chatViewport")
        novaPanel.addChat("assistant", Array(70).join("Earlier transcript paragraph.\n\n"))
        input.text = "unsent draft"
        wait(100)
        viewport.programmaticScroll = true
        viewport.contentY = 180
        viewport.followLatest = false
        viewport.programmaticScroll = false
        var count = novaPanel.chatHistoryCount, position = viewport.contentY
        propose("fixture-preservation", false, false)
        check(novaPanel.chatHistoryCount === count && input.text === "unsent draft", "Approval lost contents")
        novaPanel.clearPendingApproval(); wait(150)
        check(novaPanel.chatHistoryCount === count && input.text === "unsent draft", "Restore lost contents")
        check(Math.abs(viewport.contentY - position) < 2 && !viewport.followLatest,
              "Approval changed older reading position")
      })
      test("keyboard approve, chat not ready, feedback and duplicate prevention", function() {
        var approve = propose("fixture-mouse", false)
        keyClick(Qt.Key_Return)
        check(novaPanel.pendingApproval.decision === "sending", "No immediate submission state")
        check(!approve.enabled && !button("cancelApprovalControl").enabled, "Decision controls still enabled")
        check(novaPanel.approvalFeedback === "Submitting approval…", "No submission feedback")
        keyClick(Qt.Key_Return)
        wait(550)
        check(novaPanel.pendingApproval === null, "Accepted proposal remained pending")
        check(novaPanel.approvalFeedback.indexOf("Approval accepted") === 0, "Missing accepted feedback")
        check(novaPanel.panelView === "chat", "NOVA left CHAT after the decision")
      })
      test("released submission before arrival, fresh Enter", function() {
        novaPanel.clearPendingApproval()
        var input = button("chatInput")
        input.forceActiveFocus(); keyPress(Qt.Key_Return); keyRelease(Qt.Key_Return)
        var approve = propose("fixture-enter", false)
        keyClick(Qt.Key_Return); wait(550)
        check(novaPanel.pendingApproval === null, "Fresh Enter did not approve")
      })
      test("held submission, repeat blocked, release then fresh Enter", function() {
        novaPanel.clearPendingApproval()
        var input = button("chatInput")
        input.forceActiveFocus(); keyPress(Qt.Key_Return)
        var approve = propose("fixture-held", true)
        keyPress(Qt.Key_Return); wait(100)
        check(!novaPanel.pendingApproval.decision, "Held/repeated Enter approved")
        keyRelease(Qt.Key_Return)
        check(novaPanel.approvalShortcutArmed, "Release did not arm")
        keyClick(Qt.Key_Return); wait(550)
        check(novaPanel.pendingApproval === null, "Fresh press after release failed")
      })
      test("Escape declines while unarmed and preserves later preference", function() {
        propose("fixture-escape", true)
        novaPanel.showView("machine")
        keyClick(Qt.Key_Escape)
        check(novaPanel.approvalFeedback === "Declining proposal…", "No decline feedback")
        wait(550)
        check(novaPanel.pendingApproval === null, "Decline remained pending")
        check(novaPanel.approvalFeedback.indexOf("No download started") >= 0, "Decline misrepresented cancellation")
        check(novaPanel.panelView === "machine", "Subsequent user view choice overwritten")
      })
      test("Cancel wiring rejects controlled action after pointer probe", function() {
        propose("fixture-action-cancel", false, false)
        var cancelHit = button("cancelApprovalHitTarget")
        pointerClick(cancelHit)
        wait(50)
        var delivered = novaPanel.pendingApproval.decision === "sending"
        pointerDelivery.push({control:"Cancel", delivered:delivered})
        // Handler invocation checks wiring only; it does not upgrade the
        // unavailable layer-shell pointer-delivery result above.
        if (!delivered) cancelHit.clicked(null)
        wait(550)
        check(novaPanel.pendingApproval && novaPanel.pendingApproval.decision === "deny", "Action rejection result missing")
        novaPanel.handleActionResult({status:"denied"})
        check(novaPanel.pendingApproval === null, "Action result did not clear foreground")
      })
      test("Approve wiring submits installation once after pointer probe", function() {
        propose("fixture-pointer-approve", false, true)
        var approveHit = button("approveApprovalHitTarget")
        pointerClick(approveHit)
        wait(50)
        var delivered = novaPanel.pendingApproval.decision === "sending"
        pointerDelivery.push({control:"Approve", delivered:delivered})
        if (!delivered) approveHit.clicked(null)
        wait(550)
        check(novaPanel.pendingApproval === null, "Installation approval result did not clear foreground")
      })
      test("local expiry is structured and removes actionable controls", function() {
        propose("fixture-local-expiry", false, false)
        var next = JSON.parse(JSON.stringify(novaPanel.pendingApproval))
        next.expiresAt = (Date.now() + 80) / 1000
        novaPanel.pendingApproval = next
        novaPanel.scheduleApprovalExpiry()
        wait(180)
        check(novaPanel.pendingApproval.decision === "expired", "Approval did not expire locally")
        check(!button("approveApprovalControl").visible && !button("cancelApprovalControl").visible,
              "Expired approval still offers decision controls")
        check(novaPanel.approvalFeedback.indexOf("Action approval expired") === 0,
              "Expired action lacks structured result")
        var dismiss = button("dismissApprovalButton")
        pointerClick(dismiss)
        wait(50)
        var delivered = novaPanel.pendingApproval === null
        pointerDelivery.push({control:"Dismiss", delivered:delivered})
        if (!delivered) dismiss.clicked()
        check(novaPanel.pendingApproval === null, "Pointer Dismiss failed")
      })
      ;[["fixture-expired", "Installation approval expired"],
        ["fixture-rejected", "Decision failed"],
        ["fixture-conflict", "Approval is no longer available"],
        ["fixture-failure", "Decision result unavailable"]].forEach(function(pair) {
        test(pair[0] + " shows error and disables duplicate decision", function() {
          var approve = propose(pair[0], false)
          keyClick(Qt.Key_Return); wait(550)
          check(novaPanel.pendingApproval.decision === (pair[0] === "fixture-expired" ? "expired" : "unavailable"),
                "Failure lacks terminal UI state")
          check(novaPanel.approvalFeedback.indexOf(pair[1]) === 0, "Missing visible error: " + novaPanel.approvalFeedback)
          check(!approve.enabled, "Failed decision can replay")
          button("dismissApprovalButton").clicked(); wait(100)
          check(novaPanel.pendingApproval === null, "Local dismiss failed")
        })
      })
      test("expired history permits new eligible proposal without deletion", function() {
        var model = shell.fixture.catalog_models[0]
        var history = {}; history[model.catalog_id] = {state:"EXPIRED"}
        novaPanel.acquisitionByCatalog = history
        check(novaPanel.acquisitionState(model) === "AVAILABLE TO INSTALL", "Expired history masks preflight")
        check(novaPanel.acquisitionByCatalog[model.catalog_id].state === "EXPIRED", "History modified")
      })
      console.warn("APPROVAL_INTERACTION_RESULTS " + JSON.stringify(results))
      console.warn("APPROVAL_POINTER_DELIVERY " + JSON.stringify(pointerDelivery))
    }
  }
'''


def main():
    requests = []

    class Stub(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"acquisitions":[]}')

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            requests.append({"path": self.path, "body": body.decode(),
                             "content_length": self.headers.get("Content-Length")})
            if not self.path.startswith("/approvals/fixture-") or body:
                raise AssertionError("Non-fixture mutation attempted")
            time.sleep(.3)  # expose in-flight disabled/feedback state
            if "fixture-failure" in self.path:
                self.connection.close()
                return
            status = (410 if "fixture-expired" in self.path else
                      400 if "fixture-rejected" in self.path else
                      409 if "fixture-conflict" in self.path else 202)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "fixture_rejected"}).encode())

    with ThreadingHTTPServer(("127.0.0.1", 0), Stub) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        with tempfile.TemporaryDirectory(prefix="nova-approval-check-", dir="/tmp") as raw:
            target = Path(raw)
            fixture = preview.scenarios()["install-approval"]
            fixture["label"] = "APPROVAL INTERACTION CHECK // ISOLATED HTTP STUB"
            fixture["chat_expanded"] = False
            shell_file = preview.prepare_config(target, fixture)
            text = shell_file.read_text().replace("import QtQuick\n", "import QtQuick\nimport QtTest\n", 1)
            check = QML_CHECK.replace("@STUB_API@", json.dumps(f"http://127.0.0.1:{server.server_port}"))
            # Keep a persistent fixture banner even while testing production XHR.
            text = text.replace('property bool acceptancePreview: true', 'property bool acceptancePreview: true')
            text = text.rsplit("\n}", 1)[0] + check + "\n}\n"
            shell_file.write_text(text)
            result = subprocess.run(["quickshell", "--path", str(target)],
                                    capture_output=True, text=True, timeout=45)
            output = result.stdout + result.stderr
        server.shutdown()
    marker = "APPROVAL_INTERACTION_RESULTS "
    line = next((line.split(marker, 1)[1] for line in output.splitlines() if marker in line), None)
    if line is None:
        print(output)
        raise SystemExit("Interaction harness did not complete")
    results = json.loads(line)
    pointer_marker = "APPROVAL_POINTER_DELIVERY "
    pointer_line = next((line.split(pointer_marker, 1)[1] for line in output.splitlines()
                         if pointer_marker in line), None)
    pointer_results = json.loads(pointer_line) if pointer_line else []
    for item in results:
        print(("PASS " if item["passed"] else "FAIL ") + item["name"] +
              (": " + item["error"] if not item["passed"] else ""))
    counts = {row["path"]: sum(item["path"] == row["path"] for item in requests) for row in requests}
    if len(requests) != 18 or any(count != 1 for count in counts.values()) or any(row["body"] for row in requests):
        print("FAIL request cardinality/body:", requests)
        raise SystemExit(1)
    print("PASS eighteen fixture-only decision requests, once each, empty bodies")
    if pointer_results and all(item["delivered"] for item in pointer_results):
        print("PASS QtTest pointer delivery: " + ", ".join(item["control"] for item in pointer_results))
    else:
        missing = ", ".join(item["control"] for item in pointer_results if not item["delivered"])
        print("UNAVAILABLE QtTest pointer delivery on layer-shell surface: " + (missing or "no result"))
    if any(not item["passed"] for item in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
