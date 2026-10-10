"""QWEN-17: catalog health report across models, fit and evidence files."""
from __future__ import annotations
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "catalog_report.py"
NOW = "2026-10-10T00:00:00Z"
NOW_STALE = "2026-10-10T00:00:00Z"
REVISION = "a" * 64


def write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2))


def model_record(catalog_id: str, revision: str = REVISION, size: int = 1000,
                 checked_at: str = "2026-10-09T00:00:00Z",
                 acquisition_size: int | None = None) -> dict:
    rec = {
        "catalog_id": catalog_id,
        "display_name": catalog_id.upper(),
        "source": {"revision_digest": revision},
        "properties": {"weight_size_bytes": size},
        "metadata": {"checked_at": checked_at},
    }
    if acquisition_size is not None:
        rec["acquisition"] = {"expected_size_bytes": acquisition_size}
    return rec


def fit_record(catalog_id: str, revision: str = REVISION, size: int = 1000) -> dict:
    return {
        "catalog_id": catalog_id,
        "revision": revision,
        "metadata": {"size_bytes": size},
    }


def evidence_record(catalog_id: str, revision: str = REVISION) -> dict:
    return {
        "evidence_id": f"ev-{catalog_id}",
        "models": [{"catalog_id": catalog_id, "revision": revision}],
    }


def make_catalog(dir_path: Path,
                 models: list[dict],
                 fit_records: list[dict] | None = None,
                 evidence_records: list[dict] | None = None) -> None:
    write_json(dir_path / "models.json",
               {"schema_version": 1, "catalog_revision": "test", "models": models})
    write_json(dir_path / "acquisition_fit.json",
               {"version": 1, "records": fit_records if fit_records is not None else []})
    write_json(dir_path / "evidence.json",
               {"schema_version": 1, "records": evidence_records if evidence_records is not None else []})


def run_tool(catalog_dir: Path, *extra: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-B", str(TOOL), "--catalog-dir", str(catalog_dir),
           "--now", NOW, *extra]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


