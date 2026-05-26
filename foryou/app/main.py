# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""For You sidecar — FastAPI entry point.

Listens on the internal Docker network; PYTR's reverse-proxy is the only way
in. No port is exposed in docker-compose.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import config, profile_sync
from .db import init_db, cursor
from .egress import close_client
from .feeds import enhance as enhance_mod
from .feeds import engine as engine_mod
from .feeds import spec as spec_mod
from .jobs import runner as job_runner
from .jobs import scheduled as job_scheduled
from .llm import get_embedding_backend, get_llm_backend, llm_available
from .llm.ollama_setup import ensure_models as ollama_ensure_models
from .onboarding import wizard

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s [foryou] %(name)s: %(message)s")
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app):
    init_db()
    job_scheduled.register_all()
    job_runner.start()
    # Pre-fill the global candidate pool with universal content so the very first
    # feed generation after onboarding has something to rank instead of nothing.
    job_runner.trigger_oneshot(job_scheduled.bootstrap_candidates)
    # Auto-pull Ollama models in the background so first boot just works.
    # Doesn't block readiness — feeds that don't need LLM serve immediately.
    autopull_task = asyncio.create_task(ollama_ensure_models())
    log.info("foryou ready — llm_backend=%s embed_backend=%s (ollama autopull + bootstrap in background)",
             config.LLM_BACKEND, config.EMBED_BACKEND)
    yield
    autopull_task.cancel()
    await job_runner.stop()
    await close_client()


app = FastAPI(title="PYTR For You", lifespan=lifespan)


# ── Health ──────────────────────────────────────────────────────────────────

@app.get("/health")
@app.get("/api/foryou/health")
async def health():
    llm = get_llm_backend()
    emb = get_embedding_backend()
    return {
        "status": "ok",
        "llm_backend": llm.name,
        "llm_available": await llm.available(),
        "llm_model": llm.model if hasattr(llm, "model") else None,
        "embed_backend": emb.name,
        "embed_available": await emb.available(),
        "privacy_mode_default": config.PRIVACY_MODE_DEFAULT,
        "enhanced_surfaces": list(enhance_mod.SURFACE_CONFIG.keys()),
    }


# ── Onboarding ──────────────────────────────────────────────────────────────

class OnboardingStartReq(BaseModel):
    mode: str = "C"


class OnboardingAnswerReq(BaseModel):
    session_id: str
    question_id: str
    answer: dict | str | bool | list


class OnboardingFinalizeReq(BaseModel):
    session_id: str
    persona_text_override: str | None = None


@app.get("/api/foryou/onboarding/status")
async def onboarding_status(x_pytr_profile_uuid: str | None = Header(default=None)):
    if not x_pytr_profile_uuid:
        return {"complete": False, "sparsity_state": "cold", "privacy_mode": "balanced"}
    profile_sync.ensure_taste_profile(x_pytr_profile_uuid)
    return wizard.status(x_pytr_profile_uuid)


@app.get("/api/foryou/onboarding/ui")
async def onboarding_ui():
    p = Path(__file__).parent.parent / "static" / "onboarding.html"
    return FileResponse(str(p), media_type="text/html")


@app.post("/api/foryou/onboarding/start")
async def onboarding_start(req: OnboardingStartReq,
                           x_pytr_profile_uuid: str | None = Header(default=None)):
    if not x_pytr_profile_uuid:
        raise HTTPException(status_code=400, detail="Missing X-PYTR-Profile-UUID")
    profile_sync.ensure_taste_profile(x_pytr_profile_uuid)
    # If mode B requested but no LLM, downgrade to A.
    mode = req.mode.upper()
    if mode == "B" and not await llm_available():
        mode = "A"
    return wizard.start_session(x_pytr_profile_uuid, mode)


@app.post("/api/foryou/onboarding/answer")
async def onboarding_answer(req: OnboardingAnswerReq):
    return await wizard.submit_answer(req.session_id, req.question_id, req.answer)


@app.post("/api/foryou/onboarding/finalize")
async def onboarding_finalize(req: OnboardingFinalizeReq):
    return await wizard.finalize(req.session_id, req.persona_text_override)


