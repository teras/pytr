# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Remote control: WebSocket relay, device listing, rename, position save."""
import asyncio
import hashlib
import logging
import time
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect

import profiles_db
from auth import require_auth, require_profile, get_profile_id, require_admin, extract_token

log = logging.getLogger(__name__)

router = APIRouter()

# ── In-memory state ─────────────────────────────────────────────────────────
# conn_key = f"{token}:{tab_id}" — unique per browser tab

_connections: dict[str, WebSocket] = {}       # conn_key → WebSocket
_device_states: dict[str, dict] = {}          # conn_key → latest player state
_pairings: dict[str, str] = {}                # remote_conn_key → target_conn_key
_conn_to_token: dict[str, str] = {}           # conn_key → token
_token_to_profile: dict[str, int] = {}        # token → profile_id (session-level)
_token_to_name: dict[str, str] = {}           # token → device_name (session-level)
_last_position_save: dict[str, float] = {}    # conn_key → last save timestamp

_POSITION_SAVE_INTERVAL = 5  # save to DB at most every 5 seconds


def _device_id_from_key(conn_key: str) -> str:
    """Deterministic, non-reversible device_id from conn_key."""
    return hashlib.sha256(conn_key.encode()).hexdigest()[:12]


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
    tab_id = websocket.query_params.get("tab", "default")

    # If no cookie, accept and wait for first-message auth (Bearer)
    if not token:
        await websocket.accept()
        try:
            data = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
            if data.get("type") == "auth" and data.get("token"):
                bearer_token = data["token"]
                session = profiles_db.get_session(bearer_token)
                if session and session.get("bearer_allowed") and session.get("profile_id") is not None:
                    token = bearer_token
                else:
                    await websocket.close(code=4001, reason="Invalid bearer token")
                    return
            else:
                await websocket.close(code=4001, reason="No session")
                return
        except Exception:
            await websocket.close(code=4001, reason="Auth timeout")
            return
    else:
        session = profiles_db.get_session(token)
        if not session or session.get("profile_id") is None:
            await websocket.accept()
            await websocket.close(code=4001, reason="Invalid session")
            return
        await websocket.accept()

    profile_id = session["profile_id"]
    conn_key = f"{token}:{tab_id}"

    # If same conn_key already exists (tab reconnect), close the old WS
    old_ws = _connections.get(conn_key)
    if old_ws:
        try:
            await old_ws.close(code=1000, reason="Replaced by new connection")
        except Exception:
            pass

    # Register connection
    _connections[conn_key] = websocket
    _conn_to_token[conn_key] = token
    _token_to_profile[token] = profile_id

    # Cache device name (session-level, only if not already cached)
    if token not in _token_to_name:
        sessions = profiles_db.get_online_sessions(profile_id)
        for s in sessions:
            if s["token"] == token:
                _token_to_name[token] = s["device_name"]
                break

    log.info(f"WebSocket connected: device={_device_id_from_key(conn_key)} profile={profile_id} tab={tab_id}")

    try:
        while True:
            data = await websocket.receive_json()
            await _handle_message(conn_key, data)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.warning(f"WebSocket error: {e}")
    finally:
        await _cleanup_connection(conn_key, websocket)


async def _cleanup_connection(conn_key: str, old_ws: WebSocket):
    """Clean up when a tab disconnects."""
    if _connections.get(conn_key) is not old_ws:
        log.info(f"WebSocket replaced (reconnect), skipping cleanup")
        return

    log.info(f"WebSocket disconnected: device={_device_id_from_key(conn_key)}")

    token = _conn_to_token.get(conn_key)

    # Flush final position save
    state = _device_states.get(conn_key)
    if state:
        _last_position_save.pop(conn_key, None)  # force save (no throttle)
        _save_position_from_state(conn_key, state)

    _connections.pop(conn_key, None)
    _device_states.pop(conn_key, None)
    _conn_to_token.pop(conn_key, None)
    _last_position_save.pop(conn_key, None)

    # Only clean session-level dicts when no more conn_keys reference this token
    if token and not any(t == token for t in _conn_to_token.values()):
        _token_to_profile.pop(token, None)
        _token_to_name.pop(token, None)

    # If this was a target, notify all remotes controlling it
    remotes_to_notify = [r for r, t in _pairings.items() if t == conn_key]
    for remote_ck in remotes_to_notify:
        _pairings.pop(remote_ck, None)
        ws = _connections.get(remote_ck)
        if ws:
            await _send_json(ws, {"type": "target_disconnected"})

    # If this was a remote, notify the target
    target_ck = _pairings.pop(conn_key, None)
    if target_ck:
        ws = _connections.get(target_ck)
        if ws:
            await _send_json(ws, {"type": "remote_disconnected"})


async def _handle_message(sender_ck: str, data: dict):
    msg_type = data.get("type")

    if msg_type == "pair":
        await _handle_pair(sender_ck, data)
    elif msg_type == "unpair":
        await _handle_unpair(sender_ck)
    elif msg_type == "command":
        await _handle_command(sender_ck, data)
    elif msg_type == "state":
        await _handle_state(sender_ck, data)


