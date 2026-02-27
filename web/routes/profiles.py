# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Profile management routes."""
import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, Depends
from pydantic import BaseModel, Field

from auth import require_auth, require_profile, get_profile_id, get_session, verify_session
from helpers import maybe_long_cleanup
import profiles_db as db

router = APIRouter(prefix="/api/profiles")


# ── Request models ──────────────────────────────────────────────────────────

class CreateProfileReq(BaseModel):
    name: str = Field(..., min_length=1, max_length=30, pattern=r'^.+$')
    pin: str | None = None
    avatar_color: str = Field(default="#cc0000", pattern=r'^#[0-9a-fA-F]{6}$|^transparent$')
    avatar_emoji: str = ""
    password: str | None = None  # Only used during first-run setup

class SelectProfileReq(BaseModel):
    pin: str | None = None

class UpdatePrefsReq(BaseModel):
    quality: int | None = None
    subtitle_lang: str | None = None
    cookie_mode: str | None = None

class SavePositionReq(BaseModel):
    video_id: str
    position: float
    title: str = ""
    channel: str = ""
    thumbnail: str = ""
    duration: int = 0
    duration_str: str = Field(default="", pattern=r'^(\d{1,2}:\d{2}(:\d{2})?)?$')

class EditProfileReq(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=30)
    avatar_color: str | None = Field(default=None, pattern=r'^#[0-9a-fA-F]{6}$|^transparent$')
    avatar_emoji: str | None = None
    pin: str | None = None  # absent from JSON = no change, null = remove, string = set


class FavoriteReq(BaseModel):
    title: str = ""
    channel: str = ""
    thumbnail: str = ""
    duration: int = 0
    duration_str: str = Field(default="", pattern=r'^(\d{1,2}:\d{2}(:\d{2})?)?$')
    item_type: Literal["video", "playlist", "mix", "live"] = "video"
    playlist_id: str = ""
    first_video_id: str = ""
    video_count: str = ""

class FollowChannelReq(BaseModel):
    channel_name: str = ""
    avatar_url: str = ""

class UpdatePasswordReq(BaseModel):
    password: str | None = None  # None or empty = remove password

class UpdateAllowEmbedReq(BaseModel):
    allow_embed: bool = False

class UpdateSBPrefsReq(BaseModel):
    enabled: bool = True
    categories: list[str] = []


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

@router.get("/boot")
async def boot(request: Request):
    """Single endpoint to determine app state on load."""
    profiles = db.list_profiles()
    if not profiles and not db.get_app_password():
        return {"state": "first-run"}
    if not verify_session(request):
        return {"state": "login-required"}
    pid = get_profile_id(request)
    if pid:
        profile = db.get_profile(pid)
        if profile:
            return {"state": "ready", "profile": profile}
    # Hourly cleanup (cache expiry, etc.) — runs here and in list_profiles because
    # these are the only idle-state endpoints hit regularly. Not needed in the
    # "ready" branch above since that returns immediately without listing.
    maybe_long_cleanup()
    return {"state": "profile-select", "profiles": profiles}


@router.get("")
async def list_profiles(auth: bool = Depends(require_auth)):
    maybe_long_cleanup()  # also triggered from boot/profile-select above
    return db.list_profiles()


@router.post("")
async def create_profile(req: CreateProfileReq, request: Request, response: Response,
                         auth: bool = Depends(require_auth)):
    profiles = db.list_profiles()
    is_first_run = not profiles and db.get_app_password() is None
    # First profile: anyone can create. After that: admin only.
    if profiles:
        _require_admin(request)
    try:
        profile = db.create_profile(req.name, req.pin, req.avatar_color, req.avatar_emoji)
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(status_code=409, detail="Name already taken")
        raise HTTPException(status_code=400, detail=str(e))
    # First-run: also set app password and create authenticated session
    if is_first_run:
        if not req.password or len(req.password) < 4:
            raise HTTPException(status_code=400, detail="Password must be at least 4 characters")
        db.set_app_password(req.password)
        token, _ = get_session(request)
        db.set_session_profile(token, profile["id"])
        response.set_cookie(key="pytr_session", value=token, max_age=10 * 365 * 86400, httponly=True, samesite="lax")
    return profile


