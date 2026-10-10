"""Provider-neutral model control-plane types and the local Ollama adapter."""
from __future__ import annotations
import json
from threading import Lock
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from typing import Any, Callable

class ProviderUnavailable(Exception): pass
class ProviderOperationFailed(Exception): pass
class ProviderTimeout(ProviderOperationFailed): pass
# Per-reply generation caps (num_predict). Thinking counts against the cap, so
# thinking models need far more room: Qwen 27B thought past 4096 tokens and wrote
# no answer (2026-10-05). The overall deadline still bounds a looping model.
MAX_REPLY_TOKENS = 8192
MAX_THINKING_REPLY_TOKENS = 32768
REPLY_DEADLINE_SECONDS = 600
SMALL_MODEL_BILLIONS = 3.0
# First Ollama whose /api/show reports model capabilities ("Return model
# capabilities from the show endpoint", 0.6.5 release notes); novad decides
# think/tools from them.
MIN_OLLAMA_VERSION = (0, 6, 5)


def parse_version(text: Any) -> tuple[int, int, int] | None:
    """"0.33.3" or "0.6.5-rc1" -> (major, minor, patch); anything else -> None."""
    if not isinstance(text, str): return None
    parts = text.strip().split("-", 1)[0].split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts): return None
    return int(parts[0]), int(parts[1]), int(parts[2])


def parameter_billions(size: Any) -> float | None:
    """Ollama's parameter_size ("1.2B", "500M") in billions, or None."""
    if not isinstance(size, str) or len(size) < 2: return None
    scale = {"B": 1.0, "M": 0.001, "K": 0.000001}.get(size[-1].upper())
    try: return float(size[:-1]) * scale if scale else None
    except ValueError: return None


# Told to a model that chats without NOVA's tools, so it says so instead of
# pretending: in walkthrough run 4 the 1B model "opened" a terminal it couldn't.
NO_TOOLS_NOTE = ("In this chat you have no tools: you cannot open apps, move windows, change settings "
                 "or do anything else on this computer. If the user asks for such an action, say plainly "
                 "that this model can't do that in NOVA and suggest how they can do it themselves.")


