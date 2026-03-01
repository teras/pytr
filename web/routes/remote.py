# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Remote control: WebSocket relay, device listing, rename, position save."""
import hashlib
import logging
import time
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect

import profiles_db
from auth import require_auth, require_profile, get_profile_id, _require_admin

log = logging.getLogger(__name__)

router = APIRouter()

# ── In-memory state ─────────────────────────────────────────────────────────

_connections: dict[str, WebSocket] = {}       # session_token → WebSocket
_device_states: dict[str, dict] = {}          # session_token → latest player state
_pairings: dict[str, str] = {}                # remote_token → target_token
_token_to_profile: dict[str, int] = {}        # session_token → profile_id
_token_to_name: dict[str, str] = {}           # session_token → device_name (cached)
_last_position_save: dict[str, float] = {}    # session_token → last save timestamp

_POSITION_SAVE_INTERVAL = 5  # save to DB at most every 5 seconds


def _device_id_from_token(token: str) -> str:
    """Deterministic, non-reversible device_id from session token."""
    return hashlib.sha256(token.encode()).hexdigest()[:12]


def _get_token_from_ws(websocket: WebSocket) -> str | None:
    """Extract pytr_session cookie from WebSocket handshake."""
    cookies = websocket.cookies
    return cookies.get("pytr_session")


async def _send_json(ws: WebSocket, data: dict):
    try:
        await ws.send_json(data)
    except Exception:
        pass


# ── WebSocket endpoint ──────────────────────────────────────────────────────

@router.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket):
    token = _get_token_from_ws(websocket)
    if not token:
        await websocket.accept()
        await websocket.close(code=4001, reason="No session")
        return

    session = profiles_db.get_session(token)
    if not session or session.get("profile_id") is None:
        await websocket.accept()
        await websocket.close(code=4001, reason="Invalid session")
        return

    profile_id = session["profile_id"]

    await websocket.accept()

    # Register connection
    device_id = _device_id_from_token(token)
    _connections[token] = websocket
    _token_to_profile[token] = profile_id

    # Cache device name
    sessions = profiles_db.get_online_sessions(profile_id)
    for s in sessions:
        if s["token"] == token:
            _token_to_name[token] = s["device_name"]
            break

    log.info(f"WebSocket connected: device={device_id} profile={profile_id}")

    try:
        while True:
            data = await websocket.receive_json()
            await _handle_message(token, data)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.warning(f"WebSocket error: {e}")
    finally:
        await _cleanup_connection(token, websocket)


async def _cleanup_connection(token: str, old_ws: WebSocket):
    """Clean up when a device disconnects.

    If the same token has already reconnected (new WS replaced old in
    _connections), skip cleanup to avoid destroying the new connection.
    """
    if _connections.get(token) is not old_ws:
        log.info(f"WebSocket replaced (reconnect), skipping cleanup")
        return

    log.info(f"WebSocket disconnected: device={_device_id_from_token(token)}")

    # Flush final position save
    state = _device_states.get(token)
    if state:
        _last_position_save.pop(token, None)  # force save (no throttle)
        _save_position_from_state(token, state)

    _connections.pop(token, None)
    _device_states.pop(token, None)
    _token_to_profile.pop(token, None)
    _token_to_name.pop(token, None)
    _last_position_save.pop(token, None)

    # If this was a target device, notify all remotes controlling it
    remotes_to_notify = [r for r, t in _pairings.items() if t == token]
    for remote_token in remotes_to_notify:
        _pairings.pop(remote_token, None)
        ws = _connections.get(remote_token)
        if ws:
            await _send_json(ws, {"type": "target_disconnected"})

    # If this was a remote, notify the target
    target_token = _pairings.pop(token, None)
    if target_token:
        ws = _connections.get(target_token)
        if ws:
            await _send_json(ws, {"type": "remote_disconnected"})


async def _handle_message(sender_token: str, data: dict):
    msg_type = data.get("type")

    if msg_type == "pair":
        await _handle_pair(sender_token, data)
    elif msg_type == "unpair":
        await _handle_unpair(sender_token)
    elif msg_type == "command":
        await _handle_command(sender_token, data)
    elif msg_type == "state":
        await _handle_state(sender_token, data)


async def _handle_pair(remote_token: str, data: dict):
    target_device_id = data.get("device_id")
    remote_profile = _token_to_profile.get(remote_token)

    # Find matching device — only within the same profile
    target_token = None
    for t in _connections:
        if _token_to_profile.get(t) == remote_profile and _device_id_from_token(t) == target_device_id:
            target_token = t
            break

    if not target_token:
        ws = _connections.get(remote_token)
        if ws:
            await _send_json(ws, {"type": "error", "message": "Device not found or offline"})
        return

    # Unpair previous if any
    old_target = _pairings.get(remote_token)
    if old_target and old_target != target_token:
        ws = _connections.get(old_target)
        if ws:
            await _send_json(ws, {"type": "remote_disconnected"})

    _pairings[remote_token] = target_token

    # Notify remote
    target_name = _token_to_name.get(target_token, "Device")
    ws = _connections.get(remote_token)
    if ws:
        msg = {"type": "paired", "device_name": target_name}
        # Send cached state if available
        state = _device_states.get(target_token)
        if state:
            msg["state"] = state
        await _send_json(ws, msg)

    # Notify target
    remote_name = _token_to_name.get(remote_token, "Remote")
    ws = _connections.get(target_token)
    if ws:
        await _send_json(ws, {"type": "remote_connected", "remote_name": remote_name})


