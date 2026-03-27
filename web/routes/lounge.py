# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""YouTube Lounge API: screen-side client for 'Link with TV code' pairing."""
import asyncio
import json
import logging
import uuid

import httpx
from fastapi import APIRouter, Depends

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

_STATE_PLAYING = 1
_STATE_PAUSED = 2

# ── Singleton lounge state ────────────────────────────────────────────────────
_screen_id: str | None = None
_lounge_token: str | None = None
_device_id: str | None = None
_session_id: str | None = None
_gsession_id: str | None = None
_pairing_code: str | None = None
_ofs_counter = 0
_rid_counter = 1000
_listener_task: asyncio.Task | None = None
_running = False
_screen_name = "PYTR"

_command_callback = None  # async def(command: str, params: dict)
_player_state: dict = {}

# State tracking for event-driven reporting from player callbacks
_last_report_video: str = ""
_last_report_playing: bool | None = None

# Track playlist context from YouTube app
_ctt: str | None = None
_playlist_id: str | None = None
_playlist_index: str | None = None


def _get_http_client():
    from helpers import http_client
    return http_client


def _load_persisted():
    global _screen_id, _device_id
    import profiles_db
    _screen_id = profiles_db.get_setting("lounge_screen_id")
    _device_id = profiles_db.get_setting("lounge_device_id")


def has_persisted_session() -> bool:
    import profiles_db
    return profiles_db.get_setting("lounge_screen_id") is not None


def _persist():
    import profiles_db
    profiles_db.set_setting("lounge_screen_id", _screen_id)
    profiles_db.set_setting("lounge_device_id", _device_id)


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


async def _open_session(device_id: str, lounge_token: str) -> tuple[str | None, str | None]:
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
        if resp.status_code == 200:
            return _parse_session_ids(resp.text)
    except Exception as e:
        log.error(f"Lounge: open_session failed: {e}")
    return None, None


def _parse_session_ids(text: str) -> tuple[str | None, str | None]:
    sid = None
    gsid = None
    try:
        arrays = _parse_response_arrays(text)
        for arr in arrays:
            if len(arr) >= 2 and isinstance(arr[1], list):
                cmd_type = arr[1][0] if arr[1] else None
                if cmd_type == "c" and len(arr[1]) > 1:
                    sid = arr[1][1]
                elif cmd_type == "S" and len(arr[1]) > 1:
                    gsid = arr[1][1]
    except Exception as e:
        log.error(f"Lounge: parse_session_ids failed: {e}")
    return sid, gsid


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


# ── Long-polling listener ─────────────────────────────────────────────────────

async def _listen_loop():
    global _screen_id, _lounge_token, _device_id, _session_id, _gsession_id
    global _pairing_code, _running, _ofs_counter, _rid_counter

    _running = True
    _ofs_counter = 0
    _rid_counter = 1000
    _load_persisted()

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

            _session_id, _gsession_id = await _open_session(_device_id, _lounge_token)
            if not _session_id or not _gsession_id:
                log.error("Lounge: failed to open session, retrying in 10s")
                _lounge_token = None
                await asyncio.sleep(10)
                continue

            log.info(f"Lounge: session opened (SID={_session_id[:8]}...)")
            await _long_poll()

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
                            # Fire-and-forget: don't block parser on state POSTs
                            asyncio.create_task(_handle_lounge_command(cmd_type, params_dict))
                        result = ""

        except httpx.ReadTimeout:
            log.debug("Lounge: read timeout (normal, reconnecting)")
        except httpx.RemoteProtocolError:
            log.debug("Lounge: connection closed by server")
        except Exception as e:
            log.error(f"Lounge: long_poll error: {e}")


async def _handle_lounge_command(cmd_type: str, params: dict):
    """Process a command from YouTube — post state IMMEDIATELY like SmartTube."""
    global _ctt, _playlist_id, _playlist_index, _last_report_video, _last_report_playing

    if cmd_type in ("c", "S", "noop", "loungeStatus"):
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
        # Like SmartTube postStartPlaying: nowPlaying + onStateChange
        await _report_now_playing()
        duration = _player_state.get("duration", 0)
        if duration > 0:
            is_playing = not _player_state.get("paused", True)
            await _post_state_change(
                _player_state.get("currentTime", 0), duration, is_playing)
        return

    if cmd_type == "getVolume":
        # Respond with current volume
        volume = int(_player_state.get("volume", 1) * 100)
        await report_volume_change(volume)
        return

    if cmd_type in ("getSubtitlesTrack",):
        return  # acknowledged but not implemented

    if cmd_type == "setPlaylist":
        video_id = params.get("videoId")
        _ctt = params.get("ctt", _ctt)
        _playlist_id = params.get("listId", _playlist_id)
        _playlist_index = params.get("currentIndex", _playlist_index)
        start_time = float(params.get("currentTime", "0") or "0")
        if video_id:
            if _command_callback:
                await _command_callback("play", {
                    "videoId": video_id,
                    "startTime": start_time,
                    "playlistId": _playlist_id,
                })
            # DON'T report state here — we don't know the duration yet.
            # The player callback (report_state_change) will fire after
            # the video loads with the real duration, like SmartTube's
            # onVideoLoaded → postStartPlaying.
            _player_state["videoId"] = video_id
        return

    if cmd_type == "setVideo":
        video_id = params.get("videoId")
        start_time = float(params.get("currentTime", "0") or "0")
        if video_id:
            if _command_callback:
                await _command_callback("play", {
                    "videoId": video_id,
                    "startTime": start_time,
                })
            _player_state["videoId"] = video_id
        return

    if cmd_type in ("play", "resume"):
        if _command_callback:
            await _command_callback("resume", {})
        # Immediate state report (like SmartTube postPlayState)
        # Only if we have valid cached state (video loaded with duration)
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
        # Immediate state report with new position (like SmartTube postSeek)
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
            await report_volume_change(int(volume))
        elif delta is not None and _command_callback:
            await _command_callback("volume_delta", {"delta": int(delta) / 100})
        return

    if cmd_type == "stopVideo":
        if _command_callback:
            await _command_callback("stop", {})
        return

    if cmd_type == "dpadCommand":
        key = params.get("key")
        if key and _command_callback:
            await _command_callback("dpad", {"key": key})
        return

    log.debug(f"Lounge: unhandled command: {cmd_type}")


