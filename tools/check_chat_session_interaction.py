#!/usr/bin/env python3
"""Exercise CHAT-02D in canonical Panel.qml against an isolated HTTP stub.

The temporary preview never contacts production NOVA. It uses QtTest event
delivery and the real panel XHR/session code to cover lazy setup, reuse,
context loss, retention reconciliation, and uncertain transport outcomes.
"""

from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
import subprocess
import tempfile
from threading import Lock, Thread
import time

import fake_nova
import onboarding_preview as preview


SESSION_A = "chat_" + "A" * 32
SESSION_B = "chat_" + "B" * 32
SESSION_C = "chat_" + "C" * 32
SESSION_D = "chat_" + "D" * 32
EXPIRED = "chat_" + "E" * 32
STALE = "chat_" + "S" * 32
REPLACED = "chat_" + "R" * 32
MOVED = "chat_" + "T" * 32


QML_CHECK = r'''
  NovaClient { id: fakeNova; socketPath: "@SOCKET@" }
  TestCase {
    name: "IsolatedChatSessionInteraction"
    when: novaPanel.opened
    function check(value, message) { if (!value) throw new Error(message) }
    function waitUntil(predicate, message, timeout) {
      var elapsed = 0, maximum = timeout || 5000
      while (!predicate() && elapsed < maximum) { wait(50); elapsed += 50 }
      check(predicate(), message)
    }
    function input() { return findChild(novaPanel, "chatInput") }
    function setResetMode(mode) {
      var done = false
      var xhr = fakeNova.request()
      xhr.open("GET", "/fixture/reset-mode/" + mode, true)
      xhr.onload = function() { done = true }
      xhr.onerror = function() { done = true }
      xhr.send()
      var elapsed = 0
      while (!done && elapsed < 4000) { wait(25); elapsed += 25 }
      check(done, "could not arm fixture reset mode " + mode)
    }
    function send(text) {
      var field = input()
      field.text = text
      field.forceActiveFocus()
      keyClick(Qt.Key_Return)
    }
    function test_session_flow() {
      var results = []
      function test(name, fn) {
        try { fn(); results.push({name:name, passed:true}) }
        catch (error) { results.push({name:name, passed:false, error:String(error)}) }
      }
      previewHost.acceptancePreview = false
      novaPanel.pendingApproval = null
      novaPanel.onboardingState = "READY"
      novaPanel.onboardingActions = ["CHAT"]
      novaPanel.onboardingSnapshotFresh = true
      novaPanel.onboardingReceivedAt = Date.now()
      novaPanel.onboardingExplicitSelection = true
      novaPanel.onboardingSelectedProvider = "ollama"
      novaPanel.onboardingSelectedModel = "fixture:model"

      test("setup failure preserves unsent draft and does not send chat", function() {
        send("preserve this draft")
        waitUntil(function() { return !novaPanel.chatSessionSetupInFlight }, "setup failure did not settle")
        check(input().text === "preserve this draft", "draft was cleared")
        check(novaPanel.chatSessionId === "", "failed setup installed a session")
        check(novaPanel.chatError.indexOf("draft is preserved") >= 0, "setup failure not actionable")
        check(novaPanel.chatHistoryCount === 0, "failed setup added transcript turns")
      })

      test("session is lazy, retained, and survives panel close/reopen", function() {
        send("first turn")
        waitUntil(function() { return novaPanel.chatSessionId === @SESSION_A@
                                      && novaPanel.chatHistoryCount >= 2
                                      && !novaPanel.chatBusy && !novaPanel.chatRetentionPending },
                  "first turn did not reconcile")
        check(novaPanel.chatSessionId === @SESSION_A@, "first session not established")
        check(novaPanel.chatContextNotice.indexOf("Older conversation context") === 0,
              "eviction disclosure missing")
        var identifier = novaPanel.chatSessionId
        novaPanel.close(); wait(50); novaPanel.open(); wait(150)
        check(novaPanel.chatSessionId === identifier, "close/reopen discarded session")
        novaPanel.onboardingState = "READY"
        novaPanel.onboardingSnapshotFresh = true
        novaPanel.onboardingReceivedAt = Date.now()
        send("second turn")
        waitUntil(function() { return novaPanel.chatHistoryCount >= 4
                                      && !novaPanel.chatBusy && !novaPanel.chatRetentionPending },
                  "second turn did not reconcile")
        check(novaPanel.chatSessionId === identifier, "second turn replaced session")
        check(novaPanel.chatContextNotice === "", "ordinary retained turn left a warning")
      })

      test("confirmed loss keeps transcript, restores draft, and creates without replay", function() {
        var before = novaPanel.chatHistoryCount
        novaPanel.chatSessionId = @EXPIRED@
        novaPanel.chatSessionGeneration++
        send("review before resending")
        waitUntil(function() { return !novaPanel.chatSessionSetupInFlight && novaPanel.chatSessionId === @SESSION_B@ },
                  "replacement session not established")
        check(input().text === "review before resending", "lost-context draft not restored")
        check(novaPanel.chatHistoryCount >= before, "visible transcript was discarded")
        check(novaPanel.chatContextNotice.indexOf("Earlier messages remain visible") === 0,
              "display-only context warning missing")
      })

      test("oversized omission is disclosed after exact turn match", function() {
        send("oversized reply")
        waitUntil(function() { return !novaPanel.chatBusy && !novaPanel.chatRetentionPending }, "oversized turn did not reconcile")
        check(novaPanel.chatContextNotice.indexOf("This reply was too large") === 0,
              "oversized omission not disclosed")
      })

      test("uncertain transport is incomplete and never replayed", function() {
        send("uncertain transport")
        waitUntil(function() { return !novaPanel.chatBusy }, "uncertain transport did not settle")
        check(novaPanel.chatContextNotice.indexOf("Whether this interrupted turn was remembered is unknown") === 0,
              "transport uncertainty not disclosed")
        check(novaPanel.chatError !== "", "transport failure lacks visible error")
      })

      test("stale status callback cannot overwrite replacement context", function() {
        novaPanel.chatSessionId = @STALE@
        novaPanel.chatSessionGeneration++
        novaPanel.activeChatRequestSerial++
        var serial = novaPanel.activeChatRequestSerial
        var generation = novaPanel.chatSessionGeneration
        novaPanel.reconcileChatRetention(@STALE@, "turn-stale", serial, generation, 0)
        wait(40)
        novaPanel.contextLossConfirmed("", serial, @STALE@, generation, true)
        waitUntil(function() { return !novaPanel.chatSessionSetupInFlight && novaPanel.chatSessionId === @SESSION_C@ },
                  "stale replacement session not established")
        var notice = novaPanel.chatContextNotice
        wait(500)
        check(novaPanel.chatContextNotice === notice, "stale completion overwrote context state")
      })

      test("new chat success clears transcript and notice, keeps draft and session", function() {
        novaPanel.onboardingState = "READY"
        novaPanel.onboardingSnapshotFresh = true
        novaPanel.onboardingReceivedAt = Date.now()
        send("reset success turn")
        waitUntil(function() { return novaPanel.chatHistoryCount >= 2
                                      && !novaPanel.chatBusy && !novaPanel.chatRetentionPending },
                  "reset success turn did not reconcile")
        check(novaPanel.chatSessionId === @SESSION_C@, "session moved before the confirmed reset")
        var identifier = novaPanel.chatSessionId
        input().text = "draft after confirmed reset"
        setResetMode("success")
        novaPanel.startChatReset()
        waitUntil(function() { return !novaPanel.chatResetInFlight && !novaPanel.chatSessionSetupInFlight },
                  "confirmed reset did not settle")
        check(novaPanel.chatSessionId === identifier, "confirmed reset replaced the session")
        check(novaPanel.chatHistoryCount === 0, "confirmed reset left transcript rows")
        check(novaPanel.chatContextNotice === "", "confirmed reset left a context notice")
        check(input().text === "draft after confirmed reset", "confirmed reset discarded the draft")
        check(novaPanel.chatError === "", "confirmed reset left an error message")
      })

      test("busy reset preserves transcript, draft, and session with truthful feedback", function() {
        send("busy reset turn")
        waitUntil(function() { return novaPanel.chatHistoryCount >= 2
                                      && !novaPanel.chatBusy && !novaPanel.chatRetentionPending },
                  "busy reset turn did not reconcile")
        var identifier = novaPanel.chatSessionId
        var count = novaPanel.chatHistoryCount
        input().text = "draft before busy reset"
        setResetMode("busy")
        novaPanel.startChatReset()
        waitUntil(function() { return !novaPanel.chatResetInFlight }, "busy reset did not settle")
        check(novaPanel.chatSessionId === identifier, "busy reset moved the session")
        check(novaPanel.chatHistoryCount === count, "busy reset discarded the transcript")
        check(input().text === "draft before busy reset", "busy reset discarded the draft")
        check(novaPanel.chatError !== "" && novaPanel.chatError.indexOf("busy") >= 0,
              "busy reset lacks truthful feedback")
        check(novaPanel.chatError.indexOf("preserved") >= 0,
              "busy reset feedback omits preservation")
      })

      test("failed reset preserves transcript, draft, and session with truthful feedback", function() {
        send("failure reset turn")
        waitUntil(function() { return novaPanel.chatHistoryCount >= 4
                                      && !novaPanel.chatBusy && !novaPanel.chatRetentionPending },
                  "failure reset turn did not reconcile")
        var identifier = novaPanel.chatSessionId
        var count = novaPanel.chatHistoryCount
        input().text = "draft before failed reset"
        setResetMode("failure")
        novaPanel.startChatReset()
        waitUntil(function() { return !novaPanel.chatResetInFlight }, "failed reset did not settle")
        check(novaPanel.chatSessionId === identifier, "failed reset moved the session")
        check(novaPanel.chatHistoryCount === count, "failed reset discarded the transcript")
        check(input().text === "draft before failed reset", "failed reset discarded the draft")
        check(novaPanel.chatError !== "" && novaPanel.chatError.indexOf("preserved") >= 0,
              "failed reset lacks truthful feedback")
      })

      test("unknown session is replaced without sending and cleared only after replacement", function() {
        check(novaPanel.chatHistoryCount >= 4, "expected retained transcript before unknown reset")
        input().text = "draft before unknown reset"
        var generation = novaPanel.chatSessionGeneration
        setResetMode("unknown")
        novaPanel.startChatReset()
        waitUntil(function() { return novaPanel.chatSessionId === @SESSION_D@
                                      && !novaPanel.chatSessionSetupInFlight && !novaPanel.chatResetInFlight },
                  "unknown session was not replaced")
        check(novaPanel.chatHistoryCount === 0, "replacement left transcript rows")
        check(input().text === "draft before unknown reset", "replacement discarded the draft")
        check(novaPanel.chatSessionGeneration > generation, "replacement did not bump generation")
      })

      test("duplicate new chat clicks issue a single reset request", function() {
        input().text = "draft with duplicate clicks"
        setResetMode("delay")
        novaPanel.startChatReset()
        novaPanel.startChatReset()
        novaPanel.startChatReset()
        waitUntil(function() { return !novaPanel.chatResetInFlight }, "duplicate resets did not settle")
        check(novaPanel.chatHistoryCount === 0, "duplicate resets corrupted transcript state")
        check(input().text === "draft with duplicate clicks", "duplicate reset discarded the draft")
        check(novaPanel.chatSessionId === @SESSION_D@, "duplicate reset moved the session")
      })

      test("stale reset callback cannot clear the replacement context", function() {
        novaPanel.chatSessionId = @REPLACED@
        novaPanel.chatSessionGeneration++
        novaPanel.onboardingState = "READY"
        novaPanel.onboardingSnapshotFresh = true
        novaPanel.onboardingReceivedAt = Date.now()
        send("stale reset turn")
        waitUntil(function() { return novaPanel.chatHistoryCount >= 2
                                      && !novaPanel.chatBusy && !novaPanel.chatRetentionPending },
                  "stale reset turn did not reconcile")
        var count = novaPanel.chatHistoryCount
        input().text = "draft during stale reset"
        setResetMode("delay")
        novaPanel.startChatReset()
        wait(80)
        novaPanel.chatSessionId = @MOVED@
        novaPanel.chatSessionGeneration++
        waitUntil(function() { return !novaPanel.chatResetInFlight }, "stale reset did not settle")
        wait(200)
        check(novaPanel.chatHistoryCount === count,
              "stale reset callback cleared the replacement context")
        check(novaPanel.chatSessionId === @MOVED@, "stale reset moved the replacement session")
        check(input().text === "draft during stale reset", "stale reset discarded the draft")
        check(novaPanel.chatError === "", "stale reset raised an error")
      })

      test("stalled reset hits the deadline, keeps content, and ignores the late reply", function() {
        check(novaPanel.chatHistoryCount >= 2, "expected retained transcript before stalled reset")
        var identifier = novaPanel.chatSessionId
        var count = novaPanel.chatHistoryCount
        input().text = "draft before stalled reset"
        setResetMode("stall")
        novaPanel.startChatReset()
        waitUntil(function() { return !novaPanel.chatResetInFlight && !novaPanel.chatSessionSetupInFlight },
                  "stalled reset did not settle at the deadline", 8000)
        check(novaPanel.chatSessionId === identifier, "stalled reset moved the session")
        check(novaPanel.chatHistoryCount === count, "stalled reset discarded the transcript")
        check(input().text === "draft before stalled reset", "stalled reset discarded the draft")
        check(novaPanel.chatError.indexOf("uncertain") >= 0, "stalled reset does not report uncertainty")
        check(novaPanel.chatError.indexOf("preserved") >= 0, "stalled reset omits preservation")
        wait(2200)
        check(novaPanel.chatSessionId === identifier, "late stalled reset reply moved the session")
        check(novaPanel.chatHistoryCount === count, "late stalled reset reply cleared the transcript")
        check(input().text === "draft before stalled reset", "late stalled reset reply touched the draft")
      })

      test("stalled replacement creation hits the deadline and drops the late success", function() {
        var identifier = novaPanel.chatSessionId
        check(identifier === @MOVED@, "expected the moved session before stalled creation")
        check(novaPanel.chatHistoryCount >= 2, "expected retained transcript before stalled creation")
        var count = novaPanel.chatHistoryCount
        input().text = "draft before stalled creation"
        setResetMode("stall-create")
        novaPanel.startChatReset()
        waitUntil(function() { return !novaPanel.chatResetInFlight && !novaPanel.chatSessionSetupInFlight },
                  "stalled replacement did not settle at the deadline", 8000)
        check(novaPanel.chatSessionId === "", "confirmed loss left a session id after the deadline")
        check(novaPanel.chatHistoryCount === count, "stalled replacement discarded the transcript")
        check(input().text === "draft before stalled creation", "stalled replacement discarded the draft")
        check(novaPanel.chatError.indexOf("uncertain") >= 0, "stalled replacement does not report uncertainty")
        check(novaPanel.chatError.indexOf("preserved") >= 0, "stalled replacement omits preservation")
        wait(2200)
        check(novaPanel.chatSessionId === "", "late success installed a session after the deadline")
        check(novaPanel.chatHistoryCount === count, "late success cleared the transcript after the deadline")
        check(input().text === "draft before stalled creation", "late success touched the draft")
      })

      test("late success after the reset timeout cannot clear the confirmed content", function() {
        novaPanel.chatSessionId = @MOVED@
        novaPanel.chatSessionGeneration++
        var identifier = novaPanel.chatSessionId
        check(identifier === @MOVED@, "expected the moved session before timeout reset")
        check(novaPanel.chatHistoryCount >= 2, "expected retained transcript before timeout reset")
        var count = novaPanel.chatHistoryCount
        input().text = "draft before timeout reset"
        setResetMode("stall")
        novaPanel.startChatReset()
        waitUntil(function() { return !novaPanel.chatResetInFlight && !novaPanel.chatSessionSetupInFlight },
                  "timeout reset did not settle at the deadline", 8000)
        check(novaPanel.chatSessionId === identifier, "timeout reset moved the session")
        check(novaPanel.chatHistoryCount === count, "timeout reset discarded the transcript")
        check(input().text === "draft before timeout reset", "timeout reset discarded the draft")
        check(novaPanel.chatError.indexOf("uncertain") >= 0, "timeout reset does not report uncertainty")
        wait(2200)
        check(novaPanel.chatSessionId === identifier, "late success moved the session after the timeout")
        check(novaPanel.chatHistoryCount === count, "late success cleared the transcript after the timeout")
        check(input().text === "draft before timeout reset", "late success touched the draft")
      })

      test("invalid reset responses fail safely without clearing content", function() {
        novaPanel.chatSessionId = @MOVED@
        novaPanel.chatSessionGeneration++
        var identifier = novaPanel.chatSessionId
        check(identifier === @MOVED@, "expected the moved session before invalid resets")
        check(novaPanel.chatHistoryCount >= 2, "expected retained transcript before invalid resets")
        var count = novaPanel.chatHistoryCount
        input().text = "draft before invalid resets"
        setResetMode("invalid-null")
        novaPanel.startChatReset()
        waitUntil(function() { return !novaPanel.chatResetInFlight }, "invalid-null reset did not settle")
        check(novaPanel.chatSessionId === identifier, "invalid-null reset moved the session")
        check(novaPanel.chatHistoryCount === count, "invalid-null reset discarded the transcript")
        check(novaPanel.chatError !== "" && novaPanel.chatError.indexOf("preserved") >= 0,
              "invalid-null reset lacks truthful feedback")
        setResetMode("invalid-json")
        novaPanel.startChatReset()
        waitUntil(function() { return !novaPanel.chatResetInFlight }, "invalid-json reset did not settle")
        check(novaPanel.chatSessionId === identifier, "invalid-json reset moved the session")
        check(novaPanel.chatHistoryCount === count, "invalid-json reset discarded the transcript")
        check(novaPanel.chatError !== "" && novaPanel.chatError.indexOf("preserved") >= 0,
              "invalid-json reset lacks truthful feedback")
        setResetMode("invalid-array")
        novaPanel.startChatReset()
        waitUntil(function() { return !novaPanel.chatResetInFlight }, "invalid-array reset did not settle")
        check(novaPanel.chatSessionId === identifier, "invalid-array reset moved the session")
        check(novaPanel.chatHistoryCount === count, "invalid-array reset discarded the transcript")
        check(novaPanel.chatError !== "" && novaPanel.chatError.indexOf("preserved") >= 0,
              "invalid-array reset lacks truthful feedback")
        check(input().text === "draft before invalid resets", "invalid resets discarded the draft")
      })

      test("expired reset A cannot disable retry B's real deadline", function() {
        novaPanel.chatSessionId = @MOVED@
        novaPanel.chatSessionGeneration++
        var count = novaPanel.chatHistoryCount
        input().text = "draft across two deadlines"
        setResetMode("stall")
        novaPanel.startChatReset()
        waitUntil(function() { return !novaPanel.chatResetInFlight }, "A deadline did not fire", 8000)
        check(novaPanel.chatError.indexOf("uncertain") >= 0, "A did not report uncertainty")
        var serial = novaPanel.chatResetSerial
        novaPanel.startChatReset()
        check(novaPanel.chatResetInFlight, "B did not start")
        var feedback = novaPanel.chatError
        wait(2200) // A responds at 6.5s, while B's actual 5s deadline is still pending.
        check(novaPanel.chatResetInFlight, "A's late reply released B's guard")
        check(novaPanel.chatResetSerial === serial + 1, "A changed B's ownership")
        check(novaPanel.chatError === feedback, "A changed B's feedback")
        check(novaPanel.chatHistoryCount === count, "A cleared retained content")
        waitUntil(function() { return !novaPanel.chatResetInFlight }, "B deadline was disabled", 5000)
        check(novaPanel.chatResetSerial === serial + 2, "B did not expire through the real deadline")
        check(novaPanel.chatError.indexOf("uncertain") >= 0, "B did not report uncertainty")
        wait(2200) // B's late response must also be ignored.
        check(novaPanel.chatHistoryCount === count, "late B cleared retained content")
        check(input().text === "draft across two deadlines", "retry discarded draft")
      })

      test("409 null reports failure without clearing content", function() {
        var count = novaPanel.chatHistoryCount
        setResetMode("null409")
        novaPanel.startChatReset()
        waitUntil(function() { return !novaPanel.chatResetInFlight }, "null409 did not settle")
        check(novaPanel.chatError.indexOf("preserved") >= 0, "null409 lacks truthful feedback")
        check(novaPanel.chatHistoryCount === count, "null409 cleared transcript")
        check(input().text === "draft across two deadlines", "null409 discarded draft")
      })

      test("invalid session creation payloads release reset safely", function() {
        var count = novaPanel.chatHistoryCount
        novaPanel.chatSessionId = ""
        novaPanel.chatSessionGeneration++
        var modes = ["create-null", "create-array", "create-json"]
        for (var i = 0; i < modes.length; i++) {
          setResetMode(modes[i])
          novaPanel.startChatReset()
          waitUntil(function() { return !novaPanel.chatResetInFlight && !novaPanel.chatSessionSetupInFlight },
                    "invalid creation did not settle")
          check(novaPanel.chatSessionId === "", "invalid creation installed a session")
          check(novaPanel.chatHistoryCount === count, "invalid creation cleared transcript")
          check(novaPanel.chatError.indexOf("preserved") >= 0, "invalid creation lacks feedback")
          check(input().text === "draft across two deadlines", "invalid creation discarded draft")
        }
      })

      test("failed replacement creation releases flags and keeps the draft", function() {
        check(novaPanel.chatHistoryCount >= 2, "expected retained transcript before failed creation")
        var count = novaPanel.chatHistoryCount
        input().text = "draft before failed creation"
        novaPanel.chatSessionId = ""
        novaPanel.chatSessionGeneration++
        setResetMode("create-fail")
        novaPanel.startChatReset()
        waitUntil(function() { return !novaPanel.chatSessionSetupInFlight && !novaPanel.chatResetInFlight },
                  "failed replacement did not settle")
        check(novaPanel.chatSessionId === "", "failed replacement installed a session")
        check(novaPanel.chatHistoryCount === count, "failed replacement discarded the transcript")
        check(input().text === "draft before failed creation", "failed replacement discarded the draft")
        check(novaPanel.chatError !== "" && novaPanel.chatError.indexOf("preserved") >= 0,
              "failed replacement lacks truthful feedback")
        setResetMode("success")
      })

      test("new chat with no session establishes one before clearing content", function() {
        check(novaPanel.chatHistoryCount >= 2, "expected retained transcript before no-session reset")
        novaPanel.chatSessionId = ""
        novaPanel.chatSessionGeneration++
        input().text = "draft with no session"
        novaPanel.startChatReset()
        waitUntil(function() { return novaPanel.chatSessionId !== ""
                                      && !novaPanel.chatSessionSetupInFlight && !novaPanel.chatResetInFlight },
                  "no-session reset did not establish a session")
        check(novaPanel.chatHistoryCount === 0, "no-session reset left transcript rows")
        check(input().text === "draft with no session", "no-session reset discarded the draft")
        check(novaPanel.chatError === "", "no-session reset left an error message")
      })

      test("reset never dismisses or decides a pending approval", function() {
        var approval = {id: "reset-approval", tool: "shell", summary: "FIXTURE APPROVAL", decision: ""}
        novaPanel.pendingApproval = approval
        var count = novaPanel.chatHistoryCount
        input().text = "draft with approval"
        novaPanel.startChatReset()
        wait(80)
        check(novaPanel.pendingApproval === approval, "reset touched the pending approval")
        check(novaPanel.chatHistoryCount === count,
              "reset modified the transcript while approval was pending")
        check(!novaPanel.chatResetInFlight, "reset began despite the pending approval")
        check(input().text === "draft with approval",
              "reset discarded the draft while approval was pending")
        novaPanel.pendingApproval = null
      })

      // Run 6 (2026-10-10): Enter while NOVA was still checking the model did nothing.
      test("run 6: a message sent while NOVA checks the model waits, says so, then sends", function() {
        input().text = ""
        var count = novaPanel.chatHistoryCount
        novaPanel.onboardingRequestInFlight = true  // keep the fixture from answering the check
        novaPanel.onboardingState = "CHECKING"
        send("wait for me")
        wait(80)
        check(input().text === "wait for me", "draft lost while waiting")
        check(novaPanel.chatHistoryCount === count, "sent before NOVA was ready")
        check(novaPanel.chatWaitNotice === "NOVA is checking the model. Your message sends by itself in a moment.", "notice: " + novaPanel.chatWaitNotice)
        check(findChild(novaPanel, "chatWaitNotice").visible, "notice not shown")
        novaPanel.onboardingRequestInFlight = false
        novaPanel.onboardingReceivedAt = Date.now()
        novaPanel.onboardingSnapshotFresh = true
        novaPanel.onboardingState = "READY"
        waitUntil(function() { return novaPanel.chatHistoryCount >= count + 2 && !novaPanel.chatBusy && !novaPanel.chatRetentionPending },
                  "waiting message was not sent once ready")
        check(novaPanel.chatWaitNotice === "", "notice stayed after sending")
      })

      test("run 6: when the wait needs the user, it says what to do and does not send later", function() {
        var count = novaPanel.chatHistoryCount
        novaPanel.onboardingRequestInFlight = true
        novaPanel.onboardingState = "MODEL_NEEDED"
        send("not yet")
        wait(80)
        check(novaPanel.chatWaitNotice.indexOf("NOVA can't chat yet") === 0, "notice: " + novaPanel.chatWaitNotice)
        check(novaPanel.chatSendWhenReadyUntil === 0, "queued a send that can't resolve by itself")
        novaPanel.onboardingRequestInFlight = false
        novaPanel.onboardingReceivedAt = Date.now(); novaPanel.onboardingSnapshotFresh = true
        novaPanel.onboardingState = "READY"
        wait(300)
        check(novaPanel.chatHistoryCount === count, "sent without a new Enter")
        check(input().text === "not yet", "draft lost")
        check(novaPanel.chatWaitNotice === "", "stale notice once NOVA is ready: " + novaPanel.chatWaitNotice)
      })
      console.warn("CHAT_SESSION_INTERACTION_RESULTS " + JSON.stringify(results))
    }
  }
'''


