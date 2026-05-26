# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Generic enhance-list contract: rerank/filter/decorate any YT-sourced list.

The PYTR frontend hands us the baseline (already rendered). We never block,
we never reorder in-place — clients decide whether to surface decorations or
an alternate view.
"""
from __future__ import annotations

import logging

from .. import candidates
from ..db import cursor
from ..llm import get_embedding_backend
from ..profile_sync import fetch_profile_export, get_taste_profile
from ..ranking import centroid, cosine
from ..sources import transcript as transcript_src

log = logging.getLogger(__name__)

# Per-surface defaults — config is internal to the foryou container.
# All surfaces filter out unavailable videos and per-user never_again blocks.
SURFACE_CONFIG = {
    "related": {"mode": "rerank+decorate", "filter_spam": True, "channel_quota": 2},
    "search":  {"mode": "rerank+decorate", "filter_spam": False, "channel_quota": 3},
    "trending": {"mode": "decorate"},
    "playlist": {"mode": "passthrough"},  # playlist order is semantic; respect it
    "channel": {"mode": "decorate"},
}


def _dead_video_ids(ids: list[str], profile_uuid: str | None) -> set[str]:
    """Return the subset of ids known to be dead (negative transcript cache) or
    permanently blocked by the user (never_again feedback)."""
    if not ids:
        return set()
    out: set[str] = set()
    placeholders = ",".join("?" * len(ids))
    with cursor() as c:
        # Negative transcript cache means yt-dlp confirmed the video has no
        # captions OR the video itself is gone (transcript.py also wipes the
        # candidate row when it sees "Video unavailable", but the negative
        # cache row stays — that's what we leverage here).
        rows = c.execute(
            f"SELECT entity_id, payload FROM enrichment_cache "
            f"WHERE entity_type='transcript' AND source='youtube_captions' "
            f"AND entity_id IN ({placeholders})",
            ids,
        ).fetchall()
        for r in rows:
            try:
                import json as _json
                text = _json.loads(r["payload"]).get("text", "")
            except Exception:
                continue
            # Only treat as dead when we ALSO can't find a candidate row — the
            # negative cache hits both "video gone" AND "video alive but no
            # captions". A live-but-captionless video should still surface.
            if text == "":
                surviving = c.execute(
                    "SELECT 1 FROM candidates WHERE video_id = ?", (r["entity_id"],)
                ).fetchone()
                if not surviving:
                    out.add(r["entity_id"])
        if profile_uuid:
            rows = c.execute(
                f"SELECT video_id FROM feedback "
                f"WHERE profile_uuid=? AND signal='never_again' "
                f"AND video_id IN ({placeholders})",
                [profile_uuid, *ids],
            ).fetchall()
            out.update(r["video_id"] for r in rows)
    return out


async def _user_query_vec(profile_uuid: str) -> list[float]:
    tp = get_taste_profile(profile_uuid) or {}
    seeds_raw = tp.get("onboarding_seed_interests")
    import json as _json
    try:
        seeds = _json.loads(seeds_raw) if seeds_raw else []
    except Exception:
        seeds = []
    parts: list[str] = list(seeds)
    if tp.get("persona_text"):
        parts.append(tp["persona_text"])
    if not parts:
        export = await fetch_profile_export(profile_uuid) or {}
        for f in (export.get("favorites") or [])[:20]:
            if f.get("title"):
                parts.append(f["title"])
    if not parts:
        return []
    emb = get_embedding_backend()
    if not await emb.available():
        return []
    return centroid(await emb.embed(parts))


def _spam_signals(item: dict) -> int:
    """Crude spam heuristic: ALLCAPS-heavy title, view-count anomalies."""
    title = item.get("title", "") or ""
    if not title:
        return 0
    upper_ratio = sum(1 for c in title if c.isupper()) / max(1, sum(1 for c in title if c.isalpha()))
    spam = 0
    if upper_ratio > 0.55 and len(title) > 25:
        spam += 1
    if any(emoji in title for emoji in ["🔥🔥🔥", "❗❗", "💯💯"]):
        spam += 1
    return spam


async def enhance(
    profile_uuid: str,
    surface: str,
    context: dict,
    baseline: list[dict],
) -> dict:
    """Return {videos, decorations, removed} per the spec.

    Falls back to baseline pass-through on errors so PYTR never sees a degraded
    response. The frontend ignores the result when not opted in to enhancement.
    """
    cfg = SURFACE_CONFIG.get(surface, {"mode": "passthrough"})
    if not baseline:
        return {"videos": baseline, "decorations": {}, "removed": []}

    decorations: dict[str, dict] = {}
    removed: list[dict] = []

    # Always strip videos we know are dead / blocked, even for passthrough
    # surfaces. This is the cheap fix for stale items in PYTR's related sidebar.
    baseline_ids = [v["video_id"] for v in baseline if v.get("video_id")]
    dead = _dead_video_ids(baseline_ids, profile_uuid)
    if dead:
        removed.extend([{"video_id": v, "reason": "unavailable_or_blocked"} for v in dead])
        baseline = [v for v in baseline if v.get("video_id") not in dead]

    if cfg["mode"] == "passthrough":
        return {"videos": baseline, "decorations": {}, "removed": removed}

    # Spam filtering (related-list only by default).
    keep = list(baseline)
    if cfg.get("filter_spam"):
        # Use cached transcripts to rescue videos with shouty titles but real content.
        ids = [v["video_id"] for v in baseline if v.get("video_id")]
        rescues = set(transcript_src.cached_transcripts(ids).keys())
        spammy = []
        for v in baseline:
            if _spam_signals(v) >= 2 and v.get("video_id") not in rescues:
                spammy.append(v["video_id"])
        if spammy:
            removed.extend([{"video_id": vid, "reason": "spam_heuristic"} for vid in spammy])
            keep = [v for v in keep if v["video_id"] not in spammy]

    # Rerank if asked.
    videos = keep
    if "rerank" in cfg["mode"]:
        try:
            qvec = await _user_query_vec(profile_uuid)
            if qvec:
                # Embed each baseline item's title; lightweight when LLM unavail.
                emb = get_embedding_backend()
                if await emb.available():
                    texts = [(v.get("title") or "") for v in keep]
                    vecs = await emb.embed(texts)
                    scored = []
                    for v, vec in zip(keep, vecs):
                        sim = cosine(vec, qvec) if vec else 0.0
                        scored.append((sim, v))
                        decorations[v["video_id"]] = {
                            "score": round(sim, 3),
                            "tag": "ταιριάζει στα γούστα σου" if sim > 0.55 else None,
                        }
                    scored.sort(key=lambda p: p[0], reverse=True)
                    videos = [v for _, v in scored]
        except Exception as e:
            log.warning("enhance %s rerank failed: %s — passthrough", surface, e)
            videos = keep

    return {"videos": videos, "decorations": decorations, "removed": removed}
