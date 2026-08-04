"""Per-user conversation state for the slot-filling capture flow.

Kept on disk (not in memory) so a restart mid-conversation does not lose
the answers already given. Sessions expire — an abandoned capture from
this morning must not swallow tonight's first message as an answer.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


@dataclass
class Session:
    user_id: str
    entry_id: str
    title: str
    slot_index: int = 0
    answers: dict[str, str] = field(default_factory=dict)
    updated_at: float = 0.0


class SessionStore:
    def __init__(self, directory: Path, ttl_seconds: int = 1800) -> None:
        self.directory = Path(directory)
        self.ttl_seconds = ttl_seconds

    def _path(self, user_id: str) -> Path:
        return self.directory / f"{_UNSAFE.sub('_', user_id)}.json"

    def get(self, user_id: str, now: float | None = None) -> Session | None:
        now = time.time() if now is None else now
        path = self._path(user_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            session = Session(**data)
        except (ValueError, TypeError, OSError):
            path.unlink(missing_ok=True)
            return None
        if now - session.updated_at > self.ttl_seconds:
            path.unlink(missing_ok=True)
            return None
        return session

    def save(self, session: Session, now: float | None = None) -> None:
        session.updated_at = time.time() if now is None else now
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(session.user_id)
        path.write_text(json.dumps(asdict(session)), encoding="utf-8")

    def clear(self, user_id: str) -> None:
        self._path(user_id).unlink(missing_ok=True)
