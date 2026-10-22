"""Describe an Ollama registry model without downloading it.

The offline, network-free core of the catalog probe: reads the public registry
manifest (exact identity pin: manifest sha256 and total size), the small
template/license/config blobs, and a ranged read of the GGUF header (architecture,
layers, attention, native context, parameter count). ``probe`` returns a found
dictionary; ``records`` turns that into a catalog record plus an acquisition-fit
record. The CLI (argument parsing and catalog merge) lives in tools/catalog_probe.py.
"""
from __future__ import annotations

import hashlib
import json
import re
import struct
import time
import urllib.request

import local_socket

REGISTRY = "https://registry.ollama.ai"
MANIFEST_ACCEPT = "application/vnd.docker.distribution.manifest.v2+json"
TAGS = {"general", "coding", "reasoning", "fast", "vision"}
# GGUF general.file_type (llama.cpp LLAMA_FTYPE) names for common quantizations.
FILE_TYPES = {0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1", 10: "Q2_K",
              11: "Q3_K_S", 12: "Q3_K_M", 13: "Q3_K_L", 14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S",
              17: "Q5_K_M", 18: "Q6_K", 19: "IQ2_XXS", 20: "IQ2_XS", 24: "IQ1_S", 25: "IQ4_NL",
              26: "IQ3_S", 27: "IQ3_M", 28: "IQ2_S", 29: "IQ2_M", 30: "IQ4_XS", 31: "IQ1_M", 32: "BF16"}
# Newer models use a built-in renderer/parser instead of a template. Only parsers
# whose Ollama-declared capabilities were checked locally (`ollama show` on an
# installed model of the same parser) are mapped; others stay unknown (None).
PARSER_CAPABILITIES = {"qwen3.5": {"tools": True, "thinking": True}}  # verified: qwen3.8:27b, 2026-10-04
SCALAR = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?", 10: "Q", 11: "q", 12: "d"}


# Registry blobs redirect to a CDN: follow redirects only to https and without reading the
# redirect's body, so ``limit`` bounds every read (marketplace review 2026-10-22).
OPENER = urllib.request.build_opener(local_socket.BoundedRedirectHandler(https_only=True))


def http_get(url: str, headers: dict[str, str] | None = None, limit: int = 1 << 20, timeout: int = 60) -> bytes:
    """Up to ``limit`` bytes of ``url``. ``timeout`` bounds the whole body too, not only each
    socket read: a server sending a byte now and then can't hold a request open for hours."""
    request = urllib.request.Request(url, headers={"User-Agent": "nova-catalog-probe", **(headers or {})})
    deadline = time.monotonic() + timeout
    with OPENER.open(request, timeout=timeout) as response:
        parts, size = [], 0
        while size < limit:
            if time.monotonic() > deadline: raise TimeoutError(f"{url}: no whole answer within {timeout} s")
            part = response.read1(min(1 << 16, limit - size))
            if not part: break
            parts.append(part); size += len(part)
        return b"".join(parts)


class Truncated(Exception):
    pass


def parse_gguf(data: bytes) -> tuple[dict, int]:
    """Return (metadata, parameter_count) from a GGUF prefix; Truncated if too short."""
    offset = 0

    def take(fmt: str):
        nonlocal offset
        size = struct.calcsize("<" + fmt)
        if offset + size > len(data): raise Truncated
        value = struct.unpack_from("<" + fmt, data, offset)[0]
        offset += size
        return value

    def text() -> str:
        nonlocal offset
        length = take("Q")
        if offset + length > len(data): raise Truncated
        value = data[offset:offset + length].decode("utf-8", "replace")
        offset += length
        return value

    def value(kind: int):
        nonlocal offset
        if kind == 8: return text()
        if kind == 9:
            inner, count = take("I"), take("Q")
            if inner == 8:
                for _ in range(count): text()
                return None  # string arrays (vocabularies) are not needed
            size = struct.calcsize("<" + SCALAR[inner])
            if offset + count * size > len(data): raise Truncated
            values = (list(struct.unpack_from(f"<{count}{SCALAR[inner]}", data, offset))
                      if count <= 4096 and inner not in (6, 12) else None)  # keep small per-layer int lists
            offset += count * size
            return values
        return take(SCALAR[kind])

    if data[:4] != b"GGUF": raise ValueError("not a GGUF model blob")
    offset = 4
    version, tensors, pairs = take("I"), take("Q"), take("Q")
    if version < 2: raise ValueError("unsupported GGUF version")
    metadata = {}
    for _ in range(pairs):
        key = text()
        metadata[key] = value(take("I"))
    parameters = 0
    for _ in range(tensors):
        text()
        dims = [take("Q") for _ in range(take("I"))]
        take("I"); take("Q")  # tensor type, data offset
        count = 1
        for dim in dims: count *= dim
        parameters += count
    return metadata, parameters


