# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""TV setup: deploy PYTR app to LG webOS or Android TV."""
import asyncio
import logging
import shutil
import socket
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from auth import require_auth, verify_session, get_profile_id
import profiles_db

router = APIRouter(prefix="/api/tv-setup")
page_router = APIRouter()
log = logging.getLogger(__name__)

# Pre-built package paths (inside Docker image at /app/clients/...)
_WEBOS_IPK = Path(__file__).parent.parent / "clients" / "webos" / "pytr-tv.ipk"
_ANDROID_APK = Path(__file__).parent.parent / "clients" / "android" / "pytr-tv.apk"

# Deploy progress tracking
# session_key -> {steps: [], done: bool, error: str|None, token: str|None, cancelled: bool}
_deploy_status: dict = {}


class DeployCancelled(Exception):
    pass


class DeployReq(BaseModel):
    type: str = Field(..., pattern=r'^(webos|android)$')
    ip: str = Field(..., min_length=1, max_length=45)
    passphrase: str = ""


def _require_admin(request: Request):
    pid = get_profile_id(request)
    if pid is None:
        raise HTTPException(status_code=403, detail="No profile selected")
    profile = profiles_db.get_profile(pid)
    if not profile or not profile["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin required")


def _status_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/deploy")
async def deploy(req: DeployReq, request: Request, auth: bool = Depends(require_auth)):
    _require_admin(request)
    key = _status_key(request)
    _deploy_status[key] = {"steps": [], "done": False, "error": None, "token": None, "cancelled": False}

    def add_step(msg: str):
        if _deploy_status[key].get("cancelled"):
            raise DeployCancelled()
        _deploy_status[key]["steps"].append({"msg": msg, "time": time.time()})
        log.info(f"TV deploy [{req.type}] {msg}")

    def is_cancelled() -> bool:
        return _deploy_status[key].get("cancelled", False)

    try:
        if req.type == "webos":
            result = await _deploy_webos(req.ip, req.passphrase, add_step, is_cancelled)
        else:
            result = await _deploy_android(req.ip, add_step, is_cancelled)

        _deploy_status[key]["done"] = True
        _deploy_status[key]["token"] = result.get("token")
        return result
    except DeployCancelled:
        _deploy_status[key]["done"] = True
        _deploy_status[key]["error"] = "Cancelled"
        raise HTTPException(status_code=499, detail="Deploy cancelled")
    except Exception as e:
        _deploy_status[key]["done"] = True
        _deploy_status[key]["error"] = str(e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cancel")
async def cancel_deploy(request: Request, auth: bool = Depends(require_auth)):
    _require_admin(request)
    key = _status_key(request)
    if key in _deploy_status and not _deploy_status[key].get("done"):
        _deploy_status[key]["cancelled"] = True
    return {"ok": True}


@router.get("/has-key")
async def has_key(ip: str, request: Request, auth: bool = Depends(require_auth)):
    """Check if we have a stored SSH key for this IP."""
    _require_admin(request)
    return {"has_key": _load_stored_key(ip) is not None}


@router.get("/status")
async def status(request: Request, auth: bool = Depends(require_auth)):
    _require_admin(request)
    key = _status_key(request)
    return _deploy_status.get(key, {"steps": [], "done": True, "error": None, "token": None})


def _get_tokens() -> list:
    """Load webos_dev_tokens from DB."""
    import json
    raw = profiles_db.get_setting("webos_dev_tokens")
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []


def _save_tokens(tokens: list):
    """Save webos_dev_tokens to DB."""
    import json
    profiles_db.set_setting("webos_dev_tokens", json.dumps(tokens) if tokens else None)


def _load_stored_key(ip: str):
    """Load a stored SSH key for this IP from webos_dev_tokens."""
    import io
    import paramiko
    for entry in _get_tokens():
        if entry.get("name") == ip and entry.get("ssh_key"):
            try:
                return paramiko.RSAKey.from_private_key(io.StringIO(entry["ssh_key"]))
            except Exception:
                return None
    return None


def _load_stored_passphrase(ip: str) -> str | None:
    """Load a stored passphrase for this IP."""
    for entry in _get_tokens():
        if entry.get("name") == ip:
            return entry.get("passphrase")
    return None


def _store_key(ip: str, pkey, token: str | None = None, passphrase: str | None = None):
    """Store SSH key (and optionally token/passphrase) in webos_dev_tokens, keyed by IP."""
    import io
    buf = io.StringIO()
    pkey.write_private_key(buf)
    pem = buf.getvalue()

    tokens = _get_tokens()
    # Find existing entry for this IP
    for entry in tokens:
        if entry.get("name") == ip:
            entry["ssh_key"] = pem
            if token:
                entry["token"] = token
            if passphrase:
                entry["passphrase"] = passphrase
            _save_tokens(tokens)
            return

    # Create new entry
    entry = {"name": ip, "ssh_key": pem}
    if token:
        entry["token"] = token
    if passphrase:
        entry["passphrase"] = passphrase
    tokens.append(entry)
    _save_tokens(tokens)


async def _deploy_webos(ip: str, passphrase: str, add_step, is_cancelled) -> dict:
    import io
    import json
    import paramiko
    from helpers import http_client

    if not _WEBOS_IPK.exists():
        raise HTTPException(status_code=500, detail="webOS IPK package not found on server")

    # Use provided passphrase, or fall back to stored one
    effective_passphrase = passphrase or _load_stored_passphrase(ip)

    async def _fetch_new_key():
        if not effective_passphrase:
            raise HTTPException(status_code=400,
                                detail="Passphrase is required for first-time setup. Enable Key Server on the TV and enter the passphrase.")

        add_step("Downloading SSH key from TV...")
        try:
            key_resp = await http_client.get(f"http://{ip}:9991/webos_rsa", timeout=10)
            key_resp.raise_for_status()
        except Exception:
            raise HTTPException(status_code=400, detail="Cannot reach Key Server. Make sure Key Server is ON in the Developer Mode app.")

        try:
            return paramiko.RSAKey.from_private_key(io.StringIO(key_resp.text), password=effective_passphrase)
        except paramiko.ssh_exception.PasswordRequiredException:
            raise HTTPException(status_code=400, detail="Invalid passphrase for SSH key")
        except Exception:
            raise HTTPException(status_code=400, detail="Failed to decrypt SSH key — check your passphrase")

    # Try stored key first
    pkey = _load_stored_key(ip)
    stored_key = pkey is not None
    if stored_key:
        add_step("Using stored SSH key...")
    else:
        pkey = await _fetch_new_key()
        add_step("SSH key saved for future deploys.")

    add_step("Connecting to TV via SSH...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    def _ssh_connect(key):
        ssh.connect(ip, port=9922, username="prisoner", pkey=key, timeout=10,
                    disabled_algorithms={"pubkeys": ["rsa-sha2-256", "rsa-sha2-512"]})

    try:
        await asyncio.get_event_loop().run_in_executor(None, _ssh_connect, pkey)
    except Exception:
        if not stored_key:
            raise
        # Stored key failed — try fetching a fresh key
        add_step("Stored key rejected, fetching new key...")
        ssh.close()
        pkey = await _fetch_new_key()
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        await asyncio.get_event_loop().run_in_executor(None, _ssh_connect, pkey)

    try:
        sftp = ssh.open_sftp()

        add_step("Uploading app package...")
        remote_dir = "/media/developer/temp"
        remote_path = f"{remote_dir}/pytr-tv.ipk"

        def _upload():
            try:
                sftp.mkdir(remote_dir)
            except IOError:
                pass  # already exists
            sftp.put(str(_WEBOS_IPK), remote_path)

        await asyncio.get_event_loop().run_in_executor(None, _upload)

        add_step("Installing app...")
        install_params = json.dumps({"id": "com.pytr.tv", "ipkUrl": remote_path, "subscribe": True})
        install_cmd = f"/usr/bin/luna-send-pub -i luna://com.webos.appInstallService/dev/install '{install_params}'"

        def _run_install():
            channel = ssh.get_transport().open_session()
            channel.exec_command(install_cmd)
            channel.settimeout(30)
            buf = ""
            result = (None, None)
            try:
                while True:
                    chunk = channel.recv(4096).decode(errors="replace")
                    if not chunk:
                        break
                    buf += chunk
                    lines = buf.split('\n')
                    buf = lines[-1]
                    for line in lines[:-1]:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            msg = json.loads(line)
                        except ValueError:
                            continue
                        log.info(f"Install: {msg.get('details', {}).get('state', msg)}")
                        if msg.get("returnValue") is False:
                            result = (False, msg.get("errorText", "Installation failed"))
                        else:
                            state = str(msg.get("details", {}).get("state", "")).upper()
                            if state in ("INSTALLED", "SUCCESS"):
                                result = (True, None)
                            elif "FAILED" in state:
                                reason = msg.get("details", {}).get("reason", state)
                                result = (False, f"Installation failed: {reason}")
                        if result[0] is not None:
                            break
                    if result[0] is not None:
                        break
            except socket.timeout:
                pass
            finally:
                try:
                    channel.shutdown(1)
                except Exception:
                    pass
                channel.close()
            if result[0] is None:
                return False, "No response from install service"
            return result

        installed, err = await asyncio.get_event_loop().run_in_executor(None, _run_install)
        if not installed:
            raise Exception(err)

        add_step("Launching app...")
        ssh.exec_command("/usr/bin/luna-send-pub -n 1 'luna://com.webos.service.applicationManager/launch' "
                         "'{\"id\":\"com.pytr.tv\"}'")
        await asyncio.sleep(1)

        add_step("Reading dev mode token...")
        token = None
        stdin, stdout, stderr = ssh.exec_command("cat /var/luna/preferences/devmode_enabled")
        token_output = stdout.read().decode().strip()
        if token_output:
            token = token_output

        # Cleanup temp file
        try:
            sftp.remove(remote_path)
        except Exception:
            pass
        sftp.close()

        # Store SSH key (and token/passphrase if available) for this IP
        _store_key(ip, pkey, token, effective_passphrase)

        add_step("Done! App deployed successfully.")
        return {"ok": True, "token": token}
    finally:
        ssh.close()


async def _deploy_android(ip: str, add_step, is_cancelled) -> dict:
    adb_path = shutil.which("adb")
    if not adb_path:
        raise HTTPException(status_code=500, detail="adb not found on server")

    if not _ANDROID_APK.exists():
        raise HTTPException(status_code=500, detail="Android APK package not found on server")

    target = f"{ip}:5555"

    async def _adb(*args, timeout=15):
        proc = await asyncio.create_subprocess_exec(
            adb_path, *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (stdout.decode() + stderr.decode()).strip()

    async def _is_authorized():
        out = await _adb("devices")
        for line in out.splitlines():
            if target in line:
                return "device" in line and "unauthorized" not in line
        return False

    try:
        # Clean slate: disconnect any stale connection
        await _adb("disconnect", target)

        add_step("Connecting to TV via ADB...")
        output = await _adb("connect", target)
        out_lower = output.lower()
        connected = "connected" in out_lower or "already" in out_lower
        needs_auth = "authenticate" in out_lower or "unauthorized" in out_lower
        if not connected and not needs_auth:
            if "refused" in out_lower or "no route" in out_lower or "timed out" in out_lower:
                raise Exception(f"Cannot reach TV at {ip}. Make sure ADB debugging is enabled.")
            raise Exception(f"Failed to connect: {output}")

        # Check if actually authorized
        if needs_auth or not await _is_authorized():
            add_step("Waiting for authorization — approve the connection on your TV...")
            authorized = False
            for _ in range(12):  # ~60 seconds total
                if is_cancelled():
                    raise DeployCancelled()
                await asyncio.sleep(5)
                await _adb("connect", target)
                if await _is_authorized():
                    authorized = True
                    break
            if not authorized:
                raise Exception("TV did not authorize the connection. Approve on the TV and try again.")

        add_step("Installing app...")
        output = await _adb("-s", target, "install", "-r", "--no-streaming", str(_ANDROID_APK), timeout=60)
        if "success" not in output.lower():
            raise Exception(f"Install failed: {output}")

        add_step("Launching app...")
        await _adb("-s", target, "shell", "am", "start", "-n", "com.pytr.tv/.SetupActivity")

        add_step("Done! App deployed successfully.")
        return {"ok": True}
    finally:
        await _adb("disconnect", target)


# ── Page route ───────────────────────────────────────────────────────────────

@page_router.get("/setup-tv")
async def setup_tv_page(request: Request):
    from urllib.parse import quote
    from fastapi.responses import RedirectResponse
    pw = profiles_db.get_app_password()
    if pw and not verify_session(request):
        return RedirectResponse(url=f"/login?next={quote('/setup-tv', safe='')}", status_code=302)
    # Admin check
    pid = get_profile_id(request)
    if pid is None:
        return RedirectResponse(url="/", status_code=302)
    profile = profiles_db.get_profile(pid)
    if not profile or not profile["is_admin"]:
        return RedirectResponse(url="/", status_code=302)
    return FileResponse("static/tv-setup.html")
