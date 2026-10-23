"""Delete an installed model from this computer, behind an approval (QWEN-11).

The panel asks for a proposal; novad checks the model can go (installed, not NOVA's
selected model, not loaded in GPU memory, no coding CLI on it) and returns a `del_`
approval id. `POST /approvals/<id>/approve` re-checks everything (including the
installed digest) right before Ollama deletes it, then confirms it is gone.
The model's coding-CLI builds (0.15 tailored builds: same files, its own working context)
go with it, first, so nothing is left holding the files (0.16).
Nothing here talks to Ollama directly: the server passes `recheck` and `delete`.
"""
from __future__ import annotations

import secrets
from threading import Lock
import time

from providers import ProviderOperationFailed, ProviderTimeout, ProviderUnavailable

TTL_SECONDS = 120


def _plain(name):
    """Ollama's default tag: "x" and "x:latest" are the same model."""
    return name[:-len(":latest")] if isinstance(name, str) and name.endswith(":latest") else name


def _same(a, b):
    return isinstance(a, str) and isinstance(b, str) and _plain(a) == _plain(b)


def _entry(model, inventory):
    return next((item for item in inventory or [] if _same(item.get("id"), model)), None)


def blockers_for(model, *, inventory, selected_model, resident, cli_models, builds=()):
    """`builds`: the model's coding-CLI builds, deleted with it, so they are checked too."""
    entry = _entry(model, inventory)
    if entry is None:
        return ["NOT_INSTALLED"], None
    blockers = []
    going = [model, *builds]
    if any(_same(name, selected_model) for name in going):
        blockers.append("SELECTED_MODEL")
    if resident is None:
        blockers.append("MEMORY_STATE_UNKNOWN")
    elif any(_same(name, loaded) for name in going for loaded in resident):
        blockers.append("LOADED_IN_MEMORY")
    if cli_models is None:
        blockers.append("CLI_STATE_UNKNOWN")
    elif any(_same(model, name) for name in cli_models):
        blockers.append("CLI_SESSION_RUNNING")
    return blockers, entry


# Files smaller than this (licences, templates, settings) are often identical across
# unrelated models; only shared model files are worth naming.
SHARED_FILE_MIN_BYTES = 1 << 20


def sharing(model, records, together=()):
    """What deleting `model` frees, and which other models keep its main files.

    `records` are local inventory records with `layers` ({blob digest: size}). Ollama
    deletes a model's name and only the files no other model uses, so a model built on
    another (same weights, different settings) frees almost nothing. `together` are models
    deleted with it (its coding-CLI builds): their files count as going too. Returns None
    when the model's files are unknown.
    """
    going = lambda r: any(_same(r.get('id'), name) for name in (model, *together))
    mine = next((r.get('layers') for r in records or [] if _same(r.get('id'), model)), None)
    if not isinstance(mine, dict):
        return None
    mine = dict(mine)
    for record in records:
        if going(record) and isinstance(record.get('layers'), dict): mine.update(record['layers'])
    others = [(r['id'], r['layers']) for r in records if not going(r) and isinstance(r.get('layers'), dict)]
    if any(not isinstance(r.get('layers'), dict) for r in records if not _same(r.get('id'), model)):
        return None  # an unreadable model might share files: don't promise a number
    used_elsewhere = {digest for _, layers in others for digest in layers}
    shared_with = sorted(_plain(name) for name, layers in others
                         if sum(size for digest, size in mine.items() if digest in layers) >= SHARED_FILE_MIN_BYTES)
    return {"frees_bytes": sum(size for digest, size in mine.items() if digest not in used_elsewhere),
            "shared_bytes": sum(size for digest, size in mine.items() if digest in used_elsewhere),
            "shared_with": shared_with}


class RemovalStore:
    def __init__(self):
        self.lock, self.items = Lock(), {}

    def propose(self, model, *, inventory, selected_model, resident, cli_models, builds=(), now=None, shares=None):
        now = time.time() if now is None else now
        blockers, entry = blockers_for(model, inventory=inventory, selected_model=selected_model,
                                       resident=resident, cli_models=cli_models, builds=builds)
        if blockers:
            return {"status": "blocked", "model": model, "blockers": blockers}
        size = (entry.get("metadata") or {}).get("size_bytes")
        proposal = {"status": "proposed", "id": "del_" + secrets.token_urlsafe(24), "model": entry["id"],
                    "size_bytes": size if isinstance(size, int) else None,
                    "digest": entry.get("digest") if isinstance(entry.get("digest"), str) else None,
                    "frees_bytes": shares["frees_bytes"] if shares else None,
                    "shared_with": list(shares["shared_with"]) if shares else [],
                    "also_deletes": list(builds), "expires_at": now + TTL_SECONDS}
        with self.lock:
            self.items[proposal["id"]] = {**proposal, "created": now, "decision": None}
        return dict(proposal)

    def decide(self, identifier, decision, *, recheck, delete, disk_free=None, forget=None, now=None):
        """`forget(name)` runs after each deletion, to drop NOVA's own records of a build."""
        now = time.time() if now is None else now
        with self.lock:
            item = self.items.get(identifier)
            if item is None:
                return 404, {"error": "unknown_approval"}
            if item["decision"] is not None:
                return 409, {"error": "approval_already_used"}
            if now - item["created"] > TTL_SECONDS:
                item["decision"] = "expired"
                return 410, {"error": "expired_approval"}
            item["decision"] = decision  # single use, claimed before any slow work
        if decision != "approved":
            return 202, {"status": "denied"}
        model = item["model"]
        current = recheck(model)
        blockers, entry = blockers_for(model, **current)
        if not blockers and entry.get("digest") != item["digest"]:
            blockers = ["DIGEST_CHANGED"]
        if blockers:
            return 409, {"status": "blocked", "blockers": blockers}
        # Models can share files (an alias built on a base model), so the space actually
        # freed is measured on the storage disk, not assumed from the model's size.
        before = _measure(disk_free)
        # Builds first: if one fails, the model and everything else stay as they were; if the
        # model then fails, what's left (the model without its CLI) is still a normal state.
        builds = list(current.get("builds") or ())
        for name in [*builds, model]:
            try:
                delete(name)
            except (ProviderOperationFailed, ProviderTimeout, ProviderUnavailable):
                return 502, {"status": "failed", "error": "delete_failed"}
            if forget: forget(name)
        inventory = recheck(model)["inventory"]
        if any(_entry(name, inventory) is not None for name in [*builds, model]):
            return 502, {"status": "failed", "error": "still_installed"}
        after = _measure(disk_free)
        freed = max(0, after - before) if before is not None and after is not None else None
        result = {"status": "deleted", "model": model, "freed_bytes": freed, "size_bytes": item["size_bytes"],
                  "shared_with": item.get("shared_with", [])}
        if builds: result["also_deleted"] = builds
        return 200, result


def _measure(disk_free):
    try:
        value = disk_free() if disk_free else None
    except OSError:
        return None
    return value if isinstance(value, int) else None