async def _handle_unpair(remote_token: str):
    target_token = _pairings.pop(remote_token, None)
    if target_token:
        ws = _connections.get(target_token)
        if ws:
            await _send_json(ws, {"type": "remote_disconnected"})


async def _handle_command(remote_token: str, data: dict):
    target_token = _pairings.get(remote_token)
    if not target_token:
        ws = _connections.get(remote_token)
        if ws:
            await _send_json(ws, {"type": "error", "message": "Not paired with any device"})
        return

    ws = _connections.get(target_token)
    if not ws:
        _pairings.pop(remote_token, None)
        ws = _connections.get(remote_token)
        if ws:
            await _send_json(ws, {"type": "target_disconnected"})
        return

    # Forward command to target
    await _send_json(ws, {"type": "command", "action": data.get("action"), **{k: v for k, v in data.items() if k not in ("type",)}})


async def _handle_state(sender_token: str, data: dict):
    state = {k: v for k, v in data.items() if k != "type"}

    # If video changed, flush the old position first
    old_state = _device_states.get(sender_token)
    if old_state and old_state.get("videoId") and old_state["videoId"] != state.get("videoId"):
        _last_position_save.pop(sender_token, None)  # bypass throttle
        _save_position_from_state(sender_token, old_state)

    _device_states[sender_token] = state

    # Save position to DB (throttled)
    _save_position_from_state(sender_token, state)

    # Forward to all paired remotes
    for remote_token, target_token in _pairings.items():
        if target_token == sender_token:
            ws = _connections.get(remote_token)
            if ws:
                await _send_json(ws, {"type": "state", **state})


def _save_position_from_state(token: str, state: dict):
    """Save playback position to DB, throttled to every 5 seconds."""
    profile_id = _token_to_profile.get(token)
    if not profile_id:
        return

    video_id = state.get("videoId")
    current_time = state.get("currentTime", 0)
    duration = state.get("duration", 0)
    if not video_id or not current_time:
        return

    now = time.time()
    last_save = _last_position_save.get(token, 0)
    if now - last_save < _POSITION_SAVE_INTERVAL:
        return

    _last_position_save[token] = now

    # Near end → save position 0 to clear (same logic as frontend)
    if duration > 0 and (current_time > duration - 30 or current_time / duration > 0.95):
        profiles_db.save_position(profile_id, video_id, 0)
        return

    if current_time > 5:
        title = state.get("title", "")
        channel = state.get("channel", "")
        thumbnail = state.get("thumbnail", "")
        dur_int = int(duration) if duration else 0
        # Format duration string
        dur_str = ""
        if dur_int > 0:
            h, rem = divmod(dur_int, 3600)
            m, s = divmod(rem, 60)
            dur_str = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        profiles_db.save_position(profile_id, video_id, round(current_time, 1),
                                  title, channel, thumbnail, dur_int, dur_str)


# ── REST endpoints ──────────────────────────────────────────────────────────

@router.get("/api/remote/devices")
async def list_devices(request: Request, profile_id: int = Depends(require_profile)):
    """List connected devices for the current profile."""
    token = request.cookies.get("pytr_session")
    devices = []
    for dev_token in _connections:
        if _token_to_profile.get(dev_token) != profile_id:
            continue
        if dev_token == token:
            continue  # exclude self
        if dev_token in _pairings:
            continue  # exclude devices acting as remote controllers
        devices.append({
            "device_id": _device_id_from_token(dev_token),
            "device_name": _token_to_name.get(dev_token, "Unknown Device"),
            "has_state": dev_token in _device_states,
        })
    return devices


@router.post("/api/remote/rename")
async def rename_device(request: Request, body: dict, auth: bool = Depends(require_auth)):
    """Rename the current session's device (admin only)."""
    _require_admin(request)
    token = request.cookies.get("pytr_session")
    if not token:
        raise HTTPException(status_code=400, detail="No session")
    name = body.get("device_name", "").strip()
    if not name or len(name) > 50:
        raise HTTPException(status_code=400, detail="Device name must be 1-50 characters")
    profiles_db.update_session_device_name(token, name)
    # Update cache
    if token in _token_to_name:
        _token_to_name[token] = name
    return {"ok": True, "device_name": name}
