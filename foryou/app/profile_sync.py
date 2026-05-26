# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fetch profile data from PYTR via /api/internal/profile-export."""
from __future__ import annotations

import logging
import time

import httpx

from .config import PYTR_INTERNAL_URL
from .db import cursor
from .egress import get_client

log = logging.getLogger(__name__)


async def fetch_profile_export(profile_uuid: str) -> dict | None:
    try:
        r = await get_client().get(
            f"{PYTR_INTERNAL_URL}/api/internal/profile-export/{profile_uuid}",
            timeout=10.0,
        )
        if r.status_code != 200:
            log.warning("profile-export %s returned %s", profile_uuid, r.status_code)
            return None
        return r.json()
    except httpx.HTTPError as e:
        log.warning("profile-export %s failed: %s", profile_uuid, e)
        return None


def ensure_taste_profile(profile_uuid: str, timezone: str | None = None,
                         privacy_mode: str = "balanced") -> None:
    """Insert an empty row in taste_profiles if missing."""
    now = int(time.time())
    with cursor(write=True) as c:
        c.execute(
            "INSERT OR IGNORE INTO taste_profiles (profile_uuid, timezone, privacy_mode, last_refreshed_at) "
            "VALUES (?, ?, ?, ?)",
            (profile_uuid, timezone, privacy_mode, now),
        )


def get_taste_profile(profile_uuid: str) -> dict | None:
    with cursor() as c:
        r = c.execute(
            "SELECT * FROM taste_profiles WHERE profile_uuid = ?", (profile_uuid,)
        ).fetchone()
    return dict(r) if r else None


def set_privacy_mode(profile_uuid: str, mode: str):
    with cursor(write=True) as c:
        c.execute(
            "UPDATE taste_profiles SET privacy_mode = ? WHERE profile_uuid = ?",
            (mode, profile_uuid),
        )


def mark_onboarding_complete(profile_uuid: str, seeds: list[str], persona_text: str | None,
                             persona_source: str | None):
    import json as _json
    now = int(time.time())
    with cursor(write=True) as c:
        c.execute(
            "UPDATE taste_profiles SET onboarding_seed_interests = ?, onboarding_completed_at = ?, "
            "persona_text = COALESCE(?, persona_text), persona_source = COALESCE(?, persona_source) "
            "WHERE profile_uuid = ?",
            (_json.dumps(seeds), now, persona_text, persona_source, profile_uuid),
        )


def compute_signal_richness(history_videos: int, history_channels: int) -> tuple[float, str]:
    """Inverse-confidence richness: tiny histories get high exploration."""
    import math
    score = max(0.0, math.log10(max(1, history_videos) / 30.0)) + \
            max(0.0, math.log10(max(1, history_channels) / 10.0))
    score = max(0.0, min(1.0, score))
    if score < 0.2:
        state = "cold"
    elif score < 0.7:
        state = "warm"
    else:
        state = "mature"
    return score, state
