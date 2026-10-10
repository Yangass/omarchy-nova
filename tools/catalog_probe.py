#!/usr/bin/env python3
"""Describe an Ollama registry model for NOVA's catalog without downloading it.

Reads the public registry manifest (exact identity pin: manifest sha256 and total
size), the small template/license/config blobs, and a ranged read of the GGUF
header (architecture, layers, attention, native context, parameter count). The
output is a catalog record plus an acquisition-fit record, both labelled as a
registry probe: declared metadata, not a hardware test or a quality claim.

The network-free probe core (http_get, parse_gguf, attention_shape, probe,
records and its constants) lives in novad/registry_probe.py; this file is only
the command-line wrapper that parses arguments and merges the result into the
shipped catalog files.

    tools/catalog_probe.py qwen3:14b --tags general,reasoning            # print
    tools/catalog_probe.py qwen3:14b --tags general,reasoning --write    # merge
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "novad") not in sys.path:
    sys.path.insert(0, str(ROOT / "novad"))
from registry_probe import (  # noqa: E402
    REGISTRY, MANIFEST_ACCEPT, TAGS, FILE_TYPES, PARSER_CAPABILITIES, SCALAR,
    http_get, Truncated, parse_gguf, attention_shape, probe, records,
)

CATALOG = ROOT / "catalog/models.json"
FIT = ROOT / "catalog/acquisition_fit.json"


def merge(path: Path, key: str, record: dict, list_key: str, **envelope) -> None:
    document = json.loads(path.read_text())
    document.update(envelope)
    rows = [row for row in document[list_key] if row[key] != record[key]]
    rows.append(record)
    document[list_key] = rows
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", help="Ollama model, e.g. qwen3:14b")
    parser.add_argument("--tags", default="", help="comma-separated task tags: " + ",".join(sorted(TAGS)))
    parser.add_argument("--display-name")
    parser.add_argument("--ref", action="append", default=[], help="citation backing the task tags (repeatable)")
    parser.add_argument("--write", action="store_true", help="merge into catalog/models.json and acquisition_fit.json")
    args = parser.parse_args()
    tags = [t for t in args.tags.split(",") if t]
    if set(tags) - TAGS: parser.error("unknown tag(s): " + ",".join(sorted(set(tags) - TAGS)))
    observed = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    catalog, fit = records(probe(args.name), tags, args.display_name, observed, args.ref)
    if not args.write:
        print(json.dumps({"catalog": catalog, "fit": fit}, indent=2, ensure_ascii=False))
        return 0
    merge(CATALOG, "catalog_id", catalog, "models")
    merge(FIT, "catalog_id", fit, "records", source="local_show_or_registry_gguf_probe")
    print(f"merged {catalog['catalog_id']} ({catalog['properties']['weight_size_bytes'] / 1e9:.1f} GB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
