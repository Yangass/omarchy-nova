"""Contract checks for the isolated production-panel onboarding preview."""
import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "onboarding_preview", ROOT / "tools/onboarding_preview.py")
PREVIEW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREVIEW)
PANEL = (ROOT / "Panel.qml").read_text()
BAR = (ROOT / "BarWidget.qml").read_text()
TEMPLATE = (ROOT / "tools/onboarding_preview_shell.qml").read_text()


class OnboardingPreviewTests(unittest.TestCase):
    def setUp(self):
        self.fixtures = PREVIEW.scenarios()

    def test_required_acceptance_branches_are_present(self):
        expected = {
            "complete-flow": "NEEDS_MODEL",
            "runtime-unreachable": "NEEDS_USER_SETUP",
            "no-models": "NEEDS_MODEL",
            "installed-no-selection": "NEEDS_ACTIVATION",
            "no-eligible-candidate": "NEEDS_MODEL",
            "install-approval": "ACQUISITION_PROPOSED",
            "install-failure": "INSTALLATION_FAILED",
            "download-progress-unknown": "ACQUISITION_IN_PROGRESS",
            "download-progress-blob-transition": "ACQUISITION_IN_PROGRESS",
            "verified-awaiting-activation": "NEEDS_ACTIVATION",
        }
        for name, state in expected.items():
            with self.subTest(name=name):
                self.assertEqual(self.fixtures[name]["onboarding"]["primary_state"], state)

    def test_provider_unreachable_is_not_claimed_as_not_installed(self):
        fixture = self.fixtures["provider-unreachable"]
        self.assertIn("PROVIDER_UNAVAILABLE", fixture["onboarding"]["explanation_codes"])
        message = PANEL[PANEL.index('function onboardingMessage'):
                        PANEL.index('function onboardingActionLabel')]
        # Unreachable stays ambiguous ("installed or running"), never a definite "not installed".
        self.assertIn("Ollama, the engine that runs AI models, isn’t installed or running yet.", message)
        self.assertNotIn("Ollama is not installed", message)
        # Install vs start is decided from the package's service file, not from reachability.
        self.assertIn('path: "/usr/lib/systemd/system/ollama.service"', PANEL)

    def test_candidate_actions_preserve_policy_meanings(self):
        self.assertEqual(self.fixtures["no-models"]["onboarding"]["allowed_actions"],
                         ["PROPOSE_INSTALL"])
        blocked = self.fixtures["no-eligible-candidate"]["onboarding"]
        self.assertNotIn("PROPOSE_INSTALL", blocked["allowed_actions"])
        self.assertIn("NO_ELIGIBLE_CATALOG_CANDIDATE", blocked["explanation_codes"])
        self.assertIn("INSUFFICIENT_STORAGE", " ".join(blocked["limitations"]))

    def test_approval_displays_frozen_storage_values(self):
        fixture = self.fixtures["install-approval"]
        approval = fixture["pending_approval"]
        self.assertEqual(fixture["onboarding"]["primary_state"],
                         "ACQUISITION_PROPOSED")
        self.assertNotIn("ACQUISITION_APPROVED",
                         fixture["onboarding"]["explanation_codes"])
        self.assertEqual(fixture["acquisition_by_catalog"], {})
        self.assertEqual(approval["size"], PREVIEW.ARTIFACT_SIZE)
        self.assertEqual(approval["requiredSpace"], PREVIEW.REQUIRED_SPACE)
        self.assertEqual(approval["availableSpace"], PREVIEW.AVAILABLE_SPACE)
        # Cancellation is disclosed by its own approval-card row; the preview impact matches the live one.
        self.assertIn(approval["impact"], (ROOT / "Panel.qml").read_text())

    def test_failure_and_verified_install_are_explicit(self):
        failure = self.fixtures["install-failure"]
        self.assertEqual(failure["acquisition_by_catalog"][PREVIEW.CATALOG_ID]["state"],
                         "FAILED")
        verified = self.fixtures["verified-awaiting-activation"]["onboarding"]
        self.assertIn("ACQUISITION_INSTALLED", verified["explanation_codes"])
        self.assertEqual(verified["allowed_actions"], ["ACTIVATE_MODEL"])

    def test_preview_uses_the_canonical_production_panel(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as raw:
            target = Path(raw)
            shell_file = PREVIEW.prepare_config(target, self.fixtures["no-models"])
            self.assertEqual((target / "Panel.qml").resolve(), ROOT / "Panel.qml")
            self.assertIn("ACCEPTANCE PREVIEW", PANEL)
            self.assertNotIn("@FIXTURE_JSON@", shell_file.read_text())

    def test_deployed_host_does_not_enter_preview_by_default(self):
        self.assertIn("property bool acceptancePreview: false", BAR)
        self.assertIn("property var acceptancePreviewFixture: null", BAR)

    def test_preview_boundary_blocks_live_mutation_paths(self):
        self.assertIn('property string api: "fixture://no-production-api"', TEMPLATE)
        self.assertNotIn("127.0.0.1", TEMPLATE)
        functions = (
            ("proposeCatalogAcquisition", "refreshRecommendations"),
            ("selectModel", "applyStreamState"),
            ("decideApproval", "checkNovadRecovery"),
            ("sendChat", "open"),
        )
        for start_name, end_name in functions:
            with self.subTest(function=start_name):
                body = PANEL[PANEL.index("function " + start_name):
                             PANEL.index("function " + end_name,
                                         PANEL.index("function " + start_name))]
                guard = body.index("acceptancePreview")
                request = body.find("new XMLHttpRequest")
                self.assertTrue(request < 0 or guard < request)

    def test_preview_actions_are_local_fixture_transitions(self):
        action = PANEL[PANEL.index('function performAcceptancePreviewAction'):
                       PANEL.index('function isExplicitlySelected')]
        self.assertNotIn("XMLHttpRequest", action)
        self.assertIn("beginApproval(acceptancePreviewFixture.install_approval)", action)
        self.assertIn('modelSwitchStatus = "PREVIEW // ACTIVATION NOT EXECUTED"', action)
        approval = PANEL[PANEL.index('function decideApproval'):
                         PANEL.index('function checkNovadRecovery')]
        preview_decision = approval[approval.index("if (acceptancePreview)"):
                                    approval.index("var xhr")]
        self.assertIn("pendingApproval = null", preview_decision)
        self.assertIn("PREVIEW_APPROVAL_RECORDED", preview_decision)
        self.assertIn("NO DOWNLOAD STARTED", preview_decision)
        self.assertNotIn('onboardingState = "ACQUISITION_IN_PROGRESS"',
                         preview_decision)

    def test_compact_core_wave_fills_region_above_status(self):
        core = PANEL[PANEL.index("id: corePanel"):
                     PANEL.index('id: telemetryFrame')]
        self.assertIn("id: coreAnimationRegion", core)
        self.assertIn("anchors.bottom: coreStatus.top", core)
        self.assertIn("anchors.margins: Style.spacing.sm", core)
        self.assertIn("property real uFieldW: Math.min(width, height * root.coreAspect)", core)
        # Shader wave and its Canvas fallback both fill the bounded region.
        region = core[core.index("id: coreAnimationRegion"):]
        self.assertEqual(region.count("anchors.fill: parent"), 2)
        self.assertIn('fragmentShader: Qt.resolvedUrl("wave.frag.qsb")', region)
        self.assertIn("visible: !livingWave.active", region)
        self.assertNotIn("width: Style.space(112); height: width", core)

    def test_continuous_simulation_uses_classifier_states(self):
        fixture = self.fixtures["complete-flow"]
        self.assertTrue(fixture["continuous_simulation"])
        expected = {
            "initial": ("NEEDS_MODEL", ["PROPOSE_INSTALL"]),
            "proposed": ("ACQUISITION_PROPOSED", ["DECIDE_INSTALL_APPROVAL"]),
            "downloading": ("ACQUISITION_IN_PROGRESS", []),
            "verifying": ("ACQUISITION_IN_PROGRESS", []),
            "failed": ("INSTALLATION_FAILED", ["RECHECK_MODEL_OPTIONS"]),
            "installed": ("NEEDS_ACTIVATION", ["ACTIVATE_MODEL"]),
            "activating": ("ACTIVATING", []),
            "ready": ("READY", ["SEND_FIRST_MESSAGE"]),
        }
        for stage, (state, actions) in expected.items():
            with self.subTest(stage=stage):
                payload = fixture["simulation_states"][stage]["onboarding"]
                self.assertEqual(payload["primary_state"], state)
                self.assertEqual(payload["allowed_actions"], actions)

    def test_continuous_simulation_preserves_install_activation_boundary(self):
        states = self.fixtures["complete-flow"]["simulation_states"]
        self.assertFalse(states["installed"]["configuration"]["explicit_selection"])
        self.assertFalse(states["installed"]["host"]["runtime_ready"])
        self.assertTrue(states["ready"]["configuration"]["explicit_selection"])
        self.assertTrue(states["ready"]["host"]["runtime_ready"])
        self.assertIn("NO INFERENCE",
                      self.fixtures["complete-flow"]["canned_reply"])

    def test_continuous_transition_contract_requires_approval(self):
        install = PANEL[PANEL.index("function beginAcceptanceSimulationInstall"):
                        PANEL.index("function beginAcceptanceSimulationActivation")]
        self.assertIn('applyAcceptanceSimulationStage("proposed")', install)
        self.assertIn("beginApproval", install)
        apply_fixture = PANEL[PANEL.index("function applyAcceptanceFixture"):
                              PANEL.index("function applyAcceptancePreview")]
        self.assertIn("JSON.parse(JSON.stringify(fixture.pending_approval))",
                      apply_fixture)
        decision = PANEL[PANEL.index("function decideApproval"):
                         PANEL.index("function checkNovadRecovery")]
        start = decision.index("if (acceptanceSimulation)")
        simulation = decision[start:decision.index(
            "pendingApproval = null\n      onboardingState", start)]
        self.assertIn('decision === "approve"', simulation)
        self.assertIn('applyAcceptanceSimulationStage("downloading")', simulation)
        self.assertIn('applyAcceptanceSimulationStage("initial")', simulation)
        self.assertIn("APPROVAL CANCELLED", simulation)

    def test_continuous_progress_failure_restart_and_reply_are_fixture_only(self):
        advance = PANEL[PANEL.index("function advanceAcceptanceSimulation"):
                        PANEL.index("function sendAcceptanceSimulationReply")]
        self.assertIn('"downloading"', advance)
        self.assertIn('"verifying"', advance)
        self.assertIn('acceptanceSimulationFailure ? "failed" : "installed"', advance)
        self.assertIn('"activating"', advance)
        reply = PANEL[PANEL.index("function sendAcceptanceSimulationReply"):
                      PANEL.index("function acceptanceSimulationStageLabel")]
        self.assertIn("acceptancePreviewFixture.canned_reply", reply)
        self.assertNotIn("XMLHttpRequest", advance + reply)
        self.assertIn("function resetAcceptanceSimulation", PANEL)
        self.assertIn("RESTART SIMULATION", PANEL)
        self.assertIn("FIXTURE SIMULATION // NO LIVE ACTIONS", PANEL)
        self.assertNotIn("IpcHandler", TEMPLATE)
        self.assertIn("acceptanceSimulation", PANEL)
        self.assertIn("|| (onboardingReceivedAt > 0", PANEL)

    def test_progress_fixtures_cover_unknown_zero_cached_and_blob_transition(self):
        unknown = self.fixtures["download-progress-unknown"]["acquisition_by_catalog"][PREVIEW.CATALOG_ID]["progress"]
        self.assertIsNone(unknown["current_blob_total_bytes"])
        transition = self.fixtures["download-progress-blob-transition"]["acquisition_by_catalog"][PREVIEW.CATALOG_ID]["progress"]
        self.assertEqual(transition["current_blob_index"], 2)
        self.assertEqual(transition["current_blob_completed_bytes"], 0)
        self.assertGreater(transition["provider_completed_bytes"], 0)
        self.assertIsNone(transition["bytes_transferred"])
        known = self.fixtures["complete-flow"]["simulation_states"]["downloading"]["acquisition_by_catalog"][PREVIEW.CATALOG_ID]["progress"]
        self.assertGreater(known["current_blob_total_bytes"], 0)
        self.assertGreater(known["current_blob_completed_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
