# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""DASH streaming: MPD manifest generation, YouTube CDN proxy."""
import asyncio
import logging
import re
import time
from urllib.parse import quote
from xml.sax.saxutils import escape as xml_escape

import httpx
from fastapi import APIRouter, HTTPException, Request, Response, Depends
from fastapi.responses import JSONResponse, StreamingResponse

from auth import require_auth, require_auth_or_embed
from container import probe_ranges
from helpers import register_cleanup, make_cache_cleanup, get_video_info, invalidate_video_cache, init_ydl, http_client, is_youtube_url, VIDEO_ID_RE

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


# YouTube throttles each connection to a videoplayback URL to roughly twice the
# format's encoded bitrate (it paces delivery, resetting per fresh connection). A
# single connection therefore sits near real-time and stalls under jitter. We defeat
# this by fetching the requested byte range as parallel ≤2MB sub-requests, each on a
# fresh connection, and streaming them to the client in order — multiplying
# throughput well past real-time (smooth up to 4K) without SABR. This mirrors how
# invidious-companion chunks videoplayback requests.
_PROXY_CHUNK = 2 * 1024 * 1024   # 2 MB per upstream sub-request
# Throughput scales linearly with concurrency until the server's uplink saturates
# (~4 connections at ≈31 Mbps each); beyond that extra connections add no speed,
# only load. 3 gives ample headroom above any format's bitrate without waste.
_PROXY_CONCURRENCY = 3           # parallel sub-requests in flight
_PROXY_RETRIES = 3               # attempts per sub-request before giving up
_PROXY_RETRY_BACKOFF = 0.25      # seconds, linear backoff between attempts


class _UpstreamChunkError(Exception):
    """Raised mid-stream when a sub-request fails after all retries. Breaking the
    client connection (incomplete transfer) makes the player issue a clean
    re-request, instead of silently receiving a truncated 206 it can't detect."""


async def _fetch_chunk(url: str, lo: int, hi: int):
    """GET a single byte range [lo, hi] on a fresh connection.

    Retries transient upstream failures (connection resets, timeouts, 5xx) so a
    momentary googlevideo hiccup re-fetches the chunk instead of stalling playback.
    """
    headers = {'Range': f'bytes={lo}-{hi}'}
    last_exc = None
    for attempt in range(_PROXY_RETRIES):
        try:
            r = await http_client.get(url, headers=headers)
            if r.status_code < 500:
                return r
            last_exc = None
            log.warning(f"chunk {lo}-{hi}: upstream {r.status_code} (attempt {attempt + 1}/{_PROXY_RETRIES})")
        except httpx.RequestError as e:
            last_exc = e
            log.warning(f"chunk {lo}-{hi}: {type(e).__name__} (attempt {attempt + 1}/{_PROXY_RETRIES})")
        if attempt + 1 < _PROXY_RETRIES:
            await asyncio.sleep(_PROXY_RETRY_BACKOFF * (attempt + 1))
    if last_exc is not None:
        raise last_exc
    return r


