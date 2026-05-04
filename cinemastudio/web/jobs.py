"""In-memory job tracking + log fan-out for the web UI.

A "job" is a pipeline step running in the background (script, moodboard,
generate-all, regenerate-shot). Each job has a status, a buffered log, and
async subscribers that receive new log lines for SSE streaming.
"""
from __future__ import annotations

import asyncio
import time
import traceback
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable


@dataclass
class JobLogLine:
    ts: float
    level: str  # info | error | done
    message: str


@dataclass
class Job:
    id: str
    project: str
    kind: str  # "script" | "moodboard" | "generate" | "regen-keyframe" | "regen-clip"
    status: str = "running"  # running | done | failed
    created_at: float = field(default_factory=time.time)
    log: deque[JobLogLine] = field(default_factory=lambda: deque(maxlen=1000))
    subscribers: list[asyncio.Queue[JobLogLine | None]] = field(default_factory=list)

    def emit(self, message: str, level: str = "info") -> None:
        line = JobLogLine(ts=time.time(), level=level, message=message)
        self.log.append(line)
        for q in list(self.subscribers):
            try:
                q.put_nowait(line)
            except asyncio.QueueFull:
                pass

    def finish(self, ok: bool, message: str = "") -> None:
        self.status = "done" if ok else "failed"
        self.emit(message or self.status, level="done" if ok else "error")
        for q in list(self.subscribers):
            try:
                q.put_nowait(None)  # sentinel: stream over
            except asyncio.QueueFull:
                pass


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = asyncio.Lock()

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list_for_project(self, project: str) -> list[Job]:
        return sorted(
            (j for j in self._jobs.values() if j.project == project),
            key=lambda j: j.created_at,
            reverse=True,
        )

    def start(
        self,
        *,
        project: str,
        kind: str,
        coro_factory: Callable[[Job], Awaitable[None]],
    ) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], project=project, kind=kind)
        self._jobs[job.id] = job

        async def _runner() -> None:
            try:
                job.emit(f"{kind} started")
                await coro_factory(job)
                job.finish(ok=True, message=f"{kind} completed")
            except Exception as e:  # noqa: BLE001
                tb = traceback.format_exc()
                job.emit(tb, level="error")
                job.finish(ok=False, message=f"{kind} failed: {e}")

        asyncio.create_task(_runner())
        return job

    async def subscribe(self, job: Job) -> asyncio.Queue[JobLogLine | None]:
        q: asyncio.Queue[JobLogLine | None] = asyncio.Queue(maxsize=2000)
        # Replay existing log so a late subscriber sees history.
        for line in list(job.log):
            await q.put(line)
        if job.status != "running":
            await q.put(None)
        else:
            job.subscribers.append(q)
        return q


manager = JobManager()