class CatalogReportTests(unittest.TestCase):
    """Acceptance cases 1-8 from QWEN-17."""

    def _tmp(self) -> Path:
        d = Path(tempfile.mkdtemp(prefix="cat_report_"))
        return d

    def run_report(self, catalog_dir: Path, *extra: str) -> tuple[str, int, str]:
        proc = run_tool(catalog_dir, *extra)
        return proc.stdout, proc.stderr, proc.returncode

    # --- Case 1: consistent two-model catalog ---

    def test_consistent_two_models_exit_0(self):
        d = self._tmp()
        models = [
            model_record("model-a"),
            model_record("model-b"),
        ]
        fit = [fit_record("model-a"), fit_record("model-b")]
        ev = [evidence_record("model-a"), evidence_record("model-b")]
        make_catalog(d, models, fit, ev)
        stdout, stderr, rc = self.run_report(d)
        self.assertEqual(rc, 0, f"stdout={stdout!r} stderr={stderr!r}")
        lines = stdout.strip().split("\n")
        self.assertEqual(lines[-1], "2 models · 0 errors · 0 warnings · 0 info")

    # --- Case 2: each error on its own ---

    def test_error_duplicate_catalog_id(self):
        d = self._tmp()
        models = [
            model_record("model-a"),
            model_record("model-a"),  # duplicate
        ]
        fit = [fit_record("model-a")]
        ev = [evidence_record("model-a")]
        make_catalog(d, models, fit, ev)
        stdout, stderr, rc = self.run_report(d)
        self.assertEqual(rc, 1)
        self.assertIn("ERROR model-a: duplicate catalog_id", stdout)

    def test_error_no_fit_record(self):
        d = self._tmp()
        models = [model_record("model-a")]
        fit = []  # no fit record
        ev = [evidence_record("model-a")]
        make_catalog(d, models, fit, ev)
        stdout, stderr, rc = self.run_report(d)
        self.assertEqual(rc, 1)
        self.assertIn("ERROR model-a: no fit record", stdout)

    def test_error_fit_revision_differs(self):
        d = self._tmp()
        models = [model_record("model-a", revision=REVISION)]
        fit = [fit_record("model-a", revision="b" * 64)]  # different revision
        ev = [evidence_record("model-a")]
        make_catalog(d, models, fit, ev)
        stdout, stderr, rc = self.run_report(d)
        self.assertEqual(rc, 1)
        self.assertIn("ERROR model-a: fit revision differs", stdout)

    def test_error_fit_size_differs(self):
        d = self._tmp()
        models = [model_record("model-a", size=1000)]
        fit = [fit_record("model-a", size=2000)]  # different size
        ev = [evidence_record("model-a")]
        make_catalog(d, models, fit, ev)
        stdout, stderr, rc = self.run_report(d)
        self.assertEqual(rc, 1)
        self.assertIn("ERROR model-a: fit size differs", stdout)

    def test_error_acquisition_size_differs(self):
        d = self._tmp()
        models = [model_record("model-a", size=1000, acquisition_size=9999)]
        fit = [fit_record("model-a", size=1000)]
        ev = [evidence_record("model-a")]
        make_catalog(d, models, fit, ev)
        stdout, stderr, rc = self.run_report(d)
        self.assertEqual(rc, 1)
        self.assertIn("ERROR model-a: acquisition size differs", stdout)

    def test_error_evidence_revision_differs(self):
        d = self._tmp()
        models = [model_record("model-a", revision=REVISION)]
        fit = [fit_record("model-a", revision=REVISION)]
        ev = [evidence_record("model-a", revision="c" * 64)]  # different revision
        make_catalog(d, models, fit, ev)
        stdout, stderr, rc = self.run_report(d)
        self.assertEqual(rc, 1)
        self.assertIn("ERROR model-a: evidence revision differs", stdout)

    def test_error_fit_record_for_unknown_model(self):
        d = self._tmp()
        models = [model_record("model-a")]
        fit = [fit_record("model-a"), fit_record("model-ghost")]
        ev = [evidence_record("model-a")]
        make_catalog(d, models, fit, ev)
        stdout, stderr, rc = self.run_report(d)
        self.assertEqual(rc, 1)
        self.assertIn("ERROR model-ghost: fit record for unknown model", stdout)

    # --- Case 3: stale date ---

    def test_stale_checked_at_warns(self):
        d = self._tmp()
        # 31 days before 2026-10-10 → 2026-09-09
        models = [model_record("model-a", checked_at="2026-09-09T00:00:00Z")]
        fit = [fit_record("model-a")]
        ev = [evidence_record("model-a")]
        make_catalog(d, models, fit, ev)
        stdout, stderr, rc = self.run_report(d)
        self.assertEqual(rc, 0)
        self.assertIn("WARN model-a: checked 31 days ago (limit 30)", stdout)

    def test_stale_checked_at_strict_exit_1(self):
        d = self._tmp()
        models = [model_record("model-a", checked_at="2026-09-09T00:00:00Z")]
        fit = [fit_record("model-a")]
        ev = [evidence_record("model-a")]
        make_catalog(d, models, fit, ev)
        stdout, stderr, rc = self.run_report(d, "--strict")
        self.assertEqual(rc, 1)

    def test_exactly_max_age_no_warn(self):
        d = self._tmp()
        # Exactly 30 days before 2026-10-10 → 2026-09-10
        models = [model_record("model-a", checked_at="2026-09-10T00:00:00Z")]
        fit = [fit_record("model-a")]
        ev = [evidence_record("model-a")]
        make_catalog(d, models, fit, ev)
        stdout, stderr, rc = self.run_report(d)
        self.assertEqual(rc, 0)
        self.assertNotIn("WARN", stdout)
        lines = stdout.strip().split("\n")
        self.assertEqual(lines[-1], "1 models · 0 errors · 0 warnings · 0 info")

    # --- Case 4: missing checked_at ---

    def test_missing_checked_at_warns(self):
        d = self._tmp()
        rec = model_record("model-a")
        del rec["metadata"]["checked_at"]
        models = [rec]
        fit = [fit_record("model-a")]
        ev = [evidence_record("model-a")]
        make_catalog(d, models, fit, ev)
        stdout, stderr, rc = self.run_report(d)
        self.assertEqual(rc, 0)
        self.assertIn("WARN model-a: no valid checked_at", stdout)

    def test_unparseable_checked_at_warns(self):
        d = self._tmp()
        models = [model_record("model-a", checked_at="not-a-date")]
        fit = [fit_record("model-a")]
        ev = [evidence_record("model-a")]
        make_catalog(d, models, fit, ev)
        stdout, stderr, rc = self.run_report(d)
        self.assertEqual(rc, 0)
        self.assertIn("WARN model-a: no valid checked_at", stdout)

    # --- Case 5: no evidence ---

    def test_no_evidence_info(self):
        d = self._tmp()
        models = [model_record("model-a")]
        fit = [fit_record("model-a")]
        ev = []  # no evidence records
        make_catalog(d, models, fit, ev)
        stdout, stderr, rc = self.run_report(d)
        self.assertEqual(rc, 0)
        self.assertIn("INFO model-a: no evidence records", stdout)
        lines = stdout.strip().split("\n")
        self.assertEqual(lines[-1], "1 models · 0 errors · 0 warnings · 1 info")

    # --- Case 6: ordering ---

    def test_severity_and_id_ordering(self):
        d = self._tmp()
        # model-b: error (no fit), model-a: warn (stale), model-c: info (no evidence)
        models = [
            model_record("model-a", checked_at="2026-09-09T00:00:00Z"),
            model_record("model-b"),
            model_record("model-c"),
        ]
        fit = [fit_record("model-a"), fit_record("model-c")]
        ev = [evidence_record("model-a"), evidence_record("model-b")]
        make_catalog(d, models, fit, ev)
        stdout, stderr, rc = self.run_report(d)
        lines = [l for l in stdout.strip().split("\n") if l and not l.startswith("3 models")]
        # Expected order: ERROR model-b, WARN model-a, INFO model-c
        self.assertEqual(lines[0], "ERROR model-b: no fit record")
        self.assertEqual(lines[1], "WARN model-a: checked 31 days ago (limit 30)")
        self.assertEqual(lines[2], "INFO model-c: no evidence records")

    def test_ids_sorted_within_severity(self):
        d = self._tmp()
        # two models with no evidence → both INFO, sorted by id
        models = [
            model_record("model-z"),
            model_record("model-a"),
        ]
        fit = [fit_record("model-z"), fit_record("model-a")]
        ev = []
        make_catalog(d, models, fit, ev)
        stdout, stderr, rc = self.run_report(d)
        lines = [l for l in stdout.strip().split("\n") if l.startswith("INFO")]
        self.assertEqual(lines[0], "INFO model-a: no evidence records")
        self.assertEqual(lines[1], "INFO model-z: no evidence records")

    # --- Case 7: --json output ---

    def test_json_output(self):
        d = self._tmp()
        # one error (model-e), one warn (model-w), one info (model-i)
        models = [
            model_record("model-e"),
            model_record("model-w", checked_at="2026-09-09T00:00:00Z"),
            model_record("model-i"),
        ]
        fit = [fit_record("model-w"), fit_record("model-i")]
        ev = [evidence_record("model-e"), evidence_record("model-w")]
        make_catalog(d, models, fit, ev)
        stdout, stderr, rc = self.run_report(d, "--json")
        self.assertEqual(rc, 1)  # has an error
        data = json.loads(stdout)
        self.assertEqual(data["models"], 3)
        self.assertEqual(len(data["errors"]), 1)
        self.assertEqual(data["errors"][0]["catalog_id"], "model-e")
        self.assertEqual(data["errors"][0]["message"], "no fit record")
        self.assertEqual(len(data["warnings"]), 1)
        self.assertEqual(data["warnings"][0]["catalog_id"], "model-w")
        self.assertIn("31 days ago", data["warnings"][0]["message"])
        self.assertEqual(len(data["info"]), 1)
        self.assertEqual(data["info"][0]["catalog_id"], "model-i")
        self.assertEqual(data["info"][0]["message"], "no evidence records")

    # --- Case 8: missing file and invalid JSON ---

    def test_missing_models_file_exit_2(self):
        d = self._tmp()
        # Only write fit and evidence, skip models.json
        write_json(d / "acquisition_fit.json", {"version": 1, "records": []})
        write_json(d / "evidence.json", {"schema_version": 1, "records": []})
        stdout, stderr, rc = self.run_report(d)
        self.assertEqual(rc, 2)
        self.assertIn("ERROR catalog: cannot read models.json", stdout)

    def test_missing_fit_file_exit_2(self):
        d = self._tmp()
        write_json(d / "models.json",
                   {"schema_version": 1, "models": [model_record("m")]})
        write_json(d / "evidence.json", {"schema_version": 1, "records": []})
        stdout, stderr, rc = self.run_report(d)
        self.assertEqual(rc, 2)
        self.assertIn("ERROR catalog: cannot read acquisition_fit.json", stdout)

    def test_invalid_json_exit_2(self):
        d = self._tmp()
        write_json(d / "acquisition_fit.json", {"version": 1, "records": []})
        write_json(d / "evidence.json", {"schema_version": 1, "records": []})
        (d / "models.json").write_text("{invalid json")
        stdout, stderr, rc = self.run_report(d)
        self.assertEqual(rc, 2)
        self.assertIn("ERROR catalog: cannot read models.json", stdout)

    def test_invalid_json_names_the_broken_file(self):
        d = self._tmp()
        write_json(d / "models.json", {"models": []})
        write_json(d / "acquisition_fit.json", {"version": 1, "records": []})
        (d / "evidence.json").write_text("{invalid json")
        stdout, stderr, rc = self.run_report(d)
        self.assertEqual(rc, 2)
        self.assertEqual(stdout.strip(), "ERROR catalog: cannot read evidence.json")


if __name__ == "__main__":
    unittest.main()
