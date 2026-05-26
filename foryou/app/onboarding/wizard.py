# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Onboarding state machine: seed-grid (A) + LLM-adaptive (B) + hybrid (C).

Persisted per-session in onboarding_sessions so the wizard survives reloads.
Final result writes onboarding_seed_interests + persona_text + persona_source
into taste_profiles.
"""
from __future__ import annotations

import json
import logging
import time
import uuid as uuid_lib

from ..db import cursor
from ..llm import get_llm_backend, llm_available
from ..profile_sync import fetch_profile_export, get_taste_profile, mark_onboarding_complete
from ..sources import transcript as transcript_src

log = logging.getLogger(__name__)

# Always-available seed grid for mode A (Fortress-safe, no LLM).
SEED_GRID = [
    {"id": "music_rock", "label": "Rock / Indie", "emoji": "🎸"},
    {"id": "music_electronic", "label": "Electronic / Dance", "emoji": "🎛"},
    {"id": "music_jazz", "label": "Jazz / Soul", "emoji": "🎷"},
    {"id": "music_classical", "label": "Classical / Opera", "emoji": "🎻"},
    {"id": "music_hiphop", "label": "Hip-hop / Rap", "emoji": "🎤"},
    {"id": "music_world", "label": "World / Folk", "emoji": "🪕"},
    {"id": "tech_software", "label": "Software & Dev", "emoji": "💻"},
    {"id": "tech_hardware", "label": "Hardware / DIY", "emoji": "🛠"},
    {"id": "tech_ai", "label": "AI & Research", "emoji": "🤖"},
    {"id": "science_physics", "label": "Physics & Math", "emoji": "🧮"},
    {"id": "science_biology", "label": "Biology & Nature", "emoji": "🧬"},
    {"id": "science_space", "label": "Space & Astronomy", "emoji": "🪐"},
    {"id": "history", "label": "History & Culture", "emoji": "🏛"},
    {"id": "philosophy", "label": "Philosophy", "emoji": "📚"},
    {"id": "talks_lectures", "label": "Talks & Lectures", "emoji": "🎙"},
    {"id": "docs", "label": "Documentaries", "emoji": "🎬"},
    {"id": "comedy", "label": "Comedy & Sketch", "emoji": "😄"},
    {"id": "gaming", "label": "Gaming", "emoji": "🎮"},
    {"id": "cooking", "label": "Cooking", "emoji": "🍳"},
    {"id": "travel", "label": "Travel & Outdoors", "emoji": "🌍"},
    {"id": "cars", "label": "Cars & Engineering", "emoji": "🚗"},
    {"id": "art", "label": "Art & Design", "emoji": "🎨"},
    {"id": "fitness", "label": "Fitness & Sports", "emoji": "🏃"},
    {"id": "finance", "label": "Finance & Economics", "emoji": "📈"},
]

SEED_LABEL = {s["id"]: s["label"] for s in SEED_GRID}


def start_session(profile_uuid: str, mode: str) -> dict:
    session_id = str(uuid_lib.uuid4())
    state = {
        "step": 0,
        "selected_seeds": [],
        "rejected_seeds": [],
        "adaptive_qa": [],
        "next_topics": None,
    }
    with cursor(write=True) as c:
        c.execute(
            "INSERT INTO onboarding_sessions (session_id, profile_uuid, mode, state, started_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, profile_uuid, mode, json.dumps(state), int(time.time())),
        )
    return {
        "session_id": session_id,
        "mode": mode,
        "first_question": _first_question(mode),
    }


def _first_question(mode: str) -> dict:
    if mode in ("A", "C"):
        return {
            "kind": "seed_grid",
            "id": "seed_grid",
            "prompt": "Διάλεξε ό,τι σε ενδιαφέρει (👍 / 👎):",
            "options": SEED_GRID,
        }
    if mode == "B":
        return {
            "kind": "open_text",
            "id": "warmup",
            "prompt": "Πες μου με 2-3 λέξεις: τι σε ξεσηκώνει σε ένα video που το παρακολουθείς ως το τέλος;",
        }
    return _first_question("A")


def _load(session_id: str) -> dict | None:
    with cursor() as c:
        r = c.execute("SELECT * FROM onboarding_sessions WHERE session_id = ?",
                      (session_id,)).fetchone()
    if not r:
        return None
    out = dict(r)
    try:
        out["state"] = json.loads(out["state"])
    except Exception:
        out["state"] = {}
    return out


def _save_state(session_id: str, state: dict):
    with cursor(write=True) as c:
        c.execute("UPDATE onboarding_sessions SET state = ? WHERE session_id = ?",
                  (json.dumps(state), session_id))


async def submit_answer(session_id: str, question_id: str, answer) -> dict:
    sess = _load(session_id)
    if not sess:
        return {"error": "no_session"}
    state = sess["state"]
    mode = sess["mode"]
    profile_uuid = sess["profile_uuid"]

    if question_id == "seed_grid":
        sel = answer.get("selected") or []
        rej = answer.get("rejected") or []
        state["selected_seeds"] = sel
        state["rejected_seeds"] = rej
        if mode == "A":
            return {"done": True, "preview": _seed_preview(sel)}
        # Mode C: ask if user wants to escalate to mode B
        if mode == "C":
            if not await llm_available():
                return {"done": True, "preview": _seed_preview(sel)}
            state["step"] = 1
            _save_state(session_id, state)
            return {
                "next_question": {
                    "kind": "yes_no",
                    "id": "escalate_to_b",
                    "prompt": "Θες να σου κάνω 4-6 επιπλέον στοχευμένες ερωτήσεις για πιο ακριβές προφίλ;",
                },
            }
        # Mode B not reached via seed_grid normally
        _save_state(session_id, state)
        return {"next_question": _first_question(mode)}

    if question_id == "escalate_to_b":
        _save_state(session_id, state)
        if answer is True or (isinstance(answer, dict) and answer.get("yes")):
            return await _next_adaptive_question(session_id, state)
        return {"done": True, "preview": _seed_preview(state["selected_seeds"])}

    if question_id == "warmup" or question_id.startswith("adaptive_"):
        state["adaptive_qa"].append({"q": question_id, "a": answer})
        _save_state(session_id, state)
        if len(state["adaptive_qa"]) >= 8:
            return {"done": True, "preview": _adaptive_preview(state)}
        return await _next_adaptive_question(session_id, state)

    return {"error": "unknown_question"}


def _seed_preview(seeds: list[str]) -> dict:
    labels = [SEED_LABEL.get(s, s) for s in seeds]
    return {"persona_so_far": "Φαίνεται να σε τραβούν: " + ", ".join(labels[:8]) + ".",
            "confidence": min(1.0, len(seeds) / 8.0)}


def _adaptive_preview(state: dict) -> dict:
    seeds = state.get("selected_seeds") or []
    qa = state.get("adaptive_qa") or []
    base = "Φαίνεται να σε τραβούν: " + ", ".join(SEED_LABEL.get(s, s) for s in seeds[:6])
    extra = " — Επιπλέον σήματα: " + " | ".join(f"{x['a']}" for x in qa[:4] if isinstance(x.get("a"), str))
    return {"persona_so_far": base + extra,
            "confidence": min(1.0, (len(seeds) + len(qa)) / 12.0)}


async def _next_adaptive_question(session_id: str, state: dict) -> dict:
    llm = get_llm_backend()
    if not await llm.available():
        return {"done": True, "preview": _adaptive_preview(state)}
    seeds = [SEED_LABEL.get(s, s) for s in state.get("selected_seeds") or []]
    qa = state.get("adaptive_qa") or []
    qa_text = "\n".join(f"Q: {q['q']}  A: {q['a']}" for q in qa) if qa else "(none yet)"
    prompt = (
        "You are helping a self-hosted YouTube client learn the user's taste. "
        "Given their initial interests and prior Q&A, generate ONE next probing question "
        "(in Greek) that would reveal a distinct neighbouring interest. Avoid yes/no. "
        "Output JSON: {\"question\": \"...\"}.\n"
        f"Seeds: {', '.join(seeds) or 'none'}\nPrior Q&A:\n{qa_text}"
    )
    try:
        resp = await llm.generate(prompt, json_mode=True, max_tokens=200)
        data = json.loads(resp.text)
        q = data.get("question") or "Πες μου ένα παράδειγμα video που θα παρακολουθούσες με ησυχία βράδυ."
    except Exception as e:
        log.warning("adaptive Q gen failed: %s", e)
        q = "Πες μου ένα παράδειγμα video που θα παρακολουθούσες με ησυχία βράδυ."
    qid = f"adaptive_{len(qa) + 1}"
    return {"next_question": {"kind": "open_text", "id": qid, "prompt": q}}


async def finalize(session_id: str, persona_override: str | None = None) -> dict:
    sess = _load(session_id)
    if not sess:
        return {"error": "no_session"}
    state = sess["state"]
    profile_uuid = sess["profile_uuid"]
    seeds = state.get("selected_seeds") or []
    persona_text = persona_override
    persona_source = "user" if persona_override else None
    if not persona_text:
        persona_text, persona_source = await _synth_persona(state, profile_uuid)
    mark_onboarding_complete(profile_uuid, seeds, persona_text, persona_source)
    # Record initial persona version.
    if persona_text:
        with cursor(write=True) as c:
            c.execute(
                "INSERT OR REPLACE INTO persona_history (profile_uuid, version, persona_text, source, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (profile_uuid, 1, persona_text, persona_source or "auto", int(time.time())),
            )
    # Mark session done.
    with cursor(write=True) as c:
        c.execute("UPDATE onboarding_sessions SET finalized_at = ? WHERE session_id = ?",
                  (int(time.time()), session_id))
    # Seed the default feeds AND kick off their first generation so the user
    # doesn't land on empty tabs. Each feed generates in the background queue,
    # one at a time, behind the existing serialised job runner.
    from ..feeds.engine import ensure_default_feeds, generate_feed, list_feeds
    from ..jobs import runner as job_runner
    ensure_default_feeds(profile_uuid)
    for fd in list_feeds(profile_uuid):
        feed_id = fd["feed_id"]

        async def _gen(fid=feed_id):  # capture by default arg
            try:
                await generate_feed(fid)
            except Exception as e:
                log.warning("first-run generation failed for %s: %s", fid, e)
        job_runner.trigger_oneshot(_gen)
    return {"ok": True, "persona_text": persona_text, "persona_source": persona_source}


async def _synth_persona(state: dict, profile_uuid: str) -> tuple[str, str | None]:
    seeds = [SEED_LABEL.get(s, s) for s in state.get("selected_seeds") or []]
    qa = state.get("adaptive_qa") or []
    llm = get_llm_backend()
    # Pull whatever transcript snippets we already have for the user's favorites
    # — much richer than just titles. Misses fall back silently to title-only.
    fav_snippets: list[str] = []
    try:
        export = await fetch_profile_export(profile_uuid) or {}
        fav_ids = [f["video_id"] for f in (export.get("favorites") or [])
                   if f.get("video_id")][:10]
        if fav_ids:
            transcripts = transcript_src.cached_transcripts(fav_ids)
            for f in (export.get("favorites") or [])[:10]:
                vid = f.get("video_id")
                tx = transcripts.get(vid, "")
                if tx:
                    fav_snippets.append(f"« {f.get('title', '')} »: {tx[:300]}…")
                elif f.get("title"):
                    fav_snippets.append(f"« {f.get('title')} »")
    except Exception as e:
        log.debug("favorite transcript lookup failed: %s", e)
    if not await llm.available():
        if not seeds and not fav_snippets:
            return "", None
        bits = list(seeds[:8])
        return ("Ο/Η χρήστης φαίνεται να ενδιαφέρεται για " + ", ".join(bits) + ".", "auto")
    prompt = (
        "Write a concise 2-3 paragraph 'taste persona' (in Greek) for a self-hosted "
        "YouTube viewer based on the data below. Avoid generic platitudes. Be specific. "
        "Mention notable patterns or unexpected combinations. End with one sentence about "
        "what they're probably underexplored on.\n"
        f"Seeds: {', '.join(seeds) or 'none'}\n"
        f"Q&A:\n" + "\n".join(f"  {q['q']}: {q['a']}" for q in qa) +
        (("\nFavourite videos (title + transcript excerpt):\n" +
          "\n".join(f"  - {s}" for s in fav_snippets)) if fav_snippets else "")
    )
    try:
        resp = await llm.generate(prompt, max_tokens=600, temperature=0.7)
        return (resp.text.strip(), resp.backend)
    except Exception as e:
        log.warning("persona synth failed: %s", e)
        return ("Ο/Η χρήστης φαίνεται να ενδιαφέρεται για " + ", ".join(seeds[:8]) + ".", "auto")


def status(profile_uuid: str) -> dict:
    with cursor() as c:
        r = c.execute(
            "SELECT onboarding_completed_at, sparsity_state, privacy_mode FROM taste_profiles WHERE profile_uuid = ?",
            (profile_uuid,)).fetchone()
    if not r:
        return {"complete": False, "sparsity_state": "cold", "privacy_mode": "balanced"}
    return {
        "complete": bool(r["onboarding_completed_at"]),
        "sparsity_state": r["sparsity_state"] or "cold",
        "privacy_mode": r["privacy_mode"] or "balanced",
    }