def without_tools(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Messages with NO_TOOLS_NOTE added to the system instructions (a new list)."""
    if messages and messages[0].get("role") == "system" and isinstance(messages[0].get("content"), str):
        return [{**messages[0], "content": messages[0]["content"] + "\n\n" + NO_TOOLS_NOTE}, *messages[1:]]
    return [{"role": "system", "content": NO_TOOLS_NOTE}, *messages]


class ProviderFeatureRejected(ProviderUnavailable):
    """Ollama refused an optional chat feature (think/tools) for this model."""

class OllamaProvider:
    id = "ollama"
    type = "local"
    def __init__(self, base_url: str, keep_alive: str) -> None:
        self.base_url, self.keep_alive = base_url, keep_alive
        self._fit_cache = {}
        self._fit_lock = Lock()
        self._chat_capabilities: dict[str, frozenset[str]] = {}
    def _json(self, path: str, timeout: int = 3) -> tuple[int, dict[str, Any]]:
        try:
            with urllib.request.urlopen(self.base_url + path, timeout=timeout) as response:
                value = json.loads(response.read(2_000_000).decode())
                return response.status, value if isinstance(value, dict) else {}
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError): return 0, {}
    def _post_json(self, path: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
        request=urllib.request.Request(self.base_url+path,data=json.dumps(payload,separators=(",",":")).encode(),method="POST",headers={"Content-Type":"application/json"})
        try:
            with urllib.request.urlopen(request,timeout=timeout) as response:
                value=json.loads(response.read(2_000_000).decode())
                if response.status != HTTPStatus.OK or not isinstance(value,dict): raise ProviderOperationFailed("Ollama returned an invalid response.")
                return value
        except ProviderOperationFailed: raise
        except urllib.error.HTTPError as error:
            try: detail=json.loads(error.read(65_536).decode()).get("error")
            except (ValueError,UnicodeDecodeError,AttributeError): detail=None
            raise ProviderOperationFailed(detail if isinstance(detail,str) else "Ollama rejected the model operation.") from error
        except TimeoutError as error: raise ProviderTimeout("Ollama model operation timed out.") from error
        except urllib.error.URLError as error:
            if isinstance(error.reason,TimeoutError): raise ProviderTimeout("Ollama model operation timed out.") from error
            raise ProviderOperationFailed("Ollama model operation failed.") from error
        except (ValueError,UnicodeDecodeError,OSError) as error: raise ProviderOperationFailed("Ollama model operation failed.") from error
    def available(self) -> bool: return self._json("/api/tags", 2)[0] == HTTPStatus.OK
    def version(self) -> tuple[int, int, int] | None:
        status, payload = self._json("/api/version", 2)
        return parse_version(payload.get("version")) if status == HTTPStatus.OK else None
    def descriptor(self) -> dict[str, Any]: return {"id":self.id, "type":self.type, "status":"online" if self.available() else "offline"}
    def models(self) -> list[dict[str, Any]]:
        status, payload = self._json("/api/tags")
        if status != HTTPStatus.OK: raise ProviderUnavailable("Ollama is unavailable.")
        result=[]
        for item in payload.get("models", []):
            if not isinstance(item, dict) or not isinstance(item.get("name"), str): continue
            details=item.get("details") if isinstance(item.get("details"), dict) else {}
            name=item["name"]; display=name.replace("qwen3.5", "Qwen 3.5").replace("qwen3", "Qwen 3").replace(":", " ")
            result.append({"id":name,"provider":self.id,"display_name":display,"local":True,"installed":True,"available":True,"digest":item.get("digest") if isinstance(item.get("digest"),str) else None,"capabilities":{"chat":True,"streaming":True,"tools":None,"thinking":None,"vision":None},"metadata":{"size_bytes":item.get("size") if isinstance(item.get("size"),int) else None,"context_length":details.get("context_length") if isinstance(details.get("context_length"),int) else None,"parameter_size":details.get("parameter_size"),"family":details.get("family"),"format":details.get("format"),"quantization":details.get("quantization_level")}})
        return result
    def fit_models(self) -> list[dict[str, Any]]:
        """Enrich installed inventory only; show never loads/downloads a model.

        Cache details for 60 seconds. Never serve cached inventory while offline.
        Raw templates, licenses, tokenizer arrays and local paths are not exposed.
        """
        models = self.models()
        with self._fit_lock:
            self._fit_cache = {key: value for key, value in self._fit_cache.items()
                               if key in {m['id'] for m in models}}
            deadline = time.monotonic() + 8
            for model in models:
                cached = self._fit_cache.get(model['id'])
                if cached and time.monotonic()-cached[0] < 60 and cached[1] == model['metadata']['size_bytes']:
                    details = cached[2]
                else:
                    try: details = self._post_json('/api/show', {'model': model['id']}, 2) if time.monotonic() < deadline else {}
                    except ProviderOperationFailed: details = {}
                    self._fit_cache[model['id']] = (time.monotonic(), model['metadata']['size_bytes'], details)
                info = details.get('model_info') if isinstance(details.get('model_info'), dict) else {}
                architecture = info.get('general.architecture')
                metadata = model['metadata']
                metadata['architecture'] = architecture if isinstance(architecture, str) else None
                if isinstance(architecture, str):
                    context = info.get(architecture + '.context_length')
                    if isinstance(context, int) and not isinstance(context, bool) and context > 0:
                        metadata['context_length'] = context
                    metadata['attention'] = {name: info.get(architecture + '.' + key) for name, key in
                        [('layers','block_count'), ('kv_heads','attention.head_count_kv'),
                         ('key_length','attention.key_length'), ('value_length','attention.value_length')]}
                capabilities = details.get('capabilities')
                if isinstance(capabilities, list):
                    model['capabilities'].update(chat='completion' in capabilities,
                        tools='tools' in capabilities, thinking='thinking' in capabilities, vision='vision' in capabilities)
                if details.get('remote_model') or details.get('remote_host'):
                    model['local'] = False
                metadata['fit_metadata_source'] = 'ollama/api/show' if info else 'ollama/api/tags; details unavailable'
        return [model for model in models if model.get('local') and model.get('installed')]

    def running_models(self, timeout: int = 3) -> list[dict[str, Any]]:
        status,payload=self._json("/api/ps",timeout)
        if status != HTTPStatus.OK: raise ProviderUnavailable("Ollama runtime state is unavailable.")
        return [item for item in payload.get("models",[]) if isinstance(item,dict)]
    def is_loaded(self, model: str, timeout: int = 3) -> bool:
        return any(item.get("name") == model or item.get("model") == model for item in self.running_models(timeout))
    def preload(self, model: str, timeout: int) -> dict[str, Any]:
        # Ollama's canonical empty-chat preload initializes the model without
        # creating a user-visible conversation turn.
        result=self._post_json("/api/chat",{"model":model,"messages":[],"stream":False,"keep_alive":self.keep_alive},timeout)
        returned=result.get("model")
        if result.get("done") is not True or returned != model: raise ProviderOperationFailed("Ollama did not confirm the requested model identity.")
        if not self.is_loaded(model,10): raise ProviderOperationFailed("Ollama did not report the requested model as loaded.")
        return {"model":returned,"done":True,"done_reason":result.get("done_reason"),"load_duration":result.get("load_duration")}
    def unload(self, model: str, timeout: int = 30) -> None:
        result=self._post_json("/api/chat",{"model":model,"messages":[],"stream":False,"keep_alive":0},timeout)
        if result.get("done") is not True or result.get("model") != model: raise ProviderOperationFailed("Ollama did not confirm model unload.")
        deadline=time.monotonic()+10
        while time.monotonic() < deadline:
            if not self.is_loaded(model,3): return
            time.sleep(0.2)
        raise ProviderOperationFailed("Ollama still reports the model as loaded.")
    def delete(self, model: str, timeout: int = 30) -> None:
        """Ollama DELETE /api/delete; anything but 200 is a failure (QWEN-11)."""
        request=urllib.request.Request(self.base_url+"/api/delete",data=json.dumps({"model":model},separators=(",",":")).encode(),method="DELETE",headers={"Content-Type":"application/json"})
        try:
            with urllib.request.urlopen(request,timeout=timeout) as response:
                if response.status != HTTPStatus.OK: raise ProviderOperationFailed("Ollama did not delete the model.")
        except ProviderOperationFailed: raise
        except (urllib.error.URLError,urllib.error.HTTPError,TimeoutError,OSError) as error:
            raise ProviderOperationFailed("Ollama did not delete the model.") from error
    def chat_capabilities(self, model: str) -> frozenset[str] | None:
        """Ollama's declared capabilities for a model (cached), or None when unknown."""
        if model in self._chat_capabilities: return self._chat_capabilities[model]
        try: details = self._post_json("/api/show", {"model": model}, 5)
        except (ProviderOperationFailed, ProviderTimeout, ProviderUnavailable): return None
        declared = details.get("capabilities")
        if not isinstance(declared, list): return None
        names = {c for c in declared if isinstance(c, str)}
        # Tiny models (under 3B parameters) can't use NOVA's tool schemas: given them,
        # llama3.2:1b wrote an endless pseudo tool call that Ollama buffered, so no
        # reply ever reached the panel (walkthrough run 2). Chat without tools.
        size = details.get("details", {}).get("parameter_size") if isinstance(details.get("details"), dict) else None
        if parameter_billions(size) is not None and parameter_billions(size) < SMALL_MODEL_BILLIONS: names.discard("tools")
        self._chat_capabilities[model] = frozenset(names)
        return self._chat_capabilities[model]

    def stream_chat(self, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], on_thinking: Callable[[], None] | None = None, on_content: Callable[[str], None] | None = None) -> dict[str, Any]:
        # Only request features the model declares: Ollama rejects `think` or
        # `tools` for models without them (e.g. llama3.2:1b has no thinking).
        declared = self.chat_capabilities(model)
        think = declared is not None and "thinking" in declared
        use_tools = bool(tools) and (declared is None or "tools" in declared)
        try:
            return self._stream_chat_once(model, messages if use_tools or not tools else without_tools(messages),
                                          tools if use_tools else [], think, on_thinking, on_content)
        except ProviderFeatureRejected:
            # Declarations can be stale or missing: retry once with neither optional feature.
            return self._stream_chat_once(model, without_tools(messages) if tools else messages, [], False, on_thinking, on_content)

    def _stream_chat_once(self, model, messages, tools, think, on_thinking, on_content) -> dict[str, Any]:
        # Cap one reply's length: with context shifting a looping model never stops.
        cap = MAX_THINKING_REPLY_TOKENS if think else MAX_REPLY_TOKENS
        payload={"model":model,"stream":True,"keep_alive":self.keep_alive,"messages":messages,"options":{"num_predict":cap}}
        if think: payload["think"]=True
        if tools: payload["tools"]=tools
        request=urllib.request.Request(self.base_url+"/api/chat",data=json.dumps(payload,separators=(",",":")).encode(),method="POST",headers={"Content-Type":"application/json"})
        content_parts: list[str]=[]; tool_calls: list[dict[str,Any]]=[]
        try:
            deadline=time.monotonic()+REPLY_DEADLINE_SECONDS
            with urllib.request.urlopen(request,timeout=180) as response:
                for raw in response:
                    if time.monotonic() > deadline: raise ProviderUnavailable("The model took too long to reply.")
                    if not raw.strip(): continue
                    try: chunk=json.loads(raw.decode())
                    except (UnicodeDecodeError,ValueError): continue
                    message=chunk.get("message") if isinstance(chunk.get("message"),dict) else {}
                    if isinstance(message.get("thinking"),str) and message["thinking"] and on_thinking: on_thinking()
                    if isinstance(message.get("tool_calls"),list): tool_calls.extend(call for call in message["tool_calls"] if isinstance(call,dict))
                    if isinstance(message.get("content"),str) and message["content"]:
                        content_parts.append(message["content"])
                        if on_content: on_content(message["content"])
                    if chunk.get("done") is True:
                        if chunk.get("done_reason") == "length" and not content_parts and not tool_calls:
                            raise ProviderUnavailable("The model used its whole reply budget"
                                                      + (" thinking" if think else "") + " before writing an answer.")
                        return {"role":"assistant","content":"".join(content_parts),"tool_calls":tool_calls}
        except urllib.error.HTTPError as error:
            try: detail=json.loads(error.read(65_536).decode()).get("error")
            except (ValueError,UnicodeDecodeError,AttributeError): detail=None
            if error.code == 400 and isinstance(detail,str) and "does not support" in detail and (think or tools):
                raise ProviderFeatureRejected(detail) from error
            raise ProviderUnavailable("Ollama stream failed" + (": " + detail if isinstance(detail,str) else ".")) from error
        except (urllib.error.URLError,TimeoutError,OSError) as error: raise ProviderUnavailable("Ollama stream failed.") from error
        raise ProviderUnavailable("Ollama stream ended unexpectedly.")
