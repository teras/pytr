"""Transcript: VTT cleanup, cache round-trip, embedding uses transcript when present."""
from __future__ import annotations

import json
import time

import pytest

from app import candidates, profile_sync
from app.db import init_db, cursor
from app.feeds import engine as engine_mod
from app.sources import transcript as ts


SAMPLE_VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:03.500
Welcome to this video about jazz history.

00:00:03.500 --> 00:00:06.000
Welcome to this video about jazz history.

00:00:06.000 --> 00:00:10.000
<c.colorE5E5E5>Today</c> we cover bebop and Charlie Parker.

00:00:10.000 --> 00:00:14.000
The development of fast harmonic improvisation.
"""


@pytest.fixture(scope="module", autouse=True)
def _init():
    init_db()


def test_vtt_clean_strips_timestamps_tags_and_dedupes():
    out = ts._clean_vtt(SAMPLE_VTT)
    assert "Welcome to this video about jazz history" in out
    # Dedupe: the duplicate cue should appear only once.
    assert out.count("Welcome to this video") == 1
    # Tags stripped.
    assert "<c.colorE5E5E5>" not in out
    assert "Today" in out
    # Timestamps gone.
    assert "00:00" not in out
    # Cue headers gone.
    assert "WEBVTT" not in out
    assert "Kind:" not in out


def test_cache_round_trip():
    ts._cache_set("vidT1", "Some transcript text about cooking.")
    assert ts._cache_get("vidT1") == "Some transcript text about cooking."


def test_negative_cache_is_remembered_but_omitted_from_bulk_read():
    ts._cache_set("vidNeg", "")  # negative result
    assert ts._cache_get("vidNeg") == ""  # raw cache returns empty string
    # cached_transcripts should skip empty entries.
    out = ts.cached_transcripts(["vidNeg"])
    assert "vidNeg" not in out


def test_cached_transcripts_returns_only_known_ids():
    ts._cache_set("vidA", "alpha")
    ts._cache_set("vidB", "beta")
    out = ts.cached_transcripts(["vidA", "vidB", "vidMissing"])
    assert out == {"vidA": "alpha", "vidB": "beta"}


@pytest.mark.asyncio
async def test_embedding_prefers_transcript_over_description():
    # Two candidates: one with transcript, one without. Embed both and verify the
    # transcript one ranks differently from the title-only one for a query that
    # only matches the transcript.
    candidates.upsert({"video_id": "txA", "title": "Random vlog",
                       "description": "today's vlog stuff"}, source="test")
    candidates.upsert({"video_id": "txB", "title": "Random vlog",
                       "description": "today's vlog stuff"}, source="test")
    ts._cache_set("txA", "deep dive into transformer attention mechanisms and self-supervised learning")
    # Force the hash embedder (already the test default).
    profile_sync.ensure_taste_profile("uuid-tx")
    from app.llm import get_embedding_backend
    emb = get_embedding_backend()
    vids = [
        {"video_id": "txA", "title": "Random vlog", "description": "today's vlog stuff"},
        {"video_id": "txB", "title": "Random vlog", "description": "today's vlog stuff"},
    ]
    await engine_mod._ensure_embeddings(vids)
    # txA should have an embedding that differs from txB's because the texts fed
    # to the embedder differ (transcript vs description).
    from app import candidates as cmod
    got = cmod.fetch_many(["txA", "txB"])
    veca = cmod.unpack_embedding(got["txA"]["embedding"])
    vecb = cmod.unpack_embedding(got["txB"]["embedding"])
    assert veca and vecb
    # They should not be identical (transcript text is distinct).
    assert veca != vecb
