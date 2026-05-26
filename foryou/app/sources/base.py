# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Source adapter pattern: error budget + quiet failure."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class SourceHealth:
    failure_count: int = 0
    backoff_until: float = 0.0
    last_error: str = ""


class Source:
    """Adapters subclass this. Quietly drops out of the pool on repeated failure."""

    name: str = "abstract"
    purpose: str = "abstract"  # for the egress gate

    def __init__(self):
        self.health = SourceHealth()

    def in_backoff(self) -> bool:
        return time.time() < self.health.backoff_until

    def record_failure(self, err: str):
        self.health.failure_count += 1
        self.health.last_error = err
        # Exponential backoff capped at 1 hour.
        self.health.backoff_until = time.time() + min(3600.0, 60.0 * (2 ** min(6, self.health.failure_count)))
        log.warning("source %s failure (%s) → backoff %ss", self.name, err, int(self.health.backoff_until - time.time()))

    def record_success(self):
        self.health.failure_count = 0
        self.health.last_error = ""
        self.health.backoff_until = 0.0
