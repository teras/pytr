# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Authentication: sessions, brute-force protection, login/logout routes, device pairing."""
import io
import logging
import secrets
import time

from html import escape as html_escape
from urllib.parse import quote, urlparse

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, HTTPException, Request, Response, Depends, Form
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from helpers import register_cleanup
import profiles_db

log = logging.getLogger(__name__)

router = APIRouter()

# Brute-force protection (in-memory, resets on restart — that's fine)
AUTH_FAILURES: dict = {}   # ip -> {"count": int, "blocked_until": float}

_COOKIE_MAX_AGE = 10 * 365 * 86400  # 10 years

# ── Device pairing ────────────────────────────────────────────────────────────
_PAIR_CHARSET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no ambiguous I/O/0/1
_PAIR_CODE_LEN = 6
_PAIR_TTL = 300  # 5 minutes
_PAIR_RATE_WINDOW = 600  # 10 minutes
_PAIR_RATE_MAX = 5  # max codes per IP per window

PAIRING_REQUESTS: dict[str, dict] = {}  # code → {created_at, expires_at, requester_ip, status, session_token}
PAIRING_RATE: dict[str, list[float]] = {}  # ip → [timestamps]


def _generate_pair_code() -> str:
    while True:
        code = "".join(secrets.choice(_PAIR_CHARSET) for _ in range(_PAIR_CODE_LEN))
        if code not in PAIRING_REQUESTS:
            return code


def _generate_qr_svg(data: str) -> str:
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


def _cleanup_pairing():
    now = time.time()
    expired = [code for code, req in PAIRING_REQUESTS.items() if req["expires_at"] < now]
    for code in expired:
        req = PAIRING_REQUESTS.pop(code)
        if req.get("session_token") and req["status"] != "approved":
            profiles_db.delete_session(req["session_token"])
    # Clean old rate entries
    for ip in list(PAIRING_RATE):
        PAIRING_RATE[ip] = [t for t in PAIRING_RATE[ip] if now - t < _PAIR_RATE_WINDOW]
        if not PAIRING_RATE[ip]:
            del PAIRING_RATE[ip]
    if expired:
        log.info(f"Cleaned {len(expired)} expired pairing requests")


