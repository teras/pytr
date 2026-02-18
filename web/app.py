"""YTP - YouTube Proxy. FastAPI entry point."""
import logging
from pathlib import Path

from dotenv import load_dotenv

# Load .env from current dir or parent dir (for local dev)
load_dotenv()
load_dotenv(Path(__file__).parent.parent / ".env")

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler()]
)

app = FastAPI(title="YTP")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Import helpers to trigger downloads dir cleanup on startup
import helpers  # noqa: F401

# Register routers
from auth import router as auth_router
from dash import router as dash_router
from routes.video import router as video_router
from routes.browse import router as browse_router

app.include_router(auth_router)
app.include_router(dash_router)
app.include_router(video_router)
app.include_router(browse_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
