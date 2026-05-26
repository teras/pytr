# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Auto-pull Ollama models on first boot so the user never has to.

Runs in a background task during the foryou container's lifespan startup. The
foryou app is fully usable while pulls are in flight — the LLM-dependent feeds
(Discover, Surprise, persona) report unavailable until the model lands, every
other feed (Fresh, Quality, Trending, Community) works immediately.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from .. import config

log = logging.getLogger(__name__)

# Max wall-clock to wait for Ollama to come up on the network. Pull progress is
# tracked separately and not bounded — we let big models finish.
_BOOT_WAIT_SEC = 60


async def _wait_for_ollama() -> bool:
    """Poll the Ollama server until it answers, up to _BOOT_WAIT_SEC seconds."""
    deadline = asyncio.get_event_loop().time() + _BOOT_WAIT_SEC
    async with httpx.AsyncClient(timeout=3.0) as client:
        while asyncio.get_event_loop().time() < deadline:
            try:
                r = await client.get(f"{config.OLLAMA_URL}/api/tags")
                if r.status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            await asyncio.sleep(2)
    log.warning("ollama not reachable at %s after %ds", config.OLLAMA_URL, _BOOT_WAIT_SEC)
    return False


async def _installed_models() -> set[str]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{config.OLLAMA_URL}/api/tags")
        r.raise_for_status()
        data = r.json()
    out = set()
    for m in data.get("models") or []:
        name = m.get("name") or ""
        out.add(name)
        # Ollama tags include suffix like ":latest" — also record the bare name.
        if ":" in name:
            out.add(name.split(":")[0])
    return out


async def _pull(model: str):
    """Stream-pull a model via Ollama's /api/pull. Logs progress milestones."""
    log.info("ollama pull %s → starting (this can take several minutes for big models)", model)
    async with httpx.AsyncClient(timeout=None) as client:
        try:
            async with client.stream("POST", f"{config.OLLAMA_URL}/api/pull",
                                     json={"name": model, "stream": True}) as r:
                if r.status_code != 200:
                    body = await r.aread()
                    log.warning("ollama pull %s failed: HTTP %s — %s",
                                model, r.status_code, body[:200])
                    return
                last_logged_pct = -10
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        import json as _json
                        evt = _json.loads(line)
                    except Exception:
                        continue
                    status = evt.get("status", "")
                    total = evt.get("total") or 0
                    done = evt.get("completed") or 0
                    if total > 0:
                        pct = int(done * 100 / total)
                        if pct >= last_logged_pct + 10:
                            log.info("ollama pull %s: %s (%d%%)", model, status, pct)
                            last_logged_pct = pct
                    elif status:
                        log.debug("ollama pull %s: %s", model, status)
                    if evt.get("error"):
                        log.warning("ollama pull %s error: %s", model, evt["error"])
                        return
        except httpx.HTTPError as e:
            log.warning("ollama pull %s connection failed: %s", model, e)
            return
    log.info("ollama pull %s → done", model)


async def ensure_models():
    """Public entry point. Idempotent. Safe to call multiple times.

    Does NOT raise on failure — feeds that need the LLM degrade gracefully if
    the pull fails (network down, model name typo, etc.).
    """
    if not config.OLLAMA_AUTOPULL:
        log.info("ollama autopull disabled (FORYOU_OLLAMA_AUTOPULL=0)")
        return
    if config.LLM_BACKEND != "ollama" and config.EMBED_BACKEND != "ollama":
        return
    if not await _wait_for_ollama():
        return
    try:
        installed = await _installed_models()
    except Exception as e:
        log.warning("could not list installed models: %s", e)
        return
    wanted: list[str] = []
    if config.LLM_BACKEND == "ollama" and config.OLLAMA_MODEL not in installed:
        wanted.append(config.OLLAMA_MODEL)
    if config.EMBED_BACKEND == "ollama" and config.OLLAMA_EMBED_MODEL not in installed:
        wanted.append(config.OLLAMA_EMBED_MODEL)
    if not wanted:
        log.info("ollama models already present: llm=%s embed=%s",
                 config.OLLAMA_MODEL, config.OLLAMA_EMBED_MODEL)
        return
    log.info("ollama pulling %s — feeds that need LLM are unavailable until this finishes", wanted)
    for m in wanted:
        await _pull(m)
