"""YouTube Web App - FastAPI Backend"""
import asyncio
import logging
import httpx
import yt_dlp
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse

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

# Downloads directory
DOWNLOADS_DIR = Path("downloads")
DOWNLOADS_DIR.mkdir(exist_ok=True)

# Track active downloads: video_id -> {"status": str, "progress": float, ...}
active_downloads = {}

# yt-dlp instances (reused for speed)
ydl_search = yt_dlp.YoutubeDL({
    'quiet': True,
    'no_warnings': True,
    'extract_flat': True,
})

ydl_info = yt_dlp.YoutubeDL({
    'quiet': True,
    'no_warnings': True,
})

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.get("/api/search")
async def search(q: str = Query(..., min_length=1), count: int = Query(default=10, ge=1)):
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
async def play_video(video_id: str):
    """Start download and return stream URL"""
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
    }

    async def download():
        try:
            url = f"https://www.youtube.com/watch?v={video_id}"
            log.info(f"Starting download for {video_id}")

            active_downloads[video_id]['status'] = 'downloading'
            active_downloads[video_id]['message'] = 'Downloading...'

            # Select format based on config
            if CONFIG['quality'] == 'fast':
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

            # Parse progress output
            # For best quality: video (0-80%) + audio (80-95%) + merge (95-100%)
            phase = 'video'
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                line = line.decode().strip()

                # Detect phase changes
                if 'Destination:' in line:
                    if '.m4a' in line or 'audio' in line.lower():
                        phase = 'audio'
                elif '[Merger]' in line:
                    phase = 'merge'
                    active_downloads[video_id]['progress'] = 95
                    active_downloads[video_id]['message'] = 'Merging...'
                elif '[download]' in line and '%' in line:
                    try:
                        pct = float(line.split('%')[0].split()[-1])
                        # Scale progress based on phase
                        if phase == 'video':
                            scaled = pct * 0.8  # 0-80%
                            msg = f'Video: {pct:.0f}%'
                        elif phase == 'audio':
                            scaled = 80 + pct * 0.15  # 80-95%
                            msg = f'Audio: {pct:.0f}%'
                        else:
                            scaled = pct
                            msg = f'{pct:.0f}%'
                        active_downloads[video_id]['progress'] = scaled
                        active_downloads[video_id]['message'] = msg
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
async def get_video_info(video_id: str):
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
            'upload_date': upload_date,
            'duration': info.get('duration', 0),
            'views': format_number(info.get('view_count')),
            'likes': format_number(info.get('like_count')),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/progress/{video_id}")
async def get_progress(video_id: str):
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


@app.get("/api/stream/{video_id}")
async def stream_video(video_id: str):
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
async def stream_live(video_id: str, request: Request):
    """Stream video with range request support (proxy to YouTube)"""

    url = f"https://www.youtube.com/watch?v={video_id}"

    # Get direct video URL from yt-dlp
    try:
        info = await asyncio.to_thread(
            ydl_info.extract_info, url, download=False
        )

        # Find format 22 or 18 (progressive with audio)
        video_url = None
        filesize = None
        for fmt in info.get('formats', []):
            if fmt.get('format_id') in ('22', '18'):
                video_url = fmt.get('url')
                filesize = fmt.get('filesize') or fmt.get('filesize_approx')
                break

        if not video_url:
            # Fallback to best progressive format
            for fmt in info.get('formats', []):
                if fmt.get('acodec') != 'none' and fmt.get('vcodec') != 'none':
                    video_url = fmt.get('url')
                    filesize = fmt.get('filesize') or fmt.get('filesize_approx')
                    break

        if not video_url:
            raise HTTPException(status_code=404, detail="No suitable format found")

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

        async with httpx.AsyncClient() as client:
            async with client.stream('GET', video_url, headers=headers, timeout=30.0) as resp:
                async for chunk in resp.aiter_bytes(65536):
                    yield chunk

    if range_header:
        # Parse range header: "bytes=start-end" or "bytes=start-"
        range_match = range_header.replace('bytes=', '').split('-')
        start = int(range_match[0]) if range_match[0] else 0
        end = int(range_match[1]) if range_match[1] else None

        # Calculate content length
        if filesize:
            end = end or (filesize - 1)
            content_length = end - start + 1
            content_range = f'bytes {start}-{end}/{filesize}'
        else:
            content_length = None
            content_range = f'bytes {start}-*/*'

        headers = {
            'Content-Type': 'video/mp4',
            'Accept-Ranges': 'bytes',
            'Content-Range': content_range,
            'Cache-Control': 'no-cache',
        }
        if content_length:
            headers['Content-Length'] = str(content_length)

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
        if filesize:
            headers['Content-Length'] = str(filesize)

        return StreamingResponse(
            proxy_stream(),
            status_code=200,
            headers=headers,
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
