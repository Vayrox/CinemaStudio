"""In-process job runner with live log streaming.

Each project has at most one active job. Jobs broadcast log lines to any number
of SSE subscribers and persist them to `projects/<slug>/job.log` so a refresh
recovers history.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable


JobFn = Callable[["JobContext"], Awaitable[Any]]


@dataclass
class JobContext:
    slug: str
    project_dir: Path
    log: Callable[[str, str], None]  # (level, message)


@dataclass
class _JobState:
    slug: str
    kind: str
    status: str = "pending"  # pending | running | succeeded | failed
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    error: str | None = None
    history: deque = field(default_factory=lambda: deque(maxlen=2000))
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    task: asyncio.Task | None = None


class JobRunner:
    """One active job per slug. Multiple SSE subscribers per job."""

    def __init__(self, projects_root: Path):
        self.projects_root = projects_root
        self._jobs: dict[str, _JobState] = {}
        self._lock = asyncio.Lock()

    def status(self, slug: str) -> dict[str, Any] | None:
        st = self._jobs.get(slug)
        if not st:
            return None
        return {
            "slug": st.slug,
            "kind": st.kind,
            "status": st.status,
            "started_at": st.started_at,
            "finished_at": st.finished_at,
            "error": st.error,
        }

    def history(self, slug: str) -> list[dict[str, Any]]:
        st = self._jobs.get(slug)
        if not st:
            return []
        return list(st.history)

    async def start(self, slug: str, kind: str, fn: JobFn) -> bool:
        """Start a new job for slug. Returns False if one is already running."""
        async with self._lock:
            existing = self._jobs.get(slug)
            if existing and existing.status == "running":
                return False
            project_dir = self.projects_root / slug
            project_dir.mkdir(parents=True, exist_ok=True)
            state = _JobState(slug=slug, kind=kind, status="running")
            self._jobs[slug] = state

        log_path = project_dir / "job.log"
        log_path.write_text("")  # truncate per run

        def emit(level: str, message: str) -> None:
            entry = {"ts": time.time(), "level": level, "msg": message}
            state.history.append(entry)
            try:
                with log_path.open("a") as f:
                    f.write(json.dumps(entry) + "\n")
            except Exception:
                pass
            for q in list(state.subscribers):
                try:
                    q.put_nowait(entry)
                except asyncio.QueueFull:
                    pass

        ctx = JobContext(slug=slug, project_dir=project_dir, log=emit)
        emit("info", f"Job started: {kind}")

        async def run() -> None:
            try:
                await fn(ctx)
                state.status = "succeeded"
                emit("success", "Job completed.")
            except asyncio.CancelledError:
                state.status = "failed"
                state.error = "cancelled"
                emit("error", "Job cancelled.")
                raise
            except Exception as exc:  # noqa: BLE001
                state.status = "failed"
                msg = str(exc) or repr(exc)
                state.error = f"{type(exc).__name__}: {msg}"
                emit("error", f"Job failed: {state.error}")
            finally:
                state.finished_at = time.time()
                # Sentinel so subscribers can close cleanly.
                for q in list(state.subscribers):
                    try:
                        q.put_nowait(None)
                    except asyncio.QueueFull:
                        pass

        state.task = asyncio.create_task(run())
        return True

    async def cancel(self, slug: str) -> bool:
        st = self._jobs.get(slug)
        if not st or not st.task or st.task.done():
            return False
        st.task.cancel()
        try:
            await st.task
        except (asyncio.CancelledError, Exception):
            pass
        return True

    async def subscribe(self, slug: str) -> AsyncIterator[dict[str, Any] | None]:
        """Yield prior history immediately, then live entries until the job ends."""
        st = self._jobs.get(slug)
        if not st:
            return
        for entry in list(st.history):
            yield entry
        if st.status != "running":
            yield None
            return
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        st.subscribers.append(q)
        try:
            while True:
                entry = await q.get()
                yield entry
                if entry is None:
                    return
        finally:
            try:
                st.subscribers.remove(q)
            except ValueError:
                pass