async def _handle_pair(remote_ck: str, data: dict):
    target_device_id = data.get("device_id")
    remote_token = _conn_to_token.get(remote_ck)
    remote_profile = _token_to_profile.get(remote_token)

    # Find matching device — only within the same profile
    target_ck = None
    for ck in _connections:
        ck_token = _conn_to_token.get(ck)
        if _token_to_profile.get(ck_token) == remote_profile and _device_id_from_key(ck) == target_device_id:
            target_ck = ck
            break

    if not target_ck:
        ws = _connections.get(remote_ck)
        if ws:
            await _send_json(ws, {"type": "error", "message": "Device not found or offline"})
        return

    # Unpair previous if any
    old_target = _pairings.get(remote_ck)
    if old_target and old_target != target_ck:
        ws = _connections.get(old_target)
        if ws:
            await _send_json(ws, {"type": "remote_disconnected"})

    _pairings[remote_ck] = target_ck

    # Notify remote
    target_token = _conn_to_token.get(target_ck)
    target_name = _token_to_name.get(target_token, "Device") if target_token else "Device"
    ws = _connections.get(remote_ck)
    if ws:
        msg = {"type": "paired", "device_name": target_name}
        state = _device_states.get(target_ck)
        if state:
            msg["state"] = state
        await _send_json(ws, msg)

    # Notify target
    remote_name = _token_to_name.get(remote_token, "Remote") if remote_token else "Remote"
    ws = _connections.get(target_ck)
    if ws:
        await _send_json(ws, {"type": "remote_connected", "remote_name": remote_name})


async def _handle_unpair(remote_ck: str):
    target_ck = _pairings.pop(remote_ck, None)
    if target_ck:
        ws = _connections.get(target_ck)
        if ws:
            await _send_json(ws, {"type": "remote_disconnected"})


async def _handle_command(remote_ck: str, data: dict):
    target_ck = _pairings.get(remote_ck)
    if not target_ck:
        ws = _connections.get(remote_ck)
        if ws:
            await _send_json(ws, {"type": "error", "message": "Not paired with any device"})
        return

    ws = _connections.get(target_ck)
    if not ws:
        _pairings.pop(remote_ck, None)
        ws = _connections.get(remote_ck)
        if ws:
            await _send_json(ws, {"type": "target_disconnected"})
        return

    # Forward command to target
    await _send_json(ws, {"type": "command", "action": data.get("action"), **{k: v for k, v in data.items() if k not in ("type",)}})


async def _handle_state(sender_ck: str, data: dict):
    state = {k: v for k, v in data.items() if k != "type"}

    # If video changed, flush the old position first
    old_state = _device_states.get(sender_ck)
    if old_state and old_state.get("videoId") and old_state["videoId"] != state.get("videoId"):
        _last_position_save.pop(sender_ck, None)  # bypass throttle
        _save_position_from_state(sender_ck, old_state)

    _device_states[sender_ck] = state

    # Save position to DB (throttled)
    _save_position_from_state(sender_ck, state)

    # Forward to all paired remotes
    for remote_ck, target_ck in _pairings.items():
        if target_ck == sender_ck:
            ws = _connections.get(remote_ck)
            if ws:
                await _send_json(ws, {"type": "state", **state})


def _save_position_from_state(conn_key: str, state: dict):
    """Save playback position to DB, throttled to every 5 seconds."""
    if state.get("private"):
        return
    token = _conn_to_token.get(conn_key)
    profile_id = _token_to_profile.get(token) if token else None
    if not profile_id:
        return

    video_id = state.get("videoId")
    current_time = state.get("currentTime", 0)
    duration = state.get("duration", 0)
    if not video_id or not current_time:
        return

    now = time.time()
    last_save = _last_position_save.get(conn_key, 0)
    if now - last_save < _POSITION_SAVE_INTERVAL:
        return

    _last_position_save[conn_key] = now

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

    # Near end → mark as watched (position 0) but preserve metadata
    if duration > 0 and (current_time > duration - 30 or current_time / duration > 0.95):
        profiles_db.save_position(profile_id, video_id, 0,
                                  title, channel, thumbnail, dur_int, dur_str)
        return

    if current_time > 5:
        profiles_db.save_position(profile_id, video_id, round(current_time, 1),
                                  title, channel, thumbnail, dur_int, dur_str)


# ── REST endpoints ──────────────────────────────────────────────────────────

@router.get("/api/remote/devices")
async def list_devices(request: Request, profile_id: int = Depends(require_profile)):
    """List connected devices for the current profile, grouped by device."""
    token, _ = extract_token(request)
    tab_id = request.query_params.get("tab_id", "default")
    self_ck = f"{token}:{tab_id}" if token else None

    # Group conn_keys by token (device)
    device_groups: dict[str, list[str]] = {}
    for ck in _connections:
        ck_token = _conn_to_token.get(ck)
        if not ck_token:
            continue
        if _token_to_profile.get(ck_token) != profile_id:
            continue
        if ck == self_ck:
            continue  # exclude self
        if ck in _pairings:
            continue  # exclude tabs acting as remote controllers
        device_groups.setdefault(ck_token, []).append(ck)

    devices = []
    for dev_token, conn_keys in device_groups.items():
        tabs = []
        for ck in conn_keys:
            state = _device_states.get(ck)
            tab_info = {
                "device_id": _device_id_from_key(ck),
            }
            if state:
                tab_info["video_title"] = state.get("title", "")
                tab_info["video_thumbnail"] = state.get("thumbnail", "")
                if state.get("paused"):
                    tab_info["status"] = "paused"
                else:
                    tab_info["status"] = "playing"
            else:
                tab_info["status"] = "idle"
            tabs.append(tab_info)
        devices.append({
            "device_name": _token_to_name.get(dev_token, "Unknown Device"),
            "tabs": tabs,
        })
    return devices


@router.post("/api/remote/rename")
async def rename_device(request: Request, body: dict, auth: bool = Depends(require_auth)):
    """Rename the current session's device (admin only)."""
    require_admin(request)
    token, _ = extract_token(request)
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
