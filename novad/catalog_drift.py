"""Check catalog revision pins against the live Ollama registry.

Offline-testable core: ``manifest_check`` fetches one registry manifest through
an injectable ``fetch`` (default ``registry_probe.http_get``) and reports the
live manifest sha256, total size and model layer digest, or a stable failure
status (gone / unreachable / invalid). ``compare`` lines one catalog model up
against that check; ``drift_report`` runs the comparison for a whole catalog.
No network access is attempted here; the network only happens inside ``fetch``.
"""
from __future__ import annotations

import hashlib
import json
import urllib.error

from registry_probe import MANIFEST_ACCEPT, REGISTRY, http_get


def _manifest_url(upstream_id: str) -> str:
    model, _, tag = upstream_id.partition(":")
    tag = tag or "latest"
    repo = model if "/" in model else "library/" + model
    return f"{REGISTRY}/v2/{repo}/manifests/{tag}"


def _invalid(upstream_id: str, manifest_sha256: str | None) -> dict:
    return {"upstream_id": upstream_id, "status": "invalid", "manifest_sha256": manifest_sha256,
            "total_size": None, "model_layer_digest": None, "reason": "unexpected manifest"}


def manifest_check(upstream_id: str, fetch=http_get) -> dict:
    try:
        raw = fetch(_manifest_url(upstream_id), {"Accept": MANIFEST_ACCEPT})
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return {"upstream_id": upstream_id, "status": "gone", "manifest_sha256": None,
                    "total_size": None, "model_layer_digest": None, "reason": "not in the registry"}
        return {"upstream_id": upstream_id, "status": "unreachable", "manifest_sha256": None,
                "total_size": None, "model_layer_digest": None, "reason": f"registry answered {error.code}"}
    except (urllib.error.URLError, TimeoutError, OSError):
        return {"upstream_id": upstream_id, "status": "unreachable", "manifest_sha256": None,
                "total_size": None, "model_layer_digest": None, "reason": "network unavailable"}
    digest = hashlib.sha256(raw).hexdigest()
    try:
        manifest = json.loads(raw)
        if not isinstance(manifest, dict): raise ValueError("not a JSON object")
        config_size = manifest["config"]["size"]
        if isinstance(config_size, bool) or not isinstance(config_size, int): raise ValueError("config size")
        layers = manifest["layers"]
        if not isinstance(layers, list): raise ValueError("layers")
        total = config_size
        model_layer = None
        for layer in layers:
            size = layer["size"]
            if isinstance(size, bool) or not isinstance(size, int): raise ValueError("layer size")
            total += size
            if str(layer.get("mediaType", "")).endswith(".model"): model_layer = layer["digest"]
        if model_layer is None: raise ValueError("no model layer")
    except (ValueError, KeyError, TypeError):
        return _invalid(upstream_id, digest)
    return {"upstream_id": upstream_id, "status": "ok", "manifest_sha256": digest,
            "total_size": total, "model_layer_digest": model_layer, "reason": None}


def upstream_id_of(model: dict) -> str | None:
    acquisition = model.get("acquisition")
    if isinstance(acquisition, dict) and isinstance(acquisition.get("artifact_id"), str):
        return acquisition["artifact_id"]
    source = model.get("source")
    if isinstance(source, dict) and isinstance(source.get("upstream_id"), str):
        return source["upstream_id"]
    return None


def _pinned(model: dict, acquisition_key: str, fallback_path: tuple) -> int | str | None:
    acquisition = model.get("acquisition")
    if isinstance(acquisition, dict) and acquisition.get(acquisition_key) is not None:
        return acquisition[acquisition_key]
    node = model
    for key in fallback_path:
        if not isinstance(node, dict): return None
        node = node.get(key)
    return node if isinstance(node, (int, str)) and not isinstance(node, bool) else None


def compare(model: dict, check: dict) -> dict:
    status = check["status"]
    live_digest = check["manifest_sha256"] if status == "ok" else None
    live_size = check["total_size"] if status == "ok" else None
    pinned_digest = _pinned(model, "expected_revision", ("source", "revision_digest"))
    pinned_size = _pinned(model, "expected_size_bytes", ("properties", "weight_size_bytes"))
    if status == "ok" and pinned_digest is not None and pinned_digest == live_digest:
        verdict = "same"
    elif status == "ok":
        verdict = "changed"
    else:
        verdict = status
    pct = None
    if verdict == "changed" and isinstance(pinned_size, int) and not isinstance(pinned_size, bool) \
            and isinstance(live_size, int) and not isinstance(live_size, bool) and pinned_size > 0:
        pct = round((live_size - pinned_size) * 100 / pinned_size, 1)
    return {"catalog_id": model.get("catalog_id"), "upstream_id": check["upstream_id"], "verdict": verdict,
            "pinned_digest": pinned_digest, "live_digest": live_digest,
            "pinned_size": pinned_size, "live_size": live_size, "size_change_pct": pct,
            "reason": check.get("reason") if verdict == "unreachable" else None}


def drift_report(models: list[dict], fetch=http_get) -> list[dict]:
    report = []
    for model in models:
        upstream_id = upstream_id_of(model)
        if upstream_id is None:
            report.append({"catalog_id": model.get("catalog_id"), "upstream_id": None, "verdict": "invalid",
                           "pinned_digest": None, "live_digest": None, "pinned_size": None,
                           "live_size": None, "size_change_pct": None, "reason": None})
            continue
        report.append(compare(model, manifest_check(upstream_id, fetch=fetch)))
    return report
