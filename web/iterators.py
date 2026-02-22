# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cursor-based pagination for search and channel browsing.

Uses InnerTube continuation tokens (~200 bytes each) instead of holding
yt-dlp generators (~3.5 MB each) in memory.
"""

import logging
import secrets
import time
from dataclasses import dataclass, field

from helpers import register_cleanup
from directcalls import (search_first, search_next, channel_first, channel_next,
                         channel_playlists_first, channel_playlists_next)

log = logging.getLogger(__name__)

_CURSOR_TTL = 5 * 3600  # 5 hours
_MAX_ENTRIES = 1_000

# In-memory cursor store: session_token -> {cursor_id -> CursorState}
_CURSORS: dict[str, dict[str, "CursorState"]] = {}


@dataclass
class CursorState:
    type: str                       # "search" or "channel"
    continuation_token: str | None  # YouTube's InnerTube token (~200 bytes)
    last_access: float = field(default_factory=time.time)
    channel_name: str | None = None
    pulled: int = 0


def _get_bucket(session_token: str) -> dict[str, CursorState]:
    if session_token not in _CURSORS:
        _CURSORS[session_token] = {}
    return _CURSORS[session_token]


async def create_search(session_token: str, query: str) -> tuple[list[dict], str | None]:
    """Search YouTube and return (first_batch, cursor_id)."""
    results, yt_token = await search_first(query)

    if not results:
        return [], None

    if not yt_token:
        return results, None

    cursor_id = secrets.token_urlsafe(16)
    _get_bucket(session_token)[cursor_id] = CursorState(
        type="search",
        continuation_token=yt_token,
        pulled=len(results),
    )
    return results, cursor_id


async def create_channel(session_token: str, channel_id: str) -> tuple[str, str, list[dict], str | None]:
    """Get channel videos and return (channel_name, avatar_url, first_batch, cursor_id)."""
    channel_name, avatar_url, results, yt_token = await channel_first(channel_id)

    if not results:
        return channel_name, avatar_url, [], None

    if not yt_token:
        return channel_name, avatar_url, results, None

    cursor_id = secrets.token_urlsafe(16)
    _get_bucket(session_token)[cursor_id] = CursorState(
        type="channel",
        continuation_token=yt_token,
        channel_name=channel_name,
        pulled=len(results),
    )
    return channel_name, avatar_url, results, cursor_id


async def create_channel_playlists(session_token: str, channel_id: str) -> tuple[str, list[dict], str | None]:
    """Get channel playlists and return (channel_name, first_batch, cursor_id)."""
    channel_name, results, yt_token = await channel_playlists_first(channel_id)

    if not results:
        return channel_name, [], None

    if not yt_token:
        return channel_name, results, None

    cursor_id = secrets.token_urlsafe(16)
    _get_bucket(session_token)[cursor_id] = CursorState(
        type="channel_playlists",
        continuation_token=yt_token,
        channel_name=channel_name,
        pulled=len(results),
    )
    return channel_name, results, cursor_id


async def fetch_more(session_token: str, cursor_id: str) -> tuple[list[dict], str | None]:
    """Fetch next batch using a cursor ID.

    Returns (results, cursor_id | None). Expired/missing cursor returns ([], None).
    """
    bucket = _CURSORS.get(session_token, {})
    state = bucket.get(cursor_id)
    if not state or not state.continuation_token:
        bucket.pop(cursor_id, None)
        return [], None

    if state.pulled >= _MAX_ENTRIES:
        bucket.pop(cursor_id, None)
        return [], None

    state.last_access = time.time()

    if state.type == "search":
        results, yt_token = await search_next(state.continuation_token)
    elif state.type == "channel":
        results, yt_token = await channel_next(state.continuation_token)
    elif state.type == "channel_playlists":
        results, yt_token = await channel_playlists_next(state.continuation_token)
    else:
        bucket.pop(cursor_id, None)
        return [], None

    state.pulled += len(results)
    state.continuation_token = yt_token

    if not yt_token or state.pulled >= _MAX_ENTRIES:
        bucket.pop(cursor_id, None)
        return results, None

    return results, cursor_id


def _cleanup_cursors():
    """Remove expired cursors."""
    now = time.time()
    total = 0
    empty_tokens = []
    for token, bucket in _CURSORS.items():
        expired = [k for k, v in bucket.items()
                   if now - v.last_access > _CURSOR_TTL]
        for k in expired:
            del bucket[k]
        total += len(expired)
        if not bucket:
            empty_tokens.append(token)
    for token in empty_tokens:
        del _CURSORS[token]
    if total:
        log.info(f"Cleaned {total} expired cursor(s)")


register_cleanup(_cleanup_cursors)
