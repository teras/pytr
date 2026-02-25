# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""PYTR - Private YouTube Relay. FastAPI entry point."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler()]
)


@asynccontextmanager
async def lifespan(app):
    yield
    # Shutdown: close httpx client
    from helpers import http_client
    await http_client.aclose()
    logging.getLogger(__name__).info("httpx client closed")


app = FastAPI(title="PYTR", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


# Init DB before helpers (helpers reads cookies_browser setting from DB)
import profiles_db
profiles_db.init_db()

import helpers  # noqa: F401 — ensure cache dir + yt-dlp instance created on startup
from helpers import maybe_cleanup
from auth import buffer_session_ip


@app.middleware("http")
async def cleanup_middleware(request, call_next):
    maybe_cleanup()
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

app.include_router(auth_router)
app.include_router(dash_router)
app.include_router(hls_router)
app.include_router(video_router)
app.include_router(browse_router)
app.include_router(profiles_router)
app.include_router(sponsorblock_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
