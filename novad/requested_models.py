"""Describe a model the user asked for by name or link, and remember it.

Backend step 1 of "ADD A MODEL". Takes a free-text reference (a bare model name
like ``gemma4:12b`` or an Ollama library URL), validates it strictly, runs the
network probe from ``registry_probe`` (with an injectable ``fetch`` so tests need
no network), turns the result into a catalog record plus an acquisition-fit
record, labels them as user-requested, and stores both in a small private store
under ``$XDG_STATE_HOME/nova/requested`` (default ``~/.local/state/nova/requested``).

This module never talks to the network itself: every HTTP call goes through the
``fetch`` callable (default ``registry_probe.http_get``), which tests replace with
a fake. The stored store is a private cache for NOVA to describe; it is not the
shipped catalog and does not grant any model a capability.
"""
from __future__ import annotations

import json
import os
import re
import struct
import tempfile
import urllib.error
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import catalog_drift
import catalog_repins
import registry_probe

MAX_MODELS = 20

_NAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}(:[a-z0-9][a-z0-9._-]{0,127})?")
_PREFIXES = (
    "https://ollama.com/library/",
    "http://ollama.com/library/",
    "ollama.com/library/",
    "https://registry.ollama.ai/library/",
    "registry.ollama.ai/library/",
)


def parse_reference(text) -> str | None:
    """Normalize a user reference to ``name:tag``; return None when it is not a valid Ollama name."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    if not text or len(text) > 200:
        return None
    rest = text
    for prefix in _PREFIXES:
        if text.startswith(prefix):
            rest = text[len(prefix):]
            break
    if not _NAME_RE.fullmatch(rest):
        return None
    name, _, tag = rest.partition(":")
    tag = tag or "latest"
    if "cloud" in tag:
        return None
    return f"{name}:{tag}"


def default_store() -> Path:
    """The private requested-models store directory, resolved when called (not at import)."""
    base = os.environ.get("XDG_STATE_HOME")
    if base:
        return Path(base).expanduser() / "nova" / "requested"
    return Path.home() / ".local" / "state" / "nova" / "requested"


def _resolve_store(store) -> Path:
    return Path(store) if store is not None else default_store()


def request_model(text, *, fetch=registry_probe.http_get, store=None, observed=None) -> dict:
    """Probe the referenced model, store it as a user-requested record, and report the outcome.

    ``fetch`` is the callable used for every registry read (inject a fake in tests).
    ``store`` is the directory to persist into (defaults to ``default_store()``).
    ``observed`` is the UTC ``YYYY-MM-DDTHH:MM:SSZ`` timestamp to record (defaults to now).
    """
    reference = parse_reference(text)
    if reference is None:
        return {"status": "invalid_reference"}
    name = reference
    try:
        found = registry_probe.probe(name, fetch=fetch)
    except urllib.error.HTTPError as error:
        if getattr(error, "code", None) == 404:
            return {"status": "not_found", "name": name}
        return {"status": "registry_unavailable", "name": name}
    except OSError:
        return {"status": "registry_unavailable", "name": name}
    except (ValueError, KeyError, struct.error, registry_probe.Truncated):
        return {"status": "unsupported_model", "name": name}

    if observed is None:
        observed = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    catalog_record, fit_record = registry_probe.records(found, [], None, observed)
    catalog_record["catalog_id"] = "requested-" + catalog_record["catalog_id"]
    catalog_record["acquisition"]["source"] = "ollama_registry_requested"
    catalog_record["metadata"]["provenance"]["method"] = "registry_probe+user_requested"
    fit_record["catalog_id"] = catalog_record["catalog_id"]

    base = _resolve_store(store)
    base.mkdir(parents=True, exist_ok=True)
    os.chmod(base, 0o700)
    models_doc, fit_doc = load_requested(base)
    models_doc["models"] = _upsert(models_doc.get("models", []), catalog_record)
    fit_doc["records"] = _upsert(fit_doc.get("records", []), fit_record)
    _write_atomic(base, "models.json", models_doc)
    _write_atomic(base, "acquisition_fit.json", fit_doc)

    return {
        "status": "added",
        "catalog_id": catalog_record["catalog_id"],
        "display_name": catalog_record["display_name"],
        "name": name,
    }


def refresh_requested(catalog_id, *, fetch=catalog_repins.probe_fetch,
                      digest_fetch=catalog_repins.manifest_fetch, store=None,
                      observed=None) -> dict:
    """Check a user-requested model against the registry and, if it re-published,
    store the new pin in place. Same result shapes as ``catalog_repins.refresh``:
    ``unchanged``, ``repinned``, ``unchecked`` (with reason), ``gone`` or
    ``changed_too_much`` (with reasons). Never raises for registry problems.
    """
    models_doc, fit_doc = load_requested(store)
    index = next((i for i, row in enumerate(models_doc.get("models", []))
                  if isinstance(row, dict) and row.get("catalog_id") == catalog_id), None)
    if index is None:
        return {"status": "unchecked", "reason": "not_requested"}
    record = models_doc["models"][index]
    fit_record = next((r for r in fit_doc.get("records", [])
                       if isinstance(r, dict) and r.get("catalog_id") == catalog_id), None)
    if fit_record is None:
        fit_record = {"catalog_id": catalog_id}
    acquisition = record.get("acquisition") or {}
    upstream = acquisition.get("artifact_id")
    pinned = acquisition.get("expected_revision")
    if not isinstance(upstream, str) or not isinstance(pinned, str):
        return {"status": "unchecked", "reason": "no_acquisition_pin"}
    check = catalog_drift.manifest_check(upstream, fetch=digest_fetch)
    if check["status"] == "gone":
        return {"status": "gone", "upstream_id": upstream}
    if check["status"] != "ok":
        return {"status": "unchecked", "reason": "registry_" + check["status"]}
    if check["manifest_sha256"] == pinned:
        return {"status": "unchanged"}
    try:
        found = registry_probe.probe(upstream, fetch=fetch)
    except urllib.error.HTTPError as error:
        if getattr(error, "code", None) == 404:
            return {"status": "gone", "upstream_id": upstream}
        return {"status": "unchecked", "reason": "registry_unreachable"}
    except OSError:
        return {"status": "unchecked", "reason": "registry_unreachable"}
    except (ValueError, KeyError, TypeError, struct.error, registry_probe.Truncated):
        return {"status": "changed_too_much", "upstream_id": upstream,
                "reasons": ["unreadable_model"]}
    if observed is None:
        observed = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    catalog_record, fit_new = registry_probe.records(found, [], None, observed)
    catalog_record["catalog_id"] = catalog_id
    catalog_record["acquisition"]["source"] = "ollama_registry_requested"
    catalog_record["metadata"]["provenance"]["method"] = "registry_probe+user_requested"
    fit_new["catalog_id"] = catalog_id
    reasons = catalog_repins.guard(record, catalog_record)
    if reasons:
        return {"status": "changed_too_much", "upstream_id": upstream, "reasons": reasons,
                "pinned_size": acquisition.get("expected_size_bytes"),
                "live_size": catalog_record["properties"].get("weight_size_bytes")}
    base = _resolve_store(store)
    base.mkdir(parents=True, exist_ok=True)
    os.chmod(base, 0o700)
    models_doc, fit_doc = load_requested(base)
    models_doc["models"][index] = catalog_record
    fit_index = next((i for i, row in enumerate(fit_doc.get("records", []))
                      if isinstance(row, dict) and row.get("catalog_id") == catalog_id), None)
    if fit_index is None:
        fit_doc["records"].append(fit_new)
    else:
        fit_doc["records"][fit_index] = fit_new
    _write_atomic(base, "models.json", models_doc)
    _write_atomic(base, "acquisition_fit.json", fit_doc)
    return {"status": "repinned", "upstream_id": upstream, "previous_revision": pinned,
            "revision": catalog_record["acquisition"]["expected_revision"],
            "pinned_size": acquisition.get("expected_size_bytes"),
            "live_size": catalog_record["acquisition"].get("expected_size_bytes")}


def remove_requested(catalog_id, store=None) -> dict:
    """Remove a user-requested model from both stored documents by catalog_id.

    ``catalog_id`` must fully match ``requested-[a-z0-9][a-z0-9._-]{0,127}``;
    otherwise return ``{"status": "invalid_id"}`` without touching the store.
    If no stored model has that id, return ``{"status": "not_found", "catalog_id": ...}``.
    Otherwise remove the record from both ``models.json`` and ``acquisition_fit.json``
    and return ``{"status": "removed", "catalog_id": ...}``.
    Never touches Ollama: removing from the list is not uninstalling.
    """
    if not isinstance(catalog_id, str) or not re.fullmatch(r"requested-[a-z0-9][a-z0-9._-]{0,127}", catalog_id):
        return {"status": "invalid_id"}
    base = _resolve_store(store)
    models_doc, fit_doc = load_requested(base)
    if not any(m.get("catalog_id") == catalog_id for m in models_doc.get("models", [])):
        return {"status": "not_found", "catalog_id": catalog_id}
    models_doc["models"] = [m for m in models_doc.get("models", []) if m.get("catalog_id") != catalog_id]
    fit_doc["records"] = [r for r in fit_doc.get("records", []) if r.get("catalog_id") != catalog_id]
    _write_atomic(base, "models.json", models_doc)
    _write_atomic(base, "acquisition_fit.json", fit_doc)
    return {"status": "removed", "catalog_id": catalog_id}


def load_requested(store=None) -> tuple[dict, dict]:
    """Return the stored (models doc, fit doc); empty same-shape docs when missing or unreadable."""
    base = _resolve_store(store)
    models = _read_doc(base / "models.json",
                       {"schema_version": 1, "catalog_revision": "requested", "models": []})
    fit = _read_doc(base / "acquisition_fit.json",
                    {"version": 1, "source": "local_show_or_registry_gguf_probe", "records": []})
    return models, fit


def _upsert(rows, record) -> list:
    """Replace any row with the same catalog_id, append the new one, keep the most recent MAX_MODELS."""
    rows = [row for row in rows if row.get("catalog_id") != record.get("catalog_id")]
    rows.append(record)
    return rows[-MAX_MODELS:]


def _write_atomic(base: Path, name: str, doc: dict) -> None:
    """Write the document to a temp file in the same directory, then atomically replace the target."""
    fd = tempfile.NamedTemporaryFile("w", dir=base, delete=False, prefix=".tmp-requested-")
    tmp = Path(fd.name)
    try:
        with fd:
            json.dump(doc, fd, indent=2, sort_keys=True, ensure_ascii=False)
            fd.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, base / name)
    finally:
        if tmp.exists():
            tmp.unlink()


def _read_doc(path: Path, empty: dict) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return deepcopy(empty)
    return value if isinstance(value, dict) else deepcopy(empty)