def attention_shape(metadata: dict, arch: str | None) -> dict:
    """Attention fields NOVA's fit uses, derived only from explicit header metadata."""
    get = lambda key: metadata.get(f"{arch}.{key}")
    layers, heads_kv = get("block_count"), get("attention.head_count_kv")
    key, value = get("attention.key_length"), get("attention.value_length")
    heads, width = get("attention.head_count"), get("embedding_length")
    if key is None and value is None and isinstance(heads, int) and isinstance(width, int) and heads and width % heads == 0:
        key = value = width // heads  # standard attention: head width = embedding / heads
    shape = {"layers": layers, "kv_heads": heads_kv if isinstance(heads_kv, int) else None,
             "key_length": key, "value_length": value}
    interval = get("full_attention_interval")
    if isinstance(interval, int) and interval > 0 and isinstance(layers, int):
        shape["full_attention_layers"] = layers // interval  # hybrid: every interval-th layer
        if isinstance(heads_kv, list):
            shape["kv_heads"] = max(heads_kv) or None
    pattern, window = get("attention.sliding_window_pattern"), get("attention.sliding_window")
    if isinstance(pattern, list) and isinstance(heads_kv, list) and len(pattern) == len(heads_kv) == layers and isinstance(window, int):
        full = [i for i, sliding in enumerate(pattern) if not sliding]
        swa_key, swa_value = get("attention.key_length_swa") or key, get("attention.value_length_swa") or value
        if full and isinstance(swa_key, int) and isinstance(swa_value, int):
            shape["full_attention_layers"] = len(full)
            shape["kv_heads"] = max(heads_kv[i] for i in full)
            shape["window_cache_elements"] = sum(heads_kv[i] * (swa_key + swa_value)
                                                 for i, sliding in enumerate(pattern) if sliding) * window
    return shape


def probe(name: str, fetch=http_get) -> dict:
    model, _, tag = name.partition(":")
    tag = tag or "latest"
    repo = model if "/" in model else "library/" + model
    raw = fetch(f"{REGISTRY}/v2/{repo}/manifests/{tag}", {"Accept": MANIFEST_ACCEPT})
    manifest = json.loads(raw)
    layers = {layer["mediaType"].rsplit(".", 1)[-1]: layer for layer in manifest["layers"]}
    if "model" not in layers: raise ValueError(f"{name}: no model layer")
    blob = lambda digest, limit=1 << 20: fetch(f"{REGISTRY}/v2/{repo}/blobs/{digest}", None, limit)
    template = blob(layers["template"]["digest"]).decode("utf-8", "replace") if "template" in layers else ""
    config = json.loads(blob(manifest["config"]["digest"], 65536)) if manifest["config"].get("digest") else {}
    parser = config.get("parser") if isinstance(config.get("parser"), str) else None
    declared = PARSER_CAPABILITIES.get(parser, {})
    license_text = blob(layers["license"]["digest"], 4096).decode("utf-8", "replace") if "license" in layers else ""
    header, size = None, 8 << 20
    while header is None:
        try:
            data = fetch(f"{REGISTRY}/v2/{repo}/blobs/{layers['model']['digest']}", {"Range": f"bytes=0-{size - 1}"}, size)
            header = parse_gguf(data)
        except Truncated:
            if size >= 128 << 20: raise ValueError(f"{name}: GGUF header larger than 128 MiB")
            size *= 4
    metadata, parameters = header
    arch = metadata.get("general.architecture")
    first_license_line = next((line.strip() for line in license_text.splitlines() if line.strip()), None)
    return {
        "name": f"{model}:{tag}", "tag": tag, "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "total_size": manifest["config"]["size"] + sum(layer["size"] for layer in manifest["layers"]),
        "architecture": arch, "parameter_count": parameters or None,
        "context_length": metadata.get(f"{arch}.context_length"),
        "quantization": (config.get("file_type") if isinstance(config.get("file_type"), str)
                         else FILE_TYPES.get(metadata.get("general.file_type"))),
        "requires_ollama": config.get("requires") if isinstance(config.get("requires"), str) else None,
        "parser": parser,
        "attention": attention_shape(metadata, arch),
        # Same declarations Ollama derives: tools/thinking from the template, vision from a projector.
        "capabilities": {"chat": True if (template or config.get("renderer")) else None,
                         "tools": True if (".Tools" in template or declared.get("tools")) else None,
                         "thinking": True if (re.search(r"\.Think|<think>", template) or declared.get("thinking")) else None,
                         "vision": True if "projector" in layers else None},
        "license_line": first_license_line[:120] if first_license_line else None,
        "basename": metadata.get("general.basename"), "size_label": metadata.get("general.size_label"),
    }