@router.delete("/profile/{profile_id}")
async def delete_profile(profile_id: int, request: Request, auth: bool = Depends(require_auth)):
    _require_admin(request)
    current = get_profile_id(request)
    if current == profile_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own profile")
    target = db.get_profile(profile_id)
    if not target:
        raise HTTPException(status_code=404, detail="Profile not found")
    db.delete_profile(profile_id)
    db.clear_profile_from_sessions(profile_id)
    return {"ok": True}


@router.post("/select/{profile_id}")
async def select_profile(profile_id: int, req: SelectProfileReq,
                         request: Request, response: Response,
                         auth: bool = Depends(require_auth)):
    profile = db.get_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    # Skip PIN check if already on this profile or if sole profile (already authenticated via app password)
    current_pid = get_profile_id(request)
    solo = len(db.list_profiles()) == 1
    if profile["has_pin"] and current_pid != profile_id and not solo:
        if not req.pin or not db.verify_pin(profile_id, req.pin):
            raise HTTPException(status_code=403, detail="Invalid PIN")
    # Store in session
    token, session = get_session(request)
    db.set_session_profile(token, profile_id)
    # Set cookie on the injected response so FastAPI includes it
    response.set_cookie(key="pytr_session", value=token, max_age=10 * 365 * 86400, httponly=True, samesite="lax")
    return {"ok": True, "profile": profile}


@router.put("/edit")
async def edit_profile(req: EditProfileReq, profile_id: int = Depends(require_profile)):
    # PIN intent: absent from JSON = no change, null = remove, string = set
    pin_provided = "pin" in req.model_fields_set
    pin = None
    if pin_provided:
        pin = req.pin.strip() if req.pin else None
        if pin and (len(pin) != 4 or not pin.isdigit()):
            raise HTTPException(status_code=400, detail="PIN must be exactly 4 digits")
    try:
        profile = db.update_profile(
            profile_id,
            name=req.name,
            avatar_color=req.avatar_color,
            avatar_emoji=req.avatar_emoji,
            pin=pin,
            pin_provided=pin_provided,
        )
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(status_code=409, detail="Name already taken")
        raise HTTPException(status_code=400, detail=str(e))
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


@router.put("/preferences")
async def update_preferences(req: UpdatePrefsReq, profile_id: int = Depends(require_profile)):
    db.update_preferences(profile_id, req.quality, req.subtitle_lang, req.cookie_mode)
    return {"ok": True}


@router.get("/history")
async def get_history(limit: int = 50, offset: int = 0,
                      profile_id: int = Depends(require_profile)):
    return db.get_watch_history(profile_id, limit, offset)


@router.delete("/history")
async def clear_history(profile_id: int = Depends(require_profile)):
    db.clear_watch_history(profile_id)
    return {"ok": True}


@router.delete("/history/{video_id}")
async def delete_history_entry(video_id: str, profile_id: int = Depends(require_profile)):
    db.delete_history_entry(profile_id, video_id)
    return {"ok": True}


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
                        type: str | None = None,
                        profile_id: int = Depends(require_profile)):
    return db.get_favorites(profile_id, limit, offset, item_type=type)


@router.delete("/favorites")
async def clear_favorites(profile_id: int = Depends(require_profile)):
    db.clear_favorites(profile_id)
    return {"ok": True}


@router.post("/favorites/{video_id}")
async def add_favorite(video_id: str, req: FavoriteReq,
                       profile_id: int = Depends(require_profile)):
    db.add_favorite(profile_id, video_id, req.title, req.channel,
                    req.thumbnail, req.duration, req.duration_str,
                    req.item_type, req.playlist_id, req.first_video_id, req.video_count)
    return {"ok": True}


@router.delete("/favorites/{video_id}")
async def remove_favorite(video_id: str, profile_id: int = Depends(require_profile)):
    db.remove_favorite(profile_id, video_id)
    return {"ok": True}


@router.get("/favorites/{video_id}/status")
async def favorite_status(video_id: str, profile_id: int = Depends(require_profile)):
    return {"is_favorite": db.is_favorite(profile_id, video_id)}


# ── Followed Channels ─────────────────────────────────────────────────────

@router.get("/channels")
async def get_followed_channels(profile_id: int = Depends(require_profile)):
    return db.get_followed_channels(profile_id)


@router.post("/channels/{channel_id}")
async def follow_channel(channel_id: str, req: FollowChannelReq,
                          profile_id: int = Depends(require_profile)):
    db.follow_channel(profile_id, channel_id, req.channel_name, req.avatar_url)
    return {"ok": True}


