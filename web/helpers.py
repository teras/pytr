# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared configuration, yt-dlp instances, helper functions, and cleanup registry."""
import hashlib
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
CACHE_DIR = Path(".cache/subtitles")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# yt-dlp base options
_BASE_YDL_OPTS = {
    'quiet': True,
    'no_warnings': True,
}

# yt-dlp instances: anonymous (always) + authenticated (only if cookies exist)
ydl_info: yt_dlp.YoutubeDL | None = None       # anonymous — no cookies
ydl_info_auth: yt_dlp.YoutubeDL | None = None   # authenticated — with cookies (or None)


COOKIES_FILE = Path("data/cookies.txt")

# Anonymous guest session (no account). A stable visitor identity (persisted
# guest cookies: VISITOR_INFO1_LIVE, YSC, ...) gets friendlier treatment from
# YouTube than a brand-new visitor per extraction — fewer transient 403s on
# IPs YouTube distrusts (non-residential ASNs) — without tying playback to any
# Google account. Deleting the file rotates the identity.
#
# The jar is PER-USER: each viewer (profile, or a per-browser id) extracts under
# their own guest identity, so YouTube never sees one hyper-active guest that
# blends the whole household's activity — better privacy and each looks like a
# normal single viewer. Only the guest COOKIE is per-user; the resulting (signed,
# server-portable) videoplayback URLs stay in a shared cache, so a given video is
# still extracted only once for everyone. COOKIES_ANON_FILE is the shared default
# jar for profile-less paths (embed, pre-login); per-user jars live in the dir.
COOKIES_ANON_FILE = Path("data/cookies-anon.txt")
COOKIES_ANON_DIR = Path("data/anon")

# Privacy: the guest jar would otherwise accumulate watch activity into one
# pseudonymous profile forever. After this much idle time the identity is
# rotated, splitting activity into disconnected sessions that cookies can't
# chain together. The idle clock resets on every extraction, so within a
# continuous viewing session the identity stays stable (trusted); the window
# only controls how long it lingers after watching stops.
_ANON_SESSION_IDLE_MAX = 2 * 3600

_AGE_RESTRICTED_PATTERNS = (
    'sign in to confirm your age',
    'age-restricted',
    'age restricted',
    'age_restricted',
    'content warning',
)

_THROTTLE_PATTERNS = (
    'sign in to confirm you',
    'this video requires login',
    'the page needs to be reloaded',
    'bot',
)


def _is_age_restricted(error_msg: str) -> bool:
    """Check if error indicates genuinely age-restricted content."""
    lower = error_msg.lower()
    return any(p in lower for p in _AGE_RESTRICTED_PATTERNS)


def _is_throttled(error_msg: str) -> bool:
    """Check if error indicates YouTube bot/throttle detection."""
    lower = error_msg.lower()
    # Don't match throttle if it's actually age-restricted
    if _is_age_restricted(error_msg):
        return False
    return any(p in lower for p in _THROTTLE_PATTERNS)


# Global throttle cooldown: when YT throttles us, use cookies for 10 min
_throttled_until: float = 0
_THROTTLE_COOLDOWN = 600  # 10 minutes


_cookies_mtime: float = 0


def init_ydl():
    """(Re)create the global yt-dlp instances."""
    global ydl_info, ydl_info_auth, _cookies_mtime
    anon_opts = dict(_BASE_YDL_OPTS)
    anon_opts['cookiefile'] = str(COOKIES_ANON_FILE)  # persistent guest session
    ydl_info = yt_dlp.YoutubeDL(anon_opts)
    if COOKIES_FILE.is_file():
        _cookies_mtime = COOKIES_FILE.stat().st_mtime
        auth_opts = dict(_BASE_YDL_OPTS)
        auth_opts['cookiefile'] = str(COOKIES_FILE)
        ydl_info_auth = yt_dlp.YoutubeDL(auth_opts)
        log.info("yt-dlp: anonymous + authenticated (%s) instances created", COOKIES_FILE.name)
    else:
        _cookies_mtime = 0
        ydl_info_auth = None
        log.info("yt-dlp: anonymous instance created (no cookies)")


def _maybe_reload_cookies():
    """Reload yt-dlp instances if cookies.txt has changed on disk."""
    try:
        mtime = COOKIES_FILE.stat().st_mtime if COOKIES_FILE.is_file() else 0
    except OSError:
        return
    if mtime != _cookies_mtime:
        log.info("cookies.txt changed, reloading yt-dlp instances")
        init_ydl()


