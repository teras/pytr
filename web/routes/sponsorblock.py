# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""SponsorBlock proxy — privacy-preserving segment lookup with caching."""
import hashlib
import logging
import time

from fastapi import APIRouter, Depends

from auth import require_auth
from helpers import http_client, register_cleanup, make_cache_cleanup, VIDEO_ID_RE

log = logging.getLogger(__name__)

router = APIRouter()

_SB_API = "https://sponsor.ajay.app/api/skipSegments"
_ALL_CATEGORIES = '["sponsor","intro","outro","selfpromo","interaction","preview","filler","music_offtopic","poi_highlight"]'

_cache: dict = {}  # video_id -> {"data": dict, "created": float}
_CACHE_TTL = 3600  # 1 hour

register_cleanup(make_cache_cleanup(_cache, _CACHE_TTL, "sponsorblock"))


@router.get("/api/sponsorblock/{video_id}")
async def get_segments(video_id: str, auth: bool = Depends(require_auth)):
    if not VIDEO_ID_RE.match(video_id):
        return {"segments": [], "highlight": None}

    cached = _cache.get(video_id)
    if cached and time.time() - cached["created"] < _CACHE_TTL:
        return cached["data"]

    # SHA-256 hash prefix lookup (privacy-preserving)
    sha = hashlib.sha256(video_id.encode()).hexdigest()
    prefix = sha[:4]

    try:
        resp = await http_client.get(
            f"{_SB_API}/{prefix}",
            params={"categories": _ALL_CATEGORIES},
        )
        if resp.status_code == 404:
            result = {"segments": [], "highlight": None}
            _cache[video_id] = {"data": result, "created": time.time()}
            return result
        resp.raise_for_status()
        all_entries = resp.json()
    except Exception as e:
        log.warning(f"SponsorBlock API error: {e}")
        result = {"segments": [], "highlight": None}
        _cache[video_id] = {"data": result, "created": time.time()}
        return result

    # Filter to exact video match
    match = None
    for entry in all_entries:
        if entry.get("videoID") == video_id:
            match = entry
            break

    if not match:
        result = {"segments": [], "highlight": None}
        _cache[video_id] = {"data": result, "created": time.time()}
        return result

    segments = []
    highlight = None
    for seg in match.get("segments", []):
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
