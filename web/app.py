"""YTP - FastAPI Backend"""
import asyncio
import json as json_module
import logging
import os
import re
import secrets
import struct
import time
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape as xml_escape

import httpx
import yt_dlp
from dotenv import load_dotenv

# Load .env from current dir or parent dir (for local dev)
load_dotenv()
load_dotenv(Path(__file__).parent.parent / ".env")
from fastapi import FastAPI, HTTPException, Query, Request, Response, Depends, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, HTMLResponse, RedirectResponse

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)

app = FastAPI(title="YTP")

# Authentication
AUTH_PASSWORD = os.environ.get('YTP_PASSWORD')
AUTH_SESSIONS = {}  # token -> expiry_time
AUTH_FAILURES = {}  # ip -> {"count": int, "blocked_until": float}

def get_client_ip(request: Request) -> str:
    """Get client IP, checking X-Forwarded-For for proxies"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host

def is_ip_blocked(ip: str) -> tuple[bool, int]:
    """Check if IP is blocked. Returns (blocked, seconds_remaining)"""
    if ip not in AUTH_FAILURES:
        return False, 0
    info = AUTH_FAILURES[ip]
    if info.get("blocked_until", 0) > time.time():
        remaining = int(info["blocked_until"] - time.time())
        return True, remaining
    return False, 0

def record_failure(ip: str):
    """Record a failed login attempt"""
    if ip not in AUTH_FAILURES:
        AUTH_FAILURES[ip] = {"count": 0, "blocked_until": 0}

    AUTH_FAILURES[ip]["count"] += 1
    count = AUTH_FAILURES[ip]["count"]

    if count >= 10:
        # Block for 24 hours
        AUTH_FAILURES[ip]["blocked_until"] = time.time() + 86400
        log.warning(f"IP {ip} blocked for 24 hours after {count} failures")
    elif count >= 5:
        # Block for 1 hour
        AUTH_FAILURES[ip]["blocked_until"] = time.time() + 3600
        log.warning(f"IP {ip} blocked for 1 hour after {count} failures")

def clear_failures(ip: str):
    """Clear failure count on successful login"""
    AUTH_FAILURES.pop(ip, None)

def verify_session(request: Request) -> bool:
    """Check if request has valid session"""
    if not AUTH_PASSWORD:
        return True  # No password set, allow all
    token = request.cookies.get("ytp_session")
    if token and token in AUTH_SESSIONS:
        if AUTH_SESSIONS[token] > time.time():
            return True
        del AUTH_SESSIONS[token]  # Expired
    return False

async def require_auth(request: Request):
    """Dependency that requires authentication"""
    if not AUTH_PASSWORD:
        return True
    if not verify_session(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True

# Downloads directory - clear contents on startup (not the dir itself for Docker volumes)
DOWNLOADS_DIR = Path("downloads")
DOWNLOADS_DIR.mkdir(exist_ok=True)
for f in DOWNLOADS_DIR.iterdir():
    try:
        if f.is_file():
            f.unlink()
        elif f.is_dir():
            import shutil
            shutil.rmtree(f)
    except Exception as e:
        log.warning(f"Could not delete {f}: {e}")
log.info("Cleared downloads cache")

# Track active downloads: video_id -> {"status": str, "progress": float, ...}
active_downloads = {}

# DASH manifest cache: video_id -> {"mpd": str, "created": float, "formats": dict}
_dash_cache: dict = {}
_DASH_CACHE_TTL = 5 * 3600  # URLs expire after ~6h, refresh at 5h

# Cache subtitle URLs per video (populated by /api/info, consumed by /api/subtitle)
_subtitle_cache: dict = {}  # video_id -> {lang: {"auto": bool, "url": str}}

# Cache subtitle download failures to avoid hammering YouTube with 429s
_subtitle_fail_cache: dict = {}  # (video_id, lang) -> timestamp
_SUBTITLE_FAIL_TTL = 300  # 5 minutes

# yt-dlp options - set YOUTUBE_COOKIES_BROWSER env var to enable (e.g. "chrome", "firefox")
_cookies_browser = os.environ.get('YOUTUBE_COOKIES_BROWSER')
YDL_OPTS = {
    'quiet': True,
    'no_warnings': True,
    'remote_components': ['ejs:github'],  # Required for YouTube JS challenge solving
}
if _cookies_browser:
    YDL_OPTS['cookiesfrombrowser'] = (_cookies_browser,)

# yt-dlp instances (reused for speed)
ydl_search = yt_dlp.YoutubeDL({
    **YDL_OPTS,
    'extract_flat': True,
})

ydl_info = yt_dlp.YoutubeDL(YDL_OPTS)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _yt_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _format_duration(seconds) -> str:
    if not seconds:
        return "?"
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def format_number(n):
    """Format large numbers: 1500000 -> 1.5M"""
    if n is None:
        return None
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def format_bytes(b):
    """Format bytes: 1500000 -> 1.4 MB"""
    if b >= 1_000_000_000:
        return f"{b/1_000_000_000:.1f} GB"
    if b >= 1_000_000:
        return f"{b/1_000_000:.1f} MB"
    if b >= 1_000:
        return f"{b/1_000:.1f} KB"
    return f"{b} B"


app.mount("/static", StaticFiles(directory="static"), name="static")

LOGIN_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Login</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background-color: #0f0f0f;
            color: #f1f1f1;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .login-box {
            background-color: #1a1a1a;
            padding: 40px;
            border-radius: 16px;
            width: 100%;
            max-width: 400px;
            margin: 20px;
        }
        .error { color: #ff4444; margin-bottom: 20px; text-align: center; font-size: 14px; }
        .blocked { color: #ff8800; }
        input[type="password"] {
            width: 100%;
            padding: 14px 18px;
            font-size: 16px;
            border: 1px solid #303030;
            border-radius: 12px;
            background-color: #121212;
            color: #f1f1f1;
            margin-bottom: 15px;
        }
        input[type="password"]:focus { border-color: #3ea6ff; outline: none; }
        .remember-row {
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 20px;
            font-size: 14px;
            color: #aaa;
        }
        input[type="checkbox"] { width: 18px; height: 18px; }
        button {
            width: 100%;
            padding: 14px;
            font-size: 16px;
            background-color: #cc0000;
            color: #fff;
            border: none;
            border-radius: 12px;
            cursor: pointer;
            font-weight: 500;
        }
        button:hover { background-color: #ee0000; }
    </style>
</head>
<body>
    <form class="login-box" method="POST" action="/login">
        {{ERROR_PLACEHOLDER}}
        <input type="password" name="password" placeholder="Password" autofocus autocomplete="current-password">
        <label class="remember-row">
            <input type="checkbox" name="remember" value="1">
            Remember this device (30 days)
        </label>
        <button type="submit">Login</button>
    </form>
</body>
</html>"""


