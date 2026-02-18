"""DASH streaming: MPD manifest generation, MP4 box probing, YouTube CDN proxy."""
import asyncio
import logging
import struct
import time
from urllib.parse import quote
from xml.sax.saxutils import escape as xml_escape

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, Response, Depends
from fastapi.responses import StreamingResponse

from auth import require_auth
from helpers import _yt_url, ydl_info

log = logging.getLogger(__name__)

router = APIRouter()

# DASH manifest cache: video_id -> {"mpd": str, "created": float}
_dash_cache: dict = {}
_DASH_CACHE_TTL = 5 * 3600  # URLs expire after ~6h, refresh at 5h


# ── MP4 box probing ──────────────────────────────────────────────────────────

def _parse_mp4_boxes(data: bytes) -> dict:
    """Parse MP4 box headers to find initRange and indexRange."""
    offset = 0
    boxes = []
    while offset < len(data) - 8:
        size = struct.unpack('>I', data[offset:offset + 4])[0]
        box_type = data[offset + 4:offset + 8].decode('ascii', errors='replace')
        if size == 1 and offset + 16 <= len(data):
            size = struct.unpack('>Q', data[offset + 8:offset + 16])[0]
        elif size == 0:
            size = len(data) - offset
        if size < 8:
            break
        boxes.append({'type': box_type, 'offset': offset, 'size': size})
        offset += size

    result = {}
    for box in boxes:
        if box['type'] == 'moov':
            result['init_end'] = box['offset'] + box['size'] - 1
        elif box['type'] == 'sidx':
            result['index_start'] = box['offset']
            result['index_end'] = box['offset'] + box['size'] - 1
    return result


async def _probe_mp4_ranges(url: str) -> dict | None:
    """Fetch first 4KB of a URL and parse MP4 box structure."""
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, headers={'Range': 'bytes=0-4095'}, timeout=10.0)
            if resp.status_code not in (200, 206):
                return None
            return _parse_mp4_boxes(resp.content)
    except Exception:
        return None


# ── Proxy helper (shared with stream-live) ───────────────────────────────────

async def proxy_range_request(request: Request, video_url: str, filesize: int = None):
    """Proxy a YouTube URL with range request support, forwarding upstream headers."""
    range_header = request.headers.get('range')

    upstream_headers = {}
    if range_header:
        upstream_headers['Range'] = range_header
    elif filesize:
        upstream_headers['Range'] = 'bytes=0-'

    client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    try:
        upstream = await client.send(
            client.build_request('GET', video_url, headers=upstream_headers),
            stream=True,
        )
    except Exception as e:
        await client.aclose()
        log.warning(f"Upstream connection error: {e}")
        raise HTTPException(status_code=502, detail="Upstream connection failed")

    if upstream.status_code >= 400:
        await upstream.aclose()
        await client.aclose()
        log.warning(f"Upstream error {upstream.status_code}")
        raise HTTPException(status_code=upstream.status_code, detail="Upstream error")

    resp_headers = {
        'Accept-Ranges': 'bytes',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Expose-Headers': 'Content-Range, Content-Length',
        'Cache-Control': 'no-cache',
    }

    ct = upstream.headers.get('content-type', 'video/mp4')
    resp_headers['Content-Type'] = ct

    if upstream.headers.get('content-range'):
        resp_headers['Content-Range'] = upstream.headers['content-range']
    if upstream.headers.get('content-length'):
        resp_headers['Content-Length'] = upstream.headers['content-length']

    status = 206 if upstream.status_code == 206 else 200

    async def stream_body():
        try:
            async for chunk in upstream.aiter_bytes(65536):
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(stream_body(), status_code=status, headers=resp_headers)


# ── DASH manifest endpoint ───────────────────────────────────────────────────

