# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""For You sidecar integration.

Adds three things to PYTR:
  1. ``/api/internal/profile-export/{uuid}`` — read-only export the sidecar
     consumes. Internal, never exposed publicly (no auth required because the
     sidecar reaches PYTR only on the internal Docker network).
  2. ``/api/foryou/*`` — reverse-proxy to the sidecar with the profile UUID
     injected via ``X-PYTR-Profile-UUID`` so the sidecar never has to know
     PYTR's integer profile IDs.
  3. ``foryou_probe_once()`` — startup probe whose result feeds
     ``/api/profiles/boot`` so the SPA gates all For You UI on a single boolean.

If the sidecar is down at startup, the reverse-proxy returns 502 and the
frontend never renders any For You UI — exactly the contract from the plan.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from auth import require_profile
import profiles_db as db

log = logging.getLogger(__name__)

FORYOU_URL = os.environ.get("FORYOU_URL", "http://foryou:8000").rstrip("/")

# Set by foryou_probe_once() at startup.
FORYOU_AVAILABLE: bool = False

router = APIRouter()

_proxy_client: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    global _proxy_client
    if _proxy_client is None:
        _proxy_client = httpx.AsyncClient(timeout=120.0)
    return _proxy_client


async def foryou_probe_once() -> bool:
    """Startup probe — sets FORYOU_AVAILABLE once and never re-checks during life."""
    global FORYOU_AVAILABLE
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{FORYOU_URL}/health")
            FORYOU_AVAILABLE = r.status_code == 200
    except httpx.HTTPError as e:
        log.info("foryou probe failed (%s) — feature disabled this session", e)
        FORYOU_AVAILABLE = False
    log.info("foryou_available=%s url=%s", FORYOU_AVAILABLE, FORYOU_URL)
    return FORYOU_AVAILABLE


# ── Internal export endpoint ────────────────────────────────────────────────

@router.get("/api/internal/profile-export/{profile_uuid}")
async def internal_profile_export(profile_uuid: str, request: Request):
    """Cross-container handoff. We accept it from any source within the Docker
    network — there is no auth wall between siblings."""
    pid = db.get_profile_id_by_uuid(profile_uuid)
    if not pid:
        raise HTTPException(status_code=404, detail="profile uuid not found")
    return db.export_profile_for_foryou(pid)


# ── Reverse-proxy ───────────────────────────────────────────────────────────

@router.api_route("/api/foryou/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def foryou_proxy(path: str, request: Request, profile_id: int = Depends(require_profile)):
    if not FORYOU_AVAILABLE:
        raise HTTPException(status_code=502, detail="For You sidecar not available")
    uuid = db.get_profile_uuid(profile_id)
    if not uuid:
        raise HTTPException(status_code=500, detail="missing profile uuid (migration not applied?)")
    upstream = f"{FORYOU_URL}/api/foryou/{path}"
    body = await request.body()
    headers = {
        "X-PYTR-Profile-UUID": uuid,
        "Content-Type": request.headers.get("content-type", "application/json"),
    }
    try:
        r = await _client().request(
            request.method, upstream, params=dict(request.query_params),
            content=body, headers=headers,
        )
    except httpx.HTTPError as e:
        log.warning("foryou proxy %s failed: %s", path, e)
        raise HTTPException(status_code=502, detail="For You sidecar unreachable")
    # Pass through status + body. Strip hop-by-hop headers.
    excluded = {"content-encoding", "transfer-encoding", "connection"}
    out_headers = {k: v for k, v in r.headers.items() if k.lower() not in excluded}
    return Response(content=r.content, status_code=r.status_code,
                    headers=out_headers, media_type=r.headers.get("content-type"))


async def close_proxy_client():
    global _proxy_client
    if _proxy_client is not None:
        await _proxy_client.aclose()
        _proxy_client = None


_DEAD_MARKERS = (
    "video unavailable",
    "private video",
    "removed by the uploader",
    "no longer available",
    "this video has been removed",
    "members-only",
    "this live stream recording is not available",
)


def looks_dead(error_msg: str) -> bool:
    m = (error_msg or "").lower()
    return any(marker in m for marker in _DEAD_MARKERS)


async def notify_unavailable(video_id: str, reason: str = ""):
    """Best-effort: tell the For You sidecar that this video is dead so future
    candidate pulls skip it. Silently no-ops if foryou is down or disabled."""
    if not FORYOU_AVAILABLE or not video_id:
        return
    try:
        await _client().post(
            f"{FORYOU_URL}/api/foryou/report-unavailable",
            json={"video_id": video_id, "reason": reason[:200]},
            timeout=3.0,
        )
    except Exception as e:
        log.debug("foryou notify_unavailable %s failed: %s", video_id, e)
