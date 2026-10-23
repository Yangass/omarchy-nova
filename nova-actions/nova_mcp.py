#!/usr/bin/env python3
"""NOVA's tool for the coding CLI (0.15): a Model Context Protocol server over stdin/stdout.

OpenCode starts it (NOVA's launch settings name it). Through it the CLI can read its own model
settings and ask NOVA to change its working context. It cannot change anything itself: novad
shows the request in NOVA's panel with the fit (and a warning when it is tight, doesn't fit or
is past what the model was trained for), and the user decides. It talks only to this user's
novad over NOVA's private socket (local_socket.py), and only to two fixed endpoints.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any
from urllib.parse import quote

import local_socket

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
BUILD = os.environ.get("NOVA_CLI_BUILD", "")
TOOLS = [
    {"name": "nova_my_settings",
     "description": "Your own model settings from NOVA: which local model you run on, your working context (how much text you can keep in mind at once), what fits the user's graphics card, and the contexts NOVA offers.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "nova_change_my_working_context",
     "description": ("Ask NOVA to change your own working context. The user approves or declines in NOVA's panel, "
                     "and NOVA warns them when it is tight, doesn't fit the graphics card (much slower) or is past what "
                     "the model was trained for. Waits up to two minutes for their answer. Use it only when the user asks "
                     "for more or less context, or a task clearly needs it; say why in `reason`."),
     "inputSchema": {"type": "object", "properties": {
         "context_tokens": {"type": "integer", "minimum": 4096, "maximum": 262144, "description": "The working context you want, in tokens (for example 65536 for 64K)."},
         "reason": {"type": "string", "maxLength": 200, "description": "One sentence for the user: why."}},
         "required": ["context_tokens", "reason"], "additionalProperties": False}},
]


def novad(method: str, path: str, value: dict[str, Any] | None = None, timeout: float = 10) -> tuple[int, dict[str, Any]]:
    body = json.dumps(value, separators=(",", ":")).encode() if value is not None else None
    headers = {"Content-Type": "application/json"} if body else {}
    try:
        status, raw = local_socket.call("novad", method, path, body=body, headers=headers, timeout=timeout)
        decoded = json.loads(raw[:200_000].decode())
        return status, decoded if isinstance(decoded, dict) else {}
    except (OSError, ValueError, UnicodeDecodeError) as error:
        return 0, {"error": "novad_unavailable", "detail": str(error)[:120]}


def k(tokens: Any) -> str:
    return f"{tokens // 1024}K" if isinstance(tokens, int) else "unknown"


def my_settings() -> tuple[str, bool]:
    status, state = novad("GET", "/cli/self?build=" + quote(BUILD, safe=""))
    if status != 200: return f"NOVA could not read your settings ({state.get('error', 'no answer')}).", True
    verdict = state.get("verdict") or {}
    lines = [f"Model: {state.get('model')} (NOVA build {state.get('build')}) on {state.get('gpu') or 'this computer'}.",
             f"Working context: {k(state.get('context'))}. Recommended: {k(state.get('recommended_context'))}. Trained for: {k(state.get('native_context'))}.",
             f"Fit now: {verdict.get('text', 'unknown')}",
             "NOVA offers: " + ", ".join(k(step) for step in state.get("offered_contexts", [])) + "."]
    return "\n".join(lines), False


def change_context(arguments: dict[str, Any]) -> tuple[str, bool]:
    tokens, reason = arguments.get("context_tokens"), arguments.get("reason", "")
    if type(tokens) is not int or not 4096 <= tokens <= 262144: return "context_tokens must be a whole number from 4096 to 262144.", True
    if not isinstance(reason, str): reason = ""
    status, result = novad("POST", "/cli/self/proposals", {"build": BUILD, "context": tokens, "reason": reason[:200]}, timeout=150)
    if status == 429: return "NOVA only takes one such request every 20 seconds. Wait, then ask again only if the user still wants it.", True
    if status != 200: return f"NOVA could not ask the user ({result.get('error', 'no answer')}).", True
    outcome = result.get("status")
    if outcome == "changed":
        return (f"The user approved: your working context is now {k(result.get('context'))} (asked for {k(result.get('requested_context'))}). "
                "The model reloads with it on the next message."), False
    if outcome == "unchanged": return f"Your working context is already {k(result.get('context'))}.", False
    words = {"denied": "The user declined", "expired": "The user didn't answer within two minutes", "replaced": "A newer request replaced this one"}
    if outcome in words: return f"{words[outcome]}; your working context stays {k(result.get('previous_context'))}.", False
    return f"The change failed: {result.get('error', 'unknown error')}. Your working context stays {k(result.get('previous_context'))}.", True


def answer(message: dict[str, Any]) -> dict[str, Any] | None:
    method, identifier = message.get("method"), message.get("id")
    if identifier is None: return None  # a notification (initialized, cancelled)
    if method == "initialize":
        asked = (message.get("params") or {}).get("protocolVersion")
        result = {"protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0], "capabilities": {"tools": {}},
                  "serverInfo": {"name": "nova", "version": "0.15"},
                  "instructions": "NOVA runs your local model. Changes to your own settings need the user's approval in NOVA's panel."}
    elif method == "ping": result = {}
    elif method == "tools/list": result = {"tools": TOOLS}
    elif method == "tools/call":
        params = message.get("params") or {}
        name, arguments = params.get("name"), params.get("arguments") or {}
        if name == "nova_my_settings": text, error = my_settings()
        elif name == "nova_change_my_working_context" and isinstance(arguments, dict): text, error = change_context(arguments)
        else: return {"jsonrpc": "2.0", "id": identifier, "error": {"code": -32602, "message": "Unknown tool."}}
        result = {"content": [{"type": "text", "text": text}], "isError": error}
    else: return {"jsonrpc": "2.0", "id": identifier, "error": {"code": -32601, "message": "Method not found."}}
    return {"jsonrpc": "2.0", "id": identifier, "result": result}


def main() -> None:
    for line in sys.stdin:
        if len(line) > 100_000: continue
        try: message = json.loads(line)
        except ValueError:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error."}}) + "\n"); sys.stdout.flush(); continue
        reply = answer(message) if isinstance(message, dict) else None
        if reply is not None:
            sys.stdout.write(json.dumps(reply, separators=(",", ":")) + "\n"); sys.stdout.flush()


if __name__ == "__main__":
    main()