@router.delete("/channels/{channel_id}")
async def unfollow_channel(channel_id: str, profile_id: int = Depends(require_profile)):
    db.unfollow_channel(profile_id, channel_id)
    return {"ok": True}


@router.delete("/channels")
async def clear_followed_channels(profile_id: int = Depends(require_profile)):
    db.clear_followed_channels(profile_id)
    return {"ok": True}


@router.get("/channels/{channel_id}/status")
async def channel_follow_status(channel_id: str, profile_id: int = Depends(require_profile)):
    return {"is_following": db.is_following(profile_id, channel_id)}


# ── SponsorBlock preferences (per-user) ───────────────────────────────────

@router.put("/preferences/sponsorblock")
async def update_sb_prefs(req: UpdateSBPrefsReq, profile_id: int = Depends(require_profile)):
    valid_cats = {"sponsor", "intro", "outro", "selfpromo", "interaction", "preview", "filler", "music_offtopic"}
    cats = [c for c in req.categories if c in valid_cats]
    prefs = json.dumps({"enabled": req.enabled, "categories": cats})
    db.update_sb_prefs(profile_id, prefs)
    return {"ok": True}


# ── Settings (admin only) ──────────────────────────────────────────────────

@router.get("/settings")
async def get_settings(request: Request, auth: bool = Depends(require_auth)):
    _require_admin(request)
    raw = db.get_setting("registered_tvs")
    tvs = []
    if raw:
        try:
            tvs = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass
    # Mask tokens for display (exclude ssh_key from response)
    masked = []
    for t in tvs:
        tok = t.get("token", "")
        masked.append({
            "name": t.get("name", ""),
            "type": t.get("type", "webos"),
            "has_key": bool(t.get("ssh_key")),
            "has_token": bool(tok),
            "token_masked": (tok[:6] + "..." + tok[-4:]) if len(tok) > 10 else ("***" if tok else ""),
            "last_renewed": t.get("last_renewed"),
            "last_error": t.get("last_error"),
        })
    return {
        "allow_embed": db.get_setting("allow_embed") == "1",
        "registered_tvs": masked,
    }


@router.put("/settings/password")
async def update_password(req: UpdatePasswordReq, request: Request, response: Response,
                          auth: bool = Depends(require_auth)):
    first_run = db.get_app_password() is None
    # First-run: no password set yet and no profile selected — allow setting initial password
    if not first_run or get_profile_id(request) is not None:
        _require_admin(request)
    if not req.password or len(req.password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters")
    db.set_app_password(req.password)
    # On first-run, create a session so subsequent calls (selectProfile) are authenticated
    if first_run:
        token, _ = get_session(request)
        response.set_cookie(key="pytr_session", value=token, max_age=10 * 365 * 86400, httponly=True, samesite="lax")
    return {"ok": True}


@router.put("/settings/allow-embed")
async def update_allow_embed(req: UpdateAllowEmbedReq, request: Request,
                             auth: bool = Depends(require_auth)):
    _require_admin(request)
    db.set_setting("allow_embed", "1" if req.allow_embed else None)
    return {"ok": True, "allow_embed": req.allow_embed}


class AddWebosTokenReq(BaseModel):
    token: str = Field(..., min_length=1, max_length=200)
    name: str = Field(default="", max_length=50)


@router.post("/settings/webos-token")
async def add_webos_token(req: AddWebosTokenReq, request: Request,
                          auth: bool = Depends(require_auth)):
    _require_admin(request)
    raw = db.get_setting("registered_tvs")
    tvs = []
    if raw:
        try:
            tvs = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass
    token_val = req.token.strip()
    ip = req.name.strip()
    # Update existing entry for this IP (preserves ssh_key), or create new
    found = False
    for t in tvs:
        if t.get("name") == ip:
            t["token"] = token_val
            t["type"] = "webos"
            found = True
            break
    if not found:
        tvs.append({"token": token_val, "name": ip, "type": "webos"})
    db.set_setting("registered_tvs", json.dumps(tvs))
    return {"ok": True}


@router.delete("/settings/registered-tv/{index}")
async def delete_registered_tv(index: int, request: Request,
                                auth: bool = Depends(require_auth)):
    _require_admin(request)
    raw = db.get_setting("registered_tvs")
    tvs = []
    if raw:
        try:
            tvs = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass
    if 0 <= index < len(tvs):
        tvs.pop(index)
    db.set_setting("registered_tvs", json.dumps(tvs) if tvs else None)
    return {"ok": True}
