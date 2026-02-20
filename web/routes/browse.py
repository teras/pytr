"""Browse routes: search, channel, related videos, cursor pagination."""
import asyncio
import json as json_module
import logging

from fastapi import APIRouter, HTTPException, Query, Request, Response, Depends

from auth import require_auth, get_session
from iterators import create_search, create_channel, fetch_more
from directcalls import fetch_related

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


def _set_session_cookie(response: Response, token: str, request: Request):
    """Set session cookie if not already present."""
    if request.cookies.get("ytp_session") != token:
        response.set_cookie(
            key="ytp_session",
            value=token,
            max_age=24 * 3600,
            httponly=True,
            samesite="lax"
        )


@router.get("/search")
async def search(request: Request, q: str = Query(..., min_length=1), auth: bool = Depends(require_auth)):
    """Search YouTube. Returns first batch + cursor for pagination."""
    token, session = get_session(request)

    try:
        results, cursor = await asyncio.to_thread(create_search, session, q)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    resp = Response(
        content=json_module.dumps({'results': results, 'cursor': cursor}),
        media_type='application/json'
    )
    _set_session_cookie(resp, token, request)
    return resp


@router.get("/more")
async def more(request: Request, cursor: str = Query(..., min_length=1), auth: bool = Depends(require_auth)):
    """Fetch next batch using cursor. Expired/invalid cursor returns empty results."""
    token, session = get_session(request)

    try:
        results, next_cursor = await asyncio.to_thread(fetch_more, session, cursor)
    except Exception:
        results, next_cursor = [], None

    resp = Response(
        content=json_module.dumps({'results': results, 'cursor': next_cursor}),
        media_type='application/json'
    )
    _set_session_cookie(resp, token, request)
    return resp


@router.get("/related/{video_id}")
async def get_related_videos(video_id: str, auth: bool = Depends(require_auth)):
    """Get related videos for a video."""
    results = await fetch_related(video_id)
    return {"results": results}


@router.get("/channel/{channel_id}")
async def get_channel_videos(
    request: Request,
    channel_id: str,
    auth: bool = Depends(require_auth)
):
    """Get videos from a channel. Returns first batch + cursor for pagination."""
    token, session = get_session(request)

    try:
        channel_name, results, cursor = await asyncio.to_thread(create_channel, session, channel_id)
    except Exception as e:
        log.error(f"Channel videos error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    resp = Response(
        content=json_module.dumps({
            'channel': channel_name,
            'channel_id': channel_id,
            'results': results,
            'cursor': cursor
        }),
        media_type='application/json'
    )
    _set_session_cookie(resp, token, request)
    return resp
