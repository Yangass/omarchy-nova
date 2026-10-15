"""Fixed loopback Ollama pull adapter. No commands, URLs or paths from callers."""
from dataclasses import asdict
import http.client
import json
import re
from threading import Lock, Thread
import time

from acquisition_lifecycle import frozen_descriptor, LifecycleError
from catalog_observability import OLLAMA_STORAGE, local_inventory, _read_manifest
from ollama_readiness import write_readiness

MAX_JSON = 262144
MAX_FAILURE_MESSAGE = 256


def sanitized_provider_message(value):
    """Keep a bounded diagnostic without URLs, paths, control text or credentials."""
    if not isinstance(value, str): return None
    value = re.sub(r'https?://\S+', '[redacted-url]', value)
    value = re.sub(r'(?<![A-Za-z0-9._-])/(?:[^\s]+)', '[redacted-path]', value)
    value = re.sub(r'(?i)\b(private|secret|token|password|credential|bearer)\b(?:\s*[:=]\s*\S+)?', '[redacted]', value)
    value = re.sub(r'\b[A-Za-z0-9_-]{48,}\b', '[redacted-value]', value)
    value = re.sub(r'[\x00-\x1f\x7f]+', ' ', value)
    value = re.sub(r'\s+', ' ', value).strip()
    return value[:MAX_FAILURE_MESSAGE] or None


def failure_detail(stage, code, *, http_status=None, message=None, activity='UNKNOWN'):
    return {'stage': stage, 'code': code,
            'http_status': http_status if type(http_status) is int else None,
            'provider_message': sanitized_provider_message(message),
            'provider_activity': activity if activity in ('INACTIVE_CONFIRMED', 'UNKNOWN') else 'UNKNOWN'}


def validate_target(descriptor):
    frozen_descriptor(asdict(descriptor))
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,127}:[a-z0-9][a-z0-9._-]{0,127}', descriptor.artifact_id):
        raise ValueError('unsupported_artifact_identity')
    if descriptor.artifact_id.split(':')[1] != descriptor.exact_tag or 'cloud' in descriptor.exact_tag:
        raise ValueError('unsupported_tag')
    if not re.fullmatch(r'[0-9a-f]{64}', descriptor.expected_revision): raise ValueError('invalid_revision')
    return descriptor


def connection():
    # http.client does not follow redirects or inherit proxy environment settings; the
    # loopback check refuses a stand-in on Ollama's port (local_socket.py).
    from local_socket import LoopbackHTTPConnection
    return LoopbackHTTPConnection('127.0.0.1', 11434, timeout=30)


def local_json(path):
    if path != '/api/tags': raise ValueError('unsupported_read')
    conn = connection()
    try:
        conn.request('GET', path)
        response = conn.getresponse()
        raw = response.read(MAX_JSON + 1)
        if response.status != 200 or len(raw) > MAX_JSON: raise ValueError('provider_unavailable')
        return json.loads(raw)
    finally: conn.close()


def inspect_artifact(descriptor):
    """Require agreement of provider inventory, exact manifest hash and blob sizes."""
    validate_target(descriptor)
    try:
        models = local_json('/api/tags')['models']
        matches = [m for m in models if m.get('name') == descriptor.artifact_id]
        inventory = local_inventory()
        local = [m for m in inventory['records'] if m.get('id') == descriptor.artifact_id]
        if len(matches) > 1 or len(local) > 1: return {'state': 'unknown', 'reason': 'inventory_ambiguous'}
        if not matches and not local and inventory['status'] == 'ready': return {'state': 'absent'}
        if inventory.get('status') != 'ready': return {'state': 'unknown', 'reason': 'inventory_unavailable'}
        if len(matches) != 1 or len(local) != 1 or local[0].get('installed') is not True:
            return {'state': 'unknown', 'reason': 'inventory_ambiguous'}
        if matches[0].get('digest') != descriptor.expected_revision or local[0].get('digest') != descriptor.expected_revision:
            return {'state': 'mismatch'}
        name, tag = descriptor.artifact_id.split(':')
        path = OLLAMA_STORAGE / 'manifests/registry.ollama.ai/library' / name / tag
        digest = _read_manifest(path, OLLAMA_STORAGE)
        with path.open('rb') as file: manifest = json.loads(file.read(MAX_JSON + 1))
        layers = manifest['layers'] + [manifest['config']]
        footprint = sum(item['size'] for item in layers)
        if digest != descriptor.expected_revision or footprint != descriptor.expected_artifact_size:
            return {'state': 'mismatch'}
        # Detect replacement during inspection as well as incomplete references.
        if _read_manifest(path, OLLAMA_STORAGE) != digest: return {'state': 'unknown'}
        return {'state': 'exact', 'revision': digest, 'footprint_bytes': footprint}
    except (OSError, http.client.HTTPException):
        return {'state': 'unknown', 'reason': 'provider_unavailable'}
    except (ValueError, KeyError, TypeError):
        return {'state': 'unknown', 'reason': 'manifest_unavailable'}


def installed_revision(artifact_id):
    """The manifest digest Ollama reports for this exact name, or None."""
    matches = [m for m in local_json('/api/tags')['models'] if m.get('name') == artifact_id]
    return matches[0].get('digest') if len(matches) == 1 and isinstance(matches[0].get('digest'), str) else None


