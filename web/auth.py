# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Authentication: sessions, brute-force protection, login/logout routes."""
import logging
import secrets
import time

from html import escape as html_escape
from urllib.parse import quote, urlparse

from fastapi import APIRouter, HTTPException, Request, Response, Depends, Form
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from helpers import register_cleanup
import profiles_db

log = logging.getLogger(__name__)

router = APIRouter()

# Brute-force protection (in-memory, resets on restart — that's fine)
AUTH_FAILURES: dict = {}   # ip -> {"count": int, "blocked_until": float}

_COOKIE_MAX_AGE = 10 * 365 * 86400  # 10 years


def _get_password() -> str | None:
    """Get the app password from DB (None = open access)."""
    return profiles_db.get_app_password()


def _cleanup():
    now = time.time()
    # Clean old failure entries (>24h and not currently blocked)
    old_failures = [ip for ip, info in AUTH_FAILURES.items()
                    if info.get('blocked_until', 0) < now and
                    now - info.get('last_failure', 0) > 86400]
    for ip in old_failures:
        del AUTH_FAILURES[ip]
    if old_failures:
        log.info(f"Cleaned {len(old_failures)} old failure entries")


register_cleanup(_cleanup)


def _safe_redirect(url: str) -> str:
    """Ensure URL is a safe relative path (no open redirect via // or netloc)."""
    if not url or not url.startswith("/") or url.startswith("//") or urlparse(url).netloc:
        return "/"
    return url


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host


def is_ip_blocked(ip: str) -> tuple[bool, int]:
    if ip not in AUTH_FAILURES:
        return False, 0
    info = AUTH_FAILURES[ip]
    if info.get("blocked_until", 0) > time.time():
        return True, int(info["blocked_until"] - time.time())
    return False, 0


def record_failure(ip: str):
    if ip not in AUTH_FAILURES:
        AUTH_FAILURES[ip] = {"count": 0, "blocked_until": 0}
    AUTH_FAILURES[ip]["count"] += 1
    AUTH_FAILURES[ip]["last_failure"] = time.time()
    count = AUTH_FAILURES[ip]["count"]
    if count >= 10:
        AUTH_FAILURES[ip]["blocked_until"] = time.time() + 86400
        log.warning(f"IP {ip} blocked for 24 hours after {count} failures")
    elif count >= 5:
        AUTH_FAILURES[ip]["blocked_until"] = time.time() + 3600
        log.warning(f"IP {ip} blocked for 1 hour after {count} failures")


def clear_failures(ip: str):
    AUTH_FAILURES.pop(ip, None)


def get_session(request: Request) -> tuple[str, dict]:
    """Get existing session from DB or create a new one. Returns (token, session_dict)."""
    token = request.cookies.get("ytp_session")
    if token:
        session = profiles_db.get_session(token)
        if session:
            return token, session

    # Create new persistent session
    token, session = profiles_db.create_session()
    return token, session


def verify_session(request: Request) -> bool:
    if not _get_password():
        return True
    token = request.cookies.get("ytp_session")
    if token:
        session = profiles_db.get_session(token)
        if session:
            return True
    return False


async def require_auth(request: Request):
    """FastAPI dependency that requires authentication."""
    if not _get_password():
        return True
    if verify_session(request):
        return True
    raise HTTPException(status_code=401, detail="Unauthorized")


# ── IP-based embed access ────────────────────────────────────────────────────

# Buffered IP updates: token -> ip (flushed to DB every 5 minutes)
_pending_ip_updates: dict[str, str] = {}


def buffer_session_ip(request: Request):
    """If request has a valid session cookie, buffer the IP for deferred DB write."""
    token = request.cookies.get("ytp_session")
    if token:
        _pending_ip_updates[token] = get_client_ip(request)


def _flush_session_ips():
    """Flush buffered IP updates to SQLite."""
    if not _pending_ip_updates:
        return
    updates = dict(_pending_ip_updates)
    _pending_ip_updates.clear()
    for token, ip in updates.items():
        profiles_db.update_session_ip(token, ip)
    log.debug(f"Flushed {len(updates)} session IP updates")


register_cleanup(_flush_session_ips)


async def require_auth_or_embed(request: Request):
    """Like require_auth, but also allows access if embed is enabled and IP has an active session."""
    if not _get_password():
        return True
    if verify_session(request):
        return True
    if profiles_db.get_setting("allow_embed") == "1":
        ip = get_client_ip(request)
        if profiles_db.has_session_with_ip(ip):
            return True
    raise HTTPException(status_code=401, detail="Unauthorized")


def get_profile_id(request: Request) -> int | None:
    """Get the profile_id from the current session, or None."""
    token = request.cookies.get("ytp_session")
    if token:
        session = profiles_db.get_session(token)
        if session:
            return session.get("profile_id")
    return None


