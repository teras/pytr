# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scheduled jobs: source_poll, sessionize, feed_generate, cleanup, hit_rate."""
from __future__ import annotations

import json
import logging
import time

from .. import candidates
from ..db import cursor
from ..feeds.engine import generate_feed, list_feeds
from ..profile_sync import compute_signal_richness, fetch_profile_export, get_taste_profile

log = logging.getLogger(__name__)


async def bootstrap_candidates():
    """Pre-fill the candidate pool with universal, non-personalised content.

    Runs on container boot (via runner.trigger_oneshot in main.py) AND weekly.
    Ensures the candidate table never starts empty — so feed generation right
    after onboarding has something to rank instead of returning zero items.
    """
    from ..sources.tournesol import TournesolSource
    from ..sources.ytdlp import YtDlpChartsSource
    from ..sources.reddit_hn import RedditSource, HNAlgoliaSource
    mode = "balanced"  # bootstrap uses the most permissive shared mode
    log.info("bootstrap_candidates: starting")
    # Pull each major language separately so the global pool isn't dominated
    # by one regional bias. EN goes first (largest YT corpus by far).
    for lang in ("en", "el"):
        try:
            n_t = await TournesolSource().fetch_top(mode=mode, language=lang, limit=40)
            log.info("bootstrap_candidates: tournesol %s +%d", lang, n_t)
        except Exception as e:
            log.warning("bootstrap tournesol %s failed: %s", lang, e)
    for country in ("US", "GB", "DE"):
        try:
            n_c = await YtDlpChartsSource().charts(mode=mode, country=country, limit=30)
            log.info("bootstrap_candidates: trending %s +%d", country, n_c)
        except Exception as e:
            log.warning("bootstrap trending %s failed: %s", country, e)
    try:
        n_r = await RedditSource().fetch(mode=mode, limit=25)
        log.info("bootstrap_candidates: reddit +%d", n_r)
    except Exception as e:
        log.warning("bootstrap reddit failed: %s", e)
    try:
        n_h = await HNAlgoliaSource().fetch(mode=mode, limit=25)
        log.info("bootstrap_candidates: hn +%d", n_h)
    except Exception as e:
        log.warning("bootstrap hn failed: %s", e)
    log.info("bootstrap_candidates: done")


async def source_poll():
    """Refresh RSS for every followed channel across all profiles."""
    from ..sources.rss import ChannelRSSSource
    with cursor() as c:
        rows = c.execute("SELECT profile_uuid, privacy_mode FROM taste_profiles").fetchall()
    rss = ChannelRSSSource()
    seen = set()
    for r in rows:
        export = await fetch_profile_export(r["profile_uuid"]) or {}
        for ch in export.get("followed_channels") or []:
            cid = ch.get("channel_id")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            await rss.fetch_channel(cid, mode=r["privacy_mode"] or "balanced")
    log.info("source_poll RSS done channels=%d", len(seen))


async def taste_refresh():
    """Recompute signal_richness + sparsity_state from PYTR history."""
    with cursor() as c:
        rows = c.execute("SELECT profile_uuid FROM taste_profiles").fetchall()
    for r in rows:
        export = await fetch_profile_export(r["profile_uuid"]) or {}
        hist = export.get("history") or []
        videos = len({h.get("video_id") for h in hist if h.get("video_id")})
        chans = len({h.get("channel_id") for h in hist if h.get("channel_id")})
        score, state = compute_signal_richness(videos, chans)
        with cursor(write=True) as c:
            c.execute(
                "UPDATE taste_profiles SET signal_richness=?, sparsity_state=?, last_refreshed_at=? "
                "WHERE profile_uuid=?",
                (score, state, int(time.time()), r["profile_uuid"]),
            )


async def feed_generate_all():
    """Regenerate every feed in the system. Heavy; cadence is conservative."""
    with cursor() as c:
        rows = c.execute(
            "SELECT feed_id, profile_uuid, kind FROM feed_defs"
        ).fetchall()
    for r in rows:
        try:
            await generate_feed(r["feed_id"])
        except Exception as e:
            log.exception("regen feed %s failed: %s", r["feed_id"], e)