def main():
    requests = []
    state = {"creates": 0, "turn": None, "turn1_status": 0, "reset_mode": "success"}
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

        def raw(self, status, body):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            with lock:
                requests.append({"method": "GET", "path": self.path})
            if self.path == "/onboarding/state":
                self.json(200, {"schema_version": 1, "primary_state": "READY",
                                "allowed_actions": ["CHAT"], "explanation_codes": [],
                                "limitations": [], "target_id": "fixture:model",
                                "configuration": {"explicit_selection": True,
                                                  "provider": "ollama", "model": "fixture:model"}})
                return
            if self.path.startswith("/fixture/reset-mode/"):
                mode = self.path.rsplit("/", 1)[-1]
                with lock:
                    state["reset_mode"] = mode if mode in ("success", "busy", "failure", "unknown", "delay",
                                                           "stall", "stall-create", "create-fail",
                                                           "invalid-null", "invalid-json", "invalid-array",
                                                           "null409", "create-null", "create-array", "create-json") else "success"
                self.json(200, {"reset_mode": state["reset_mode"]})
                return
            if self.path.startswith("/chat/sessions/"):
                identifier = self.path.rsplit("/", 1)[-1]
                if identifier == STALE:
                    time.sleep(.3)
                    self.json(200, {"session_id": STALE, "busy": False,
                                    "last_completion": {"turn_id": "turn-stale",
                                                        "retention": "RETAINED",
                                                        "evicted_turns": 0}})
                    return
                with lock:
                    turn = state["turn"]
                    if turn == "turn-1":
                        state["turn1_status"] += 1
                        first = state["turn1_status"] == 1
                    else:
                        first = False
                if first:
                    self.json(200, {"session_id": SESSION_A, "busy": True,
                                    "last_completion": {"turn_id": "older-turn",
                                                        "retention": "RETAINED",
                                                        "evicted_turns": 0}})
                    return
                retention = "OMITTED_OVERSIZED" if turn == "turn-3" else "RETAINED"
                self.json(200, {"session_id": identifier, "busy": False,
                                "last_completion": {"turn_id": turn,
                                                    "retention": retention,
                                                    "evicted_turns": 1 if turn == "turn-1" else 0}})
                return
            self.json(200, {"status": "ready", "models": [], "acquisitions": []})

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            with lock:
                requests.append({"method": "POST", "path": self.path,
                                 "body": body.decode()})
            if self.path == "/chat/sessions":
                with lock:
                    state["creates"] += 1
                    count = state["creates"]
                    mode = state["reset_mode"]
                if count == 1 or mode == "create-fail":
                    self.json(503, {"error": "fixture_setup_failure"})
                    return
                if mode == "stall-create":
                    time.sleep(6.5)
                if mode in ("create-null", "create-array", "create-json"):
                    self.raw(201, {"create-null": b"null", "create-array": b"[]",
                                   "create-json": b"not json"}[mode])
                    return
                identifier = {2: SESSION_A, 3: SESSION_B, 4: SESSION_C, 5: SESSION_D}.get(count, SESSION_D)
                self.json(201, {"session_id": identifier, "busy": False,
                                "history": {"retained_turns": 0}, "last_completion": None})
                return
            if self.path.startswith("/chat/sessions/") and self.path.endswith("/reset"):
                identifier = self.path[len("/chat/sessions/"):-len("/reset")]
                with lock:
                    mode = state["reset_mode"]
                if mode == "delay":
                    time.sleep(0.4)
                if mode == "stall":
                    time.sleep(6.5)
                    self.json(200, {"session_id": identifier, "busy": False, "last_completion": None})
                    return
                if mode == "stall-create":
                    self.json(404, {"error": "unknown_session"})
                    return
                if mode == "invalid-null":
                    self.raw(200, b"null")
                    return
                if mode == "null409":
                    self.raw(409, b"null")
                    return
                if mode == "invalid-json":
                    self.raw(200, b"not valid json {")
                    return
                if mode == "invalid-array":
                    self.raw(200, b"[1, 2, 3]")
                    return
                if mode == "busy":
                    self.json(409, {"error": "busy_session"})
                    return
                if mode == "failure":
                    self.json(500, {"error": "fixture_reset_failure"})
                    return
                if mode == "unknown":
                    self.json(404, {"error": "unknown_session"})
                    return
                self.json(200, {"session_id": identifier, "busy": False, "last_completion": None})
                return
            if self.path != "/chat/stream":
                self.json(404, {"error": "fixture_unknown_path"})
                return
            payload = json.loads(body or b"{}")
            message, identifier = payload.get("message"), payload.get("session_id")
            if identifier == EXPIRED:
                self.json(404, {"error": "unknown_session"})
                return
            if message == "uncertain transport":
                self.connection.close()
                return
            turn = {"first turn": "turn-1", "second turn": "turn-2",
                    "oversized reply": "turn-3", "reset success turn": "turn-4",
                    "busy reset turn": "turn-5", "failure reset turn": "turn-6",
                    "stale reset turn": "turn-7", "wait for me": "turn-8"}.get(message)
            with lock:
                state["turn"] = turn
            frames = (f'event: token\ndata: {json.dumps({"text": "fixture " + str(turn)})}\n\n'
                      f'event: done\ndata: {json.dumps({"done": True, "session_id": identifier, "turn_id": turn})}\n\n').encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(frames)))
            self.end_headers()
            self.wfile.write(frames)

    with fake_nova.server(Stub) as server:
        Thread(target=server.serve_forever, daemon=True).start()
        with tempfile.TemporaryDirectory(prefix="nova-chat-session-check-", dir="/tmp") as raw:
            target = Path(raw)
            fixture = preview.scenarios()["verified-awaiting-activation"]
            fixture["label"] = "CHAT SESSION CHECK // ISOLATED HTTP STUB"
            fixture["host"] = {"online": True, "runtime_state": "idle",
                               "active_provider": "ollama", "active_model": "fixture:model",
                               "active_available": True, "runtime_ready": True}
            shell_file = preview.prepare_config(target, fixture)
            text = shell_file.read_text().replace("import QtQuick\n", "import QtQuick\nimport QtTest\n", 1)
            check = (QML_CHECK.replace("@SOCKET@", server.server_address).replace("@SESSION_A@", json.dumps(SESSION_A))
                     .replace("@SESSION_B@", json.dumps(SESSION_B))
                     .replace("@SESSION_C@", json.dumps(SESSION_C))
                     .replace("@EXPIRED@", json.dumps(EXPIRED))
                     .replace("@STALE@", json.dumps(STALE))
                     .replace("@SESSION_D@", json.dumps(SESSION_D))
                     .replace("@REPLACED@", json.dumps(REPLACED))
                     .replace("@MOVED@", json.dumps(MOVED)))
            text = text.replace('property var novaClient: null', 'property var novaClient: fakeNova')
            text = text.rsplit("\n}", 1)[0] + check + "\n}\n"
            shell_file.write_text(text)
            result = subprocess.run(["quickshell", "--path", str(target)],
                                    capture_output=True, text=True, timeout=90)
            output = result.stdout + result.stderr
        server.shutdown()

    marker = "CHAT_SESSION_INTERACTION_RESULTS "
    encoded = next((line.split(marker, 1)[1] for line in output.splitlines()
                    if marker in line), None)
    if encoded is None:
        print(output)
        raise SystemExit("Interaction harness did not complete")
    results = json.loads(encoded)
    for item in results:
        print(("PASS " if item["passed"] else "FAIL ") + item["name"]
              + ((": " + item["error"]) if not item["passed"] else ""))
    with lock:
        chat_posts = [row for row in requests if row["method"] == "POST"
                      and row["path"] == "/chat/stream"]
    decoded = [json.loads(row["body"]) for row in chat_posts]
    first_two = [item for item in decoded if item.get("message") in ("first turn", "second turn")]
    if len(first_two) != 2 or any(item.get("session_id") != SESSION_A for item in first_two):
        print("FAIL session reuse request capture:", decoded)
        raise SystemExit(1)
    lost = [item for item in decoded if item.get("message") == "review before resending"]
    if len(lost) != 1:
        print("FAIL lost-context request was replayed:", decoded)
        raise SystemExit(1)
    print("PASS captured explicit session IDs; no stateless request or automatic replay")
    with lock:
        reset_posts = [row for row in requests if row["method"] == "POST"
                       and row["path"].startswith("/chat/sessions/")
                       and row["path"].endswith("/reset")]
    expected_reset_paths = ["/chat/sessions/" + i + "/reset"
                            for i in (SESSION_C, SESSION_C, SESSION_C, SESSION_C, SESSION_D, REPLACED,
                                     MOVED, MOVED, MOVED, MOVED, MOVED, MOVED,
                                     MOVED, MOVED, MOVED)]
    if len(reset_posts) != len(expected_reset_paths) or [r["path"] for r in reset_posts] != expected_reset_paths \
            or any(row["body"] != "" for row in reset_posts):
        print("FAIL reset request capture:", [r["path"] for r in reset_posts])
        raise SystemExit(1)
    messages = [item.get("message") for item in decoded]
    expected_messages = ["first turn", "second turn", "review before resending", "oversized reply",
                         "uncertain transport", "reset success turn", "busy reset turn",
                         "failure reset turn", "stale reset turn", "wait for me"]
    if messages != expected_messages:
        print("FAIL chat stream message capture:", messages)
        raise SystemExit(1)
    print("PASS reset requests use the existing reset API exactly once per attempt; reset never sends messages")
    if any(not item["passed"] for item in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
