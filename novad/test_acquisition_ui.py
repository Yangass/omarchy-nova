"""Static contract checks for the deliberately small v0.12F QML surface."""
import os
from pathlib import Path
import unittest


PLUGIN_DIR = Path(os.environ.get(
    'NOVA_PLUGIN_DIR',
    Path(__file__).resolve().parents[1]))
PANEL = (PLUGIN_DIR / 'Panel.qml').read_text()
BAR_WIDGET = (PLUGIN_DIR / 'BarWidget.qml').read_text()


class AcquisitionUiContractTests(unittest.TestCase):
    def test_catalog_identity_only_install_request(self):
        start = PANEL.index('function proposeAcquisition')
        end = PANEL.index('function refreshRecommendations', start)
        proposal = PANEL[start:end]
        self.assertIn('proposeCatalogAcquisition(modelData.catalog_id', proposal)
        self.assertIn('encodeURIComponent(catalogId)', proposal)
        self.assertIn('"/propose"', proposal)
        self.assertIn('xhr.send()', proposal)
        self.assertNotIn('JSON.stringify', proposal)
        for forbidden in ('artifact_id', 'expected_revision', 'expected_size_bytes', 'registry', 'executable'):
            self.assertNotIn(forbidden, proposal)

    def test_states_and_truthful_progress_are_visible(self):
        for state in ('INSTALLED', 'AVAILABLE TO INSTALL', 'BLOCKED', 'UNVERIFIED',
                      'DOWNLOADING', 'VERIFYING', 'FAILED', 'RECOVERY_REQUIRED',
                      'INSTALLED AFTER ACQUISITION'):
            self.assertIn(state, PANEL)
        self.assertIn('DOWNLOAD PART', PANEL)
        self.assertIn('CANCEL  Not possible once the download has started', PANEL)
        self.assertNotIn('NETWORK DOWNLOADED', PANEL)

    def test_progress_is_current_blob_only_and_can_be_indeterminate(self):
        start = PANEL.index('function acquisitionBlobProgress')
        end = PANEL.index('function refreshAcquisitions', start)
        progress = PANEL[start:end]
        self.assertIn('current_blob_completed_bytes', progress)
        self.assertIn('current_blob_total_bytes', progress)
        self.assertIn('total > 0', progress)
        self.assertIn('Math.floor(fraction * 100)', progress)
        self.assertIn('PROGRESS UNAVAILABLE', progress)
        self.assertIn('may count parts downloaded earlier', progress)
        self.assertNotIn('provider_completed_bytes', progress)
        self.assertNotIn('provider_total_bytes', progress)
        component = PANEL[PANEL.index('component AcquisitionProgress'):
                          PANEL.index('component TelemetryRow')]
        self.assertIn('observation.determinate === true', component)
        self.assertIn('observation.determinate !== true', component)
        self.assertIn('width: parent.width * Math.max(0, Math.min(1', component)
        self.assertIn('id: acquisitionIndeterminateSegment', component)
        self.assertIn('SequentialAnimation on x', component)

    def test_approval_discloses_frozen_operation_not_activation(self):
        for label in ('INSTALL APPROVAL', 'DOWNLOADED BY  ', 'SIZE ON DISK  ', 'FIT  ',
                      'SOURCE  ', 'DETAILS  ', 'AFTER INSTALL  Nothing starts by itself'):
            self.assertIn(label, PANEL)
        self.assertIn('beginApproval({ id: payload.id', PANEL)

    def test_approval_uses_frozen_preflight_space_not_transfer_guesses(self):
        start = PANEL.index('function proposeAcquisition')
        end = PANEL.index('function refreshRecommendations', start)
        proposal = PANEL[start:end]
        self.assertIn('requiredSpace: detail.disk && detail.disk.total_required', proposal)
        self.assertIn('availableSpace: detail.disk && detail.disk.free_space', proposal)
        approval = PANEL[PANEL.index('INSTALL APPROVAL'):PANEL.index('CANCEL  Not possible')]
        for label in ('FREE SPACE NEEDED  ', 'including a safety margin',
                      'FREE SPACE NOW  ', 'measured when this request was made',
                      'the download itself may differ a little'):
            self.assertIn(label, approval)

    def test_selector_refreshes_authoritative_preflight_and_lifecycle(self):
        self.assertIn('"/acquisition/models/" + encodeURIComponent(modelData.catalog_id) + "/preflight"', PANEL)
        self.assertIn('"/acquisitions"', PANEL)
        self.assertIn('"/acquisitions/" + encodeURIComponent(trackedAcquisitionId)', PANEL)
        self.assertIn('refreshModels(); refreshCatalog()', PANEL)

    def test_main_rows_use_plain_fit_and_context_language(self):
        for phrase in ('Hardware fit hasn’t been verified yet.',
                       'Expected to run comfortably.',
                       'Expected to run well.',
                       'Estimated working context: ',
                       'Working context hasn’t been estimated yet.',
                       'Selected'):
            self.assertIn(phrase, PANEL)
        self.assertIn('how much conversation and reference material', PANEL)
        self.assertNotIn('FIT // ', PANEL[PANEL.index('id: modelSelector'):])

    def test_bookkeeping_is_only_rendered_inside_details_controls(self):
        selector = PANEL[PANEL.index('id: modelSelector'):]
        details_start = selector.index('Column { visible: root.selectorDetailsExpanded')
        details = selector[details_start:selector.index('Repeater { model: root.orderedCatalogModels', details_start)]
        for value in ('root.catalogSummary', 'root.hardwareSummary', 'root.recommendationSummary'):
            self.assertIn(value, details)
        self.assertIn('root.modelDetailsOpen(modelData.catalog_id)', selector)
        self.assertIn('Native maximum: ', PANEL[PANEL.index('function catalogPropertiesSummary'):])
        self.assertIn('Fit estimate confidence: ', PANEL[PANEL.index('function catalogFitConfidenceSummary'):])
        for helper in ('catalogPropertiesSummary', 'catalogFitConfidenceSummary'):
            self.assertIn('root.' + helper + '(modelData)', selector)
            self.assertIn('root.' + helper + '(installedModelRow.catalogMatch)', selector)

    def test_model_details_control_cannot_switch_models(self):
        start = PANEL.index('function toggleModelDetails')
        end = PANEL.index('function selectModel', start)
        self.assertNotIn('selectModel', PANEL[start:end])
        selector = PANEL[PANEL.index('id: modelSelector'):]
        details_button = selector[selector.index('id: modelDetailsButton'):selector.index('Column { visible: root.modelDetailsOpen')]
        self.assertIn('root.toggleModelDetails(modelData.catalog_id)', details_button)
        self.assertNotIn('root.selectModel(', details_button)

    def test_terminal_acquisition_status_stops_saying_installing(self):
        lifecycle = PANEL[PANEL.index('function acquisitionLifecycleStatus'):
                          PANEL.index('function refreshTrackedAcquisition')]
        self.assertIn('INSTALLED AND CHECKED // ', lifecycle)
        self.assertIn('CHECK NOT CONFIRMED', lifecycle)
        self.assertIn(' // NOT IN USE YET', lifecycle)
        self.assertIn('INSTALLATION FAILED // ', lifecycle)
        self.assertIn('VERIFYING INSTALLATION // ', lifecycle)
        self.assertIn('INSTALLING // ', lifecycle)
        self.assertNotIn('"INSTALLING // " + target + " // " + state',
                         lifecycle[:lifecycle.index('return "INSTALLING // "')])

    def test_terminal_transition_replaces_approval_checking_feedback(self):
        transition = PANEL[PANEL.index('function acquisitionTerminalFeedback'):
                           PANEL.index('function refreshTrackedAcquisition')]
        self.assertIn('Download finished and checked. Choose LOAD AND USE to start it.', transition)
        self.assertIn('Installation failed. Nothing was started.', transition)
        self.assertIn('approvalFeedback = acquisitionTerminalFeedback(item)', transition)
        self.assertIn('applyTrackedAcquisition(JSON.parse(xhr.responseText))', PANEL)

    def test_model_actions_are_explicit_buttons_and_status_is_not_an_action(self):
        selector = PANEL[PANEL.index('id: modelSelector'):PANEL.index('id: fixedBottom')]
        self.assertIn('objectName: "acquisitionStatusBadge"', selector)
        self.assertIn('objectName: "installModelButton"', selector)
        self.assertIn('objectName: "activateModelButton"', selector)
        self.assertIn('text: "LOAD AND USE " + root.catalogDisplayName(modelData.catalog_id).toUpperCase()', selector)
        self.assertIn('root.selectModel(root.catalogRuntimeId(modelData), true)', selector)
        self.assertNotIn('height: Style.space(24); cursorShape: Qt.PointingHandCursor; enabled: root.catalogIsInstalled', selector)

    def test_load_and_activation_labels_name_the_target(self):
        labels = PANEL[PANEL.index('function onboardingActionLabel'):
                       PANEL.index('function modelForCatalog')]
        self.assertIn('"LOAD AND VERIFY " + (onboardingTarget ? runtimeDisplayName(onboardingTarget) : "MODEL").toUpperCase()', labels)
        self.assertIn('"LOAD AND USE " + (onboardingTarget ? runtimeDisplayName(onboardingTarget) : "MODEL").toUpperCase()', labels)

    def test_long_names_and_status_rows_wrap(self):
        selector = PANEL[PANEL.index('id: modelSelector'):]
        self.assertIn('root.catalogRuntimeId(modelData)', selector)
        self.assertGreaterEqual(selector.count('wrapMode: Text.Wrap'), 7)

    def test_switch_destination_uses_existing_authoritative_target(self):
        start = PANEL.index('function isSwitchDestination')
        end = PANEL.index('function selectModel', start)
        helper = PANEL[start:end]
        self.assertIn('modelTransitionActive', helper)
        self.assertIn('transitionTarget', helper)
        self.assertIn('catalogRuntimeId(modelData) === transitionTarget', helper)
        selector = PANEL[PANEL.index('id: modelSelector'):]
        self.assertIn('root.isSwitchDestination(modelData) ? "SWITCHING…"', selector)  # card tag (UX Phase 2 step 2c)
        self.assertNotIn('Loading…', selector)
        self.assertNotIn('  Ready', selector)

    def test_switch_label_clears_with_existing_success_and_failure_state(self):
        switch = PANEL[PANEL.index('function selectModel'):PANEL.index('function applyStreamState')]
        self.assertIn('modelSwitching = false', switch)
        self.assertGreaterEqual(switch.count('requestedModel = ""'), 3)
        self.assertIn('hostWidget.modelTarget = ""', switch)
        self.assertIn('root.isExplicitlySelected(modelData) ? "IN USE"', PANEL)

    def test_telemetry_distinguishes_activity_memory_and_unavailable_data(self):
        telemetry = PANEL[PANEL.index('id: telemetryFrame'):PANEL.index('id: approvalForegroundSpace')]
        for label in ('label: "GPU"', 'label: "GPU MEM"', 'label: "CPU"', 'label: "RAM"', 'label: "DISK"'):
            self.assertIn(label, telemetry)
        self.assertIn('How busy the graphics processor is.', PANEL)
        self.assertIn('High memory use does not necessarily mean high activity.', PANEL)
        # Run 5: readings the machine can't report are hidden, not shown as empty boxes.
        self.assertIn('visible: root.gpuAvailable && root.valid(root.gpu)', telemetry)
        self.assertIn('visible: root.valid(root.vramUsed) && root.valid(root.vramTotal) && root.vramTotal > 0', telemetry)
        self.assertIn('Machine readings unavailable', telemetry)
        self.assertNotIn('Data unavailable', PANEL)

    def test_gpu_explanations_use_wrapped_standard_tooltips_from_rows(self):
        self.assertIn('Ui.PanelToolTip', PANEL)
        self.assertIn('hoverEnabled: row.tooltipText !== ""', PANEL)
        self.assertIn('activeFocusOnTab: row.tooltipText !== ""', PANEL)
        self.assertIn('acceptedButtons: Qt.NoButton', PANEL)
        self.assertIn('rowHelpTarget.containsMouse || rowHelpTarget.activeFocus', PANEL)
        self.assertIn('wrapMode: Text.WordWrap', PANEL)
        self.assertIn('tooltipText: root.gpuActivityTooltip()', PANEL)
        self.assertIn('tooltipText: root.gpuMemoryTooltip()', PANEL)

    def test_gpu_tooltips_preserve_compact_source_subtitles_without_inference(self):
        start = PANEL.index('function gpuActivitySubtitle')
        end = PANEL.index('function refreshAge', start)
        gpu_help = PANEL[start:end]
        self.assertIn('return gpuAvailable ? gpuName', gpu_help)
        self.assertIn('celsius(gpuTemperature)', gpu_help)
        self.assertIn('watts(gpuPower)', gpu_help)
        self.assertIn('"\\n\\nGPU: " + gpuName', gpu_help)
        self.assertIn('"\\n\\n" + gpuMemorySubtitle()', gpu_help)

    def test_temperature_is_nvme_only_when_selected_source_proves_it(self):
        start = PANEL.index('function temperatureIsNvme')
        end = PANEL.index('function refreshAge', start)
        temperature = PANEL[start:end]
        self.assertIn('temperatureLabel.toLowerCase().indexOf("nvme:") === 0', temperature)
        self.assertIn('"SSD temperature"', temperature)
        self.assertIn('"Highest reported NVMe sensor"', temperature)
        self.assertIn('"Hardware temperature"', temperature)
        self.assertIn('"Highest reported hardware sensor: " + temperatureLabel', temperature)

    def test_disk_and_temperature_do_not_render_unimplemented_history(self):
        telemetry = PANEL[PANEL.index('id: telemetryFrame'):PANEL.index('id: approvalForegroundSpace')]
        # Only GPU, CPU and the two network rows (2026-10-09) have a history; disk shows
        # free space, temperature sits in the frame.
        self.assertEqual(telemetry.count('dots: true'), 4)
        self.assertIn('value: root.freeSpace(root.diskUsed, root.diskTotal)', telemetry)
        self.assertIn('(root.temperatureIsNvme() ? "SSD " : "TEMP ") + root.celsius(root.systemTemperature)', telemetry)
        self.assertIn('visible: telemetryFrame.hasTemperature', telemetry)

    def test_zero_samples_are_retained_while_missing_samples_are_not(self):
        self.assertIn('if (!isFinite(value) || value < 0) return history', BAR_WIDGET)
        self.assertIn('next.push(Math.max(0, Math.min(100, value)))', BAR_WIDGET)
        self.assertIn('visible: root.valid(root.cpu); label: "CPU"', PANEL)

    def test_history_graphs_use_consistent_dot_geometry_and_the_theme_tint(self):
        graph = PANEL[PANEL.index('component TelemetryRow'):PANEL.index('component ChatLine')]
        self.assertIn('var pitch = 3, dot = 2', graph)
        self.assertIn('ctx.fillRect(c * pitch, height - (r + 1) * pitch, dot, dot)', graph)
        self.assertIn('Qt.rgba(tint.r, tint.g, tint.b,', graph)
        self.assertIn('orientation: Gradient.Horizontal', graph)

    def test_history_graphs_keep_zero_at_baseline_and_do_not_render_missing_as_zero(self):
        graph = PANEL[PANEL.index('component TelemetryRow'):PANEL.index('component ChatLine')]
        self.assertIn('if (raw === null || typeof raw === "undefined" || !isFinite(Number(raw)) || Number(raw) < 0) continue', graph)
        self.assertIn('var lit = v > 0 ?', graph)
        self.assertNotIn('Number(modelData) || 0', graph)

    def test_telemetry_hierarchy_uses_theme_derived_text_contrast(self):
        row = PANEL[PANEL.index('component TelemetryRow'):PANEL.index('component ChatLine')]
        self.assertIn('color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.72)', row)
        self.assertIn('color: row.fraction >= 0.85 ? row.tint : root.foreground', row)
        # Colour warms only near the limit, from theme colours.
        self.assertIn('if (fraction >= 0.95) return Commons.Color.urgent', PANEL)
        self.assertIn('if (fraction >= 0.85) return Qt.rgba((accent.r + Commons.Color.urgent.r) / 2', PANEL)

    def test_chat_marks_nova_replies_and_only_successful_data_sources(self):
        self.assertIn('chatLine.responseStatus === "incomplete" ? "NOVA · INCOMPLETE" : "NOVA"', PANEL)
        self.assertIn('else if (event === "data_source") root.addDataSource(payload)', PANEL)
        start = PANEL.index('function addDataSource')
        end = PANEL.index('function markAssistantError', start)
        marker = PANEL[start:end]
        self.assertIn('payload.source', marker)
        self.assertIn('payload.checked_at', marker)
        self.assertIn('item.source === payload.source', marker)
        self.assertIn('sources.push', marker)
        self.assertIn('text: "Source: " + chatLine.sourceSummary()', PANEL)
        self.assertIn('addChat("assistant", "", [], "streaming")', PANEL)
        self.assertIn('sources: Array.isArray(sources) ? sources : []', PANEL)
        self.assertNotIn('LIVE DATA', marker)

    def test_assistant_copy_uses_complete_stored_text_and_keeps_turn_state_separate(self):
        start = PANEL.index('component ChatLine')
        end = PANEL.index('Ui.KeyboardPanel', start)
        chat_line = PANEL[start:end]
        self.assertIn('import Quickshell', PANEL)
        self.assertIn('readonly property bool canCopy: role === "assistant" && !streaming && text.length > 0', chat_line)
        self.assertIn('Quickshell.clipboardText = text', chat_line)
        self.assertNotIn('Quickshell.clipboardText = messageText', chat_line)
        self.assertIn('tooltipText: chatLine.copied ? "Copied" : "Copy answer"', chat_line)
        self.assertIn('Accessible.name: "Copy answer"', chat_line)
        self.assertIn('copiedTimer.restart()', chat_line)
        self.assertIn('interval: 1400', chat_line)
        self.assertIn('focusable: true', chat_line)
        self.assertIn('anchors.right: parent.right', chat_line)

    def test_transcript_passes_sources_and_streaming_state_per_answer(self):
        delegate_start = PANEL.index('Repeater {', PANEL.index('id: chatLines'))
        delegate_end = PANEL.index('Item { width: parent.width; height: Style.spacing.md }', delegate_start)
        delegate = PANEL[delegate_start:delegate_end]
        self.assertIn('sources: model.sources', delegate)
        self.assertIn('streaming: model.role === "assistant" && root.chatBusy && index === chatHistoryModel.count - 1', delegate)

    def test_approval_card_uses_labeled_focusable_controls(self):
        approval = PANEL[PANEL.index('id: approvalColumn'):PANEL.index('Item { width: parent.width; height: Style.spacing.md }', PANEL.index('id: approvalColumn'))]
        self.assertIn('Ui.Button', approval)
        self.assertIn('text: "Cancel Esc"', approval)
        self.assertIn('text: "Approve ↵"', approval)
        self.assertIn('id: cancelApprovalButton', approval)
        self.assertIn('id: approveApprovalButton', approval)
        self.assertIn('id: cancelApprovalControl', approval)
        self.assertIn('id: approveApprovalControl', approval)
        self.assertGreaterEqual(approval.count('activeFocusOnTab: true'), 2)
        self.assertIn('Accessible.name: "Cancel approval"', approval)
        self.assertIn('Accessible.name: "Approve request"', approval)
        self.assertGreaterEqual(approval.count('enabled: root.pendingApproval && !root.pendingApproval.decision'), 2)
        self.assertIn('root.decideApproval("deny")', approval)
        self.assertIn('root.decideApproval("approve")', approval)

    def test_every_approval_owns_foreground_without_overwriting_preferences(self):
        self.assertIn('readonly property bool approvalForeground: pendingApproval !== null', PANEL)
        # UX Phase 2: an approval shows in CHAT; the view switch is the only layout change.
        self.assertIn('readonly property string shownView: approvalForeground ? "chat" : panelView', PANEL)
        self.assertIn('readonly property bool selectorVisible: shownView === "models"', PANEL)
        self.assertIn('onApprovalForegroundChanged: if (approvalForeground) setPanelView("chat")', PANEL)
        foreground = PANEL[PANEL.index('id: approvalForegroundSpace'):PANEL.index('id: chatViewport')]
        self.assertIn('id: approvalCard', foreground)
        self.assertIn('height: parent.height', foreground)
        self.assertNotIn('parent: root.', foreground)
        self.assertIn('anchors.bottom: approvalDecisionRow.top', PANEL)
        begin = PANEL[PANEL.index('function beginApproval'):PANEL.index('function requestApproval')]
        self.assertNotIn('panelView =', begin)

    def test_approval_http_result_is_reactive_visible_and_never_retried(self):
        decision = PANEL[PANEL.index('function decideApproval'):PANEL.index('function checkNovadRecovery')]
        self.assertIn('JSON.parse(JSON.stringify(pendingApproval))', decision)
        self.assertIn('item.decision = "sending"', decision)
        self.assertIn('approvalFeedback = decision === "approve" ? "Submitting approval…"', decision)
        self.assertIn('function finishApprovalDecision', decision)
        self.assertIn('"Installation approval expired. No download was started by this proposal."', decision)
        self.assertIn('"Action approval expired. The action was not authorized."', decision)
        self.assertIn('"Decision result unavailable. Check request status; do not retry automatically."', decision)
        self.assertIn('approvalDecisionDeadline.restart()', decision)
        self.assertEqual(decision.count('xhr.send()'), 1)

    def test_approval_controls_are_independent_of_chat_readiness_and_guard_keys(self):
        approval = PANEL[PANEL.index('id: approvalDecisionRow'):PANEL.index('Item { width: parent.width; height: Style.spacing.md }', PANEL.index('id: approvalDecisionRow'))]
        self.assertNotIn('onboardingCanSend', approval)
        self.assertNotIn('runtime_ready', approval)
        self.assertIn('focusable: false', approval)
        self.assertIn('hasCursor: approveApprovalControl.activeFocus', approval)
        self.assertIn('hasCursor: cancelApprovalControl.activeFocus', approval)
        self.assertIn('Keys.onReturnPressed: function(event) { root.approvalButtonKey(event) }', approval)
        self.assertIn('objectName: "approveApprovalHitTarget"', approval)
        self.assertIn('objectName: "cancelApprovalHitTarget"', approval)

    def test_expired_or_rejected_history_does_not_mask_fresh_preflight(self):
        state = PANEL[PANEL.index('function acquisitionState'):PANEL.index('function catalogRuntimeId')]
        self.assertIn('state.state !== "REJECTED" && state.state !== "EXPIRED"', state)
        self.assertIn('preflight.status === "ELIGIBLE"', state)
        detail = PANEL[PANEL.index('function acquisitionDetail'):PANEL.index('function acquisitionBlobProgress')]
        self.assertIn('PREVIOUS APPROVAL EXPIRED // REVIEW A NEW PROPOSAL', detail)
        self.assertIn('PREVIOUS PROPOSAL DECLINED // NO DOWNLOAD STARTED', detail)

    def test_approval_keyboard_gate_requires_release_then_routes_once(self):
        start = PANEL.index('function beginApproval')
        end = PANEL.index('function decideApproval', start)
        keyboard = PANEL[start:end]
        self.assertIn('approvalShortcutArmed = !chatSubmissionKeyDown', keyboard)
        self.assertIn('chatSubmissionKeyDown = false', keyboard)
        self.assertIn('approvalShortcutArmed = true', keyboard)
        self.assertIn('if (decision === "approve" && !approvalShortcutArmed) return true', keyboard)
        self.assertIn('Escape is always an explicit rejection while a request is pending.', keyboard)
        self.assertIn('approvalShortcutArmed = false', keyboard)
        self.assertIn('decideApproval(decision)', keyboard)
        catcher = PANEL[PANEL.index('id: keyCatcher'):PANEL.index('Item {', PANEL.index('id: keyCatcher'))]
        self.assertIn('blocked: chatInput.activeFocus && root.pendingApproval === null', catcher)
        self.assertIn('root.handleApprovalShortcut("deny")', catcher)
        # Approval decides first; only without a pending approval may Enter/Escape
        # reach the Open CLI confirm card.
        self.assertIn('onReturnRequested: { if (!root.handleApprovalShortcut("approve")) root.handleCliConfirmShortcut("launch") }', catcher)
        self.assertIn('if (!root.handleApprovalShortcut("deny") && !root.handleCliConfirmShortcut("cancel")) root.close()', catcher)
        cli = PANEL[PANEL.index('function handleCliConfirmShortcut'):PANEL.index('function revealInModelList')]
        self.assertIn('if (cliConfirmModel === "" || pendingApproval) return false', cli)
        self.assertIn('Keys.onReleased: function(event) { root.noteApprovalKeyReleased(event) }', catcher)
        self.assertIn('root.focusApprovalControl(direction)', catcher)

    def test_approval_arrival_forces_card_visibility_and_focuses_a_control(self):
        start = PANEL.index('function beginApproval')
        end = PANEL.index('function requestApproval', start)
        begin = PANEL[start:end]
        self.assertNotIn('chatViewport.contentY =', begin)
        self.assertNotIn('chatViewport.followLatest =', begin)
        self.assertIn('approveApprovalControl.forceActiveFocus()', begin)
        self.assertIn('approvalFocusIndex = 1', begin)

    def test_expiry_is_structured_and_controls_are_not_actionable(self):
        self.assertIn('function scheduleApprovalExpiry()', PANEL)
        self.assertIn('function markApprovalExpired()', PANEL)
        self.assertIn('next.decision = "expired"', PANEL)
        self.assertIn('Timer { id: approvalExpiryTimer;', PANEL)
        approval = PANEL[PANEL.index('id: approvalDecisionRow'):PANEL.index('Item { width: parent.width; height: Style.spacing.md }', PANEL.index('id: approvalDecisionRow'))]
        self.assertGreaterEqual(approval.count('visible: !!(root.pendingApproval && !root.pendingApproval.decision'), 2)
        self.assertIn('visible: !!(root.pendingApproval && root.pendingApproval.decision', approval)

    def test_simulated_approval_key_sequences_match_the_release_gate(self):
        # Mirrors the QML handler's three observable states. This guards the
        # required event ordering separately from source-string coverage.
        def shortcut(armed, decision, key):
            if decision:
                return armed, decision
            if key == 'enter' and not armed:
                return armed, None
            return False, 'approve' if key == 'enter' else 'deny'

        # Approval after the submission key was released: fresh Enter decides.
        armed, decision = shortcut(True, None, 'enter')
        self.assertEqual((armed, decision), (False, 'approve'))
        # Approval while Enter is held: repeats do not decide; release arms the
        # next fresh press, which decides once.
        armed, decision = shortcut(False, None, 'enter')
        self.assertEqual((armed, decision), (False, None))
        armed = True  # simulated release
        armed, decision = shortcut(armed, decision, 'enter')
        self.assertEqual((armed, decision), (False, 'approve'))
        self.assertEqual(shortcut(armed, decision, 'enter'), (False, 'approve'))
        # Escape remains available before the release gate is armed.
        self.assertEqual(shortcut(False, None, 'escape'), (False, 'deny'))

    def test_submission_enter_is_tracked_without_changing_normal_input_acceptance(self):
        input_start = PANEL.index('id: chatInput')
        input_end = PANEL.index('Ui.PanelActionButton', input_start)
        chat_input = PANEL[input_start:input_end]
        self.assertIn('onAccepted: root.sendChat()', chat_input)
        self.assertIn('Keys.onPressed: function(event) { root.noteChatKeyPressed(event) }', chat_input)
        self.assertIn('Keys.onReleased: function(event) { root.noteApprovalKeyReleased(event) }', chat_input)
        self.assertIn('if (!root.handleApprovalShortcut("deny")) root.close()', chat_input)
        self.assertIn('root.pendingApproval === null', chat_input)

    def test_approval_completion_clears_card_and_restores_input_when_available(self):
        clear = PANEL[PANEL.index('function clearPendingApproval()'):PANEL.index('function restoreChatInputFocus()')]
        self.assertIn('approvalExpiryTimer.stop()', clear)
        self.assertIn('pendingApproval = null', clear)
        self.assertIn('approvalShortcutArmed = true', clear)
        self.assertIn('if (!root.pendingApproval && !root.chatBusy && chatInput && chatInput.enabled) chatInput.forceActiveFocus()', PANEL)
        self.assertIn('else if (event === "action_result") root.handleActionResult(payload)', PANEL)
        self.assertIn('root.restoreChatInputFocus()', PANEL)

    def test_chat_keeps_reader_position(self):
        self.assertIn('contentHeight: Math.max(height, chatLines.implicitHeight)', PANEL)
        self.assertIn('if (!chatViewport || (!chatViewport.followLatest && !chatViewport.nearBottom())) return', PANEL)

    def test_views_show_their_own_blocks(self):
        # UX Phase 2 (2026-10-09): CHAT core band + conversation + input, MODELS the list,
        # MACHINE the big core + telemetry. The waves only run while the core is shown.
        core = PANEL[PANEL.index('id: corePanel'):PANEL.index('id: telemetryFrame')]
        self.assertIn('visible: root.coreVisible', core)
        self.assertIn('root.shownView === "machine" ? Math.max(root.coreBandHeight, Math.min(Style.space(200), machineRoom)) : root.coreBandHeight', core)
        self.assertIn('root.opened && root.coreVisible && visible', core)
        self.assertIn('running: pixelWave.ticking', core)
        self.assertIn('root.opened && root.coreVisible && active', core)
        self.assertIn('running: livingWave.ticking', core)
        self.assertIn('visible: root.shownView === "machine"', PANEL[PANEL.index('id: telemetryFrame'):])
        self.assertIn('visible: root.selectorVisible', PANEL[PANEL.index('id: modelSelector'):])
        self.assertIn('visible: root.shownView === "chat" && !root.approvalForeground', PANEL[PANEL.index('id: chatViewport'):])
        self.assertIn('visible: root.shownView === "chat"  // the message box lives in CHAT only', PANEL)
        self.assertNotIn('EXPAND CHAT', PANEL)
        self.assertIn('id: chatInput', PANEL)
        self.assertIn('sources: model.sources', PANEL)
        self.assertIn('panel.cappedContentHeight(', PANEL)


if __name__ == '__main__':
    unittest.main()