async def sessionize():
    """Derive session_id + completion_pct from PYTR history (rough but useful)."""
    with cursor() as c:
        rows = c.execute("SELECT profile_uuid, timezone FROM taste_profiles").fetchall()
    for r in rows:
        export = await fetch_profile_export(r["profile_uuid"]) or {}
        hist = sorted(export.get("history") or [], key=lambda h: h.get("watched_at") or 0)
        if not hist:
            continue
        session_id = None
        last_ts = 0
        for h in hist:
            ts = float(h.get("watched_at") or 0)
            if ts - last_ts > 30 * 60 or session_id is None:
                session_id = f"{r['profile_uuid']}-{int(ts)}"
            last_ts = ts
            dur = float(h.get("duration") or 0)
            pos = float(h.get("position") or 0)
            completion = min(1.0, pos / dur) if dur > 0 else None
            tod = _tod_bucket(ts)
            dow = _dow_label(ts)
            with cursor(write=True) as c:
                c.execute(
                    "INSERT OR REPLACE INTO latent_signals "
                    "(profile_uuid, video_id, completion_pct, time_of_day_bucket, day_of_week, "
                    " session_id, last_watched_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (r["profile_uuid"], h.get("video_id") or "", completion, tod, dow,
                     session_id, int(ts)),
                )


def _tod_bucket(ts: float) -> str:
    import datetime
    h = datetime.datetime.fromtimestamp(ts).hour
    if 5 <= h < 12:
        return "morning"
    if 12 <= h < 17:
        return "afternoon"
    if 17 <= h < 22:
        return "evening"
    return "night"


def _dow_label(ts: float) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(ts).strftime("%a")


async def cleanup():
    candidates.cleanup_stale(older_than_days=90)
    cutoff = int(time.time()) - 90 * 86400
    with cursor(write=True) as c:
        # impressions_daily uses YYYYMMDD ints; convert cutoff.
        import datetime
        cutoff_day = int(datetime.datetime.fromtimestamp(cutoff).strftime("%Y%m%d"))
        c.execute("DELETE FROM impressions_daily WHERE day < ?", (cutoff_day,))


async def hit_rate_snapshot():
    now = int(time.time())
    cutoff = now - 30 * 86400
    import datetime
    cutoff_day = int(datetime.datetime.fromtimestamp(cutoff).strftime("%Y%m%d"))
    with cursor() as c:
        rows = c.execute(
            "SELECT profile_uuid, SUM(shown_count) shown, SUM(clicked_count) clicked, "
            "SUM(watched_30s_count) watched FROM impressions_daily WHERE day >= ? "
            "GROUP BY profile_uuid",
            (cutoff_day,),
        ).fetchall()
    for r in rows:
        with cursor(write=True) as c:
            c.execute(
                "INSERT OR REPLACE INTO hit_rate_history "
                "(profile_uuid, period_start, period_end, shown, clicked, watched_30s) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (r["profile_uuid"], cutoff, now, r["shown"] or 0, r["clicked"] or 0,
                 r["watched"] or 0),
            )


def register_all():
    from . import runner
    runner.register("source_poll", source_poll, cadence_sec=6 * 3600)
    runner.register("taste_refresh", taste_refresh, cadence_sec=7 * 86400)
    runner.register("feed_generate_all", feed_generate_all, cadence_sec=4 * 3600)
    runner.register("sessionize", sessionize, cadence_sec=3600)
    runner.register("cleanup", cleanup, cadence_sec=86400)
    runner.register("hit_rate_snapshot", hit_rate_snapshot, cadence_sec=86400)
    # Refresh the global candidate pool weekly. The boot-time prime is triggered
    # as a one-shot from main.py so the very first run also benefits.
    runner.register("bootstrap_candidates", bootstrap_candidates, cadence_sec=7 * 86400)
