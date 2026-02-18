"""Video routes: play, info, subtitle, formats, progress, cancel, stream, stream-live."""
import asyncio
import logging
import time

import httpx
import yt_dlp
from fastapi import APIRouter, HTTPException, Query, Request, Depends
from fastapi.responses import FileResponse, Response

from auth import require_auth
from dash import proxy_range_request
from helpers import (
    DOWNLOADS_DIR, YDL_OPTS, active_downloads, ydl_info,
    _yt_url, format_number, format_bytes,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# Cache subtitle URLs per video (populated by /api/info, consumed by /api/subtitle)
_subtitle_cache: dict = {}

# Cache subtitle download failures to avoid hammering YouTube with 429s
_subtitle_fail_cache: dict = {}
_SUBTITLE_FAIL_TTL = 300  # 5 minutes


@router.get("/play/{video_id}")
async def play_video(video_id: str, quality: int = Query(default=0), auth: bool = Depends(require_auth)):
    """Start download and return stream URL."""
    video_path = DOWNLOADS_DIR / f"{video_id}.mp4"

    if video_path.exists() and video_id not in active_downloads:
        return {"status": "ready", "url": f"/api/stream/{video_id}"}

    if video_id in active_downloads:
        dl = active_downloads[video_id]
        return {
            "status": dl.get('status', 'downloading'),
            "progress": dl.get('progress', 0),
            "message": dl.get('message', ''),
            "url": f"/api/stream/{video_id}"
        }

    active_downloads[video_id] = {
        "status": "starting",
        "progress": 0,
        "message": "Starting...",
        "process": None,
    }

    async def download():
        try:
            url = _yt_url(video_id)
            log.info(f"Starting download for {video_id} (quality={quality or 'best'})")

            active_downloads[video_id]['status'] = 'downloading'
            active_downloads[video_id]['message'] = 'Downloading...'

            if quality > 0:
                fmt = f'bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={quality}]+bestaudio/best[height<={quality}]'
            else:
                fmt = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best'

            process = await asyncio.create_subprocess_exec(
                'yt-dlp',
                '-f', fmt,
                '--merge-output-format', 'mp4',
                '--write-sub',
                '--write-auto-sub',
                '--sub-langs', 'all',
                '--sub-format', 'vtt',
                '--convert-subs', 'vtt',
                '-o', str(video_path),
                '--no-warnings',
                '--progress',
                '--newline',
                url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            active_downloads[video_id]['process'] = process

            is_video_phase = True
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                line = line.decode().strip()

                if 'Destination:' in line and ('.m4a' in line or 'audio' in line.lower()):
                    is_video_phase = False
                    active_downloads[video_id]['message'] = 'Audio...'
                elif '[Merger]' in line:
                    active_downloads[video_id]['progress'] = 99
                    active_downloads[video_id]['message'] = 'Merging...'
                elif '[download]' in line and '%' in line and is_video_phase:
                    try:
                        pct = float(line.split('%')[0].split()[-1])
                        active_downloads[video_id]['progress'] = pct
                        active_downloads[video_id]['message'] = f'{pct:.0f}%'
                    except:
                        pass

            await process.wait()

            if process.returncode == 0 and video_path.exists():
                log.info(f"Complete: {video_id} ({video_path.stat().st_size} bytes)")
                active_downloads[video_id]['status'] = 'finished'
                active_downloads[video_id]['progress'] = 100
                active_downloads[video_id]['message'] = 'Complete'
            else:
                raise Exception("Download failed")

        except Exception as e:
            log.error(f"Download error for {video_id}: {e}")
            active_downloads[video_id]['status'] = 'error'
            active_downloads[video_id]['message'] = str(e)[:100]
        finally:
            await asyncio.sleep(60)
            active_downloads.pop(video_id, None)

    asyncio.create_task(download())
    await asyncio.sleep(0.3)

    return {
        "status": "downloading",
        "progress": 0,
        "message": "Starting...",
        "url": f"/api/stream/{video_id}"
    }


@router.get("/info/{video_id}")
async def get_video_info(video_id: str, auth: bool = Depends(require_auth)):
    """Get video info (views, likes, etc.)"""
    try:
        url = _yt_url(video_id)
        def _extract():
            with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
                return ydl.extract_info(url, download=False)
        info = await asyncio.to_thread(_extract)

        upload_date = info.get('upload_date', '')
        if upload_date and len(upload_date) == 8:
            upload_date = f"{upload_date[6:8]}/{upload_date[4:6]}/{upload_date[0:4]}"

        _SKIP_LANGS = {'live_chat', 'rechat'}
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

        for lang, formats in info.get('automatic_captions', {}).items():
            if lang in _SKIP_LANGS or lang in cache_entry:
                continue
            vtt = next((f for f in formats if f.get('ext') == 'vtt'), None)
            if vtt:
                name = next((f.get('name') for f in formats if f.get('name')), lang)
                cache_entry[lang] = {'auto': True, 'url': vtt['url']}
                subtitle_tracks.append({'lang': lang, 'label': f"{name} (auto)", 'auto': True})

        _subtitle_cache[video_id] = cache_entry

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
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/subtitle/{video_id}")
async def get_subtitle(video_id: str, lang: str, auth: bool = Depends(require_auth)):
    """Proxy a subtitle VTT file."""
    def _find_local():
        matches = list(DOWNLOADS_DIR.glob(f"{video_id}*.{lang}.vtt"))
        return matches[0] if matches else None

    found = _find_local()
    if found:
        return FileResponse(found, media_type='text/vtt', headers={'Cache-Control': 'max-age=3600'})

    fail_key = (video_id, lang)
    if fail_key in _subtitle_fail_cache:
        if time.time() - _subtitle_fail_cache[fail_key] < _SUBTITLE_FAIL_TTL:
            raise HTTPException(status_code=404, detail="Subtitle unavailable (rate-limited)")
        del _subtitle_fail_cache[fail_key]

    cache = _subtitle_cache.get(video_id, {})
    sub_info = cache.get(lang) or cache.get(lang.split('-')[0])
    if sub_info and sub_info.get('url'):
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(sub_info['url'])
            if resp.status_code == 200:
                out_path = DOWNLOADS_DIR / f"{video_id}.{lang}.vtt"
                out_path.write_bytes(resp.content)
                return Response(resp.content, media_type='text/vtt',
                                headers={'Cache-Control': 'max-age=3600'})
        except Exception:
            pass

    yt_url = _yt_url(video_id)
    out_tpl = str(DOWNLOADS_DIR / video_id)

    def _download_sub():
        opts = {
            **YDL_OPTS,
            'skip_download': True,
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': [lang],
            'subtitlesformat': 'vtt',
            'convertsubtitles': 'vtt',
            'outtmpl': out_tpl,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([yt_url])

    try:
        await asyncio.to_thread(_download_sub)
    except Exception as e:
        _subtitle_fail_cache[fail_key] = time.time()
        raise HTTPException(status_code=500, detail=str(e))

    found = _find_local()
    if not found:
        _subtitle_fail_cache[fail_key] = time.time()
        raise HTTPException(status_code=404, detail="Subtitle not found")

    return FileResponse(found, media_type='text/vtt', headers={'Cache-Control': 'max-age=3600'})


@router.get("/formats/{video_id}")
async def get_formats(video_id: str, auth: bool = Depends(require_auth)):
    """Get available download qualities."""
    try:
        url = _yt_url(video_id)
        info = await asyncio.to_thread(ydl_info.extract_info, url, download=False)

        qualities = {}
        for fmt in info.get('formats', []):
            if fmt.get('vcodec') in (None, 'none'):
                continue
            if fmt.get('acodec') not in (None, 'none'):
                continue
            height = fmt.get('height') or 0
            if height < 360:
                continue
            size = fmt.get('filesize') or fmt.get('filesize_approx') or 0
            if height not in qualities or size > qualities[height]['size']:
                qualities[height] = {'format_id': fmt.get('format_id'), 'size': size}

        options = []
        for height in sorted(qualities.keys()):
            q = qualities[height]
            size = int(q['size'] * 1.15) if q['size'] else 0
            options.append({
                'height': height,
                'label': f"{height}p",
                'size': size,
                'size_str': format_bytes(size) if size else None,
            })

        return {'options': options}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/progress/{video_id}")
async def get_progress(video_id: str, auth: bool = Depends(require_auth)):
    """Get download progress."""
    video_path = DOWNLOADS_DIR / f"{video_id}.mp4"

    if video_id in active_downloads:
        dl = active_downloads[video_id]
        return {
            "status": dl.get('status', 'unknown'),
            "progress": dl.get('progress', 0),
            "message": dl.get('message', ''),
        }
    elif video_path.exists():
        return {"status": "ready", "progress": 100, "message": "Ready"}
    else:
        return {"status": "not_found", "progress": 0, "message": "Not found"}


@router.post("/cancel/{video_id}")
async def cancel_download(video_id: str, auth: bool = Depends(require_auth)):
    """Cancel an active download."""
    if video_id in active_downloads:
        dl = active_downloads[video_id]
        process = dl.get('process')
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                process.kill()
            log.info(f"Cancelled download for {video_id}")
        dl['status'] = 'cancelled'
        dl['message'] = 'Cancelled'

    for f in DOWNLOADS_DIR.glob(f"{video_id}.*"):
        try:
            f.unlink()
        except:
            pass

    return {"status": "cancelled"}


@router.get("/stream/{video_id}")
async def stream_video(video_id: str, auth: bool = Depends(require_auth)):
    """Serve downloaded video file."""
    video_path = DOWNLOADS_DIR / f"{video_id}.mp4"
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    return FileResponse(video_path, media_type='video/mp4')


@router.get("/stream-live/{video_id}")
async def stream_live(video_id: str, request: Request, auth: bool = Depends(require_auth)):
    """Fallback: proxy progressive format (22/18) with range requests."""
    url = _yt_url(video_id)

    try:
        info = await asyncio.to_thread(ydl_info.extract_info, url, download=False)

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
