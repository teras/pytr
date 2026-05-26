# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Candidate-pool CRUD: write fetched videos, mark surfaced, prune old."""
from __future__ import annotations

import json
import time

from .db import cursor


def upsert(video: dict, source: str, source_meta: dict | None = None):
    """video must contain at least video_id and title.

    Filters applied at this single chokepoint (every source funnels through):
    * YouTube Shorts: any video <= 70 seconds, or explicitly tagged.
    * Globally-tombstoned dead videos: anything previously reported as
      unavailable. We DO NOT re-add them even when Reddit / RSS / Tournesol
      keep echoing the URL.
    """
    vid = video.get("video_id")
    if not vid:
        return
    dur = video.get("duration_seconds")
    if dur is not None and 0 < dur <= 70:
        return
    if (source_meta or {}).get("is_short"):
        return
    if is_tombstoned(vid):
        return
    now = int(time.time())
    with cursor(write=True) as c:
        c.execute(
            """INSERT INTO candidates
               (video_id, title, channel_id, channel_name, published_at, duration_seconds,
                view_count, description, thumbnail_url, source, source_meta, fetched_at, quality_score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(video_id) DO UPDATE SET
                  title = excluded.title,
                  channel_id = COALESCE(excluded.channel_id, candidates.channel_id),
                  channel_name = COALESCE(excluded.channel_name, candidates.channel_name),
                  published_at = COALESCE(excluded.published_at, candidates.published_at),
                  duration_seconds = COALESCE(excluded.duration_seconds, candidates.duration_seconds),
                  view_count = COALESCE(excluded.view_count, candidates.view_count),
                  thumbnail_url = COALESCE(excluded.thumbnail_url, candidates.thumbnail_url),
                  source_meta = COALESCE(excluded.source_meta, candidates.source_meta),
                  quality_score = COALESCE(excluded.quality_score, candidates.quality_score)
            """,
            (
                video["video_id"],
                video.get("title", ""),
                video.get("channel_id"),
                video.get("channel_name"),
                video.get("published_at"),
                video.get("duration_seconds"),
                video.get("view_count"),
                video.get("description"),
                video.get("thumbnail_url"),
                source,
                json.dumps(source_meta) if source_meta else None,
                now,
                video.get("quality_score"),
            ),
        )


def by_source(source: str, limit: int = 200) -> list[dict]:
    with cursor() as c:
        rows = c.execute(
            "SELECT * FROM candidates WHERE source = ? ORDER BY published_at DESC NULLS LAST LIMIT ?",
            (source, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def fetch_many(video_ids: list[str]) -> dict[str, dict]:
    if not video_ids:
        return {}
    placeholders = ",".join("?" * len(video_ids))
    with cursor() as c:
        rows = c.execute(
            f"SELECT * FROM candidates WHERE video_id IN ({placeholders})", video_ids
        ).fetchall()
    return {r["video_id"]: dict(r) for r in rows}


def mark_surfaced(video_ids: list[str]):
    if not video_ids:
        return
    now = int(time.time())
    placeholders = ",".join("?" * len(video_ids))
    with cursor(write=True) as c:
        c.execute(
            f"UPDATE candidates SET last_used_at = ? WHERE video_id IN ({placeholders})",
            [now, *video_ids],
        )


def set_embedding(video_id: str, vec: list[float]):
    import struct
    blob = struct.pack(f"{len(vec)}f", *vec)
    with cursor(write=True) as c:
        c.execute("UPDATE candidates SET embedding = ? WHERE video_id = ?", (blob, video_id))


def unpack_embedding(blob: bytes | None) -> list[float]:
    if not blob:
        return []
    import struct
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def tombstone(video_id: str, reason: str = ""):
    """Mark a video as permanently dead. Future upsert() calls will refuse it
    no matter which source rediscovers the URL. Stored in enrichment_cache so
    it shares the same table + cleanup policy as other persistent state.
    """
    import json as _json
    import time as _time
    with cursor(write=True) as c:
        c.execute(
            "INSERT OR REPLACE INTO enrichment_cache "
            "(entity_type, entity_id, source, payload, fetched_at) VALUES (?, ?, ?, ?, ?)",
            ("tombstone", video_id, "unavailable",
             _json.dumps({"reason": reason[:200]}), int(_time.time())),
        )


def is_tombstoned(video_id: str) -> bool:
    with cursor() as c:
        r = c.execute(
            "SELECT 1 FROM enrichment_cache WHERE entity_type='tombstone' "
            "AND source='unavailable' AND entity_id = ?", (video_id,)
        ).fetchone()
    return r is not None


def cleanup_stale(older_than_days: int = 90):
    cutoff = int(time.time()) - older_than_days * 86400
    with cursor(write=True) as c:
        c.execute(
            "DELETE FROM candidates WHERE last_used_at IS NOT NULL AND last_used_at < ?",
            (cutoff,),
        )
