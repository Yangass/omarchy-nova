"""Source-contract tests for the compact P1-01C onboarding surface.

Fresh-system states are fixtures of the pure classifier/API suites. These checks
ensure the QML consumes that contract without creating a second policy engine.
"""
from pathlib import Path
import unittest


PANEL = (Path(__file__).resolve().parents[1] / 'Panel.qml').read_text()


class OnboardingUiContractTests(unittest.TestCase):
    def test_reads_only_the_joined_onboarding_endpoint(self):
        start = PANEL.index('function refreshOnboarding')
        end = PANEL.index('function onboardingTitle', start)
        refresh = PANEL[start:end]
        self.assertIn('"/onboarding/state"', refresh)
        self.assertIn('xhr.open("GET"', refresh)
        self.assertIn('if (onboardingRequestInFlight || !onboardingRefreshAllowed()) return', refresh)
        self.assertIn('xhr.timeout = 35000', refresh)
        self.assertNotIn('POST', refresh)

    def test_timeout_is_separate_from_the_backend_cooperative_budget(self):
        refresh = PANEL[PANEL.index('function refreshOnboarding'):
                        PANEL.index('function onboardingTitle')]
        self.assertIn("30-second collection budget is cooperative", refresh)
        self.assertIn("panel's hard bound", refresh)
        self.assertIn('xhr.ontimeout = finishUnavailable', refresh)

    def test_refresh_never_overlaps_chat_approval_action_switch_or_acquisition(self):
        gate = PANEL[PANEL.index('function onboardingRefreshAllowed'):
                     PANEL.index('function setOnboardingUnavailable')]
        for value in ('!chatBusy', 'pendingApproval === null', '!modelTransitionActive',
                      'trackedAcquisitionId === ""', '"executing_action"',
                      '"verifying_action"'):
            self.assertIn(value, gate)
        self.assertIn('onTriggered: root.refreshOnboarding()', PANEL)
        self.assertNotIn('refreshOnboarding()', PANEL[PANEL.index('function sendChat'):
                                                       PANEL.index('function open()')])

    def test_all_classifier_states_have_plain_presentations(self):
        title = PANEL[PANEL.index('function onboardingTitle'):
                      PANEL.index('function onboardingActionLabel')]
        for state in ('CHECKING', 'NEEDS_USER_SETUP', 'NEEDS_MODEL',
                      'NEEDS_ACTIVATION', 'NEEDS_LOAD', 'ACQUISITION_PROPOSED',
                      'ACQUISITION_IN_PROGRESS', 'INSTALLATION_FAILED',
                      'INSTALLATION_RECOVERY_REQUIRED', 'ACTIVATING',
                      'ACTIVATION_FAILED', 'READY'):
            self.assertIn('"' + state + '"', title)
        for phrase in ('No download starts before approval.',
                       "It won't start by itself afterwards.",
                       'Your previous model stays selected.',
                       "hardware details couldn't be read."):
            self.assertIn(phrase, title)

    def test_actions_reuse_install_selector_and_transactional_activation(self):
        action = PANEL[PANEL.index('function performOnboardingAction'):
                       PANEL.index('function isExplicitlySelected')]
        self.assertIn('proposeCatalogAcquisition(onboardingTarget', action)
        self.assertIn('selectModel(onboardingTarget, true)', action)
        self.assertIn('showModelChoices()', action)
        self.assertIn('refreshOnboarding()', action)
        self.assertNotIn('XMLHttpRequest', action)
        self.assertIn('if (!onboardingSnapshotFresh)', action)
        self.assertIn('if (onboardingHasAction("RETRY_CHECK")) refreshOnboarding()', action)

    def test_existing_lifecycle_updates_compact_active_operation_state(self):
        self.assertIn('onboardingState = "ACQUISITION_PROPOSED"', PANEL)
        self.assertIn('onboardingState = "ACQUISITION_IN_PROGRESS"', PANEL)
        self.assertIn('onboardingState = "ACTIVATING"', PANEL)
        self.assertIn('onboardingCodes = ["ACQUISITION_" + state]', PANEL)

    def test_install_request_still_sends_catalog_identity_only(self):
        install = PANEL[PANEL.index('function proposeCatalogAcquisition'):
                        PANEL.index('function refreshRecommendations')]
        self.assertIn('encodeURIComponent(catalogId)', install)
        self.assertIn('xhr.send()', install)
        self.assertNotIn('JSON.stringify', install)

    def test_fallback_is_not_rendered_as_explicit_selection(self):
        self.assertIn('configuration.explicit_selection === true', PANEL)
        self.assertIn('function isExplicitlySelected(modelData)', PANEL)
        self.assertIn('if (!onboardingExplicitSelection) return "MODEL · NONE SELECTED"', PANEL)
        selector = PANEL[PANEL.index('id: modelSelector'):PANEL.index('id: fixedBottom')]
        # UX Phase 2 step 2c: the catalog card's IN USE tag follows the explicit selection only.
        self.assertIn('root.isExplicitlySelected(modelData) ? "IN USE"', selector)
        self.assertNotIn('root.catalogRuntimeId(modelData) === root.activeModel ? "IN USE"', selector)

    def test_qml_defaults_and_active_state_refresh_preserve_no_selection(self):
        bar = (Path(__file__).resolve().parents[1] / 'BarWidget.qml').read_text()
        self.assertIn('property string activeProvider: ""', bar)
        self.assertIn('property string activeModel: ""', bar)
        self.assertIn('typeof payload.provider === "string" ? payload.provider : ""', bar)
        self.assertIn('typeof payload.model === "string" ? payload.model : ""', bar)
        self.assertIn('hostWidget ? hostWidget.activeProvider : ""', PANEL)
        self.assertIn('hostWidget ? hostWidget.activeModel : ""', PANEL)
        self.assertIn('property string onboardingSelectedProvider: ""', PANEL)
        # The bar shows the model's plain name; the exact provider/id is in the tooltip.
        self.assertIn('return "Selected model: " + onboardingSelectedProvider + " / " + onboardingSelectedModel', PANEL)
        self.assertNotIn('hostWidget ? hostWidget.activeModel : "qwen3.5:35b"', PANEL)

    def test_chat_permission_requires_a_fresh_ready_snapshot(self):
        self.assertIn('onboardingState === "READY"', PANEL)
        self.assertIn('Date.now() - onboardingReceivedAt <= 45000', PANEL)
        send = PANEL[PANEL.index('id: chatInput'):PANEL.index('onClicked: root.sendChat()',
                                                              PANEL.index('id: sendButton'))]
        self.assertIn('placeholderText: root.chatPlaceholder()', send)
        self.assertIn('root.onboardingCanSend', send)
        send_function = PANEL[PANEL.index('function sendChat'):
                              PANEL.index('function open()')]
        self.assertIn('if (!onboardingCanSend', send_function)

    def test_refresh_does_not_clear_draft_or_transcript(self):
        refresh = PANEL[PANEL.index('function refreshOnboarding'):
                        PANEL.index('function onboardingTitle')]
        for forbidden in ('chatInput.text =', 'chatHistoryModel.clear',
                          'chatHistoryModel.remove', 'xhr.abort'):
            self.assertNotIn(forbidden, refresh)


if __name__ == '__main__':
    unittest.main()
