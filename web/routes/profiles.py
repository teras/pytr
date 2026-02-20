"""Profile management routes."""
from fastapi import APIRouter, HTTPException, Request, Response, Depends
from pydantic import BaseModel

from auth import require_auth, require_profile, get_profile_id, get_session, AUTH_SESSIONS
import profiles_db as db

router = APIRouter(prefix="/api/profiles")


# ── Request models ──────────────────────────────────────────────────────────

class CreateProfileReq(BaseModel):
    name: str
    pin: str | None = None
    avatar_color: str = "#cc0000"
    avatar_emoji: str = ""

class SelectProfileReq(BaseModel):
    pin: str | None = None

class UpdatePrefsReq(BaseModel):
    quality: int | None = None
    subtitle_lang: str | None = None

class SavePositionReq(BaseModel):
    video_id: str
    position: float
    title: str = ""
    channel: str = ""
    thumbnail: str = ""
    duration: int = 0
    duration_str: str = ""

class FavoriteReq(BaseModel):
    title: str = ""
    channel: str = ""
    thumbnail: str = ""
    duration: int = 0
    duration_str: str = ""

class UpdatePasswordReq(BaseModel):
    password: str | None = None  # None or empty = remove password

class UpdateCookiesBrowserReq(BaseModel):
    cookies_browser: str | None = None  # None or empty = disable


# ── Helpers ─────────────────────────────────────────────────────────────────

def _require_admin(request: Request):
    """Check that current profile is admin."""
    pid = get_profile_id(request)
    if pid is None:
        raise HTTPException(status_code=403, detail="No profile selected")
    profile = db.get_profile(pid)
    if not profile or not profile["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin required")


# ── Routes ──────────────────────────────────────────────────────────────────

@router.get("")
async def list_profiles(auth: bool = Depends(require_auth)):
    return db.list_profiles()


@router.post("")
async def create_profile(req: CreateProfileReq, request: Request, response: Response,
                         auth: bool = Depends(require_auth)):
    profiles = db.list_profiles()
    # First profile: anyone can create. After that: admin only.
    if profiles:
        _require_admin(request)
    try:
        profile = db.create_profile(req.name, req.pin, req.avatar_color, req.avatar_emoji)
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(status_code=409, detail="Name already taken")
        raise HTTPException(status_code=400, detail=str(e))
    return profile


@router.delete("/{profile_id}")
async def delete_profile(profile_id: int, request: Request, auth: bool = Depends(require_auth)):
    _require_admin(request)
    # Can't delete yourself
    current = get_profile_id(request)
    if current == profile_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own profile")
    target = db.get_profile(profile_id)
    if not target:
        raise HTTPException(status_code=404, detail="Profile not found")
    db.delete_profile(profile_id)
    # Clear profile_id from any session that had this profile selected
    for session in AUTH_SESSIONS.values():
        if session.get("profile_id") == profile_id:
            session["profile_id"] = None
    return {"ok": True}


@router.post("/select/{profile_id}")
async def select_profile(profile_id: int, req: SelectProfileReq,
                         request: Request, response: Response,
                         auth: bool = Depends(require_auth)):
    profile = db.get_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    if profile["has_pin"]:
        if not req.pin or not db.verify_pin(profile_id, req.pin):
            raise HTTPException(status_code=403, detail="Invalid PIN")
    # Store in session
    token, session = get_session(request)
    session["profile_id"] = profile_id
    AUTH_SESSIONS[token] = session
    # Set cookie on the injected response so FastAPI includes it
    response.set_cookie(key="ytp_session", value=token, httponly=True, samesite="lax")
    return {"ok": True, "profile": profile}


@router.get("/current")
async def current_profile(request: Request, auth: bool = Depends(require_auth)):
    pid = get_profile_id(request)
    if pid is None:
        raise HTTPException(status_code=404, detail="No profile selected")
    profile = db.get_profile(pid)
    if not profile:
        # Profile was deleted
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


@router.post("/deselect")
async def deselect_profile(request: Request, auth: bool = Depends(require_auth)):
    token = request.cookies.get("ytp_session")
    if token and token in AUTH_SESSIONS:
        AUTH_SESSIONS[token]["profile_id"] = None
    return {"ok": True}


@router.put("/preferences")
async def update_preferences(req: UpdatePrefsReq, profile_id: int = Depends(require_profile)):
    db.update_preferences(profile_id, req.quality, req.subtitle_lang)
    return {"ok": True}


@router.get("/history")
async def get_history(limit: int = 50, offset: int = 0,
                      profile_id: int = Depends(require_profile)):
    return db.get_watch_history(profile_id, limit, offset)


@router.post("/position")
async def save_position(req: SavePositionReq, profile_id: int = Depends(require_profile)):
    db.save_position(profile_id, req.video_id, req.position,
                     req.title, req.channel, req.thumbnail,
                     req.duration, req.duration_str)
    return {"ok": True}


@router.get("/position/{video_id}")
async def get_position(video_id: str, profile_id: int = Depends(require_profile)):
    pos = db.get_position(profile_id, video_id)
    return {"position": pos}


@router.get("/favorites")
async def get_favorites(limit: int = 50, offset: int = 0,
                        profile_id: int = Depends(require_profile)):
    return db.get_favorites(profile_id, limit, offset)


@router.post("/favorites/{video_id}")
async def add_favorite(video_id: str, req: FavoriteReq,
                       profile_id: int = Depends(require_profile)):
    db.add_favorite(profile_id, video_id, req.title, req.channel,
                    req.thumbnail, req.duration, req.duration_str)
    return {"ok": True}


@router.delete("/favorites/{video_id}")
async def remove_favorite(video_id: str, profile_id: int = Depends(require_profile)):
    db.remove_favorite(profile_id, video_id)
    return {"ok": True}


@router.get("/favorites/{video_id}/status")
async def favorite_status(video_id: str, profile_id: int = Depends(require_profile)):
    return {"is_favorite": db.is_favorite(profile_id, video_id)}


# ── Settings (admin only) ──────────────────────────────────────────────────

@router.get("/settings")
async def get_settings(request: Request, auth: bool = Depends(require_auth)):
    _require_admin(request)
    return {
        "has_password": db.get_app_password() is not None,
        "cookies_browser": db.get_setting("cookies_browser") or "",
    }


@router.put("/settings/password")
async def update_password(req: UpdatePasswordReq, request: Request,
                          auth: bool = Depends(require_auth)):
    # First-run: no password set yet and no profile selected — allow setting initial password
    if db.get_app_password() is not None or get_profile_id(request) is not None:
        _require_admin(request)
    db.set_app_password(req.password)
    return {"ok": True, "has_password": req.password is not None and len(req.password) > 0}


@router.put("/settings/cookies-browser")
async def update_cookies_browser(req: UpdateCookiesBrowserReq, request: Request,
                                 auth: bool = Depends(require_auth)):
    _require_admin(request)
    value = req.cookies_browser.strip() if req.cookies_browser else None
    db.set_setting("cookies_browser", value)
    # Recreate yt-dlp instance with new cookies setting
    from helpers import init_ydl
    init_ydl()
    return {"ok": True, "cookies_browser": value or ""}
