# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Single-thread background job runner.

Jobs are serialised so SQLite writes never race. Each job carries a cadence
(seconds between runs) and last_run timestamp.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

log = logging.getLogger(__name__)


@dataclass
class JobSpec:
    name: str
    fn: Callable[[], Awaitable[None]]
    cadence_sec: int
    last_run: float = 0.0
    enabled: bool = True
    next_run_override: float | None = field(default=None)  # for on-demand triggers


_jobs: dict[str, JobSpec] = {}
_pending_oneshots: list[Callable[[], Awaitable[None]]] = []
_runner_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None


def register(name: str, fn: Callable[[], Awaitable[None]], cadence_sec: int):
    _jobs[name] = JobSpec(name=name, fn=fn, cadence_sec=cadence_sec)


def trigger_now(name: str):
    """Run this job at the next loop iteration regardless of cadence."""
    if name in _jobs:
        _jobs[name].next_run_override = time.time()


def trigger_oneshot(fn: Callable[[], Awaitable[None]]):
    """Queue an ad-hoc async function (e.g. on-demand feed refresh)."""
    _pending_oneshots.append(fn)


async def _loop():
    assert _stop_event is not None
    while not _stop_event.is_set():
        # one-shots first
        if _pending_oneshots:
            fn = _pending_oneshots.pop(0)
            try:
                await fn()
            except Exception as e:
                log.exception("oneshot failed: %s", e)
        now = time.time()
        ran = False
        for spec_ in list(_jobs.values()):
            if not spec_.enabled:
                continue
            due = spec_.next_run_override is not None and spec_.next_run_override <= now
            due = due or (now - spec_.last_run >= spec_.cadence_sec)
            if due:
                spec_.next_run_override = None
                spec_.last_run = now
                try:
                    await spec_.fn()
                except Exception as e:
                    log.exception("job %s failed: %s", spec_.name, e)
                ran = True
                break  # only one job per tick → strict serialisation
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=2 if not ran else 0.1)
        except asyncio.TimeoutError:
            pass


def start():
    global _runner_task, _stop_event
    if _runner_task is not None:
        return
    _stop_event = asyncio.Event()
    _runner_task = asyncio.create_task(_loop())
    log.info("job runner started")


async def stop():
    global _runner_task, _stop_event
    if _stop_event is not None:
        _stop_event.set()
    if _runner_task is not None:
        try:
            await _runner_task
        except Exception:
            pass
    _runner_task = None
    _stop_event = None
