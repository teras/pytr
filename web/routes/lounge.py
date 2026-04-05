# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""YouTube Lounge API: screen-side client for 'Link with TV code' pairing.

Faithful reimplementation of SmartTube's screen-side Lounge protocol:
- Sequential command processing (single worker, no fire-and-forget)
- Synchronous state POSTs (each completes before next command)
- Static RID=1337 for data POSTs, auto-incrementing ofs counter
- Null field filtering (skip None/empty values)
"""
import asyncio
import json
import logging
import time
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException

from auth import require_auth

log = logging.getLogger(__name__)

router = APIRouter()

# ── Constants ─────────────────────────────────────────────────────────────────
_YT_BASE = "https://www.youtube.com/api/lounge"
_BIND_URL = f"{_YT_BASE}/bc/bind"
_APP = "lb-v4"
_ACCESS_TYPE = "permanent"
_BIND_PARAMS = {
    "device": "LOUNGE_SCREEN",
    "theme": "cl",
    "capabilities": "dsp,mic,dpa",
    "mdxVersion": "2",
    "VER": "8",
    "v": "2",
    "t": "1",
    "app": _APP,
    "zx": "xxxxxxxxxxxx",
}

# Match SmartTube's CommandParams constants exactly
_STATE_UNDETECTED = 0
_STATE_PLAYING = 1
_STATE_PAUSED = 2
_STATE_IDLE = 3

# ── Singleton lounge state ────────────────────────────────────────────────────
_screen_id: str | None = None
_lounge_token: str | None = None
_device_id: str | None = None
_session_id: str | None = None
_gsession_id: str | None = None
_pairing_code: str | None = None
_ofs_counter = 0
_listener_task: asyncio.Task | None = None
_cmd_worker_task: asyncio.Task | None = None
_cmd_queue: asyncio.Queue | None = None
_running = False
_screen_name = "PYTR"

_command_callback = None  # async def(command: str, params: dict)
_player_state: dict = {}

# State tracking for event-driven reporting from player callbacks
_last_report_video: str = ""
_last_report_playing: bool | None = None

# Track playlist context from YouTube app (echoed back in state POSTs)
_ctt: str | None = None
_playlist_id: str | None = None
_playlist_index: str | None = None

# Target binding: stable per-tab UUID (sessionStorage-backed client-side) that
# identifies WHICH PYTR tab currently receives lounge commands. Persisted in
# profiles_db so it survives PYTR restarts; the actual conn_key lookup lives
# in remote.py. Never auto-cleared — only explicit unpair or full reset.
_target_tab_uuid: str | None = None

# Pending-command buffer: if a `setPlaylist`/`setVideo` arrives before any tab
# is claimed (or while the target is orphaned), we hold the LAST such command
# here with a timestamp. When a tab is claimed, we replay it if still fresh.
# TTL is 5 minutes — covers "user pressed play on phone, then walked over to
# the TV and opened PYTR" without auto-executing stale commands hours later.
_PENDING_COMMAND_TTL = 300  # seconds
_pending_command: tuple[str, dict, float] | None = None  # (cmd_type, params, ts)


def _get_http_client():
    from helpers import http_client
    return http_client


def _load_persisted():
    global _screen_id, _device_id, _target_tab_uuid
    import profiles_db
    from routes.remote import set_lounge_target_tab_uuid
    _screen_id = profiles_db.get_setting("lounge_screen_id")
    _device_id = profiles_db.get_setting("lounge_device_id")
    _target_tab_uuid = profiles_db.get_setting("lounge_target_tab_uuid")
    set_lounge_target_tab_uuid(_target_tab_uuid)


def has_persisted_session() -> bool:
    import profiles_db
    return profiles_db.get_setting("lounge_screen_id") is not None


def _persist():
    import profiles_db
    profiles_db.set_setting("lounge_screen_id", _screen_id)
    profiles_db.set_setting("lounge_device_id", _device_id)


def _persist_target():
    import profiles_db
    profiles_db.set_setting("lounge_target_tab_uuid", _target_tab_uuid)


# ── YouTube Lounge API calls ──────────────────────────────────────────────────

async def _generate_screen_id() -> str | None:
    try:
        resp = await _get_http_client().get(f"{_YT_BASE}/pairing/generate_screen_id")
        if resp.status_code == 200:
            return resp.text.strip()
    except Exception as e:
        log.error(f"Lounge: generate_screen_id failed: {e}")
    return None


async def _get_lounge_token(screen_id: str) -> str | None:
    try:
        resp = await _get_http_client().post(
            f"{_YT_BASE}/pairing/get_lounge_token_batch",
            data={"screen_ids": screen_id},
        )
        if resp.status_code == 200:
            data = resp.json()
            screens = data.get("screens", [])
            if screens:
                return screens[0].get("loungeToken")
    except Exception as e:
        log.error(f"Lounge: get_lounge_token failed: {e}")
    return None


async def _get_pairing_code(lounge_token: str, screen_id: str, device_id: str) -> str | None:
    try:
        resp = await _get_http_client().post(
            f"{_YT_BASE}/pairing/get_pairing_code?ctx=pair",
            data={
                "lounge_token": lounge_token,
                "screen_id": screen_id,
                "screen_name": _screen_name,
                "access_type": _ACCESS_TYPE,
                "app": _APP,
                "device_id": device_id,
                "qr": "1",
            },
        )
        if resp.status_code == 200:
            text = resp.text.strip()
            try:
                data = json.loads(text)
                code = data.get("code", data.get("pairing_code", text))
            except (json.JSONDecodeError, AttributeError):
                code = text
            code = str(code).replace("-", "").replace(" ", "")
            if len(code) >= 12:
                return f"{code[0:3]}-{code[3:6]}-{code[6:9]}-{code[9:12]}"
            return code
    except Exception as e:
        log.error(f"Lounge: get_pairing_code failed: {e}")
    return None


async def _open_session(device_id: str, lounge_token: str) -> tuple[str | None, str | None, list]:
    """Initial session bind (RID=1337, count=0) — returns (SID, gsessionid, initial_commands)."""
    params = {
        **_BIND_PARAMS,
        "name": _screen_name,
        "id": device_id,
        "loungeIdToken": lounge_token,
        "RID": "1337",
        "AID": "42",
    }
    try:
        resp = await _get_http_client().post(
            _BIND_URL, params=params, data={"count": "0"}, timeout=30.0,
        )
        log.info(f"Lounge: open_session response: {resp.status_code} | {repr(resp.text[:800])}")
        if resp.status_code == 200:
            return _parse_session_ids(resp.text)
    except Exception as e:
        log.error(f"Lounge: open_session failed: {e}")
    return None, None, []


def _parse_session_ids(text: str) -> tuple[str | None, str | None, list]:
    sid = None
    gsid = None
    initial_cmds = []
    try:
        arrays = _parse_response_arrays(text)
        for arr in arrays:
            if len(arr) >= 2 and isinstance(arr[1], list):
                cmd_type = arr[1][0] if arr[1] else None
                if cmd_type == "c" and len(arr[1]) > 1:
                    sid = arr[1][1]
                elif cmd_type == "S" and len(arr[1]) > 1:
                    gsid = arr[1][1]
                elif cmd_type not in ("c", "S"):
                    # Collect initial commands (getNowPlaying, loungeStatus, etc.)
                    params = arr[1][1] if len(arr[1]) > 1 else {}
                    if isinstance(params, str):
                        params = {"value": params}
                    elif not isinstance(params, dict):
                        params = {}
                    initial_cmds.append((cmd_type, params))
    except Exception as e:
        log.error(f"Lounge: parse_session_ids failed: {e}")
    return sid, gsid, initial_cmds


def _parse_response_arrays(text: str) -> list:
    """Parse length-prefixed JSON from YouTube Lounge response."""
    commands = []
    lines = text.strip().split('\n')
    buf = ""
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.isdigit():
            if buf:
                try:
                    outer = json.loads(buf)
                    if isinstance(outer, list):
                        commands.extend(outer)
                except json.JSONDecodeError:
                    pass
                buf = ""
            continue
        buf += line + "\n"
    if buf:
        try:
            outer = json.loads(buf)
            if isinstance(outer, list):
                commands.extend(outer)
        except json.JSONDecodeError:
            pass
    return commands


def _parse_commands(text: str) -> list[tuple[str, dict]]:
    """Parse command arrays into (type, params) tuples."""
    commands = []
    try:
        arrays = json.loads(text.strip())
    except json.JSONDecodeError:
        arrays = _parse_response_arrays(text)

    if not isinstance(arrays, list):
        return commands
    for arr in arrays:
        if not isinstance(arr, list) or len(arr) < 2:
            continue
        inner = arr[1]
        if not isinstance(inner, list) or not inner:
            continue
        cmd_type = inner[0]
        params = inner[1] if len(inner) > 1 else {}
        if isinstance(params, str):
            params = {"value": params}
        elif not isinstance(params, dict):
            params = {}
        commands.append((cmd_type, params))
    return commands


# ── Play dispatch helper ──────────────────────────────────────────────────────

async def _dispatch_play(video_id: str, start_time: float, playlist_id: str | None = None):
    """Forward a play command to the bound cast target, or buffer it for later
    replay if no target is currently reachable. Shared by the setPlaylist and
    setVideo handlers — any new code paths that need to kick off playback from
    a lounge event should go through here so buffer semantics stay uniform."""
    if not _command_callback:
        return
    params = {"videoId": video_id, "startTime": start_time}
    if playlist_id:
        params["playlistId"] = playlist_id
    delivered = await _command_callback("play", params)
    if not delivered:
        _buffer_pending_command("play", params)


# ── Sequential command queue ─────────────────────────────────────────────────

async def _cmd_queue_worker():
    """Process lounge commands one at a time, sequentially.

    Like SmartTube's single-threaded listener: each command is fully handled
    (including state POSTs) before the next one starts. This ensures ofs
    counters are sequential and YouTube doesn't see out-of-order responses.
    """
    while _running:
        try:
            cmd_type, params = await asyncio.wait_for(_cmd_queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            break
        try:
            await _handle_lounge_command(cmd_type, params)
        except Exception as e:
            log.error(f"Lounge: command handler error ({cmd_type}): {e}")


# ── Long-polling listener ─────────────────────────────────────────────────────

async def _listen_loop():
    global _screen_id, _lounge_token, _device_id, _session_id, _gsession_id
    global _pairing_code, _running, _ofs_counter
    global _cmd_queue, _cmd_worker_task

    _running = True

    while _running:
        try:
            if not _screen_id:
                _screen_id = await _generate_screen_id()
                if not _screen_id:
                    log.error("Lounge: failed to get screen_id, retrying in 30s")
                    await asyncio.sleep(30)
                    continue

            if not _lounge_token:
                _lounge_token = await _get_lounge_token(_screen_id)
                if not _lounge_token:
                    log.error("Lounge: failed to get lounge_token, retrying in 30s")
                    _screen_id = None
                    await asyncio.sleep(30)
                    continue

            if not _device_id:
                _device_id = str(uuid.uuid4())

            _persist()

            if not _pairing_code:
                _pairing_code = await _get_pairing_code(_lounge_token, _screen_id, _device_id)
                if _pairing_code:
                    log.info(f"Lounge: pairing code = {_pairing_code}")
                else:
                    _lounge_token = await _get_lounge_token(_screen_id)
                    _pairing_code = await _get_pairing_code(_lounge_token, _screen_id, _device_id)
                    if _pairing_code:
                        log.info(f"Lounge: pairing code (retry) = {_pairing_code}")

            _lounge_token = await _get_lounge_token(_screen_id)
            if not _lounge_token:
                log.error("Lounge: failed to refresh token for session")
                await asyncio.sleep(10)
                continue

            _session_id, _gsession_id, initial_cmds = await _open_session(_device_id, _lounge_token)
            if not _session_id or not _gsession_id:
                log.error("Lounge: failed to open session, retrying in 10s")
                _lounge_token = None
                await asyncio.sleep(10)
                continue

            # Reset ofs counter on new session (like SmartTube's sOfsCounter)
            _ofs_counter = 0

            # Start command queue worker for this session
            _cmd_queue = asyncio.Queue()
            _cmd_worker_task = asyncio.create_task(_cmd_queue_worker())

            log.info(f"Lounge: session opened (SID={_session_id[:8]}...), {len(initial_cmds)} initial commands")

            # Process initial commands from bind response (getNowPlaying, etc.)
            for cmd_type, cmd_params in initial_cmds:
                await _cmd_queue.put((cmd_type, cmd_params))

            await _long_poll()

            # Stop worker when long-poll ends (session expired/reconnect)
            if _cmd_worker_task:
                _cmd_worker_task.cancel()
                try:
                    await _cmd_worker_task
                except asyncio.CancelledError:
                    pass
                _cmd_worker_task = None

        except asyncio.CancelledError:
            log.info("Lounge: listener cancelled")
            break
        except Exception as e:
            log.error(f"Lounge: listener error: {e}")

        if _running:
            await asyncio.sleep(3)


async def _long_poll():
    """Long-polling: line-by-line parsing like SmartTube."""
    global _session_id, _gsession_id, _lounge_token

    params = {
        **_BIND_PARAMS,
        "name": _screen_name,
        "id": _device_id,
        "loungeIdToken": _lounge_token,
        "SID": _session_id,
        "gsessionid": _gsession_id,
        "RID": "rpc",
        "CI": "0",
        "AID": "42",
        "TYPE": "xmlhttp",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=90.0, write=10.0, pool=10.0)) as client:
        try:
            async with client.stream("GET", _BIND_URL, params=params) as resp:
                if resp.status_code == 400:
                    log.warning("Lounge: Unknown SID, resetting session")
                    _session_id = None
                    _gsession_id = None
                    return
                if resp.status_code == 401:
                    log.warning("Lounge: Token expired, refreshing")
                    _lounge_token = None
                    return
                if resp.status_code != 200:
                    log.warning(f"Lounge: bind returned {resp.status_code}")
                    return

                result = ""
                async for line in resp.aiter_lines():
                    if not _running:
                        return

                    stripped = line.strip()
                    if not stripped or stripped.isdigit():
                        continue

                    result += line + "\n"

                    # End of block: "]" on its own line (skip noop)
                    if stripped == "]" and '"noop"]\n]\n' not in result:
                        cmds = _parse_commands(result)
                        if cmds:
                            log.debug(f"Lounge: parsed {len(cmds)} commands")
                        for cmd_type, params_dict in cmds:
                            # Enqueue for sequential processing
                            await _cmd_queue.put((cmd_type, params_dict))
                        result = ""

        except httpx.ReadTimeout:
            log.debug("Lounge: read timeout (normal, reconnecting)")
        except httpx.RemoteProtocolError:
            log.debug("Lounge: connection closed by server")
        except Exception as e:
            log.error(f"Lounge: long_poll error: {e}")


async def _handle_lounge_command(cmd_type: str, params: dict):
    """Process a single command from YouTube — synchronous, sequential.

    Like SmartTube: updateData() then handle + post state, all before
    the next command is dequeued.
    """
    global _ctt, _playlist_id, _playlist_index, _last_report_video, _last_report_playing

    if cmd_type in ("c", "S", "noop", "loungeStatus", "onUserActivity"):
        return

    log.info(f"Lounge command: {cmd_type} {params}")

    if cmd_type == "remoteConnected":
        log.info(f"Lounge: remote connected: {params.get('name', '?')}")
        if _command_callback:
            await _command_callback("remote_connected", {"name": params.get("name", "YouTube App")})
        return

    if cmd_type == "remoteDisconnected":
        log.info("Lounge: remote disconnected")
        if _command_callback:
            await _command_callback("remote_disconnected", {})
        return

    if cmd_type == "getNowPlaying":
        await _report_now_playing()
        duration = _player_state.get("duration", 0)
        if duration > 0:
            is_playing = not _player_state.get("paused", True)
            await _post_state_change(
                _player_state.get("currentTime", 0), duration, is_playing)
        return

    if cmd_type == "getVolume":
        volume = int(_player_state.get("volume", 1) * 100)
        await _report_volume_change(volume)
        return

    if cmd_type == "getSubtitlesTrack":
        # Respond with empty subtitle state (like SmartTube's onSubtitlesTrackChanged)
        fields = _base_command()
        fields["req0__sc"] = "onSubtitlesTrackChanged"
        await _post_command(fields)
        return

    if cmd_type == "getDiscoveryDeviceId":
        # Respond with our device_id (like SmartTube's setDiscoveryDeviceId)
        fields = _base_command()
        fields["req0__sc"] = "setDiscoveryDeviceId"
        fields["req0_discoveryDeviceId"] = _device_id
        fields["req0_loungeDeviceId"] = _device_id
        await _post_command(fields)
        return

    if cmd_type == "setPlaylist":
        # Like SmartTube: extract and store playlist context for echo-back
        video_id = params.get("videoId")
        _ctt = params.get("ctt") or _ctt
        _playlist_id = params.get("listId") or _playlist_id
        _playlist_index = params.get("currentIndex") or _playlist_index
        start_time = float(params.get("currentTime", "0") or "0")
        if video_id:
            is_new_video = video_id != _player_state.get("videoId", "")
            if is_new_video:
                # Genuinely new video: reset duration (unknown yet)
                _player_state["videoId"] = video_id
                _player_state["currentTime"] = start_time
                _player_state["duration"] = 0
                _player_state["paused"] = True
                _last_report_video = video_id
                _last_report_playing = None
            # Send play to TV FIRST (don't delay with HTTP POSTs)
            if is_new_video:
                await _dispatch_play(video_id, start_time, _playlist_id)
            # Then report state to YouTube
            await _report_now_playing()
            duration = _player_state.get("duration", 0)
            if duration > 0:
                is_playing = not _player_state.get("paused", True)
                await _post_state_change(
                    _player_state.get("currentTime", 0), duration, is_playing)
        return

    if cmd_type == "setVideo":
        video_id = params.get("videoId")
        start_time = float(params.get("currentTime", "0") or "0")
        if video_id:
            await _dispatch_play(video_id, start_time)
            _player_state["videoId"] = video_id
        return

    if cmd_type == "updatePlaylist":
        # Update playlist context without changing playback
        _ctt = params.get("ctt") or _ctt
        _playlist_id = params.get("listId") or _playlist_id
        _playlist_index = params.get("currentIndex") or _playlist_index
        return

    if cmd_type in ("play", "resume"):
        if _command_callback:
            await _command_callback("resume", {})
        # Immediate state report like SmartTube
        duration = _player_state.get("duration", 0)
        if duration > 0:
            _last_report_playing = True
            await _post_state_change(
                _player_state.get("currentTime", 0), duration, True)
        return

    if cmd_type == "pause":
        if _command_callback:
            await _command_callback("pause", {})
        duration = _player_state.get("duration", 0)
        if duration > 0:
            _last_report_playing = False
            await _post_state_change(
                _player_state.get("currentTime", 0), duration, False)
        return

    if cmd_type == "seekTo":
        new_time = float(params.get("newTime", "0") or "0")
        if _command_callback:
            await _command_callback("seek", {"time": new_time})
        duration = _player_state.get("duration", 0)
        if duration > 0:
            is_playing = not _player_state.get("paused", True)
            await _post_state_change(new_time, duration, is_playing)
        return

    if cmd_type == "next":
        if _command_callback:
            await _command_callback("queue_next", {})
        return

    if cmd_type == "previous":
        if _command_callback:
            await _command_callback("queue_prev", {})
        return

    if cmd_type == "setVolume":
        volume = params.get("volume")
        delta = params.get("delta")
        if volume is not None and _command_callback:
            await _command_callback("volume", {"level": int(volume) / 100})
            await _report_volume_change(int(volume))
        elif delta is not None and _command_callback:
            await _command_callback("volume_delta", {"delta": int(delta) / 100})
        return

    if cmd_type == "stopVideo":
        # Phone-initiated "stop casting" must NOT touch the player state —
        # the TV should keep playing/paused/whatever as-is. The phone's own
        # UI will reflect that casting ended; the player carries on.
        log.info("Lounge: stopVideo ignored (player state preserved)")
        return

    if cmd_type == "dpadCommand":
        key = params.get("key")
        if key and _command_callback:
            await _command_callback("dpad", {"key": key})
        return

    log.debug(f"Lounge: unhandled command: {cmd_type}")


# ── State reporting (screen → YouTube app) ────────────────────────────────────

async def _post_command(fields: dict):
    """POST state to YouTube — synchronous, awaited fully before returning.

    Like SmartTube's CommandManager.postCommand(): uses static RID=1337
    (not incrementing), with ofs as the serialization counter.
    """
    if not _session_id or not _gsession_id:
        return

    sc = fields.get("req0__sc", "?")
    log.info(f"Lounge POST: {sc} "
             f"vid={fields.get('req0_videoId', '-')} "
             f"state={fields.get('req0_state', '-')} "
             f"time={fields.get('req0_currentTime', '-')} "
             f"dur={fields.get('req0_duration', '-')} "
             f"vol={fields.get('req0_volume', '-')}")

    # SmartTube uses static RID=1337 and AID=42 for all data POSTs
    params = {
        **_BIND_PARAMS,
        "name": _screen_name,
        "id": _device_id,
        "loungeIdToken": _lounge_token,
        "SID": _session_id,
        "gsessionid": _gsession_id,
        "RID": "1337",
        "AID": "42",
    }

    # Filter out None values (like SmartTube's null-filtering HashMap)
    filtered_fields = {k: v for k, v in fields.items() if v is not None}

    try:
        resp = await _get_http_client().post(_BIND_URL, params=params, data=filtered_fields, timeout=10.0)
        body = resp.text
        # Check if YouTube returns updated gsessionid via redirect or header
        new_gsid = resp.headers.get("x-http-session-id")
        if resp.status_code in (301, 302, 307, 308, 400, 410):
            log.warning(f"Lounge POST {sc} ofs={filtered_fields.get('ofs','-')} → {resp.status_code} headers={dict(resp.headers)} body={repr(body[:300])}")
        else:
            log.info(f"Lounge POST {sc} ofs={filtered_fields.get('ofs','-')} → {resp.status_code} | {repr(body[:200])}")
        # Some implementations report gsessionid changes via response body
        if body and '"S"' in body:
            log.warning(f"Lounge POST response may contain gsessionid update: {body[:300]}")
    except httpx.HTTPStatusError as e:
        log.error(f"Lounge: post_command HTTP error: {e}")
    except Exception as e:
        log.error(f"Lounge: post_command failed: {e}")


def _base_command() -> dict:
    """Build base command fields with auto-incrementing ofs (like SmartTube's sOfsCounter)."""
    global _ofs_counter
    fields = {"count": "1", "ofs": str(_ofs_counter)}
    _ofs_counter += 1
    return fields


def _base_command_with_time(position_sec: float, duration_sec: float) -> dict:
    """Like SmartTube's getBaseCommand(positionMs, durationMs) — adds time fields as floats."""
    fields = _base_command()
    if position_sec >= 0:
        fields["req0_currentTime"] = str(float(position_sec))
    if duration_sec > 0:
        fields["req0_duration"] = str(float(duration_sec))
        fields["req0_seekableEndTime"] = str(float(duration_sec))
    fields["req0_loadedTime"] = "0"
    fields["req0_seekableStartTime"] = "0"
    return fields


async def _report_now_playing():
    """Like SmartTube's postStartPlaying — sends nowPlaying with state=IDLE(3)."""
    state = _player_state
    video_id = state.get("videoId", "")
    current_time = state.get("currentTime", 0)
    duration = state.get("duration", 0)

    fields = _base_command_with_time(
        current_time if video_id else -1,
        duration if video_id else -1,
    )
    fields["req0__sc"] = "nowPlaying"
    fields["req0_state"] = str(_STATE_IDLE)
    fields["req0_videoId"] = video_id or None
    fields["req0_ctt"] = _ctt
    fields["req0_listId"] = _playlist_id
    fields["req0_currentIndex"] = _playlist_index

    await _post_command(fields)


async def report_state_change(video_id: str, current_time: float, duration: float, is_playing: bool, volume: float = 1):
    """Called by player state broadcasts — only reports genuinely NEW events.

    The command handlers already report state immediately (synchronously).
    This catches state changes NOT triggered by lounge commands
    (e.g., user pauses on the TV itself, video ends naturally).
    """
    global _last_report_video, _last_report_playing

    # Guard against stale callbacks from old video during video transitions.
    # When setPlaylist sets a new videoId + duration=0, we're "expecting" that
    # video. Reject any callback that doesn't match the expected video.
    expected_video = _player_state.get("videoId", "")
    waiting_for_load = expected_video and _player_state.get("duration", 0) == 0

    if waiting_for_load:
        if video_id != expected_video:
            # Stale callback from old video — ignore completely
            return
        if duration <= 0:
            # Right video but no duration yet — just cache, don't report
            return
        # New video loaded with real duration!
        log.info(f"Lounge: player loaded video {video_id} (dur={duration})")
        _player_state["currentTime"] = current_time
        _player_state["duration"] = duration
        _player_state["paused"] = not is_playing
        _player_state["volume"] = volume
        _last_report_playing = is_playing
        await _report_now_playing()
        await _post_state_change(current_time, duration, is_playing)
        return

    _player_state["videoId"] = video_id
    _player_state["currentTime"] = current_time
    _player_state["duration"] = duration
    _player_state["paused"] = not is_playing
    _player_state["volume"] = volume

    if not _session_id or not _gsession_id:
        return

    video_changed = video_id != _last_report_video
    play_state_changed = is_playing != _last_report_playing

    if video_changed and video_id:
        _last_report_video = video_id
        _last_report_playing = is_playing
        _player_state["_last_reported_time"] = current_time
        log.info(f"Lounge: player reported new video {video_id}")
        await _report_now_playing()
        await _post_state_change(current_time, duration, is_playing)
        return

    if play_state_changed:
        _last_report_playing = is_playing
        _player_state["_last_reported_time"] = current_time
        log.info(f"Lounge: player reported {'play' if is_playing else 'pause'}")
        await _post_state_change(current_time, duration, is_playing)
        return

    # Detect seek: position jumped significantly from cached value
    old_time = _player_state.get("_last_reported_time", current_time)
    if duration > 0 and abs(current_time - old_time) > 5:
        log.info(f"Lounge: player seeked {old_time:.0f} → {current_time:.0f}")
        _player_state["_last_reported_time"] = current_time
        await _post_state_change(current_time, duration, is_playing)
        return

    # Normal position updates — just cache, don't send


async def _post_state_change(current_time: float, duration: float, is_playing: bool):
    """Like SmartTube's postOnStateChange — includes seekable range and loaded time."""
    fields = _base_command_with_time(current_time, duration)
    fields["req0__sc"] = "onStateChange"
    fields["req0_state"] = str(_STATE_PLAYING if is_playing else _STATE_PAUSED)
    fields["req0_cpn"] = "foo"
    await _post_command(fields)


async def _report_volume_change(volume: int):
    """Like SmartTube's postVolumeChange."""
    if not _session_id or not _gsession_id:
        return
    fields = _base_command()
    fields["req0__sc"] = "onVolumeChanged"
    fields["req0_volume"] = str(volume)
    fields["req0_muted"] = "false"
    await _post_command(fields)


# ── Public API ────────────────────────────────────────────────────────────────

def set_command_callback(callback):
    global _command_callback
    _command_callback = callback


async def start():
    global _listener_task
    if _listener_task and not _listener_task.done():
        return
    _listener_task = asyncio.create_task(_listen_loop())
    log.info("Lounge: listener started")


async def stop():
    global _running, _listener_task, _cmd_worker_task
    _running = False
    if _cmd_worker_task:
        _cmd_worker_task.cancel()
        try:
            await _cmd_worker_task
        except asyncio.CancelledError:
            pass
        _cmd_worker_task = None
    if _listener_task:
        _listener_task.cancel()
        try:
            await _listener_task
        except asyncio.CancelledError:
            pass
        _listener_task = None
    log.info("Lounge: listener stopped")


async def reset():
    global _screen_id, _lounge_token, _device_id, _pairing_code
    global _session_id, _gsession_id, _target_tab_uuid, _pending_command
    from routes.remote import set_lounge_target_tab_uuid
    await stop()
    _screen_id = None
    _lounge_token = None
    _device_id = None
    _pairing_code = None
    _session_id = None
    _gsession_id = None
    _target_tab_uuid = None
    _pending_command = None
    _persist()
    _persist_target()
    set_lounge_target_tab_uuid(None)


def get_status() -> dict:
    return {
        "active": _running and _listener_task is not None and not _listener_task.done(),
        "pairing_code": _pairing_code,
        "connected": _session_id is not None,
    }


# ── Target binding ────────────────────────────────────────────────────────────

def _buffer_pending_command(cmd_type: str, params: dict):
    """Store the last play command so we can replay it when a target is claimed
    or reconnects. Overwrites any previous pending command (only the latest
    counts — user's most recent intent wins)."""
    global _pending_command
    _pending_command = (cmd_type, dict(params), time.time())
    log.info(f"Lounge: buffered pending command {cmd_type} (no target yet)")


async def _replay_pending_on_bind():
    """Invoked after the target is (re)claimed or reconnects. If a fresh
    pending command exists, dispatch it to the (now reachable) target.

    Consumes the buffer ONLY on successful delivery so a transient miss
    (e.g. the target's WebSocket flaps between the replay trigger and the
    actual send) doesn't silently lose the user's queued play intent.
    Stale commands past the TTL are always discarded.

    Runs fire-and-forget from remote.py's WS handshake — catch everything
    so a failure here doesn't end up as an unhandled task exception."""
    global _pending_command
    if not _pending_command or not _command_callback:
        return
    cmd_type, params, ts = _pending_command
    if time.time() - ts > _PENDING_COMMAND_TTL:
        log.info(f"Lounge: dropping stale pending command {cmd_type} (>{_PENDING_COMMAND_TTL}s)")
        _pending_command = None
        return
    log.info(f"Lounge: replaying pending {cmd_type} to newly-claimed target")
    try:
        delivered = await _command_callback(cmd_type, params)
    except Exception as e:
        log.warning(f"Lounge: replay of {cmd_type} failed: {e}")
        return
    if delivered:
        _pending_command = None


def get_target_status(tab_uuid: str | None = None) -> dict:
    """Return the lounge pairing + target-binding status for a given client tab.

    `tab_uuid` is the caller's own sessionStorage UUID, used to tell whether
    THIS tab is the current target (so the UI can show the "Casting" indicator
    instead of the "Make this screen the cast target" notification).
    """
    from routes.remote import is_lounge_target_connected
    is_this_tab = bool(tab_uuid and _target_tab_uuid and tab_uuid == _target_tab_uuid)
    orphaned = bool(_target_tab_uuid and not is_lounge_target_connected())
    return {
        "active": _running and _listener_task is not None and not _listener_task.done(),
        "pairing_code": _pairing_code,
        "connected": _session_id is not None,
        "has_target": _target_tab_uuid is not None,
        "is_this_tab": is_this_tab,
        "orphaned": orphaned,
    }


async def claim_target(tab_uuid: str) -> dict:
    """Claim the given tab_uuid as the lounge target. Called from the UI's
    'Make this screen the cast target' button. Replays any fresh buffered
    command so the phone's last play intent takes effect immediately."""
    global _target_tab_uuid
    from routes.remote import set_lounge_target_tab_uuid
    if not tab_uuid:
        return get_target_status(tab_uuid)
    _target_tab_uuid = tab_uuid
    _persist_target()
    set_lounge_target_tab_uuid(tab_uuid)
    log.info(f"Lounge: target tab claimed ({tab_uuid[:8]}...)")
    await _replay_pending_on_bind()
    return get_target_status(tab_uuid)


def unpair_target() -> dict:
    """Clear the lounge target binding. Keeps the YouTube pairing alive —
    a different tab can immediately claim, or the same one can re-claim."""
    global _target_tab_uuid, _pending_command
    from routes.remote import set_lounge_target_tab_uuid
    _target_tab_uuid = None
    _pending_command = None
    _persist_target()
    set_lounge_target_tab_uuid(None)
    log.info("Lounge: target unpaired (pairing with YouTube still active)")
    return get_target_status(None)


# ── REST endpoints ────────────────────────────────────────────────────────────

@router.get("/api/lounge/status")
async def lounge_status(auth: bool = Depends(require_auth)):
    return get_status()


@router.post("/api/lounge/start")
async def lounge_start(auth: bool = Depends(require_auth)):
    await start()
    for _ in range(20):
        if _pairing_code:
            break
        await asyncio.sleep(0.5)
    return get_status()


@router.post("/api/lounge/stop")
async def lounge_stop(auth: bool = Depends(require_auth)):
    await stop()
    return {"ok": True}


@router.post("/api/lounge/reset")
async def lounge_reset(auth: bool = Depends(require_auth)):
    await reset()
    await start()
    for _ in range(20):
        if _pairing_code:
            break
        await asyncio.sleep(0.5)
    return get_status()


@router.get("/api/lounge/target")
async def lounge_target_get(tab_uuid: str = "", auth: bool = Depends(require_auth)):
    """Return lounge status + target binding, scoped to the calling tab.

    The client passes its own sessionStorage `tab_uuid` so the response can
    say "this tab IS the target" or "this tab is NOT the target".
    """
    return get_target_status(tab_uuid or None)


@router.post("/api/lounge/target")
async def lounge_target_claim(body: dict, auth: bool = Depends(require_auth)):
    """Claim the calling tab as the lounge cast target. The request body must
    include a `tab_uuid` (sessionStorage-stable). Replaces any previous target
    (including orphaned ones) and replays fresh buffered commands."""
    raw = (body or {}).get("tab_uuid")
    tab_uuid = raw.strip() if isinstance(raw, str) else ""
    if not tab_uuid or len(tab_uuid) > 128:
        raise HTTPException(status_code=400, detail="missing or invalid tab_uuid")
    return await claim_target(tab_uuid)


@router.delete("/api/lounge/target")
async def lounge_target_unpair(auth: bool = Depends(require_auth)):
    """Clear the target binding without tearing down the YouTube pairing."""
    return unpair_target()