# ── Feeds ───────────────────────────────────────────────────────────────────

@app.get("/api/foryou/feeds")
async def get_feeds(x_pytr_profile_uuid: str | None = Header(default=None)):
    if not x_pytr_profile_uuid:
        raise HTTPException(status_code=400, detail="Missing X-PYTR-Profile-UUID")
    profile_sync.ensure_taste_profile(x_pytr_profile_uuid)
    return {"feeds": engine_mod.list_feeds(x_pytr_profile_uuid),
            "kinds": spec_mod.ALL_KINDS, "labels": spec_mod.labels()}


class CreateFeedReq(BaseModel):
    kind: str
    label: str | None = None
    pinned_order: int | None = None


@app.post("/api/foryou/feeds")
async def create_feed_route(req: CreateFeedReq, x_pytr_profile_uuid: str | None = Header(default=None)):
    if not x_pytr_profile_uuid:
        raise HTTPException(status_code=400, detail="Missing X-PYTR-Profile-UUID")
    if req.kind not in spec_mod.ALL_KINDS:
        raise HTTPException(status_code=400, detail="Unknown feed kind")
    feed_id = engine_mod.create_feed(x_pytr_profile_uuid, req.kind, label=req.label,
                                     pinned_order=req.pinned_order)
    return {"feed_id": feed_id}


@app.delete("/api/foryou/feeds/{feed_id}")
async def delete_feed_route(feed_id: str, x_pytr_profile_uuid: str | None = Header(default=None)):
    if not x_pytr_profile_uuid:
        raise HTTPException(status_code=400, detail="Missing X-PYTR-Profile-UUID")
    engine_mod.delete_feed(x_pytr_profile_uuid, feed_id)
    return {"ok": True}


@app.post("/api/foryou/feeds/{feed_id}/refresh")
async def refresh_feed(feed_id: str):
    # Queue a one-shot regeneration so we don't block the request.
    async def _do():
        await engine_mod.generate_feed(feed_id)
    job_runner.trigger_oneshot(_do)
    return {"queued": True}


class UnavailableReq(BaseModel):
    video_id: str
    reason: str = ""


@app.post("/api/foryou/report-unavailable")
async def report_unavailable(req: UnavailableReq, x_pytr_profile_uuid: str | None = Header(default=None)):
    """Mark a video as dead so we stop surfacing it.

    Triggered by:
      * the PYTR frontend when /api/info returns 500/404 for a card the user clicked
      * the PYTR backend itself when yt-dlp fails to fetch a video at all

    Effect: removes it from candidates + feed_items, records a permanent
    global tombstone (so upsert() refuses to re-add it from any future source
    pull), and writes a per-profile never_again feedback row.
    """
    if not req.video_id:
        raise HTTPException(status_code=400, detail="missing video_id")
    from . import candidates as cand_mod
    with cursor(write=True) as c:
        n_c = c.execute("DELETE FROM candidates WHERE video_id = ?", (req.video_id,)).rowcount
        n_f = c.execute("DELETE FROM feed_items WHERE video_id = ?", (req.video_id,)).rowcount
        if x_pytr_profile_uuid:
            c.execute(
                "INSERT OR REPLACE INTO feedback (profile_uuid, video_id, source_of_rec, signal, ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (x_pytr_profile_uuid, req.video_id, "unavailable", "never_again", int(time.time())),
            )
    cand_mod.tombstone(req.video_id, reason=req.reason)
    log.info("reported unavailable: %s (cands=%d feed_items=%d reason=%s)",
             req.video_id, n_c, n_f, req.reason[:80])
    return {"ok": True, "deleted": {"candidates": n_c, "feed_items": n_f}}


@app.post("/api/foryou/feeds/refresh-all")
async def refresh_all_feeds(x_pytr_profile_uuid: str | None = Header(default=None)):
    if not x_pytr_profile_uuid:
        raise HTTPException(status_code=400, detail="Missing X-PYTR-Profile-UUID")
    feeds = engine_mod.list_feeds(x_pytr_profile_uuid)
    for f in feeds:
        feed_id = f["feed_id"]
        async def _do(fid=feed_id):
            await engine_mod.generate_feed(fid)
        job_runner.trigger_oneshot(_do)
    return {"queued": len(feeds)}


