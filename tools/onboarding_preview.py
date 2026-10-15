#!/usr/bin/env python3
"""Launch the production NOVA panel against isolated onboarding fixtures.

The tool creates only a temporary Quickshell config under /tmp. The production
Panel.qml is symlinked into that config, and its acceptance-preview boundary
prevents live reads and mutations. No deployed plugin or shell configuration is
changed.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "novad"))
import onboarding as ob  # noqa: E402


CATALOG_ID = "qwen3-8b-q4-k-m"
MODEL_ID = "qwen3:8b"
ARTIFACT_SIZE = 5_225_388_164
REQUIRED_SPACE = 13_814_185_472
AVAILABLE_SPACE = 96_636_764_160


def _base(**changes):
    values = dict(
        prerequisites=ob.PrerequisiteSnapshot("AVAILABLE", "AVAILABLE", "OBSERVED"),
        inventory=ob.InventorySnapshot("KNOWN", (MODEL_ID,)),
        selection=ob.SelectionSnapshot("VALID", MODEL_ID, "INSTALLED_EXACT", True),
        candidates=(),
        recommendation=ob.RecommendationSnapshot("NO_DEFENSIBLE_RECOMMENDATION"),
        operations=(),
    )
    values.update(changes)
    return ob.OnboardingSnapshot(**values)


def _candidate(status="ELIGIBLE", artifact="NOT_INSTALLED", blockers=()):
    installed_id = MODEL_ID if artifact == "INSTALLED_EXACT" else None
    return ob.CandidateSnapshot(CATALOG_ID, status, artifact, installed_id, tuple(blockers))


def _catalog_model(installed=False, trusted=True):
    catalog = json.loads((ROOT / "catalog/models.json").read_text())
    model = deepcopy(next(item for item in catalog["models"] if item["catalog_id"] == CATALOG_ID))
    model["installed_match"] = {
        "match_status": "exact" if installed else "not_installed",
        "installed": installed,
        "installed_model_id": MODEL_ID if installed else None,
    }
    if not trusted:
        model.pop("acquisition", None)
    return model


def _approval():
    return {
        "id": "fixture-install-approval",
        "action": "acquire_catalog_model",
        "acquisition": True,
        "target": "",
        "process": "",
        "pid": 0,
        "owner": "",
        "application": "",
        "decision": "",
        "summary": "INSTALL QWEN 3 8B (Q4_K_M)",
        "provider": "ollama",
        "size": ARTIFACT_SIZE,
        "requiredSpace": REQUIRED_SPACE,
        "availableSpace": AVAILABLE_SPACE,
        "fit": "GOOD",
        "trust": "backend_allowlist",
        "provenance": "CURATED LOCAL CATALOG // PINNED ARTIFACT",
        "impact": "Ollama downloads the model and NOVA checks that it is complete. Nothing else on this computer changes.",
    }


def _fixture(snapshot, label, *, online=True, installed=False, trusted=True,
             preflight=None, acquisition=None, approval=None, models_expanded=True,
             chat_expanded=False, snapshot_fresh=True, runtime_state="idle",
             acquisition_status=None):
    result = ob.classify(snapshot).as_dict()
    model = _catalog_model(installed=installed, trusted=trusted)
    return {
        "label": label,
        "onboarding": result,
        "snapshot_fresh": snapshot_fresh,
        "configuration": {
            "explicit_selection": snapshot.selection.config_state == "VALID",
            "provider": "ollama" if snapshot.selection.config_state == "VALID" else None,
            "model": snapshot.selection.selected_model_id,
        },
        "host": {
            "online": online,
            "runtime_state": runtime_state if online else "error",
            "active_provider": "ollama" if snapshot.selection.config_state == "VALID" else "",
            "active_model": snapshot.selection.selected_model_id or "",
            "active_available": snapshot.selection.artifact_state == "INSTALLED_EXACT",
            "runtime_ready": snapshot.selection.runtime_ready is True,
        },
        "discovered_models": ([{"id": MODEL_ID, "provider": "ollama", "available": True}]
                              if installed else []),
        "catalog_models": [model],
        "model_fits": {MODEL_ID: {"rating": "GOOD", "confidence": "MEDIUM",
                                    "recommended_context": 32768}},
        "preflight_by_catalog": {CATALOG_ID: preflight} if preflight else {},
        "acquisition_by_catalog": {CATALOG_ID: acquisition} if acquisition else {},
        "pending_approval": approval,
        "install_approval": _approval(),
        "models_expanded": models_expanded,
        "chat_expanded": chat_expanded,
        "hardware_summary": "LOCAL AI // FIXTURE GPU · RAM 32.0 GiB · EST. BUDGET 12.0 GiB",
        "catalog_summary": "CATALOG // FIXTURE // 1 MODEL",
        "recommendation_summary": "RECOMMEND // INSUFFICIENT EVIDENCE",
        "acquisition_status": (acquisition_status if acquisition_status is not None
                               else ("INSTALLATION FAILED // FIXTURE" if acquisition and acquisition.get("state") == "FAILED" else "")),
    }


def _continuous_simulation():
    """Build one isolated classifier-backed onboarding journey."""
    eligible_preflight = {
        "status": "ELIGIBLE",
        "disk": {"expected_artifact_size": ARTIFACT_SIZE,
                 "total_required": REQUIRED_SPACE, "free_space": AVAILABLE_SPACE},
    }
    no_models = _base(
        inventory=ob.InventorySnapshot("KNOWN"),
        selection=ob.SelectionSnapshot("MISSING"),
        candidates=(_candidate(),),
    )
    proposed = _base(
        inventory=ob.InventorySnapshot("KNOWN"),
        selection=ob.SelectionSnapshot("MISSING"),
        candidates=(_candidate(),),
        operations=(ob.OperationSnapshot("ACQUISITION", "PROPOSED", CATALOG_ID),),
    )

    def acquisition_stage(state, status):
        snapshot = _base(
            inventory=ob.InventorySnapshot("KNOWN"),
            selection=ob.SelectionSnapshot("MISSING"),
            candidates=(_candidate(),),
            operations=(ob.OperationSnapshot("ACQUISITION", state, CATALOG_ID),),
        )
        record = {"state": state}
        if state == "DOWNLOADING":
            record["progress"] = {
                "bytes_transferred": None,
                "provider_completed_bytes": 3_221_225_472,
                "provider_total_bytes": 4_294_967_296,
                "current_blob_index": 2,
                "current_blob_completed_bytes": 1_073_741_824,
                "current_blob_total_bytes": 2_147_483_648,
            }
        if state == "FAILED":
            record["error"] = "FIXTURE_VERIFICATION_FAILED"
        return _fixture(
            snapshot, "Complete onboarding simulation", installed=False,
            preflight=eligible_preflight, acquisition=record,
            models_expanded=True, acquisition_status=status,
        )

    installed_candidate = _candidate(
        "NO_OP_ALREADY_INSTALLED", "INSTALLED_EXACT")
    installed = _base(
        inventory=ob.InventorySnapshot("KNOWN", (MODEL_ID,)),
        selection=ob.SelectionSnapshot("MISSING"),
        candidates=(installed_candidate,),
        operations=(ob.OperationSnapshot("ACQUISITION", "INSTALLED", CATALOG_ID),),
    )
    activating = _base(
        inventory=ob.InventorySnapshot("KNOWN", (MODEL_ID,)),
        selection=ob.SelectionSnapshot("MISSING"),
        candidates=(installed_candidate,),
        operations=(ob.OperationSnapshot("ACTIVATION", "ACTIVATING", MODEL_ID),),
    )
    ready = _base(
        inventory=ob.InventorySnapshot("KNOWN", (MODEL_ID,)),
        selection=ob.SelectionSnapshot(
            "VALID", MODEL_ID, "INSTALLED_EXACT", True),
        candidates=(installed_candidate,),
    )

    states = {
        "initial": _fixture(
            no_models, "Complete onboarding simulation", installed=False,
            preflight=eligible_preflight, models_expanded=True),
        "proposed": _fixture(
            proposed, "Complete onboarding simulation", installed=False,
            preflight=eligible_preflight, approval=_approval(),
            models_expanded=True, chat_expanded=True,
            acquisition_status="SIMULATION // AWAITING INSTALL APPROVAL"),
        "downloading": acquisition_stage(
            "DOWNLOADING", "SIMULATION // DOWNLOADING // PROVIDER PROGRESS"),
        "verifying": acquisition_stage(
            "VERIFYING", "SIMULATION // VERIFYING EXACT ARTIFACT"),
        "failed": acquisition_stage(
            "FAILED", "SIMULATION // VERIFICATION FAILED // NO RETRY STARTED"),
        "installed": _fixture(
            installed, "Complete onboarding simulation", installed=True,
            preflight={"status": "NO_OP_ALREADY_INSTALLED"},
            acquisition={"state": "INSTALLED"}, models_expanded=True,
            acquisition_status="SIMULATION // INSTALLED AND VERIFIED // INACTIVE"),
        "activating": _fixture(
            activating, "Complete onboarding simulation", installed=True,
            preflight={"status": "NO_OP_ALREADY_INSTALLED"},
            acquisition={"state": "INSTALLED"}, models_expanded=True,
            runtime_state="loading_model",
            acquisition_status="SIMULATION // INSTALLED // ACTIVATION IN PROGRESS"),
        "ready": _fixture(
            ready, "Complete onboarding simulation", installed=True,
            preflight={"status": "NO_OP_ALREADY_INSTALLED"},
            acquisition={"state": "INSTALLED"}, models_expanded=False,
            acquisition_status="SIMULATION // MODEL READY // NO INFERENCE YET"),
    }
    fixture = deepcopy(states["initial"])
    fixture.update({
        "label": "Complete onboarding simulation",
        "continuous_simulation": True,
        "simulation_states": states,
        "install_approval": _approval(),
        "canned_reply": (
            "CANNED FIXTURE REPLY // NO INFERENCE\n\n"
            "NOVA’s isolated onboarding simulation reached its ready state. "
            "This reply came from the fixture; no model generated it."
        ),
    })
    return fixture


def scenarios():
    runtime_missing = _base(
        prerequisites=ob.PrerequisiteSnapshot("UNAVAILABLE", "UNKNOWN", "UNKNOWN"),
        inventory=ob.InventorySnapshot("UNKNOWN"),
        selection=ob.SelectionSnapshot("MISSING"),
    )
    provider_unreachable = _base(
        prerequisites=ob.PrerequisiteSnapshot("AVAILABLE", "UNAVAILABLE", "PARTIAL"),
        inventory=ob.InventorySnapshot("UNKNOWN"),
        selection=ob.SelectionSnapshot("MISSING"),
    )
    no_models = _base(
        inventory=ob.InventorySnapshot("KNOWN"),
        selection=ob.SelectionSnapshot("MISSING"),
        candidates=(_candidate(),),
    )
    installed_no_selection = _base(
        selection=ob.SelectionSnapshot("MISSING"),
        candidates=(_candidate("NO_OP_ALREADY_INSTALLED", "INSTALLED_EXACT"),),
    )
    no_candidate = _base(
        inventory=ob.InventorySnapshot("KNOWN"),
        selection=ob.SelectionSnapshot("MISSING"),
        candidates=(_candidate("BLOCKED", blockers=("INSUFFICIENT_STORAGE",)),),
    )
    proposed = _base(
        inventory=ob.InventorySnapshot("KNOWN"),
        selection=ob.SelectionSnapshot("MISSING"),
        candidates=(_candidate(),),
        operations=(ob.OperationSnapshot("ACQUISITION", "PROPOSED", CATALOG_ID),),
    )
    failed = _base(
        inventory=ob.InventorySnapshot("KNOWN"),
        selection=ob.SelectionSnapshot("MISSING"),
        candidates=(_candidate(),),
        operations=(ob.OperationSnapshot("ACQUISITION", "FAILED", CATALOG_ID),),
    )
    verified = _base(
        selection=ob.SelectionSnapshot("MISSING"),
        candidates=(_candidate("NO_OP_ALREADY_INSTALLED", "INSTALLED_EXACT"),),
        operations=(ob.OperationSnapshot("ACQUISITION", "INSTALLED", CATALOG_ID),),
    )
    eligible_preflight = {
        "status": "ELIGIBLE",
        "disk": {"expected_artifact_size": ARTIFACT_SIZE,
                 "total_required": REQUIRED_SPACE, "free_space": AVAILABLE_SPACE},
    }
    progress_snapshot = _base(
        inventory=ob.InventorySnapshot("KNOWN"),
        selection=ob.SelectionSnapshot("MISSING"),
        candidates=(_candidate(),),
        operations=(ob.OperationSnapshot("ACQUISITION", "DOWNLOADING", CATALOG_ID),),
    )
    progress_unknown = {
        "state": "DOWNLOADING",
        "progress": {
            "bytes_transferred": None,
            "provider_completed_bytes": None,
            "provider_total_bytes": None,
            "current_blob_index": 1,
            "current_blob_completed_bytes": None,
            "current_blob_total_bytes": None,
        },
    }
    progress_transition = {
        "state": "DOWNLOADING",
        "progress": {
            "bytes_transferred": None,
            # Blob 1 was reported complete from cache/resume; blob 2 has just begun.
            "provider_completed_bytes": 1_073_741_824,
            "provider_total_bytes": 3_221_225_472,
            "current_blob_index": 2,
            "current_blob_completed_bytes": 0,
            "current_blob_total_bytes": 2_147_483_648,
        },
    }
    recovery_record = {
        "id": "fixture-recovery", "state": "RECOVERY_REQUIRED",
        "error": "provider_failure", "updated_at": 1_789_498_367,
        "failure_detail": {"provider_message": "Provider stopped before verification."},
        "historical_progress": {"observed_at": 1_789_498_340,
                                "progress": {"current_blob_index": 2,
                                             "current_blob_completed_bytes": 1_073_741_824,
                                             "current_blob_total_bytes": 4_294_967_296}},
        "recovery_resolution": None,
    }
    recovery_assessment = {
        "acquisition_id": "fixture-recovery", "assessment_id": "a" * 64,
        "artifact_state": "ABSENT", "artifact_detail": None,
        "provider_activity": "UNKNOWN", "resolution_id": None,
        "resolution_status": "AVAILABLE", "operator_attestation_required": True,
        "retry_allowed": False, "limitations": ["PROVIDER_ACTIVITY_NOT_MACHINE_VERIFIED"],
    }
    resolved_record = deepcopy(recovery_record)
    resolved_record["recovery_resolution"] = {
        "outcome": "RETRY_ALLOWED", "retry_allowed": True,
        "basis": "OPERATOR_ATTESTATION", "created_at": 1_789_623_565,
    }
    mismatch_assessment = deepcopy(recovery_assessment)
    mismatch_assessment.update({"artifact_state": "MISMATCH", "provider_activity": "UNKNOWN",
                                "resolution_status": "BLOCKED",
                                "operator_attestation_required": False})
    return {
        "complete-flow": _continuous_simulation(),
        "runtime-unreachable": _fixture(runtime_missing, "Runtime unreachable", online=False,
                                         models_expanded=False, snapshot_fresh=False),
        "provider-unreachable": _fixture(provider_unreachable, "Ollama unreachable",
                                          online=True, models_expanded=False),
        "no-models": _fixture(no_models, "No installed models", installed=False,
                               preflight=eligible_preflight),
        "installed-no-selection": _fixture(installed_no_selection, "Installed models, no selection",
                                             installed=True),
        "no-eligible-candidate": _fixture(
            no_candidate, "No eligible install candidate", installed=False,
            preflight={"status": "BLOCKED", "blockers": ["INSUFFICIENT_STORAGE"]}),
        "install-approval": _fixture(proposed, "Installation approval with storage",
                                      installed=False, preflight=eligible_preflight,
                                      approval=_approval(), chat_expanded=True),
        "install-failure": _fixture(failed, "Installation or verification failure",
                                     installed=False, preflight=eligible_preflight,
                                     acquisition={"state": "FAILED", "error": "FIXTURE_VERIFICATION_FAILED"}),
        "download-progress-unknown": _fixture(
            progress_snapshot, "Download progress without a blob total",
            installed=False, preflight=eligible_preflight,
            acquisition=progress_unknown, models_expanded=True),
        "download-progress-blob-transition": _fixture(
            progress_snapshot, "Per-blob progress after cached or resumed data",
            installed=False, preflight=eligible_preflight,
            acquisition=progress_transition, models_expanded=True,
            chat_expanded=True),
        "verified-awaiting-activation": _fixture(
            verified, "Verified installation awaiting activation", installed=True,
            preflight={"status": "NO_OP_ALREADY_INSTALLED"},
            acquisition={"state": "INSTALLED"}),
        "recovery-unresolved": dict(_fixture(
            no_models, "Interrupted install recovery review", installed=False,
            preflight={"status": "BLOCKED", "blockers": ["RECOVERY_REQUIRED"]},
            acquisition=recovery_record, models_expanded=True),
            recovery_assessment=recovery_assessment),
        "recovery-retry-allowed": _fixture(
            no_models, "Reviewed recovery awaiting fresh approval", installed=False,
            preflight=eligible_preflight, acquisition=resolved_record, models_expanded=True),
        "recovery-blocked-mismatch": dict(_fixture(
            no_models, "Recovery blocked by revision mismatch", installed=False,
            preflight={"status": "BLOCKED", "blockers": ["REVISION_MISMATCH"]},
            acquisition=recovery_record, models_expanded=True),
            recovery_assessment=mismatch_assessment),
    }


def prepare_config(target: Path, fixture: dict) -> Path:
    shell_root = Path("/usr/share/omarchy/shell")
    for module in ("Commons", "Ui"):
        source = shell_root / module
        if not (source / "qmldir").is_file():
            raise RuntimeError(f"Omarchy QML module unavailable: {source}")
        (target / module).symlink_to(source, target_is_directory=True)
    (target / "Panel.qml").symlink_to(ROOT / "Panel.qml")
    (target / "wave.frag.qsb").symlink_to(ROOT / "wave.frag.qsb")
    (target / "NovaMark.qml").symlink_to(ROOT / "NovaMark.qml")
    (target / "NovaClient.qml").symlink_to(ROOT / "NovaClient.qml")
    template = (ROOT / "tools/onboarding_preview_shell.qml").read_text()
    rendered = template.replace("@FIXTURE_JSON@", json.dumps(fixture, ensure_ascii=False))
    shell_file = target / "shell.qml"
    shell_file.write_text(rendered)
    return shell_file


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", nargs="?", choices=tuple(scenarios()))
    parser.add_argument("--list", action="store_true", help="list fixture scenarios")
    args = parser.parse_args(argv)
    available = scenarios()
    if args.list:
        for name in available:
            print(name)
        return 0
    if not args.scenario:
        parser.error("choose a scenario or use --list")
    if not shutil.which("quickshell"):
        parser.error("quickshell is not installed")
    with tempfile.TemporaryDirectory(prefix="nova-onboarding-preview-", dir="/tmp") as raw:
        config = Path(raw)
        prepare_config(config, available[args.scenario])
        if available[args.scenario].get("continuous_simulation"):
            print("NOVA continuous onboarding simulation:", args.scenario)
        else:
            print("NOVA onboarding acceptance preview:", args.scenario)
        print("Fixture data only; production endpoints and configuration are not used.")
        print("Press Ctrl+C in this terminal to close the preview.")
        try:
            return subprocess.run(["quickshell", "--path", str(config)], check=False).returncode
        except KeyboardInterrupt:
            return 130


if __name__ == "__main__":
    raise SystemExit(main())
