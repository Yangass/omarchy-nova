"""Source contracts for CHAT-02D's canonical-panel session integration.

Behavioral QML/API interaction is exercised by
``tools/check_chat_session_interaction.py``. These checks keep security and
truthfulness boundaries visible in the ordinary regression suite.
"""

from pathlib import Path
import unittest


PANEL = (Path(__file__).resolve().parents[1] / "Panel.qml").read_text()


def section(start: str, end: str) -> str:
    return PANEL[PANEL.index(start):PANEL.index(end, PANEL.index(start))]


class ChatSessionUiContractTests(unittest.TestCase):
    def test_session_is_lazy_explicit_and_never_stateless(self):
        send = section("function sendChat()", "function open()")
        start = section("function startChatRequest", "function sendChat()")
        create = section("function createChatSession", "function contextLossConfirmed")
        self.assertIn('if (!chatSessionId) createChatSession(message, true)', send)
        self.assertIn('root.openNova(xhr, "POST", chatApiBase() + "/chat/sessions")', create)
        self.assertIn('session_id: requestSessionId', start)
        self.assertNotIn('JSON.stringify({ message: message })', start)

    def test_setup_failure_preserves_the_unsent_draft(self):
        create = section("function createChatSession", "function contextLossConfirmed")
        send = section("function sendChat()", "function open()")
        self.assertNotIn('chatInput.text = ""', create)
        self.assertIn("Your draft is preserved", create)
        self.assertLess(send.index("createChatSession(message, true)"),
                        send.find('chatInput.text = ""') if 'chatInput.text = ""' in send else len(send))

    def test_session_survives_panel_close_and_model_switch(self):
        open_close = section("function open()", "function switchPanel")
        switch = section("function selectModel", "function applyStreamState")
        self.assertNotIn("chatSessionId", open_close)
        self.assertNotIn("chatSessionId", switch)

    def test_confirmed_context_loss_replaces_context_without_replay(self):
        loss = section("function contextLossConfirmed", "function finishRetentionReconciliation")
        request = section("function startChatRequest", "function sendChat()")
        self.assertIn('chatInput.text = message', loss)
        self.assertIn('Earlier messages remain visible here but are no longer remembered', loss)
        self.assertIn('createChatSession("", false)', loss)
        self.assertNotIn("startChatRequest", loss)
        self.assertIn('payload.error === "unknown_session"', request)

    def test_completion_reconciliation_matches_current_turn_and_is_bounded(self):
        reconcile = section("function reconcileChatRetention", "function startChatRequest")
        self.assertIn("completion.turn_id !== turnId", reconcile)
        self.assertIn("attempt >= 5", reconcile)
        self.assertIn("OMITTED_OVERSIZED", reconcile)
        self.assertIn("evicted_turns", reconcile)
        self.assertIn("Whether this reply will be remembered is unknown.", reconcile)
        self.assertIn("requestSerial !== activeChatRequestSerial", reconcile)
        self.assertIn("generation !== chatSessionGeneration", reconcile)

    def test_disconnect_is_uncertain_and_never_replayed(self):
        request = section("function startChatRequest", "function sendChat()")
        self.assertIn("Whether this interrupted turn was remembered is unknown", request)
        self.assertIn("It will not be sent again automatically", request)
        self.assertEqual(request.count("xhr.send("), 1)

    def test_partial_and_failed_answers_are_visually_distinct(self):
        self.assertIn('responseStatus: "incomplete"', PANEL)
        self.assertIn('NOVA · INCOMPLETE', PANEL)
        self.assertIn('markAssistantComplete()', PANEL)

    def test_preview_boundary_still_prevents_session_requests(self):
        send = section("function sendChat()", "function open()")
        create = section("function createChatSession", "function contextLossConfirmed")
        self.assertLess(send.index("if (acceptancePreview)"),
                        send.index("createChatSession(message, true)"))
        self.assertIn("if (acceptancePreview || chatSessionSetupInFlight) return", create)

    def test_new_chat_control_is_visible_and_idle_gated(self):
        row = PANEL[PANEL.index("id: newChatButton"):PANEL.index("id: sendButton")]
        self.assertIn("onClicked: root.startChatReset()", row)
        self.assertIn("!root.chatBusy", row)
        self.assertIn("!root.chatContextOperationActive", row)
        self.assertIn("!root.chatResetInFlight", row)
        self.assertIn("root.pendingApproval === null", row)

    def test_reset_uses_the_existing_reset_api_with_stale_guards(self):
        reset = section("function startChatReset", "function open()")
        self.assertIn('root.openNova(xhr, "POST", chatApiBase() + "/chat/sessions/" + encodeURIComponent(targetSessionId) + "/reset")', reset)
        self.assertIn("if (acceptancePreview || chatResetInFlight || chatBusy || chatContextOperationActive", reset)
        self.assertIn("resetSerial !== chatResetSerial", reset)
        self.assertIn("targetGeneration !== chatSessionGeneration", reset)
        self.assertEqual(reset.count("xhr.send("), 1)

    def test_reset_clears_only_on_confirmed_success_and_keeps_the_draft(self):
        reset = section("function startChatReset", "function open()")
        self.assertIn("payload.session_id === targetSessionId", reset)
        self.assertIn("chatHistoryModel.clear()", reset)
        self.assertIn('chatContextNotice = ""', reset)
        self.assertNotIn('chatInput.text = ""', reset)
        self.assertIn("busy_session", reset)
        self.assertIn("Transcript and draft are preserved", reset)

    def test_unknown_session_is_replaced_without_sending(self):
        reset = section("function startChatReset", "function open()")
        create = section("function createChatSession", "function contextLossConfirmed")
        self.assertIn('payload.error === "unknown_session"', reset)
        self.assertIn('createChatSession("", false)', reset)
        self.assertNotIn("startChatRequest", reset)
        self.assertIn("chatResetPendingClear === chatResetSerial", create)
        self.assertIn("chatHistoryModel.clear()", create)

    def test_send_and_input_are_blocked_while_reset_is_in_flight(self):
        send = section("function sendChat()", "function startChatReset")
        self.assertIn("chatResetInFlight", send)
        row = PANEL[PANEL.index("id: chatInput"):PANEL.index("id: sendButton")]
        self.assertIn("!root.chatResetInFlight", row)

    def test_reset_deadline_releases_flags_and_keeps_content(self):
        deadline = PANEL[PANEL.index("id: chatResetDeadline"):PANEL.index("id: chatResetDeadline") + 400]
        self.assertIn("interval: 5000", deadline)
        self.assertIn("root.chatResetDeadlineReached()", deadline)
        handler = section("function chatResetDeadlineReached", "function startChatReset")
        self.assertIn("chatResetSerial++", handler)
        self.assertIn("chatResetInFlight = false", handler)
        self.assertIn("chatResetPendingClear = 0", handler)
        self.assertIn("uncertain", handler)
        self.assertIn("Transcript and draft are preserved", handler)
        self.assertNotIn("chatHistoryModel.clear()", handler)
        self.assertNotIn('chatInput.text = ""', handler)
        reset = section("function startChatReset", "function open()")
        self.assertIn("chatResetDeadline.start()", reset)
        self.assertIn("chatResetDeadline.stop()", reset)

    def test_stale_reset_returns_before_mutating_current_operation(self):
        reset = section("function startChatReset", "function open()")
        callback = reset[reset.index("function finish(status, body)"):]
        guard = callback.index("targetGeneration !== chatSessionGeneration) return")
        self.assertLess(guard, callback.index("chatResetDeadline.stop()"))
        self.assertLess(guard, callback.index("chatResetInFlight = false"))

    def test_reset_and_create_normalize_all_payloads_before_field_access(self):
        for start, end in (("function startChatReset", "function open()"),
                           ("function createChatSession", "function contextLossConfirmed")):
            code = section(start, end)
            normalize = code.index('if (payload === null || typeof payload !== "object" || Array.isArray(payload)) payload = ({})')
            self.assertLess(normalize, code.index("payload.session_id"))

    def test_reset_payloads_are_shape_checked_before_use(self):
        reset = section("function startChatReset", "function open()")
        self.assertIn("typeof payload === \"object\" && payload !== null && typeof payload.session_id === \"string\"", reset)
        self.assertIn('payload.error === "unknown_session"', reset)
        handler = section("function chatResetDeadlineReached", "function startChatReset")
        self.assertNotIn("chatHistoryModel.clear()", handler)
        self.assertNotIn('chatInput.text = ""', handler)


if __name__ == "__main__":
    unittest.main()
