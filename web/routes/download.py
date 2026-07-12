# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Download: full-quality video (chunked fetch + ffmpeg merge → MKV) or audio-only.

Both kinds fetch from googlevideo via parallel fresh-connection chunks (the same
throttle-defeating mechanism DASH playback uses), which is also what makes large
downloads reliable — a single long-lived connection gets dropped by YouTube mid-
transfer. Video is merged in a *seekable* temp file so the MKV carries a correct
Duration in its header (a streamed pipe cannot, since ffmpeg writes Duration by
seeking back to the start at finalize).
"""
import asyncio
import logging
import re
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from auth import require_auth
from dash import proxy_range_request, _fetch_chunk, _PROXY_CHUNK, _PROXY_CONCURRENCY
from helpers import get_video_info, is_youtube_url, register_cleanup, VIDEO_ID_RE

log = logging.getLogger(__name__)

router = APIRouter()

_VIDEO_EXTS = {'mp4', 'webm'}
_AUDIO_EXTS = {'m4a', 'mp4', 'webm'}

# Ephemeral workspace for merged downloads. Lives in /tmp: wiped on container
# restart, and a periodic sweep removes orphans left by aborted/crashed merges.
DOWNLOAD_TMP = Path(tempfile.gettempdir()) / "pytr-dl"
DOWNLOAD_TMP.mkdir(exist_ok=True)
_TMP_MAX_AGE = 3600  # seconds; orphan temp files older than this are swept


def _sweep_orphans():
    now = time.time()
    removed = 0
    for p in DOWNLOAD_TMP.glob('*'):
        try:
            if now - p.stat().st_mtime > _TMP_MAX_AGE:
                p.unlink()
                removed += 1
        except OSError:
            pass
    if removed:
        log.info("Swept %d orphan download temp file(s)", removed)


register_cleanup(_sweep_orphans)


def _sanitize_filename(name: str) -> str:
    """Strip characters illegal in filenames; cap length for safe headers."""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', '_', name or 'video').strip()
    return name[:120] or 'video'


def _content_disposition(filename: str) -> str:
    """RFC 5987 attachment header with a UTF-8 filename* for non-ASCII titles."""
    ascii_fallback = filename.encode('ascii', 'ignore').decode() or 'download'
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"


def _best_video(info: dict) -> dict | None:
    """Highest-resolution video-only HTTPS format (MKV holds any codec)."""
    vids = [
        f for f in info.get('formats', [])
        if f.get('protocol') == 'https' and f.get('url')
        and f.get('vcodec') not in (None, 'none')
        and f.get('acodec') in (None, 'none')
        and f.get('ext') in _VIDEO_EXTS
    ]
    if not vids:
        return None
    return max(vids, key=lambda f: (f.get('height') or 0, f.get('tbr') or 0))


def _best_audio(info: dict) -> dict | None:
    """Best audio-only HTTPS format, preferring the original (non-descriptive) track."""
    auds = [
        f for f in info.get('formats', [])
        if f.get('protocol') == 'https' and f.get('url')
        and f.get('acodec') not in (None, 'none')
        and f.get('vcodec') in (None, 'none')
        and f.get('ext') in _AUDIO_EXTS
    ]
    if not auds:
        return None
    return max(auds, key=lambda f: (f.get('language_preference') or 0, f.get('tbr') or 0))


async def _download_to_file(url: str, path: Path):
    """Fetch a googlevideo URL into a local file via parallel fresh-connection
    chunks — defeats the per-connection throttle and survives mid-transfer drops
    (each ≤2MB sub-request retries transiently and runs on its own connection)."""
    first = await _fetch_chunk(url, 0, _PROXY_CHUNK - 1)
    if first.status_code >= 400:
        raise HTTPException(status_code=502, detail="Upstream error")

    total = None
    cr = first.headers.get('content-range', '')
    if '/' in cr:
        tail = cr.rsplit('/', 1)[-1]
        if tail.isdigit():
            total = int(tail)

    with open(path, 'wb') as f:
        f.write(first.content)
        # Upstream ignored Range (200 = whole file) or gave no size → already done.
        if first.status_code != 206 or total is None:
            return

        pos = len(first.content)
        ranges = []
        while pos < total:
            hi = min(pos + _PROXY_CHUNK - 1, total - 1)
            ranges.append((pos, hi))
            pos = hi + 1

        # Bounded sliding window of parallel fetches, written strictly in order.
        tasks = {}
        nxt = 0
        for _ in range(min(_PROXY_CONCURRENCY, len(ranges))):
            lo, hi = ranges[nxt]
            tasks[nxt] = asyncio.create_task(_fetch_chunk(url, lo, hi))
            nxt += 1
        try:
            for i in range(len(ranges)):
                r = await tasks.pop(i)
                if r.status_code >= 400:
                    raise HTTPException(status_code=502, detail="Upstream error mid-download")
                f.write(r.content)
                if nxt < len(ranges):
                    lo, hi = ranges[nxt]
                    tasks[nxt] = asyncio.create_task(_fetch_chunk(url, lo, hi))
                    nxt += 1
        finally:
            for t in tasks.values():
                t.cancel()


async def _merge_to_mkv(video_path: Path, audio_path: Path, out_path: Path, video_id: str):
    """Remux a local video + audio file into a seekable MKV (correct Duration)."""
    proc = await asyncio.create_subprocess_exec(
        'ffmpeg', '-loglevel', 'error', '-nostdin', '-y',
        '-i', str(video_path), '-i', str(audio_path),
        '-map', '0:v:0', '-map', '1:a:0', '-c', 'copy', str(out_path),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        log.error("ffmpeg merge failed for %s: %s", video_id, (err or b'').decode()[:500])
        raise HTTPException(status_code=500, detail="Merge failed")


@router.get("/api/download/{video_id}")
async def download(video_id: str, request: Request, kind: str = "video",
                   cookies: str = "auto", uid: str = "", auth: bool = Depends(require_auth)):
    """Download a video as MKV (max quality, merged) or audio-only (original track)."""
    if not VIDEO_ID_RE.match(video_id):
        raise HTTPException(status_code=400, detail="Invalid video ID")
    if kind not in ("video", "audio"):
        raise HTTPException(status_code=400, detail="Invalid kind")

    try:
        info = await asyncio.to_thread(get_video_info, video_id, cookies, uid)
    except Exception as e:
        clean = re.sub(r'^(?:ERROR:\s*)?\[youtube\]\s*[\w-]+:\s*', '', str(e))
        if 'Sign in' in str(e) or 'bot' in str(e):
            return JSONResponse(status_code=503, content={
                'error': 'rate_limited',
                'message': clean or 'YouTube is temporarily blocking requests.',
            })
        raise HTTPException(status_code=500, detail=clean or str(e))

    title = _sanitize_filename(info.get('title'))

    if kind == "audio":
        fmt = _best_audio(info)
        if not fmt:
            raise HTTPException(status_code=404, detail="No audio format available")
        if not is_youtube_url(fmt['url']):
            raise HTTPException(status_code=403, detail="URL not allowed")
        ext = 'webm' if fmt['ext'] == 'webm' else 'm4a'
        # Chunked streaming proxy: same throttle-defeat as playback, but reliable
        # for a full-length download. The original track already has a correct
        # Duration, so no remux is needed.
        resp = await proxy_range_request(request, fmt['url'])
        resp.headers['Content-Disposition'] = _content_disposition(f"{title}.{ext}")
        return resp

    # Video: fetch both streams to temp, merge to a seekable MKV, serve, clean up.
    video = _best_video(info)
    audio = _best_audio(info)
    if not video or not audio:
        raise HTTPException(status_code=404, detail="No downloadable formats available")
    if not is_youtube_url(video['url']) or not is_youtube_url(audio['url']):
        raise HTTPException(status_code=403, detail="URL not allowed")

    job = uuid.uuid4().hex
    vpath = DOWNLOAD_TMP / f"{job}.v"
    apath = DOWNLOAD_TMP / f"{job}.a"
    mpath = DOWNLOAD_TMP / f"{job}.mkv"
    log.info("Download %s: fetching %sp video + audio → MKV", video_id, video.get('height'))
    try:
        await asyncio.gather(
            _download_to_file(video['url'], vpath),
            _download_to_file(audio['url'], apath),
        )
        await _merge_to_mkv(vpath, apath, mpath, video_id)
    except BaseException:
        for p in (vpath, apath, mpath):
            p.unlink(missing_ok=True)
        raise
    finally:
        # Source streams are no longer needed once merged (or on failure).
        vpath.unlink(missing_ok=True)
        apath.unlink(missing_ok=True)

    return FileResponse(
        str(mpath), media_type='video/x-matroska', filename=f"{title}.mkv",
        background=BackgroundTask(lambda: mpath.unlink(missing_ok=True)),
    )
