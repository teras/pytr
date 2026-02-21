# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""DASH streaming: MPD manifest generation, YouTube CDN proxy."""
import asyncio
import logging
import time
from urllib.parse import quote
from xml.sax.saxutils import escape as xml_escape

from fastapi import APIRouter, HTTPException, Request, Response, Depends
from fastapi.responses import StreamingResponse

from auth import require_auth
from container import probe_ranges
from helpers import register_cleanup, make_cache_cleanup, get_video_info, http_client, is_youtube_url, VIDEO_ID_RE

log = logging.getLogger(__name__)

router = APIRouter()

# DASH manifest cache: video_id -> {"mpd": str, "created": float}
_dash_cache: dict = {}
_DASH_CACHE_TTL = 5 * 3600  # URLs expire after ~6h, refresh at 5h


register_cleanup(make_cache_cleanup(_dash_cache, _DASH_CACHE_TTL, "DASH"))

# Allowed extensions
_VIDEO_EXTS = {'mp4', 'webm'}
_AUDIO_EXTS = {'m4a', 'mp4', 'webm'}


# ── Proxy helper (shared with stream-live) ───────────────────────────────────


async def proxy_range_request(request: Request, video_url: str, filesize: int = None):
    """Proxy a YouTube URL with range request support, forwarding upstream headers."""
    range_header = request.headers.get('range')

    upstream_headers = {}
    if range_header:
        upstream_headers['Range'] = range_header
    elif filesize:
        upstream_headers['Range'] = 'bytes=0-'

    try:
        upstream = await http_client.send(
            http_client.build_request('GET', video_url, headers=upstream_headers),
            stream=True,
        )
    except Exception as e:
        log.warning(f"Upstream connection error: {e}")
        raise HTTPException(status_code=502, detail="Upstream connection failed")

    if upstream.status_code >= 400:
        await upstream.aclose()
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

    return StreamingResponse(stream_body(), status_code=status, headers=resp_headers)


# ── Format helpers ────────────────────────────────────────────────────────────

def _container_of(fmt: dict) -> str:
    return 'webm' if fmt.get('ext') == 'webm' else 'mp4'


def _mime_for(container: str, media: str) -> str:
    return f'{media}/webm' if container == 'webm' else f'{media}/mp4'


def _is_hdr(fmt: dict) -> bool:
    """Check if format uses HDR codec (vp9 profile 2+, av01 high profile)."""
    codec = (fmt.get('vcodec') or '').lower()
    return 'vp9.2' in codec or 'vp09.02' in codec


def _dedup_by_height(fmts: list) -> list:
    """Keep best format per height. Prefer SDR over HDR at same height."""
    best = {}
    for fmt in fmts:
        h = fmt.get('height', 0)
        existing = best.get(h)
        if not existing:
            best[h] = fmt
        elif _is_hdr(existing) and not _is_hdr(fmt):
            # Replace HDR with SDR — HDR often unsupported by browser
            best[h] = fmt
        elif not _is_hdr(existing) and _is_hdr(fmt):
            pass  # Keep existing SDR
        elif (fmt.get('tbr') or 0) > (existing.get('tbr') or 0):
            best[h] = fmt
    return sorted(best.values(), key=lambda f: f.get('height', 0))


# ── DASH manifest endpoint ───────────────────────────────────────────────────