@app.get("/")
async def index(request: Request):
    if AUTH_PASSWORD and not verify_session(request):
        return RedirectResponse(url="/login", status_code=302)
    return FileResponse("static/index.html")


@app.get("/watch")
async def watch_page(request: Request):
    """Serve index.html for /watch?v=xxx (SPA routing)"""
    if AUTH_PASSWORD and not verify_session(request):
        return RedirectResponse(url="/login", status_code=302)
    return FileResponse("static/index.html")


@app.get("/channel/{channel_id}")
async def channel_page(request: Request, channel_id: str):
    """Serve index.html for /channel/xxx (SPA routing)"""
    if AUTH_PASSWORD and not verify_session(request):
        return RedirectResponse(url="/login", status_code=302)
    return FileResponse("static/index.html")


@app.get("/login")
async def login_page(request: Request, error: str = ""):
    if not AUTH_PASSWORD:
        return RedirectResponse(url="/", status_code=302)
    if verify_session(request):
        return RedirectResponse(url="/", status_code=302)

    ip = get_client_ip(request)
    blocked, remaining = is_ip_blocked(ip)

    if blocked:
        minutes = remaining // 60
        hours = minutes // 60
        if hours > 0:
            time_str = f"{hours}h {minutes % 60}m"
        else:
            time_str = f"{minutes}m {remaining % 60}s"
        error_html = f'<p class="error blocked">Too many attempts. Try again in {time_str}</p>'
    elif error:
        error_html = f'<p class="error">{error}</p>'
    else:
        error_html = ""

    return HTMLResponse(LOGIN_PAGE.replace("{{ERROR_PLACEHOLDER}}", error_html))


