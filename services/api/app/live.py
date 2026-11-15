"""In-process fan-out of the latest request snapshot to the browser (SSE) and to GET requests."""

import asyncio
from uuid import UUID

FINAL = {"placed", "held", "blocked", "rejected", "failed"}


class Hub:
    def __init__(self, keep: int = 300):
        self.keep = keep
        self.snapshots: dict[UUID, dict] = {}
        self.revisions: dict[UUID, int] = {}
        self.waiters: dict[UUID, list[asyncio.Event]] = {}

    def publish(self, request_id: UUID, snapshot: dict) -> None:
        self.snapshots.pop(request_id, None)  # re-insert so the dict stays ordered by recency
        self.snapshots[request_id] = snapshot
        self.revisions[request_id] = self.revisions.get(request_id, 0) + 1
        for event in self.waiters.pop(request_id, []):
            event.set()
        while len(self.snapshots) > self.keep:
            oldest = next(iter(self.snapshots))
            self.snapshots.pop(oldest)
            self.revisions.pop(oldest, None)

    def latest(self, request_id: UUID) -> tuple[dict | None, int]:
        return self.snapshots.get(request_id), self.revisions.get(request_id, 0)

    async def wait(self, request_id: UUID, revision: int, timeout: float = 10) -> bool:
        """True as soon as a snapshot newer than `revision` exists, False after `timeout` seconds."""
        if self.revisions.get(request_id, 0) != revision:
            return True
        event = asyncio.Event()
        self.waiters.setdefault(request_id, []).append(event)
        try:
            await asyncio.wait_for(event.wait(), timeout)
            return True
        except TimeoutError:
            return False