@router.get("/api/dash/{video_id}")
async def get_dash_manifest(video_id: str, auth: bool = Depends(require_auth)):
    """Generate DASH MPD manifest with proxied URLs.

    Uses a single container type for video to avoid track-switching issues.
    Prefers webm/VP9 (available 360p-4K), falls back to mp4/avc1.
    """

    if not VIDEO_ID_RE.match(video_id):
        raise HTTPException(status_code=400, detail="Invalid video ID")
    cached = _dash_cache.get(video_id)
    if cached and time.time() - cached['created'] < _DASH_CACHE_TTL:
        return Response(cached['mpd'], media_type='application/dash+xml',
                        headers={'Cache-Control': 'no-cache'})

    try:
        info = await asyncio.to_thread(get_video_info, video_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    duration = info.get('duration') or 0

    # Collect HTTPS video-only and audio-only formats, grouped by container
    video_by_container: dict[str, list] = {}  # container -> [fmt, ...]
    audio_by_container: dict[str, list] = {}

    for fmt in info.get('formats', []):
        if fmt.get('protocol') != 'https' or not fmt.get('url'):
            continue
        has_video = fmt.get('vcodec') not in (None, 'none')
        has_audio = fmt.get('acodec') not in (None, 'none')

        if has_video and not has_audio:
            height = fmt.get('height') or 0
            if height < 360 or fmt.get('ext') not in _VIDEO_EXTS:
                continue
            c = _container_of(fmt)
            video_by_container.setdefault(c, []).append(fmt)
        elif has_audio and not has_video:
            if fmt.get('ext') not in _AUDIO_EXTS:
                continue
            c = _container_of(fmt)
            audio_by_container.setdefault(c, []).append(fmt)

    if not video_by_container:
        raise HTTPException(status_code=404, detail="No DASH video formats available")

    # Pick one container for video: prefer webm (VP9, up to 4K), fall back to mp4
    video_container = 'webm' if 'webm' in video_by_container else 'mp4'
    video_fmts = _dedup_by_height(video_by_container[video_container])

    # Pick best audio: prefer mp4/m4a (widest browser support), fall back to webm
    audio_container = 'mp4' if 'mp4' in audio_by_container else 'webm'
    audio_fmts_raw = audio_by_container.get(audio_container, [])
    # Keep single best audio
    best_audio = max(audio_fmts_raw, key=lambda f: f.get('tbr') or 0) if audio_fmts_raw else None
    audio_fmts = [best_audio] if best_audio else []

    if not audio_fmts:
        raise HTTPException(status_code=404, detail="No DASH audio formats available")

    # Probe all formats for initRange/indexRange (parallel)
    all_fmts = video_fmts + audio_fmts
    orig_video_count = len(video_fmts)
    probe_results = await asyncio.gather(*[probe_ranges(f['url']) for f in all_fmts])

    # Filter out formats where probing failed
    valid_video = []
    valid_video_probes = []
    for i, fmt in enumerate(video_fmts):
        probe = probe_results[i]
        if probe and 'init_end' in probe and 'index_start' in probe:
            valid_video.append(fmt)
            valid_video_probes.append(probe)
        else:
            log.warning(f"Skipping {video_container} {fmt.get('height')}p: probe failed")

    # If preferred container failed entirely, try the other one
    if not valid_video and len(video_by_container) > 1:
        fallback = 'mp4' if video_container == 'webm' else 'webm'
        video_container = fallback
        video_fmts = _dedup_by_height(video_by_container[fallback])
        probe_results_fb = await asyncio.gather(*[probe_ranges(f['url']) for f in video_fmts])
        for i, fmt in enumerate(video_fmts):
            probe = probe_results_fb[i]
            if probe and 'init_end' in probe and 'index_start' in probe:
                valid_video.append(fmt)
                valid_video_probes.append(probe)

    audio_probe = probe_results[orig_video_count] if len(probe_results) > orig_video_count else None
    if not audio_probe or 'init_end' not in audio_probe or 'index_start' not in audio_probe:
        # Try other audio container
        other_audio = 'webm' if audio_container == 'mp4' else 'mp4'
        other_audio_fmts = audio_by_container.get(other_audio, [])
        if other_audio_fmts:
            best_other = max(other_audio_fmts, key=lambda f: f.get('tbr') or 0)
            audio_probe = await probe_ranges(best_other['url'])
            if audio_probe and 'init_end' in audio_probe:
                audio_fmts = [best_other]
                audio_container = other_audio

    if not valid_video:
        raise HTTPException(status_code=404, detail="No DASH formats with valid ranges")
    if not audio_probe or 'init_end' not in audio_probe:
        raise HTTPException(status_code=404, detail="No DASH audio with valid ranges")

    v_mime = _mime_for(video_container, 'video')
    a_mime = _mime_for(audio_container, 'audio')

    # Build MPD XML — single video AdaptationSet, single audio AdaptationSet
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
        f'<AdaptationSet id="0" mimeType="{v_mime}" '
        f'startWithSAP="1" subsegmentAlignment="true" scanType="progressive">'
    )
    for i, fmt in enumerate(valid_video):
        probe = valid_video_probes[i]
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
        mpd_lines.append(
            f'<SegmentBase indexRange="{probe["index_start"]}-{probe["index_end"]}">'
            f'<Initialization range="0-{probe["init_end"]}"/>'
            f'</SegmentBase>'
        )
        mpd_lines.append('</Representation>')
    mpd_lines.append('</AdaptationSet>')

    # Audio AdaptationSet
    afmt = audio_fmts[0]
    proxy_url = f'/api/videoplayback?url={quote(afmt["url"], safe="")}'
    codecs = afmt.get('acodec', 'mp4a.40.2')
    bandwidth = int((afmt.get('tbr') or afmt.get('abr') or 0) * 1000) or 128000

    mpd_lines.append(
        f'<AdaptationSet id="1" mimeType="{a_mime}" '
        f'startWithSAP="1" subsegmentAlignment="true">'
    )
    mpd_lines.append(
        f'<Representation id="{afmt.get("format_id", "audio")}" '
        f'codecs="{codecs}" bandwidth="{bandwidth}">'
    )
    mpd_lines.append(
        '<AudioChannelConfiguration '
        'schemeIdUri="urn:mpeg:dash:23003:3:audio_channel_configuration:2011" '
        'value="2"/>'
    )
    mpd_lines.append(f'<BaseURL>{xml_escape(proxy_url)}</BaseURL>')
    mpd_lines.append(
        f'<SegmentBase indexRange="{audio_probe["index_start"]}-{audio_probe["index_end"]}">'
        f'<Initialization range="0-{audio_probe["init_end"]}"/>'
        f'</SegmentBase>'
    )
    mpd_lines.append('</Representation>')
    mpd_lines.append('</AdaptationSet>')

    mpd_lines.append('</Period>')
    mpd_lines.append('</MPD>')

    mpd = '\n'.join(mpd_lines)

    heights = [f.get('height', 0) for f in valid_video]
    log.info(f"DASH {video_id}: {len(valid_video)} video ({video_container}) "
             f"+ 1 audio ({audio_container}), max {max(heights)}p")

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
async def videoplayback_proxy(url: str, request: Request):
    """Proxy range requests to YouTube CDN for DASH playback.

    No auth required — the manifest endpoint already checks auth,
    and the YouTube URLs are opaque/temporary.
    """
    if not is_youtube_url(url):
        raise HTTPException(status_code=403, detail="URL not allowed")
    return await proxy_range_request(request, url)
