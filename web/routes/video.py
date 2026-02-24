# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Video routes: info, subtitle, stream-live."""
import asyncio
import logging
import re
import time

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import FileResponse, JSONResponse, Response

from auth import require_auth, require_auth_or_embed
from dash import proxy_range_request
from helpers import CACHE_DIR, VIDEO_ID_RE, format_number, register_cleanup, make_cache_cleanup, get_video_info as _cached_info, http_client

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

_SKIP_LANGS = {'live_chat', 'rechat'}
_LANG_RE = re.compile(r'^[a-zA-Z]{2,3}(-[a-zA-Z0-9]{1,8})*$')


def _check_video_id(video_id: str):
    if not VIDEO_ID_RE.match(video_id):
        raise HTTPException(status_code=400, detail="Invalid video ID")


# Cache subtitle URLs per video (populated by /api/info, consumed by /api/subtitle)
# Each entry: {lang: {auto, url}, ..., "created": float}
_subtitle_cache: dict = {}
_SUBTITLE_CACHE_TTL = 5 * 3600


register_cleanup(make_cache_cleanup(_subtitle_cache, _SUBTITLE_CACHE_TTL, "subtitle"))


@router.get("/info/{video_id}")
async def get_video_info(video_id: str, auth: bool = Depends(require_auth_or_embed)):
    """Get video info (views, likes, etc.)"""
    _check_video_id(video_id)
    try:
        info = await asyncio.to_thread(_cached_info, video_id)

        upload_date = info.get('upload_date', '')
        if upload_date and len(upload_date) == 8:
            upload_date = f"{upload_date[6:8]}/{upload_date[4:6]}/{upload_date[0:4]}"

        cache_entry: dict = {}
        subtitle_tracks = []

        for lang, formats in info.get('subtitles', {}).items():
            if lang in _SKIP_LANGS:
                continue
            vtt = next((f for f in formats if f.get('ext') == 'vtt'), None)
            if vtt:
                name = next((f.get('name') for f in formats if f.get('name')), lang)
                cache_entry[lang] = {'auto': False, 'url': vtt['url']}
                subtitle_tracks.append({'lang': lang, 'label': name, 'auto': False})

        # Only include the original-language auto-caption (translations get 429'd by YouTube)
        for lang, formats in info.get('automatic_captions', {}).items():
            if lang in _SKIP_LANGS or lang in cache_entry:
                continue
            # Skip translated auto-captions: check if URL has &tlang (= translation)
            vtt = next((f for f in formats if f.get('ext') == 'vtt'), None)
            if not vtt:
                continue
            vtt_url = vtt.get('url', '')
            if '&tlang=' in vtt_url or '?tlang=' in vtt_url:
                continue  # translated auto-caption, will 429
            name = next((f.get('name') for f in formats if f.get('name')), lang)
            cache_entry[lang] = {'auto': True, 'url': vtt_url}
            subtitle_tracks.append({'lang': lang, 'label': name, 'auto': True})

        cache_entry['created'] = time.time()
        _subtitle_cache[video_id] = cache_entry

        # Detect multi-audio: count distinct languages among audio formats
        audio_langs = set()
        for fmt in info.get('formats', []):
            if fmt.get('acodec') not in (None, 'none') and fmt.get('language'):
                audio_langs.add(fmt['language'])
        has_multi_audio = len(audio_langs) > 1

        return {
            'title': info.get('title', 'Unknown'),
            'channel': info.get('channel') or info.get('uploader', 'Unknown'),
            'channel_id': info.get('channel_id', ''),
            'upload_date': upload_date,
            'duration': info.get('duration', 0),
            'views': format_number(info.get('view_count')),
            'likes': format_number(info.get('like_count')),
            'description': info.get('description', ''),
            'subtitle_tracks': subtitle_tracks,
            'is_live': bool(info.get('is_live')),
            'has_multi_audio': has_multi_audio,
            'hls_manifest_url': f'/api/hls/master/{video_id}' if has_multi_audio else None,
        }
    except Exception as e:
        err_msg = str(e)
        if 'Sign in' in err_msg or 'bot' in err_msg:
            return JSONResponse(status_code=503, content={
                'error': 'rate_limited',
                'message': 'YouTube is temporarily blocking requests. Try again later.',
            })
        # Clean up yt-dlp error prefix like "ERROR: [youtube] ID: "
        clean_msg = re.sub(r'^(?:ERROR:\s*)?\[youtube\]\s*[\w-]+:\s*', '', err_msg)
        raise HTTPException(status_code=500, detail=clean_msg or err_msg)


@router.get("/subtitle/{video_id}")
async def get_subtitle(video_id: str, lang: str, auth: bool = Depends(require_auth_or_embed)):
    """Proxy a subtitle VTT file (original language or manual subs only)."""
    _check_video_id(video_id)
    if not _LANG_RE.match(lang):
        raise HTTPException(status_code=400, detail="Invalid language code")
    # Check local cache first
    def _find_local():
        matches = list(CACHE_DIR.glob(f"{video_id}.{lang}.vtt"))
        return matches[0] if matches else None

    found = _find_local()
    if found:
        return FileResponse(found, media_type='text/vtt', headers={'Cache-Control': 'max-age=3600'})

    # Fetch from cached YouTube URL
    cache = _subtitle_cache.get(video_id, {})
    sub_info = cache.get(lang) or cache.get(lang.split('-')[0])
    if sub_info and sub_info.get('url'):
        try:
            resp = await http_client.get(sub_info['url'])
            if resp.status_code == 200:
                out_path = CACHE_DIR / f"{video_id}.{lang}.vtt"
                out_path.write_bytes(resp.content)
                return Response(resp.content, media_type='text/vtt',
                                headers={'Cache-Control': 'max-age=3600'})
            log.warning(f"Subtitle fetch {lang}: HTTP {resp.status_code}")
        except Exception as e:
            log.warning(f"Subtitle fetch {lang} failed: {e}")

    raise HTTPException(status_code=404, detail="Subtitle not available")


@router.get("/stream-live/{video_id}")
async def stream_live(video_id: str, request: Request, auth: bool = Depends(require_auth_or_embed)):
    """Fallback: proxy progressive format (22/18) with range requests."""
    _check_video_id(video_id)
    try:
        info = await asyncio.to_thread(_cached_info, video_id)

        video_url = None
        filesize = None
        selected_format = None

        for fmt_id in ('22', '18'):
            for fmt in info.get('formats', []):
                if fmt.get('format_id') == fmt_id and fmt.get('url'):
                    video_url = fmt['url']
                    filesize = fmt.get('filesize') or fmt.get('filesize_approx')
                    selected_format = fmt_id
                    break
            if video_url:
                break

        if not video_url:
            for fmt in info.get('formats', []):
                proto = fmt.get('protocol', '')
                if (fmt.get('acodec') not in (None, 'none') and
                        fmt.get('vcodec') not in (None, 'none') and
                        fmt.get('url') and proto in ('https', 'http')):
                    video_url = fmt['url']
                    filesize = fmt.get('filesize') or fmt.get('filesize_approx')
                    selected_format = fmt.get('format_id')
                    break

        if not video_url:
            raise HTTPException(status_code=404, detail="No suitable format found")

        log.info(f"stream-live {video_id}: progressive proxy format {selected_format}")

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Failed to get video URL: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return await proxy_range_request(request, video_url, filesize)
