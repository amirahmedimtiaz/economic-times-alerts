from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path


class StateError(RuntimeError):
    """Raised when the deduplication state cannot be read or written."""


@dataclass(slots=True)
class StateStore:
    path: Path
    initialized: bool = False
    seen_ids: set[str] = field(default_factory=set)
    updated_at: str | None = None

    @classmethod
    def load(cls, path: Path) -> "StateStore":
        if not path.exists():
            return cls(path=path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateError(f"Could not read state file {path}: {exc}") from exc
        return cls(
            path=path,
            initialized=bool(raw.get("initialized", False)),
            seen_ids={str(value) for value in raw.get("seen_ids", [])},
            updated_at=raw.get("updated_at"),
        )

    def initialize(self, ids: list[str]) -> None:
        self.initialized = True
        self.seen_ids.update(ids)
        self._touch()

    def mark_seen(self, story_id: str) -> None:
        self.initialized = True
        self.seen_ids.add(story_id)
        self._touch()

    def has_seen(self, story_id: str) -> bool:
        return story_id in self.seen_ids

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "initialized": self.initialized,
            "seen_ids": sorted(self.seen_ids),
            "updated_at": self.updated_at,
        }
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        try:
            temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError as exc:
            raise StateError(f"Could not write state file {self.path}: {exc}") from exc

    def _touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()
