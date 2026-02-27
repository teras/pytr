# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""HLS streaming: proxy YouTube HLS manifests and segments to avoid CORS.

YouTube multi-audio videos use muxed variants (audio+video per stream) with
YT-EXT-AUDIO-CONTENT-ID rather than standard #EXT-X-MEDIA TYPE=AUDIO.
We parse these and filter the manifest per audio language, so HLS.js only
sees quality levels for the selected language.
"""
import asyncio
import logging
import re
import time
from urllib.parse import quote, urljoin

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import Response, StreamingResponse

from auth import require_auth, require_auth_or_embed
from helpers import register_cleanup, make_cache_cleanup, get_video_info, invalidate_video_cache, http_client, is_youtube_url, VIDEO_ID_RE

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/hls")

# Raw (rewritten) manifest cache: video_id -> {"manifest": str, "created": float}
_hls_cache: dict = {}
_HLS_CACHE_TTL = 5 * 3600  # URLs expire after ~6h, refresh at 5h


register_cleanup(make_cache_cleanup(_hls_cache, _HLS_CACHE_TTL, "HLS"))


_RE_AUDIO_ID = re.compile(r'YT-EXT-AUDIO-CONTENT-ID="([^"]+)"')
_RE_URI = re.compile(r'URI="([^"]+)"')


def _rewrite_uris_in_line(line: str, base_url: str, proxy_path: str) -> str:
    """Rewrite URI="..." attributes in a manifest line."""
    def _sub(m):
        uri = m.group(1)
        absolute = urljoin(base_url, uri)
        return f'URI="{proxy_path}?url={quote(absolute, safe="")}"'
    return _RE_URI.sub(_sub, line)


def _rewrite_master_manifest(manifest_text: str, base_url: str) -> str:
    """Rewrite a master HLS manifest: proxy all playlist URIs."""
    lines = manifest_text.splitlines()
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith('#'):
            absolute = urljoin(base_url, stripped)
            out.append(f'/api/hls/playlist?url={quote(absolute, safe="")}')
        elif stripped.startswith('#EXT-X-MEDIA') and 'URI="' in stripped:
            out.append(_rewrite_uris_in_line(stripped, base_url, '/api/hls/playlist'))
        elif stripped.startswith('#EXT-X-MAP') and 'URI="' in stripped:
            out.append(_rewrite_uris_in_line(stripped, base_url, '/api/hls/segment'))
        else:
            out.append(stripped)
    return '\n'.join(out)


def _rewrite_media_playlist(playlist_text: str, base_url: str) -> str:
    """Rewrite a media playlist: proxy all segment URIs."""
    lines = playlist_text.splitlines()
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith('#'):
            absolute = urljoin(base_url, stripped)
            out.append(f'/api/hls/segment?url={quote(absolute, safe="")}')
        elif stripped.startswith('#EXT-X-MAP') and 'URI="' in stripped:
            out.append(_rewrite_uris_in_line(stripped, base_url, '/api/hls/segment'))
        else:
            out.append(stripped)
    return '\n'.join(out)


def _extract_audio_langs(manifest: str) -> list[dict]:
    """Extract available audio languages from YT-EXT-AUDIO-CONTENT-ID tags.

    Returns list of {lang, default} sorted with default first.
    """
    langs = set()
    for line in manifest.splitlines():
        m = _RE_AUDIO_ID.search(line)
        if m:
            # e.g. "en.3" -> "en", "en-GB.3" -> "en-GB"
            lang = m.group(1).rsplit('.', 1)[0]
            langs.add(lang)

    if not langs:
        return []

    # The default audio has no YT-EXT-AUDIO-CONTENT-ID — we label it "original"
    result = [{'lang': 'original', 'default': True}]
    for lang in sorted(langs):
        result.append({'lang': lang, 'default': False})
    return result


def _filter_manifest_by_audio(manifest: str, audio_lang: str | None) -> str:
    """Filter manifest variants to only those matching the given audio language.

    audio_lang=None or 'original': keep only variants WITHOUT YT-EXT-AUDIO-CONTENT-ID
    audio_lang='fr': keep only variants with YT-EXT-AUDIO-CONTENT-ID="fr.N"
    """
    lines = manifest.splitlines()
    out = []
    skip_next_uri = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith('#EXT-X-STREAM-INF'):
            has_audio_id = _RE_AUDIO_ID.search(stripped)

            if audio_lang is None or audio_lang == 'original':
                # Keep only default (no audio content ID)
                if has_audio_id:
                    skip_next_uri = True
                    continue
            else:
                # Keep only matching language
                if has_audio_id:
                    lang_in_tag = has_audio_id.group(1).rsplit('.', 1)[0]
                    if lang_in_tag != audio_lang:
                        skip_next_uri = True
                        continue
                else:
                    # Default variant — skip when specific lang requested
                    skip_next_uri = True
                    continue

            skip_next_uri = False
            out.append(stripped)
        elif stripped and not stripped.startswith('#'):
            # URI line following #EXT-X-STREAM-INF
            if skip_next_uri:
                skip_next_uri = False
                continue
            out.append(stripped)
        else:
            out.append(stripped)

    return '\n'.join(out)


@router.get("/master/{video_id}")
async def get_hls_master(
    video_id: str,
    audio: str | None = None,
    live: bool = False,
    cookies: str = "auto",
    auth: bool = Depends(require_auth_or_embed),
):
    """Fetch YouTube's HLS master manifest, filter by audio language, rewrite URIs.

    ?audio=fr  -> only French audio variants
    ?audio=original or omitted -> only default audio variants
    ?live=1    -> skip cache (live streams need fresh URLs)
    ?cookies=off|auto|on -> cookie mode for yt-dlp
    """
    if not VIDEO_ID_RE.match(video_id):
        raise HTTPException(status_code=400, detail="Invalid video ID")
    # Get or fetch the full rewritten manifest
    cached = _hls_cache.get(video_id) if not live else None
    if not cached or time.time() - cached['created'] >= _HLS_CACHE_TTL:
        try:
            info = await asyncio.to_thread(get_video_info, video_id, cookies)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

        manifest_url = info.get('manifest_url')
        if not manifest_url:
            for fmt in info.get('formats', []):
                if fmt.get('manifest_url'):
                    manifest_url = fmt['manifest_url']
                    break
        if not manifest_url:
            raise HTTPException(status_code=404, detail="No HLS manifest available")

        resp = await http_client.get(manifest_url)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Failed to fetch HLS manifest: {resp.status_code}")

        rewritten = _rewrite_master_manifest(resp.text, manifest_url)
        cached = {'manifest': rewritten, 'created': time.time()}
        _hls_cache[video_id] = cached
        log.info(f"HLS master {video_id}: fetched and cached")

    # Filter by audio language
    filtered = _filter_manifest_by_audio(cached['manifest'], audio)

    return Response(filtered, media_type='application/vnd.apple.mpegurl',
                    headers={'Cache-Control': 'no-cache'})


@router.get("/audio-tracks/{video_id}")
async def get_audio_tracks(video_id: str, auth: bool = Depends(require_auth_or_embed)):
    """Return available audio languages for a video (from cached HLS manifest)."""
    if not VIDEO_ID_RE.match(video_id):
        raise HTTPException(status_code=400, detail="Invalid video ID")
    cached = _hls_cache.get(video_id)
    if not cached or time.time() - cached['created'] >= _HLS_CACHE_TTL:
        # Trigger manifest fetch by calling master endpoint logic
        await get_hls_master(video_id, auth=auth)
        cached = _hls_cache.get(video_id)

    if not cached:
        return {'audio_tracks': []}

    tracks = _extract_audio_langs(cached['manifest'])
    return {'audio_tracks': tracks}


@router.get("/playlist")
async def get_hls_playlist(url: str):
    """Fetch an HLS media playlist and rewrite segment URIs to proxy."""
    if not is_youtube_url(url):
        raise HTTPException(status_code=403, detail="URL not allowed")
    resp = await http_client.get(url)
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Failed to fetch playlist: {resp.status_code}")

    rewritten = _rewrite_media_playlist(resp.text, url)
    return Response(rewritten, media_type='application/vnd.apple.mpegurl',
                    headers={'Cache-Control': 'no-cache'})


@router.get("/segment")
async def get_hls_segment(url: str, request: Request):
    """Stream an HLS segment from YouTube CDN (passthrough)."""
    if not is_youtube_url(url):
        raise HTTPException(status_code=403, detail="URL not allowed")
    try:
        upstream_headers = {}
        range_header = request.headers.get('range')
        if range_header:
            upstream_headers['Range'] = range_header

        upstream = await http_client.send(
            http_client.build_request('GET', url, headers=upstream_headers),
            stream=True,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Segment fetch failed: {e}")

    if upstream.status_code >= 400:
        await upstream.aclose()
        raise HTTPException(status_code=upstream.status_code, detail="Segment upstream error")

    resp_headers = {
        'Access-Control-Allow-Origin': '*',
        'Cache-Control': 'max-age=300',
    }
    ct = upstream.headers.get('content-type', 'video/mp2t')
    resp_headers['Content-Type'] = ct
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
