# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Browse routes: search, channel, related videos, cursor pagination."""
import json
import logging
import re

from fastapi import APIRouter, HTTPException, Query, Request, Response, Depends

from auth import require_auth, get_session
from helpers import VIDEO_ID_RE
from iterators import create_search, create_channel, create_channel_playlists, fetch_more
from directcalls import fetch_related, fetch_playlist_contents, resolve_handle

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

_COOKIE_MAX_AGE = 10 * 365 * 86400  # 10 years


def _json_with_cookie(data: dict, token: str, request: Request) -> Response:
    """Return a JSON response, setting session cookie if needed."""
    resp = Response(content=json.dumps(data), media_type='application/json')
    if request.cookies.get("pytr_session") != token:
        resp.set_cookie(
            key="pytr_session",
            value=token,
            max_age=_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax"
        )
    return resp


@router.get("/search")
async def search(request: Request, q: str = Query(..., min_length=1), auth: bool = Depends(require_auth)):
    """Search YouTube. Returns first batch + cursor for pagination."""
    token, session = get_session(request)

    try:
        results, cursor = await create_search(token, q)
    except Exception:
        raise HTTPException(status_code=500, detail="Search failed")

    return _json_with_cookie({'results': results, 'cursor': cursor}, token, request)


@router.get("/more")
async def more(request: Request, cursor: str = Query(..., min_length=1), auth: bool = Depends(require_auth)):
    """Fetch next batch using cursor. Expired/invalid cursor returns empty results."""
    token, session = get_session(request)

    try:
        results, next_cursor = await fetch_more(token, cursor)
    except Exception:
        results, next_cursor = [], None

    return _json_with_cookie({'results': results, 'cursor': next_cursor}, token, request)


_CHANNEL_ID_RE = re.compile(r'^UC[a-zA-Z0-9_-]{22}$')


@router.get("/related/{video_id}")
async def get_related_videos(video_id: str, auth: bool = Depends(require_auth)):
    """Get related videos for a video."""
    if not VIDEO_ID_RE.match(video_id):
        raise HTTPException(status_code=400, detail="Invalid video ID")
    results = await fetch_related(video_id)
    return {"results": results}


_HANDLE_RE = re.compile(r'^[a-zA-Z0-9_.\-]+$')


@router.get("/resolve-handle/{handle}")
async def resolve_channel_handle(handle: str, auth: bool = Depends(require_auth)):
    """Resolve a YouTube @handle to a channel ID."""
    if not _HANDLE_RE.match(handle):
        raise HTTPException(status_code=400, detail="Invalid handle")
    channel_id = await resolve_handle(handle)
    if not channel_id:
        raise HTTPException(status_code=404, detail="Channel not found")
    return {"channel_id": channel_id}


@router.get("/channel/{channel_id}")
async def get_channel_videos(
    request: Request,
    channel_id: str,
    auth: bool = Depends(require_auth)
):
    """Get videos from a channel. Returns first batch + cursor for pagination."""
    if not _CHANNEL_ID_RE.match(channel_id):
        raise HTTPException(status_code=400, detail="Invalid channel ID")
    token, session = get_session(request)

    try:
        channel_name, avatar_url, subscriber_count, results, cursor = await create_channel(token, channel_id)
    except Exception as e:
        log.error(f"Channel videos error: {e}")
        raise HTTPException(status_code=500, detail="Failed to load channel videos")

    return _json_with_cookie({
        'channel': channel_name,
        'channel_id': channel_id,
        'avatar_url': avatar_url,
        'subscriber_count': subscriber_count,
        'results': results,
        'cursor': cursor
    }, token, request)


@router.get("/channel/{channel_id}/playlists")
async def get_channel_playlists(
    request: Request,
    channel_id: str,
    auth: bool = Depends(require_auth)
):
    """Get playlists from a channel. Returns first batch + cursor for pagination."""
    if not _CHANNEL_ID_RE.match(channel_id):
        raise HTTPException(status_code=400, detail="Invalid channel ID")
    token, session = get_session(request)

    try:
        channel_name, results, cursor = await create_channel_playlists(token, channel_id)
    except Exception as e:
        log.error(f"Channel playlists error: {e}")
        raise HTTPException(status_code=500, detail="Failed to load channel playlists")

    return _json_with_cookie({
        'channel': channel_name,
        'channel_id': channel_id,
        'results': results,
        'cursor': cursor
    }, token, request)


_PLAYLIST_ID_RE = re.compile(r'^(PL|RD)[a-zA-Z0-9_-]+$')


@router.get("/playlist-contents")
async def get_playlist_contents(
    request: Request,
    video_id: str = Query(..., min_length=1),
    playlist_id: str = Query(..., min_length=1),
    auth: bool = Depends(require_auth)
):
    """Get playlist/mix contents (list of videos)."""
    if not VIDEO_ID_RE.match(video_id):
        raise HTTPException(status_code=400, detail="Invalid video ID")
    if not _PLAYLIST_ID_RE.match(playlist_id):
        raise HTTPException(status_code=400, detail="Invalid playlist ID")
    token, session = get_session(request)
    result = await fetch_playlist_contents(video_id, playlist_id)
    return _json_with_cookie(result, token, request)