async def proxy_range_request(request: Request, video_url: str, filesize: int = None):
    """Proxy a YouTube URL, defeating the per-connection throttle by fetching the
    requested byte range as parallel ≤2MB sub-requests and streaming them in order."""
    range_header = request.headers.get('range')

    start = 0
    end = None  # inclusive; None = open-ended (stream to EOF)
    if range_header:
        m = re.match(r'bytes=(\d+)-(\d*)', range_header)
        if m:
            start = int(m.group(1))
            if m.group(2):
                end = int(m.group(2))
                # Fix dash.js 32-bit overflow: for files >4GB the end byte wraps
                # due to 32-bit truncation in the SIDX parser. Start is always
                # correct, so reconstruct end's high bits from it.
                if start > end:
                    end = ((start >> 32) << 32) | (end & 0xFFFFFFFF)
                    if start > end:
                        # Segment crosses a 4GB boundary — carry into next block
                        end += (1 << 32)
                    log.info(f"Fixed 32-bit overflow in Range header: bytes={start}-{end}")

    # The first sub-request doubles as a size probe: its Content-Range gives the total.
    first_hi = start + _PROXY_CHUNK - 1
    if end is not None:
        first_hi = min(first_hi, end)
    try:
        first = await _fetch_chunk(video_url, start, first_hi)
    except Exception as e:
        log.warning(f"Upstream connection error: {e}")
        raise HTTPException(status_code=502, detail="Upstream connection failed")

    if first.status_code == 416:
        # Range not satisfiable — return empty response so dash.js ends gracefully
        log.warning(f"416 Range Not Satisfiable: requested bytes={start}-{first_hi}")
        return Response(status_code=200, content=b'', headers={
            'Content-Length': '0',
            'Access-Control-Allow-Origin': '*',
        })
    if first.status_code >= 400:
        log.warning(f"Upstream error {first.status_code}")
        raise HTTPException(status_code=first.status_code, detail="Upstream error")

    resp_headers = {
        'Accept-Ranges': 'bytes',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Expose-Headers': 'Content-Range, Content-Length',
        'Cache-Control': 'no-cache',
        'Content-Type': first.headers.get('content-type', 'video/mp4'),
    }

    first_bytes = first.content

    # If upstream ignored the Range and returned the whole file (200), serve it as-is.
    if first.status_code != 206:
        resp_headers['Content-Length'] = str(len(first_bytes))

        async def stream_whole():
            yield first_bytes

        return StreamingResponse(stream_whole(), status_code=200, headers=resp_headers)

    # Total file size from Content-Range ("bytes start-end/total").
    total = None
    cr = first.headers.get('content-range', '')
    if '/' in cr:
        tail = cr.rsplit('/', 1)[-1]
        if tail.isdigit():
            total = int(tail)
    if end is None:
        end = (total - 1) if total is not None else (start + len(first_bytes) - 1)

    if range_header:
        status = 206
        if total is not None:
            resp_headers['Content-Range'] = f'bytes {start}-{end}/{total}'
    else:
        status = 200
    resp_headers['Content-Length'] = str(end - start + 1)

    async def stream_body():
        yield first_bytes
        pos = first_hi + 1
        if pos > end:
            return
        # Remaining (lo, hi) sub-ranges, fetched with a bounded sliding window of
        # parallel fresh connections and yielded strictly in order.
        ranges = []
        while pos <= end:
            hi = min(pos + _PROXY_CHUNK - 1, end)
            ranges.append((pos, hi))
            pos = hi + 1
        tasks = {}
        nxt = 0
        for _ in range(min(_PROXY_CONCURRENCY, len(ranges))):
            lo, hi = ranges[nxt]
            tasks[nxt] = asyncio.create_task(_fetch_chunk(video_url, lo, hi))
            nxt += 1
        try:
            for i in range(len(ranges)):
                try:
                    r = await tasks.pop(i)
                except Exception as e:
                    log.warning(f"chunk {ranges[i]} fetch error after retries: {e}")
                    raise _UpstreamChunkError() from e
                if r.status_code >= 400:
                    log.warning(f"chunk {ranges[i]} -> {r.status_code} after retries")
                    raise _UpstreamChunkError()
                yield r.content
                if nxt < len(ranges):
                    lo, hi = ranges[nxt]
                    tasks[nxt] = asyncio.create_task(_fetch_chunk(video_url, lo, hi))
                    nxt += 1
        finally:
            # Cancel any sub-requests still in flight so they don't keep
            # downloading bytes we'll never use after an early exit.
            for t in tasks.values():
                t.cancel()

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


def _normalize_vp9_codec(codec: str, height: int, fps: int) -> str:
    """Convert bare "vp9"/"vp9.2" to ISO codec string vp09.PP.LL.DD.

    dash.js v5+ uses navigator.mediaCapabilities.decodingInfo() which strictly
    requires the full form. Bare "vp9" returns supported=false even though
    MediaSource.isTypeSupported accepts it, causing all VP9 representations to
    be filtered out (audio plays, video doesn't).
    Profile: 00 (8-bit SDR) or 02 (10-bit HDR).
    Level chosen to match resolution+fps so the browser doesn't reject decoding
    capability claims that exceed the device's real ceiling.
    """
    c = codec.lower()
    if c.startswith('vp09'):
        return codec  # already canonical
    if c not in ('vp9', 'vp9.2'):
        return codec  # not VP9
    profile = '02' if c == 'vp9.2' else '00'
    bitdepth = '10' if profile == '02' else '08'
    h = height or 0
    high_fps = (fps or 0) > 30
    if h <= 480:
        level = '30'
    elif h <= 720:
        level = '31'
    elif h <= 1080:
        level = '41' if high_fps else '40'
    elif h <= 1440:
        level = '50'
    elif h <= 2160:
        level = '51'
    else:
        level = '60'
    return f'vp09.{profile}.{level}.{bitdepth}'


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
    result = sorted(best.values(), key=lambda f: f.get('height', 0))
    # Below HD (720p), keep only the highest resolution
    hd = [f for f in result if f.get('height', 0) >= 720]
    sd = [f for f in result if f.get('height', 0) < 720]
    return ([sd[-1]] if sd else []) + hd


