# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Transcript adapter: pulls captions/subtitles from YouTube via yt-dlp.

Replaces the would-be Whisper pipeline — captions exist for ~99 % of videos,
either manual (high quality) or auto-generated (good enough for retrieval).
Cached in enrichment_cache so each video is fetched at most once.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time

from .. import candidates
from ..db import cursor
from ..egress import get_client
from .base import Source
from .ytdlp import _ydl_opts

log = logging.getLogger(__name__)

# Crude VTT/SRV3 cleanup: strip timestamps, cue ids, html-ish tags, dedupe lines.
_VTT_TS = re.compile(r"\d{2}:\d{2}[:.]\d{2}[.,]\d{3}\s*-->\s*\d{2}:\d{2}[:.]\d{2}[.,]\d{3}.*")
_VTT_TAG = re.compile(r"<[^>]+>")
_VTT_CUE = re.compile(r"^(WEBVTT|Kind:|Language:|NOTE|STYLE|\d+\s*$)", re.IGNORECASE)

# YouTube auto-captions can land at any of several URLs; we treat them as part
# of the existing yt_search egress purpose so they share Balanced+ mode rules.
PURPOSE = "yt_search"

# How much text to keep per video — embeddings need ~1-2 KB of representative
# content. More just dilutes the centroid with filler.
MAX_CHARS = 2400


class TranscriptSource(Source):
    name = "transcript"
    purpose = PURPOSE

    async def fetch(self, video_id: str, *, mode: str,
                    preferred_langs: tuple[str, ...] = ("en", "el", "auto")) -> str | None:
        """Return cleaned plain-text transcript for video_id, or None."""
        if not video_id or self.in_backoff():
            return None
        cached = _cache_get(video_id)
        if cached is not None:
            return cached or None  # empty string means "tried, none available"
        from ..privacy import check_egress
        if not check_egress("https://www.youtube.com/", purpose=self.purpose, mode=mode).allowed:
            return None
        url = await self._pick_caption_url(video_id, preferred_langs)
        if not url:
            _cache_set(video_id, "")  # remember the negative result
            self.record_success()
            return None
        try:
            r = await get_client().get(url, timeout=30.0)
            if r.status_code == 429:
                # YouTube rate-limited us. Do NOT cache a negative — the video
                # might have perfectly good captions, we just need to back off.
                self.record_failure("HTTP 429 (rate-limited)")
                return None
            if r.status_code != 200:
                _cache_set(video_id, "")
                return None
            # An HTML body where VTT is expected is ambiguous: usually a per-video
            # bad/age-gated caption URL, occasionally the leading edge of a
            # throttle. Don't trigger a source-wide backoff here (that would let
            # one bad video sabotage the rest of the batch) and don't negative-
            # cache yet — surface it via last_error and let fetch_many() decide
            # based on how often it's happening.
            if r.headers.get("content-type", "").startswith("text/html"):
                self.health.last_error = "HTML response"
                return None
            text = _clean_vtt(r.text)
        except Exception as e:
            self.record_failure(str(e))
            return None
        # Trim to MAX_CHARS — keep the start (intro usually states the topic).
        text = text[:MAX_CHARS]
        _cache_set(video_id, text)
        self.record_success()
        return text or None

    async def _pick_caption_url(self, video_id: str, langs: tuple[str, ...]) -> str | None:
        """Use yt-dlp metadata extraction to find a usable captions URL."""
        import yt_dlp

        def _do():
            opts = _ydl_opts(use_cookies=True)
            # We just want the metadata (incl. caption tracks); no actual download.
            opts.update({
                "writesubtitles": False,
                "writeautomaticsub": False,
                "extract_flat": False,
            })
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
        try:
            info = await asyncio.get_running_loop().run_in_executor(None, _do)
        except Exception as e:
            # If yt-dlp says the video is gone, garbage-collect it from the pool.
            # This is a cheap drive-by cleanup that piggybacks on the transcript
            # enrichment cycle.
            msg = str(e).lower()
            dead_markers = ("video unavailable", "private video", "removed by the uploader",
                            "no longer available", "terminated", "this video has been removed")
            if any(m in msg for m in dead_markers):
                _delete_dead_candidate(video_id, msg[:200])
                _cache_set(video_id, "")  # negative cache so we don't retry
                return None
            self.record_failure(str(e))
            return None
        if not info:
            return None
        subs = info.get("subtitles") or {}
        autos = info.get("automatic_captions") or {}
        # Prefer manual subs in the requested languages, then any manual, then auto.
        for lang in langs:
            if lang == "auto":
                continue
            entry = subs.get(lang) or subs.get(lang.split("-")[0])
            if entry:
                return _best_vtt_url(entry)
        if subs:
            return _best_vtt_url(next(iter(subs.values())))
        for lang in langs:
            if lang == "auto":
                continue
            entry = autos.get(lang) or autos.get(lang.split("-")[0])
            if entry:
                return _best_vtt_url(entry)
        if autos:
            return _best_vtt_url(next(iter(autos.values())))
        return None