# ── State reporting (screen → YouTube app) ────────────────────────────────────

async def _post_command(fields: dict):
    global _rid_counter
    if not _session_id or not _gsession_id:
        return

    sc = fields.get("req0__sc", "?")
    log.info(f"Lounge POST: {sc} "
             f"vid={fields.get('req0_videoId', '-')} "
             f"state={fields.get('req0_state', '-')} "
             f"time={fields.get('req0_currentTime', '-')} "
             f"dur={fields.get('req0_duration', '-')} "
             f"vol={fields.get('req0_volume', '-')}")

    _rid_counter += 1
    params = {
        **_BIND_PARAMS,
        "name": _screen_name,
        "id": _device_id,
        "loungeIdToken": _lounge_token,
        "SID": _session_id,
        "gsessionid": _gsession_id,
        "RID": str(_rid_counter),
        "AID": "42",
    }
    try:
        await _get_http_client().post(_BIND_URL, params=params, data=fields, timeout=10.0)
    except Exception as e:
        log.error(f"Lounge: post_command failed: {e}")


def _base_command() -> dict:
    global _ofs_counter
    fields = {"count": "1", "ofs": str(_ofs_counter)}
    _ofs_counter += 1
    return fields


async def _report_now_playing():
    state = _player_state
    video_id = state.get("videoId", "")
    current_time = state.get("currentTime", 0)
    duration = state.get("duration", 0)

    fields = _base_command()
    fields["req0__sc"] = "nowPlaying"
    fields["req0_videoId"] = video_id
    fields["req0_currentTime"] = str(current_time)
    fields["req0_duration"] = str(duration)
    fields["req0_state"] = str(0)  # idle
    fields["req0_loadedTime"] = "0"
    fields["req0_seekableStartTime"] = "0"
    fields["req0_seekableEndTime"] = str(duration)
    if _ctt:
        fields["req0_ctt"] = _ctt
    if _playlist_id:
        fields["req0_listId"] = _playlist_id
    if _playlist_index:
        fields["req0_currentIndex"] = _playlist_index

    await _post_command(fields)


async def report_state_change(video_id: str, current_time: float, duration: float, is_playing: bool, volume: float = 1):
    """Called by player state broadcasts — only reports genuinely NEW events.

    The command handlers already report state immediately.
    This catches state changes NOT triggered by lounge commands
    (e.g., user pauses on the TV itself).
    """
    global _last_report_video, _last_report_playing

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
        log.info(f"Lounge: player reported new video {video_id}")
        await _report_now_playing()
        await _post_state_change(current_time, duration, is_playing)
        return

    if play_state_changed:
        _last_report_playing = is_playing
        log.info(f"Lounge: player reported {'play' if is_playing else 'pause'}")
        await _post_state_change(current_time, duration, is_playing)
        return

    # Position updates — just cache, don't send


async def _post_state_change(current_time: float, duration: float, is_playing: bool):
    fields = _base_command()
    fields["req0__sc"] = "onStateChange"
    fields["req0_state"] = str(_STATE_PLAYING if is_playing else _STATE_PAUSED)
    fields["req0_currentTime"] = str(current_time)
    fields["req0_duration"] = str(duration)
    fields["req0_cpn"] = "foo"
    await _post_command(fields)


async def report_volume_change(volume: int):
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
    global _running, _listener_task
    _running = False
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
    global _session_id, _gsession_id
    await stop()
    _screen_id = None
    _lounge_token = None
    _device_id = None
    _pairing_code = None
    _session_id = None
    _gsession_id = None
    _persist()


def get_status() -> dict:
    return {
        "active": _running and _listener_task is not None and not _listener_task.done(),
        "pairing_code": _pairing_code,
        "connected": _session_id is not None,
    }


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
