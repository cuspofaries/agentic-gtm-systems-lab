"""Append-only JSONL audit trail. No logging framework, no JSON-formatted-strings-in-loggers.

Every event is one line. `event_type` is a dotted string
(`discovery.complete`, `qualification.decision`, `sync.complete`). Payload is a JSON-safe dict.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


class EventRecorder:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, run_id: str, event_type: str, payload: dict) -> None:
        entry = {
            "at": datetime.now(UTC).isoformat(),
            "run_id": run_id,
            "event_type": event_type,
            "payload": payload,
        }
        with self.path.open("a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def read(self, run_id: str | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text().splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            if run_id is None or e.get("run_id") == run_id:
                out.append(e)
        return out


class NullRecorder(EventRecorder):
    """No-op recorder for tests that don't care about events."""

    def __init__(self):
        self._events: list[dict] = []

    def record(self, run_id: str, event_type: str, payload: dict) -> None:
        self._events.append(
            {"run_id": run_id, "event_type": event_type, "payload": payload}
        )

    def read(self, run_id: str | None = None) -> list[dict]:
        return [e for e in self._events if run_id is None or e["run_id"] == run_id]
