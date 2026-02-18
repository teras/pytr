"""YouTube Web App - FastAPI Backend"""
import asyncio
import logging
import os
import httpx
import yt_dlp
import secrets
import time
from pathlib import Path
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

app = FastAPI(title="YouTube Web App")

# Configuration
CONFIG = {
    # 'fast' = 720p max (format 22/18), 'best' = best quality (bestvideo+bestaudio)
    'quality': 'best',
}

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

# yt-dlp options - set YOUTUBE_COOKIES_BROWSER env var to enable (e.g. "chrome", "firefox")
import os
_cookies_browser = os.environ.get('YOUTUBE_COOKIES_BROWSER')
YDL_OPTS = {
    'quiet': True,
    'no_warnings': True,
    'remote_components': ['ejs:github'],  # Required for YouTube JS challenge solving
}
_youtube_cookies = {}
if _cookies_browser:
    YDL_OPTS['cookiesfrombrowser'] = (_cookies_browser,)
    # Extract cookies for use in httpx
    try:
        from yt_dlp.cookies import extract_cookies_from_browser
        cookie_jar = extract_cookies_from_browser(_cookies_browser)
        for cookie in cookie_jar:
            if 'youtube' in cookie.domain or 'google' in cookie.domain:
                _youtube_cookies[cookie.name] = cookie.value
        log.info(f"Extracted {len(_youtube_cookies)} YouTube cookies from {_cookies_browser}")
    except Exception as e:
        log.warning(f"Could not extract cookies: {e}")

# yt-dlp instances (reused for speed)
ydl_search = yt_dlp.YoutubeDL({
    **YDL_OPTS,
    'extract_flat': True,
})

