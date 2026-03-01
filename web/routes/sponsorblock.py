# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""SponsorBlock proxy — segment lookup with caching."""
import asyncio
import logging
import time

from fastapi import APIRouter, Depends

from auth import require_auth
from helpers import http_client, register_cleanup, make_cache_cleanup, VIDEO_ID_RE

log = logging.getLogger(__name__)

router = APIRouter()

_SB_BASE = "https://sponsor.ajay.app"
_SB_API = f"{_SB_BASE}/api/skipSegments"
_ALL_CATEGORIES = '["sponsor","intro","outro","selfpromo","interaction","preview","filler","music_offtopic","poi_highlight"]'

_cache: dict = {}  # video_id -> {"data": dict, "created": float}
_CACHE_TTL = 3600  # 1 hour

register_cleanup(make_cache_cleanup(_cache, _CACHE_TTL, "sponsorblock"))


async def warmup_connection():
    """Keep TLS connection to SponsorBlock API alive to avoid cold-start latency."""
    while True:
        try:
            await http_client.head(_SB_BASE)
            log.debug("SponsorBlock connection kept alive")
        except Exception:
            pass
        await asyncio.sleep(30)


@router.get("/api/sponsorblock/{video_id}")
async def get_segments(video_id: str, auth: bool = Depends(require_auth)):
    if not VIDEO_ID_RE.match(video_id):
        return {"segments": [], "highlight": None}

    cached = _cache.get(video_id)
    if cached and time.time() - cached["created"] < _CACHE_TTL:
        return cached["data"]

    try:
        resp = await http_client.get(
            _SB_API,
            params={"videoID": video_id, "categories": _ALL_CATEGORIES},
        )
        if resp.status_code == 404:
            result = {"segments": [], "highlight": None}
            _cache[video_id] = {"data": result, "created": time.time()}
            return result
        resp.raise_for_status()
        all_segments = resp.json()
    except Exception as e:
        log.warning(f"SponsorBlock API error: {e}")
        result = {"segments": [], "highlight": None}
        _cache[video_id] = {"data": result, "created": time.time()}
        return result

    segments = []
    highlight = None
    for seg in all_segments:
        action = seg.get("actionType", "skip")
        category = seg.get("category", "")
        if category == "poi_highlight" and action == "poi":
            s = seg.get("segment", [0])[0] if seg.get("segment") else 0
            highlight = {"timestamp": float(s)}
        elif action in ("skip", "mute"):
            segments.append({
                "segment": seg["segment"],
                "category": category,
                "actionType": action,
            })

    result = {"segments": segments, "highlight": highlight}
    _cache[video_id] = {"data": result, "created": time.time()}
    return result