@app.post("/login")
async def do_login(request: Request, response: Response, password: str = Form(...), remember: str = Form(default="")):
    if not AUTH_PASSWORD:
        return RedirectResponse(url="/", status_code=302)

    ip = get_client_ip(request)
    blocked, remaining = is_ip_blocked(ip)
    if blocked:
        return RedirectResponse(url="/login", status_code=302)

    if password == AUTH_PASSWORD:
        clear_failures(ip)
        token = secrets.token_urlsafe(32)
        # 30 days if remember, else 24 hours
        expiry = time.time() + (30 * 86400 if remember else 86400)
        AUTH_SESSIONS[token] = expiry

        response = RedirectResponse(url="/", status_code=302)
        response.set_cookie(
            key="ytp_session",
            value=token,
            max_age=30 * 86400 if remember else None,
            httponly=True,
            samesite="lax"
        )
        log.info(f"Login successful from {ip}")
        return response
    else:
        record_failure(ip)
        log.warning(f"Failed login attempt from {ip}")
        return RedirectResponse(url="/login?error=Invalid+password", status_code=302)


@app.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("ytp_session")
    if token and token in AUTH_SESSIONS:
        del AUTH_SESSIONS[token]
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("ytp_session")
    return response


@app.get("/auth/status")
async def auth_status():
    """Show blocked IPs (for debugging)"""
    now = time.time()
    blocked = {
        ip: {
            "failures": info["count"],
            "blocked_for": int(info["blocked_until"] - now) if info["blocked_until"] > now else 0
        }
        for ip, info in AUTH_FAILURES.items()
    }
    return {"blocked_ips": blocked, "active_sessions": len(AUTH_SESSIONS)}


@app.get("/api/search")
async def search(q: str = Query(..., min_length=1), count: int = Query(default=10, ge=1), auth: bool = Depends(require_auth)):
    """Search YouTube"""
    try:
        # Cap at 100 results (YouTube's practical limit)
        count = min(count, 100)
        result = ydl_search.extract_info(f"ytsearch{count}:{q}", download=False)

        videos = []
        for entry in result.get('entries', []):
            if not entry:
                continue
            vid = entry.get('id', '')
            duration = entry.get('duration') or 0
            videos.append({
                'id': vid,
                'title': entry.get('title', 'Unknown'),
                'duration': duration,
                'duration_str': _format_duration(duration),
                'channel': entry.get('channel') or entry.get('uploader', 'Unknown'),
                'thumbnail': f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
            })

        return {'results': videos}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/play/{video_id}")
async def play_video(video_id: str, quality: int = Query(default=0), auth: bool = Depends(require_auth)):
    """Start download and return stream URL. Quality=0 means best available."""
    video_path = DOWNLOADS_DIR / f"{video_id}.mp4"

    # Already downloaded?
    if video_path.exists() and video_id not in active_downloads:
        return {"status": "ready", "url": f"/api/stream/{video_id}"}

    # Already downloading?
    if video_id in active_downloads:
        dl = active_downloads[video_id]
        return {
            "status": dl.get('status', 'downloading'),
            "progress": dl.get('progress', 0),
            "message": dl.get('message', ''),
            "url": f"/api/stream/{video_id}"
        }

    # Start new download
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

            # Select format based on requested quality
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

            # Parse progress output - track video only (largest file)
            is_video_phase = True
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                line = line.decode().strip()

                # Detect when audio phase starts (stop updating progress)
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


@app.get("/api/info/{video_id}")
async def get_video_info(video_id: str, auth: bool = Depends(require_auth)):
    """Get video info (views, likes, etc.)"""
    try:
        url = _yt_url(video_id)
        # Use a fresh instance to avoid shared-state issues with concurrent HLS/stream calls
        def _extract():
            with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
                return ydl.extract_info(url, download=False)
        info = await asyncio.to_thread(_extract)

        upload_date = info.get('upload_date', '')
        if upload_date and len(upload_date) == 8:
            upload_date = f"{upload_date[6:8]}/{upload_date[4:6]}/{upload_date[0:4]}"

        # Parse subtitle tracks and populate cache
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


@app.get("/api/subtitle/{video_id}")
async def get_subtitle(video_id: str, lang: str, auth: bool = Depends(require_auth)):
    """Proxy a subtitle VTT file. Tries: local file → cached URL → yt-dlp download."""
    def _find_local():
        matches = list(DOWNLOADS_DIR.glob(f"{video_id}*.{lang}.vtt"))
        return matches[0] if matches else None

    # 1. Local cache (already downloaded)
    found = _find_local()
    if found:
        return FileResponse(found, media_type='text/vtt', headers={'Cache-Control': 'max-age=3600'})

    # 2. Check backend fail cache — avoid hammering YouTube when it 429s
    fail_key = (video_id, lang)
    if fail_key in _subtitle_fail_cache:
        if time.time() - _subtitle_fail_cache[fail_key] < _SUBTITLE_FAIL_TTL:
            raise HTTPException(status_code=404, detail="Subtitle unavailable (rate-limited)")
        del _subtitle_fail_cache[fail_key]

    # 3. Try direct URL from info cache (fast, no yt-dlp process)
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
            pass  # fall through to yt-dlp

    # 4. Fall back to yt-dlp (slower, but handles auth/signing)
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