def _get_password() -> str | None:
    """Get the app password from DB (None only during first-run bootstrap)."""
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
register_cleanup(_cleanup_pairing)


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
    token = request.cookies.get("pytr_session")
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
    token = request.cookies.get("pytr_session")
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
    token = request.cookies.get("pytr_session")
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
    token = request.cookies.get("pytr_session")
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
        button, .link-btn {
            width: 100%;
            padding: 14px;
            font-size: 16px;
            background-color: #cc0000;
            color: #fff;
            border: none;
            border-radius: 12px;
            cursor: pointer;
            font-weight: 500;
            display: block;
            text-align: center;
            text-decoration: none;
        }
        button:hover, .link-btn:hover { background-color: #ee0000; }
        .divider {
            display: flex;
            align-items: center;
            margin: 24px 0;
            color: #666;
            font-size: 13px;
        }
        .divider::before, .divider::after {
            content: '';
            flex: 1;
            border-bottom: 1px solid #303030;
        }
        .divider span { padding: 0 12px; }
        .link-btn.secondary {
            background-color: #272727;
            color: #aaa;
        }
        .link-btn.secondary:hover {
            background-color: #333;
            color: #f1f1f1;
        }
        /* Pairing view */
        #pair-view { display: none; }
        .pair-code {
            font-size: 48px;
            font-weight: 700;
            letter-spacing: 12px;
            text-align: center;
            margin: 20px 0;
            font-family: 'Courier New', monospace;
            color: #3ea6ff;
        }
        .pair-instruction {
            text-align: center;
            color: #aaa;
            font-size: 14px;
            margin-bottom: 16px;
            line-height: 1.5;
        }
        .pair-qr {
            display: flex;
            justify-content: center;
            margin: 20px 0;
        }
        .pair-qr svg {
            width: 180px;
            height: 180px;
            border-radius: 8px;
        }
        .pair-qr svg path { fill: #f1f1f1 !important; }
        .pair-qr svg rect { fill: transparent !important; }
        .pair-status {
            text-align: center;
            font-size: 14px;
            color: #aaa;
            margin: 12px 0;
        }
        .pair-status.denied { color: #ff4444; }
        .pair-status.approved { color: #4caf50; }
        .back-link {
            display: block;
            text-align: center;
            color: #666;
            text-decoration: none;
            margin-top: 20px;
            font-size: 14px;
        }
        .back-link:hover { color: #aaa; }
    </style>
</head>
<body>
    <div class="login-box">
        <div id="login-view">
            <form method="POST" action="/login">
                {{ERROR_PLACEHOLDER}}
                <input type="hidden" name="next" value="{{NEXT_URL}}">
                <input type="password" name="password" placeholder="Password" autofocus autocomplete="current-password">
                <button type="submit">Login</button>
            </form>
            <div class="divider"><span>or</span></div>
            <a href="#" class="link-btn secondary" id="start-pair-btn">Link from another device</a>
        </div>
        <div id="pair-view">
            <p class="pair-instruction">Enter this code on a device that's already logged in,<br>or scan the QR code</p>
            <div class="pair-code" id="pair-code"></div>
            <div class="pair-qr" id="pair-qr"></div>
            <p class="pair-instruction">Go to <strong id="pair-url"></strong> on your other device</p>
            <p class="pair-status" id="pair-status">Waiting for approval...</p>
            <a href="#" class="back-link" id="pair-back">Back to password login</a>
        </div>
    </div>
    <script>
    (function() {
        const loginView = document.getElementById('login-view');
        const pairView = document.getElementById('pair-view');
        const startBtn = document.getElementById('start-pair-btn');
        const backBtn = document.getElementById('pair-back');
        const codeEl = document.getElementById('pair-code');
        const qrEl = document.getElementById('pair-qr');
        const urlEl = document.getElementById('pair-url');
        const statusEl = document.getElementById('pair-status');
        let pollTimer = null;

        startBtn.addEventListener('click', async function(e) {
            e.preventDefault();
            try {
                const res = await fetch('/api/pair/request', { method: 'POST' });
                if (!res.ok) {
                    const data = await res.json();
                    alert(data.detail || 'Failed to create pairing code');
                    return;
                }
                const data = await res.json();
                codeEl.textContent = data.code;
                qrEl.innerHTML = data.qr_svg;
                urlEl.textContent = location.origin + '/link';
                loginView.style.display = 'none';
                pairView.style.display = 'block';
                statusEl.textContent = 'Waiting for approval...';
                statusEl.className = 'pair-status';
                startPolling(data.code);
            } catch (err) {
                alert('Network error');
            }
        });

        backBtn.addEventListener('click', function(e) {
            e.preventDefault();
            stopPolling();
            pairView.style.display = 'none';
            loginView.style.display = 'block';
        });

        function startPolling(code) {
            stopPolling();
            pollTimer = setInterval(async () => {
                try {
                    const res = await fetch('/api/pair/status/' + code);
                    const data = await res.json();
                    if (data.status === 'approved') {
                        stopPolling();
                        statusEl.textContent = 'Approved! Redirecting...';
                        statusEl.className = 'pair-status approved';
                        setTimeout(() => { location.href = '/'; }, 500);
                    } else if (data.status === 'denied') {
                        stopPolling();
                        showRetry('Denied.');
                    } else if (data.status === 'expired') {
                        stopPolling();
                        showRetry('Code expired.');
                    }
                } catch (err) {}
            }, 2000);
        }

        function stopPolling() {
            if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
        }

        function showRetry(reason) {
            statusEl.innerHTML = reason + ' <a href="#" id="retry-link" style="color:#3ea6ff">Try again</a>';
            statusEl.className = 'pair-status denied';
            document.getElementById('retry-link').addEventListener('click', async function(e) {
                e.preventDefault();
                startBtn.click();
            });
        }
    })();
    </script>
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
            key="pytr_session",
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
    token = request.cookies.get("pytr_session")
    if token:
        profiles_db.delete_session(token)
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("pytr_session")
    return response


# ── Device pairing routes ─────────────────────────────────────────────────────

@router.post("/api/pair/request")
async def pair_request(request: Request):
    if not _get_password():
        raise HTTPException(status_code=400, detail="No password set")

    ip = get_client_ip(request)
    now = time.time()

    # Rate limiting
    timestamps = PAIRING_RATE.get(ip, [])
    timestamps = [t for t in timestamps if now - t < _PAIR_RATE_WINDOW]
    if len(timestamps) >= _PAIR_RATE_MAX:
        raise HTTPException(status_code=429, detail="Too many pairing requests. Try again later.")
    timestamps.append(now)
    PAIRING_RATE[ip] = timestamps

    code = _generate_pair_code()
    token, session = profiles_db.create_session()

    PAIRING_REQUESTS[code] = {
        "created_at": now,
        "expires_at": now + _PAIR_TTL,
        "requester_ip": ip,
        "status": "pending",
        "session_token": token,
    }

    base_url = str(request.base_url).rstrip("/")
    link_url = f"{base_url}/link?code={code}"
    qr_svg = _generate_qr_svg(link_url)

    return {"code": code, "qr_svg": qr_svg, "expires_in": _PAIR_TTL}


@router.get("/api/pair/status/{code}")
async def pair_status(code: str, request: Request, response: Response):
    code = code.upper()
    req = PAIRING_REQUESTS.get(code)
    if not req:
        return {"status": "expired"}
    if req["expires_at"] < time.time():
        expired_req = PAIRING_REQUESTS.pop(code)
        if expired_req.get("session_token") and expired_req["status"] != "approved":
            profiles_db.delete_session(expired_req["session_token"])
        return {"status": "expired"}

    if req["status"] == "approved":
        # Deliver the session cookie and clean up
        token = req["session_token"]
        ip = get_client_ip(request)
        profiles_db.update_session_ip(token, ip)
        PAIRING_REQUESTS.pop(code, None)
        response.set_cookie(
            key="pytr_session",
            value=token,
            max_age=_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
        )
        return {"status": "approved"}

    if req["status"] == "denied":
        PAIRING_REQUESTS.pop(code, None)
        return {"status": "denied"}

    result = {"status": "pending"}
    if verify_session(request):
        result["requester_ip"] = req["requester_ip"]
    return result


LINK_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Link Device</title>
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
        .link-box {
            background-color: #1a1a1a;
            padding: 40px;
            border-radius: 16px;
            width: 100%;
            max-width: 400px;
            margin: 20px;
            text-align: center;
        }
        h2 { margin-bottom: 8px; font-size: 20px; }
        .subtitle { color: #aaa; font-size: 14px; margin-bottom: 24px; }
        input[type="text"] {
            width: 100%;
            padding: 14px 18px;
            font-size: 28px;
            font-weight: 700;
            letter-spacing: 8px;
            text-align: center;
            border: 1px solid #303030;
            border-radius: 12px;
            background-color: #121212;
            color: #f1f1f1;
            margin-bottom: 16px;
            text-transform: uppercase;
            font-family: 'Courier New', monospace;
        }
        input[type="text"]:focus { border-color: #3ea6ff; outline: none; }
        .ip-hint {
            color: #888;
            font-size: 13px;
            margin-bottom: 20px;
        }
        .buttons { display: flex; gap: 12px; }
        .btn {
            flex: 1;
            padding: 14px;
            font-size: 16px;
            border: none;
            border-radius: 12px;
            cursor: pointer;
            font-weight: 500;
        }
        .btn-approve { background-color: #2e7d32; color: #fff; }
        .btn-approve:hover { background-color: #388e3c; }
        .btn-approve:disabled { background-color: #1a3d1a; color: #666; cursor: default; }
        .btn-deny { background-color: #c62828; color: #fff; }
        .btn-deny:hover { background-color: #d32f2f; }
        .btn-deny:disabled { background-color: #3d1a1a; color: #666; cursor: default; }
        .msg { margin-top: 16px; font-size: 14px; }
        .msg.error { color: #ff4444; }
        .msg.success { color: #4caf50; }
    </style>
</head>
<body>
    <div class="link-box">
        <h2>Link a Device</h2>
        <p class="subtitle">Enter the code shown on the device you want to link</p>
        <input type="text" id="code-input" maxlength="6" placeholder="XXXXXX" autofocus>
        <p class="ip-hint" id="ip-hint"></p>
        <div class="buttons">
            <button class="btn btn-deny" id="deny-btn" disabled>Deny</button>
            <button class="btn btn-approve" id="approve-btn" disabled>Approve</button>
        </div>
        <p class="msg" id="msg"></p>
    </div>
    <script>
    (function() {
        const codeInput = document.getElementById('code-input');
        const approveBtn = document.getElementById('approve-btn');
        const denyBtn = document.getElementById('deny-btn');
        const ipHint = document.getElementById('ip-hint');
        const msg = document.getElementById('msg');
        let validatedCode = null;

        // Pre-fill from URL
        const params = new URLSearchParams(location.search);
        if (params.get('code')) {
            codeInput.value = params.get('code').toUpperCase();
            validateCode();
        }

        codeInput.addEventListener('input', function() {
            this.value = this.value.toUpperCase().replace(/[^ABCDEFGHJKLMNPQRSTUVWXYZ23456789]/g, '');
            ipHint.textContent = '';
            msg.textContent = '';
            approveBtn.disabled = true;
            denyBtn.disabled = true;
            validatedCode = null;
            if (this.value.length === 6) validateCode();
        });

        async function validateCode() {
            const code = codeInput.value;
            try {
                const res = await fetch('/api/pair/status/' + code);
                const data = await res.json();
                if (data.status === 'pending') {
                    ipHint.textContent = 'Requesting device: ' + data.requester_ip;
                    approveBtn.disabled = false;
                    denyBtn.disabled = false;
                    validatedCode = code;
                } else {
                    msg.textContent = data.status === 'expired' ? 'Code expired or invalid.' : 'Code already ' + data.status + '.';
                    msg.className = 'msg error';
                }
            } catch (err) {
                msg.textContent = 'Network error';
                msg.className = 'msg error';
            }
        }

        approveBtn.addEventListener('click', async () => {
            if (!validatedCode) return;
            approveBtn.disabled = true;
            denyBtn.disabled = true;
            try {
                const res = await fetch('/api/pair/approve', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({code: validatedCode})
                });
                if (res.ok) {
                    msg.textContent = 'Device linked successfully! Redirecting...';
                    msg.className = 'msg success';
                    codeInput.disabled = true;
                    setTimeout(() => { location.href = '/'; }, 2000);
                } else {
                    const data = await res.json();
                    msg.textContent = data.detail || 'Failed';
                    msg.className = 'msg error';
                    approveBtn.disabled = false;
                    denyBtn.disabled = false;
                }
            } catch (err) {
                msg.textContent = 'Network error';
                msg.className = 'msg error';
                approveBtn.disabled = false;
                denyBtn.disabled = false;
            }
        });

        denyBtn.addEventListener('click', async () => {
            if (!validatedCode) return;
            approveBtn.disabled = true;
            denyBtn.disabled = true;
            try {
                const res = await fetch('/api/pair/deny', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({code: validatedCode})
                });
                if (res.ok) {
                    msg.textContent = 'Pairing denied.';
                    msg.className = 'msg error';
                    codeInput.value = '';
                    codeInput.disabled = false;
                    validatedCode = null;
                }
            } catch (err) {
                msg.textContent = 'Network error';
                msg.className = 'msg error';
            }
        });
    })();
    </script>
</body>
</html>"""


@router.get("/link")
async def link_page(request: Request, code: str = ""):
    if not _get_password():
        return RedirectResponse(url="/", status_code=302)
    if not verify_session(request):
        next_url = "/link"
        if code:
            next_url += f"?code={quote(code, safe='')}"
        return RedirectResponse(url=f"/login?next={quote(next_url, safe='')}", status_code=302)
    return HTMLResponse(LINK_PAGE)


@router.post("/api/pair/approve")
async def pair_approve(request: Request, body: dict, auth: bool = Depends(require_auth)):
    code = body.get("code", "").upper()
    req = PAIRING_REQUESTS.get(code)
    if not req or req["expires_at"] < time.time():
        raise HTTPException(status_code=404, detail="Code expired or invalid")
    if req["status"] != "pending":
        raise HTTPException(status_code=400, detail="Code already used")
    req["status"] = "approved"
    log.info(f"Pairing approved for code {code} (requester IP: {req['requester_ip']})")
    return {"status": "approved"}


@router.post("/api/pair/deny")
async def pair_deny(request: Request, body: dict, auth: bool = Depends(require_auth)):
    code = body.get("code", "").upper()
    req = PAIRING_REQUESTS.get(code)
    if not req or req["expires_at"] < time.time():
        raise HTTPException(status_code=404, detail="Code expired or invalid")
    if req["status"] != "pending":
        raise HTTPException(status_code=400, detail="Code already used")
    req["status"] = "denied"
    if req.get("session_token"):
        profiles_db.delete_session(req["session_token"])
    log.info(f"Pairing denied for code {code}")
    return {"status": "denied"}


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