class OllamaAcquisitionAdapter:
    simulation = False
    cancellation_supported = False
    replace_revision = None  # set by the lifecycle for an approved model update

    def __init__(self):
        self.lock, self.started = Lock(), False
        self.observation = {'state': 'downloading', 'progress': None,
                            'transfer': {'bytes_transferred': None,
                                         'provider_completed_bytes': None,
                                         'provider_total_bytes': None}}
        self.verification_observation = None

    def begin(self, descriptor):
        validate_target(descriptor)
        with self.lock:
            if self.started: raise LifecycleError('duplicate_begin')
            self.started = True
        Thread(target=self._pull, args=(descriptor,), daemon=True).start()

    def _publish(self, state, sizes=None, failure=None):
        with self.lock:
            prior = self.observation.get('transfer')
            transfer = sizes if sizes is not None else prior
            self.observation = {'state': state, 'progress': None,
                                'transfer': transfer,
                                'transfer_observed_at': time.time() if sizes is not None else self.observation.get('transfer_observed_at')}
            if failure is not None: self.observation['failure_detail'] = failure

    def _pull(self, descriptor):
        conn = None
        try:
            if write_readiness() is not True:
                self._publish('failed', failure=failure_detail('preflight', 'provider_not_ready'))
                return
            existing = inspect_artifact(descriptor)['state']
            if existing == 'exact': self._publish('complete'); return
            replacing = (existing == 'mismatch' and isinstance(self.replace_revision, str)
                         and installed_revision(descriptor.artifact_id) == self.replace_revision)
            if existing != 'absent' and not replacing:
                self._publish('failed', failure=failure_detail('preflight', 'artifact_state_not_absent'))
                return
            conn = connection()
            body = json.dumps({'model': 'registry.ollama.ai/library/' + descriptor.artifact_id,
                               'stream': True, 'insecure': False})
            conn.request('POST', '/api/pull', body, {'Content-Type': 'application/json'})
            response = conn.getresponse()
            if response.status != 200:
                self._publish('failed', failure=failure_detail('provider_response', 'provider_http_error',
                              http_status=response.status, activity='INACTIVE_CONFIRMED'))
                return
            layers, blob_indices, deadline, events = {}, {}, time.monotonic() + 24 * 3600, 0
            last_current = {'current_blob_index': None,
                            'current_blob_completed_bytes': None,
                            'current_blob_total_bytes': None}
            while time.monotonic() < deadline:
                raw = response.readline(MAX_JSON + 1); events += 1
                if not raw:
                    self._publish('failed', failure=failure_detail('provider_stream', 'stream_ended_without_success'))
                    return
                if len(raw) > MAX_JSON or events > 2_000_000:
                    self._publish('failed', failure=failure_detail('provider_stream', 'provider_event_limit'))
                    return
                value = json.loads(raw)
                if not isinstance(value, dict):
                    self._publish('failed', failure=failure_detail('provider_stream', 'invalid_provider_event'))
                    return
                if 'error' in value:
                    self._publish('failed', failure=failure_detail('provider_response', 'provider_reported_error',
                                  message=value.get('error'), activity='INACTIVE_CONFIRMED'))
                    return
                digest, total, completed = value.get('digest'), value.get('total'), value.get('completed')
                current_index = current_completed = current_total = None
                if isinstance(digest, str) and re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
                    if digest not in blob_indices:
                        if len(blob_indices) >= 128:
                            self._publish('failed', failure=failure_detail('provider_stream', 'provider_blob_limit'))
                            return
                        blob_indices[digest] = len(blob_indices) + 1
                    current_index = blob_indices[digest]
                    if type(total) is int and type(completed) is int and 0 <= completed <= total < 2**63:
                        layers[digest] = (completed, total)
                        current_completed, current_total = completed, total
                    last_current = {'current_blob_index': current_index,
                                    'current_blob_completed_bytes': current_completed,
                                    'current_blob_total_bytes': current_total}
                sizes = {'bytes_transferred': None,
                         'provider_completed_bytes': sum(v[0] for v in layers.values()) if layers else None,
                         'provider_total_bytes': sum(v[1] for v in layers.values()) if layers else None,
                         **last_current}
                # Discovered blob totals are not a whole-download denominator.
                self._publish('complete' if value.get('status') == 'success' else 'downloading', sizes)
                if value.get('status') == 'success': return
            self._publish('failed', failure=failure_detail('provider_stream', 'provider_timeout'))
        except json.JSONDecodeError:
            self._publish('failed', failure=failure_detail('provider_stream', 'invalid_provider_event'))
        except (OSError, http.client.HTTPException):
            self._publish('failed', failure=failure_detail('provider_transport', 'transport_error'))
        except (ValueError, TypeError):
            self._publish('failed', failure=failure_detail('provider_protocol', 'invalid_provider_state'))
        finally:
            if conn: conn.close()

    def observe_progress(self):
        with self.lock: return {**self.observation}

    def verify(self, descriptor):
        self.verification_observation = inspect_artifact(descriptor)
        return self.verification_observation.get('revision')

    def request_cancel(self): raise LifecycleError('cancel_unsupported')
    def recover(self, descriptor): return inspect_artifact(descriptor)