async def require_profile(request: Request) -> int:
    """FastAPI dependency that requires an active profile selection."""
    await require_auth(request)
    pid = get_profile_id(request)
    if pid is None:
        raise HTTPException(status_code=403, detail="No profile selected")
    return pid


# ── Login page HTML ──────────────────────────────────────────────────────────

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
            margin-bottom: 20px;
        }
        input[type="password"]:focus { border-color: #3ea6ff; outline: none; }
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
        <input type="hidden" name="next" value="{{NEXT_URL}}">
        <input type="password" name="password" placeholder="Password" autofocus autocomplete="current-password">
        <button type="submit">Login</button>
    </form>
</body>
</html>"""


# ── Routes ───────────────────────────────────────────────────────────────────

def _serve_spa(request: Request):
    """Serve index.html or redirect to login, preserving the original URL."""
    if _get_password() and not verify_session(request):
        next_url = str(request.url.path)
        if request.url.query:
            next_url += f"?{request.url.query}"
        return RedirectResponse(url=f"/login?next={quote(next_url, safe='')}", status_code=302)
    return FileResponse("static/index.html")


@router.get("/")
async def index(request: Request):
    return _serve_spa(request)


@router.get("/embed/{video_id}")
@router.get("/v/{video_id}")
@router.get("/shorts/{video_id}")
@router.get("/live/{video_id}")
async def embed_page(video_id: str):
    if profiles_db.get_setting("allow_embed") != "1":
        raise HTTPException(status_code=403, detail="Embed access is disabled")
    return FileResponse("static/embed.html")


@router.get("/watch")
async def watch_page(request: Request):
    return _serve_spa(request)


@router.get("/channel/{channel_id}")
async def channel_page(request: Request, channel_id: str):
    return _serve_spa(request)


@router.get("/channel/{channel_id}/playlists")
async def channel_playlists_page(request: Request, channel_id: str):
    return _serve_spa(request)


@router.get("/@{handle}")
async def handle_page(request: Request, handle: str):
    return _serve_spa(request)


@router.get("/@{handle}/playlists")
async def handle_playlists_page(request: Request, handle: str):
    return _serve_spa(request)


@router.get("/results")
async def results_page(request: Request):
    return _serve_spa(request)


@router.get("/history")
async def history_page(request: Request):
    return _serve_spa(request)


@router.get("/favorites")
async def favorites_page(request: Request):
    return _serve_spa(request)


@router.get("/login")
async def login_page(request: Request, error: str = "", next: str = "/"):
    if not _get_password():
        return RedirectResponse(url="/", status_code=302)
    if verify_session(request):
        return RedirectResponse(url=next or "/", status_code=302)

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
        error_html = f'<p class="error">{html_escape(error)}</p>'
    else:
        error_html = ""

    safe_next = _safe_redirect(next)
    html = LOGIN_PAGE.replace("{{ERROR_PLACEHOLDER}}", error_html)
    html = html.replace("{{NEXT_URL}}", html_escape(safe_next))
    return HTMLResponse(html)


@router.post("/login")
async def do_login(request: Request, response: Response, password: str = Form(default=""), next: str = Form(default="/")):
    app_password = _get_password()
    if not app_password:
        return RedirectResponse(url="/", status_code=302)

    redirect_to = _safe_redirect(next)

    ip = get_client_ip(request)
    blocked, remaining = is_ip_blocked(ip)
    if blocked:
        return RedirectResponse(url=f"/login?next={quote(redirect_to, safe='')}", status_code=302)

    if not password:
        return RedirectResponse(url=f"/login?error=Password+required&next={quote(redirect_to, safe='')}", status_code=302)

    if secrets.compare_digest(password, app_password):
        clear_failures(ip)
        token, session = profiles_db.create_session()
        profiles_db.update_session_ip(token, ip)

        response = RedirectResponse(url=redirect_to, status_code=302)
        response.set_cookie(
            key="ytp_session",
            value=token,
            max_age=_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax"
        )
        log.info(f"Login successful from {ip}")
        return response
    else:
        record_failure(ip)
        log.warning(f"Failed login attempt from {ip}")
        return RedirectResponse(url=f"/login?error=Invalid+password&next={quote(redirect_to, safe='')}", status_code=302)


@router.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("ytp_session")
    if token:
        profiles_db.delete_session(token)
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("ytp_session")
    return response


@router.get("/auth/status")
async def auth_status(auth: bool = Depends(require_auth)):
    now = time.time()
    blocked = {
        ip: {
            "failures": info["count"],
            "blocked_for": int(info["blocked_until"] - now) if info["blocked_until"] > now else 0
        }
        for ip, info in AUTH_FAILURES.items()
    }
    return {"blocked_ips": blocked}