ydl_info = yt_dlp.YoutubeDL(YDL_OPTS)

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

            video_id = entry.get('id', '')
            duration = entry.get('duration') or 0

            if duration:
                duration = int(duration)
                hours, remainder = divmod(duration, 3600)
                minutes, seconds = divmod(remainder, 60)
                duration_str = f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"
            else:
                duration_str = "?"

            videos.append({
                'id': video_id,
                'title': entry.get('title', 'Unknown'),
                'duration': duration,
                'duration_str': duration_str,
                'channel': entry.get('channel') or entry.get('uploader', 'Unknown'),
                'thumbnail': f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
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
            url = f"https://www.youtube.com/watch?v={video_id}"
            log.info(f"Starting download for {video_id} (quality={quality or 'best'})")

            active_downloads[video_id]['status'] = 'downloading'
            active_downloads[video_id]['message'] = 'Downloading...'

            # Select format based on requested quality
            if quality > 0:
                # Specific quality requested
                fmt = f'bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={quality}]+bestaudio/best[height<={quality}]'
            elif CONFIG['quality'] == 'fast':
                fmt = '22/18/best'  # 720p/360p combined, fast
            else:
                fmt = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best'  # Best quality

            process = await asyncio.create_subprocess_exec(
                'yt-dlp',
                '-f', fmt,
                '--merge-output-format', 'mp4',
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


@app.get("/api/info/{video_id}")
async def get_video_info(video_id: str, auth: bool = Depends(require_auth)):
    """Get video info (views, likes, etc.)"""
    try:
        url = f"https://www.youtube.com/watch?v={video_id}"
        info = await asyncio.to_thread(ydl_info.extract_info, url, download=False)

        upload_date = info.get('upload_date', '')
        if upload_date and len(upload_date) == 8:
            upload_date = f"{upload_date[6:8]}/{upload_date[4:6]}/{upload_date[0:4]}"

        return {
            'title': info.get('title', 'Unknown'),
            'channel': info.get('channel') or info.get('uploader', 'Unknown'),
            'channel_id': info.get('channel_id', ''),
            'upload_date': upload_date,
            'duration': info.get('duration', 0),
            'views': format_number(info.get('view_count')),
            'likes': format_number(info.get('like_count')),
            'description': info.get('description', ''),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/formats/{video_id}")
async def get_formats(video_id: str, auth: bool = Depends(require_auth)):
    """Get available download qualities"""
    try:
        url = f"https://www.youtube.com/watch?v={video_id}"
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


def format_bytes(b):
    """Format bytes: 1500000 -> 1.4 MB"""
    if b >= 1_000_000_000:
        return f"{b/1_000_000_000:.1f} GB"
    if b >= 1_000_000:
        return f"{b/1_000_000:.1f} MB"
    if b >= 1_000:
        return f"{b/1_000:.1f} KB"
    return f"{b} B"


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
        video_path = DOWNLOADS_DIR / f"{video_id}.mp4"
        for f in DOWNLOADS_DIR.glob(f"{video_id}.*"):
            try:
                f.unlink()
            except:
                pass
        return {"status": "cancelled"}
    return {"status": "not_found"}


@app.get("/api/stream/{video_id}")
async def stream_video(video_id: str, auth: bool = Depends(require_auth)):
    """Serve video file"""
    video_path = DOWNLOADS_DIR / f"{video_id}.mp4"

    # Wait for download to complete
    for _ in range(600):  # Max 60 seconds
        if video_path.exists() and video_id not in active_downloads:
            break
        if video_path.exists() and active_downloads.get(video_id, {}).get('status') == 'finished':
            break
        await asyncio.sleep(0.1)

    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video not found")

    return FileResponse(video_path, media_type='video/mp4')


@app.get("/api/stream-live/{video_id}")
async def stream_live(video_id: str, request: Request, auth: bool = Depends(require_auth)):
    """Stream video with range request support (proxy to YouTube)"""

    url = f"https://www.youtube.com/watch?v={video_id}"

    # Get direct video URL from yt-dlp
    try:
        info = await asyncio.to_thread(
            ydl_info.extract_info, url, download=False
        )

        # Find best format: prefer direct (22/18), fallback to any progressive
        video_url = None
        filesize = None
        selected_format = None

        # First try format 22 (720p direct)
        for fmt in info.get('formats', []):
            if fmt.get('format_id') == '22' and fmt.get('url'):
                video_url = fmt.get('url')
                filesize = fmt.get('filesize') or fmt.get('filesize_approx')
                selected_format = '22 (720p)'
                break

        # Fallback to format 18 (360p direct)
        if not video_url:
            for fmt in info.get('formats', []):
                if fmt.get('format_id') == '18' and fmt.get('url'):
                    video_url = fmt.get('url')
                    filesize = fmt.get('filesize') or fmt.get('filesize_approx')
                    selected_format = '18 (360p)'
                    break

        # Try any direct progressive format
        if not video_url:
            for fmt in info.get('formats', []):
                protocol = fmt.get('protocol', '')
                if (fmt.get('acodec') not in (None, 'none') and
                    fmt.get('vcodec') not in (None, 'none') and
                    fmt.get('url') and
                    protocol in ('https', 'http')):
                    video_url = fmt.get('url')
                    filesize = fmt.get('filesize') or fmt.get('filesize_approx')
                    selected_format = f"{fmt.get('format_id')} ({fmt.get('height', '?')}p)"
                    break

        if not video_url:
            raise HTTPException(status_code=404, detail="No suitable format found")

        log.info(f"stream-live {video_id}: using format {selected_format}")

    except Exception as e:
        log.error(f"Failed to get video URL: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    # Handle range requests
    range_header = request.headers.get('range')

    async def proxy_stream(start: int = 0, end: int = None):
        headers = {}
        if start > 0 or end:
            if end:
                headers['Range'] = f'bytes={start}-{end}'
            else:
                headers['Range'] = f'bytes={start}-'

        try:
            async with httpx.AsyncClient() as client:
                async with client.stream('GET', video_url, headers=headers, timeout=30.0) as resp:
                    if resp.status_code >= 400:
                        log.warning(f"Upstream error {resp.status_code} for range {start}-{end}")
                        return
                    async for chunk in resp.aiter_bytes(65536):
                        yield chunk
        except Exception as e:
            log.warning(f"Stream error: {e}")
            return

    if range_header:
        # Parse range header: "bytes=start-end" or "bytes=start-"
        range_match = range_header.replace('bytes=', '').split('-')
        start = int(range_match[0]) if range_match[0] else 0
        end = int(range_match[1]) if range_match[1] else None

        # Don't set Content-Length for proxied streams (upstream may fail)
        if filesize:
            end = end or (filesize - 1)
            content_range = f'bytes {start}-{end}/{filesize}'
        else:
            content_range = f'bytes {start}-*/*'

        headers = {
            'Content-Type': 'video/mp4',
            'Accept-Ranges': 'bytes',
            'Content-Range': content_range,
            'Cache-Control': 'no-cache',
        }

        return StreamingResponse(
            proxy_stream(start, end),
            status_code=206,
            headers=headers,
        )
    else:
        # No range - stream from beginning
        headers = {
            'Content-Type': 'video/mp4',
            'Accept-Ranges': 'bytes',
            'Cache-Control': 'no-cache',
        }

        return StreamingResponse(
            proxy_stream(),
            status_code=200,
            headers=headers,
        )


@app.get("/api/hls/{video_id}")
async def get_hls_stream(video_id: str, auth: bool = Depends(require_auth)):
    """Get HLS manifest with proxied segment URLs"""
    from urllib.parse import quote

    url = f"https://www.youtube.com/watch?v={video_id}"

    try:
        info = await asyncio.to_thread(ydl_info.extract_info, url, download=False)

        # Find best HLS format (combined video+audio)
        best_hls = None
        best_height = 0

        for fmt in info.get('formats', []):
            if not fmt.get('url'):
                continue
            protocol = fmt.get('protocol', '')
            if 'm3u8' not in protocol:
                continue
            has_video = fmt.get('vcodec') not in (None, 'none')
            has_audio = fmt.get('acodec') not in (None, 'none')
            height = fmt.get('height') or 0

            if has_video and has_audio and height > best_height:
                best_hls = fmt
                best_height = height

        if not best_hls:
            raise HTTPException(status_code=404, detail="No HLS format found")

        log.info(f"HLS {video_id}: using format {best_hls.get('format_id')} ({best_height}p)")

        # Fetch the m3u8 manifest
        async with httpx.AsyncClient(cookies=_youtube_cookies) as client:
            resp = await client.get(best_hls['url'], timeout=30.0)
            manifest = resp.text

        # Rewrite segment URLs to go through our proxy
        lines = []
        for line in manifest.split('\n'):
            if line.startswith('http'):
                lines.append(f"/api/hls-segment?url={quote(line, safe='')}")
            elif line.startswith('#EXT-X-MAP:URI="'):
                uri = line.split('URI="')[1].split('"')[0]
                lines.append(f'#EXT-X-MAP:URI="/api/hls-segment?url={quote(uri, safe='')}"')
            else:
                lines.append(line)

        proxied_manifest = '\n'.join(lines)

        return StreamingResponse(
            iter([proxied_manifest.encode()]),
            media_type='application/vnd.apple.mpegurl',
            headers={'Cache-Control': 'no-cache'}
        )

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"HLS error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/hls-segment")
async def proxy_hls_segment(url: str, auth: bool = Depends(require_auth)):
    """Proxy HLS segment to avoid CORS"""
    try:
        async with httpx.AsyncClient(cookies=_youtube_cookies) as client:
            resp = await client.get(url, timeout=30.0)
            if resp.status_code == 403:
                log.warning(f"HLS segment 403 - may need fresh cookies")
            return StreamingResponse(
                iter([resp.content]),
                media_type=resp.headers.get('content-type', 'video/mp2t'),
                headers={'Cache-Control': 'max-age=3600'}
            )
    except Exception as e:
        log.error(f"HLS segment error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/related/{video_id}")
async def get_related_videos(video_id: str, auth: bool = Depends(require_auth)):
    """Get related videos for a video"""
    import re
    import json as json_module

    try:
        url = f"https://www.youtube.com/watch?v={video_id}"
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

            video_id = entry.get('id', '')
            duration = entry.get('duration') or 0

            if duration:
                duration = int(duration)
                hours, remainder = divmod(duration, 3600)
                minutes, seconds = divmod(remainder, 60)
                duration_str = f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"
            else:
                duration_str = "?"

            videos.append({
                'id': video_id,
                'title': entry.get('title', 'Unknown'),
                'duration': duration,
                'duration_str': duration_str,
                'channel': result.get('channel') or result.get('uploader', 'Unknown'),
                'thumbnail': f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
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
