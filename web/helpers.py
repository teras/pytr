"""Shared configuration, yt-dlp instances, and helper functions."""
import logging
import os
import shutil
from pathlib import Path

import yt_dlp

log = logging.getLogger(__name__)

# Downloads directory - clear contents on startup
DOWNLOADS_DIR = Path("downloads")
DOWNLOADS_DIR.mkdir(exist_ok=True)
for f in DOWNLOADS_DIR.iterdir():
    try:
        if f.is_file():
            f.unlink()
        elif f.is_dir():
            shutil.rmtree(f)
    except Exception as e:
        log.warning(f"Could not delete {f}: {e}")
log.info("Cleared downloads cache")

# Track active downloads: video_id -> {"status": str, "progress": float, ...}
active_downloads: dict = {}

# yt-dlp options
_cookies_browser = os.environ.get('YOUTUBE_COOKIES_BROWSER')
YDL_OPTS = {
    'quiet': True,
    'no_warnings': True,
    'remote_components': ['ejs:github'],
}
if _cookies_browser:
    YDL_OPTS['cookiesfrombrowser'] = (_cookies_browser,)

# yt-dlp instances (reused for speed)
ydl_search = yt_dlp.YoutubeDL({**YDL_OPTS, 'extract_flat': True})
ydl_info = yt_dlp.YoutubeDL(YDL_OPTS)


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
    if b >= 1_000_000_000:
        return f"{b/1_000_000_000:.1f} GB"
    if b >= 1_000_000:
        return f"{b/1_000_000:.1f} MB"
    if b >= 1_000:
        return f"{b/1_000:.1f} KB"
    return f"{b} B"
