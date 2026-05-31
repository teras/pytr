# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Single feed-generation engine driven by the spec.

Per the contract: never blocks user-facing requests. Always run from the
background job runner. Writes feed_items snapshots that the API serves.
"""
from __future__ import annotations

import json
import logging
import random
import time
import uuid as uuid_lib

from .. import candidates
from ..db import cursor
from ..llm import get_embedding_backend, get_llm_backend
from ..profile_sync import fetch_profile_export, get_taste_profile
from ..ranking import centroid, cosine, exploration_floor, mmr, stratified_sample
from ..sources.rss import ChannelRSSSource
from ..sources.tournesol import TournesolSource
from ..sources.ytdlp import YtDlpSearchSource, YtDlpRelatedSource, YtDlpChartsSource
from ..sources.reddit_hn import RedditSource, HNAlgoliaSource
from . import spec

log = logging.getLogger(__name__)


def list_feeds(profile_uuid: str) -> list[dict]:
    with cursor() as c:
        rows = c.execute(
            "SELECT feed_id, kind, label, pinned_order, config FROM feed_defs "
            "WHERE profile_uuid = ? ORDER BY pinned_order ASC NULLS LAST, kind",
            (profile_uuid,),
        ).fetchall()
    return [dict(r) for r in rows]


def ensure_default_feeds(profile_uuid: str):
    """Seed a sensible set of feeds for a new profile."""
    existing = {r["kind"] for r in list_feeds(profile_uuid)}
    presets = [
        (spec.KIND_FRESH, 1),
        (spec.KIND_QUALITY, 2),
        (spec.KIND_DISCOVER, 3),
        (spec.KIND_TRENDING, 4),
        (spec.KIND_COMMUNITY, 5),
        (spec.KIND_CLOSER, 6),
        (spec.KIND_SURPRISE, 7),
    ]
    for kind, order in presets:
        if kind in existing:
            continue
        create_feed(profile_uuid, kind, label=spec.labels()[kind], pinned_order=order)


def create_feed(profile_uuid: str, kind: str, *,
                label: str | None = None, pinned_order: int | None = None,
                config: dict | None = None) -> str:
    feed_id = str(uuid_lib.uuid4())
    cfg = dict(spec.DEFAULT_CONFIG.get(kind, {}))
    if config:
        cfg.update(config)
    with cursor(write=True) as c:
        c.execute(
            "INSERT INTO feed_defs (feed_id, profile_uuid, kind, label, pinned_order, config) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (feed_id, profile_uuid, kind, label, pinned_order, json.dumps(cfg)),
        )
    return feed_id


def delete_feed(profile_uuid: str, feed_id: str):
    with cursor(write=True) as c:
        c.execute("DELETE FROM feed_defs WHERE profile_uuid = ? AND feed_id = ?",
                  (profile_uuid, feed_id))


def get_feed_def(feed_id: str) -> dict | None:
    with cursor() as c:
        r = c.execute("SELECT * FROM feed_defs WHERE feed_id = ?", (feed_id,)).fetchone()
    if not r:
        return None
    out = dict(r)
    try:
        out["config"] = json.loads(out["config"]) if out.get("config") else {}
    except Exception:
        out["config"] = {}
    return out


def get_feed_items(feed_id: str, limit: int = 60, offset: int = 0) -> dict:
    with cursor() as c:
        rows = c.execute(
            "SELECT fi.rank, fi.why, fi.source_of_rec, fi.generated_at, c.* "
            "FROM feed_items fi JOIN candidates c ON c.video_id = fi.video_id "
            "WHERE fi.feed_id = ? ORDER BY fi.rank ASC LIMIT ? OFFSET ?",
            (feed_id, limit, offset),
        ).fetchall()
        # total count is cheap & useful for "no more results" UI hint
        total = c.execute("SELECT COUNT(*) FROM feed_items WHERE feed_id = ?",
                          (feed_id,)).fetchone()[0]
    items = [dict(r) for r in rows]
    generated_at = items[0]["generated_at"] if items else 0
    age = int(time.time()) - generated_at if generated_at else None
    # Drop embedding blobs from API output — they're internal.
    for it in items:
        it.pop("embedding", None)
    has_more = (offset + len(items)) < total
    return {
        "videos": items,
        "generated_at": generated_at,
        "generation_age_sec": age,
        "total": total,
        "offset": offset,
        "has_more": has_more,
        # "expanding" + "exhausted" are filled in by the API layer (it owns the
        # ability to trigger expansions via the job runner).
    }


def _replace_feed_items(feed_id: str, ranked: list[dict], source_of_rec: str):
    now = int(time.time())
    with cursor(write=True) as c:
        c.execute("DELETE FROM feed_items WHERE feed_id = ?", (feed_id,))
        c.executemany(
            "INSERT INTO feed_items (feed_id, video_id, rank, why, source_of_rec, generated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(feed_id, it["video_id"], i, it.get("why", ""), source_of_rec, now)
             for i, it in enumerate(ranked)],
        )
    candidates.mark_surfaced([it["video_id"] for it in ranked])


def _append_feed_items(feed_id: str, ranked: list[dict], source_of_rec: str) -> int:
    """Append new items after the existing rank tail. Returns count appended.

    Items whose video_id is already in this feed are silently skipped — keeps
    the user from seeing duplicates as they scroll.
    """
    now = int(time.time())
    with cursor(write=True) as c:
        max_rank = c.execute(
            "SELECT COALESCE(MAX(rank), -1) FROM feed_items WHERE feed_id = ?",
            (feed_id,),
        ).fetchone()[0]
        existing = {r["video_id"] for r in c.execute(
            "SELECT video_id FROM feed_items WHERE feed_id = ?", (feed_id,)
        ).fetchall()}
        new_items = [it for it in ranked if it["video_id"] not in existing]
        if not new_items:
            return 0
        c.executemany(
            "INSERT INTO feed_items (feed_id, video_id, rank, why, source_of_rec, generated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(feed_id, it["video_id"], max_rank + 1 + i, it.get("why", ""), source_of_rec, now)
             for i, it in enumerate(new_items)],
        )
    candidates.mark_surfaced([it["video_id"] for it in new_items])
    return len(new_items)


# ── per-kind generators ─────────────────────────────────────────────────────


async def _generate_fresh(profile_uuid: str, cfg: dict, ctx: dict) -> list[dict]:
    """Followed-channels RSS only. Fortress-safe."""
    channels = ctx.get("followed_channels") or []
    rss = ChannelRSSSource()
    for ch in channels:
        await rss.fetch_channel(ch["channel_id"], mode=ctx["mode"])
    pool = candidates.by_source("channel_rss", limit=cfg.get("size", 60) * 4)
    # Sort by published_at desc.
    pool.sort(key=lambda v: v.get("published_at") or 0, reverse=True)
    for v in pool[:cfg.get("size", 60)]:
        v["why"] = "από κανάλι που ακολουθείς"
    return pool[:cfg.get("size", 60)]


async def _generate_quality(profile_uuid: str, cfg: dict, ctx: dict) -> list[dict]:
    src = TournesolSource()
    await src.fetch_top(mode=ctx["mode"], language=ctx.get("language"), limit=80)
    pool = candidates.by_source("tournesol", limit=cfg.get("size", 60) * 2)
    pool.sort(key=lambda v: v.get("quality_score") or 0, reverse=True)
    for v in pool[:cfg.get("size", 60)]:
        v["why"] = "ψηλά στην ποιοτική αξιολόγηση Tournesol"
    return pool[:cfg.get("size", 60)]


async def _generate_trending(profile_uuid: str, cfg: dict, ctx: dict) -> list[dict]:
    src = YtDlpChartsSource()
    country = ctx.get("region") or "US"
    await src.charts(mode=ctx["mode"], country=country, limit=80)
    pool = candidates.by_source("yt_charts", limit=cfg.get("size", 60) * 2)
    for v in pool[:cfg.get("size", 60)]:
        v["why"] = f"trending στο {country}"
    return pool[:cfg.get("size", 60)]


async def _generate_community(profile_uuid: str, cfg: dict, ctx: dict) -> list[dict]:
    await RedditSource().fetch(mode=ctx["mode"])
    await HNAlgoliaSource().fetch(mode=ctx["mode"])
    pool = [*candidates.by_source("reddit", 50), *candidates.by_source("hn_algolia", 50)]
    # Sort by fetched_at desc.
    pool.sort(key=lambda v: v.get("fetched_at") or 0, reverse=True)
    for v in pool[:cfg.get("size", 60)]:
        v["why"] = "ανεβαίνει σε community feeds"
    return pool[:cfg.get("size", 60)]


async def _llm_query_gen(seeds: list[str], k: int, ctx: dict) -> list[str]:
    """Ask the LLM for k YT search queries.

    Sources of inspiration tried in order: explicit seeds → persona text → top
    favourite/history titles. When nothing usable exists we return [].
    """
    persona_text = ((ctx.get("taste_profile") or {}).get("persona_text") or "").strip()
    fav_titles = [f.get("title", "") for f in (ctx.get("favorites") or []) if f.get("title")][:10]
    hist_titles = [h.get("title", "") for h in (ctx.get("history") or []) if h.get("title")][:10]
    inputs = list(seeds) or fav_titles or hist_titles
    if not inputs and not persona_text:
        return []
    llm = get_llm_backend()
    if not await llm.available():
        # Use the seeds/titles verbatim as queries — better than nothing.
        return (inputs or [persona_text[:80]])[:k]
    bullet_inputs = "\n- ".join(inputs) if inputs else "(none)"
    persona_clip = persona_text[:400] if persona_text else "(no persona yet)"
    prompt = (
        "Generate diverse YouTube search queries that would surface new videos "
        f"matching the user. Output JSON: {{\"queries\": [\"q1\", ...]}}. Produce {k} queries.\n"
        f"Persona description:\n{persona_clip}\n\n"
        f"Known interests:\n- {bullet_inputs}"
    )
    try:
        resp = await llm.generate(prompt, json_mode=True, max_tokens=512,
                                  privacy_mode=ctx["mode"], profile_uuid=ctx.get("profile_uuid"))
        data = json.loads(resp.text)
        out = [q for q in data.get("queries", []) if isinstance(q, str) and q.strip()]
        return out[:k] if out else (inputs or [persona_text[:80]])[:k]
    except Exception as e:
        log.warning("LLM query gen failed: %s", e)
        return (inputs or [persona_text[:80]])[:k]


async def _build_query_vec(ctx: dict) -> list[float]:
    """Compute a query vector from persona + favorites centroid."""
    emb = get_embedding_backend()
    parts: list[str] = []
    tp = ctx.get("taste_profile") or {}
    if tp.get("persona_text"):
        parts.append(tp["persona_text"])
    parts.extend(ctx.get("seed_interests") or [])
    fav_titles = [f.get("title", "") for f in (ctx.get("favorites") or [])][:20]
    parts.extend(fav_titles)
    if not parts:
        return []
    vectors = await emb.embed(parts)
    return centroid(vectors)


async def _generate_discover(profile_uuid: str, cfg: dict, ctx: dict) -> list[dict]:
    seeds = ctx.get("seed_interests") or []
    queries = await _llm_query_gen(seeds, k=8, ctx=ctx)
    src = YtDlpSearchSource()
    pool_ids: list[str] = []
    for q in queries:
        ids = await src.search(q, mode=ctx["mode"], limit=10)
        pool_ids.extend(ids)
    # Also include a sliver of Tournesol top-N as a quality anchor.
    if "tournesol" in cfg.get("sources", []):
        await TournesolSource().fetch_top(mode=ctx["mode"], language=ctx.get("language"), limit=30)
    pool = candidates.by_source("yt_search", limit=400)
    pool.extend(candidates.by_source("tournesol", limit=80))
    # Dedupe.
    seen, deduped = set(), []
    for v in pool:
        if v["video_id"] in seen:
            continue
        seen.add(v["video_id"])
        deduped.append(v)
    # Exclude videos the user already watched / hated.
    watched = ctx.get("watched_ids") or set()
    banned = ctx.get("banned_ids") or set()
    deduped = [v for v in deduped if v["video_id"] not in watched and v["video_id"] not in banned]
    # Compute embeddings for items missing them.
    await _ensure_embeddings(deduped)
    query_vec = await _build_query_vec(ctx)
    if not query_vec:
        # No persona/seeds yet: random sample.
        random.shuffle(deduped)
        ranked = deduped[:cfg.get("size", 60)]
    else:
        ranked = mmr(
            [{**v, "embedding": candidates.unpack_embedding(v.get("embedding"))} for v in deduped],
            query_vec, lam=cfg.get("mmr_lambda", 0.7),
            k=cfg.get("size", 60), channel_quota=cfg.get("channel_quota", 2),
        )
    # Enforce exploration floor: replace some slots with random off-graph items.
    floor = cfg.get("exploration_floor", 0.25)
    richness = (ctx.get("taste_profile") or {}).get("signal_richness", 0.0)
    pct = max(floor, exploration_floor(richness, base=floor))
    n_explore = int(len(ranked) * pct)
    if n_explore > 0 and len(deduped) > len(ranked):
        leftovers = [v for v in deduped if v["video_id"] not in {r["video_id"] for r in ranked}]
        random.shuffle(leftovers)
        # Splice exploration items in at every 4th slot.
        for i, picked in enumerate(leftovers[:n_explore]):
            slot = min(len(ranked) - 1, (i + 1) * 4 - 1)
            ranked[slot] = picked
            ranked[slot]["why"] = "δοκιμή έξω από το προφίλ σου"
    for v in ranked:
        v.setdefault("why", "βάσει του προφίλ σου")
    return ranked


async def _generate_closer(profile_uuid: str, cfg: dict, ctx: dict) -> list[dict]:
    # Pure exploit: lean on favorited channels + related to favorited videos.
    favs = ctx.get("favorites") or []
    followed = ctx.get("followed_channels") or []
    fav_channels = {f.get("channel_id") for f in favs if f.get("channel_id")}
    rss = ChannelRSSSource()
    for ch in followed:
        await rss.fetch_channel(ch["channel_id"], mode=ctx["mode"])
    for ch in fav_channels:
        if ch:
            await rss.fetch_channel(ch, mode=ctx["mode"])
    rel = YtDlpRelatedSource()
    for f in favs[:10]:
        if f.get("video_id"):
            await rel.related(f["video_id"], mode=ctx["mode"], limit=10)
    pool = [*candidates.by_source("channel_rss", 200), *candidates.by_source("yt_related", 200)]
    seen, deduped = set(), []
    for v in pool:
        if v["video_id"] in seen:
            continue
        seen.add(v["video_id"])
        deduped.append(v)
    watched = ctx.get("watched_ids") or set()
    deduped = [v for v in deduped if v["video_id"] not in watched]
    await _ensure_embeddings(deduped)
    query_vec = await _build_query_vec(ctx)
    if not query_vec:
        ranked = deduped[:cfg.get("size", 60)]
    else:
        ranked = mmr(
            [{**v, "embedding": candidates.unpack_embedding(v.get("embedding"))} for v in deduped],
            query_vec, lam=cfg.get("mmr_lambda", 0.9),
            k=cfg.get("size", 60), channel_quota=cfg.get("channel_quota", 3),
        )
    for v in ranked:
        v["why"] = "κοντά σε αυτά που ήδη αγαπάς"
    return ranked


async def _generate_surprise(profile_uuid: str, cfg: dict, ctx: dict) -> list[dict]:
    # Discover with knobs inverted: oblique LLM bridges, high exploration.
    seeds = ctx.get("seed_interests") or []
    persona_text = ((ctx.get("taste_profile") or {}).get("persona_text") or "").strip()
    fav_titles = [f.get("title", "") for f in (ctx.get("favorites") or []) if f.get("title")][:10]
    seeds_for_llm = seeds or fav_titles or ([persona_text[:80]] if persona_text else [])
    queries: list[str] = []
    llm = get_llm_backend()
    if cfg.get("oblique_llm") and await llm.available() and (seeds_for_llm or persona_text):
        prompt = (
            "List 8 *unexpected adjacent* YouTube search queries — topics that share aesthetic, "
            "intellectual, or emotional DNA with the user's tastes but they might not have "
            "thought to look at. Output JSON: {\"queries\": [...]}.\n"
            f"Persona:\n{persona_text[:400] or '(none)'}\n\n"
            f"Known interests:\n- " + "\n- ".join(seeds_for_llm or ['(none)'])
        )
        try:
            resp = await llm.generate(prompt, json_mode=True, max_tokens=600,
                                      privacy_mode=ctx["mode"], profile_uuid=profile_uuid,
                                      temperature=0.9)
            data = json.loads(resp.text)
            queries = [q for q in data.get("queries", []) if isinstance(q, str) and q.strip()]
        except Exception as e:
            log.warning("oblique LLM failed: %s", e)
    if not queries:
        # Fall back to the seeds / favourite titles verbatim.
        queries = seeds_for_llm[:6]
    src = YtDlpSearchSource()
    for q in queries:
        await src.search(q, mode=ctx["mode"], limit=8)
    # Pool from EVERY upstream source so we always have something to surface —
    # surprise should never be empty.
    pool = []
    for src_name in ("yt_search", "tournesol", "reddit", "hn_algolia", "yt_charts", "channel_rss"):
        pool.extend(candidates.by_source(src_name, limit=80))
    # Dedupe while preserving the random-ish ordering we get from shuffle.
    seen, deduped = set(), []
    for v in pool:
        if v["video_id"] in seen:
            continue
        seen.add(v["video_id"])
        deduped.append(v)
    random.shuffle(deduped)
    watched = ctx.get("watched_ids") or set()
    banned = ctx.get("banned_ids") or set()
    deduped = [v for v in deduped if v["video_id"] not in watched and v["video_id"] not in banned]
    ranked = deduped[:cfg.get("size", 40)]
    for v in ranked:
        v["why"] = "απρόσμενο εύρημα — μπορεί να σε εκπλήξει"
    return ranked


_GENERATORS = {
    spec.KIND_FRESH: _generate_fresh,
    spec.KIND_QUALITY: _generate_quality,
    spec.KIND_TRENDING: _generate_trending,
    spec.KIND_COMMUNITY: _generate_community,
    spec.KIND_DISCOVER: _generate_discover,
    spec.KIND_CLOSER: _generate_closer,
    spec.KIND_SURPRISE: _generate_surprise,
}


async def _ensure_embeddings(videos: list[dict]):
    """Embed any candidates missing an ``embedding`` blob.

    Uses title + a short slice of the description. (Transcript-based embeddings
    were dropped: bulk caption fetching triggered YouTube throttling that bled
    into the user-facing subtitle feature.)
    """
    emb = get_embedding_backend()
    if not await emb.available():
        return
    missing = [v for v in videos if not v.get("embedding")]
    if not missing:
        return
    texts = [(v.get("title") or "") + " — " + (v.get("description") or "")[:200]
             for v in missing]
    try:
        vecs = await emb.embed(texts)
    except Exception as e:
        log.warning("embed batch failed: %s", e)
        return
    import struct
    for v, vec in zip(missing, vecs):
        if vec:
            candidates.set_embedding(v["video_id"], vec)
            v["embedding"] = struct.pack(f"{len(vec)}f", *vec)


async def _build_context(profile_uuid: str, mode: str) -> dict:
    """Pull profile data from PYTR + local taste profile."""
    export = await fetch_profile_export(profile_uuid) or {}
    tp = get_taste_profile(profile_uuid) or {}
    seeds_raw = tp.get("onboarding_seed_interests")
    try:
        seeds = json.loads(seeds_raw) if seeds_raw else []
    except Exception:
        seeds = []
    profile_meta = export.get("profile_meta") or {}
    return {
        "profile_uuid": profile_uuid,
        "mode": mode,
        "taste_profile": tp,
        "language": profile_meta.get("content_lang"),
        "region": profile_meta.get("content_region"),
        "favorites": export.get("favorites") or [],
        "history": export.get("history") or [],
        "followed_channels": export.get("followed_channels") or [],
        "watched_ids": {h["video_id"] for h in (export.get("history") or []) if h.get("video_id")},
        "banned_ids": {fb["video_id"] for fb in (export.get("feedback") or []) if fb.get("signal") == "never_again"},
        "seed_interests": seeds,
    }


async def generate_feed(feed_id: str, mode: str | None = None) -> int:
    """Top-level entry point: produce a fresh snapshot for one feed."""
    fd = get_feed_def(feed_id)
    if not fd:
        return 0
    ctx_mode = mode or (get_taste_profile(fd["profile_uuid"]) or {}).get("privacy_mode") or "balanced"
    ctx = await _build_context(fd["profile_uuid"], ctx_mode)
    gen = _GENERATORS.get(fd["kind"])
    if not gen:
        log.warning("unknown feed kind %s", fd["kind"])
        return 0
    try:
        items = await gen(fd["profile_uuid"], fd["config"], ctx)
    except Exception as e:
        log.exception("feed generation failed for %s: %s", feed_id, e)
        return 0
    _replace_feed_items(feed_id, items, source_of_rec=f"{fd['kind']}:generate")
    return len(items)


# ── Lazy expansion ──────────────────────────────────────────────────────────
#
# Per-feed locks + "exhausted" markers live in-process. Restart-safe because
# the worst case after a restart is one duplicate expansion attempt that
# silently appends zero new items.

_EXPANSIONS_IN_FLIGHT: set[str] = set()
_EXPANSIONS_EXHAUSTED: dict[str, float] = {}  # feed_id -> ts of last 0-result expand
_EXHAUSTED_COOLDOWN_SEC = 1800  # 30 min before we try again


def is_expanding(feed_id: str) -> bool:
    return feed_id in _EXPANSIONS_IN_FLIGHT


def is_exhausted(feed_id: str) -> bool:
    ts = _EXPANSIONS_EXHAUSTED.get(feed_id)
    if ts is None:
        return False
    return (time.time() - ts) < _EXHAUSTED_COOLDOWN_SEC


async def expand_feed(feed_id: str) -> int:
    """Add more items to an existing feed.

    Same retrieval logic as generate_feed but appends instead of replacing.
    LLM-driven generators get fresh randomness in their query gen via the
    `expansion_round` ctx flag so we don't replay the same queries.
    """
    if feed_id in _EXPANSIONS_IN_FLIGHT:
        return 0
    _EXPANSIONS_IN_FLIGHT.add(feed_id)
    try:
        fd = get_feed_def(feed_id)
        if not fd:
            return 0
        ctx_mode = (get_taste_profile(fd["profile_uuid"]) or {}).get("privacy_mode") or "balanced"
        ctx = await _build_context(fd["profile_uuid"], ctx_mode)
        ctx["expansion_round"] = int(time.time())  # generators can use as random seed
        gen = _GENERATORS.get(fd["kind"])
        if not gen:
            return 0
        try:
            items = await gen(fd["profile_uuid"], fd["config"], ctx)
        except Exception as e:
            log.exception("feed expansion failed for %s: %s", feed_id, e)
            return 0
        appended = _append_feed_items(feed_id, items, source_of_rec=f"{fd['kind']}:expand")
        if appended == 0:
            _EXPANSIONS_EXHAUSTED[feed_id] = time.time()
            log.info("feed %s exhausted (no new items this round)", feed_id)
        else:
            _EXPANSIONS_EXHAUSTED.pop(feed_id, None)
            log.info("feed %s expanded by %d items", feed_id, appended)
        return appended
    finally:
        _EXPANSIONS_IN_FLIGHT.discard(feed_id)