def _anon_jar_path(anon_uid: str) -> Path:
    """Filesystem path for a viewer's guest cookie jar.

    The uid comes from the client (profile id, or a per-browser id); it is hashed
    so it can never be used for path traversal and filenames stay uniform. An
    empty uid maps to the shared default jar (embed / pre-login paths)."""
    if not anon_uid:
        return COOKIES_ANON_FILE
    token = hashlib.sha1(anon_uid.encode('utf-8', 'replace')).hexdigest()[:16]
    return COOKIES_ANON_DIR / f"{token}.txt"


def _load_anon_jar(anon_uid: str):
    """Point the shared anon yt-dlp instance at this viewer's guest jar.

    Called under _info_lock (extraction is serialized), so swapping the jar on
    the single anon instance is safe. Rotates the identity if it has been idle
    past the window — each viewer's jar carries its own inactivity clock, and the
    clock resets on every extraction so it stays stable during a viewing session."""
    path = _anon_jar_path(anon_uid)
    jar = ydl_info.cookiejar
    jar.clear()
    jar.filename = str(path)
    if path.is_file():
        if time.time() - path.stat().st_mtime > _ANON_SESSION_IDLE_MAX:
            try:
                path.unlink()
                log.info("Guest identity idle >%dh — rotating", _ANON_SESSION_IDLE_MAX // 3600)
            except OSError:
                pass
        else:
            try:
                jar.load(ignore_discard=True, ignore_expires=True)
            except Exception:
                pass


def _save_anon_jar(anon_uid: str):
    """Persist the guest session so the visitor identity survives restarts."""
    try:
        if anon_uid:
            COOKIES_ANON_DIR.mkdir(parents=True, exist_ok=True)
        ydl_info.cookiejar.save(ignore_discard=True, ignore_expires=True)
    except Exception as e:
        log.warning("Could not save guest cookies: %s", e)


def reset_anon_jar(anon_uid: str) -> bool:
    """Force-rotate a viewer's guest identity (menu action). Returns True if a
    jar existed and was removed; the next extraction starts a fresh identity."""
    try:
        path = _anon_jar_path(anon_uid)
        if path.is_file():
            path.unlink()
            return True
    except OSError as e:
        log.warning("Could not reset guest jar: %s", e)
    return False


def _sweep_anon_jars():
    """GC per-user guest jars whose owners haven't returned within the idle
    window, so abandoned identities don't linger on disk."""
    try:
        if not COOKIES_ANON_DIR.is_dir():
            return
        now = time.time()
        for f in COOKIES_ANON_DIR.glob("*.txt"):
            try:
                if now - f.stat().st_mtime > _ANON_SESSION_IDLE_MAX:
                    f.unlink()
            except OSError:
                pass
    except Exception as e:
        log.warning("Could not sweep guest jars: %s", e)


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
register_long_cleanup(_sweep_anon_jars)


_info_lock = threading.Lock()


def invalidate_video_cache(video_id: str, min_age: float = 0) -> bool:
    """Remove a video from the info cache (e.g. when CDN URLs expire).

    min_age: only invalidate entries older than this many seconds — prevents
    re-extraction storms when freshly extracted URLs also fail upstream.
    Returns True if an entry was removed."""
    entry = _info_cache.get(video_id)
    if entry is None or time.time() - entry.get('created', 0) < min_age:
        return False
    _info_cache.pop(video_id, None)
    return True


def get_video_info(video_id: str, cookie_mode: str = "auto", anon_uid: str = "") -> dict:
    """Get yt-dlp info dict for a video, with caching (5h TTL).

    cookie_mode:
      "off"  — anonymous only; raise on failure (show real error)
      "auto" — anonymous first; auto-fallback to cookies on age-restriction
               (cached per-video) or throttle (global 10min cooldown)
      "on"   — always use cookies (if available)

    anon_uid: identifies the viewer for the anonymous path — an anonymous
    extraction runs under this viewer's own guest cookie jar (privacy: no shared
    hyper-active guest). The result is cached per-video (shared), so a video is
    still extracted only once regardless of how many viewers watch it.

    Thread-safe: ydl_info.extract_info() is not safe to call concurrently,
    so we serialize with a global lock (double-checked pattern).
    Failures are cached for 5 minutes to avoid hammering YouTube.
    """
    global _throttled_until

    if cookie_mode not in ("off", "auto", "on"):
        cookie_mode = "auto"

    cached = _info_cache.get(video_id)
    if cached:
        age = time.time() - cached['created']
        if cached.get('error'):
            if age < _NEGATIVE_CACHE_TTL:
                raise yt_dlp.utils.DownloadError(cached['error'])
        elif age < _INFO_CACHE_TTL:
            return cached['info']

    with _info_lock:
        _maybe_reload_cookies()
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
        now = time.time()

        # Determine which instance to try first
        if cookie_mode == "on" and ydl_info_auth is not None:
            use_auth_first = True
        elif cookie_mode == "auto" and ydl_info_auth is not None:
            # Use cookies upfront if: video is known age-restricted, or global throttle active
            use_auth_first = (
                (cached and cached.get('age_restricted'))
                or now < _throttled_until
            )
        else:
            use_auth_first = False

        try:
            if use_auth_first:
                info = ydl_info_auth.extract_info(url, download=False)
            else:
                _load_anon_jar(anon_uid)
                info = ydl_info.extract_info(url, download=False)
                _save_anon_jar(anon_uid)
            cache_entry = {'info': info, 'created': now}
            if cached and cached.get('age_restricted'):
                cache_entry['age_restricted'] = True  # preserve flag
            _info_cache[video_id] = cache_entry
            return info
        except Exception as e:
            err_msg = str(e)

            # In auto mode, fallback to cookies on age-restriction or throttle
            if cookie_mode == "auto" and ydl_info_auth is not None and not use_auth_first:
                should_retry = _is_age_restricted(err_msg) or _is_throttled(err_msg)
                if should_retry:
                    reason = "age-restricted" if _is_age_restricted(err_msg) else "throttled"
                    try:
                        log.info("%s %s — retrying with cookies", reason.capitalize(), video_id)
                        info = ydl_info_auth.extract_info(url, download=False)
                        cache_entry = {'info': info, 'created': now}
                        if _is_age_restricted(err_msg):
                            cache_entry['age_restricted'] = True  # remember per-video
                        if _is_throttled(err_msg):
                            _throttled_until = now + _THROTTLE_COOLDOWN
                            log.info("Throttle cooldown active for %ds", _THROTTLE_COOLDOWN)
                        _info_cache[video_id] = cache_entry
                        return info
                    except Exception as e2:
                        _info_cache[video_id] = {'error': str(e2), 'created': now}
                        raise

            _info_cache[video_id] = {'error': err_msg, 'created': now}
            raise


def _format_duration(seconds) -> str:
    if not seconds:
        return "?"
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


# ── webOS Dev Mode auto-renewal (background task) ───────────────────────────

_WEBOS_CHECK_INTERVAL = 3600   # check every 1 hour
_WEBOS_RENEWAL_PERIOD = 86400  # renew once per 24 hours


async def webos_renewal_loop():
    """Background task: check hourly, renew each token once per 24h.

    Tokens stored as JSON list in 'registered_tvs' setting.
    Each entry: {"token": "...", "name": "...", "type": "L", "last_renewed": epoch, "last_error": str|None}
    last_renewed is persisted in DB, so survives server restarts.
    If renewal fails, retries next check (1h) until successful.
    Only processes LG TVs (type == "L").
    """
    import asyncio
    import json

    while True:
        await asyncio.sleep(_WEBOS_CHECK_INTERVAL)
        try:
            import profiles_db
            raw = profiles_db.get_setting("registered_tvs")
            if not raw:
                continue
            try:
                tokens = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if not tokens:
                continue

            now = time.time()
            changed = False
            for entry in tokens:
                if entry.get("type") != "webos":
                    continue  # skip non-LG TVs
                token = entry.get("token", "")
                name = entry.get("name", "?")
                if not token:
                    continue
                last = entry.get("last_renewed") or 0
                if now - last < _WEBOS_RENEWAL_PERIOD:
                    continue  # renewed recently, skip
                url = f"https://developer.lge.com/secure/ResetDevModeSession.dev?sessionToken={token}"
                try:
                    resp = await http_client.get(url, timeout=15)
                    log.info(f"webOS renewal [{name}]: {resp.status_code} {resp.text[:100]}")
                    entry["last_renewed"] = now
                    entry["last_error"] = None
                    changed = True
                except Exception as e:
                    log.warning(f"webOS renewal [{name}] failed: {e}")
                    entry["last_error"] = str(e)
                    changed = True

            if changed:
                profiles_db.set_setting("registered_tvs", json.dumps(tokens))
        except Exception as e:
            log.warning(f"webOS renewal loop error: {e}")


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