@router.get("/api/dash/{video_id}")
async def get_dash_manifest(video_id: str, quality: int = Query(default=1080), auth: bool = Depends(require_auth)):
    """Generate DASH MPD manifest with proxied URLs."""

    cached = _dash_cache.get(video_id)
    if cached and time.time() - cached['created'] < _DASH_CACHE_TTL:
        return Response(cached['mpd'], media_type='application/dash+xml',
                        headers={'Cache-Control': 'no-cache'})

    url = _yt_url(video_id)
    try:
        info = await asyncio.to_thread(ydl_info.extract_info, url, download=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    duration = info.get('duration') or 0

    # Collect HTTPS video-only and audio-only formats
    video_fmts = []
    audio_fmts = []

    for fmt in info.get('formats', []):
        if fmt.get('protocol') != 'https' or not fmt.get('url'):
            continue
        has_video = fmt.get('vcodec') not in (None, 'none')
        has_audio = fmt.get('acodec') not in (None, 'none')

        if has_video and not has_audio:
            height = fmt.get('height') or 0
            if height < 360 or height > quality:
                continue
            if fmt.get('ext') not in ('mp4', 'mp4a'):
                continue
            video_fmts.append(fmt)
        elif has_audio and not has_video:
            if fmt.get('ext') not in ('m4a', 'mp4'):
                continue
            audio_fmts.append(fmt)

    if not video_fmts or not audio_fmts:
        raise HTTPException(status_code=404, detail="No DASH formats available")

    # Deduplicate: keep best format per height (prefer H.264/avc1 > AV1)
    best_video = {}
    for fmt in video_fmts:
        height = fmt.get('height', 0)
        codec = fmt.get('vcodec', '')
        existing = best_video.get(height)
        if not existing:
            best_video[height] = fmt
        elif codec.startswith('avc1') and not existing.get('vcodec', '').startswith('avc1'):
            best_video[height] = fmt
        elif codec.startswith('avc1') == existing.get('vcodec', '').startswith('avc1'):
            if (fmt.get('tbr') or 0) > (existing.get('tbr') or 0):
                best_video[height] = fmt
    video_fmts = sorted(best_video.values(), key=lambda f: f.get('height', 0))

    # Best audio: prefer m4a
    best_audio = None
    for fmt in audio_fmts:
        if not best_audio:
            best_audio = fmt
        elif fmt.get('ext') == 'm4a' and best_audio.get('ext') != 'm4a':
            best_audio = fmt
        elif fmt.get('ext') == best_audio.get('ext') and (fmt.get('tbr') or 0) > (best_audio.get('tbr') or 0):
            best_audio = fmt
    audio_fmts = [best_audio] if best_audio else []

    # Probe MP4 boxes for initRange/indexRange (parallel)
    all_fmts = video_fmts + audio_fmts
    probe_results = await asyncio.gather(*[_probe_mp4_ranges(f['url']) for f in all_fmts])

    # Build MPD XML
    mpd_lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" '
        'profiles="urn:mpeg:dash:profile:isoff-on-demand:2011" '
        f'minBufferTime="PT1.5S" type="static" '
        f'mediaPresentationDuration="PT{duration}S">',
        '<Period>',
    ]

    # Video AdaptationSet
    mpd_lines.append(
        '<AdaptationSet id="0" mimeType="video/mp4" '
        'startWithSAP="1" subsegmentAlignment="true" scanType="progressive">'
    )
    for i, fmt in enumerate(video_fmts):
        probe = probe_results[i]
        proxy_url = f'/api/videoplayback?url={quote(fmt["url"], safe="")}'
        codecs = fmt.get('vcodec', 'avc1.4d401e')
        height = fmt.get('height', 0)
        width = fmt.get('width', 0)
        fps = fmt.get('fps', 30)
        bandwidth = int((fmt.get('tbr') or fmt.get('vbr') or 0) * 1000) or 1000000

        mpd_lines.append(
            f'<Representation id="{fmt.get("format_id", i)}" '
            f'codecs="{codecs}" width="{width}" height="{height}" '
            f'bandwidth="{bandwidth}" frameRate="{fps}">'
        )
        mpd_lines.append(f'<BaseURL>{xml_escape(proxy_url)}</BaseURL>')
        if probe and 'init_end' in probe and 'index_start' in probe:
            mpd_lines.append(
                f'<SegmentBase indexRange="{probe["index_start"]}-{probe["index_end"]}">'
                f'<Initialization range="0-{probe["init_end"]}"/>'
                f'</SegmentBase>'
            )
        mpd_lines.append('</Representation>')

    mpd_lines.append('</AdaptationSet>')

    # Audio AdaptationSet
    if audio_fmts:
        audio_idx_offset = len(video_fmts)
        mpd_lines.append(
            '<AdaptationSet id="1" mimeType="audio/mp4" '
            'startWithSAP="1" subsegmentAlignment="true">'
        )
        for j, fmt in enumerate(audio_fmts):
            probe = probe_results[audio_idx_offset + j]
            proxy_url = f'/api/videoplayback?url={quote(fmt["url"], safe="")}'
            codecs = fmt.get('acodec', 'mp4a.40.2')
            bandwidth = int((fmt.get('tbr') or fmt.get('abr') or 0) * 1000) or 128000

            mpd_lines.append(
                f'<Representation id="{fmt.get("format_id", "audio")}" '
                f'codecs="{codecs}" bandwidth="{bandwidth}">'
            )
            mpd_lines.append(
                '<AudioChannelConfiguration '
                'schemeIdUri="urn:mpeg:dash:23003:3:audio_channel_configuration:2011" '
                'value="2"/>'
            )
            mpd_lines.append(f'<BaseURL>{xml_escape(proxy_url)}</BaseURL>')
            if probe and 'init_end' in probe and 'index_start' in probe:
                mpd_lines.append(
                    f'<SegmentBase indexRange="{probe["index_start"]}-{probe["index_end"]}">'
                    f'<Initialization range="0-{probe["init_end"]}"/>'
                    f'</SegmentBase>'
                )
            mpd_lines.append('</Representation>')

        mpd_lines.append('</AdaptationSet>')

    mpd_lines.append('</Period>')
    mpd_lines.append('</MPD>')

    mpd = '\n'.join(mpd_lines)

    log.info(f"DASH {video_id}: {len(video_fmts)} video + {len(audio_fmts)} audio tracks, max {video_fmts[-1].get('height')}p")

    _dash_cache[video_id] = {'mpd': mpd, 'created': time.time()}

    return Response(mpd, media_type='application/dash+xml',
                    headers={'Cache-Control': 'no-cache'})


# ── Videoplayback proxy endpoint ─────────────────────────────────────────────

@router.options("/api/videoplayback")
async def videoplayback_options():
    """CORS preflight for dash.js range requests."""
    return Response(
        status_code=204,
        headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, Range',
            'Access-Control-Max-Age': '86400',
        },
    )


@router.get("/api/videoplayback")
async def videoplayback_proxy(url: str, request: Request, auth: bool = Depends(require_auth)):
    """Proxy range requests to YouTube CDN for DASH playback."""
    return await proxy_range_request(request, url)