def records(found: dict, tags: list[str], display: str | None, observed: str,
            refs: list[str] | None = None) -> tuple[dict, dict]:
    name, digest = found["name"], found["manifest_sha256"]
    quant = (found["quantization"] or "unknown").lower()
    base = name.lower() if quant.replace("_", "") in name.lower().replace("_", "") else f"{name}-{quant}".lower()
    catalog_id = re.sub(r"[^a-z0-9.]+", "-", base).strip("-")
    notes = ("Registry probe: manifest identity plus GGUF header metadata read without downloading. "
             "Capabilities are template/parser/projector declarations; unknown stays null; fit is not quality."
             + (f" Requires Ollama >= {found['requires_ollama']}." if found.get("requires_ollama") else "")
             + (f" Parser: {found['parser']}." if found.get("parser") else ""))
    caps = found["capabilities"]
    minimum = found.get("requires_ollama")
    minimum = minimum if isinstance(minimum, str) and re.fullmatch(r"\d+\.\d+\.\d+", minimum) else None
    acquisition = {"provider": "ollama", "artifact_id": name, "exact_tag": found["tag"],
                   "expected_revision": digest, "expected_size_bytes": found["total_size"],
                   "source": "ollama_registry",
                   "provenance": {"method": "registry_manifest_probe", "observed_at": observed,
                                  "notes": "Manifest sha256 and summed layer sizes from the public registry; an identity pin, not a download measurement."}}
    if minimum:
        acquisition["minimum_provider_version"] = minimum
    catalog = {
        "catalog_id": catalog_id,
        "display_name": display or f"{found['basename'] or name} {found['size_label'] or ''} ({found['quantization'] or '?'})".replace("  ", " "),
        "family": found["architecture"], "release_label": None,
        "source": {"kind": "upstream_metadata", "provider": "ollama", "upstream_id": name,
                   "reference": f"registry.ollama.ai/library/{name}", "revision_digest": digest},
        "properties": {"architecture": found["architecture"], "parameter_count": found["parameter_count"],
                       "quantizations": [found["quantization"]] if found["quantization"] else None,
                       "weight_size_bytes": found["total_size"], "native_context": found["context_length"]},
        "capabilities": {**caps, "coding": None, "instruction_following": None},
        "task_tags": tags,
        "acquisition": acquisition,
        "metadata": {"release_date": None, "license": found["license_line"], "checked_at": observed,
                     "provenance": {"method": "registry_probe" + ("+curated_task_tags" if refs else ""),
                                    "references": [f"registry.ollama.ai/v2/library/{name.split(':')[0]}/manifests/{found['tag']}", *(refs or [])],
                                    "notes": notes + (" Task tags are descriptive, curated from the cited guides; not NOVA benchmarks." if refs else "")}},
    }
    fit = {"catalog_id": catalog_id, "revision": digest, "provider": "ollama", "artifact_id": name,
           "metadata": {"size_bytes": found["total_size"], "context_length": found["context_length"],
                        "parameter_size": f"{found['parameter_count'] / 1e9:.1f}B" if found["parameter_count"] else None,
                        "family": found["architecture"], "format": "gguf", "quantization": found["quantization"],
                        "architecture": found["architecture"], "attention": found["attention"],
                        "fit_metadata_source": "registry/gguf_header"},
           "capabilities": {**{k: v for k, v in caps.items()}, "streaming": True if caps["chat"] else None}}
    return catalog, fit
