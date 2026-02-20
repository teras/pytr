"""Authentication: sessions, brute-force protection, login/logout routes."""
import logging
import secrets
import time

from html import escape as html_escape
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response, Depends, Form
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from helpers import register_cleanup
import profiles_db

log = logging.getLogger(__name__)

router = APIRouter()

# State
AUTH_SESSIONS: dict = {}   # token -> {"expiry": float, "searches": {}}
AUTH_FAILURES: dict = {}   # ip -> {"count": int, "blocked_until": float}


def _get_password() -> str | None:
    """Get the app password from DB (None = open access)."""
    return profiles_db.get_app_password()


def _cleanup_sessions():
    now = time.time()
    expired = [t for t, s in AUTH_SESSIONS.items() if now > s['expiry']]
    for t in expired:
        del AUTH_SESSIONS[t]
    # Clean old failure entries (>24h and not currently blocked)
    old_failures = [ip for ip, info in AUTH_FAILURES.items()
                    if info.get('blocked_until', 0) < now and
                    now - info.get('last_failure', 0) > 86400]
    for ip in old_failures:
        del AUTH_FAILURES[ip]
    if expired or old_failures:
        log.info(f"Cleaned {len(expired)} expired sessions, {len(old_failures)} old failure entries")


register_cleanup(_cleanup_sessions)


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


def _create_session(expiry: float) -> tuple[str, dict]:
    """Create a new session and return (token, session_dict)."""
    token = secrets.token_urlsafe(32)
    session = {"expiry": expiry, "searches": {}, "profile_id": None}
    AUTH_SESSIONS[token] = session
    return token, session


def get_session(request: Request) -> tuple[str, dict]:
    """Get existing session or create a new one. Returns (token, session_dict)."""
    token = request.cookies.get("ytp_session")
    if token and token in AUTH_SESSIONS:
        session = AUTH_SESSIONS[token]
        if session['expiry'] > time.time():
            return token, session
        del AUTH_SESSIONS[token]

    # Create session (profile selection will persist profile_id into it)
    token, session = _create_session(time.time() + 24 * 3600)
    return token, session


def verify_session(request: Request) -> bool:
    if not _get_password():
        return True
    token = request.cookies.get("ytp_session")
    if token and token in AUTH_SESSIONS:
        if AUTH_SESSIONS[token]['expiry'] > time.time():
            return True
        del AUTH_SESSIONS[token]
    return False


async def require_auth(request: Request):
    """FastAPI dependency that requires authentication."""
    if not _get_password():
        return True
    if not verify_session(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True


def get_profile_id(request: Request) -> int | None:
    """Get the profile_id from the current session, or None."""
    token = request.cookies.get("ytp_session")
    if token and token in AUTH_SESSIONS:
        session = AUTH_SESSIONS[token]
        if session["expiry"] > time.time():
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
        <input type="hidden" name="next" value="{{NEXT_URL}}">
        <input type="password" name="password" placeholder="Password" autofocus autocomplete="current-password">
        <label class="remember-row">
            <input type="checkbox" name="remember" value="1">
            Remember this device (30 days)
        </label>
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


@router.get("/watch")
async def watch_page(request: Request):
    return _serve_spa(request)


@router.get("/channel/{channel_id}")
async def channel_page(request: Request, channel_id: str):
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

    # Only allow relative URLs to prevent open redirect
    safe_next = next if next.startswith("/") else "/"
    html = LOGIN_PAGE.replace("{{ERROR_PLACEHOLDER}}", error_html)
    html = html.replace("{{NEXT_URL}}", html_escape(safe_next))
    return HTMLResponse(html)


@router.post("/login")
async def do_login(request: Request, response: Response, password: str = Form(...), remember: str = Form(default=""), next: str = Form(default="/")):
    app_password = _get_password()
    if not app_password:
        return RedirectResponse(url="/", status_code=302)

    # Only allow relative URLs to prevent open redirect
    redirect_to = next if next.startswith("/") else "/"

    ip = get_client_ip(request)
    blocked, remaining = is_ip_blocked(ip)
    if blocked:
        return RedirectResponse(url=f"/login?next={quote(redirect_to, safe='')}", status_code=302)

    if secrets.compare_digest(password, app_password):
        clear_failures(ip)
        expiry = time.time() + (30 * 86400 if remember else 86400)
        token, session = _create_session(expiry)

        response = RedirectResponse(url=redirect_to, status_code=302)
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
        return RedirectResponse(url=f"/login?error=Invalid+password&next={quote(redirect_to, safe='')}", status_code=302)


@router.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("ytp_session")
    if token and token in AUTH_SESSIONS:
        del AUTH_SESSIONS[token]
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("ytp_session")
    return response


@router.get("/auth/status")
async def auth_status():
    now = time.time()
    blocked = {
        ip: {
            "failures": info["count"],
            "blocked_for": int(info["blocked_until"] - now) if info["blocked_until"] > now else 0
        }
        for ip, info in AUTH_FAILURES.items()
    }
    return {"blocked_ips": blocked, "active_sessions": len(AUTH_SESSIONS)}
