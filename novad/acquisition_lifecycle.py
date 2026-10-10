"""Durable acquisition authority with explicit simulated or fixed real adapters."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
from threading import RLock
import time

from acquisition import AcquisitionDescriptor, descriptor_complete

STATE_PATH = Path.home() / '.local/state/nova/acquisitions/state.json'
MAX_BYTES, MAX_RECORDS, TTL, STATE_VERSION = 1_048_576, 128, 120, 2
OPERATOR_INACTIVE_ATTESTATION = 'NO_PROVIDER_ACQUISITION_ACTIVE'
PROGRESS_FIELDS = frozenset(('bytes_transferred', 'provider_completed_bytes',
    'provider_total_bytes', 'current_blob_index', 'current_blob_completed_bytes',
    'current_blob_total_bytes', 'verified_footprint_bytes'))
ACTIVE_WORK = frozenset(('APPROVED', 'STARTING', 'DOWNLOADING', 'VERIFYING', 'CANCEL_REQUESTED'))
ARTIFACT_STATES = frozenset(('EXACT', 'ABSENT', 'MISMATCH', 'UNVERIFIABLE'))
ACTIVITY_STATES = frozenset(('INACTIVE_CONFIRMED', 'ACTIVE', 'UNKNOWN'))
TERMINAL = frozenset(('INSTALLED', 'FAILED', 'CANCELLED', 'REJECTED', 'EXPIRED', 'RECOVERY_REQUIRED'))
TRANSITIONS = {
    'PROPOSED': {'APPROVED', 'REJECTED', 'EXPIRED', 'FAILED'},
    'APPROVED': {'STARTING', 'FAILED', 'RECOVERY_REQUIRED'},
    'STARTING': {'DOWNLOADING', 'FAILED', 'RECOVERY_REQUIRED'},
    'DOWNLOADING': {'VERIFYING', 'FAILED', 'CANCEL_REQUESTED', 'RECOVERY_REQUIRED'},
    'VERIFYING': {'INSTALLED', 'FAILED', 'RECOVERY_REQUIRED'},
    'CANCEL_REQUESTED': {'CANCELLED', 'FAILED', 'RECOVERY_REQUIRED'},
    **{state: set() for state in TERMINAL},
}


class LifecycleError(ValueError):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code, self.status = code, status


def frozen_descriptor(value):
    value = deepcopy(value)
    value['provenance_summary'] = tuple(value['provenance_summary'])
    descriptor = AcquisitionDescriptor(**value)
    if descriptor_complete(descriptor) or descriptor.source_trust_state != 'TRUSTED':
        raise ValueError('incomplete_or_untrusted_descriptor')
    if descriptor.provider != 'ollama' or type(descriptor.expected_artifact_size) is not int or descriptor.expected_artifact_size <= 0:
        raise ValueError('invalid_descriptor')
    for name in ('catalog_id', 'catalog_revision', 'artifact_id', 'exact_tag', 'expected_revision'):
        text = getattr(descriptor, name)
        if type(text) is not str or not text or len(text) > 512:
            raise ValueError('invalid_descriptor')
    if len(descriptor.provenance_summary) > 32 or any(type(v) is not str or len(v) > 4096 for v in descriptor.provenance_summary):
        raise ValueError('invalid_provenance')
    return descriptor


class SimulatedAdapter:
    simulation = True
    cancellation_supported = True
    """In-memory deterministic job; never receives approval authority or paths.

    Scenario is constructor-injected by tests only, never an HTTP input.
    """
    def __init__(self, scenario='success'):
        if scenario not in ('success', 'failure', 'mismatch'): raise ValueError('scenario')
        self.scenario, self.started, self.cancelled, self.step = scenario, False, False, 0

    def begin(self, descriptor):
        if self.started: raise LifecycleError('duplicate_begin')
        if not isinstance(descriptor, AcquisitionDescriptor): raise ValueError('descriptor')
        self.started = True

    def observe_progress(self):
        if not self.started: raise LifecycleError('not_started')
        if self.cancelled: return {'state': 'cancelled', 'progress': None}
        if self.scenario == 'failure': return {'state': 'failed', 'progress': None}
        self.step += 1
        return {'state': 'complete' if self.step >= 2 else 'downloading', 'progress': min(self.step * 50, 100)}

    def verify(self, descriptor):
        return descriptor.expected_revision if self.started and self.step >= 2 and self.scenario != 'mismatch' else None

    def request_cancel(self): self.cancelled = True
    def recover(self): return 'RECOVERY_REQUIRED'


class AcquisitionLifecycle:
    def __init__(self, path=STATE_PATH, clock=time.time, adapter_factory=SimulatedAdapter):
        self.path, self.clock, self.adapter_factory = Path(path), clock, adapter_factory
        self.lock, self.records, self.jobs, self.error = RLock(), {}, {}, None
        self.progress_history, self.failure_details, self.recovery_resolutions = {}, {}, {}
        self._load()

    @staticmethod
    def _valid_progress(value):
        return (type(value) is dict and not (set(value) - PROGRESS_FIELDS)
                and all(v is None or (type(v) is int and 0 <= v < 2**63) for v in value.values()))

    @staticmethod
    def _valid_timestamp(value): return type(value) in (int, float) and 0 <= value < 1e12

    def _validate_supplemental(self):
        if any(type(value) is not dict for value in
               (self.progress_history, self.failure_details, self.recovery_resolutions)):
            raise ValueError('supplemental')
        if any(identifier not in self.records for mapping in
               (self.progress_history, self.failure_details, self.recovery_resolutions)
               for identifier in mapping):
            raise ValueError('supplemental_reference')
        for value in self.progress_history.values():
            if set(value) != {'progress', 'observed_at'} or not self._valid_progress(value['progress']) or not self._valid_timestamp(value['observed_at']):
                raise ValueError('progress_history')
        for value in self.failure_details.values():
            if set(value) != {'stage', 'code', 'http_status', 'provider_message', 'provider_activity', 'observed_at'}:
                raise ValueError('failure_detail')
            if type(value['stage']) is not str or not value['stage'] or len(value['stage']) > 64: raise ValueError('failure_stage')
            if type(value['code']) is not str or not value['code'] or len(value['code']) > 64: raise ValueError('failure_code')
            if value['http_status'] is not None and (type(value['http_status']) is not int or not 100 <= value['http_status'] <= 599): raise ValueError('failure_http_status')
            if value['provider_message'] is not None and (type(value['provider_message']) is not str or len(value['provider_message']) > 256): raise ValueError('failure_message')
            if value['provider_activity'] not in ACTIVITY_STATES or not self._valid_timestamp(value['observed_at']): raise ValueError('failure_observation')
        for identifier, value in self.recovery_resolutions.items():
            expected = {'id', 'acquisition_id', 'created_at', 'assessment_id', 'artifact_state',
                        'provider_activity', 'outcome', 'basis', 'operator_attestation', 'retry_allowed'}
            if set(value) != expected or value['acquisition_id'] != identifier: raise ValueError('recovery_resolution')
            if type(value['id']) is not str or not value['id'].startswith('rec_') or len(value['id']) != 36: raise ValueError('recovery_resolution_id')
            if not self._valid_timestamp(value['created_at']): raise ValueError('recovery_resolution_time')
            if type(value['assessment_id']) is not str or len(value['assessment_id']) != 64: raise ValueError('recovery_assessment_id')
            if value['artifact_state'] not in ('EXACT', 'ABSENT') or value['provider_activity'] not in ACTIVITY_STATES: raise ValueError('recovery_resolution_state')
            if value['outcome'] not in ('EXACT_INSTALLED', 'RETRY_ALLOWED') or value['basis'] not in ('MACHINE_VERIFIED_IDENTITY', 'MACHINE_CONFIRMED_INACTIVE', 'OPERATOR_ATTESTATION'): raise ValueError('recovery_resolution_outcome')
            if type(value['operator_attestation']) is not bool or type(value['retry_allowed']) is not bool: raise ValueError('recovery_resolution_flags')

    def _load(self):
        try:
            if not self.path.exists() and not self.path.is_symlink(): return
            info = self.path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > MAX_BYTES:
                raise ValueError('unsafe_state')
            with self.path.open('rb') as handle: raw = handle.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES: raise ValueError('oversized')
            document = json.loads(raw)
            if type(document) is not dict or document.get('version') not in (1, STATE_VERSION): raise ValueError('schema')
            if document['version'] == 1:
                if set(document) != {'version', 'records'}: raise ValueError('schema')
            elif set(document) != {'version', 'records', 'progress_history', 'failure_details', 'recovery_resolutions'}:
                raise ValueError('schema')
            records = document['records']
            if type(records) is not dict or len(records) > MAX_RECORDS: raise ValueError('records')
            for identifier, row in records.items():
                if not identifier.startswith('acq_') or len(identifier) != 47: raise ValueError('id')
                if set(row) != {'id', 'action', 'simulation', 'activation', 'state', 'preflight', 'created_at', 'expires_at', 'updated_at', 'progress', 'verification', 'error'}: raise ValueError('record')
                if row['id'] != identifier or row['action'] != 'acquire_catalog_model' or type(row['simulation']) is not bool or row['activation'] != 'not_performed': raise ValueError('authority')
                if row['state'] not in TRANSITIONS: raise ValueError('state')
                frozen_descriptor(row['preflight']['descriptor'])
                if row['preflight']['eligible'] is not True or row['preflight']['status'] != 'ELIGIBLE': raise ValueError('preflight')
                for key in ('created_at', 'expires_at', 'updated_at'):
                    if type(row[key]) not in (int, float) or not 0 <= row[key] < 1e12: raise ValueError('timestamp')
                if row['expires_at'] != row['created_at'] + TTL: raise ValueError('expiry')
                if row['progress'] is not None and type(row['progress']) is not dict and row['progress'] not in (50, 100): raise ValueError('progress')
                if isinstance(row['progress'], dict):
                    if not self._valid_progress(row['progress']): raise ValueError('progress_fields')
                if row['verification'] not in ('pending', 'matched', 'mismatch'): raise ValueError('observation')
                if row['state'] == 'INSTALLED' and row['verification'] != 'matched': raise ValueError('false_success')
                if row['error'] is not None and (type(row['error']) is not str or len(row['error']) > 128): raise ValueError('error')
            self.records = records
            if document['version'] == STATE_VERSION:
                self.progress_history = document['progress_history']
                self.failure_details = document['failure_details']
                self.recovery_resolutions = document['recovery_resolutions']
                self._validate_supplemental()
            changed = False
            for row in self.records.values():
                if row['state'] not in TERMINAL:
                    prior, prior_progress, prior_updated_at = row['state'], row['progress'], row['updated_at']
                    row['state'] = 'EXPIRED' if prior == 'PROPOSED' else 'RECOVERY_REQUIRED'
                    row['error'], row['updated_at'] = 'restart_requires_new_proposal', self.clock()
                    if prior != 'PROPOSED' and not row['simulation'] and not getattr(self.adapter_factory, 'simulation', True):
                        if isinstance(prior_progress, dict):
                            self._record_progress(row['id'], prior_progress, prior_updated_at)
                        row['progress'] = None
                        try: observation = self.adapter_factory().recover(frozen_descriptor(row['preflight']['descriptor']))
                        except Exception: observation = {'state': 'unknown'}
                        if observation.get('state') == 'exact':
                            row.update(state='INSTALLED', verification='matched', error=None)
                            row['progress'] = {'verified_footprint_bytes': observation.get('footprint_bytes')}
                        elif observation.get('state') == 'mismatch':
                            row.update(state='FAILED', verification='mismatch', error='revision_mismatch')
                        # Absence is not proof that an upstream pull stopped.
                    changed = True
            if changed: self._save()
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            self.records, self.progress_history, self.failure_details, self.recovery_resolutions = {}, {}, {}, {}
            self.error = 'acquisition_state_unavailable'

    def _save(self):
        try:
            self._validate_supplemental()
            raw = json.dumps({'version': STATE_VERSION, 'records': self.records,
                              'progress_history': self.progress_history,
                              'failure_details': self.failure_details,
                              'recovery_resolutions': self.recovery_resolutions}, allow_nan=False).encode()
            if len(raw) > MAX_BYTES: raise ValueError('state_capacity')
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = self.path.parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077: raise ValueError('unsafe_directory')
            fd, temporary = tempfile.mkstemp(prefix='.state-', dir=self.path.parent)
            try:
                with os.fdopen(fd, 'wb') as handle:
                    os.fchmod(handle.fileno(), 0o600)
                    handle.write(raw); handle.flush(); os.fsync(handle.fileno())
                os.replace(temporary, self.path)
                directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try: os.fsync(directory)
                finally: os.close(directory)
            finally:
                if os.path.exists(temporary): os.unlink(temporary)
        except (OSError, ValueError):
            self.error = 'acquisition_state_unavailable'
            raise LifecycleError(self.error, 503)

    def _ready(self):
        if self.error: raise LifecycleError(self.error, 503)

    def get(self, identifier):
        with self.lock:
            self._ready()
            if identifier not in self.records: raise LifecycleError('unknown_acquisition', 404)
            result = deepcopy(self.records[identifier])
            result['cancellation'] = 'supported_simulation' if result['simulation'] else 'CANCEL_UNSUPPORTED'
            result['progress_basis'] = 'simulation' if result['simulation'] else 'provider_blob_counters_include_cached_bytes_no_global_percentage'
            result['historical_progress'] = deepcopy(self.progress_history.get(identifier))
            result['failure_detail'] = deepcopy(self.failure_details.get(identifier))
            result['recovery_resolution'] = deepcopy(self.recovery_resolutions.get(identifier))
            return result

    def list_records(self):
        """Read-only, newest-first lifecycle view for the native selector."""
        with self.lock:
            self._ready()
            rows = [self.get(identifier) for identifier in self.records]
            return sorted(rows, key=lambda row: (row['updated_at'], row['id']), reverse=True)

    def _transition(self, row, target, error=None):
        if target not in TRANSITIONS[row['state']]: raise LifecycleError('invalid_transition')
        row.update(state=target, updated_at=self.clock(), error=error)
        self._save()

    def _record_progress(self, identifier, progress, observed_at=None):
        if not self._valid_progress(progress) or not any(value is not None for value in progress.values()): return
        when = observed_at if self._valid_timestamp(observed_at) else self.clock()
        self.progress_history[identifier] = {'progress': deepcopy(progress), 'observed_at': when}

    def _record_failure(self, identifier, detail=None):
        source = detail if isinstance(detail, dict) else {}
        stage = source.get('stage') if isinstance(source.get('stage'), str) else 'provider_execution'
        code = source.get('code') if isinstance(source.get('code'), str) else 'provider_failure'
        status = source.get('http_status') if type(source.get('http_status')) is int and 100 <= source['http_status'] <= 599 else None
        message = source.get('provider_message') if isinstance(source.get('provider_message'), str) else None
        activity = source.get('provider_activity') if source.get('provider_activity') in ACTIVITY_STATES else 'UNKNOWN'
        self.failure_details[identifier] = {
            'stage': stage[:64] or 'provider_execution', 'code': code[:64] or 'provider_failure',
            'http_status': status, 'provider_message': message[:256] if message else None,
            'provider_activity': activity, 'observed_at': self.clock(),
        }

    def _active_work(self):
        return any(row['state'] in ACTIVE_WORK for row in self.records.values()) or bool(self.jobs)

    @staticmethod
    def _artifact_assessment(observation):
        value = observation if isinstance(observation, dict) else {}
        state = {'exact': 'EXACT', 'absent': 'ABSENT', 'mismatch': 'MISMATCH'}.get(value.get('state'), 'UNVERIFIABLE')
        reason = value.get('reason') if isinstance(value.get('reason'), str) and value.get('reason') in {
            'provider_unavailable', 'inventory_unavailable', 'inventory_ambiguous',
            'manifest_unavailable', 'invalid_observation'} else None
        return state, reason

    def _assessment_locked(self, identifier, inspect):
        self._ready()
        if identifier not in self.records: raise LifecycleError('unknown_acquisition', 404)
        row = self.records[identifier]
        if row['state'] != 'RECOVERY_REQUIRED': raise LifecycleError('recovery_not_required')
        descriptor = frozen_descriptor(row['preflight']['descriptor'])
        try: observation = inspect(descriptor)
        except Exception: observation = {'state': 'unknown', 'reason': 'provider_unavailable'}
        artifact_state, detail = self._artifact_assessment(observation)
        failure = self.failure_details.get(identifier)
        activity = failure.get('provider_activity') if failure else 'UNKNOWN'
        if identifier in self.jobs: activity = 'ACTIVE'
        existing = self.recovery_resolutions.get(identifier)
        material = {'acquisition_id': identifier, 'record_updated_at': row['updated_at'],
                    'artifact_state': artifact_state, 'artifact_detail': detail,
                    'provider_activity': activity,
                    'resolution_id': existing.get('id') if existing else None}
        assessment_id = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        allowed = artifact_state == 'EXACT' or (artifact_state == 'ABSENT' and activity != 'ACTIVE')
        return {**material, 'assessment_id': assessment_id,
                'resolution_status': 'RESOLVED' if existing else ('AVAILABLE' if allowed else 'BLOCKED'),
                'operator_attestation_required': artifact_state == 'ABSENT' and activity == 'UNKNOWN',
                'retry_allowed': bool(existing and existing['retry_allowed']),
                'limitations': ([] if activity != 'UNKNOWN' else ['PROVIDER_ACTIVITY_NOT_MACHINE_VERIFIED'])
                               + ([] if artifact_state != 'UNVERIFIABLE' else ['ARTIFACT_IDENTITY_UNVERIFIABLE'])}

    def assess_recovery(self, identifier, inspect):
        """Read-only exact-identity assessment. It never probes or changes provider work."""
        with self.lock: return deepcopy(self._assessment_locked(identifier, inspect))

    def resolve_recovery(self, identifier, assessment_id, operator_attestation, inspect):
        """Record a separate immutable resolution; never alter the failed attempt or start work."""
        with self.lock:
            self._ready()
            if self._active_work(): raise LifecycleError('acquisition_active')
            if identifier in self.recovery_resolutions: raise LifecycleError('recovery_already_resolved')
            if type(assessment_id) is not str or len(assessment_id) != 64: raise LifecycleError('invalid_recovery_request', 400)
            assessment = self._assessment_locked(identifier, inspect)
            if assessment['assessment_id'] != assessment_id: raise LifecycleError('stale_recovery_assessment')
            artifact, activity = assessment['artifact_state'], assessment['provider_activity']
            if artifact == 'MISMATCH': raise LifecycleError('artifact_revision_mismatch')
            if artifact == 'UNVERIFIABLE': raise LifecycleError('artifact_identity_unverifiable', 503)
            if activity == 'ACTIVE': raise LifecycleError('provider_acquisition_active')
            attested = operator_attestation == OPERATOR_INACTIVE_ATTESTATION
            if artifact == 'ABSENT' and activity == 'UNKNOWN' and not attested:
                raise LifecycleError('operator_attestation_required', 400)
            if operator_attestation not in (None, OPERATOR_INACTIVE_ATTESTATION):
                raise LifecycleError('invalid_operator_attestation', 400)
            if artifact == 'EXACT':
                outcome, basis, retry_allowed = 'EXACT_INSTALLED', 'MACHINE_VERIFIED_IDENTITY', False
            else:
                outcome, retry_allowed = 'RETRY_ALLOWED', True
                basis = 'MACHINE_CONFIRMED_INACTIVE' if activity == 'INACTIVE_CONFIRMED' else 'OPERATOR_ATTESTATION'
            value = {'id': 'rec_' + secrets.token_urlsafe(24), 'acquisition_id': identifier,
                     'created_at': self.clock(), 'assessment_id': assessment_id,
                     'artifact_state': artifact, 'provider_activity': activity,
                     'outcome': outcome, 'basis': basis,
                     'operator_attestation': attested, 'retry_allowed': retry_allowed}
            self.recovery_resolutions[identifier] = value
            try: self._save()
            except Exception:
                self.recovery_resolutions.pop(identifier, None)
                raise
            return deepcopy(value)

    def propose(self, preflight):
        with self.lock:
            self._ready()
            preflight = json.loads(json.dumps(preflight, allow_nan=False))
            if not preflight or preflight.get('eligible') is not True or preflight.get('status') != 'ELIGIBLE':
                raise LifecycleError('preflight_not_eligible')
            descriptor = frozen_descriptor(preflight['descriptor'])
            if preflight.get('catalog_revision_state') != 'CURRENT' or preflight.get('blockers'):
                raise LifecycleError('preflight_not_eligible')
            if len(self.records) >= MAX_RECORDS: raise LifecycleError('state_capacity', 503)
            for row in self.records.values():
                if row['state'] == 'PROPOSED' and self.clock() >= row['expires_at']:
                    self._transition(row, 'EXPIRED', 'expired_approval')
                old = row['preflight']['descriptor']
                same_artifact = (old['provider'], old['artifact_id']) == (descriptor.provider, descriptor.artifact_id)
                recovery = self.recovery_resolutions.get(row['id'])
                recovery_allows_retry = bool(recovery and recovery['retry_allowed'])
                if same_artifact and (row['state'] not in TERMINAL or (row['state'] == 'RECOVERY_REQUIRED' and not recovery_allows_retry)):
                    raise LifecycleError('acquisition_conflict')
                # An update's preflight has just observed a different build on disk, so an
                # earlier completed install of the same build does not make it a repeat (run 8).
                if old == preflight['descriptor'] and row['state'] == 'INSTALLED' and row['simulation'] == getattr(self.adapter_factory, 'simulation', True) \
                        and preflight.get('inventory_state') != 'NOT_INSTALLED' \
                        and not (preflight.get('intent') == 'update' and preflight.get('inventory_state') == 'INSTALLED_DIFFERENT_REVISION'):
                    raise LifecycleError('acquisition_already_completed')
            identifier, now = 'acq_' + secrets.token_urlsafe(32), self.clock()
            self.records[identifier] = {'id': identifier, 'action': 'acquire_catalog_model', 'simulation': getattr(self.adapter_factory, 'simulation', True),
                'activation': 'not_performed', 'state': 'PROPOSED', 'preflight': deepcopy(preflight),
                'created_at': now, 'expires_at': now + TTL, 'updated_at': now,
                'progress': None, 'verification': 'pending', 'error': None}
            self._save()
            return self.get(identifier)

    def decide(self, identifier, decision, resolve):
        with self.lock:
            self.get(identifier)
            row = self.records[identifier]
            if row['state'] == 'EXPIRED': raise LifecycleError('expired_approval', 410)
            if row['state'] != 'PROPOSED': raise LifecycleError('approval_already_used')
            if self.clock() >= row['expires_at']:
                self._transition(row, 'EXPIRED', 'expired_approval')
                raise LifecycleError('expired_approval', 410)
            if decision == 'denied':
                self._transition(row, 'REJECTED'); return self.get(identifier)
            if decision != 'approved': raise LifecycleError('invalid_decision', 400)
            try: current = json.loads(json.dumps(resolve(row['preflight']['descriptor']['catalog_id']), allow_nan=False))
            except Exception: current = None
            # Strict revision equality is intentionally conservative in this slice.
            if row['simulation'] != getattr(self.adapter_factory, 'simulation', True) or not current or current.get('eligible') is not True or current.get('descriptor') != row['preflight']['descriptor']:
                self._transition(row, 'FAILED', 'stale_or_blocked_proposal')
                raise LifecycleError('stale_or_blocked_proposal')
            self._transition(row, 'APPROVED')
            return self.get(identifier)

    def advance(self, identifier):
        """Internal worker step. GET never advances a job."""
        with self.lock:
            self.get(identifier)
            row = self.records[identifier]
            descriptor = frozen_descriptor(row['preflight']['descriptor'])
            try:
                if row['state'] == 'APPROVED':
                    self._transition(row, 'STARTING')
                    adapter = self.adapter_factory()
                    self.jobs[identifier] = adapter
                    if row['preflight'].get('intent') == 'update':
                        # Only the exact older build named in the approved proposal may be replaced.
                        adapter.replace_revision = row['preflight'].get('replaces_revision')
                    adapter.begin(descriptor)
                    self._transition(row, 'DOWNLOADING')
                elif row['state'] in ('DOWNLOADING', 'CANCEL_REQUESTED'):
                    observed = self.jobs[identifier].observe_progress()
                    progress = observed.get('transfer', observed.get('progress'))
                    row['progress'] = progress
                    if not row['simulation'] and isinstance(progress, dict):
                        self._record_progress(identifier, progress, observed.get('transfer_observed_at'))
                    if row['state'] == 'CANCEL_REQUESTED':
                        self._transition(row, 'CANCELLED' if observed['state'] == 'cancelled' else 'RECOVERY_REQUIRED')
                    elif observed['state'] == 'failed':
                        if not row['simulation']:
                            self._record_failure(identifier, observed.get('failure_detail'))
                            row['progress'] = None
                        self._transition(row, 'FAILED' if row['simulation'] else 'RECOVERY_REQUIRED', 'provider_failure')
                    elif observed['state'] == 'complete': self._transition(row, 'VERIFYING')
                    else: self._save()
                elif row['state'] == 'VERIFYING':
                    matched = self.jobs[identifier].verify(descriptor) == descriptor.expected_revision
                    if not row['simulation']:
                        observation = self.jobs[identifier].verification_observation or {}
                        row['progress'] = {**(row['progress'] or {}), 'verified_footprint_bytes': observation.get('footprint_bytes')}
                    row['verification'] = 'matched' if matched else 'mismatch'
                    self._transition(row, 'INSTALLED' if matched else 'FAILED', None if matched else 'revision_mismatch')
                else: raise LifecycleError('invalid_transition')
            except LifecycleError: raise
            except Exception:
                if not row['simulation']:
                    self._record_failure(identifier)
                    row['progress'] = None
                self._transition(row, 'FAILED' if row['simulation'] else 'RECOVERY_REQUIRED', 'provider_failure')
            if row['state'] in TERMINAL: self.jobs.pop(identifier, None)
            return self.get(identifier)

    def cancel(self, identifier):
        with self.lock:
            self.get(identifier)
            row = self.records[identifier]
            if not getattr(self.adapter_factory, 'cancellation_supported', True): raise LifecycleError('cancel_unsupported')
            self._transition(row, 'CANCEL_REQUESTED')
            try: self.jobs[identifier].request_cancel()
            except Exception: self._transition(row, 'RECOVERY_REQUIRED', 'cancellation_unconfirmed')
            return self.get(identifier)

    def run(self, identifier):
        try:
            while self.get(identifier)['state'] not in TERMINAL:
                self.advance(identifier)
                if not getattr(self.adapter_factory, 'simulation', True): time.sleep(0.25)
        except LifecycleError:
            # Persistence failure disables the store; never retry provider work.
            return
