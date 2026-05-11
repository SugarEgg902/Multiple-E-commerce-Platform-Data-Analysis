from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass
class ChatMessage:
    role: str
    content: str


@dataclass
class SessionSlots:
    platform: str | None = None
    brand: str | None = None
    count: int | None = None


@dataclass
class ChatSession:
    session_id: str
    messages: list[ChatMessage] = field(default_factory=list)
    slots: SessionSlots = field(default_factory=SessionSlots)
    active_run_id: str | None = None


class ConcurrentRunError(RuntimeError):
    pass


class SessionStore:
    def __init__(self):
        self._sessions: dict[str, ChatSession] = {}

    def create_session(self) -> ChatSession:
        session = ChatSession(session_id=uuid.uuid4().hex)
        self._sessions[session.session_id] = session
        return session

    def get_session(self, session_id: str) -> ChatSession:
        return self._sessions[session_id]

    def append_message(self, session_id: str, role: str, content: str) -> ChatMessage:
        session = self.get_session(session_id)
        message = ChatMessage(role=role, content=content)
        session.messages.append(message)
        return message

    def update_slots(
        self,
        session_id: str,
        *,
        platform: str | None = None,
        brand: str | None = None,
        count: int | None = None,
    ) -> SessionSlots:
        session = self.get_session(session_id)
        if platform is not None:
            session.slots.platform = platform
        if brand is not None:
            session.slots.brand = brand
        if count is not None:
            session.slots.count = count
        return session.slots

    def start_run(self, session_id: str) -> str:
        session = self.get_session(session_id)
        if session.active_run_id is not None:
            raise ConcurrentRunError(f"session {session_id} already active: {session.active_run_id}")

        run_id = uuid.uuid4().hex
        session.active_run_id = run_id
        return run_id

    def finish_run(self, session_id: str, run_id: str) -> None:
        session = self.get_session(session_id)
        if session.active_run_id == run_id:
            session.active_run_id = None
