"""Shared configuration, yt-dlp instances, helper functions, and cleanup registry."""
import logging
import threading
import time
from pathlib import Path

import yt_dlp

log = logging.getLogger(__name__)

# Cache directory for subtitle VTT files
CACHE_DIR = Path("cache")
CACHE_DIR.mkdir(exist_ok=True)

# yt-dlp base options (cookies added dynamically from DB setting)
_BASE_YDL_OPTS = {
    'quiet': True,
    'no_warnings': True,
    'remote_components': ['ejs:github'],
}

# yt-dlp instance — recreated when cookies_browser setting changes
ydl_info: yt_dlp.YoutubeDL | None = None


def _build_ydl_opts() -> dict:
    """Build yt-dlp options, reading cookies_browser from DB."""
    opts = dict(_BASE_YDL_OPTS)
    try:
        import profiles_db
        cookies_browser = profiles_db.get_setting("cookies_browser")
        if cookies_browser:
            opts['cookiesfrombrowser'] = (cookies_browser,)
    except Exception:
        pass
    return opts


def init_ydl():
    """(Re)create the global yt-dlp instance."""
    global ydl_info
    opts = _build_ydl_opts()
    ydl_info = yt_dlp.YoutubeDL(opts)
    log.info("yt-dlp instance created (cookies_browser=%s)",
             opts.get('cookiesfrombrowser', (None,))[0])


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


def _yt_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


# ── Video info cache ────────────────────────────────────────────────────────

_info_cache: dict = {}  # video_id -> {"info": dict, "created": float}
_INFO_CACHE_TTL = 5 * 3600  # 5 hours (YouTube URLs expire ~6h)


def _cleanup_info_cache():
    now = time.time()
    expired = [k for k, v in _info_cache.items() if now - v['created'] > _INFO_CACHE_TTL]
    for k in expired:
        del _info_cache[k]
    if expired:
        log.info(f"Cleaned {len(expired)} expired info cache entries")


register_cleanup(_cleanup_info_cache)


_info_lock = threading.Lock()


def get_video_info(video_id: str) -> dict:
    """Get yt-dlp info dict for a video, with caching (5h TTL).

    Thread-safe: ydl_info.extract_info() is not safe to call concurrently,
    so we serialize cache misses with a lock (double-checked pattern).
    """
    cached = _info_cache.get(video_id)
    if cached and time.time() - cached['created'] < _INFO_CACHE_TTL:
        return cached['info']

    with _info_lock:
        # Re-check after acquiring lock (another thread may have populated cache)
        cached = _info_cache.get(video_id)
        if cached and time.time() - cached['created'] < _INFO_CACHE_TTL:
            return cached['info']

        url = _yt_url(video_id)
        info = ydl_info.extract_info(url, download=False)
        _info_cache[video_id] = {'info': info, 'created': time.time()}
        return info


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
