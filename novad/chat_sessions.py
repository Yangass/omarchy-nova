"""Bounded, in-memory chat session history.

The store owns no provider, transport, approval, or persistence behavior.  A
caller begins one turn, then explicitly completes or aborts that turn after it
has determined the request outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import secrets
from threading import Lock


MAX_SESSIONS = 16
MAX_TURNS = 4
MAX_HISTORY_BYTES = 16 * 1024


class SessionStoreError(Exception):
    """Base error with a stable code for later transport integration."""

    code = "session_store_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


class UnknownSessionError(SessionStoreError):
    code = "unknown_session"


class BusySessionError(SessionStoreError):
    code = "busy_session"


class SessionCapacityError(SessionStoreError):
    code = "session_capacity_exhausted"


class StaleTurnError(SessionStoreError):
    code = "stale_turn"


class InvalidMessageError(SessionStoreError):
    code = "invalid_message"


@dataclass(frozen=True, slots=True)
class Turn:
    user: str
    assistant: str


@dataclass(frozen=True, slots=True)
class BeginResult:
    session_id: str
    turn_id: str
    history: tuple[Turn, ...]


@dataclass(frozen=True, slots=True)
class RetentionResult:
    session_id: str
    turn_id: str
    retention: str
    evicted_turns: int
    retained_turns: int
    history_bytes: int


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    session_id: str
    busy: bool
    history: tuple[Turn, ...]
    history_bytes: int
    last_completion: RetentionResult | None


@dataclass(slots=True)
class _Session:
    session_id: str
    history: list[Turn]
    last_used: int
    active_turn_id: str | None = None
    active_user: str | None = None
    last_completion: RetentionResult | None = None


def _validated_text(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise InvalidMessageError(f"{field} must be non-empty text")
    return value


def _serialized_history_bytes(turns: list[Turn] | tuple[Turn, ...]) -> int:
    value = [{"user": turn.user, "assistant": turn.assistant} for turn in turns]
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


class ChatSessionStore:
    """Thread-safe session registry with bounded completed-turn history."""

    def __init__(self, *, max_sessions: int = MAX_SESSIONS,
                 max_turns: int = MAX_TURNS,
                 max_history_bytes: int = MAX_HISTORY_BYTES) -> None:
        if type(max_sessions) is not int or not 1 <= max_sessions <= MAX_SESSIONS:
            raise ValueError(f"max_sessions must be between 1 and {MAX_SESSIONS}")
        if type(max_turns) is not int or not 1 <= max_turns <= MAX_TURNS:
            raise ValueError(f"max_turns must be between 1 and {MAX_TURNS}")
        if type(max_history_bytes) is not int or not 2 <= max_history_bytes <= MAX_HISTORY_BYTES:
            raise ValueError(f"max_history_bytes must be between 2 and {MAX_HISTORY_BYTES}")
        self._max_sessions = max_sessions
        self._max_turns = max_turns
        self._max_history_bytes = max_history_bytes
        self._lock = Lock()
        self._sessions: dict[str, _Session] = {}
        self._usage_counter = 0

    def _touch(self, session: _Session) -> None:
        self._usage_counter += 1
        session.last_used = self._usage_counter

    def _new_identifier(self, prefix: str, existing: set[str] | None = None) -> str:
        while True:
            identifier = prefix + secrets.token_urlsafe(24)
            if existing is None or identifier not in existing:
                return identifier

    def _get(self, session_id: str) -> _Session:
        session = self._sessions.get(session_id)
        if session is None:
            raise UnknownSessionError()
        return session

    @staticmethod
    def _snapshot(session: _Session) -> SessionSnapshot:
        history = tuple(session.history)
        return SessionSnapshot(
            session_id=session.session_id,
            busy=session.active_turn_id is not None,
            history=history,
            history_bytes=_serialized_history_bytes(history),
            last_completion=session.last_completion,
        )

    def create(self) -> str:
        with self._lock:
            if len(self._sessions) >= self._max_sessions:
                idle = [item for item in self._sessions.values()
                        if item.active_turn_id is None]
                if not idle:
                    raise SessionCapacityError()
                evicted = min(idle, key=lambda item: item.last_used)
                del self._sessions[evicted.session_id]
            identifier = self._new_identifier("chat_", set(self._sessions))
            session = _Session(identifier, [], 0)
            self._touch(session)
            self._sessions[identifier] = session
            return identifier

    def snapshot(self, session_id: str) -> SessionSnapshot:
        with self._lock:
            return self._snapshot(self._get(session_id))

    def begin(self, session_id: str, user_text: object) -> BeginResult:
        user = _validated_text(user_text, "user_text")
        with self._lock:
            session = self._get(session_id)
            if session.active_turn_id is not None:
                raise BusySessionError()
            turn_id = self._new_identifier("turn_")
            session.active_turn_id = turn_id
            session.active_user = user
            self._touch(session)
            return BeginResult(session_id, turn_id, tuple(session.history))

    def complete(self, session_id: str, turn_id: str,
                 assistant_text: object) -> RetentionResult:
        assistant = _validated_text(assistant_text, "assistant_text")
        with self._lock:
            session = self._get(session_id)
            if session.active_turn_id != turn_id or session.active_user is None:
                raise StaleTurnError()

            new_turn = Turn(session.active_user, assistant)
            evicted_turns = 0
            if _serialized_history_bytes([new_turn]) > self._max_history_bytes:
                retention = "OMITTED_OVERSIZED"
            else:
                retention = "RETAINED"
                session.history.append(new_turn)
                while (len(session.history) > self._max_turns or
                       _serialized_history_bytes(session.history) > self._max_history_bytes):
                    session.history.pop(0)
                    evicted_turns += 1

            session.active_turn_id = None
            session.active_user = None
            self._touch(session)
            result = RetentionResult(
                session_id=session_id,
                turn_id=turn_id,
                retention=retention,
                evicted_turns=evicted_turns,
                retained_turns=len(session.history),
                history_bytes=_serialized_history_bytes(session.history),
            )
            session.last_completion = result
            return result

    def abort(self, session_id: str, turn_id: str) -> None:
        with self._lock:
            session = self._get(session_id)
            if session.active_turn_id != turn_id:
                raise StaleTurnError()
            session.active_turn_id = None
            session.active_user = None
            self._touch(session)

    def reset(self, session_id: str) -> SessionSnapshot:
        with self._lock:
            session = self._get(session_id)
            if session.active_turn_id is not None:
                raise BusySessionError()
            session.history.clear()
            session.last_completion = None
            self._touch(session)
            return self._snapshot(session)