def _best_vtt_url(entries: list[dict]) -> str | None:
    """Pick a VTT track from yt-dlp's caption list; fall back to anything."""
    vtt = [e for e in entries if e.get("ext") in ("vtt", "vtt.srt")]
    pick = vtt[0] if vtt else (entries[0] if entries else None)
    return pick.get("url") if pick else None


def _clean_vtt(text: str) -> str:
    """Strip timestamps, cue ids, tags; dedupe consecutive duplicate lines."""
    out: list[str] = []
    last = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or _VTT_TS.match(line) or _VTT_CUE.match(line):
            continue
        line = _VTT_TAG.sub("", line).strip()
        if not line or line == last:
            continue
        out.append(line)
        last = line
    return " ".join(out)


def _delete_dead_candidate(video_id: str, reason: str = ""):
    """Hard-delete + global tombstone a video that yt-dlp confirms is gone."""
    try:
        with cursor(write=True) as c:
            c.execute("DELETE FROM candidates WHERE video_id = ?", (video_id,))
            c.execute("DELETE FROM feed_items WHERE video_id = ?", (video_id,))
        candidates.tombstone(video_id, reason=reason)
        log.info("gc dead candidate %s (%s)", video_id, reason)
    except Exception as e:
        log.warning("dead candidate gc failed for %s: %s", video_id, e)


def _cache_get(video_id: str) -> str | None:
    """Returns: cached text (possibly empty string for known-missing), or None."""
    cutoff = int(time.time()) - 30 * 86400
    with cursor() as c:
        row = c.execute(
            "SELECT payload FROM enrichment_cache "
            "WHERE entity_type='transcript' AND entity_id=? AND source='youtube_captions' "
            "AND fetched_at > ?",
            (video_id, cutoff),
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["payload"]).get("text", "")
    except Exception:
        return None


def _cache_set(video_id: str, text: str):
    with cursor(write=True) as c:
        c.execute(
            "INSERT OR REPLACE INTO enrichment_cache "
            "(entity_type, entity_id, source, payload, fetched_at) VALUES (?, ?, ?, ?, ?)",
            ("transcript", video_id, "youtube_captions",
             json.dumps({"text": text}), int(time.time())),
        )


def cached_transcripts(video_ids: list[str]) -> dict[str, str]:
    """Read transcripts already in the cache. Misses are omitted from the result.

    Cheap — used by the embedding step to avoid blocking on yt-dlp calls. The
    background enrichment job is responsible for populating the cache.
    """
    if not video_ids:
        return {}
    cutoff = int(time.time()) - 30 * 86400
    placeholders = ",".join("?" * len(video_ids))
    with cursor() as c:
        rows = c.execute(
            f"SELECT entity_id, payload FROM enrichment_cache "
            f"WHERE entity_type='transcript' AND source='youtube_captions' "
            f"AND entity_id IN ({placeholders}) AND fetched_at > ?",
            [*video_ids, cutoff],
        ).fetchall()
    out: dict[str, str] = {}
    for r in rows:
        try:
            text = json.loads(r["payload"]).get("text", "")
        except Exception:
            continue
        if text:  # skip negative-cache rows
            out[r["entity_id"]] = text
    return out


async def fetch_many(video_ids: list[str], *, mode: str,
                     pause_sec: float = 2.0, block_threshold: int = 3) -> dict[str, str]:
    """Sequential, throttle-aware caption fetches. A short pause between
    requests empirically keeps us under YouTube's limits.

    Two failure shapes are handled differently:

    * an *isolated* HTML/non-200 response → a per-video bad caption URL. The
      video is negative-cached and the batch carries on. One bad video must
      never penalise the rest (the previous behaviour backed off the whole
      source on the first HTML, skipping every remaining good video).
    * ``block_threshold`` blocked responses in a row (429 or HTML, with no
      success in between) → YouTube is throttling us. Abort the cycle and leave
      the rest for next time. Crucially the videos that triggered the abort are
      *not* negative-cached, so good videos aren't wrongly marked captionless
      during a throttle storm.
    """
    src = TranscriptSource()
    results: dict[str, str] = {}
    consecutive_block = 0

    for vid in video_ids:
        if consecutive_block >= block_threshold:
            log.warning("transcript fetch aborting cycle after %d consecutive blocks "
                        "(likely throttled) — %d/%d filled before stop",
                        consecutive_block, len(results), len(video_ids))
            break
        # Escalating politeness if blocks keep coming.
        extra_sleep = min((consecutive_block) * 5.0, 30.0)
        if extra_sleep:
            await asyncio.sleep(extra_sleep)
        t = await src.fetch(vid, mode=mode)
        err = src.health.last_error or ""
        if t:
            results[vid] = t
            consecutive_block = 0
        elif "429" in err:
            consecutive_block += 1
        elif "HTML" in err:
            # Looks like an isolated bad-URL video — remember it so we don't
            # retry it every cycle. If this is actually the start of a throttle
            # burst, the loop bails at block_threshold, capping how many good
            # videos can be mismarked to at most block_threshold - 1.
            if consecutive_block < block_threshold - 1:
                _cache_set(vid, "")
            consecutive_block += 1
        else:
            # Genuine "no captions" (already negative-cached in fetch) or a
            # benign miss — not a block signal.
            consecutive_block = 0
        await asyncio.sleep(pause_sec)

    return results
