#!/usr/bin/env python3
"""Read-only catalog health report across models.json, acquisition_fit.json and evidence.json.

Compares revisions, sizes and checked_at dates across the three files and reports
disagreements as ERROR/WARN/INFO lines sorted by severity then catalog_id.

    tools/catalog_report.py --now 2026-10-10T00:00:00Z --strict --json
"""
from __future__ import annotations
import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG = ROOT / "catalog"


def parse_iso8601(value: str) -> datetime | None:
    """Parse an ISO 8601 timestamp; return a UTC-aware datetime or None."""
    if not isinstance(value, str):
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


class CatalogReadError(Exception):
    def __init__(self, name: str):
        super().__init__(name)
        self.name = name


def load_json(path: Path) -> dict:
    try:
        with path.open() as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise CatalogReadError(path.name) from exc
    if not isinstance(data, dict):
        raise CatalogReadError(path.name)
    return data


def report(catalog_dir: Path, now_iso: str, max_age_days: int) -> dict:
    """Build the report dict: {models, errors, warnings, info}."""
    now_dt = parse_iso8601(now_iso)
    if now_dt is None:
        raise ValueError(f"cannot parse --now value: {now_iso!r}")

    models_doc = load_json(catalog_dir / "models.json")
    fit_doc = load_json(catalog_dir / "acquisition_fit.json")
    evidence_doc = load_json(catalog_dir / "evidence.json")

    models = models_doc.get("models", [])
    fit_records = {r.get("catalog_id", ""): r for r in fit_doc.get("records", []) if isinstance(r, dict)}
    evidence_by_model: dict[str, list[dict]] = {}
    for ev_rec in evidence_doc.get("records", []):
        for m in ev_rec.get("models", []):
            cid = m.get("catalog_id")
            if cid:
                evidence_by_model.setdefault(cid, []).append(m)

    errors: list[dict] = []
    warnings: list[dict] = []
    info: list[dict] = []

    def add_error(cid: str, msg: str) -> None:
        errors.append({"catalog_id": cid, "message": msg})

    def add_warn(cid: str, msg: str) -> None:
        warnings.append({"catalog_id": cid, "message": msg})

    def add_info(cid: str, msg: str) -> None:
        info.append({"catalog_id": cid, "message": msg})

    seen_ids: set[str] = set()
    for m in models:
        cid = m.get("catalog_id", "")
        if cid in seen_ids:
            add_error(cid, "duplicate catalog_id")
            continue
        seen_ids.add(cid)

        rev = (m.get("source") or {}).get("revision_digest")
        size = (m.get("properties") or {}).get("weight_size_bytes")
        acq = m.get("acquisition")
        acq_size = (acq or {}).get("expected_size_bytes") if acq else None
        checked_raw = (m.get("metadata") or {}).get("checked_at")

        # Error 2: no fit record
        fr = fit_records.get(cid)
        if fr is None:
            add_error(cid, "no fit record")
        else:
            # Error 3: fit revision differs
            if fr.get("revision") != rev:
                add_error(cid, "fit revision differs")
            # Error 4: fit size differs
            if (fr.get("metadata") or {}).get("size_bytes") != size:
                add_error(cid, "fit size differs")

        # Error 5: acquisition size differs (only if acquisition object present)
        if acq is not None and acq_size is not None and acq_size != size:
            add_error(cid, "acquisition size differs")

        # Error 6: evidence revision differs
        for ev_m in evidence_by_model.get(cid, []):
            if ev_m.get("revision") != rev:
                add_error(cid, "evidence revision differs")
                break

        # Warning: stale checked_at or missing
        checked_dt = parse_iso8601(checked_raw)
        if checked_dt is None:
            add_warn(cid, "no valid checked_at")
        else:
            days = math.floor((now_dt - checked_dt).total_seconds() / 86400)
            if days > max_age_days:
                add_warn(cid, f"checked {days} days ago (limit {max_age_days})")

        # Info: no evidence records
        if cid not in evidence_by_model:
            add_info(cid, "no evidence records")

    # Error 7: fit record for unknown model
    for cid in fit_records:
        if cid not in seen_ids:
            add_error(cid, "fit record for unknown model")

    # Sort each group by catalog_id
    errors.sort(key=lambda x: x["catalog_id"])
    warnings.sort(key=lambda x: x["catalog_id"])
    info.sort(key=lambda x: x["catalog_id"])

    return {
        "models": len(models),
        "errors": errors,
        "warnings": warnings,
        "info": info,
    }


def format_text(rep: dict) -> str:
    lines: list[str] = []
    for cid, msg in [(e["catalog_id"], e["message"]) for e in rep["errors"]]:
        lines.append(f"ERROR {cid}: {msg}")
    for cid, msg in [(w["catalog_id"], w["message"]) for w in rep["warnings"]]:
        lines.append(f"WARN {cid}: {msg}")
    for cid, msg in [(i["catalog_id"], i["message"]) for i in rep["info"]]:
        lines.append(f"INFO {cid}: {msg}")
    summary = f"{rep['models']} models · {len(rep['errors'])} errors · {len(rep['warnings'])} warnings · {len(rep['info'])} info"
    lines.append(summary)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report catalog health across models, fit and evidence files.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--catalog-dir", type=Path, default=None,
                        help="Directory containing models.json, acquisition_fit.json, evidence.json")
    parser.add_argument("--now", type=str, default=None,
                        help="ISO 8601 timestamp to compare checked_at against (default: current UTC)")
    parser.add_argument("--max-age-days", type=int, default=30,
                        help="Warn when checked_at is older than this many days (default 30)")
    parser.add_argument("--json", action="store_true",
                        help="Print JSON output instead of text lines")
    parser.add_argument("--strict", action="store_true",
                        help="Exit 1 if any warning (in addition to any error)")
    args = parser.parse_args()

    catalog_dir = args.catalog_dir or DEFAULT_CATALOG
    now_iso = args.now or datetime.now(timezone.utc).isoformat()

    try:
        rep = report(catalog_dir, now_iso, args.max_age_days)
    except CatalogReadError as exc:
        print(f"ERROR catalog: cannot read {exc.name}", file=sys.stdout)
        return 2

    if args.json:
        print(json.dumps(rep))
    else:
        print(format_text(rep))

    if rep["errors"]:
        return 1
    if args.strict and rep["warnings"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
