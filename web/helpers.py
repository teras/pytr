# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared configuration, yt-dlp instances, helper functions, and cleanup registry."""
import logging
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yt_dlp

log = logging.getLogger(__name__)

# Shared validation regex for YouTube video IDs (used across multiple modules)
VIDEO_ID_RE = re.compile(r'^[a-zA-Z0-9_-]{11}$')

# Cache directory for subtitle VTT files
CACHE_DIR = Path("cache")
CACHE_DIR.mkdir(exist_ok=True)

# yt-dlp base options
_BASE_YDL_OPTS = {
    'quiet': True,
    'no_warnings': True,
    'remote_components': ['ejs:github'],
}

# yt-dlp instance
ydl_info: yt_dlp.YoutubeDL | None = None


COOKIES_FILE = Path("data/cookies.txt")


def init_ydl():
    """(Re)create the global yt-dlp instance."""
    global ydl_info
    opts = dict(_BASE_YDL_OPTS)
    if COOKIES_FILE.is_file():
        opts['cookiefile'] = str(COOKIES_FILE)
    ydl_info = yt_dlp.YoutubeDL(opts)
    log.info("yt-dlp instance created (cookies=%s)", COOKIES_FILE.name if COOKIES_FILE.is_file() else "none")


# Initialize on import
init_ydl()


# ── Cleanup registry ─────────────────────────────────────────────────────────

_cleanup_registry: list = []
_last_cleanup: float = 0


def register_cleanup(fn):
    """Register a cleanup function to be called periodically."""
    _cleanup_registry.append(fn)


def maybe_cleanup():
    """Run all registered cleanup functions if 5+ minutes since last run."""
    global _last_cleanup
    now = time.time()
    if now - _last_cleanup < 300:
        return
    _last_cleanup = now
    for fn in _cleanup_registry:
        try:
            fn()
        except Exception as e:
            log.warning(f"Cleanup error: {e}")


# ── Long-term cleanup registry (hourly) ──────────────────────────────────

_long_cleanup_fns: list = []
_last_long_cleanup: float = 0


def register_long_cleanup(fn):
    """Register a cleanup function to be called at most once per hour."""
    _long_cleanup_fns.append(fn)


def maybe_long_cleanup():
    """Run all long-term cleanup functions if 1+ hour since last run."""
    global _last_long_cleanup
    now = time.time()
    if now - _last_long_cleanup < 3600:
        return
    _last_long_cleanup = now
    for fn in _long_cleanup_fns:
        try:
            fn()
        except Exception as e:
            log.warning(f"Long cleanup error: {e}")


def make_cache_cleanup(cache: dict, ttl: float, label: str):
    """Create a cleanup function that purges expired entries from a cache dict.

    Expects cache values to have a 'created' key (epoch timestamp).
    """
    def _cleanup():
        now = time.time()
        expired = [k for k, v in cache.items()
                   if now - v.get('created', 0) > ttl]
        for k in expired:
            del cache[k]
        if expired:
            log.info(f"Cleaned {len(expired)} expired {label} cache entries")
    return _cleanup


# ── Shared httpx async client ────────────────────────────────────────────────

http_client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)


def _yt_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


# ── URL validation (SSRF protection) ────────────────────────────────────────

_ALLOWED_DOMAINS = ('googlevideo.com', 'youtube.com', 'ytimg.com',
                    'googleusercontent.com', 'ggpht.com')


def is_youtube_url(url: str) -> bool:
    """Check if a URL points to a known YouTube/Google video domain."""
    try:
        host = urlparse(url).hostname or ''
        return any(host == d or host.endswith('.' + d) for d in _ALLOWED_DOMAINS)
    except Exception:
        return False


# ── Video info cache ────────────────────────────────────────────────────────

_info_cache: dict = {}  # video_id -> {"info": dict, "created": float} or {"error": str, "created": float}
_INFO_CACHE_TTL = 5 * 3600  # 5 hours (YouTube URLs expire ~6h)
_NEGATIVE_CACHE_TTL = 300  # 5 minutes — cache failures to avoid hammering YouTube


register_cleanup(make_cache_cleanup(_info_cache, _INFO_CACHE_TTL, "info"))


_info_lock = threading.Lock()


def get_video_info(video_id: str) -> dict:
    """Get yt-dlp info dict for a video, with caching (5h TTL).

    Thread-safe: ydl_info.extract_info() is not safe to call concurrently,
    so we serialize with a global lock (double-checked pattern).
    Failures are cached for 5 minutes to avoid hammering YouTube.
    """
    cached = _info_cache.get(video_id)
    if cached:
        age = time.time() - cached['created']
        if cached.get('error'):
            if age < _NEGATIVE_CACHE_TTL:
                raise yt_dlp.utils.DownloadError(cached['error'])
        elif age < _INFO_CACHE_TTL:
            return cached['info']

    with _info_lock:
        # Re-check after acquiring lock (another thread may have populated cache)
        cached = _info_cache.get(video_id)
        if cached:
            age = time.time() - cached['created']
            if cached.get('error'):
                if age < _NEGATIVE_CACHE_TTL:
                    raise yt_dlp.utils.DownloadError(cached['error'])
            elif age < _INFO_CACHE_TTL:
                return cached['info']

        url = _yt_url(video_id)
        try:
            info = ydl_info.extract_info(url, download=False)
            _info_cache[video_id] = {'info': info, 'created': time.time()}
            return info
        except Exception as e:
            _info_cache[video_id] = {'error': str(e), 'created': time.time()}
            raise


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