def _collect_dash_formats(info: dict) -> tuple[dict, dict]:
    """Collect HTTPS video-only and audio-only formats, grouped by container."""
    video_by_container: dict[str, list] = {}
    audio_by_container: dict[str, list] = {}
    for fmt in info.get('formats', []):
        if fmt.get('protocol') != 'https' or not fmt.get('url'):
            continue
        has_video = fmt.get('vcodec') not in (None, 'none')
        has_audio = fmt.get('acodec') not in (None, 'none')
        if has_video and not has_audio:
            if fmt.get('ext') not in _VIDEO_EXTS:
                continue
            c = _container_of(fmt)
            video_by_container.setdefault(c, []).append(fmt)
        elif has_audio and not has_video:
            if fmt.get('ext') not in _AUDIO_EXTS:
                continue
            c = _container_of(fmt)
            audio_by_container.setdefault(c, []).append(fmt)
    return video_by_container, audio_by_container


# ── DASH manifest endpoint ───────────────────────────────────────────────────

@router.get("/api/dash/{video_id}")
async def get_dash_manifest(video_id: str, cookies: str = "auto", auth: bool = Depends(require_auth_or_embed)):
    """Generate DASH MPD manifest with proxied URLs.

    Uses a single container type for video to avoid track-switching issues.
    Prefers webm/VP9 (available 144p-4K). Falls back to mp4 only if no WebM.
    """

    if not VIDEO_ID_RE.match(video_id):
        raise HTTPException(status_code=400, detail="Invalid video ID")
    cached = _dash_cache.get(video_id)
    if cached and time.time() - cached['created'] < _DASH_CACHE_TTL:
        return Response(cached['mpd'], media_type='application/dash+xml',
                        headers={'Cache-Control': 'no-cache'})

    try:
        info = await asyncio.to_thread(get_video_info, video_id, cookies)
    except Exception as e:
        err_msg = str(e)
        clean_msg = re.sub(r'^(?:ERROR:\s*)?\[youtube\]\s*[\w-]+:\s*', '', err_msg)
        if 'Sign in' in err_msg or 'bot' in err_msg:
            return JSONResponse(status_code=503, content={
                'error': 'rate_limited',
                'message': clean_msg or 'YouTube is temporarily blocking requests.',
            })
        raise HTTPException(status_code=500, detail=clean_msg or err_msg)

    duration = info.get('duration') or 0

    # Collect HTTPS video-only and audio-only formats, grouped by container
    video_by_container, audio_by_container = _collect_dash_formats(info)

    if not video_by_container:
        # Stale cache or corrupted yt-dlp state — refresh and retry once
        log.warning("No DASH video formats for %s — invalidating cache and retrying", video_id)
        invalidate_video_cache(video_id)
        await asyncio.to_thread(init_ydl)
        try:
            info = await asyncio.to_thread(get_video_info, video_id, cookies)
        except Exception:
            raise HTTPException(status_code=404, detail="No DASH video formats available")
        duration = info.get('duration') or 0
        video_by_container, audio_by_container = _collect_dash_formats(info)

    if not video_by_container:
        raise HTTPException(status_code=404, detail="No DASH video formats available")

    # Pick one container for video: prefer webm (VP9, 144p-4K), fall back to mp4.
    video_container = 'webm' if 'webm' in video_by_container else 'mp4'
    video_fmts = _dedup_by_height(video_by_container[video_container])

    # Pick best audio: prefer mp4/m4a (widest browser support), fall back to webm
    audio_container = 'mp4' if 'mp4' in audio_by_container else 'webm'
    audio_fmts_raw = audio_by_container.get(audio_container, [])
    # Keep single best audio. Prefer the original track over alternates like
    # descriptive audio (yt-dlp marks descriptive tracks with negative
    # language_preference, e.g. -10), then break ties by bitrate.
    best_audio = max(
        audio_fmts_raw,
        key=lambda f: (f.get('language_preference') or 0, f.get('tbr') or 0),
    ) if audio_fmts_raw else None
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
            best_other = max(
                other_audio_fmts,
                key=lambda f: (f.get('language_preference') or 0, f.get('tbr') or 0),
            )
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

    # yt-dlp rounds `duration` to int, which makes dash.js stop playback up to
    # ~1s before the real end. YouTube's format URLs expose the precise float
    # duration via the `dur` query param — prefer the max seen across formats.
    precise_duration = duration
    for fmt in valid_video + audio_fmts:
        m = re.search(r'[?&]dur=([0-9.]+)', fmt.get('url') or '')
        if m:
            d = float(m.group(1))
            if d > precise_duration:
                precise_duration = d

    # Build MPD XML — single video AdaptationSet, single audio AdaptationSet
    mpd_lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" '
        'profiles="urn:mpeg:dash:profile:isoff-on-demand:2011" '
        f'minBufferTime="PT1.5S" type="static" '
        f'mediaPresentationDuration="PT{precise_duration:.3f}S">',
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
        height = fmt.get('height', 0)
        width = fmt.get('width', 0)
        fps = fmt.get('fps', 30)
        codecs = _normalize_vp9_codec(fmt.get('vcodec', 'avc1.4d401e'), height, fps)
        bandwidth = int((fmt.get('tbr') or fmt.get('vbr') or 0) * 1000) or 1000000

        mpd_lines.append(
            f'<Representation id="{fmt.get("format_id", i)}" '
            f'codecs="{codecs}" width="{width}" height="{height}" '
            f'bandwidth="{bandwidth}" frameRate="{fps}">'
        )
        mpd_lines.append(f'<BaseURL>{xml_escape(proxy_url)}</BaseURL>')

        # ── WebM last-segment bug workaround ────────────────────────────────
        # dash.js (verified on v4.x AND v5.x) mis-handles the LAST segment of
        # a WebM file when segments are defined implicitly via SegmentBase +
        # indexRange pointing at the Cues element. The parser lazily derives
        # each segment's byte range from consecutive CueClusterPositions, but
        # for the final CuePoint there is no "next" to delimit it, and the
        # code path that should fall back to EOF truncates — so trailing
        # cluster content (frames encoded after the last cue time) is never
        # fetched or decoded. Symptom on YouTube 4K WebM: the video freezes
        # ~5s before the real end, cutting off fade-outs and end credits.
        #
        # Root cause lives inside dash.js, not in the WebM file — yt-dlp
        # downloads the file intact and ffprobe reads every frame, and
        # YouTube's own (non-dash.js) player plays it fine. We could not find
        # a dash.js setting or attribute that fixes it.
        #
        # Workaround: sidestep the Cues parser entirely by emitting an
        # explicit <SegmentList> with per-segment byte ranges AND an explicit
        # <SegmentTimeline> with per-segment durations. The last SegmentURL's
        # mediaRange ends at `filesize - 1`, which forces dash.js to fetch the
        # whole trailing cluster. No implicit derivation, no bug.
        #
        # Fallback: if we lack either parsed cue points or a filesize, we
        # fall back to SegmentBase — same as before this workaround. That
        # path still works for the majority of videos whose last cluster
        # happens to start close to the end of the file (no fade-out tail).
        #
        # Dependencies / risks:
        #  - `probe['cues']` comes from our own WebM EBML parser in
        #    container.py — stable WebM spec, not YouTube-specific.
        #  - `filesize` comes from yt-dlp; if missing or approximate, the
        #    last mediaRange may slightly overshoot EOF. Browsers handle
        #    this gracefully (CDN returns what exists).
        #  - The MPD grows roughly linearly with cue count (for a 15-min 4K
        #    video with 5 reps: ~40 KB vs ~2 KB for SegmentBase). Still tiny.
        #  - If a future dash.js release fixes the SegmentBase+Cues bug,
        #    we can delete this block and keep only the SegmentBase path.
        filesize = fmt.get('filesize') or fmt.get('filesize_approx') or 0
        cues = probe.get('cues') or []
        if video_container == 'webm' and cues and filesize:
            mpd_lines.append(
                f'<SegmentList timescale="1000" duration="1000">'
                f'<Initialization range="0-{probe["init_end"]}"/>'
            )
            # Per-segment durations: each cue's duration is the delta to the
            # next cue; the last cue extends to the MPD's precise duration.
            mpd_lines.append('<SegmentTimeline>')
            for ci in range(len(cues)):
                t_cur = cues[ci][0]
                t_next = cues[ci + 1][0] if ci + 1 < len(cues) else int(precise_duration * 1000)
                d = max(t_next - t_cur, 1)
                mpd_lines.append(f'<S t="{t_cur}" d="{d}"/>')
            mpd_lines.append('</SegmentTimeline>')
            # Per-segment byte ranges: each segment starts at its cluster
            # position and ends just before the next cluster. The last
            # segment explicitly ends at filesize-1 → trailing cluster
            # included in full (this is the fix).
            for ci in range(len(cues)):
                b_start = cues[ci][1]
                b_end = (cues[ci + 1][1] - 1) if ci + 1 < len(cues) else (filesize - 1)
                mpd_lines.append(
                    f'<SegmentURL mediaRange="{b_start}-{b_end}"/>'
                )
            mpd_lines.append('</SegmentList>')
        else:
            # Fallback path: SegmentBase with indexRange. dash.js will parse
            # the file's Cues itself. Works fine for MP4 (uses SIDX, not
            # affected by the WebM bug) and for WebM where we happen to
            # lack filesize or cues (the trailing-cluster truncation may
            # still occur but there is nothing further we can do without
            # the prerequisites).
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
            'Access-Control-Allow-Headers': 'Authorization, Content-Type, Range',
            'Access-Control-Max-Age': '86400',
        },
    )


@router.get("/api/videoplayback")
async def videoplayback_proxy(url: str, request: Request, auth: bool = Depends(require_auth_or_embed)):
    """Proxy range requests to YouTube CDN for DASH playback."""
    if not is_youtube_url(url):
        raise HTTPException(status_code=403, detail="URL not allowed")
    return await proxy_range_request(request, url)