@app.get("/api/formats/{video_id}")
async def get_formats(video_id: str, auth: bool = Depends(require_auth)):
    """Get available download qualities"""
    try:
        url = _yt_url(video_id)
        info = await asyncio.to_thread(ydl_info.extract_info, url, download=False)

        # Collect all video-only formats (will be merged with audio when downloading)
        qualities = {}  # height -> {format_id, size}
        for fmt in info.get('formats', []):
            if fmt.get('vcodec') in (None, 'none'):
                continue
            if fmt.get('acodec') not in (None, 'none'):
                continue  # Skip combined formats
            height = fmt.get('height') or 0
            if height < 360:
                continue  # Skip very low quality
            size = fmt.get('filesize') or fmt.get('filesize_approx') or 0
            # Keep best format for each height
            if height not in qualities or size > qualities[height]['size']:
                qualities[height] = {
                    'format_id': fmt.get('format_id'),
                    'size': size,
                }

        # Build sorted list (lowest first)
        options = []
        for height in sorted(qualities.keys()):
            q = qualities[height]
            # Estimate total size (video + audio ~15% extra)
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


@app.get("/api/progress/{video_id}")
async def get_progress(video_id: str, auth: bool = Depends(require_auth)):
    """Get download progress"""
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


@app.post("/api/cancel/{video_id}")
async def cancel_download(video_id: str, auth: bool = Depends(require_auth)):
    """Cancel an active download"""
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

    # Clean up partial files
    for f in DOWNLOADS_DIR.glob(f"{video_id}.*"):
        try:
            f.unlink()
        except:
            pass

    return {"status": "cancelled"}


@app.get("/api/stream/{video_id}")
async def stream_video(video_id: str, auth: bool = Depends(require_auth)):
    """Serve downloaded video file."""
    video_path = DOWNLOADS_DIR / f"{video_id}.mp4"
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video not found")

    return FileResponse(video_path, media_type='video/mp4')


@app.get("/api/stream-live/{video_id}")
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

    return await _proxy_range_request(request, video_url, filesize)


# ── DASH Streaming ───────────────────────────────────────────────────────────


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


@app.get("/api/dash/{video_id}")
async def get_dash_manifest(video_id: str, quality: int = Query(default=1080), auth: bool = Depends(require_auth)):
    """Generate DASH MPD manifest with proxied URLs."""

    # Check cache
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
            # Only mp4 container (H.264/AVC) for browser compatibility
            if fmt.get('ext') not in ('mp4', 'mp4a'):
                continue
            video_fmts.append(fmt)
        elif has_audio and not has_video:
            # Only m4a/mp4 audio
            if fmt.get('ext') not in ('m4a', 'mp4'):
                continue
            audio_fmts.append(fmt)

    if not video_fmts or not audio_fmts:
        raise HTTPException(status_code=404, detail="No DASH formats available")

    # Deduplicate: keep best format per height (prefer H.264/avc1 > AV1 for compatibility)
    best_video = {}
    for fmt in video_fmts:
        height = fmt.get('height', 0)
        codec = fmt.get('vcodec', '')
        existing = best_video.get(height)
        if not existing:
            best_video[height] = fmt
        elif codec.startswith('avc1') and not existing.get('vcodec', '').startswith('avc1'):
            # Prefer H.264 over AV1 for broader compatibility
            best_video[height] = fmt
        elif codec.startswith('avc1') == existing.get('vcodec', '').startswith('avc1'):
            # Same codec family — pick higher bitrate
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
    probe_tasks = [_probe_mp4_ranges(fmt['url']) for fmt in all_fmts]
    probe_results = await asyncio.gather(*probe_tasks)

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


@app.options("/api/videoplayback")
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


@app.get("/api/videoplayback")
async def videoplayback_proxy(url: str, request: Request, auth: bool = Depends(require_auth)):
    """Proxy range requests to YouTube CDN for DASH playback."""
    return await _proxy_range_request(request, url)