@app.get("/api/foryou/feeds/{feed_id}/items")
async def get_feed_items_route(feed_id: str, limit: int = 30, offset: int = 0):
    """Serve a page of items, lazy-expanding when the user nears the tail.

    Expansion is triggered only when the remaining tail is small AND no
    expansion is already in flight AND the feed isn't in its exhaustion
    cooldown. That keeps us from making wasted upstream calls.
    """
    data = engine_mod.get_feed_items(feed_id, limit=limit, offset=offset)
    remaining = data["total"] - (offset + len(data["videos"]))
    expanding = engine_mod.is_expanding(feed_id)
    exhausted = engine_mod.is_exhausted(feed_id)
    threshold = max(10, limit // 2)
    if remaining < threshold and not expanding and not exhausted:
        async def _do():
            await engine_mod.expand_feed(feed_id)
        job_runner.trigger_oneshot(_do)
        expanding = True
    data["expanding"] = expanding
    data["exhausted"] = exhausted
    # If no more local items but the expansion is running, hint the client to
    # poll — that's how the bottomless illusion stays seamless.
    if not data["has_more"] and expanding:
        data["has_more"] = True  # encourage the frontend to keep polling
        data["pending_expansion"] = True
    return data


# ── Enhance-list ────────────────────────────────────────────────────────────

class EnhanceReq(BaseModel):
    surface: str
    context: dict = {}
    baseline: list[dict] = []


@app.post("/api/foryou/enhance-list")
async def enhance_list(req: EnhanceReq, x_pytr_profile_uuid: str | None = Header(default=None)):
    if not x_pytr_profile_uuid:
        return {"videos": req.baseline, "decorations": {}, "removed": []}
    return await enhance_mod.enhance(x_pytr_profile_uuid, req.surface, req.context, req.baseline)


# ── Feedback / impressions / metrics ────────────────────────────────────────

class FeedbackReq(BaseModel):
    video_id: str
    source_of_rec: str = ""
    signal: str  # thumbs_up | thumbs_down | never_again | less_of_channel


@app.post("/api/foryou/feedback")
async def feedback_route(req: FeedbackReq, x_pytr_profile_uuid: str | None = Header(default=None)):
    if not x_pytr_profile_uuid:
        raise HTTPException(status_code=400, detail="Missing X-PYTR-Profile-UUID")
    if req.signal not in ("thumbs_up", "thumbs_down", "never_again", "less_of_channel"):
        raise HTTPException(status_code=400, detail="Bad signal")
    with cursor(write=True) as c:
        c.execute(
            "INSERT OR REPLACE INTO feedback (profile_uuid, video_id, source_of_rec, signal, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (x_pytr_profile_uuid, req.video_id, req.source_of_rec, req.signal, int(time.time())),
        )
    return {"ok": True}


class ImpressionReq(BaseModel):
    video_id: str
    feed_kind: str = ""
    clicked: bool = False
    watched_30s: bool = False


@app.post("/api/foryou/impression")
async def impression_route(req: ImpressionReq, x_pytr_profile_uuid: str | None = Header(default=None)):
    if not x_pytr_profile_uuid:
        return {"ok": True}
    import datetime
    day = int(datetime.datetime.now().strftime("%Y%m%d"))
    with cursor(write=True) as c:
        c.execute(
            "INSERT INTO impressions_daily (profile_uuid, video_id, day, feed_kind, "
            " shown_count, clicked_count, watched_30s_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(profile_uuid, video_id, day) DO UPDATE SET "
            " shown_count = shown_count + 1, "
            " clicked_count = clicked_count + ?, "
            " watched_30s_count = watched_30s_count + ?",
            (x_pytr_profile_uuid, req.video_id, day, req.feed_kind,
             1, 1 if req.clicked else 0, 1 if req.watched_30s else 0,
             1 if req.clicked else 0, 1 if req.watched_30s else 0),
        )
    return {"ok": True}


@app.get("/api/foryou/metrics")
async def metrics_route(x_pytr_profile_uuid: str | None = Header(default=None)):
    if not x_pytr_profile_uuid:
        raise HTTPException(status_code=400, detail="Missing X-PYTR-Profile-UUID")
    import datetime
    now = datetime.datetime.now()
    d30 = int((now - datetime.timedelta(days=30)).strftime("%Y%m%d"))
    d7 = int((now - datetime.timedelta(days=7)).strftime("%Y%m%d"))
    with cursor() as c:
        r30 = c.execute(
            "SELECT COALESCE(SUM(shown_count),0) shown, COALESCE(SUM(clicked_count),0) clicked, "
            "COALESCE(SUM(watched_30s_count),0) watched FROM impressions_daily "
            "WHERE profile_uuid=? AND day >= ?",
            (x_pytr_profile_uuid, d30)).fetchone()
        r7 = c.execute(
            "SELECT COALESCE(SUM(shown_count),0) shown, COALESCE(SUM(clicked_count),0) clicked, "
            "COALESCE(SUM(watched_30s_count),0) watched FROM impressions_daily "
            "WHERE profile_uuid=? AND day >= ?",
            (x_pytr_profile_uuid, d7)).fetchone()
    def _rate(d):
        sh = d["shown"] or 0
        return round((d["watched"] / sh) * 100, 1) if sh else None
    return {
        "hit_rate_30d_pct": _rate(r30),
        "hit_rate_7d_pct": _rate(r7),
        "shown_30d": r30["shown"],
        "clicked_30d": r30["clicked"],
        "watched_30s_30d": r30["watched"],
    }


# ── Settings ────────────────────────────────────────────────────────────────

class SettingsReq(BaseModel):
    privacy_mode: str | None = None


@app.get("/api/foryou/settings")
async def settings_route(x_pytr_profile_uuid: str | None = Header(default=None)):
    if not x_pytr_profile_uuid:
        raise HTTPException(status_code=400, detail="Missing X-PYTR-Profile-UUID")
    profile_sync.ensure_taste_profile(x_pytr_profile_uuid)
    tp = profile_sync.get_taste_profile(x_pytr_profile_uuid) or {}
    return {
        "privacy_mode": tp.get("privacy_mode") or "balanced",
        "sparsity_state": tp.get("sparsity_state") or "cold",
        "signal_richness": tp.get("signal_richness") or 0.0,
        "persona_text": tp.get("persona_text") or "",
        "llm_backend": get_llm_backend().name,
        "llm_available": await get_llm_backend().available(),
    }


@app.put("/api/foryou/settings")
async def update_settings_route(req: SettingsReq, x_pytr_profile_uuid: str | None = Header(default=None)):
    if not x_pytr_profile_uuid:
        raise HTTPException(status_code=400, detail="Missing X-PYTR-Profile-UUID")
    if req.privacy_mode:
        if req.privacy_mode not in ("fortress", "balanced", "cloud"):
            raise HTTPException(status_code=400, detail="Bad privacy mode")
        profile_sync.set_privacy_mode(x_pytr_profile_uuid, req.privacy_mode)
    return {"ok": True}


# ── Persona editing ─────────────────────────────────────────────────────────

class PersonaReq(BaseModel):
    persona_text: str


@app.put("/api/foryou/persona")
async def persona_route(req: PersonaReq, x_pytr_profile_uuid: str | None = Header(default=None)):
    if not x_pytr_profile_uuid:
        raise HTTPException(status_code=400, detail="Missing X-PYTR-Profile-UUID")
    with cursor(write=True) as c:
        c.execute(
            "UPDATE taste_profiles SET persona_text=?, persona_edited_by_user=1, persona_source='user' "
            "WHERE profile_uuid=?",
            (req.persona_text, x_pytr_profile_uuid),
        )
        v = c.execute(
            "SELECT COALESCE(MAX(version),0)+1 FROM persona_history WHERE profile_uuid=?",
            (x_pytr_profile_uuid,)).fetchone()[0]
        c.execute(
            "INSERT INTO persona_history (profile_uuid, version, persona_text, source, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (x_pytr_profile_uuid, v, req.persona_text, "user", int(time.time())),
        )
    return {"ok": True}
