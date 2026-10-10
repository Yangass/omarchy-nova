"""Focused P1-01E.5 source and isolated-fixture contracts."""
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PANEL = (ROOT / "Panel.qml").read_text()
SPEC = importlib.util.spec_from_file_location("onboarding_preview", ROOT / "tools/onboarding_preview.py")
PREVIEW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREVIEW)


class RecoveryUiTests(unittest.TestCase):
    def test_resolved_retry_requires_both_resolution_and_fresh_eligibility(self):
        state = PANEL[PANEL.index("function acquisitionState"):PANEL.index("function catalogRuntimeId")]
        self.assertIn("recoveryAllowsRetry(state)", state)
        self.assertIn('preflight.status === "ELIGIBLE"', state)
        self.assertNotIn("return \"AVAILABLE TO INSTALL\"", state.split("var preflight", 1)[0])

    def test_review_uses_only_existing_recovery_routes(self):
        block = PANEL[PANEL.index("function reviewRecovery"):PANEL.index("function acquisitionBlobProgress")]
        self.assertIn('"/acquisitions/" + encodeURIComponent(id) + "/recovery"', block)
        self.assertIn('"/resolve-recovery"', block)
        self.assertIn('"NO_PROVIDER_ACQUISITION_ACTIVE"', block)
        self.assertIn("operatorConfirmed === true", block)
        self.assertIn("It does not start downloading", block)
        for forbidden in ("/propose", "ollama pull", "automatic retry"):
            self.assertNotIn(forbidden, block)

    def test_recovery_card_reuses_foreground_and_shared_decision_controls(self):
        self.assertIn('pendingApproval.recovery ? "RECOVERY REVIEW"', PANEL)
        self.assertIn('objectName: "recoveryConfirmationControl"', PANEL)
        self.assertIn('objectName: "approveApprovalHitTarget"', PANEL)
        self.assertIn('objectName: "cancelApprovalHitTarget"', PANEL)
        self.assertIn('text: "Approve ↵"', PANEL)
        self.assertIn("recoveryAllowsRetry(root.acquisitionFor(modelData.catalog_id))", PANEL)
        self.assertIn("acquisitionHistory(modelData)", PANEL)

    def test_fixtures_cover_unresolved_retry_blocked_and_installed(self):
        scenarios = PREVIEW.scenarios()
        unresolved = scenarios["recovery-unresolved"]
        self.assertEqual(unresolved["acquisition_by_catalog"][PREVIEW.CATALOG_ID]["state"], "RECOVERY_REQUIRED")
        self.assertIsNone(unresolved["acquisition_by_catalog"][PREVIEW.CATALOG_ID]["recovery_resolution"])
        self.assertTrue(unresolved["recovery_assessment"]["operator_attestation_required"])
        retry = scenarios["recovery-retry-allowed"]
        self.assertTrue(retry["acquisition_by_catalog"][PREVIEW.CATALOG_ID]["recovery_resolution"]["retry_allowed"])
        self.assertEqual(retry["preflight_by_catalog"][PREVIEW.CATALOG_ID]["status"], "ELIGIBLE")
        blocked = scenarios["recovery-blocked-mismatch"]
        self.assertEqual(blocked["recovery_assessment"]["artifact_state"], "MISMATCH")
        self.assertEqual(blocked["recovery_assessment"]["resolution_status"], "BLOCKED")
        installed = scenarios["verified-awaiting-activation"]
        self.assertEqual(installed["catalog_models"][0]["installed_match"]["match_status"], "exact")

    def test_stale_and_duplicate_protection_remain_backend_owned(self):
        lifecycle = (ROOT / "novad/acquisition_lifecycle.py").read_text()
        self.assertIn("stale_recovery_assessment", lifecycle)
        self.assertIn("recovery_already_resolved", lifecycle)
        self.assertIn("provider_acquisition_active", lifecycle)
        self.assertIn("artifact_revision_mismatch", lifecycle)
        self.assertIn("artifact_identity_unverifiable", lifecycle)


if __name__ == "__main__":
    unittest.main()
