#!/usr/bin/env python3
"""List which catalog revision pins have drifted against the Ollama registry.

Reads ``catalog/models.json`` (or ``--catalog-dir DIR``) and, for every model
with an Ollama upstream id, fetches the live registry manifest through
``novad.catalog_drift``. ``fetch=None`` means the real ``registry_probe.http_get``;
tests inject a fake. Verdicts per model: changed, gone, invalid, unreachable,
same; the summary line and exit code reflect the counts (1 = drift detected,
3 = registry unreachable for some pins, 0 = all same, 2 = unreadable catalog).

    tools/catalog_drift_report.py                # text, one line per model
    tools/catalog_drift_report.py --json         # {"models": N, "results": [...]}
    tools/catalog_drift_report.py --catalog-dir DIR
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "novad") not in sys.path:
    sys.path.insert(0, str(ROOT / "novad"))
from catalog_drift import drift_report  # noqa: E402
from registry_probe import http_get  # noqa: E402

VERDICT_ORDER = {"changed": 0, "gone": 1, "invalid": 2, "unreachable": 3, "same": 4}


def _pct(value) -> str:
    return f"{value:+.1f}%"


def _line(row: dict) -> str:
    cid, verdict = row["catalog_id"], row["verdict"]
    if verdict == "changed":
        if isinstance(row["pinned_size"], int) and isinstance(row["live_size"], int) and row["size_change_pct"] is not None:
            return f"CHANGED {cid}: {row['pinned_size']} → {row['live_size']} bytes ({_pct(row['size_change_pct'])})"
        return f"CHANGED {cid}: size unknown"
    if verdict == "gone":
        return f"GONE {cid}: {row['upstream_id']} not in the registry"
    if verdict == "invalid":
        reason = "unexpected manifest" if row["upstream_id"] is not None else "no upstream id"
        return f"INVALID {cid}: {reason}"
    if verdict == "unreachable":
        return f"UNREACHABLE {cid}: {row.get('reason') or 'network unavailable'}"
    return f"SAME {cid}"


def main(argv: list[str] | None = None, fetch=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--catalog-dir", default=str(ROOT / "catalog"))
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    if fetch is None:
        fetch = http_get
    try:
        document = json.loads((Path(args.catalog_dir) / "models.json").read_text())
        models = document["models"]
        if not isinstance(models, list):
            raise ValueError("models is not a list")
    except Exception:
        print("ERROR catalog: cannot read models.json")
        return 2
    rows = drift_report(models, fetch=fetch)
    rows.sort(key=lambda row: (VERDICT_ORDER[row["verdict"]], str(row["catalog_id"] or "")))
    counts = {verdict: sum(1 for row in rows if row["verdict"] == verdict) for verdict in VERDICT_ORDER}
    summary = (f"{len(rows)} models · {counts['changed']} changed · {counts['gone']} gone · "
               f"{counts['invalid']} invalid · {counts['unreachable']} unreachable · {counts['same']} same")
    if args.as_json:
        print(json.dumps({"models": len(rows), "results": rows}))
    else:
        for row in rows:
            print(_line(row))
        print(summary)
    if counts["changed"] or counts["gone"] or counts["invalid"]:
        return 1
    if counts["unreachable"]:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