async def _proxy_range_request(request: Request, video_url: str, filesize: int = None):
    """Proxy a YouTube URL with range request support, forwarding upstream headers."""
    range_header = request.headers.get('range')

    # Build upstream request headers
    upstream_headers = {}
    if range_header:
        upstream_headers['Range'] = range_header
    elif filesize:
        # Always send Range to avoid YouTube throttling
        upstream_headers['Range'] = 'bytes=0-'

    # Open upstream connection to read headers, then stream body
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

    # Build response headers from upstream
    resp_headers = {
        'Accept-Ranges': 'bytes',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Expose-Headers': 'Content-Range, Content-Length',
        'Cache-Control': 'no-cache',
    }

    # Forward content type
    ct = upstream.headers.get('content-type', 'video/mp4')
    resp_headers['Content-Type'] = ct

    # Forward Content-Range and Content-Length from upstream
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


@app.get("/api/related/{video_id}")
async def get_related_videos(video_id: str, auth: bool = Depends(require_auth)):
    """Get related videos for a video"""
    try:
        url = _yt_url(video_id)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        }

        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, follow_redirects=True, timeout=30.0)
            html = resp.text

        # Find ytInitialData JSON
        match = re.search(r'var ytInitialData = ({.*?});', html)
        if not match:
            return {'results': []}

        data = json_module.loads(match.group(1))

        # Navigate to related videos
        contents = data.get('contents', {}).get('twoColumnWatchNextResults', {})
        secondary = contents.get('secondaryResults', {}).get('secondaryResults', {}).get('results', [])

        related = []
        for item in secondary:
            if 'lockupViewModel' in item:
                vm = item['lockupViewModel']
                content_id = vm.get('contentId', '')

                # Skip mixes/playlists (IDs starting with RD)
                if content_id.startswith('RD'):
                    continue

                metadata = vm.get('metadata', {}).get('lockupMetadataViewModel', {})
                title = metadata.get('title', {}).get('content', '')

                # Get channel from metadata
                channel = ''
                metadata_rows = metadata.get('metadata', {}).get('contentMetadataViewModel', {}).get('metadataRows', [])
                if metadata_rows:
                    for row in metadata_rows:
                        parts = row.get('metadataParts', [])
                        if parts:
                            channel = parts[0].get('text', {}).get('content', '')
                            break

                # Duration from contentImage overlay
                duration_str = ''
                content_image = vm.get('contentImage', {}).get('collectionThumbnailViewModel', {})
                primary_thumb = content_image.get('primaryThumbnail', {}).get('thumbnailViewModel', {})
                overlays = primary_thumb.get('overlays', [])
                for overlay in overlays:
                    badge = overlay.get('thumbnailOverlayBadgeViewModel', {})
                    for b in badge.get('thumbnailBadges', []):
                        if 'thumbnailBadgeViewModel' in b:
                            duration_str = b['thumbnailBadgeViewModel'].get('text', '')
                            break

                if content_id and title:
                    related.append({
                        'id': content_id,
                        'title': title,
                        'channel': channel,
                        'duration_str': duration_str,
                        'thumbnail': f"https://i.ytimg.com/vi/{content_id}/mqdefault.jpg",
                    })

        return {'results': related}

    except Exception as e:
        log.error(f"Related videos error: {e}")
        return {'results': []}


@app.get("/api/channel/{channel_id}")
async def get_channel_videos(
    channel_id: str,
    count: int = Query(default=10, ge=1, le=50),
    auth: bool = Depends(require_auth)
):
    """Get videos from a channel"""
    try:
        # Use yt-dlp to get channel videos
        url = f"https://www.youtube.com/channel/{channel_id}/videos"

        # Configure to get more entries for pagination
        ydl_opts = {
            **YDL_OPTS,
            'extract_flat': True,
            'playlistend': count,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = await asyncio.to_thread(ydl.extract_info, url, download=False)

        videos = []
        entries = result.get('entries', [])

        for entry in entries:
            if not entry:
                continue
            vid = entry.get('id', '')
            duration = entry.get('duration') or 0
            videos.append({
                'id': vid,
                'title': entry.get('title', 'Unknown'),
                'duration': duration,
                'duration_str': _format_duration(duration),
                'channel': result.get('channel') or result.get('uploader', 'Unknown'),
                'thumbnail': f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
            })

        return {
            'channel': result.get('channel') or result.get('uploader', 'Unknown'),
            'channel_id': channel_id,
            'results': videos
        }

    except Exception as e:
        log.error(f"Channel videos error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
