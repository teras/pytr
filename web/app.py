# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""PYTR - Private YouTube Relay. FastAPI entry point."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler()]
)


@asynccontextmanager
async def lifespan(app):
    # Start background tasks
    from helpers import webos_renewal_loop
    renewal_task = asyncio.create_task(webos_renewal_loop())
    # Warm up external API connections
    from routes.sponsorblock import warmup_connection
    asyncio.create_task(warmup_connection())
    yield
    # Shutdown
    renewal_task.cancel()
    from helpers import http_client
    await http_client.aclose()
    logging.getLogger(__name__).info("httpx client closed")


app = FastAPI(title="PYTR", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

ERROR_MESSAGES = {
    404: "This page got lost in the stream...",
    403: "Whoa there! You're not supposed to be here.",
    405: "That's not how this works...",
    500: "Something broke backstage!",
}

ERROR_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{code} — PYTR</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0f0f0f;color:#f1f1f1;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
display:flex;align-items:center;justify-content:center;min-height:100vh;text-align:center}}
.card{{background:#1a1a1a;border-radius:16px;padding:48px 40px;max-width:420px;width:90%}}
.mascot{{width:200px;height:auto;margin-bottom:24px;animation:wobble 2s ease-in-out infinite}}
@keyframes wobble{{0%,100%{{transform:rotate(0)}}25%{{transform:rotate(-3deg)}}75%{{transform:rotate(3deg)}}}}
.code{{font-family:'Courier New',monospace;font-size:72px;font-weight:bold;color:#cc3333;line-height:1;margin-bottom:8px}}
.msg{{font-size:18px;color:#aaa;margin-bottom:32px}}
.btn{{display:inline-block;background:#cc0000;color:#fff;text-decoration:none;padding:12px 32px;
border-radius:8px;font-size:16px;font-weight:600;transition:background .2s}}
.btn:hover{{background:#e00}}
</style></head><body><div class="card">
<img src="/static/error.png" alt="Peter" class="mascot">
<div class="code">{code}</div>
<p class="msg">{message}</p>
<a href="/" class="btn">Go Home</a>
</div></body></html>"""


def render_error_page(code: int, detail: str = "") -> str:
    message = ERROR_MESSAGES.get(code, detail or "Well, that didn't go as planned.")
    return ERROR_PAGE.format(code=code, message=message)


@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return HTMLResponse(render_error_page(exc.status_code, exc.detail), status_code=exc.status_code)


@app.exception_handler(Exception)
async def custom_unhandled_exception_handler(request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Internal Server Error"}, status_code=500)
    return HTMLResponse(render_error_page(500), status_code=500)


# Init DB before helpers (helpers reads cookies_browser setting from DB)
import profiles_db
profiles_db.init_db()

import helpers  # noqa: F401 — ensure cache dir + yt-dlp instance created on startup
from helpers import maybe_cleanup, maybe_long_cleanup
from auth import buffer_session_ip


@app.middleware("http")
async def cleanup_middleware(request, call_next):
    maybe_cleanup()
    maybe_long_cleanup()
    buffer_session_ip(request)
    return await call_next(request)

# Register routers
from auth import router as auth_router
from dash import router as dash_router
from hls import router as hls_router
from routes.video import router as video_router
from routes.browse import router as browse_router
from routes.profiles import router as profiles_router
from routes.sponsorblock import router as sponsorblock_router
from routes.tv_setup import router as tv_setup_router, page_router as tv_setup_page_router
from routes.remote import router as remote_router

app.include_router(auth_router)
app.include_router(dash_router)
app.include_router(hls_router)
app.include_router(video_router)
app.include_router(browse_router)
app.include_router(profiles_router)
app.include_router(sponsorblock_router)
app.include_router(tv_setup_router)
app.include_router(tv_setup_page_router)
app.include_router(remote_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
