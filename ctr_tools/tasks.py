"""
ctr_tools/tasks.py

A tiny, Qt-free registry of the background work the app runs, so the F12
Inspector can show it in order (queued / running / done) with timings, and so
the user can ask a job to *stop after the task that is running now*.

Stopping is cooperative on purpose: a worker only ever looks at
`job.stop_requested` *between* its tasks — never inside one — so a file that
is being written is always finished, never cut off halfway. A task that has
already started always runs to the end.

Thread-safety: every mutation takes the registry lock; the UI reads
snapshots through `TASKS.jobs()`.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

QUEUED, RUNNING, DONE, FAILED, SKIPPED = "queued", "running", "done", "failed", "skipped"

_MAX_JOBS_KEPT = 30


@dataclass
class Step:
    name: str
    status: str = QUEUED
    started: float | None = None
    ended: float | None = None
    detail: str = ""

    @property
    def seconds(self) -> float | None:
        if self.started is None:
            return None
        return (self.ended if self.ended is not None else time.time()) - self.started


@dataclass
class Job:
    label: str
    steps: list[Step]
    stoppable: bool = True
    # Tasks all start at once (e.g. the pricebook load pool), so by the time
    # Stop is pressed there is usually nothing left to skip.
    parallel: bool = False
    created: float = field(default_factory=time.time)
    stop_requested: bool = False
    finished: float | None = None

    def __post_init__(self) -> None:
        self._lock = TASKS._lock

    # ── worker side ──────────────────────────────────────────────────────────

    def should_stop(self) -> bool:
        """True once a stop was requested. Call only *between* tasks."""
        return self.stoppable and self.stop_requested

    def start(self, index: int) -> None:
        with self._lock:
            step = self.steps[index]
            step.status, step.started = RUNNING, time.time()

    def done(self, index: int, detail: str = "") -> None:
        with self._lock:
            step = self.steps[index]
            step.status, step.ended, step.detail = DONE, time.time(), detail

    def failed(self, index: int, detail: str = "") -> None:
        with self._lock:
            step = self.steps[index]
            step.status, step.ended, step.detail = FAILED, time.time(), detail

    def skip_remaining(self, from_index: int, detail: str = "stopped") -> None:
        """Marks every step from `from_index` that has not started as skipped."""
        with self._lock:
            for step in self.steps[from_index:]:
                if step.status == QUEUED:
                    step.status, step.detail = SKIPPED, detail

    def skip(self, index: int, detail: str = "stopped") -> None:
        with self._lock:
            step = self.steps[index]
            if step.status == QUEUED:
                step.status, step.detail = SKIPPED, detail

    def finish(self) -> None:
        with self._lock:
            self.finished = time.time()

    # ── UI side ──────────────────────────────────────────────────────────────

    def request_stop(self) -> None:
        with self._lock:
            if self.stoppable and self.finished is None:
                self.stop_requested = True

    @property
    def active(self) -> bool:
        return self.finished is None

    @property
    def can_stop(self) -> bool:
        """Something is still queued, so a stop would actually save work."""
        return (self.stoppable and self.finished is None and not self.stop_requested
                and any(s.status == QUEUED for s in self.steps))

    @property
    def status(self) -> str:
        if self.finished is None:
            if self.stop_requested:
                return "stopping after current task"
            return RUNNING if any(s.status == RUNNING for s in self.steps) else QUEUED
        if any(s.status == FAILED for s in self.steps):
            return FAILED
        if any(s.status == SKIPPED for s in self.steps):
            return "stopped"
        return DONE

    @property
    def seconds(self) -> float:
        return (self.finished if self.finished is not None else time.time()) - self.created


class TaskRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: list[Job] = []

    def begin_job(self, label: str, step_names: list[str], stoppable: bool = True,
                  parallel: bool = False) -> Job:
        job = Job(label=label, steps=[Step(n) for n in step_names], stoppable=stoppable,
                  parallel=parallel)
        with self._lock:
            self._jobs.append(job)
            del self._jobs[:-_MAX_JOBS_KEPT]
        return job

    def jobs(self) -> list[Job]:
        with self._lock:
            return list(self._jobs)

    def stop_all(self) -> int:
        """Asks every active job to stop after its current task. Returns how many."""
        n = 0
        for job in self.jobs():
            if job.can_stop:
                job.request_stop()
                n += 1
        return n

    def can_stop_any(self) -> bool:
        return any(job.can_stop for job in self.jobs())

    def clear_finished(self) -> None:
        with self._lock:
            self._jobs = [j for j in self._jobs if j.active]


TASKS = TaskRegistry()
