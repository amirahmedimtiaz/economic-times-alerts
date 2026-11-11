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
    stale_ids: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, path: Path) -> "StateStore":
        if not path.exists():
            return cls(path=path)
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise StateError(f"Could not read state file {path}: {exc}") from exc
        return cls.from_json(path, content)

    @classmethod
    def from_json(cls, path: Path, content: str) -> "StateStore":
        try:
            raw = json.loads(content)
        except json.JSONDecodeError as exc:
            raise StateError(f"Could not read state file {path}: {exc}") from exc
        if (
            not isinstance(raw, dict)
            or type(raw.get("version", 1)) is not int
            or raw.get("version", 1) not in (1, 2)
            or not isinstance(raw.get("initialized", False), bool)
            or (raw.get("initialized") is True and "seen_ids" not in raw)
            or not isinstance(raw.get("seen_ids", []), list)
            or not isinstance(raw.get("stale_ids", []), list)
            or any(not isinstance(value, str) or not value for value in raw.get("seen_ids", []))
            or any(not isinstance(value, str) or not value for value in raw.get("stale_ids", []))
        ):
            raise StateError(f"Invalid deduplication state in {path}")
        return cls(
            path=path,
            initialized=bool(raw.get("initialized", False)),
            seen_ids={str(value) for value in raw.get("seen_ids", [])},
            updated_at=raw.get("updated_at"),
            stale_ids=set(raw.get("stale_ids", [])),
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

    def mark_stale(self, story_id: str) -> None:
        """Remember a rejected archive without recording it as delivered."""
        self.stale_ids.add(story_id)
        self._touch()

    def has_processed(self, story_id: str) -> bool:
        return story_id in self.seen_ids or story_id in self.stale_ids

    def to_json(self) -> str:
        payload = {
            "version": 2,
            "initialized": self.initialized,
            "seen_ids": sorted(self.seen_ids),
            "updated_at": self.updated_at,
            "stale_ids": sorted(self.stale_ids),
        }
        return json.dumps(payload, indent=2) + "\n"

    def save(self) -> None:
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(self.to_json(), encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError as exc:
            raise StateError(f"Could not write state file {self.path}: {exc}") from exc

    def _touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()
